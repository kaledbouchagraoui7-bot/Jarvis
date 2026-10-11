"""The Automaton Engine — end-to-end operational workflow (the 7-stage pipeline).

  1. Risk Classification   (Policy Engine, deterministic)
  2. Planning              (Frontier oracle via Orchestration, Pydantic-validated)
  3. Implementation        (Local workhorse Qwen Coder / MCP tool calls)
  4. Validation            (PydanticOutputParser + Claude-class reviewer)
  5. Sandboxing            (isolated subprocess execution)
  6. Approval Gateway      (Human-in-the-Loop for medium+ risk)
  7. Execution + Memory    (AuthManager injects PAT at the last microsecond;
                            outcome logged to Episodic Memory)
"""
from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .core.approval import ApprovalGateway
from .core.auth_manager import AuthManager
from .core.governance import OutputContractError, PydanticOutputParser, redact_secrets
from .core.model_router import ModelRouter
from .core.orchestrator import Orchestrator
from .core.policy_engine import ActionDescriptor, PolicyEngine
from .core.types import (
    ApprovalStatus,
    AuditEvent,
    Domain,
    EvidenceCitation,
    ExecutionResult,
    MemoryKind,
    PlannedStep,
    PolicyDecision,
    ReviewReport,
    StepStatus,
    TaskPlan,
    TaskRequest,
    Verdict,
)
from .memory.store import MemoryStore, RetrievalCompiler, WorkingMemory
from .tools.registry import ToolRegistry


@dataclass
class TaskRun:
    request: TaskRequest
    plan: Optional[TaskPlan] = None
    results: dict[str, ExecutionResult] = field(default_factory=dict)
    approvals: list[str] = field(default_factory=list)
    denied_reason: Optional[str] = None
    completed: bool = False
    # injected by the engine so status/resume can query the gateway
    gateway: Optional[Any] = field(default=None, repr=False)

    @property
    def status(self) -> str:
        if self.denied_reason:
            return "denied"
        if self.gateway and any(
            self.gateway.get(a).status == ApprovalStatus.PENDING for a in self.approvals
        ):
            return "awaiting_approval"
        return "completed" if self.completed else "in_progress"


