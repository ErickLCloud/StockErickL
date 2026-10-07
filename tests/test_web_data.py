"""Tests for scripts/build_web_data.py. Fixtures only; no network."""

import json
from datetime import datetime

import pandas as pd
import pytest

from scripts import build_web_data as bw

# --------------------------------------------------------------------- num

@pytest.mark.parametrize("raw,want", [
    ("1,234.50", 1234.5), ("+10.0000", 10.0), ("-0.55", -0.55), ("0.0000", 0.0),
    ("", None), ("--", None), ("---", None), ("X", None), (None, None),
    ("abc", None), ("nan", None),
])
def test_num(raw, want):
    assert bw.num(raw) == want


def test_num_keeps_zero_distinct_from_missing():
    assert bw.num("0.00") == 0.0 and bw.num("0.00") is not None
    assert bw.num("") is None


def test_kind_of():
    assert bw.kind_of("0050") == "ETF" and bw.kind_of("00679B") == "ETF"
    assert bw.kind_of("2330") == "STOCK" and bw.kind_of("6488") == "STOCK"


# ---------------------------------------------------------------- universe

TWSE = [
    {"Code": "2330", "Name": "台積電", "ClosingPrice": "2,585.00", "Change": "+10.0000"},
    {"Code": "0050", "Name": "元大台灣50", "ClosingPrice": "116.45", "Change": "0.0000"},
    {"Code": "9999", "Name": "停牌股", "ClosingPrice": "--", "Change": ""},
]
TPEX = [
    {"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "1205.00", "Change": "+25.00"},
    {"SecuritiesCompanyCode": "2330", "CompanyName": "重複", "Close": "1", "Change": "0"},
    {"SecuritiesCompanyCode": "00679B", "CompanyName": "元大美債20年", "Close": "24.32", "Change": "-0.17"},
]


def test_universe_merges_both_boards_sorted_and_deduplicated():
    uni = bw.parse_universe(TWSE, TPEX)
    assert [i["c"] for i in uni] == ["0050", "00679B", "2330", "6488", "9999"]
    by = {i["c"]: i for i in uni}
    assert by["2330"]["b"] == "TWSE" and by["2330"]["n"] == "台積電"   # TWSE row wins the duplicate
    assert by["6488"]["b"] == "TPEX"
    assert by["00679B"]["k"] == "ETF" and by["6488"]["k"] == "STOCK"


def test_universe_parses_numbers_and_keeps_missing_as_none():
    by = {i["c"]: i for i in bw.parse_universe(TWSE, TPEX)}
    assert by["2330"]["pc"] == 2585.0 and by["2330"]["ch"] == 10.0
    assert by["0050"]["ch"] == 0.0                       # an unchanged price is 0, not missing
    assert by["9999"]["pc"] is None and by["9999"]["ch"] is None


def test_universe_skips_rows_without_a_code():
    assert bw.parse_universe([{"Code": "", "Name": "x"}, {"Name": "y"}], []) == []


# -------------------------------------------------------------------- MIS

def test_parse_mis_computes_change_from_last_and_prev_close():
    q = bw.parse_mis([{"c": "2330", "z": "2585.0000", "y": "2575.0000", "o": "2575.0000",
                       "h": "2590.0000", "l": "2565.0000", "v": "18343", "t": "13:30:00"}])["2330"]
    assert q["last"] == 2585.0 and q["chg"] == 10.0 and q["pct"] == 0.39
    assert q["vol"] == 18343 and q["time"] == "13:30:00"


def test_parse_mis_no_trade_yet_has_no_price_or_change():
    q = bw.parse_mis([{"c": "2330", "z": "-", "y": "2575.0000"}])["2330"]
    assert q["last"] is None and q["chg"] is None and q["pct"] is None
    assert q["prev"] == 2575.0


def test_parse_mis_ignores_messages_without_a_code():
    assert bw.parse_mis([{"z": "1"}, {"c": ""}]) == {}


# --------------------------------------------------------------- quote_item

def test_quote_item_live():
    live = {"last": 116.5, "prev": 115.95, "chg": 0.55, "pct": 0.47, "open": 116.3,
            "high": 116.6, "low": 115.7, "vol": 65962, "time": "13:30:00"}
    q = bw.quote_item(live, {"pc": 116.45, "ch": 0.5})
    assert q["live"] is True and q["last"] == 116.5 and q["chg"] == 0.55


def test_quote_item_falls_back_to_last_official_close():
    q = bw.quote_item(None, {"pc": 105.0, "ch": 5.0})
    assert q["live"] is False and q["last"] == 105.0 and q["chg"] == 5.0
    assert q["pct"] == 5.0                                 # 5 / (105 - 5) * 100


def test_quote_item_zero_change_is_kept_not_dropped():
    q = bw.quote_item({"last": 10.0, "prev": 10.0, "chg": 0.0, "pct": 0.0}, {})
    assert q["chg"] == 0.0 and q["pct"] == 0.0


def test_quote_item_with_nothing_is_just_not_live():
    assert bw.quote_item(None, {"pc": None, "ch": None}) == {"live": False}


# ------------------------------------------------------------ fundamentals

def test_fundamentals_missing_values_are_none_never_zero():
    f = bw.parse_fundamentals(
        [{"Code": "1101", "PEratio": "", "DividendYield": "3.20", "PBratio": "0.81"}],
        [{"SecuritiesCompanyCode": "6488", "PriceEarningRatio": "57.28",
          "YieldRatio": "0.65", "PriceBookRatio": "5.83"}])
    assert f["1101"] == [None, 3.2, 0.81]
    assert f["6488"] == [57.28, 0.65, 5.83]


def test_fundamentals_twse_wins_a_duplicate_code():
    f = bw.parse_fundamentals([{"Code": "1", "PEratio": "10", "DividendYield": "1", "PBratio": "1"}],
                              [{"SecuritiesCompanyCode": "1", "PriceEarningRatio": "99"}])
    assert f["1"][0] == 10.0


# ---------------------------------------------------------- web_indicators

def test_web_indicators_handles_the_non_numeric_fields_that_once_crashed_the_build():
    raw = {"rows": 487, "date": "2026-10-06", "k": 89.21, "d": float("nan"),
           "vol_ratio": 0.924, "extra_dropped": 5}
    w = bw.web_indicators(raw)
    assert w["date"] == "2026-10-06" and w["rows"] == 487
    assert w["k"] == 89.21 and w["vol_ratio"] == 0.92
    assert w["d"] is None                               # NaN becomes null
    assert "extra_dropped" not in w
    json.dumps(w)                                       # must be serialisable


def test_web_indicators_emit_only_what_needs_more_than_closing_prices():
    """MA / RSI / MACD have ONE implementation (docs/analysis.js), run by the
    page on the same closes. The server must not publish a second copy of them:
    that is exactly how the scan's first pass and its second pass came to apply
    different rules."""
    raw = {"close": 116.4, "ma5": 114.0, "ma20": 110.0, "ma60": 100.0, "ma240": 86.0,
           "rsi": 73.6, "hist": 0.5, "dif": 2.0, "macd": 1.5, "k": 89.2, "d": 82.4,
           "vol_ratio": 0.92, "vol_ma20": 72_208_886.0, "rows": 487, "date": "2026-10-06"}
    w = bw.web_indicators(raw)
    for banned in ("close", "ma5", "ma20", "ma60", "ma240", "rsi", "hist", "dif", "macd"):
        assert banned not in w, banned
    assert set(w) == {"k", "d", "vol_ratio", "avg_lots", "rows", "date"}


# ---------------------------------------------------------- process_symbol

def _frame(closes, start="2025-01-01", divs=None):
    """divs: {row_index: cash dividend per share on that (ex-)date}."""
    dates = pd.bdate_range(start, periods=len(closes)).strftime("%Y-%m-%d")
    c = pd.Series(closes, dtype=float)
    div = pd.Series(0.0, index=c.index)
    for i, amt in (divs or {}).items():
        div.iloc[i] = amt
    return pd.DataFrame({"date": list(dates), "open": c, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": 1000.0, "div": div})


def test_process_symbol_without_a_gap_keeps_everything():
    ind, hist, gap = bw.process_symbol(_frame([100 + (i % 5) for i in range(300)]))
    assert gap is None and ind["rows"] == 300
    assert len(hist["p"]) == len(hist["d"]) == min(300, bw.HISTORY_DAYS)


def test_avg_lots_converts_shares_to_lots_and_survives_missing_data():
    """The screener's liquidity rule needs 20-day average volume in LOTS."""
    assert bw.web_indicators({"vol_ma20": 72208886.65, "rows": 487, "date": "d"})["avg_lots"] == 72209
    assert bw.web_indicators({"vol_ma20": 499_400.0})["avg_lots"] == 499     # just under 500 lots
    assert bw.web_indicators({"vol_ma20": 500_000.0})["avg_lots"] == 500
    assert bw.web_indicators({"vol_ma20": None})["avg_lots"] is None
    assert bw.web_indicators({"vol_ma20": float("nan")})["avg_lots"] is None
    assert bw.web_indicators({})["avg_lots"] is None
    assert bw.web_indicators({"vol_ma20": "abc"})["avg_lots"] is None
    json.dumps(bw.web_indicators({"vol_ma20": 1e6}))


def test_process_symbol_carries_average_volume_through():
    ind, _hist, _gap = bw.process_symbol(_frame([100.0] * 300))       # volume is 1000 shares/day
    assert ind["avg_lots"] == 1


def test_history_window_covers_two_years_so_older_buys_can_be_priced():
    assert bw.HISTORY_DAYS >= 480                       # ~2 trading years
    ind, hist, gap = bw.process_symbol(_frame([100.0] * 600))
    assert len(hist["p"]) == bw.HISTORY_DAYS


def test_process_symbol_cuts_at_a_reverse_split_and_says_so():
    closes = [100.0] * 200 + [25.0] * 100                # a 1:4 split at row 200
    ind, hist, gap = bw.process_symbol(_frame(closes))
    assert gap is not None and gap["ratio"] == 4.0
    assert ind["rows"] == 100                            # only the new regime
    assert max(hist["p"]) == 25.0                        # nothing from before the gap leaks in


# --------------------------------------------------------------- dividends

def test_dividends_are_kept_with_their_ex_dates():
    ind, hist, gap = bw.process_symbol(_frame([100.0] * 300, divs={50: 4.5, 200: 5.0}))
    dates = _frame([100.0] * 300)["date"]
    assert hist["div"] == [[dates[50], 4.5], [dates[200], 5.0]]


def test_a_symbol_that_pays_nothing_has_no_div_key():
    _ind, hist, _gap = bw.process_symbol(_frame([100.0] * 300))
    assert "div" not in hist


def test_dividends_before_a_price_gap_are_dropped_with_the_prices():
    """Before the gap the share units differ, so those dividends cannot be added
    to a holding measured in today's units; they go with the old prices."""
    closes = [100.0] * 200 + [25.0] * 100
    _ind, hist, gap = bw.process_symbol(_frame(closes, divs={50: 9.9, 250: 1.0}))
    assert gap is not None
    assert [x[1] for x in hist["div"]] == [1.0]


def test_dividends_outside_the_history_window_are_dropped():
    n = bw.HISTORY_DAYS + 100
    _ind, hist, _gap = bw.process_symbol(_frame([100.0] * n, divs={10: 7.0, n - 5: 2.0}))
    assert [x[1] for x in hist["div"]] == [2.0]          # row 10 is older than the window


def test_fractional_and_float_noise_dividends_survive_serialisation():
    _ind, hist, _gap = bw.process_symbol(_frame([100.0] * 300, divs={60: 3.9996, 120: 0.675}))
    json.dumps(hist)
    assert [x[1] for x in hist["div"]] == [3.9996, 0.675]


# ------------------------------------------------------------- closes.json

def test_closes_share_one_date_axis_and_use_null_for_missing_days():
    hs = {"A": {"d": ["2026-01-02", "2026-01-05", "2026-01-06"], "p": [10.0, 11.0, 12.0]},
          "B": {"d": ["2026-01-05", "2026-01-06"], "p": [20.0, 21.0]},        # listed later
          "C": {"d": ["2026-01-02", "2026-01-06"], "p": [30.0, 31.0]}}        # suspended on the 5th
    c = bw.build_closes(hs)
    assert c["d"] == ["2026-01-02", "2026-01-05", "2026-01-06"]
    assert c["items"]["A"] == [10.0, 11.0, 12.0]
    assert c["items"]["B"] == [None, 20.0, 21.0]
    assert c["items"]["C"] == [30.0, None, 31.0]
    json.dumps(c)


def test_every_row_has_exactly_one_value_per_date():
    hs = {str(i): {"d": [f"2026-01-{d:02d}" for d in range(2 + i % 3, 12)], "p": [1.0 + i] * (10 - i % 3)}
          for i in range(30)}
    c = bw.build_closes(hs)
    assert all(len(row) == len(c["d"]) for row in c["items"].values())


def test_process_symbol_empty_input_is_none():
    assert bw.process_symbol(None) is None
    assert bw.process_symbol(_frame([]).iloc[0:0]) is None


# --------------------------------------------------- build_quotes end to end

def _fake_fetch(url):
    if url == bw.TWSE_DAY:
        return TWSE
    if url == bw.TPEX_DAY:
        return TPEX
    if url == bw.TWSE_PE:
        return [{"Code": "2330", "PEratio": "29.85", "DividendYield": "0.85", "PBratio": "10.38"}]
    if url == bw.TPEX_PE:
        return []
    if url == bw.TWSE_INDEX:
        return [{"日期": "1151005", "指數": "發行量加權股價報酬指數", "收盤指數": "114,952.55",
                 "漲跌": "+", "漲跌點數": "2,858.78", "漲跌百分比": "2.55"},
                {"日期": "1151005", "指數": "發行量加權股價指數", "收盤指數": "49,712.04",
                 "漲跌": "+", "漲跌點數": "1,236.30", "漲跌百分比": "2.55"}]
    if url.startswith(bw.MIS_URL):
        return {"rtcode": "0000", "msgArray": [
            {"c": "2330", "z": "2585.0000", "y": "2575.0000", "v": "18343", "t": "13:30:00"}]}
    raise AssertionError("unexpected url " + url)


def test_build_quotes_writes_every_file_from_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr(bw, "fetch_json", _fake_fetch)
    monkeypatch.setattr(bw.time, "sleep", lambda *_: None)
    summary = bw.build_quotes(tmp_path)

    assert summary["symbols"] == 5 and summary["live"] == 1 and summary["failed_batches"] == 0
    idx = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["items"]
    assert [i["c"] for i in idx][:2] == ["0050", "00679B"]
    assert set(idx[0]) == {"c", "n", "b", "k"}           # no volatile fields in the static index
    q = json.loads((tmp_path / "quotes.json").read_text(encoding="utf-8"))
    assert q["items"]["2330"]["live"] is True and q["items"]["2330"]["chg"] == 10.0
    assert q["items"]["0050"]["live"] is False and q["items"]["0050"]["last"] == 116.45
    fund = json.loads((tmp_path / "fundamental.json").read_text(encoding="utf-8"))["items"]
    assert fund["2330"] == [29.85, 0.85, 10.38] and "0050" not in fund
    mk = json.loads((tmp_path / "market.json").read_text(encoding="utf-8"))
    assert mk["close"] == 49712.04 and mk["chg"] == 1236.3   # not the 114,952 total-return decoy
    assert mk["updated"].endswith("+08:00")                  # Taipei time, not the runner's zone


def test_build_quotes_survives_a_dead_pe_endpoint_and_dead_index(tmp_path, monkeypatch):
    def flaky(url):
        if url in (bw.TWSE_PE, bw.TWSE_INDEX):
            raise RuntimeError("down")
        return _fake_fetch(url)

    monkeypatch.setattr(bw, "fetch_json", flaky)
    monkeypatch.setattr(bw.time, "sleep", lambda *_: None)
    s = bw.build_quotes(tmp_path)
    assert s["symbols"] == 5 and s["fundamentals"] == 0 and s["market_ok"] is False
    assert "error" in json.loads((tmp_path / "market.json").read_text(encoding="utf-8"))


def test_failed_mis_batch_is_retried_then_reported_not_fatal(monkeypatch):
    calls = []

    def always_fails(url):
        calls.append(url)
        raise RuntimeError("blocked")

    monkeypatch.setattr(bw.time, "sleep", lambda *_: None)
    out, failed = bw.fetch_intraday([{"c": "2330", "b": "TWSE"}], fetch=always_fails)
    assert out == {} and failed == ["batch 0"] and len(calls) == 2   # one retry


def test_stamp_is_taipei_time():
    assert bw.stamp().endswith("+08:00")


# ------------------------------------------------- OHLCV for the candle chart

def test_history_carries_open_high_low_and_volume_in_lots_aligned_with_the_dates():
    df = _frame([100 + (i % 5) for i in range(300)])
    df["volume"] = 250_000.0                                       # shares -> 250 lots
    _ind, hist, _gap = bw.process_symbol(df)
    n = len(hist["d"])
    assert all(len(hist[k]) == n for k in ("p", "o", "h", "l", "v"))
    assert set(hist["v"]) == {250}
    assert all(l <= o <= h and l <= c <= h for o, h, l, c in zip(hist["o"], hist["h"], hist["l"], hist["p"]))


def test_missing_open_or_volume_becomes_null_not_nan():
    df = _frame([100.0] * 300)
    df.loc[299, "open"] = float("nan"); df.loc[299, "volume"] = float("nan")
    _ind, hist, _gap = bw.process_symbol(df)
    assert hist["o"][-1] is None and hist["v"][-1] is None
    json.loads(json.dumps(hist, allow_nan=False))                 # strict JSON: NaN would raise


def test_the_gap_cut_applies_to_every_series():
    _ind, hist, gap = bw.process_symbol(_frame([100.0] * 200 + [25.0] * 100))
    assert gap is not None
    assert len(hist["o"]) == len(hist["v"]) == len(hist["d"]) == 100
    assert max(hist["h"]) < 30


# ------------------------------------------------------------- industries

def test_parse_industries_reads_both_exchanges_and_skips_unnamed_codes():
    twse = [{"公司代號": "2330", "產業別": "24"}, {"公司代號": "1101", "產業別": "01"}, {"公司代號": "9999", "產業別": "91"}]
    tpex = [{"SecuritiesCompanyCode": "8299", "SecuritiesIndustryCode": "24"}, {"SecuritiesCompanyCode": "6488", "SecuritiesIndustryCode": "zz"}]
    got = bw.parse_industries(twse, tpex)
    assert got == {"2330": "半導體業", "1101": "水泥工業", "8299": "半導體業"}


def test_parse_industries_survives_junk_and_empty_input():
    assert bw.parse_industries(None, []) == {}
    assert bw.parse_industries([{"公司代號": None, "產業別": None}, {}], [{}]) == {}


# ------------------------------------------------------- OTC (櫃買) index

def test_parse_tpex_index_takes_the_latest_session_and_computes_pct_against_the_previous_close():
    rows = [{"Date": "20261006", "Close": "430.86", "Change": "-1.62"}, {"Date": "20261007", "Close": "430.46", "Change": "-0.40"},
            {"Date": "20261005", "Close": "432.48", "Change": "5.55"}]               # unordered on purpose
    got = bw.parse_tpex_index(rows)
    assert got["date"] == "2026-10-07" and got["close"] == 430.46 and got["chg"] == -0.40
    assert got["pct"] == round(-0.40 / 430.86 * 100, 2)                                 # previous close = 430.46 + 0.40


def test_parse_tpex_index_survives_junk():
    assert bw.parse_tpex_index(None) is None and bw.parse_tpex_index([]) is None
    assert bw.parse_tpex_index([{"Date": "20261007", "Close": "--", "Change": "x"}, {}, {"Date": "20261007", "Close": "0", "Change": "0"}]) is None


def test_the_otc_index_is_optional_in_market_json(monkeypatch):
    import src.market
    monkeypatch.setattr(src.market, "parse_market_summary", lambda rows: {"name": "TAIEX", "close": 20000.0, "change": 10.0, "change_pct": 0.05})

    def down(url):
        if url == bw.TPEX_INDEX:
            raise RuntimeError("down")
        return []
    m = bw.build_market(fetch=down)
    assert m["close"] == 20000.0 and "otc" not in m                     # a failing OTC fetch never breaks TAIEX

    ok = bw.build_market(fetch=lambda url: [{"Date": "20261007", "Close": "430.46", "Change": "-0.40"}] if url == bw.TPEX_INDEX else [])
    assert ok["otc"]["close"] == 430.46 and ok["close"] == 20000.0


# ------------------------------------------------------------------ events

def test_roc_to_iso():
    assert bw.roc_to_iso("1151008") == "2026-10-08" and bw.roc_to_iso("991231") == "2010-12-31"
    for bad in (None, "", "abc", "1151340", "11510", "11510081", "1150230"):
        assert bw.roc_to_iso(bad) is None, bad


def test_parse_ex_events_reads_both_exchanges_drops_past_and_junk_and_sorts():
    twse = [{"Date": "1151008", "Code": "00400A", "Exdividend": "息", "CashDividend": "0.120000", "StockDividendRatio": ""},
            {"Date": "1151001", "Code": "2330", "Exdividend": "息", "CashDividend": "5.0"},                    # past
            {"Date": "1151015", "Code": "1101", "Exdividend": "權息", "CashDividend": "1.5", "StockDividendRatio": "0.05"}]
    tpex = [{"ExRrightsExDividendDate": "1151007", "SecuritiesCompanyCode": "8440", "ExRrightsExDividend": "除息",
             "CashDividend": "0.35000000", "StockDividendRatio": "0.00000000"}, {}, {"ExRrightsExDividendDate": "xx", "SecuritiesCompanyCode": "1"}]
    got = bw.parse_ex_events(twse, tpex, "2026-10-07")
    assert [(e["c"], e["d"], e["k"]) for e in got] == [("8440", "2026-10-07", "除息"), ("00400A", "2026-10-08", "除息"), ("1101", "2026-10-15", "除權息")]
    assert got[0]["cash"] == 0.35 and got[0]["stock"] is False and got[2]["stock"] is True and got[2]["cash"] == 1.5


def test_parse_meetings_keeps_upcoming_and_tells_regular_from_extraordinary():
    row = lambda code, d, kind: {"公司代號": code, "開會日期": d, "股東常(臨時)會": kind, "是否採電子投票": "強制"}
    got = bw.parse_meetings([row("1101", "1151013", "臨時會"), row("2330", "1150610", "常會")], [row("1240", "1151105", "常會"), {}], "2026-10-07")
    assert [(e["c"], e["d"], e["k"]) for e in got] == [("1101", "2026-10-13", "臨時會"), ("1240", "2026-11-05", "常會")]


def test_build_events_names_a_failed_source_and_still_writes(tmp_path):
    def fetch(url):
        if url == bw.TPEX_AGM:
            raise RuntimeError("down")
        return [{"Date": "1151008", "Code": "2330", "Exdividend": "息", "CashDividend": "6"}] if url == bw.TWSE_EXPRE else []
    ev = bw.build_events(tmp_path, fetch=fetch, today="2026-10-07")
    assert ev["errors"] == ["tpex_agm"] and len(ev["ex"]) == 1
    saved = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert saved["ex"][0]["c"] == "2330" and saved["errors"] == ["tpex_agm"]


# ------------------------------------------------------- global indices / TAIEX futures

def _yahoo(price=51521.28, prev=51267.9, closes=None, t=1791319357):
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "chartPreviousClose": prev, "regularMarketTime": t},
                                  "indicators": {"quote": [{"close": closes if closes is not None else [51300.0, None, 51400.5, 51521.28]}]}}]}}


