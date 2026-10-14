"""籌碼面: institutional net buying, margin balances, and the TDCC shareholding distribution.

Three sources, all public, all stdlib:
  三大法人   TWSE T86 + TPEx 3itrade, daily, per stock, shares -> stored in 張 (lots)
  融資融券   TWSE MI_MARGN + TPEx margin_bal, daily, per stock, already in 張
  集保分散   TDCC open data 1-5, WEEKLY, latest week only (no history: it is kept by saving
             one file per week from now on)

大戶 / 散戶 come only from the TDCC distribution: 大戶 = holders of 400 張 or more
(levels 12-15), 散戶 = holders of less than 50 張 (levels 1-8). These are holdings, not
trades, and the 1,000 張+ level also contains foreign custodians, ETFs and insiders, so
大戶 overlaps with 外資. The page says so.

Storage (rides on the data branch, like the financials snapshots):
  <dir>/daily/<YYYY-MM-DD>.json   {code: [foreign, trust, dealer, margin_bal, short_bal]}
  <dir>/tdcc/<YYYY-MM-DD>.json    {code: [big_pct, k1000_pct, small_holders, small_pct, holders]}
and the page reads one summary file, <out>/chips.json, built from the latest N of each.
"""
import json
import re
import time
from pathlib import Path

T86 = "https://www.twse.com.tw/rwd/zh/fund/T86?date={ymd}&selectType=ALLBUT0999&response=json"
TPEX_3 = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={roc}&o=json"
TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ymd}&selectType=ALL&response=json"
TPEX_MARGIN = "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d={roc}"
TDCC = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"

N_DAYS = 20          # daily series shown on the page
N_WEEKS = 12         # weekly TDCC series shown on the page
KEEP_DAYS = 90       # daily snapshot files kept on the data branch
BIG_LEVELS = range(12, 16)     # 400 張 and up
SMALL_LEVELS = range(1, 9)     # under 50 張


