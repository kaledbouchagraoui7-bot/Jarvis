"""Personal Constitution loader (YAML with a dependency-free fallback parser).

The constitution is DATA, not code — the Policy Decision Engine reads it and
enforces it deterministically. Editing this file is itself a HIGH-risk action
per rule R-003.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

DEFAULT_PATH = os.environ.get(
    "AUTOMATON_CONSTITUTION",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "constitution.yaml"),
)


_BARE_RE = re.compile(r"""(?<![\w"'-])([A-Za-z][\w .+@/-]*)(?![\w"'])""")


def _flow_to_json(s: str) -> str:
    """Convert a YAML flow collection ({k: v} / [a, b]) with bare tokens to JSON."""
    def repl(m: re.Match) -> str:
        tok = m.group(1)
        low = tok.lower()
        if low in ("true", "false", "null", "yes", "no"):
            return tok  # json.loads won't accept yes/no but tolerate; handled later
        return '"' + tok.replace('"', '\\"') + '"'

    fixed = _BARE_RE.sub(repl, s)
    # normalize yes/no booleans inside the flow
    fixed = re.sub(r'"(yes|true)"', "true", fixed)
    fixed = re.sub(r'"(no|false)"', "false", fixed)
    return fixed


def _parse_scalar(tok: str) -> Any:
    tok = tok.strip()
    if tok.startswith("{") or tok.startswith("["):
        try:
            return json.loads(_flow_to_json(tok))
        except json.JSONDecodeError:
            return tok
    if len(tok) >= 2 and tok[0] in "\"'" and tok[-1] == tok[0]:
        return tok[1:-1]
    low = tok.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


def _simple_yaml_load(text: str) -> dict[str, Any]:
    """Tiny YAML-subset parser used when PyYAML is unavailable.

    Supports nested mappings by indentation, "- " sequences of scalars or
    inline flow maps, quoted strings, folded scalars (> |), and comments.
    Sufficient for constitution.yaml; NOT a general YAML implementation.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # strip trailing comments that are outside quotes
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, stripped))

    pos = [0]

    def peek() -> tuple[int, str] | None:
        return lines[pos[0]] if pos[0] < len(lines) else None

    def parse_seq(indent: int) -> list[Any]:
        out: list[Any] = []
        while True:
            nxt = peek()
            if nxt is None or nxt[0] < indent or not nxt[1].startswith("- "):
                break
            if nxt[0] > indent:
                raise ValueError(f"bad sequence indent at {nxt[1]!r}")
            pos[0] += 1
            item = nxt[1][2:].strip()
            if ":" in item and not item.startswith(("{", "[")):
                # "- key: value" — treat as map entry; may have deeper block children
                key, _, val = item.partition(":")
                m: dict[str, Any] = {}
                if val.strip():
                    m[key.strip()] = _parse_scalar(val)
                else:
                    child = peek()
                    if child and child[0] > indent + 2:
                        m[key.strip()] = parse_block(child[0])
                    else:
                        m[key.strip()] = None
                # absorb sibling keys of this map at indent+2
                while True:
                    sib = peek()
                    if sib is None or sib[0] != indent + 2 or sib[1].startswith("- "):
                        break
                    pos[0] += 1
                    k2, _, v2 = sib[1].partition(":")
                    if v2.strip():
                        m[k2.strip()] = _parse_scalar(v2)
                    else:
                        c2 = peek()
                        if c2 and c2[0] > sib[0]:
                            m[k2.strip()] = parse_block(c2[0])
                        else:
                            m[k2.strip()] = None
                out.append(m)
            else:
                out.append(_parse_scalar(item))
        return out

    def parse_map(indent: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            nxt = peek()
            if nxt is None or nxt[0] < indent:
                break
            if nxt[0] > indent:
                raise ValueError(f"unexpected indent at {nxt[1]!r}")
            if nxt[1].startswith("- "):
                break
            pos[0] += 1
            key, _, val = nxt[1].partition(":")
            key, val = key.strip(), val.strip()
            if val in (">", "|", ">-", "|-"):
                block: list[str] = []
                while True:
                    n2 = peek()
                    if n2 is None or n2[0] <= indent:
                        break
                    block.append(n2[1])
                    pos[0] += 1
                out[key] = " ".join(block)
            elif val == "":
                n2 = peek()
                if n2 is not None and n2[0] > indent:
                    out[key] = parse_block(n2[0])
                elif n2 is not None and n2[0] == indent and n2[1].startswith("- "):
                    out[key] = parse_seq(indent)
                else:
                    out[key] = {}
            else:
                out[key] = _parse_scalar(val)
        return out

    def parse_block(indent: int) -> Any:
        nxt = peek()
        if nxt is not None and nxt[1].startswith("- "):
            return parse_seq(indent)
        return parse_map(indent)

    first = peek()
    return parse_map(first[0]) if first else {}


def load_constitution(path: str | None = None) -> dict[str, Any]:
    path = path or DEFAULT_PATH
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        data = _simple_yaml_load(text)
    if not isinstance(data, dict):
        raise ValueError("Constitution must be a mapping at the top level.")
    return data


if __name__ == "__main__":  # manual check: python -m automaton.core.constitution
    c = load_constitution()
    print(json.dumps(c, indent=2)[:1200])
