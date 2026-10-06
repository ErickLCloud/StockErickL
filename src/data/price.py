"""W1 price/volume data layer: universe, 2y backfill (yfinance), daily increment (TWSE/TPEx).

CLI:  python -m src.data.price universe|backfill|daily|all
"""

import csv
import datetime as dt
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "db" / "stock.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
WATCHLIST_PATH = ROOT / "input" / "watchlist.csv"

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"

# board -> (source tag, field-name map)
FIELDS = {
    "TWSE": {"code": "Code", "name": "Name", "open": "OpeningPrice", "high": "HighestPrice",
             "low": "LowestPrice", "close": "ClosingPrice", "volume": "TradeVolume",
             "turnover": "TradeValue", "date": "Date"},
    "TPEX": {"code": "SecuritiesCompanyCode", "name": "CompanyName", "open": "Open", "high": "High",
             "low": "Low", "close": "Close", "volume": "TradingShares",
             "turnover": "TransactionAmount", "date": "Date"},
}
SOURCE = {"TWSE": "twse", "TPEX": "tpex"}


# ---------- pure helpers ----------

def roc_to_iso(s):
    """'1151005' -> '2026-10-05'. Returns None if unparseable."""
    if s is None:
        return None
    s = str(s).strip()
    if len(s) != 7 or not s.isdigit():
        return None
    try:
        return dt.date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])).isoformat()
    except ValueError:
        return None


def clean_num(v):
    """'' / '--' / None -> None; '1,234.5' -> 1234.5."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "--", "---", "-", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_row(raw, board):
    """Raw TWSE/TPEx record -> price_daily dict, or None if unusable (no code/date/close)."""
    f = FIELDS[board]
    code = (raw.get(f["code"]) or "").strip()
    date = roc_to_iso(raw.get(f["date"]))
    if not code or not date:
        return None
    vol = clean_num(raw.get(f["volume"]))
    row = {
        "code": code, "date": date, "board": board,
        "open": clean_num(raw.get(f["open"])), "high": clean_num(raw.get(f["high"])),
        "low": clean_num(raw.get(f["low"])), "close": clean_num(raw.get(f["close"])),
        "adj_close": None, "volume": int(vol) if vol is not None else None,
        "turnover": clean_num(raw.get(f["turnover"])), "source": SOURCE[board],
    }
    if row["close"] is None:  # suspended / no trade
        return None
    return row


def yf_symbol(code, board):
    return code + (".TW" if board == "TWSE" else ".TWO")


def kind_of(code):
    return "ETF" if code.startswith("00") else "STOCK"


def read_watchlist(path=WATCHLIST_PATH):
    p = Path(path)
    if not p.exists():
        return set()
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return {(r.get("code") or "").strip() for r in csv.DictReader(fh) if (r.get("code") or "").strip()}


# ---------- DB ----------

def connect(path=DB_PATH):
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    return conn


def _count(conn):
    return conn.execute("SELECT COUNT(*) FROM price_daily").fetchone()[0]


_COLS = ("code", "date", "board", "open", "high", "low", "close", "adj_close", "volume", "turnover", "source")


def upsert_prices(conn, rows):
    """Official rows (twse/tpex) overwrite OHLCV but keep an existing adj_close.
    Returns number of NEW rows."""
    before = _count(conn)
    conn.executemany(
        """INSERT INTO price_daily (code,date,board,open,high,low,close,adj_close,volume,turnover,source)
           VALUES (:code,:date,:board,:open,:high,:low,:close,:adj_close,:volume,:turnover,:source)
           ON CONFLICT(code,date) DO UPDATE SET
             open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
             volume=excluded.volume, turnover=excluded.turnover, source=excluded.source,
             adj_close=COALESCE(excluded.adj_close, price_daily.adj_close)""",
        rows)
    conn.commit()
    return _count(conn) - before


def insert_missing_prices(conn, rows):
    """Backfill rows never overwrite existing (official) rows. Returns NEW row count."""
    before = _count(conn)
    conn.executemany(
        """INSERT OR IGNORE INTO price_daily (code,date,board,open,high,low,close,adj_close,volume,turnover,source)
           VALUES (:code,:date,:board,:open,:high,:low,:close,:adj_close,:volume,:turnover,:source)""",
        rows)
    conn.commit()
    return _count(conn) - before


def build_universe(conn, twse_raw, tpex_raw, watchlist=()):
    """Upsert instrument from today's quote lists. ETF (code startswith '00') or watchlist -> in_universe=1."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    wl = set(watchlist)
    recs = []
    for board, raws in (("TWSE", twse_raw), ("TPEX", tpex_raw)):
        f = FIELDS[board]
        for r in raws:
            code = (r.get(f["code"]) or "").strip()
            if not code:
                continue
            kind = kind_of(code)
            recs.append({"code": code, "name": (r.get(f["name"]) or "").strip() or None,
                         "board": board, "kind": kind, "yf": yf_symbol(code, board),
                         "inu": 1 if (kind == "ETF" or code in wl) else 0, "now": now})
    conn.executemany(
        """INSERT INTO instrument (code,name,board,kind,yf_symbol,in_universe,updated_at)
           VALUES (:code,:name,:board,:kind,:yf,:inu,:now)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, board=excluded.board, kind=excluded.kind,
             yf_symbol=excluded.yf_symbol, in_universe=excluded.in_universe, updated_at=excluded.updated_at""",
        recs)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM instrument WHERE in_universe=1").fetchone()[0]


