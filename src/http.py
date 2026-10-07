"""Shared HTTP/TLS helper. PILOT-OWNED — workers must not modify this file.

Why this exists: TWSE and TPEx serve certificates whose chain omits the
Subject Key Identifier extension. Python 3.13+ enables VERIFY_X509_STRICT in
create_default_context(), so those hosts fail with
"CERTIFICATE_VERIFY_FAILED ... Missing Subject Key Identifier" even though the
chain is otherwise valid (pypi.org and google.com verify fine on this machine,
which is how we know it is the server's cert and not a local proxy).

We clear ONLY the strict RFC-5280 extra checks. CA verification and hostname
checking stay on (verify_mode=CERT_REQUIRED, check_hostname=True). Do not
replace this with ssl._create_unverified_context() or verify=False.
"""

import http.client
import json
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

__all__ = ["make_context", "fetch_bytes", "fetch_json", "fetch_xml"]

_UA = "Mozilla/5.0 (compatible; stock-analyzer/1.0)"
_TIMEOUT = 90
ATTEMPTS = 3          # total tries, not retries
BACKOFF = 1.5         # seconds, multiplied by the attempt number


def make_context() -> ssl.SSLContext:
    """A verifying TLS context that tolerates a missing Subject Key Identifier."""
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
    return ctx


_CTX = make_context()


def _transient(exc: BaseException) -> bool:
    """Is this worth trying again? Connection-level failures and server-side 5xx / 429.

    Deliberately NOT retried: a timeout (a hung server will hang again, and
    retrying would triple a 90 s wait), a certificate failure (that is not
    transient, and retrying must never be a way around verification), and any
    other 4xx (the request itself is wrong)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return _transient(exc.reason) if isinstance(exc.reason, BaseException) else False
    if isinstance(exc, TimeoutError):
        return False
    return isinstance(exc, (ConnectionError, ssl.SSLError, http.client.HTTPException))


def fetch_bytes(url: str, timeout: int = _TIMEOUT) -> bytes:
    """GET `url`. Retries a few times on transient failures (see _transient).

    The TWSE servers reset connections now and then. Without a retry a single
    reset at the very first request killed a ten-minute history build before it
    had fetched anything."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == ATTEMPTS or not _transient(exc):
                raise
            time.sleep(BACKOFF * attempt)


def fetch_json(url: str, timeout: int = _TIMEOUT):
    return json.loads(fetch_bytes(url, timeout))


def fetch_xml(url: str, timeout: int = _TIMEOUT) -> ET.Element:
    return ET.fromstring(fetch_bytes(url, timeout))
