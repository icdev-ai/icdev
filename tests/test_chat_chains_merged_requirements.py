"""Unit test: POST /api/chat/chains returns merged_requirements with correct count."""
import pytest
from flask import Flask
from unittest.mock import patch


@pytest.fixture
def chat_app(icdev_db, monkeypatch):
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    app = Flask(__name__)
    app.config["TESTING"] = True

    from tools.dashboard.api.chat import chat_api
    app.register_blueprint(chat_api)

    return app


@pytest.fixture
def chat_client(chat_app):
    return chat_app.test_client()


def test_create_chain_merged_requirements_count(chat_client):
    """POST /api/chat/chains with two non-overlapping UCs: 201, ok, chain_id, correct count."""
    ato_reqs = [
        {"type": "compliance", "priority": "high", "text": f"ATO requirement item {i}"}
        for i in range(5)
    ]
    cdrl_reqs = [
        {"type": "functional", "priority": "high", "text": f"CDRL requirement item {i}"}
        for i in range(4)
    ]
    mock_use_cases = [
        {"id": "ato_package_builder", "label": "ATO Package Builder", "template_requirements": ato_reqs},
        {"id": "cdrl_generator", "label": "CDRL Generator", "template_requirements": cdrl_reqs},
    ]

    with patch("tools.dashboard.api.chat._uc_load_all", return_value=mock_use_cases):
        resp = chat_client.post(
            "/api/chat/chains",
            json={
                "name": "ATO + CDRL Chain",
                "use_case_ids": ["ato_package_builder", "cdrl_generator"],
            },
        )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["ok"] is True
    assert isinstance(body["chain_id"], str) and body["chain_id"]
    assert body["requirement_count"] == len(ato_reqs) + len(cdrl_reqs)
