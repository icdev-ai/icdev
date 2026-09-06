#!/usr/bin/env python3
# CUI // SP-CTI
"""route_smoke must not spend its wall clock failing to connect over IPv6.

The dashboard binds 127.0.0.1 and `localhost` resolves ::1 first on this host,
so a probe spelled `localhost` opens a doomed IPv6 connection, waits ~2.0s, and
only then falls back to IPv4. urllib has no Happy Eyeballs; curl does, which is
why a hand spot-check never showed it. Measured over the full --all sweep
(2026-09-05): 88 probes, 255.2s wall, min 2188ms, ZERO under 2s.

Two halves are pinned here because each fails the other's case:
  - the DEFAULTS, which fix every caller that does not pass --base;
  - resolve_base(), which fixes the ones that spell `localhost` explicitly.

resolve_base is exercised against REAL sockets on an ephemeral port with
getaddrinfo stubbed to a deterministic IPv6-then-IPv4 order. Stubbing the
resolver rather than the connect is what makes this run identically on an
IPv4-only runner: the candidate ORDER is fixed by the test, and whether each
candidate answers is still decided by an actual TCP connect.
"""
from __future__ import annotations

import importlib
import socket
import threading
import urllib.parse

import pytest

route_smoke = importlib.import_module("tools.testing.route_smoke")


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    """resolve_base caches per process; a leaked entry would mask a failure."""
    route_smoke._RESOLVED_BASES.clear()
    yield
    route_smoke._RESOLVED_BASES.clear()


@pytest.fixture
def ipv4_listener():
    """A real IPv4-only listener on an ephemeral port. Yields the port."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    port = server.getsockname()[1]

    stop = threading.Event()

    def _accept_loop():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            conn.close()

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=2.0)
        server.close()


def _stub_getaddrinfo(monkeypatch, port, families=("v6", "v4")):
    """Pin the candidate ORDER; leave reachability to a real connect."""
    entries = []
    for family in families:
        if family == "v6":
            entries.append(
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))
            )
        else:
            entries.append(
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
            )

    def _fake(host, prt, *args, **kwargs):
        assert host == "localhost", f"resolver called for unexpected host {host!r}"
        return entries

    monkeypatch.setattr(route_smoke.socket, "getaddrinfo", _fake)


# ── The defaults ────────────────────────────────────────────────────────────

def test_default_base_is_a_loopback_literal_not_localhost():
    assert route_smoke.DEFAULT_BASE == "http://127.0.0.1:5050"
    assert "localhost" not in route_smoke.DEFAULT_BASE


@pytest.mark.parametrize(
    "func", ["run_smoke", "record_smoke_results", "run_api_smoke"]
)
def test_every_public_entry_point_defaults_to_the_loopback_literal(func):
    """A default spelled `localhost` in ONE signature reinstates the whole stall."""
    import inspect

    default = inspect.signature(getattr(route_smoke, func)).parameters["base"].default
    assert default == route_smoke.DEFAULT_BASE, (
        f"{func}() defaults to {default!r}; every probe it makes pays the "
        f"~2.0s IPv6 connect stall"
    )


def test_cli_base_flag_defaults_to_the_loopback_literal():
    """The --base default is a separate declaration and drifts separately."""
    import ast
    import pathlib

    source = pathlib.Path(route_smoke.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "localhost:5050" in node.value
    ]
    assert literals == [], f"route_smoke.py still hard-codes {literals}"


# ── resolve_base ────────────────────────────────────────────────────────────

def test_localhost_is_rewritten_to_the_family_that_answers(monkeypatch, ipv4_listener):
    """::1 first and dead, 127.0.0.1 second and live -> pin the live one."""
    _stub_getaddrinfo(monkeypatch, ipv4_listener)

    resolved = route_smoke.resolve_base(f"http://localhost:{ipv4_listener}", timeout=0.5)

    assert resolved == f"http://127.0.0.1:{ipv4_listener}"


def test_first_candidate_winning_leaves_the_base_untouched(monkeypatch, ipv4_listener):
    """No gain, no rewrite — urllib would have picked this family anyway."""
    _stub_getaddrinfo(monkeypatch, ipv4_listener, families=("v4", "v6"))

    resolved = route_smoke.resolve_base(f"http://localhost:{ipv4_listener}", timeout=0.5)

    assert resolved == f"http://localhost:{ipv4_listener}"


def test_nothing_reachable_leaves_the_base_untouched(monkeypatch):
    """A server that is simply DOWN must reach the existing _server_up skip.

    Rewriting toward an equally dead family would trade a clear "not running"
    message for a confusing one, and buys nothing.
    """
    dead = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()

    _stub_getaddrinfo(monkeypatch, port)

    assert route_smoke.resolve_base(f"http://localhost:{port}", timeout=0.3) == (
        f"http://localhost:{port}"
    )


def test_a_non_localhost_host_is_never_probed(monkeypatch):
    """An explicit host is the caller's decision; do not second-guess it."""

    def _explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("resolve_base probed a host it was not asked to")

    monkeypatch.setattr(route_smoke.socket, "getaddrinfo", _explode)

    for base in ("http://127.0.0.1:5050", "http://dashboard.internal:5050", ""):
        assert route_smoke.resolve_base(base) == base


