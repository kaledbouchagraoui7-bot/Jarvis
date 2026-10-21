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
import os
import shutil
import time
from pathlib import Path

import webapp  # reuse the exact console UI (PAGE) — single source of truth

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

DEMO_BAR_CSS = (".demobar{position:fixed;top:0;left:0;right:0;z-index:99;background:#1E1B16;"
                "color:#F2EEE7;text-align:center;padding:8px 12px;font-family:var(--sans);"
                "font-size:9.5px;letter-spacing:.28em;text-transform:uppercase}"
                "#layout{padding-top:36px}")

MARBLE = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='800'%3E"
          "%3Cfilter id='m'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.012 0.06' numOctaves='4' seed='7'/%3E"
          "%3CfeColorMatrix values='0 0 0 0 0.66 0 0 0 0 0.60 0 0 0 0 0.50 0 0 0 0.85 0'/%3E%3C/filter%3E"
          "%3Crect width='640' height='800' fill='%23ECE6DC'/%3E%3Crect width='640' height='800' filter='url(%23m)' opacity='0.5'/%3E"
          "%3Cpath d='M60 640 C 220 520 340 700 580 560' stroke='%23FBFAF7' stroke-width='2.5' fill='none' opacity='0.65'/%3E"
          "%3Cpath d='M90 180 C 260 240 420 120 600 210' stroke='%23C9BFAF' stroke-width='1.5' fill='none' opacity='0.6'/%3E%3C/svg%3E\")")

VESSEL = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='640' height='640'%3E"
          "%3Cfilter id='m'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.02 0.045' numOctaves='4' seed='13'/%3E"
          "%3CfeColorMatrix values='0 0 0 0 0.70 0 0 0 0 0.64 0 0 0 0 0.55 0 0 0 0.8 0'/%3E%3C/filter%3E"
          "%3Crect width='640' height='640' fill='%23E9E2D7'/%3E%3Crect width='640' height='640' filter='url(%23m)' opacity='0.45'/%3E"
          "%3Cellipse cx='320' cy='500' rx='180' ry='26' fill='%23C9BFAF' opacity='0.5'/%3E"
          "%3Ccircle cx='320' cy='360' r='130' fill='%23D9D1C3'/%3E%3Ccircle cx='285' cy='325' r='95' fill='%23E4DDD1'/%3E"
          "%3Crect x='300' y='200' width='40' height='60' rx='14' fill='%23CFC6B6'/%3E%3C/svg%3E\")")

LANDING = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JARVIS — The enterprise that runs itself. With your key.</title>
<meta name="description" content="A Swiss solo enterprise of 110 specialist AI agents in seven universes, a human-gated central automaton, and a compliance-bound investment engine. Ask the concierge anything.">
<meta property="og:title" content="JARVIS — the enterprise that runs itself. With your key.">
<meta property="og:description" content="110 agents, seven universes, one human gate. Lead enrichment & programmatic SEO at machine cost.">
<meta property="og:type" content="website">
<link rel="stylesheet" href="/style.css"></head><body>
<div class="blobs"><i></i><i></i><i></i></div>
<nav class="glassnav rv"><div class="wrap navin">
 <a class="brand2" href="/">JARVIS</a>
 <div class="nlinks"><a href="/nexus.html" style="color:var(--g3);font-weight:800">Nexus</a><a href="#products">Products</a><a href="#how">How it works</a><a href="#calc">Calculator</a><a href="/pricing.html">Pricing</a><a href="/journal.html">Journal</a><a href="/portal.html">Portal</a></div>
 <button class="btn grad" data-chat-open>Ask the concierge</button>
</div></nav>
<header class="hero"><div class="wrap" style="text-align:center">
 <div class="badge rv">Sovereign AI enterprise · Switzerland</div>
 <h1 class="rv">Build the business that<br>runs itself. <em class="grad-t">With your key.</em></h1>
 <p class="sub rv">110 specialist agents in seven universes manufacture B2B deliverables at machine cost — while every franc, order and new machine instance waits for one keystroke: yours.</p>
 <div class="prompt rv" id="hero-prompt">
  <input id="hero-q" type="text" placeholder="Ask anything — pricing, safety, how the agents work…" aria-label="Ask the concierge">
  <button class="btn grad" id="hero-send" aria-label="Send">→</button>
 </div>
 <div class="hints rv">Try: <button class="hint" data-q="What does it cost?">“What does it cost?”</button>
  <button class="hint" data-q="Who approves the money?">“Who approves the money?”</button>
  <button class="hint" data-q="Can the AI act alone?">“Can the AI act alone?”</button></div>
</div></header>
<div class="marquee rv"><div class="mtrack">
 <span>110 specialist agents</span><span>7 universes</span><span>100% human-approved actions</span><span>30% profit sweep</span><span>180-day minimum holding</span><span>no leverage · no derivatives</span><span>0 cloud dependencies for money ops</span>
 <span>110 specialist agents</span><span>7 universes</span><span>100% human-approved actions</span><span>30% profit sweep</span><span>180-day minimum holding</span><span>no leverage · no derivatives</span><span>0 cloud dependencies for money ops</span>
