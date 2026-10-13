/* Cloudflare Worker: a CORS-enabled, whitelisted pass-through to TWSE MIS.
 *
 * Why it exists: mis.twse.com.tw sends no CORS headers, so the dashboard (a static
 * page) cannot call it from the browser. This forwards ONLY quote lookups, and only
 * for symbols that look like Taiwan tickers, so it cannot be used as an open proxy.
 *
 *   GET /?ex_ch=tse_2330.tw|otc_6488.tw|tse_t00.tw
 *
 * Edge-cached for 4 s so many open tabs cost TWSE one request per 4 s, not one each.
 */
const MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0&ex_ch=";
const CH = /^(tse|otc)_[0-9A-Za-z]{1,8}\.tw$/;
const MAX_CHANNELS = 40;
const TTL = 4;

export function validChannels(raw) {
  if (!raw) return null;
  const list = raw.split("|");
  if (list.length > MAX_CHANNELS || !list.every((c) => CH.test(c))) return null;
  return list;
}

function cors(env, req) {
  const allow = (env.ALLOW_ORIGIN || "*").split(",").map((s) => s.trim());
  const o = req.headers.get("Origin");
  const ok = allow.includes("*") ? "*" : allow.includes(o) ? o : allow[0];
  return { "Access-Control-Allow-Origin": ok, "Vary": "Origin" };
}

export default {
  async fetch(req, env, ctx) {
    const h = cors(env, req);
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: { ...h, "Access-Control-Allow-Methods": "GET" } });
    if (req.method !== "GET") return new Response("method not allowed", { status: 405, headers: h });
    const list = validChannels(new URL(req.url).searchParams.get("ex_ch"));
    if (!list) return new Response("bad ex_ch", { status: 400, headers: h });

    const key = new Request(new URL("/?ex_ch=" + list.join("|"), req.url).toString());
    const cache = caches.default;
    let res = await cache.match(key);
    if (!res) {
      const up = await fetch(MIS + list.join("|"), { headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp" } });
      if (!up.ok) return new Response("upstream " + up.status, { status: 502, headers: h });
      res = new Response(await up.text(), { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=" + TTL } });
      ctx.waitUntil(cache.put(key, res.clone()));
    }
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(h)) out.headers.set(k, v);
    return out;
  },
};
