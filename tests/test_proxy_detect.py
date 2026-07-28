# CUI // SP-CTI
"""Proxy detection and the .env strategy it implies.

The behaviour worth protecting here is NOT "can we find a proxy" — it is
"do we refuse to write a rotating proxy down as a literal URL". A config that
bakes in today's value works until the pool rotates and then presents as the
LLM being down, which is the expensive failure this module exists to prevent.
"""

from __future__ import annotations

import pytest

from tools.cli import proxy_detect
from tools.cli.proxy_detect import ProxyInfo, detect_proxy, proxy_env_updates
from tools.cli.setup_wizard import PROVIDERS, provider_by_key, probe_provider


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    """Detection reads the ambient environment, so the real machine's proxy
    would otherwise leak in and make these tests pass or fail by location."""
    for var in ("ICDEV_LLM_PROXY", "ICDEV_LLM_PROXY_CMD", "HTTPS_PROXY",
                "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY",
                "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    # Neutralise OS-level probes; each is exercised on its own below.
    monkeypatch.setattr(proxy_detect, "_from_windows_registry", lambda: None)
    monkeypatch.setattr(proxy_detect, "_from_macos", lambda: None)


class TestDetection:
    def test_no_proxy(self):
        info = detect_proxy()
        assert not info.found
        assert info.source == "none"

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
        info = detect_proxy()
        assert info.url == "http://proxy.corp:8080"
        assert info.source == "env"
        # The environment is re-read per call, so it can move under us.
        assert info.rotating

    def test_https_wins_over_http(self, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://plain:8080")
        monkeypatch.setenv("HTTPS_PROXY", "http://secure:8443")
        assert detect_proxy().url == "http://secure:8443"

    def test_icdev_command_outranks_os_settings(self, monkeypatch):
        """An explicitly configured rotator is a decision, not a stale value to
        be overwritten by whatever the OS happens to say."""
        monkeypatch.setenv("HTTPS_PROXY", "http://stale:8080")
        monkeypatch.setenv("ICDEV_LLM_PROXY_CMD", "get-proxy.sh")
        info = detect_proxy()
        assert info.source == "icdev-command"
        assert info.rotating
        assert info.found          # found despite carrying no URL

    def test_no_proxy_list_captured(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
        monkeypatch.setenv("NO_PROXY", "localhost,.internal")
        assert detect_proxy().no_proxy == "localhost,.internal"


class TestEnvUpdates:
    def test_static_proxy_is_recorded(self):
        info = ProxyInfo(url="http://proxy:8080", source="windows-registry",
                         rotating=False)
        assert proxy_env_updates(info) == {"ICDEV_LLM_PROXY": "http://proxy:8080"}

    def test_rotating_url_is_not_baked_in(self):
        """The core invariant. Writing this URL is what produces 'it worked
        yesterday' when the pool rotates."""
        info = ProxyInfo(url="http://rotating:8080", source="env", rotating=True)
        assert proxy_env_updates(info) == {}

    def test_command_beats_everything(self):
        info = ProxyInfo(url="http://now:8080", source="env", rotating=True)
        out = proxy_env_updates(info, command="get-proxy.sh", ttl_seconds=60)
        assert out["ICDEV_LLM_PROXY_CMD"] == "get-proxy.sh"
        assert out["ICDEV_LLM_PROXY_CMD_TTL"] == "60"
        assert "ICDEV_LLM_PROXY" not in out   # never both

    def test_pac_writes_no_literal_url(self):
        info = ProxyInfo(source="pac", pac_url="http://wpad/p.pac", rotating=True)
        assert proxy_env_updates(info) == {}

    def test_no_proxy_writes_nothing(self):
        assert proxy_env_updates(ProxyInfo()) == {}


class TestKeylessProbe:
    """A gateway environment has no API key by design. Reporting that as a
    failure is reporting the intended configuration as broken."""

    def test_gateway_provider_is_offered(self):
        gw = provider_by_key("gateway")
        assert gw is not None and gw.keyless
        assert gw in PROVIDERS

    def test_gateway_without_url_fails(self):
        r = probe_provider(provider_by_key("gateway"), {})
        assert not r["ok"]
        assert "GATEWAY_URL" in r["detail"]

    def test_gateway_probes_its_own_host(self, monkeypatch):
        seen = {}

        def fake_open(host, port, timeout=0):
            seen["addr"] = (host, port)
            return True

        monkeypatch.setattr("tools.cli.setup_wizard._port_open", fake_open)
        r = probe_provider(provider_by_key("gateway"),
                           {"ICDEV_LLM_GATEWAY_URL": "https://llm.corp:8443/v1"})
        assert r["ok"]
        assert seen["addr"] == ("llm.corp", 8443)

    def test_missing_key_behind_proxy_is_not_a_failure(self, monkeypatch):
        """The regression this was written for: an unreachable vendor host is
        the EXPECTED state behind a corporate proxy, not a fault to chase."""
        monkeypatch.setattr("tools.cli.setup_wizard._port_open",
                            lambda *a, **k: False)
        r = probe_provider(provider_by_key("anthropic"),
                           {"ANTHROPIC_API_KEY": "sk-x",
                            "HTTPS_PROXY": "http://proxy:8080"})
        assert r["ok"]
        assert "proxy" in r["detail"]

    def test_unreachable_without_proxy_still_fails(self, monkeypatch):
        monkeypatch.setattr("tools.cli.setup_wizard._port_open",
                            lambda *a, **k: False)
        monkeypatch.setattr(proxy_detect, "detect_proxy", lambda: ProxyInfo())
        r = probe_provider(provider_by_key("anthropic"),
                           {"ANTHROPIC_API_KEY": "sk-x"})
        assert not r["ok"]


class TestHostPort:
    @pytest.mark.parametrize("url,expected", [
        ("https://llm.corp/v1", ("llm.corp", 443)),
        ("http://llm.corp/v1", ("llm.corp", 80)),
        ("https://llm.corp:8443/v1", ("llm.corp", 8443)),
        ("llm.corp:3128", ("llm.corp", 3128)),
    ])
    def test_split(self, url, expected):
        from tools.cli.setup_wizard import _split_host_port
        assert _split_host_port(url) == expected
