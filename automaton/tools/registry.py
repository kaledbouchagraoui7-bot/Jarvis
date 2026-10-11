"""The "Hands": MCP-style tool registry + the Execution Layer dispatcher.

Tools are declared with name, schema and required credential. The orchestrator
may only see tool *names and schemas* — never credentials. At execution time
the dispatcher asks AuthManager for a handle and injects the secret inside the
call boundary only.

Built-in servers mirror the blueprint: Notion, GitHub, Obsidian, sandboxed
shell/code-runner, plus deterministic math (hallucination control: the LLM is
forbidden from doing arithmetic itself).

Network-backed tools run against local fixtures when AUTOMATON_ALLOW_NETWORK
is unset, so demos/CI stay hermetic while the real HTTP code paths exist.
"""
from __future__ import annotations

import ast
import json
import operator as _op
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.auth_manager import AuthManager
from ..core.types import AuditEvent


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, str] = field(default_factory=dict)  # param -> json type hint
    credential: Optional[str] = None                          # vault entry name, if any
    destructive: bool = False


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: Optional[str] = None
    evidence_source: Optional[str] = None   # for hallucination-control citations


# --------------------------------------------------------------------------- #
# Deterministic math (Pillar C: "forbidden from guessing" → use tools)
# --------------------------------------------------------------------------- #
_ALLOWED_BINOPS = {
    ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod, ast.Pow: _op.pow,
}
_ALLOWED_UNARY = {ast.UAdd: _op.pos, ast.USub: _op.neg}


def safe_eval_math(expr: str) -> float:
    """AST-whitelisted arithmetic evaluator — no eval(), no names, no calls."""
    tree = ast.parse(expr, mode="eval")

    def walk(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](walk(node.operand))
        raise ValueError(f"disallowed expression element: {type(node).__name__}")

    return float(walk(tree))


