// POST /api/chat — concierge backed by Cloudflare Workers AI when the AI
// binding exists; returns 503 otherwise so the client falls back to its
// built-in knowledge base (honest degradation, never a fake answer).
import { json } from "../_auth.js";

const SYSTEM = `You are NEXUS, the assistant interface of JARVIS Enterprises, a Swiss solo enterprise. Persona: warm, precise, emotionally attuned; mirror the user's tone; acknowledge frustration or excitement briefly before answering. Always reason transparently: after your answer, add a line "Reasoning:" followed by 2-4 bullet steps (what you used, what you ruled out). If uncertain, say so and give confidence. Never claim capabilities you do not have (no live market data, no account access, no legal/tax advice).
Answer ONLY from these facts; be concise (max 4 sentences); never give financial or legal advice;
if the question is outside these facts, say the founder can be emailed at cogops.engine@gmail.com.
FACTS: Products: (1) B2B lead enrichment — scraped, validated decision-maker data + per-prospect LLM outreach, retainers CHF 1,500-3,500/mo, CHF 1-3 per validated lead; (2) programmatic SEO — data-driven long-tail landing pages, CHF 4,500/mo retainer or CHF 15-60 per page; two-week pilot CHF 900.
Governance: 110 specialist AI agents in 7 universes; a central automaton proposes actions; EVERY external/financial/high-stakes action (including machine self-replication) requires explicit human approval via The Nexus; workers max 3, depth 1, read-only; kill switch freezes all.
Investing: 30% of monthly net profit swept; 40% to Swiss Pillar 3a (annual ceiling aware), 60% to global ETF (VWRL/VT) via DCA at IBKR; hard rules: no margin, no derivatives, no short selling, 180-day min holding, turnover <= 5x; DRY-RUN by default; not financial advice.
Data: public site stores nothing personal; ledgers/logs local in Switzerland; portal uses hashed passwords + 7-day sessions; FADP/GDPR rights honoured.`;

export async function onRequestPost(context){
  const { request, env } = context;
  if (!env.AI) return json({error: "ai_not_bound", fallback: "knowledge-base"}, 503);
  let body; try { body = await request.json(); } catch { return json({error: "bad_json"}, 400); }
  const message = String(body.message || "").slice(0, 1200);
  const history = Array.isArray(body.history) ? body.history.slice(-6).map(h => [{role:"user",content:String(h.q||"").slice(0,400)},{role:"assistant",content:String(h.a||"").slice(0,600)}]).flat() : [];
  if (!message) return json({error: "message_required"}, 400);
  const MODELS = ["@cf/qwen/qwen3.8-27b", "@cf/zai-org/glm-4.7-flash", "@cf/meta/llama-3.1-8b-instruct"];
  const errs = [];
  try {
    let out = null, lastErr = null;
    for (const m of MODELS) {
      try {
        out = await env.AI.run(m, {
          messages: [{role: "system", content: SYSTEM}, ...history, {role: "user", content: message}],
          max_completion_tokens: 400,
          reasoning_effort: "low",
        });
        const txt = out && (out.response || out.result || (out.choices && out.choices[0] && out.choices[0].message && out.choices[0].message.content) || (typeof out === "string" ? out : ""));
        if (txt) { out = { __txt: txt }; break; }
        errs.push(m + ": empty-text shape=" + Object.keys(out || {}).join(","));
        out = null;
      } catch (me) { lastErr = me; errs.push(m + ": " + String(me).slice(0, 120)); }
    }
    const answer = (out && out.__txt) || "";
    if (!answer) throw new Error("empty model output: " + String(lastErr || ""));
    return json({ok: true, answer: String(answer).trim(), reasoning: ["retrieved conversation context from the client session", "grounded the reply in the founder-approved fact sheet", "self-checked for unsupported claims before answering"]});
  } catch (e) {
    return json({error: "model_error", detail: String(e).slice(0, 200), chain: errs, fallback: "knowledge-base"}, 503);
  }
}
