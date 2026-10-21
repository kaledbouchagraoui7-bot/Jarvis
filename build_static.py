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
<title>JARVIS — The Sovereign AI Enterprise</title>
<meta name="description" content="A lean Swiss solo enterprise: 110 specialist agents in seven universes, a Nexus-gated central automaton, and a compliance-bound Investment Automaton.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap" rel="stylesheet">
<style>
:root{--paper:#F2EEE7;--paper2:#EAE4D9;--card:#FBFAF7;--ink:#1E1B16;--ink2:#57503F;--taupe:#A99C86;
--sand:#C9BFAF;--line:#DAD3C6;--serif:'Cormorant Garamond',Georgia,'Times New Roman',serif;
--sans:system-ui,'Segoe UI',Roboto,sans-serif;
--grain:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E")}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.65}
body::before{content:"";position:fixed;inset:0;pointer-events:none;background:var(--grain),
 linear-gradient(112deg,transparent 38%,rgba(255,255,255,.5) 45%,transparent 57%),
 linear-gradient(112deg,transparent 64%,rgba(255,255,255,.38) 71%,transparent 83%)}
.wrap{max-width:1180px;margin:0 auto;padding:0 34px;position:relative}
.eyebrow{font-size:10px;letter-spacing:.42em;text-transform:uppercase;color:var(--ink2)}
.eyebrow.taupe{color:var(--taupe)}
h1,h2,h3.serif{font-family:var(--serif);font-weight:500}
.rule{width:44px;height:1px;background:var(--taupe);margin:26px 0}
.btn{display:inline-block;background:var(--ink);color:var(--paper);padding:13px 26px;font-size:10px;
 letter-spacing:.26em;text-transform:uppercase;text-decoration:none;border:0;cursor:pointer;border-radius:1px}
.btn:hover{background:#3a352c}
.link{font-size:10.5px;letter-spacing:.24em;text-transform:uppercase;color:var(--ink);text-decoration:none;border-bottom:1px solid var(--taupe);padding-bottom:3px}
header{padding:34px 0 0}
.topbar{display:flex;justify-content:space-between;align-items:baseline;padding-bottom:70px}
.brand{font-family:var(--serif);font-size:19px;letter-spacing:.4em}
.topnav{display:flex;gap:26px}
.topnav a{font-size:9.5px;letter-spacing:.26em;text-transform:uppercase;color:var(--ink2);text-decoration:none}
.topnav a:hover{color:var(--ink)}
.hero{display:grid;grid-template-columns:1.05fr .95fr;gap:60px;align-items:start;padding-bottom:90px;position:relative}
.hero h1{font-size:clamp(52px,7.4vw,96px);line-height:.98;letter-spacing:-.01em;margin:20px 0 0}
.hero h1 em{font-style:italic;color:var(--taupe)}
.lede{margin-top:26px;max-width:430px;color:var(--ink2);font-size:14.5px}
.plate{position:relative}
.plate .img{width:100%;aspect-ratio:4/5;background:var(--marble) center/cover;border:1px solid var(--line)}
.plate .cap{position:absolute;top:34px;left:-56px;background:var(--card);border:1px solid var(--line);
 padding:30px 30px 26px;max-width:270px;box-shadow:0 18px 44px rgba(30,27,22,.10)}
.plate .cap h3{font-family:var(--serif);font-size:31px;line-height:1.08;margin:10px 0 12px;font-weight:500}
.plate .cap p{font-size:12px;color:var(--ink2);margin-bottom:16px}
.swatches{position:absolute;right:-64px;top:6px;display:flex;flex-direction:column;gap:14px}
.swatches i{width:64px;height:74px;display:block}
@media(max-width:1080px){.swatches{display:none}.plate .cap{left:-14px}}
@media(max-width:860px){.hero{grid-template-columns:1fr;gap:44px}.topnav{display:none}}
section{padding:70px 0}
.approach{display:grid;grid-template-columns:.9fr 1.1fr;gap:56px;align-items:center}
.approach h2{font-size:clamp(34px,4vw,50px);line-height:1.05;margin:14px 0 18px}
.approach p{color:var(--ink2);font-size:14.5px;max-width:400px}
.approach .img{width:100%;aspect-ratio:1/1;background:var(--vessel) center/cover;border:1px solid var(--line)}
@media(max-width:860px){.approach{grid-template-columns:1fr}}
.features{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--ink);margin-top:64px}
.features div{padding:30px 26px 6px;border-right:1px solid var(--line)}
.features div:last-child{border-right:0}
.features h4{font-size:10px;letter-spacing:.26em;text-transform:uppercase;margin:16px 0 10px}
.features p{font-size:12.5px;color:var(--ink2)}
@media(max-width:760px){.features{grid-template-columns:1fr}.features div{border-right:0;border-bottom:1px solid var(--line)}}
.products{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:26px}
.pcard{background:var(--card);border:1px solid var(--line);padding:34px 32px}
.pcard .n{font-family:var(--serif);font-style:italic;font-size:15px;color:var(--taupe)}
.pcard h3{font-size:29px;line-height:1.12;margin:8px 0 12px}
.pcard p{font-size:13.5px;color:var(--ink2)}
.pcard ul{margin:16px 0 0;list-style:none}
.pcard li{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink2);
 padding:8px 0;border-top:1px solid var(--line)}
