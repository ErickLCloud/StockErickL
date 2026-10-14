"""Backtest 籌碼 conditions before they go anywhere near the score.

For every common stock (4-digit code) and every day of docs/data/closes.json, compute
institutional-flow conditions from the daily 三大法人 tables (TWSE T86, TPEx 3itrade).
A condition met on day t is acted on at the close of t+1 (the tables come out after
the close), and its forward return over H days is compared with the same day's
average forward return of all stocks (so the market's move is taken out).

    n        signal days (only the first day of a run, so overlapping days are not counted twice)
    excess   mean forward return minus the cross-section mean, in %
    hit      share of signals that beat the cross-section
    early / late   the same excess on the first and second half of the period (stability)

Daily tables are cached in docs/data/chips/bt/<date>.json ({code: [f, t, d]} in 張), so a
stopped run resumes; the first run takes ~6 s a day (TWSE rate-limits bursts).
Volume comes from docs/data/history/<code>.json (張).

    python scripts/check_chips.py            # fetch what is missing, then report
    python scripts/check_chips.py --no-fetch # report on what is cached
    python scripts/check_chips.py --data <dir> # closes.json + history/ from elsewhere (e.g. a data-branch clone)
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import chips as CH  # noqa: E402

DATA = ROOT / "docs" / "data"
CACHE = DATA / "chips" / "bt"            # always the local cache, whatever --data says
HORIZONS = (5, 20)


def fetch_inst(day, fetch, sleep=3.0):
    """{code: [f, t, d]} for one day, or None when neither market has a table (no trading)."""
    got = []
    for url, parse in ((CH.T86.format(ymd=day.replace("-", "")), CH.parse_t86), (CH.TPEX_3.format(roc=CH.roc(day)), CH.parse_tpex_3)):
        try:
            got.append(parse(fetch(url)))
        except Exception as e:
            print(f"  {day} {type(e).__name__}", flush=True)
            got.append(None)
        if sleep:
            time.sleep(sleep)
    if not any(got):
        return None
    out = {}
    for g in got:
        out.update(g or {})
    return out


def backfill(days, fetch, sleep=3.0):
    CACHE.mkdir(parents=True, exist_ok=True)
    todo = [d for d in days if not (CACHE / f"{d}.json").exists()]
    print(f"cached {len(days) - len(todo)} of {len(days)} days; fetching {len(todo)}", flush=True)
    for i, d in enumerate(todo, 1):
        v = fetch_inst(d, fetch, sleep)
        if v:                                   # a failed day is retried next run
            (CACHE / f"{d}.json").write_text(json.dumps(v, separators=(",", ":")), encoding="utf-8")
        if i % 20 == 0:
            print(f"  {i}/{len(todo)} ({d})", flush=True)


def matrices(days, codes):
    """flows[k] (days x codes, 張, NaN where missing), closes and volumes aligned to the same grid."""
    ix = {c: j for j, c in enumerate(codes)}
    F = {k: np.full((len(days), len(codes)), np.nan) for k in "ftd"}
    for i, d in enumerate(days):
        p = CACHE / f"{d}.json"
        if not p.exists():
            continue
        for c, v in json.loads(p.read_text(encoding="utf-8")).items():
            j = ix.get(c)
            if j is None:
                continue
            for k, x in zip("ftd", v):
                if x is not None:
                    F[k][i, j] = x
    return F


def runs(x):
    """Consecutive days with x > 0 (buy) ending at each day; NaN breaks a run."""
    out = np.zeros_like(x)
    for i in range(len(x)):
        out[i] = np.where(x[i] > 0, (out[i - 1] if i else 0) + 1, 0)
    return out


def rolling_sum(x, n):
    c = np.nancumsum(np.nan_to_num(x), axis=0)
    out = np.full_like(c, np.nan)
    out[n - 1:] = c[n - 1:] - np.vstack([np.zeros((1, c.shape[1])), c[:-n]])
    return out


def first_day(sig):
    """Keep only the first day of each run of True (no double counting of the same episode)."""
    prev = np.vstack([np.zeros((1, sig.shape[1]), bool), sig[:-1]])
    return sig & ~prev


def evaluate(sig, P, h, universe):
    """Signal on day t -> buy at close t+1, sell at close t+1+h; excess over the mean of the same
    day's `universe` (the liquid stocks the signals are drawn from: comparing them with all stocks
    would credit any signal with the large caps' run)."""
    T = len(P)
    fwd = np.full_like(P, np.nan)
    fwd[: T - 1 - h] = P[1 + h:] / P[1: T - h] - 1
    fwd[np.abs(fwd) > 0.11 * h ** 0.5 * 3] = np.nan        # splits / bad prints
    xs = fwd - np.nanmean(np.where(universe, fwd, np.nan), axis=1, keepdims=True)
    hits = xs[sig & ~np.isnan(xs)]
    half = T // 2
    part = lambda a, b: xs[a:b][sig[a:b] & ~np.isnan(xs[a:b])]
    m = lambda a: float(np.mean(a) * 100) if len(a) else float("nan")
    return {"n": int(len(hits)), "excess": m(hits), "median": float(np.median(hits) * 100) if len(hits) else float("nan"),
            "hit": float((hits > 0).mean() * 100) if len(hits) else float("nan"),
            "early": m(part(0, half)), "late": m(part(half, T))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--data", type=Path, default=DATA, help="folder with closes.json and history/")
    a = ap.parse_args(argv)
    cl = json.loads((a.data / "closes.json").read_text(encoding="utf-8"))
    days = cl["d"]
    if not a.no_fetch:
        from src.http import fetch_json
        backfill(days, fetch_json, a.sleep)
    codes = sorted(c for c in cl["items"] if len(c) == 4 and c.isdigit() and not c.startswith("0"))
    P = np.array([[x if x and x > 0 else np.nan for x in cl["items"][c]] for c in codes], float).T
    have = np.array([(CACHE / f"{d}.json").exists() for d in days])
    V = np.full_like(P, np.nan)
    for j, c in enumerate(codes):
        p = a.data / "history" / f"{c}.json"
        if not p.exists():
            continue
        h = json.loads(p.read_text(encoding="utf-8"))
        vv = dict(zip(h["d"], h.get("v") or []))
        V[:, j] = [vv.get(d) if vv.get(d) is not None else np.nan for d in days]
    F = matrices(days, codes)
    F = {k: np.where(have[:, None], v, np.nan) for k, v in F.items()}
    f, t = F["f"], F["t"]
    vol5 = rolling_sum(V, 5)
    f5, t5 = rolling_sum(f, 5), rolling_sum(t, 5)
    fr, tr = runs(f), runs(t)
    frs, trs = runs(-f), runs(-t)
    liquid = rolling_sum(V, 20) / 20 >= 500                 # >= 500 張 a day: thin stocks are noise
    conds = {
        "外資連買 >= 5 日": fr >= 5,
        "外資連賣 >= 5 日": frs >= 5,
        "投信連買 >= 3 日": tr >= 3,
        "投信連賣 >= 3 日": trs >= 3,
        "外資 5 日買超 > 成交量 10%": f5 > 0.10 * vol5,
        "外資 5 日賣超 > 成交量 10%": -f5 > 0.10 * vol5,
        "投信 5 日買超 > 成交量 5%": t5 > 0.05 * vol5,
        "外資＋投信 5 日同步買超": (f5 > 0) & (t5 > 0) & (fr >= 3) & (tr >= 3),
    }
    covered = int(have.sum())
    print(f"\n{len(codes)} stocks, {covered} of {len(days)} days with flows ({days[0]} .. {days[-1]}), liquid = 20-day avg >= 500 張")
    print("excess / median = forward return minus the same day's liquid-stock average (%), first day of each run only\n")
    for h in HORIZONS:
        print(f"--- {h} trading days after (enter at next close) ---")
        print(f"{'condition':<26}{'n':>7}{'excess':>9}{'median':>8}{'hit%':>7}{'early':>8}{'late':>8}")
        for name, s in conds.items():
            r = evaluate(first_day(np.nan_to_num(s, nan=0).astype(bool) & liquid), P, h, liquid)
            print(f"{name:<26}{r['n']:>7}{r['excess']:>9.2f}{r['median']:>8.2f}{r['hit']:>7.1f}{r['early']:>8.2f}{r['late']:>8.2f}")
        print()


if __name__ == "__main__":
    main()
