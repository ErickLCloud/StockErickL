"""W4 tests: fixed fixtures, no network, no DB content dependency."""
import sqlite3

import pandas as pd
import pytest

from src.analysis.indicators import compute_indicators, kd_series, macd_series, rsi_series
from src.analysis import screener as sc


def frame(close, high=None, low=None, volume=None):
    n = len(close)
    return pd.DataFrame({
        "date": [f"2026-01-{i + 1:03d}" for i in range(n)],
        "open": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume if volume is not None else [1000] * n,
    })


def test_ma_hand_values():
    ind = compute_indicators(frame([float(i) for i in range(1, 26)]))
    assert ind["ma5"] == 23.0          # (21..25)/5
    assert ind["ma20"] == 15.5         # (6..25)/20
    assert ind["ma20_prev"] == 14.5    # (5..24)/20
    assert ind["ma60"] is None and ind["ma240"] is None


def test_kd_hand_values():
    # 8 flat bars then close at top of range (RSV=100), then close at bottom (RSV=0)
    close = [5.0] * 8 + [10.0, 0.0]
    s = pd.Series(close)
    k, d = kd_series(pd.Series([10.0] * 10), pd.Series([0.0] * 10), s)
    assert k.iloc[:8].isna().all()
    assert k.iloc[8] == pytest.approx(200 / 3)              # 50*2/3 + 100/3
    assert d.iloc[8] == pytest.approx(50 * 2 / 3 + (200 / 3) / 3)  # 55.555..
    assert k.iloc[9] == pytest.approx(400 / 9)              # 44.44
    assert d.iloc[9] == pytest.approx(d.iloc[8] * 2 / 3 + k.iloc[9] / 3)


def test_kd_flat_window_no_crash():
    s = pd.Series([5.0] * 12)
    k, d = kd_series(s, s, s)
    assert k.iloc[-1] == pytest.approx(50.0) and d.iloc[-1] == pytest.approx(50.0)


def test_rsi_hand_values():
    assert rsi_series(pd.Series([float(i) for i in range(20)])).iloc[-1] == 100.0
    assert rsi_series(pd.Series([7.0] * 20)).iloc[-1] == 50.0
    # 14 changes: 7 x +2, 7 x -1 => avg gain 1, avg loss 0.5 => RS 2 => RSI 66.67
    c = [100.0]
    for i in range(14):
        c.append(c[-1] + (2 if i % 2 == 0 else -1))
    assert rsi_series(pd.Series(c)).iloc[-1] == pytest.approx(100 - 100 / 3)
    # one more +2 step: ag=(1*13+2)/14, al=0.5*13/14
    c.append(c[-1] + 2)
    ag, al = 15 / 14, 6.5 / 14
    assert rsi_series(pd.Series(c)).iloc[-1] == pytest.approx(100 - 100 / (1 + ag / al))
    assert rsi_series(pd.Series(c[:14])).isna().all()  # needs 15 closes


def _ema(vals, span):
    a, e, out = 2 / (span + 1), None, []
    for v in vals:
        e = v if e is None else a * v + (1 - a) * e
        out.append(e)
    return out


def test_macd_matches_independent_ema():
    close = [10 + (i % 7) * 0.5 + i * 0.1 for i in range(60)]
    dif, sig, hist = macd_series(pd.Series(close))
    e12, e26 = _ema(close, 12), _ema(close, 26)
    exp_dif = [a - b for a, b in zip(e12, e26)]
    exp_sig = _ema(exp_dif[25:], 9)
    assert dif.iloc[:25].isna().all() and dif.iloc[25] == pytest.approx(exp_dif[25])
    assert dif.iloc[-1] == pytest.approx(exp_dif[-1])
    assert sig.iloc[-1] == pytest.approx(exp_sig[-1])
    assert hist.iloc[-1] == pytest.approx(exp_dif[-1] - exp_sig[-1])


