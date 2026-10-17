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

  /* Cash dividends per share a holder since `buyISO` received by `todayISO`.
   * h = a symbol's history file: {d: [ISO dates], div: [[exDate, perShare], ...]}.
   *
   * A buyer must hold BEFORE the ex-date, so a dividend whose ex-date is the
   * buy date or earlier is not theirs. `complete` is false when the buy date is
   * earlier than the history window: dividends before the window cannot be seen,
   * so the figure is a floor, not the whole story. `through` is the last date
   * the history covers, so a stale history is visible to the caller.
   * Amounts are in today's share units (the same units as the adjusted prices):
   * 0050's January 2025 dividend is 0.675 here but 2.70 as TWSE paid it, because
   * of the 1-for-4 split since. null when the history cannot say anything. */
  function dividendsFor(h, buyISO, todayISO) {
    if (!h || !Array.isArray(h.d) || !h.d.length || !validISO(buyISO)) return null;
    const rows = (h.div || []).filter((x) => x[0] > buyISO && x[0] <= todayISO);
    return { perShare: rows.reduce((s, x) => s + x[1], 0), count: rows.length, rows,
             complete: buyISO >= h.d[0], through: h.d[h.d.length - 1] };
  }

  /* Same definitions as the Python side (src/holdings.py):
   *   pnl      = (last - cost) * shares                      (price only)
   *   return % = (last / cost - 1) * 100
   *   annual % = ((last / cost) ** (365 / days) - 1) * 100, only when held >= 30 days
   * Given `div` (from dividendsFor) it also reports the dividends received and
   * the TOTAL return, which adds the dividends per share to the price:
   *   total pnl = pnl + dividends * shares
   *   total %   = total pnl / cost value * 100
   *   total annual % = (((last + dividends per share) / cost) ** (365 / days) - 1) * 100
   * Dividends are not reinvested; fees, the transaction tax, the dividend
   * withholding / supplementary health premium are NOT included. */
  function lotMetrics(lot, last, today, div) {
    const cost = lot.cost, shares = lot.shares;
    const out = { costValue: cost * shares, value: null, pnl: null, retPct: null, days: null, annPct: null,
                  div: null, divComplete: null, total: null, totRetPct: null, totAnnPct: null };
    out.days = validISO(lot.date) && validISO(today) ? isoDays(lot.date, today) : null;
    if (div) { out.div = div.perShare * shares; out.divComplete = div.complete; }
    if (last == null || !(last > 0) || !(cost > 0)) return out;
    out.value = last * shares;
    out.pnl = (last - cost) * shares;
    out.retPct = (last / cost - 1) * 100;
    if (out.days != null && out.days >= 30) {
      out.annPct = (Math.pow(last / cost, 365 / out.days) - 1) * 100;
    }
    if (div) {
      out.total = out.pnl + out.div;
      out.totRetPct = (out.total / out.costValue) * 100;
      if (out.days != null && out.days >= 30)
        out.totAnnPct = (Math.pow((last + div.perShare) / cost, 365 / out.days) - 1) * 100;
    }
    return out;
  }

  /* Totals over the lots that have a price. Unpriced lots are counted, not
   * silently folded in, so the total cannot understate a loss. Likewise a lot
   * whose dividends are unknown is counted in `divUnknown` and contributes
   * price-only to `total`, instead of being quietly treated as "paid nothing". */
  function totals(metrics) {
    let cost = 0, value = 0, unpriced = 0, div = 0, divUnknown = 0, divIncomplete = 0;
    for (const m of metrics) {
      if (m.value == null) { unpriced++; continue; }
      cost += m.costValue;
      value += m.value;
      if (m.div == null) divUnknown++; else { div += m.div; if (m.divComplete === false) divIncomplete++; }
    }
    const pnl = value - cost;
    return { cost, value, pnl, retPct: cost > 0 ? (pnl / cost) * 100 : null, unpriced,
             div, total: pnl + div, totRetPct: cost > 0 ? ((pnl + div) / cost) * 100 : null, divUnknown, divIncomplete };
  }

  /* ---- live overlay (TWSE MIS via the worker proxy) */
  const LIVE_INDEX = { TWSE: "tse_t00.tw", TPEX: "otc_o00.tw" };

  /* MIS channel list for the given codes (+ the two indices); unknown codes are skipped. */
  function liveChannels(codes, byCode) {
    const out = [LIVE_INDEX.TWSE, LIVE_INDEX.TPEX];
    for (const c of codes) {
      const it = byCode[c];
      if (it) out.push((it.b === "TWSE" ? "tse_" : "otc_") + c + ".tw");
    }
    return [...new Set(out)];
  }

  /* MIS msgArray -> {codes: {code: quote}, twse, otc}. z is the last trade ('-' before the first),
   * y the previous close, v cumulative lots. Mirrors parse_mis in build_web_data.py. */
  function parseLive(msgs) {
    const f = (v) => { const x = parseFloat(String(v == null ? "" : v).replace(/,/g, "")); return Number.isFinite(x) ? x : null; };
    const out = { codes: {}, twse: null, otc: null };
    /* z is the last trade, but MIS often sends '-' mid-session while the stock trades;
     * fall back to pz, best bid, best ask (same order as mis_price in Python) and flag it. */
    const lvl = (s) => f(String(s || "").split("_")[0]);
    for (const m of msgs || []) {
      let last = f(m.z), est = false;
      if (last === null) for (const v of [f(m.pz), lvl(m.b), lvl(m.a)]) if (v !== null && v > 0) { last = v; est = true; break; }
      const prev = f(m.y);
      if (!m.c || last === null) continue;
      const chg = prev ? Math.round((last - prev) * 100) / 100 : null;
      const pct = prev ? Math.round((last / prev - 1) * 10000) / 100 : null;
      const date = /^\d{8}$/.test(m.d || "") ? m.d.slice(0, 4) + "-" + m.d.slice(4, 6) + "-" + m.d.slice(6) : null;
      if (m.c === "t00") out.twse = { close: last, chg, pct, time: m.t || null, at: f(m.tlong), date };
      else if (m.c === "o00") out.otc = { close: last, chg, pct, time: m.t || null, at: f(m.tlong), date };
      else out.codes[m.c] = { live: true, est, last, prev, chg, pct, open: f(m.o), high: f(m.h), low: f(m.l), vol: f(m.v), time: m.t || null, at: f(m.tlong), date };
    }
    return out;
  }

  /* Taiwan cash session plus a few minutes either side: Mon-Fri 08:55-13:45 Taipei
   * (the closing auction ends 13:30 and the final index prints a few minutes later). */
  function liveWindow(nowMs) {
    const t = new Date(nowMs + 8 * 3600000), dow = t.getUTCDay(), mins = t.getUTCHours() * 60 + t.getUTCMinutes();
    return dow >= 1 && dow <= 5 && mins >= 8 * 60 + 55 && mins <= 13 * 60 + 45;
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

  /* ------------------------------------------------------------ upcoming events for the holdings
   * Two kinds. DATED events the exchanges publish (ex-dividend, shareholders' meetings: data/events.json)
   * and RULE events that recur on a fixed day (monthly revenue by the 10th, the statutory filing deadlines). */
  const pad2 = (n) => String(n).padStart(2, "0");

  /* The first date on or after `today` whose day-of-month is `day`. */
  function nextMonthDay(today, day) {
    let [y, m, d] = today.split("-").map(Number);
    if (d > day) { m += 1; if (m > 12) { m = 1; y += 1; } }
    return y + "-" + pad2(m) + "-" + pad2(day);
  }
  const nextRevenueDeadline = (today) => nextMonthDay(today, 10);

  /* Statutory filing deadlines for ordinary companies: annual 3/31, Q1 5/15, Q2 (half-year) 8/14, Q3 11/14. */
  const REPORT_DEADLINES = [[3, 31, "年報"], [5, 15, "第一季財報"], [8, 14, "第二季（半年）財報"], [11, 14, "第三季財報"]];
  function nextReportDeadline(today) {
    const y = Number(today.slice(0, 4));
    for (const yy of [y, y + 1])
      for (const [m, d, label] of REPORT_DEADLINES) {
        const iso = yy + "-" + pad2(m) + "-" + pad2(d);
        if (iso >= today) return { date: iso, label };
      }
    return null;
  }

  /* opts: { lots, kindOf(code) -> "ETF"|"STOCK"|undefined, events: {ex, agm} | null, today, days }.
   * Returns rows sorted by date: { date, days, type, codes, details: {code: text}, text }. */
  function upcomingEvents(opts) {
    const { lots, kindOf, events, today } = opts, horizon = opts.days == null ? 30 : opts.days;
    const rows = [], held = [...new Set(lots.map((l) => l.code))];
    const within = (iso) => { const n = isoDays(today, iso); return n >= 0 && n <= horizon ? n : null; };
    const money0 = (v) => Math.round(v).toLocaleString("en-US");
    if (events) {
      for (const e of events.ex || []) {
        if (!held.includes(e.c)) continue;
        const n = within(e.d); if (n === null) continue;
        const sh = lots.filter((l) => l.code === e.c && l.date < e.d).reduce((a, l) => a + l.shares, 0);   // bought BEFORE the ex-date
        if (sh <= 0) continue;
        const bits = [];
        if (e.cash) bits.push("每股現金 " + e.cash + " 元，持有 " + money0(sh) + " 股預估約 " + money0(e.cash * sh) + " 元（稅前）");
        if (e.stock) bits.push("另有配股");
        rows.push({ date: e.d, days: n, type: e.k, codes: [e.c], details: { [e.c]: bits.join("；") || "詳見公告" }, text: bits.join("；") || "詳見公告" });
      }
      for (const e of events.agm || []) {
        if (!held.includes(e.c)) continue;
        const n = within(e.d); if (n === null) continue;
        const t = e.k + (e.e ? "（電子投票：" + e.e + "）" : "");
        rows.push({ date: e.d, days: n, type: "股東會", codes: [e.c], details: { [e.c]: t }, text: t });
      }
    }
    const stocks = held.filter((c) => kindOf(c) === "STOCK");
    if (stocks.length) {
      const rv = nextRevenueDeadline(today), rn = within(rv);
      if (rn !== null) rows.push({ date: rv, days: rn, type: "月營收", codes: stocks, details: {}, text: "上月營收公告截止（每月 10 日前）" });
      const rp = nextReportDeadline(today), pn = rp && within(rp.date);
      if (rp && pn !== null) rows.push({ date: rp.date, days: pn, type: "財報", codes: stocks, details: {}, text: rp.label + "法定公告截止（一般公司；金融業另有規定）" });
    }
    return rows.sort((a, b) => a.date < b.date ? -1 : a.date > b.date ? 1 : a.type < b.type ? -1 : 1);
  }

  /* ------------------------------------------------------------ the calendar page (all stocks)
   * opts: { events: {ex}, conf: {items: {code: [{d, t, p, m}]}}, today, days, only: [codes] | null }.
   * Rows { date, days, type: "除權息" | "法說會" | "財報", c (null for a rule row), kind, cash, stock, time, text, report }.
   * Exchanges publish ex-dates and conferences per company; filing dates per company are not published
   * in advance, so 財報 is the statutory deadline (one row), and a conference that announces results says so. */
  function calendarRows(opts) {
    const { events, conf, today } = opts, horizon = opts.days == null ? 14 : opts.days;
    const only = opts.only ? new Set(opts.only) : null;
    const within = (iso) => { const n = isoDays(today, iso); return n >= 0 && n <= horizon ? n : null; };
    const rows = [];
    for (const e of (events && events.ex) || []) {
      const n = within(e.d); if (n === null || (only && !only.has(e.c))) continue;
      rows.push({ date: e.d, days: n, type: "除權息", c: e.c, kind: e.k, cash: e.cash || null, stock: !!e.stock });
    }
    for (const [c, list] of Object.entries((conf && conf.items) || {})) {
      if (only && !only.has(c)) continue;
      for (const x of list) {
        const n = within(x.d); if (n === null) continue;
        rows.push({ date: x.d, days: n, type: "法說會", c, time: x.t || null, text: x.m || "", place: x.p || "",
                    report: /財報|財務(報告|業務|資訊)|營運(成果|結果|績效)|季報|業績/.test(x.m || "") });
      }
    }
    const rp = nextReportDeadline(today), pn = rp && within(rp.date);
    if (rp && pn !== null) rows.push({ date: rp.date, days: pn, type: "財報", c: null, text: rp.label + "法定公告截止（一般公司；金融業另有規定）" });
    const rv = nextRevenueDeadline(today), rn = within(rv);
    if (rn !== null) rows.push({ date: rv, days: rn, type: "財報", c: null, text: "上月營收公告截止（每月 10 日前）" });
    const order = { "財報": 0, "除權息": 1, "法說會": 2 };
    return rows.sort((a, b) => a.date < b.date ? -1 : a.date > b.date ? 1 : order[a.type] - order[b.type] || ((a.c || "") < (b.c || "") ? -1 : 1));
  }

  root.Calc = { calendarRows, nextMonthDay, nextRevenueDeadline, nextReportDeadline, upcomingEvents, REPORT_DEADLINES, isoDays, validISO, taipeiToday, priceOnOrBefore, dividendsFor, lotMetrics, totals, ageMinutes, liveChannels, parseLive, liveWindow,
                filterItems, sortItems, parseLots };
})(typeof window !== "undefined" ? window : globalThis);
