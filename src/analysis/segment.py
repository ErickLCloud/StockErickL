"""Cut a price history at its last discontinuity.

Why this exists: unadjusted splits / reverse splits / capital reductions leave
a single-day jump in the price series, and every moving average or oscillator
computed across that jump is wrong. R3 found five such symbols in the 357-ETF
universe (00673R is exactly 1:4.00), and a full-market universe has more.

Why we do NOT back-adjust: Yahoo's `Adj Close` is not adjusted at these gaps
(measured: adj/close stays x1.000 across all five), so the split cannot be
recovered from the data and any ratio we invented would silently corrupt the
series. Instead we keep only the data after the last gap. Indicators computed
on that segment are correct by construction; the cost is a shorter history,
which the indicators already handle by degrading to None.

Why the thresholds are safe: Taiwan stocks and ETFs have a +/-10% daily price
limit. An ex-dividend day can move the raw close a little further than that
(the reference price drops by the dividend), but never anywhere near -30% or
+45%. So a move outside [LOW, HIGH] cannot be ordinary trading.
"""

LOW = 0.70    # close / previous close below this: drop of more than 30%
HIGH = 1.45   # above this: rise of more than 45%

__all__ = ["LOW", "HIGH", "find_gaps", "last_regime"]


def find_gaps(closes, low=LOW, high=HIGH):
    """Indices i where closes[i] / closes[i-1] falls outside [low, high].

    None / non-positive values are skipped, not treated as a gap, so a
    suspended day does not hide a real gap on either side of it.
    """
    gaps, prev = [], None
    for i, c in enumerate(closes):
        if c is None or c != c or c <= 0:
            continue
        if prev is not None:
            r = c / prev
            if r < low or r > high:
                gaps.append(i)
        prev = c
    return gaps


def last_regime(dates, closes, low=LOW, high=HIGH):
    """Return (start_index, info) for the segment after the last gap.

    start_index is 0 and info is None when there is no gap. Otherwise info is
    {"date": first date of the new regime, "ratio": previous/new close}, so
    ratio ~ 4.0 means the price fell to a quarter (a 1:4 split) and ~ 0.25
    means it rose fourfold (a reverse split).
    """
    gaps = find_gaps(closes, low, high)
    if not gaps:
        return 0, None
    i = gaps[-1]
    before = next((closes[j] for j in range(i - 1, -1, -1)
                   if closes[j] is not None and closes[j] == closes[j] and closes[j] > 0), None)
    ratio = round(before / closes[i], 3) if before else None
    return i, {"date": str(dates[i]), "ratio": ratio}
