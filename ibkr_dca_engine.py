"""
ibkr_dca_engine.py — IBKR Dollar-Cost-Averaging execution engine (Part A §3).
==============================================================================

Pipeline for one sweep plan period:
  1. load runtime/sweep_plans/plan_<period>.json (written by cashflow_bridge)
  2. build a LIMIT buy order for the primary ETF (VWRL/VT)
  3. swiss_compliance_guard.check_order()      -> hard block on violations
  4. nexus_gate.require_approval(action_id)   -> The Nexus human gate
  5. execute via ib_insync (TWS/Gateway) — or simulate when DRY_RUN=true
  6. record the fill in runtime/investment_ledger.json (FIFO lots)

Error handling (STRICT CONSTRAINT): at most MAX_CONNECT_ATTEMPTS connects
and ORDER_SUBMIT_ATTEMPTS submissions; any failure is written to
runtime/investment_log.txt and the process HALTS (exit 1). No infinite
retry loops, ever.

Security: host/port/client-id come from the environment (.env); no
credentials are stored in this repository. IBKR API authentication remains
the local TWS/Gateway session on your machine.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time

import config
import nexus_gate
import swiss_compliance_guard as guard

# ib_insync is imported lazily so DRY_RUN works on machines without TWS.
_ib = None


def _log(msg: str, level: str = "INFO") -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [IBKR-DCA] [{level}] {msg}"
    print(line)
    with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _halt(msg: str) -> int:
    _log(f"HALT: {msg}", "ERROR")
    print(f"[halt] {msg} — see {config.LOG_FILE}")
    return 1


def connect():
    """Bounded connect attempts; returns an ib_insync IB object or raises."""
    global _ib
    from ib_insync import IB  # deferred: dry-run needs no dependency
    last = None
    for attempt in range(1, config.MAX_CONNECT_ATTEMPTS + 1):
        try:
            ib = IB()
            ib.connect(config.IBKR_HOST, config.IBKR_PORT,
                       clientId=config.IBKR_CLIENT_ID,
                       timeout=config.IBKR_CONNECT_TIMEOUT_S)
            _ib = ib
            _log(f"connected to TWS/Gateway {config.IBKR_HOST}:{config.IBKR_PORT} "
                 f"(attempt {attempt})")
            return ib
        except Exception as exc:  # noqa: BLE001 — log & halt policy
            last = exc
            _log(f"connect attempt {attempt}/{config.MAX_CONNECT_ATTEMPTS} "
                 f"failed: {exc}", "ERROR")
    raise ConnectionError(f"IBKR unreachable after "
                          f"{config.MAX_CONNECT_ATTEMPTS} attempts: {last}")


def market_mid(ib, tgt: config.EtfTarget):
    """Best-effort mid price; None when market data is unavailable."""
    from ib_insync import Stock
    contract = Stock(tgt.symbol, tgt.exchange, tgt.currency)
    ib.qualifyContracts(contract)
    ib.reqMktData(contract, "", False, False)
    ib.sleep(2)
    mid = None
    try:
        mid = ib.ticker(contract).midpoint()
    except Exception:  # noqa: BLE001
        mid = None
    ib.cancelMktData(contract)
    return mid


def build_order(amount_chf: float, price: float, tgt: config.EtfTarget, fx: float):
    """LIMIT buy — floor one cent (config.MIN_ORDER_CHF), NO maximum.

    Whole shares when the amount allows; below one share it becomes a
    fractional cash-quantity order (IBKR cashQty) so even CHF 0.01 invests.
    Whole-share residual carries over to the next sweep."""
    qty, fractional = 0, False
    if price > 0:
        qty = int(math.floor(amount_chf / fx / price))
        if qty <= 0 and config.FRACTIONAL_ENABLED and amount_chf >= config.MIN_ORDER_CHF:
            qty = round(amount_chf / (price * fx), 6)      # fractional share count
            fractional = True
    limit = config.as_chf(price * (1 + config.LIMIT_MAX_OFFSET_BPS / 10_000.0))
    notional = config.as_chf(min(qty * limit * fx, amount_chf))
    return {"side": "BUY", "sec_type": "STK", "ticker": tgt.symbol, "qty": qty,
            "limit_price": limit, "currency": tgt.currency, "margin": False,
            "derivative": False, "short": False, "fractional": fractional,
            "cash_qty": round(amount_chf / fx, 2) if fractional else None,
            "notional_chf": notional,
            "residual_chf": config.as_chf(max(amount_chf - notional, 0.0))}


def execute(ib, order: dict, tgt: config.EtfTarget):
    """Single submission attempt; raises on failure (caller halts)."""
    from ib_insync import LimitOrder, Stock
    contract = Stock(tgt.symbol, tgt.exchange, tgt.currency)
    ib.qualifyContracts(contract)
    leg = LimitOrder("BUY", order["qty"], order["limit_price"])
    if order.get("fractional"):
        # IBKR cash-quantity fractional order: totalQuantity 0 + cashQty amount
        leg.totalQuantity = 0
        leg.cashQty = order.get("cash_qty")
    trade = ib.placeOrder(contract, leg)
    ib.sleep(3)
    status = trade.orderStatus.status
    if status not in ("PreSubmitted", "Submitted", "Filled"):
        raise RuntimeError(f"order rejected/failed: status={status}")
    fill_price = trade.orderStatus.avgFillPrice or order["limit_price"]
    return {"status": status, "fill_price": fill_price}


def run_period(period: str, fx_usd_chf: float = 0.88) -> int:
    plan_path = config.SWEEP_PLAN_DIR / f"plan_{period}.json"
    if not plan_path.exists():
        return _halt(f"no sweep plan for {period} — run cashflow_bridge.py first")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    amount = float(plan.get("ibkr_chf", 0.0))
    if amount <= 0:
        _log(f"{period}: IBKR leg is 0 — nothing to execute (bootstrap month)")
        return 0
    action_ids = [a for a in plan.get("nexus_action_ids", [])
                  if nexus_gate.status(a) != "UNKNOWN"]
    order_actions = [a for a in action_ids
                     if next((r for r in json.loads(config.NEXUS_PENDING.read_text())
                              if r["id"] == a), {}).get("kind") == "ibkr_order"]
    if not order_actions:
        return _halt(f"plan {period} has no staged ibkr_order action — "
                     f"re-run cashflow_bridge.py (Nexus staging)")
    action_id = order_actions[0]

    tgt = config.DCA_TARGETS[config.PRIMARY_TICKER]
    fx = 1.0 if tgt.currency == "CHF" else fx_usd_chf

    # ---- price -------------------------------------------------------------
    ib = None
    price = config.DRY_RUN_PRICES.get(tgt.symbol)
    if not config.DRY_RUN:
        try:
            ib = connect()
        except ConnectionError as exc:
            return _halt(str(exc))
        mid = market_mid(ib, tgt)
        if mid is None or mid <= 0:
            ib.disconnect()
            return _halt(f"no market data for {tgt.symbol} — refusing blind limit")
        price = mid

    order = build_order(amount, price, tgt, fx)
    if order["qty"] <= 0:
        _log(f"{period}: amount CHF {amount:,.2f} buys 0 shares at "
             f"{price:.2f} {tgt.currency} — carried over to next sweep")
        return 0

    # ---- guardrails ---------------------------------------------------------
    portfolio_value = amount * 10  # conservative base for turnover ratio; replace
    # with your real account value via IBKR account summary in live mode.
    if ib is not None:
        try:
            vals = ib.accountSummary(tags=["NetLiquidation"])
            for row in vals:
                if row.currency in ("CHF", "BASE"):
                    portfolio_value = float(row.value)
        except Exception as exc:  # noqa: BLE001
            _log(f"WARN: account summary unavailable ({exc}); using plan-based base")
    report = guard.check_order(order, portfolio_value)
    if not report.ok:
        if ib: ib.disconnect()
        return _halt("compliance block: " + " | ".join(report.violations))
    for w in report.warnings:
        _log(f"WARN: {w}")

    # ---- THE NEXUS ----------------------------------------------------------
    try:
        nexus_gate.require_approval(action_id)
    except nexus_gate.NexusDenied as exc:
        if ib: ib.disconnect()
        return _halt(str(exc))

    # ---- execute ------------------------------------------------------------
    if config.DRY_RUN:
        fill = {"status": "SimulatedFilled", "fill_price": order["limit_price"]}
        _log(f"DRY-RUN fill {order['qty']} x {tgt.symbol} @ {fill['fill_price']:.2f} "
             f"{tgt.currency} (notional CHF {order['notional_chf']:,.2f})")
    else:
        try:
            fill = execute(ib, order, tgt)
        except Exception as exc:  # noqa: BLE001 — halt, do not retry-loop
            if ib: ib.disconnect()
            return _halt(f"order submission failed: {exc}")
        ib.disconnect()

    guard.record_fill("BUY", tgt.symbol, order["qty"], fill["fill_price"],
                      tgt.currency, order["notional_chf"], fx)
    _log(f"{period}: DCA complete — {order['qty']} {tgt.symbol} @ "
         f"{fill['fill_price']:.2f} {tgt.currency} status={fill['status']} "
         f"residual CHF {order['residual_chf']:,.2f} carried over")
    print(f"\n✓ {period}: bought {order['qty']} {tgt.symbol} @ "
          f"{fill['fill_price']:.2f} {tgt.currency} "
          f"({fill['status']}); residual CHF {order['residual_chf']:,.2f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="IBKR DCA execution engine")
    ap.add_argument("--plan", required=True, help="period id, e.g. 2026-10")
    ap.add_argument("--fx-usd-chf", type=float, default=0.88,
                    help="USD/CHF rate used only to size USD-listed legs")
    args = ap.parse_args(argv)
    print("== IBKR DCA Engine ==", config.summary(), sep="\n   ")
    if config.DRY_RUN:
        _log("DRY_RUN=true — simulated execution; set DRY_RUN=false in .env for live")
    return run_period(args.plan, args.fx_usd_chf)


if __name__ == "__main__":
    sys.exit(main())
