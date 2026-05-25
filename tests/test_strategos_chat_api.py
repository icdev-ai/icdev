#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit + integration tests for the Strategos chat API.

Covers:
  - POST /api/strategos/chat/init          → {context_id, status}
  - POST /api/strategos/chat/<id>/inject   → {status: "ok"}
  - POST /api/strategos/chat/<id>/send     → {status: "queued"}
  - GET  /api/strategos/chat/poll/<id>     → context state dict
  - 503 when chat_manager unavailable
  - inject_entity_context entity formatting (vessel, kg_node, orbat_unit)
  - STRATEGOS_SYSTEM_PROMPT content contract
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask

from apps.strategos.blueprint import create_strategos_api_blueprint
from tools.strategos.strategos_chat import (
    STRATEGOS_SYSTEM_PROMPT,
    inject_entity_context,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_CTX = {
    "context_id": "ctx-abc123",
    "status": "active",
    "title": "STRATEGOS Intelligence Chat",
    "is_processing": False,
    "dirty_version": 0,
    "turn_number": 0,
}


def _make_app() -> Flask:
    """Minimal Flask app with the Strategos API blueprint registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = create_strategos_api_blueprint()
    app.register_blueprint(bp, url_prefix="/api/strategos")
    return app


@pytest.fixture()
def mock_mgr():
    mgr = MagicMock()
    mgr.create_context.return_value = FAKE_CTX.copy()
    mgr.send_message.return_value = {"context_id": "ctx-abc123", "turn_number": 1, "queued": True}
    mgr.get_context.return_value = FAKE_CTX.copy()
    mgr.get_messages.return_value = []
    return mgr


@pytest.fixture()
def client(mock_mgr):
    app = _make_app()
    with patch("apps.strategos.blueprint._require_chat_manager", return_value=mock_mgr):
        with app.test_client() as c:
            yield c


@pytest.fixture()
def client_no_mgr():
    """Client where chat_manager is unavailable (simulates ImportError path)."""
    app = _make_app()
    with patch("apps.strategos.blueprint._require_chat_manager", return_value=None):
        with app.test_client() as c:
            yield c


# ---------------------------------------------------------------------------
# POST /api/strategos/chat/init
# ---------------------------------------------------------------------------

class TestChatInit:
    def test_returns_context_id_and_status(self, client, mock_mgr):
        with patch(
            "tools.strategos.strategos_chat.create_strategos_context",
            return_value=FAKE_CTX.copy(),
        ):
            resp = client.post(
                "/api/strategos/chat/init",
                json={"user_id": "user-1", "page": "orbat"},
                content_type="application/json",
            )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "context_id" in data
        assert "status" in data
        assert data["context_id"] == "ctx-abc123"
        assert data["status"] == "active"

    def test_classification_header_present(self, client):
        with patch(
            "tools.strategos.strategos_chat.create_strategos_context",
            return_value=FAKE_CTX.copy(),
        ):
            resp = client.post("/api/strategos/chat/init", json={})
        assert resp.headers.get("X-Classification") == "CUI"

    def test_defaults_user_id_to_local(self, client, mock_mgr):
        with patch(
            "tools.strategos.strategos_chat.create_strategos_context",
            return_value=FAKE_CTX.copy(),
        ) as mock_create:
            client.post("/api/strategos/chat/init", json={})
        mock_create.assert_called_once_with(user_id="local", page="")

    def test_503_when_manager_unavailable(self, client_no_mgr):
        resp = client_no_mgr.post("/api/strategos/chat/init", json={})
        assert resp.status_code == 503
        data = resp.get_json()
        assert "error" in data

    def test_500_when_create_fails(self, client):
        with patch(
            "tools.strategos.strategos_chat.create_strategos_context",
            return_value={"error": "DB unavailable"},
        ):
            resp = client.post("/api/strategos/chat/init", json={})
        assert resp.status_code == 500
        assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# POST /api/strategos/chat/<id>/inject
# ---------------------------------------------------------------------------

class TestChatInject:
    def test_returns_status_ok(self, client):
        with patch(
            "tools.strategos.strategos_chat.inject_entity_context",
            return_value={"context_id": "ctx-abc123", "turn_number": 1},
        ):
            resp = client.post(
                "/api/strategos/chat/ctx-abc123/inject",
                json={"entity": {"type": "vessel", "label": "MV Phantom"}},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "turn_number" in data

    def test_classification_header_present(self, client):
        with patch(
            "tools.strategos.strategos_chat.inject_entity_context",
            return_value={"context_id": "ctx-abc123", "turn_number": 2},
        ):
            resp = client.post(
                "/api/strategos/chat/ctx-abc123/inject",
                json={"entity": {"type": "kg_node", "label": "Node-1"}},
            )
        assert resp.headers.get("X-Classification") == "CUI"

    def test_400_when_entity_missing(self, client):
        resp = client.post(
            "/api/strategos/chat/ctx-abc123/inject",
            json={},
        )
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_404_when_context_not_found(self, client):
        with patch(
            "tools.strategos.strategos_chat.inject_entity_context",
            return_value={"error": "context not found"},
        ):
            resp = client.post(
                "/api/strategos/chat/missing-ctx/inject",
                json={"entity": {"type": "vessel"}},
            )
        assert resp.status_code == 404

    def test_503_when_manager_unavailable(self, client_no_mgr):
        resp = client_no_mgr.post(
            "/api/strategos/chat/ctx-abc123/inject",
            json={"entity": {"type": "vessel"}},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/strategos/chat/<id>/send
# ---------------------------------------------------------------------------

class TestChatSend:
    def test_returns_status_queued(self, client, mock_mgr):
        mock_mgr.send_message.return_value = {
            "context_id": "ctx-abc123",
            "turn_number": 2,
            "queued": True,
        }
        resp = client.post(
            "/api/strategos/chat/ctx-abc123/send",
            json={"content": "Assess vessel threat"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "queued"
        assert "turn_number" in data

    def test_classification_header_present(self, client, mock_mgr):
        resp = client.post(
            "/api/strategos/chat/ctx-abc123/send",
            json={"content": "What is the threat?"},
        )
        assert resp.headers.get("X-Classification") == "CUI"

    def test_400_when_content_empty(self, client):
        resp = client.post(
            "/api/strategos/chat/ctx-abc123/send",
            json={"content": "   "},
        )
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_400_when_content_missing(self, client):
        resp = client.post(
            "/api/strategos/chat/ctx-abc123/send",
            json={},
        )
        assert resp.status_code == 400

    def test_404_when_context_not_found(self, client, mock_mgr):
        mock_mgr.send_message.return_value = {"error": "context not found"}
        resp = client.post(
            "/api/strategos/chat/missing-ctx/send",
            json={"content": "Hello"},
        )
        assert resp.status_code == 404

    def test_503_when_manager_unavailable(self, client_no_mgr):
        resp = client_no_mgr.post(
            "/api/strategos/chat/ctx-abc123/send",
            json={"content": "Hello"},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/strategos/chat/poll/<id>
# ---------------------------------------------------------------------------

class TestChatPoll:
    def test_returns_context_state_dict(self, client, mock_mgr):
        mock_mgr.get_context.return_value = FAKE_CTX.copy()
        mock_mgr.get_messages.return_value = [
            {"turn_number": 1, "role": "user", "content": "Hello"}
        ]
        resp = client.get("/api/strategos/chat/poll/ctx-abc123")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["context_id"] == "ctx-abc123"
        assert data["status"] == "active"
        assert "is_processing" in data
        assert "dirty_version" in data
        assert "turn_number" in data
        assert "messages" in data
        assert isinstance(data["messages"], list)

    def test_classification_header_present(self, client, mock_mgr):
        resp = client.get("/api/strategos/chat/poll/ctx-abc123")
        assert resp.headers.get("X-Classification") == "CUI"

    def test_404_when_context_not_found(self, client, mock_mgr):
        mock_mgr.get_context.return_value = None
        resp = client.get("/api/strategos/chat/poll/missing-ctx")
        assert resp.status_code == 404
        assert "error" in resp.get_json()

    def test_503_when_manager_unavailable(self, client_no_mgr):
        resp = client_no_mgr.get("/api/strategos/chat/poll/ctx-abc123")
        assert resp.status_code == 503

    def test_messages_fetched_from_turn_zero(self, client, mock_mgr):
        client.get("/api/strategos/chat/poll/ctx-abc123")
        mock_mgr.get_messages.assert_called_once_with("ctx-abc123", since_turn=0, limit=100)


# ---------------------------------------------------------------------------
# inject_entity_context — unit tests (domain layer, no Flask)
# ---------------------------------------------------------------------------

class TestInjectEntityContext:
    def _mock_mgr(self, turn=1):
        mgr = MagicMock()
        mgr.send_message.return_value = {
            "context_id": "ctx-1",
            "turn_number": turn,
            "queued": True,
        }
        return mgr

    def _patch_mgr(self, mock_mgr):
        """Patch the chat_manager singleton at the source (lazy-imported in inject_entity_context)."""
        return patch("tools.dashboard.chat_manager.chat_manager", mock_mgr)

    def test_vessel_format_contains_vessel_context(self):
        mock_mgr = self._mock_mgr()
        with self._patch_mgr(mock_mgr):
            result = inject_entity_context(
                "ctx-1",
                {
                    "type": "vessel",
                    "label": "MV Phantom",
                    "mmsi": "123456789",
                    "flag": "Panama",
                    "lat": 12.34,
                    "lon": 56.78,
                    "speed_kts": 14.5,
                    "cargo_type": "General",
                    "track_points": 42,
                    "anomaly_flag": True,
                },
            )
        call_text = mock_mgr.send_message.call_args[0][1]
        assert "VESSEL CONTEXT" in call_text
        assert "MV Phantom" in call_text
        assert "123456789" in call_text
        assert result.get("turn_number") == 1

    def test_kg_node_format_contains_kg_node_header(self):
        mock_mgr = self._mock_mgr()
        with self._patch_mgr(mock_mgr):
            result = inject_entity_context(
                "ctx-1",
                {
                    "type": "kg_node",
                    "label": "Entity Alpha",
                    "node_type": "Actor",
                    "id": "node-001",
                    "neighbors": 5,
                },
            )
        call_text = mock_mgr.send_message.call_args[0][1]
        assert "KNOWLEDGE GRAPH NODE" in call_text
        assert "Entity Alpha" in call_text
        assert result.get("turn_number") == 1

    def test_orbat_unit_format_contains_orbat_header(self):
        mock_mgr = self._mock_mgr()
        with self._patch_mgr(mock_mgr):
            result = inject_entity_context(
                "ctx-1",
                {
                    "type": "orbat_unit",
                    "label": "3rd Infantry Brigade",
                    "side": "Blue",
                    "strength": "900",
                    "unit_id": "unit-007",
                },
            )
        call_text = mock_mgr.send_message.call_args[0][1]
        assert "ORBAT UNIT" in call_text
        assert "3rd Infantry Brigade" in call_text
        assert "Blue" in call_text
        assert result.get("turn_number") == 1

    def test_unknown_type_falls_back_to_generic_header(self):
        mock_mgr = self._mock_mgr()
        with self._patch_mgr(mock_mgr):
            inject_entity_context(
                "ctx-1",
                {"type": "unknown_entity", "label": "Thing X"},
            )
        call_text = mock_mgr.send_message.call_args[0][1]
        assert "ENTITY CONTEXT" in call_text

    def test_message_sent_as_user_role(self):
        mock_mgr = self._mock_mgr()
        with self._patch_mgr(mock_mgr):
            inject_entity_context("ctx-1", {"type": "vessel", "label": "Ship"})
        call = mock_mgr.send_message.call_args
        positional_args = call[0]
        keyword_args = call[1]
        assert positional_args[0] == "ctx-1"
        role = positional_args[2] if len(positional_args) > 2 else keyword_args.get("role")
        assert role == "user"

    def test_returns_error_dict_on_exception(self):
        mock_mgr = MagicMock()
        mock_mgr.send_message.side_effect = RuntimeError("DB gone")
        with self._patch_mgr(mock_mgr):
            result = inject_entity_context("ctx-1", {"type": "vessel"})
        assert "error" in result


# ---------------------------------------------------------------------------
# STRATEGOS_SYSTEM_PROMPT contract
# ---------------------------------------------------------------------------

class TestStrategosSystemPrompt:
    def test_is_non_empty_string(self):
        assert isinstance(STRATEGOS_SYSTEM_PROMPT, str)
        assert len(STRATEGOS_SYSTEM_PROMPT.strip()) > 0

    def test_contains_bluf(self):
        assert "BLUF" in STRATEGOS_SYSTEM_PROMPT

    def test_contains_strategos_identity(self):
        assert "STRATEGOS" in STRATEGOS_SYSTEM_PROMPT

    def test_contains_cui_classification(self):
        assert "CUI" in STRATEGOS_SYSTEM_PROMPT
