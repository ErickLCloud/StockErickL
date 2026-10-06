"""Screening engine: apply composable conditions over the universe.
A condition maps an indicator dict -> True / False / None (None = cannot
evaluate because an input indicator is missing)."""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from .indicators import compute_indicators

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "db" / "stock.db"


def _need(ind, *keys):
    return all(ind.get(k) is not None for k in keys)


def kd_golden_cross_low(ind, low=30):
    """K crosses above D today (K_prev<=D_prev, K>D) with K_prev below `low`."""
    if not _need(ind, "k", "d", "k_prev", "d_prev"):
        return None
    return ind["k_prev"] <= ind["d_prev"] and ind["k"] > ind["d"] and ind["k_prev"] < low


def above_ma20(ind):
    if not _need(ind, "close", "ma20"):
        return None
    return ind["close"] > ind["ma20"]


def cross_above_ma20(ind):
    """Closed at/below MA20 yesterday, above today."""
    if not _need(ind, "close", "ma20", "close_prev", "ma20_prev"):
        return None
    return ind["close_prev"] <= ind["ma20_prev"] and ind["close"] > ind["ma20"]


def volume_surge(ind, ratio=1.5):
    if not _need(ind, "vol_ratio"):
        return None
    return ind["vol_ratio"] > ratio


def rsi_oversold(ind, level=30):
    if not _need(ind, "rsi"):
        return None
    return ind["rsi"] < level


def macd_hist_positive(ind):
    if not _need(ind, "hist"):
        return None
    return ind["hist"] > 0


def above_ma240(ind):
    if not _need(ind, "close", "ma240"):
        return None
    return ind["close"] > ind["ma240"]


CONDITIONS = {
    "kd_golden_cross_low": kd_golden_cross_low,
    "above_ma20": above_ma20,
    "cross_above_ma20": cross_above_ma20,
    "volume_surge": volume_surge,
    "rsi_oversold": rsi_oversold,
    "macd_hist_positive": macd_hist_positive,
    "above_ma240": above_ma240,
}


def evaluate(ind, names, mode="all"):
    """Returns (passed, matched, unknown). 'all': every condition True.
    'any': at least one True. Unknown (None) never counts as matched."""
    results = {n: CONDITIONS[n](ind) for n in names}
    matched = [n for n, v in results.items() if v is True]
    unknown = [n for n, v in results.items() if v is None]
    passed = len(matched) == len(names) if mode == "all" else bool(matched)
    return passed, matched, unknown


def screen_frames(frames, names, mode="all", names_map=None):
    """frames: {code: DataFrame}. A failing code is reported in `errors`
    instead of failing the batch. Returns dict(candidates, scanned, errors)."""
    if mode not in ("all", "any"):
        raise ValueError("mode must be 'all' or 'any'")
    bad = [n for n in names if n not in CONDITIONS]
    if bad:
        raise ValueError(f"unknown conditions: {bad}")
    cands, errors = [], {}
    for code, df in frames.items():
        try:
            ind = compute_indicators(df)
            passed, matched, unknown = evaluate(ind, names, mode)
        except Exception as e:
            errors[code] = repr(e)
            continue
        if passed:
            cands.append({
                "code": code,
                "name": (names_map or {}).get(code),
                "matched": matched,
                "unknown": unknown,
                "missing_indicators": sorted(k for k, v in ind.items() if v is None),
                "indicators": ind,
            })
    return {"candidates": cands, "scanned": len(frames), "errors": errors}


def load_frames(conn=None, db_path=DB_PATH):
    """Read-only load of price history for in_universe=1 instruments."""
    own = conn is None
    if own:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    try:
        inst = conn.execute(
            "select code, name from instrument where in_universe=1 order by code").fetchall()
        px = pd.read_sql_query(
            "select p.code, p.date, p.open, p.high, p.low, p.close, p.volume "
            "from price_daily p join instrument i on i.code=p.code "
            "where i.in_universe=1 order by p.code, p.date", conn)
    finally:
        if own:
            conn.close()
    grouped = {c: g.drop(columns="code").reset_index(drop=True) for c, g in px.groupby("code")}
    empty = px.iloc[0:0].drop(columns="code")
    return {code: grouped.get(code, empty) for code, _ in inst}, dict(inst)


def screen(names, mode="all", conn=None, db_path=DB_PATH):
    """Screen the whole universe from the DB (read-only)."""
    frames, nm = load_frames(conn, db_path)
    return screen_frames(frames, names, mode, nm)


if __name__ == "__main__":
    import time
    conds = sys.argv[1:] or ["above_ma20", "volume_surge"]
    t = time.perf_counter()
    r = screen(conds)
    print(f"{len(r['candidates'])} candidates / {r['scanned']} scanned, "
          f"{len(r['errors'])} errors, {time.perf_counter() - t:.2f}s")
