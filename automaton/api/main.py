"""FastAPI control plane for the Automaton (local-first; bind to 127.0.0.1).

Endpoints implement the operational workflow: submit tasks, inspect plans,
list/resolve pending approvals, query memory and read the audit trail.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..engine import Automaton
from ..core.types import ApprovalStatus, MemoryKind

app = FastAPI(title="Sovereign Multi-Agent AI Automaton", version="0.1.0")

_ENGINE: Optional[Automaton] = None


def engine() -> Automaton:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Automaton()
    return _ENGINE


def set_engine(e: Automaton) -> None:
    global _ENGINE
    _ENGINE = e


# --------------------------------------------------------------------------- #
class TaskIn(BaseModel):
    objective: str
    context: dict[str, Any] = Field(default_factory=dict)


class DecisionIn(BaseModel):
    approve: bool
    decided_by: str = "owner"


class CredentialIn(BaseModel):
    name: str
    secret: str
    kind: str = "pat"  # pat | api_key


class MemoryIn(BaseModel):
    kind: str = "semantic"
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict[str, Any]:
    e = engine()
    return {
        "status": "ok",
        "tiers": {t: e.router.last_tier_used.get(t, "idle") for t in ("local", "oracle_frontier", "oracle_safety")},
        "memory_counts": e.memory.count(),
        "pending_approvals": len(e.gateway.pending()),
    }


@app.post("/tasks")
def submit_task(body: TaskIn) -> dict[str, Any]:
    run = engine().submit(body.objective, body.context)
    return _run_view(run)


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    e = engine()
    run = _find_run(e, task_id)
    if run is None:
        raise HTTPException(404, "task not found")
    return _run_view(run)


@app.post("/tasks/{task_id}/resume")
def resume_task(task_id: str) -> dict[str, Any]:
    e = engine()
    run = _find_run(e, task_id)
    if run is None:
        raise HTTPException(404, "task not found")
    run = e.resume(run)
    return _run_view(run)


@app.get("/approvals")
def list_approvals() -> list[dict[str, Any]]:
    return [r.model_dump() for r in engine().gateway.all_requests()]


@app.post("/approvals/{approval_id}/decision")
def decide(approval_id: str, body: DecisionIn) -> dict[str, Any]:
    try:
        req = engine().gateway.decide(approval_id, body.approve, body.decided_by)
    except KeyError:
        raise HTTPException(404, "approval not found")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return req.model_dump()


@app.get("/tools")
def tool_specs() -> list[dict[str, Any]]:
    """Names + schemas only. Credentials are NEVER exposed by this API."""
    return engine().tools.specs()


@app.post("/credentials")
def enroll_credential(body: CredentialIn) -> dict[str, str]:
    e = engine()
    if body.kind == "api_key":
        e.auth.store_api_key(body.name, body.secret)
    else:
        e.auth.store_pat(body.name, body.secret)
    return {"stored": body.name}


@app.get("/memory/search")
def memory_search(q: str, top_k: int = 5) -> list[dict[str, Any]]:
    recs = engine().memory.search(q, top_k=top_k)
    return [{"id": r.id, "kind": r.kind.value, "content": r.content,
             "score": round(r.score or 0, 3), "meta": r.meta} for r in recs]


@app.post("/memory")
def memory_add(body: MemoryIn) -> dict[str, int]:
    try:
        kind = MemoryKind(body.kind)
    except ValueError:
        raise HTTPException(422, f"unknown memory kind {body.kind!r}")
    rid = engine().memory.add(kind, body.content, body.meta)
    return {"id": rid}


@app.get("/audit")
def audit(limit: int = 100) -> list[dict[str, Any]]:
    events = engine().audit[-limit:]
    return [ev.model_dump() for ev in events]


@app.get("/constitution")
def constitution() -> dict[str, Any]:
    return engine().policy.constitution


# --------------------------------------------------------------------------- #
_RUNS: dict[str, Any] = {}


def _find_run(e: Automaton, task_id: str):
    return _RUNS.get(task_id)


_orig_submit = engine  # keep linter quiet about ordering; runs tracked below


@app.on_event("startup")
def _startup() -> None:
    pass


def _track(run) -> None:
    _RUNS[run.request.id] = run


def _run_view(run) -> dict[str, Any]:
    _track(run)
    return {
        "task_id": run.request.id,
        "objective": run.request.objective,
        "status": run.status,
        "denied_reason": run.denied_reason,
        "plan": run.plan.model_dump() if run.plan else None,
        "approvals": run.approvals,
    }
