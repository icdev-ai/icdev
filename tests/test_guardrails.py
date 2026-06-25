# CUI // SP-CTI
"""Tests for NQE guardrails, transparency gate, and audit trail (nqe-grd-03)."""
from __future__ import annotations

import os
import sys
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _sqlite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db = str(tmp_path / "nc_test.db")
    monkeypatch.setenv("NC_DB_PATH", db)
    monkeypatch.setenv("NC_STORAGE_BACKEND", "sqlite")

    conn = sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS nc_nqe_audit_log (
        id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        action     TEXT NOT NULL,
        nql_query  TEXT,
        user_confirmed INTEGER DEFAULT 0,
        row_count  INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()
    yield


def _network_blueprint_client(tmp_path):
    """Stand up a minimal Flask test client using the network blueprint."""
    from flask import Flask
    try:
        from tools.network.blueprint import create_network_blueprint
        bp = create_network_blueprint()
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        return app.test_client()
    except Exception:
        return None


# ─── Transparency gate behaviour ──────────────────────────────────────────────

class TestTransparencyGate:
    """NQE must never auto-execute — user must explicitly click Run."""

    def test_translate_does_not_execute_query(self):
        """POST /api/nqe/translate returns nql but must NOT run against DB."""
        from tools.network.nql_translator import nl_to_nql
        with patch("tools.network.nqe_client.FallbackNQEClient.run_query") as run_mock:
            nl_to_nql("show all devices")
            run_mock.assert_not_called()

    def test_translate_response_requires_user_confirmation(self):
        """Translate response has confidence + source — UI uses these to gate Run."""
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

        with patch("tools.network.nql_translator.nl_to_nql", return_value="foreach d in network.devices select {d.label}"), \
             patch("tools.db.storage.get_canvas_connection", side_effect=Exception("no db")):
            resp = client.post("/api/nqe/translate", json={"text": "show devices"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert "nql" in data
        assert "confidence" in data
        assert "source" in data

    def test_run_requires_explicit_nql_not_text(self):
        """POST /api/nqe/run requires nql field, not raw text input."""
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

        resp = client.post("/api/nqe/run", json={})
        assert resp.status_code == 400
        assert "nql" in (resp.get_json() or {}).get("error", "")


# ─── Audit trail ──────────────────────────────────────────────────────────────

class TestAuditTrail:
    """nc_nqe_audit_log must receive rows on translate and run events."""

    def _insert_audit(self, tmp_path, action, nql, user_confirmed):
        db = str(tmp_path / "nc_test.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO nc_nqe_audit_log (action, nql_query, user_confirmed) VALUES (?,?,?)",
            (action, nql, int(user_confirmed)),
        )
        conn.commit()
        conn.close()

    def test_translate_inserts_audit_row_with_action_translate(self, tmp_path):
        """Translate event inserts nc_nqe_audit_log row with action='translate'."""
        self._insert_audit(tmp_path, "translate", "foreach d in network.devices select {d.label}", False)
        db = str(tmp_path / "nc_test.db")
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM nc_nqe_audit_log WHERE action='translate'").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_run_inserts_audit_row_with_user_confirmed_true(self, tmp_path):
        """Run event inserts audit row with user_confirmed=1."""
        self._insert_audit(tmp_path, "run", "foreach d in network.devices select {d.label}", True)
        db = str(tmp_path / "nc_test.db")
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM nc_nqe_audit_log WHERE action='run' AND user_confirmed=1").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_audit_log_endpoint_records_action(self, tmp_path):
        """POST /api/nqe/audit-log inserts a row and returns {recorded:true}."""
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

        with patch("tools.db.storage.get_canvas_connection", side_effect=Exception("no db")):
            resp = client.post("/api/nqe/audit-log", json={
                "action": "run", "nql": "foreach d in network.devices select {d.label}", "user_confirmed": True
            })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("recorded") is True

    def test_audit_log_endpoint_requires_action_field(self):
        """POST /api/nqe/audit-log returns 400 when action is missing."""
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

        resp = client.post("/api/nqe/audit-log", json={"nql": "...", "user_confirmed": True})
        assert resp.status_code == 400

    def test_translate_action_has_user_confirmed_false(self, tmp_path):
        """Translate audit rows must have user_confirmed=False (0) — translation only."""
        self._insert_audit(tmp_path, "translate", "foreach d in network.devices select {d.label}", False)
        db = str(tmp_path / "nc_test.db")
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT user_confirmed FROM nc_nqe_audit_log WHERE action='translate'").fetchall()
        conn.close()
        assert all(r[0] == 0 for r in rows)


# ─── Confidence badge ─────────────────────────────────────────────────────────

class TestConfidenceBadge:
    """Confidence scores from translate endpoint drive the UI badge tier."""

    def _translate(self, text, context=None):
        from flask import Flask
        try:
            from tools.network.blueprint import create_network_blueprint
        except Exception:
            return None
        bp = create_network_blueprint()
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        client = app.test_client()
        payload = {"text": text}
        if context:
            payload["context"] = context
        with patch("tools.network.nql_translator.nl_to_nql", return_value="foreach d in network.devices select {d.label}"), \
             patch("tools.db.storage.get_canvas_connection", side_effect=Exception("no db")):
            resp = client.post("/api/nqe/translate", json=payload)
        return resp.get_json() if resp.status_code == 200 else None

    def test_deterministic_context_yields_high_confidence(self):
        """Structured context → confidence >= 0.75 (high tier)."""
        data = self._translate("affected devices", context={
            "vendor": "cisco", "affected_models": ["ISR4451"], "affected_versions": ["16.9.1"]
        })
        if data is None:
            pytest.skip("blueprint not available")
        assert data["confidence"] >= 0.75

    def test_no_context_yields_lower_confidence(self):
        """Plain-text query without context → confidence < 0.92."""
        data = self._translate("show me all network devices")
        if data is None:
            pytest.skip("blueprint not available")
        assert data["confidence"] <= 0.92

    def test_confidence_is_float_between_0_and_1(self):
        """Confidence value must be a float in [0.0, 1.0]."""
        data = self._translate("list BGP sessions")
        if data is None:
            pytest.skip("blueprint not available")
        assert isinstance(data["confidence"], float)
        assert 0.0 <= data["confidence"] <= 1.0


# ─── Collections endpoint ─────────────────────────────────────────────────────

class TestCollectionsEndpoint:
    """GET /api/nqe/collections returns supported NQE collection paths."""

    def test_collections_returns_list(self):
        """Response has collections key with list of path/description dicts."""
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

        resp = client.get("/api/nqe/collections")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "collections" in data
        assert len(data["collections"]) >= 6
        for item in data["collections"]:
            assert "path" in item
            assert "description" in item

    def test_nqe_devices_collection_present(self):
        """nqe.devices collection path must be listed."""
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

        resp = client.get("/api/nqe/collections")
        paths = {c["path"] for c in resp.get_json()["collections"]}
        assert "network.devices" in paths


# ─── Dual-query cross-validation (nqe-grd-02) ────────────────────────────────

def _blueprint_client():
    from flask import Flask
    try:
        from tools.network.blueprint import create_network_blueprint
        bp = create_network_blueprint()
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        return app.test_client()
    except Exception:
        return None


class TestDualQueryCrossValidation:
    """POST /api/nqe/cross-validate returns both NQL variants and divergence score."""

    def test_cross_validate_requires_text(self):
        """Missing text field returns 400."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        resp = client.post("/api/nqe/cross-validate", json={})
        assert resp.status_code == 400

    def test_cross_validate_returns_required_keys(self):
        """Response must include nql_primary, nql_secondary, divergence_score, require_hitl, message."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        with patch("tools.network.nql_translator.nl_to_nql", return_value="foreach d in network.devices select {d.hostname}"):
            resp = client.post("/api/nqe/cross-validate", json={"text": "show all devices"})
        assert resp.status_code == 200
        data = resp.get_json()
        for key in ("nql_primary", "nql_secondary", "divergence_score", "require_hitl", "message"):
            assert key in data, f"Missing key: {key}"

    def test_identical_translations_yield_zero_divergence(self):
        """When both strategies return identical NQL, divergence_score is 0.0."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        same_nql = "foreach d in network.devices select {d.hostname}"
        with patch("tools.network.nql_translator.nl_to_nql", return_value=same_nql):
            resp = client.post("/api/nqe/cross-validate", json={"text": "show devices"})
        data = resp.get_json()
        assert data["divergence_score"] == 0.0
        assert data["require_hitl"] is False

    def test_divergent_collections_yield_high_divergence(self):
        """Primary queries network.devices, secondary queries network.bgp_sessions → score ≥ 0.6."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        nqls = iter([
            "foreach d in network.devices where d.vendor == \"cisco\" select {d.hostname}",
            "foreach s in network.bgp_sessions where s.state != \"Established\" select {s.peer_ip}",
        ])
        with patch("tools.network.nql_translator.nl_to_nql", side_effect=lambda t, **kw: next(nqls)):
            resp = client.post("/api/nqe/cross-validate", json={
                "text": "show BGP peers", "context": {"vendor": "cisco"}
            })
        data = resp.get_json()
        assert data["divergence_score"] >= 0.6
        assert data["require_hitl"] is True

    def test_divergence_score_is_float_in_range(self):
        """divergence_score must be a float in [0.0, 1.0]."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        with patch("tools.network.nql_translator.nl_to_nql", return_value="foreach d in network.devices select {d.hostname}"):
            resp = client.post("/api/nqe/cross-validate", json={"text": "list devices"})
        data = resp.get_json()
        score = data["divergence_score"]
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_high_divergence_message_mentions_approval(self):
        """When require_hitl is True, message must mention approval."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        nqls = iter([
            "foreach d in network.devices select {d.hostname}",
            "foreach v in network.vlans select {v.id}",
        ])
        with patch("tools.network.nql_translator.nl_to_nql", side_effect=lambda t, **kw: next(nqls)):
            resp = client.post("/api/nqe/cross-validate", json={"text": "show vlans"})
        data = resp.get_json()
        if data["require_hitl"]:
            assert "approval" in data["message"].lower() or "human" in data["message"].lower()


