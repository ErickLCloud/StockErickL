import json
from datetime import date

import pytest

from scripts import dividends as D
from scripts import earnings as E


def page(tables):
    """t163sb04-like HTML: [(header list, [row lists])]."""
    out = ""
    for hdr, rows in tables:
        out += "<table><tr>" + "".join(f"<th>{h}</th>" for h in hdr) + "</tr>"
        out += "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"
    return out


GENERAL = ["公司代號", "公司名稱", "營業收入", "營業成本", "營業毛利（毛損）", "營業毛利（毛損）淨額", "營業費用", "營業利益（損失）",
           "本期淨利（淨損）", "淨利（淨損）歸屬於母公司業主", "淨利（淨損）歸屬於非控制權益", "基本每股盈餘（元）"]
BANK = ["公司代號", "公司名稱", "利息淨收益", "利息以外淨損益", "本期稅後淨利（淨損）", "淨利（損）歸屬於母公司業主", "基本每股盈餘（元）"]


def season(rev, gross, op, net, eps):
    return page([(GENERAL, [["2330", "台積電", f"{rev:,}", "1", "--", f"{gross:,}", "1", f"{op:,}", "1", f"{net:,}", "0", str(eps)]]),
                 (BANK, [["2881", "富邦金", "1", "1", "1", "50,000", "3.00"]])])


def test_parse_season_finds_columns_by_name_across_layouts():
    rows = E.parse_season(season(1000, 600, 450, 400, 15.5))
    assert rows["2330"] == [1000.0, 600.0, 450.0, 400.0, 15.5]
    assert rows["2881"] == [None, None, None, 50000.0, 3.0]          # banks: no revenue / gross / op lines


def test_quarter_helpers():
    assert E.seasons_back(2026, 2, 3) == [(2025, 4), (2026, 1), (2026, 2)]
    assert E.latest_due(date(2026, 10, 13)) == (2026, 3)
    assert E.latest_due(date(2026, 2, 1)) == (2025, 4)


def test_single_quarters_subtract_ytd_within_a_year():
    cum = {(2025, 4): [400, 200, 100, 80, 8.0], (2026, 1): [100, 50, 30, 20, 2.0], (2026, 2): [250, 130, 70, 50, 5.0], (2026, 4): [600, 1, 1, 1, 9.0]}
    sq = E.single_quarters(cum)
    assert sq[(2026, 1)] == [100, 50, 30, 20, 2.0]
    assert sq[(2026, 2)] == [150, 80, 40, 30, 3.0]
    assert sq[(2026, 4)] == [None] * 5                                   # Q3 missing: no guess
    assert sq[(2025, 4)] == [None] * 5                                   # Q3 2025 missing too


def test_update_fetches_missing_and_refreshes_the_newest_two(tmp_path):
    calls = []
    def fetch(market, y, s):
        calls.append((market, y, s))
        return season(1000 * s, 600 * s, 450 * s, 400 * s, 10.0 * s)
    done = E.update(tmp_path, date(2026, 10, 13), fetch=fetch, sleep=0)
    assert done[0] == "2024Q2" and done[-1] == "2026Q3" and len(done) == 10
    calls.clear()
    assert E.update(tmp_path, date(2026, 10, 13), fetch=fetch, sleep=0) == ["2026Q2", "2026Q3"]
    assert len(calls) == 4                                               # 2 seasons x 2 markets


def test_build_earnings_margins_units_and_unfiled_latest_quarter(tmp_path):
    def put(name, obj):
        (tmp_path / f"{name}.json").write_text(json.dumps(obj), encoding="utf-8")
    put("2025Q4", {"2330": [9, 9, 9, 9, 9.0]})                           # Q4 with no Q3 on file: base only, not shown
    put("2026Q1", {"2330": [1_000_000, 600_000, 450_000, 400_000, 2.0], "2317": [1, 1, 1, 1, 1], "6488": [1, 1, 1, 1, 1],
                   "2881": [500, None, None, 100, 1.2]})
    put("2026Q2", {"2330": [2_500_000, 1_500_000, 1_100_000, 1_000_000, 5.0], "2317": [2, 2, 2, 2, 2], "6488": [2, 2, 2, 2, 2],
                   "2881": [80, None, None, 300, 3.0]})
    put("2026Q3", {"2330": [4_000_000, 2_400_000, 1_800_000, 1_600_000, 8.0]})        # 1 of 3 filed so far
    doc = E.build_earnings(tmp_path, tmp_path, stamp="s")
    assert doc["q"] == ["2026Q1", "2026Q2"]                              # Q3 held back until half have filed
    it = doc["items"]["2330"]
    assert it["rev"] == [10.0, 15.0]                                     # 千元 -> 億
    assert it["gm"] == [60.0, 60.0] and it["om"] == [45.0, 43.33] and it["eps"] == [2.0, 3.0]
    fin = doc["items"]["2881"]                                           # no gross line: EPS only
    assert fin["rev"] == [None, None] and fin["nm"] == [None, None] and fin["eps"] == [1.2, 1.8]


TWT49U = {"stat": "OK", "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息"],
          "data": [["113年03月18日", "2330", "台積電", "753", "749.5", "3.499789", "息"],
                   ["110年09月06日", "2881", "富邦金", "1", "1", "8.464387", "權"],
                   ["111年07月21日", "1101", "台泥", "1", "1", "4.687849", "權息"],
                   ["113年05月02日", "00878", "國泰永續高股息", "1", "1", "0.5", "息"]]}
TPEX = {"tables": [{"fields": ["除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值", "權值+息值", "權/息", "漲停價",
                               "跌停價", "開始交易基準價", "減除股利參考價", "現金股利", "每仟股無償配股"],
                    "data": [["113/01/19", "6488", "環球晶", "580", "572", "0", "8", "8.000000", "除息", "", "", "", "", "8.00000000", "0.00000000"],
                             ["113/08/01", "3105", "穩懋", "1", "1", "1", "2", "3.0", "除權息", "", "", "", "", "2.0", "100.0"]]}]}


def test_parse_dividends_keeps_cash_only_where_known():
    tw = {r[0]: r for r in D.parse_twse(TWT49U)}
    assert tw["2330"] == ("2330", "2024-03-18", 3.499789, None, 3.499789)
    assert tw["2881"][2] is None and tw["1101"][2] is None             # 權 / 權息: cash part unknown
    tp = {r[0]: r for r in D.parse_tpex(TPEX)}
    assert tp["6488"] == ("6488", "2024-01-19", 8.0, None, 8.0)
    assert tp["3105"] == ("3105", "2024-08-01", 2.0, 100.0, 3.0)


def test_build_dividends_filters_etfs_and_survives_one_market(tmp_path):
    def fetch(url):
        if "tpex" in url:
            raise RuntimeError("down")
        return TWT49U
    doc = D.build_dividends(tmp_path, today=date(2026, 10, 13), fetch=fetch, stamp="s")
    assert set(doc["items"]) == {"2330", "2881", "1101"} and doc["from"] == "2021-01-01"
    assert doc["errors"] == ["tpex: RuntimeError"]
    with pytest.raises(RuntimeError):
        D.build_dividends(tmp_path, today=date(2026, 10, 13), fetch=lambda u: {"stat": "no"})