def test_parse_yahoo_chart_change_time_and_spark():
    g = bw.parse_yahoo_chart(_yahoo())
    assert g["price"] == 51521.28 and g["chg"] == round(51521.28 - 51267.9, 2) and g["pct"] == round((51521.28 / 51267.9 - 1) * 100, 2)
    assert g["spark"] == [51300.0, 51400.5, 51521.28]                         # gaps (None) dropped
    assert g["time"] == datetime.fromtimestamp(1791319357, bw.TAIPEI).strftime("%Y-%m-%d %H:%M")


def test_parse_yahoo_chart_caps_the_spark_and_keeps_the_last_point():
    g = bw.parse_yahoo_chart(_yahoo(closes=[float(i) for i in range(1, 300)]))
    assert len(g["spark"]) == bw.SPARK_POINTS and g["spark"][0] == 1.0 and g["spark"][-1] == 299.0


def test_parse_yahoo_chart_rejects_junk():
    for bad in ({}, {"chart": {"result": []}}, _yahoo(price=0), _yahoo(prev=None), {"chart": {"result": [{"meta": {}}]}}):
        assert bw.parse_yahoo_chart(bad) is None
    assert bw.parse_yahoo_chart(_yahoo(closes=[1.0]))["spark"] is None          # one point is not a line


