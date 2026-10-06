"""Tests for the pilot's integration layer (main.py).

These exist because the W4->W5 seam broke in a way that 50 unit tests could
not see: each side was individually correct, but main.py passed `reasons` as a
joined string where render.py documents list[str], so the report rendered the
condition names character-by-character.
"""

import sqlite3

import pytest

from main import _to_candidates
from src.report.render import section_candidates

# Exactly the shape src/analysis/screener.py::screen_frames returns.
SCREEN_RESULT = {
    "scanned": 357,
    "errors": {},
    "candidates": [
        {"code": "00692", "name": "富邦公司治理",
         "matched": ["above_ma20", "volume_surge"], "unknown": [],
         "missing_indicators": [],
         "indicators": {"close": 100.0, "ma20": 95.0}},
        {"code": "00985A", "name": "主動野村台灣50",
         "matched": ["above_ma20"], "unknown": ["above_ma240"],
         "missing_indicators": ["ma240"],
         "indicators": {"close": 24.54, "ma20": 24.0}},
    ],
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE instrument (code TEXT, kind TEXT, in_universe INT)")
    c.executemany("INSERT INTO instrument VALUES (?,?,1)",
                  [("00692", "ETF"), ("00985A", "ETF")])
    return c


def test_reasons_is_a_list_not_a_joined_string(conn):
    """The actual regression: a str here renders one character per entry."""
    for cand in _to_candidates(conn, SCREEN_RESULT):
        assert isinstance(cand["reasons"], list), "reasons must be list[str]"
        assert all(isinstance(r, str) for r in cand["reasons"])


def test_condition_names_survive_rendering(conn):
    """End-to-end through the renderer: the table must contain whole words."""
    md = section_candidates(_to_candidates(conn, SCREEN_RESULT))
    assert "above_ma20" in md
    assert "volume_surge" in md
    # The bug's signature: single letters separated by the join separator.
    assert "a; b; o; v; e" not in md


def test_thin_history_is_surfaced_not_hidden(conn):
    """W4's note: flag insufficient data via `unknown`, never a row-count guess."""
    by_code = {c["code"]: c for c in _to_candidates(conn, SCREEN_RESULT)}
    assert any("資料不足" in r for r in by_code["00985A"]["reasons"])
    assert not any("資料不足" in r for r in by_code["00692"]["reasons"])


def test_score_counts_only_matched_conditions(conn):
    by_code = {c["code"]: c for c in _to_candidates(conn, SCREEN_RESULT)}
    assert by_code["00692"]["score"] == 2.0
    assert by_code["00985A"]["score"] == 1.0


def test_sorted_by_score_descending(conn):
    scores = [c["score"] for c in _to_candidates(conn, SCREEN_RESULT)]
    assert scores == sorted(scores, reverse=True)
