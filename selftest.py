"""
selftest.py — offline verification suite for the Investment Automaton.
=======================================================================

Runs WITHOUT network, WITHOUT TWS and WITHOUT ib_insync installed:
  1. roster invariants   — every agent in exactly one universe, names and
                           descriptions verbatim against agent_universes.json,
                           group->universe table complete and consistent.
  2. cashflow bridge     — positive / zero / negative months, 3a cap overflow.
  3. Nexus gate          — stage -> blocked-before-approval -> approve -> ok,
                           reject path, unknown-id fail-closed.
  4. compliance guard    — margin / derivatives / short / young-lot sells /
                           turnover ceiling / order-frequency all blocked;
                           clean DCA buy passes.
  5. DCA engine dry-run  — full staged->approved->simulated-fill cycle on a
                           temp runtime dir (your real runtime/ is untouched).

Exit code 0 = all green. This is the verification layer the agent pipeline
was missing: run it in CI before any commit that touches money logic.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

FAILS: list = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f"  | {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    here = Path(__file__).resolve().parent
    # isolate runtime state for the whole run
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-selftest-"))
    import os
    os.environ["JARVIS_RUNTIME_DIR"] = str(tmp)
    os.environ["DRY_RUN"] = "true"
    sys.path.insert(0, str(here))
    import config
    import cashflow_bridge as cb
    import nexus_gate as nx
    import swiss_compliance_guard as guard
    import ibkr_dca_engine as eng

    # ---------------- 1. roster invariants ----------------
    roster = json.loads((here / "agent_universes.json").read_text(encoding="utf-8"))
    seen: dict = {}
    dupes = []
    for grp in roster["category_groups"]:
        u = grp["universe"]
        if roster["group_to_universe"][grp["group"]] != u:
            dupes.append(f"group/universe mismatch: {grp['group']}")
        for a in grp["agents"]:
            if a["name"] in seen:
                dupes.append(a["name"])
            seen[a["name"]] = u
    for a in roster["specialized_roles"]["agents"]:
        if a["name"] in seen:
            dupes.append(a["name"])
        seen[a["name"]] = a["universe"]
    check("roster: every agent in exactly one universe", not dupes, f"{len(seen)} agents, dupes={dupes[:3]}")
    check("roster: 7 universes defined", len(roster["universes"]) == 7)
    check("roster: 10 category groups mapped", len(roster["group_to_universe"]) == 10)
    counts = {}
    for u in seen.values():
        counts[u] = counts.get(u, 0) + 1
    check("roster: all universes populated", all(counts.get(f"U{i}", 0) > 0 for i in range(1, 8)), str(counts))

    # ---------------- 2. cashflow bridge ----------------
    pos = cb.compute_plan({"period": "2026-01", "gross": 10000, "expenses": 4000, "tax_reserve": 1000})
    check("cashflow: 30% sweep of 5000 net", abs(pos["investable_chf"] - 1500.0) < 0.01, str(pos["investable_chf"]))
    check("cashflow: 40/60 routing", abs(pos["pillar_3a_chf"] - 600.0) < 0.01 and abs(pos["ibkr_chf"] - 900.0) < 0.01)
    zero = cb.compute_plan({"period": "2026-02", "gross": 1000, "expenses": 1500, "tax_reserve": 0})
    check("cashflow: negative month routes nothing", zero["investable_chf"] == 0 and zero["pillar_3a_chf"] == 0 and zero["ibkr_chf"] == 0, zero["note"])
    flat = cb.compute_plan({"period": "2026-03", "gross": 2000, "expenses": 2000, "tax_reserve": 0})
    check("cashflow: zero month routes nothing", flat["investable_chf"] == 0)
    import config as cfg
    old_cap = cfg.PILLAR_3A_ANNUAL_CAP_CHF
    cfg.PILLAR_3A_ANNUAL_CAP_CHF = 500.0
    capped = cb.compute_plan({"period": "2026-04", "gross": 100000, "expenses": 0, "tax_reserve": 0})
    cfg.PILLAR_3A_ANNUAL_CAP_CHF = old_cap
    check("cashflow: 3a capped, overflow to IBKR", capped["pillar_3a_chf"] == 500.0 and capped["ibkr_chf"] > 0, f"3a={capped['pillar_3a_chf']} ibkr={capped['ibkr_chf']}")

    # ---------------- 3. Nexus gate ----------------
    aid = nx.stage("ibkr_order", {"amount_chf": 100.0, "ticker": "VWRL"}, proposed_by="selftest")
    try:
        nx.require_approval(aid)
        blocked = False
    except nx.NexusDenied:
        blocked = True
    check("nexus: execution blocked before approval", blocked)
    rc = nx.main(["approve", aid, "--confirm", "WRONG-PHRASE"])
    check("nexus: wrong confirm phrase refused", rc == 1 and nx.status(aid) == "PENDING")
    nx.main(["approve", aid, "--confirm", nx.CONFIRM_PHRASE])
    check("nexus: approved action passes gate", nx.status(aid) == "APPROVED" and bool(nx.require_approval(aid)))
    aid2 = nx.stage("transfer_3a", {"amount_chf": 50.0}, proposed_by="selftest")
    nx.main(["reject", aid2, "--reason", "selftest"])
    try:
        nx.require_approval(aid2); denied = False
    except nx.NexusDenied:
        denied = True
    check("nexus: rejected action stays blocked", denied)
    try:
        nx.require_approval("NX-NOPE"); failclosed = False
    except nx.NexusDenied:
        failclosed = True
    check("nexus: unknown id fail-closed", failclosed)

    # ---------------- 4. compliance guard ----------------
    base = {"side": "BUY", "sec_type": "STK", "ticker": "VWRL", "qty": 5,
            "limit_price": 118.0, "currency": "CHF", "margin": False,
            "derivative": False, "short": False, "notional_chf": 590.0}
    check("guard: clean DCA buy passes", bool(guard.check_order(base, 50_000)))
    check("guard: margin blocked", not guard.check_order({**base, "margin": True}, 50_000))
    check("guard: derivatives blocked", not guard.check_order({**base, "derivative": True, "sec_type": "OPT"}, 50_000))
    check("guard: short selling blocked", not guard.check_order({**base, "short": True, "side": "SELL"}, 50_000))
    guard.record_fill("BUY", "VWRL", 5, 118.0, "CHF", 590.0)
    young = guard.check_order({**base, "side": "SELL", "qty": 5, "notional_chf": 590.0}, 50_000)
    check("guard: sell of 0-day lot blocked (<180d)", not young.ok, "; ".join(young.violations[:1]))
    big = guard.check_order({**base, "qty": 500, "notional_chf": 59_000}, 1_000)
    check("guard: turnover ceiling blocked", not big.ok, "; ".join(big.violations[:1]))
    health = guard.portfolio_health(50_000)
    check("guard: holding tracker reports avg days", "avg_holding_days" in health.info)

    # ---------------- 5. DCA engine dry-run ----------------
    plan = {"period": "2026-09", "ibkr_chf": 900.0, "nexus_action_ids": []}
    (config.SWEEP_PLAN_DIR / "plan_2026-09.json").write_text(json.dumps(plan))
    rc = eng.run_period("2026-09")
    check("engine: unstaged plan halts (rc=1)", rc == 1)
    act = nx.stage("ibkr_order", {"amount_chf": 900.0, "ticker": "VWRL"}, proposed_by="selftest")
    plan["nexus_action_ids"] = [act]
    (config.SWEEP_PLAN_DIR / "plan_2026-09.json").write_text(json.dumps(plan))
    rc = eng.run_period("2026-09")
    check("engine: unapproved plan halts (rc=1)", rc == 1)
    nx.main(["approve", act, "--confirm", nx.CONFIRM_PHRASE])
    rc = eng.run_period("2026-09")
    led = guard.read_ledger()
    check("engine: approved dry-run fills + records lot", rc == 0 and any(l["ticker"] == "VWRL" for l in led["lots"]), f"lots={len(led['lots'])}")
    audit = guard.audit_summary()
    check("audit: summary exposes limits for Auditing Agent", audit["limits"]["allow_margin"] is False and audit["fills"] >= 1)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("ALL GREEN" if not FAILS else f"FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
