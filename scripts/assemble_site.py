"""Assemble the deployable site: everything in docs/ except data/.

    python scripts/assemble_site.py _site

Why this is a script and not a hard-coded `cp docs/index.html docs/calc.js`:
the workflow once listed its files by name. analysis.js was added later and
nobody updated the list, so the deployed site shipped an index.html that
referenced a script which returned 404, and the scan / analysis / backtest
tabs did nothing. Copying the directory means a new file ships by default.

data/ is generated (and deployed from the data branch plus a fresh build), so
it is created empty here and filled in by the later workflow steps.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs"
SKIP = {"data"}


def assemble(out, src=SRC):
    """Copy every entry of `src` except SKIP into `out`; return the names copied."""
    out, src = Path(out), Path(src)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    copied = []
    for p in sorted(src.iterdir()):
        if p.name in SKIP:
            continue
        dest = out / p.name
        if p.is_dir():
            shutil.copytree(p, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(p, dest)
        copied.append(p.name)
    return copied


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    names = assemble(args[0])
    print(f"assembled {len(names)} entries into {args[0]}: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
