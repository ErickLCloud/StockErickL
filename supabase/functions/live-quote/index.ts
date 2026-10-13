/* Supabase Edge Function: a CORS-enabled, whitelisted pass-through to TWSE MIS.
 * Same contract as worker/live-proxy.js (the Cloudflare alternative):
 *
 *   GET /functions/v1/live-quote?ex_ch=tse_2330.tw|otc_6488.tw|tse_t00.tw
 *
 * mis.twse.com.tw sends no CORS headers, so the static dashboard cannot call it
 * directly. Only Taiwan-ticker channels are forwarded; this is not an open proxy.
 * Deployed with verify_jwt=false: it serves public, read-only quotes.
 */
const MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0&ex_ch=";
const CH = /^(tse|otc)_[0-9A-Za-z]{1,8}\.tw$/;
const MAX_CHANNELS = 40;
const ALLOW_ORIGIN = Deno.env.get("ALLOW_ORIGIN") || "*";

function cors(req: Request): Record<string, string> {
  const allow = ALLOW_ORIGIN.split(",").map((s) => s.trim());
  const o = req.headers.get("Origin") || "";
  return { "Access-Control-Allow-Origin": allow.includes("*") ? "*" : allow.includes(o) ? o : allow[0], "Vary": "Origin" };
}

export function validChannels(raw: string | null): string[] | null {
  if (!raw) return null;
  const list = raw.split("|");
  return list.length <= MAX_CHANNELS && list.every((c) => CH.test(c)) ? list : null;
}

Deno.serve(async (req: Request) => {
  const h = cors(req);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: { ...h, "Access-Control-Allow-Methods": "GET" } });
  if (req.method !== "GET") return new Response("method not allowed", { status: 405, headers: h });
  const list = validChannels(new URL(req.url).searchParams.get("ex_ch"));
  if (!list) return new Response("bad ex_ch", { status: 400, headers: h });
  try {
    const up = await fetch(MIS + list.join("|"), { headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp" } });
    if (!up.ok) return new Response("upstream " + up.status, { status: 502, headers: h });
    return new Response(await up.text(), { headers: { ...h, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
  } catch (e) {
    return new Response("upstream error", { status: 502, headers: h });
  }
});
