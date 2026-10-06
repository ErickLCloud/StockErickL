"""Mirror the publishable part of this project into publish/ (the public repo).

    python scripts/sync_publish.py            # dry run: show what would change
    python scripts/sync_publish.py --apply    # do it

publish/ is a separate git repository with a deliberately clean history: no
_context/, no multi-work notes, no holdings. This script copies ONLY the paths
listed in MANAGED, so nothing private can drift across by accident. It never
touches publish/.git, and never copies generated data (docs/data), CSV inputs,
the database, or caches. Review `git status` inside publish/ afterwards, then
commit and push yourself: publishing is deliberately not automated here.
"""

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "publish"

# Directories are mirrored (files removed from the source are removed from the
# destination too); plain files are just copied.
MANAGED_DIRS = [".github", "scripts", "src", "tests", "docs"]
MANAGED_FILES = ["main.py", "requirements.txt", "README.md", "db/schema.sql"]

# Never copied, wherever they appear under a managed path.
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".venv"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".csv"}
EXCLUDE_PREFIXES = ("docs/data",)       # generated and deployed by the workflow
EXCLUDE_NAMES = {"_seed_tmp.html"}      # a temporary test helper


def excluded(rel: Path) -> bool:
    s = rel.as_posix()
    if any(p in EXCLUDE_PARTS for p in rel.parts):
        return True
    if rel.suffix in EXCLUDE_SUFFIXES or rel.name in EXCLUDE_NAMES:
        return True
    return any(s == p or s.startswith(p + "/") for p in EXCLUDE_PREFIXES)


def source_files():
    out = {}
    for d in MANAGED_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            rel = f.relative_to(ROOT)
            if f.is_file() and not excluded(rel):
                out[rel.as_posix()] = f
    for name in MANAGED_FILES:
        f = ROOT / name
        if f.is_file():
            out[name] = f
    return out


def dest_managed_files():
    out = set()
    for d in MANAGED_DIRS:
        base = DEST / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            rel = f.relative_to(DEST)
            if f.is_file() and not excluded(rel):
                out.add(rel.as_posix())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="actually copy and delete (default: dry run)")
    args = ap.parse_args(argv)

    if not (DEST / ".git").is_dir():
        print(f"{DEST} is not a git repository; refusing to sync into it.")
        return 2

    src = source_files()
    add, change = [], []
    for rel, f in sorted(src.items()):
        d = DEST / rel
        if not d.exists():
            add.append(rel)
        elif not filecmp.cmp(f, d, shallow=False):
            change.append(rel)
    remove = sorted(dest_managed_files() - set(src))

    for label, items in (("add", add), ("change", change), ("remove", remove)):
        print(f"{label:>7}: {len(items)}")
        for rel in items[:25]:
            print(f"           {rel}")
        if len(items) > 25:
            print(f"           ... and {len(items) - 25} more")

    if not args.apply:
        print("\ndry run only; re-run with --apply to make these changes.")
        return 0

    for rel in add + change:
        (DEST / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src[rel], DEST / rel)
    for rel in remove:
        (DEST / rel).unlink()
    print(f"\napplied. Now review `git status` in {DEST}, then commit and push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
