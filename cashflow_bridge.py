"""
cashflow_bridge.py — Cashflow Sweep & Routing Engine (Part A §1-2).
===================================================================

Ingests the business income CSV produced by the agent-run company
(Part B; the **Cash Flow Manager** agent of the Capital & Compliance
Universe supplies these figures), computes the investable surplus and
outputs the exact CHF amounts for:
  * Swiss Pillar 3a  — QR-bill payment reference emitted for manual
    execution (or API call if your provider exposes one),
  * IBKR             — handed to ibkr_dca_engine.py as a sweep plan.

Rules
-----
* investable = max(0, net_profit) * SWEEP_PROFIT_SHARE   (e.g. 30%)
* net_profit = gross_income - business_expenses - tax_reserve
* zero or negative net profit  -> NOTHING is routed (bootstrap months),
  the period is logged and a zero-plan is written for the audit trail.
* Pillar 3a leg is capped by the remaining annual ceiling
  (PILLAR_3A_ANNUAL_CAP_CHF - PILLAR_3A_PAID_YTD_CHF); the overflow falls
  through to the IBKR leg.
* Every non-zero transfer is STAGED in The Nexus (nexus_gate.stage) —
  nothing moves without explicit human approval.

CSV schema (header row required, see sample_data/business_income_sample.csv):
    period,gross_income_chf,business_expenses_chf,tax_reserve_chf
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import config
import nexus_gate


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [CASHFLOW] {msg}"
    print(line)
    with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_income_csv(path: Path) -> list:
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        missing = [c for c in config.CSV_COLUMNS if c not in (rdr.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name} lacks columns {missing}; "
                             f"expected {config.CSV_COLUMNS}")
        for i, r in enumerate(rdr, start=2):
            try:
                rows.append({
                    "period": r["period"].strip(),
                    "gross": float(r["gross_income_chf"]),
                    "expenses": float(r["business_expenses_chf"]),
                    "tax_reserve": float(r["tax_reserve_chf"]),
                })
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name} line {i}: unparseable number ({exc})")
    return rows


def compute_plan(row: dict) -> dict:
    """Pure function: one income row -> one routing plan (no side effects)."""
    net = row["gross"] - row["expenses"] - row["tax_reserve"]
    investable = config.as_chf(max(0.0, net) * config.SWEEP_PROFIT_SHARE)
    cap_left = max(0.0, config.PILLAR_3A_ANNUAL_CAP_CHF - config.PILLAR_3A_PAID_YTD_CHF)

    if investable <= 0:
        plan_3a = plan_ibkr = 0.0
        note = ("net profit <= 0 — bootstrap month: NOTHING routed "
                "(zero-capital discipline, Part C)")
    else:
        plan_3a = config.as_chf(min(investable * config.ALLOC_PILLAR_3A, cap_left))
        plan_ibkr = config.as_chf(investable - plan_3a)
        if plan_3a < investable * config.ALLOC_PILLAR_3A:
            note = (f"3a leg capped at remaining annual ceiling "
                    f"CHF {cap_left:,.2f}; overflow redirected to IBKR")
        else:
            note = "standard routing"
        if plan_ibkr < config.MIN_ORDER_CHF:
            note += "; IBKR leg below one cent — carried over"
            plan_ibkr = 0.0

    return {
        "period": row["period"],
        "net_profit_chf": config.as_chf(net),
        "investable_chf": investable,
        "pillar_3a_chf": plan_3a,
        "ibkr_chf": plan_ibkr,
        "pillar_3a_qr_reference": config.PILLAR_3A_PROVIDER_QR_REF,
        "note": note,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "proposed_by": "Cash Flow Manager agent (figures) / Investment Analyst "
                       "agent (proposal only — execution requires The Nexus)",
    }


def write_plan(plan: dict) -> Path:
    path = config.SWEEP_PLAN_DIR / f"plan_{plan['period']}.json"
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return path


def stage_transfers(plan: dict) -> list:
    """Stage Nexus actions for every non-zero leg (human approval required)."""
    ids = []
    if plan["pillar_3a_chf"] > 0:
        ids.append(nexus_gate.stage(
            "transfer_3a",
            {"period": plan["period"], "amount_chf": plan["pillar_3a_chf"],
             "qr_reference": plan["pillar_3a_qr_reference"],
             "target": "Pillar 3a provider (manual QR-bill execution)"},
            proposed_by=plan["proposed_by"]))
    if plan["ibkr_chf"] > 0:
        ids.append(nexus_gate.stage(
            "ibkr_order",
            {"period": plan["period"], "amount_chf": plan["ibkr_chf"],
             "ticker": config.PRIMARY_TICKER,
             "target": f"IBKR DCA buy {config.PRIMARY_TICKER} (limit order)"},
            proposed_by=plan["proposed_by"]))
    return ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Business cashflow -> investment sweep")
    ap.add_argument("csv_path", nargs="?", default="sample_data/business_income_sample.csv",
                    help="business income CSV (see sample_data/)")
    ap.add_argument("--no-stage", action="store_true",
                    help="compute + write plans only; do not stage Nexus actions")
    args = ap.parse_args(argv)

    print("== Cashflow Sweep & Routing ==", config.summary(), sep="\n   ")
    try:
        rows = read_income_csv(Path(args.csv_path))
    except (OSError, ValueError) as exc:
        _log(f"FATAL: {exc}")
        print(f"[halt] {exc}")
        return 1

    if not rows:
        _log("WARN: CSV contains no data rows; nothing to route")
        return 0

    for row in rows:
        plan = compute_plan(row)
        path = write_plan(plan)
        ids = [] if args.no_stage else stage_transfers(plan)
        plan["nexus_action_ids"] = ids
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        _log(f"{plan['period']}: net={plan['net_profit_chf']:,.2f} "
             f"investable={plan['investable_chf']:,.2f} "
             f"3a={plan['pillar_3a_chf']:,.2f} ibkr={plan['ibkr_chf']:,.2f} "
             f"nexus={ids or 'none'}")
        print(f"\n{plan['period']:>8}  net {plan['net_profit_chf']:>12,.2f} CHF  ->  "
              f"3a {plan['pillar_3a_chf']:>10,.2f}  |  IBKR {plan['ibkr_chf']:>10,.2f}")
        print(f"          {plan['note']}")
        if ids:
            print(f"          Nexus approvals required: {', '.join(ids)}")

    print("\nNext: review with `python nexus_gate.py list`, approve, then run "
          "`python ibkr_dca_engine.py --plan <period>`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
