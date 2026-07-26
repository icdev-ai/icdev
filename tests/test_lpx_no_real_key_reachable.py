# CUI // SP-CTI
"""lpx-vv-01 — no real provider key is reachable when the proxy is enabled.

The card's central claim, made a test rather than an assertion:

* With the proxy ENABLED, every redirectable cloud provider type resolves to the
  proxy ``base_url`` and presents a VIRTUAL key — the real provider-key env var
  is never selected, even when a real key is present in the environment.
* With the proxy DISABLED, behaviour is byte-identical to today (the config is
  the very same object) — no regression for existing deployments.
* The six call sites migrated in lpx-router-01/02 read NO real provider key and
  embed NO provider URL literal — they go through ``LLMRouter``, so the gateway
  (and the egress gate) governs them.

Shim-aware: modules are obtained via importlib and patched by attribute, never by
string form; the shared conftest schema is used (no raw sqlite3).
"""

from __future__ import annotations

import importlib

import pytest

pg = importlib.import_module("tools.llm.proxy_gateway")

# Every redirectable cloud provider type and its canonical real-key env var.
_CLOUD_TYPES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
}

_MIGRATED_SITES = (
    "tools/network/routes/ai.py",
    "tools/network/routes/topology.py",
    "tools/network/routes/twin_migration.py",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("ICDEV_LLM_PROXY_ENABLED", "ICDEV_LLM_PROXY_BASE_URL",
                "ICDEV_LLM_PROXY_VIRTUAL_KEY", "ICDEV_LLM_LOCAL_COPY",
                *_CLOUD_TYPES.values()):
        monkeypatch.delenv(var, raising=False)
    yield


# ── Proxy ENABLED: virtual key, never the real key ──────────────────────────

@pytest.mark.parametrize("ptype,real_env", list(_CLOUD_TYPES.items()))
def test_proxy_on_presents_virtual_key_not_real(monkeypatch, ptype, real_env):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_BASE_URL", "https://gw.internal:4000")
    monkeypatch.setenv("ICDEV_LLM_PROXY_VIRTUAL_KEY", "sk-icdev-virtual")
    # A REAL key present in the environment must never be selected.
    monkeypatch.setenv(real_env, "REAL-should-never-be-used")

    cfg = {"type": ptype, "api_key_env": real_env, "base_url": "https://real.example.com"}
    out = pg.apply_gateway_to_provider_cfg(ptype, cfg)

    assert out is not cfg                                  # a redirected copy
    assert out["api_key_env"] == pg.ENV_VIRTUAL_KEY       # virtual key env...
    assert out["api_key_env"] != real_env                 # ...not the real one
    assert out["base_url"].startswith("https://gw.internal:4000")
    # The original config is never mutated.
    assert cfg["api_key_env"] == real_env


def test_proxy_on_per_provider_virtual_key_override(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY",
           "proxy_api_key_env": "ICDEV_LLM_PROXY_VK_ANTHROPIC"}
    out = pg.apply_gateway_to_provider_cfg("anthropic", cfg)
    assert out["api_key_env"] == "ICDEV_LLM_PROXY_VK_ANTHROPIC"
    assert out["api_key_env"] != "ANTHROPIC_API_KEY"


# ── Proxy DISABLED: byte-identical, no regression ───────────────────────────

@pytest.mark.parametrize("ptype,real_env", list(_CLOUD_TYPES.items()))
def test_proxy_off_is_byte_identical(ptype, real_env):
    cfg = {"type": ptype, "api_key_env": real_env, "base_url": "https://real.example.com"}
    # The very same object is returned — no rewrite, no copy.
    assert pg.apply_gateway_to_provider_cfg(ptype, cfg) is cfg


def test_local_and_govcloud_never_redirected_even_when_on(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    for ptype in ("ollama", "bedrock"):
        cfg = {"type": ptype, "api_key_env": f"{ptype.upper()}_KEY"}
        assert pg.apply_gateway_to_provider_cfg(ptype, cfg) is cfg


# ── Router-level: provider built points at the proxy, not the real endpoint ──

def test_router_configured_cloud_providers_redirect_to_proxy(monkeypatch):
    """Every cloud provider in the router's ACTUAL config resolves to the proxy
    with a virtual key when the proxy is on (config-level, provider-class
    agnostic)."""
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_BASE_URL", "http://127.0.0.1:4000")
    monkeypatch.setenv("ICDEV_LLM_PROXY_VIRTUAL_KEY", "sk-icdev-virtual")

    router_mod = importlib.import_module("tools.llm.router")
    router = router_mod.LLMRouter()
    providers = router._config.get("providers", {})
    seen = 0
    for name, cfg in providers.items():
        if str(cfg.get("type", "")) not in _CLOUD_TYPES:
            continue
        seen += 1
        out = pg.apply_gateway_to_provider_cfg(name, cfg)
        assert ":4000" in out["base_url"], f"{name} not redirected: {out['base_url']!r}"
        assert out["api_key_env"] == pg.ENV_VIRTUAL_KEY, f"{name} kept a non-virtual key"
        assert out["api_key_env"] != cfg.get("api_key_env"), f"{name} kept the real key env"
    assert seen > 0, "no cloud providers found in config to verify"


def test_get_provider_actually_invokes_the_gateway(monkeypatch):
    """Proves the wiring: _get_provider routes cloud configs through the gateway
    (spy is shim-aware — patch the proxy_gateway module attribute the router
    imports at call time)."""
    monkeypatch.setenv("ICDEV_LLM_PROXY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_BASE_URL", "http://127.0.0.1:4000")
    monkeypatch.setenv("ICDEV_LLM_PROXY_VIRTUAL_KEY", "sk-icdev-virtual")

    router_mod = importlib.import_module("tools.llm.router")
    calls = []
    real = pg.apply_gateway_to_provider_cfg

    def _spy(name, cfg):
        calls.append(name)
        return real(name, cfg)

    monkeypatch.setattr(pg, "apply_gateway_to_provider_cfg", _spy, raising=True)

    router = router_mod.LLMRouter()
    for attr in ("_provider_cache", "_providers", "_provider_instances"):
        cache = getattr(router, attr, None)
        if isinstance(cache, dict):
            cache.clear()
    for name, cfg in router._config.get("providers", {}).items():
        if str(cfg.get("type", "")) in _CLOUD_TYPES:
            router._get_provider(name)
            break
    assert calls, "_get_provider did not route the provider config through the gateway"


# ── The six migrated call sites read no real key / no provider URL literal ────

def test_migrated_sites_have_no_provider_bypass():
    import pathlib

    cc = importlib.import_module("tools.workflow.coherence_checker")
    scan = cc._lpx_scan_provider_bypass
    repo_root = pathlib.Path(cc.__file__).resolve().parents[2]
    for rel in _MIGRATED_SITES:
        src = (repo_root / rel).read_text(encoding="utf-8")
        hits = scan(src, rel)
        assert hits == [], f"{rel} regressed to a real-key/URL bypass: {hits}"


def test_migrated_sites_route_through_router():
    import pathlib

    cc = importlib.import_module("tools.workflow.coherence_checker")
    repo_root = pathlib.Path(cc.__file__).resolve().parents[2]
    for rel in _MIGRATED_SITES:
        src = (repo_root / rel).read_text(encoding="utf-8")
        assert "LLMRouter" in src and ".invoke(" in src, f"{rel} no longer routes via LLMRouter"


def test_provider_bypass_gate_still_passes():
    cc = importlib.import_module("tools.workflow.coherence_checker")
    result = cc.check_provider_bypass()
    assert result.status == "pass", result.message
