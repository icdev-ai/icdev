# CUI // SP-CTI
"""nav-bld-03 — AISG honesty: real handoff import + honest pattern-usage label.

Two P1 "fake success" bugs are covered here:

  1. ``/api/ai-handoff/import`` used to return ``{status: ok, imported_for: …}``
     WITHOUT calling any import routine. The export format
     (``knowledge_handoff.export_package``) is a structured ZIP
     (patterns.json / audit_summary.json / args_snapshot.yaml), so a REAL import
     is implemented: the fine-tuning ``patterns.json`` payload is restored into
     ``ft_datasets`` + ``ft_training_pairs`` owned by the new user, and the round
     trip export -> import -> data-present is asserted below. The append-only
     ``audit_trail`` is intentionally NOT re-imported (immutable, NIST AU) — it is
     reported as ``skipped_audit_entries`` instead of silently forged.

  2. ``/api/aisg/patterns/<id>/deploy`` only incremented a counter while the UI
     claimed a GovCloud deployment + generated kanban tasks. It is relabelled to
     honest usage tracking: the registry function is ``record_pattern_usage``,
     the counter is ``usage_count``, the response carries
     ``action == "usage_recorded"``, and the template no longer claims a deploy.

Shared conftest, SQLite backend (forced by conftest). RBAC (nav-sec-06) is
respected — the blueprint tests authenticate with an allowed role via the same
``X-Test-Role`` fake-auth harness that nav-sec-06 uses.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# DDL: the two fine-tuning tables the import restores into. Mirrors the orphan
# migration (017) / init_icdev_db schema for the columns export/import touch.
# ---------------------------------------------------------------------------

_FT_DDL = """
CREATE TABLE IF NOT EXISTS ft_datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    purpose TEXT DEFAULT 'general',
    base_model TEXT DEFAULT 'qwen3:latest',
    version INTEGER DEFAULT 1,
    example_count INTEGER DEFAULT 0,
    content_hash TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ft_training_pairs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT DEFAULT '',
    instruction TEXT DEFAULT '',
    input_text TEXT DEFAULT '',
    output_text TEXT DEFAULT '',
    purpose TEXT DEFAULT 'general',
    approved INTEGER DEFAULT 0,
    source TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS aisg_patterns (
    id TEXT PRIMARY KEY,
    name TEXT,
    category TEXT,
    description TEXT,
    use_case TEXT,
    deploy_config TEXT,
    tags TEXT,
    is_builtin INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
-- export_package reads these audit_trail columns (kept empty here as the
-- pattern round trip does not depend on audit rows)
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    session_id TEXT,
    created_at TEXT
);
"""


@pytest.fixture
def ft_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with the ft_* + aisg_patterns tables created."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        for stmt in _FT_DDL.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _seed_dataset_and_pairs(owner_email: str, pairs: list[dict]) -> str:
    from tools.db.storage import get_connection

    ds_id = "ds-alice-001"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO ft_datasets (id, name, created_by, purpose, status, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (ds_id, "Alice's dataset", owner_email, "general", "ready", "2026-01-01", "2026-01-01"),
        )
        for i, p in enumerate(pairs):
            conn.execute(
                "INSERT INTO ft_training_pairs (id, dataset_id, instruction, "
                "input_text, output_text, purpose, approved, source, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"ftp-alice-{i}", ds_id, p["instruction"], p.get("input_text", ""),
                    p["output_text"], p.get("purpose", "general"), int(p.get("approved", 1)),
                    "alice-session", "2026-01-01",
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return ds_id


# ===========================================================================
# 1. Handoff import — real round trip (export -> import -> data present)
# ===========================================================================

def test_import_round_trip_restores_patterns(ft_db):
    kh = importlib.import_module("tools.aisg.knowledge_handoff")

    src_pairs = [
        {"instruction": "Summarize the SSP", "input_text": "long doc",
         "output_text": "a summary", "purpose": "general", "approved": 1},
        {"instruction": "Draft a POA&M item", "input_text": "finding X",
         "output_text": "poam text", "purpose": "general", "approved": 0},
    ]
    _seed_dataset_and_pairs("alice@agency.gov", src_pairs)

    # Export Alice's package (finds her dataset via created_by).
    out_dir = ft_db.parent / "export"
    export_summary = kh.export_package("alice@agency.gov", out_dir)
    zip_path = Path(export_summary["zip_path"])
    assert zip_path.exists()
    assert export_summary["pattern_entries"] == 2

    # Import into Bob's account.
    result = kh.import_package(zip_path, "bob@agency.gov")
    assert result["status"] == "ok"
    assert result["imported_patterns"] == 2
    new_ds = result["dataset_id"]
    assert new_ds

    # Data is actually present, owned by Bob, with content preserved.
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        ds_owner = conn.execute(
            "SELECT created_by FROM ft_datasets WHERE id = %s", (new_ds,)
        ).fetchone()
        assert ds_owner is not None
        assert (ds_owner[0] if not isinstance(ds_owner, dict) else ds_owner["created_by"]) == "bob@agency.gov"

        rows = conn.execute(
            "SELECT instruction, output_text FROM ft_training_pairs WHERE dataset_id = %s "
            "ORDER BY instruction",
            (new_ds,),
        ).fetchall()
    finally:
        conn.close()

    restored = sorted((r[0] if not isinstance(r, dict) else r["instruction"]) for r in rows)
    assert restored == ["Draft a POA&M item", "Summarize the SSP"]

    # Second-order round trip: exporting Bob now re-emits the same 2 pairs.
    bob_summary = kh.export_package("bob@agency.gov", ft_db.parent / "export_bob")
    assert bob_summary["pattern_entries"] == 2


def test_import_does_not_forge_audit_trail(ft_db, monkeypatch):
    """Audit rows in the package are reported, never re-inserted (append-only)."""
    kh = importlib.import_module("tools.aisg.knowledge_handoff")

    # Build a package by hand with an audit_summary payload but no patterns.
    import zipfile

    pkg = ft_db.parent / "pkg.zip"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("patterns.json", json.dumps([]))
        zf.writestr("audit_summary.json", json.dumps([{"id": 1, "actor": "alice"},
                                                      {"id": 2, "actor": "alice"}]))
        zf.writestr("args_snapshot.yaml", "llm_config.yaml: {}\n")

    result = kh.import_package(pkg, "carol@agency.gov")
    assert result["imported_patterns"] == 0
    assert result["skipped_audit_entries"] == 2
    assert result["args_files_in_package"] == 1


def test_import_missing_zip_raises(ft_db):
    kh = importlib.import_module("tools.aisg.knowledge_handoff")
    with pytest.raises(FileNotFoundError):
        kh.import_package(ft_db.parent / "nope.zip", "x@y.gov")


# ===========================================================================
# 2. Pattern usage — honest semantics, no "deploy" claim
# ===========================================================================

def _seed_pattern(pattern_id: str = "aisg-doc-qa-001"):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO aisg_patterns (id, name, category, description, use_case, "
            "deploy_config, tags, is_builtin, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pattern_id, "Document Q&A", "document_qa", "desc", "uc",
                json.dumps({"complexity": "beginner", "canvas_type": "DDC"}),
                json.dumps(["policy"]), 1, "2026-01-01", "2026-01-01",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_record_usage_increments_usage_count_not_deploy(ft_db):
    pr = importlib.import_module("tools.aisg.pattern_registry")
    _seed_pattern()

    r1 = pr.record_pattern_usage("aisg-doc-qa-001")
    assert r1["usage_count"] == 1
    assert r1["action"] == "usage_recorded"
    # The response must NOT claim a deployment happened.
    assert "deploy_count" not in r1

    r2 = pr.record_pattern_usage("aisg-doc-qa-001")
    assert r2["usage_count"] == 2

    # Surfaced on list_patterns for the card.
    pats = pr.list_patterns()
    p = next(p for p in pats if p["id"] == "aisg-doc-qa-001")
    assert p["usage_count"] == 2


def test_record_usage_migrates_legacy_deploy_count(ft_db):
    """A pattern whose config still has the old deploy_count key upgrades cleanly."""
    from tools.db.storage import get_connection

    pr = importlib.import_module("tools.aisg.pattern_registry")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO aisg_patterns (id, name, category, description, use_case, "
            "deploy_config, tags, is_builtin, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                "legacy-1", "Legacy", "custom", "d", "u",
                json.dumps({"deploy_count": 5}), json.dumps([]), 0,
                "2026-01-01", "2026-01-01",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    r = pr.record_pattern_usage("legacy-1")
    assert r["usage_count"] == 6
    assert "deploy_count" not in r["deploy_config"]


def test_deploy_pattern_alias_points_at_usage(ft_db):
    pr = importlib.import_module("tools.aisg.pattern_registry")
    assert pr.deploy_pattern is pr.record_pattern_usage


def test_record_usage_unknown_pattern_errors(ft_db):
    pr = importlib.import_module("tools.aisg.pattern_registry")
    r = pr.record_pattern_usage("does-not-exist")
    assert "error" in r


# ===========================================================================
# 3. Template honesty — no fake "deployed to GovCloud" / kanban-tasks copy
# ===========================================================================

def test_patterns_template_has_no_deploy_claims():
    root = Path(__file__).resolve().parents[1]
    for base in (root / "tools", root / "icdev" / "tools"):
        html = (base / "dashboard" / "templates" / "aisg" / "patterns.html").read_text(encoding="utf-8")
        low = html.lower()
        assert "generated tasks" not in low, "template still claims kanban tasks are generated"
        assert "govcloud" not in low, "template still claims GovCloud deployment"
        assert "pattern deployed" not in low, "template still claims a deployment"
        # Honest label present.
        assert "track usage" in low
        assert "usage_count" in html


# ===========================================================================
# 4. Blueprint routes respect nav-sec-06 RBAC and call the real routines
# ===========================================================================

def _fake_auth_app():
    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    from tools.aisg.blueprint import bp

    app.register_blueprint(bp)
    return app


def test_handoff_import_route_real_import_as_admin(ft_db, monkeypatch):
    monkeypatch.setenv("ICDEV_DB_PATH", str(ft_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    kh = importlib.import_module("tools.aisg.knowledge_handoff")
    _seed_dataset_and_pairs("alice@agency.gov", [
        {"instruction": "i1", "output_text": "o1", "approved": 1},
    ])
    export_summary = kh.export_package("alice@agency.gov", ft_db.parent / "exp")
    zip_path = export_summary["zip_path"]

    app = _fake_auth_app()
    c = app.test_client()

    # anonymous -> 401 (RBAC preserved)
    assert c.post("/api/ai-handoff/import", json={}).status_code == 401
    # developer -> 403 (RBAC preserved)
    assert c.post(
        "/api/ai-handoff/import",
        json={"zip_path": zip_path, "new_user_email": "bob@agency.gov"},
        headers={"X-Test-Role": "developer"},
    ).status_code == 403

    # admin -> real import runs
    resp = c.post(
        "/api/ai-handoff/import",
        json={"zip_path": zip_path, "new_user_email": "bob@agency.gov"},
        headers={"X-Test-Role": "admin"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["imported_patterns"] == 1
    assert body["status"] == "ok"


def test_handoff_import_missing_fields_is_400(ft_db):
    app = _fake_auth_app()
    c = app.test_client()
    resp = c.post("/api/ai-handoff/import", json={"zip_path": ""},
                  headers={"X-Test-Role": "admin"})
    assert resp.status_code == 400


def test_usage_route_returns_usage_count_as_pm(ft_db, monkeypatch):
    monkeypatch.setenv("ICDEV_DB_PATH", str(ft_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    _seed_pattern("px")

    app = _fake_auth_app()
    c = app.test_client()

    # RBAC preserved
    assert c.post("/api/aisg/patterns/px/deploy").status_code == 401
    assert c.post("/api/aisg/patterns/px/deploy",
                  headers={"X-Test-Role": "developer"}).status_code == 403

    resp = c.post("/api/aisg/patterns/px/deploy", headers={"X-Test-Role": "pm"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["usage_count"] == 1
    assert body["action"] == "usage_recorded"
