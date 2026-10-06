/* Pure analysis for the dashboard: scan, daily/weekly technicals, backtest.
 *
 * No DOM, no network, no storage (same contract as calc.js), so it can be
 * tested headlessly (tests/web/analysis_selftest.html). Exposes window.Analysis.
 *
 * Data limits that shape every number here:
 *  - history is CLOSE ONLY (no high / low / volume), ~2 years of days;
 *  - so support / resistance, stops and "hits" are judged on closes, and an
 *    intraday touch of a stop or target is invisible;
 *  - rule-based, not a forecast: nothing here knows news or earnings.
 */
"use strict";
(function (root) {
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const fin = (v) => typeof v === "number" && Number.isFinite(v);

  /* ------------------------------------------------------------ series */
  function sma(a, n) {
    const out = new Array(a.length).fill(null);
    let s = 0;
    for (let i = 0; i < a.length; i++) {
      s += a[i];
      if (i >= n) s -= a[i - n];
      if (i >= n - 1) out[i] = s / n;
    }
    return out;
  }

  function ema(a, n) {
    const k = 2 / (n + 1), out = new Array(a.length).fill(null);
    if (!a.length) return out;
    out[0] = a[0];
    for (let i = 1; i < a.length; i++) out[i] = a[i] * k + out[i - 1] * (1 - k);
    return out;
  }

  /* Wilder RSI. */
  function rsi(a, n) {
    n = n || 14;
    const out = new Array(a.length).fill(null);
    if (a.length <= n) return out;
    let g = 0, l = 0;
    for (let i = 1; i <= n; i++) { const d = a[i] - a[i - 1]; if (d > 0) g += d; else l -= d; }
    g /= n; l /= n;
    out[n] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
    for (let i = n + 1; i < a.length; i++) {
      const d = a[i] - a[i - 1];
      g = (g * (n - 1) + (d > 0 ? d : 0)) / n;
      l = (l * (n - 1) + (d < 0 ? -d : 0)) / n;
      out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
    }
    return out;
  }

  function macd(a) {
    const e12 = ema(a, 12), e26 = ema(a, 26);
    const dif = a.map((_, i) => e12[i] - e26[i]);
    const dea = ema(dif, 9);
    return { dif, dea, hist: dif.map((v, i) => v - dea[i]) };
  }

  function stdev(a) {
    if (a.length < 2) return 0;
    const m = a.reduce((s, v) => s + v, 0) / a.length;
    return Math.sqrt(a.reduce((s, v) => s + (v - m) * (v - m), 0) / (a.length - 1));
  }

  /* Daily volatility of the last n returns (fraction, e.g. 0.018). */
  function vol(p, n, end) {
    end = end == null ? p.length - 1 : end;
    const r = [];
    for (let i = Math.max(1, end - n + 1); i <= end; i++) if (p[i - 1] > 0) r.push(p[i] / p[i - 1] - 1);
    return stdev(r);
  }

  /* Cut a history at a detected gap (split / data break) and drop bad closes. */
  function clean(h, gap) {
    if (!h || !Array.isArray(h.d) || !Array.isArray(h.p)) return null;
    const d = [], p = [];
    for (let i = 0; i < h.d.length; i++) {
      if (gap && gap.date && h.d[i] < gap.date) continue;
      if (!(h.p[i] > 0)) continue;
      d.push(h.d[i]); p.push(Number(h.p[i]));
    }
    return { d, p };
  }

  /* Daily -> weekly closes (week starts Monday; last close of each week). */
  function weekly(h) {
    const d = [], p = [];
    let key = null;
    for (let i = 0; i < h.d.length; i++) {
      const [y, m, dd] = h.d[i].split("-").map(Number);
      const t = Date.UTC(y, m - 1, dd);
      const dow = (new Date(t).getUTCDay() + 6) % 7;          // Mon = 0
      const k = t - dow * 86400000;
      if (k !== key) { key = k; d.push(h.d[i]); p.push(h.p[i]); }
      else { d[d.length - 1] = h.d[i]; p[p.length - 1] = h.p[i]; }
    }
    return { d, p };
  }

  /* ------------------------------------------------- structure: S/R, trend */
  function pivots(a, w, type) {
    const out = [];
    for (let i = w; i < a.length - w; i++) {
      let ok = true;
      for (let j = i - w; j <= i + w && ok; j++) {
        if (j === i) continue;
        if (type === "high" ? a[j] > a[i] : a[j] < a[i]) ok = false;
      }
      if (ok) out.push(i);
    }
    return out;
  }

  /* Price clusters from pivot highs and lows of the last 250 closes.
   * Returns {supports (nearest first), resistances (nearest first)}. */
  function levels(p) {
    const start = Math.max(0, p.length - 250), seg = p.slice(start), last = p[p.length - 1];
    const pts = pivots(seg, 5, "high").concat(pivots(seg, 5, "low")).map((i) => seg[i]).sort((a, b) => a - b);
    const clusters = [];
    for (const v of pts) {
      const c = clusters[clusters.length - 1];
      if (c && v <= (c.sum / c.n) * 1.015) { c.sum += v; c.n++; } else clusters.push({ sum: v, n: 1 });
    }
    const lv = clusters.map((c) => ({ price: c.sum / c.n, n: c.n }));
    const supports = lv.filter((x) => x.price < last * 0.995).sort((a, b) => b.price - a.price).slice(0, 3);
    const resistances = lv.filter((x) => x.price > last * 1.005).sort((a, b) => a.price - b.price).slice(0, 3);
    return { supports, resistances };
  }

  /* Linear-regression channel over the last n closes plus pivot trend lines. */
  function trend(p, n) {
    n = Math.min(n || 60, p.length);
    const seg = p.slice(-n), x0 = p.length - n;
    const mx = (n - 1) / 2, my = seg.reduce((s, v) => s + v, 0) / n;
    let sxy = 0, sxx = 0, syy = 0;
    seg.forEach((v, i) => { sxy += (i - mx) * (v - my); sxx += (i - mx) * (i - mx); syy += (v - my) * (v - my); });
    const slope = sxx ? sxy / sxx : 0, icpt = my - slope * mx;
    const res = seg.map((v, i) => v - (icpt + slope * i)), sigma = stdev(res);
    const r2 = syy ? (sxy * sxy) / (sxx * syy) : 0;
    const per20 = my ? (slope * 20) / my * 100 : 0;
    const dir = per20 > 2 ? "up" : per20 < -2 ? "down" : "flat";
    const line = (a, b) => ({ x1: a, y1: p[a], x2: b, y2: p[b],
      now: p[b] + ((p[b] - p[a]) / (b - a)) * (p.length - 1 - b) });
    const lows = pivots(p.slice(-120), 5, "low").map((i) => i + Math.max(0, p.length - 120));
    const highs = pivots(p.slice(-120), 5, "high").map((i) => i + Math.max(0, p.length - 120));
    const supLine = lows.length >= 2 && p[lows[lows.length - 1]] > p[lows[lows.length - 2]]
      ? line(lows[lows.length - 2], lows[lows.length - 1]) : null;
    const resLine = highs.length >= 2 && p[highs[highs.length - 1]] < p[highs[highs.length - 2]]
      ? line(highs[highs.length - 2], highs[highs.length - 1]) : null;
    return { x0, slope, icpt, sigma, r2, per20, dir,
      fitNow: icpt + slope * (n - 1), upperNow: icpt + slope * (n - 1) + 2 * sigma,
      lowerNow: icpt + slope * (n - 1) - 2 * sigma, supLine, resLine };
  }

  /* ------------------------------------------------- daily + weekly analysis */
  const f2 = (v) => Number(v).toFixed(2);

  function analyze(h) {
    const p = h.p, n = p.length, last = p[n - 1];
    if (n < 30) return null;
    const ma5 = sma(p, 5), ma20 = sma(p, 20), ma60 = sma(p, 60);
    const r = rsi(p, 14), m = macd(p);
    const lv = levels(p), tr = trend(p, 60);
    const steps = [];
    const add = (tf, name, score, text) => steps.push({ tf, name, score, text });

    /* weekly: the bigger picture */
    const w = weekly(h), wp = w.p, wl = wp[wp.length - 1];
    const wma5 = sma(wp, 5), wma10 = sma(wp, 10), wma20 = sma(wp, 20);
    const wr = rsi(wp, 14), wm = macd(wp), wn = wp.length - 1;
    if (wma20[wn] != null)
      add("週", "週線位置", wl > wma20[wn] ? 1 : -1,
        "週收 " + f2(wl) + (wl > wma20[wn] ? " 高於" : " 低於") + " 20 週線 " + f2(wma20[wn]) +
        (wl > wma20[wn] ? "，中期趨勢偏多" : "，中期趨勢偏空"));
    if (wma10[wn] != null)
      add("週", "週均線排列", wma5[wn] > wma10[wn] ? 1 : -1,
        "5 週線 " + f2(wma5[wn]) + (wma5[wn] > wma10[wn] ? " 在" : " 在") + (wma5[wn] > wma10[wn] ? "上方" : "下方") +
        " 10 週線 " + f2(wma10[wn]));
    if (wr[wn] != null) {
      const v = wr[wn];
      add("週", "週 RSI", v > 70 ? -1 : v >= 50 ? 1 : v < 40 ? -1 : 0,
        "週 RSI " + f2(v) + (v > 70 ? "，週線過熱，追高風險升高" : v >= 50 ? "，動能偏多" : v < 40 ? "，動能弱" : "，動能中性"));
    }
    if (wn >= 34)
      add("週", "週 MACD", wm.hist[wn] > 0 ? 1 : -1,
        "週 MACD 柱 " + f2(wm.hist[wn]) + (wm.hist[wn] > 0 ? "，多方動能" : "，空方動能"));

    /* daily: timing */
    if (ma60[n - 1] != null)
      add("日", "日線位置", last > ma60[n - 1] ? 1 : -1,
        "收盤 " + f2(last) + (last > ma60[n - 1] ? " 高於" : " 低於") + " 60 日線 " + f2(ma60[n - 1]));
    if (ma60[n - 1] != null) {
      const bull = ma5[n - 1] > ma20[n - 1] && ma20[n - 1] > ma60[n - 1];
      const bear = ma5[n - 1] < ma20[n - 1] && ma20[n - 1] < ma60[n - 1];
      add("日", "均線排列", bull ? 1 : bear ? -1 : 0,
        "5 / 20 / 60 日線 " + f2(ma5[n - 1]) + " / " + f2(ma20[n - 1]) + " / " + f2(ma60[n - 1]) +
        (bull ? "，多頭排列" : bear ? "，空頭排列" : "，均線糾結、方向未明"));
    }
    {
      const hn = m.hist[n - 1], hp = m.hist[n - 2];
      add("日", "MACD 動能", hn > 0 && hn > hp ? 1 : hn < 0 && hn < hp ? -1 : 0,
        "MACD 柱 " + f2(hn) + (hn > hp ? "（較前日擴張）" : "（較前日收斂）") +
        (hn > 0 && hn > hp ? "，多方動能增強" : hn < 0 && hn < hp ? "，空方動能增強" : "，動能轉弱或換向中"));
    }
    if (r[n - 1] != null) {
      const v = r[n - 1];
      add("日", "RSI", v > 75 ? -1 : v >= 55 ? 1 : v < 30 ? 0 : v < 45 ? -1 : 0,
        "RSI14 " + f2(v) + (v > 75 ? "，過熱" : v >= 55 ? "，動能偏多" : v < 30 ? "，超賣，可能反彈但趨勢仍弱" :
          v < 45 ? "，動能偏弱" : "，中性"));
    }
    add("日", "趨勢線", tr.dir === "up" ? 1 : tr.dir === "down" ? -1 : 0,
      "近 60 日回歸斜率約 " + (tr.per20 > 0 ? "+" : "") + f2(tr.per20) + "% / 20 日（R² " + f2(tr.r2) + "）" +
      (tr.dir === "up" ? "，上升通道" : tr.dir === "down" ? "，下降通道" : "，橫向整理") +
      (last > tr.upperNow ? "；已突破通道上緣，短線過度延伸" : last < tr.lowerNow ? "；已跌破通道下緣" : ""));
    {
      const rs = lv.resistances[0], sp = lv.supports[0];
      const nearR = rs && rs.price / last - 1 < 0.02, nearS = sp && last / sp.price - 1 < 0.02;
      add("日", "支撐壓力", nearR ? -1 : nearS ? 1 : 0,
        (rs ? "上方壓力 " + f2(rs.price) + "（+" + f2((rs.price / last - 1) * 100) + "%）" : "上方無明顯壓力（接近區間高點）") +
        "；" + (sp ? "下方支撐 " + f2(sp.price) + "（-" + f2((1 - sp.price / last) * 100) + "%）" : "下方無明顯支撐") +
        (nearR ? "，離壓力太近，上檔空間有限" : nearS ? "，貼近支撐，防守點明確" : ""));
    }

    const wScore = steps.filter((s) => s.tf === "週").reduce((a, s) => a + s.score, 0);
    const dScore = steps.filter((s) => s.tf === "日").reduce((a, s) => a + s.score, 0);
    const total = wScore + dScore;
    const verdict = total >= 4 && wScore >= 0 ? "買進" : total <= -4 ? "賣出" : "持有";
    const why = verdict === "買進" ? "週線與日線同向偏多（合計 " + total + " 分，需 ≥4 且週線不為負）" :
      verdict === "賣出" ? "多數指標偏空（合計 " + total + " 分，≤ -4 視為賣出）" :
      total >= 4 ? "日線偏多但週線為負（" + wScore + "），逆大方向不追，持有觀望" :
      "訊號互有抵銷（合計 " + total + " 分，介於 -3 ~ +3），持有或觀望";
    return { steps, wScore, dScore, total, verdict, why, levels: lv, trend: tr, last,
      ma5, ma20, ma60, rsi: r, macd: m, weekly: w, wma5, wma10, wma20 };
  }

  /* ------------------------------------------------------------ trade plan */
  const COST_RT = (kind) => 0.002851 + (kind === "ETF" ? 0.001 : 0.003);  // 2 x 0.1425% fee + sell tax

  /* One shared definition of risk, so the live plan and its historical test agree:
   * stop = 2.5 x 20-day volatility below entry, clamped to 3%..8%; target = 2R. */
  function riskPct(p, end) { return clamp(2.5 * vol(p, 20, end), 0.03, 0.08); }

  /* Trend pullback setup: uptrend, price near the 20-day line, momentum not stretched. */
  function setupAt(a, i) {
    const { ma20, ma60, rsi: r, macd: m } = a;
    if (i < 61 || ma60[i] == null || ma20[i - 5] == null || r[i] == null) return false;
    const c = a.p[i];
    return c > ma20[i] && ma20[i] > ma60[i] && ma20[i] > ma20[i - 5] &&
      c <= ma20[i] * 1.06 && r[i] >= 40 && r[i] <= 68 && m.hist[i] > 0;
  }

  /* How this same setup played out earlier on THIS stock: enter at that day's
   * close, exit at stop / 2R target / 40 bars. Close-only, costs included. */
  function setupStats(p, kind) {
    const a = { p, ma20: sma(p, 20), ma60: sma(p, 60), rsi: rsi(p, 14), macd: macd(p) };
    let n = 0, wins = 0, sum = 0, i = 61;
    const cost = COST_RT(kind);
    while (i < p.length - 1) {
      if (!setupAt(a, i)) { i++; continue; }
      const risk = riskPct(p, i), stop = p[i] * (1 - risk), tgt = p[i] * (1 + 2 * risk);
      let j = i + 1, out = null;
      for (; j < p.length && j <= i + 40; j++) {
        if (p[j] <= stop || p[j] >= tgt) { out = p[j]; break; }
      }
      if (out == null) { if (j >= p.length) break; out = p[Math.min(j, p.length - 1)]; }  // timed out
      const ret = out / p[i] - 1 - cost;
      n++; sum += ret; if (ret > 0) wins++;
      i = j + 1;
    }
    return { n, wins, winRate: n ? wins / n : null, avgRet: n ? sum / n : null,
      /* shrunk toward 50% so 2 wins out of 2 does not rank as a sure thing */
      shrunk: (wins + 2) / (n + 4) };
  }

  /* Cheap first pass over indicators.json for every symbol in scope. */
  function candidates(index, o) {
    const terms = (o.term || "").toLowerCase().split(/[\s,，]+/).filter(Boolean);
    const out = [];
    for (const it of index) {
      if (o.scope === "ETF" && it.k !== "ETF") continue;
      if (o.scope === "STOCK" && it.k === "ETF") continue;
      if (o.scope === "TWSE" && it.b !== "TWSE") continue;
      if (o.scope === "TPEx" && it.b === "TWSE") continue;
      if (terms.length && !terms.some((t) => it.c.toLowerCase().startsWith(t) || (it.n || "").toLowerCase().includes(t))) continue;
      const d = (o.ind && o.ind[it.c]) || null, q = (o.quotes && o.quotes[it.c]) || {};
      if (!d || d.gap || d.ma60 == null || d.ma20 == null || !(d.close > 0)) continue;
      const last = q.last > 0 ? q.last : d.close;
      if (!(last > d.ma20 && d.ma20 > d.ma60)) continue;
      if (last > d.ma20 * 1.06) continue;
      if (d.rsi == null || d.rsi < 40 || d.rsi > 68) continue;
      if (d.hist != null && d.hist <= 0) continue;
      if ((q.vol || 0) < (o.minVol == null ? 500 : o.minVol)) continue;      // lots; thin names are untradeable
      const pull = 1 - clamp((last / d.ma20 - 1) / 0.06, 0, 1);               // closer to MA20 = better entry
      out.push({ it, pre: pull * 2 + clamp(d.vol_ratio || 1, 0, 2) + (d.ma5 > d.ma20 ? 1 : 0) });
    }
    return out.sort((a, b) => b.pre - a.pre);
  }

  function fundText(it, f) {
    if (it.k === "ETF") return { text: "ETF：無本益比等基本面資料（TWSE 未提供）", bonus: 0 };
    if (!f) return { text: "無基本面資料", bonus: -0.5 };
    const [pe, dy, pb] = f;
    const bits = [];
    let bonus = 0;
    if (pe == null || !(pe > 0)) { bits.push("本益比 —（虧損或無資料）"); bonus -= 1; }
    else { bits.push("本益比 " + f2(pe) + (pe <= 15 ? "（偏低）" : pe > 40 ? "（偏高）" : "")); bonus += pe <= 25 ? 1 : pe > 40 ? -1 : 0; }
    if (dy != null) { bits.push("殖利率 " + f2(dy) + "%"); if (dy >= 3) bonus += 1; }
    if (pb != null) bits.push("股價淨值比 " + f2(pb));
    return { text: bits.join("、"), bonus };
  }

  /* Full evaluation of one candidate against its own history. Null = reject. */
  function evaluate(it, h, q, d, f) {
    const c = clean(h, d && d.gap);
    if (!c || c.p.length < 100) return null;
    const p = c.p.slice();
    const an = analyze({ d: c.d, p });
    if (!an || an.verdict === "賣出") return null;
    const a = { p, ma20: an.ma20, ma60: an.ma60, rsi: an.rsi, macd: an.macd };
    if (!setupAt(a, p.length - 1)) return null;
    const st = setupStats(p, it.k);
    const entry = q && q.last > 0 ? q.last : p[p.length - 1], risk = riskPct(p);
    const stop = entry * (1 - risk), target = entry * (1 + 2 * risk);
    const res = an.levels.resistances[0], sup = an.levels.supports[0];
    const room = res ? res.price / entry - 1 : null;
    const blocked = room != null && room < risk * 1.2;             // resistance in the way of 1.2R
    const fu = fundText(it, f);
    const reasons = an.steps.filter((s) => s.score > 0).map((s) => "[" + s.tf + "] " + s.text);
    reasons.unshift("型態：多頭回檔——收盤在 20 日線上方 " + f2((entry / an.ma20[p.length - 1] - 1) * 100) +
      "%，20 日線在 60 日線上方且上彎，RSI " + f2(an.rsi[p.length - 1]) + "，MACD 柱為正");
    const score = an.total + (st.shrunk - 0.5) * 10 + fu.bonus - (blocked ? 2 : 0);
    const pull = Math.max(entry * 0.97, Math.min(entry, an.ma20[p.length - 1] * 1.01));
    return { code: it.c, name: it.n, kind: it.k, entry, pullback: entry - pull > entry * 0.01 ? pull : null,
      stop, target, risk, rr: 2, support: sup ? sup.price : null, resist: res ? res.price : null, blocked,
      stats: st, fund: fu.text, reasons, verdict: an.verdict, total: an.total, score };
  }

  /* ---------------------------------------------------------------- backtest */
  const STRATS = {
    ma_cross: { label: "均線交叉", a: { k: "快線", v: 10 }, b: { k: "慢線", v: 30 } },
    rsi_rev: { label: "RSI 超賣反彈", a: { k: "買進 RSI <", v: 30 }, b: { k: "賣出 RSI >", v: 55 } },
    breakout: { label: "N 日高點突破", a: { k: "突破 N 日", v: 20 }, b: { k: "跌破 M 日低", v: 10 } },
    rsi_div: { label: "RSI 底背離", a: null, b: null },
  };

  function makeSignals(kind, p, A, B, ma60Filter) {
    const m60 = ma60Filter ? sma(p, 60) : null;
    const ok = (i) => !m60 || (m60[i] != null && p[i] > m60[i]);
    let entry, exit;
    if (kind === "ma_cross") {
      const f = sma(p, A), s = sma(p, B);
      const up = (i) => i > 0 && f[i] != null && s[i] != null && f[i - 1] != null && s[i - 1] != null && f[i] > s[i] && f[i - 1] <= s[i - 1];
      const dn = (i) => i > 0 && f[i] != null && s[i] != null && f[i - 1] != null && s[i - 1] != null && f[i] < s[i] && f[i - 1] >= s[i - 1];
      entry = (i) => up(i) && ok(i); exit = (i) => dn(i);
    } else if (kind === "rsi_rev") {
      const r = rsi(p, 14);
      entry = (i) => r[i] != null && r[i] < A && ok(i); exit = (i) => r[i] != null && r[i] > B;
    } else if (kind === "breakout") {
      const hi = (i, n) => { let m = -Infinity; for (let j = i - n; j < i; j++) m = Math.max(m, p[j]); return m; };
      const lo = (i, n) => { let m = Infinity; for (let j = i - n; j < i; j++) m = Math.min(m, p[j]); return m; };
      entry = (i) => i > A && p[i] > hi(i, A) && ok(i); exit = (i) => i > B && p[i] < lo(i, B);
    } else {                                                      // rsi_div: bullish divergence on confirmed pivots
      const r = rsi(p, 14), W = 3, lows = pivots(p, W, "low");
      const at = new Map();
      for (let k = 1; k < lows.length; k++) {
        const a = lows[k - 1], b = lows[k];
        if (r[a] == null || r[b] == null) continue;
        if (b - a >= 5 && b - a <= 60 && p[b] < p[a] && r[b] > r[a] && r[b] < 45) at.set(b + W, true);
      }
      entry = (i) => at.has(i) && ok(i);
      exit = (i, pos) => (r[i] != null && r[i] > 60) || (pos && i - pos.i >= 30);
    }
    return { entry, exit };
  }

  /* Long-only, one position, all-in. A signal on the close of day i fills at the
   * close of day i+1 (no look-ahead). Costs: 2 x 0.1425% fee + sell tax. A stop
   * is also evaluated on closes and fills the next close, so a gap through it
   * costs real money here as it does live. An open trade is closed at the last
   * close and flagged. */
  function backtest(h, spec) {
    const p = h.p, n = p.length;
    const bars = spec.bars && spec.bars > 0 ? Math.min(spec.bars, n) : n;
    const start = n - bars;
    const sg = makeSignals(spec.kind, p, spec.a, spec.b, spec.ma60);
    const fee = spec.cost === false ? 0 : 0.001425;
    const tax = spec.cost === false ? 0 : (spec.etf ? 0.001 : 0.003);
    let cash = 1, sh = 0, pos = null, pend = null, peak = 1, mdd = 0, held = 0;
    const trades = [];
    const close = (i, open) => {
      const proceeds = sh * p[i] * (1 - fee - tax), cost = sh * pos.entryPx;
      trades.push({ d0: h.d[pos.i], d1: h.d[i], in: pos.px, out: p[i], bars: i - pos.i,
        ret: proceeds / cost - 1, open: !!open });
      cash = proceeds; sh = 0; pos = null;
    };
    for (let i = start; i < n; i++) {
      if (pend === "buy" && !pos) {
        const ex = p[i] * (1 + fee);
        sh = cash / ex; cash = 0; pos = { i, px: p[i], entryPx: ex }; pend = null;
      } else if (pend === "sell" && pos) { close(i, false); pend = null; }
      const eq = cash + sh * p[i];
      peak = Math.max(peak, eq); mdd = Math.max(mdd, 1 - eq / peak);
      if (pos) held++;
      if (i === n - 1) break;
      if (!pos && !pend && sg.entry(i)) pend = "buy";
      else if (pos && !pend && (sg.exit(i, pos) || (spec.stopPct > 0 && p[i] <= pos.px * (1 - spec.stopPct / 100)))) pend = "sell";
    }
    if (pos) close(n - 1, true);
    const wins = trades.filter((t) => t.ret > 0), losses = trades.filter((t) => t.ret <= 0);
    const gw = wins.reduce((s, t) => s + t.ret, 0), gl = -losses.reduce((s, t) => s + t.ret, 0);
    const final = cash + sh * p[n - 1];
    return {
      n: trades.length, trades, bars, from: h.d[start], to: h.d[n - 1],
      winRate: trades.length ? wins.length / trades.length : null,
      pf: gl > 0 ? gw / gl : (gw > 0 ? Infinity : null),
      avgWin: wins.length ? gw / wins.length : null,
      avgLoss: losses.length ? -gl / losses.length : null,
      total: final - 1, bh: p[n - 1] / p[start] - 1, mdd,
      exposure: bars ? held / bars : 0, openTrade: trades.some((t) => t.open),
    };
  }

  /* Run the base spec, then measured variants and a small parameter sweep, and
   * turn the numbers into plain suggestions. Variants are MEASURED on the same
   * data, not generic advice; the sweep is in-sample, so it is labelled as such. */
  function backtestReport(h, spec) {
    const base = backtest(h, spec);
    const vs = [];
    if (!spec.ma60) vs.push({ label: "加 60 日線趨勢濾網（只在收盤高於 60 日線時進場）", r: backtest(h, Object.assign({}, spec, { ma60: true })) });
    if (!(spec.stopPct > 0)) vs.push({ label: "加 8% 停損", r: backtest(h, Object.assign({}, spec, { stopPct: 8 })) });
    const sweep = [];
    const grid = spec.kind === "ma_cross" ? [[5, 10, 20], [20, 30, 60, 120]] :
      spec.kind === "rsi_rev" ? [[20, 25, 30, 35], [50, 55, 60, 70]] :
      spec.kind === "breakout" ? [[10, 20, 55], [5, 10, 20]] : null;
    if (grid) {
      for (const a of grid[0]) for (const b of grid[1]) {
        if (spec.kind === "ma_cross" && a >= b) continue;
        const r = backtest(h, Object.assign({}, spec, { a, b }));
        if (r.n >= 5 && r.pf != null) sweep.push({ a, b, r });
      }
      sweep.sort((x, y) => (y.r.pf === Infinity ? 1e9 : y.r.pf) - (x.r.pf === Infinity ? 1e9 : x.r.pf));
    }
    const tips = [];
    if (base.n < 10) tips.push("交易只有 " + base.n + " 次，樣本太少，勝率與獲利因子不可靠；請拉長期間或換較常觸發的參數。");
    if (base.winRate != null && base.winRate < 0.4 && base.avgWin != null && base.avgLoss != null &&
        Math.abs(base.avgLoss) > 0 && base.avgWin / Math.abs(base.avgLoss) < 1.5)
      tips.push("勝率低、賺賠比也不高：這個策略靠量不靠質，先考慮加趨勢濾網或縮短停損。");
    if (base.pf != null && base.pf < 1) tips.push("獲利因子 < 1：扣除成本後整體是虧的，目前參數不建議實盤。");
    if (base.total < base.bh) tips.push("報酬低於同期買進持有（" + pct(base.total) + " vs " + pct(base.bh) + "）：這段期間主動交易沒有勝過不動。");
    if (base.mdd > 0.2) tips.push("最大回撤 " + pct(base.mdd) + " 偏大：考慮停損或降低單次部位。");
    for (const v of vs) {
      const b = v.r;
      if (b.n >= 3 && base.pf != null && b.pf != null && b.pf > base.pf)
        tips.push(v.label + "：獲利因子 " + fpf(base.pf) + " → " + fpf(b.pf) + "，最大回撤 " + pct(base.mdd) + " → " + pct(b.mdd) + "（交易 " + b.n + " 次）。");
    }
    return { base, variants: vs, sweep: sweep.slice(0, 3), tips };
  }

  const pct = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
  const fpf = (v) => v == null ? "—" : v === Infinity ? "∞" : v.toFixed(2);

  root.Analysis = { sma, ema, rsi, macd, vol, clean, weekly, pivots, levels, trend, analyze, riskPct,
    setupAt, setupStats, candidates, evaluate, backtest, backtestReport, STRATS, COST_RT };
})(typeof window !== "undefined" ? window : globalThis);
