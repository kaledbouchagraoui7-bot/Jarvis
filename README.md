# Jarvis — AI Orchestrator & Investment Automaton

One system, three layers:

| Layer | What it is | Entry point |
|---|---|---|
| **A — Investment Automaton** | Tax-compliant automation bridging business cashflow → Pillar 3a + IBKR DCA, behind hard Swiss private-investor guardrails | `cashflow_bridge.py`, `ibkr_dca_engine.py`, `swiss_compliance_guard.py`, `copytrade_mirror.py` |
| **B — Agent-run business hierarchy** | 110 specialist agents distributed across **7 universes** (10 category groups feed universes as blocks; the Specialized Roles pool is distributed per-agent) | `agent_universes.json` |
| **C — The Nexus** | Human-in-the-loop control gate: **no transfer, no order, no copy-trade executes without explicit human approval**, on top of the compliance guardrail | `nexus_gate.py` |

```
business income CSV ──> cashflow_bridge.py ──> sweep plan (30% of net profit)
        │                     │                        │
        │                     ├─> Pillar 3a leg ─> QR-bill ref (manual) ─┐
        │                     └─> IBKR leg ─> sweep_plans/plan_*.json    │
        │                                                  │             │
        │                          swiss_compliance_guard  │             │
        │                                  (hard block)    ▼             ▼
        │                          ibkr_dca_engine.py / transfer   THE NEXUS
        │                                  (limit order)      (human approve)
        │                                                  │
        └─ Cash Flow Manager agent supplies figures        ▼
                                   runtime/investment_ledger.json + investment_log.txt
                                                  │
                                                  └─ Auditing Agent reviews
```

> **Not financial, tax or legal advice.** The guardrail encodes conservative,
> configurable safe-haven criteria for Swiss *private-investor* status
> (ESTV/STA practice; cantonal practice varies). Have a qualified Swiss tax
> advisor validate every threshold in `config.LIMITS` before live use.
> `DRY_RUN=true` is the default: until you flip it in `.env`, everything simulates.

---

## 1. Setup

```bash
git clone https://github.com/kaledbouchagraoui7-bot/Jarvis.git && cd Jarvis
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env (NEVER commit it — gitignored)
python selftest.py            # 26 offline checks; must print ALL GREEN
```

Requires Python 3.10+. `ib_insync` is imported lazily: **dry-run works without
TWS installed**.

### IBKR TWS / Gateway API permissions (one-time)
1. Install IBKR **TWS** (or IB Gateway) and log in to a **paper** account first.
2. TWS: `File → Global Configuration → API → Settings`:
   - tick **Enable ActiveX and Socket Clients**;
   - note the socket port (paper TWS `7497`, live TWS `7496`; Gateway paper `4002`, live `4001`);
   - keep **Download open orders on each connection** off unless needed;
   - under `API → Precautions`, leave order-confirmation settings as you prefer — the Automaton only submits limit orders sized by the sweep plan.
3. Allow localhost: in the same Settings pane, ensure connections from `127.0.0.1` are trusted (default) — never expose the socket to your network.
4. Put host/port/client-id in `.env` (`IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`); **no credentials or ports are hardcoded in the repository**.
5. Verify with `DRY_RUN=false` only after a full paper cycle succeeded.

### Monthly routine (the intended cadence)
```bash
python cashflow_bridge.py business_income_2026-11.csv   # 1. sweep + route + stage
python nexus_gate.py list                               # 2. human review
python nexus_gate.py approve NX-... --confirm I-AUTHORISE-THIS-ACTION
python ibkr_dca_engine.py --plan 2026-11                # 3. guarded execution
python -c "import swiss_compliance_guard as g; print(g.audit_summary())"  # 4. audit
```

### Scheduling
**Windows Task Scheduler** (first business day of month, reminder-style; the
Nexus step always stays manual):
```powershell
schtasks /Create /SC MONTHLY /D 1 /TN "JarvisSweep" /TR "cmd /c cd /d C:\path\Jarvis && .venv\Scripts\python.exe cashflow_bridge.py income.csv" /ST 09:00
```
**cron (Linux/macOS):**
```cron
0 9 1 * * cd /path/to/Jarvis && .venv/bin/python cashflow_bridge.py income.csv
```
Never schedule `ibkr_dca_engine.py` unattended: it refuses to run without a
Nexus approval that a human must type.

---