class Automaton:
    def __init__(
        self,
        constitution_path: Optional[str] = None,
        router: Optional[ModelRouter] = None,
        auth: Optional[AuthManager] = None,
        memory: Optional[MemoryStore] = None,
        vault_path: str = "data/vault.json",
        memory_path: str = "data/memory.sqlite",
        fixtures_dir: str = "data/fixtures",
    ):
        self.audit: list[AuditEvent] = []
        self.router = router or ModelRouter()
        self.auth = auth or AuthManager(vault_path=vault_path, audit_log=self.audit)
        self.memory = memory or MemoryStore(db_path=memory_path)
        self.compiler = RetrievalCompiler(self.memory)
        self.working = WorkingMemory()
        self.policy = PolicyEngine(path=constitution_path)
        self.gateway = ApprovalGateway(audit_log=self.audit)
        self.tools = ToolRegistry(self.auth, audit=self.audit, fixtures_dir=fixtures_dir)
        self.parser_for = lambda schema: PydanticOutputParser(schema, max_retries=3, audit_log=self.audit)
        self.brain = Orchestrator(
            self.router, self.memory, self.compiler, self.working,
            self.parser_for, audit=self.audit,
        )

    # ------------------------------------------------------------------ #
    def log(self, actor: str, event: str, **detail: Any) -> None:
        safe = {k: redact_secrets(str(v))[:500] for k, v in detail.items()}
        self.audit.append(AuditEvent(actor=actor, event=event, detail=safe))

    # ------------------------------------------------------------------ #
    def submit(self, objective: str, context: Optional[dict[str, Any]] = None) -> TaskRun:
        """Run the full pipeline. Returns immediately if approval is required."""
        request = TaskRequest(objective=objective, context=context or {})
        run = TaskRun(request=request, gateway=self.gateway)
        self.log("engine", "task_submitted", task=request.id, objective=redact_secrets(objective))

        # ---- Stage 1: Risk Classification (deterministic, pre-planning) -- #
        verdict = self.policy.evaluate(
            request.id,
            ActionDescriptor(text=objective, domains=(context or {}).get("domains")),
        )
        self.log("policy_engine", "classified", task=request.id,
                 decision=verdict.decision.value, risk=verdict.risk.value,
                 rules=",".join(verdict.matched_rules))
        if verdict.decision == PolicyDecision.DENIED:
            run.denied_reason = f"Constitution denied: {'; '.join(verdict.reasons)}"
            self.memory.add(MemoryKind.EPISODIC,
                            f"DENIED task '{redact_secrets(objective)[:200]}' — {run.denied_reason}",
                            {"task": request.id})
            return run

        # ---- Stage 2: Planning (frontier oracle, governance-wrapped) ----- #
        try:
            run.plan = self.brain.create_plan(request, self.tools.specs())
        except OutputContractError as exc:
            run.denied_reason = f"planner failed output contract: {exc}"
            self.log("governance", "plan_contract_failure", error=str(exc))
            return run

        # Re-classify with the concrete tools the plan intends to use.
        planned_tools = [s.tool for s in run.plan.steps if s.tool]
        verdict = self.policy.evaluate(
            request.id,
            ActionDescriptor(text=objective, tools=planned_tools,
                             domains=[d.value for d in run.plan.domains]),
        )
        self.policy.record_tool_use(planned_tools)
        self.log("policy_engine", "reclassified_with_plan", task=request.id,
                 decision=verdict.decision.value, risk=verdict.risk.value)

        # ---- Execute steps in order -------------------------------------- #
        for step in run.plan.steps:
            ok = self._execute_step(run, step, verdict)
            if not ok:
                break
        else:
            run.completed = True

        # ---- Stage 8: Memory Update -------------------------------------- #
        if run.completed:
            summary = f"COMPLETED task '{redact_secrets(objective)[:160]}' with {len(run.results)} steps."
            self.memory.add(MemoryKind.EPISODIC, summary, {"task": request.id})
            self.memory.add(MemoryKind.PROJECT_STATE,
                            f"project state after '{redact_secrets(objective)[:80]}': "
                            + "; ".join(s.title + "=" + s.status.value for s in run.plan.steps),
                            {"task": request.id})
            self.log("engine", "task_completed", task=request.id)
        return run

    # ------------------------------------------------------------------ #
    def resume(self, run: TaskRun) -> TaskRun:
        """Continue a run parked at the Approval Gateway once decisions land.

        Iterates to a fixpoint so that after one approval clears, subsequent
        gated steps park (or execute) in plan order.
        """
        if run.plan is None:
            return run
        progressed = True
        while progressed:
            progressed = self._resume_pass(run)
        if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in run.plan.steps):
            run.completed = True
            self.memory.add(MemoryKind.EPISODIC,
                            f"COMPLETED (after approval) '{redact_secrets(run.request.objective)[:160]}'",
                            {"task": run.request.id})
            self.log("engine", "task_completed_after_approval", task=run.request.id)
        return run

    def _resume_pass(self, run: TaskRun) -> bool:
        """One sweep over the plan; returns True if any step changed status."""
        changed = False
        for step in run.plan.steps:
            if step.status == StepStatus.AWAITING_APPROVAL:
                appr_id = step.result.get("approval_id") if isinstance(step.result, dict) else None
                if appr_id and self.gateway.is_approved(appr_id):
                    step.status = StepStatus.PLANNED
                    step.result = {"approved": True}   # marker consumed by _already_approved
                    changed = True
                    self._execute_step(run, step, self.policy.evaluate(
                        run.request.id,
                        ActionDescriptor(text=run.request.objective,
                                         tools=[step.tool] if step.tool else [],
                                         domains=[d.value for d in (run.plan.domains if run.plan else [])]),
                    ))
                elif appr_id and self.gateway.get(appr_id).status.value in ("rejected", "expired"):
                    step.status = StepStatus.SKIPPED
                    changed = True
                    self.log("engine", "step_rejected_by_human", step=step.id)
        return changed

    # ------------------------------------------------------------------ #
    def _execute_step(self, run: TaskRun, step: PlannedStep, verdict) -> bool:
        """Returns False if the pipeline must halt (failure or pending approval)."""
        t0 = time.time()
        request = run.request

        # Gate: does THIS step need approval?
        step_verdict = verdict
        if step.tool:
            step_verdict = self.policy.evaluate(
                request.id,
                ActionDescriptor(
                    text=f"{step.title} {step.description}",
                    tools=[step.tool],
                    domains=[d.value for d in run.plan.domains] if run.plan else None,
                    reversible=not self.tools.is_destructive(step.tool),
                ),
            )
        needs_approval = step_verdict.decision == PolicyDecision.APPROVAL_REQUIRED
        if step_verdict.decision == PolicyDecision.DENIED:
            step.status = StepStatus.SKIPPED
            self.log("policy_engine", "step_denied", step=step.id,
                     reason="; ".join(step_verdict.reasons))
            return True  # skipping is fine; pipeline continues

        step.status = StepStatus.RUNNING

        # ---- Stage 3: Implementation ------------------------------------- #
        try:
            if step.agent_role == "coder":
                evidence = self.compiler.compile(step.title)
                code = self.brain.implement(step.description or step.title, evidence)

                # ---- Stage 4a: Governance validation of structure -------- #
                # Deterministic lint: parseable Python, no secret-shaped strings.
                compile(code, "<generated>", "exec")  # syntax gate
                from .core.governance import redact_secrets as _r
                sanitized = _r(code)
                if sanitized != code:
                    raise ValueError("generated code contained credential-shaped content")

                # ---- Stage 4b: Claude-class safety review ----------------- #
                report = self.brain.review(step.id, code)
                if report.verdict == Verdict.BLOCK:
                    step.status = StepStatus.FAILED
                    step.error = f"blocked by safety reviewer: {report.issues}"
                    self.log("governance", "review_block", step=step.id, issues=str(report.issues))
                    return False

                # ---- Stage 5: Sandboxing ---------------------------------- #
                sandbox = self.tools.call("sandbox.run", {"code": code, "timeout_s": 15})
                step.result = {"code": code, "review": report.model_dump(),
                               "sandbox_ok": sandbox.ok,
                               "sandbox_report": (sandbox.output or {}).get("stdout", "") if sandbox.ok else sandbox.error}
                if not sandbox.ok:
                    step.status = StepStatus.FAILED
                    step.error = f"sandbox failure: {sandbox.error or sandbox.output}"
                    self.log("engine", "sandbox_failed", step=step.id)
                    return False

            elif step.tool:
                args = self._tool_args(step, run)
                if args is None:
                    step.status = StepStatus.SKIPPED
                    return True
                if needs_approval and not self._already_approved(run, step):
                    return self._park_for_approval(run, step, step_verdict,
                                                   action_summary=f"{step.title}: call {step.tool}({sorted(args)})",
                                                   worst_case=self._worst_case(step),
                                                   diff_preview=args.get("diff_preview", ""))
                exec_args = {k: v for k, v in args.items() if k != "diff_preview"}
                res = self.tools.call(step.tool, exec_args)
                if not res.ok:
                    step.status = StepStatus.FAILED
                    step.error = res.error
                    self.log("execution", "tool_failed", step=step.id, tool=step.tool, error=res.error)
                    return False
                step.result = {"output": res.output, "source": res.evidence_source}
                if res.evidence_source:
                    self.memory.add(
                        MemoryKind.SEMANTIC,
                        f"Fact from {res.evidence_source}: {str(res.output)[:300]}",
                        {"verified": True, "step": step.id},
                    )
                if step.tool == "github.commit_and_push" and isinstance(res.output, dict):
                    self.log("execution", "deployed", step=step.id,
                             commit=str(res.output.get("commit_sha", "simulated")))
            else:
                # synthesis / analysis on the routed tier
                tier = step.model_tier or self.router.route_for_role(step.agent_role, "synthesis")
                evidence = self.compiler.compile(f"{step.title} {request.objective}")
                out = self.router.complete(tier, f"{step.description or step.title}\n\nEvidence:\n{evidence}\n\nProduce the analysis result.")
                step.result = {"analysis": out[:4000]}

        except Exception as exc:  # noqa: BLE001 — one bad step must not crash the automaton
            step.status = StepStatus.FAILED
            step.error = redact_secrets(str(exc))
            self.log("engine", "step_error", step=step.id, error=step.error)
            return False

        step.status = StepStatus.DONE
        dur = int((time.time() - t0) * 1000)
        run.results[step.id] = ExecutionResult(step_id=step.id, ok=True, output=step.result, duration_ms=dur)
        self.log("engine", "step_done", step=step.id, ms=dur)
        return True

    # ------------------------------------------------------------------ #
    def _tool_args(self, step: PlannedStep, run: TaskRun) -> Optional[dict[str, Any]]:
        """Deterministically bind arguments for known tools from prior results."""
        req_ctx = run.request.context
        if step.tool == "notion.query":
            return {"database_id": req_ctx.get("notion_database", "db-ops-workflows")}
        if step.tool == "obsidian.read":
            return {"vault_path": req_ctx.get("obsidian_vault", "~/ObsidianVault")}
        if step.tool == "github.list_repos":
            return {}
        if step.tool == "sandbox.run":
            code = self._latest_artifact(run, "code")
            if not code:
                return None
            return {"code": code, "timeout_s": 15}
        if step.tool == "github.commit_and_push":
            code = self._latest_artifact(run, "code")
            if not code:
                return None
            repo = req_ctx.get("github_repo", "me/automation-scripts")
            path = req_ctx.get("target_path", "scripts/workflow_automator.py")
            prev = self._repo_file_preview(repo, path)
            return {
                "repo": repo,
                "path": path,
                "content": code,
                "message": f"auto: {step.title} [via automaton {run.request.id}]",
                "diff_preview": "".join(
                    difflib.unified_diff(prev.splitlines(keepends=True), code.splitlines(keepends=True),
                                         fromfile=f"a/{path}", tofile=f"b/{path}")
                ),
            }
        if step.tool == "math.calculate":
            expr = req_ctx.get("expression")
            return {"expression": expr} if expr else None
        return {}

    @staticmethod
    def _latest_artifact(run: TaskRun, key: str) -> Optional[str]:
        if not run.plan:
            return None
        for s in reversed(run.plan.steps):
            if isinstance(s.result, dict) and key in s.result:
                return s.result[key]
        return None

    @staticmethod
    def _repo_file_preview(repo: str, path: str) -> str:
        import os
        p = os.path.join("data", "repos", repo.split("/")[-1], path)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as fh:
                return fh.read()
        return ""

    @staticmethod
    def _worst_case(step: PlannedStep) -> str:
        if step.tool == "github.commit_and_push":
            return "Wrong code lands on the default branch; CI breaks; revert requires git history rewrite or manual revert commit."
        if step.tool and step.tool.startswith(("gmail", "instagram", "slack")):
            return "An unintended message reaches external recipients; reputational damage; cannot be unsent."
        return "Unintended side effect occurs; recovery cost unknown until inspected."

    # ------------------------------------------------------------------ #
    def _already_approved(self, run: TaskRun, step: PlannedStep) -> bool:
        return isinstance(step.result, dict) and step.result.get("approved") is True

    def _park_for_approval(self, run: TaskRun, step: PlannedStep, verdict, *,
                           action_summary: str, worst_case: str, diff_preview: str = "") -> bool:
        evidence = []
        if run.plan:
            for s in run.plan.steps:
                src = isinstance(s.result, dict) and s.result.get("source")
                if src:
                    evidence.append(EvidenceCitation(claim=s.title, source=src, verified=True))
        sandbox_report = self._latest_artifact(run, "sandbox_report")
        if sandbox_report:
            evidence.append(EvidenceCitation(claim="generated code passed sandbox tests",
                                             source="sandbox://tmp", verified=True))
        req = self.gateway.request(
            task_id=run.request.id,
            verdict=verdict,
            action_summary=action_summary + (f"\nSandbox tests: PASS\n{str(sandbox_report)[:400]}" if sandbox_report else ""),
            worst_case=worst_case,
            step_ids=[step.id],
            evidence=evidence,
            diff_preview=diff_preview,
        )
        run.approvals.append(req.id)
        step.status = StepStatus.AWAITING_APPROVAL
        step.result = {"approval_id": req.id}
        self.log("approval_gateway", "parked", step=step.id, approval=req.id)
        return False  # halt pipeline until human clicks
