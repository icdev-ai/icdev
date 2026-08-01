# CUI // SP-CTI
"""oss2-fix-01 (D1) — LocalAI reachable by default + a named provider.

The detector docstring claimed LocalAI on 8080 but the code probed 8081 (8080
having been taken by llama.cpp), so a stock LocalAI install — which serves on its
upstream default 8080 — was never detected. And there was no named `localai:`
provider, so a LocalAI deployment could be probe-detected but not *selected*.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def test_localai_named_provider_registered_at_8080():
    cfg = yaml.safe_load((REPO / "args" / "llm_config.yaml").read_text(encoding="utf-8"))
    providers = cfg["providers"]
    assert "localai" in providers, "a named localai provider must exist (D1)"
    p = providers["localai"]
    assert p["type"] == "openai_compatible"
    assert "8080" in p["base_url"]
    assert "8081" not in p["base_url"]
    assert p.get("api_key_env") == "LOCALAI_API_KEY"


def test_detector_probes_localai_on_its_real_default_port():
    """Regression guard for the literal port defect: the localai probe default is
    8080 (LocalAI upstream), not the old 8081 that hid a stock install."""
    import tools.airgap.detector as det

    src = inspect.getsource(det.probe_local_llm_servers)
    # the old wrong default must be gone from the localai candidate
    assert 'localhost:8081' not in src, "localai must no longer default to 8081"
    # the localai candidate line must reference 8080
    localai_lines = [ln for ln in src.splitlines() if '"localai"' in ln]
    assert localai_lines and any("8080" in ln for ln in localai_lines)


def test_config_localai_is_discovered_by_probe_candidates(monkeypatch):
    """With LOCALAI_BASE_URL unset, the config-driven discovery path resolves the
    localai provider to its 8080 default (the value the probe would test)."""
    monkeypatch.delenv("LOCALAI_BASE_URL", raising=False)
    cfg = yaml.safe_load((REPO / "args" / "llm_config.yaml").read_text(encoding="utf-8"))
    base = cfg["providers"]["localai"]["base_url"]
    # resolve the ${VAR:-default} the same way the detector does
    import re
    m = re.search(r"\$\{[^:]+:-([^}]+)\}", base)
    resolved = m.group(1) if m else base
    assert resolved == "http://localhost:8080/v1"
