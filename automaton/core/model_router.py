"""Pillar A — The Hybrid "Unlimited" Compute Stack.

Dual-engine routing:
  * LOCAL WORKHORSE  — Ollama / LM Studio open-weight models (Qwen Coder for
    code, Llama Maverick-class for bulk/general). Infinite queries, zero API
    cost, private data.
  * FRONTIER ORACLE  — proprietary heavyweights via a local hub (LibreChat /
    Docker router): Gemini-class for massive-context synthesis & multimodal
    planning; Claude-class for high-stakes safety review & low-hallucination
    reasoning.

If no local server or API key is configured, the router transparently falls
back to a deterministic ``StubModel`` so the whole automaton remains testable
offline. Set AUTOMATON_ALLOW_NETWORK=1 to enable real HTTP calls.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

# Tier names used across the system
LOCAL = "local"                 # bulk/coding workhorse
ORACLE_FRONTIER = "oracle_frontier"  # Gemini-class: big context, planning
ORACLE_SAFETY = "oracle_safety"      # Claude-class: review, low hallucination


@dataclass
class ModelProfile:
    tier: str
    name: str                       # e.g. "qwen2.5-coder:32b", "gemini-pro", "claude-review"
    base_url: str                   # ollama: http://localhost:11434 ; hub: http://localhost:3000/...
    api_key_env: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.2
    kind: str = "ollama"            # ollama | openai_compat | stub


@dataclass
class RouterConfig:
    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    allow_network: bool = False
    fallback_to_stub: bool = True

    @classmethod
    def default(cls) -> "RouterConfig":
        allow = os.environ.get("AUTOMATON_ALLOW_NETWORK", "0") == "1"
        cfg = cls(allow_network=allow)
        # Local workhorse (Ollama)
        cfg.profiles[LOCAL] = ModelProfile(
            tier=LOCAL,
            name=os.environ.get("AUTOMATON_LOCAL_MODEL", "qwen2.5-coder:32b"),
            base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            kind="ollama",
        )
        # Frontier oracle via local hub (LibreChat/OpenRouter-style OpenAI-compat endpoint)
        cfg.profiles[ORACLE_FRONTIER] = ModelProfile(
            tier=ORACLE_FRONTIER,
            name=os.environ.get("AUTOMATON_FRONTIER_MODEL", "gemini-class-planner"),
            base_url=os.environ.get("AUTOMATON_HUB_URL", "http://localhost:3000/v1"),
            api_key_env="AUTOMATON_FRONTIER_KEY",
            kind="openai_compat",
        )
        cfg.profiles[ORACLE_SAFETY] = ModelProfile(
            tier=ORACLE_SAFETY,
            name=os.environ.get("AUTOMATON_SAFETY_MODEL", "claude-class-reviewer"),
            base_url=os.environ.get("AUTOMATON_HUB_URL", "http://localhost:3000/v1"),
            api_key_env="AUTOMATON_SAFETY_KEY",
            kind="openai_compat",
        )
        return cfg


class StubModel:
    """Deterministic offline stand-in that behaves like each tier's model.

    It produces schema-valid JSON for planning/review prompts (deliberately
    malformed on the FIRST plan attempt to exercise the governance retry loop),
    and simple text otherwise. This keeps demos and CI fully hermetic.
    """

    def __init__(self, profile: ModelProfile):
        self.profile = profile
        self._plan_calls = 0

    def complete(self, prompt: str, **_: Any) -> str:
        p = prompt.lower()
        if "json schema" in p or "taskplan" in p or ("plan" in p and "objective" in p):
            self._plan_calls += 1
            plan = self._fake_plan(prompt)
            if self._plan_calls == 1:
                # simulate the classic local-model defect: trailing comma + prose
                return (
                    json.dumps(plan, indent=1).replace('"steps": [', '"steps": [,')
                    + "\n\nHope this helps!"
                )
            return json.dumps(plan)
        if "review" in p and ("security" in p or "flaw" in p or "verdict" in p):
            return json.dumps(
                {
                    "step_id": "reviewed",
                    "verdict": "pass",
                    "issues": [],
                    "recommendations": ["pin dependency versions"],
                    "reviewed_by": f"stub-{self.profile.name}",
                }
            )
        if "python" in p or "script" in p or "code" in p:
            return (
                "```python\n"
                "def summarize_workflows(rows):\n"
                '    """Return count of rows per database (deterministic, no guessing)."""\n'
                "    out = {}\n"
                "    for r in rows:\n"
                "        db = r.get('database', 'unknown')\n"
                "        out[db] = out.get(db, 0) + 1\n"
                "    return out\n\n"
                "if __name__ == '__main__':\n"
                "    print(summarize_workflows([{'database': 'ops'}, {'database': 'ops'}, {'database': 'crm'}]))\n"
                "```\n"
            )
        return f"[stub:{self.profile.tier}:{self.profile.name}] acknowledged: {prompt[:120]}"

    def _fake_plan(self, prompt: str) -> dict[str, Any]:
        objective = "task"
        for line in prompt.splitlines():
            if line.lower().startswith("objective"):
                objective = line.split(":", 1)[-1].strip()
                break
        return {
            "objective": objective,
            "reasoning": "Decomposed into query -> analyze -> implement -> validate -> sandbox -> approve -> deploy -> remember.",
            "estimated_risk": "medium",
            "domains": ["coding", "research"],
            "steps": [
                {"title": "Query Notion databases", "tool": "notion.query", "agent_role": "worker", "model_tier": "local"},
                {"title": "Analyze workflows for inefficiencies", "tool": None, "agent_role": "worker", "model_tier": "oracle_frontier"},
                {"title": "Write automation Python script", "tool": None, "agent_role": "coder", "model_tier": "local"},
                {"title": "Security & logic review", "tool": None, "agent_role": "reviewer", "model_tier": "oracle_safety"},
                {"title": "Sandbox tests", "tool": "sandbox.run", "agent_role": "worker", "model_tier": "local"},
                {"title": "Deploy to GitHub", "tool": "github.commit_and_push", "agent_role": "worker", "model_tier": "local"},
            ],
        }


class ModelRouter:
    """Routes a completion request to the right engine for the task tier."""

    def __init__(self, config: Optional[RouterConfig] = None):
        self.config = config or RouterConfig.default()
        self._stubs: dict[str, StubModel] = {}
        self.last_tier_used: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    def _stub(self, profile: ModelProfile) -> StubModel:
        if profile.tier not in self._stubs:
            self._stubs[profile.tier] = StubModel(profile)
        return self._stubs[profile.tier]

    def complete(self, tier: str, prompt: str, **kw: Any) -> str:
        profile = self.config.profiles.get(tier) or self.config.profiles[LOCAL]
        if self.config.allow_network:
            try:
                out = self._remote(profile, prompt, **kw)
                self.last_tier_used[tier] = f"remote:{profile.name}"
                return out
            except Exception as exc:  # noqa: BLE001 — fall back, never crash the automaton
                if not self.config.fallback_to_stub:
                    raise
                self.last_tier_used[tier] = f"stub-fallback({exc.__class__.__name__})"
        else:
            self.last_tier_used[tier] = f"stub:{profile.name}"
        return self._stub(profile).complete(prompt, **kw)

    # -- real backends ---------------------------------------------------- #
    def _remote(self, profile: ModelProfile, prompt: str, **kw: Any) -> str:
        if profile.kind == "ollama":
            return self._ollama(profile, prompt, **kw)
        return self._openai_compat(profile, prompt, **kw)

    def _ollama(self, profile: ModelProfile, prompt: str, **kw: Any) -> str:
        payload = {
            "model": profile.name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": kw.get("temperature", profile.temperature)},
        }
        req = urllib.request.Request(
            f"{profile.base_url.rstrip('/')}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=kw.get("timeout", 120)) as resp:
            data = json.loads(resp.read().decode())
        return data.get("response", "")

    def _openai_compat(self, profile: ModelProfile, prompt: str, **kw: Any) -> str:
        key = os.environ.get(profile.api_key_env or "", "")
        payload = {
            "model": profile.name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": kw.get("max_tokens", profile.max_tokens),
            "temperature": kw.get("temperature", profile.temperature),
        }
        req = urllib.request.Request(
            f"{profile.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=kw.get("timeout", 120)) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------ #
    def route_for_role(self, agent_role: str, task_kind: str) -> str:
        """Coding/bulk → local workhorse; planning/synthesis → frontier; review → safety."""
        if agent_role == "reviewer" or task_kind == "safety_review":
            return ORACLE_SAFETY
        if agent_role == "planner" or task_kind in ("synthesis", "multimodal_plan"):
            return ORACLE_FRONTIER
        return LOCAL


def build_router(config: Optional[RouterConfig] = None) -> ModelRouter:
    return ModelRouter(config)
