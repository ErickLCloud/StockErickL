"""法說會 (investor conferences) from MOPS, for the stock detail.

There is no open-data feed for these; the MOPS query page t100sb02_1 answers a form POST
with an HTML table, one market (sii = 上市, otc = 上櫃) and one month at a time. Only the
legacy host mopsov.twse.com.tw serves it. Slides are downloadable with a plain GET on the
same host (FileDownLoad, step 9), so the page can link straight to the PDF.

Output <out>/conferences.json:
  {"updated", "months": [...], "items": {code: [{d, t, p, m, zh, en}, ...newest first, at most 4]}}
d = ISO date, t = time, p = place, m = summary, zh / en = slide file names (may be empty).
"""
import html as htmllib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

HOST = "https://mopsov.twse.com.tw"
QUERY = HOST + "/mops/web/ajax_t100sb02_1"
SLIDE = HOST + "/server-java/FileDownLoad?" + "step=9&filePath=%2Fhome%2Fhtml%2Fnas%2FSTR%2F&functionName=t100sb02_1&fileName="
MARKETS = ("sii", "otc")
PER_CODE = 4


def months_around(today, back=2, ahead=1):
    """[(roc_year, month)] from `back` months before to `ahead` after `today`."""
    out = []
    for k in range(-back, ahead + 1):
        m0 = today.year * 12 + today.month - 1 + k
        y, m = divmod(m0, 12)
        out.append((y - 1911, m + 1))
    return out


def fetch_month(market, roc_year, month, timeout=60):
    from src.http import make_context
    body = urllib.parse.urlencode({"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "TYPEK": market,
                                   "year": str(roc_year), "month": f"{month:02d}", "co_id": ""}).encode()
    req = urllib.request.Request(QUERY, data=body, headers={"User-Agent": "Mozilla/5.0",
                                                            "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout, context=make_context()) as r:
        return r.read().decode("utf-8", "replace")


def _text(cell):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def _file(cell):
    m = re.search(r'fileName\.value="([^"]+)"', cell)
    return m.group(1) if m else ""


def _iso(roc):
    m = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", roc.strip())
    return f"{int(m.group(1)) + 1911}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def parse_month(page):
    """MOPS t100sb02_1 HTML -> [{c, d, t, p, m, zh, en}]. Rows that do not start with a
    stock code (headers, sub-headers) are skipped."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 8:
            continue
        code = _text(cells[0])
        d = _iso(_text(cells[2]))
        if not re.fullmatch(r"\d{4,6}", code) or not d:
            continue
        out.append({"c": code, "d": d, "t": _text(cells[3]), "p": _text(cells[4]), "m": _text(cells[5]),
                    "zh": _file(cells[6]), "en": _file(cells[7])})
    return out


def build_conferences(out, today=None, fetch=fetch_month, sleep=2.0, stamp=None):
    today = today or date.today()
    months = months_around(today)
    rows, errors = [], []
    for market in MARKETS:
        for y, m in months:
            try:
                rows += parse_month(fetch(market, y, m))
            except Exception as e:
                errors.append(f"{market} {y}/{m}: {type(e).__name__}")
            if sleep:
                time.sleep(sleep)
    items = {}
    for r in sorted(rows, key=lambda r: (r["d"], r["t"]), reverse=True):
        lst = items.setdefault(r.pop("c"), [])
        if len(lst) < PER_CODE and r not in lst:
            lst.append(r)
    doc = {"updated": stamp, "months": [f"{y + 1911}-{m:02d}" for y, m in months], "slide": SLIDE,
           "items": items, "errors": errors}
    if not rows and errors:
        raise RuntimeError("法說會: every request failed: " + "; ".join(errors))
    Path(out, "conferences.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return doc
