"""Pin the PYTHON indicator code to tests/fixtures/parity.json.

The JavaScript side is pinned to the same file by tests/web/selftest.html. If
you change src/analysis/indicators.py on purpose, regenerate the fixture
(scripts/make_parity_fixture.py) and the JavaScript test will then tell you
whether docs/analysis.js still agrees.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from src.analysis.indicators import compute_indicators

FIXTURE = Path(__file__).parent / "fixtures" / "parity.json"
FX = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _python(closes):
    df = pd.DataFrame({"date": [f"d{i}" for i in range(len(closes))], "open": closes,
                       "high": closes, "low": closes, "close": closes, "volume": 1000.0})
    return compute_indicators(df)


def test_the_fixture_is_substantial_and_real():
    assert len(FX["cases"]) >= 6
    assert all(len(c["closes"]) >= 260 for c in FX["cases"]), "needs enough bars for MA240"
    assert set(FX["fields"]) == {"ma5", "ma20", "ma60", "ma240", "rsi", "hist"}


@pytest.mark.parametrize("case", FX["cases"], ids=[c["code"] for c in FX["cases"]])
def test_python_indicators_still_produce_the_pinned_numbers(case):
    got = _python(case["closes"])
    for k in FX["fields"]:
        assert got[k] == pytest.approx(case["expect"][k], abs=1e-9), (case["code"], k)


def test_every_pinned_value_is_a_real_number_not_a_blank():
    """A fixture full of None would 'pass' on both sides and prove nothing."""
    for c in FX["cases"]:
        for k in FX["fields"]:
            assert isinstance(c["expect"][k], (int, float)), (c["code"], k)
