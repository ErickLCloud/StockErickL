"""Technical indicators. Each indicator degrades independently to None when
history is insufficient; nothing raises, nothing is padded with 0."""
import math

import pandas as pd

MA_WINDOWS = (5, 20, 60, 240)


def _r(x):
    """Round for output; NaN/None -> None."""
    if x is None:
        return None
    try:
        if math.isnan(x):
            return None
    except TypeError:
        return None
    return round(float(x), 2)


def kd_series(high, low, close, n=9):
    """Taiwan-style KD: K=2/3*K_prev+1/3*RSV, D likewise, seeds 50.
    Flat window (high==low) -> RSV 50. First n-1 rows are NaN."""
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    rng = hh - ll
    rsv = ((close - ll) / rng * 100).where(rng > 0, 50.0)
    rsv = rsv.where(ll.notna())
    k_vals, d_vals = [], []
    k = d = 50.0
    for v in rsv:
        if pd.isna(v):
            k_vals.append(float("nan"))
            d_vals.append(float("nan"))
            continue
        k = k * 2 / 3 + v / 3
        d = d * 2 / 3 + k / 3
        k_vals.append(k)
        d_vals.append(d)
    return pd.Series(k_vals, index=close.index), pd.Series(d_vals, index=close.index)


def macd_series(close, fast=12, slow=26, signal=9):
    """Returns (dif, signal, hist). EMA adjust=False seeded at first value;
    dif reported from row `slow`, signal from row `slow+signal-1`."""
    pos = pd.Series(range(len(close)), index=close.index)
    dif = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    dif = dif.where(pos >= slow - 1)
    sig = dif.dropna().ewm(span=signal, adjust=False).mean().reindex(close.index)
    return dif, sig, dif - sig


def rsi_series(close, n=14):
    """Wilder RSI. Needs n+1 closes."""
    c = close.to_numpy(dtype=float)
    res = [float("nan")] * len(c)
    if len(c) > n and not pd.isna(c[: n + 1]).any():
        d = c[1:] - c[:-1]
        ag = sum(max(x, 0.0) for x in d[:n]) / n  # SMA seed (classic Wilder)
        al = sum(max(-x, 0.0) for x in d[:n]) / n
        for i in range(n, len(c)):
            if i > n:
                x = d[i - 1]
                if pd.isna(x):
                    break
                ag = (ag * (n - 1) + max(x, 0.0)) / n
                al = (al * (n - 1) + max(-x, 0.0)) / n
            if al == 0:
                res[i] = 50.0 if ag == 0 else 100.0
            else:
                res[i] = 100 - 100 / (1 + ag / al)
    return pd.Series(res, index=close.index)


def compute_indicators(df):
    """df: ascending-by-date DataFrame with high/low/close/volume (+date) columns.
    Returns flat dict of latest values (2dp, None if not computable) plus
    *_prev values (previous bar) used by crossover conditions."""
    keys = (["close", "close_prev", "volume", "vol_ma20", "vol_ratio"]
            + [f"ma{n}" for n in MA_WINDOWS] + ["ma20_prev"]
            + ["k", "d", "k_prev", "d_prev", "dif", "macd", "hist", "rsi"])
    out = {"rows": 0, "date": None}
    out.update({k: None for k in keys})
    if df is None or len(df) == 0:
        return out
    df = df.reset_index(drop=True)
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    vol = pd.to_numeric(df["volume"], errors="coerce")
    out["rows"] = len(df)
    if "date" in df:
        out["date"] = str(df["date"].iloc[-1])

    def last(s, back=0):
        i = len(s) - 1 - back
        return _r(s.iloc[i]) if i >= 0 else None

    out["close"] = last(close)
    out["close_prev"] = last(close, 1)
    out["volume"] = _r(vol.iloc[-1])
    for n in MA_WINDOWS:
        ma = close.rolling(n).mean()
        out[f"ma{n}"] = last(ma)
        if n == 20:
            out["ma20_prev"] = last(ma, 1)
    vma = vol.rolling(20).mean()
    out["vol_ma20"] = last(vma)
    vm, vl = vma.iloc[-1], vol.iloc[-1]
    if not pd.isna(vm) and vm > 0 and not pd.isna(vl):
        out["vol_ratio"] = _r(vl / vm)
    k, d = kd_series(high, low, close)
    out["k"], out["d"] = last(k), last(d)
    out["k_prev"], out["d_prev"] = last(k, 1), last(d, 1)
    dif, sig, hist = macd_series(close)
    out["dif"], out["macd"], out["hist"] = last(dif), last(sig), last(hist)
    out["rsi"] = last(rsi_series(close))
    return out
