"""Policy Decision Engine — deterministic governance from the Personal Constitution.

Classifies every request by risk, reversibility and domain BEFORE execution,
returning one of: AUTONOMOUS / APPROVAL_REQUIRED / DENIED. This engine is pure
and deterministic: no LLM involved, so it cannot be prompt-injected.
"""
from __future__ import annotations

import fnmatch
from typing import Any, Iterable, Optional

from .constitution import load_constitution
from .types import (
    Domain,
    PolicyDecision,
    PolicyVerdict,
    Reversibility,
    RiskLevel,
)

_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

_DECISION_FOR_RISK = {
    RiskLevel.LOW: PolicyDecision.AUTONOMOUS,
    RiskLevel.MEDIUM: PolicyDecision.APPROVAL_REQUIRED,
    RiskLevel.HIGH: PolicyDecision.APPROVAL_REQUIRED,
    RiskLevel.CRITICAL: PolicyDecision.DENIED,
}


class ActionDescriptor:
    """Normalized description of a prospective action for policy evaluation."""

    def __init__(
        self,
        text: str = "",
        tools: Optional[Iterable[str]] = None,
        domains: Optional[Iterable[str]] = None,
        paths: Optional[Iterable[str]] = None,
        reversible: Optional[bool] = None,
    ):
        self.text = (text or "").lower()
        self.tools = [t.lower() for t in (tools or [])]
        self.domains = [d.lower() for d in (domains or [])]
        self.paths = [p for p in (paths or [])]
        self.reversible = reversible


