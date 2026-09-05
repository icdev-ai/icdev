# CUI // SP-CTI
# Classification: CUI — Controlled Unclassified Information
"""Tests for IDC IaC Twin Phase 1.

Covers:
- terraform_show_importer: parse terraform show -json → IDC graph nodes/edges
- infra_engine over an imported plan: IDC compliance findings for a plan -json
  (the `pre_apply_gate` wrapper this file used to cover was deleted by
  flx-ci-02 -- see the tombstone below the importer tests)
- snapshot writer: persist IDC graph snapshot with timestamp + snapshot_id
- DB tables: idc_twin_snapshots, idc_twin_violations

NIST 800-53 controls: SA-11 (Developer Testing), CM-3 (Configuration Change Control),
                       CM-8 (Information System Component Inventory), SI-3 (Malicious Code Protection)
"""
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_TF_SHOW = {
    "format_version": "0.1",
    "values": {
        "root_module": {
            "resources": [
                {
                    "address": "aws_instance.web",
                    "type": "aws_instance",
                    "name": "web",
                    "values": {
                        "ami": "ami-0abc1234",
                        "instance_type": "t3.medium",
                        "tags": {"classification": "CUI", "environment": "prod"},
                    },
                },
                {
                    "address": "aws_s3_bucket.data",
                    "type": "aws_s3_bucket",
                    "name": "data",
                    "values": {
                        "bucket": "my-data-bucket",
                        "tags": {"classification": "CUI"},
                    },
                },
                {
                    "address": "aws_kms_key.main",
                    "type": "aws_kms_key",
                    "name": "main",
                    "values": {
                        "description": "Main encryption key",
                        "enable_key_rotation": True,
                    },
                },
            ]
        }
    },
}

MINIMAL_TF_PLAN = {
    "format_version": "0.1",
    "resource_changes": [
        {
            "address": "aws_instance.app",
            "type": "aws_instance",
            "name": "app",
            "change": {
                "actions": ["create"],
                "after": {
                    "instance_type": "t3.large",
                    "tags": {"classification": "CUI"},
                },
            },
        },
        {
            "address": "aws_s3_bucket.logs",
            "type": "aws_s3_bucket",
            "name": "logs",
            "change": {
                "actions": ["create"],
                "after": {
                    "bucket": "logs-bucket",
                    "tags": {},
                },
            },
        },
    ],
}

# A plan that would violate IL-boundary (missing KMS for storage)
TF_PLAN_NO_KMS = {
    "format_version": "0.1",
    "resource_changes": [
        {
            "address": "aws_s3_bucket.secret_data",
            "type": "aws_s3_bucket",
            "name": "secret_data",
            "change": {
                "actions": ["create"],
                "after": {"bucket": "secret-data", "tags": {}},
            },
        },
        {
            "address": "aws_db_instance.prod",
            "type": "aws_db_instance",
            "name": "prod",
            "change": {
                "actions": ["create"],
                "after": {"engine": "postgres", "tags": {}},
            },
        },
    ],
}

# A plan with no storage/db resources — passes ENC checks but may have other CAT2 findings.
# This plan includes KMS + IAM + Secrets Manager so IDC-IAM-002 (CAT1) is also satisfied.
TF_PLAN_MINIMAL_PASSING = {
    "format_version": "0.1",
    "resource_changes": [
        {
            "address": "aws_kms_key.main",
            "type": "aws_kms_key",
            "name": "main",
            "change": {
                "actions": ["create"],
                "after": {"description": "key", "enable_key_rotation": True},
            },
        },
        {
            "address": "aws_iam_role.app",
            "type": "aws_iam_role",
            "name": "app",
            "change": {
                "actions": ["create"],
                "after": {"name": "app-role"},
            },
        },
        {
            "address": "aws_secretsmanager_secret.app",
            "type": "aws_secretsmanager_secret",
            "name": "app",
            "change": {
                "actions": ["create"],
                "after": {"name": "app-secret"},
            },
        },
    ],
}


