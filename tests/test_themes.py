import json
from pathlib import Path

THEMES = json.loads((Path(__file__).resolve().parents[1] / "docs" / "themes.json").read_text(encoding="utf-8"))


def test_themes_file_is_well_formed():
    names = [t["name"] for t in THEMES["themes"]]
    assert len(names) == len(set(names)) and len(names) >= 15
    for t in THEMES["themes"]:
        assert t["name"].strip() and t["codes"], t
        assert all(isinstance(c, str) and c.isalnum() for c in t["codes"]), t["name"]
        assert len(t["codes"]) == len(set(t["codes"])), f"duplicate code in {t['name']}"


def test_every_theme_code_exists_in_the_market_when_data_is_present():
    idx = Path(__file__).resolve().parents[1] / "docs" / "data" / "index.json"
    if not idx.exists():
        return                                           # CI restores the data first; locally it may be absent
    known = {i["c"] for i in json.loads(idx.read_text(encoding="utf-8"))["items"]}
    if len(known) < 1000:
        return                                           # a stale ETF-only index says nothing about stocks
    missing = {t["name"]: [c for c in t["codes"] if c not in known] for t in THEMES["themes"]}
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, f"themes.json lists codes that are not listed: {missing}"
