import pandas as pd
import pytest

from src.data import price as P

TWSE = [
    {"Date": "1151005", "Code": "0050", "Name": "元大台灣50", "TradeVolume": "1,000", "TradeValue": "150,000",
     "OpeningPrice": "150.0", "HighestPrice": "152", "LowestPrice": "149", "ClosingPrice": "151.5"},
    {"Date": "1151005", "Code": "2330", "Name": "台積電", "TradeVolume": "5000", "TradeValue": "5000000",
     "OpeningPrice": "1000", "HighestPrice": "1010", "LowestPrice": "990", "ClosingPrice": "1005"},
    {"Date": "1151005", "Code": "2315", "Name": "停牌", "TradeVolume": "0", "TradeValue": "0",
     "OpeningPrice": "", "HighestPrice": "", "LowestPrice": "", "ClosingPrice": "--"},
]
TPEX = [
    {"Date": "1151005", "SecuritiesCompanyCode": "00679B", "CompanyName": "元大美債20年", "TradingShares": "2000",
     "TransactionAmount": "70000", "Open": "35", "High": "35.5", "Low": "34.9", "Close": "35.2"},
    {"Date": "1151005", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "TradingShares": "300",
     "TransactionAmount": "90000", "Open": "300", "High": "305", "Low": "298", "Close": "301"},
]


@pytest.fixture
def conn(tmp_path):
    return P.connect(tmp_path / "t.db")


def test_roc_date():
    assert P.roc_to_iso("1151005") == "2026-10-05"
    assert P.roc_to_iso("1000101") == "2011-01-01"
    assert P.roc_to_iso("") is None
    assert P.roc_to_iso("1151345") is None


def test_clean_num():
    assert P.clean_num("1,234.5") == 1234.5
    assert P.clean_num("") is None
    assert P.clean_num("--") is None
    assert P.clean_num(None) is None


def test_field_mapping_both_boards():
    a = P.normalize_row(TWSE[0], "TWSE")
    assert a == {"code": "0050", "date": "2026-10-05", "board": "TWSE", "open": 150.0, "high": 152.0,
                 "low": 149.0, "close": 151.5, "adj_close": None, "volume": 1000, "turnover": 150000.0,
                 "source": "twse"}
    b = P.normalize_row(TPEX[0], "TPEX")
    assert b["code"] == "00679B" and b["close"] == 35.2 and b["volume"] == 2000
    assert b["turnover"] == 70000.0 and b["source"] == "tpex"
    assert P.normalize_row(TWSE[2], "TWSE") is None


def test_universe(conn):
    n = P.build_universe(conn, TWSE, TPEX, watchlist={"2330"})
    assert n == 3  # 0050, 00679B, 2330
    rows = dict((r[0], r[1:]) for r in conn.execute("SELECT code,board,kind,yf_symbol,in_universe FROM instrument"))
    assert rows["00679B"] == ("TPEX", "ETF", "00679B.TWO", 1)
    assert rows["0050"] == ("TWSE", "ETF", "0050.TW", 1)
    assert rows["6488"] == ("TPEX", "STOCK", "6488.TWO", 0)


def test_watchlist_missing_file(tmp_path):
    assert P.read_watchlist(tmp_path / "nope.csv") == set()


def test_watchlist_read(tmp_path):
    f = tmp_path / "w.csv"
    f.write_text("code\n2330\n 6488 \n", encoding="utf-8")
    assert P.read_watchlist(f) == {"2330", "6488"}


def test_daily_idempotent(conn):
    P.build_universe(conn, TWSE, TPEX, watchlist={"2330"})
    assert P.daily_update(conn, TWSE, TPEX) == 3
    assert P.daily_update(conn, TWSE, TPEX) == 0
    assert conn.execute("SELECT COUNT(*) FROM price_daily WHERE code='6488'").fetchone()[0] == 0


def _fake_download(symbols, period):
    idx = pd.to_datetime(["2026-10-01", "2026-10-02"])
    frames = {}
    for s in symbols:
        if s == "0050.TW":  # silently dropped, even on retry
            continue
        frames[s] = pd.DataFrame({"Open": [1.0, 2.0], "High": [1.5, 2.5], "Low": [0.9, 1.9], "Close": [1.2, 2.2],
                                  "Adj Close": [1.1, 2.1], "Volume": [10, 20]}, index=idx)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1)


def test_backfill_skip_and_idempotent(conn):
    P.build_universe(conn, TWSE, TPEX, watchlist={"2330"})
    logs = []
    n, skipped = P.backfill(conn, download=_fake_download, log=logs.append)
    assert n == 4 and skipped == ["0050"]  # 2330 + 00679B, 2 days each
    n2, _ = P.backfill(conn, download=_fake_download, log=lambda *_: None)
    assert n2 == 0
    r = conn.execute("SELECT source, adj_close, volume, turnover FROM price_daily WHERE code='2330' AND date='2026-10-01'").fetchone()
    assert r == ("yfinance", 1.1, 10, None)


def test_backfill_does_not_overwrite_official_and_daily_keeps_adj(conn):
    P.build_universe(conn, TWSE, TPEX, watchlist={"2330"})
    P.backfill(conn, download=_fake_download, log=lambda *_: None)
    # add an official row on a date yfinance also has
    raw = dict(TWSE[1], Date="1151001")
    assert P.daily_update(conn, [raw], []) == 0  # row exists -> updated, not new
    r = conn.execute("SELECT source, close, adj_close FROM price_daily WHERE code='2330' AND date='2026-10-01'").fetchone()
    assert r == ("twse", 1005.0, 1.1)