def _taifex(rows):
    return {"RtData": {"QuoteList": rows}}


def test_parse_taifex_picks_the_first_traded_contract_of_the_session_and_skips_spot():
    rows = [{"SymbolID": "TXF-S", "CLastPrice": "49806.37", "CDiff": "-16.18", "CDiffRate": "-0.03", "CDate": "20261007", "CTime": "133315"},
            {"SymbolID": "TXFJ6-F", "CLastPrice": "49979.00", "CDiff": "-103.00", "CDiffRate": "-0.21", "CDate": "20261007", "CTime": "134500"},
            {"SymbolID": "TXFK6-F", "CLastPrice": "50143.00", "CDiff": "-149.00", "CDiffRate": "-0.30", "CDate": "20261007", "CTime": "134400"}]
    g = bw.parse_taifex(_taifex(rows), "-F")
    assert (g["contract"], g["price"], g["chg"], g["pct"], g["time"]) == ("TXFJ6-F", 49979.0, -103.0, -0.21, "2026-10-07 13:45")
    night = [{"SymbolID": "TXF-P", "CLastPrice": "", "CDiff": "0.00"}, {"SymbolID": "TXFJ6-M", "CLastPrice": "49804.00", "CDiff": "-164.00", "CDiffRate": "-0.33", "CDate": "20261007", "CTime": "164456"}]
    assert bw.parse_taifex(_taifex(night), "-M")["contract"] == "TXFJ6-M"
    assert bw.parse_taifex(_taifex(night), "-F") is None and bw.parse_taifex({}, "-F") is None


