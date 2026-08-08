# CUI // SP-CTI
"""Network migration sessions had no way to be closed.

`mc_net_sessions.status` is plain TEXT defaulting to `in_progress`, and the
only writer was a generic PATCH endpoint that no UI ever called with a status
and that validated nothing. So every session ever created stayed `in_progress`
forever and the NMCE reflex flagged it as stale week after week — 15 open
sessions on the live board, plus 46 that someone had closed out by editing the
database directly, because the product offered no other way.

These cover the two halves that fix it: the endpoint enforces the status
vocabulary, and the wizard actually wires a close control to it.
"""

import re
from pathlib import Path

import pytest

from tools.migration_canvas.constants import (
    NET_SESSION_STATUSES,
    NET_SESSION_TERMINAL_STATUSES,
)

_REPO = Path(__file__).resolve().parent.parent
_BLUEPRINT = _REPO / "tools" / "migration_canvas" / "blueprint.py"
_WIZARD = _REPO / "tools" / "dashboard" / "templates" / "migration_canvas" / "network_wizard.html"
_DDL = _REPO / "tools" / "migration_canvas" / "db" / "init_db.py"


# ── Vocabulary coherence ────────────────────────────────────────────────────

def test_terminal_statuses_match_active_session_queries():
    """`status NOT IN (...)` filters must agree with the terminal set.

    A status that is terminal in Python but missing from these SQL filters
    leaves a closed session counted as active work forever — which is the
    failure this whole change exists to stop.
    """
    sources = [
        _BLUEPRINT.read_text(encoding="utf-8"),
        (_REPO / "tools" / "migration_canvas" / "network_migration.py").read_text(encoding="utf-8"),
    ]
    found = False
    for src in sources:
        for match in re.finditer(r"status NOT IN \(([^)]*)\)", src):
            found = True
            in_sql = {v.strip().strip("'\"") for v in match.group(1).split(",")}
            assert in_sql == set(NET_SESSION_TERMINAL_STATUSES), (
                f"active-session filter excludes {sorted(in_sql)} but "
                f"NET_SESSION_TERMINAL_STATUSES is {sorted(NET_SESSION_TERMINAL_STATUSES)}"
            )
    assert found, "no active-session filter found — did the query shape change?"


def test_ddl_default_status_is_a_known_status():
    ddl = _DDL.read_text(encoding="utf-8")
    # Scope to the mc_net_sessions block — several tables in this file declare
    # a `status` column and they do not share a vocabulary.
    block = re.search(
        r"CREATE TABLE IF NOT EXISTS mc_net_sessions \((.*?)\n\);", ddl, re.DOTALL
    )
    assert block, "mc_net_sessions DDL not found"
    match = re.search(r"status\s+TEXT DEFAULT '([^']+)'", block.group(1))
    assert match, "mc_net_sessions.status default not found in DDL"
    default = match.group(1)
    assert default in NET_SESSION_STATUSES
    assert not NET_SESSION_STATUSES[default]["terminal"], (
        "sessions must not be born in a terminal state"
    )


def test_terminal_set_is_non_empty_and_derived():
    assert NET_SESSION_TERMINAL_STATUSES, "no way to close a session"
    assert set(NET_SESSION_TERMINAL_STATUSES) <= set(NET_SESSION_STATUSES)


# ── Endpoint enforcement ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client(monkeypatch_module=None):
    """Flask test client with the migration canvas blueprint registered."""
    import os

    from flask import Flask

    os.environ["ICDEV_AUTH_BYPASS"] = "1"
    from tools.migration_canvas.blueprint import create_migration_blueprint

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"  # nosec B105 — test-only Flask session key
    app.register_blueprint(create_migration_blueprint(), url_prefix="/migration-canvas")
    return app.test_client()


@pytest.mark.parametrize("bad", ["done", "closed", "IN_PROGRESS", "", "deleted"])
def test_patch_rejects_unknown_status(client, bad):
    """An unvalidated status strands the session outside every status query."""
    resp = client.patch(
        "/migration-canvas/api/network-migration/nmig-test-vocab",
        json={"status": bad},
    )
    assert resp.status_code == 400, f"status {bad!r} was accepted"
    body = resp.get_json()
    assert "allowed" in body
    assert set(body["allowed"]) == set(NET_SESSION_STATUSES)


@pytest.mark.parametrize("good", sorted(NET_SESSION_STATUSES))
def test_patch_accepts_every_declared_status(client, good):
    """Every status in the vocabulary must be reachable through the API."""
    resp = client.patch(
        "/migration-canvas/api/network-migration/nmig-test-vocab",
        json={"status": good},
    )
    assert resp.status_code == 200, f"declared status {good!r} was rejected"


# ── UI wiring ───────────────────────────────────────────────────────────────

def test_wizard_exposes_a_close_control():
    """The API half alone is what left 46 sessions closed by hand."""
    html = _WIZARD.read_text(encoding="utf-8")
    assert "setSessionStatus" in html, "wizard has no close control"
    for status in NET_SESSION_TERMINAL_STATUSES:
        assert f"setSessionStatus('{status}')" in html, (
            f"wizard cannot close a session as '{status}'"
        )
    # Reopening matters too — otherwise a misclick is unrecoverable in the UI.
    assert "setSessionStatus('in_progress')" in html


def test_wizard_does_not_hardcode_the_vocabulary():
    """The status list is server-rendered so it cannot drift from constants.py."""
    html = _WIZARD.read_text(encoding="utf-8")
    assert "net_session_statuses | tojson" in html
