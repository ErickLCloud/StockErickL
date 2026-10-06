"""Daily Markdown report: market / candidates / holdings / news."""
import re
from pathlib import Path

from src.data import fundamental

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "output" / "daily-report"
DASH = "—"

MARKET_PLACEHOLDER = "_（大盤概況：待 R3 由 pilot 填入）_"

# Candidate interface for W4 (wired by pilot in R3). Each candidate is a dict:
#   {"code": str, "name": str|None, "kind": "ETF"|"STOCK", "close": float|None,
#    "score": float|None, "reasons": list[str]}
FAKE_CANDIDATES = [
    {"code": "0050", "name": "元大台灣50", "kind": "ETF", "close": 116.45, "score": 3.0,
     "reasons": ["(假資料) 站上月線"]},
    {"code": "2330", "name": "台積電", "kind": "STOCK", "close": 1000.0, "score": 2.0,
     "reasons": ["(假資料) 量增"]},
]


def f2(x):
    return DASH if x is None else f"{round(float(x), 2):.2f}"


def _fund_cells(code, kind, db_path=None):
    if kind == "ETF":
        return DASH, DASH, DASH
    f = fundamental.get_latest(code, db_path)
    if f is None:
        return DASH, DASH, DASH
    return f2(f["pe_ratio"]), f2(f["pb_ratio"]), f2(f["dividend_yield"])


def _signed(x, suffix=""):
    if x is None:
        return DASH
    v = round(float(x), 2)
    return f"{v:+.2f}{suffix}"


def section_market(market):
    """market: None (placeholder), a pre-formatted str, or a dict
    {name, close, change, change_pct}."""
    lines = ["## 一、大盤概況", ""]
    if not market:
        body = MARKET_PLACEHOLDER
    elif isinstance(market, str):
        body = market
    else:
        name = market.get("name") or "加權指數"
        chg, pct = market.get("change"), market.get("change_pct")
        body = (f"{name}　{f2(market.get('close'))}　{_signed(chg)}"
                f"（{_signed(pct, '%')}）")
    return "\n".join(lines + [body, ""])


def section_candidates(candidates, db_path=None):
    lines = ["## 二、篩選候選名單", ""]
    if not candidates:
        return "\n".join(lines + ["無候選標的。", ""])
    lines += ["| 代號 | 名稱 | 收盤 | 分數 | PE | PB | 殖利率% | 理由 |", "|---|---|---|---|---|---|---|---|"]
    for c in candidates:
        pe, pb, dy = _fund_cells(c["code"], c.get("kind"), db_path)
        lines.append(f"| {c['code']} | {c.get('name') or DASH} | {f2(c.get('close'))} | {f2(c.get('score'))} "
                     f"| {pe} | {pb} | {dy} | {'; '.join(c.get('reasons') or []) or DASH} |")
    return "\n".join(lines + [""])


def section_holdings(rows):
    lines = ["## 三、持倉報酬率", ""]
    if rows is None:
        return "\n".join(lines + ["無持倉資料", ""])
    if not rows:
        return "\n".join(lines + ["持倉檔為空", ""])
    lines += ["| 代號 | 股數 | 成本 | 最新收盤 | 未實現損益 | 報酬率% | 持有天數 | 年化% |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['code']} | {r['shares']:g} | {f2(r['cost_price'])} | {f2(r['last_close'])} "
                     f"| {f2(r['pnl'])} | {f2(r['return_pct'])} | {DASH if r['days'] is None else r['days']} "
                     f"| {f2(r['annualized_pct'])} |")
    return "\n".join(lines + [""])


def section_news(items, limit=10):
    lines = ["## 四、新聞摘要", ""]
    if not items:
        return "\n".join(lines + ["無新聞資料", ""])
    for i in items[:limit]:
        pub = f"（{i['publisher']}）" if i.get("publisher") else ""
        lines.append(f"- [{i['title']}]({i['link']}){pub} {i['published_at']}")
    return "\n".join(lines + [""])


def render_report(date, market=None, candidates=None, holdings_rows=None, news_items=None, db_path=None):
    cands = FAKE_CANDIDATES if candidates is None else candidates
    return "\n".join([
        f"# 每日報告 {date}", "",
        section_market(market),
        section_candidates(cands, db_path),
        section_holdings(holdings_rows),
        section_news(news_items or []),
    ])


def next_path(date, out_dir=REPORT_DIR):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pat = re.compile(rf"daily-report_{re.escape(date)}_v(\d+)\.md$")
    vs = [int(m.group(1)) for p in out_dir.iterdir() if (m := pat.match(p.name))]
    return out_dir / f"daily-report_{date}_v{max(vs or [0]) + 1}.md"


def write_report(date, text, out_dir=REPORT_DIR):
    p = next_path(date, out_dir)
    p.write_text(text, encoding="utf-8")
    return p
