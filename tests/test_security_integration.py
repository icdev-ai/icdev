#!/usr/bin/env python3
# CUI // SP-CTI
"""End-to-end integration tests for the 3-layer security stack.

Verifies that RLS predicate injection (row-level), column masking, and
field-level response filtering all fire correctly in a real Flask request,
including audit records when each layer is enabled.

Each test uses an isolated SQLite DB via tmp_path so the real icdev.db is
never touched.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from flask import Flask, g, jsonify

from tools.db import storage as _storage_mod
from tools.security import field_security as _field_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_dict(row) -> dict:
    """Convert sqlite3.Row or DictRow to a plain dict."""
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return {}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def secure_app(tmp_path, monkeypatch):
    """Minimal Flask app wired with init_security and a test DB."""
    db_file = tmp_path / "test_security.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))

    # Seed the test DB with tables and data matching security_config.yaml policies.
    raw = sqlite3.connect(str(db_file))
    raw.executescript("""
        CREATE TABLE dashboard_users (
            id              INTEGER PRIMARY KEY,
            tenant_id       TEXT,
            classification  TEXT DEFAULT 'CUI',
            email           TEXT,
            api_key_hash    TEXT,
            password_hash   TEXT,
            role            TEXT
        );
        CREATE TABLE rls_audit (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name  TEXT NOT NULL,
            action      TEXT,
            tenant_id   TEXT,
            details     TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE column_mask_audit (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name     TEXT NOT NULL,
            role           TEXT,
            masked_columns TEXT,
            recorded_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE field_filter_audit (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_name     TEXT,
            role            TEXT,
            filtered_fields TEXT,
            recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO dashboard_users VALUES
            (1,'tenant_a','CUI','alice@test.com','hash_abc','pw_hash_a','admin'),
            (2,'tenant_b','CUI','bob@test.com',  'hash_xyz','pw_hash_b','viewer');
    """)
    raw.commit()
    raw.close()

    from tools.security.middleware import init_security
    from tools.db.storage import get_connection

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app._test_db = str(db_file)

    # Inject test auth context BEFORE init_security so the middleware reads it.
    @app.before_request
    def _inject_test_ctx():
        from flask import request
        g.tenant_id = request.headers.get("X-Tenant-Id") or None
        g.user_role = request.headers.get("X-Role", "")
        g.current_user = {
            "id": "test_user",
            "role": request.headers.get("X-Role", ""),
            "classification": "CUI",
            "impact_level": "IL4",
        }

    init_security(app, classification="CUI")

    @app.route("/api/users")
    def list_users():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM dashboard_users").fetchall()
        return jsonify([_as_dict(r) for r in rows])

    @app.route("/api/users/<int:uid>")
    def get_user(uid):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM dashboard_users WHERE id = ?", (uid,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(_as_dict(row))

    return app


@pytest.fixture(autouse=True)
def _reset_audit_flags(monkeypatch):
    """Ensure audit flags start disabled for every test (opt-in per test)."""
    monkeypatch.setattr(_storage_mod, "AUDIT_RLS", False)
    monkeypatch.setattr(_storage_mod, "AUDIT_COLUMN", False)
    monkeypatch.setattr(_field_mod, "AUDIT_FIELD", False)


# ---------------------------------------------------------------------------
# Row-Level Security (RLS)
# ---------------------------------------------------------------------------

class TestRLSIntegration:
    def test_tenant_filter_returns_single_row(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Tenant-Id": "tenant_a", "X-Role": "admin",
                         "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        assert len(data) == 1, f"Expected 1 row, got {len(data)}"
        assert data[0]["tenant_id"] == "tenant_a"

    def test_different_tenant_sees_own_row(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Tenant-Id": "tenant_b", "X-Role": "admin",
                         "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        assert all(r["tenant_id"] == "tenant_b" for r in data)

    def test_no_tenant_returns_both_rows(self, secure_app):
        # classification-only filter: all rows have classification=CUI, both returned
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Role": "admin", "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        assert len(data) == 2

    def test_classification_ceiling_header(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get("/api/users", headers={"Accept": "application/json"})
        assert resp.headers.get("X-Classification-Ceiling") == "CUI"


# ---------------------------------------------------------------------------
# Column-Level Masking
# ---------------------------------------------------------------------------

class TestColumnMaskingIntegration:
    def test_viewer_email_redacted_in_fetchall(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Role": "viewer", "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        for row in data:
            assert row["email"] == "[REDACTED]", f"email not redacted: {row}"
            assert row["api_key_hash"] is None, f"api_key_hash not nulled: {row}"
            assert row["password_hash"] is None, f"password_hash not nulled: {row}"

    def test_viewer_email_redacted_in_fetchone(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users/1",
                headers={"X-Role": "viewer", "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        assert data["email"] == "[REDACTED]"
        assert data["api_key_hash"] is None

    def test_admin_sees_clear_text_email(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Role": "admin", "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        emails = [r["email"] for r in data]
        assert any("@" in e for e in emails), "Admin should see clear-text emails"

    def test_auditor_key_nulled_email_visible(self, secure_app):
        # auditor policy: api_key_hash→null, password_hash→null; email NOT redacted
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Role": "auditor", "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        for row in data:
            assert row["api_key_hash"] is None
            assert row["password_hash"] is None
            assert "@" in (row["email"] or ""), "auditor should see email"


# ---------------------------------------------------------------------------
# Field-Level Security
# ---------------------------------------------------------------------------

class TestFieldSecurityIntegration:
    def test_field_filter_header_set_for_viewer(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Role": "viewer", "Accept": "application/json"},
            )
        # /api/users → user_profile schema → viewer policy exists → header set
        assert resp.headers.get("X-Field-Filtered") == "true"

    def test_field_filter_header_absent_for_unknown_role(self, secure_app):
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Role": "unknown_role", "Accept": "application/json"},
            )
        # No policy for unknown_role → no filtering → header absent
        assert resp.headers.get("X-Field-Filtered") is None

    def test_field_filter_applies_to_json_body(self, secure_app):
        # user_profile/viewer policy redacts email — already done by column masking,
        # but we verify the field filter fires independently via the header.
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Role": "viewer", "Accept": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.headers.get("X-Field-Filtered") == "true"


# ---------------------------------------------------------------------------
# Combined: all three layers in one request
# ---------------------------------------------------------------------------

class TestAllLayersTogether:
    def test_rls_and_column_mask_together(self, secure_app):
        """RLS filters to tenant_a AND column masking applies in same request."""
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Tenant-Id": "tenant_a", "X-Role": "viewer",
                         "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        assert len(data) == 1, "RLS should have filtered to 1 row"
        assert data[0]["tenant_id"] == "tenant_a"
        assert data[0]["email"] == "[REDACTED]"
        assert data[0]["api_key_hash"] is None

    def test_all_three_layers_on_single_request(self, secure_app):
        """RLS + column mask + field filter all fire on one request."""
        with secure_app.test_client() as c:
            resp = c.get(
                "/api/users",
                headers={"X-Tenant-Id": "tenant_a", "X-Role": "viewer",
                         "Accept": "application/json"},
            )
        data = json.loads(resp.data)
        # RLS: 1 row
        assert len(data) == 1
        # Column masking: email redacted
        assert data[0]["email"] == "[REDACTED]"
        # Field filtering: header present
        assert resp.headers.get("X-Field-Filtered") == "true"
        # Classification ceiling: always set
        assert resp.headers.get("X-Classification-Ceiling") == "CUI"


# ---------------------------------------------------------------------------
# Audit Logging
# ---------------------------------------------------------------------------

class TestAuditLogging:
    @pytest.fixture(autouse=True)
    def enable_all_audit(self, monkeypatch):
        monkeypatch.setattr(_storage_mod, "AUDIT_RLS", True)
        monkeypatch.setattr(_storage_mod, "AUDIT_COLUMN", True)
        monkeypatch.setattr(_field_mod, "AUDIT_FIELD", True)

    def _read_audit(self, db_path: str, table: str) -> list:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        conn.close()
        return rows

    def test_rls_audit_record_written(self, secure_app):
        with secure_app.test_client() as c:
            c.get(
                "/api/users",
                headers={"X-Tenant-Id": "tenant_a", "X-Role": "admin",
                         "Accept": "application/json"},
            )
        rows = self._read_audit(secure_app._test_db, "rls_audit")
        assert len(rows) >= 1, "rls_audit should have at least one record"
        # Check tenant_id is recorded
        tenant_ids = [r[3] for r in rows]  # tenant_id column
        assert "tenant_a" in tenant_ids

    def test_column_mask_audit_record_written(self, secure_app):
        with secure_app.test_client() as c:
            c.get(
                "/api/users",
                headers={"X-Role": "viewer", "Accept": "application/json"},
            )
        rows = self._read_audit(secure_app._test_db, "column_mask_audit")
        assert len(rows) >= 1, "column_mask_audit should have at least one record"
        # Verify the masked columns are recorded
        masked = json.loads(rows[0][3])  # masked_columns column
        assert "email" in masked

    def test_field_filter_audit_record_written(self, secure_app):
        with secure_app.test_client() as c:
            c.get(
                "/api/users",
                headers={"X-Role": "viewer", "Accept": "application/json"},
            )
        rows = self._read_audit(secure_app._test_db, "field_filter_audit")
        assert len(rows) >= 1, "field_filter_audit should have at least one record"
        schema = rows[0][1]  # schema_name column
        assert schema == "user_profile"

    def test_audit_disabled_writes_no_records(self, secure_app, monkeypatch):
        monkeypatch.setattr(_storage_mod, "AUDIT_RLS", False)
        monkeypatch.setattr(_storage_mod, "AUDIT_COLUMN", False)
        monkeypatch.setattr(_field_mod, "AUDIT_FIELD", False)
        with secure_app.test_client() as c:
            c.get(
                "/api/users",
                headers={"X-Tenant-Id": "tenant_a", "X-Role": "viewer",
                         "Accept": "application/json"},
            )
        assert self._read_audit(secure_app._test_db, "rls_audit") == []
        assert self._read_audit(secure_app._test_db, "column_mask_audit") == []
        assert self._read_audit(secure_app._test_db, "field_filter_audit") == []

    def test_all_three_layers_audited_in_one_request(self, secure_app):
        with secure_app.test_client() as c:
            c.get(
                "/api/users",
                headers={"X-Tenant-Id": "tenant_a", "X-Role": "viewer",
                         "Accept": "application/json"},
            )
        assert len(self._read_audit(secure_app._test_db, "rls_audit")) >= 1
        assert len(self._read_audit(secure_app._test_db, "column_mask_audit")) >= 1
        assert len(self._read_audit(secure_app._test_db, "field_filter_audit")) >= 1
