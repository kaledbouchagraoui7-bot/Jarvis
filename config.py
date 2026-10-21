"""
config.py — Investment Automaton centralised configuration.
=============================================================

Single source of truth for tickers, allocation percentages, IBKR connection
settings (read from the environment ONLY — never hardcoded credentials or
ports beyond safe paper-trading defaults) and the Swiss private-investor
compliance limits.

Security model
--------------
* Secrets / connection settings come from a `.env` file loaded via
  python-dotenv (see `.env.example`). Nothing sensitive is hardcoded here.
* `DRY_RUN` defaults to True: until you explicitly set DRY_RUN=false in
  `.env`, every module simulates orders and transfers without touching
  real money.

Swiss compliance note (NOT tax advice)
--------------------------------------
The limits below encode the widely-cited safe-haven criteria that distinguish
a tax-favoured *private investor* from a *professional securities trader*
under Swiss practice (ESTV/STA circular criteria, cantonal practice varies):
  1. holding period >= 6 months,
  2. annual transaction volume <= ~5x the beginning-of-period securities
     assets,
  3. no leverage / margin lending,
  4. no derivatives (except plain hedging),
  5. capital gains remain accessory to ordinary income.
The rules in `swiss_compliance_guard.py` are *owned* by the Tax Advisor and
Regulatory Compliance Officer agents of the Capital & Compliance Universe;
verify every threshold with a qualified Swiss tax advisor before live use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv is a hard requirement for live use; degrade gracefully
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.environ.get("JARVIS_RUNTIME_DIR", ROOT / "runtime"))
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = RUNTIME_DIR / "investment_log.txt"          # error + audit trail
LEDGER_FILE = RUNTIME_DIR / "investment_ledger.json"    # fills / lots (FIFO)
NEXUS_PENDING = RUNTIME_DIR / "nexus_pending.json"      # actions awaiting approval
NEXUS_HISTORY = RUNTIME_DIR / "nexus_history.json"      # append-only decisions
SWEEP_PLAN_DIR = RUNTIME_DIR / "sweep_plans"            # per-period allocation plans
COPYTRADE_TARGET = RUNTIME_DIR / "copytrade_target.json"
SWEEP_PLAN_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Cashflow sweep & routing (Part A §1-2)
# --------------------------------------------------------------------------
SWEEP_PROFIT_SHARE = float(os.environ.get("SWEEP_PROFIT_SHARE", "0.30"))   # 30% of net profit
ALLOC_PILLAR_3A = float(os.environ.get("ALLOC_PILLAR_3A", "0.40"))         # X% of the sweep -> 3a
ALLOC_IBKR = float(os.environ.get("ALLOC_IBKR", "0.60"))                  # Y% of the sweep -> IBKR
# Pillar 3a annual ceiling for employed persons with a pension fund.
# UPDATE EVERY YEAR (2025: CHF 7'258). Owned by the Tax Advisor agent.
PILLAR_3A_ANNUAL_CAP_CHF = float(os.environ.get("PILLAR_3A_ANNUAL_CAP_CHF", "7258.0"))
PILLAR_3A_PAID_YTD_CHF = float(os.environ.get("PILLAR_3A_PAID_YTD_CHF", "0.0"))
PILLAR_3A_PROVIDER_QR_REF = os.environ.get(           # printed on the QR-bill for manual execution
    "PILLAR_3A_PROVIDER_QR_REF", "CH-CREDITOR-REFERENCE-PLACEHOLDER")

# --------------------------------------------------------------------------
# IBKR connection (Part A §3) — environment only, paper-trading defaults
# --------------------------------------------------------------------------
IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7497"))        # 7497 TWS paper / 7496 live
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "11"))
IBKR_ACCOUNT_TYPE = os.environ.get("IBKR_ACCOUNT_TYPE", "CASH")  # CASH only: no margin, ever
IBKR_CONNECT_TIMEOUT_S = float(os.environ.get("IBKR_CONNECT_TIMEOUT_S", "10"))
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() not in ("0", "false", "no")

# --------------------------------------------------------------------------
# DCA targets (Part A §3 primary)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EtfTarget:
    symbol: str
    exchange: str          # IBKR exchange code
    currency: str
    note: str

DCA_TARGETS: dict[str, EtfTarget] = {
    "VWRL": EtfTarget("VWRL", "SIX", "CHF",
                      "Vanguard FTSE All-World UCITS ETF — CHF line, no FX conversion fee"),
    "VT": EtfTarget("VT", "ARCA", "USD",
                    "Vanguard Total World Stock ETF — USD line"),
}
PRIMARY_TICKER = os.environ.get("PRIMARY_TICKER", "VWRL")
LIMIT_MAX_OFFSET_BPS = float(os.environ.get("LIMIT_MAX_OFFSET_BPS", "25"))  # limit <= mid*(1+25bps)
DRY_RUN_PRICES = {"VWRL": 118.50, "VT": 132.40}        # simulated marks for DRY_RUN only
MIN_ORDER_CHF = float(os.environ.get("MIN_ORDER_CHF", "0.01"))            # sanity floor: one cent. No maximum by design.
FRACTIONAL_SHARES = os.environ.get("FRACTIONAL_SHARES", "true").lower() in ("1", "true", "yes")  # cash-quantity orders when amount < one share

# --------------------------------------------------------------------------
# Copy-trading (Part A §3 secondary) — strict parameter envelope
# --------------------------------------------------------------------------
COPYTRADE_ENABLED = os.environ.get("COPYTRADE_ENABLED", "false").lower() in ("1", "true", "yes")
COPYTRADE_MAX_NOTIONAL_PER_RUN_CHF = float(os.environ.get("COPYTRADE_MAX_NOTIONAL_PER_RUN_CHF", "2000"))
COPYTRADE_MAX_SINGLE_POSITION_PCT = float(os.environ.get("COPYTRADE_MAX_SINGLE_POSITION_PCT", "0.20"))

# --------------------------------------------------------------------------
# Swiss private-investor guardrail (Part A §4 — CRITICAL)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SwissComplianceLimits:
    min_holding_days: int = int(os.environ.get("MIN_HOLDING_DAYS", "180"))       # > 6 months
    max_annual_turnover_ratio: float = float(os.environ.get("MAX_TURNOVER_RATIO", "5.0"))
    allow_margin: bool = False            # hard-coded: never
    allow_derivatives: bool = False       # hard-coded: never
    allow_short_selling: bool = False     # hard-coded: never
    allowed_sec_types: tuple = ("STK",)   # plain stocks/ETFs only
    max_orders_per_month: int = int(os.environ.get("MAX_ORDERS_PER_MONTH", "12"))

LIMITS = SwissComplianceLimits()

# --------------------------------------------------------------------------
# Central Automaton budgets (bounded autonomy)
# --------------------------------------------------------------------------
AUTOMATON_CYCLE_INTERVAL_S = int(os.environ.get("AUTOMATON_CYCLE_INTERVAL_S", "300"))
AUTOMATON_MAX_CYCLES_PER_DAY = int(os.environ.get("AUTOMATON_MAX_CYCLES_PER_DAY", "48"))
AUTOMATON_MAX_PROPOSALS_PER_CYCLE = int(os.environ.get("AUTOMATON_MAX_PROPOSALS_PER_CYCLE", "6"))
AUTOMATON_MAX_WORKERS = int(os.environ.get("AUTOMATON_MAX_WORKERS", "3"))

# --------------------------------------------------------------------------
# Error handling (STRICT CONSTRAINT): bounded retries, graceful halt
# --------------------------------------------------------------------------
MAX_CONNECT_ATTEMPTS = 2          # one reconnect, then halt — never infinite
ORDER_SUBMIT_ATTEMPTS = 1         # a failed order is logged and the run halts

# --------------------------------------------------------------------------
# Business cashflow CSV schema (Part B -> Part A bridge)
# --------------------------------------------------------------------------
CSV_COLUMNS = ["period", "gross_income_chf", "business_expenses_chf", "tax_reserve_chf"]


def as_chf(x: float) -> float:
    """Round to Swiss centimes; the only rounding boundary in the suite."""
    return round(x + 1e-9, 2)


def summary() -> str:
    mode = "DRY-RUN (simulated)" if DRY_RUN else "LIVE — real orders possible"
    return (f"mode={mode} | sweep={SWEEP_PROFIT_SHARE:.0%} of net profit | "
            f"3a={ALLOC_PILLAR_3A:.0%} ibkr={ALLOC_IBKR:.0%} | "
            f"{PRIMARY_TICKER} @ {IBKR_HOST}:{IBKR_PORT} clientId={IBKR_CLIENT_ID} | "
            f"hold>={LIMITS.min_holding_days}d turnover<={LIMITS.max_annual_turnover_ratio}x "
            f"margin={LIMITS.allow_margin} derivs={LIMITS.allow_derivatives}")
