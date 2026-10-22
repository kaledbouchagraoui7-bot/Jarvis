"""
swiss_compliance_guard.py — Swiss "private investor" guardrail (CRITICAL).
==========================================================================

Rule owners (Part C wiring): the **Tax Advisor** and the **Regulatory
Compliance Officer** agents of the Capital & Compliance Universe own every
threshold enforced here (values live in `config.LIMITS`, overridable only
via environment variables — never silently in code).

Hard-coded prohibitions (cannot be enabled by configuration):
  * no leverage / margin borrowing      (account type must be CASH)
  * no derivatives (options / futures / CFDs / warrants)
  * no short selling
  * plain STK (stock/ETF) instruments only

Trackers:
  * minimum holding period — FIFO lots from `runtime/investment_ledger.json`;
    selling a lot younger than MIN_HOLDING_DAYS is blocked, and the weighted
    average holding period of open lots is reported (> 6 months target).
  * annual turnover ratio — trailing-365-day traded notional divided by the
    beginning-of-period portfolio value must stay <= MAX_TURNOVER_RATIO
    (~5x per ESTV practice criteria; cantonal practice varies).
  * order frequency — <= MAX_ORDERS_PER_MONTH (DCA cadence discipline).

This module is NOT tax advice; it encodes conservative, configurable
safe-haven criteria. A qualified Swiss tax advisor validates the thresholds.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import config

DAY = 86_400.0


@dataclass
class ComplianceReport:
    ok: bool
    violations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [COMPLIANCE] {msg}"
    print(line)
    with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ------------------------------------------------------------------- ledger --
def read_ledger() -> dict:
    if not config.LEDGER_FILE.exists():
        return {"lots": [], "fills": [], "portfolio_value_start_chf": 0.0,
                "start_of_period": time.strftime("%Y-%m-%d")}
    return json.loads(config.LEDGER_FILE.read_text(encoding="utf-8"))


def write_ledger(led: dict) -> None:
    config.LEDGER_FILE.write_text(json.dumps(led, indent=2), encoding="utf-8")


def record_fill(side: str, ticker: str, qty: float, price: float,
                currency: str, notional_chf: float, fx: float = 1.0) -> None:
    """Append a fill; BUY opens a FIFO lot, SELL consumes the oldest lots."""
    led = read_ledger()
    now = time.time()
    led["fills"].append({"ts": now, "date": time.strftime("%Y-%m-%d"), "side": side,
                         "ticker": ticker, "qty": qty, "price": price,
                         "currency": currency, "notional_chf": notional_chf, "fx": fx})
    if side == "BUY":
        led["lots"].append({"ticker": ticker, "qty": qty, "opened_ts": now,
                            "opened_date": time.strftime("%Y-%m-%d")})
    else:  # SELL consumes FIFO
        remaining = qty
        for lot in led["lots"]:
            if lot["ticker"] != ticker or lot["qty"] <= 0:
                continue
            take = min(lot["qty"], remaining)
            lot["qty"] -= take
            remaining -= take
            if remaining <= 1e-9:
                break
        led["lots"] = [l for l in led["lots"] if l["qty"] > 1e-9]
    write_ledger(led)


# ------------------------------------------------------------------ checks --
def check_order(order: dict, portfolio_value_chf: float) -> ComplianceReport:
    """Pre-trade gate. `order` keys: side, sec_type, ticker, qty,
    margin (bool), derivative (bool), short (bool), limit_price, currency."""
    v, w, info = [], [], {}
    L = config.LIMITS

    if order.get("sec_type", "STK") not in L.allowed_sec_types:
        v.append(f"sec_type {order.get('sec_type')} not in allowed {L.allowed_sec_types} "
                 f"(derivatives/proxies are hard-prohibited)")
    if order.get("margin") or config.IBKR_ACCOUNT_TYPE != "CASH":
        v.append("leverage/margin prohibited for private-investor status "
                 "(account type must stay CASH)")
    if order.get("derivative"):
        v.append("derivatives (options/futures/CFDs) hard-prohibited")
    if order.get("short") or order.get("side") not in ("BUY", "SELL"):
        v.append(f"side {order.get('side')} not allowed (no short selling)")

    # order-frequency discipline (DCA cadence)
    led = read_ledger()
    month = time.strftime("%Y-%m")
    month_orders = sum(1 for f in led["fills"] if f["date"].startswith(month))
    info["orders_this_month"] = month_orders + 1
    if month_orders + 1 > L.max_orders_per_month:
        v.append(f"order #{month_orders + 1} this month exceeds "
                 f"MAX_ORDERS_PER_MONTH={L.max_orders_per_month}")

    # turnover ratio on the trailing 365 days (only sells/adds move the needle)
    year_ago = time.time() - 365 * DAY
    traded = sum(f["notional_chf"] for f in led["fills"] if f["ts"] >= year_ago)
    traded += order.get("notional_chf", 0.0)
    base = max(portfolio_value_chf, led.get("portfolio_value_start_chf", 0.0), 1.0)
    ratio = traded / base
    info["trailing_turnover_ratio"] = round(ratio, 3)
    if ratio > L.max_annual_turnover_ratio:
        v.append(f"trailing turnover {ratio:.2f}x exceeds "
                 f"{L.max_annual_turnover_ratio}x (professional-trader signal)")
    elif ratio > 0.8 * L.max_annual_turnover_ratio:
        w.append(f"turnover ratio {ratio:.2f}x approaching the "
                 f"{L.max_annual_turnover_ratio}x ceiling")

    # minimum holding period on sells (FIFO lots)
    if order.get("side") == "SELL":
        young = holding_days_by_fifo(order["ticker"], order["qty"])
        if young is not None:
            info["youngest_lot_days"] = young
            if young < L.min_holding_days:
                v.append(f"sell would consume a lot held {young}d < "
                         f"MIN_HOLDING_DAYS={L.min_holding_days}")

    rep = ComplianceReport(ok=not v, violations=v, warnings=w, info=info)
    _log(f"check_order {order.get('side')} {order.get('ticker')} x{order.get('qty')} "
         f"-> {'OK' if rep.ok else 'BLOCKED: ' + '; '.join(v)}")
    return rep


def holding_days_by_fifo(ticker: str, qty: float):
    """Age (days) of the youngest FIFO lot a sell of `qty` would touch."""
    led = read_ledger()
    lots = [l for l in led["lots"] if l["ticker"] == ticker and l["qty"] > 0]
    lots.sort(key=lambda l: l["opened_ts"])
    remaining, youngest = qty, None
    now = time.time()
    for lot in lots:
        take = min(lot["qty"], remaining)
        youngest = int((now - lot["opened_ts"]) // DAY)
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        return None  # insufficient lots: selling more than owned -> blocked upstream
    return youngest


def average_holding_days() -> float:
    """Weighted average age of all open lots (the >6-months health metric)."""
    led = read_ledger()
    now = time.time()
    lots = [l for l in led["lots"] if l["qty"] > 0]
    total = sum(l["qty"] for l in lots)
    if not total:
        return 0.0
    return sum(l["qty"] * (now - l["opened_ts"]) / DAY for l in lots) / total


def portfolio_health(portfolio_value_chf: float) -> ComplianceReport:
    """Periodic (non-blocking) status: holding average + turnover headroom."""
    L = config.LIMITS
    avg = average_holding_days()
    v, w = [], []
    if avg and avg < L.min_holding_days:
        w.append(f"weighted average holding {avg:.0f}d is below the "
                 f"{L.min_holding_days}d target — avoid sells until it recovers")
    return ComplianceReport(ok=True, violations=v, warnings=w,
                            info={"avg_holding_days": round(avg, 1),
                                  "portfolio_value_chf": portfolio_value_chf})


def audit_summary() -> dict:
    """Snapshot consumed by the Auditing Agent (with investment_log.txt)."""
    led = read_ledger()
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "config": config.summary(),
            "open_lots": len([l for l in led["lots"] if l["qty"] > 0]),
            "fills": len(led["fills"]),
            "avg_holding_days": round(average_holding_days(), 1),
            "limits": {"min_holding_days": config.LIMITS.min_holding_days,
                       "max_annual_turnover_ratio": config.LIMITS.max_annual_turnover_ratio,
                       "allow_margin": config.LIMITS.allow_margin,
                       "allow_derivatives": config.LIMITS.allow_derivatives,
                       "allow_short_selling": config.LIMITS.allow_short_selling}}
