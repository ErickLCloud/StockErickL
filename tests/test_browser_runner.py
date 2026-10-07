from pathlib import Path

from scripts.run_browser_tests import SUITES, verdict


def test_pass_fail_and_missing_result_are_told_apart():
    assert verdict('<pre id="out">RESULT: PASS 87/87\n</pre>')[0] == "PASS"
    assert verdict('<pre id="out">RESULT: FAIL 2/87\n</pre>')[0] == "FAIL"
    # The pages quote "RESULT: PASS" in a source comment; a page that never finished must not pass on that.
    assert verdict('<script>/* look for "RESULT: PASS". */</script><pre id="out">running…</pre>')[0] == "NONE"
    # A page that crashed before reporting must not count as a pass.
    assert verdict("<html><body></body></html>")[0] == "NONE"


def test_every_browser_test_page_is_run():
    pages = {p.name for p in (Path(__file__).parent / "web").glob("*.html")} - {"viewport.html"}
    assert pages == set(SUITES)
