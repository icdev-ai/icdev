# CUI // SP-CTI
"""Outbound DataBridge calls must pass an egress check before the socket opens.

`saas_base`'s three `_http_*` methods called `urlopen` bare, each annotated
``# nosec B310 -- URL scheme validated`` while **no validation occurred
anywhere**. A suppression comment asserting a control that does not exist is
worse than no comment: it tells the reviewer and the scanner it is handled.

`tools/http/egress_guard.py` was already written and correct — HTTPS-only,
deny-beats-allow, DNS resolve-then-check on every A record — it simply had two
callers, neither in DataBridge.

Also covers the three `platform_connector_*` MCP tools, which raised
`ImportError` on every call because `connector_cli` imported a `get_registry`
that no module defined. They had never executed.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def connector():
    from tools.databridge.connectors.saas_base import SaaSBaseConnector

    class _T(SaaSBaseConnector):
        _connector_name = "test"
        _default_base_url = "https://api.example.com"
        _endpoints: dict = {}

        def _build_auth_headers(self, config):
            return {}

    c = _T()
    c._config = {}
    return c


# ---------------------------------------------------------------------------
# Egress
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,why", [
    ("http://api.example.com/data", "plain HTTP"),
    ("file:///etc/passwd", "file scheme"),
    ("ftp://example.com/x", "ftp scheme"),
])
def test_non_https_schemes_are_blocked(connector, url, why):
    with pytest.raises(PermissionError):
        connector._guard_egress(url)


@pytest.mark.parametrize("url,why", [
    ("https://169.254.169.254/latest/meta-data/", "cloud metadata endpoint"),
    ("https://127.0.0.1/admin", "loopback"),
    ("https://10.0.0.5/internal", "RFC1918"),
])
def test_internal_addresses_are_blocked(connector, url, why):
    """SSRF: a connector base_url is operator-supplied config, not a constant."""
    with pytest.raises(PermissionError):
        connector._guard_egress(url)


def test_air_gap_blocks_everything(connector, monkeypatch):
    """In air-gap posture no connector may reach out, allowlist or not."""
    import tools.airgap as airgap

    monkeypatch.setattr(airgap, "is_airgap", lambda: True)

    with pytest.raises(PermissionError, match="air-gap"):
        connector._guard_egress("https://api.example.com/data")


def test_missing_guard_fails_closed(connector, monkeypatch):
    """An unimportable guard means the destination is unchecked.

    That is exactly when an outbound call must not proceed. Compare
    skill_promoter's SIPA gate, which used to fail OPEN for the same shape of
    reason and so was strongest only when it was working.
    """
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "tools.http.egress_guard":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)

    with pytest.raises(PermissionError, match="egress guard unavailable"):
        connector._guard_egress("https://api.example.com/data")


def test_every_http_method_is_guarded():
    """All three verbs, not just GET — a POST is what exfiltrates."""
    import inspect

    from tools.databridge.connectors import saas_base

    for method in ("_http_get", "_http_post", "_http_delete"):
        src = inspect.getsource(getattr(saas_base.SaaSBaseConnector, method))
        assert "_guard_egress" in src, f"{method} is unguarded"
        assert src.index("_guard_egress") < src.index("urlopen"), (
            f"{method} guards AFTER opening the socket"
        )


def test_no_false_nosec_claims_remain():
    """The old comment claimed validation that did not exist."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1]
        / "tools/databridge/connectors/saas_base.py"
    ).read_text(encoding="utf-8")

    assert "URL scheme validated; internal/configured endpoints only" not in text


# ---------------------------------------------------------------------------
# The three MCP tools that had never executed
# ---------------------------------------------------------------------------


def test_connector_cli_imports():
    """It raised ImportError on `get_registry`, killing all three MCP tools."""
    import tools.platform_connectors.connector_cli as cli

    assert callable(cli.cmd_fetch)
    assert callable(cli.cmd_fetch_all)
    assert callable(cli.cmd_doctor)


def test_registry_loads_every_shipped_adapter():
    from tools.platform_connectors.connector_registry import get_registry

    registry = get_registry()
    assert set(registry.platforms()) >= {
        "github", "hackernews", "reddit", "stackoverflow", "youtube"
    }


def test_multi_backend_platforms_keep_all_backends():
    """youtube ships two backends; naming classes by hand would drop one."""
    from tools.platform_connectors.connector_registry import get_registry

    backends = get_registry().adapters_for("youtube")
    assert len(backends) >= 2
    priorities = [b.priority for b in backends]
    assert priorities == sorted(priorities), "backends must be in fallback order"


def test_unknown_platform_returns_a_result_not_an_exception():
    """The CLI and MCP tools treat a result object as their contract."""
    from tools.platform_connectors.connector_registry import get_registry

    result = get_registry().fetch("not_a_platform", "q")
    assert result.ok is False
    assert "no adapter registered" in result.error


def test_doctor_probes_without_raising():
    from tools.platform_connectors.connector_registry import get_registry

    report = get_registry().doctor()
    assert report
    for platform, results in report.items():
        for health in results:
            assert health.status in ("ok", "degraded", "unreachable", "auth_error")
