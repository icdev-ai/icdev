# CUI // SP-CTI — tests for twx-fed-03 high-side compatibility report + ATO wiring
"""Tests for tools/twin_core/compat_report.py.

Deterministic: uses the committed public presets/catalog + iac_control_map.yaml,
and a temp SQLite DB (fresh worktrees ship an empty data/icdev.db) with minimal
project_controls/poam_items tables for the cATO-feed test.
"""
import sqlite3

import pytest

from tools.twin_core import compat_report as cr


# ── fixtures ──────────────────────────────────────────────────────────────────

def _govcloud_design():
    """A terraform-plan-shaped design that maps cleanly to controls."""
    return {
        "resource_changes": [
            {"type": "aws_kms_key", "name": "main", "address": "aws_kms_key.main"},
            {"type": "aws_cloudtrail", "name": "audit", "address": "aws_cloudtrail.audit"},
            {"type": "aws_iam_role", "name": "app", "address": "aws_iam_role.app"},
            {"type": "aws_security_group", "name": "web", "address": "aws_security_group.web"},
            {"type": "aws_s3_bucket", "name": "data", "address": "aws_s3_bucket.data"},
        ]
    }


def _airgap_bad_design():
    """A design with a public-egress marker that the fed-01 air-gap rules deny."""
    return {
        "nodes": [
            {"id": "sg-1", "type": "aws_security_group", "label": "open",
             "cidr": "0.0.0.0/0"},
        ]
    }


