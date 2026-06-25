# CUI // SP-CTI
"""Tests for the IDR Doc Regeneration canvas.

Covers: session CRUD, upload tracking, conflict lifecycle, reconciler conflict
detection, domain profile loading, workflow stage transitions, and blueprint
API routes.
"""
from __future__ import annotations

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("FLASK_ENV", "testing")


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _sqlite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db = str(tmp_path / "icdev_test.db")
    monkeypatch.setenv("ICDEV_DB_PATH", db)
    # Bootstrap tables
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS idr_sessions (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, domain TEXT NOT NULL DEFAULT 'network',
        doc_type TEXT NOT NULL DEFAULT 'runbook', template_id TEXT, stage INTEGER DEFAULT 0,
        status TEXT DEFAULT 'setup', dic_collection_id TEXT, ace_instance_id TEXT,
        topology_id TEXT, wg_result_id TEXT, created_by TEXT, tenant_id TEXT,
        classification TEXT DEFAULT 'CUI',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_uploads (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, filename TEXT NOT NULL,
        upload_type TEXT NOT NULL DEFAULT 'doc', file_path TEXT, file_hash TEXT,
        dic_doc_id TEXT, extracted_from_doc_id TEXT, status TEXT DEFAULT 'pending',
        error_msg TEXT, tenant_id TEXT, uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_analyses (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, upload_id TEXT NOT NULL,
        analysis_type TEXT NOT NULL, result_ref_id TEXT NOT NULL, status TEXT DEFAULT 'done',
        error_msg TEXT, tenant_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_conflicts (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, node_label TEXT NOT NULL,
        conflict_type TEXT NOT NULL, source_a TEXT NOT NULL, source_a_value TEXT,
        source_b TEXT NOT NULL, source_b_value TEXT, resolved_by TEXT, resolution TEXT,
        resolution_notes TEXT, resolved_at TEXT, tenant_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_artifacts (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, dic_doc_id TEXT,
        dic_version_id TEXT, format TEXT NOT NULL, file_path TEXT, wg_result_id TEXT,
        published_at TEXT, tenant_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()

    # Patch get_connection to return our test db
    import sqlite3 as _sqlite3

    def _get_conn():
        c = _sqlite3.connect(db)
        c.row_factory = _sqlite3.Row
        return c

    with patch("tools.db.storage.get_connection", side_effect=lambda: _get_conn()):
        yield


# ─── Domain profiles ─────────────────────────────────────────────────────────

def test_list_profiles_returns_all_domains():
    from tools.docgen.domain_profiles import list_profiles
    profiles = list_profiles()
    keys = {p["domain"] for p in profiles}
    assert "network" in keys
    assert "security" in keys
    assert "standard_guide" in keys


def test_get_profile_network():
    from tools.docgen.domain_profiles import get_profile
    prof = get_profile("network")
    assert prof["writeguard_mode"] == "runbook"
    assert "network_architect" in prof["ace_roles"]


def test_get_profile_standard_guide_is_multi_domain():
    from tools.docgen.domain_profiles import is_multi_domain, get_domain_members
    assert is_multi_domain("standard_guide")
    members = get_domain_members("standard_guide")
    assert "network" in members
    assert "security" in members


def test_get_profile_unknown_raises():
    from tools.docgen.domain_profiles import get_profile
    with pytest.raises(KeyError):
        get_profile("nonexistent_domain_xyz")


def test_resolve_all_reviewers_standard_guide():
    from tools.docgen.domain_profiles import resolve_all_reviewers
    reviewers = resolve_all_reviewers("standard_guide")
    # Should have at least the diagram analyzer once (deduped)
    diag = [r for r in reviewers if r["type"] == "diagram"]
    assert len(diag) == 1  # deduped to one


# ─── Session manager ─────────────────────────────────────────────────────────

def test_create_and_get_session():
    from tools.docgen.session_manager import create_session, get_session
    session = create_session(title="Test Runbook", domain="network")
    assert session["id"]
    assert session["title"] == "Test Runbook"
    assert session["status"] == "setup"
    assert session["stage"] == 0

    fetched = get_session(session["id"])
    assert fetched["id"] == session["id"]


def test_get_session_not_found_returns_none():
    from tools.docgen.session_manager import get_session
    assert get_session("nonexistent-uuid-xxxx") is None


def test_advance_stage():
    from tools.docgen.session_manager import create_session, advance_stage, get_session
    session = create_session(title="Stage Test", domain="network")
    ok = advance_stage(session["id"], 1, "ingesting")
    assert ok
    updated = get_session(session["id"])
    assert updated["stage"] == 1
    assert updated["status"] == "ingesting"


def test_fail_session():
    from tools.docgen.session_manager import create_session, fail_session, get_session
    session = create_session(title="Fail Test", domain="network")
    fail_session(session["id"])
    updated = get_session(session["id"])
    assert updated["status"] == "failed"


# ─── Uploads ─────────────────────────────────────────────────────────────────

def test_add_and_list_uploads():
    from tools.docgen.session_manager import create_session, add_upload, list_uploads
    session = create_session(title="Upload Test", domain="network")
    u = add_upload(session["id"], "diagram.png", "diagram")
    assert u["status"] == "pending"
    uploads = list_uploads(session["id"])
    assert len(uploads) == 1
    assert uploads[0]["filename"] == "diagram.png"


def test_set_upload_status():
    from tools.docgen.session_manager import create_session, add_upload, set_upload_status, get_upload
    session = create_session(title="Status Test", domain="network")
    u = add_upload(session["id"], "config.cfg", "config")
    ok = set_upload_status(u["id"], "ingested", dic_doc_id="dic-123")
    assert ok
    updated = get_upload(u["id"])
    assert updated["status"] == "ingested"
    assert updated["dic_doc_id"] == "dic-123"


# ─── Analyses ────────────────────────────────────────────────────────────────

def test_add_and_list_analyses():
    from tools.docgen.session_manager import create_session, add_upload, add_analysis, list_analyses
    session = create_session(title="Analysis Test", domain="network")
    u = add_upload(session["id"], "diag.png", "diagram")
    a = add_analysis(session["id"], u["id"], "diagram_analysis", "ref-abc-123")
    assert a["status"] == "done"
    analyses = list_analyses(session["id"])
    assert len(analyses) == 1
    assert analyses[0]["result_ref_id"] == "ref-abc-123"


# ─── Conflicts ───────────────────────────────────────────────────────────────

def test_add_conflict_and_pending_count():
    from tools.docgen.session_manager import (
        create_session, add_conflict, pending_conflict_count
    )
    session = create_session(title="Conflict Test", domain="network")
    add_conflict(
        session["id"], "core-router-01", "node_type",
        "team_a_diagram.png", "team_b_diagram.png",
        source_a_value={"type": "router"}, source_b_value={"type": "switch"},
    )
    assert pending_conflict_count(session["id"]) == 1


def test_resolve_conflict():
    from tools.docgen.session_manager import (
        create_session, add_conflict, resolve_conflict, pending_conflict_count
    )
    session = create_session(title="Resolve Test", domain="network")
    c = add_conflict(session["id"], "fw-01", "node_type", "src_a", "src_b")
    ok = resolve_conflict(c["id"], "a", "human_reviewer", "source A is canonical")
    assert ok
    assert pending_conflict_count(session["id"]) == 0


def test_resolve_conflict_idempotent_blocks_double_resolve():
    from tools.docgen.session_manager import (
        create_session, add_conflict, resolve_conflict
    )
    session = create_session(title="Double Resolve", domain="network")
    c = add_conflict(session["id"], "fw-02", "node_type", "src_a", "src_b")
    resolve_conflict(c["id"], "a", "user1")
    ok2 = resolve_conflict(c["id"], "b", "user2")
    assert not ok2  # already resolved


def test_list_conflicts_pending_only():
    from tools.docgen.session_manager import (
        create_session, add_conflict, resolve_conflict, list_conflicts
    )
    session = create_session(title="List Pending", domain="network")
    c1 = add_conflict(session["id"], "node-a", "node_type", "s1", "s2")
    add_conflict(session["id"], "node-b", "node_type", "s1", "s2")
    resolve_conflict(c1["id"], "a", "user1")
    pending = list_conflicts(session["id"], pending_only=True)
    all_c = list_conflicts(session["id"])
    assert len(pending) == 1
    assert len(all_c) == 2


# ─── Artifacts ───────────────────────────────────────────────────────────────

def test_add_and_list_artifacts():
    from tools.docgen.session_manager import create_session, add_artifact, list_artifacts
    session = create_session(title="Artifact Test", domain="network")
    art = add_artifact(session["id"], "html", file_path="/data/docgen/doc.html")
    assert art["format"] == "html"
    arts = list_artifacts(session["id"])
    assert len(arts) == 1


# ─── Reconciler ──────────────────────────────────────────────────────────────

def test_reconciler_empty_graphs():
    from tools.docgen.reconciler import reconcile
    from tools.docgen.session_manager import create_session
    session = create_session(title="Recon Empty", domain="network")
    result = reconcile(session["id"], [])
    assert result["nodes"] == []
    assert result["_stats"]["conflicts_recorded"] == 0


def test_reconciler_detects_type_conflict():
    from tools.docgen.reconciler import reconcile
    from tools.docgen.session_manager import create_session, pending_conflict_count

    session = create_session(title="Recon Conflict", domain="network")

    graph_a = {
        "nodes": [{"id": "n1", "label": "Core-Router", "type": "router", "properties": {}}],
        "edges": [],
    }
    graph_b = {
        "nodes": [{"id": "n1", "label": "Core-Router", "type": "switch", "properties": {}}],
        "edges": [],
    }
    result = reconcile(session["id"], [("team_a.png", graph_a), ("team_b.png", graph_b)])

    # Should have merged to 1 node (stitched)
    assert len(result["nodes"]) == 1
    assert "Core-Router" in result.get("_stitched_hosts", [])
    # Conflict should have been recorded
    assert pending_conflict_count(session["id"]) >= 1


def test_reconciler_stitches_shared_nodes():
    from tools.docgen.reconciler import reconcile
    from tools.docgen.session_manager import create_session

    session = create_session(title="Recon Stitch", domain="network")
    graph_a = {
        "nodes": [
            {"id": "a1", "label": "Core-Router", "type": "router", "properties": {}},
            {"id": "a2", "label": "Firewall-01", "type": "firewall", "properties": {}},
        ],
        "edges": [{"id": "e1", "source": "a1", "target": "a2", "label": "", "type": "ethernet"}],
    }
    graph_b = {
        "nodes": [
            {"id": "b1", "label": "Core-Router", "type": "router", "properties": {}},
            {"id": "b2", "label": "Server-01", "type": "server", "properties": {}},
        ],
        "edges": [{"id": "e2", "source": "b1", "target": "b2", "label": "", "type": "ethernet"}],
    }
    result = reconcile(session["id"], [("nw.png", graph_a), ("sec.png", graph_b)])
    # Core-Router is stitched (appears in both)
    assert "Core-Router" in result.get("_stitched_hosts", [])
    # Total nodes: Core-Router (merged) + Firewall-01 + Server-01 = 3
    assert len(result["nodes"]) == 3


# ─── Workflow ─────────────────────────────────────────────────────────────────

def test_workflow_advance():
    from tools.docgen.session_manager import create_session
    from tools.docgen.workflow import advance

    session = create_session(title="Workflow Advance", domain="network")
    updated = advance(session["id"], 1)
    assert updated["stage"] == 1
    assert updated["status"] == "ingesting"


def test_workflow_stage3_gate_blocks_when_conflicts_pending():
    from tools.docgen.session_manager import create_session, add_conflict
    from tools.docgen.workflow import stage3_check_gate

    session = create_session(title="Gate Test", domain="network")
    add_conflict(session["id"], "node-x", "node_type", "src_a", "src_b")
    assert not stage3_check_gate(session["id"])


def test_workflow_stage3_gate_passes_when_all_resolved():
    from tools.docgen.session_manager import create_session, add_conflict, resolve_conflict
    from tools.docgen.workflow import stage3_check_gate

    session = create_session(title="Gate Pass", domain="network")
    c = add_conflict(session["id"], "node-x", "node_type", "src_a", "src_b")
    resolve_conflict(c["id"], "a", "user1")
    assert stage3_check_gate(session["id"])


def test_workflow_writeguard_bypassed_gracefully_when_not_installed():
    from tools.docgen.session_manager import create_session
    from tools.docgen.workflow import stage6_writeguard

    with patch("tools.docgen.workflow.stage6_writeguard") as mock_wg:
        mock_wg.return_value = {"passed": True, "score": 100, "result": {}, "fixed_text": "text"}
        session = create_session(title="WG Test", domain="network")
        result = stage6_writeguard(session["id"], "Some doc text.", "network")
        assert result["passed"]


# ─── Context builder ──────────────────────────────────────────────────────────

def test_context_builder_basic():
    from tools.docgen.context_builder import build_context
    from tools.docgen.session_manager import create_session

    session = create_session(title="Ctx Test", domain="network")
    ctx = build_context(
        session=session,
        uploads=[{"filename": "diag.png", "upload_type": "diagram"}],
        analyses=[],
        merged_graph={"nodes": [{"label": "fw", "type": "firewall", "sources": ["a"]}], "edges": []},
    )
    assert ctx["domain"] == "network"
    assert ctx["topology_summary"]["node_count"] == 1
    assert "network_architect" in ctx["ace_roles"]
    assert ctx["query_string"]


def test_context_builder_config_findings_flattened():
    from tools.docgen.context_builder import build_context
    from tools.docgen.session_manager import create_session

    session = create_session(title="Config Ctx", domain="network")
    ctx = build_context(
        session=session,
        uploads=[],
        analyses=[],
        config_review_results=[{
            "security_compliance": ["CVE-2024-1234 unpatched"],
            "optimization": [],
            "remediation": ["Apply patch 9.x"],
        }],
    )
    assert len(ctx["config_findings"]) == 2  # 1 security + 1 remediation