@media(max-width:860px){.products{grid-template-columns:1fr}}
.index{margin-top:26px;border-top:1px solid var(--ink)}
.index .row{display:grid;grid-template-columns:64px 1.1fr 2fr 90px;gap:18px;padding:15px 4px;
 border-bottom:1px solid var(--line);align-items:baseline}
.index .u{font-family:var(--mono);font-size:11px;color:var(--taupe);letter-spacing:.1em}
.index .nm{font-family:var(--serif);font-size:21px}
.index .ds{font-size:12.5px;color:var(--ink2)}
.index .ct{font-family:var(--mono);font-size:11px;text-align:right;color:var(--ink2)}
@media(max-width:760px){.index .row{grid-template-columns:44px 1fr 70px}.index .ds{display:none}}
.gov{display:grid;grid-template-columns:1.15fr .85fr;gap:56px;align-items:start}
.quote{font-family:var(--serif);font-style:italic;font-size:clamp(24px,2.6vw,33px);line-height:1.32;color:var(--ink)}
.quote .att{display:block;margin-top:20px;font-family:var(--sans);font-style:normal;font-size:10px;
 letter-spacing:.3em;text-transform:uppercase;color:var(--taupe)}
.trust{background:var(--card);border:1px solid var(--line);padding:30px 30px 12px}
.trust h3{font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:var(--taupe);margin-bottom:14px}
.kv{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line);font-size:12.5px;color:var(--ink2)}
.kv:last-child{border:0}.kv b{font-family:var(--serif);font-size:16px;color:var(--ink)}
@media(max-width:860px){.gov{grid-template-columns:1fr}}
footer{padding:54px 0 64px;text-align:center}
footer p{font-size:9.5px;letter-spacing:.3em;text-transform:uppercase;color:var(--taupe)}
.calc{margin-top:26px;background:var(--card);border:1px solid var(--line);padding:32px}
.calc input{font-family:var(--serif);font-size:34px;padding:8px 14px;border:1px solid var(--line);background:var(--paper);width:250px;color:var(--ink);outline:none}
.calc input:focus{border-color:var(--ink)}
.cout{margin-top:26px}.cout b{display:block;font-family:var(--serif);font-size:31px;margin-top:7px;color:var(--ink)}
.cout i{display:block;font-style:normal;font-size:11px;color:var(--taupe);margin-top:5px}
.fine{font-size:11.5px;color:var(--taupe);margin-top:20px}
#faq details{background:var(--card);border:1px solid var(--line);margin-bottom:10px;padding:16px 22px}
#faq summary{font-family:var(--serif);font-size:20px;letter-spacing:0;text-transform:none;color:var(--ink)}
#faq details p{font-size:13.5px;color:var(--ink2);margin-top:10px;max-width:760px}
</style></head><body>
<header><div class="wrap">
 <div class="topbar"><div class="brand">J A R V I S</div>
  <nav class="topnav"><a href="#approach">Approach</a><a href="#products">Products</a>
  <a href="#universes">Universes</a><a href="#governance">Governance</a>
  <a href="#calculator">Calculator</a></nav></div>
 <div class="hero">
  <div>
   <div class="eyebrow">What makes an enterprise sovereign</div>
   <h1>Machine<br>scale.<br><em>Human consent.</em></h1>
   <div class="rule"></div>
   <p class="lede">A lean Swiss solo enterprise where 110 specialist agents manufacture
   B2B deliverables at compute cost — and every franc, order and new machine
   instance waits for one keystroke: yours.</p>
   <div style="margin-top:30px"><a class="btn" href="#calculator">Try the sweep calculator</a></div>
  </div>
  <div class="plate">
   <div class="img" style="background-image:__MARBLE__"></div>
   <div class="cap"><div class="eyebrow taupe">The system</div>
    <h3>Bounded<br>autonomy.</h3>
    <p>We build automation that not only works alone, but knows exactly where
    its authority ends.</p>
    <a class="link" href="#governance">Learn more →</a></div>
   <div class="swatches"><i style="background:#EDE9E1"></i><i style="background:#C9BFAF"></i>
    <i style="background:#A99C86"></i><i style="background:#1E1B16"></i></div>
  </div>
 </div>