# ---------- yfinance backfill ----------

def _default_download(symbols, period):
    import yfinance as yf
    return yf.download(symbols, period=period, auto_adjust=False, group_by="ticker",
                       threads=False, progress=False)


def _frame_to_rows(df, code, board, symbol):
    """Extract one ticker's rows from a (possibly multi-ticker) yfinance frame."""
    try:
        sub = df[symbol] if getattr(df.columns, "nlevels", 1) > 1 else df
    except KeyError:
        return []
    sub = sub.dropna(subset=["Close"]) if "Close" in sub.columns else sub.iloc[0:0]
    rows = []
    for idx, r in sub.iterrows():
        vol = r.get("Volume")
        rows.append({
            "code": code, "date": idx.strftime("%Y-%m-%d"), "board": board,
            "open": _f(r.get("Open")), "high": _f(r.get("High")), "low": _f(r.get("Low")),
            "close": _f(r.get("Close")), "adj_close": _f(r.get("Adj Close")),
            "volume": int(vol) if _f(vol) is not None else None,
            "turnover": None, "source": "yfinance"})
    return rows


def _f(v):
    try:
        if v is None or v != v:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def backfill(conn, period="2y", batch_size=40, download=_default_download, log=print):
    """Backfill in_universe instruments. Verifies requested vs. received tickers, retries
    missing ones individually, and returns (new_rows, skipped_codes)."""
    inst = conn.execute("SELECT code, board, yf_symbol FROM instrument WHERE in_universe=1 ORDER BY code").fetchall()
    new_rows, skipped = 0, []

    def fetch(items):
        got, missing = {}, []
        try:
            df = download([s for _, _, s in items], period)
        except Exception as e:  # whole batch failed -> everything is missing
            log(f"batch error: {e}")
            return got, list(items)
        for code, board, sym in items:
            rows = _frame_to_rows(df, code, board, sym) if df is not None and len(df) else []
            if rows:
                got[code] = rows
            else:
                missing.append((code, board, sym))
        return got, missing

    for i in range(0, len(inst), batch_size):
        items = inst[i:i + batch_size]
        got, missing = fetch(items)
        if missing:
            log(f"batch {i // batch_size}: requested {len(items)}, got {len(got)}; retrying {len(missing)}")
        for item in missing:  # single retry per missing ticker
            g, m = fetch([item])
            got.update(g)
            if m:
                skipped.append(item[0])
        for code, rows in got.items():
            new_rows += insert_missing_prices(conn, rows)
    if skipped:
        log(f"skipped (no data): {skipped}")
    return new_rows, skipped


# ---------- daily increment ----------

def daily_update(conn, twse_raw, tpex_raw):
    """Upsert today's official quotes for in_universe instruments. Returns NEW row count."""
    univ = {r[0] for r in conn.execute("SELECT code FROM instrument WHERE in_universe=1")}
    rows = []
    for board, raws in (("TWSE", twse_raw), ("TPEX", tpex_raw)):
        for raw in raws:
            r = normalize_row(raw, board)
            if r and r["code"] in univ:
                rows.append(r)
    return upsert_prices(conn, rows)


def fetch_quotes():
    from src.http import fetch_json
    return fetch_json(TWSE_URL), fetch_json(TPEX_URL)


def main(argv=None):
    cmd = (argv or sys.argv[1:] or ["all"])[0]
    conn = connect()
    twse = tpex = None
    if cmd in ("universe", "daily", "all"):
        twse, tpex = fetch_quotes()
    if cmd in ("universe", "all"):
        print("in_universe =", build_universe(conn, twse, tpex, read_watchlist()))
    if cmd in ("backfill", "all"):
        n, sk = backfill(conn)
        print("backfill new rows:", n, "skipped:", len(sk), sk)
    if cmd in ("daily", "all"):
        print("daily new rows:", daily_update(conn, twse, tpex))


if __name__ == "__main__":
    main()
