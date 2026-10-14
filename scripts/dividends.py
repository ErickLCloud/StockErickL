"""股利: dividend history per stock, from the exchanges' ex-rights/ex-dividend result tables.

  TWSE TWT49U   any date range; gives 權值+息值 combined and 權/息/權息. For 息 the value is
                cash per share; for 權 and 權息 the cash part is not separated, so cash is
                left None there and the combined value is kept.
  TPEx exDailyQ any date range; gives 現金股利 and 每仟股無償配股 separately.

Output <out>/dividends.json: {"from", "to", "items": {code: [[iso date, cash, stock_per_1000, total], ...]}}
oldest first. Yield and per-year sums are worked out on the page against the live price.
"""
import json
import re
from datetime import date
from pathlib import Path

TWSE = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={a}&endDate={b}&response=json"
TPEX = "https://www.tpex.org.tw/web/stock/exright/dailyquo/exDailyQ_result.php?l=zh-tw&d={a}&ed={b}&o=json"
YEARS = 5


def _f(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def _roc_iso(s):
    m = re.match(r"(\d{2,3})\D(\d{1,2})\D(\d{1,2})", str(s).strip())
    return f"{int(m.group(1)) + 1911}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _roc(d):
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def parse_twse(doc):
    """TWT49U rows -> [(code, date, cash, stock, total)]. 息: cash = total. 權 / 權息: cash None."""
    if not doc or doc.get("stat") != "OK":
        return []
    f = doc["fields"]
    i_d, i_c, i_v, i_k = f.index("資料日期"), f.index("股票代號"), f.index("權值+息值"), f.index("權/息")
    out = []
    for r in doc.get("data") or []:
        total, kind = _f(r[i_v]), str(r[i_k]).strip()
        out.append((r[i_c].strip(), _roc_iso(r[i_d]), total if kind == "息" else None, None, total))
    return out


def parse_tpex(doc):
    t = ((doc or {}).get("tables") or [{}])[0]
    f, rows = t.get("fields") or [], t.get("data") or []
    if not rows:
        return []
    i_d, i_c, i_v = f.index("除權息日期"), f.index("代號"), f.index("權值+息值")
    i_cash, i_stk = f.index("現金股利"), f.index("每仟股無償配股")
    return [(r[i_c].strip(), _roc_iso(r[i_d]), _f(r[i_cash]), _f(r[i_stk]) or None, _f(r[i_v])) for r in rows]


def build_dividends(out, today=None, fetch=None, stamp=None):
    from src.http import fetch_json
    fetch = fetch or fetch_json
    today = today or date.today()
    start = date(today.year - YEARS, 1, 1)
    rows, errors = [], []
    for name, url, parse in (("twse", TWSE.format(a=start.strftime("%Y%m%d"), b=today.strftime("%Y%m%d")), parse_twse),
                             ("tpex", TPEX.format(a=_roc(start), b=_roc(today)), parse_tpex)):
        try:
            got = parse(fetch(url))
            rows += got
            if not got:
                errors.append(name + ": empty")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
    if not rows:
        raise RuntimeError("股利: no rows from either market: " + "; ".join(errors))
    items = {}
    for c, d, cash, stock, total in sorted(rows, key=lambda r: (r[0], r[1] or "")):
        if d and re.fullmatch(r"\d{4}", c):
            items.setdefault(c, []).append([d, cash, stock, total])
    doc = {"updated": stamp, "from": start.isoformat(), "to": today.isoformat(), "items": items, "errors": errors}
    Path(out, "dividends.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return doc
