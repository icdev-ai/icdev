# CUI // SP-CTI
"""SSRF-safe egress gate (oss-filter-03).

The gate itself is not new — it has been correct since it was written. What was
wrong is that it lived in ``tools/doc_modernization/link_check.py``, a link-rot
checker, so nobody writing a new fetch path ever found it. These tests move with
it and pin the behaviour that makes it worth finding.

The property that separates this from a naive SSRF check: **the decision is made
on the RESOLVED addresses, not the hostname string.** A hostname that passes any
amount of string validation can still resolve to cloud instance metadata at
169.254.169.254, to loopback, or to RFC1918 space.
"""
from __future__ import annotations

import pytest

from tools.http.egress_guard import egress_guard


def _resolver(*addresses):
    """Fake getaddrinfo returning the given addresses, so tests never touch DNS."""
    def _fake(host, port, *a, **kw):
        return [(2, 1, 6, "", (addr, 443)) for addr in addresses]
    return _fake


# ── Scheme ───────────────────────────────────────────────────────────────────


def test_plaintext_http_is_refused():
    """Not style: a plaintext fetch of an attacker-influenced URL is a downgrade."""
    allowed, reason, _ = egress_guard("http://example.com", {})
    assert allowed is False
    assert reason == "scheme_not_https"


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "gopher://x"])
def test_non_https_schemes_are_refused(url):
    assert egress_guard(url, {})[0] is False


def test_malformed_url_is_refused_not_crashed():
    assert egress_guard("::::", {})[0] is False


def test_missing_host_is_refused():
    assert egress_guard("https://", {})[1] in ("no_host", "malformed")


# ── Allow / deny precedence ──────────────────────────────────────────────────


def test_denylist_beats_allowlist():
    """Deny-wins is the only safe precedence for a security control."""
    cfg = {"allowlist": ["example.com"], "denylist": ["example.com"]}
    allowed, reason, _ = egress_guard("https://example.com", cfg)
    assert allowed is False
    assert reason == "denylisted"


def test_denylist_matches_subdomains():
    cfg = {"denylist": ["example.com"]}
    assert egress_guard("https://secrets.internal.example.com", cfg)[1] == "denylisted"


def test_allowlist_matches_subdomains():
    cfg = {"allowlist": ["example.com"]}
    allowed, reason, _ = egress_guard(
        "https://docs.example.com", cfg, resolver=_resolver("93.184.216.34")
    )
    assert allowed is True, reason


def test_host_outside_the_allowlist_is_refused():
    cfg = {"allowlist": ["example.com"]}
    assert egress_guard("https://evil.test", cfg)[1] == "not_allowlisted"


def test_empty_allowlist_imposes_no_restriction():
    """Default-off semantics, preserved from the original.

    tools/browser/scope.py already relies on this: IT supplies the allowlist, and
    an empty one here must not mean "deny everything" or the browser scope layer
    would double-deny.
    """
    allowed, reason, _ = egress_guard(
        "https://example.com", {"allowlist": []}, resolver=_resolver("93.184.216.34")
    )
    assert allowed is True, reason


# ── The part naive guards miss ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr,what",
    [
        ("169.254.169.254", "cloud instance metadata"),
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "RFC1918"),
        ("192.168.1.1", "RFC1918"),
        ("172.16.0.1", "RFC1918"),
        ("0.0.0.0", "unspecified"),
        ("::1", "IPv6 loopback"),
        ("fe80::1", "IPv6 link-local"),
        ("fc00::1", "IPv6 unique-local"),
    ],
)
def test_public_hostname_resolving_internally_is_refused(addr, what):
    """A hostname that passes every string check can still point inward."""
    allowed, reason, ips = egress_guard(
        "https://totally-legit.example", {}, resolver=_resolver(addr)
    )
    assert allowed is False, f"{what} ({addr}) was allowed"
    assert reason == "denied_ip_range"
    assert addr in ips


def test_any_internal_answer_refuses_the_whole_host():
    """A multi-A-record host must not smuggle one internal address through.

    Refusing only if ALL answers are internal would let an attacker publish one
    public and one private A record and win.
    """
    allowed, reason, _ = egress_guard(
        "https://mixed.example", {}, resolver=_resolver("93.184.216.34", "10.0.0.5")
    )
    assert allowed is False
    assert reason == "denied_ip_range"


def test_literal_internal_ip_is_range_checked_without_dns():
    for addr in ("https://10.0.0.1", "https://127.0.0.1", "https://169.254.169.254"):
        assert egress_guard(addr, {})[0] is False, addr


def test_public_address_is_allowed():
    allowed, reason, ips = egress_guard(
        "https://example.com", {}, resolver=_resolver("93.184.216.34")
    )
    assert allowed is True, reason
    assert ips == ["93.184.216.34"]


def test_unresolvable_host_is_refused():
    def _boom(*a, **kw):
        raise OSError("NXDOMAIN")

    assert egress_guard("https://nope.invalid", {}, resolver=_boom)[0] is False


def test_no_addresses_returned_is_refused():
    assert egress_guard("https://empty.example", {}, resolver=lambda *a, **k: [])[0] is False


# ── The move itself ──────────────────────────────────────────────────────────


def test_legacy_import_path_still_resolves_to_the_same_function():
    """link_check's own callers must not break because the module moved."""
    from tools.doc_modernization.link_check import egress_guard as legacy

    assert legacy is egress_guard


def test_browser_scope_imports_from_the_new_home():
    """New code should look where fetch code lives, not in a link-rot checker."""
    import pathlib

    src = pathlib.Path(
        pathlib.Path(__file__).resolve().parents[2] / "tools" / "browser" / "scope.py"
    ).read_text(encoding="utf-8")
    assert "from tools.http.egress_guard import egress_guard" in src
    assert "from tools.doc_modernization.link_check import egress_guard" not in src
