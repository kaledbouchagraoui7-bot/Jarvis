// POST /api/chat — concierge backed by Cloudflare Workers AI when the AI
// binding exists; returns 503 otherwise so the client falls back to its
// built-in knowledge base (honest degradation, never a fake answer).
import { json } from "./_auth.js";

const SYSTEM = `You are the concierge of JARVIS Enterprises, a Swiss solo enterprise.
Answer ONLY from these facts; be concise (max 4 sentences); never give financial or legal advice;
if the question is outside these facts, say the founder can be emailed at kaled.boouchagraoui@gmail.com.
FACTS: Products: (1) B2B lead enrichment — scraped, validated decision-maker data + per-prospect LLM outreach, retainers CHF 1,500-3,500/mo, CHF 1-3 per validated lead; (2) programmatic SEO — data-driven long-tail landing pages, CHF 4,500/mo retainer or CHF 15-60 per page; two-week pilot CHF 900.
Governance: 110 specialist AI agents in 7 universes; a central automaton proposes actions; EVERY external/financial/high-stakes action (including machine self-replication) requires explicit human approval via The Nexus; workers max 3, depth 1, read-only; kill switch freezes all.
Investing: 30% of monthly net profit swept; 40% to Swiss Pillar 3a (annual ceiling aware), 60% to global ETF (VWRL/VT) via DCA at IBKR; hard rules: no margin, no derivatives, no short selling, 180-day min holding, turnover <= 5x; DRY-RUN by default; not financial advice.
Data: public site stores nothing personal; ledgers/logs local in Switzerland; portal uses hashed passwords + 7-day sessions; FADP/GDPR rights honoured.`;

export async function onRequestPost(context){
  const { request, env } = context;
  if (!env.AI) return json({error: "ai_not_bound", fallback: "knowledge-base"}, 503);
  let body; try { body = await request.json(); } catch { return json({error: "bad_json"}, 400); }
  const message = String(body.message || "").slice(0, 1200);
  if (!message) return json({error: "message_required"}, 400);
  try {
    const out = await env.AI.run("@cf/meta/llama-3-8b-instruct", {
      messages: [{role: "system", content: SYSTEM}, {role: "user", content: message}],
      max_tokens: 220,
    });
    const answer = (out && (out.response || out.result)) || "";
    if (!answer) throw new Error("empty model output");
    return json({ok: true, answer: String(answer).trim()});
  } catch (e) {
    return json({error: "model_error", fallback: "knowledge-base"}, 503);
  }
}
