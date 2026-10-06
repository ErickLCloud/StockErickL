"""Tests for the 大盤 module. Fixtures only — no network."""

from src.market import (TAIEX_NAME, clean_num, fetch_market_summary,
                        parse_market_summary, roc_to_iso)

# Shape copied from a real MI_INDEX response, including the two decoys that a
# substring match on "加權" would wrongly select.
PAYLOAD = [
    {"日期": "1151005", "指數": "寶島股價指數", "收盤指數": "55145.96",
     "漲跌": "+", "漲跌點數": "1,325.40", "漲跌百分比": "2.46", "特殊處理註記": ""},
    {"日期": "1151005", "指數": "發行量加權股價報酬指數", "收盤指數": "114952.55",
     "漲跌": "+", "漲跌點數": "2,858.78", "漲跌百分比": "2.55", "特殊處理註記": ""},
    {"日期": "1151005", "指數": "發行量加權股價指數", "收盤指數": "49712.04",
     "漲跌": "+", "漲跌點數": "1,236.30", "漲跌百分比": "2.55", "特殊處理註記": ""},
    {"日期": "1151005", "指數": "加權指數掩護性臺指買權價外5%報酬指數",
     "收盤指數": "18403.00", "漲跌": "+", "漲跌點數": "99.03",
     "漲跌百分比": "0.54", "特殊處理註記": ""},
]


def test_roc_to_iso():
    assert roc_to_iso("1151005") == "2026-10-05"
    assert roc_to_iso("") is None
    assert roc_to_iso("abc") is None


def test_clean_num_strips_thousands_separator():
    assert clean_num("1,236.30") == 1236.3
    assert clean_num("") is None
    assert clean_num("--") is None
    assert clean_num(None) is None


def test_picks_taiex_not_the_total_return_decoy():
    m = parse_market_summary(PAYLOAD)
    assert m["name"] == TAIEX_NAME
    # 114952.55 is the total-return index; selecting it would be the bug.
    assert m["close"] == 49712.04
    assert m["change"] == 1236.3
    assert m["change_pct"] == 2.55
    assert m["date"] == "2026-10-05"


def test_negative_change_takes_sign_from_its_own_column():
    payload = [dict(PAYLOAD[2], **{"漲跌": "-"})]
    m = parse_market_summary(payload)
    assert m["change"] == -1236.3
    assert m["change_pct"] == -2.55


def test_missing_taiex_row_returns_none():
    assert parse_market_summary([PAYLOAD[0]]) is None


def test_fetch_degrades_to_none_on_source_failure():
    def boom(_url):
        raise RuntimeError("source down")

    assert fetch_market_summary(fetch=boom) is None
