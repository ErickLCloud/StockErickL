"""Generate the data the dashboard reads. Full market: ~2,400 listed symbols.

Why static JSON: none of the TWSE/TPEx endpoints send CORS headers (verified
2026-10-06), so a page on github.io cannot call them. Fetching happens
server-side in a GitHub Action; the page reads same-origin files.

    python scripts/build_web_data.py quotes   [--out DIR]
        index + fundamentals + intraday quotes + TAIEX. STANDARD LIBRARY ONLY:
        the 5-minute job runs on a bare runner with no pip install.

    python scripts/build_web_data.py history  [--out DIR]
        2y of history for every symbol (yfinance), indicators and per-symbol
        chart files. Needs pandas/yfinance. Slow (~9 min for 2,393 symbols).

    python scripts/build_web_data.py all      [--out DIR]    both

pandas/yfinance are imported lazily inside build_history so `quotes` never
needs them. A top-level import once made every intraday run fail with
ModuleNotFoundError.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.http import fetch_json  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "data"
# Two years, not one: the holdings form fills the cost price from the close on
# the buy date, so any buy inside the history window can be priced. The chart
# only ever shows up to the last 250 of these.
HISTORY_DAYS = 500
MIS_BATCH = 100          # 200 is rejected with rtcode=9999
YF_BATCH = 100
MIN_HISTORY_OK = 0.90    # refuse to publish a history build that lost >10%

TWSE_DAY = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAY = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
TWSE_PE = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_PE = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
TWSE_INDEX = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
MIS_URL = ("https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
           "?json=1&delay=0&ex_ch=")

# Taiwan has no DST, so a fixed +08:00 is exact. Using the machine's local
# zone would label a UTC runner's clock as Taipei time.
TAIPEI = timezone(timedelta(hours=8))


# ------------------------------------------------------------------ parsing

def num(v, nd=2):
    """'1,234.50' -> 1234.5 ; '', '--', '---', 'X', None -> None."""
    if v is None:
        return None
    t = str(v).replace(",", "").replace("+", "").strip()
    if t in ("", "-", "--", "---", "X", "x"):
        return None
    try:
        f = float(t)
    except ValueError:
        return None
    if f != f:
        return None
    return round(f, nd)


def kind_of(code):
    """Imprecise by construction: '00*' also covers bond ETFs. The free
    endpoints expose no product-type field."""
    return "ETF" if code.startswith("00") else "STOCK"


def stamp():
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def parse_universe(twse_rows, tpex_rows):
    """Merge the two daily snapshots into one list, de-duplicated by code.

    Each item: c code, n name, b board, k kind, pc last close, ch last change.
    """
    out, seen = [], set()
    for board, rows, f_code, f_name, f_close, f_chg in (
        ("TWSE", twse_rows, "Code", "Name", "ClosingPrice", "Change"),
        ("TPEX", tpex_rows, "SecuritiesCompanyCode", "CompanyName", "Close", "Change"),
    ):
        for r in rows:
            code = (r.get(f_code) or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({"c": code, "n": (r.get(f_name) or "").strip(), "b": board,
                        "k": kind_of(code), "pc": num(r.get(f_close)),
                        "ch": num(r.get(f_chg))})
    out.sort(key=lambda i: i["c"])
    return out


def fetch_universe(fetch=None):
    fetch = fetch or fetch_json     # resolved per call so tests can substitute it
    return parse_universe(fetch(TWSE_DAY), fetch(TPEX_DAY))


def parse_fundamentals(twse_rows, tpex_rows):
    """code -> [pe, dividend_yield_pct, pb]. Missing values stay None, never 0."""
    out = {}
    for r in twse_rows:
        code = (r.get("Code") or "").strip()
        if code:
            out[code] = [num(r.get("PEratio")), num(r.get("DividendYield")),
                         num(r.get("PBratio"))]
    for r in tpex_rows:
        code = (r.get("SecuritiesCompanyCode") or r.get("Code") or "").strip()
        if code and code not in out:
            out[code] = [num(r.get("PriceEarningRatio") or r.get("PEratio")),
                         num(r.get("YieldRatio") or r.get("DividendYield")),
                         num(r.get("PriceBookRatio") or r.get("PBratio"))]
    return out


# ----------------------------------------------------------------- intraday

def ex_ch(code, board):
    return f"{'tse' if board == 'TWSE' else 'otc'}_{code}.tw"


def parse_mis(messages):
    """MIS msgArray -> {code: quote}. z is the last trade ('-' before the
    first trade), y the previous close, v the cumulative volume in lots."""
    out = {}
    for m in messages:
        code = m.get("c")
        if not code:
            continue
        last, prev = num(m.get("z")), num(m.get("y"))
        chg = pct = None
        if last is not None and prev not in (None, 0):
            chg = round(last - prev, 2)
            pct = round((last / prev - 1) * 100, 2)
        out[code] = {"last": last, "prev": prev, "chg": chg, "pct": pct,
                     "open": num(m.get("o")), "high": num(m.get("h")),
                     "low": num(m.get("l")), "vol": num(m.get("v"), 0),
                     "time": m.get("t") or m.get("ot")}
    return out


def fetch_intraday(universe, fetch=None, sleep=1.0):
    """Every symbol, MIS_BATCH at a time. A failed batch is retried once and
    then reported; the remaining batches still run."""
    fetch = fetch or fetch_json
    out, failed = {}, []
    chans = [ex_ch(i["c"], i["b"]) for i in universe]
    for n, i in enumerate(range(0, len(chans), MIS_BATCH)):
        url = MIS_URL + "|".join(chans[i:i + MIS_BATCH])
        data = None
        for attempt in (1, 2):
            try:
                data = fetch(url)
                if str(data.get("rtcode")) == "0000":
                    break
                data = None
            except Exception:
                data = None
            time.sleep(3 if attempt == 1 else 0)
        if data is None:
            failed.append(f"batch {n}")
        else:
            out.update(parse_mis(data.get("msgArray", [])))
        if i + MIS_BATCH < len(chans):
            time.sleep(sleep)
    return out, failed


def quote_item(live, base):
    """One compact quotes.json entry. Falls back to the last official close
    when MIS has no trade for the symbol (suspended, or not yet traded today).
    None-valued keys are dropped to keep the file small."""
    live = live or {}
    last = live.get("last")
    q = {"live": last is not None}
    if last is not None:
        q.update({k: live.get(k) for k in
                  ("last", "prev", "chg", "pct", "open", "high", "low", "vol", "time")})
    else:
        pc, ch = base.get("pc"), base.get("ch")
        q["last"] = pc
        q["chg"] = ch
        if pc is not None and ch is not None and pc - ch > 0:
            q["pct"] = round(ch / (pc - ch) * 100, 2)
    return {k: v for k, v in q.items() if v is not None and v is not False or k == "live"}


def build_market(fetch=None):
    """TAIEX. The exact-name match (the payload also carries the total-return
    index at ~2.3x the level) lives in src.market and is tested there; reuse it
    rather than keeping a second copy."""
    from src.market import parse_market_summary
    fetch = fetch or fetch_json
    try:
        m = parse_market_summary(fetch(TWSE_INDEX))
    except Exception as exc:
        return {"updated": stamp(), "error": str(exc)[:120]}
    if m is None:
        return {"updated": stamp(), "error": "TAIEX row absent"}
    return {"updated": stamp(), "name": m["name"], "close": m["close"],
            "chg": m["change"], "pct": m["change_pct"]}


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")


def build_quotes(out):
    out = Path(out)
    uni = fetch_universe()
    try:
        fund = parse_fundamentals(fetch_json(TWSE_PE), _try(TPEX_PE))
    except Exception:
        fund = {}
    live, failed = fetch_intraday(uni)
    items = {i["c"]: quote_item(live.get(i["c"]), i) for i in uni}

    write_json(out / "index.json", {"updated": stamp(),
                                    "items": [{k: i[k] for k in ("c", "n", "b", "k")}
                                              for i in uni]})
    write_json(out / "fundamental.json", {"updated": stamp(), "items": fund})
    write_json(out / "quotes.json", {
        "updated": stamp(),
        "live_count": sum(1 for q in items.values() if q.get("live")),
        "failed_batches": failed, "items": items})
    market = build_market()
    write_json(out / "market.json", market)
    return {"symbols": len(uni), "live": sum(1 for q in items.values() if q.get("live")),
            "failed_batches": len(failed), "fundamentals": len(fund),
            "market_ok": "error" not in market}


def _try(url):
    try:
        return fetch_json(url)
    except Exception:
        return []


# ------------------------------------------------------------------ history

def yf_symbol(item):
    return item["c"] + (".TW" if item["b"] == "TWSE" else ".TWO")


def frame_for(close_df, open_df, high_df, low_df, vol_df, sym, div_df=None):
    """One symbol's OHLCV (+ cash dividend per share) as a DataFrame, rows with
    no close dropped. `div` is 0.0 on every day except an ex-dividend date."""
    import pandas as pd
    cols = {"date": close_df.index.strftime("%Y-%m-%d"),
            "open": open_df[sym].values, "high": high_df[sym].values,
            "low": low_df[sym].values, "close": close_df[sym].values,
            "volume": vol_df[sym].values}
    cols["div"] = (div_df[sym].fillna(0.0).values
                   if div_df is not None and sym in div_df.columns else 0.0)
    return pd.DataFrame(cols).dropna(subset=["close"]).reset_index(drop=True)


# What the SERVER still computes: only what needs more than closing prices.
#   k, d       KD needs high and low
#   vol_ratio  and avg_lots need volume
# The page holds only closes, so it cannot compute these, and the server holds
# the same closes, so it must NOT compute MA / RSI / MACD: those have exactly
# one implementation, docs/analysis.js, which the page runs on the same closes.
# (compute_indicators also returns *_prev values and two non-numeric fields,
# rows: int and date: str, which a blanket float() conversion chokes on.)
WEB_NUMERIC = ("k", "d", "vol_ratio")


def web_indicators(raw):
    out = {}
    for k in WEB_NUMERIC:
        v = raw.get(k)
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = None
        out[k] = None if f is None or f != f else round(f, 2)
    # 20-day average daily volume in LOTS (1 lot = 1,000 shares; yfinance gives
    # shares). The stock screener's liquidity rule uses this, not today's
    # cumulative volume: that figure is tiny in the first minutes of the
    # session, which made a scan right after the open reject nearly everything.
    vma = raw.get("vol_ma20")
    try:
        out["avg_lots"] = None if vma is None or vma != vma else int(round(float(vma) / 1000))
    except (TypeError, ValueError):
        out["avg_lots"] = None
    out["rows"] = int(raw["rows"]) if raw.get("rows") is not None else None
    out["date"] = str(raw["date"]) if raw.get("date") is not None else None
    return out


def _num(x, nd):
    """float rounded to nd places; None for NaN/None so JSON stays valid."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return int(round(x)) if nd == 0 else round(x, nd)


