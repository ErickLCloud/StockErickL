"""盤前解讀（AI）: one Claude call a trading day, theme level only.

Decisions D10–D15 (output/multi-work/advisor/decisions.md):
  - runs in GitHub Actions at 08:00 Taipei; skips weekends and TWSE holidays
  - authenticates with Workload Identity Federation (GitHub OIDC token in a file,
    exchanged for a short-lived Anthropic token); no API key is stored anywhere
  - input: filtered headlines + international indicators + rules fired last night
    + a revenue summary per theme. Holdings live only in the browser and are never sent.
  - output: a short summary and theme items (偏多／偏空／注意 + why + refs). No single-stock
    calls, no prices. Whether an item agrees with a fired rule is computed here, not by the model.
  - at most one successful call a day: if today's archive file exists the run is a no-op.

Writes <out>/interpret.json (what the page shows) and <out>/interpret/<date>.json (the archive
that later scoring reads; it rides on the data branch).

Standard library only, like the rest of the data pipeline.

    python scripts/interpret.py --out _site/data            # needs the WIF env vars
    python scripts/interpret.py --out docs/data --dry-run   # prints the prompt, no call
"""
import argparse
import json
import os
import re
import statistics
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.http import fetch_json, post_json  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
API = "https://api.anthropic.com"
MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 2000
HOLIDAYS = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"

# Google News queries (D12). Broad market first, then the drivers the rules use.
NEWS_TOPICS = (("tw", "台股"), ("us", "美股"), ("sox", "費半"), ("nvda", "輝達"),
               ("tsmc", "台積電"), ("fed", "聯準會"), ("oil", "油價"), ("mem", "記憶體 報價"))
NEWS_PER_TOPIC = 5          # 8 topics x 5 = 40: every topic gets its share
NEWS_MAX = 40
# Headlines that carry no market information: ETF payout chatter, SEO quote pages, lotteries.
NOISE = re.compile(r"配息|除息|領息|受益人|本益比歷史|基本分析|股價明細|抽籤|申購|存股|千金股|高股息")
DIRS = ("up", "down", "watch")


def title_key(t):
    """Duplicate check: the same story is often syndicated with different spacing or a byline
    suffix, so compare the first 18 characters with all whitespace removed."""
    return re.sub(r"\s", "", t)[:18]


def today_taipei(now=None):
    return (now or datetime.now(TAIPEI)).astimezone(TAIPEI).date()


def is_trading_day(day, holidays=()):
    """Weekdays that are not TWSE holidays. `holidays` is a set of ISO dates."""
    return day.weekday() < 5 and day.isoformat() not in holidays


def parse_holidays(rows):
    """TWSE holidaySchedule rows -> ISO dates the market is closed. Dates are ROC (1151010).
    The list also carries trading days ("國曆新年開始交易日", "農曆春節前最後交易日"); those
    names end in 交易日 and are left out. "市場無交易，僅辦理結算交割作業" is a closed day."""
    out = set()
    for r in rows or []:
        s = str(r.get("Date") or "").strip()
        if str(r.get("Name") or "").strip().endswith("交易日"):
            continue
        if len(s) == 7 and s.isdigit():
            out.add(f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:]}")
    return out


# ------------------------------------------------------------------ inputs
def pick_headlines(by_topic, per_topic=NEWS_PER_TOPIC, limit=NEWS_MAX):
    """{topic: [news items]} -> [{id, topic, t, p, at, l}], noise dropped, one entry per title,
    newest first inside each topic. Ids are n1, n2, ... so the model can cite them."""
    seen, out = set(), []
    for topic, items in by_topic.items():
        kept = 0
        for it in sorted(items, key=lambda i: i.get("published_at") or "", reverse=True):
            t = (it.get("title") or "").strip()
            link = (it.get("link") or "").strip()
            if not t or title_key(t) in seen or NOISE.search(t) or not link.startswith(("https://", "http://")):
                continue
            seen.add(title_key(t))
            out.append({"topic": topic, "t": t, "p": it.get("publisher"), "at": it.get("published_at"), "l": link})
            kept += 1
            if kept >= per_topic or len(out) >= limit:
                break
        if len(out) >= limit:
            break
    for i, x in enumerate(out, 1):
        x["id"] = f"n{i}"
    return out