</div></header>
<section id="approach"><div class="wrap">
 <div class="approach">
  <div><div class="eyebrow taupe">Our approach</div>
   <h2>Strategy meets<br>sovereignty.</h2>
   <p>We design enterprises that not only run themselves, but are built to
   stay owned — bootstrap first, sovereign hardware last, human authority
   always.</p>
   <div style="margin-top:22px"><a class="link" href="#universes">The hierarchy →</a></div></div>
  <div class="img" style="background-image:__VESSEL__"></div>
 </div>
 <div class="features">
  <div><svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke="#1E1B16"><rect x="3" y="4" width="20" height="13"/><path d="M13 17v5M8 22h10"/></svg>
   <h4>Human gate</h4><p>Every external, financial or high-stakes action — including each act of
   machine self-replication — passes through The Nexus: one human, one keystroke.</p></div>
  <div><svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke="#1E1B16"><rect x="4" y="4" width="7" height="7"/><rect x="15" y="4" width="7" height="7"/><rect x="4" y="15" width="7" height="7"/><rect x="15" y="15" width="7" height="7"/></svg>
   <h4>Bounded autonomy</h4><p>The central automaton perceives, proposes and replicates inside hard
   budgets — cycles, proposals, workers — and freezes on a single kill switch.</p></div>
  <div><svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke="#1E1B16"><path d="M5 21V11M12 21V4M19 21v-7"/></svg>
   <h4>Compounded capital</h4><p>A fixed share of every month's profit sweeps into Pillar 3a and
   ETF dollar-cost averaging under Swiss private-investor guardrails.</p></div>
 </div>
</div></section>
<section id="products" style="background:var(--paper2)"><div class="wrap">
 <div class="eyebrow taupe">Product lines</div>
 <div class="products">
  <div class="pcard"><div class="n">No. 01</div><h3 class="serif">Hyper-personalized B2B lead enrichment</h3>
   <p>Decision-maker data scraped, validated and enriched at machine cost, then turned
   into outreach written for one person at a time — sold as retainer subscriptions.</p>
   <ul><li>Validated decision-maker data</li><li>LLM outreach, per prospect</li><li>Retainer delivery</li></ul></div>
  <div class="pcard"><div class="n">No. 02</div><h3 class="serif">Programmatic SEO at scale</h3>
   <p>Live data streams ingested and transformed into long-tail landing pages that rank —
   hundreds per client, built and refreshed autonomously.</p>
   <ul><li>Data-stream ingestion</li><li>Long-tail page generation</li><li>Autonomous refresh</li></ul></div>
 </div>
