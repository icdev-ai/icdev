# CUI // SP-CTI
"""lpx-keys-04 — local canvas copy egress: per-person keys, fail-closed.

Proves:
  * in local-copy mode a cloud provider is redirected to the gateway and the
    credential presented is ALWAYS the virtual-key env var — never the real
    provider key, even when a real key is set in the environment;
  * a local copy with no virtual key fails CLOSED with a clear message
    (enforce_local_copy_egress raises) rather than falling back to a real key;
  * local/GovCloud provider types (ollama/bedrock) are untouched;
  * the onboarding .env template ships NO real-provider-key slot.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

pg = importlib.import_module("tools.llm.proxy_gateway")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("ICDEV_LLM_LOCAL_COPY", "ICDEV_LLM_PROXY_ENABLED",
                "ICDEV_LLM_PROXY_BASE_URL", "ICDEV_LLM_PROXY_VIRTUAL_KEY",
                "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_local_copy_forces_virtual_key_never_real(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_LOCAL_COPY", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_BASE_URL", "https://gw.internal:4000")
    monkeypatch.setenv("ICDEV_LLM_PROXY_VIRTUAL_KEY", "sk-icdev-personal")
    # A real key present in the environment MUST be ignored.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL-should-never-be-used")

    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY",
           "base_url": "https://api.anthropic.com"}
    out = pg.apply_gateway_to_provider_cfg("anthropic", cfg)
    assert out["api_key_env"] == "ICDEV_LLM_PROXY_VIRTUAL_KEY"  # not the real key env
    assert out["base_url"].startswith("https://gw.internal:4000")
    # No real-key fallback should even be possible.
    pg.enforce_local_copy_egress(out)  # virtual key present -> no raise


def test_local_copy_redirects_even_without_proxy_enabled(monkeypatch):
    # A local copy has no legitimate direct path, so redirect regardless of the
    # ICDEV_LLM_PROXY_ENABLED flag.
    monkeypatch.setenv("ICDEV_LLM_LOCAL_COPY", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_VIRTUAL_KEY", "sk-icdev-x")
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}
    out = pg.apply_gateway_to_provider_cfg("anthropic", cfg)
    assert out["api_key_env"] == "ICDEV_LLM_PROXY_VIRTUAL_KEY"
    assert out is not cfg  # a copy, redirected


def test_no_virtual_key_fails_closed_with_clear_message(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_LOCAL_COPY", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL")  # tempting, must be refused
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}
    with pytest.raises(pg.LocalCopyEgressError) as exc:
        pg.enforce_local_copy_egress(cfg)
    msg = str(exc.value)
    assert "virtual key" in msg.lower()
    assert "will NOT fall back" in msg or "not fall back" in msg.lower()
    # Even so, the rewritten cfg never points at the real key.
    out = pg.apply_gateway_to_provider_cfg("anthropic", cfg)
    assert out["api_key_env"] == "ICDEV_LLM_PROXY_VIRTUAL_KEY"


def test_local_types_untouched(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_LOCAL_COPY", "true")
    for ptype in ("ollama", "bedrock"):
        cfg = {"type": ptype, "api_key_env": f"{ptype.upper()}_KEY"}
        # No redirect, no raise for local/GovCloud paths.
        assert pg.apply_gateway_to_provider_cfg(ptype, cfg) is cfg
        pg.enforce_local_copy_egress(cfg)


def test_not_local_copy_is_noop(monkeypatch):
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}
    # Proxy disabled + not local copy -> original object returned, no raise.
    assert pg.apply_gateway_to_provider_cfg("anthropic", cfg) is cfg
    pg.enforce_local_copy_egress(cfg)


def test_preflight_reports_status(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_LOCAL_COPY", "true")
    monkeypatch.setenv("ICDEV_LLM_PROXY_BASE_URL", "http://127.0.0.1:1")  # unreachable
    # No virtual key -> not ready.
    s = pg.local_copy_preflight(timeout=0.2)
    assert s["local_copy"] is True
    assert s["ok"] is False
    assert s["virtual_key_set"] is False


def test_onboarding_template_has_no_real_key_slot():
    tmpl = (REPO_ROOT / ".env.local-copy.template").read_text(encoding="utf-8")
    assert "ICDEV_LLM_LOCAL_COPY=true" in tmpl
    assert "ICDEV_LLM_PROXY_VIRTUAL_KEY=" in tmpl
    # No active real-provider-key assignments (commented mentions are fine).
    for line in tmpl.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        for real in ("ANTHROPIC_API_KEY=", "OPENAI_API_KEY=",
                     "AWS_SECRET_ACCESS_KEY=", "LITELLM_MASTER_KEY="):
            assert not stripped.startswith(real), f"real key slot present: {line}"
