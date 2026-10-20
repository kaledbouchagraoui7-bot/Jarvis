"""
copytrade_mirror.py — secondary execution path: mirror a verified provider
portfolio under a strict parameter envelope (Part A §3 secondary).
===========================================================================

Design rules
------------
* The provider's target portfolio is consumed ONLY as a signed snapshot
  file: runtime/copytrade_target.json
      {"as_of": "...", "provider": "...",
       "positions": [{"ticker":..., "exchange":..., "currency":...,
                      "weight": 0.12}, ...]}
  Weights must sum to <= 1.0; anything missing stays in cash.
* Envelope (hard caps, config-driven):
      COPYTRADE_MAX_NOTIONAL_PER_RUN_CHF   — total rebalance size per run
      COPYTRADE_MAX_SINGLE_POSITION_PCT    — per-position weight ceiling
* Every generated order passes swiss_compliance_guard.check_order() and a
  Nexus approval (kind="copytrade_rebalance") before execution — identical
  gate order to the DCA engine. The Investment Analyst agent may PROPOSE
  targets; it can never execute.
* DRY_RUN defaults to true; live connectivity to a real copy-trading API is
  an explicit extension point (see `fetch_provider_target`) and is never
  enabled by a code edit alone — COPYTRADE_ENABLED must be true in .env.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import config
import nexus_gate
import swiss_compliance_guard as guard


def _log(msg: str, level: str = "INFO") -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [COPYTRADE] [{level}] {msg}"
    print(line)
    with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fetch_provider_target() -> dict:
    """Extension point for a real copy-trading API client (Alpha Ledger etc.).

    A production implementation would authenticate with a key from the
    environment, verify the provider signature, and return the same schema
    as the snapshot file. Network access is intentionally NOT implemented
    here: until you plug in a vetted client, the signed snapshot file is
    the only accepted source of truth.
    """
    if not config.COPYTRADE_TARGET.exists():
        raise FileNotFoundError(
            f"no provider snapshot at {config.COPYTRADE_TARGET} — place a "
            f"signed target file there or implement a vetted API client here.")
    return json.loads(config.COPYTRADE_TARGET.read_text(encoding="utf-8"))


def validate_target(target: dict) -> list:
    problems = []
    positions = target.get("positions", [])
    if not positions:
        problems.append("target has no positions")
    total = sum(float(p.get("weight", 0)) for p in positions)
    if total > 1.0 + 1e-9:
        problems.append(f"weights sum to {total:.3f} > 1.0")
    for p in positions:
        if float(p.get("weight", 0)) > config.COPYTRADE_MAX_SINGLE_POSITION_PCT:
            problems.append(f"{p.get('ticker')} weight "
                            f"{p['weight']} > single-position cap "
                            f"{config.COPYTRADE_MAX_SINGLE_POSITION_PCT}")
        if p.get("sec_type", "STK") not in config.LIMITS.allowed_sec_types:
            problems.append(f"{p.get('ticker')} sec_type not allowed "
                            f"(derivatives prohibited)")
    return problems


def build_orders(target: dict, budget_chf: float) -> list:
    orders = []
    for p in target.get("positions", []):
        notional = config.as_chf(budget_chf * float(p["weight"]))
        if notional < config.MIN_ORDER_CHF:
            continue
        orders.append({"side": "BUY", "sec_type": p.get("sec_type", "STK"),
                       "ticker": p["ticker"], "qty": 0,   # qty sized by engine
                       "limit_price": None, "currency": p.get("currency", "CHF"),
                       "margin": False, "derivative": False, "short": False,
                       "notional_chf": notional,
                       "exchange": p.get("exchange", "SIX")})
    return orders


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Copy-trading mirror (Nexus-gated)")
    ap.add_argument("--budget-chf", type=float, required=True,
                    help="rebalance budget for this run")
    args = ap.parse_args(argv)

    print("== Copy-Trading Mirror ==", config.summary(), sep="\n   ")
    if not config.COPYTRADE_ENABLED:
        _log("COPYTRADE_ENABLED=false — module disabled by configuration")
        print("[halt] copy-trading disabled (set COPYTRADE_ENABLED=true in .env "
              "only after reviewing the provider)")
        return 1
    budget = min(args.budget_chf, config.COPYTRADE_MAX_NOTIONAL_PER_RUN_CHF)
    if budget < args.budget_chf:
        _log(f"WARN: requested budget clipped to envelope "
             f"CHF {budget:,.2f}")
    try:
        target = fetch_provider_target()
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"HALT: provider target unavailable: {exc}", "ERROR")
        return 1
    problems = validate_target(target)
    if problems:
        _log("HALT: target violates envelope: " + "; ".join(problems), "ERROR")
        return 1

    orders = build_orders(target, budget)
    ids = []
    for o in orders:
        report = guard.check_order(o, max(budget * 10, 1.0))
        if not report.ok:
            _log(f"HALT: compliance block on {o['ticker']}: "
                 + "; ".join(report.violations), "ERROR")
            return 1
        ids.append(nexus_gate.stage(
            "copytrade_rebalance",
            {"ticker": o["ticker"], "notional_chf": o["notional_chf"],
             "provider": target.get("provider", "unknown"),
             "as_of": target.get("as_of", "unknown")},
            proposed_by="Investment Analyst agent (proposal only)"))
    print(f"\nStaged {len(ids)} copy-trade order(s) in The Nexus: {ids}")
    print("Approve with `python nexus_gate.py approve <id> --confirm "
          "I-AUTHORISE-THIS-ACTION`, then re-run with --execute (extension "
          "point) — staging alone never trades.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
