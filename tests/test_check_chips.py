import numpy as np

from scripts import check_chips as K


def test_runs_count_consecutive_buy_days_and_reset_on_a_sell_or_gap():
    x = np.array([[1], [2], [-1], [3], [np.nan], [4]], float)
    assert K.runs(x)[:, 0].tolist() == [1, 2, 0, 1, 0, 1]


def test_first_day_keeps_one_signal_per_episode():
    s = np.array([[True], [True], [False], [True]])
    assert K.first_day(s)[:, 0].tolist() == [True, False, False, True]


def test_evaluate_enters_the_day_after_the_signal_and_nets_out_the_universe():
    # stock 0 jumps 10% between day 1 and day 2; stock 1 is flat. A signal on day 0 is acted on at
    # the close of day 1 (the flows come out after day 0's close), so a 1-day horizon catches the jump.
    P = np.array([[100, 100], [100, 100], [110, 100], [110, 100]], float)
    sig = np.array([[True, False], [False, False], [False, False], [False, False]])
    r = K.evaluate(sig, P, 1, np.ones_like(sig))
    assert r["n"] == 1 and abs(r["excess"] - 5.0) < 1e-9          # +10% vs the two-stock mean of +5%
    # entering on the signal day's own close would also be +10%; entering a day late misses it
    late = np.array([[False, False], [False, False], [True, False], [False, False]])
    assert K.evaluate(late, P, 1, np.ones_like(sig))["n"] == 0    # no price two days after the last signal
