"""Backtest the 國際連動 rules in docs/globalmap.json.

For each rule: every US trading day the indicator met the threshold, take the
next Taiwan trading day's close-to-close return of the target group (equal
weight, from docs/data/history) and compare it with TAIEX.

    hit      share of trigger days the group moved in the rule's direction
    vsTWII   mean group return minus TAIEX return on those days
    beatMkt  share of trigger days the group beat TAIEX in the rule's direction
    base     the group's ordinary day, for comparison

Needs docs/data/history (run the history build first) and yfinance.
Close prices only: cannot separate the opening gap from the intraday move.
Rules on keys with no daily history (txf, txf_night) are listed, not tested.

    python scripts/check_globalmap.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TICKERS = {"sox": "^SOX", "ixic": "^IXIC", "spx": "^GSPC", "dji": "^DJI",
           "tsm_adr": "TSM", "nvda": "NVDA", "wti": "CL=F"}
LIMIT = 0.11   # daily moves beyond the 10% limit are splits or bad data


def load(name):
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def target_codes(targets, themes, sectors):
    out = []
    for t in targets:
        if t["type"] == "code":
            out.append(t["name"])
        elif t["type"] == "theme":
            out += themes.get(t["name"], [])
        else:
            out += sectors.get(t["name"], [])
    return list(dict.fromkeys(out))


def daily_pct(ticker, start):
    s = yf.download(ticker, start=start, progress=False, auto_adjust=True)["Close"].squeeze()
    return s.pct_change()


def main():
    rules = load("globalmap.json")["rules"]
    themes = {t["name"]: t["codes"] for t in load("themes.json")["themes"]}
    index = load("data/index.json")
    sectors = {}
    for x in index["items"] if isinstance(index, dict) else index:
        sectors.setdefault(x.get("s"), []).append(x["c"])

    groups = {r["id"]: target_codes(r["targets"], themes, sectors) for r in rules}
    closes = {}
    for c in {c for cs in groups.values() for c in cs}:
        f = DOCS / "data" / "history" / f"{c}.json"
        if f.exists():
            h = json.loads(f.read_text(encoding="utf-8"))
            closes[c] = pd.Series(h["p"], index=pd.to_datetime(h["d"]), dtype=float)
    if not closes:
        sys.exit("no docs/data/history; run the history build first")
    ret = pd.DataFrame(closes).sort_index().pct_change(fill_method=None)
    ret = ret.where(ret.abs() < LIMIT)
    days = ret.index
    start = (days[0] - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    twii = daily_pct("^TWII", start)
    us = {k: daily_pct(t, start) * 100 for k, t in TICKERS.items()}

    def next_tw(d):
        i = days.searchsorted(d, side="right")
        return days[i] if i < len(days) else None

    print(f"period {days[0].date()} .. {days[-1].date()}")
    for r in rules:
        w = r["when"]
        if w["key"] not in us:
            print(f"{r['id']:20s} {r['status']}  not tested: no daily history for {w['key']}")
            continue
        cs = [c for c in groups[r["id"]] if c in ret]
        grp = ret[cs].mean(axis=1)
        s = us[w["key"]].dropna()
        trig = s[s >= w["pct"]] if w["op"] == ">=" else s[s <= w["pct"]]
        hit_days = list(dict.fromkeys(x for x in map(next_tw, trig.index) if x is not None))
        g = grp.reindex(hit_days).dropna()
        ex = (g - twii.reindex(g.index)).dropna()
        sign = 1 if r["dir"] == "up" else -1
        base = grp.dropna()
        print(f"{r['id']:20s} {r['status']}  n={len(g):3d}  grp={g.mean()*100:+.2f}%  "
              f"hit={(np.sign(g) == sign).mean()*100:3.0f}%  vsTWII={ex.mean()*100:+.2f}%  "
              f"beatMkt={(np.sign(ex) == sign).mean()*100:3.0f}%  | base {base.mean()*100:+.2f}% "
              f"up {(base > 0).mean()*100:.0f}%  stocks={len(cs)}")


if __name__ == "__main__":
    main()
