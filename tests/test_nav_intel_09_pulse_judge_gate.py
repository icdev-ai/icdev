# CUI // SP-CTI
"""nav-intel-09-d1 — LLM judge verdict gates the Pulse publish endpoint.

POST /api/pulse/posts/<id>/publish must consult the latest judge verdict
(pulse_posts.judge_color, written only when a judge run completes):

- RED verdict            -> 409 with explicit "blocked" status
- judge never ran/errored -> 409 fail-closed with "run judge first" message
- GREEN / YELLOW (and the higher BLUE / PURPLE FAR ratings, plus the AMBER
  alias) -> publish proceeds (200)

The blueprint is mounted on a bare Flask app; ``require_role`` is satisfied by
seeding ``g.current_user`` in a before_request hook, and the pulse DB / exporter
/ WordPress layers are patched out so only the gate logic is exercised.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch

import pytest
from flask import Flask, g

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dashboard.api.pulse import pulse_api  # noqa: E402


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(pulse_api)

    @app.before_request
    def _fake_user():
        g.current_user = {"id": "u-test", "role": "admin"}

    with app.test_client() as c:
        yield c


def _publish(client, judge_color):
    post = {
        "id": "p1",
        "title": "Test Post",
        "slug": "test-post",
        "status": "approved",
        "judge_color": judge_color,
    }
    with (
        patch("tools.pulse.db.get_row", return_value=post),
        patch("tools.pulse.db.update_row") as mock_update,
        patch("tools.pulse.engine.exporter.export_both", return_value={"mdx": "x", "html": "y"}),
    ):
        resp = client.post("/api/pulse/posts/p1/publish", json={"auto_push": False})
    return resp, mock_update


def test_red_verdict_blocks_publish_with_409(client):
    resp, mock_update = _publish(client, "red")
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["status"] == "blocked"
    assert body["judge_color"] == "red"
    mock_update.assert_not_called()


def test_green_verdict_allows_publish(client):
    resp, mock_update = _publish(client, "green")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "published"
    mock_update.assert_called_once()
    assert mock_update.call_args.args[2]["status"] == "published"


@pytest.mark.parametrize("color", ["yellow", "amber", "blue", "purple", "GREEN"])
def test_non_red_verdicts_allow_publish(client, color):
    resp, _ = _publish(client, color)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "published"


@pytest.mark.parametrize("color", [None, "", "  "])
def test_missing_judge_verdict_fails_closed(client, color):
    resp, mock_update = _publish(client, color)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["status"] == "blocked"
    assert "run judge first" in body["error"]
    mock_update.assert_not_called()


def test_unknown_verdict_fails_closed(client):
    resp, mock_update = _publish(client, "chartreuse")
    assert resp.status_code == 409
    assert "run judge first" in resp.get_json()["error"]
    mock_update.assert_not_called()


def test_not_found_still_404(client):
    with patch("tools.pulse.db.get_row", return_value=None):
        resp = client.post("/api/pulse/posts/nope/publish", json={"auto_push": False})
    assert resp.status_code == 404
