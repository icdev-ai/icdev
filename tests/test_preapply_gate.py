# CUI // SP-CTI
"""Tests for tools/infra_canvas/preapply_gate.py — 6 cases incl. negative paths."""
from __future__ import annotations

from tools.infra_canvas.preapply_gate import _compute_delta, run_gate


# ── helpers ──────────────────────────────────────────────────────────────────


def _rc(
    address: str,
    tf_type: str,
    *,
    provider: str = "hashicorp/aws",
    actions: list[str] | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> dict:
    return {
        "address": address,
        "type": tf_type,
        "provider_name": f"registry.terraform.io/{provider}",
        "change": {
            "actions": actions or ["create"],
            "before": before,
            "after": after or {},
        },
    }


# ── Case 1: empty plan passes ─────────────────────────────────────────────────


def test_empty_plan_passes():
    result = run_gate({"resource_changes": []})
    assert result["gate"] == "pass"
    assert result["violations"] == []
    assert result["delta"] == {"add": [], "modify": [], "delete": []}


# ── Case 2: no resource_changes key is handled gracefully ─────────────────────


def test_missing_resource_changes_key_passes():
    result = run_gate({"format_version": "1.0"})
    assert result["gate"] == "pass"
    assert result["delta"] == {"add": [], "modify": [], "delete": []}


# ── Case 3: properly tagged GovCloud resource passes all IQE checks ──────────


def test_tagged_govcloud_unclassified_passes():
    """Tagged UNCLASSIFIED resource in us-gov region clears all five IQE queries."""
    plan = {
        "resource_changes": [
            _rc(
                "aws_kms_key.cmk",
                "aws_kms_key",
                after={
                    "region": "us-gov-east-1",
                    "tags": {
                        "Classification": "UNCLASSIFIED",
                        "Env": "prod",
                    },
                },
            )
        ]
    }
    result = run_gate(plan)
    assert result["gate"] == "pass", (
        f"Expected pass but got violations: {result['violations']}"
    )


# ── Case 4: untagged resource triggers IQE violation ─────────────────────────


def test_untagged_resource_fails():
    """Resource with no tags triggers untagged_resources.iqe."""
    plan = {
        "resource_changes": [
            _rc(
                "aws_s3_bucket.data",
                "aws_s3_bucket",
                after={"region": "us-gov-east-1"},
            )
        ]
    }
    result = run_gate(plan)
    assert result["gate"] == "fail"
    checks = [v["check"] for v in result["violations"]]
    assert "untagged_resources" in checks


# ── Case 5: CUI resource outside GovCloud fails ───────────────────────────────


def test_cui_non_govcloud_fails():
    """CUI resource in commercial region triggers cross_region_data_paths.iqe."""
    plan = {
        "resource_changes": [
            _rc(
                "aws_s3_bucket.cui_data",
                "aws_s3_bucket",
                after={
                    "region": "us-east-1",
                    "tags": {
                        "Classification": "CUI",
                        "Env": "prod",
                    },
                },
            )
        ]
    }
    result = run_gate(plan)
    assert result["gate"] == "fail"
    checks = [v["check"] for v in result["violations"]]
    assert "cross_region_data_paths" in checks


# ── Case 6: delete-only change is excluded from row set ──────────────────────


def test_delete_only_change_excluded_from_violations():
    """Pure-delete resource_change is excluded from IQE rows but lands in delta.delete."""
    plan = {
        "resource_changes": [
            _rc(
                "aws_instance.legacy",
                "aws_instance",
                actions=["delete"],
                before={"id": "i-abc123", "region": "us-east-1"},
                after=None,
            )
        ]
    }
    result = run_gate(plan)
    # Deleted resource should not produce IQE violations
    assert result["gate"] == "pass"
    assert "aws_instance.legacy" in result["delta"]["delete"]
    assert result["delta"]["add"] == []


# ── Extra unit: delta computation ────────────────────────────────────────────


def test_delta_create_update_delete():
    rcs = [
        _rc("res.a", "aws_instance", actions=["create"]),
        _rc("res.b", "aws_s3_bucket", actions=["update"],
            before={"id": "b-1"}, after={"id": "b-1", "versioning": True}),
        _rc("res.c", "aws_db_instance", actions=["delete"],
            before={"id": "db-1"}, after=None),
    ]
    delta = _compute_delta(rcs)
    assert delta["add"] == ["res.a"]
    assert delta["modify"] == ["res.b"]
    assert delta["delete"] == ["res.c"]


def test_replace_action_goes_to_add():
    rcs = [_rc("res.x", "aws_instance", actions=["delete", "create"])]
    delta = _compute_delta(rcs)
    assert "res.x" in delta["add"]
