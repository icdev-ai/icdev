# CUI // SP-CTI
"""The one read-only HTTP path every collector uses (icx-ent-02).

Bounded by construction:

* the base URL is the reviewed configuration's (``args/evidence_collectors.yaml``), never a caller's; HTTPS, or plain
  HTTP to a loopback address only (a local stub);
* every request stays on that host: a path is joined to the base, an absolute next-page link on another host is
  refused, and redirects are never followed;
* ``GET`` only, except the few POSTs that only READ, named here (:data:`READ_POSTS`): an AWS JSON-protocol
  ``Describe*`` call and an OAuth 2.0 client-credentials token request. A collector that asks for anything else is
  refused before a byte is sent;
* a response is at most :data:`MAX_BYTES`; a timeout is fixed.

Errors say the host, the path and the status, never a header (where a credential travels) or a body.
"""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 20

#: The POST operations that only read. ``op`` names one; any other POST is refused.
READ_POSTS = frozenset({
    "aws-config:DescribeComplianceByConfigRule",
    "aws-config:DescribeConfigRuleEvaluationStatus",
    "oauth2:client_credentials_token",
})


class CollectorError(Exception):
    """A source could not be read. Its message names the host and path, never a credential."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectorError(f"{urllib.parse.urlsplit(req.full_url).netloc}: answered {code} with a redirect, "
                             "which a collector never follows")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_base(url: str) -> str:
    """A base URL a collector may read: https, or http on loopback; no credentials, query or fragment in it."""
    u = urllib.parse.urlsplit(str(url or ""))
    if u.scheme not in ("https", "http") or not u.hostname:
        raise CollectorError(f"{url!r} is not an http(s) URL")
    if u.scheme == "http" and not _loopback(u.hostname):
        raise CollectorError(f"{u.netloc}: plain http is allowed only to a loopback address (a local stub)")
    if u.username or u.password or u.query or u.fragment:
        raise CollectorError(f"{u.hostname}: a base URL carries no credentials, query or fragment")
    return url.rstrip("/")


def url_for(base: str, path: str, query: dict | None = None) -> str:
    """``base`` + ``path`` (+ query). An absolute ``path`` (a next-page link) must be on the base's own host."""
    b = urllib.parse.urlsplit(check_base(base))
    if path.startswith(("http://", "https://")):
        p = urllib.parse.urlsplit(path)
        if (p.scheme, p.netloc) != (b.scheme, b.netloc):
            raise CollectorError(f"{b.netloc}: a next-page link to {p.netloc} is off the configured host, refused")
        url = path
    else:
        url = base.rstrip("/") + "/" + path.lstrip("/")
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    return url


def request_json(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
                 op: str = "") -> Any:
    """One read. ``method`` is GET, or POST with ``op`` in :data:`READ_POSTS`. Returns the parsed JSON."""
    method = method.upper()
    if method != "GET" and not (method == "POST" and op in READ_POSTS):
        raise CollectorError(f"{method} {op or url!r} is not a read a collector may make")
    parts = urllib.parse.urlsplit(url)
    check_base(f"{parts.scheme}://{parts.netloc}")
    where = f"{parts.netloc}{parts.path}"
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as resp:  # nosec B310 — scheme checked above (https, or loopback http)
            raw = resp.read(MAX_BYTES + 1)
    except CollectorError:
        raise
    except urllib.error.HTTPError as exc:
        raise CollectorError(f"{where}: HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise CollectorError(f"{where}: unreachable ({type(reason).__name__})") from None
    if len(raw) > MAX_BYTES:
        raise CollectorError(f"{where}: the response is larger than {MAX_BYTES} bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CollectorError(f"{where}: the response is not JSON") from None
