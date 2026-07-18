# CUI // SP-CTI
"""Catalog-driven coverage for tools/observability_canvas/sigma_generator.py (obx-test-02).

test_sigma_generator.py covers a handful of hand-picked (tid, src) pairs. This
file drives the generator across the *entire* unified MITRE catalog at runtime
and pins the #473 delegation envelope, extending it to the error paths the
existing suite omits:

  * Every technique in mitre_catalog.MITRE_CATALOG yields parseable Sigma YAML
    carrying an attack.<primary_tactic> tag consistent with the catalog.
  * generate_sigma_rules() envelope: keys, export types, rule_count == len(rules).
  * Error paths: an unknown technique id (fallback tactic + generic detection)
    and an empty rule list (converters stay stable, no raise).

Deterministic — no DB, no LLM. Nothing here touches the shared canvas DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.observability_canvas import sigma_generator as sg  # noqa: E402
from tools.observability_canvas.mitre_catalog import (  # noqa: E402
    MITRE_CATALOG,
    DEFAULT_TACTIC,
    primary_tactic,
)


# ── Full-catalog rule generation ─────────────────────────────────────────────

@pytest.mark.parametrize("tid", sorted(MITRE_CATALOG.keys()))
def test_every_catalog_technique_yields_valid_yaml_with_tactic_tag(tid):
    """Each catalog technique -> one parseable Sigma rule tagged attack.<tactic>."""
    rules = sg.generate_rules([(tid, "src-os-log")], design_name="cat-test")
    assert len(rules) == 1

    parsed = yaml.safe_load(rules[0])
    assert isinstance(parsed, dict)
    assert parsed.get("title")
    assert parsed.get("id")

    det = parsed.get("detection")
    assert det and "condition" in det

    tags = parsed.get("tags", [])
    # Technique tag (underscored) always present.
    assert f"attack.{tid.lower().replace('.', '_')}" in tags
    # Tactic tag must match the catalog's primary tactic (hyphen form).
    assert f"attack.{primary_tactic(tid)}" in tags


def test_catalog_covers_all_tactics_represented():
    """Sanity: generating across the catalog surfaces >1 distinct tactic tag."""
    tactics = set()
    for tid in MITRE_CATALOG:
        parsed = yaml.safe_load(sg.generate_rules([(tid, "src-endpoint")])[0])
        tactics.update(t for t in parsed["tags"] if t.startswith("attack.") and "_" not in t)
    assert len(tactics) >= 5  # execution, persistence, credential-access, ...


# ── generate_sigma_rules envelope (#473 delegation) ──────────────────────────

def _graph_with_uncovered() -> dict:
    return {
        "nodes": [
            {"id": "s1", "type": "src-os-log", "label": "OS"},
            {"id": "s2", "type": "src-iam", "label": "IAM"},
            {
                "id": "baseline", "type": "cmp-baseline", "label": "Baseline",
                "config_json": {
                    "techniques": [
                        {"id": "T1059", "covered": False},
                        {"id": "T1078", "covered": True},
                    ]
                },
            },
        ],
        "edges": [],
    }


def test_generate_sigma_rules_envelope_stable():
    result = sg.generate_sigma_rules(_graph_with_uncovered(), design_name="env")
    assert set(result.keys()) >= {
        "rule_count", "rules", "exports", "volume_estimate", "generated_at",
    }
    assert result["rule_count"] == len(result["rules"])
    assert result["rule_count"] >= 1  # T1059 uncovered x 2 sources

    exports = result["exports"]
    assert set(exports.keys()) == {"sigma_yaml", "splunk_spl", "elastic_kql", "sentinel_kql"}
    for key, val in exports.items():
        assert isinstance(val, str), f"export {key} must be a string"
        assert val.strip(), f"export {key} must be non-empty for a populated design"

    # sigma_yaml round-trips through YAML (multi-doc separated by ---).
    docs = [d for d in yaml.safe_load_all(exports["sigma_yaml"]) if isinstance(d, dict)]
    assert len(docs) == result["rule_count"]


def test_delegation_converters_stable_for_single_rule():
    """The thin _to_* adapters return non-empty strings for a real rule (#473)."""
    rules = sg.generate_rules([("T1003", "src-endpoint")])
    assert isinstance(sg._to_splunk(rules), str) and sg._to_splunk(rules).strip()
    assert isinstance(sg._to_elastic(rules), str) and sg._to_elastic(rules).strip()
    assert isinstance(sg._to_sentinel(rules), str) and sg._to_sentinel(rules).strip()


# ── Error paths ──────────────────────────────────────────────────────────────

def test_unknown_technique_falls_back_to_default_tactic():
    """An id absent from the catalog still yields valid YAML with the fallback tactic."""
    parsed = yaml.safe_load(sg.generate_rules([("T9999", "src-unknown-src")])[0])
    assert parsed["title"]
    det = parsed["detection"]
    # Generic presence-check fallback for unknown (tid, src).
    assert "condition" in det and "selection" in det
    tags = parsed["tags"]
    assert "attack.t9999" in tags
    assert f"attack.{DEFAULT_TACTIC}" in tags


def test_empty_pairs_yields_empty_list():
    assert sg.generate_rules([]) == []


def test_converters_tolerate_empty_rule_list():
    """Empty input must not raise — the export envelope stays well-typed."""
    for conv in (sg._to_splunk, sg._to_elastic, sg._to_sentinel):
        out = conv([])
        assert isinstance(out, str)


def test_generate_sigma_rules_empty_design():
    """A design with no source nodes produces a zero-rule, still-well-formed envelope."""
    result = sg.generate_sigma_rules({"nodes": [], "edges": []})
    assert result["rule_count"] == 0
    assert result["rules"] == []
    exports = result["exports"]
    assert set(exports.keys()) == {"sigma_yaml", "splunk_spl", "elastic_kql", "sentinel_kql"}
    for val in exports.values():
        assert isinstance(val, str)