def test_macd_constant_is_zero():
    ind = compute_indicators(frame([50.0] * 40))
    assert ind["dif"] == 0 and ind["macd"] == 0 and ind["hist"] == 0


def test_volume_ratio():
    vol = [100] * 19 + [400]
    ind = compute_indicators(frame([1.0] * 20, volume=vol))
    assert ind["vol_ma20"] == 115.0          # (1900+400)/20
    assert ind["vol_ratio"] == round(400 / 115, 2) == 3.48


def test_float32_output_rounded():
    ind = compute_indicators(frame([116.44999694824219] * 5))
    assert ind["close"] == 116.45 and ind["ma5"] == 116.45


def test_insufficient_history_degrades_per_indicator():
    ind = compute_indicators(frame([float(i) for i in range(1, 11)]))  # 10 rows
    assert ind["ma5"] == 8.0 and ind["k"] is not None
    for k in ("ma20", "ma60", "ma240", "dif", "macd", "rsi", "vol_ratio"):
        assert ind[k] is None
    empty = compute_indicators(frame([]))
    assert empty["rows"] == 0 and empty["ma5"] is None
    one = compute_indicators(frame([3.0]))
    assert one["close"] == 3.0 and one["close_prev"] is None


def test_conditions_true_false_none():
    ind = compute_indicators(frame([float(i) for i in range(1, 26)], volume=[100] * 24 + [300]))
    assert sc.above_ma20(ind) is True
    assert sc.volume_surge(ind) is True          # 300 / 110 = 2.73
    assert sc.above_ma240(ind) is None           # not enough history
    assert sc.cross_above_ma20(ind) is False     # was already above


def test_kd_golden_cross_low():
    ind = {"k": 25.0, "d": 24.0, "k_prev": 20.0, "d_prev": 22.0}
    assert sc.kd_golden_cross_low(ind) is True
    assert sc.kd_golden_cross_low({**ind, "k_prev": 45.0, "d_prev": 46.0, "k": 47.0, "d": 46.5}) is False
    assert sc.kd_golden_cross_low({**ind, "k": None}) is None


def test_screen_frames_composition_and_isolation():
    up = frame([float(i) for i in range(1, 26)], volume=[100] * 24 + [300])
    flat = frame([5.0] * 25)
    short = frame([1.0, 2.0])
    bad = pd.DataFrame({"foo": [1]})  # missing columns -> error isolated
    frames = {"UP": up, "FLAT": flat, "SHORT": short, "BAD": bad}
    r = sc.screen_frames(frames, ["above_ma20", "volume_surge"], "all")
    assert [c["code"] for c in r["candidates"]] == ["UP"]
    assert r["scanned"] == 4 and "BAD" in r["errors"]
    r = sc.screen_frames(frames, ["above_ma20", "above_ma240"], "any")
    up_c = r["candidates"][0]
    assert up_c["matched"] == ["above_ma20"] and up_c["unknown"] == ["above_ma240"]
    assert "ma240" in up_c["missing_indicators"]
    with pytest.raises(ValueError):
        sc.screen_frames(frames, ["nope"])


def test_load_frames_inmemory_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "create table instrument(code, name, in_universe);"
        "create table price_daily(code, date, open, high, low, close, volume);"
        "insert into instrument values('A','Alpha',1),('B','Beta',1),('C','Out',0);"
        "insert into price_daily values('A','2026-01-02',1,2,0.5,1.5,10),"
        "('A','2026-01-01',1,2,0.5,1.0,10),('C','2026-01-01',1,1,1,1,1);")
    frames, names = sc.load_frames(conn)
    assert list(frames) == ["A", "B"] and names["A"] == "Alpha"
    assert list(frames["A"]["date"]) == ["2026-01-01", "2026-01-02"]
    assert len(frames["B"]) == 0
    r = sc.screen_frames(frames, ["above_ma20"])
    assert r["errors"] == {} and r["candidates"] == []