def process_symbol(df):
    """Cut at the last price discontinuity, then compute indicators on what is
    left. Returns (indicators, history, gap) or None when there is no data."""
    from src.analysis.indicators import compute_indicators
    from src.analysis.segment import last_regime

    if df is None or df.empty:
        return None
    start, gap = last_regime(df["date"].tolist(), df["close"].tolist())
    seg = df.iloc[start:].reset_index(drop=True)
    ind = web_indicators(compute_indicators(seg))
    tail = seg.tail(HISTORY_DAYS)
    hist = {"d": tail["date"].tolist(),
            "p": [round(float(x), 2) for x in tail["close"]]}
    # Open / high / low / volume (in lots of 1,000 shares) so the page can draw
    # candles and show a day's full figures. null where Yahoo has no value; the
    # page then falls back to the close for that day.
    hist["o"], hist["h"], hist["l"] = ([_num(x, 2) for x in tail[k]] for k in ("open", "high", "low"))
    hist["v"] = [_num(x / 1000, 0) if x == x and x is not None else None for x in tail["volume"]]
    # Cash dividends by ex-date, inside the same window as the prices (so also
    # only after any price gap). Yahoo states them in the same share units as
    # its adjusted prices: 0050's 2025-01 dividend is 0.675 here but 2.70 in
    # TWSE's table because of the 1-for-4 split since; every other row checked
    # (2330, 00878, 2317, 1101) matches TWSE exactly.
    if "div" in tail.columns:
        divs = [[d, round(float(x), 4)] for d, x in zip(tail["date"], tail["div"]) if x and x > 0]
        if divs:
            hist["div"] = divs
    return ind, hist, gap


