"""Run the browser test pages headless and fail unless each reports RESULT: PASS.

    python scripts/run_browser_tests.py            # needs docs/data (see below)
    python scripts/run_browser_tests.py --browser "C:/path/to/msedge.exe"

The pages under tests/web/ load docs/*.js and docs/data/*.json, so the repo
root is served over http (file:// would block fetch). Each page gets a FRESH
browser profile: a reused profile once kept its HTTP cache, so a rerun tested
stale data files and still reported green.

docs/data is generated and gitignored. Fill it first (CI does) with the history
from the `data` branch plus `python scripts/build_web_data.py quotes --out docs/data`.
A missing data file is a failure, never a skip: a silent skip is how a suite
turns into a green check that proves nothing.
"""

import argparse
import functools
import http.server
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = ["selftest.html", "analysis_selftest.html", "e2e.html", "e2e_analysis.html"]
NEEDS = ["index.json", "quotes.json", "fundamental.json", "indicators.json", "closes.json"]
CANDIDATES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"]


def find_browser(explicit=None):
    for c in ([explicit] if explicit else CANDIDATES):
        if c and (Path(c).exists() or shutil.which(c)):
            return c
    raise SystemExit("no Chrome/Edge found; pass --browser")


def verdict(dom):
    """('PASS'|'FAIL'|'NONE', first line) from a dumped DOM."""
    # Only the page's own output element counts. The test pages quote "RESULT: PASS"
    # in a source comment, so a page that crashed before reporting would look green.
    m = re.search(r'<pre id="out">\s*(RESULT: (PASS|FAIL)[^\n<]*)', dom)
    return (m.group(2), m.group(1)) if m else ("NONE", "page did not report a RESULT")


def run_suite(browser, base, name, budget, sandbox):
    prof = tempfile.mkdtemp(prefix="bt-")
    try:
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={prof}",
               f"--virtual-time-budget={budget}", "--window-size=1280,1000", "--dump-dom",
               f"{base}/tests/web/{name}"]
        if not sandbox:
            cmd.insert(1, "--no-sandbox")
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        v, line = verdict(p.stdout)
        if v != "PASS":                       # show WHY: the page lists each failed check after the RESULT line
            m = re.search(r'<pre id="out">[^\n]*\n((?:.+\n){0,25})', p.stdout)
            line += "\n" + (m.group(1) if m else p.stdout[-600:])
        return v, line
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser")
    ap.add_argument("--budget", type=int, default=60000, help="virtual-time budget in ms")
    ap.add_argument("--sandbox", action="store_true", help="keep the browser sandbox (default off: CI runners need --no-sandbox)")
    a = ap.parse_args(argv)

    missing = [n for n in NEEDS if not (ROOT / "docs" / "data" / n).exists()]
    if missing:
        print("missing docs/data files:", ", ".join(missing))
        return 2
    browser = find_browser(a.browser)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(ROOT))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    bad = 0
    for name in SUITES:
        v, line = run_suite(browser, base, name, a.budget, a.sandbox)
        print(f"{'ok  ' if v == 'PASS' else 'FAIL'} {name}: {line}")
        bad += v != "PASS"
    srv.shutdown()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
