"""Holdings return calculation. Reads input/holdings.csv (read-only)."""
import csv
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLDINGS_PATH = ROOT / "input" / "holdings.csv"


def load_holdings(path=HOLDINGS_PATH):
    """Return list of dicts, or None if the file does not exist."""
    path = Path(path)
    if not path.exists():
        return None
    out = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            code = (r.get("code") or "").strip()
            if not code:
                continue
            try:
                out.append({
                    "code": code,
                    "buy_date": (r.get("buy_date") or "").strip(),
                    "shares": float(r["shares"]),
                    "cost_price": float(r["cost_price"]),
                    "note": (r.get("note") or "").strip() or None,
                })
            except (KeyError, ValueError, TypeError):
                continue  # skip malformed rows
    return out


def compute_return(h, last_close, as_of):
    """Return metrics for one holding. annualized_pct is None if held < 30 days."""
    cost, shares = h["cost_price"], h["shares"]
    pnl = (last_close - cost) * shares
    pct = (last_close / cost - 1) * 100
    days = (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(h["buy_date"])).days
    ann = None
    if days >= 30:
        ann = ((last_close / cost) ** (365 / days) - 1) * 100
    return {"code": h["code"], "shares": shares, "cost_price": cost,
            "last_close": last_close, "pnl": pnl, "return_pct": pct,
            "days": days, "annualized_pct": ann}


def holdings_report(conn, path=HOLDINGS_PATH):
    """None if no holdings file; else list of metric dicts (no price -> last_close None)."""
    hs = load_holdings(path)
    if hs is None:
        return None
    rows = []
    for h in hs:
        r = conn.execute("SELECT close, date FROM price_daily WHERE code=? AND close IS NOT NULL "
                         "ORDER BY date DESC LIMIT 1", (h["code"],)).fetchone()
        if r is None:
            rows.append({"code": h["code"], "shares": h["shares"], "cost_price": h["cost_price"],
                         "last_close": None, "pnl": None, "return_pct": None,
                         "days": None, "annualized_pct": None})
        else:
            rows.append(compute_return(h, float(r[0]), r[1]))
    return rows