def build_closes(histories):
    """Every symbol's closes on ONE shared date axis (None where a symbol did
    not trade or had not listed yet). The page loads this once to run the
    scan's first pass and the market-wide validation with the same
    analysis.js code that analyses a single symbol."""
    dates = sorted({d for h in histories.values() for d in h["d"]})
    where = {d: i for i, d in enumerate(dates)}
    items = {}
    for code, h in histories.items():
        row = [None] * len(dates)
        for d, p in zip(h["d"], h["p"]):
            row[where[d]] = p
        items[code] = row
    return {"d": dates, "items": items}


def build_history(out):
    import yfinance as yf

    out = Path(out)
    uni = fetch_universe()
    indicators, histories, gaps, skipped = {}, {}, {}, []

    for i in range(0, len(uni), YF_BATCH):
        chunk = uni[i:i + YF_BATCH]
        syms = [yf_symbol(c) for c in chunk]
        try:
            df = yf.download(syms, period="2y", interval="1d", auto_adjust=False,
                             progress=False, threads=False,   # threads=True drops tickers silently
                             actions=True)                    # adds the Dividends column
            o, h, l, c, v = (df[k] for k in ("Open", "High", "Low", "Close", "Volume"))
            dv = df["Dividends"] if "Dividends" in df.columns.get_level_values(0) else None
        except Exception as exc:
            print(f"  batch {i // YF_BATCH}: {type(exc).__name__}: {str(exc)[:60]}")
            skipped += [x["c"] for x in chunk]
            continue
        for item, sym in zip(chunk, syms):
            if sym not in c.columns:
                skipped.append(item["c"])
                continue
            res = process_symbol(frame_for(c, o, h, l, v, sym, dv))
            if res is None:
                skipped.append(item["c"])
                continue
            indicators[item["c"]], histories[item["c"]], gap = res
            if gap:
                gaps[item["c"]] = gap
                indicators[item["c"]]["gap"] = gap
        print(f"  history batch {i // YF_BATCH + 1}/{-(-len(uni) // YF_BATCH)}: "
              f"ok={len(histories)} skipped={len(skipped)}", flush=True)

    ok_ratio = len(histories) / len(uni) if uni else 0
    if ok_ratio < MIN_HISTORY_OK:
        # Do not overwrite last good data with a partial build.
        raise SystemExit(f"history build kept only {len(histories)}/{len(uni)} symbols "
                         f"({ok_ratio:.0%} < {MIN_HISTORY_OK:.0%}); refusing to publish")

    for code, h in histories.items():
        write_json(out / "history" / f"{code}.json", {"c": code, **h})
    write_json(out / "closes.json", build_closes(histories))
    write_json(out / "indicators.json", {"updated": stamp(), "items": indicators})
    write_json(out / "meta.json", {"history_updated": stamp(), "symbols": len(histories),
                                   "skipped": skipped, "gap_symbols": len(gaps)})
    return {"symbols": len(histories), "skipped": len(skipped), "gaps": len(gaps)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("quotes", "history", "all"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    if args.mode in ("quotes", "all"):
        print("quotes :", build_quotes(args.out))
    if args.mode in ("history", "all"):
        print("history:", build_history(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
