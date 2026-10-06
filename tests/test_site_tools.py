"""assemble_site + check_site: the guard against deploying a page whose scripts 404.

History: the workflow once copied `docs/index.html docs/calc.js` by name.
analysis.js was added later, never made it into the deployed site, and the
scan / analysis / backtest tabs were dead on the live page while every test
(which ran against docs/, where the file exists) stayed green.
"""

import json
from pathlib import Path

import pytest

from scripts import assemble_site as asm
from scripts import check_site as chk

REAL_DOCS = Path(__file__).resolve().parents[1] / "docs"


def write(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


PAGE = ('<html><head><link rel="stylesheet" href="app.css">'
        '<script src="calc.js"></script><script src="analysis.js?v=3"></script>'
        '<script src="https://cdn.example/x.js"></script>'
        '<link rel="icon" href="data:image/png;base64,AAAA"></head><body>'
        '<a href="#top">x</a></body></html>')


def make_quotes(site, n=2400, live=2000, failed=()):
    items = {str(i): {"live": True} for i in range(n)}
    write(site / "data" / "quotes.json",
          json.dumps({"items": items, "live_count": live, "failed_batches": list(failed)}))


@pytest.fixture
def site(tmp_path):
    s = tmp_path / "site"
    write(s / "index.html", PAGE)
    for f in ("app.css", "calc.js", "analysis.js"):
        write(s / f)
    make_quotes(s)
    return s


# ---------------------------------------------------------------- assemble

def test_assemble_copies_everything_but_data_without_naming_files(tmp_path):
    src = tmp_path / "docs"
    write(src / "index.html"); write(src / "calc.js"); write(src / "analysis.js")
    write(src / "brand_new_file.js")                       # a file nobody listed anywhere
    write(src / "img" / "logo.svg"); write(src / ".nojekyll", "")
    write(src / "data" / "quotes.json", "{}")              # generated: must not travel
    out = tmp_path / "out"
    names = asm.assemble(out, src)
    assert "brand_new_file.js" in names and (out / "brand_new_file.js").is_file()
    assert (out / "img" / "logo.svg").is_file()
    assert (out / "analysis.js").is_file()
    assert not (out / "data" / "quotes.json").exists()
    assert (out / "data").is_dir()                         # created empty for the later steps


def test_assemble_is_repeatable(tmp_path):
    src = tmp_path / "docs"; write(src / "index.html")
    out = tmp_path / "out"
    asm.assemble(out, src); asm.assemble(out, src)         # second run must not fail
    assert (out / "index.html").is_file()


def test_the_REAL_docs_assemble_into_a_site_that_serves_every_asset_it_references(tmp_path):
    """The permanent regression test for the dead-tabs bug. Uses the real files."""
    out = tmp_path / "out"
    asm.assemble(out, REAL_DOCS)
    refs = chk.local_assets((out / "index.html").read_text(encoding="utf-8"))
    assert refs, "index.html references no local scripts at all; the parser is broken"
    missing = [r for r in refs if not (out / r).is_file()]
    assert missing == [], f"deployed site is missing {missing}"
    assert "analysis.js" in refs and "calc.js" in refs     # the two that mattered


# ------------------------------------------------------------------- check

def test_complete_site_passes(site):
    assert chk.check(site) == []


def test_missing_analysis_js_is_caught_the_exact_regression(site):
    (site / "analysis.js").unlink()
    problems = chk.check(site)
    assert any("analysis.js" in p and "not in the site" in p for p in problems), problems


def test_query_string_on_a_reference_does_not_hide_a_missing_file(site):
    (site / "analysis.js").unlink()                        # PAGE references analysis.js?v=3
    assert chk.check(site) != []


def test_external_and_data_uris_and_fragments_are_not_treated_as_local():
    refs = chk.local_assets(PAGE)
    assert refs == ["app.css", "calc.js", "analysis.js"]


def test_root_absolute_reference_is_flagged_even_if_the_file_exists(site):
    write(site / "index.html", PAGE.replace('src="calc.js"', 'src="/calc.js"'))
    problems = chk.check(site)
    assert any("root-absolute" in p for p in problems), problems


def test_reference_escaping_the_site_is_flagged(site, tmp_path):
    write(tmp_path / "secret.js")
    write(site / "index.html", PAGE.replace('src="calc.js"', 'src="../secret.js"'))
    assert any("outside the site" in p for p in chk.check(site))


def test_missing_index_is_reported_not_raised(tmp_path):
    assert chk.check(tmp_path) == ["index.html is missing"]


@pytest.mark.parametrize("n,expect", [(2400, False), (2001, False), (2000, True), (50, True)])
def test_a_truncated_market_is_refused(site, n, expect):
    make_quotes(site, n=n)
    assert any("symbols" in p for p in chk.check(site)) is expect


def test_every_batch_failing_is_refused(site):
    make_quotes(site, live=0, failed=["batch 0", "batch 1"])
    assert any("batch failed" in p for p in chk.check(site))


def test_some_failed_batches_with_live_prices_are_tolerated(site):
    make_quotes(site, live=1800, failed=["batch 3"])
    assert chk.check(site) == []


def test_unreadable_or_missing_quotes_are_reported(site):
    write(site / "data" / "quotes.json", "{not json")
    assert any("unreadable" in p for p in chk.check(site))
    (site / "data" / "quotes.json").unlink()
    assert any("missing" in p for p in chk.check(site))


def test_cli_exit_codes(site, capsys):
    assert chk.main([str(site)]) == 0
    (site / "analysis.js").unlink()
    assert chk.main([str(site)]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert chk.main([]) == 2
