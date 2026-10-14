"""獲利趨勢: quarterly revenue, margins and EPS for the stock detail.

Source: MOPS 綜合損益表彙總 (t163sb04), one market and one quarter per request, HTML. Every
figure is year-to-date (累計), so a single quarter is this quarter's YTD minus the previous
quarter's (Q1 is its own). Industries use different layouts (one table each; banks and
insurers have no 營業收入 / 毛利), so columns are found by name.

Season snapshots are kept like the financials ones (data branch):
  <dir>/<YYYY>Q<n>.json   {code: [rev, gross, op, net_parent, eps]}  YTD, 千元 and 元
Missing seasons are fetched once; the newest two are re-fetched every run to pick up late
filers. <out>/earnings.json holds the last 8 single quarters per code for the page.
"""
import html as htmllib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

QUERY = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
MARKETS = ("sii", "otc")
N_QUARTERS = 8

REV = ("營業收入", "收益", "淨收益", "收入")
GROSS = ("營業毛利（毛損）淨額", "營業毛利（毛損）")
OP = ("營業利益（損失）", "營業利益")
EPS = ("基本每股盈餘（元）",)


def fetch_season(market, year, season, timeout=120):
    from src.http import make_context
    body = urllib.parse.urlencode({"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y",
                                   "TYPEK": market, "year": str(year - 1911), "season": f"{season:02d}"}).encode()
    req = urllib.request.Request(QUERY, data=body, headers={"User-Agent": "Mozilla/5.0",
                                                            "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout, context=make_context()) as r:
        return r.read().decode("utf-8", "replace")


def _clean(cell):
    return re.sub(r"\s+", "", htmllib.unescape(re.sub(r"<[^>]+>", "", cell)))


def _num(s):
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _col(hdr, names):
    for n in names:
        if n in hdr:
            return hdr.index(n)
    return None


def parse_season(page):
    """t163sb04 HTML -> {code: [rev, gross, op, net_parent, eps]} (YTD; None where the
    industry's layout has no such line)."""
    out = {}
    for table in re.findall(r"<table[^>]*>(.*?)</table>", page, re.S):
        hdr = [_clean(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
        if "公司代號" not in hdr:
            continue
        net = next((i for i, h in enumerate(hdr) if h.startswith("淨利") and "母公司業主" in h), None)
        idx = [_col(hdr, REV), _col(hdr, GROSS), _col(hdr, OP), net, _col(hdr, EPS)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
            cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < len(hdr) or not re.fullmatch(r"\d{4,6}", cells[0]):
                continue
            out[cells[0]] = [None if i is None else _num(cells[i]) for i in idx]
    return out


def seasons_back(year, season, n):
    """[(year, season)] for the n quarters ending at (year, season), oldest first."""
    k = year * 4 + season - 1
    return [(j // 4, j % 4 + 1) for j in range(k - n + 1, k + 1)]


def latest_due(today):
    """The newest quarter whose reports can exist on `today` (Q1 by 5/15, Q2 by 8/14,
    Q3 by 11/14, Q4 by 3/31; early filers come earlier, so the quarter just ended counts
    once a month has passed)."""
    y, m = today.year, today.month
    q = (m - 1) // 3                    # quarters fully ended this year
    if q == 0:
        return y - 1, 4
    return y, q


def update(store, today, fetch=fetch_season, sleep=2.0):
    """Fetch missing seasons among the 9 needed for 8 single quarters, plus the newest two
    again. A season with no rows yet (not published) is not written."""
    store = Path(store)
    ly, lq = latest_due(today)
    # 8 shown + 1 base for the first subtraction + the newest, which may not be filed yet
    need = seasons_back(ly, lq, N_QUARTERS + 2)
    done = []
    for y, s in need:
        p = store / f"{y}Q{s}.json"
        if p.exists() and (y, s) not in need[-2:]:
            continue
        merged = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        got = 0
        for market in MARKETS:
            # MOPS sometimes answers a burst with an error page: retry, except for the
            # newest quarter, where an empty answer usually just means not filed yet
            for attempt in range(1 if (y, s) == (ly, lq) else 3):
                if attempt and sleep:
                    time.sleep(sleep * 5 * attempt)
                try:
                    rows = parse_season(fetch(market, y, s))
                except Exception as e:
                    rows = {}
                    print(f"earnings: {y}Q{s} {market} attempt {attempt + 1}: {type(e).__name__}", flush=True)
                if rows:
                    break
            merged.update(rows)
            got += len(rows)
            if sleep:
                time.sleep(sleep)
        if got:
            store.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            done.append(f"{y}Q{s}")
    return done


def single_quarters(cum):
    """{(y, s): [rev, gross, op, net, eps] YTD} -> same keys, single-quarter values."""
    out = {}
    for (y, s), v in cum.items():
        if s == 1:
            out[(y, s)] = v
            continue
        prev = cum.get((y, s - 1))
        out[(y, s)] = [None if a is None or prev is None or prev[i] is None else round(a - prev[i], 2)
                       for i, a in enumerate(v)]
    return out


def build_earnings(store, out, stamp=None):
    """Last N_QUARTERS single quarters per code -> <out>/earnings.json:
    {"q": ["2024Q3", ...], "items": {code: {"rev": [億], "gm": [%], "om": [%], "nm": [%], "eps": [元]}}}."""
    store = Path(store)
    files = sorted(store.glob("*Q*.json"), key=lambda p: (int(p.stem[:4]), int(p.stem[-1])))
    snaps = {(int(p.stem[:4]), int(p.stem[-1])): json.loads(p.read_text(encoding="utf-8")) for p in files}
    keys = sorted(snaps)
    # The newest quarter fills up over ~6 weeks (early filers first). Until half the
    # companies of the quarter before have filed, it would be a mostly empty column.
    if len(keys) >= 2 and len(snaps[keys[-1]]) < 0.5 * len(snaps[keys[-2]]):
        keys = keys[:-1]
    codes = set().union(*[snaps[k].keys() for k in keys]) if keys else set()
    # a quarter can be shown only if it is Q1 or its previous quarter is on file
    show = [k for k in keys if k[1] == 1 or (k[0], k[1] - 1) in snaps][-N_QUARTERS:]
    items = {}
    for c in sorted(codes):
        cum = {k: snaps[k][c] for k in keys if c in snaps[k]}
        if not any(v[1] is not None for v in cum.values()):
            # banks, insurers, brokers: no 營業毛利 line, and their 收益 lines are not
            # comparable revenue (single quarters even go negative) -> EPS only
            cum = {k: [None, None, None, None, v[4]] for k, v in cum.items()}
        sq = single_quarters(cum)
        rev, gm, om, nm, eps = [], [], [], [], []
        for k in show:
            v = sq.get(k)
            r = v[0] if v else None
            pct = (lambda x: None if x is None or not r else round(x / r * 100, 2))
            rev.append(None if r is None else round(r / 1e5, 2))     # 千元 -> 億
            gm.append(pct(v[1]) if v else None)
            om.append(pct(v[2]) if v else None)
            nm.append(pct(v[3]) if v else None)
            eps.append(v[4] if v else None)
        if any(x is not None for x in eps + rev):
            items[c] = {"rev": rev, "gm": gm, "om": om, "nm": nm, "eps": eps}
    doc = {"updated": stamp, "q": [f"{y}Q{s}" for y, s in show], "items": items}
    Path(out, "earnings.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return doc