</div></section>
<section id="universes"><div class="wrap">
 <div class="eyebrow taupe">The modular hierarchy</div>
 <div class="index">
  <div class="row"><span class="u">U1</span><span class="nm">Revenue</span><span class="ds">Demand, customers, market presence</span><span class="ct">24 agents</span></div>
  <div class="row"><span class="u">U2</span><span class="nm">Delivery</span><span class="ds">Operations, logistics, product</span><span class="ct">23 agents</span></div>
  <div class="row"><span class="u">U3</span><span class="nm">Capital &amp; Compliance</span><span class="ds">Finance, tax, legal — owns the Automaton's rules</span><span class="ct">21 agents</span></div>
  <div class="row"><span class="u">U4</span><span class="nm">People</span><span class="ds">Hiring, growth, culture</span><span class="ct">10 agents</span></div>
  <div class="row"><span class="u">U5</span><span class="nm">Infrastructure</span><span class="ds">Technology, data, security</span><span class="ct">10 agents</span></div>
  <div class="row"><span class="u">U6</span><span class="nm">Strategy</span><span class="ds">Direction, intelligence, scenarios</span><span class="ct">11 agents</span></div>
  <div class="row"><span class="u">U7</span><span class="nm">Continuity</span><span class="ds">Administration, knowledge, crisis</span><span class="ct">11 agents</span></div>
 </div>
</div></section>
<section id="governance" style="background:var(--paper2)"><div class="wrap">
 <div class="gov">
  <div><div class="eyebrow taupe">Governance</div>
   <div class="rule"></div>
   <p class="quote">“The automaton may think, propose, replicate and audit freely — but every
   franc, every order and every new instance of itself passes through a gate
   only your keystroke can open.”<span class="att">— The Nexus principle</span></p></div>
  <div class="trust"><h3>Details that build trust</h3>
   <div class="kv"><span>Minimum holding period</span><b>180 days</b></div>
   <div class="kv"><span>Max annual turnover</span><b>5× assets</b></div>
   <div class="kv"><span>Margin · derivatives · short</span><b>Prohibited</b></div>
   <div class="kv"><span>Replication depth</span><b>Core → worker</b></div>
   <div class="kv"><span>Actions requiring human approval</span><b>100%</b></div></div>
 </div>
</div></section>
<section id="calculator"><div class="wrap">
 <div class="eyebrow taupe">Try it — real maths, in your browser</div>
 <h2 style="font-size:clamp(30px,3.6vw,44px);margin:12px 0 10px">What would this month invest?</h2>
 <p style="color:var(--ink2);max-width:560px;font-size:14px">Type a monthly net profit (income minus expenses and tax reserve).
 The same rules the real system applies: a 30% sweep, split 40% to Pillar 3a and 60% to the ETF —
 and in the live system, nothing moves without your approval.</p>
 <div class="calc">
  <label class="eyebrow" for="c-in" style="display:block;margin-bottom:10px">Net profit this month (CHF)</label>
  <input id="c-in" type="number" min="0" step="50" value="5000" inputmode="numeric">
  <div class="cout grid g3">
   <div><span class="eyebrow taupe">Monthly sweep · 30%</span><b id="c-sweep">—</b></div>
   <div><span class="eyebrow taupe">To Pillar 3a</span><b id="c-3a">—</b><i id="c-capnote"></i></div>
   <div><span class="eyebrow taupe">To ETF via IBKR</span><b id="c-etf">—</b></div>
  </div>
  <p class="fine">Estimate only. The live system applies the same percentages plus your remaining annual 3a ceiling,
  compliance guardrails and your Nexus approval — on your machine, never in the cloud.</p>
 </div>
</div></section>
<section id="faq" style="background:var(--paper2)"><div class="wrap">
 <div class="eyebrow taupe">Plain answers</div>
 <h2 style="font-size:clamp(30px,3.6vw,44px);margin:12px 0 18px">Questions, without jargon.</h2>
 <details><summary>Who approves the money movements?</summary>
  <p>You. Every transfer, order or new worker instance appears as a plain-language request —
  “Request: transfer CHF 636.00 to your Pillar 3a provider” — and waits for your approval phrase.
  Nothing executes by itself, ever.</p></details>
 <details><summary>Can the system lose money on its own?</summary>
  <p>No. It only buys whole shares of a broad global ETF at a limit price you approved: no leverage,
  no derivatives, no short selling, and nothing sells before 180 days. The only risk left is ordinary
  market movement on amounts you chose.</p></details>
 <details><summary>Where does my data live?</summary>
  <p>On your machine. Ledger, logs, approvals and plans are local files; this website carries no
  personal or financial data at all.</p></details>
 <details><summary>Is this investment advice?</summary>
  <p>No. It is engineering that enforces rules you set. A qualified Swiss tax advisor validates the
  thresholds before live use.</p></details>
