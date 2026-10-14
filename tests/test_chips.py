import json

from scripts import chips as C

T86_FIELDS = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)", "外陸資買賣超股數(不含外資自營商)",
              "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數", "投信買進股數", "投信賣出股數", "投信買賣超股數",
              "自營商買賣超股數", "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
              "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數"]
T86 = {"stat": "OK", "fields": T86_FIELDS, "data": [
    ["2330  ", "台積電", "0", "0", "-25,700,500", "0", "0", "59,000", "0", "0", "2,999,400", "2,492,000"] + ["0"] * 7]}
# a real TPEx row (006201, 115/08/14): 外資合計 14,000 / 投信 -30,000 / 自營合計 153,103 / 三大法人 137,103
TPEX_ROW = ["006201", "元大富櫃50", "27,000", "13,000", "14,000", "0", "0", "0", "27,000", "13,000", "14,000", "0", "30,000",
            "-30,000", "0", "0", "0", "229,534", "76,431", "153,103", "229,534", "76,431", "153,103", "137,103"]
TPEX_3 = {"tables": [{"data": [TPEX_ROW, ["6488", "環球晶"] + ["0"] * 8 + ["68,600"] + ["0", "0", "-32,000"] + ["0"] * 8 + ["-9,400", "27,200"]]}]}
TWSE_M = {"stat": "OK", "tables": [{"title": "信用交易統計", "data": [["融資(交易單位)"] + ["1"] * 5]},
                                   {"title": "115年10月12日 融資融券彙總 (全部)", "data": [
                                       ["2330", "台積電", "1", "2", "0", "32,000", "32,542", "0", "0", "0", "0", "40", "39", "0", "0", " "]]}]}
TPEX_M = {"tables": [{"data": [["6488", "環球晶", "16,400", "1", "1", "0", "16,501", "0", "0", "0", "480", "9", "0", "0", "489",
                                "0", "0", "0", "0", ""]]}]}
TDCC = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n" + "\n".join(
    f"20261008,2330  ,{lv},{n},0,{p}" for lv, n, p in
    [(1, 2459344, 1.10), (2, 446137, 3.29), (8, 2043, 0.35), (11, 1312, 1.42), (12, 579, 1.08), (15, 1482, 84.78), (16, 1, 0.0), (17, 3007088, 100.0)])


def test_parse_t86_sums_foreign_and_converts_to_lots():
    out = C.parse_t86(T86)
    assert out["2330"] == [round((-25700500 + 59000) / 1000), 2999, 2492]
    assert C.parse_t86({"stat": "很抱歉，沒有符合條件的資料!"}) is None


def test_parse_tpex_3_by_position_matches_the_total():
    out = C.parse_tpex_3(TPEX_3)
    f, t, d = out["006201"]
    assert (f, t, d) == (14, -30, 153)
    assert out["6488"] == [69, -32, -9]
    assert C.parse_tpex_3({"tables": []}) is None


def test_parse_margins():
    assert C.parse_twse_margin(TWSE_M) == {"2330": [32542, 39]}
    assert C.parse_tpex_margin(TPEX_M) == {"6488": [16501, 489]}
    assert C.parse_twse_margin({"stat": "x"}) is None


def test_parse_tdcc_big_and_small():
    day, snap = C.parse_tdcc(TDCC)
    assert day == "2026-10-08"
    big, k1000, small_n, small, holders = snap["2330"]
    assert big == round(1.08 + 84.78, 2) and k1000 == 84.78
    assert small_n == 2459344 + 446137 + 2043 and small == round(1.10 + 3.29 + 0.35, 2)
    assert holders == 3007088


def router(missing=()):
    def fetch(url):
        for key, doc in (("fund/T86", T86), ("3itrade", TPEX_3), ("MI_MARGN", TWSE_M), ("margin_bal", TPEX_M)):
            if key in url:
                if key in missing:
                    raise RuntimeError("down")
                return doc
        raise AssertionError(url)
    return fetch


def test_fetch_day_merges_markets_and_tolerates_a_missing_source():
    snap = C.fetch_day("2026-10-12", router(), sleep=0)
    assert snap["2330"] == [-25642, 2999, 2492, 32542, 39]
    assert snap["6488"] == [69, -32, -9, 16501, 489]
    snap = C.fetch_day("2026-10-12", router(missing=("MI_MARGN",)), sleep=0)
    assert snap["2330"][3:] == [None, None]
    assert C.fetch_day("2026-10-12", router(missing=("fund/T86", "3itrade")), sleep=0) is None


def test_update_daily_skips_done_days_refetches_recent_incomplete_and_prunes(tmp_path, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return router()(url)
    assert C.update_daily(tmp_path, ["2026-10-09", "2026-10-12"], fetch, sleep=0) == ["2026-10-09", "2026-10-12"]
    n = len(calls)
    assert C.update_daily(tmp_path, ["2026-10-09", "2026-10-12"], fetch, sleep=0) == []
    assert len(calls) == n                                         # nothing fetched twice
    (tmp_path / "daily" / "2026-10-12.json").write_text(json.dumps({"2330": [1, 2, 3, None, None]}), encoding="utf-8")
    assert C.update_daily(tmp_path, ["2026-10-09", "2026-10-12"], fetch, sleep=0) == ["2026-10-12"]   # margin came later
    monkeypatch.setattr(C, "KEEP_DAYS", 1)
    C.update_daily(tmp_path, ["2026-10-09", "2026-10-12"], fetch, sleep=0)
    assert [p.name for p in (tmp_path / "daily").glob("*.json")] == ["2026-10-12.json"]


def test_build_chips_columns_and_filters(tmp_path):
    (tmp_path / "daily").mkdir()
    (tmp_path / "tdcc").mkdir()
    (tmp_path / "daily" / "2026-10-09.json").write_text(json.dumps({"2330": [1, 2, 3, 4, 5], "00878": [9] * 5}), encoding="utf-8")
    (tmp_path / "daily" / "2026-10-12.json").write_text(json.dumps({"2330": [6, 7, 8, 9, 10], "6488": [1] * 5}), encoding="utf-8")
    (tmp_path / "tdcc" / "2026-10-08.json").write_text(json.dumps({"2330": [87.5, 84.8, 100, 8.8, 300], "9999": [1] * 5}), encoding="utf-8")
    doc = C.build_chips(tmp_path, tmp_path, stamp="s")
    assert doc["days"] == ["2026-10-09", "2026-10-12"] and doc["weeks"] == ["2026-10-08"]
    assert set(doc["items"]) == {"2330", "6488"}                   # no ETF, no code that never traded
    it = doc["items"]["2330"]
    assert it["f"] == [1, 6] and it["m"] == [4, 9] and it["b"] == [87.5] and it["n"] == [100]
    assert doc["items"]["6488"]["f"] == [None, 1] and doc["items"]["6488"]["b"] == [None]
    assert json.loads((tmp_path / "chips.json").read_text(encoding="utf-8")) == doc
