#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression tests for the memoized security-config load (nav-intel-09-d4).

``tools/db/storage.py::StorageCursor._apply_column_masking`` calls
``get_column_policies_for_role`` once per fetched ROW. Before this cache,
each of those calls re-read and re-parsed ~11KB of YAML, which made
high-row-count dashboard routes (/ai-roi, /dashboard/executive-view,
/dashboard/pm-view) take 30-90 seconds per request.

These tests pin the *defect* — the number of YAML parses — rather than
wall-clock, so they stay meaningful on a slow or loaded CI box.
"""
from __future__ import annotations

import importlib

import pytest
import yaml

colsec = importlib.import_module("tools.security.column_security")


@pytest.fixture(autouse=True)
def _clean_cache():
    """Every test starts and ends with a cold cache."""
    colsec.reset_config_cache()
    yield
    colsec.reset_config_cache()


@pytest.fixture
def count_parses(monkeypatch):
    """Count yaml.safe_load calls made by the module under test."""
    calls = {"n": 0}
    real = yaml.safe_load

    def counting(stream):
        calls["n"] += 1
        return real(stream)

    monkeypatch.setattr(yaml, "safe_load", counting)
    return calls


def test_repeated_loads_parse_yaml_once(count_parses):
    """The whole point: N calls must not mean N parses."""
    for _ in range(50):
        colsec._load_config()
    assert count_parses["n"] == 1


def test_policy_lookup_is_cached_across_rows(count_parses):
    """Simulate the per-row masking hot path that caused the 86s route."""
    for _ in range(500):
        colsec.get_column_policies_for_role("pg_cost_volumes", "reviewer")
    assert count_parses["n"] == 1


def test_cache_invalidates_when_file_changes(tmp_path, monkeypatch, count_parses):
    """Editing security_config.yaml must take effect without a restart."""
    cfg = tmp_path / "security_config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"column_policies": [{"table": "t", "role": "r", "columns": {"a": "redact"}}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(colsec, "_resolve_config_path", lambda: cfg)

    assert colsec.get_column_policies_for_role("t", "r") == {"a": "redact"}
    assert count_parses["n"] == 1

    # Rewrite with a different strategy. Bump mtime explicitly: the write may
    # land inside the same filesystem timestamp granularity on Windows, and
    # the test must exercise invalidation rather than a coincidental stat diff.
    cfg.write_text(
        yaml.safe_dump(
            {"column_policies": [{"table": "t", "role": "r", "columns": {"a": "hash"}}]}
        ),
        encoding="utf-8",
    )
    st = cfg.stat()
    import os

    os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    assert colsec.get_column_policies_for_role("t", "r") == {"a": "hash"}
    assert count_parses["n"] == 2


def test_missing_config_is_not_cached_as_empty(tmp_path, monkeypatch):
    """A file that appears later must be picked up, not pinned as 'no policy'.

    Caching the miss would silently leave columns unmasked for the life of
    the process — a fail-open security regression.
    """
    cfg = tmp_path / "security_config.yaml"
    monkeypatch.setattr(colsec, "_resolve_config_path", lambda: cfg)

    assert colsec._load_config() == {}

    cfg.write_text(
        yaml.safe_dump(
            {"column_policies": [{"table": "t", "role": "r", "columns": {"a": "null"}}]}
        ),
        encoding="utf-8",
    )
    assert colsec.get_column_policies_for_role("t", "r") == {"a": "null"}


def test_caller_mutation_cannot_corrupt_cached_policy(tmp_path, monkeypatch):
    """get_column_policies_for_role must hand back a copy, not the cache."""
    cfg = tmp_path / "security_config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"column_policies": [{"table": "t", "role": "r", "columns": {"a": "redact"}}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(colsec, "_resolve_config_path", lambda: cfg)

    first = colsec.get_column_policies_for_role("t", "r")
    first["a"] = "TAMPERED"
    first["injected"] = "nope"

    assert colsec.get_column_policies_for_role("t", "r") == {"a": "redact"}


def test_masking_behaviour_unchanged(tmp_path, monkeypatch):
    """The cache must not alter what actually gets masked."""
    cfg = tmp_path / "security_config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "column_policies": [
                    {"table": "t", "role": "reviewer", "columns": {"secret": "redact"}}
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(colsec, "_resolve_config_path", lambda: cfg)

    row = {"id": 1, "secret": "classified"}
    assert colsec.apply_column_policy("t", "reviewer", row) == {
        "id": 1,
        "secret": "[REDACTED]",
    }
    # Unrelated role is untouched, and repeated calls stay stable under cache.
    for _ in range(3):
        assert colsec.apply_column_policy("t", "other", row) == row
