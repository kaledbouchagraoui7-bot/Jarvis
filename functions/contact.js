// POST /contact — store a contact message in KV (CONTACTS binding) when enabled.
import { json } from "./_auth.js";
export async function onRequestPost(context){
  const { request, env } = context;
  let body; try { body = await request.json(); } catch { return json({error:"bad_json"}, 400); }
  const { name, email, message } = body || {};
  if (!email || !message) return json({error:"email_and_message_required"}, 400);
  const store = env.CONTACTS;
  if (store){
    await store.put("contact:" + Date.now() + ":" + (email || "anon"),
      JSON.stringify({name: name || null, email, message, at: new Date().toISOString()}));
    return json({ok:true, stored:true});
  }
  return json({ok:false, stored:false, fallback:"mailto:kaled.boouchagraoui@gmail.com",
    note:"Storage not enabled on this deployment yet — your browser will open its mail app."});
}
export async function onRequestGet(){ return json({error:"post_only"}, 405); }
