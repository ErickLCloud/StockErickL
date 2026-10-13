import json
from pathlib import Path

from scripts import financials as F

FX = Path(__file__).parent / "fixtures" / "financials"


def _load(name):
    return json.loads((FX / f"{name}.json").read_text(encoding="utf-8"))


ROUTES = {F.REV_L: "rev_L", F.REV_O: "rev_O", F.EPS_L: "eps_L", F.EPS_O: "eps_O",
          F.IS_L: "is_L", F.IS_O: "is_O"}


def fake_fetch(fail=()):
    def fetch(url):
        name = ROUTES[url]
        if name in fail:
            raise RuntimeError("boom")
        return _load(name)
    return fetch


def test_build_both_markets(tmp_path):
    doc = F.build_financials(tmp_path, fetch=fake_fetch())
    assert doc["errors"] == []
    assert doc["rev_month"] == "2026-09"
    assert doc["eps_period"] == "2026Q2"
    assert doc["eps_basis"] == "累計"
    it = doc["items"]
    # TWSE
    rev, mom, yoy, cum, eps, gm = it["2330"]
    assert rev == 511857399 and eps == 49.33 and gm == 67.03
    assert round(yoy, 1) == 54.6
    # TPEX: gross margin computed from the income statement
    assert it["6488"][4] == 11.87
    assert it["6488"][5] == round((29199108 - 23145169) / 29199108 * 100, 2)
    assert json.loads((tmp_path / "financials.json").read_text(encoding="utf-8"))["items"] == it


def test_missing_values_are_null_not_zero(tmp_path):
    doc = F.build_financials(tmp_path, fetch=fake_fetch())
    it = doc["items"]
    # 1316 (construction) has no revenue row: those fields null, never 0
    assert it["1316"][:4] == [None] * 4
    assert it["1316"][4] is not None
    assert F.parse_gross_margin([{"公司代號": "1", "營業收入": "", "營業毛利（毛損）淨額": "5"}]) == {"1": None}
    assert F.parse_gross_margin([{"公司代號": "1", "營業收入": "0", "營業毛利（毛損）淨額": "5"}]) == {"1": None}
    assert F.parse_eps([{"公司代號": "1", "年度": "115", "季別": "1", "基本每股盈餘(元)": "--"}])[0] == {"1": None}


def test_financial_has_null_gross_margin_construction_does_not(tmp_path):
    it = F.build_financials(tmp_path, fetch=fake_fetch())["items"]
    assert it["5878"][5] is None      # 金融業 on TPEX, present in the income statement
    assert it["2881"][5] is None      # 金融保險業, absent from the income statement
    assert it["1316"][5] is not None  # 建材營造 reports 營業毛利
    assert it["1101"][5] == 18.22     # ordinary industry still computed


def test_endpoint_failure_recorded_others_kept(tmp_path):
    doc = F.build_financials(tmp_path, fetch=fake_fetch(fail=("is_O", "rev_L")))
    assert len(doc["errors"]) == 2
    assert any(e.startswith("is_O") for e in doc["errors"])
    assert doc["items"]["2330"][0] is None            # TWSE revenue lost
    assert doc["items"]["2330"][5] == 67.03           # TWSE gross margin intact
    assert doc["items"]["6488"][5] is None            # TPEX gross margin lost
    assert doc["items"]["6488"][0] is not None        # TPEX revenue intact
    assert doc["rev_month"] == "2026-09"              # still known from TPEX


def test_non_list_response_is_an_error(tmp_path):
    def fetch(url):
        return {"msg": "html"} if url == F.EPS_O else _load(ROUTES[url])
    doc = F.build_financials(tmp_path, fetch=fetch)
    assert len(doc["errors"]) == 1 and doc["errors"][0].startswith("eps_O")


def test_snapshot_roundtrip_and_merge(tmp_path):
    doc = F.build_financials(tmp_path / "out", fetch=fake_fetch())
    snaps = F.period_items(doc)
    assert set(snaps) == {"2026-09", "2026Q2"}
    d = tmp_path / "snap"
    assert sorted(F.save_snapshot(snaps, d)) == ["2026-09", "2026Q2"]
    first = (d / "2026-09.json").read_text(encoding="utf-8")
    # same period, same codes with different data: first value kept, file untouched
    assert F.save_snapshot({"2026-09": {"2330": [1, 2, 3, 4]}}, d) == []
    assert (d / "2026-09.json").read_text(encoding="utf-8") == first
    # a late reporter in the same period is added; existing codes still keep their value
    assert F.save_snapshot({"2026-09": {"2330": [1, 2, 3, 4], "9999": [5, 6, 7, 8]}}, d) == ["2026-09"]
    # a new period is added
    assert F.save_snapshot({"2026-10": {"2330": [1, 2, 3, 4]}}, d) == ["2026-10"]
    loaded = F.load_snapshots(d)
    assert list(loaded) == ["2026-09", "2026-10", "2026Q2"]
    assert loaded["2026-09"]["2330"][0] == 511857399
    assert loaded["2026-09"]["9999"] == [5, 6, 7, 8]
    assert loaded["2026Q2"]["2330"] == [49.33, 67.03]


def test_load_snapshots_missing_dir(tmp_path):
    assert F.load_snapshots(tmp_path / "nope") == {}
