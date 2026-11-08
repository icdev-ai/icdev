# CUI // SP-CTI
"""robots.txt for the engines' web scanners -- honoured, cached per origin, and fail closed.

A scanner asks ``allows(url, user_agent)`` before it fetches a public page. The answer comes from the
origin's robots.txt, fetched once per process through the central HTTP client:

    200            parsed, and the rules for this user agent apply
    404 / 410      no robots.txt: everything is allowed
    anything else  (401, 403, 5xx, a network error) the site's wishes cannot be read: REFUSED

Robots rules govern crawlers. A site's official, authenticated API (Reddit's OAuth Data API, GitHub's
REST API) is governed by its own terms instead, and is not asked about here.

Usage:
    from tools.http.robots import allows
    if not allows(url, UA):
        ...  # record "robots_disallowed", never fetch
"""

from __future__ import annotations

import urllib.parse
import urllib.robotparser

_CACHE: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _fetch(origin: str, user_agent: str):
    from tools.http.client import request

    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = request("GET", f"{origin}/robots.txt", headers={"User-Agent": user_agent}, timeout=15)
    except Exception:  # noqa: BLE001 -- any failure to read the rules refuses (fail closed)
        return None
    if resp.status_code == 200:
        rp.parse(resp.text.splitlines())
        return rp
    if resp.status_code in (404, 410):
        rp.parse([])
        return rp
    return None


def allows(url: str, user_agent: str) -> bool:
    """True when ``url``'s robots.txt permits ``user_agent``; False when it forbids it or cannot be read."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _CACHE:
        _CACHE[origin] = _fetch(origin, user_agent)
    rp = _CACHE[origin]
    return rp is not None and rp.can_fetch(user_agent, url)


def clear() -> None:
    """Forget every cached robots.txt (tests; a long-running daemon between cycles)."""
    _CACHE.clear()
