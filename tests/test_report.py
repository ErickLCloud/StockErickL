from src.report import render as R


def test_f2():
    assert R.f2(116.44999694824219) == "116.45"
    assert R.f2(None) == "—"


def test_report_degraded(tmp_path):
    txt = R.render_report("2026-10-06", holdings_rows=None, news_items=[], db_path=tmp_path / "x.db")
    for h in ("一、大盤概況", "二、篩選候選名單", "三、持倉報酬率", "四、新聞摘要"):
        assert h in txt
    assert "無持倉資料" in txt and "無新聞資料" in txt and R.MARKET_PLACEHOLDER in txt


def test_etf_fundamental_dash_and_2dp(tmp_path):
    c = [{"code": "0050", "name": "ETF", "kind": "ETF", "close": 116.44999694824219, "score": 1, "reasons": []}]
    txt = R.section_candidates(c, tmp_path / "x.db")
    assert "116.45" in txt and "116.449" not in txt
    assert "| — | — | — |" in txt


def test_holdings_section():
    rows = [{"code": "0050", "shares": 1000.0, "cost_price": 100.0, "last_close": 125.0, "pnl": 25000.0,
             "return_pct": 25.0, "days": 365, "annualized_pct": None}]
    txt = R.section_holdings(rows)
    assert "25000.00" in txt and "| — |" in txt


def test_versioning_never_overwrites(tmp_path):
    p1 = R.write_report("2026-10-06", "a", tmp_path)
    p2 = R.write_report("2026-10-06", "b", tmp_path)
    assert p1.name.endswith("_v1.md") and p2.name.endswith("_v2.md")
    assert p1.read_text(encoding="utf-8") == "a"


def test_market_real_dict(tmp_path):
    m = {"name": "發行量加權股價指數", "close": 49712.04, "change": 1236.30, "change_pct": 2.55}
    assert "49712.04　+1236.30（+2.55%）" in R.section_market(m)
    txt = R.render_report("2026-10-06", market=m, db_path=tmp_path / "x.db")
    assert "+1236.30" in txt and R.MARKET_PLACEHOLDER not in txt


def test_market_negative_and_none_fields():
    s = R.section_market({"name": "N", "close": 100.0, "change": -1.234, "change_pct": -0.5})
    assert "-1.23（-0.50%）" in s
    s = R.section_market({"name": "N", "close": 100.0, "change": None, "change_pct": None})
    assert "—（—）" in s


def test_market_none_placeholder():
    s = R.section_market(None)
    assert s.startswith("## 一、大盤概況") and R.MARKET_PLACEHOLDER in s
