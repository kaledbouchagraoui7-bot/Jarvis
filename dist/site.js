
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
    [/contact|email|founder|human being|call/i, "One human reads every message: kaled.boouchagraoui@gmail.com — or use the 'Write to the founder' button below."],
    [/domain|google|search|seo|index/i, "The site ships sitemap.xml and robots.txt; a custom domain plus Google Search Console submission brings search traffic within days to weeks. Programmatic SEO for clients runs on the same discipline."],
    [/product|service|offer|sell|do you/i, "Two product lines: hyper-personalized B2B lead enrichment (validated decision-maker data + per-prospect outreach) and programmatic SEO (data-driven long-tail landing pages at scale), delivered as monthly retainers."]
  ];
  function kbAnswer(q){
    for (var i=0;i<KB.length;i++) if (KB[i][0].test(q)) return KB[i][1];
    return "I can answer about products, pricing, safety, governance, data and contact — try one of those, or email the founder: kaled.boouchagraoui@gmail.com";
  }
  function buildChat(){
    var fab = document.createElement("button"); fab.id = "chat-fab"; fab.setAttribute("aria-label","Open concierge chat"); fab.textContent = "✦";
    var panel = document.createElement("div"); panel.id = "chat-panel";
    panel.innerHTML = '<div id="chat-head"><div><b>Jarvis Concierge</b><br><span id="chat-mode">knowledge mode</span></div>' +
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
    add("Hello — I answer from the enterprise's public information" + (window.__JARVIS_AI__ ? " and the founder-approved AI." : ". For anything else, email the founder."), "ai", "concierge");
    async function send(){
      var inp = panel.querySelector("#chat-in"); var q = inp.value.trim(); if (!q) return;
      inp.value = ""; add(q, "me");
      var placeholder = add("…", "ai", "thinking");
      var answer = null, mode = "concierge";
      try {
        var r = await fetch("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({message: q})});
        if (r.ok){ var j = await r.json(); if (j.answer){ answer = j.answer; mode = "workers ai"; } }
      } catch (e) { /* fall through to knowledge base */ }
      placeholder.remove();
      add(answer || kbAnswer(q), "ai", mode);
      panel.querySelector("#chat-mode").textContent = mode === "workers ai" ? "ai mode (workers ai)" : "knowledge mode";
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
