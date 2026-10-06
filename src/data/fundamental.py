"""Fundamental data layer: PE / dividend yield / PB from the TWSE BWIBBU_ALL snapshot.

LIMITATIONS (v1, by decision):
* The endpoint covers TWSE-listed (上市) securities ONLY. TPEx-listed (上櫃)
  securities have NO fundamental data in v1; lookups for them return ``None``
  (never raise). Alternative sources are v2 scope.
* The endpoint serves only the latest trading day's snapshot, no history.
  Run ``fetch_and_store()`` once per day to accumulate history.
* Missing values arrive as ``""`` and are stored as NULL (``None``), never 0.
* ``dividend_yield`` is a percentage as served (3.20 means 3.20%).
"""

import sqlite3
from pathlib import Path

from src.http import fetch_json

URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
SOURCE = "twse_bwibbu"
DB_PATH = Path(__file__).resolve().parents[2] / "db" / "stock.db"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"

__all__ = ["roc_to_iso", "to_float", "parse_rows", "connect", "upsert",
           "fetch_and_store", "get_latest"]


def roc_to_iso(s: str) -> str:
    """'1151005' -> '2026-10-05'."""
    s = str(s).strip()
    return f"{int(s[:-4]) + 1911:04d}-{s[-4:-2]}-{s[-2:]}"


def to_float(v):
    """'' / None / unparseable -> None (never 0); else float."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_rows(payload):
    """API records -> list of (code, date, pe, yield, pb, source)."""
    out = []
    for r in payload:
        code = r.get("Code")
        d = r.get("Date")
        if not code or not d:
            continue
        out.append((code, roc_to_iso(d), to_float(r.get("PEratio")),
                    to_float(r.get("DividendYield")), to_float(r.get("PBratio")),
                    SOURCE))
    return out


def connect(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    # Schema uses IF NOT EXISTS; this only ensures tables exist.
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def upsert(conn, rows) -> int:
    conn.executemany(
        "INSERT INTO fundamental (code, date, pe_ratio, dividend_yield, pb_ratio, source) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(code, date) DO UPDATE SET "
        "pe_ratio=excluded.pe_ratio, dividend_yield=excluded.dividend_yield, "
        "pb_ratio=excluded.pb_ratio, source=excluded.source", rows)
    conn.commit()
    return len(rows)


def fetch_and_store(db_path=None, payload=None) -> int:
    """Fetch today's snapshot (or use ``payload``) and upsert. Returns row count."""
    if payload is None:
        payload = fetch_json(URL)
    rows = parse_rows(payload)
    conn = connect(db_path)
    try:
        return upsert(conn, rows)
    finally:
        conn.close()


def get_latest(code, db_path=None):
    """Latest fundamentals for ``code`` as a dict, or None if absent.

    None is returned for TPEx (上櫃) codes and unknown codes: v1 has no data for them.
    Individual fields may also be None (e.g. no PE for loss-making firms).
    """
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT code, date, pe_ratio, dividend_yield, pb_ratio FROM fundamental "
            "WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(zip(("code", "date", "pe_ratio", "dividend_yield", "pb_ratio"), row))


if __name__ == "__main__":
    print(f"stored {fetch_and_store()} rows")
