// POST /portal/login — verify and start a 7-day session.
import { json, kvCheck, hashPassword, setSession } from "../_auth.js";
export async function onRequestPost(context){
  try {
  const { request, env } = context;
  const kv = kvCheck(env);
  if (!kv) return json({error:"storage_not_enabled"}, 503);
  let body; try { body = await request.json(); } catch { return json({error:"bad_json"}, 400); }
  const { email, password } = body || {};
  const rec = await kv.get("user:" + (email || "").toLowerCase(), "json").catch(() => null);
  if (!rec || (await hashPassword(password || "", rec.salt)) !== rec.hash) return json({error:"invalid_credentials"}, 401);
  const token = crypto.randomUUID();
  await kv.put("session:" + token, rec.email, {expirationTtl: 604800});
  const dels = await kv.get("deliverables:" + rec.email, "json").catch(() => []);
  return new Response(JSON.stringify({email: rec.email, deliverables: dels || []}),
    {status:200, headers:{"Content-Type":"application/json", "Set-Cookie": setSession(token)}});} catch (e) { return json({error:"internal", detail: String((e && e.stack) || e).slice(0,300)}, 500); }
}