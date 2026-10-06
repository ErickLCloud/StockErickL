import sqlite3

import pytest

from src import holdings as H


def test_manual_calc():
    # cost 100, close 125, 1000 shares, 365 days -> pnl 25000, 25%, annualized 25%
    r = H.compute_return({"code": "X", "buy_date": "2025-10-06", "shares": 1000, "cost_price": 100.0},
                         125.0, "2026-10-06")
    assert r["pnl"] == pytest.approx(25000)
    assert r["return_pct"] == pytest.approx(25.0)
    assert r["days"] == 365
    assert r["annualized_pct"] == pytest.approx(25.0)


def test_annualized_two_years():
    # 100 -> 121 over 730 days: 1.21 ** (365/730) - 1 = 10%
    r = H.compute_return({"code": "X", "buy_date": "2024-10-06", "shares": 1, "cost_price": 100.0},
                         121.0, "2026-10-06")
    assert r["annualized_pct"] == pytest.approx(10.0, abs=0.05)


def test_short_hold_no_annualized():
    r = H.compute_return({"code": "X", "buy_date": "2026-09-20", "shares": 1, "cost_price": 10.0},
                         11.0, "2026-10-06")
    assert r["annualized_pct"] is None and r["return_pct"] == pytest.approx(10.0)


def test_missing_file(tmp_path):
    assert H.load_holdings(tmp_path / "nope.csv") is None


def test_load_and_report(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("code,buy_date,shares,cost_price,note\n0050,2025-10-06,1000,100,hi\n"
                 "bad,,x,y,\n9999,2025-10-06,1,1,\n", encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE price_daily (code, date, close)")
    conn.execute("INSERT INTO price_daily VALUES ('0050','2026-10-05',110),('0050','2026-10-06',125)")
    rows = H.holdings_report(conn, p)
    assert len(rows) == 2
    assert rows[0]["last_close"] == 125 and rows[0]["pnl"] == pytest.approx(25000)
    assert rows[1]["last_close"] is None
