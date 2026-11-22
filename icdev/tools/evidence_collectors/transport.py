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
* a response is at most :data:`MAX_BYTES`, and the WHOLE exchange -- connect, TLS handshake, status line, headers,
  body -- ends by a wall-clock :data:`DEADLINE_S`: a watchdog shuts the request's socket at the deadline, so a drip
  anywhere is cut, not waited out (:class:`_Watch`); only the name lookup before connecting is outside it; inside :func:`budget` (one per collector run, ``service.collect``) every request also spends a shared
  byte budget and run deadline, so paging cannot pull gigabytes or run for hours;
* the JSON must be finite (no NaN or Infinity) and not nested past what the parser can hold; anything else is an
  unread source, never a crash;
* no proxy from the environment is ever used: the bearer token would travel through it.

Errors say the host, the path and the status, never a header (where a credential travels) or a body.
"""
from __future__ import annotations

import contextlib
import contextvars
import http.client
import ipaddress
import json
import math
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 20          # per socket operation
DEADLINE_S = 60.0       # per request, wall clock
_CHUNK = 64 * 1024
_BUDGET: contextvars.ContextVar = contextvars.ContextVar("ec_budget", default=None)

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


class _Watch:
    """One request's deadline: at it, every socket the request opened is shut down, so whatever read is blocked --
    the TLS handshake, the status line, a header, the body -- returns at once."""

    def __init__(self, deadline: float):
        self.deadline, self.fired, self._socks = deadline, False, []
        self._lock = threading.Lock()
        self._timer = threading.Timer(max(0.0, deadline - time.monotonic()), self._fire)
        self._timer.daemon = True

    def register(self, sock) -> None:
        with self._lock:
            self._socks.append(sock)
            fired = self.fired
        if fired:
            self._shut(sock)

    @staticmethod
    def _shut(sock) -> None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)

    def _fire(self) -> None:
        with self._lock:
            self.fired = True
            socks = list(self._socks)
        for sock in socks:
            self._shut(sock)

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *exc):
        self._timer.cancel()
        return False


_WATCH: contextvars.ContextVar = contextvars.ContextVar("ec_watch", default=None)


def _watched_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kw):
    sock = socket.create_connection(address, timeout, source_address, **kw)
    w = _WATCH.get()
    if w is not None:
        w.register(sock)
    return sock


class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _watched_connection


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _watched_connection     # the raw socket, before the TLS handshake on it


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect, _HTTPHandler, _HTTPSHandler)


@contextlib.contextmanager
def budget(*, seconds: float, max_bytes: int):
    """One collector run's shared allowance: every request inside it spends from the same bytes and deadline."""
    token = _BUDGET.set({"deadline": time.monotonic() + float(seconds), "bytes": int(max_bytes)})
    try:
        yield
    finally:
        _BUDGET.reset(token)


def _finite(v: str):
    raise ValueError(f"{v} is not a finite number")


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
    try:
        u.port      # noqa: B018 — raises on a port that is not one
    except ValueError:
        raise CollectorError(f"{u.hostname}: not a valid port") from None
    if u.scheme == "http" and not _loopback(u.hostname):
        raise CollectorError(f"{u.netloc}: plain http is allowed only to a loopback address (a local stub)")
    if u.username or u.password or u.query or u.fragment:
        raise CollectorError(f"{u.hostname}: a base URL carries no credentials, query or fragment")
    return url.rstrip("/")


def url_for(base: str, path: str, query: dict | None = None) -> str:
    """``base`` + ``path`` (+ query). An absolute ``path`` (a next-page link) must be on the base's own host."""
    b = urllib.parse.urlsplit(check_base(base))
    p = urllib.parse.urlsplit(str(path))
    if p.fragment:
        raise CollectorError(f"{b.netloc}: a link carries no fragment")
    if p.scheme or p.netloc or str(path).startswith("//"):
        if (p.scheme.lower(), p.netloc.lower()) != (b.scheme, b.netloc.lower()):
            raise CollectorError(f"{b.netloc}: a next-page link off the configured host is refused")
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
    run = _BUDGET.get()
    deadline = time.monotonic() + DEADLINE_S
    cap = MAX_BYTES
    if run is not None:
        deadline = min(deadline, run["deadline"])
        cap = min(cap, run["bytes"])
    if time.monotonic() >= deadline:
        raise CollectorError(f"{where}: past the collector's deadline, not sent")
    watch = _Watch(deadline)
    token = _WATCH.set(watch)
    try:
        with watch, _OPENER.open(req, timeout=max(0.1, min(TIMEOUT_S, deadline - time.monotonic()))) as resp:  # nosec B310 — scheme checked above
            chunks, got = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise CollectorError(f"{where}: past the request's deadline ({DEADLINE_S:g}s), cut")
                part = resp.read1(_CHUNK)       # what has arrived, not "wait for the whole chunk"
                if not part:
                    break
                got += len(part)
                if got > cap:
                    raise CollectorError(f"{where}: past the response or the collector's byte budget, cut")
                chunks.append(part)
            raw = b"".join(chunks)
        if watch.fired:
            raise CollectorError(f"{where}: past the request's deadline, cut")
        if run is not None:
            run["bytes"] -= len(raw)
    except CollectorError:
        raise
    except urllib.error.HTTPError as exc:
        if watch.fired:
            raise CollectorError(f"{where}: past the request's deadline, cut") from None
        raise CollectorError(f"{where}: HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException, ValueError) as exc:
        if watch.fired or time.monotonic() >= deadline - 0.05:
            raise CollectorError(f"{where}: past the request's deadline, cut") from None
        reason = getattr(exc, "reason", exc)
        raise CollectorError(f"{where}: unreachable or unreadable ({type(reason).__name__})") from None
    finally:
        _WATCH.reset(token)
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_finite)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise CollectorError(f"{where}: the response is not finite, well-formed JSON") from None


def finite(v) -> float | None:
    """A number a source sent, when it is a real, finite number (not a bool, NaN or infinity); else None."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None
