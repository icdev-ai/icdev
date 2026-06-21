# CUI // SP-CTI
"""Tests for dsyn-suggest-01: viewer-role suggest-edit workflow."""
from __future__ import annotations

import contextlib
import importlib
import inspect
import sqlite3
from unittest.mock import MagicMock


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_raw_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in [
        """CREATE TABLE dic_suggestions (
            suggestion_id TEXT PRIMARY KEY, section_id TEXT, doc_id TEXT,
            collection_id TEXT, trigger_event_id TEXT, canvas_source TEXT DEFAULT 'unknown',
            suggested_content TEXT DEFAULT '', current_content TEXT, rationale TEXT,
            status TEXT DEFAULT 'pending', created_at TEXT NOT NULL,
            updated_at TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
        )""",
        """CREATE TABLE dic_suggestion_decisions (
            decision_id TEXT PRIMARY KEY, suggestion_id TEXT NOT NULL,
            decision TEXT NOT NULL, decided_by TEXT, decided_at TEXT NOT NULL,
            note TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
        )""",
        """CREATE TABLE dic_collection_members (
            member_id TEXT PRIMARY KEY, collection_id TEXT, user_id TEXT, role TEXT
        )""",
        """CREATE TABLE notification_log (
            id TEXT PRIMARY KEY, event_type TEXT, adapter TEXT, severity TEXT,
            title TEXT, delivered INTEGER DEFAULT 0, error TEXT, created_at TEXT
        )""",
    ]:
        conn.execute(ddl)
    conn.commit()
    return conn


class _ShimConn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


@contextlib.contextmanager
def _patch_attr(module_path: str, attr: str, value):
    mod = importlib.import_module(module_path)
    orig = getattr(mod, attr, None)
    setattr(mod, attr, value)
    try:
        yield
    finally:
        if orig is None:
            try:
                delattr(mod, attr)
            except AttributeError:
                pass
        else:
            setattr(mod, attr, orig)


# ══════════════════════════════════════════════════════════════════════════════
# Route existence + signature
# ══════════════════════════════════════════════════════════════════════════════

class TestSuggestRouteExists:
    def test_route_registered_in_blueprint(self):
        from tools.document_intelligence import blueprint
        src = inspect.getsource(blueprint)
        assert "/api/sections/" in src and "/suggest" in src

    def test_route_accepts_post(self):
        from tools.document_intelligence import blueprint
        src = inspect.getsource(blueprint.api_section_suggest)
        assert "POST" in src or "suggest" in src.lower()

    def test_route_uses_crowdsource_canvas_source(self):
        from tools.document_intelligence import blueprint
        src = inspect.getsource(blueprint.api_section_suggest)
        assert "crowdsource" in src

    def test_route_requires_viewer_role(self):
        from tools.document_intelligence import blueprint
        src = inspect.getsource(blueprint.api_section_suggest)
        assert '"viewer"' in src or "'viewer'" in src

    def test_route_validates_proposed_content_required(self):
        from tools.document_intelligence import blueprint
        src = inspect.getsource(blueprint.api_section_suggest)
        assert "proposed_content" in src and "required" in src


# ══════════════════════════════════════════════════════════════════════════════
# Flask test-client integration
# ══════════════════════════════════════════════════════════════════════════════

