# CUI // SP-CTI
"""Tests for tools/infra_canvas/importers/aws_rgt.py.

4 cases:
  1. parse() returns one SnapshotRow per resource (3 fixture resources → 3 rows).
  2. EC2 row fields (resource_id, resource_type, csp, region) are correct.
  3. Tags from the RGT response land in tags_json.
  4. Unknown ARN service falls back to ``aws-<service>`` resource_type.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.infra_canvas.importers.aws_rgt import parse

# ── Fixture helpers ───────────────────────────────────────────────────────────

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "aws_rgt_resources.json").read_text(encoding="utf-8")
)["ResourceTagMappingList"]

_SID = "test-snapshot-id-rgt-001"
_PROJECT = "proj-govcloud-east"


# ── Case 1: one row per resource ──────────────────────────────────────────────

def test_parse_returns_one_row_per_resource():
    rows = parse(_FIXTURE, snapshot_id=_SID, project_id=_PROJECT)
    assert len(rows) == 3


# ── Case 2: EC2 row fields are mapped correctly ───────────────────────────────

def test_ec2_snapshot_row_fields():
    rows = parse(_FIXTURE, snapshot_id=_SID, project_id=_PROJECT)
    ec2_row = next(r for r in rows if "instance" in r.resource_id or r.resource_type == "aws-ec2")

    assert ec2_row.resource_id == "i-0abc123def456789"
    assert ec2_row.resource_type == "aws-ec2"
    assert ec2_row.csp == "aws"
    assert ec2_row.region == "us-gov-west-1"
    assert ec2_row.snapshot_id == _SID
    assert ec2_row.project_id == _PROJECT
    assert ec2_row.classification == "CUI"


# ── Case 3: tags land in tags_json ────────────────────────────────────────────

def test_tags_extracted_to_tags_json():
    rows = parse(_FIXTURE, snapshot_id=_SID, project_id=_PROJECT)
    ec2_row = next(r for r in rows if r.resource_type == "aws-ec2")

    tags = json.loads(ec2_row.tags_json)
    assert tags["Name"] == "app-server-01"
    assert tags["Environment"] == "prod"
    assert tags["classification"] == "CUI"


# ── Case 4: unknown ARN service uses aws-<service> fallback ───────────────────

def test_unknown_arn_service_uses_aws_prefix():
    mappings = [
        {
            "ResourceARN": "arn:aws:customservice:us-east-1:123456789012:widget/w-001",
            "Tags": [],
        }
    ]
    rows = parse(mappings, snapshot_id=_SID, project_id=_PROJECT)

    assert len(rows) == 1
    assert rows[0].resource_type == "aws-customservice"
    assert rows[0].resource_id == "w-001"
    assert rows[0].csp == "aws"
    assert rows[0].region == "us-east-1"
