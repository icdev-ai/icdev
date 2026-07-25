# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for the LLM provider-bypass coherence gate (lpx-router-03)."""

from tools.workflow.coherence_checker import (
    _lpx_scan_provider_bypass,
    _LPX_BYPASS_BASELINE,
    check_provider_bypass,
)


def test_detects_provider_url_literal():
    src = 'URL = "https://api.anthropic.com/v1/messages"\n'
    hits = _lpx_scan_provider_bypass(src, "tools/foo/bar.py")
    assert any(tok == "api.anthropic.com" for _, tok, _ in hits)


def test_detects_env_key_read_getenv_and_environ():
    src = (
        "import os\n"
        "a = os.getenv('OPENAI_API_KEY')\n"
        "b = os.environ.get('ANTHROPIC_API_KEY')\n"
        "c = os.environ['GOOGLE_API_KEY']\n"
    )
    hits = _lpx_scan_provider_bypass(src, "tools/foo/bar.py")
    tokens = {tok for _, tok, _ in hits}
    assert {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"} <= tokens


def test_docstring_url_not_flagged():
    src = '"""See https://api.openai.com for details."""\nx = 1\n'
    hits = _lpx_scan_provider_bypass(src, "tools/foo/bar.py")
    assert hits == []


def test_non_provider_env_not_flagged():
    src = "import os\nx = os.getenv('ICDEV_LLM_PROXY_VIRTUAL_KEY')\ny = os.getenv('SOME_OTHER')\n"
    hits = _lpx_scan_provider_bypass(src, "tools/foo/bar.py")
    assert hits == []


def test_network_routes_not_in_baseline():
    """The sites migrated in lpx-router-01/02 must NOT be grandfathered, so a
    regression there would fail the gate."""
    for sig in _LPX_BYPASS_BASELINE:
        assert "tools/network/routes/" not in sig


def test_gate_passes_on_current_tree():
    """After router-01/02, the only remaining hits are grandfathered — no NEW."""
    result = check_provider_bypass()
    assert result.status == "pass", result.message