def fire_rules(rules, globals_doc):
    """Same test as docs/globalmap.js evaluate(): rules whose indicator crossed its threshold."""
    pct = {g["key"]: g["pct"] for g in (globals_doc or {}).get("items", [])
           if isinstance(g.get("pct"), (int, float))}
    out = []
    for r in rules:
        w = r.get("when") or {}
        v = pct.get(w.get("key"))
        if v is None:
            continue
        if (w.get("op") == ">=" and v >= w["pct"]) or (w.get("op") == "<=" and v <= w["pct"]):
            out.append({"id": r["id"], "key": w["key"], "value": v, "dir": r["dir"], "status": r.get("status"),
                        "themes": [t["name"] for t in r["targets"] if t["type"] == "theme"],
                        "targets": [t["name"] for t in r["targets"]], "why": r.get("why")})
    return out


def theme_revenue(themes, fin):
    """Per theme: median 月營收年增 % over members that reported, and how many reported."""
    items = (fin or {}).get("items", {})
    out = {}
    for th in themes:
        yoy = [items[c][2] for c in th["codes"] if c in items and isinstance(items[c][2], (int, float))]
        out[th["name"]] = {"n": len(th["codes"]), "reported": len(yoy),
                           "rev_yoy_median": round(statistics.median(yoy), 1) if yoy else None}
    return out


def build_context(day, globals_doc, rules, themes, fin, headlines):
    fired = fire_rules(rules, globals_doc)
    return {
        "date": day.isoformat(),
        "indicators": [{"ref": "g:" + g["key"], "name": g.get("name"), "pct": g.get("pct"), "time": g.get("time")}
                       for g in (globals_doc or {}).get("items", []) if g.get("pct") is not None],
        "rules_fired": [{"ref": "r:" + f["id"], "indicator": f["key"], "value": f["value"], "dir": f["dir"],
                         "targets": f["targets"], "status": f["status"], "why": f["why"]} for f in fired],
        "themes": [{"name": k, **v} for k, v in theme_revenue(themes, fin).items()],
        "rev_month": (fin or {}).get("rev_month"),
        "headlines": [{"ref": h["id"], "topic": h["topic"], "title": h["t"], "publisher": h["p"], "at": h["at"]}
                      for h in headlines],
    }, fired


SYSTEM = """你是台股盤前分析助手，用繁體中文寫給一位個人投資人看。
依據使用者訊息裡的 JSON（昨晚的國際指標、已觸發的規則、各題材的月營收年增中位數、新聞標題），
判斷今天台股開盤前哪些「題材」值得注意。

規則：
- 只能寫 JSON 裡 themes 列出的題材名稱，一字不差；不要寫個股代號、個股名稱、價位、停損或買賣建議。
- 每一項都要在 refs 列出依據，只能用 JSON 裡出現過的 ref（n…、g:…、r:…）。沒有依據就不要寫那一項。
- dir：up＝偏多、down＝偏空、watch＝方向不明但要注意（例如消息正反都有、或與規則訊號矛盾）。
- 規則（r:…）是回測過的統計傾向；新聞標題只是線索，不要把單一標題當成定論。
- 寧可少寫，不要湊數：最多 6 項。真的沒有值得說的就回空清單，summary 說明今天沒有明顯訊號。
- summary 最多 3 句，講今天最重要的一兩件事和原因。
- 一定要呼叫 report 工具回答，不要另外寫文字。"""

TOOL = {
    "name": "report",
    "description": "回報今天的盤前解讀。",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "最多 3 句"},
            "items": {"type": "array", "maxItems": 6, "items": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "dir": {"type": "string", "enum": list(DIRS)},
                    "why": {"type": "string", "description": "1–2 句，說明原因"},
                    "refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["theme", "dir", "why", "refs"]}},
        },
        "required": ["summary", "items"],
    },
}


