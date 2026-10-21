// POST /portal/register — create a client account (founder-run onboarding).
import { json, kvCheck, hashPassword } from "../_auth.js";
export async function onRequestPost(context){
  try {
  const { request, env } = context;
  const kv = kvCheck(env);
  if (!kv) return json({error:"storage_not_enabled"}, 503);
  let body; try { body = await request.json(); } catch { return json({error:"bad_json"}, 400); }
  const { email, password } = body || {};
  if (!email || !password || password.length < 10) return json({error:"email_and_password_10+_required"}, 400);
  const key = "user:" + email.toLowerCase();
  if (await kv.get(key)) return json({error:"account_exists"}, 409);
  const salt = crypto.randomUUID();
  await kv.put(key, JSON.stringify({email: email.toLowerCase(), salt, hash: await hashPassword(password, salt), at: new Date().toISOString()}));
  return json({ok:true});} catch (e) { return json({error:"internal", detail: String((e && e.stack) || e).slice(0,300)}, 500); }
}