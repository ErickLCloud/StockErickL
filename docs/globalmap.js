/* Pure logic for the international-to-Taiwan mapping: no DOM, no network, no storage.
 *
 * Rules live in globalmap.json; globals come from data/globals.json. Exposes
 * window.GlobalMap. Tested by tests/web/globalmap_selftest.html.
 */
"use strict";
(function (root) {
  const OPS = [">=", "<="];
  const DIRS = ["up", "down"];
  const TYPES = ["theme", "sector", "code"];

  /* globals may be the parsed globals.json ({items:[...]}), a bare array, or {key: {pct}}. */
  function pctByKey(globals) {
    const m = {};
    if (!globals) return m;
    const items = Array.isArray(globals) ? globals : (Array.isArray(globals.items) ? globals.items : null);
    if (items) {
      for (const it of items) {
        if (it && it.key != null && typeof it.pct === "number" && isFinite(it.pct)) m[it.key] = it.pct;
      }
    } else {
      for (const k of Object.keys(globals)) {
        const v = globals[k];
        if (v && typeof v.pct === "number" && isFinite(v.pct)) m[k] = v.pct;
      }
    }
    return m;
  }

  /* Returns the triggered rules as [{rule, value, targets, dir}].
   * The array also carries .skipped = [{rule, reason}] for rules that could not be
   * evaluated (missing key / malformed), so callers can report them. */
  function evaluate(rules, globals) {
    const pct = pctByKey(globals);
    const out = [];
    out.skipped = [];
    for (const rule of rules || []) {
      const w = rule && rule.when;
      if (!w || typeof w.key !== "string" || OPS.indexOf(w.op) < 0 || typeof w.pct !== "number") {
        out.skipped.push({ rule, reason: "malformed" });
        continue;
      }
      if (!(w.key in pct)) {
        out.skipped.push({ rule, reason: "missing:" + w.key });
        continue;
      }
      const v = pct[w.key];
      const hit = w.op === ">=" ? v >= w.pct : v <= w.pct;
      if (hit) out.push({ rule, value: v, targets: rule.targets || [], dir: rule.dir });
    }
    return out;
  }

  /* targets -> unique stock codes. index: index.json ({items:[{c,s}]} or array),
   * themes: themes.json ({themes:[{name,codes}]} or array). Unknown names yield nothing. */
  function resolveTargets(targets, index, themes) {
    const idx = Array.isArray(index) ? index : ((index && index.items) || []);
    const ths = Array.isArray(themes) ? themes : ((themes && themes.themes) || []);
    const known = new Set(idx.map(x => x.c));
    const seen = new Set(), out = [];
    const add = c => { if (!seen.has(c)) { seen.add(c); out.push(c); } };
    for (const t of targets || []) {
      if (!t) continue;
      if (t.type === "code") {
        if (known.has(t.name)) add(t.name);
      } else if (t.type === "sector") {
        for (const x of idx) if (x.s === t.name) add(x.c);
      } else if (t.type === "theme") {
        const th = ths.find(x => x.name === t.name);
        if (th) for (const c of th.codes || []) if (known.has(c)) add(c);
      }
    }
    return out;
  }

  /* Format check. Returns a list of problem strings; empty means OK. */
  function validate(rules) {
    const p = [];
    if (!Array.isArray(rules)) return ["rules is not an array"];
    const ids = new Set();
    rules.forEach((r, i) => {
      const id = r && r.id ? r.id : "#" + i;
      if (!r || typeof r !== "object") { p.push(id + ": not an object"); return; }
      if (!r.id || typeof r.id !== "string") p.push(id + ": missing id");
      else if (ids.has(r.id)) p.push(id + ": duplicate id");
      else ids.add(r.id);
      const w = r.when;
      if (!w || typeof w !== "object") p.push(id + ": missing when");
      else {
        if (!w.key || typeof w.key !== "string") p.push(id + ": when.key missing");
        if (OPS.indexOf(w.op) < 0) p.push(id + ": when.op must be >= or <=");
        if (typeof w.pct !== "number" || !isFinite(w.pct)) p.push(id + ": when.pct must be a number");
      }
      if (!Array.isArray(r.targets) || !r.targets.length) p.push(id + ": targets empty");
      else r.targets.forEach(t => {
        if (!t || TYPES.indexOf(t.type) < 0 || !t.name || typeof t.name !== "string") p.push(id + ": bad target");
      });
      if (DIRS.indexOf(r.dir) < 0) p.push(id + ": dir must be up or down");
      if (!r.why || typeof r.why !== "string") p.push(id + ": missing why");
      if (!r.status || typeof r.status !== "string") p.push(id + ": missing status");
    });
    return p;
  }

  root.GlobalMap = { evaluate, resolveTargets, validate };
})(typeof window !== "undefined" ? window : globalThis);
