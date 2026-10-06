"""Tests for cutting a history at its last discontinuity.

The "real" cases use closes measured from price_daily on 2026-10-06, so the
thresholds are pinned to data we actually saw rather than to a guess.
"""

from src.analysis.segment import HIGH, LOW, find_gaps, last_regime


def test_no_gap_returns_whole_series():
    closes = [100, 101, 99.5, 100.2]
    assert find_gaps(closes) == []
    assert last_regime(list("abcd"), closes) == (0, None)


# ---- real measured gaps -------------------------------------------------

def test_00673R_exact_one_to_four():
    # 28.08 -> 7.02 on 2025-10-15: exactly 4.00, a textbook reverse-split signature
    closes = [27.92, 28.08, 7.02, 7.02, 7.02]
    dates = ["10-13", "10-14", "10-15", "10-16", "10-17"]
    start, info = last_regime(dates, closes)
    assert start == 2
    assert info == {"date": "10-15", "ratio": 4.0}


def test_0052_roughly_one_to_seven():
    closes = [254.1, 248.5, 35.571, 35.043]
    start, info = last_regime(["a", "b", "c", "d"], closes)
    assert start == 2
    assert 6.9 < info["ratio"] < 7.1


def test_00663L_non_integer_ratio_still_detected():
    # 175.4 -> 23.979 (7.31x): not a clean integer, so we must not rely on one
    start, info = last_regime(["a", "b", "c"], [180.0, 175.4, 23.979])
    assert start == 2 and 7.2 < info["ratio"] < 7.4


# ---- ordinary trading must never be flagged -----------------------------

def test_daily_limit_moves_are_not_gaps():
    up = [100, 110.0]          # +10% limit-up
    down = [100, 90.0]         # -10% limit-down
    assert find_gaps(up) == []
    assert find_gaps(down) == []


def test_ex_dividend_day_drop_is_not_a_gap():
    # Reference price falls by the dividend, then the limit applies on top:
    # worst realistic raw move is about -14%, far above the -30% cut.
    assert find_gaps([100, 85.5]) == []


def test_thresholds_are_strict_at_the_boundary():
    assert find_gaps([100, 100 * LOW]) == []           # exactly -30%: not a gap
    assert find_gaps([100, 100 * LOW - 0.01]) == [1]   # just past it: gap
    assert find_gaps([100, 100 * HIGH]) == []
    assert find_gaps([100, 100 * HIGH + 0.01]) == [1]


# ---- structure ----------------------------------------------------------

def test_only_the_last_gap_matters():
    closes = [38.83, 20.0, 23.0, 22.15, 10.86, 9.13]   # 00887's two jumps
    start, info = last_regime(list("abcdef"), closes)
    assert find_gaps(closes) == [1, 4]
    assert start == 4 and info["date"] == "e"


def test_missing_values_do_not_hide_or_fake_a_gap():
    # A suspended (None) day between 100 and 24 must not stop the 100 -> 24
    # drop from being seen, and a None alone must not create one.
    assert find_gaps([100, None, 24.0]) == [2]
    assert find_gaps([100, None, 101.0]) == []
    assert find_gaps([None, 100, float("nan"), 101]) == []


def test_ratio_uses_last_valid_close_before_the_gap():
    start, info = last_regime(["a", "b", "c"], [80.0, None, 20.0])
    assert start == 2 and info["ratio"] == 4.0


def test_segment_slicing_gives_only_the_new_regime():
    closes = [100, 101, 25, 25.5, 26]
    start, _ = last_regime(list("abcde"), closes)
    assert closes[start:] == [25, 25.5, 26]
