"""Approval Gateway — Human-in-the-Loop interception for risky/irreversible actions.

When the Policy Engine says APPROVAL_REQUIRED, execution halts here. The human
is presented with: action summary, risk summary, worst-case scenarios, evidence,
and (for code) a diff preview + sandbox results. Nothing proceeds without an
explicit click. Pending requests expire per the constitution.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .types import ApprovalRequest, ApprovalStatus, AuditEvent, PolicyVerdict


class ApprovalGateway:
    """In-process approval queue. Swap ``notify`` for Slack/email/push delivery."""

    def __init__(
        self,
        audit_log: Optional[list[AuditEvent]] = None,
        expiry_hours: int = 24,
        notify: Optional[Callable[[ApprovalRequest], None]] = None,
    ):
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}
        self.audit_log = audit_log if audit_log is not None else []
        self.expiry = timedelta(hours=expiry_hours)
        self._notify = notify or self._default_notify

    # ------------------------------------------------------------------ #
    @staticmethod
    def _default_notify(req: ApprovalRequest) -> None:
        banner = "=" * 72
        print(f"\n{banner}\nAPPROVAL REQUIRED  [{req.id}]  created {req.created_at}\n{banner}")
        print(f"ACTION      : {req.action_summary}")
        print(f"RISK        : {req.risk_summary}")
        print(f"WORST CASE  : {req.worst_case}")
        if req.evidence:
            print("EVIDENCE    :")
            for e in req.evidence:
                mark = "verified" if e.verified else "UNVERIFIED"
                print(f"  - [{mark}] {e.claim}  ({e.source})")
        if req.diff_preview:
            print("DIFF PREVIEW:")
            print(req.diff_preview)
        print(f"{banner}\nResolve with: gateway.decide('{req.id}', approve=True|False)\n")

    # ------------------------------------------------------------------ #
    def request(
        self,
        task_id: str,
        verdict: PolicyVerdict,
        action_summary: str,
        worst_case: str,
        step_ids: list[str] | None = None,
        evidence: list | None = None,
        diff_preview: str = "",
        notify: Optional[Callable[[ApprovalRequest], None]] = None,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            task_id=task_id,
            step_ids=step_ids or [],
            action_summary=action_summary,
            risk_summary=f"{verdict.risk.value.upper()} / {verdict.reversibility.value} / domain={verdict.domain.value}",
            worst_case=worst_case,
            evidence=evidence or [],
            diff_preview=diff_preview,
        )
        with self._lock:
            self._requests[req.id] = req
        self.audit_log.append(
            AuditEvent(actor="approval_gateway", event="requested", detail={"id": req.id, "task": task_id})
        )
        (notify or self._notify)(req)
        return req

    def decide(self, request_id: str, approve: bool, decided_by: str = "owner") -> ApprovalRequest:
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                raise KeyError(f"unknown approval request {request_id}")
            if req.status != ApprovalStatus.PENDING:
                raise RuntimeError(f"request {request_id} already {req.status.value}")
            req.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
            req.decided_by = decided_by
            req.decided_at = datetime.now(timezone.utc).isoformat()
        self.audit_log.append(
            AuditEvent(
                actor="approval_gateway",
                event="approved" if approve else "rejected",
                detail={"id": request_id, "by": decided_by},
            )
        )
        return req

    def get(self, request_id: str) -> ApprovalRequest:
        self.expire_stale()
        with self._lock:
            return self._requests[request_id]

    def is_approved(self, request_id: str) -> bool:
        return self.get(request_id).status == ApprovalStatus.APPROVED

    def pending(self) -> list[ApprovalRequest]:
        self.expire_stale()
        with self._lock:
            return [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING]

    def all_requests(self) -> list[ApprovalRequest]:
        with self._lock:
            return list(self._requests.values())

    # ------------------------------------------------------------------ #
    def expire_stale(self) -> list[str]:
        """Auto-reject expired requests per constitution (auto_expire_action)."""
        expired: list[str] = []
        now = datetime.now(timezone.utc)
        with self._lock:
            for req in self._requests.values():
                if req.status != ApprovalStatus.PENDING:
                    continue
                created = datetime.fromisoformat(req.created_at)
                if now - created > self.expiry:
                    req.status = ApprovalStatus.EXPIRED
                    req.decided_at = now.isoformat()
                    req.decided_by = "system:expiry"
                    expired.append(req.id)
        for rid in expired:
            self.audit_log.append(
                AuditEvent(actor="approval_gateway", event="expired_rejected", detail={"id": rid})
            )
        return expired