</div></div>
<section id="products"><div class="wrap">
 <div class="eyebrow rv">Product lines</div>
 <h2 class="rv">Two assets, manufactured at scale.</h2>
 <div class="grid g2">
  <div class="gcard rv"><div class="gicon">①</div><h3>Hyper-personalized B2B lead enrichment</h3>
   <p>Decision-maker data scraped, validated and enriched at compute cost — then outreach written for one person at a time.</p>
   <div class="chips"><span>validated data</span><span>per-prospect copy</span><span>retainer delivery</span></div>
   <a class="btn ghost" href="mailto:cogops.engine@gmail.com?subject=Lead%20enrichment">Request a sample batch</a></div>
  <div class="gcard rv"><div class="gicon">②</div><h3>Programmatic SEO at scale</h3>
   <p>Live data streams ingested and transformed into long-tail landing pages that rank — hundreds per client, refreshed autonomously.</p>
   <div class="chips"><span>data-stream ingestion</span><span>long-tail pages</span><span>autonomous refresh</span></div>
   <a class="btn ghost" href="mailto:cogops.engine@gmail.com?subject=Programmatic%20SEO">Request a pilot cluster</a></div>
 </div>
</div></section>
<section id="how" class="tint"><div class="wrap">
 <div class="eyebrow rv">How it works</div>
 <h2 class="rv">Unlimited automation. Zero unapproved action.</h2>
 <div class="grid g3">
  <div class="scard rv"><div class="step">01</div><h3>Agents produce</h3><p>Seven universes — revenue, delivery, capital, people, infrastructure, strategy, continuity — run delivery, research and operations as bounded tasks.</p></div>
  <div class="scard rv"><div class="step">02</div><h3>The Nexus approves</h3><p>Every external, financial or high-stakes action becomes a plain-language request. Nothing executes until a human types the approval phrase.</p></div>
  <div class="scard rv"><div class="step">03</div><h3>Capital compounds</h3><p>30% of each month's net profit sweeps into Pillar 3a and ETF dollar-cost averaging under Swiss private-investor guardrails.</p></div>
 </div>
</div></section>
<section id="calc"><div class="wrap">
 <div class="eyebrow rv">Live maths, in your browser</div>
 <h2 class="rv">What would this month invest?</h2>
 <div class="glasspanel rv">
  <label class="eyebrow" for="c-in">Net profit this month (CHF)</label>
  <input id="c-in" type="number" min="0" step="50" value="5000" inputmode="numeric">
  <div class="cout grid g3">
   <div><span class="eyebrow">Monthly sweep · 30%</span><b id="c-sweep">—</b></div>
   <div><span class="eyebrow">To Pillar 3a</span><b id="c-3a">—</b><i id="c-capnote"></i></div>
   <div><span class="eyebrow">To ETF via IBKR</span><b id="c-etf">—</b></div>
  </div>
  <p class="fine">Real calculation, running in your browser. The live system applies the same rules plus your annual 3a ceiling, compliance guardrails and your Nexus approval — on your machine, never in the cloud.</p>
 </div>
</div></section>
<section id="trust" class="tint"><div class="wrap grid g2 trustgrid">
 <div class="rv"><div class="eyebrow">Governance</div>
  <h2>The gate only your keystroke opens.</h2>
  <p class="lede">The central automaton may think, propose, replicate and audit freely — but every franc, every order and every new instance of itself passes through The Nexus. Workers cannot breed; budgets cap cycles; one file freezes everything.</p>
  <div class="kvlist">
   <div class="kv"><span>Minimum holding period</span><b>180 days</b></div>
   <div class="kv"><span>Max annual turnover</span><b>5× assets</b></div>
   <div class="kv"><span>Margin · derivatives · short</span><b>Prohibited</b></div>
   <div class="kv"><span>Replication depth</span><b>Core → worker</b></div>
   <div class="kv"><span>Actions requiring human approval</span><b>100%</b></div>
  </div></div>
 <div class="gcard rv"><h3>Journal — notes from the machine room</h3>
  <a class="jrow" href="/journal/post-01.html"><span class="jt">How a one-person business runs itself — without running away</span><span class="jd">Bounded autonomy, the Nexus, the kill switch · 6 min</span></a>
  <a class="jrow" href="/journal/post-02.html"><span class="jt">What CHF 5,000 of profit does in this system</span><span class="jd">The sweep, the split, the approval · 5 min</span></a>
  <a class="btn ghost" href="/journal.html" style="margin-top:14px">All notes</a></div>
</div></section>
<section id="cta"><div class="wrap" style="text-align:center">
 <h2 class="rv">Work with the enterprise.</h2>
 <p class="sub rv" style="margin:10px auto 26px">Retainers for lead enrichment and programmatic SEO. One human reads every message.</p>
 <div class="rv" style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap">
  <a class="btn grad" href="mailto:cogops.engine@gmail.com?subject=JARVIS%20enquiry">Write to the founder</a>
  <button class="btn ghost" data-chat-open>Ask the concierge</button>
 </div>
</div></section>
<footer><div class="wrap fline">
 <span>JARVIS · Bootstrap → Sovereignty</span>
 <span><a href="/privacy.html">Privacy</a> · <a href="/impressum.html">Impressum</a> · <a href="/portal.html">Client portal</a></span>
 <span>Not financial advice</span>