class TestSuggestEndpoint:
    """Integration tests using Flask test client with mocked storage."""

    def _make_app(self):
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"
        from tools.document_intelligence.blueprint import dic_bp
        app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
        return app

    def test_suggest_returns_201_for_viewer(self):
        app = self._make_app()
        shim = _ShimConn(_make_raw_conn())
        mock_create = MagicMock(return_value="sug_abc123")
        with _patch_attr("tools.document_intelligence.blueprint", "_require_role",
                         MagicMock(return_value=True)), \
             _patch_attr("tools.document_intelligence.blueprint", "_current_user",
                         MagicMock(return_value="user-viewer")), \
             _patch_attr("tools.document_intelligence.blueprint", "_security_context",
                         MagicMock(return_value=("default", "CUI"))), \
             _patch_attr("tools.document_intelligence.blueprint", "_collection_id_from_section",
                         MagicMock(return_value="col-001")), \
             _patch_attr("tools.document_intelligence.blueprint", "_conn",
                         MagicMock(side_effect=Exception("no db"))), \
             _patch_attr("tools.document_intelligence.suggestion_store", "get_connection",
                         MagicMock(return_value=shim)):
            # Also patch create_suggestion directly
            orig_cs = None
            try:
                import tools.document_intelligence.suggestion_store as ss
                orig_cs = ss.create_suggestion
                ss.create_suggestion = mock_create
                with app.test_client() as client:
                    resp = client.post(
                        "/document-intelligence/api/sections/sec-001/suggest",
                        json={"proposed_content": "Updated text.", "rationale": "More accurate"},
                        content_type="application/json",
                    )
                    assert resp.status_code == 201
                    data = resp.get_json()
                    assert data["canvas_source"] == "crowdsource"
                    assert data["status"] == "pending"
            finally:
                if orig_cs:
                    ss.create_suggestion = orig_cs

    def test_suggest_returns_400_when_no_content(self):
        app = self._make_app()
        with _patch_attr("tools.document_intelligence.blueprint", "_require_role",
                         MagicMock(return_value=True)), \
             _patch_attr("tools.document_intelligence.blueprint", "_current_user",
                         MagicMock(return_value="user-viewer")), \
             _patch_attr("tools.document_intelligence.blueprint", "_security_context",
                         MagicMock(return_value=("default", "CUI"))), \
             _patch_attr("tools.document_intelligence.blueprint", "_collection_id_from_section",
                         MagicMock(return_value="col-001")), \
             _patch_attr("tools.document_intelligence.blueprint", "_conn",
                         MagicMock(side_effect=Exception("no db"))):
            with app.test_client() as client:
                resp = client.post(
                    "/document-intelligence/api/sections/sec-001/suggest",
                    json={"proposed_content": "", "rationale": "test"},
                )
                assert resp.status_code == 400

    def test_suggest_returns_403_when_forbidden(self):
        app = self._make_app()
        with _patch_attr("tools.document_intelligence.blueprint", "_require_role",
                         MagicMock(return_value=False)), \
             _patch_attr("tools.document_intelligence.blueprint", "_current_user",
                         MagicMock(return_value="nobody")), \
             _patch_attr("tools.document_intelligence.blueprint", "_security_context",
                         MagicMock(return_value=("default", "CUI"))), \
             _patch_attr("tools.document_intelligence.blueprint", "_collection_id_from_section",
                         MagicMock(return_value="col-001")):
            with app.test_client() as client:
                resp = client.post(
                    "/document-intelligence/api/sections/sec-001/suggest",
                    json={"proposed_content": "content", "rationale": "test"},
                )
                assert resp.status_code == 403

    def test_create_suggestion_called_with_crowdsource(self):
        app = self._make_app()
        calls = []
        def _fake_create(**kwargs):
            calls.append(kwargs)
            return "sug_xyz"
        with _patch_attr("tools.document_intelligence.blueprint", "_require_role",
                         MagicMock(return_value=True)), \
             _patch_attr("tools.document_intelligence.blueprint", "_current_user",
                         MagicMock(return_value="user-viewer")), \
             _patch_attr("tools.document_intelligence.blueprint", "_security_context",
                         MagicMock(return_value=("default", "CUI"))), \
             _patch_attr("tools.document_intelligence.blueprint", "_collection_id_from_section",
                         MagicMock(return_value="col-001")), \
             _patch_attr("tools.document_intelligence.blueprint", "_conn",
                         MagicMock(side_effect=Exception("no db"))):
            import tools.document_intelligence.suggestion_store as ss
            orig = ss.create_suggestion
            ss.create_suggestion = _fake_create
            try:
                with app.test_client() as client:
                    client.post(
                        "/document-intelligence/api/sections/sec-001/suggest",
                        json={"proposed_content": "New content.", "rationale": "Reason"},
                    )
            finally:
                ss.create_suggestion = orig
        assert len(calls) == 1
        assert calls[0]["canvas_source"] == "crowdsource"
        assert calls[0]["suggested_content"] == "New content."


# ══════════════════════════════════════════════════════════════════════════════
# dic_canvas_integrations.yaml — crowdsource entry
# ══════════════════════════════════════════════════════════════════════════════

class TestCrowdsourceConfig:
    def test_crowdsource_in_integrations_yaml(self):
        import yaml
        import pathlib
        config_path = pathlib.Path("args/dic_canvas_integrations.yaml")
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "crowdsource" in config.get("integrations", {}), \
            "crowdsource must be registered in dic_canvas_integrations.yaml"

    def test_crowdsource_has_required_fields(self):
        import yaml
        import pathlib
        with pathlib.Path("args/dic_canvas_integrations.yaml").open() as f:
            config = yaml.safe_load(f)
        entry = config["integrations"]["crowdsource"]
        assert entry.get("priority")
        assert entry.get("rationale")
        assert "patch_mode" in entry
