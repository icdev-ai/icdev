# CUI // SP-CTI
"""Tests for tools/infra_canvas/importers/pulumi_state.py.

4 cases:
  1. Non-custom and provider meta-resources are skipped; only 2 rows returned.
  2. VPC snapshot row fields (resource_id, resource_type, csp, region) are correct.
  3. Tags from inputs.tags land in tags_json.
  4. Unknown Pulumi type falls back to ``unknown-<type>`` resource_type.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.infra_canvas.importers.pulumi_state import parse

# ── Fixture helpers ───────────────────────────────────────────────────────────

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "pulumi_stack_export.json").read_text(encoding="utf-8")
)

_SID = "test-snapshot-id-0001"
_PROJECT = "proj-govcloud-east"


# ── Case 1: skip non-custom and provider resources ────────────────────────────

def test_non_custom_and_provider_resources_skipped():
    rows = parse(_FIXTURE, snapshot_id=_SID, project_id=_PROJECT)
    # Fixture has 4 resources: 1 Stack (custom=False), 1 provider, 2 real resources
    assert len(rows) == 2


# ── Case 2: VPC row fields are mapped correctly ───────────────────────────────

def test_vpc_snapshot_row_fields():
    rows = parse(_FIXTURE, snapshot_id=_SID, project_id=_PROJECT)
    vpc_row = next(r for r in rows if "vpc" in r.resource_id)

    assert vpc_row.resource_id == "vpc-0abc123def456789"
    assert vpc_row.resource_type == "aws-vpc"
    assert vpc_row.csp == "aws"
    assert vpc_row.region == "us-gov-west-1"
    assert vpc_row.snapshot_id == _SID
    assert vpc_row.project_id == _PROJECT
    assert vpc_row.classification == "CUI"


# ── Case 3: tags from inputs land in tags_json ────────────────────────────────

def test_tags_extracted_to_tags_json():
    rows = parse(_FIXTURE, snapshot_id=_SID, project_id=_PROJECT)
    vpc_row = next(r for r in rows if "vpc" in r.resource_id)

    tags = json.loads(vpc_row.tags_json)
    assert tags["Name"] == "main-vpc"
    assert tags["Environment"] == "prod"
    assert tags["classification"] == "CUI"


# ── Case 4: unknown Pulumi type uses ``unknown-`` fallback ────────────────────

def test_unknown_type_uses_unknown_prefix():
    export = {
        "version": 3,
        "deployment": {
            "manifest": {"time": "2026-01-01T00:00:00Z"},
            "resources": [
                {
                    "urn": "urn:pulumi:dev::my-proj::mycorp:custom/widget:Widget::w1",
                    "custom": True,
                    "id": "widget-001",
                    "type": "mycorp:custom/widget:Widget",
                    "inputs": {},
                    "outputs": {},
                }
            ],
        },
    }
    rows = parse(export, snapshot_id=_SID, project_id=_PROJECT)

    assert len(rows) == 1
    assert rows[0].resource_type == "unknown-mycorp:custom/widget:Widget"
