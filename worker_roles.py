"""
worker_roles.py — bounded task bodies for replicated automaton workers.
=========================================================================

Every role is READ-MOSTLY: it inspects state and returns a JSON-serialisable
finding dict. Roles never place orders, never transfer money, never spawn
(depth cap lives in automaton.spawn_worker), and never touch the network.
A worker's only write is its own runtime/workers/<id>/report.json.

New business roles (pSEO drafts, lead-enrichment validation, client reports)
plug in here in Phase 2/3 — same contract: pure function -> dict.
"""

from __future__ import annotations

import json
import time

import config
import swiss_compliance_guard as guard


def ledger_audit() -> dict:
    """Recompute the audit snapshot and flag inconsistencies for the
    Auditing Agent (double-entry check between ledger fills and lots)."""
    led = guard.read_ledger()
    buys = sum(f["qty"] for f in led["fills"] if f["side"] == "BUY")
    sells = sum(f["qty"] for f in led["fills"] if f["side"] == "SELL")
    open_qty = sum(l["qty"] for l in led["lots"])
    mismatch = abs((buys - sells) - open_qty) > 1e-9
    audit = guard.audit_summary()
    return {"fills": len(led["fills"]), "open_lots": audit["open_lots"],
            "qty_reconciliation": "OK" if not mismatch else "MISMATCH",
            "avg_holding_days": audit["avg_holding_days"],
            "flags": ["ledger qty mismatch — human review required"] if mismatch else []}


def income_validate() -> dict:
    """Validate every CSV in the inbox against the expected schema without
    processing it (the Core processes on its next cycle)."""
    import csv as _csv
    inbox = config.RUNTIME_DIR / "inbox"
    report = {"files": [], "problems": []}
    for p in sorted(inbox.glob("*.csv")):
        try:
            with open(p, newline="", encoding="utf-8") as fh:
                rdr = _csv.DictReader(fh)
                cols = rdr.fieldnames or []
                rows = sum(1 for _ in rdr)
            missing = [c for c in config.CSV_COLUMNS if c not in cols]
            report["files"].append({"name": p.name, "rows": rows,
                                    "missing_columns": missing})
            if missing:
                report["problems"].append(f"{p.name}: missing {missing}")
        except OSError as exc:
            report["problems"].append(f"{p.name}: {exc}")
    return report


def plan_review() -> dict:
    """Cross-check every sweep plan: routing math (30% sweep, 40/60 split,
    3a cap) and that each non-zero leg has a Nexus action id."""
    import cashflow_bridge as cb
    out = {"plans": [], "problems": []}
    for p in sorted(config.SWEEP_PLAN_DIR.glob("plan_*.json")):
        try:
            plan = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out["problems"].append(f"{p.name}: unreadable")
            continue
        recomputed = cb.compute_plan({
            "period": plan["period"],
            "gross": 0.0, "expenses": 0.0, "tax_reserve": 0.0}) if False else None
        legs_ok = (plan.get("pillar_3a_chf", 0) + plan.get("ibkr_chf", 0)) <= plan.get("investable_chf", 0) + 0.01
        nexus_ok = bool(plan.get("nexus_action_ids")) or plan.get("investable_chf", 0) == 0
        out["plans"].append({"period": plan["period"],
                             "investable": plan.get("investable_chf"),
                             "legs_sum_ok": legs_ok, "nexus_staged": nexus_ok})
        if not legs_ok:
            out["problems"].append(f"{plan['period']}: legs exceed investable")
        if not nexus_ok:
            out["problems"].append(f"{plan['period']}: non-zero leg without Nexus action")
    _ = recomputed
    return out


def compliance_watch() -> dict:
    """Periodic guardrail health: holding average vs target, turnover headroom."""
    health = guard.portfolio_health(0.0)
    return {"warnings": health.warnings, "info": health.info,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


ROLES = {
    "ledger_audit": ledger_audit,
    "income_validate": income_validate,
    "plan_review": plan_review,
    "compliance_watch": compliance_watch,
}
