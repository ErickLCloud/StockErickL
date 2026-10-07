"""Regenerate tests/fixtures/parity.json: real closes plus what the PYTHON
indicator code (src/analysis/indicators.py) computes from them.

Why this exists. The web page computes MA / RSI / MACD in docs/analysis.js; the
local command line (main.py, src/analysis) computes them in Python. The web
pipeline publishes no copy of its own, but those two implementations still
exist, and nothing else would notice if one of them drifted. This fixture pins
both to the same numbers:

  * tests/test_parity_fixture.py recomputes the Python side and compares;
  * tests/web/selftest.html runs the JavaScript side on the same closes.

Run it only when the Python indicator code changes ON PURPOSE:

    python scripts/make_parity_fixture.py

It reads docs/data/history/*.json (build them first), so it is not run in CI;
the committed fixture is what CI checks against.
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.indicators import compute_indicators  # noqa: E402

HIST = ROOT / "docs" / "data" / "history"
OUT = ROOT / "tests" / "fixtures" / "parity.json"
# A spread: big ETF, a high-yield ETF, a bond ETF (OTC), large caps, a mid cap, an OTC stock.
CODES = ["0050", "00878", "00679B", "2330", "2317", "1101", "6488", "2454"]
BARS = 300                      # enough for MA240 and for the MACD seed to have washed out
FIELDS = ("ma5", "ma20", "ma60", "ma240", "rsi", "hist")


def python_values(closes):
    df = pd.DataFrame({"date": [f"d{i}" for i in range(len(closes))], "open": closes,
                       "high": closes, "low": closes, "close": closes, "volume": 1000.0})
    raw = compute_indicators(df)
    return {k: raw.get(k) for k in FIELDS}


def main():
    cases = []
    for code in CODES:
        f = HIST / f"{code}.json"
        if not f.exists():
            raise SystemExit(f"{f} is missing; run scripts/build_web_data.py history first")
        h = json.loads(f.read_text(encoding="utf-8"))
        closes = [float(x) for x in h["p"][-BARS:]]
        if len(closes) < BARS:
            raise SystemExit(f"{code} has only {len(closes)} closes")
        cases.append({"code": code, "closes": closes, "expect": python_values(closes)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"fields": FIELDS, "cases": cases}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e3:.0f} KB, {len(cases)} symbols x {BARS} closes)")
    for c in cases:
        print(" ", c["code"], c["expect"])


if __name__ == "__main__":
    main()
