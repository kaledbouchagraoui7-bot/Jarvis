"""Shared domain types for the Automaton (Governance Layer contracts).

Every structured LLM output in this system is modeled as a Pydantic schema.
The Governance layer wraps all parsing in ``PydanticOutputParser`` so malformed
model output never reaches databases or tools.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Risk & policy
# --------------------------------------------------------------------------- #
class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    SEMI_REVERSIBLE = "semi_reversible"
    IRREVERSIBLE = "irreversible"


class Domain(str, Enum):
    RESEARCH = "research"
    CODING = "coding"
    COMMUNICATION = "communication"
    FINANCIAL = "financial"
    DATA = "data"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"


class PolicyDecision(str, Enum):
    AUTONOMOUS = "autonomous"          # execute without asking
    APPROVAL_REQUIRED = "approval"     # route to Approval Gateway
    DENIED = "denied"                  # hard-blocked by constitution


class Verdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class StepStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


class MemoryKind(str, Enum):
    WORKING = "working"        # active context window state
    EPISODIC = "episodic"      # what happened, when, outcomes
    SEMANTIC = "semantic"      # durable facts / knowledge
    PROJECT_STATE = "project"  # long-horizon project state


# --------------------------------------------------------------------------- #
# Requests, plans, steps
# --------------------------------------------------------------------------- #
class TaskRequest(BaseModel):
    """A command issued to the automaton."""

    id: str = Field(default_factory=lambda: _id("task"))
    objective: str
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class PlannedStep(BaseModel):
    """One decomposed sub-task produced by the Orchestration layer."""

    id: str = Field(default_factory=lambda: _id("step"))
    title: str
    description: str = ""
    tool: Optional[str] = None                 # MCP tool name, if any
    agent_role: str = "worker"                 # planner | coder | reviewer | worker
    model_tier: str = "local"                  # local | oracle_frontier | oracle_safety
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PLANNED
    result: Any = None
    error: Optional[str] = None


class TaskPlan(BaseModel):
    """Structured plan emitted by the planner model (Pydantic-validated)."""

    id: str = Field(default_factory=lambda: _id("plan"))
    objective: str
    reasoning: str = ""
    steps: list[PlannedStep] = Field(default_factory=list)
    estimated_risk: RiskLevel = RiskLevel.LOW
    domains: list[Domain] = Field(default_factory=list)


class PolicyVerdict(BaseModel):
    """Deterministic output of the Policy Decision Engine."""

    task_id: str
    decision: PolicyDecision
    risk: RiskLevel
    reversibility: Reversibility
    domain: Domain
    reasons: list[str] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    evaluated_at: str = Field(default_factory=_now)


class ReviewReport(BaseModel):
    """Structured safety/logic review from the Frontier safety reviewer."""

    step_id: str
    verdict: Verdict = Verdict.PASS
    issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    reviewed_by: str = "frontier-safety"


class EvidenceCitation(BaseModel):
    claim: str
    source: str
    verified: bool = False


class ApprovalRequest(BaseModel):
    """Presented to the human at the Approval Gateway."""

    id: str = Field(default_factory=lambda: _id("appr"))
    task_id: str
    step_ids: list[str] = Field(default_factory=list)
    action_summary: str
    risk_summary: str
    worst_case: str
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    diff_preview: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    created_at: str = Field(default_factory=_now)


class ExecutionResult(BaseModel):
    step_id: str
    ok: bool
    output: Any = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    sandbox_report: Optional[str] = None
    duration_ms: int = 0


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: _id("evt"))
    ts: str = Field(default_factory=_now)
    actor: str
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)