# ---------------------------------------------------------------------------
# ── terraform_show_importer ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestTerraformShowImporter:
    """Verify terraform show -json → IDC graph conversion."""

    def test_import_returns_dict(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        assert isinstance(result, dict)

    def test_import_has_nodes_and_edges_keys(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        assert "nodes" in result
        assert "edges" in result

    def test_import_nodes_is_list(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        assert isinstance(result["nodes"], list)

    def test_import_edges_is_list(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        assert isinstance(result["edges"], list)

    def test_import_maps_ec2_to_aws_ec2(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        types = [n["type"] for n in result["nodes"]]
        assert any(t.startswith("aws-ec2") for t in types), f"No aws-ec2 in {types}"

    def test_import_maps_s3_to_aws_s3(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        types = [n["type"] for n in result["nodes"]]
        assert "aws-s3" in types, f"No aws-s3 in {types}"

    def test_import_maps_kms_to_aws_kms(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        types = [n["type"] for n in result["nodes"]]
        assert "aws-kms" in types, f"No aws-kms in {types}"

    def test_node_has_required_fields(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        node = result["nodes"][0]
        assert "id" in node
        assert "type" in node
        assert "label" in node

    def test_node_label_includes_tf_address(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        labels = [n["label"] for n in result["nodes"]]
        # At least one label should reference the resource name
        assert any("web" in lbl or "data" in lbl or "main" in lbl for lbl in labels)

    def test_node_metadata_has_tf_address(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        node = result["nodes"][0]
        meta = node.get("metadata", {})
        assert "tf_address" in meta or "address" in meta

    def test_import_empty_resources_returns_empty_graph(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        empty = {"format_version": "0.1", "values": {"root_module": {"resources": []}}}
        result = import_terraform_show(empty)
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_import_missing_values_key_returns_empty_graph(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show({})
        assert result["nodes"] == []

    def test_import_tags_preserved_in_metadata(self):
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        result = import_terraform_show(MINIMAL_TF_SHOW)
        # Find the aws_instance.web node
        web_node = next(
            (n for n in result["nodes"] if "web" in n.get("label", "") or "web" in str(n.get("metadata", {}))),
            None,
        )
        assert web_node is not None
        meta = web_node.get("metadata", {})
        tags = meta.get("tags", {})
        assert tags.get("classification") == "CUI"

    def test_import_from_plan_resource_changes(self):
        """import_terraform_plan wraps terraform plan -json resource_changes."""
        from tools.infra_canvas.terraform_show_importer import import_terraform_plan

        result = import_terraform_plan(MINIMAL_TF_PLAN)
        assert isinstance(result, dict)
        assert "nodes" in result
        assert len(result["nodes"]) > 0

    def test_import_plan_only_includes_create_and_update(self):
        """Destroy-only changes should be excluded from the twin graph."""
        from tools.infra_canvas.terraform_show_importer import import_terraform_plan

        plan_with_destroy = {
            "format_version": "0.1",
            "resource_changes": [
                {
                    "address": "aws_instance.old",
                    "type": "aws_instance",
                    "name": "old",
                    "change": {"actions": ["delete"], "after": None},
                },
                {
                    "address": "aws_s3_bucket.new",
                    "type": "aws_s3_bucket",
                    "name": "new",
                    "change": {
                        "actions": ["create"],
                        "after": {"bucket": "new-bucket", "tags": {}},
                    },
                },
            ],
        }
        result = import_terraform_plan(plan_with_destroy)
        types = [n["type"] for n in result["nodes"]]
        # aws_instance.old (delete) should be excluded; aws_s3_bucket.new should be included
        assert "aws-s3" in types
        # No aws-ec2 nodes from a delete-only change
        ec2_nodes = [t for t in types if t.startswith("aws-ec2")]
        assert len(ec2_nodes) == 0

    def test_unknown_resource_type_maps_to_generic(self):
        """Unknown resource types should map to a generic IDC node type, not raise."""
        from tools.infra_canvas.terraform_show_importer import import_terraform_show

        show_with_unknown = {
            "format_version": "0.1",
            "values": {
                "root_module": {
                    "resources": [
                        {
                            "address": "custom_provider_thing.foo",
                            "type": "custom_provider_thing",
                            "name": "foo",
                            "values": {},
                        }
                    ]
                }
            },
        }
        result = import_terraform_show(show_with_unknown)
        assert len(result["nodes"]) == 1
        # The type should be a valid string (not crash)
        assert isinstance(result["nodes"][0]["type"], str)


# ---------------------------------------------------------------------------
# ── pre-apply gate: DELETED by flx-ci-02 ─────────────────────────────
# ---------------------------------------------------------------------------
#
# `TestPreApplyGate` lived here and asserted the return shape of
# `tools/infra_canvas/pre_apply_gate.py::check_plan` -- {"passed", "score",
# "snapshot_id", "assessed_at", ...}.  That module was one of TWO pre-apply
# gates in `tools/infra_canvas/` taking the same input and answering the same
# question with two verdict vocabularies.  flx-ci-02 measured the pair and
# deleted this one; the surviving gate is
# `tools/infra_canvas/preapply_gate.py::run_gate`, tested by
# `tests/test_preapply_gate.py` and `tests/ci/test_floci_iac_gate.py`.
#
# The class is DELETED rather than migrated because its subject was the
# WRAPPER'S SHAPE, and that shape is gone.  What the wrapper actually did --
# `import_terraform_plan` then `assess_infra_design` -- is two lines, still
# exercised by `TestTerraformShowImporter` above and by `TestFullGateFlow`
# below, which now calls them directly.
#
# WHY DELETION AND NOT A MERGE: check_plan had zero runtime callers, and its
# rules are ESTATE-COMPLETENESS questions ("is there a KMS service in this
# design?") asked of a plan DELTA -- so it returned `passed=True` only for a
# plan that was itself the whole estate.  The rulebook is not lost:
# `infra_engine.assess_infra_design` is consumed live by
# `tools/infra_canvas/blueprint.py` over the full design graph, which is the
# input those rules were written for.  Full derivation:
# `docs/audits/flx-ci-02-two-preapply-gates.md`.
# Standing guard: `tests/infra_canvas/test_one_preapply_gate.py`.


# ---------------------------------------------------------------------------
# ── snapshot writer ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestSnapshotWriter:
    """Verify IDC twin snapshot persistence to idc_twin_snapshots table."""

    def test_write_snapshot_returns_snapshot_id(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_snapshot

        graph = {"nodes": [{"id": "n1", "type": "aws-s3", "label": "S3"}], "edges": []}
        sid = write_snapshot(graph, db_path=str(tmp_path / "test.db"))
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_write_snapshot_creates_table(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_snapshot
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = {"nodes": [], "edges": []}
        write_snapshot(graph, db_path=db)
        conn = sqlite3.connect(db)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()
        assert "idc_twin_snapshots" in tables

    def test_write_snapshot_row_retrievable(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_snapshot
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = {"nodes": [{"id": "n1", "type": "aws-kms", "label": "KMS"}], "edges": []}
        sid = write_snapshot(graph, db_path=db)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT * FROM idc_twin_snapshots WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row is not None

    def test_write_snapshot_stores_graph_json(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_snapshot
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = {"nodes": [{"id": "n1", "type": "aws-eks", "label": "EKS"}], "edges": []}
        sid = write_snapshot(graph, db_path=db)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT graph_json FROM idc_twin_snapshots WHERE id=?", (sid,)).fetchone()
        conn.close()
        stored = json.loads(row[0])
        assert stored["nodes"][0]["type"] == "aws-eks"

    def test_write_snapshot_stores_source_label(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_snapshot
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = {"nodes": [], "edges": []}
        sid = write_snapshot(graph, db_path=db, source="terraform_plan")
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT source FROM idc_twin_snapshots WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row[0] == "terraform_plan"

    def test_write_snapshot_has_created_at(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_snapshot
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = {"nodes": [], "edges": []}
        sid = write_snapshot(graph, db_path=db)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT created_at FROM idc_twin_snapshots WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row[0]  # non-empty timestamp

    def test_write_multiple_snapshots_all_stored(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_snapshot
        import sqlite3

        db = str(tmp_path / "test.db")
        ids = set()
        for i in range(3):
            sid = write_snapshot({"nodes": [], "edges": []}, db_path=db)
            ids.add(sid)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM idc_twin_snapshots").fetchone()[0]
        conn.close()
        assert count == 3
        assert len(ids) == 3  # all unique

    def test_write_violations_table_created(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_violations
        import sqlite3

        db = str(tmp_path / "test.db")
        write_violations("snap-001", [], db_path=db)
        conn = sqlite3.connect(db)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()
        assert "idc_twin_violations" in tables

    def test_write_violations_stores_rows(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_violations
        import sqlite3

        db = str(tmp_path / "test.db")
        violations = [
            {"rule_id": "IDC-ENC-001", "title": "Storage encrypted", "severity": "CAT1", "detail": "No KMS"},
            {"rule_id": "IDC-ENC-002", "title": "DB encrypted", "severity": "CAT1", "detail": "No KMS"},
        ]
        write_violations("snap-abc", violations, db_path=db)
        conn = sqlite3.connect(db)
        count = conn.execute(
            "SELECT COUNT(*) FROM idc_twin_violations WHERE snapshot_id=?", ("snap-abc",)
        ).fetchone()[0]
        conn.close()
        assert count == 2

    def test_write_violations_empty_list_no_rows(self, tmp_path):
        from tools.infra_canvas.snapshot_writer import write_violations
        import sqlite3

        db = str(tmp_path / "test.db")
        write_violations("snap-xyz", [], db_path=db)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM idc_twin_violations").fetchone()[0]
        conn.close()
        assert count == 0


# ---------------------------------------------------------------------------
# ── Integration: full gate flow ──────────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestFullGateFlow:
    """End-to-end: plan → importer → assessment → snapshot → violations persisted."""

    def test_full_flow_no_kms_produces_violations_in_db(self, tmp_path):
        from tools.infra_canvas.terraform_show_importer import import_terraform_plan
        from tools.infra_canvas.infra_engine import assess_infra_design
        from tools.infra_canvas.snapshot_writer import write_snapshot, write_violations
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = import_terraform_plan(TF_PLAN_NO_KMS)
        result = {
            "graph": graph,
            "violations": assess_infra_design(graph)["findings"],
        }
        sid = write_snapshot(result["graph"], db_path=db, source="terraform_plan")
        write_violations(sid, result["violations"], db_path=db)

        conn = sqlite3.connect(db)
        violation_count = conn.execute(
            "SELECT COUNT(*) FROM idc_twin_violations WHERE snapshot_id=?", (sid,)
        ).fetchone()[0]
        snap_count = conn.execute(
            "SELECT COUNT(*) FROM idc_twin_snapshots WHERE id=?", (sid,)
        ).fetchone()[0]
        conn.close()

        assert snap_count == 1
        assert violation_count > 0

    def test_full_flow_passing_plan_zero_enc_violations(self, tmp_path):
        """Minimal passing plan (KMS + IAM + Secrets Manager) has zero encryption violations."""
        from tools.infra_canvas.terraform_show_importer import import_terraform_plan
        from tools.infra_canvas.infra_engine import assess_infra_design
        from tools.infra_canvas.snapshot_writer import write_snapshot, write_violations
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = import_terraform_plan(TF_PLAN_MINIMAL_PASSING)
        result = {
            "graph": graph,
            "violations": assess_infra_design(graph)["findings"],
        }
        sid = write_snapshot(result["graph"], db_path=db, source="terraform_plan")
        write_violations(sid, result["violations"], db_path=db)

        conn = sqlite3.connect(db)
        enc_violation_count = conn.execute(
            "SELECT COUNT(*) FROM idc_twin_violations WHERE snapshot_id=? AND (rule_id='IDC-ENC-001' OR rule_id='IDC-ENC-002')",
            (sid,),
        ).fetchone()[0]
        conn.close()
        # Minimal passing plan has no storage/db → zero ENC violations
        assert enc_violation_count == 0

    def test_snapshot_id_from_gate_matches_db(self, tmp_path):
        from tools.infra_canvas.terraform_show_importer import import_terraform_plan
        from tools.infra_canvas.infra_engine import assess_infra_design
        from tools.infra_canvas.snapshot_writer import write_snapshot
        import sqlite3

        db = str(tmp_path / "test.db")
        graph = import_terraform_plan(MINIMAL_TF_PLAN)
        result = {
            "graph": graph,
            "violations": assess_infra_design(graph)["findings"],
        }
        sid = write_snapshot(result["graph"], db_path=db)

        conn = sqlite3.connect(db)
        row = conn.execute("SELECT id FROM idc_twin_snapshots WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == sid


# [TEMPLATE: CUI // SP-CTI]