def request_body(ctx):
    # tool_choice "tool"/"any" (forcing the tool) is rejected by this model; "auto" plus the
    # instruction in SYSTEM, and tool_input() also accepts a bare JSON answer.
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM,
            "tools": [TOOL], "tool_choice": {"type": "auto"},
            "messages": [{"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}]}


# ------------------------------------------------------------------ output checks
def clean(report, theme_names, refs, fired):
    """Keep only what can be checked: known themes, known dirs, known refs, at least one ref.
    Adds vs_rule: agree / differ when a fired rule targets the same theme, else None."""
    rule_dir = {}
    for f in fired:
        for t in f["themes"]:
            rule_dir.setdefault(t, set()).add(f["dir"])
    items, dropped = [], []
    for it in (report or {}).get("items") or []:
        theme, d = it.get("theme"), it.get("dir")
        rs = [r for r in it.get("refs") or [] if r in refs]
        if theme not in theme_names or d not in DIRS or not rs or not (it.get("why") or "").strip():
            dropped.append(theme or "?")
            continue
        rd = rule_dir.get(theme)
        vs = None if not rd or d == "watch" else ("agree" if rd == {d} else "differ")
        items.append({"theme": theme, "dir": d, "why": it["why"].strip(), "refs": rs, "vs_rule": vs})
    summary = ((report or {}).get("summary") or "").strip()
    return {"summary": summary, "items": items[:6], "dropped": dropped}


def tool_input(resp):
    """The report: the `report` tool call, or failing that a JSON object in the text."""
    blocks = (resp or {}).get("content") or []
    for block in blocks:
        if block.get("type") == "tool_use" and block.get("name") == "report":
            return block.get("input")
    text = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            doc = json.loads(text[start:end + 1])
            if isinstance(doc, dict) and "items" in doc:
                return doc
        except ValueError:
            pass
    raise ValueError("no report in the response: " + text[:300])


# ------------------------------------------------------------------ Claude API (WIF)
def access_token(post=post_json, env=os.environ):
    """Exchange the GitHub OIDC JWT (written to ANTHROPIC_IDENTITY_TOKEN_FILE) for an Anthropic token."""
    jwt = Path(env["ANTHROPIC_IDENTITY_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    body = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt,
            "federation_rule_id": env["ANTHROPIC_FEDERATION_RULE_ID"],
            "organization_id": env["ANTHROPIC_ORGANIZATION_ID"],
            "service_account_id": env["ANTHROPIC_SERVICE_ACCOUNT_ID"]}
    if env.get("ANTHROPIC_WORKSPACE_ID"):
        body["workspace_id"] = env["ANTHROPIC_WORKSPACE_ID"]
    return post(API + "/v1/oauth/token", body, timeout=60)["access_token"]


def call_claude(body, token, post=post_json):
    return post(API + "/v1/messages", body, timeout=120,
                headers={"authorization": "Bearer " + token, "anthropic-version": "2023-06-01"})


# ------------------------------------------------------------------ run
def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gather_news(fetch_news=None):
    if fetch_news is None:
        from src.data.news import fetch_news
    return pick_headlines({k: fetch_news(q) for k, q in NEWS_TOPICS})


def run(out, now=None, fetch_news=None, holidays=None, post=post_json, env=os.environ, dry_run=False):
    out = Path(out)
    day = today_taipei(now)
    if holidays is None:
        try:
            holidays = parse_holidays(fetch_json(HOLIDAYS))
        except Exception:
            holidays = set()          # weekend check still applies
    if not is_trading_day(day, holidays):
        print(f"{day}: not a trading day, skipped")
        return None
    archive = out / "interpret" / f"{day.isoformat()}.json"
    if archive.exists() and not dry_run:
        print(f"{day}: already interpreted, skipped (one call a day)")
        return None

    themes = (load(DOCS / "themes.json") or {}).get("themes", [])
    rules = (load(DOCS / "globalmap.json") or {}).get("rules", [])
    headlines = gather_news(fetch_news)
    ctx, fired = build_context(day, load(out / "globals.json"), rules, themes, load(out / "financials.json"), headlines)
    body = request_body(ctx)
    if dry_run:
        print(json.dumps(body, ensure_ascii=False, indent=1))
        return None

    resp = call_claude(body, access_token(post, env), post)
    refs = {h["ref"] for h in ctx["headlines"]} | {g["ref"] for g in ctx["indicators"]} | {r["ref"] for r in ctx["rules_fired"]}
    doc = clean(tool_input(resp), {t["name"] for t in themes}, refs, fired)
    usage = resp.get("usage") or {}
    doc.update({
        "date": day.isoformat(),
        "generated": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "model": resp.get("model") or MODEL,
        "usage": {"input": usage.get("input_tokens"), "output": usage.get("output_tokens")},
        "news": [{"id": h["id"], "t": h["t"], "p": h["p"], "l": h["l"]} for h in headlines],
        "indicators": {g["ref"]: {"name": g["name"], "pct": g["pct"]} for g in ctx["indicators"]},
        "rules": {r["ref"]: {"targets": r["targets"], "dir": r["dir"]} for r in ctx["rules_fired"]},
    })
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(text, encoding="utf-8")
    (out / "interpret.json").write_text(text, encoding="utf-8")
    print(f"{day}: {len(doc['items'])} items ({len(doc['dropped'])} dropped), "
          f"tokens in {doc['usage']['input']} / out {doc['usage']['output']}")
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true", help="print the request body; no API call")
    a = ap.parse_args(argv)
    try:
        run(a.out, dry_run=a.dry_run)
    except urllib.error.HTTPError as e:
        # The API explains a 400 in the body (bad request, credit balance, ...); a 401 from the token
        # exchange is deliberately opaque, its reason is in the Console's workload identity history.
        body = e.read().decode("utf-8", "replace")[:800]
        sys.exit(f"HTTP {e.code} from {e.url}: {body}")


if __name__ == "__main__":
    main()
