// POST /api/research — autonomous deep-research brief with real citations.
// Cross-references two primary public sources server-side (Wikipedia REST +
// Crossref DOI registry). If the AI binding exists, Workers AI synthesises a
// summary over the retrieved facts; otherwise the brief is compiled
// deterministically from the sources (labelled "compiled mode").
import { json } from "../_auth.js";

async function wiki(topic){
  try {
    const r = await fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(topic),
      { headers: { "User-Agent": "JARVIS-concierge/1.0 (research; contact cogops.engine@gmail.com)" } });
    if (!r.ok) return null;
    const j = await r.json();
    return j.extract ? { title: j.title, text: j.extract, url: j.content_urls?.desktop?.page || ("https://en.wikipedia.org/wiki/" + encodeURIComponent(topic)) } : null;
  } catch { return null; }
}
async function crossref(query, rows = 5){
  try {
    const r = await fetch("https://api.crossref.org/works?query=" + encodeURIComponent(query) +
      "&rows=" + rows + "&select=DOI,title,author,container-title,issued",
      { headers: { "User-Agent": "JARVIS-concierge/1.0 (mailto:cogops.engine@gmail.com)" } });
    if (!r.ok) return [];
    const j = await r.json();
    return (j.message?.items || []).map(it => ({
      doi: it.DOI,
      title: (it.title || [""])[0],
      authors: (it.author || []).slice(0, 3).map(a => (a.family || "") + (a.given ? " " + a.given[0] : "")).join("; "),
      journal: (it["container-title"] || [""])[0],
      year: it.issued?.["date-parts"]?.[0]?.[0] || "",
    })).filter(x => x.title);
  } catch { return []; }
}

export async function onRequestPost(context){
  const { request, env } = context;
  let body; try { body = await request.json(); } catch { return json({ error: "bad_json" }, 400); }
  const q = String(body.question || "").slice(0, 300);
  if (!q) return json({ error: "question_required" }, 400);
  const [w, refs] = await Promise.all([wiki(q), crossref(q)]);
  if (!w && !refs.length) return json({ error: "no_sources_found", note: "Neither Wikipedia nor Crossref returned results for this topic." }, 404);
  let synthesis = null, mode = "compiled";
  if (env.AI) {
    try {
      const ctx = (w ? "BACKGROUND: " + w.text + "\n" : "") +
        "CITED SOURCES:\n" + refs.map((r, i) => `[${i + 1}] ${r.title} (${r.year}) DOI ${r.doi}`).join("\n");
      const MODELS = ["@cf/qwen/qwen3.8-27b", "@cf/zai-org/glm-4.7-flash"];
      let out = null;
      for (const m of MODELS) { try { out = await env.AI.run(m, {
        messages: [
          { role: "system", content: "You are Nexus, a research assistant. Write a 120-word brief answering the question using ONLY the provided background and cited sources. Cite sources as [n]. End with 'Confidence:' high/medium/low and one sentence why." },
          { role: "user", content: q + "\n\n" + ctx }],
        max_completion_tokens: 400, reasoning_effort: "low" });
          const t = out && (out.response || out.result || (out.choices && out.choices[0] && out.choices[0].message && out.choices[0].message.content) || (typeof out === "string" ? out : ""));
          if (t) { out = { __txt: t }; break; } out = null; } catch (me) { } }
      synthesis = (out && out.__txt) || null;
      if (synthesis) mode = "ai-synthesised";
    } catch { /* stay compiled */ }
  }
  const brief = synthesis ||
    (w ? w.text.split(". ").slice(0, 3).join(". ") + "." : "No encyclopaedic background found; see citations.") +
    (refs.length ? " Peer-reviewed context: " + refs.slice(0, 2).map((r, i) => `${r.title} (${r.year}) [${i + 1}]`).join("; ") + "." : "");
  return json({ ok: true, mode, question: q, brief,
    reasoning: ["queried Wikipedia REST summary for background",
                "queried Crossref DOI registry for primary literature",
                mode === "ai-synthesised" ? "Workers AI synthesised the brief over retrieved facts only" : "brief compiled deterministically from retrieved facts (AI binding absent)"],
    sources: { background: w, citations: refs } });
}
