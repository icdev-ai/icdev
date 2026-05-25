#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for `icdev enable|disable|status|list`."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


from tools.cli.enable import (
    TOGGLES,
    _normalize,
    _parse_env,
    _rewrite_flags,
    _resolve_toggle,
    get_status,
    set_toggles,
)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def test_normalize_truthy_values():
    for v in ("true", "True", "1", "yes", "on", " TRUE "):
        assert _normalize(v), f"{v!r} should be truthy"


def test_normalize_falsy_values():
    for v in ("false", "0", "", "no", "off", "random"):
        assert not _normalize(v), f"{v!r} should be falsy"


def test_parse_env_skips_comments_and_blanks():
    text = "# comment\n\nFOO=bar\n# another\nBAZ=qux\n"
    parsed = _parse_env(text)
    assert set(parsed.keys()) == {"FOO", "BAZ"}
    assert parsed["FOO"][1] == "bar"


def test_rewrite_flags_updates_existing():
    text = "FOO=false\nBAR=false\n"
    out = _rewrite_flags(text, {"FOO": "true"})
    assert "FOO=true" in out
    assert "BAR=false" in out  # untouched


def test_rewrite_flags_appends_missing():
    text = "FOO=false\n"
    out = _rewrite_flags(text, {"BAR": "true"})
    assert "FOO=false" in out
    assert "BAR=true" in out


def test_resolve_toggle_case_insensitive():
    assert _resolve_toggle("BOUNDARY") == TOGGLES["boundary"]
    assert _resolve_toggle("canvas_kg") == TOGGLES["canvas-kg"]


def test_resolve_toggle_unknown_returns_none():
    assert _resolve_toggle("not-a-real-canvas") is None


# ---------------------------------------------------------------------------
# set_toggles (writes to .env)
# ---------------------------------------------------------------------------


def test_set_toggles_enable_flips_both_flags(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "ICDEV_BOUNDARY_ENABLED=false\nICDEV_BDC_ENABLED=false\n",
        encoding="utf-8",
    )
    result = set_toggles(env, ["boundary"], True)
    assert not result["unknown"]
    text = env.read_text(encoding="utf-8")
    assert "ICDEV_BOUNDARY_ENABLED=true" in text
    assert "ICDEV_BDC_ENABLED=true" in text


def test_set_toggles_disable_flips_both_flags(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "ICDEV_BOUNDARY_ENABLED=true\nICDEV_BDC_ENABLED=true\n",
        encoding="utf-8",
    )
    result = set_toggles(env, ["boundary"], False)
    assert not result["unknown"]
    text = env.read_text(encoding="utf-8")
    assert "ICDEV_BOUNDARY_ENABLED=false" in text
    assert "ICDEV_BDC_ENABLED=false" in text


def test_set_toggles_multiple_names(tmp_path):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    set_toggles(env, ["boundary", "security", "pipeline"], True)
    text = env.read_text(encoding="utf-8")
    for flag in [
        "ICDEV_BOUNDARY_ENABLED", "ICDEV_BDC_ENABLED",
        "ICDEV_SECURITY_ENABLED", "ICDEV_SDC_ENABLED",
        "ICDEV_PIPELINE_ENABLED", "ICDEV_PDC_ENABLED",
    ]:
        assert f"{flag}=true" in text, f"missing {flag}"


def test_set_toggles_reports_unknown(tmp_path):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    result = set_toggles(env, ["not-a-canvas"], True)
    assert result["unknown"] == ["not-a-canvas"]
    # No writes should happen when there are unknown names
    assert env.read_text(encoding="utf-8") == ""


def test_set_toggles_preserves_other_content(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# important comment\nANTHROPIC_API_KEY=secret\n"
        "ICDEV_BOUNDARY_ENABLED=false\nICDEV_BDC_ENABLED=false\n",
        encoding="utf-8",
    )
    set_toggles(env, ["boundary"], True)
    text = env.read_text(encoding="utf-8")
    assert "# important comment" in text
    assert "ANTHROPIC_API_KEY=secret" in text


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


def test_get_status_reports_enabled_and_disabled(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "ICDEV_BOUNDARY_ENABLED=true\nICDEV_BDC_ENABLED=true\n"
        "ICDEV_SDC_ENABLED=true\n"
        # Note: SECURITY flag NOT set — should be considered off
        , encoding="utf-8",
    )
    result = get_status(env)
    assert result["env_file_exists"]
    by_name = {r["name"]: r for r in result["toggles"]}
    assert by_name["boundary"]["enabled"] is True
    # security requires BOTH flags; only one is true → disabled
    assert by_name["security"]["enabled"] is False
    # data wasn't touched at all → disabled
    assert by_name["data"]["enabled"] is False


def test_get_status_missing_env_reports_all_off(tmp_path):
    env = tmp_path / ".env"
    result = get_status(env)
    assert not result["env_file_exists"]
    assert result["enabled_count"] == 0
    assert all(not r["enabled"] for r in result["toggles"])


def test_all_documented_toggles_have_flags_and_descriptions():
    from tools.cli.enable import DESCRIPTIONS
    for name, flags in TOGGLES.items():
        assert flags, f"{name} has no flags"
        assert all(f.isupper() or f.startswith("ICDEV_") for f in flags), (
            f"{name} has suspicious flag names: {flags}"
        )
        assert name in DESCRIPTIONS, f"{name} missing description"
