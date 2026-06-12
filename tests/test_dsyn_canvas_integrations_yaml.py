# CUI // SP-CTI
"""Tests for args/dic_canvas_integrations.yaml (dsyn-adapt-01).

Validates the YAML structure, required canvas coverage, and fallback behaviour
without any Python module imports — pure YAML parse + schema assertions.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_YAML_PATH = Path(__file__).resolve().parents[1] / "args" / "dic_canvas_integrations.yaml"

TIER1_CANVASES = {
    "ndc",
    "network",
    "zig",
    "compliance",
    "sipa",
    "devsecops",
    "cloudforge",
    "aiify",
}

REQUIRED_KEYS = {"target_canvas", "target_collection_tags", "doc_types", "priority", "rationale"}
VALID_PRIORITIES = {"high", "medium", "low"}


@pytest.fixture(scope="module")
def config() -> dict:
    with open(_YAML_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_yaml_parses_without_error(config):
    assert isinstance(config, dict)


def test_integrations_key_exists(config):
    assert "integrations" in config, "Top-level 'integrations' key missing"


def test_all_tier1_canvases_covered(config):
    entries = config["integrations"]
    prefixes = {k.split(".")[0] for k in entries}
    missing = TIER1_CANVASES - prefixes
    assert not missing, f"Tier-1 canvas prefixes missing from config: {missing}"


def test_fallback_entry_exists(config):
    assert "fallback" in config["integrations"], "fallback entry is required"


def test_fallback_has_low_priority(config):
    fb = config["integrations"]["fallback"]
    assert fb["priority"] == "low"


def test_all_entries_have_required_keys(config):
    entries = config["integrations"]
    for event_type, entry in entries.items():
        missing = REQUIRED_KEYS - set(entry.keys())
        assert not missing, f"Entry '{event_type}' missing keys: {missing}"


def test_all_entries_have_valid_priority(config):
    for event_type, entry in config["integrations"].items():
        assert entry["priority"] in VALID_PRIORITIES, (
            f"Entry '{event_type}' has invalid priority '{entry['priority']}'"
        )


def test_all_entries_target_canvas_is_dic(config):
    for event_type, entry in config["integrations"].items():
        assert entry["target_canvas"] == "dic", (
            f"Entry '{event_type}' target_canvas must be 'dic', got '{entry['target_canvas']}'"
        )


def test_all_entries_have_nonempty_collection_tags(config):
    for event_type, entry in config["integrations"].items():
        tags = entry.get("target_collection_tags", [])
        assert isinstance(tags, list) and len(tags) >= 1, (
            f"Entry '{event_type}' must have at least one target_collection_tag"
        )


def test_all_entries_have_nonempty_doc_types(config):
    for event_type, entry in config["integrations"].items():
        doc_types = entry.get("doc_types", [])
        assert isinstance(doc_types, list) and len(doc_types) >= 1, (
            f"Entry '{event_type}' must have at least one doc_type"
        )


def test_all_entries_have_nonempty_rationale(config):
    for event_type, entry in config["integrations"].items():
        rationale = (entry.get("rationale") or "").strip()
        assert len(rationale) >= 20, (
            f"Entry '{event_type}' rationale is too short or missing"
        )


def test_ndc_topology_entry_is_high_priority(config):
    entry = config["integrations"].get("ndc.topology.drift_detected")
    assert entry is not None, "ndc.topology.drift_detected entry missing"
    assert entry["priority"] == "high"


def test_compliance_finding_entry_exists_and_high(config):
    entry = config["integrations"].get("compliance.finding.created")
    assert entry is not None, "compliance.finding.created entry missing"
    assert entry["priority"] == "high"


def test_crowdsource_entry_exists(config):
    assert "crowdsource" in config["integrations"]


def test_dic_internal_events_present(config):
    assert "dic.review_overdue" in config["integrations"]
    assert "dic.consistency_flag" in config["integrations"]


def test_patch_mode_is_boolean(config):
    for event_type, entry in config["integrations"].items():
        if "patch_mode" in entry:
            assert isinstance(entry["patch_mode"], bool), (
                f"Entry '{event_type}' patch_mode must be a boolean"
            )
