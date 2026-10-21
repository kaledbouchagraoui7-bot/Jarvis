// Unified LLM gateway for JARVIS Pages Functions.
// Provider priority: Anthropic key > OpenAI key > Cloudflare Workers AI chain.
// Everything is env-driven: swap models or providers with dashboard variables
// or secrets — no code change, no redeploy of logic.
import { json } from "./_auth.js";

function extract(out){
  if (!out) return "";
  if (typeof out === "string") return out;
  if (out.response) return out.response;
  if (out.result) return out.result;
  if (out.choices && out.choices[0] && out.choices[0].message && out.choices[0].message.content)
    return out.choices[0].message.content;
  return "";
}

export async function generate(env, messages, maxTok){
  const system = (messages.find(m => m.role === "system") || {}).content || "";
  const convo = messages.filter(m => m.role !== "system");

  if (env.ANTHROPIC_API_KEY){
    try {
      const r = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {"x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        body: JSON.stringify({model: env.ANTHROPIC_MODEL || "claude-sonnet-4-5", max_tokens: maxTok, system, messages: convo}), signal: AbortSignal.timeout(8000)});
      if (r.ok){ const t = ((await r.json()).content || []).filter(c => c.type === "text").map(c => c.text).join(""); if (t) return {text: t, provider: "anthropic"}; }
    } catch (e) { /* fall through */ }
  }
  if (env.OPENAI_API_KEY){
    try {
      const r = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {"Authorization": "Bearer " + env.OPENAI_API_KEY, "content-type": "application/json"},
        body: JSON.stringify({model: env.OPENAI_MODEL || "gpt-5-mini", messages, max_completion_tokens: maxTok}), signal: AbortSignal.timeout(8000)});
      if (r.ok){ const t = extract(await r.json()); if (t) return {text: t, provider: "openai"}; }
    } catch (e) { /* fall through */ }
  }
  const orErrs = [];
  if (env.OPENROUTER_API_KEY){
    const orModels = (env.OPENROUTER_MODELS || "openrouter/auto").split(",").map(s => s.trim()).filter(Boolean);
    for (const m of orModels){
      try {
        const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
          method: "POST",
          headers: {"Authorization": "Bearer " + env.OPENROUTER_API_KEY, "content-type": "application/json",
                    "HTTP-Referer": "https://jarvis-8wr.pages.dev", "X-Title": "JARVIS Nexus"},
          body: JSON.stringify({model: m, messages, max_tokens: maxTok}), signal: AbortSignal.timeout(8000)});
        if (r.ok){ const t = extract(await r.json()); if (t) return {text: t, provider: "openrouter:" + m, orErrs}; }
        orErrs.push(m + ": HTTP " + r.status);
      } catch (e) { orErrs.push(m + ": " + String(e).slice(0, 90)); }
    }
  }
  if (env.GODMODE_API_KEY){
    const base = (env.GODMODE_BASE_URL || "https://api.godmode.ai/v1").replace(/\/$/, "");
    let gmModels = (env.GODMODE_MODELS || "").split(",").map(s => s.trim()).filter(Boolean);
    if (!gmModels.length){
      try {
        const dr = await fetch(base + "/models", {headers: {"Authorization": "Bearer " + env.GODMODE_API_KEY}, signal: AbortSignal.timeout(5000)});
        if (dr.ok){
          const dj = await dr.json();
          gmModels = (dj.data || dj.models || []).map(m => m.id || m.name).filter(Boolean).slice(0, 3);
        }
      } catch (e) { /* discovery failed; try generic ids below */ }
    }
    if (!gmModels.length) gmModels = ["auto", "default"];
    for (const m of gmModels){
      try {
        const r = await fetch(base + "/chat/completions", {
          method: "POST",
          headers: {"Authorization": "Bearer " + env.GODMODE_API_KEY, "content-type": "application/json"},
          body: JSON.stringify({model: m, messages, max_tokens: maxTok}), signal: AbortSignal.timeout(8000)});
        if (r.ok){ const t = extract(await r.json()); if (t) return {text: t, provider: "godmode:" + m}; }
      } catch (e) { /* next */ }
    }
  }
  if (env.AI){
    const chain = (env.CHAT_MODELS || "@cf/deepseek-ai/deepseek-v4.1-flash-1017,@cf/qwen/qwen3.8-27b,@cf/zai-org/glm-4.7-flash")
      .split(",").map(s => s.trim()).filter(Boolean);
    const chainErrs = [];
    for (const m of chain){
      try {
        const out = await env.AI.run(m, {messages, max_completion_tokens: maxTok,
          reasoning_effort: env.REASONING_EFFORT || "low"});
        const t = extract(out);
        if (t) return {text: t, provider: "workers-ai:" + m, chainErrs, orErrs};
      } catch (e) { chainErrs.push(m + ": " + String(e).slice(0, 140)); }
    }
    return {text: "", provider: null, chainErrs, orErrs};
  }
  return null;
}