def test_build_globals_drops_a_failing_source_and_names_it(tmp_path):
    def fetch(url):
        if "%5ESOX" in url:
            raise RuntimeError("down")
        return _yahoo()
    def post(url, body):
        if body["MarketType"] == "1":
            return _taifex([])
        return _taifex([{"SymbolID": "TXFJ6-F", "CLastPrice": "49979", "CDiff": "-103", "CDiffRate": "-0.21", "CDate": "20261007", "CTime": "134500"}])
    g = bw.build_globals(tmp_path, fetch=fetch, post=post)
    assert [i["key"] for i in g["items"]] == ["txf", "dji", "ixic", "spx"]
    assert g["errors"] == ["txf_night", "sox"]
    assert json.loads((tmp_path / "globals.json").read_text(encoding="utf-8"))["items"][0]["name"] == "台指期"


# ------------------------------------------------------------------ news

def test_pick_news_newest_first_dedupes_titles_and_keeps_only_web_links():
    items = [{"title": "A", "link": "https://x/a", "published_at": "2026-10-07T01:00:00Z", "publisher": "P1"},
             {"title": "B", "link": "https://x/b", "published_at": "2026-10-07T03:00:00Z", "publisher": None},
             {"title": "A", "link": "https://x/a2", "published_at": "2026-10-07T02:00:00Z"},
             {"title": "C", "link": "javascript:alert(1)", "published_at": "2026-10-07T04:00:00Z"},
             {"title": "", "link": "https://x/e", "published_at": "2026-10-07T05:00:00Z"}]
    got = bw.pick_news(items)
    assert [(g["t"], g["l"]) for g in got] == [("B", "https://x/b"), ("A", "https://x/a2")]
    assert bw.pick_news([{"title": str(i), "link": "https://x/" + str(i), "published_at": "2026-10-07T00:00:%02dZ" % i} for i in range(40)], limit=5)[0]["t"] == "39"


def test_build_news_names_an_empty_group_and_still_writes(tmp_path):
    n = bw.build_news(tmp_path, fetch_news=lambda q: [] if q == "美股" else [{"title": "T", "link": "https://x", "published_at": "2026-10-07T00:00:00Z"}])
    assert n["errors"] == ["us"] and n["groups"]["tw"][0]["t"] == "T"
    assert json.loads((tmp_path / "news.json").read_text(encoding="utf-8"))["groups"]["us"] == []