def _int(s):
    s = str(s if s is not None else "").replace(",", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return None


def lots(shares):
    return None if shares is None else round(shares / 1000)


def roc(day):
    """2026-08-14 -> 115/08/14 (TPEx legacy endpoints)."""
    y, m, d = day.split("-")
    return f"{int(y) - 1911}/{m}/{d}"


# ------------------------------------------------------------------ parsers
def parse_t86(doc):
    """TWSE T86 -> {code: [foreign, trust, dealer]} in 張; None when the day has no data.
    外資 = 外陸資 (excl. foreign dealers) + 外資自營商, the usual "外資" figure."""
    if not doc or doc.get("stat") != "OK" or not doc.get("data"):
        return None
    f = doc["fields"]
    i = {k: f.index(k) for k in ("證券代號", "外陸資買賣超股數(不含外資自營商)", "外資自營商買賣超股數",
                                  "投信買賣超股數", "自營商買賣超股數")}
    out = {}
    for r in doc["data"]:
        fx, fd = _int(r[i["外陸資買賣超股數(不含外資自營商)"]]), _int(r[i["外資自營商買賣超股數"]])
        foreign = None if fx is None else fx + (fd or 0)
        out[r[i["證券代號"]].strip()] = [lots(foreign), lots(_int(r[i["投信買賣超股數"]])), lots(_int(r[i["自營商買賣超股數"]]))]
    return out


def parse_tpex_3(doc):
    """TPEx 3itrade (24 columns; the field names repeat, so by position):
    10 外資及陸資合計 net, 13 投信 net, 22 自營商合計 net."""
    tables = (doc or {}).get("tables") or []
    rows = tables[0].get("data") if tables else None
    if not rows:
        return None
    return {r[0].strip(): [lots(_int(r[10])), lots(_int(r[13])), lots(_int(r[22]))] for r in rows if len(r) >= 23}


def parse_twse_margin(doc):
    """TWSE MI_MARGN -> {code: [margin_bal, short_bal]} in 張 (the per-stock table)."""
    if not doc or doc.get("stat") != "OK":
        return None
    for t in doc.get("tables") or []:
        if "彙總" in (t.get("title") or "") and t.get("data"):
            return {r[0].strip(): [_int(r[6]), _int(r[12])] for r in t["data"] if len(r) >= 13}
    return None


def parse_tpex_margin(doc):
    """TPEx margin_bal: 6 資餘額, 14 券餘額 (張)."""
    tables = (doc or {}).get("tables") or []
    rows = tables[0].get("data") if tables else None
    if not rows:
        return None
    return {r[0].strip(): [_int(r[6]), _int(r[14])] for r in rows if len(r) >= 15}


def parse_tdcc(text):
    """TDCC 1-5 CSV (資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%) ->
    (iso date, {code: [big_pct, k1000_pct, small_holders, small_pct, holders]})."""
    acc, day = {}, None
    for line in text.splitlines()[1:]:
        p = line.split(",")
        if len(p) < 6 or not p[0].strip().isdigit():
            continue
        day = day or p[0].strip()
        code, lv = p[1].strip(), _int(p[2])
        people, pct = _int(p[3]), float(p[5] or 0)
        a = acc.setdefault(code, [0.0, 0.0, 0, 0.0, 0])
        if lv in BIG_LEVELS:
            a[0] += pct
        if lv == 15:
            a[1] = pct
        if lv in SMALL_LEVELS:
            a[2] += people or 0
            a[3] += pct
        if lv == 17:
            a[4] = people or 0
    if not day:
        return None, {}
    iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return iso, {c: [round(a[0], 2), round(a[1], 2), a[2], round(a[3], 2), a[4]] for c, a in acc.items() if a[4]}


# ------------------------------------------------------------------ fetch + store
def fetch_day(day, fetch, sleep=3.0):
    """All four daily sources for one ISO date -> {code: [f, t, d, mb, sb]} or None (no trading).
    TWSE rate-limits bursts, hence the pause between requests."""
    ymd = day.replace("-", "")
    got = {}
    for key, url, parse in (("t86", T86.format(ymd=ymd), parse_t86), ("tp3", TPEX_3.format(roc=roc(day)), parse_tpex_3),
                            ("tm", TWSE_MARGIN.format(ymd=ymd), parse_twse_margin), ("pm", TPEX_MARGIN.format(roc=roc(day)), parse_tpex_margin)):
        try:
            got[key] = parse(fetch(url))
        except Exception:
            got[key] = None
        if sleep:
            time.sleep(sleep)
    if not got["t86"] and not got["tp3"]:
        return None
    out = {}
    for inst in (got["t86"], got["tp3"]):
        for c, v in (inst or {}).items():
            out[c] = v + [None, None]
    for mar in (got["tm"], got["pm"]):
        for c, (mb, sb) in (mar or {}).items():
            out.setdefault(c, [None, None, None, None, None])[3:5] = [mb, sb]
    return out


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def update_daily(store, days, fetch, sleep=3.0, limit=None):
    """Fetch the trading days in `days` (ISO, oldest first) that have no file yet. Returns the
    days written. `limit` caps one run (the first run back-fills ~60 days, ~4 minutes)."""
    d = Path(store) / "daily"

    def incomplete(day):
        # margin balances are published in the evening; a file saved before that has none
        p = d / f"{day}.json"
        if not p.exists():
            return True
        snap = json.loads(p.read_text(encoding="utf-8"))
        return not any(v[3] is not None for v in snap.values())

    todo = [x for x in days if not (d / f"{x}.json").exists()] + [x for x in days[-3:] if incomplete(x) and (d / f"{x}.json").exists()]
    todo = sorted(set(todo))
    if limit is not None:
        todo = todo[-limit:]
    done = []
    for day in todo:
        snap = fetch_day(day, fetch, sleep)
        if snap:
            write(d / f"{day}.json", snap)
            done.append(day)
    for old in sorted(d.glob("*.json"))[:-KEEP_DAYS]:
        old.unlink()
    return done


def update_tdcc(store, fetch_text):
    day, snap = parse_tdcc(fetch_text(TDCC))
    if not day or not snap:
        return None
    p = Path(store) / "tdcc" / f"{day}.json"
    if not p.exists():
        write(p, snap)
    return day


def _series(files, n):
    files = sorted(files)[-n:]
    return [f.stem for f in files], [json.loads(f.read_text(encoding="utf-8")) for f in files]


def build_chips(store, out, stamp=None):
    """Latest N_DAYS daily and N_WEEKS weekly snapshots -> <out>/chips.json, as columns:
    items[code] = {"f","t","d","m","s": one value per day (外資, 投信, 自營, 融資餘額, 融券餘額),
                   "b","k","n","p","h": one value per week (大戶%, 千張%, 散戶人數, 散戶%, 總人數)};
    a missing day or week is null. Only common stocks that trade (in the daily data) are kept."""
    store = Path(store)
    days, dsnaps = _series((store / "daily").glob("*.json"), N_DAYS)
    weeks, wsnaps = _series((store / "tdcc").glob("*.json"), N_WEEKS)
    codes = set().union(*[s.keys() for s in dsnaps]) if dsnaps else set()
    items = {}
    for c in sorted(codes):
        if not re.fullmatch(r"\d{4}", c):          # common stocks only; ETFs and warrants are left out
            continue
        it = {}
        for j, k in enumerate("ftdms"):
            it[k] = [(s.get(c) or [None] * 5)[j] for s in dsnaps]
        for j, k in enumerate("bknph"):
            it[k] = [(s.get(c) or [None] * 5)[j] for s in wsnaps]
        items[c] = it
    doc = {"updated": stamp, "days": days, "weeks": weeks,
           "big": "持股 400 張以上", "small": "持股未滿 50 張", "items": items}
    write(Path(out) / "chips.json", doc)
    return doc
