import json
from datetime import date, datetime

import pytest

from scripts import interpret as I

TUE = datetime(2026, 10, 13, 8, 5, tzinfo=I.TAIPEI)
SAT = datetime(2026, 10, 17, 8, 5, tzinfo=I.TAIPEI)


def news(title, at="2026-10-13T00:00:00Z", link="https://x/1", publisher="P"):
    return {"title": title, "link": link, "published_at": at, "publisher": publisher}


def test_holidays_and_trading_days():
    hol = I.parse_holidays([{"Date": "1151010", "Name": "國慶日"}, {"Date": "bad"}, {},
                            {"Date": "1150102", "Name": "國曆新年開始交易日"},             # a trading day
                            {"Date": "1150212", "Name": "市場無交易，僅辦理結算交割作業"}])
    assert hol == {"2026-10-10", "2026-02-12"}
    assert I.is_trading_day(date(2026, 10, 13), hol)
    assert not I.is_trading_day(date(2026, 10, 17), hol)        # Saturday
    assert not I.is_trading_day(date(2026, 10, 10), {"2026-10-10"})


def test_pick_headlines_filters_noise_duplicates_and_caps_per_topic():
    by = {
        "tw": [news("台股重挫960點 外資賣超", "2026-10-13T02:00:00Z"),
               news("台股重挫960點　外資賣超", "2026-10-13T01:00:00Z"),      # same story, full-width space
               news("00985A配息2.15元吸睛"),                                  # noise
               news("沒有連結", link="ftp://x")]
        + [news(f"台股新聞{i}號標題內容不同", f"2026-10-12T0{i}:00:00Z") for i in range(9)],
        "mem": [news("記憶體報價續漲 DRAM 合約價上修")],
    }
    out = I.pick_headlines(by, per_topic=5)
    titles = [x["t"] for x in out]
    assert titles[0] == "台股重挫960點 外資賣超"
    assert sum(1 for x in out if x["topic"] == "tw") == 5
    assert not any("配息" in t for t in titles)
    assert "記憶體報價續漲 DRAM 合約價上修" in titles                       # later topics are not starved
    assert [x["id"] for x in out] == [f"n{i}" for i in range(1, len(out) + 1)]


RULES = [
    {"id": "sox-down-semi", "when": {"key": "sox", "op": "<=", "pct": -2}, "dir": "down", "status": "已審",
     "targets": [{"type": "theme", "name": "晶圓代工"}, {"type": "theme", "name": "IC設計"}], "why": "w"},
    {"id": "sox-up-semi", "when": {"key": "sox", "op": ">=", "pct": 2}, "dir": "up", "status": "已審",
     "targets": [{"type": "theme", "name": "晶圓代工"}], "why": "w"},
    {"id": "txfn", "when": {"key": "txf_night", "op": "<=", "pct": -1}, "dir": "down", "status": "待審",
     "targets": [{"type": "sector", "name": "半導體業"}], "why": "w"},
]
GLOBALS = {"items": [{"key": "sox", "name": "費城半導體", "pct": -2.67}, {"key": "txf_night", "name": "台指期夜盤", "pct": None}]}


def test_fire_rules_matches_the_page_logic():
    fired = I.fire_rules(RULES, GLOBALS)
    assert [f["id"] for f in fired] == ["sox-down-semi"]          # missing pct is skipped, not treated as 0
    assert fired[0]["themes"] == ["晶圓代工", "IC設計"]


def test_theme_revenue_median_ignores_missing():
    themes = [{"name": "A", "codes": ["1", "2", "3"]}, {"name": "B", "codes": ["9"]}]
    fin = {"items": {"1": [1, 0, 10.0, 0, 1, 1], "2": [1, 0, 30.0, 0, 1, 1], "3": [None] * 6}}
    r = I.theme_revenue(themes, fin)
    assert r["A"] == {"n": 3, "reported": 2, "rev_yoy_median": 20.0}
    assert r["B"]["rev_yoy_median"] is None


def test_clean_keeps_only_checkable_items_and_computes_vs_rule():
    fired = I.fire_rules(RULES, GLOBALS)
    report = {"summary": " 費半重挫。 ", "items": [
        {"theme": "晶圓代工", "dir": "down", "why": "費半跌", "refs": ["r:sox-down-semi", "n99"]},
        {"theme": "IC設計", "dir": "up", "why": "逆勢", "refs": ["n1"]},
        {"theme": "IC設計", "dir": "watch", "why": "分歧", "refs": ["n1"]},
        {"theme": "散熱", "dir": "up", "why": "水冷", "refs": ["n2"]},
        {"theme": "2330 台積電", "dir": "down", "why": "x", "refs": ["n1"]},      # not a theme
        {"theme": "散熱", "dir": "sideways", "why": "x", "refs": ["n1"]},        # bad dir
        {"theme": "散熱", "dir": "up", "why": "x", "refs": ["n99"]},             # no known ref
    ]}
    doc = I.clean(report, {"晶圓代工", "IC設計", "散熱"}, {"n1", "n2", "r:sox-down-semi"}, fired)
    assert doc["summary"] == "費半重挫。"
    assert [(x["theme"], x["dir"], x["vs_rule"]) for x in doc["items"]] == [
        ("晶圓代工", "down", "agree"), ("IC設計", "up", "differ"), ("IC設計", "watch", None), ("散熱", "up", None)]
    assert doc["items"][0]["refs"] == ["r:sox-down-semi"]
    assert len(doc["dropped"]) == 3


