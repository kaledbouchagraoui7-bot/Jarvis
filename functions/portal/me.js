// GET /portal/me — session user + deliverables. POST /portal/logout — end session.
import { json, kvCheck, sessionCookie } from "../_auth.js";
export async function onRequestGet(context){
  try {
  const { request, env } = context;
  const kv = kvCheck(env);
  if (!kv) return json({error:"storage_not_enabled"}, 503);
  const email = await kv.get("session:" + (sessionCookie(request) || "")).catch(() => null);
  if (!email) return json({error:"not_signed_in"}, 401);
  const dels = await kv.get("deliverables:" + email, "json").catch(() => []);
  return json({email, deliverables: dels || []});} catch (e) { return json({error:"internal", detail: String((e && e.stack) || e).slice(0,300)}, 500); }
}export async function onRequestPost(context){
  try {
  const { request, env } = context;
  const kv = kvCheck(env);
  const token = sessionCookie(request);
  if (kv && token) await kv.delete("session:" + token).catch(() => {});
  return new Response(JSON.stringify({ok:true}), {status:200, headers:{"Content-Type":"application/json", "Set-Cookie":"jsession=; Path=/; Max-Age=0"}});} catch (e) { return json({error:"internal", detail: String((e && e.stack) || e).slice(0,300)}, 500); }
}