#!/usr/bin/env python3
"""
webapp.py — JARVIS CONTROL PLANE: app-style interface for the Investment
Automaton + the 7-universe agent hierarchy.
============================================================================

Zero new dependencies (Python stdlib HTTP server + one embedded HTML page,
inline CSS/JS, no CDNs). Reads the same runtime state the CLI modules write:
sweep plans, Nexus inbox/history, FIFO ledger, compliance audit, investment
log, agent roster.

The Nexus stays a HUMAN gate in the browser too: approving requires typing
the exact confirmation phrase into the console UI; rejecting requires a
reason. Execution (ibkr_dca_engine / copytrade_mirror) remains CLI-only by
design — the console decides, the terminal executes.

Run:
    python webapp.py                 # http://127.0.0.1:8010, opens browser
    python webapp.py --port 8020
    python webapp.py --host 0.0.0.0  # LAN (phone) — think twice, it can approve
    python webapp.py --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config
import nexus_gate as nx
import swiss_compliance_guard as guard
import automaton

ROOT = Path(__file__).resolve().parent
ROSTER_FILE = ROOT / "agent_universes.json"
MAX_BODY = 64 * 1024


# ------------------------------------------------------------------ state --
def _plans() -> list:
    out = []
    for p in sorted(config.SWEEP_PLAN_DIR.glob("plan_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_file"] = p.name
            out.append(d)
        except json.JSONDecodeError:
            out.append({"period": p.stem.replace("plan_", ""), "_file": p.name,
                        "error": "unreadable plan"})
    return out


def _log_tail(lines: int = 120) -> str:
    if not config.LOG_FILE.exists():
        return "(log empty — no runs yet)"
    return "\n".join(config.LOG_FILE.read_text(encoding="utf-8",
                       errors="replace").splitlines()[-lines:])


def _roster() -> dict:
    return json.loads(ROSTER_FILE.read_text(encoding="utf-8"))


def _state() -> dict:
    audit = guard.audit_summary()
    health = guard.portfolio_health(0.0)
    return {
        "mode": "DRY-RUN" if config.DRY_RUN else "LIVE",
        "config_line": config.summary(),
        "audit": audit,
        "health_warnings": health.warnings,
        "pending_count": len(nx.list_pending()),
        "plans": _plans(),
        "limits": {
            "min_holding_days": config.LIMITS.min_holding_days,
            "max_annual_turnover_ratio": config.LIMITS.max_annual_turnover_ratio,
            "max_orders_per_month": config.LIMITS.max_orders_per_month,
            "allow_margin": config.LIMITS.allow_margin,
            "allow_derivatives": config.LIMITS.allow_derivatives,
            "allow_short_selling": config.LIMITS.allow_short_selling,
        },
        "sweep": {"share": config.SWEEP_PROFIT_SHARE,
                  "alloc_3a": config.ALLOC_PILLAR_3A,
                  "alloc_ibkr": config.ALLOC_IBKR,
                  "cap_3a": config.PILLAR_3A_ANNUAL_CAP_CHF,
                  "ticker": config.PRIMARY_TICKER},
    }


# ------------------------------------------------------------------- page --
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JARVIS · Control Plane</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🤖</text></svg>">
<style>
:root{--paper:#F2EEE7;--paper2:#EAE4D9;--card:#FBFAF7;--ink:#1E1B16;--ink2:#57503F;
--taupe:#A99C86;--sand:#C9BFAF;--line:#DAD3C6;--sage:#6B8F71;--terra:#A4553F;--ochre:#B98A2E;
--serif:'Cormorant Garamond','Iowan Old Style',Georgia,'Times New Roman',serif;
--sans:system-ui,'Segoe UI',Roboto,sans-serif;
--mono:ui-monospace,'Cascadia Mono',Consolas,monospace;
--grain:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E")}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);min-height:100vh;font-size:14px}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;background:
 var(--grain),
 linear-gradient(112deg,transparent 40%,rgba(255,255,255,.55) 46%,transparent 56%),
 linear-gradient(112deg,transparent 66%,rgba(255,255,255,.4) 72%,transparent 82%)}
#layout{display:flex;min-height:100vh;position:relative;z-index:1}
nav{width:236px;flex-shrink:0;background:var(--paper2);border-right:1px solid var(--line);
 padding:26px 14px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:2px}
.logo{font-family:var(--serif);font-size:17px;letter-spacing:.42em;color:var(--ink);padding:0 10px 4px;font-weight:600}
.logo small{display:block;font-family:var(--sans);color:var(--taupe);font-size:8.5px;letter-spacing:.34em;margin-top:5px}
nav button{all:unset;cursor:pointer;padding:9px 12px;color:var(--ink2);font-size:10.5px;letter-spacing:.22em;
 text-transform:uppercase;display:flex;gap:10px;align-items:baseline;transition:.15s;border-left:1px solid transparent}
nav button .no{font-family:var(--mono);font-size:9px;color:var(--taupe);letter-spacing:.1em}
nav button:hover{color:var(--ink);background:rgba(255,255,255,.5)}
nav button.on{background:var(--ink);color:var(--paper);border-left:1px solid var(--ink)}
nav button.on .no{color:var(--sand)}
.mode{margin-top:auto;padding:9px 12px;border:1px solid var(--line);background:var(--card);
 font-family:var(--mono);font-size:9.5px;letter-spacing:.24em;text-align:center}
.mode.dry{color:var(--ink2)}.mode.live{color:var(--terra);border-color:var(--terra)}
main{flex:1;padding:34px 38px 80px;max-width:1140px}
h1{font-family:var(--serif);font-size:31px;font-weight:600;letter-spacing:.01em;margin-bottom:6px}
h1 .tag{font-family:var(--serif);font-style:italic;font-size:17px;color:var(--taupe);letter-spacing:0}
.sub{color:var(--ink2);font-size:13px;margin-bottom:26px;max-width:760px;line-height:1.6}
h2{font-size:10px;letter-spacing:.3em;color:var(--taupe);text-transform:uppercase;margin:28px 0 12px;
 padding-top:14px;border-top:1px solid var(--line)}
.grid{display:grid;gap:14px}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:repeat(3,1fr)}.g4{grid-template-columns:repeat(4,1fr)}
@media(max-width:960px){.g2,.g3,.g4{grid-template-columns:1fr 1fr}}@media(max-width:640px){.g2,.g3,.g4{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:2px;padding:18px 20px;
 box-shadow:0 1px 0 rgba(30,27,22,.05)}
.card h3{font-size:9.5px;letter-spacing:.28em;color:var(--taupe);text-transform:uppercase;margin-bottom:12px;
 display:flex;justify-content:space-between;align-items:center;gap:8px}
.big{font-family:var(--serif);font-size:30px;font-weight:600;line-height:1.1}
.ok{color:var(--sage)}.bad{color:var(--terra)}.warn{color:var(--ochre)}.info{color:#4E718B}.gold{color:#8A6A2F}
.dim{color:#8B8272}.note{font-size:12px;color:var(--ink2);line-height:1.55}
.pill{display:inline-block;padding:2px 8px;font-size:9px;font-family:var(--sans);letter-spacing:.18em;
 text-transform:uppercase;border:1px solid var(--line);border-radius:1px;background:var(--paper)}
.pill.ok{border-color:var(--sage);color:var(--sage)}
.pill.bad{border-color:var(--terra);color:var(--terra)}
.pill.warn{border-color:var(--ochre);color:var(--ochre)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{font-size:9px;letter-spacing:.22em;text-transform:uppercase;color:var(--taupe);text-align:left;
 padding:8px 10px;border-bottom:1px solid var(--ink)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;color:var(--ink2)}
tr:hover td{background:rgba(169,156,134,.07)}
td.num,th.num{text-align:right;font-family:var(--mono);font-size:11.5px}
.scroll{overflow:auto;max-height:420px}
pre{font-family:var(--mono);font-size:11px;line-height:1.6;color:var(--ink2);white-space:pre-wrap;
 background:var(--paper);border:1px solid var(--line);padding:12px;border-radius:2px}
form{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
label{display:block;font-size:9px;letter-spacing:.24em;color:var(--taupe);text-transform:uppercase;margin-bottom:5px}
input,select{background:var(--card);border:1px solid var(--line);border-radius:2px;color:var(--ink);
 padding:9px 11px;font-size:13px;font-family:var(--sans);outline:none;min-width:0}
input:focus,select:focus{border-color:var(--ink)}
.btn{all:unset;cursor:pointer;background:var(--ink);color:var(--paper);padding:10px 18px;border-radius:1px;
 font-size:10px;font-weight:600;letter-spacing:.22em;text-transform:uppercase;text-align:center;transition:.15s}
.btn:hover{background:#3a352c}
.btn.red{background:var(--terra);color:#FBF3EF}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--ink2)}
.btn.ghost:hover{border-color:var(--ink);color:var(--ink)}
.kv{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:12.5px;color:var(--ink2)}
.kv:last-child{border-bottom:0}
.kv b{font-family:var(--mono);font-size:11.5px;color:var(--ink)}
details{border:1px solid var(--line);border-radius:2px;padding:12px 16px;margin-bottom:9px;background:var(--card)}
summary{cursor:pointer;font-size:10.5px;letter-spacing:.24em;text-transform:uppercase;color:var(--ink);font-family:var(--sans)}
details ul{margin:10px 0 4px 18px;font-size:12.5px;color:var(--ink2);line-height:1.9}
.banner{border-radius:2px;padding:13px 17px;margin-bottom:16px;font-size:13px;border:1px solid;line-height:1.55}
.banner.red{background:#F6ECE8;border-color:var(--terra);color:#7C3A2D}
.banner.green,.banner.blue{background:#EEF1EA;border-color:var(--sage);color:#3F5D44}
.hidden{display:none!important}
#toast{position:fixed;bottom:24px;right:24px;z-index:60;display:flex;flex-direction:column;gap:8px}
.tmsg{background:var(--ink);color:var(--paper);border-left:3px solid var(--taupe);padding:12px 16px;
 border-radius:1px;font-size:12.5px;max-width:360px;box-shadow:0 10px 30px rgba(30,27,22,.25);letter-spacing:.03em}
.tmsg.bad{border-left-color:var(--terra)}.tmsg.ok{border-left-color:var(--sage)}
footer{margin-top:52px;color:var(--taupe);font-size:9.5px;letter-spacing:.26em;text-transform:uppercase}
.mdtable td,.mdtable th{font-size:12px}
a{color:#8A6A2F}
@media(max-width:760px){nav{width:58px;padding:16px 6px}nav button span.lbl,nav .logo small,.mode{display:none}
 nav button .no{width:auto}main{padding:20px 16px 60px}.logo{letter-spacing:.1em;font-size:14px}}
</style>
</head>
<body>
<div id="layout">
<nav>
 <div class="logo">JARVIS<small>CONTROL PLANE · MMXXVI</small></div>
 <button data-p="dash" class="on"><span class="no">01</span><span class="lbl">Dashboard</span></button>
 <button data-p="nexus"><span class="no">02</span><span class="lbl">The Nexus</span></button>
 <button data-p="plans"><span class="no">03</span><span class="lbl">Sweep Plans</span></button>
 <button data-p="ledger"><span class="no">04</span><span class="lbl">Ledger</span></button>
 <button data-p="comp"><span class="no">05</span><span class="lbl">Compliance</span></button>
 <button data-p="log"><span class="no">06</span><span class="lbl">Audit Log</span></button>
 <button data-p="roster"><span class="no">07</span><span class="lbl">Universes</span></button>
 <button data-p="auto"><span class="no">08</span><span class="lbl">Automaton</span></button>
 <div class="mode dry" id="mode-pill">DRY-RUN</div>
</nav>
<main>
<section id="p-dash">
 <h1>Investment Automaton <span class="tag">— sovereign capital engine</span></h1>
 <div class="sub" id="cfg-line"></div>
 <div id="live-warn"></div>
 <div class="grid g4">
  <div class="card"><h3>Nexus inbox</h3><div class="big gold" id="d-pending">–</div><div class="note">actions awaiting human approval</div></div>
  <div class="card"><h3>Open FIFO lots</h3><div class="big" id="d-lots">–</div><div class="note" id="d-avg"></div></div>
  <div class="card"><h3>Fills recorded</h3><div class="big" id="d-fills">–</div><div class="note">ledger entries</div></div>
  <div class="card"><h3>Sweep policy</h3><div class="big info" id="d-sweep">–</div><div class="note" id="d-route"></div></div>
 </div>
 <h2>Hard guardrails (private-investor status)</h2>
 <div class="card" id="d-limits"></div>
 <h2>Latest sweep plans</h2>
 <div class="card"><div class="scroll"><table><thead><tr><th>Period</th><th class="num">Net CHF</th><th class="num">Investable</th><th class="num">3a</th><th class="num">IBKR</th><th>Nexus</th></tr></thead><tbody id="d-plans"></tbody></table></div></div>
</section>

<section id="p-nexus" class="hidden">
 <h1>The Nexus <span class="tag">— the human gate</span></h1>
 <div class="sub">No transfer, no order, no copy-trade executes without your explicit approval. Approving requires the exact phrase; rejecting requires a reason.</div>
 <div id="nx-pending"></div>
 <h2>Decision history</h2>
 <div class="card"><div class="scroll"><table><thead><tr><th>When</th><th>Action</th><th>Kind</th><th>Decision</th><th>Reason</th></tr></thead><tbody id="nx-hist"></tbody></table></div></div>
</section>

<section id="p-plans" class="hidden">
 <h1>Sweep Plans</h1>
 <div class="sub">Output of cashflow_bridge.py — 30% of net profit, routed 40/60 to Pillar 3a / IBKR. Zero or negative months route nothing.</div>
 <div id="pl-body"></div>
</section>

<section id="p-ledger" class="hidden">
 <h1>FIFO Ledger</h1>
 <div class="sub">Open lots (holding-period tracker) and recorded fills.</div>
 <h2>Open lots</h2>
 <div class="card"><div class="scroll"><table><thead><tr><th>Ticker</th><th class="num">Qty</th><th>Opened</th><th class="num">Age (d)</th></tr></thead><tbody id="ld-lots"></tbody></table></div></div>
 <h2>Fills</h2>
 <div class="card"><div class="scroll"><table><thead><tr><th>Date</th><th>Side</th><th>Ticker</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Notional CHF</th></tr></thead><tbody id="ld-fills"></tbody></table></div></div>
</section>

<section id="p-comp" class="hidden">
 <h1>Swiss Compliance Guard</h1>
 <div class="sub">Rule owners: Tax Advisor + Regulatory Compliance Officer (Capital &amp; Compliance Universe). Not tax advice — thresholds require advisor sign-off.</div>
 <div class="grid g2"><div class="card"><h3>Enforced limits</h3><div id="cp-limits"></div></div>
 <div class="card"><h3>Portfolio health</h3><div id="cp-health"></div></div></div>
 <h2>Audit snapshot (Auditing Agent)</h2>
 <div class="card"><pre id="cp-audit"></pre></div>
</section>

<section id="p-log" class="hidden">
 <h1>Audit Log <span class="tag">— investment_log.txt</span></h1>
 <div class="sub">Every sweep, gate decision, compliance verdict and fill. Reviewed by the Auditing Agent.</div>
 <div class="card"><pre id="lg-body"></pre></div>
 <div style="margin-top:12px"><button class="btn ghost" id="lg-refresh">↻ Refresh</button></div>
</section>

<section id="p-roster" class="hidden">
 <h1>The Seven Universes <span class="tag">— one hundred ten specialists</span></h1>
 <div class="sub">Modular hierarchy of the agent-run business. Each agent belongs to exactly one universe; category groups feed universes as blocks, Specialized Roles per-agent.</div>
 <div id="rs-body"></div>
</section>
<section id="p-auto" class="hidden">
 <h1>Central Automaton <span class="tag">— bounded autonomy</span></h1>
 <div class="sub">Perceive → propose → (your approval) → execute. Replication is Nexus-gated; the kill switch stops everything.</div>
 <div id="au-halt-warn"></div>
 <div class="grid g4">
  <div class="card"><h3>Cycle</h3><div class="big" id="au-cycle">–</div><div class="note" id="au-ts"></div></div>
  <div class="card"><h3>Mode</h3><div class="big info" id="au-mode">–</div><div class="note">from config</div></div>
  <div class="card"><h3>Workers</h3><div class="big gold" id="au-workers">–</div><div class="note">replicated instances</div></div>
  <div class="card"><h3>Last cycle</h3><div class="big" id="au-sum">–</div><div class="note">proposals / executions / spawns</div></div>
 </div>
 <h2>Controls</h2>
 <div class="card" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
  <select id="au-role"><option>ledger_audit</option><option>income_validate</option><option>plan_review</option><option>compliance_watch</option></select>
  <button class="btn" id="au-spawn">Request replication</button>
  <button class="btn red" id="au-halt">Engage kill switch</button>
  <button class="btn ghost" id="au-resume">Resume</button>
  <span class="note">replication requests land in The Nexus for your approval</span>
 </div>
 <h2>Budgets &amp; workers</h2>
 <div class="grid g2"><div class="card"><h3>Limits</h3><div id="au-limits"></div></div>
 <div class="card"><h3>Worker registry</h3><div class="scroll"><table><thead><tr><th>Id</th><th>Role</th><th>Status</th></tr></thead><tbody id="au-wtab"></tbody></table></div></div></div>
 <h2>Inbox (income CSVs)</h2>
 <div class="card"><pre id="au-inbox"></pre></div>
</section>
<footer>JARVIS CONTROL PLANE · LOCALHOST-ONLY BY DEFAULT · DRY-RUN UNTIL YOU SAY OTHERWISE · NOT FINANCIAL ADVICE</footer>
</main>
</div>
<div id="toast"></div>
<script>
"use strict";
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const fv = (f, n) => f.querySelector('[name="' + n + '"]').value;
function toast(msg, kind){ const t = document.createElement("div"); t.className = "tmsg " + (kind || ""); t.textContent = msg; $("#toast").appendChild(t); setTimeout(() => t.remove(), 4500); }
async function api(path, opt){
  const r = await fetch(path, opt);
  let j = null; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((j && j.error) || ("HTTP " + r.status));
  return j;
}
const post = (path, body) => api(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
const money = x => Number(x ?? 0).toLocaleString("en", {minimumFractionDigits:2, maximumFractionDigits:2});

const loaders = {dash:loadDash, nexus:loadNexus, plans:loadPlans, ledger:loadLedger, comp:loadComp, log:loadLog, roster:loadRoster, auto:loadAuto};
$$("nav button").forEach(b => b.addEventListener("click", () => {
  $$("nav button").forEach(x => x.classList.remove("on")); b.classList.add("on");
  $$("main section").forEach(s => s.classList.add("hidden"));
  $("#p-" + b.dataset.p).classList.remove("hidden");
  Promise.resolve((loaders[b.dataset.p] || (() => {}))()).catch(e => toast(e.message, "bad"));
}));

async function loadDash(){
  const s = await api("/api/state");
  $("#mode-pill").textContent = s.mode; $("#mode-pill").className = "mode " + (s.mode === "LIVE" ? "live" : "dry");
  $("#cfg-line").textContent = s.config_line;
  $("#live-warn").innerHTML = s.mode === "LIVE" ? '<div class="banner red">⚠ LIVE MODE — real orders possible. Every execution still requires compliance + Nexus approval.</div>' : '<div class="banner blue">Simulation mode: fills are recorded as SimulatedFilled; no transfer or order leaves this machine.</div>';
  $("#d-pending").textContent = s.pending_count;
  $("#d-lots").textContent = s.audit.open_lots;
  $("#d-avg").textContent = "avg holding " + s.audit.avg_holding_days + " d (target ≥ " + s.limits.min_holding_days + ")";
  $("#d-fills").textContent = s.audit.fills;
  $("#d-sweep").textContent = Math.round(s.sweep.share * 100) + "%";
  $("#d-route").textContent = "of net profit → 3a " + Math.round(s.sweep.alloc_3a * 100) + "% / IBKR " + Math.round(s.sweep.alloc_ibkr * 100) + "% (" + s.sweep.ticker + ")";
  const L = s.limits;
  $("#d-limits").innerHTML =
    '<div class="kv"><span>Minimum holding period</span><b>' + L.min_holding_days + ' days</b></div>' +
    '<div class="kv"><span>Max annual turnover</span><b>' + L.max_annual_turnover_ratio + '× assets</b></div>' +
    '<div class="kv"><span>Max orders / month</span><b>' + L.max_orders_per_month + '</b></div>' +
    '<div class="kv"><span>Margin / leverage</span><b class="' + (L.allow_margin ? 'bad' : 'ok') + '">' + (L.allow_margin ? 'ALLOWED' : 'PROHIBITED') + '</b></div>' +
    '<div class="kv"><span>Derivatives</span><b class="' + (L.allow_derivatives ? 'bad' : 'ok') + '">' + (L.allow_derivatives ? 'ALLOWED' : 'PROHIBITED') + '</b></div>' +
    '<div class="kv"><span>Short selling</span><b class="' + (L.allow_short_selling ? 'bad' : 'ok') + '">' + (L.allow_short_selling ? 'ALLOWED' : 'PROHIBITED') + '</b></div>';
  $("#d-plans").innerHTML = s.plans.slice(-6).reverse().map(p =>
    '<tr><td>' + esc(p.period) + '</td><td class="num">' + money(p.net_profit_chf) + '</td><td class="num">' + money(p.investable_chf) + '</td><td class="num">' + money(p.pillar_3a_chf) + '</td><td class="num">' + money(p.ibkr_chf) + '</td><td>' + ((p.nexus_action_ids || []).length ? '<span class="pill warn">' + (p.nexus_action_ids || []).length + ' staged</span>' : '<span class="pill ok">none</span>') + '</td></tr>').join("") || '<tr><td colspan="6" class="dim">No plans yet — run cashflow_bridge.py</td></tr>';
}

async function loadNexus(){
  const n = await api("/api/nexus");
  $("#nx-pending").innerHTML = n.pending.length ? n.pending.map(r =>
    '<div class="card" style="margin-bottom:12px"><h3>' + esc(r.id) + ' <span class="pill warn">' + esc(r.kind) + '</span></h3>' +
    '<pre>' + esc(JSON.stringify(r.payload, null, 2)) + '</pre>' +
    '<div class="note" style="margin:8px 0">proposed by: ' + esc(r.proposed_by) + ' · staged ' + esc(r.staged_at) + '</div>' +
    '<form class="nxform" data-id="' + esc(r.id) + '" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
    '<input name="phrase" placeholder="I-AUTHORISE-THIS-ACTION" style="width:250px">' +
    '<button class="btn" type="submit" data-d="approve">Approve</button>' +
    '<input name="reason" placeholder="rejection reason" style="width:200px">' +
    '<button class="btn red" type="submit" data-d="reject">Reject</button></form></div>').join("")
    : '<div class="banner blue">Nexus inbox empty — nothing awaits your approval.</div>';
  $$("#nx-pending form.nxform").forEach(f => f.addEventListener("submit", async ev => {
    ev.preventDefault();
    const btn = ev.submitter || f.querySelector("button");
    const decision = btn.dataset.d;
    try {
      const r = await post("/api/nexus/decide", {id: f.dataset.id, decision: decision,
        phrase: fv(f, "phrase"), reason: fv(f, "reason")});
      toast(decision + " recorded for " + r.id, "ok"); loadNexus(); loadDash();
    } catch (e) { toast(e.message, "bad"); }
  }));
  $("#nx-hist").innerHTML = n.history.slice().reverse().map(h =>
    '<tr><td class="dim">' + esc(h.at) + '</td><td>' + esc(h.id) + '</td><td>' + esc(h.kind) + '</td><td class="' + (h.decision === "APPROVED" ? "ok" : "bad") + '">' + esc(h.decision) + '</td><td class="dim">' + esc(h.reason || "") + '</td></tr>').join("")
    || '<tr><td colspan="5" class="dim">No decisions yet.</td></tr>';
}

async function loadPlans(){
  const s = await api("/api/state");
  $("#pl-body").innerHTML = s.plans.map(p =>
    '<div class="card" style="margin-bottom:12px"><h3>' + esc(p.period) + ' <span class="pill ' + (p.investable_chf > 0 ? 'ok' : 'warn') + '">' + (p.investable_chf > 0 ? 'ROUTED' : 'NOTHING ROUTED') + '</span></h3>' +
    '<div class="kv"><span>Net profit</span><b>CHF ' + money(p.net_profit_chf) + '</b></div>' +
    '<div class="kv"><span>Investable (30% sweep)</span><b>CHF ' + money(p.investable_chf) + '</b></div>' +
    '<div class="kv"><span>Pillar 3a leg</span><b class="gold">CHF ' + money(p.pillar_3a_chf) + '</b></div>' +
    '<div class="kv"><span>IBKR DCA leg</span><b class="info">CHF ' + money(p.ibkr_chf) + '</b></div>' +
    '<div class="kv"><span>3a QR reference</span><b>' + esc(p.pillar_3a_qr_reference || "—") + '</b></div>' +
    '<div class="note" style="margin-top:8px">' + esc(p.note || "") + '</div>' +
    ((p.nexus_action_ids || []).length ? '<div class="note" style="margin-top:4px">Nexus: ' + (p.nexus_action_ids || []).map(esc).join(", ") + '</div>' : '') +
    '</div>').join("") || '<div class="banner blue">No sweep plans yet.</div>';
}

async function loadLedger(){
  const l = await api("/api/ledger");
  $("#ld-lots").innerHTML = l.lots.filter(x => x.qty > 0).map(x =>
    '<tr><td>' + esc(x.ticker) + '</td><td class="num">' + x.qty + '</td><td>' + esc(x.opened_date) + '</td><td class="num ' + (Math.floor((Date.now()/1000 - x.opened_ts) / 86400) >= 180 ? 'ok' : 'warn') + '">' + Math.floor((Date.now()/1000 - x.opened_ts) / 86400) + '</td></tr>').join("")
    || '<tr><td colspan="4" class="dim">No open lots.</td></tr>';
  $("#ld-fills").innerHTML = l.fills.slice().reverse().map(f =>
    '<tr><td>' + esc(f.date) + '</td><td class="' + (f.side === "BUY" ? "ok" : "warn") + '">' + esc(f.side) + '</td><td>' + esc(f.ticker) + '</td><td class="num">' + f.qty + '</td><td class="num">' + f.price + ' ' + esc(f.currency) + '</td><td class="num">' + money(f.notional_chf) + '</td></tr>').join("")
    || '<tr><td colspan="6" class="dim">No fills recorded.</td></tr>';
}

async function loadComp(){
  const s = await api("/api/state");
  const L = s.limits;
  $("#cp-limits").innerHTML = Object.entries(L).map(([k, v]) =>
    '<div class="kv"><span>' + esc(k.replace(/_/g, " ")) + '</span><b>' + esc(String(v)) + '</b></div>').join("");
  $("#cp-health").innerHTML = (s.health_warnings.length
    ? s.health_warnings.map(w => '<div class="kv"><span class="warn">⚠</span><span>' + esc(w) + '</span></div>').join("")
    : '<div class="kv"><span class="ok">✓</span><span>No health warnings — holding period and turnover within safe-haven criteria.</span></div>')
    + '<div class="kv"><span>Weighted avg holding</span><b>' + s.audit.avg_holding_days + ' d</b></div>';
  $("#cp-audit").textContent = JSON.stringify(s.audit, null, 2);
}

async function loadLog(){
  const l = await api("/api/log?lines=150");
  $("#lg-body").textContent = l.text;
}
$("#lg-refresh").addEventListener("click", loadLog);

async function loadRoster(){
  const r = await api("/api/roster");
  const counts = {};
  r.category_groups.forEach(g => { counts[g.universe] = (counts[g.universe] || 0) + g.agents.length; });
  r.specialized_roles.agents.forEach(a => { counts[a.universe] = (counts[a.universe] || 0) + 1; });
  const groupsByU = {};
  r.category_groups.forEach(g => { (groupsByU[g.universe] = groupsByU[g.universe] || []).push(g); });
  $("#rs-body").innerHTML = Object.entries(r.universes).map(([uid, u]) => {
    const spec = r.specialized_roles.agents.filter(a => a.universe === uid);
    const groups = groupsByU[uid] || [];
    return '<details><summary>' + uid + ' · ' + esc(u.name).toUpperCase() + ' — ' + (counts[uid] || 0) + ' agents</summary>' +
      '<div class="note" style="margin:8px 0">' + esc(u.mission) + '</div>' +
      groups.map(g => '<div class="note" style="margin:6px 0 2px"><b class="gold">' + esc(g.group) + '</b></div><ul>' +
        g.agents.map(a => '<li><b>' + esc(a.name) + '</b> — ' + esc(a.description) + '</li>').join("") + '</ul>').join("") +
      (spec.length ? '<div class="note" style="margin:6px 0 2px"><b class="gold">Specialized Roles (per-agent)</b></div><ul>' +
        spec.map(a => '<li><b>' + esc(a.name) + '</b> — ' + esc(a.description) + '</li>').join("") + '</ul>' : '') +
      '</details>';
  }).join("") +
  '<h2>Investment Automaton wiring</h2><div class="card"><pre>' + esc(JSON.stringify(r.investment_automaton_wiring, null, 2)) + '</pre></div>';
}

async function loadAuto(){
  const a = await api("/api/automaton");
  const h = a.heartbeat || {};
  $("#au-halt-warn").innerHTML = a.halted ? '<div class="banner red">⚠ KILL SWITCH ENGAGED — the automaton is frozen. Resume to continue.</div>' : '';
  $("#au-cycle").textContent = h.cycle ?? 0;
  $("#au-ts").textContent = h.ts || "no cycle yet";
  $("#au-mode").textContent = h.mode || a.state_mode || "–";
  $("#au-workers").textContent = (a.workers || []).length;
  const sm = h.summary || {};
  $("#au-sum").textContent = (sm.proposals ?? 0) + " / " + (sm.executions ?? 0) + " / " + (sm.spawns ?? 0);
  const L = a.limits || {};
  $("#au-limits").innerHTML = Object.entries(L).map(([k,v]) => '<div class="kv"><span>' + esc(k.replace(/_/g," ")) + '</span><b>' + esc(String(v)) + '</b></div>').join("");
  $("#au-wtab").innerHTML = (a.workers || []).map(w2 => '<tr><td>' + esc(w2.id) + '</td><td>' + esc(w2.role || "?") + '</td><td class="' + (w2.status === "done" ? "ok" : "warn") + '">' + esc(w2.status) + '</td></tr>').join("") || '<tr><td colspan="3" class="dim">No workers yet.</td></tr>';
  $("#au-inbox").textContent = (a.inbox || []).join("\n") || "(empty — drop business income CSVs into runtime/inbox/)";
}
$("#au-spawn").addEventListener("click", async () => {
  try { const r = await post("/api/automaton/spawn-request", {role: $("#au-role").value});
    toast("replication staged: " + r.id + " — approve it in The Nexus", "ok"); loadNexus(); }
  catch (e) { toast(e.message, "bad"); }
});
$("#au-halt").addEventListener("click", async () => {
  try { await post("/api/automaton/halt", {}); toast("kill switch engaged", "ok"); loadAuto(); }
  catch (e) { toast(e.message, "bad"); }
});
$("#au-resume").addEventListener("click", async () => {
  try { await post("/api/automaton/resume", {}); toast("automaton resumed", "ok"); loadAuto(); }
  catch (e) { toast(e.message, "bad"); }
});
loadDash().catch(e => toast(e.message, "bad"));
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- server --
class Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis-ControlPlane/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": msg}, code)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json({"ok": True, **_state()})
            elif path == "/api/nexus":
                self._json({"ok": True, "pending": nx.list_pending(),
                            "history": nx.list_history(100)})
            elif path == "/api/ledger":
                self._json({"ok": True, **guard.read_ledger()})
            elif path == "/api/log":
                n = min(int(q.get("lines", ["120"])[0]), 2000)
                self._json({"ok": True, "text": _log_tail(n)})
            elif path == "/api/roster":
                self._json({"ok": True, **_roster()})
            elif path == "/api/automaton":
                self._json({"ok": True, **automaton.status()})
            else:
                self._err("not found", 404)
        except Exception as exc:  # keep the console alive
            self._err(f"internal error: {exc}", 500)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._err("body too large", 413)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._err("invalid JSON")
            if path == "/api/nexus/decide":
                self._decide(payload)
            elif path == "/api/automaton/halt":
                automaton.halt(); self._json({"ok": True, "halted": True})
            elif path == "/api/automaton/resume":
                automaton.resume(); self._json({"ok": True, "halted": False})
            elif path == "/api/automaton/spawn-request":
                role = str(payload.get("role", ""))
                import worker_roles
                if role not in worker_roles.ROLES:
                    raise ValueError(f"unknown role {role!r}")
                aid = nx.stage("spawn_worker",
                               {"role": role, "note": "Core replication request from control plane"},
                               proposed_by="Central Automaton (Core, via console)")
                self._json({"ok": True, "id": aid})
            else:
                self._err("not found", 404)
        except ValueError as exc:
            self._err(str(exc))
        except Exception as exc:
            self._err(f"internal error: {exc}", 500)

    def _decide(self, p: dict):
        action_id = str(p.get("id", "")).strip()
        decision = str(p.get("decision", "")).strip().lower()
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be 'approve' or 'reject'")
        if nx.status(action_id) != "PENDING":
            raise ValueError(f"action {action_id} is not PENDING (unknown or already decided)")
        if decision == "approve":
            if str(p.get("phrase", "")) != nx.CONFIRM_PHRASE:
                raise ValueError(f"approval phrase must be exactly: {nx.CONFIRM_PHRASE}")
            nx.decide(action_id, "APPROVED")
        else:
            reason = str(p.get("reason", "")).strip()
            if not reason:
                raise ValueError("a rejection reason is required (audit trail)")
            nx.decide(action_id, "REJECTED", reason)
        self._json({"ok": True, "id": action_id, "decision": decision})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Jarvis Control Plane (stdlib-only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    url = f"http://{'localhost' if args.host in ('127.0.0.1', 'localhost') else args.host}:{args.port}"
    print("== 🤖 JARVIS // CONTROL PLANE ==")
    print(f"  Serving on {url}  (Ctrl+C to stop)")
    print(f"  {config.summary()}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("  [warn] non-local bind: anyone on your network could APPROVE actions.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Console closed. State persists in runtime/.")
    except OSError as exc:
        print(f"[fatal] cannot bind {args.host}:{args.port} — {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
