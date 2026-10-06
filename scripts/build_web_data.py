"""Generate the static JSON the web page reads.

Why static JSON: none of the TWSE/TPEx endpoints send CORS headers (verified
2026-10-06, all four return no Access-Control-Allow-Origin), so a browser on
github.io cannot call them. The fetching happens server-side in a GitHub
Action, which commits JSON that the page then reads same-origin.

Two modes, deliberately split so the 5-minute job commits a tiny diff:

    quotes  intraday prices + 漲跌, merged onto the stored indicators.
            One small file. Safe to run every 5 minutes.
    full    rebuilds index.json, indicators and per-symbol history too.
            Run once a day after close; it rewrites ~2MB.
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.http import fetch_json                              # noqa: E402

# NOTE: compute_indicators (and therefore pandas) is imported lazily inside
# build_history_and_indicators. The 5-minute `quotes` job runs on a bare
# runner with no pip install, so this module must import with the standard
# library alone. A top-level pandas import here made every intraday run fail
# with ModuleNotFoundError.

DB = ROOT / "db" / "stock.db"
# docs/ not web/: GitHub Pages "Deploy from a branch" only offers
# "/ (root)" or "/docs" as the publishing folder — an arbitrary /web is
# not selectable, and picking root serves README.md instead of the page.
OUT = ROOT / "docs" / "data"
HIST = OUT / "history"
HISTORY_DAYS = 250
MIS_BATCH = 100          # 200 is rejected with rtcode=9999
MIS_URL = ("https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
           "?json=1&delay=0&ex_ch=")


def connect():
    c = sqlite3.connect(str(DB), timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


def universe(conn):
    return conn.execute(
        "SELECT code, name, board, kind FROM instrument "
        "WHERE in_universe=1 ORDER BY code").fetchall()


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 2)


# --------------------------------------------------------------- full rebuild

def build_index(conn):
    """index.json carries the universe AND each symbol's last stored close.

    Including `pc` here is what lets the 5-minute job run with no database:
    a GitHub runner has no stock.db (it is gitignored, 148k rows), so the
    hot job reads this committed file for both the symbol list and the
    fallback close.
    """
    close = dict(conn.execute(
        "SELECT code, close FROM price_daily WHERE date = "
        "(SELECT MAX(date) FROM price_daily)"))
    rows = [{"c": c, "n": n or "", "b": b, "k": k, "pc": _f(close.get(c))}
            for c, n, b, k in universe(conn)]
    _write(OUT / "index.json", {"updated": _stamp(), "items": rows})
    return len(rows)


def build_history_and_indicators(conn):
    """Per-symbol closes for the chart, plus indicator values for the table."""
    import pandas as pd
    from src.analysis.indicators import compute_indicators

    HIST.mkdir(parents=True, exist_ok=True)
    px = pd.read_sql_query(
        "SELECT p.code, p.date, p.open, p.high, p.low, p.close, p.volume "
        "FROM price_daily p JOIN instrument i ON i.code = p.code "
        "WHERE i.in_universe = 1 ORDER BY p.code, p.date", conn)

    indicators, written = {}, 0
    for code, g in px.groupby("code"):
        g = g.drop(columns="code").reset_index(drop=True)
        try:
            indicators[code] = {k: _f(v) for k, v in compute_indicators(g).items()}
        except Exception as exc:                       # one bad symbol, not the batch
            indicators[code] = {"error": type(exc).__name__}

        tail = g.tail(HISTORY_DAYS)
        _write(HIST / f"{code}.json", {
            "c": code,
            "d": [str(x) for x in tail["date"]],
            "o": [_f(x) for x in tail["open"]],
            "h": [_f(x) for x in tail["high"]],
            "l": [_f(x) for x in tail["low"]],
            "p": [_f(x) for x in tail["close"]],
            "v": [None if x is None or x != x else int(x) for x in tail["volume"]],
        })
        written += 1

    _write(OUT / "indicators.json", {"updated": _stamp(), "items": indicators})
    return written


# ------------------------------------------------------------------- intraday

def _ex_ch(code, board):
    return f"{'tse' if board == 'TWSE' else 'otc'}_{code}.tw"


def fetch_intraday(rows, sleep=1.0):
    """MIS snapshot for every symbol, in batches. Missing symbols are skipped,
    never faked; a failed batch is reported and the rest still proceed."""
    out, failed = {}, []
    chans = [_ex_ch(c, b) for c, _, b, _ in rows]
    for i in range(0, len(chans), MIS_BATCH):
        chunk = chans[i:i + MIS_BATCH]
        try:
            data = fetch_json(MIS_URL + "|".join(chunk))
        except Exception as exc:
            failed.append(f"batch@{i}: {type(exc).__name__}")
            continue
        if str(data.get("rtcode")) != "0000":
            failed.append(f"batch@{i}: rtcode={data.get('rtcode')}")
            continue
        for m in data.get("msgArray", []):
            code = m.get("c")
            if not code:
                continue
            last, prev = _f(m.get("z")), _f(m.get("y"))
            chg = pct = None
            if last is not None and prev not in (None, 0):
                chg = round(last - prev, 2)
                pct = round((last / prev - 1) * 100, 2)
            out[code] = {
                "last": last, "prev": prev, "chg": chg, "pct": pct,
                "open": _f(m.get("o")), "high": _f(m.get("h")),
                "low": _f(m.get("l")), "vol": _f(m.get("v")),
                "time": m.get("t") or m.get("ot"),
            }
        if i + MIS_BATCH < len(chans):
            time.sleep(sleep)                          # be polite to the source
    return out, failed


def build_quotes(conn=None):
    """Intraday snapshot. Runs without a database when conn is None, reading
    the committed index.json instead — that is how the 5-minute Action works."""
    if conn is not None:
        rows = universe(conn)
        close = dict(conn.execute(
            "SELECT code, close FROM price_daily WHERE date = "
            "(SELECT MAX(date) FROM price_daily)"))
    else:
        idx = json.loads((OUT / "index.json").read_text(encoding="utf-8"))
        rows = [(i["c"], i.get("n"), i["b"], i.get("k")) for i in idx["items"]]
        close = {i["c"]: i.get("pc") for i in idx["items"]}

    live, failed = fetch_intraday(rows)

    # Deliberately volatile-only: no name/board/kind (index.json) and no
    # indicators (indicators.json). This file is rewritten every 5 minutes,
    # so duplicating static fields here would trash the repo with churn.
    items = {}
    for code, _name, _board, _kind in rows:
        q = live.get(code) or {}
        items[code] = {
            # Fall back to the stored close when intraday is unavailable
            # (outside market hours, or a symbol MIS does not serve).
            "last": q.get("last") if q.get("last") is not None else _f(close.get(code)),
            "live": q.get("last") is not None,
            "chg": q.get("chg"), "pct": q.get("pct"),
            "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
            "vol": q.get("vol"), "time": q.get("time"),
        }
    _write(OUT / "quotes.json", {
        "updated": _stamp(),
        "live_count": sum(1 for v in items.values() if v["live"]),
        "failed_batches": failed,
        "items": items,
    })
    return len(items), len(failed)


def build_market():
    """大盤. Exact name match: the payload also carries 發行量加權股價報酬指數."""
    try:
        rows = fetch_json(
            "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX")
    except Exception as exc:
        _write(OUT / "market.json", {"updated": _stamp(), "error": str(exc)[:120]})
        return False
    for r in rows:
        if r.get("指數", "").strip() != "發行量加權股價指數":
            continue
        pts = _f(str(r.get("漲跌點數", "")).replace(",", ""))
        pct = _f(r.get("漲跌百分比"))
        if str(r.get("漲跌", "")).strip() == "-":
            pts = None if pts is None else -pts
            pct = None if pct is None else -pct
        _write(OUT / "market.json", {
            "updated": _stamp(), "name": "發行量加權股價指數",
            "close": _f(str(r.get("收盤指數", "")).replace(",", "")),
            "chg": pts, "pct": pct,
        })
        return True
    _write(OUT / "market.json", {"updated": _stamp(), "error": "TAIEX row absent"})
    return False


# ---------------------------------------------------------------------- utils

def _stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")


def main(argv=None):
    mode = (argv or sys.argv[1:] or ["full"])[0]
    if mode == "full":
        conn = connect()
        print(f"index.json      : {build_index(conn)} instruments")
        print(f"history + ind   : {build_history_and_indicators(conn)} files")
    elif mode == "quotes":
        conn = None          # DB-free path: universe comes from index.json
    else:
        print(__doc__)
        return 2
    n, failed = build_quotes(conn)
    print(f"quotes.json     : {n} symbols, {failed} failed batches")
    print(f"market.json     : {'ok' if build_market() else 'unavailable'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