## 2. The Nexus — how approval is triggered before every investment action

1. **Stage.** Producers (`cashflow_bridge`, `copytrade_mirror`) call
   `nexus_gate.stage(kind, payload)` with `kind ∈ {transfer_3a, ibkr_order,
   copytrade_rebalance}`. The **Investment Analyst agent may only propose** —
   staging is a proposal, never an execution.
2. **Block.** Execution modules call `nexus_gate.require_approval(id)`
   immediately before any irreversible step; it raises `NexusDenied` unless
   status is `APPROVED` (unknown ids fail closed).
3. **Decide.** A human runs `nexus_gate.py list / show / approve / reject`.
   Approval requires typing the exact phrase `I-AUTHORISE-THIS-ACTION`.
4. **Audit.** Every decision appends to `runtime/nexus_history.json` and
   `runtime/investment_log.txt`; the **Auditing Agent** reviews both after
   every sweep and monthly.

Order of gates is fixed: **compliance guardrail first, Nexus second,
execution third.** A compliant-but-unapproved action never executes; an
approved-but-non-compliant action never executes.

---

## 3. The 7-universe modular hierarchy (Part B)

`agent_universes.json` is the configuration source of truth: every agent
appears in **exactly one universe**, names and descriptions verbatim.

| Universe | Mission | Fed by category groups | Agents |
|---|---|---|---|
| **U1 Revenue** | demand, customers, market presence | Customer Interaction; Marketing and Sales | 24 |
| **U2 Delivery** | build & deliver: ops, logistics, product | Operations and Logistics; Product Development | 23 |
| **U3 Capital & Compliance** | money, tax, legal control — owns the Automaton's rules | Finance and Accounting; Legal and Compliance | 21 |
| **U4 People** | hiring, growth, culture | Human Resources | 10 |
| **U5 Infrastructure** | tech, data, security, resilience | IT and Security | 10 |
| **U6 Strategy** | direction, intelligence, scenarios | Strategic Management | 11 |
| **U7 Continuity** | admin, knowledge, communication, crisis | Administrative Support | 11 |

The ten category groups feed universes **as blocks** (table above); the
**Specialized Roles** pool (Sustainability Coordinator, Localization Expert,
Crisis Manager, Data Privacy Officer, Accessibility Specialist, Innovation
Scout, Customer Success Manager, Vendor Manager, Negotiation Specialist,
Cultural Advisor) is distributed **per agent** across U1/U2/U3/U6/U7 — see
`specialized_roles` in the JSON. Totals: 100 grouped + 10 specialized = **110
agents in 7 universes** (the repo's original "90" tagline predates the full
roster; `selftest.py` enforces the invariants).

### Finance ↔ Automaton wiring (Part C)
* **Cash Flow Manager** supplies the figures `cashflow_bridge.py` consumes (the business income CSV).
* **Investment Analyst** may **propose** allocations only — execution exists solely behind The Nexus.
* **Tax Advisor** + **Regulatory Compliance Officer** own every rule enforced by `swiss_compliance_guard.py` (thresholds in `config.LIMITS`, env-overridable, sign-off required).
* **Auditing Agent** reviews `investment_log.txt` + `nexus_history.json` after every sweep and monthly.
* Zero or negative net profit ⇒ **nothing is routed** (bootstrap discipline); the period is still logged and a zero-plan written for the audit trail.

---

## 4. Swiss compliance guardrail (`swiss_compliance_guard.py`)

Hard-coded (cannot be enabled by config): **no margin/leverage, no
derivatives, no short selling, plain STK instruments only, CASH account
type.** Configurable limits (owned by Tax Advisor / Regulatory Compliance
Officer): minimum holding period (FIFO lots, default 180 days), trailing
12-month turnover ratio ≤ 5× beginning-of-period assets, order-frequency
cap, plus a weighted-average holding-period health metric. Every check
writes to the audit log; violations halt the run.

## 5. Security & error handling
* Secrets/endpoint settings only via `.env` (`python-dotenv`); `.env` and the
  whole `runtime/` directory (logs, ledger, Nexus decisions, sweep plans —
  your personal financial data) are gitignored.
* Bounded retries: at most `MAX_CONNECT_ATTEMPTS` connects and
  `ORDER_SUBMIT_ATTEMPTS` submissions; any failure is logged to
  `runtime/investment_log.txt` and the process **halts** (exit 1). No
  infinite retry loops.
