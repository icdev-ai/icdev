#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG evaluation dashboard API (D-KARL-9)."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def app():
    """Create Flask test app with rag_eval_api blueprint."""
    from flask import Flask
    from tools.dashboard.api.rag_eval import rag_eval_api

    test_app = Flask(__name__)
    test_app.config["TESTING"] = True
    test_app.register_blueprint(rag_eval_api)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


class TestListCampaigns:
    """GET /api/rag-eval/campaigns tests."""

    @patch("tools.dashboard.api.rag_eval.get_connection")
    def test_list_returns_campaigns(self, mock_conn, client):
        mock_db = MagicMock()
        mock_conn.return_value = mock_db
        mock_db.execute.return_value.fetchall.return_value = []
        mock_db.execute.return_value.fetchone.return_value = [0]

        resp = client.get("/api/rag-eval/campaigns")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "campaigns" in data
        assert "total" in data

    @patch("tools.dashboard.api.rag_eval.get_connection")
    def test_list_with_pagination(self, mock_conn, client):
        mock_db = MagicMock()
        mock_conn.return_value = mock_db
        mock_db.execute.return_value.fetchall.return_value = []
        mock_db.execute.return_value.fetchone.return_value = [0]

        resp = client.get("/api/rag-eval/campaigns?limit=5&offset=10")
        assert resp.status_code == 200


class TestQualityStatus:
    """GET /api/rag-eval/quality tests."""

    @patch("tools.rag.quality_feedback_loop.get_feedback_status")
    def test_quality_returns_status(self, mock_status, client):
        mock_status.return_value = {
            "status": "ok",
            "auto_remediate": True,
            "current_quality": {"status": "ok"},
        }
        resp = client.get("/api/rag-eval/quality")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"


class TestDatasetBalance:
    """GET /api/rag-eval/dataset-balance tests."""

    @patch("tools.dashboard.api.rag_eval.get_connection")
    def test_balance_returns_distribution(self, mock_conn, client):
        mock_db = MagicMock()
        mock_conn.return_value = mock_db

        # First call: taxonomy distribution, second: total count
        mock_db.execute.return_value.fetchall.return_value = []
        mock_db.execute.return_value.fetchone.return_value = [0]

        resp = client.get("/api/rag-eval/dataset-balance")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "distribution" in data
        assert "total_examples" in data