</div></footer>
<script src="/site.js"></script>
<script>
(function(){
  var inp = document.getElementById("c-in");
  function fmt(v){ return "CHF " + v.toLocaleString("en",{minimumFractionDigits:2,maximumFractionDigits:2}); }
  function f(){ var net = Math.max(0, parseFloat(inp.value || "0") || 0);
    var sweep = net * 0.30, cap = 7258, a = Math.min(sweep * 0.40, cap), e = sweep - a;
    document.getElementById("c-sweep").textContent = fmt(sweep);
    document.getElementById("c-3a").textContent = fmt(a);
    document.getElementById("c-etf").textContent = fmt(e);
    document.getElementById("c-capnote").textContent = (sweep * 0.40 > cap) ? "annual ceiling reached — overflow goes to the ETF" : ""; }
  inp.addEventListener("input", f); f();
  document.getElementById("hero-send").addEventListener("click", function(){
    window.__jarvisChatOpen(document.getElementById("hero-q").value); });
  document.getElementById("hero-q").addEventListener("keydown", function(ev){
    if (ev.key === "Enter") window.__jarvisChatOpen(this.value); });
  document.querySelectorAll(".hint").forEach(function(h){
    h.addEventListener("click", function(){ window.__jarvisChatOpen(h.dataset.q); }); });
})();
</script>
</body></html>
"""

SITE_CSS = """
:root{--bg:#FBFAF8;--ink:#141210;--mut:#6E675C;--line:rgba(20,18,16,.09);
--glass:rgba(255,255,255,.66);--glass2:rgba(255,255,255,.82);
--g1:#B4832F;--g2:#C96F4A;--g3:#8A6A2F;--grad:linear-gradient(94deg,#B4832F,#C96F4A 55%,#A4553F);
--serif:'Cormorant Garamond','Iowan Old Style',Georgia,serif;
--sans:system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;
--mono:ui-monospace,'Cascadia Mono',Consolas,monospace;--r:18px}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15.5px;line-height:1.6;overflow-x:hidden}
::selection{background:#EFD9AE;color:var(--ink)}
a{color:var(--g3)}
.wrap{max-width:1120px;margin:0 auto;padding:0 26px;position:relative}
.blobs{position:fixed;inset:0;z-index:-1;overflow:hidden}
.blobs i{position:absolute;border-radius:50%;filter:blur(90px);opacity:.5;animation:drift 22s ease-in-out infinite alternate}
.blobs i:nth-child(1){width:560px;height:560px;background:#F6DFB4;top:-160px;left:-120px}
.blobs i:nth-child(2){width:480px;height:480px;background:#F3C9BE;top:22%;right:-140px;animation-delay:-7s}
.blobs i:nth-child(3){width:420px;height:420px;background:#CFE0E8;bottom:-140px;left:28%;animation-delay:-14s}
@keyframes drift{from{transform:translate3d(0,0,0) scale(1)}to{transform:translate3d(40px,30px,0) scale(1.12)}}
.glassnav{position:fixed;top:14px;left:0;right:0;z-index:50}
.navin{display:flex;align-items:center;gap:18px;background:var(--glass);backdrop-filter:blur(16px);
 border:1px solid var(--line);border-radius:999px;padding:9px 12px 9px 22px;box-shadow:0 8px 30px rgba(20,18,16,.07)}
.brand2{font-family:var(--serif);font-size:17px;letter-spacing:.34em;color:var(--ink);text-decoration:none}
.nlinks{display:flex;gap:20px;margin-left:auto;flex-wrap:wrap}
.nlinks a{font-size:12.5px;color:var(--mut);text-decoration:none;font-weight:600}
.nlinks a:hover,.nlinks a.act{color:var(--ink)}
.btn{display:inline-block;border:0;cursor:pointer;text-decoration:none;text-align:center;
 background:var(--ink);color:#FBFAF8;padding:11px 20px;border-radius:999px;font-size:12.5px;font-weight:700;letter-spacing:.02em;transition:.18s}
.btn:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(20,18,16,.16)}
.btn.grad{background:var(--grad);color:#fff;box-shadow:0 6px 24px rgba(201,111,74,.35)}
.btn.ghost{background:var(--glass2);color:var(--ink);border:1px solid var(--line)}
.hero{padding:150px 0 60px}
.badge{display:inline-block;background:var(--glass2);border:1px solid var(--line);border-radius:999px;
 padding:6px 16px;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--mut);margin-bottom:22px}
h1{font-size:clamp(40px,6.4vw,76px);line-height:1.04;letter-spacing:-.02em;font-weight:800}
h1 em,.grad-t{font-family:var(--serif);font-style:italic;font-weight:500;
 background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
h2{font-size:clamp(28px,3.6vw,44px);letter-spacing:-.015em;font-weight:800;line-height:1.12;margin:10px 0 8px}
.sub{color:var(--mut);font-size:16.5px;max-width:640px;margin:18px auto 30px}
.prompt{display:flex;gap:10px;max-width:640px;margin:0 auto;background:var(--glass2);backdrop-filter:blur(14px);
 border:1px solid var(--line);border-radius:999px;padding:8px 8px 8px 22px;box-shadow:0 14px 44px rgba(20,18,16,.10);transition:.2s}
.prompt:focus-within{border-color:#C9A15E;box-shadow:0 14px 44px rgba(180,131,47,.22)}
.prompt input{flex:1;border:0;background:transparent;outline:none;font-size:15px;color:var(--ink)}
.hints{margin-top:16px;font-size:12.5px;color:var(--mut)}
.hint{border:0;background:transparent;color:var(--g3);font-size:12.5px;cursor:pointer;padding:2px 4px;font-weight:600}
.hint:hover{text-decoration:underline}
.marquee{overflow:hidden;border-top:1px solid var(--line);border-bottom:1px solid var(--line);
 background:var(--glass);backdrop-filter:blur(8px);padding:12px 0;margin:30px 0 0}
.mtrack{display:flex;gap:44px;width:max-content;animation:mq 36s linear infinite;white-space:nowrap}
.mtrack span{font-family:var(--mono);font-size:11.5px;letter-spacing:.22em;text-transform:uppercase;color:var(--mut)}
@keyframes mq{to{transform:translateX(-50%)}}
section{padding:76px 0}
section.tint{background:linear-gradient(180deg,rgba(255,255,255,.5),rgba(246,241,232,.65));border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.eyebrow{font-size:10.5px;letter-spacing:.4em;text-transform:uppercase;color:var(--g3);font-weight:700}
.grid{display:grid;gap:18px;margin-top:26px}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:repeat(3,1fr)}
@media(max-width:900px){.g2,.g3{grid-template-columns:1fr}}
.gcard,.scard{background:var(--glass2);backdrop-filter:blur(12px);border:1px solid var(--line);border-radius:var(--r);
 padding:28px;box-shadow:0 10px 40px rgba(20,18,16,.06);transition:.22s}
.gcard:hover,.scard:hover{transform:translateY(-5px);box-shadow:0 20px 54px rgba(20,18,16,.10)}
.gcard h3,.scard h3{font-size:21px;font-weight:800;letter-spacing:-.01em;margin:12px 0 8px}
.gcard p,.scard p{color:var(--mut);font-size:14px}
.gicon{width:44px;height:44px;border-radius:12px;background:var(--grad);color:#fff;display:grid;place-items:center;font-size:20px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 18px}
.chips span{background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:11px;color:var(--mut);font-weight:600}
.step{font-family:var(--serif);font-size:34px;font-style:italic;background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.glasspanel{background:var(--glass2);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:var(--r);padding:32px;margin-top:26px;box-shadow:0 14px 44px rgba(20,18,16,.08)}
.glasspanel input{font-family:var(--serif);font-size:34px;width:240px;padding:8px 14px;border:1px solid var(--line);border-radius:12px;background:#fff;color:var(--ink);outline:none;margin:12px 0 6px}
.glasspanel input:focus{border-color:#C9A15E}
.cout b{display:block;font-family:var(--serif);font-size:32px;margin-top:6px}
.cout i{display:block;font-style:normal;font-size:11px;color:var(--mut);margin-top:4px}
.fine{font-size:12px;color:var(--mut);margin-top:18px;max-width:640px}
.lede{color:var(--mut);font-size:15px;margin:12px 0 18px;max-width:520px}
.kvlist{background:var(--glass2);border:1px solid var(--line);border-radius:var(--r);padding:8px 22px}
.kv{display:flex;justify-content:space-between;gap:12px;padding:11px 0;border-bottom:1px solid var(--line);font-size:13px;color:var(--mut)}
.kv:last-child{border:0}.kv b{font-family:var(--mono);font-size:12px;color:var(--ink)}
.trustgrid{align-items:start}
.jrow{display:block;padding:14px 0;border-bottom:1px solid var(--line);text-decoration:none}
.jrow:last-of-type{border:0}
.jt{display:block;font-weight:700;font-size:15px;color:var(--ink)}
.jd{display:block;font-size:12px;color:var(--mut);margin-top:3px}
#cta{padding:90px 0}
footer{padding:34px 0 44px;border-top:1px solid var(--line)}
.fline{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--mut)}
.fline a{color:var(--mut);text-decoration:none}.fline a:hover{color:var(--ink)}
/* motion */
.rv{opacity:0;transform:translateY(20px);transition:opacity .7s cubic-bezier(.2,.7,.2,1),transform .7s cubic-bezier(.2,.7,.2,1)}
.rv.in{opacity:1;transform:none}
.vt-fade::view-transition-old(root){animation:vtout .28s ease forwards}
.vt-fade::view-transition-new(root){animation:vtin .34s ease}
@keyframes vtout{to{opacity:0;transform:translateY(-10px)}}
@keyframes vtin{from{opacity:0;transform:translateY(12px)}}
#vt-overlay{position:fixed;inset:0;background:var(--bg);opacity:0;pointer-events:none;transition:opacity .22s;z-index:99}
/* chat */
#chat-fab{position:fixed;right:22px;bottom:22px;z-index:80;width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;
 background:var(--grad);color:#fff;font-size:22px;box-shadow:0 10px 30px rgba(201,111,74,.4);transition:.2s}
#chat-fab:hover{transform:scale(1.06)}
#chat-panel{position:fixed;right:22px;bottom:92px;z-index:80;width:min(390px,calc(100vw - 32px));height:min(560px,70vh);
 background:var(--glass2);backdrop-filter:blur(18px);border:1px solid var(--line);border-radius:20px;display:none;flex-direction:column;
 box-shadow:0 24px 70px rgba(20,18,16,.18);overflow:hidden}
#chat-panel.open{display:flex;animation:cpin .26s cubic-bezier(.2,.7,.2,1)}
@keyframes cpin{from{opacity:0;transform:translateY(14px) scale(.98)}}
#chat-head{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
#chat-head b{font-size:13.5px}#chat-head span{font-size:10.5px;color:var(--mut);letter-spacing:.14em;text-transform:uppercase}
#chat-body{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.cmsg{max-width:86%;padding:10px 14px;border-radius:14px;font-size:13.5px;line-height:1.5;white-space:pre-wrap}
.cmsg.ai{background:#fff;border:1px solid var(--line);align-self:flex-start;border-bottom-left-radius:4px}
.cmsg.me{background:var(--grad);color:#fff;align-self:flex-end;border-bottom-right-radius:4px}
.cmsg .tag{display:block;font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;opacity:.65;margin-bottom:4px}
#chat-foot{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line)}
#chat-in{flex:1;border:1px solid var(--line);border-radius:999px;padding:10px 16px;font-size:13.5px;outline:none;background:#fff}
/* sub-page shared chrome (pricing/journal/portal/legal) */
header.pg{padding:120px 0 10px}
.topbar{display:flex;justify-content:space-between;align-items:baseline;padding:0 0 8px}
.brand{font-family:var(--serif);font-size:17px;letter-spacing:.34em;color:var(--ink);text-decoration:none}
.topnav{display:flex;gap:18px;flex-wrap:wrap}
.topnav a{font-size:12px;font-weight:600;color:var(--mut);text-decoration:none}
.topnav a:hover{color:var(--ink)}
.eyebrow.taupe{color:var(--g3)}
.products{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:24px}
@media(max-width:900px){.products{grid-template-columns:1fr}}
.pcard{background:var(--glass2);border:1px solid var(--line);border-radius:var(--r);padding:28px;transition:.2s}
.pcard:hover{transform:translateY(-4px);box-shadow:0 16px 44px rgba(20,18,16,.10)}
.pcard .n{font-family:var(--serif);font-style:italic;font-size:14px;color:var(--g3)}
.pcard h3{font-size:24px;font-weight:800;margin:8px 0 10px}
.pcard p{font-size:13.5px;color:var(--mut)}
.pcard .price{font-family:var(--serif);font-size:32px;margin:6px 0}
.pcard .price span{font-family:var(--sans);font-size:11px;letter-spacing:.14em;color:var(--mut)}
.pcard ul{margin:14px 0 0;list-style:none}
.pcard li{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);padding:7px 0;border-top:1px solid var(--line)}
.index{margin-top:22px;border-top:1px solid var(--ink)}
.index .row{display:grid;grid-template-columns:56px 1.4fr 2fr 80px;gap:16px;padding:16px 6px;border-bottom:1px solid var(--line);align-items:baseline;text-decoration:none;border-radius:8px}
.index .row:hover{background:rgba(255,255,255,.6)}
.index .u{font-family:var(--mono);font-size:11px;color:var(--g3)}
.index .nm{font-family:var(--serif);font-size:21px;color:var(--ink)}
.index .ds{font-size:12.5px;color:var(--mut)}
.index .ct{font-family:var(--mono);font-size:11px;text-align:right;color:var(--mut)}
@media(max-width:760px){.index .row{grid-template-columns:40px 1fr 60px}.index .ds{display:none}}
.prose p{margin:0 0 16px;font-size:15.5px;color:var(--mut);max-width:760px}
.prose strong{color:var(--ink)}
.card{background:var(--glass2);border:1px solid var(--line);border-radius:var(--r);padding:20px 22px}
.card h3{font-size:15px;font-weight:800;margin-bottom:8px}
.calc{background:var(--glass2);border:1px solid var(--line);border-radius:var(--r);padding:26px}
.calc input,.card input{font-family:var(--serif);font-size:20px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink);outline:none}
.calc input:focus,.card input:focus{border-color:#C9A15E}
.fine2,.fine{font-size:12px;color:var(--mut);margin-top:14px}
.banner{border-radius:12px;padding:12px 16px;font-size:13px;border:1px solid;line-height:1.55}
.banner.red{background:#FBEDE9;border-color:#C97B62;color:#7C3A2D}
.banner.blue{background:#EDF3EC;border-color:#8FAF92;color:#3F5D44}
.hidden{display:none!important}
details{background:var(--glass2);border:1px solid var(--line);border-radius:14px;margin-bottom:10px;padding:16px 22px}
summary{font-family:var(--serif);font-size:20px;color:var(--ink);cursor:pointer}
details p{font-size:13.5px;color:var(--mut);margin-top:10px;max-width:760px}
footer .wrap p,footer .wrap a{font-size:10px;letter-spacing:.26em;text-transform:uppercase;color:var(--mut);text-decoration:none}
"""


NEXUS_JS = """
"use strict";
(function(){
  var body = document.getElementById("nx-body");
  var inp = document.getElementById("nx-in");
  var modeEl = document.getElementById("nx-mode");
  var pendingTool = null;
  function esc(s){ return String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
  function add(html, who, tag){
    var m = document.createElement("div"); m.className = "m " + who;
    m.innerHTML = (tag ? '<span class="tag">' + esc(tag) + "</span>" : "") + html;
    body.appendChild(m); body.scrollTop = body.scrollHeight; return m;
  }
  function reasoning(r){ return r && r.length ? '<details><summary>why I say this</summary><ul>' + r.map(x => "<li>" + esc(x) + "</li>").join("") + "</ul></details>" : ""; }
  add("Hello — I'm <b>Nexus</b>. Ask me anything about this enterprise, or use the tools on the left. I run in <b>knowledge mode</b> (verified public facts) until the founder binds Workers AI, then I switch to <b>ai mode</b> with the same honesty labels. My memory lives only in this browser.", "ai", "nexus");
  var mem = JSON.parse(localStorage.getItem("jarvis_nexus_mem") || "[]");
  if (mem.length) add("Memory: " + mem.length + " exchange(s) stored locally on this device. Last topic: “" + esc(mem[mem.length-1].q) + "”.", "ai", "memory");

  async function send(){
    var q = inp.value.trim(); if (!q) return; inp.value = "";
    add(esc(q), "me");
    if (pendingTool === "research"){ pendingTool = null; return research(q); }
    var ph = add("…", "ai", "thinking");
    var res = await window.__nexusAsk(q);
    ph.remove();
    modeEl.textContent = res.mode + " mode";
    var out = esc(res.answer);
    var idx = out.indexOf("Reasoning:");
    if (idx !== -1){ out = out.slice(0, idx) + reasoning(out.slice(idx + 10).split("\\n").filter(Boolean).map(s => s.replace(/^[-*]\\s*/, ""))); }
    else out += reasoning(res.reasoning);
    add(out, "ai", res.mode);
    mem.push({q: q, a: res.answer, mode: res.mode, at: Date.now()});
    localStorage.setItem("jarvis_nexus_mem", JSON.stringify(mem.slice(-50)));
  }
  async function research(q){
    var ph = add("Consulting Wikipedia + Crossref…", "ai", "deep research");
    try {
      var r = await fetch("/api/research", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({question: q})});
      var j = await r.json(); ph.remove();
      if (!r.ok) return add(esc(j.note || j.error || "no sources found"), "ai", "deep research");
      modeEl.textContent = j.mode + " mode";
      var srcs = (j.sources.citations || []).map((c, i) => '<a href="https://doi.org/' + esc(c.doi) + '" target="_blank" rel="noopener">[' + (i+1) + '] ' + esc(c.title) + " (" + esc(c.year) + ")</a>").join("");
      add("<b>Brief:</b> " + esc(j.brief) + reasoning(j.reasoning) +
          (j.sources.background ? '<div class="srcs"><a href="' + esc(j.sources.background.url) + '" target="_blank" rel="noopener">[bg] ' + esc(j.sources.background.title) + " — Wikipedia</a>" + srcs + "</div>" : '<div class="srcs">' + srcs + "</div>"),
          "ai", "deep research · " + j.mode);
    } catch (e) { ph.remove(); add("Research service unreachable — try again shortly.", "ai", "deep research"); }
  }
  function map(){
    var R = window.__ROSTER__ || {};
    var html = "<b>The seven universes</b>";
    Object.entries(R.universes || {}).forEach(function(e){
      var uid = e[0], u = e[1];
      var groups = (R.category_groups || []).filter(g => g.universe === uid);
      var specs = (R.specialized_roles && R.specialized_roles.agents || []).filter(a => a.universe === uid);
      html += '<details style="margin-top:8px"><summary>' + uid + " · " + esc(u.name) + "</summary><ul>";
      groups.forEach(g => { html += "<li><b>" + esc(g.group) + ":</b> " + g.agents.map(a => esc(a.name)).join(", ") + "</li>"; });
      if (specs.length) html += "<li><b>Specialized:</b> " + specs.map(a => esc(a.name)).join(", ") + "</li>";
      html += "</ul></details>";
    });
    add(html, "ai", "system map");
  }
  function doc(){
    var m = add('Generate a proposal: <input id="dc-client" placeholder="Client company" style="border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin:0 6px"> <select id="dc-tier" style="border:1px solid var(--line);border-radius:8px;padding:6px 10px"><option>Enrichment Starter — CHF 1,500/mo</option><option>Growth Engine — CHF 3,500/mo</option><option>pSEO Engine — CHF 4,500/mo</option></select> <button class="btn grad" id="dc-go" style="padding:7px 14px;font-size:11px;margin-left:6px">Build</button>', "ai", "document synthesis");
    m.querySelector("#dc-go").addEventListener("click", function(){
      var client = m.querySelector("#dc-client").value || "Prospective client";
      var tier = m.querySelector("#dc-tier").value;
      var old = document.getElementById("doc-out"); if (old) old.remove();
      var f = document.createElement("div"); f.id = "doc-out"; f.className = "docframe";
      f.innerHTML = '<div style="font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:var(--g3)">JARVIS Enterprises · Proposal</div>' +
        "<h2>" + esc(tier.split(" — ")[0]) + "</h2>" +
        '<div style="color:var(--mut);font-size:13px;margin-bottom:14px">Prepared for ' + esc(client) + " · " + new Date().toLocaleDateString() + " · valid 30 days</div>" +
        '<div class="dkv"><span>Scope</span><b>' + esc(tier) + "</b></div>" +
        '<div class="dkv"><span>Delivery</span><b>Monthly retainer, 30-day notice</b></div>' +
        '<div class="dkv"><span>Production</span><b>110-agent hierarchy, human-reviewed</b></div>' +
        '<div class="dkv"><span>Governance</span><b>Nexus human gate on every external action</b></div>' +
        '<div class="dkv"><span>Contact</span><b>cogops.engine@gmail.com</b></div>' +
        '<p style="margin-top:16px;font-size:12px;color:var(--mut)">This document was synthesised by Nexus from the published price list. Prices exclude VAT where applicable.</p>' +
        '<button class="btn grad" style="margin-top:14px" onclick="window.print()">Print / Save as PDF</button>';
      (document.getElementById("doc-zone") || document.body).appendChild(f);
      if (f.scrollIntoView) f.scrollIntoView({behavior:"smooth", block:"start"});
      add("Proposal compiled for <b>" + esc(client) + "</b> (" + esc(tier) + "). Scroll below the chat to print or save as PDF.", "ai", "document synthesis");
    });
  }
  function calc(){
    var m = add('Monthly net profit (CHF): <input id="cl-in" type="number" value="5000" style="border:1px solid var(--line);border-radius:8px;padding:6px 10px;width:120px;margin-left:6px"> <span id="cl-out" style="margin-left:10px"></span>', "ai", "calculator");
    var f = function(){ var net = Math.max(0, parseFloat(m.querySelector("#cl-in").value || 0) || 0);
      var sw = net * .3, a = Math.min(sw * .4, 7258), e = sw - a;
      m.querySelector("#cl-out").innerHTML = "sweep <b>CHF " + sw.toFixed(2) + "</b> → 3a <b>CHF " + a.toFixed(2) + "</b> + ETF <b>CHF " + e.toFixed(2) + "</b>"; };
    m.querySelector("#cl-in").addEventListener("input", f); f();
  }
  function memoryInfo(){
    add("Memory: " + mem.length + " exchange(s) in this browser's local storage — never sent anywhere except as short context to the AI you talk to. “Clear memory” wipes it.", "ai", "memory");
  }
  document.querySelectorAll(".tool").forEach(function(t){
    t.addEventListener("click", function(){
      var k = t.dataset.tool;
      if (k === "research"){ pendingTool = "research"; add("What should I research? Type a topic and I'll compile a cited brief from Wikipedia + Crossref.", "ai", "deep research"); inp.focus(); }
      if (k === "map") map();
      if (k === "doc") doc();
      if (k === "calc") calc();
      if (k === "memory") memoryInfo();
    });
  });
  document.getElementById("nx-send").addEventListener("click", send);
  inp.addEventListener("keydown", function(e){ if (e.key === "Enter") send(); });
  document.getElementById("nx-clear").addEventListener("click", function(){
    localStorage.removeItem("jarvis_nexus_mem"); mem = [];
    add("Memory cleared from this browser.", "ai", "memory");
  });
})();
"""

SITE_JS = """
"use strict";
(function(){
  /* ---- scroll reveal ---- */
  var targets = document.querySelectorAll(".rv, section > .wrap > h2, section > .wrap > .eyebrow, .gcard, .scard, .pcard, .card, .calc, .glasspanel, .index .row, details");
  targets.forEach(function(el, i){ el.classList.add("rv"); el.style.transitionDelay = (Math.min(i,6)*60)+"ms"; });
  var rvs = document.querySelectorAll(".rv");
  if ("IntersectionObserver" in window){
    var io = new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add("in"); io.unobserve(e.target); } }); }, {threshold:.12});
    rvs.forEach(function(el){ io.observe(el); });
  } else { rvs.forEach(function(el){ el.classList.add("in"); }); }

  /* ---- smooth page transitions ---- */
  var ov = document.createElement("div"); ov.id = "vt-overlay"; document.body.appendChild(ov);
  function go(href){
    if (document.startViewTransition){
      document.documentElement.classList.add("vt-fade");
      document.startViewTransition(function(){ location.href = href; });
    } else {
      ov.style.opacity = 1; ov.style.pointerEvents = "all";
      setTimeout(function(){ location.href = href; }, 200);
    }
  }
  document.addEventListener("click", function(ev){
    var a = ev.target.closest("a");
    if (!a || a.target === "_blank" || a.getAttribute("href") === null) return;
    var href = a.getAttribute("href");
    if (!href || href.startsWith("#") || href.startsWith("mailto:") || a.host !== location.host) return;
    ev.preventDefault(); go(href);
  });

  /* ---- active nav ---- */
  document.querySelectorAll(".nlinks a, .topnav a").forEach(function(a){
    if (a.getAttribute("href") === location.pathname) a.classList.add("act");
  });

  /* ---- concierge chat (real: Workers AI when bound, knowledge-base otherwise) ---- */
  var KB = [
    [/pric|cost|fee|charge|tariff/i, "Retainers: Enrichment Starter CHF 1,500/mo (500 validated records + 50 outreach drafts), Growth Engine CHF 3,500/mo (1,500 records + CRM hygiene), pSEO Engine CHF 4,500/mo (60 landing pages/quarter). À la carte: CHF 1-3 per validated lead, CHF 15-60 per published page. Two-week pilot: CHF 900."],
    [/approv|nexus|human|consent|sign|authoris|authoriz/i, "Every external, financial or high-stakes action — including each act of machine self-replication — becomes a plain-language request in The Nexus and waits for the founder's approval phrase. Nothing executes automatically. Ever."],
    [/safe|risk|lose|crash|fail|guard/i, "Money rules are hard-coded: whole-share ETF limit orders only, no leverage, no derivatives, no short selling, 180-day minimum holding, turnover capped at 5x assets, and a kill-switch file freezes everything. It is engineering, not financial advice."],
    [/replicat|worker|spawn|multiply|alone|autonom/i, "The Core can replicate as bounded worker processes on the founder's machine: max 3 concurrent, depth 1 (workers cannot breed), each dies after one read-only report — and each spawn needs human approval first."],
    [/data|privacy|gdpr|store|secret/i, "The public site stores nothing personal. Ledgers, logs and approvals live on the founder's machine in Switzerland; the client portal (when enabled) uses Swiss-region storage with hashed passwords and 7-day sessions. FADP & GDPR rights honoured on request."],
    [/invest|etf|3a|pillar|sweep|profit/i, "Each month 30% of net profit is swept: 40% to Swiss Pillar 3a (annual ceiling aware) and 60% to a global ETF (VWRL/VT) via dollar-cost averaging — every order human-approved first. Zero or negative months route nothing."],
    [/contact|email|founder|human being|call/i, "One human reads every message: cogops.engine@gmail.com — or use the 'Write to the founder' button below."],
    [/domain|google|search|seo|index/i, "The site ships sitemap.xml and robots.txt; a custom domain plus Google Search Console submission brings search traffic within days to weeks. Programmatic SEO for clients runs on the same discipline."],
    [/product|service|offer|sell|do you/i, "Two product lines: hyper-personalized B2B lead enrichment (validated decision-maker data + per-prospect outreach) and programmatic SEO (data-driven long-tail landing pages at scale), delivered as monthly retainers."]
  ];
  function kbAnswer(q){ return window.__nexusKB(q).answer; }
  window.__nexusKB = function(q){
    for (var i=0;i<KB.length;i++) if (KB[i][0].test(q)) return {answer: KB[i][1], topic: KB[i][0].source};
    return {answer: "I can help with products, pricing, safety, governance, data, contact, research briefs, the system map and proposal documents — or open the full Nexus interface for tools. For anything else: cogops.engine@gmail.com", topic: "fallback"};
  };
  window.__nexusAsk = async function(q){
    var kbHit = window.__nexusKB(q);
    if (kbHit.topic !== "fallback")   /* fact questions cost zero model tokens */
      return {answer: kbHit.answer, mode: "knowledge", reasoning: ["matched a published-fact cluster", "answered without spending any AI tokens"]};
    try {
      var r = await fetch("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({message: q, history: (JSON.parse(localStorage.getItem("jarvis_nexus_mem") || "[]")).slice(-6)})});
      if (r.ok){ var j = await r.json(); if (j.answer) return {answer: j.answer, mode: "nexus ai", reasoning: j.reasoning || []}; }
    } catch (e) {}
    return {answer: kbHit.answer, mode: "knowledge", reasoning: ["AI unreachable — answered from published facts"]};
  };
  function buildChat(){
    var fab = document.createElement("button"); fab.id = "chat-fab"; fab.setAttribute("aria-label","Open concierge chat"); fab.textContent = "✦";
    var panel = document.createElement("div"); panel.id = "chat-panel";
    panel.innerHTML = '<div id="chat-head"><div><b>Nexus</b> <a href="/nexus.html" style="font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--g3);text-decoration:none;border:1px solid var(--line);border-radius:999px;padding:3px 9px;background:#fff;margin-left:6px">full interface</a><br><span id="chat-mode">knowledge mode</span></div>' +
      '<button class="btn ghost" id="chat-close" style="padding:6px 12px;font-size:11px">Close</button></div>' +
      '<div id="chat-body"></div><div id="chat-foot"><input id="chat-in" placeholder="Ask about pricing, safety, the agents…" aria-label="Message">' +
      '<button class="btn grad" id="chat-send" style="padding:10px 16px">Send</button></div>';
    document.body.appendChild(fab); document.body.appendChild(panel);
    var body = panel.querySelector("#chat-body");
    function add(text, who, tag){
      var m = document.createElement("div"); m.className = "cmsg " + who;
      m.innerHTML = (tag ? '<span class="tag">' + tag + "</span>" : "") + text.replace(/[&<>]/g, function(c){ return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c]; });
      body.appendChild(m); body.scrollTop = body.scrollHeight; return m;
    }
    add("I'm Nexus — this enterprise's assistant. I answer from verified public facts, and I always show my mode and reasoning. Tools (research, system map, proposals) live in the full interface.", "ai", "nexus");
    async function send(){
      var inp = panel.querySelector("#chat-in"); var q = inp.value.trim(); if (!q) return;
      inp.value = ""; add(q, "me");
      var placeholder = add("…", "ai", "thinking");
      var res = await window.__nexusAsk(q);
      placeholder.remove();
      add(res.answer, "ai", res.mode);
      panel.querySelector("#chat-mode").textContent = res.mode + " mode";
      var mem = JSON.parse(localStorage.getItem("jarvis_nexus_mem") || "[]");
      mem.push({q: q, a: res.answer, mode: res.mode, at: Date.now()});
      localStorage.setItem("jarvis_nexus_mem", JSON.stringify(mem.slice(-50)));
    }
    panel.querySelector("#chat-send").addEventListener("click", send);
    panel.querySelector("#chat-in").addEventListener("keydown", function(e){ if (e.key === "Enter") send(); });
    panel.querySelector("#chat-close").addEventListener("click", function(){ panel.classList.remove("open"); });
    fab.addEventListener("click", function(){ panel.classList.toggle("open"); if (panel.classList.contains("open")) panel.querySelector("#chat-in").focus(); });
    window.__jarvisChatOpen = function(q){ panel.classList.add("open"); if (q){ panel.querySelector("#chat-in").value = q; send(); } else panel.querySelector("#chat-in").focus(); };
    document.querySelectorAll("[data-chat-open]").forEach(function(b){ b.addEventListener("click", function(){ window.__jarvisChatOpen(""); }); });
  }
  if (document.body) buildChat(); else document.addEventListener("DOMContentLoaded", buildChat);
})();
"""


def build_landing() -> str:
    return (LANDING.replace("__MARBLE__", MARBLE)
                   .replace("__VESSEL__", VESSEL))


SITE_BASE = os.environ.get("SITE_BASE", "https://jarvis-8wr.pages.dev")

SITE_PAGES = ["index.html", "nexus.html", "pricing.html", "journal.html", "journal/post-01.html",
              "journal/post-02.html", "privacy.html", "impressum.html", "portal.html"]


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    site = ROOT / "site"
    if site.exists():
        for f in site.rglob("*"):
            if f.is_file():
                dest = DIST / f.relative_to(site)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if f.suffix == ".html":
                    html = f.read_text(encoding="utf-8")
                    if "__ROSTER_JSON__" in html:
                        roster = json.loads((ROOT / "agent_universes.json").read_text(encoding="utf-8"))
                        html = html.replace("__ROSTER_JSON__", json.dumps(roster))
                    if 'src="/site.js"' not in html:
                        html = html.replace("</body>", '<script src="/site.js"></script>\n</body>', 1)
                    dest.write_text(html, encoding="utf-8")
                else:
                    shutil.copyfile(f, dest)
    (DIST / "style.css").write_text(SITE_CSS, encoding="utf-8")
    (DIST / "site.js").write_text(SITE_JS, encoding="utf-8")
    (DIST / "nexus.js").write_text(NEXUS_JS, encoding="utf-8")
    urls = "".join(
        f"  <url><loc>{SITE_BASE}/{'index.html' if p == 'index.html' else p}"
        f"</loc><changefreq>weekly</changefreq></url>\n"
        for p in SITE_PAGES if p != "index.html")
    (DIST / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{SITE_BASE}/</loc><changefreq>weekly</changefreq></url>\n'
        + urls + "</urlset>\n", encoding="utf-8")
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE}/sitemap.xml\n", encoding="utf-8")
    (DIST / "index.html").write_text(build_landing(), encoding="utf-8")
    print(f"dist/ built at {time.strftime('%Y-%m-%d %H:%M:%S')}:")
    for p in sorted(DIST.iterdir()):
        print(f"  {p.name:<20} {p.stat().st_size:>8,} bytes")
    print("Cloudflare Pages → build command: python3 build_static.py | output: dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
