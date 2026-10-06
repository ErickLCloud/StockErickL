"""Refuse to deploy a site that is visibly broken.

    python scripts/check_site.py _site

Checks the ASSEMBLED site, not the source tree. Every earlier browser test ran
against docs/, which always has every file, so a deployment that forgot to
copy one went unnoticed until a user reported dead tabs.

  * every local <script src>, <link href> and <img src> in index.html exists
    in the site (a missing one is exactly how the scan/analysis/backtest tabs
    died: analysis.js returned 404 and window.Analysis was undefined)
  * no such reference is root-absolute ("/x.js"): on a project Pages site the
    root is github.io/, not this repo, so it would 404 even if the file exists
  * quotes.json parses and covers the whole market, and not every batch failed
"""

import json
import re
import sys
from pathlib import Path

ASSET = re.compile(r"""<(?:script|link|img)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//)", re.I)     # http:, https:, data:, mailto:, //cdn
MIN_SYMBOLS = 2000


def local_assets(html):
    """Local asset references in `html`, without query strings or fragments."""
    out = []
    for ref in ASSET.findall(html):
        ref = ref.split("#", 1)[0].split("?", 1)[0].strip()
        if ref and not EXTERNAL.match(ref):
            out.append(ref)
    return out


def check(site, min_symbols=MIN_SYMBOLS):
    """Return a list of problems; an empty list means the site is deployable."""
    site = Path(site).resolve()
    index = site / "index.html"
    if not index.is_file():
        return ["index.html is missing"]
    problems = []

    for ref in local_assets(index.read_text(encoding="utf-8")):
        if ref.startswith("/"):
            problems.append(f"{ref}: root-absolute path breaks on a project Pages site; use a relative path")
            continue
        target = (site / ref).resolve()
        if site not in target.parents and target != site:
            problems.append(f"{ref}: points outside the site")
        elif not target.is_file():
            problems.append(f"{ref}: referenced by index.html but not in the site")

    quotes = site / "data" / "quotes.json"
    if not quotes.is_file():
        problems.append("data/quotes.json is missing")
    else:
        try:
            q = json.loads(quotes.read_text(encoding="utf-8"))
            n, live = len(q["items"]), q.get("live_count", 0)
            if n <= min_symbols:
                problems.append(f"data/quotes.json holds only {n} symbols (expected > {min_symbols})")
            if q.get("failed_batches") and not live:
                problems.append("every intraday batch failed (no live price at all)")
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f"data/quotes.json is unreadable: {exc}")
    return problems


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    problems = check(args[0])
    if problems:
        print("site check FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    refs = local_assets((Path(args[0]) / "index.html").read_text(encoding="utf-8"))
    print(f"site check ok: {len(refs)} local assets present ({', '.join(refs)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