def test_tool_input_requires_the_report_block():
    assert I.tool_input({"content": [{"type": "text"}, {"type": "tool_use", "name": "report", "input": {"a": 1}}]}) == {"a": 1}
    fenced = '好的：\n```json\n{"summary": "s", "items": []}\n```'
    assert I.tool_input({"content": [{"type": "text", "text": fenced}]}) == {"summary": "s", "items": []}
    for bad in ("hi", '{"no": "items"}', "{broken"):
        with pytest.raises(ValueError):
            I.tool_input({"content": [{"type": "text", "text": bad}]})


def wif_env(tmp_path, workspace=None):
    jwt = tmp_path / "jwt"
    jwt.write_text("header.payload.sig\n", encoding="utf-8")
    env = {"ANTHROPIC_IDENTITY_TOKEN_FILE": str(jwt), "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_x",
           "ANTHROPIC_ORGANIZATION_ID": "org", "ANTHROPIC_SERVICE_ACCOUNT_ID": "svac_x"}
    if workspace:
        env["ANTHROPIC_WORKSPACE_ID"] = workspace
    return env


class FakePost:
    def __init__(self, report):
        self.calls, self.report = [], report

    def __call__(self, url, body, timeout=None, headers=None):
        self.calls.append((url, body, headers))
        if url.endswith("/v1/oauth/token"):
            return {"access_token": "sk-ant-oat01-test", "expires_in": 600}
        return {"model": "claude-sonnet-5-5", "usage": {"input_tokens": 5200, "output_tokens": 640},
                "content": [{"type": "tool_use", "name": "report", "input": self.report}]}


def test_access_token_body(tmp_path):
    post = FakePost({})
    assert I.access_token(post, wif_env(tmp_path)) == "sk-ant-oat01-test"
    body = post.calls[0][1]
    assert body["assertion"] == "header.payload.sig" and "workspace_id" not in body
    I.access_token(post, wif_env(tmp_path, workspace="wrkspc_1"))
    assert post.calls[1][1]["workspace_id"] == "wrkspc_1"


def out_dir(tmp_path):
    out = tmp_path / "data"
    out.mkdir()
    (out / "globals.json").write_text(json.dumps({"items": [{"key": "sox", "name": "費城半導體", "pct": -2.67}]}), encoding="utf-8")
    (out / "financials.json").write_text(json.dumps({"rev_month": "2026-09", "items": {"2330": [1, 0, 54.65, 0, 1, 1]}}), encoding="utf-8")
    return out


def fake_news(q):
    return [news(f"{q} 相關消息一則內容", link="https://x/" + q)]


def test_run_writes_page_file_and_archive(tmp_path):
    out = out_dir(tmp_path)
    post = FakePost({"summary": "費半重挫，半導體偏弱。", "items": [
        {"theme": "晶圓代工", "dir": "down", "why": "費半跌 2.67%", "refs": ["g:sox", "r:sox-down-semi"]}]})
    doc = I.run(out, now=TUE, fetch_news=fake_news, holidays=set(), post=post, env=wif_env(tmp_path))
    assert doc["items"][0]["vs_rule"] == "agree"
    assert doc["usage"] == {"input": 5200, "output": 640}
    assert doc["date"] == "2026-10-13" and doc["news"][0]["id"] == "n1"
    assert json.loads((out / "interpret.json").read_text(encoding="utf-8")) == json.loads(
        (out / "interpret" / "2026-10-13.json").read_text(encoding="utf-8"))
    url, body, headers = post.calls[1]
    assert url.endswith("/v1/messages") and headers["authorization"] == "Bearer sk-ant-oat01-test"
    assert body["model"] == I.MODEL and body["tool_choice"] == {"type": "auto"}
    assert "2330" not in body["messages"][0]["content"]          # no holdings or codes are sent


def test_run_makes_no_call_on_weekend_holiday_or_second_run(tmp_path):
    out = out_dir(tmp_path)
    post = FakePost({"summary": "s", "items": []})
    assert I.run(out, now=SAT, fetch_news=fake_news, holidays=set(), post=post, env=wif_env(tmp_path)) is None
    assert I.run(out, now=TUE, fetch_news=fake_news, holidays={"2026-10-13"}, post=post, env=wif_env(tmp_path)) is None
    assert post.calls == []
    I.run(out, now=TUE, fetch_news=fake_news, holidays=set(), post=post, env=wif_env(tmp_path))
    n = len(post.calls)
    assert I.run(out, now=TUE, fetch_news=fake_news, holidays=set(), post=post, env=wif_env(tmp_path)) is None
    assert len(post.calls) == n                                    # one call a day


def test_dry_run_never_calls(tmp_path, capsys):
    out = out_dir(tmp_path)
    post = FakePost({})
    I.run(out, now=TUE, fetch_news=fake_news, holidays=set(), post=post, env={}, dry_run=True)
    assert post.calls == []
    assert '"tool_choice"' in capsys.readouterr().out
