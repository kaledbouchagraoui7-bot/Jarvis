"""Orchestration Layer (The Brain) — LangGraph-style goal decomposition & routing.

Responsibilities:
  * Plan: ask the frontier planner for a TaskPlan (Pydantic-validated).
  * Route: local workhorse for code/bulk, frontier oracle for synthesis,
    safety oracle for review.
  * NEVER touch credentials: it dispatches tool NAMES through the Execution
    Layer and only ever sees tool specs without secrets.
  * Respect governance: every step passes the Policy Engine before execution;
    risky steps park at the Approval Gateway.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import ValidationError

from .types import (
    AuditEvent,
    Domain,
    EvidenceCitation,
    PlannedStep,
    ReviewReport,
    RiskLevel,
    StepStatus,
    TaskPlan,
    TaskRequest,
)
from ..memory.store import MemoryStore, RetrievalCompiler, WorkingMemory
from ..memory.store import MemoryKind

_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(text: str) -> str:
    m = _FENCE.search(text)
    return (m.group(1) if m else text).strip()


class Orchestrator:
    """Plans and decomposes. Concrete step execution lives in automaton.engine."""

    def __init__(
        self,
        router,                      # ModelRouter
        memory: MemoryStore,
        compiler: RetrievalCompiler,
        working: WorkingMemory,
        parser_factory,              # callable(schema) -> PydanticOutputParser
        audit: Optional[list[AuditEvent]] = None,
    ):
        self.router = router
        self.memory = memory
        self.compiler = compiler
        self.working = working
        self.make_parser = parser_factory
        self.audit = audit if audit is not None else []

    # ------------------------------------------------------------------ #
    def plan_prompt(self, request: TaskRequest, tool_specs: list[dict[str, Any]]) -> str:
        evidence = self.compiler.compile(request.objective)
        schema = TaskPlan.model_json_schema()
        return (
            "You are the Orchestration Brain of a sovereign local-first automaton.\n"
            f"Objective: {request.objective}\n\n"
            "Available MCP tools (names + params ONLY — you never see credentials):\n"
            f"{json.dumps(tool_specs, indent=1)}\n\n"
            "Externalized memory relevant to this objective:\n"
            f"{evidence}\n\n"
            "Decompose the objective into ordered steps. Assign each step an "
            "agent_role (planner|coder|reviewer|worker), a model_tier "
            "(local|oracle_frontier|oracle_safety) and a tool when needed.\n"
            "Return ONLY JSON matching this JSON Schema:\n"
            f"{json.dumps(schema)}\n"
        )

    def create_plan(self, request: TaskRequest, tool_specs: list[dict[str, Any]]) -> TaskPlan:
        prompt = self.plan_prompt(request, tool_specs)
        llm = lambda p: self.router.complete("oracle_frontier", p)  # planning = frontier tier
        parser = self.make_parser(TaskPlan)
        plan = parser.parse_with_retry(llm, prompt)
        plan = self._sanitize_plan(plan, {s["name"] for s in tool_specs})
        self.working.set(f"plan:{request.id}", plan.model_dump())
        self.audit.append(
            AuditEvent(actor="orchestration", event="plan_created",
                       detail={"task": request.id, "steps": len(plan.steps)})
        )
        return plan

    def _sanitize_plan(self, plan: TaskPlan, known_tools: set[str]) -> TaskPlan:
        """Deterministic guardrails on top of schema validation."""
        valid_steps: list[PlannedStep] = []
        for step in plan.steps:
            if step.tool and step.tool not in known_tools:
                self.audit.append(
                    AuditEvent(actor="orchestration", event="hallucinated_tool_dropped",
                               detail={"step": step.title, "tool": step.tool})
                )
                step.tool = None
            valid_steps.append(step)
        plan.steps = valid_steps
        if not plan.domains:
            plan.domains = [Domain.CODING]
        return plan

    # ------------------------------------------------------------------ #
    def synthesize(self, query: str, context: str) -> str:
        """Massive-context synthesis goes to the Gemini-class frontier oracle."""
        prompt = (
            f"Evidence (cited sources only; mark assumptions with [ASSUMPTION]):\n{context}\n\n"
            f"Question: {query}\nAnswer concisely with inline citations."
        )
        return self.router.complete("oracle_frontier", prompt)

    def implement(self, spec: str, context: str) -> str:
        """Code generation runs on the LOCAL workhorse (Qwen Coder). Free, private, unlimited."""
        prompt = (
            "You are Qwen-Coder, the local workhorse. Write clean Python with a "
            "self-test under `if __name__ == '__main__':`. No network calls, no secrets.\n\n"
            f"Context:\n{context[:2500]}\n\nTask: {spec}\nReturn the code in a python fence."
        )
        raw = self.router.complete("local", prompt)
        return extract_code(raw)

    def review(self, step_id: str, code: str) -> ReviewReport:
        """High-stakes safety review goes to the Claude-class oracle."""
        prompt = (
            "Perform a security and logic review. Identify injection risks, secret "
            "leaks, destructive operations and correctness flaws. Return JSON "
            "matching ReviewReport schema with fields step_id, verdict(pass|warn|block), "
            "issues[], recommendations[].\n"
            f"step_id: {step_id}\n```\n{code[:6000]}\n```"
        )
        llm = lambda p: self.router.complete("oracle_safety", p)
        parser = self.make_parser(ReviewReport)
        try:
            report = parser.parse_with_retry(llm, prompt)
        except ValidationError:
            report = ReviewReport(step_id=step_id, verdict="block",
                                  issues=["reviewer output failed contract"])
        report.step_id = step_id
        return report
