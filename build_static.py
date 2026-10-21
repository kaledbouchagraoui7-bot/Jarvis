"""
build_static.py — emit the AESTHETIC layer for Cloudflare Pages (dist/).
==========================================================================

Cloudflare Pages hosts STATIC output only: no Python processes, no SQLite,
no localhost servers. The live Jarvis system (console, automaton, Nexus,
IBKR engine) therefore stays on your machine at http://localhost:8010 —
that is a security feature, not a limitation.

What this builder produces (the "look" you deploy):
  dist/index.html         — public business landing page (your paragraph,
                            products, universes, governance story)
  dist/console-demo.html  — the REAL control-plane UI, frozen as a demo:
                            window.fetch is shimmed to return canned,
                            synthetic data; every button is cosmetic and a
                            fixed banner says so. Zero real data ships.

Cloudflare Pages settings:
  Framework preset      : None
  Build command         : python3 build_static.py
  Build output directory: dist
  Wrangler preview      : npx wrangler pages dev dist
  Wrangler deploy       : npx wrangler pages deploy dist --project-name=jarvis
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import webapp  # reuse the exact console UI (PAGE) — single source of truth

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

# ---------------------------------------------------------------- demo data --
DEMO = {
    "/api/state": {"ok": True, "mode": "DRY-RUN",
                   "config_line": "DEMO BUILD — synthetic data · live console runs at localhost:8010",
                   "audit": {"open_lots": 2, "fills": 3, "avg_holding_days": 214.0,
                             "generated": "2026-11-01T09:00:00"},
                   "health_warnings": [],
                   "pending_count": 2,
                   "plans": [
                       {"period": "2026-08", "net_profit_chf": 0.0, "investable_chf": 0.0,
                        "pillar_3a_chf": 0.0, "ibkr_chf": 0.0, "nexus_action_ids": []},
                       {"period": "2026-09", "net_profit_chf": 1800.0, "investable_chf": 540.0,
                        "pillar_3a_chf": 216.0, "ibkr_chf": 324.0,
                        "nexus_action_ids": ["NX-DEMO-0001", "NX-DEMO-0002"]},
                       {"period": "2026-10", "net_profit_chf": 5300.0, "investable_chf": 1590.0,
                        "pillar_3a_chf": 636.0, "ibkr_chf": 954.0,
                        "nexus_action_ids": ["NX-DEMO-0003", "NX-DEMO-0004"]}],
                   "limits": {"min_holding_days": 180, "max_annual_turnover_ratio": 5.0,
                              "max_orders_per_month": 12, "allow_margin": False,
                              "allow_derivatives": False, "allow_short_selling": False},
                   "sweep": {"share": 0.30, "alloc_3a": 0.40, "alloc_ibkr": 0.60,
                             "cap_3a": 7258.0, "ticker": "VWRL"}},
    "/api/nexus": {"ok": True,
                   "pending": [
                       {"id": "NX-DEMO-0003", "kind": "transfer_3a",
                        "payload": {"period": "2026-10", "amount_chf": 636.0,
                                    "qr_reference": "DEMO-QR-REF",
                                    "target": "Pillar 3a provider (manual QR-bill execution)"},
                        "proposed_by": "Cash Flow Manager agent (demo)",
                        "status": "PENDING", "staged_at": "2026-11-01T09:00:00"},
                       {"id": "NX-DEMO-0004", "kind": "ibkr_order",
                        "payload": {"period": "2026-10", "amount_chf": 954.0,
                                    "ticker": "VWRL",
                                    "target": "IBKR DCA buy VWRL (limit order)"},
                        "proposed_by": "Investment Analyst agent (proposal only, demo)",
                        "status": "PENDING", "staged_at": "2026-11-01T09:00:00"}],
                   "history": [
                       {"at": "2026-10-31T18:12:00", "id": "NX-DEMO-0002",
                        "kind": "ibkr_order", "decision": "APPROVED", "reason": ""},
                       {"at": "2026-10-31T18:11:00", "id": "NX-DEMO-0001",
                        "kind": "transfer_3a", "decision": "APPROVED", "reason": ""},
                       {"at": "2026-09-30T20:40:00", "id": "NX-DEMO-0000",
                        "kind": "spawn_worker", "decision": "REJECTED",
                        "reason": "not needed this month (demo)"}]},
    "/api/ledger": {"ok": True,
                    "lots": [{"ticker": "VWRL", "qty": 7, "opened_ts": 1759276800,
                              "opened_date": "2026-10-01"},
                             {"ticker": "VWRL", "qty": 2, "opened_ts": 1756684800,
                              "opened_date": "2026-09-01"}],
                    "fills": [
                        {"ts": 1759276800, "date": "2026-10-01", "side": "BUY",
                         "ticker": "VWRL", "qty": 7, "price": 118.8, "currency": "CHF",
                         "notional_chf": 831.6, "fx": 1.0},
                        {"ts": 1756684800, "date": "2026-09-01", "side": "BUY",
                         "ticker": "VWRL", "qty": 2, "price": 117.9, "currency": "CHF",
                         "notional_chf": 235.8, "fx": 1.0}],
                    "portfolio_value_start_chf": 0.0,
                    "start_of_period": "2026-09-01"},
    "/api/log": {"ok": True, "text":
                 "[2026-11-01 09:00:00] [AUTOMATON d0] [INFO] cycle 42 done: "
                 "{'proposals': 2, 'executions': 0, 'spawns': 0, 'workers_merged': 1}\n"
                 "[2026-11-01 08:55:00] [NEXUS] staged NX-DEMO-0004 kind=ibkr_order "
                 "awaiting human approval\n"
                 "[2026-10-31 18:12:00] [NEXUS] decision APPROVED on NX-DEMO-0002\n"
                 "[2026-10-31 18:12:01] [IBKR-DCA] [INFO] DRY-RUN fill 7 x VWRL @ 118.80 CHF\n"
                 "(demo log — synthetic lines)"},
    "/api/automaton": {"ok": True,
                       "heartbeat": {"ts": "2026-11-01T09:00:00", "mode": "DRY-RUN",
                                     "cycle": 42, "depth": 0, "halted": False,
                                     "summary": {"proposals": 2, "executions": 0,
                                                 "spawns": 0, "workers_merged": 1},
                                     "workers_active": 1},
                       "state": {"total_cycles": 42},
                       "halted": False,
                       "workers": [{"id": "W-DEMO-ledger_audit", "role": "ledger_audit",
                                    "status": "done", "finished": "2026-11-01T08:59:00"}],
                       "pending": [], "inbox": ["biz_2026-11.csv (demo)"],
                       "limits": {"max_cycles_per_day": 48, "max_workers": 3,
                                  "max_proposals_per_cycle": 6}},
}

DEMO_SHIM = """<script>
/* STATIC DEMO SHIM — window.fetch returns canned synthetic data only. */
window.__JARVIS_DEMO__ = true;
const __DEMO__ = %s;
window.fetch = (url, opts) => {
  const path = String(url).split("?")[0];
  const method = (opts && opts.method) || "GET";
  let body;
  if (method === "GET") body = __DEMO__[path] || {ok: true};
  else if (path === "/api/nexus/decide") body = {ok: true, id: "NX-DEMO", decision: "approve"};
  else if (path === "/api/automaton/halt") body = {ok: true, halted: true};
  else if (path === "/api/automaton/resume") body = {ok: true, halted: false};
  else if (path === "/api/automaton/spawn-request") body = {ok: true, id: "NX-DEMO-SPAWN"};
  else body = {ok: true};
  return Promise.resolve({ok: true, status: 200,
    json: async () => body, text: async () => JSON.stringify(body)});
};
</script>
"""

DEMO_BAR_CSS = (".demobar{position:fixed;top:0;left:0;right:0;z-index:99;background:"
                "linear-gradient(90deg,#7a2c2c,#4a1a1a);color:#ffd7d7;text-align:center;"
                "padding:7px 12px;font-family:var(--mono);font-size:11.5px;letter-spacing:1px}"
                "#layout{padding-top:34px}")

LANDING = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JARVIS — Sovereign AI Enterprise</title>
<meta name="description" content="A lean, tech-enabled Swiss solo enterprise: 110 specialist AI agents in seven universes, a Nexus-gated central automaton, and a compliance-bound Investment Automaton.">
<style>
:root{--bg:#07090d;--panel:#0d1219;--line:#1d2735;--au:#d4a24c;--tx:#dfe8f4;--dim:#8494ab;
--mono:ui-monospace,'Cascadia Mono',Consolas,monospace;--sans:system-ui,'Segoe UI',Roboto,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--tx);font-family:var(--sans);line-height:1.65}
body::before{content:"";position:fixed;inset:0;pointer-events:none;background:
 radial-gradient(1000px 480px at 70% -12%,rgba(212,162,76,.09),transparent 60%),
 radial-gradient(800px 420px at -10% 110%,rgba(91,200,232,.06),transparent 60%)}
.wrap{max-width:1020px;margin:0 auto;padding:0 26px;position:relative;z-index:1}
header{padding:80px 0 30px;text-align:center}
.brand{font-family:var(--mono);letter-spacing:8px;font-size:15px;color:var(--au)}
h1{font-size:clamp(30px,5vw,52px);line-height:1.12;margin:18px 0 10px;font-weight:800}
h1 em{color:var(--au);font-style:normal}
.tag{color:var(--dim);font-size:17px;max-width:760px;margin:0 auto}
.cta{margin:30px 0 10px;display:flex;gap:14px;justify-content:center;flex-wrap:wrap}
.btn{display:inline-block;padding:13px 26px;border-radius:11px;font-weight:700;font-size:15px;text-decoration:none;transition:.15s}
.btn.gold{background:linear-gradient(135deg,#8a6a2f,#5c451d);color:#ffe9c2;box-shadow:0 4px 22px rgba(212,162,76,.25)}
.btn.ghost{border:1px solid var(--line);color:var(--dim)}
.btn:hover{filter:brightness(1.2);transform:translateY(-1px)}
section{padding:52px 0}
h2{font-family:var(--mono);font-size:13px;letter-spacing:4px;color:var(--au);text-transform:uppercase;margin-bottom:20px}
p.lede{font-size:16.5px;color:#c3cfdf;max-width:880px}
.grid{display:grid;gap:16px;margin-top:22px}.g2{grid-template-columns:1fr 1fr}.g4{grid-template-columns:repeat(4,1fr)}
@media(max-width:860px){.g2,.g4{grid-template-columns:1fr 1fr}}@media(max-width:560px){.g2,.g4{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px}
.card h3{font-size:16px;margin-bottom:8px}.card h3 span{color:var(--au)}
.card p{font-size:13.5px;color:var(--dim)}
.uni{font-family:var(--mono);font-size:12px;letter-spacing:1px;color:var(--dim);padding:12px 14px;border:1px solid var(--line);border-radius:10px;text-align:center}
.uni b{display:block;color:var(--au);font-size:13px;margin-bottom:4px}
.quote{border-left:3px solid var(--au);padding:6px 0 6px 20px;margin:26px 0;color:#c9d4e2;font-size:15.5px;font-style:italic}
footer{padding:44px 0 60px;color:#4a5870;font-size:12px;font-family:var(--mono);letter-spacing:1px;text-align:center}
.kv{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px dashed rgba(29,39,53,.9);font-size:13.5px}
.kv b{font-family:var(--mono);color:var(--au)}
</style></head><body>
<header><div class="wrap">
 <div class="brand">⚡ J A R V I S</div>
 <h1>The sovereign AI enterprise<br>where <em>every action answers to you</em></h1>
 <p class="tag">A lean, tech-enabled Swiss solo enterprise — 110 specialist agents,
 seven universes, one human gate.</p>
 <div class="cta">
  <a class="btn gold" href="console-demo.html">Explore the control plane (demo)</a>
  <a class="btn ghost" href="https://github.com/kaledbouchagraoui7-bot/Jarvis">View the system</a>
 </div>
</div></header>
<section><div class="wrap">
 <h2>The enterprise</h2>
 <p class="lede">JARVIS arbitrages the gap between the micro-cost of AI compute and the
 market value of B2B digital deliverables: instead of selling human hours, it manufactures
 two product lines at scale — hyper-personalized B2B lead enrichment (scraped, validated
 decision-maker data plus LLM-written outreach tailored to each prospect) and programmatic
 SEO (autonomously built, long-tail landing pages fed by live data streams) — produced by a
 self-orchestrating workforce of 110 specialist AI agents organized in a seven-universe
 modular hierarchy under a central automaton that perceives, proposes and replicates only
 within hard budgets. Launched with zero capital on open-source automation and an automated
 outbound sales engine that converts retainer clients, the company reinvests a fixed share
 of every month's net profit through a compliance-gated Investment Automaton (Pillar 3a +
 IBKR ETF dollar-cost averaging under Swiss private-investor guardrails), marching from
 bootstrap to sovereignty — cloud tools first, owned local hardware last — while every
 external, financial or high-stakes action, including each act of machine self-replication,
 passes through <b>The Nexus</b>: a human-in-the-loop gate where the founder's keystroke is
 the only key that turns automation into action.</p>
</div></section>
<section><div class="wrap">
 <h2>Product lines</h2>
 <div class="grid g2">
  <div class="card"><h3><span>01 ·</span> Hyper-Personalized B2B Lead Enrichment</h3>
   <p>Decision-maker data scraped, validated and enriched at machine cost, then turned into
   outreach copy written per-prospect by LLMs — delivered as retainer subscriptions.</p></div>
  <div class="card"><h3><span>02 ·</span> Programmatic SEO at Scale</h3>
   <p>Live data streams ingested and transformed into long-tail landing pages that rank —
   hundreds of pages per client, built and refreshed autonomously.</p></div>
 </div>
</div></section>
<section><div class="wrap">
 <h2>The seven universes · 110 specialists</h2>
 <div class="grid g4">
  <div class="uni"><b>U1 REVENUE</b>24 agents · demand & customers</div>
  <div class="uni"><b>U2 DELIVERY</b>23 agents · ops & product</div>
  <div class="uni"><b>U3 CAPITAL</b>21 agents · finance & compliance</div>
  <div class="uni"><b>U4 PEOPLE</b>10 agents · culture & growth</div>
  <div class="uni"><b>U5 INFRA</b>10 agents · tech & security</div>
  <div class="uni"><b>U6 STRATEGY</b>11 agents · direction & intel</div>
  <div class="uni"><b>U7 CONTINUITY</b>11 agents · admin & knowledge</div>
  <div class="uni"><b>THE NEXUS</b>1 human · final authority</div>
 </div>
</div></section>
<section><div class="wrap">
 <h2>Governance by design</h2>
 <div class="grid g2">
  <div class="card"><h3><span>◈</span> Bounded autonomy</h3>
   <p>The central automaton perceives, proposes and replicates within hard budgets —
   cycles per day, proposals per cycle, concurrent workers — and freezes instantly on
   the kill switch.</p>
   <div class="kv"><span>Max cycles / day</span><b>48</b></div>
   <div class="kv"><span>Max proposals / cycle</span><b>6</b></div>
   <div class="kv"><span>Replication depth</span><b>1 (Core → worker)</b></div></div>
  <div class="card"><h3><span>◈</span> Swiss private-investor guardrail</h3>
   <p>The Investment Automaton sweeps a fixed share of net profit into Pillar 3a and
   ETF dollar-cost averaging under hard-coded limits.</p>
   <div class="kv"><span>Min holding period</span><b>180 days</b></div>
   <div class="kv"><span>Max annual turnover</span><b>5× assets</b></div>
   <div class="kv"><span>Margin / derivatives / short</span><b>PROHIBITED</b></div></div>
 </div>
 <div class="quote">"The automaton may think, propose, replicate and audit freely — but
 every franc, every order and every new instance of itself passes through a gate only
 your keystroke can open."</div>
</div></section>
<footer>JARVIS ENTERPRISES · ZÜRICH → GENÈVE · BOOTSTRAP → SOVEREIGNTY · NOT FINANCIAL ADVICE</footer>
</body></html>
"""