* Copy-trading (`copytrade_mirror.py`) is disabled by default, capped per run
  and per position, compliance- and Nexus-gated, and accepts only a signed
  local snapshot file until you plug in a vetted provider client.

## 6. Files
```
config.py                central config: tickers, allocations, IBKR env settings, compliance limits
cashflow_bridge.py       CSV -> sweep -> 3a/IBKR routing -> Nexus staging
ibkr_dca_engine.py       ib_insync DCA: compliance -> Nexus -> LIMIT order -> ledger
swiss_compliance_guard.py private-investor guardrail + FIFO holding/turnover trackers
nexus_gate.py            The Nexus human approval gate (CLI + fail-closed API)
copytrade_mirror.py      secondary copy-trading adapter (strict envelope, gated)
agent_universes.json     110-agent roster -> 7 universes + finance wiring
selftest.py              26 offline checks (roster, cashflow, nexus, guard, engine)
sample_data/             example business income CSV
.env.example             every knob, documented; copy to .env
runtime/                 (gitignored) logs, ledger, Nexus store, sweep plans
```

---

## 7. Control Plane (`webapp.py`) — the business console

Public aesthetic layer (Cloudflare Pages): **https://jarvis-8wr.pages.dev**
(landing + `console-demo.html` demo console with synthetic data).
`dist/` is committed so the Cloudflare pipeline deploys on every push.

App-style, localhost-only web console (stdlib-only, no new dependencies, no
CDNs) over the same runtime state the CLI writes:

```powershell
python webapp.py          # opens http://localhost:8010
```

| Tab | Shows / does |
|---|---|
| Dashboard | mode (DRY-RUN/LIVE), Nexus inbox count, FIFO lots + avg holding, sweep policy, hard guardrails, latest plans |
| The Nexus | review each staged action's full payload; **approve (phrase required) / reject (reason required)** from the browser; decision history |
| Sweep Plans | per-period routing cards: net profit → investable → 3a / IBKR legs + QR reference |
| Ledger | open FIFO lots (age colour-coded vs the 180-day rule) + fill history |
| Compliance | enforced limits, portfolio-health warnings, audit snapshot for the Auditing Agent |
| Audit Log | live tail of `runtime/investment_log.txt` |
| Universes | the 7-universe / 110-agent hierarchy with the Automaton wiring |

Design rule: **the console decides, the terminal executes** — approval is
possible from the UI (it *is* the human gate), but order execution stays in
`ibkr_dca_engine.py`, so no browser session can silently trade.

---

## 8. The Central Automaton (`automaton.py`) — bounded autonomy

A self-replicating, self-scheduling Core that runs the business **under your
per-action approval**:

```
perceive (inbox CSVs, ledger, plans, worker reports)
   -> propose (stage Nexus actions: transfers, orders, replications)
   -> YOU approve (console or CLI — the only path to execution)
   -> execute (engines re-verify approval + compliance internally)
```

```powershell
python automaton.py daemon          # bounded loop (default 300s interval)
python automaton.py run             # single cycle (cron-friendly)
python automaton.py request-spawn --role ledger_audit   # ask to replicate
python automaton.py status | workers
python automaton.py halt | resume   # kill switch (console has buttons too)
```

**Autonomy cage (all enforced in code, verified by `selftest.py`):**
* replication is Nexus-gated — the Core stages a `spawn_worker` action and a
  human approval is required before a worker process starts;
* depth cap 1: **workers can never replicate** (Core → worker, end of line);
* budgets: max cycles/day (48), max proposals/cycle (6), max concurrent
  workers (3) — all env-overridable;
* kill switch: `runtime/automaton_STOP` freezes cycles, daemon and workers;
* execution is delegated, not invented: the Core only runs engines on
  APPROVED actions, and `ibkr_dca_engine` re-checks the gate itself;
* workers are read-mostly bounded tasks (`worker_roles.py`: ledger_audit,
  income_validate, plan_review, compliance_watch) writing a single report;
* every perception, proposal, spawn and execution lands in
  `runtime/investment_log.txt` for the Auditing Agent.

**Business drop-zone:** put monthly income CSVs into `runtime/inbox/` — the
next cycle computes sweep plans and stages them in your Nexus inbox. Zero or
negative months stage nothing. Phase 2/3 business roles (pSEO drafts, lead
validation, client reports) plug into `worker_roles.py` with the same
contract: pure function → JSON report.
