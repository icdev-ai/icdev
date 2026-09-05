#!/usr/bin/env python3
# CUI // SP-CTI
"""SSRF-safe egress gate (oss-filter-03).

ICDEV already had a correct outbound-request gate — and almost nothing used it.
It lived in ``tools/doc_modernization/link_check.py``, a link-rot checker, which
is not a location anyone writing a new fetch path would think to look. This
module is that gate, moved to where fetch code lives, unchanged in behaviour.

``tools/doc_modernization/link_check.py`` re-exports from here, so its own
callers are unaffected.

What it enforces, in order — and the order matters:

  1. **https only.** Not a style preference: a plaintext fetch of a URL an
     attacker influenced is a downgrade they can exploit.
  2. **Denylist beats allowlist**, suffix-matched, so ``example.com`` covers
     every subdomain. Deny-wins is the only safe precedence.
  3. **Resolve, then check every answer.** This is the part naive SSRF guards
     miss: a hostname that passes a string check can still resolve to
     ``169.254.169.254`` (cloud instance metadata), ``127.0.0.1``, or an RFC1918
     address. The check is on the RESOLVED addresses, and if ANY answer is
     non-routable the request is refused — a multi-A-record host cannot smuggle
     one internal address past the gate.
  4. **Literal IPs skip DNS but are still range-checked**, so ``https://10.0.0.1``
     is refused just as ``https://internal.example`` resolving there would be.

Not covered here, on purpose: redirects. Each hop must be re-validated by the
caller, which is why ``link_check`` pairs this with a no-follow opener. A guard
that validates only the first URL is a guard an open redirect walks straight
through.

Usage::

    from tools.http.egress_guard import egress_guard

    allowed, reason, ips = egress_guard(url, {"allowlist": [...], "denylist": [...]})
    if not allowed:
        raise PermissionError(f"egress refused: {reason}")

Default-off semantics are preserved: this module decides nothing on its own —
the CALLER supplies the allow/deny config, and an empty allowlist means "no
allowlist restriction", matching the original behaviour that
``tools/browser/scope.py`` already depends on.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.http.egress_guard")


def _ip_is_denied(ip: ipaddress._BaseAddress) -> bool:
    """True for any address that is not globally routable public space.

    The union below covers loopback, private/unique-local, link-local (incl. the
    instance-metadata address), multicast, unspecified and reserved ranges for
    both IPv4 and IPv6.
    """
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def host_allowed(host: str, cfg: dict) -> tuple[bool, str]:
    """Apply ONLY the allow/deny HOST rules to *host*. No scheme, no DNS.

    Factored out of ``egress_guard`` so the rule has one statement rather than
    two. ``egress_guard`` is an INTERNET gate: https-only, and every resolved
    address range-checked, which by construction refuses loopback. A destination
    that is loopback ON PURPOSE -- a local service emulator reached over plain
    http -- can therefore never pass it, so a connector talking to one either
    calls no guard at all (declared allowlist, nothing enforcing it: the defect
    cef-fnd-03 named) or calls a guard that refuses every legitimate call.

    This is the part of the decision that still applies: WHICH HOST may be
    contacted. Deny beats allow, suffix-matched, identical to the rules inside
    ``egress_guard`` because it IS those rules. An empty allowlist means "no
    allowlist restriction", preserving the module's default-off semantics.

    It is deliberately NOT an SSRF gate and must not be described as one: it
    performs no DNS resolution, so a hostname that passes here can still resolve
    to 169.254.169.254. Use it only where the range check is known to be the
    wrong instrument, and say so at the call site.
    """
    host_l = str(host or "").lower()
    if not host_l:
        return (False, "no_host")
    denylist = [str(h).lower() for h in (cfg.get("denylist") or [])]
    if any(host_l == d or host_l.endswith("." + d) for d in denylist):
        return (False, "denylisted")
    allowlist = [str(h).lower() for h in (cfg.get("allowlist") or [])]
    if allowlist and not any(host_l == a or host_l.endswith("." + a) for a in allowlist):
        return (False, "not_allowlisted")
    return (True, "ok")


def egress_guard(url: str, cfg: dict, resolver=None) -> tuple[bool, str, list[str]]:
    """Decide whether ``url`` may be contacted. Returns (allowed, reason, ips).

    Enforced before any outbound connection. ``resolver`` defaults to
    ``socket.getaddrinfo`` and is injectable for testing.
    """
    resolver = resolver or socket.getaddrinfo
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return (False, "malformed", [])

    if (parts.scheme or "").lower() != "https":
        return (False, "scheme_not_https", [])
    host = parts.hostname
    if not host:
        return (False, "no_host", [])
    # Denylist beats allowlist. Suffix match so "example.com" covers subdomains.
    # One statement of that rule, shared with host_allowed() above.
    ok, reason = host_allowed(host, cfg)
    if not ok:
        return (False, reason, [])

    # A literal-IP host skips DNS but is still range-checked.
    try:
        lit = ipaddress.ip_address(host)
    except ValueError:
        lit = None
    if lit is not None:
        if _ip_is_denied(lit):
            return (False, "denied_ip_range", [str(lit)])
        return (True, "ok", [str(lit)])

    # Resolve, then check EVERY answer. One non-public address anywhere in the
    # answer set fails the whole URL — the resolve-then-check step.
    port = parts.port or 443
    try:
        infos = resolver(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Unresolvable: a dead domain and a missing network are indistinguishable
        # here, so we refuse to call it rotted — mapped to 'not_checked' upstream.
        return (False, "unresolved", [])
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("link_check: resolver error for %s: %s", host, exc)
        return (False, "unresolved", [])

    ips: list[str] = []
    for info in infos:
        try:
            ip_str = info[4][0]
        except (IndexError, TypeError):
            continue
        ips.append(ip_str)
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return (False, "denied_ip_range", ips)
        if _ip_is_denied(ip_obj):
            return (False, "denied_ip_range", ips)
    if not ips:
        return (False, "unresolved", [])
    return (True, "ok", ips)

__all__ = ["egress_guard", "host_allowed"]
