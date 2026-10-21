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
:root{--bg:#080b10;--panel:#0e141d;--panel2:#141c28;--line:#1f2a3a;--au:#d4a24c;--au2:#8a6a2f;
--tx:#dce6f5;--dim:#7c8ba3;--gr:#3ddc97;--rd:#ff6b6b;--yl:#ffd166;--cy:#5bc8e8;
--mono:ui-monospace,'Cascadia Mono',Consolas,monospace;--sans:system-ui,'Segoe UI',Roboto,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:var(--sans);min-height:100vh}
body::before{content:"";position:fixed;inset:0;pointer-events:none;
 background:radial-gradient(800px 380px at 75% -10%,rgba(212,162,76,.07),transparent 60%)}
#layout{display:flex;min-height:100vh;position:relative;z-index:1}
nav{width:225px;flex-shrink:0;background:var(--panel);border-right:1px solid var(--line);
 padding:20px 12px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:4px}
.logo{font-family:var(--mono);font-size:16px;letter-spacing:3px;color:var(--au);padding:0 10px 16px;font-weight:700}
.logo small{display:block;color:var(--dim);font-size:10px;letter-spacing:2px;margin-top:3px}
nav button{all:unset;cursor:pointer;padding:10px 12px;border-radius:9px;color:var(--dim);font-size:13.5px;display:flex;gap:9px;transition:.15s}
nav button:hover{background:var(--panel2);color:var(--tx)}
nav button.on{background:linear-gradient(90deg,rgba(212,162,76,.15),transparent);color:var(--au);box-shadow:inset 2px 0 0 var(--au)}
.mode{margin-top:auto;padding:10px 12px;border:1px solid var(--line);border-radius:10px;font-family:var(--mono);font-size:11px;letter-spacing:1px}
.mode.dry{color:var(--cy)}.mode.live{color:var(--rd);border-color:rgba(255,107,107,.5)}
main{flex:1;padding:28px 32px 70px;max-width:1120px}
h1{font-family:var(--mono);font-size:19px;letter-spacing:2px;margin-bottom:3px}
h1 .tag{color:var(--au)}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
h2{font-family:var(--mono);font-size:12px;letter-spacing:2px;color:var(--dim);text-transform:uppercase;margin:24px 0 10px}
.grid{display:grid;gap:13px}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:repeat(3,1fr)}.g4{grid-template-columns:repeat(4,1fr)}
@media(max-width:900px){.g2,.g3,.g4{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:16px 18px}
.card h3{font-family:var(--mono);font-size:11px;letter-spacing:2px;color:var(--dim);text-transform:uppercase;margin-bottom:10px;display:flex;justify-content:space-between}
.big{font-size:24px;font-weight:700;font-family:var(--mono)}
.ok{color:var(--gr)}.bad{color:var(--rd)}.warn{color:var(--yl)}.info{color:var(--cy)}.gold{color:var(--au)}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:10.5px;font-family:var(--mono);letter-spacing:1px;border:1px solid}
.pill.ok{border-color:rgba(61,220,151,.4);background:rgba(61,220,151,.07)}
.pill.bad{border-color:rgba(255,107,107,.4);background:rgba(255,107,107,.07)}
.pill.warn{border-color:rgba(255,209,102,.4);background:rgba(255,209,102,.07)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{font-family:var(--mono);font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--dim);text-align:left;padding:7px 9px;border-bottom:1px solid var(--line)}
td{padding:7px 9px;border-bottom:1px solid rgba(31,42,58,.55);vertical-align:top}
td.num,th.num{text-align:right;font-family:var(--mono)}
.scroll{overflow:auto;max-height:400px}
pre{font-family:var(--mono);font-size:11.5px;line-height:1.55;color:#b9c7da;white-space:pre-wrap}
.btn{all:unset;cursor:pointer;background:linear-gradient(135deg,var(--au2),#5c451d);color:#ffe9c2;padding:8px 15px;border-radius:8px;font-size:12.5px;font-weight:600;transition:.15s}
.btn:hover{filter:brightness(1.25)}
.btn.red{background:linear-gradient(135deg,#7a2c2c,#4a1a1a);color:#ffd7d7}
.btn.ghost{background:var(--panel2);border:1px solid var(--line);color:var(--dim)}
input{background:var(--panel2);border:1px solid var(--line);color:var(--tx);padding:8px 10px;border-radius:8px;font-size:13px;outline:none}
input:focus{border-color:var(--au)}
.kv{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px dashed rgba(31,42,58,.6);font-size:13px}
.kv:last-child{border:0}
.kv b{font-family:var(--mono)}
details{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px;background:var(--panel)}
summary{cursor:pointer;font-family:var(--mono);font-size:12.5px;letter-spacing:1px;color:var(--au)}
details ul{margin:10px 0 4px 18px;font-size:12.5px;color:var(--dim);line-height:1.8}
.banner{border-radius:11px;padding:12px 16px;margin-bottom:14px;font-size:13.5px;border:1px solid}
.banner.red{background:rgba(255,107,107,.07);border-color:rgba(255,107,107,.35);color:#ffc9c9}
.banner.blue{background:rgba(91,200,232,.06);border-color:rgba(91,200,232,.3);color:#c9ecf7}
.hidden{display:none!important}
#toast{position:fixed;bottom:22px;right:22px;z-index:60;display:flex;flex-direction:column;gap:8px}
.tmsg{background:var(--panel2);border:1px solid var(--line);border-left:3px solid var(--au);padding:11px 15px;border-radius:9px;font-size:13px;max-width:360px;box-shadow:0 8px 26px rgba(0,0,0,.5)}
.tmsg.bad{border-left-color:var(--rd)}.tmsg.ok{border-left-color:var(--gr)}
footer{margin-top:44px;color:#39465c;font-size:11px;font-family:var(--mono);letter-spacing:1px}
@media(max-width:760px){nav{width:60px;padding:14px 6px}nav button span.lbl,.logo small,.mode{display:none}main{padding:18px 14px 50px}}
</style>
</head>
<body>
<div id="layout">
<nav>
 <div class="logo">JARVIS<small>CONTROL PLANE</small></div>
 <button data-p="dash" class="on">📊 <span class="lbl">Dashboard</span></button>
 <button data-p="nexus">🛡 <span class="lbl">The Nexus</span></button>
 <button data-p="plans">🔀 <span class="lbl">Sweep Plans</span></button>
 <button data-p="ledger">📒 <span class="lbl">Ledger</span></button>
 <button data-p="comp">⚖ <span class="lbl">Compliance</span></button>
 <button data-p="log">📜 <span class="lbl">Audit Log</span></button>
 <button data-p="roster"> <span class="lbl">Universes</span></button>
 <div class="mode dry" id="mode-pill">DRY-RUN</div>
</nav>
<main>
<section id="p-dash">
 <h1><span class="tag">◤</span> INVESTMENT AUTOMATON <span class="tag">◢</span></h1>
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
 <h1>🛡 THE NEXUS <span class="tag">// HUMAN GATE</span></h1>
 <div class="sub">No transfer, no order, no copy-trade executes without your explicit approval. Approving requires the exact phrase; rejecting requires a reason.</div>
 <div id="nx-pending"></div>
 <h2>Decision history</h2>
 <div class="card"><div class="scroll"><table><thead><tr><th>When</th><th>Action</th><th>Kind</th><th>Decision</th><th>Reason</th></tr></thead><tbody id="nx-hist"></tbody></table></div></div>
</section>

<section id="p-plans" class="hidden">
 <h1>🔀 SWEEP PLANS</h1>
 <div class="sub">Output of cashflow_bridge.py — 30% of net profit, routed 40/60 to Pillar 3a / IBKR. Zero or negative months route nothing.</div>
 <div id="pl-body"></div>
</section>

<section id="p-ledger" class="hidden">
 <h1>📒 FIFO LEDGER</h1>
 <div class="sub">Open lots (holding-period tracker) and recorded fills.</div>
 <h2>Open lots</h2>
 <div class="card"><div class="scroll"><table><thead><tr><th>Ticker</th><th class="num">Qty</th><th>Opened</th><th class="num">Age (d)</th></tr></thead><tbody id="ld-lots"></tbody></table></div></div>
 <h2>Fills</h2>
 <div class="card"><div class="scroll"><table><thead><tr><th>Date</th><th>Side</th><th>Ticker</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Notional CHF</th></tr></thead><tbody id="ld-fills"></tbody></table></div></div>
</section>

<section id="p-comp" class="hidden">
 <h1>⚖ SWISS COMPLIANCE GUARD</h1>
 <div class="sub">Rule owners: Tax Advisor + Regulatory Compliance Officer (Capital &amp; Compliance Universe). Not tax advice — thresholds require advisor sign-off.</div>
 <div class="grid g2"><div class="card"><h3>Enforced limits</h3><div id="cp-limits"></div></div>
 <div class="card"><h3>Portfolio health</h3><div id="cp-health"></div></div></div>
 <h2>Audit snapshot (Auditing Agent)</h2>
 <div class="card"><pre id="cp-audit"></pre></div>
</section>

<section id="p-log" class="hidden">
 <h1>📜 AUDIT LOG <span class="tag">// investment_log.txt</span></h1>
 <div class="sub">Every sweep, gate decision, compliance verdict and fill. Reviewed by the Auditing Agent.</div>
 <div class="card"><pre id="lg-body"></pre></div>
 <div style="margin-top:12px"><button class="btn ghost" id="lg-refresh">↻ Refresh</button></div>
</section>

<section id="p-roster" class="hidden">
 <h1>🌌 THE 7 UNIVERSES <span class="tag">// 110 SPECIALISTS</span></h1>
 <div class="sub">Modular hierarchy of the agent-run business. Each agent belongs to exactly one universe; category groups feed universes as blocks, Specialized Roles per-agent.</div>
 <div id="rs-body"></div>
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

const loaders = {dash:loadDash, nexus:loadNexus, plans:loadPlans, ledger:loadLedger, comp:loadComp, log:loadLog, roster:loadRoster};
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
