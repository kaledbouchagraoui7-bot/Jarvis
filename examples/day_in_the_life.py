#!/usr/bin/env python3
"""Day-in-the-life demo: the exact scenario from the blueprint.

"Analyze my Notion databases, find inefficiencies in my current workflows,
write a Python script to automate them, and deploy it to my GitHub."

Runs fully offline (stub models + fixtures). Watch the pipeline:
risk classification -> planning (with governance retry) -> local codegen ->
review -> sandbox -> APPROVAL GATEWAY (interactive click) -> credential
injection -> deploy -> episodic memory update.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automaton.engine import Automaton  # noqa: E402

OBJECTIVE = (
    "Analyze my Notion databases, find inefficiencies in my current workflows, "
    "write a Python script to automate them, and deploy it to my GitHub"
)


def main() -> int:
    auto = Automaton()

    # Enroll a GitHub PAT into the encrypted vault. The Brain never sees this.
    auto.auth.store_pat("github", "ghp_DEMO0000000000000000EXAMPLETOKEN123")

    print("=" * 76)
    print(f"SUBMIT: {OBJECTIVE}\n" + "=" * 76)
    run = auto.submit(OBJECTIVE, context={"notion_database": "db-ops-workflows",
                                          "github_repo": "me/automation-scripts",
                                          "target_path": "scripts/workflow_automator.py"})

    print(f"\nSTATUS after pipeline: {run.status}")
    if run.denied_reason:
        print("DENIED:", run.denied_reason)
        return 1

    assert run.plan is not None
    print("\nPLAN STEPS:")
    for s in run.plan.steps:
        print(f"  [{s.status.value:>17}] {s.title:<38} tool={s.tool or '-':<22} tier={s.model_tier}")

    while run.status == "awaiting_approval":
        appr_id = run.approvals[-1]
        req = auto.gateway.get(appr_id)
        print("\n--- Approval Gateway presentation ---")
        print("Action :", req.action_summary.splitlines()[0])
        print("Risk   :", req.risk_summary)
        print("Worst  :", req.worst_case)
        for e in req.evidence:
            print(f"Evidence: [{'verified' if e.verified else 'UNVERIFIED'}] {e.claim} ({e.source})")
        if req.diff_preview:
            print("Diff preview:\n" + "\n".join(req.diff_preview.splitlines()[:14]))

        answer = ""
        while answer not in ("y", "n"):
            try:
                answer = input("\nHuman-in-the-Loop — Approve this action? [y/n] ").strip().lower()
            except EOFError:
                answer = "y"  # non-interactive CI: simulate the owner's click
        auto.gateway.decide(appr_id, approve=(answer == "y"))
        run = auto.resume(run)

    print(f"\nFINAL STATUS: {run.status}")
    deploy_step = next((s for s in run.plan.steps if s.tool == "github.commit_and_push"), None)
    if deploy_step and deploy_step.result:
        out = deploy_step.result.get("output", {})
        print("Deploy result:", {k: out.get(k) for k in ("simulated", "repo", "path", "message")})

    print("\nMemory counts:", auto.memory.count())
    print("Episodic tail:")
    for rec in auto.memory.recent(__import__("automaton.core.types", fromlist=["MemoryKind"]).MemoryKind.EPISODIC, limit=3):
        print("  -", rec.content[:110])

    print("\nAudit trail (last 12 events):")
    for ev in auto.audit[-12:]:
        print(f"  {ev.ts[11:19]} {ev.actor:<22} {ev.event:<26} {str(ev.detail)[:90]}")
    return 0 if run.completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
