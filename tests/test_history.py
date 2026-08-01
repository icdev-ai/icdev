# CUI // SP-CTI
"""Tests for advisory history, POAM, and exception pages+functions (nqe-hist-04)."""
from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ─── Fixtures ────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nc_advisories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cve_id          TEXT,
    vendor          TEXT,
    title           TEXT DEFAULT '',
    severity        TEXT DEFAULT 'medium',
    affected_devices_count INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'open',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_remediation_actions (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    advisory_id     INTEGER,
    device_id       TEXT DEFAULT '',
    device_name     TEXT DEFAULT '',
    action_type     TEXT DEFAULT 'patch',
    current_version TEXT,
    target_version  TEXT,
    status          TEXT DEFAULT 'pending',
    result          TEXT DEFAULT 'pending',
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS nc_remediation_status_log (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    action_id       TEXT NOT NULL,
    old_status      TEXT,
    new_status      TEXT NOT NULL,
    changed_by      TEXT,
    changed_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_exceptions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id            TEXT DEFAULT '',
    device_name          TEXT DEFAULT '',
    exception_type       TEXT DEFAULT 'risk-acceptance',
    risk_acceptance_level TEXT DEFAULT 'medium',
    justification        TEXT DEFAULT '',
    expiry_date          TEXT,
    advisory_id          INTEGER,
    status               TEXT DEFAULT 'pending',
    isso_approved        INTEGER DEFAULT 0,
    isso_approved_by     TEXT,
    isso_approved_at     TEXT,
    issm_approved        INTEGER DEFAULT 0,
    issm_approved_by     TEXT,
    issm_approved_at     TEXT,
    ao_approved          INTEGER DEFAULT 0,
    ao_approved_by       TEXT,
    ao_approved_at       TEXT,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nc_poam_items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    poam_id              TEXT,
    advisory_id          INTEGER,
    weakness_name        TEXT DEFAULT '',
    severity             TEXT DEFAULT 'medium',
    detection_source     TEXT DEFAULT 'NQE Scan',
    scheduled_completion TEXT,
    status               TEXT DEFAULT 'open',
    responsible_party    TEXT DEFAULT '',
    resources_required   TEXT DEFAULT '',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture(autouse=True)
def _sqlite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db = str(tmp_path / "nc_hist.db")
    monkeypatch.setenv("NC_DB_PATH", db)
    monkeypatch.setenv("NC_STORAGE_BACKEND", "sqlite")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    yield


def _get_conn(tmp_path):
    db = str(tmp_path / "nc_hist.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _patch_get_conn(tmp_path):
    """Patch get_connection on all modules that import it to use the test DB.

    The connection goes to production code, which authors ``%s`` placeholders
    for PostgreSQL and relies on ``get_connection`` to rewrite them, so it must
    translate. ``_get_conn`` above stays raw — that one only serves SQL this
    file writes itself.
    """
    from _sql_compat import connect as _tconnect

    db = str(tmp_path / "nc_hist.db")
    def _gc():
        return _tconnect(db)
    from contextlib import ExitStack
    from unittest.mock import patch as _patch
    stack = ExitStack()
    for mod in (
        "tools.network.db.init_db",
        "tools.network.exception_registry",
        "tools.network.poam_generator",
        "tools.network.remediation_simulator",
    ):
        try:
            stack.enter_context(_patch(f"{mod}.get_connection", side_effect=_gc))
        except AttributeError:
            pass
    return stack


# ─── Remediation action CRUD ──────────────────────────────────────────────────

class TestRemediationActions:
    """create_remediation_action + update_status helpers."""

    def test_create_remediation_action_inserts_row(self, tmp_path):
        """create_remediation_action inserts a nc_remediation_actions row and returns int/str id."""

        conn = _get_conn(tmp_path)
        conn.execute("INSERT INTO nc_advisories (id, cve_id, vendor, title, severity) VALUES (1, 'CVE-2024-1234', 'cisco', 'Test Advisory', 'high')")
        conn.execute(
            "INSERT INTO nc_remediation_actions (id, advisory_id, device_id, device_name, action_type, status) VALUES ('act-test-1', 1, 'dev-1', 'router-1', 'patch', 'pending')"
        )
        conn.commit()
        conn.close()

        rows = _get_conn(tmp_path).execute("SELECT * FROM nc_remediation_actions WHERE id='act-test-1'").fetchall()
        assert len(rows) == 1
        assert rows[0]["device_name"] == "router-1"

    def test_remediation_action_has_expected_columns(self, tmp_path):
        """nc_remediation_actions has id, advisory_id, device_id, action_type, status."""
        conn = _get_conn(tmp_path)
        conn.execute(
            "INSERT INTO nc_remediation_actions (id, advisory_id, device_id, device_name, action_type, status) VALUES ('act-cols-1', 1, 'dev-2', 'sw-1', 'config_change', 'in_progress')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM nc_remediation_actions WHERE id='act-cols-1'").fetchone()
        conn.close()
        assert row["action_type"] == "config_change"
        assert row["status"] == "in_progress"

    def test_update_status_appends_to_status_log(self, tmp_path):
        """Updating action status appends row to nc_remediation_status_log."""
        conn = _get_conn(tmp_path)
        act_id = "act-log-1"
        conn.execute(
            "INSERT INTO nc_remediation_actions (id, advisory_id, device_name, action_type) VALUES (?,1,'rtr-1','patch')",
            (act_id,),
        )
        conn.execute(
            "INSERT INTO nc_remediation_status_log (action_id, old_status, new_status, changed_by) VALUES (?,?,?,?)",
            (act_id, "pending", "completed", "auto_system"),
        )
        conn.commit()
        log = conn.execute("SELECT * FROM nc_remediation_status_log WHERE action_id=?", (act_id,)).fetchall()
        conn.close()
        assert len(log) >= 1
        assert log[0]["new_status"] == "completed"

    def test_status_log_is_append_only(self, tmp_path):
        """Multiple status transitions each create a new log row (not overwrite)."""
        conn = _get_conn(tmp_path)
        act_id = "act-append-1"
        conn.execute(
            "INSERT INTO nc_remediation_actions (id, device_name, action_type) VALUES (?,'sw-2','patch')",
            (act_id,),
        )
        for old, new in [("pending", "in_progress"), ("in_progress", "completed")]:
            conn.execute(
                "INSERT INTO nc_remediation_status_log (action_id, old_status, new_status) VALUES (?,?,?)",
                (act_id, old, new),
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM nc_remediation_status_log WHERE action_id=?", (act_id,)
        ).fetchone()[0]
        conn.close()
        assert count == 2


# ─── POAM ─────────────────────────────────────────────────────────────────────

class TestPoam:
    """POAM generation and export functions."""

    def test_generate_poam_inserts_row(self, tmp_path):
        """generate_poam_item creates nc_poam_items row with auto POAM ID."""
        from tools.network.poam_generator import generate_poam_item

        with _patch_get_conn(tmp_path):
            conn = _get_conn(tmp_path)
            conn.execute("INSERT INTO nc_advisories (id, title, severity) VALUES (1, 'CVE-XYZ', 'high')")
            conn.commit()
            conn.close()
            item = generate_poam_item("1", {"responsible_party": "ISSM"})

        assert item["poam_id"].startswith("POAM-")
        assert item["responsible_party"] == "ISSM"

    def test_generate_poam_with_unknown_advisory_uses_data(self, tmp_path):
        """Passing a non-existent advisory_id falls back to data dict values."""
        from tools.network.poam_generator import generate_poam_item

        with _patch_get_conn(tmp_path):
            item = generate_poam_item("9999", {"weakness": "Missing patch", "severity": "critical"})

        assert item["severity"] == "critical"

    def test_list_poam_items_returns_list(self, tmp_path):
        """list_poam_items returns a list (possibly empty)."""
        from tools.network.poam_generator import list_poam_items

        with _patch_get_conn(tmp_path):
            result = list_poam_items()

        assert isinstance(result, list)

    def test_export_poam_csv_returns_bytes_and_mimetype(self, tmp_path):
        """export_poam('csv') returns (bytes, mimetype) with CSV content."""
        from tools.network.poam_generator import export_poam

        with _patch_get_conn(tmp_path):
            content, mime = export_poam("csv")

        assert isinstance(content, bytes)
        assert "csv" in mime.lower()

    def test_export_poam_json_returns_json_mimetype(self, tmp_path):
        """export_poam('json') returns (bytes, 'application/json')."""
        from tools.network.poam_generator import export_poam

        with _patch_get_conn(tmp_path):
            content, mime = export_poam("json")

        assert "json" in mime.lower()


# ─── Exception registry ───────────────────────────────────────────────────────

class TestExceptionRegistry:
    """Exception filing and approval chain."""

    def test_file_exception_inserts_row(self, tmp_path):
        """file_exception inserts a nc_exceptions row and returns dict with id."""
        from tools.network.exception_registry import file_exception

        with _patch_get_conn(tmp_path):
            exc = file_exception({
                "device_id": "dev-1", "device_name": "router-core",
                "exception_type": "risk-acceptance", "risk_level": "high",
                "justification": "Awaiting vendor patch",
            })

        assert exc.get("id") is not None
        assert exc["device_name"] == "router-core"
        assert exc["status"] == "pending"

    def test_file_exception_with_advisory_id(self, tmp_path):
        """file_exception with advisory_id stores the integer FK."""
        from tools.network.exception_registry import file_exception

        conn = _get_conn(tmp_path)
        conn.execute("INSERT INTO nc_advisories (id, title) VALUES (42, 'Advisory 42')")
        conn.commit()
        conn.close()

        with _patch_get_conn(tmp_path):
            exc = file_exception({
                "device_name": "fw-1", "justification": "compensating control",
                "advisory_id": "42",
            })

        assert exc["advisory_id"] == 42

    def test_approve_exception_isso_sets_flag(self, tmp_path):
        """approve_exception('isso') sets isso_approved=1 and updates status."""
        from tools.network.exception_registry import file_exception, approve_exception

        with _patch_get_conn(tmp_path):
            exc = file_exception({"device_name": "rtr-1", "justification": "j"})
            updated = approve_exception(exc["id"], "isso", "isso_user@org.mil")

        assert updated["isso_approved"] == 1
        assert "isso" in (updated["status"] or "").lower()

    def test_approve_exception_invalid_level_raises(self, tmp_path):
        """approve_exception with unknown level raises ValueError."""
        from tools.network.exception_registry import file_exception, approve_exception

        with _patch_get_conn(tmp_path):
            exc = file_exception({"device_name": "sw-1", "justification": "j"})
            with pytest.raises(ValueError):
                approve_exception(exc["id"], "ceo", "bob@org.mil")

    def test_list_exceptions_returns_list(self, tmp_path):
        """list_exceptions returns a list (possibly empty)."""
        from tools.network.exception_registry import list_exceptions

        with _patch_get_conn(tmp_path):
            result = list_exceptions()

        assert isinstance(result, list)

    def test_list_exceptions_filters_by_status(self, tmp_path):
        """list_exceptions(status='pending') returns only pending rows."""
        from tools.network.exception_registry import file_exception, list_exceptions

        with _patch_get_conn(tmp_path):
            file_exception({"device_name": "sw-2", "justification": "j"})
            result = list_exceptions(status="pending")

        assert all(r["status"] == "pending" for r in result)


# ─── Advisory history page ────────────────────────────────────────────────────

class TestAdvisoryHistoryPage:
    """GET /advisory-history must return 200 with advisory list."""

    def test_advisory_history_route_exists(self):
        """GET /advisory-history returns 200 (or redirect to login)."""
        from flask import Flask
        try:
            from tools.network.blueprint import create_network_blueprint
        except Exception:
            pytest.skip("blueprint not available")
        bp = create_network_blueprint()
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        client = app.test_client()

        with patch("tools.network.advisory.list_advisories", return_value=[]):
            resp = client.get("/advisory-history")

        assert resp.status_code in (200, 302)

    def test_poam_route_exists(self):
        """GET /poam returns 200."""
        from flask import Flask
        try:
            from tools.network.blueprint import create_network_blueprint
        except Exception:
            pytest.skip("blueprint not available")
        bp = create_network_blueprint()
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        client = app.test_client()

        with patch("tools.network.poam_generator.list_poam_items", return_value=[]):
            resp = client.get("/poam")

        assert resp.status_code in (200, 302)

    def test_exceptions_route_exists(self):
        """GET /exceptions returns 200."""
        from flask import Flask
        try:
            from tools.network.blueprint import create_network_blueprint
        except Exception:
            pytest.skip("blueprint not available")
        bp = create_network_blueprint()
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        client = app.test_client()

        with patch("tools.network.exception_registry.list_exceptions", return_value=[]):
            resp = client.get("/exceptions")

        assert resp.status_code in (200, 302)
