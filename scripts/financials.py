"""Financial-statement pipeline: monthly revenue, quarterly EPS and gross margin for every
listed (TWSE) and OTC (TPEX) stock, one row per code, plus period snapshots.

The open-data endpoints only ever return the latest period for the whole market, so any trend
has to be accumulated by saving each period's snapshot (save_snapshot / load_snapshots).

Verified 2026-10-13: the quarterly EPS (and 營業收入 / 稅後淨利 in the same rows) is YEAR-TO-DATE
cumulative, not single-quarter. 2330 'Q2 115' EPS 49.33 = H1 net income 1,279,582,227k / ~25.93B
shares, and its income-statement revenue 2.40T is about half of the 3.90T Jan-Sep cumulative
monthly revenue. So eps_basis is "累計".
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_web_data import fetch_json, num, write_json

REV_L = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
REV_O = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
EPS_L = "https://openapi.twse.com.tw/v1/opendata/t187ap14_L"
EPS_O = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap14_O"
IS_L = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
IS_O = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"

EPS_BASIS = "累計"   # see module docstring
# Industries whose statements have no comparable 營業毛利: gross margin stays null.
# (Construction does report 營業毛利; its margin is lumpy, but it is a real number.)
NO_GM_INDUSTRIES = {"金融保險業", "金融業"}


def _code(r):
    return str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()


def _roc_ym(s):
    """'11509' -> '2026-09'. None for anything else."""
    t = str(s or "").strip()
    if not t.isdigit() or len(t) not in (5, 6):
        return None
    y, m = int(t[:-2]) + 1911, int(t[-2:])
    return f"{y:04d}-{m:02d}" if 1 <= m <= 12 else None


def _roc_year(y):
    t = str(y or "").strip()
    return int(t) + 1911 if t.isdigit() else None


def parse_revenue(rows):
    """-> ({code: [rev, mom%, yoy%, cum_yoy%]}, 'YYYY-MM' | None, {code: industry})."""
    out, ym, ind = {}, None, {}
    for r in rows:
        code = _code(r)
        if not code:
            continue
        ym = ym or _roc_ym(r.get("資料年月"))
        out[code] = [num(r.get("營業收入-當月營收"), 0),
                     num(r.get("營業收入-上月比較增減(%)")),
                     num(r.get("營業收入-去年同月增減(%)")),
                     num(r.get("累計營業收入-前期比較增減(%)"))]
        if r.get("產業別"):
            ind[code] = str(r["產業別"]).strip()
    return out, ym, ind


def parse_eps(rows):
    """-> ({code: eps}, 'YYYYQn' | None, {code: industry})."""
    out, period, ind = {}, None, {}
    for r in rows:
        code = _code(r)
        if not code:
            continue
        if period is None:
            y, q = _roc_year(r.get("年度") or r.get("Year")), str(r.get("季別") or "").strip()
            if y and q in ("1", "2", "3", "4"):
                period = f"{y}Q{q}"
        out[code] = num(r.get("基本每股盈餘(元)", r.get("基本每股盈餘")))
        if r.get("產業別"):
            ind[code] = str(r["產業別"]).strip()
    return out, period, ind


def parse_gross_margin(rows):
    """Income-statement rows -> {code: 營業毛利 / 營業收入 in %}; null when either is missing."""
    out = {}
    for r in rows:
        code = _code(r)
        if not code:
            continue
        gp = num(r.get("營業毛利（毛損）淨額"), 6)
        if gp is None:
            gp = num(r.get("營業毛利（毛損）"), 6)
        rev = num(r.get("營業收入"), 6)
        out[code] = round(gp / rev * 100, 2) if gp is not None and rev else None
    return out


def build_financials(out_dir, fetch=None):
    """Fetch everything, write out_dir/financials.json and return the document.
    One failing endpoint is recorded in `errors`; the rest is still produced."""
    fetch = fetch or fetch_json
    errors = []

    def get(name, url):
        try:
            rows = fetch(url)
            if not isinstance(rows, list):
                raise ValueError("not a JSON list")
            return rows
        except Exception as e:  # noqa: BLE001 - any failure is just recorded
            errors.append(f"{name}: {type(e).__name__}: {e}"[:200])
            return []

    rev, rev_month, ind = {}, None, {}
    for name, url in (("rev_L", REV_L), ("rev_O", REV_O)):
        part, ym, i = parse_revenue(get(name, url))
        rev.update(part)
        rev_month = rev_month or ym
        ind.update(i)
    eps, eps_period = {}, None
    for name, url in (("eps_L", EPS_L), ("eps_O", EPS_O)):
        part, p, i = parse_eps(get(name, url))
        eps.update(part)
        eps_period = eps_period or p
        for c, v in i.items():
            ind.setdefault(c, v)
    gm = {}
    for name, url in (("is_L", IS_L), ("is_O", IS_O)):
        gm.update(parse_gross_margin(get(name, url)))

    items = {}
    for code in sorted(set(rev) | set(eps)):
        g = None if ind.get(code) in NO_GM_INDUSTRIES else gm.get(code)
        items[code] = (rev.get(code) or [None] * 4) + [eps.get(code), g]

    doc = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rev_month": rev_month, "eps_period": eps_period, "eps_basis": EPS_BASIS,
           "errors": errors, "items": items}
    write_json(Path(out_dir) / "financials.json", doc)
    return doc


def period_items(doc):
    """Split a financials document into snapshot parts:
    {'YYYY-MM': {code: [rev, mom, yoy, cum_yoy]}, 'YYYYQn': {code: [eps, gm]}}."""
    out = {}
    if doc.get("rev_month"):
        out[doc["rev_month"]] = {c: v[:4] for c, v in doc["items"].items()
                                 if any(x is not None for x in v[:4])}
    if doc.get("eps_period"):
        out[doc["eps_period"]] = {c: v[4:] for c, v in doc["items"].items()
                                  if any(x is not None for x in v[4:])}
    return out


def save_snapshot(items_by_period, snap_dir):
    """Merge each period into snap_dir/<period>.json. Companies report over days (monthly
    revenue by the 10th, quarterly results up to the filing deadline), so the first
    snapshot of a period is always partial: codes not yet in the file are added, codes
    already there keep their first value. Returns the periods whose file changed."""
    snap_dir = Path(snap_dir)
    written = []
    for period, items in (items_by_period or {}).items():
        if not period:
            continue
        path = snap_dir / f"{period}.json"
        old = {}
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))["items"]
            except (OSError, ValueError, KeyError):
                old = {}
        new = {c: v for c, v in (items or {}).items() if c not in old}
        if not new and path.exists():
            continue
        write_json(path, {"period": period, "items": {**old, **new}})
        written.append(period)
    return written


def load_snapshots(snap_dir):
    """-> {period: {code: [...]}} for every readable snapshot in snap_dir (sorted by period)."""
    out = {}
    snap_dir = Path(snap_dir)
    if not snap_dir.is_dir():
        return out
    for p in sorted(snap_dir.glob("*.json")):
        if p.name == "financials.json":
            continue
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))["items"]
        except (OSError, ValueError, KeyError):
            continue
    return out
