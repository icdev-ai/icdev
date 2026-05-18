"""Tests for use-cases import/export API endpoints."""
import io
import sqlite3
from unittest.mock import patch

import pytest
from flask import Flask


@pytest.fixture
def chat_app(icdev_db, monkeypatch):
    """Minimal Flask app with chat_api blueprint and DB pointed at icdev_db."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    app = Flask(__name__)
    app.config["TESTING"] = True

    from tools.dashboard.api.chat import chat_api
    app.register_blueprint(chat_api)

    return app


@pytest.fixture
def chat_client(chat_app):
    return chat_app.test_client()


def test_import_valid_bundle_returns_breakdown(chat_client):
    """POST /api/chat/use-cases/import with a valid YAML bundle returns imported/skipped/errors."""
    uc_id = "test-import-uc-001"
    yaml_content = (
        "icdev_uc_bundle: '1.0'\n"
        "use_cases:\n"
        f"  - id: {uc_id}\n"
        "    label: Test Import Use Case\n"
        "    description: Created by unit test\n"
    )
    # _uc_load_yaml reads args/use_cases.yaml; patch it to return empty so our
    # test ID never collides with a YAML-base use case.
    with patch("tools.dashboard.api.chat._uc_load_yaml", return_value=[]):
        resp = chat_client.post(
            "/api/chat/use-cases/import",
            data={"file": (io.BytesIO(yaml_content.encode()), "bundle.yaml")},
            content_type="multipart/form-data",
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert "imported" in body
    assert "skipped" in body
    assert "errors" in body
    assert len(body["imported"]) == 1
    assert body["imported"][0] == uc_id
    assert body["skipped"] == []
    assert body["errors"] == []


def test_import_overwrite_false_skips_duplicate(chat_client, icdev_db):
    """overwrite=false (default) skips an ID that already exists in the DB."""
    uc_id = "test_import_uc"
    original_label = "Original Label"

    # Pre-create table and insert the existing row directly into the test DB.
    conn = sqlite3.connect(str(icdev_db))
    conn.execute("""CREATE TABLE IF NOT EXISTS use_case_overrides (
        id TEXT PRIMARY KEY, label TEXT, description TEXT, icon TEXT, badge TEXT,
        agent_model TEXT, ricoas INTEGER, boost_threshold INTEGER,
        system_prompt TEXT, seed_message TEXT,
        canvas_wiring TEXT, quick_actions TEXT,
        updated_at TEXT, updated_by TEXT,
        classification TEXT DEFAULT NULL, fast_track INTEGER DEFAULT 0,
        skip_requirement_types TEXT, user_config TEXT,
        is_user_created INTEGER DEFAULT 0, created_by TEXT DEFAULT NULL,
        created_at TEXT DEFAULT NULL, template_requirements TEXT DEFAULT NULL,
        category TEXT DEFAULT NULL, tenant_id TEXT DEFAULT '',
        canvas_seeds TEXT DEFAULT NULL, workflow_steps TEXT DEFAULT NULL
    )""")
    conn.execute(
        "INSERT INTO use_case_overrides (id, label, is_user_created) VALUES (?, ?, 1)",
        (uc_id, original_label),
    )
    conn.commit()
    conn.close()

    yaml_content = (
        "icdev_uc_bundle: '1.0'\n"
        "use_cases:\n"
        f"  - id: {uc_id}\n"
        "    label: New Label\n"
        "    description: Should not overwrite\n"
    )

    with patch("tools.dashboard.api.chat._uc_load_yaml", return_value=[]):
        resp = chat_client.post(
            "/api/chat/use-cases/import",
            json={"yaml_content": yaml_content},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert any(s["id"] == uc_id for s in body["skipped"]), (
        f"{uc_id} should be in skipped; got {body}"
    )
    assert uc_id not in body["imported"]

    # Verify the DB row was not modified.
    conn = sqlite3.connect(str(icdev_db))
    row = conn.execute(
        "SELECT label FROM use_case_overrides WHERE id=?", (uc_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == original_label


def test_import_overwrite_true_replaces_existing_uc_fields(chat_client, icdev_db):
    """overwrite=true replaces an existing DB row's fields with the bundle values."""
    uc_id = "test_import_uc"
    old_label = "OldLabel"

    # Pre-insert existing row directly into the test DB.
    conn = sqlite3.connect(str(icdev_db))
    conn.execute("""CREATE TABLE IF NOT EXISTS use_case_overrides (
        id TEXT PRIMARY KEY, label TEXT, description TEXT, icon TEXT, badge TEXT,
        agent_model TEXT, ricoas INTEGER, boost_threshold INTEGER,
        system_prompt TEXT, seed_message TEXT,
        canvas_wiring TEXT, quick_actions TEXT,
        updated_at TEXT, updated_by TEXT,
        classification TEXT DEFAULT NULL, fast_track INTEGER DEFAULT 0,
        skip_requirement_types TEXT, user_config TEXT,
        is_user_created INTEGER DEFAULT 0, created_by TEXT DEFAULT NULL,
        created_at TEXT DEFAULT NULL, template_requirements TEXT DEFAULT NULL,
        category TEXT DEFAULT NULL, tenant_id TEXT DEFAULT '',
        canvas_seeds TEXT DEFAULT NULL, workflow_steps TEXT DEFAULT NULL
    )""")
    conn.execute(
        "INSERT INTO use_case_overrides (id, label, is_user_created) VALUES (?, ?, 1)",
        (uc_id, old_label),
    )
    conn.commit()
    conn.close()

    new_label = "NewLabel"
    yaml_content = (
        "icdev_uc_bundle: '1.0'\n"
        "use_cases:\n"
        f"  - id: {uc_id}\n"
        f"    label: {new_label}\n"
        "    description: Overwritten by test\n"
    )

    with patch("tools.dashboard.api.chat._uc_load_yaml", return_value=[]):
        resp = chat_client.post(
            "/api/chat/use-cases/import",
            json={"yaml_content": yaml_content, "overwrite": True},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert uc_id in body["imported"], f"{uc_id} should be in imported; got {body}"
    assert not any(s.get("id") == uc_id for s in body["skipped"]), (
        f"{uc_id} should not be in skipped; got {body}"
    )

    # Verify DB row was updated with the new label.
    conn = sqlite3.connect(str(icdev_db))
    row = conn.execute(
        "SELECT label FROM use_case_overrides WHERE id=?", (uc_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == new_label


def test_export_import_round_trip_canvas_seeds_and_workflow_steps(chat_client):
    """canvas_seeds and workflow_steps survive export->import->get round-trip for ato_package_builder."""
    import yaml as _yaml

    # Step 1: Export ato_package_builder
    resp = chat_client.get("/api/chat/use-cases/ato_package_builder/export")
    assert resp.status_code == 200, f"Export failed: {resp.data}"

    # Step 2: Parse YAML and assert canvas_seeds + workflow_steps are present and non-empty
    bundle = _yaml.safe_load(resp.data.decode("utf-8"))
    assert "use_cases" in bundle
    assert len(bundle["use_cases"]) == 1
    exported_uc = bundle["use_cases"][0]
    assert "canvas_seeds" in exported_uc, "canvas_seeds missing from export"
    assert "workflow_steps" in exported_uc, "workflow_steps missing from export"
    assert exported_uc["canvas_seeds"], "canvas_seeds is empty in export"
    assert exported_uc["workflow_steps"], "workflow_steps is empty in export"

    original_canvas_seeds = exported_uc["canvas_seeds"]
    original_workflow_steps = exported_uc["workflow_steps"]

    # Step 3: Re-import with overwrite=True
    yaml_str = _yaml.dump(bundle, allow_unicode=True, sort_keys=False, default_flow_style=False)
    resp = chat_client.post(
        "/api/chat/use-cases/import",
        json={"yaml_content": yaml_str, "overwrite": True},
    )
    assert resp.status_code == 200, f"Import failed: {resp.data}"
    body = resp.get_json()
    assert "ato_package_builder" in body["imported"], (
        f"ato_package_builder not in imported: {body}"
    )

    # Step 4: Get the use case and assert canvas_seeds + workflow_steps match original
    resp = chat_client.get("/api/chat/use-cases/ato_package_builder")
    assert resp.status_code == 200, f"GET use case failed: {resp.data}"
    uc = resp.get_json()
    assert uc.get("canvas_seeds") == original_canvas_seeds, (
        f"canvas_seeds mismatch after round-trip:\n"
        f"  got:      {uc.get('canvas_seeds')}\n"
        f"  expected: {original_canvas_seeds}"
    )
    assert uc.get("workflow_steps") == original_workflow_steps, (
        f"workflow_steps mismatch after round-trip:\n"
        f"  got:      {uc.get('workflow_steps')}\n"
        f"  expected: {original_workflow_steps}"
    )