class PolicyEngine:
    def __init__(self, constitution: Optional[dict[str, Any]] = None, path: Optional[str] = None):
        self.constitution = constitution or load_constitution(path)
        self.rules: list[dict[str, Any]] = self.constitution.get("rules", [])
        self.defaults = self.constitution.get("defaults", {})
        self._used_tools: set[str] = set()  # first-use tracking (constitutional default)

    # ------------------------------------------------------------------ #
    def _rule_matches(self, rule: dict[str, Any], action: ActionDescriptor) -> tuple[bool, list[str]]:
        crit = rule.get("match", {}) or {}
        reasons: list[str] = []
        if not crit:
            return False, reasons

        matched_any = False

        if "domain" in crit:
            want = {str(d).lower() for d in crit["domain"]}
            hit = want.intersection(set(action.domains))
            if not hit and want.intersection(self._infer_domains(action.text)):
                hit = want & self._infer_domains(action.text)
            if hit:
                matched_any = True
                reasons.append(f"domain:{','.join(sorted(hit))}")
            elif action.domains:
                return False, []  # explicit domains given but none match this rule

        if "keywords" in crit:
            hits = [k for k in crit["keywords"] if str(k).lower() in action.text]
            if hits:
                matched_any = True
                reasons.append(f"keywords:{','.join(hits)}")
            elif "domain" not in crit:
                return False, []
            else:
                # keyword required alongside domain? treat as AND when both present
                if not any(str(k).lower() in action.text for k in crit["keywords"]):
                    return False, []

        if "tools" in crit:
            want = {str(t).lower() for t in crit["tools"]}
            hit = {t for t in action.tools if any(fnmatch.fnmatch(t, w) for w in want)}
            if hit:
                matched_any = True
                reasons.append(f"tools:{','.join(sorted(hit))}")
            elif not any(c in crit for c in ("domain", "keywords", "paths")):
                return False, []
            elif action.tools and not hit:
                # tool-scoped rule that doesn't include our tools → no match
                if crit.get("domain") is None and crit.get("keywords") is None:
                    return False, []

        if "paths" in crit:
            want = [str(p) for p in crit["paths"]]
            hit = [p for p in action.paths if any(fnmatch.fnmatch(p, w) for w in want)]
            if hit:
                matched_any = True
                reasons.append(f"paths:{','.join(hit)}")
            elif not any(c in crit for c in ("domain", "keywords", "tools")):
                return False, []

        return matched_any, reasons

    @staticmethod
    def _infer_domains(text: str) -> set[str]:
        table = {
            Domain.FINANCIAL: ["trade", "trading", "stock", "buy ", "sell ", "portfolio", "fund", "wire", "payment"],
            Domain.COMMUNICATION: ["email", "send message", "post to", "dm ", "tweet", "notify", "gmail", "instagram"],
            Domain.CODING: ["code", "script", "python", "deploy", "commit", "push", "refactor", "repository", "github"],
            Domain.RESEARCH: ["research", "summarize", "analyze", "find", "review", "investigate"],
            Domain.DATA: ["database", "query", "export", "backup", "delete data"],
            Domain.INFRASTRUCTURE: ["server", "docker", "dns", "firewall", "cron"],
        }
        out: set[str] = set()
        for dom, needles in table.items():
            if any(n in text for n in needles):
                out.add(dom.value)
        return out

    # ------------------------------------------------------------------ #
    _READ_ONLY_TOOLS = {
        "notion.query", "obsidian.read", "github.list_repos", "github.read_file",
        "fs.read", "math.calculate", "sandbox.run",
    }

    def evaluate(self, task_id: str, action: ActionDescriptor) -> PolicyVerdict:
        # Deterministic override: a step whose ONLY tools are read-only/deterministic
        # is LOW risk no matter what domains the overall plan carries.
        if action.tools and all(t in self._READ_ONLY_TOOLS for t in action.tools):
            return PolicyVerdict(
                task_id=task_id,
                decision=PolicyDecision.AUTONOMOUS,
                risk=RiskLevel.LOW,
                reversibility=Reversibility.REVERSIBLE,
                domain=Domain(action.domains[0]) if action.domains else Domain.DATA,
                reasons=["read-only/deterministic tool set (constitutional LOW class)"],
                matched_rules=["R-005"],
            )

        best: Optional[tuple[dict[str, Any], list[str]]] = None
        for rule in self.rules:
            ok, reasons = self._rule_matches(rule, action)
            if not ok:
                continue
            risk = RiskLevel(rule.get("risk", "medium"))
            if best is None or _RISK_ORDER[risk] > _RISK_ORDER[RiskLevel(best[0].get("risk", "medium"))]:
                best = (rule, reasons)

        if best is not None:
            rule, reasons = best
            risk = RiskLevel(rule.get("risk", "medium"))
            decision = PolicyDecision(rule.get("decision", self.defaults.get("unknown_action_decision", "approval")))
            reversibility = Reversibility(rule.get("reversibility", "semi_reversible"))
            domain = Domain((action.domains[0] if action.domains else sorted(self._infer_domains(action.text) or {"other"})[0]))
            return PolicyVerdict(
                task_id=task_id,
                decision=decision,
                risk=risk,
                reversibility=reversibility,
                domain=domain,
                reasons=[rule.get("description", rule.get("id", ""))] + reasons,
                matched_rules=[rule.get("id", "?")],
            )

        # ---- no rule matched: deterministic fallbacks ------------------- #
        inferred = self._infer_domains(action.text)
        domain = Domain(next(iter(inferred))) if inferred else Domain.OTHER
        # irreversible-looking actions escalate even without a rule
        if action.reversible is False:
            risk, decision = RiskLevel.HIGH, PolicyDecision.APPROVAL_REQUIRED
            reason = "no rule matched and action flagged irreversible"
        else:
            fallback = self.defaults.get("unknown_action_decision", "approval")
            decision = PolicyDecision(fallback)
            risk = RiskLevel.MEDIUM if decision == PolicyDecision.APPROVAL_REQUIRED else RiskLevel.LOW
            reason = "no rule matched; constitutional default applied"

        # first-ever use of an unknown tool escalates per defaults.new_tool_first_use
        if action.tools:
            new = [t for t in action.tools if t not in self._used_tools]
            if new and self.defaults.get("new_tool_first_use", "approval") == "approval" \
               and _RISK_ORDER[risk] < _RISK_ORDER[RiskLevel.MEDIUM]:
                risk, decision = RiskLevel.MEDIUM, PolicyDecision.APPROVAL_REQUIRED
                reason += "; first use of tool(s) " + ",".join(new)

        return PolicyVerdict(
            task_id=task_id,
            decision=decision,
            risk=risk,
            reversibility=Reversibility.SEMI_REVERSIBLE,
            domain=domain,
            reasons=[reason],
            matched_rules=[],
        )

    def record_tool_use(self, tools: Iterable[str]) -> None:
        self._used_tools.update(t.lower() for t in tools)

    # ------------------------------------------------------------------ #
    def check_hallucination_policy(self, output_text: str, has_citations: bool) -> list[str]:
        """Deterministic lint enforcing the constitution's hallucination-control clause."""
        hc = self.constitution.get("hallucination_control", {})
        violations: list[str] = []
        low = output_text.lower()
        hedged_guesses = ["i believe", "probably", "as far as i know", "i think that"]
        if hc.get("forbid_guessing") and any(h in low for h in hedged_guesses) and not has_citations:
            violations.append("unhedged guess detected without evidence citations")
        if hc.get("factual_claims_must") == "cite_evidence" and not has_citations:
            violations.append("factual output missing evidence citations")
        return violations
