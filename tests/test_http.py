"""The shared HTTP helper retries transient failures, and ONLY those."""

import http.client
import io
import ssl
import urllib.error

import pytest

from src import http as H


class _Resp:
    def __init__(self, body=b'{"ok": 1}'):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def script(monkeypatch):
    """script([exc_or_response, ...]) -> the list of calls made. Sleeps are skipped."""
    calls = []
    monkeypatch.setattr(H.time, "sleep", lambda *_: None)

    def install(seq):
        it = iter(seq)

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(req.full_url)
            nxt = next(it)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt

        monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
        return calls

    return install


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


def test_a_connection_reset_is_retried_and_then_succeeds(script):
    """The failure that killed a ten-minute build on its very first request."""
    calls = script([ConnectionResetError(10054, "reset"), ConnectionResetError(10054, "reset"), _Resp()])
    assert H.fetch_json("http://x/a") == {"ok": 1}
    assert len(calls) == 3


def test_it_gives_up_after_the_attempt_limit_and_raises_the_last_error(script):
    calls = script([ConnectionResetError("1"), ConnectionResetError("2"), ConnectionResetError("3"), _Resp()])
    with pytest.raises(ConnectionResetError, match="3"):
        H.fetch_bytes("http://x/a")
    assert len(calls) == H.ATTEMPTS == 3


@pytest.mark.parametrize("code", [500, 502, 503, 429])
def test_server_side_errors_and_rate_limits_are_retried(script, code):
    calls = script([_http_error(code), _Resp()])
    assert H.fetch_bytes("http://x/a") == b'{"ok": 1}'
    assert len(calls) == 2


@pytest.mark.parametrize("code", [400, 403, 404])
def test_client_errors_are_not_retried(script, code):
    calls = script([_http_error(code), _Resp()])
    with pytest.raises(urllib.error.HTTPError):
        H.fetch_bytes("http://x/a")
    assert len(calls) == 1


def test_a_timeout_is_not_retried(script):
    """A hung server will hang again; retrying would triple a 90-second wait."""
    calls = script([TimeoutError("timed out"), _Resp()])
    with pytest.raises(TimeoutError):
        H.fetch_bytes("http://x/a")
    assert len(calls) == 1


def test_a_certificate_failure_is_NEVER_retried(script):
    """Retrying must not become a way to wear down TLS verification."""
    err = urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
    calls = script([err, _Resp()])
    with pytest.raises(urllib.error.URLError):
        H.fetch_bytes("http://x/a")
    assert len(calls) == 1


def test_a_urlerror_wrapping_a_connection_error_is_retried(script):
    calls = script([urllib.error.URLError(ConnectionResetError(10054, "reset")), _Resp()])
    assert H.fetch_bytes("http://x/a") == b'{"ok": 1}'
    assert len(calls) == 2


def test_a_dropped_response_is_retried(script):
    calls = script([http.client.RemoteDisconnected("closed"), http.client.IncompleteRead(b"par"), _Resp()])
    assert H.fetch_bytes("http://x/a") == b'{"ok": 1}'
    assert len(calls) == 3


def test_an_unrelated_error_is_raised_immediately(script):
    calls = script([ValueError("not a network problem"), _Resp()])
    with pytest.raises(ValueError):
        H.fetch_bytes("http://x/a")
    assert len(calls) == 1


def test_the_first_success_makes_exactly_one_call(script):
    calls = script([_Resp(b"[1,2]")])
    assert H.fetch_json("http://x/a") == [1, 2]
    assert len(calls) == 1


def test_backoff_grows_with_the_attempt(monkeypatch, script):
    waits = []
    script([ConnectionResetError("a"), ConnectionResetError("b"), _Resp()])
    monkeypatch.setattr(H.time, "sleep", lambda s: waits.append(s))
    H.fetch_bytes("http://x/a")
    assert waits == [H.BACKOFF * 1, H.BACKOFF * 2]
