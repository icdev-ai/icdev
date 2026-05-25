"""Tests for portfolio_greeks.py — greek_targets.yaml parsing and fallback."""
import json
import sqlite3
from pathlib import Path

import pytest

from tools.trading.options.portfolio_greeks import (
    _DEFAULTS,
    _TARGETS_PATH,
    compute_portfolio_greeks,
    load_greek_targets,
)


# ---------------------------------------------------------------------------
# greek_targets.yaml parsing
# ---------------------------------------------------------------------------

EXPECTED_KEYS = {"delta_target", "delta_tolerance", "vega_max_per_10k", "theta_floor"}


def test_yaml_has_expected_keys():
    cfg = load_greek_targets()
    assert EXPECTED_KEYS.issubset(cfg.keys())


def test_yaml_values_match_file():
    cfg = load_greek_targets()
    assert cfg["delta_target"] == pytest.approx(0.0)
    assert cfg["delta_tolerance"] == pytest.approx(0.10)
    assert cfg["vega_max_per_10k"] == pytest.approx(0.05)
    assert cfg["theta_floor"] == pytest.approx(-0.02)


def test_yaml_file_exists():
    assert _TARGETS_PATH.exists(), f"args/greek_targets.yaml not found at {_TARGETS_PATH}"


# ---------------------------------------------------------------------------
# load_greek_targets — success path
# ---------------------------------------------------------------------------

def test_load_from_explicit_path(tmp_path):
    yaml_file = tmp_path / "targets.yaml"
    yaml_file.write_text(
        "delta_target: 0.5\ndelta_tolerance: 0.20\nvega_max_per_10k: 0.08\ntheta_floor: -0.01\n",
        encoding="utf-8",
    )
    cfg = load_greek_targets(yaml_file)
    assert cfg["delta_target"] == pytest.approx(0.5)
    assert cfg["delta_tolerance"] == pytest.approx(0.20)
    assert cfg["vega_max_per_10k"] == pytest.approx(0.08)
    assert cfg["theta_floor"] == pytest.approx(-0.01)


def test_load_merges_extra_keys(tmp_path):
    yaml_file = tmp_path / "extra.yaml"
    yaml_file.write_text("delta_target: 1.0\ncustom_key: 99\n", encoding="utf-8")
    cfg = load_greek_targets(yaml_file)
    assert cfg["custom_key"] == 99
    assert cfg["delta_tolerance"] == pytest.approx(_DEFAULTS["delta_tolerance"])


# ---------------------------------------------------------------------------
# load_greek_targets — fallback cases
# ---------------------------------------------------------------------------

def test_fallback_missing_file(tmp_path):
    cfg = load_greek_targets(tmp_path / "nonexistent.yaml")
    assert cfg == _DEFAULTS


def test_fallback_invalid_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{ unclosed: [bracket", encoding="utf-8")
    cfg = load_greek_targets(bad)
    assert cfg == _DEFAULTS


def test_fallback_non_dict_yaml(tmp_path):
    """YAML that is valid but not a mapping should fall back to defaults."""
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("- item1\n- item2\n", encoding="utf-8")
    cfg = load_greek_targets(scalar)
    assert cfg == _DEFAULTS


def test_fallback_empty_file(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    cfg = load_greek_targets(empty)
    assert cfg == _DEFAULTS


def test_fallback_returns_independent_copy():
    """Mutating the returned dict must not alter _DEFAULTS."""
    cfg = load_greek_targets(Path("nonexistent_xyz.yaml"))
    cfg["delta_target"] = 999.0
    assert _DEFAULTS["delta_target"] != 999.0


# ---------------------------------------------------------------------------
# compute_portfolio_greeks — minimal smoke tests (no real DB required)
# ---------------------------------------------------------------------------

def _make_conn(rows: list[tuple]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE ad_sandbox_option_positions"
        " (user_id TEXT, qty REAL, last_greeks_json TEXT)"
    )
    for row in rows:
        conn.execute(
            "INSERT INTO ad_sandbox_option_positions VALUES (?,?,?)", row
        )
    conn.commit()
    return conn


def test_empty_portfolio():
    conn = _make_conn([])
    result = compute_portfolio_greeks(conn=conn)
    assert result["position_count"] == 0
    assert result["stale_count"] == 0
    assert result["net_delta"] == 0.0


def test_single_long_call():
    greeks = {"delta": 0.5, "gamma": 0.02, "theta": -0.03, "vega": 0.1}
    conn = _make_conn([("default", 1.0, json.dumps(greeks))])
    result = compute_portfolio_greeks(conn=conn)
    assert result["net_delta"] == pytest.approx(0.5 * 100.0)
    assert result["stale_count"] == 0


def test_stale_position_counted():
    conn = _make_conn([("default", 1.0, None)])
    result = compute_portfolio_greeks(conn=conn)
    assert result["stale_count"] == 1
    assert result["net_delta"] == 0.0


def test_invalid_json_treated_as_stale():
    conn = _make_conn([("default", 1.0, "not-json")])
    result = compute_portfolio_greeks(conn=conn)
    assert result["stale_count"] == 1
