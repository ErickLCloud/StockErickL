"""stock-analyzer CLI — pilot-owned integration layer (R3).

    python main.py report            每日報告：更新資料 -> 篩選 -> 大盤 -> 寫出 Markdown
    python main.py query 0050        單一標的：技術指標 + 基本面 + 對大盤相對強弱
    python main.py update            只更新資料（價量增量 + 基本面 + 指數）
    python main.py setup             首次建置：universe + 2 年回補 + 指數回補
"""

import datetime as dt
import sys

from src.analysis.indicators import compute_indicators
from src.analysis.screener import load_frames, screen
from src.data import fundamental, price
from src.data.news import fetch_news, save_news
from src.holdings import holdings_report
from src.market import backfill_index, fetch_market_summary, relative_strength
from src.report.render import render_report, write_report

DEFAULT_CONDITIONS = ["above_ma20", "volume_surge"]
NEWS_QUERY = "台股"


def _today():
    return dt.date.today().strftime("%Y-%m-%d")


def cmd_setup():
    conn = price.connect()
    twse, tpex = price.fetch_quotes()
    n = price.build_universe(conn, twse, tpex, price.read_watchlist())
    print(f"universe: {n} instruments in_universe=1")
    print(f"price backfill: {price.backfill(conn)} new rows")
    print(f"index backfill: {backfill_index(conn)} new rows")
    print(f"fundamental: {fundamental.fetch_and_store()} rows")
    return 0


def cmd_update():
    conn = price.connect()
    twse, tpex = price.fetch_quotes()
    print(f"price daily: {price.daily_update(conn, twse, tpex)} new rows")
    print(f"index: {backfill_index(conn, period='1mo')} new rows")
    print(f"fundamental: {fundamental.fetch_and_store()} rows")
    return 0


def cmd_report(update=True):
    conn = price.connect()
    if update:
        try:
            twse, tpex = price.fetch_quotes()
            price.daily_update(conn, twse, tpex)
            backfill_index(conn, period="1mo")
            fundamental.fetch_and_store()
        except Exception as exc:                      # report must still render
            print(f"warning: data update failed ({exc}); using stored data")

    result = screen(DEFAULT_CONDITIONS, mode="all", conn=conn)
    candidates = _to_candidates(conn, result)
    print(f"screened {result['scanned']} instruments -> "
          f"{len(candidates)} candidates, {len(result['errors'])} errors")

    market = fetch_market_summary()
    if market is None:
        print("warning: 大盤 source unavailable; section shows placeholder")

    news = fetch_news(NEWS_QUERY)
    if news:
        save_news(conn, news)
    else:
        print("warning: no news fetched; section shows 無新聞資料")

    date = _today()
    text = render_report(date, market=market, candidates=candidates,
                         holdings_rows=holdings_report(conn), news_items=news)
    path = write_report(date, text)
    print(f"report: {path}")
    return 0


def _to_candidates(conn, result):
    """Shape W4's screen() output into W5's candidate interface.

    W4 returns {candidates, scanned, errors}; each candidate carries
    code/name/matched/unknown/missing_indicators/indicators. W5 wants
    code/name/kind/close/score/reasons. `kind` is the only field neither
    side supplies, so it is looked up here.
    """
    kinds = dict(conn.execute(
        "SELECT code, kind FROM instrument WHERE in_universe=1"))
    out = []
    for hit in result["candidates"]:
        ind = hit.get("indicators") or {}
        matched = hit.get("matched") or []
        reasons = list(matched)
        # Flag thin history in the report rather than hiding it (W4's note:
        # use `unknown`, not a row-count guess).
        if hit.get("unknown"):
            reasons.append(f"資料不足:{','.join(hit['unknown'])}")
        out.append({
            "code": hit["code"],
            "name": hit.get("name") or "",
            "kind": kinds.get(hit["code"], "STOCK"),
            "close": ind.get("close"),
            "score": float(len(matched)),
            # render.py documents reasons as list[str]; passing a joined
            # string makes it render character-by-character.
            "reasons": reasons,
        })
    out.sort(key=lambda c: (-c["score"], c["code"]))
    return out


def cmd_query(code):
    conn = price.connect()
    row = conn.execute(
        "SELECT name, board, kind FROM instrument WHERE code=?", (code,)).fetchone()
    if row is None:
        print(f"{code}: 不在 instrument 表（請先跑 setup，或確認代號）")
        return 1
    name, board, kind = row

    # load_frames returns (frames, names_map); only in_universe=1 codes appear.
    frames, _ = load_frames(conn=conn)
    df = frames.get(code)
    if df is None or df.empty:
        print(f"{code}: 不在 universe 或無價量資料（universe 只含 ETF 與 watchlist）")
        return 1
    ind = compute_indicators(df)

    print(f"{code} {name}  [{board} / {kind}]")
    print(f"  收盤      : {ind.get('close')}")
    print(f"  MA5/20/60 : {ind.get('ma5')} / {ind.get('ma20')} / {ind.get('ma60')}")
    print(f"  MA240(年線): {ind.get('ma240')}" +
          ("   (資料不足)" if ind.get("ma240") is None else ""))
    print(f"  KD        : K={ind.get('k')} D={ind.get('d')}")
    print(f"  RSI14     : {ind.get('rsi')}")
    print(f"  MACD hist : {ind.get('hist')}")
    print(f"  量比       : {ind.get('vol_ratio')}")

    f = fundamental.get_latest(code)
    if kind == "ETF":
        print("  基本面     : —  (ETF 無基本面資料；TWSE 未提供)")
    elif f is None:
        print("  基本面     : —  (查無資料；BWIBBU 僅涵蓋上市個股)")
    else:
        print(f"  PE/PB/殖利率: {f.get('pe_ratio')} / {f.get('pb_ratio')} / "
              f"{f.get('dividend_yield')}")

    rs = relative_strength(conn, code)
    print(f"  對大盤相對強弱(20日): "
          f"{'—  (歷史不足)' if rs is None else f'{rs:+.2f}%'}")
    return 0


def main(argv=None):
    args = argv or sys.argv[1:]
    cmd = args[0] if args else "report"
    if cmd == "report":
        return cmd_report()
    if cmd == "report-offline":
        return cmd_report(update=False)
    if cmd == "update":
        return cmd_update()
    if cmd == "setup":
        return cmd_setup()
    if cmd == "query":
        if len(args) < 2:
            print("usage: python main.py query <code>")
            return 2
        return cmd_query(args[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
