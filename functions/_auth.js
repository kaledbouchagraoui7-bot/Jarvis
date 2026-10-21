// Shared helpers for JARVIS Pages Functions. Imported as /functions/_auth.js.
export function json(data, status = 200){
  return new Response(JSON.stringify(data), {status, headers:{"Content-Type":"application/json"}});
}
export function kvCheck(env){
  return env.PORTAL ? env.PORTAL : null;
}
const enc = new TextEncoder();
export async function hashPassword(password, salt){
  const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({name:"PBKDF2", salt: enc.encode(salt), iterations:120000, hash:"SHA-256"}, key, 256);
  return btoa(String.fromCharCode(...new Uint8Array(bits)));
}
export function sessionCookie(request){
  const c = (request.headers.get("Cookie") || "").split(";").map(s => s.trim()).find(s => s.startsWith("jsession="));
  return c ? c.slice("jsession=".length) : null;
}
export function setSession(token){
  return "jsession=" + token + "; Path=/; HttpOnly; SameSite=Strict; Secure; Max-Age=604800";
}