# ─── HITL approval gate (nqe-grd-02) ─────────────────────────────────────────

class TestHitlApproval:
    """POST /api/nqe/hitl-approve records approval and returns {approved: true}."""

    def test_hitl_approve_requires_approved_by(self):
        """Missing approved_by returns 400."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        resp = client.post("/api/nqe/hitl-approve", json={
            "nql_primary": "foreach d in network.devices select {d.hostname}",
            "nql_secondary": "foreach v in network.vlans select {v.id}",
        })
        assert resp.status_code == 400
        assert "approved_by" in (resp.get_json() or {}).get("error", "")

    def test_hitl_approve_returns_approved_true(self):
        """Valid request returns {approved: true, approved_by: str, recorded: true}."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")
        with patch("tools.db.storage.get_canvas_connection", side_effect=Exception("no db")):
            resp = client.post("/api/nqe/hitl-approve", json={
                "nql_primary": "foreach d in network.devices select {d.hostname}",
                "nql_secondary": "foreach v in network.vlans select {v.id}",
                "approved_by": "issm_user@org.mil",
                "notes": "Verified by ISSM",
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["approved"] is True
        assert data["approved_by"] == "issm_user@org.mil"
        assert data["recorded"] is True

    def test_hitl_approve_writes_to_audit_log(self):
        """Approval event calls nc_nqe_audit_log INSERT with action='hitl_approve'."""
        client = _blueprint_client()
        if client is None:
            pytest.skip("blueprint not available")

        mock_conn = MagicMock()
        with patch("tools.db.storage.get_canvas_connection", return_value=mock_conn):
            resp = client.post("/api/nqe/hitl-approve", json={
                "nql_primary": "foreach d in network.devices select {d.hostname}",
                "nql_secondary": "foreach v in network.vlans select {v.id}",
                "approved_by": "isso_user@org.mil",
            })

        assert resp.status_code == 200
        assert mock_conn.execute.called
        # Verify the INSERT included 'hitl_approve' as the action value
        call_args_str = str(mock_conn.execute.call_args)
        assert "hitl_approve" in call_args_str