@pytest.fixture
def compliance_db(tmp_path):
    """Temp SQLite with the two tables the ATO engines read."""
    db = tmp_path / "ato.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE project_controls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            control_id TEXT NOT NULL,
            implementation_status TEXT DEFAULT 'planned',
            implementation_description TEXT,
            responsible_role TEXT,
            evidence_path TEXT,
            classification TEXT DEFAULT 'CUI'
        );
        CREATE TABLE poam_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            weakness_id TEXT NOT NULL,
            weakness_description TEXT NOT NULL,
            severity TEXT NOT NULL,
            source TEXT NOT NULL,
            control_id TEXT,
            status TEXT DEFAULT 'open',
            corrective_action TEXT,
            milestone_date TEXT,
            responsible_party TEXT,
            classification TEXT DEFAULT 'CUI'
        );
        """
    )
    conn.commit()
    conn.close()
    return str(db)


# ── resource extraction ───────────────────────────────────────────────────────

def test_extract_resources_plan_and_graph():
    plan = _govcloud_design()
    res = cr._extract_resources(plan)
    assert {r["address"] for r in res} == {
        "aws_kms_key.main", "aws_cloudtrail.audit", "aws_iam_role.app",
        "aws_security_group.web", "aws_s3_bucket.data",
    }
    graph = {"nodes": [{"id": "n1", "type": "aws_kms_key", "label": "k"}]}
    gres = cr._extract_resources(graph)
    assert gres and gres[0]["type"] == "aws_kms_key"


def test_extract_resources_dedup():
    d = {"resource_changes": [
        {"type": "aws_kms_key", "name": "m", "address": "aws_kms_key.m"},
        {"type": "aws_kms_key", "name": "m", "address": "aws_kms_key.m"},
    ]}
    assert len(cr._extract_resources(d)) == 1


# ── ATO mapping / TRUST citations ─────────────────────────────────────────────

def test_map_iac_to_controls_cites_resources():
    ato = cr.map_iac_to_controls(_govcloud_design(), il_level="IL5")
    # KMS resource must evidence SC-12 with an inline source citation.
    assert "SC-12" in ato["implemented"]
    ev = ato["implemented"]["SC-12"][0]
    assert ev["resource"] == "aws_kms_key.main"
    assert ev["citation"] == "[source: aws_kms_key.main]"
    assert "[source: aws_kms_key.main]" in ev["statement"]
    # every implemented statement is grounded (TRUST invariant)
    for evs in ato["implemented"].values():
        for e in evs:
            assert e["citation"].startswith("[source:")


def test_map_iac_to_controls_gaps_and_poam():
    # A design with ZERO mappable resources → all in-scope controls are gaps.
    ato = cr.map_iac_to_controls({"resource_changes": []}, il_level="IL5")
    assert ato["control_coverage"]["covered"] == 0
    assert ato["control_coverage"]["gaps"] > 0
    assert len(ato["poam_items"]) == ato["control_coverage"]["gaps"]
    p = ato["poam_items"][0]
    assert p["weakness_id"].startswith("IDC-TWIN-")
    assert p["severity"] in ("critical", "high", "moderate", "low")
    assert p["source"] == "idc-twin-compat"


def test_map_iac_partial_coverage_reduces_gaps():
    full = cr.map_iac_to_controls(_govcloud_design(), il_level="IL5")
    empty = cr.map_iac_to_controls({"resource_changes": []}, il_level="IL5")
    assert full["control_coverage"]["covered"] > empty["control_coverage"]["covered"]
    assert full["control_coverage"]["gaps"] < empty["control_coverage"]["gaps"]


# ── compatibility report ──────────────────────────────────────────────────────

def test_report_airgap_preset_blocks():
    report = cr.generate_compatibility_report(
        _airgap_bad_design(), "aws_high_side_airgap",
        target_id="t1", classification="SECRET",
    )
    assert report["executive"]["verdict"] == "fail"
    assert report["executive"]["blockers"] >= 1
    # SECRET banner comes from classification_manager, not hardcoded here.
    assert "SECRET" in report["banner"]["header"]


def test_report_clean_govcloud_no_blockers():
    report = cr.generate_compatibility_report(
        _govcloud_design(), "aws_govcloud_west", target_id="t2",
    )
    # All referenced services are govcloud-available → no service_parity blocker.
    assert report["executive"]["blockers"] == 0
    assert report["executive"]["ato_control_coverage"]["covered"] >= 1
    assert "CUI" in report["banner"]["header"]


def test_report_service_parity_custom_preset():
    # Custom preset dict with a region NOT in any service's region list → parity.
    # Pass the real catalog path explicitly so the result is independent of any
    # ambient global catalog-cache state left by sibling twin tests.
    from pathlib import Path

    import tools.twin_core.compat_report as _cr

    catalog = _cr._REPO_ROOT / "context" / "cloud" / "csp_service_registry.json"
    assert Path(catalog).exists()
    preset = {
        "display_name": "Synthetic unavailable region",
        "csp": "aws", "region": "us-gov-nonexistent-9",
        "region_scope": "government", "classification": "CUI",
        "network_constraints": {"airgap": False}, "reviewed_at": "2026-07-01",
    }
    report = cr.generate_compatibility_report(
        {"nodes": [{"id": "c", "type": "eks", "label": "cluster"}]}, preset,
        target_id="t3", catalog_path=catalog,
    )
    assert report["dependency_replacements"], "expected a service_parity replacement"


def test_report_per_resource_verdicts():
    report = cr.generate_compatibility_report(
        _govcloud_design(), "aws_govcloud_west", target_id="t4",
    )
    assert len(report["per_resource"]) == 5
    assert all(r["verdict"] in ("pass", "warn", "fail") for r in report["per_resource"])


def test_content_hash_stable_and_dedup_signal():
    r1 = cr.generate_compatibility_report(_govcloud_design(), "aws_govcloud_west", target_id="t5")
    r2 = cr.generate_compatibility_report(_govcloud_design(), "aws_govcloud_west", target_id="t5")
    assert r1["content_hash"] == r2["content_hash"]


# ── rendered artifact ─────────────────────────────────────────────────────────

def test_render_html_contains_banner_and_verdict(tmp_path):
    report = cr.generate_compatibility_report(_govcloud_design(), "aws_govcloud_west", target_id="t6")
    out = tmp_path / "report.html"
    html = cr.render_compatibility_report(report, out)
    assert out.exists()
    assert "Compatibility" in html
    assert "CUI" in html
    assert report["executive"]["verdict"].upper() in html


# ── persistence: dedup + retention ────────────────────────────────────────────

def test_persist_dedup(tmp_path):
    db = str(tmp_path / "p.db")
    report = cr.generate_compatibility_report(_govcloud_design(), "aws_govcloud_west", target_id="tp")
    first = cr.persist_report(report, db_path=db, retention=5)
    assert first["deduped"] is False and first["id"]
    second = cr.persist_report(report, db_path=db, retention=5)
    assert second["deduped"] is True


def test_persist_retention_caps_rows(tmp_path):
    db = str(tmp_path / "r.db")
    for i in range(6):
        rep = cr.generate_compatibility_report(_govcloud_design(), "aws_govcloud_west", target_id="tr")
        # Force distinct content so each persists (dedup would otherwise skip).
        rep["content_hash"] = f"hash-{i}"
        cr.persist_report(rep, db_path=db, retention=3)
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM twin_compat_reports WHERE target_id='tr'").fetchone()[0]
    conn.close()
    assert n == 3


# ── ATO wiring: feed evidence into project_controls / poam_items ──────────────

def test_feed_cato_evidence_writes_controls_and_poam(compliance_db):
    report = cr.generate_compatibility_report(_govcloud_design(), "aws_govcloud_west", target_id="tc")
    res = cr.feed_cato_evidence(report, "proj-1", db_path=compliance_db)
    assert res["controls_written"] >= 1
    assert res["errors"] == []
    conn = sqlite3.connect(compliance_db)
    pc = conn.execute(
        "SELECT control_id, implementation_status, implementation_description, evidence_path "
        "FROM project_controls WHERE project_id='proj-1'"
    ).fetchall()
    conn.close()
    assert pc, "expected project_controls rows"
    # every persisted statement keeps its IaC source citation (TRUST)
    assert all(row[1] == "implemented" for row in pc)
    assert any("[source:" in (row[2] or "") for row in pc)


def test_feed_cato_evidence_idempotent(compliance_db):
    report = cr.generate_compatibility_report(_govcloud_design(), "aws_govcloud_west", target_id="tc")
    cr.feed_cato_evidence(report, "proj-2", db_path=compliance_db)
    cr.feed_cato_evidence(report, "proj-2", db_path=compliance_db)
    conn = sqlite3.connect(compliance_db)
    ctrl = conn.execute("SELECT control_id, COUNT(*) FROM project_controls "
                        "WHERE project_id='proj-2' GROUP BY control_id").fetchall()
    conn.close()
    # no duplicate control rows after a second feed
    assert all(cnt == 1 for _cid, cnt in ctrl)
