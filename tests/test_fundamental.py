import sqlite3

from src.data import fundamental as f

FIXTURE = [
    {"Date": "1151005", "Code": "1101", "Name": "台泥", "PEratio": "", "DividendYield": "3.20", "PBratio": "0.85"},
    {"Date": "1151005", "Code": "2330", "Name": "台積電", "PEratio": "22.5", "DividendYield": "1.5", "PBratio": "6.1"},
    {"Date": "1151005", "Code": "0050", "Name": "x", "PEratio": "", "DividendYield": "", "PBratio": ""},
]


def test_roc_date():
    assert f.roc_to_iso("1151005") == "2026-10-05"
    assert f.roc_to_iso("990101") == "2010-01-01"


def test_empty_string_is_none_not_zero():
    assert f.to_float("") is None
    assert f.to_float("  ") is None
    assert f.to_float(None) is None
    assert f.to_float("0") == 0.0
    rows = {r[0]: r for r in f.parse_rows(FIXTURE)}
    assert rows["1101"][2] is None          # PE "" -> None, not 0
    assert rows["0050"][2:5] == (None, None, None)


def test_values_and_code_preserved():
    rows = {r[0]: r for r in f.parse_rows(FIXTURE)}
    assert rows["0050"][0] == "0050"
    assert rows["1101"][3] == 3.20 and rows["1101"][4] == 0.85
    assert rows["2330"][1] == "2026-10-05" and rows["2330"][5] == "twse_bwibbu"


def test_store_idempotent_and_null_in_db(tmp_path):
    db = tmp_path / "t.db"
    assert f.fetch_and_store(db, payload=FIXTURE) == 3
    f.fetch_and_store(db, payload=FIXTURE)
    conn = sqlite3.connect(db)
    assert conn.execute("select count(*) from fundamental").fetchone()[0] == 3
    assert conn.execute("select pe_ratio from fundamental where code='1101'").fetchone()[0] is None
    conn.close()


def test_get_latest(tmp_path):
    db = tmp_path / "t.db"
    f.fetch_and_store(db, payload=FIXTURE)
    newer = [dict(FIXTURE[1], Date="1151006", PEratio="30")]
    f.fetch_and_store(db, payload=newer)
    r = f.get_latest("2330", db)
    assert r["date"] == "2026-10-06" and r["pe_ratio"] == 30.0
    assert f.get_latest("1101", db)["pe_ratio"] is None
    assert f.get_latest("6488", db) is None   # TPEx / unknown -> None, no exception