def build_demo_console(roster: dict) -> str:
    page = webapp.PAGE
    demo = json.loads(json.dumps(DEMO))  # copy
    demo["/api/roster"] = {"ok": True, **roster}
    shim = DEMO_SHIM % json.dumps(demo)
    assert "<script>" in page
    page = page.replace("<script>\n\"use strict\";", shim + "<script>\n\"use strict\";", 1)
    page = page.replace("</style>", DEMO_BAR_CSS + "</style>", 1)
    page = page.replace("<body>\n<div id=\"layout\">",
                        "<body>\n<div class=\"demobar\">STATIC DEMO — SYNTHETIC DATA ONLY · "
                        "THE LIVE CONSOLE RUNS ON THE FOUNDER'S MACHINE AT localhost:8010"
                        "</div>\n<div id=\"layout\">", 1)
    return page


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    roster = json.loads((ROOT / "agent_universes.json").read_text(encoding="utf-8"))
    (DIST / "index.html").write_text(LANDING, encoding="utf-8")
    (DIST / "console-demo.html").write_text(build_demo_console(roster), encoding="utf-8")
    print(f"dist/ built at {time.strftime('%Y-%m-%d %H:%M:%S')}:")
    for p in sorted(DIST.iterdir()):
        print(f"  {p.name:<20} {p.stat().st_size:>8,} bytes")
    print("Cloudflare Pages → build command: python3 build_static.py | output: dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
