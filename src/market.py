"""大盤 (TAIEX) summary. PILOT-OWNED — added in R3.

Why this is not in src/data/: price_daily.board only allows TWSE/TPEX and
instrument.kind only ETF/STOCK, so an index row would break both conventions.
Index data lives in its own table.
"""

from src.http import fetch_json

MI_INDEX_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
MI_5MINS_HIST_URL = "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"

# Must be an EXACT match. The same payload also carries
# "發行量加權股價報酬指數" (total-return, ~114,952 vs ~49,712) and
# "加權指數掩護性臺指買權價外5%報酬指數". A substring test on "加權"
# silently picks the wrong one.
TAIEX_NAME = "發行量加權股價指數"

TAIEX_SYMBOL = "^TWII"

__all__ = ["TAIEX_NAME", "TAIEX_SYMBOL", "fetch_market_summary",
           "parse_market_summary", "roc_to_iso", "clean_num",
           "backfill_index", "relative_strength"]


def roc_to_iso(s):
    """'1151005' -> '2026-10-05'. None if unparseable."""
    s = (s or "").strip()
    if len(s) != 7 or not s.isdigit():
        return None
    return f"{int(s[:3]) + 1911:04d}-{s[3:5]}-{s[5:7]}"


def clean_num(v):
    """'' / '--' / None -> None; '1,236.30' -> 1236.3."""
    if v is None:
        return None
    t = str(v).replace(",", "").strip()
    if t in ("", "--", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_market_summary(payload):
    """Pick the TAIEX row out of an MI_INDEX payload.

    Returns {name, date, close, change, change_pct} with `change` already
    signed from the separate 漲跌 column, or None when the row is absent.
    """
    for row in payload:
        if row.get("指數", "").strip() != TAIEX_NAME:
            continue
        pts = clean_num(row.get("漲跌點數"))
        pct = clean_num(row.get("漲跌百分比"))
        # 漲跌 is a standalone sign column; the magnitude carries no sign.
        if str(row.get("漲跌", "")).strip() == "-":
            pts = None if pts is None else -pts
            pct = None if pct is None else -pct
        return {
            "name": TAIEX_NAME,
            "date": roc_to_iso(row.get("日期")),
            "close": clean_num(row.get("收盤指數")),
            "change": pts,
            "change_pct": pct,
        }
    return None


def fetch_market_summary(fetch=fetch_json):
    """Today's TAIEX summary, or None if the source is unavailable.

    Degrades to None rather than raising: the daily report must still render
    when the index source is down.
    """
    try:
        return parse_market_summary(fetch(MI_INDEX_URL))
    except Exception:
        return None


def backfill_index(conn, period="2y"):
    """Store ^TWII history in index_daily. Returns rows inserted.

    yfinance 1.7 returns a DataFrame (not a Series) for df[field] even with a
    single ticker, so each column is squeezed before use.
    """
    import yfinance as yf

    df = yf.download(TAIEX_SYMBOL, period=period, interval="1d",
                     auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        return 0

    def col(name):
        s = df[name]
        return s.squeeze() if hasattr(s, "squeeze") else s

    opens, highs, lows, closes = (col(c) for c in ("Open", "High", "Low", "Close"))
    rows = []
    for ts in df.index:
        close = closes.get(ts)
        if close is None or close != close:  # NaN
            continue
        rows.append((TAIEX_SYMBOL, ts.strftime("%Y-%m-%d"),
                     _num(opens.get(ts)), _num(highs.get(ts)),
                     _num(lows.get(ts)), _num(close), "yfinance"))

    before = conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO index_daily "
        "(symbol, date, open, high, low, close, source) VALUES (?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0] - before


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def relative_strength(conn, code, days=20):
    """Stock return minus TAIEX return over the same window, in percent.

    Returns None when either side lacks enough history, rather than guessing.
    """
    def window(sql, key):
        rows = conn.execute(sql, (key, days + 1)).fetchall()
        if len(rows) < days + 1:
            return None
        first, last = rows[-1][0], rows[0][0]
        if not first or not last:
            return None
        return (last / first - 1) * 100

    stock = window(
        "SELECT close FROM price_daily WHERE code=? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT ?", code)
    index = window(
        "SELECT close FROM index_daily WHERE symbol=? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT ?", TAIEX_SYMBOL)
    if stock is None or index is None:
        return None
    return round(stock - index, 2)
