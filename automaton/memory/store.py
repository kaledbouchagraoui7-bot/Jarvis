"""Pillar D — Layered Memory Architecture (externalized state, not context).

  * Working Memory   — small, hot scratchpad for the current step (bounded).
  * Episodic Memory  — append-only log of what happened and outcomes.
  * Semantic Memory  — durable facts with sources (verified vs assumed).
  * Project State    — long-horizon state per project, versioned snapshots.

Storage: SQLite (relational) + a local vector index (stdlib cosine over
hashed bag-of-words embeddings; swap in sentence-transformers + FAISS/chroma
for production by re-implementing ``embed()``/``search()`` only).

The Retrieval Compiler fetches ONLY the evidence needed for the current step,
keeping prompts small and preventing VRAM bottlenecks on the local workhorse.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..core.types import MemoryKind

_TOKEN_RX = re.compile(r"[a-z0-9]+")


def embed(text: str, dim: int = 256) -> list[float]:
    """Deterministic hashed bag-of-words embedding (offline-safe stand-in)."""
    vec = [0.0] * dim
    for tok in _TOKEN_RX.findall(text.lower()):
        h = int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 63) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class MemoryRecord:
    id: int
    kind: MemoryKind
    content: str
    meta: dict[str, Any]
    score: Optional[float] = None


class MemoryStore:
    def __init__(self, db_path: str = "data/memory.sqlite"):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS memory(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 kind TEXT NOT NULL,
                 content TEXT NOT NULL,
                 meta TEXT NOT NULL DEFAULT '{}',
                 embedding TEXT NOT NULL,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_kind ON memory(kind)")
        self.db.commit()

    # ------------------------------------------------------------------ #
    def add(self, kind: MemoryKind, content: str, meta: Optional[dict[str, Any]] = None) -> int:
        meta = meta or {}
        emb = embed(content)
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO memory(kind, content, meta, embedding) VALUES (?,?,?,?)",
                (kind.value, content, json.dumps(meta), json.dumps(emb)),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def search(
        self,
        query: str,
        kinds: Optional[Iterable[MemoryKind]] = None,
        top_k: int = 5,
        min_score: float = 0.05,
    ) -> list[MemoryRecord]:
        qv = embed(query)
        want = [k.value for k in (kinds or [])] or None
        with self._lock:
            rows = self.db.execute(
                "SELECT id, kind, content, meta, embedding FROM memory"
                + (" WHERE kind IN (%s)" % ",".join("?" * len(want)) if want else ""),
                want or (),
            ).fetchall()
        out: list[MemoryRecord] = []
        for rid, kind, content, meta, emb in rows:
            s = cosine(qv, json.loads(emb))
            if s >= min_score:
                out.append(MemoryRecord(rid, MemoryKind(kind), content, json.loads(meta), s))
        out.sort(key=lambda r: r.score or 0.0, reverse=True)
        return out[:top_k]

    def recent(self, kind: MemoryKind, limit: int = 20) -> list[MemoryRecord]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, kind, content, meta FROM memory WHERE kind=? ORDER BY id DESC LIMIT ?",
                (kind.value, limit),
            ).fetchall()
        return [MemoryRecord(r[0], MemoryKind(r[1]), r[2], json.loads(r[3])) for r in rows]

    def count(self) -> dict[str, int]:
        with self._lock:
            rows = self.db.execute("SELECT kind, COUNT(*) FROM memory GROUP BY kind").fetchall()
        return {k: c for k, c in rows}

    def close(self) -> None:
        self.db.close()


class WorkingMemory:
    """Bounded hot scratchpad for the active step — never grows unbounded."""

    MAX_ITEMS = 24

    def __init__(self) -> None:
        self._items: list[tuple[str, Any]] = []

    def set(self, key: str, value: Any) -> None:
        self._items = [(k, v) for k, v in self._items if k != key]
        self._items.append((key, value))
        if len(self._items) > self.MAX_ITEMS:
            self._items = self._items[-self.MAX_ITEMS:]

    def get(self, key: str, default: Any = None) -> Any:
        for k, v in reversed(self._items):
            if k == key:
                return v
        return default

    def snapshot(self) -> dict[str, Any]:
        return dict(self._items)

    def clear(self) -> None:
        self._items.clear()


class RetrievalCompiler:
    """Fetch exactly the evidence the current step needs — nothing more.

    Assembles a compact context block from semantic + episodic + project
    memory, deduplicated and token-budgeted, so the local model's VRAM stays
    free for reasoning instead of rote memorization.
    """

    def __init__(self, store: MemoryStore, char_budget: int = 3000):
        self.store = store
        self.char_budget = char_budget

    def compile(self, step_query: str, project: Optional[str] = None) -> str:
        parts: list[str] = []
        used = 0
        seen: set[int] = set()

        def push(header: str, records: list[MemoryRecord]) -> None:
            nonlocal used
            for rec in records:
                if rec.id in seen:
                    continue
                seen.add(rec.id)
                block = f"- [{rec.kind.value}|{rec.score:.2f}] {rec.content}"
                if used + len(block) > self.char_budget:
                    return
                parts.append(block)
                used += len(block)

        kinds = [MemoryKind.SEMANTIC, MemoryKind.EPISODIC]
        query = step_query if not project else f"{step_query} project:{project}"
        push("retrieved", self.store.search(query, kinds=kinds, top_k=6))
        if project:
            push("project", self.store.recent(MemoryKind.PROJECT_STATE, limit=3))
        if not parts:
            return "(no externalized memory relevant to this step)"
        return "\n".join(parts)
