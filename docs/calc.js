/* Pure logic for the dashboard: no DOM, no network, no storage.
 *
 * Kept separate from index.html so it can be tested in a headless browser
 * (tests/web/selftest.html) with the same numbers the Python side was
 * hand-checked against. Loaded as a plain script; exposes window.Calc.
 */
"use strict";
(function (root) {
  const DAY = 86400000;

  /* Whole days from ISO date a to ISO date b. UTC arithmetic, so no DST skew. */
  function isoDays(a, b) {
    const pa = a.split("-").map(Number), pb = b.split("-").map(Number);
    return Math.round(
      (Date.UTC(pb[0], pb[1] - 1, pb[2]) - Date.UTC(pa[0], pa[1] - 1, pa[2])) / DAY);
  }

  function validISO(s) {
    if (typeof s !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    const [y, m, d] = s.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d));
    return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
  }

  /* Close on `iso`, or on the nearest EARLIER trading day (weekend, holiday).
   * h = {d: [ISO dates ascending], p: [closes]}.
   *
   * Returns {date, price, exact, after}, or null when the history cannot
   * answer: empty, bad date, or `iso` earlier than the first stored day.
   * `after` is true when `iso` is later than the last stored day; the caller
   * decides what that means (for today, a live quote is the better price).
   * A missing or non-positive close is skipped back to the previous good day
   * rather than returned as a price. */
  function priceOnOrBefore(h, iso) {
    if (!h || !Array.isArray(h.d) || !Array.isArray(h.p) || !h.d.length || !validISO(iso)) return null;
    const d = h.d, p = h.p;
    if (iso < d[0]) return null;                     // ISO strings sort chronologically
    let lo = 0, hi = d.length - 1;
    while (lo < hi) {                                // last index with d[i] <= iso
      const mid = (lo + hi + 1) >> 1;
      if (d[mid] <= iso) lo = mid; else hi = mid - 1;
    }
    let i = lo;
    while (i >= 0 && !(p[i] > 0)) i--;
    if (i < 0) return null;
    return { date: d[i], price: p[i], exact: d[i] === iso, after: iso > d[d.length - 1] };
  }

  /* Today's date in Taipei, as ISO. */
  function taipeiToday(now) {
    return new Date(now || Date.now()).toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" });
  }

  /* Same definitions as the Python side (src/holdings.py):
   *   pnl      = (last - cost) * shares
   *   return % = (last / cost - 1) * 100
   *   annual % = ((last / cost) ** (365 / days) - 1) * 100, only when held >= 30 days
   * Fees and the securities transaction tax are NOT included. */
  function lotMetrics(lot, last, today) {
    const cost = lot.cost, shares = lot.shares;
    const out = { costValue: cost * shares, value: null, pnl: null,
                  retPct: null, days: null, annPct: null };
    out.days = validISO(lot.date) && validISO(today) ? isoDays(lot.date, today) : null;
    if (last == null || !(last > 0) || !(cost > 0)) return out;
    out.value = last * shares;
    out.pnl = (last - cost) * shares;
    out.retPct = (last / cost - 1) * 100;
    if (out.days != null && out.days >= 30) {
      out.annPct = (Math.pow(last / cost, 365 / out.days) - 1) * 100;
    }
    return out;
  }

  /* Totals over the lots that have a price. Unpriced lots are counted, not
   * silently folded in, so the total cannot understate a loss. */
  function totals(metrics) {
    let cost = 0, value = 0, unpriced = 0;
    for (const m of metrics) {
      if (m.value == null) { unpriced++; continue; }
      cost += m.costValue;
      value += m.value;
    }
    const pnl = value - cost;
    return { cost, value, pnl, retPct: cost > 0 ? (pnl / cost) * 100 : null, unpriced };
  }

  function ageMinutes(iso, nowMs) {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return null;
    return Math.max(0, (nowMs - t) / 60000);
  }

  /* Search + filter. When there is a search term, an exact code match ranks
   * first, then a code prefix, then a name prefix, then any substring. */
  function filterItems(items, o) {
    const term = (o.term || "").trim().toLowerCase();
    const out = [];
    for (const it of items) {
      if (o.type && o.type !== "all" && it.k !== o.type) continue;
      const q = (o.quotes && o.quotes[it.c]) || {};
      if (o.pick === "up" && !(q.pct > 0)) continue;
      if (o.pick === "down" && !(q.pct < 0)) continue;
      if (o.pick === "watch" && !(o.watch && o.watch.has(it.c))) continue;
      let rank = 0;
      if (term) {
        const c = it.c.toLowerCase(), n = (it.n || "").toLowerCase();
        if (c === term) rank = 0;
        else if (c.startsWith(term)) rank = 1;
        else if (n.startsWith(term)) rank = 2;
        else if (c.includes(term) || n.includes(term)) rank = 3;
        else continue;
      }
      out.push({ it, rank });
    }
    return out;
  }

  function sortItems(entries, key, quotes) {
    const val = (e) => {
      const q = (quotes && quotes[e.it.c]) || {};
      return key === "vol" ? q.vol : q.pct;
    };
    const cmpCode = (a, b) => (a.it.c < b.it.c ? -1 : a.it.c > b.it.c ? 1 : 0);
    return entries.slice().sort((a, b) => {
      if (a.rank !== b.rank) return a.rank - b.rank;          // search relevance first
      if (key === "code") return cmpCode(a, b);
      const va = val(a), vb = val(b);
      const na = va == null, nb = vb == null;
      if (na !== nb) return na ? 1 : -1;                      // missing values always last
      if (na && nb) return cmpCode(a, b);
      const d = key === "pct_asc" ? va - vb : vb - va;
      return d !== 0 ? d : cmpCode(a, b);
    });
  }

  /* Validate imported holdings. Never throws on bad input; reports it. */
  function parseLots(text) {
    let data;
    try { data = JSON.parse(text); } catch (e) { return { lots: [], rejected: [{ index: -1, reason: "不是有效的 JSON" }] }; }
    const arr = Array.isArray(data) ? data : (data && Array.isArray(data.lots) ? data.lots : null);
    if (!arr) return { lots: [], rejected: [{ index: -1, reason: "找不到持倉陣列" }] };
    const lots = [], rejected = [];
    arr.forEach((r, index) => {
      const code = r && typeof r.code === "string" ? r.code.trim() : "";
      const shares = Number(r && r.shares), cost = Number(r && r.cost);
      if (!code) return rejected.push({ index, reason: "缺少代號" });
      if (!validISO(r.date)) return rejected.push({ index, reason: "買入日需為 YYYY-MM-DD" });
      if (!(shares > 0) || !Number.isFinite(shares)) return rejected.push({ index, reason: "股數需為正數" });
      if (!(cost > 0) || !Number.isFinite(cost)) return rejected.push({ index, reason: "成本價需為正數" });
      lots.push({ code, date: r.date, shares, cost,
                  note: typeof r.note === "string" ? r.note.slice(0, 100) : "" });
    });
    return { lots, rejected };
  }

  root.Calc = { isoDays, validISO, taipeiToday, priceOnOrBefore, lotMetrics, totals, ageMinutes,
                filterItems, sortItems, parseLots };
})(typeof window !== "undefined" ? window : globalThis);