</div></section>
<section id="contact"><div class="wrap" style="text-align:center">
 <div class="eyebrow taupe">Contact</div>
 <h2 style="font-size:clamp(30px,3.6vw,44px);margin:12px 0 14px">Work with the enterprise.</h2>
 <p style="color:var(--ink2);max-width:470px;margin:0 auto 26px;font-size:14px">Retainers for lead enrichment
 and programmatic SEO. One human reads every message.</p>
 <a class="btn" href="mailto:kaled.boouchagraoui@gmail.com?subject=JARVIS%20enquiry">Write to the founder</a>
</div></section>
<footer><div class="wrap"><p>Jarvis Enterprises · Bootstrap → Sovereignty · Zürich — Genève · Not financial advice</p></div></footer>
<script>
(function(){
  var inp = document.getElementById("c-in");
  function fmt(v){ return "CHF " + v.toLocaleString("en",{minimumFractionDigits:2,maximumFractionDigits:2}); }
  function f(){
    var net = Math.max(0, parseFloat(inp.value || "0") || 0);
    var sweep = net * 0.30, cap = 7258;
    var a = Math.min(sweep * 0.40, cap), e = sweep - a;
    document.getElementById("c-sweep").textContent = fmt(sweep);
    document.getElementById("c-3a").textContent = fmt(a);
    document.getElementById("c-etf").textContent = fmt(e);
    document.getElementById("c-capnote").textContent =
      (sweep * 0.40 > cap) ? "annual ceiling reached — the overflow goes to the ETF" : "";
  }
  inp.addEventListener("input", f); f();
})();
</script>
</body></html>
"""


SITE_CSS = """
:root{--paper:#F2EEE7;--paper2:#EAE4D9;--card:#FBFAF7;--ink:#1E1B16;--ink2:#57503F;--taupe:#A99C86;
--sand:#C9BFAF;--line:#DAD3C6;--sage:#6B8F71;--terra:#A4553F;
--serif:'Cormorant Garamond','Iowan Old Style',Georgia,'Times New Roman',serif;
--sans:system-ui,'Segoe UI',Roboto,sans-serif;--mono:ui-monospace,'Cascadia Mono',Consolas,monospace;
--grain:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E")}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.65}
body::before{content:"";position:fixed;inset:0;pointer-events:none;background:var(--grain),
 linear-gradient(112deg,transparent 38%,rgba(255,255,255,.5) 45%,transparent 57%),
 linear-gradient(112deg,transparent 64%,rgba(255,255,255,.38) 71%,transparent 83%)}
.wrap{max-width:1180px;margin:0 auto;padding:0 34px;position:relative}
section{padding:56px 0}
.eyebrow{font-size:10px;letter-spacing:.42em;text-transform:uppercase;color:var(--ink2)}
.eyebrow.taupe{color:var(--taupe)}
h1{font-family:var(--serif);font-weight:500;letter-spacing:.01em}
h1 em,h3.serif{font-family:var(--serif);font-style:italic}
.rule{width:44px;height:1px;background:var(--taupe);margin:22px 0}
.btn{display:inline-block;background:var(--ink);color:var(--paper);padding:12px 24px;font-size:10px;
 letter-spacing:.26em;text-transform:uppercase;text-decoration:none;border:0;cursor:pointer;border-radius:1px}
.btn:hover{background:#3a352c}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--ink2)}
.link{font-size:10.5px;letter-spacing:.24em;text-transform:uppercase;color:var(--ink);text-decoration:none;border-bottom:1px solid var(--taupe);padding-bottom:3px}
header{padding:30px 0 0}
.topbar{display:flex;justify-content:space-between;align-items:baseline;padding-bottom:34px}
.brand{font-family:var(--serif);font-size:18px;letter-spacing:.4em}
.topnav{display:flex;gap:22px;flex-wrap:wrap}
.topnav a{font-size:9.5px;letter-spacing:.26em;text-transform:uppercase;color:var(--ink2);text-decoration:none}
.topnav a:hover{color:var(--ink)}
.products{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:24px}
@media(max-width:900px){.products{grid-template-columns:1fr}}
.pcard{background:var(--card);border:1px solid var(--line);padding:30px 28px;border-radius:2px}
.pcard .n{font-family:var(--serif);font-style:italic;font-size:14px;color:var(--taupe)}
.pcard h3{font-size:26px;line-height:1.12;margin:8px 0 10px;font-family:var(--serif);font-weight:500}
.pcard p{font-size:13px;color:var(--ink2)}
.pcard .price{font-family:var(--serif);font-size:30px;margin:6px 0 4px}
.pcard .price span{font-family:var(--sans);font-size:11px;letter-spacing:.14em;color:var(--taupe)}
.pcard ul{margin:14px 0 0;list-style:none}
.pcard li{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink2);padding:7px 0;border-top:1px solid var(--line)}
.index{margin-top:22px;border-top:1px solid var(--ink)}
.index .row{display:grid;grid-template-columns:56px 1.4fr 2fr 80px;gap:16px;padding:16px 4px;border-bottom:1px solid var(--line);align-items:baseline}
.index .u{font-family:var(--mono);font-size:11px;color:var(--taupe)}
.index .nm{font-family:var(--serif);font-size:20px}
.index .ds{font-size:12.5px;color:var(--ink2)}
.index .ct{font-family:var(--mono);font-size:11px;text-align:right;color:var(--ink2)}
.index .row:hover{background:rgba(169,156,134,.08)}
@media(max-width:760px){.index .row{grid-template-columns:40px 1fr 60px}.index .ds{display:none}}
.prose p{margin:0 0 16px;font-size:15px;color:var(--ink2);max-width:760px}
.prose strong{color:var(--ink)}
.kv{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:12.5px;color:var(--ink2)}
.kv:last-child{border:0}.kv b{font-family:var(--mono);font-size:11.5px;color:var(--ink)}
.card{background:var(--card);border:1px solid var(--line);border-radius:2px;padding:18px 20px}
.card h3{font-size:13px;font-family:var(--serif);margin-bottom:8px}
.calc{background:var(--card);border:1px solid var(--line);padding:26px;border-radius:2px}
.calc input,.card input{font-family:var(--serif);font-size:20px;padding:8px 12px;border:1px solid var(--line);background:var(--paper);color:var(--ink);outline:none;border-radius:1px}
.calc input:focus,.card input:focus{border-color:var(--ink)}
.fine{font-size:11.5px;color:var(--taupe);margin-top:16px}
.banner{border-radius:2px;padding:12px 16px;font-size:13px;border:1px solid;line-height:1.55}
.banner.red{background:#F6ECE8;border-color:var(--terra);color:#7C3A2D}
.banner.blue{background:#EEF1EA;border-color:var(--sage);color:#3F5D44}
.hidden{display:none!important}
footer{padding:48px 0 56px;text-align:center}
footer p,footer a{font-size:9.5px;letter-spacing:.3em;text-transform:uppercase;color:var(--taupe);text-decoration:none}
a{color:#8A6A2F}
::selection{background:var(--sand);color:var(--ink)}
"""

def build_landing() -> str:
    return (LANDING.replace("__MARBLE__", MARBLE)
                   .replace("__VESSEL__", VESSEL))


SITE_BASE = os.environ.get("SITE_BASE", "https://jarvis-8wr.pages.dev")

SITE_PAGES = ["index.html", "pricing.html", "journal.html", "journal/post-01.html",
              "journal/post-02.html", "privacy.html", "impressum.html", "portal.html"]


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    site = ROOT / "site"
    if site.exists():
        for f in site.rglob("*"):
            if f.is_file():
                dest = DIST / f.relative_to(site)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dest)
    (DIST / "style.css").write_text(SITE_CSS, encoding="utf-8")
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