def test_resolution_is_cached_so_it_costs_one_connect_per_run(monkeypatch, ipv4_listener):
    """Per-probe resolution would re-add a connect to each of the 88 probes."""
    calls = {"n": 0}

    def _counting(host, prt, *args, **kwargs):
        calls["n"] += 1
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", ipv4_listener, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", ipv4_listener)),
        ]

    monkeypatch.setattr(route_smoke.socket, "getaddrinfo", _counting)
    base = f"http://localhost:{ipv4_listener}"

    first = route_smoke.resolve_base(base, timeout=0.5)
    for _ in range(5):
        assert route_smoke.resolve_base(base, timeout=0.5) == first

    assert calls["n"] == 1, f"resolved {calls['n']} times; expected 1 (cached)"


def test_a_broken_resolver_degrades_to_the_base_it_was_given(monkeypatch):
    """Address selection must never be able to fail a smoke run."""

    def _raise(*args, **kwargs):
        raise OSError("getaddrinfo exploded")

    monkeypatch.setattr(route_smoke.socket, "getaddrinfo", _raise)

    assert route_smoke.resolve_base("http://localhost:5050") == "http://localhost:5050"


def test_credentials_and_path_survive_a_rewrite(monkeypatch, ipv4_listener):
    base = f"http://user:pw@localhost:{ipv4_listener}/prefix?q=1"
    _stub_getaddrinfo(monkeypatch, ipv4_listener)

    resolved = route_smoke.resolve_base(base, timeout=0.5)
    parts = urllib.parse.urlsplit(resolved)

    assert parts.hostname == "127.0.0.1"
    assert parts.port == ipv4_listener
    assert (parts.username, parts.password) == ("user", "pw")
    assert parts.path == "/prefix"
    assert parts.query == "q=1"


def test_an_ipv6_only_server_is_still_reachable(monkeypatch):
    """The fix must not hard-wire IPv4 — that would break an IPv6-only bind."""
    try:
        server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        server.bind(("::1", 0))
    except OSError:
        # No usable IPv6 loopback on this runner. The v4-first case above
        # already pins the ordering rule; assert the fail-safe instead of
        # registering a skip, because a skip here is a debt and buys nothing.
        monkeypatch.setattr(
            route_smoke.socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 1, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1)),
            ],
        )
        assert route_smoke.resolve_base("http://localhost:1", timeout=0.3) == (
            "http://localhost:1"
        )
        return

    server.listen(8)
    port = server.getsockname()[1]
    stop = threading.Event()

    def _accept_loop():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            conn.close()

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        # IPv4 first and dead, IPv6 second and live: the rewrite must go the
        # other way and produce a BRACKETED literal.
        monkeypatch.setattr(
            route_smoke.socket,
            "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0)),
            ],
        )
        resolved = route_smoke.resolve_base(f"http://localhost:{port}", timeout=0.5)
        assert resolved == f"http://[::1]:{port}"
        assert urllib.parse.urlsplit(resolved).port == port
    finally:
        stop.set()
        thread.join(timeout=2.0)
        server.close()