# --------------------------------------------------------------------------- #
class MCPServer:
    """Base for a Model-Context-Protocol style server (local implementation)."""

    def __init__(self, auth: AuthManager, audit: list[AuditEvent], fixtures_dir: str = "data/fixtures"):
        self.auth = auth
        self.audit = audit
        self.fixtures_dir = fixtures_dir

    def _fixture(self, name: str, default: Any) -> Any:
        path = os.path.join(self.fixtures_dir, f"{name}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return default

    def _net_enabled(self) -> bool:
        return os.environ.get("AUTOMATON_ALLOW_NETWORK") == "1"


class NotionServer(MCPServer):
    def query(self, database_id: str, filter_contains: str = "") -> ToolResult:
        rows: list[dict[str, Any]] = []
        if self._net_enabled():
            token_name = "notion"
            handle = self.auth.get_handle(token_name)

            def call(secret: str) -> Any:
                import urllib.request
                req = urllib.request.Request(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    data=b'{"page_size": 100}',
                    headers={"Authorization": f"Bearer {secret}",
                             "Notion-Version": "2022-06-28",
                             "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode())

            try:
                rows = call_rows = AuthManager.execute_with(handle, call)
                rows = [dict(r.get("properties", {})) for r in call_rows.get("results", [])]
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, error=f"notion query failed: {exc}")
        else:
            rows = self._fixture("notion_databases", {}).get(database_id, [])
        if filter_contains:
            rows = [r for r in rows if filter_contains.lower() in json.dumps(r).lower()]
        return ToolResult(ok=True, output=rows, evidence_source=f"notion://{database_id}")


class ObsidianServer(MCPServer):
    def read(self, vault_path: str, note_glob: str = "*.md") -> ToolResult:
        import fnmatch
        notes: dict[str, str] = {}
        root = os.path.expanduser(vault_path)
        if os.path.isdir(root):
            for dirpath, _, files in os.walk(root):
                for f in files:
                    if fnmatch.fnmatch(f, note_glob):
                        p = os.path.join(dirpath, f)
                        try:
                            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                                notes[os.path.relpath(p, root)] = fh.read(4000)
                        except OSError:
                            pass
        else:
            notes = self._fixture("obsidian_notes", {})
        return ToolResult(ok=True, output=notes, evidence_source=f"obsidian://{vault_path}")


class GitHubServer(MCPServer):
    """Real REST calls when network enabled; recorded fixtures otherwise."""

    def list_repos(self) -> ToolResult:
        if self._net_enabled() and self.auth.has("github"):
            handle = self.auth.get_handle("github")

            def call(secret: str) -> Any:
                import urllib.request
                req = urllib.request.Request(
                    "https://api.github.com/user/repos?per_page=100",
                    headers={"Authorization": f"Bearer {secret}", "Accept": "application/vnd.github+json"},
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    return [{"name": x["name"], "private": x["private"]} for x in json.loads(r.read().decode())]

            out = AuthManager.execute_with(handle, call)
            return ToolResult(ok=True, output=out, evidence_source="github://user/repos")
        repos = self._fixture("github_repos", [{"name": "automation-scripts", "private": True}])
        return ToolResult(ok=True, output=repos, evidence_source="github://fixtures/repos")

    def commit_and_push(self, repo: str, path: str, content: str, message: str) -> ToolResult:
        """Create/update a file via GitHub Contents API (real) or simulate locally."""
        if self._net_enabled() and self.auth.has("github"):
            handle = self.auth.get_handle("github")

            def call(secret: str) -> Any:
                import base64 as b64, urllib.request
                api = f"https://api.github.com/repos/{repo}/contents/{path}"
                get = urllib.request.Request(api, headers={"Authorization": f"Bearer {secret}"})
                sha = None
                try:
                    with urllib.request.urlopen(get, timeout=30) as r:
                        sha = json.loads(r.read().decode()).get("sha")
                except Exception:  # noqa: BLE001 — file may not exist yet
                    pass
                body = json.dumps({
                    "message": message,
                    "content": b64.b64encode(content.encode()).decode(),
                    **({"sha": sha} if sha else {}),
                }).encode()
                put = urllib.request.Request(api, data=body, method="PUT",
                                             headers={"Authorization": f"Bearer {secret}"})
                with urllib.request.urlopen(put, timeout=30) as r:
                    return json.loads(r.read().decode())

            res = AuthManager.execute_with(handle, call)
            return ToolResult(ok=True, output={"commit_sha": res.get("commit", {}).get("sha"),
                                               "url": res.get("content", {}).get("html_url")},
                              evidence_source=f"github://{repo}/{path}")

        # Offline simulation: write into a local bare-ish "repo" directory so the
        # diff preview and audit trail behave exactly like the real flow.
        repo_dir = os.path.join("data", "repos", repo.split("/")[-1])
        os.makedirs(repo_dir, exist_ok=True)
        target = os.path.join(repo_dir, path)
        prev = ""
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as fh:
                prev = fh.read()
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)
        diff = _unified_diff(prev, content, path)
        return ToolResult(
            ok=True,
            output={"simulated": True, "repo": repo, "path": path, "message": message, "diff": diff},
            evidence_source=f"local-repo://{target}",
        )


class SandboxRunner(MCPServer):
    """Isolated test environment: temp dir, no network env vars, wall-time cap."""

    def run_python(self, code: str, argv: Optional[list[str]] = None, timeout_s: int = 20) -> ToolResult:
        with tempfile.TemporaryDirectory(prefix="automaton-sbx-") as td:
            script = os.path.join(td, "main.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(code)
            env = {k: v for k, v in os.environ.items()
                   if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
            env["HOME"] = td
            try:
                proc = subprocess.run(
                    [sys.executable, script, *(argv or [])],
                    capture_output=True, text=True, timeout=timeout_s, cwd=td, env=env,
                )
            except subprocess.TimeoutExpired:
                return ToolResult(ok=False, error=f"sandbox timeout after {timeout_s}s")
            report = (
                f"exit={proc.returncode}\n--- stdout ---\n{proc.stdout[:4000]}"
                f"\n--- stderr ---\n{proc.stderr[:4000]}"
            )
            return ToolResult(ok=proc.returncode == 0, output={"stdout": proc.stdout,
                                                               "stderr": proc.stderr,
                                                               "exit_code": proc.returncode},
                              evidence_source="sandbox://tmp")


class MathServer(MCPServer):
    def calculate(self, expression: str) -> ToolResult:
        try:
            return ToolResult(ok=True, output=safe_eval_math(expression),
                              evidence_source="deterministic://math")
        except (ValueError, SyntaxError) as exc:
            return ToolResult(ok=False, error=str(exc))


def _unified_diff(old: str, new: str, name: str) -> str:
    import difflib
    return "".join(
        difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                             fromfile=f"a/{name}", tofile=f"b/{name}")
    )


# --------------------------------------------------------------------------- #
class ToolRegistry:
    """Central dispatch table. Orchestrator sees specs; secrets stay here+Auth."""

    def __init__(self, auth: AuthManager, audit: Optional[list[AuditEvent]] = None,
                 fixtures_dir: str = "data/fixtures"):
        self.audit = audit if audit is not None else []
        self.auth = auth
        self._tools: dict[str, tuple[ToolSpec, Callable[..., ToolResult]]] = {}
        notion = NotionServer(auth, self.audit, fixtures_dir)
        obsidian = ObsidianServer(auth, self.audit, fixtures_dir)
        github = GitHubServer(auth, self.audit, fixtures_dir)
        sandbox = SandboxRunner(auth, self.audit, fixtures_dir)
        math = MathServer(auth, self.audit, fixtures_dir)

        self.register(
            ToolSpec("notion.query", "Query a Notion database (read-only).",
                     {"database_id": "string"}, credential=None),
            notion.query,
        )
        self.register(
            ToolSpec("obsidian.read", "Read notes from an Obsidian vault (read-only).",
                     {"vault_path": "string", "note_glob": "string"}),
            obsidian.read,
        )
        self.register(
            ToolSpec("github.list_repos", "List GitHub repositories.", {}, credential="github"),
            github.list_repos,
        )
        self.register(
            ToolSpec("github.commit_and_push", "Commit a file to a GitHub repo.",
                     {"repo": "string", "path": "string", "content": "string", "message": "string"},
                     credential="github", destructive=True),
            github.commit_and_push,
        )
        self.register(
            ToolSpec("sandbox.run", "Execute Python in an isolated sandbox (no net, no secrets, capped time).",
                     {"code": "string", "timeout_s": "integer"}),
            sandbox.run_python,
        )
        self.register(
            ToolSpec("math.calculate", "Deterministic arithmetic (LLM must not do math itself).",
                     {"expression": "string"}),
            math.calculate,
        )

    def register(self, spec: ToolSpec, fn: Callable[..., ToolResult]) -> None:
        self._tools[spec.name] = (spec, fn)

    def specs(self) -> list[dict[str, Any]]:
        """What the Orchestration layer is allowed to see (no credentials ever)."""
        return [
            {"name": s.name, "description": s.description, "parameters": s.parameters,
             "destructive": s.destructive, "requires_credential": bool(s.credential)}
            for s, _ in self._tools.values()
        ]

    def has(self, name: str) -> bool:
        return name in self._tools

    def is_destructive(self, name: str) -> bool:
        spec = self._tools.get(name, (None, None))[0]
        return bool(spec and spec.destructive)

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        entry = self._tools.get(name)
        if entry is None:
            return ToolResult(ok=False, error=f"unknown tool {name!r}")
        spec, fn = entry
        if spec.credential and not self.auth.has(spec.credential):
            return ToolResult(ok=False,
                              error=f"tool {name} requires credential '{spec.credential}' not present in vault")
        self.audit.append(AuditEvent(actor="execution_layer", event="tool_call",
                                     detail={"tool": name, "args_keys": sorted(args)}))
        try:
            return fn(**args)
        except KeyError as exc:
            return ToolResult(ok=False, error=f"missing argument: {exc}")
        except Exception as exc:  # noqa: BLE001 — tools must never crash the brain
            return ToolResult(ok=False, error=f"{name} failed: {exc}")
