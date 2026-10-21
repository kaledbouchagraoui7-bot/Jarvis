
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
    if (idx !== -1){ out = out.slice(0, idx) + reasoning(out.slice(idx + 10).split("\n").filter(Boolean).map(s => s.replace(/^[-*]\s*/, ""))); }
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
