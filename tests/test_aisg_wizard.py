# CUI // SP-CTI
"""V&V gate: AISG wizard roundtrip + page render.

Four checks:
  1. wizard.process() returns non-empty recommended_goals for each maturity level.
  2. Session persisted to aisg_wizard_sessions after process().
  3. sprint_seeder.seed_for_maturity() inserts correct task count per maturity.
  4. GET /ai-wizard returns HTTP 200.
"""

from __future__ import annotations

import sqlite3
import uuid
from unittest.mock import patch

import pytest

from tools.aisg.wizard import WizardEngine
from tools.aisg.sprint_seeder import seed_for_maturity

# Building the real dashboard app (create_app) imports ~50 blueprints and, on
# import of tools.dashboard.app, runs a module-level ``app = create_app()`` that
# probes local LLM servers for air-gap detection. Cold, that exceeds the
# repo-wide 30s per-test timeout (pyproject ``timeout = 30``). The aisg_app
# fixture pays this cost on first use, so relax the timeout for this file —
# mirrors tests/test_nav_sec_06_mutation_rbac.py.
pytestmark = pytest.mark.timeout(180)

from _dashboard_auth_patch import dashboard_test_app_env  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal schemas
# ---------------------------------------------------------------------------

_WIZARD_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS aisg_wizard_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token       TEXT UNIQUE NOT NULL,
    use_case            TEXT,
    compliance_level    TEXT,
    tech_stack          TEXT,
    ai_maturity         TEXT CHECK (ai_maturity IN ('none', 'pilot', 'scaling')),
    cloud_provider      TEXT,
    generated_args_json TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
"""

_KANBAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    description          TEXT,
    task_type            TEXT DEFAULT 'chore',
    priority             TEXT DEFAULT 'low',
    status               TEXT DEFAULT 'backlog',
    scheduled_at         TEXT,
    completed_at         TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    source_prediction_id TEXT,
    executor_type        TEXT DEFAULT 'claude_cli',
    execution_id         TEXT,
    executor_url         TEXT,
    depends_on_task_id   TEXT,
    failure_count        INTEGER DEFAULT 0,
    last_failure_reason  TEXT,
    last_failure_at      TEXT,
    dispatch_source      TEXT DEFAULT 'unknown',
    completed_via_bypass INTEGER NOT NULL DEFAULT 0
);
"""

_AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE, display_name TEXT,
    role TEXT DEFAULT 'admin', status TEXT DEFAULT 'active',
    created_by TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_api_keys (
    id TEXT PRIMARY KEY, user_id TEXT, key_hash TEXT, key_prefix TEXT,
    label TEXT, status TEXT DEFAULT 'active', last_used_at TIMESTAMP,
    expires_at TIMESTAMP, created_at TIMESTAMP, revoked_at TIMESTAMP, revoked_by TEXT
);
CREATE TABLE IF NOT EXISTS dashboard_auth_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, event_type TEXT,
    ip_address TEXT, user_agent TEXT, details TEXT, created_at TIMESTAMP
);
INSERT OR IGNORE INTO dashboard_users (id, email, display_name, role)
VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin');
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def wizard_db_path(tmp_path):
    """File-based SQLite DB with wizard session table (allows re-open after close)."""
    db_path = tmp_path / "wizard_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_WIZARD_SESSION_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def wizard_engine(wizard_db_path):
    """WizardEngine with get_connection patched to a file-backed SQLite DB."""
    def _get_conn():
        conn = sqlite3.connect(str(wizard_db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _placeholder(_conn):
        return "?"

    with (
        patch("tools.aisg.wizard.get_connection", _get_conn),
        patch("tools.aisg.wizard.sql_placeholder", _placeholder),
    ):
        yield WizardEngine()


@pytest.fixture
def kanban_db():
    """In-memory SQLite DB with kanban_tasks table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_KANBAN_SCHEMA)
    return conn


@pytest.fixture
def aisg_app(tmp_path):
    """Dashboard Flask test app with the AISG blueprint registered.

    The AISG canvas is registry-gated (``ICDEV_AISG_ENABLED``, default off) and
    ``tools.dashboard.app._CANVAS_BLUEPRINTS`` is populated exactly once, at
    module import. Whether ``create_app()`` picks up AISG therefore depends on
    the env flag at that first import — which is fragile across test-collection
    order (a prior test may import the module with the flag unset, after which
    ``/ai-wizard`` 404s). To make the page deterministic we register the
    singleton aisg blueprint directly when create_app did not already register
    it. Flask permits the same blueprint object on distinct app instances; the
    ``not in app.blueprints`` guard prevents a double-registration on one app.

    Note: we deliberately do NOT flip ``ICDEV_AISG_ENABLED`` here. Doing so
    before the first import of ``tools.dashboard.app`` sends AISG down the
    registry-driven registration path, which attaches a ``guard_component_access``
    ``before_request`` hook onto the shared ``bp`` singleton — polluting it for
    every other test that reuses the same blueprint object (e.g.
    ``tests/test_nav_sec_06_mutation_rbac.py``). Direct registration leaves the
    singleton clean.
    """
    db_path = str(tmp_path / "aisg_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUTH_SCHEMA)
    conn.commit()
    conn.close()

    # dashboard_test_app_env() wraps the imports as well as create_app():
    # tools/dashboard/app.py runs a module-level ``app = create_app()``, so the
    # backend guard fires during the import below, not at the explicit call. It
    # also patches get_user_by_id on both auth module spellings — the seeded
    # dashboard_users row above is only reachable while the backend is SQLite, and
    # the global auth hook (nav-sec-01) 401s every request without it. See
    # tests/_dashboard_auth_patch.py.
    with dashboard_test_app_env():
        import tools.dashboard.app as _app_mod
        import tools.dashboard.auth as _auth_mod
        import tools.dashboard.config as _cfg_mod

        with (
            patch.object(_app_mod, "DB_PATH", db_path),
            patch.object(_auth_mod, "DB_PATH", db_path),
            patch.object(_cfg_mod, "DB_PATH", db_path),
        ):
            from tools.dashboard.app import create_app
            app = create_app()
            app.config["TESTING"] = True

            if "aisg" not in app.blueprints:
                from tools.aisg.blueprint import bp as _aisg_bp
                app.register_blueprint(_aisg_bp)

            yield app


# ---------------------------------------------------------------------------
# Check 1 — recommended_goals for each maturity level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("maturity", ["none", "pilot", "scaling"])
def test_process_returns_recommended_goals(wizard_engine, maturity):
    """wizard.process() must return non-empty recommended_goals for every maturity."""
    answers = {
        "use_case": "web_app",
        "compliance_level": "IL2",
        "tech_stack": "python",
        "ai_maturity": maturity,
        "cloud_provider": "local",
    }
    result = wizard_engine.process(session_token=str(uuid.uuid4()), answers=answers)

    assert "recommended_goals" in result
    assert isinstance(result["recommended_goals"], list)
    assert len(result["recommended_goals"]) > 0, (
        f"Expected non-empty recommended_goals for maturity={maturity!r}"
    )
    assert "recommended_skills" in result
    assert len(result["recommended_skills"]) > 0


# ---------------------------------------------------------------------------
# Check 2 — session saved to aisg_wizard_sessions
# ---------------------------------------------------------------------------

def test_session_saved_to_db(wizard_engine, wizard_db_path):
    """wizard.process() must persist session row to aisg_wizard_sessions."""
    token = str(uuid.uuid4())
    answers = {
        "use_case": "compliance",
        "compliance_level": "IL4",
        "tech_stack": "python",
        "ai_maturity": "pilot",
        "cloud_provider": "aws_govcloud",
    }
    wizard_engine.process(session_token=token, answers=answers)

    # Re-open connection after wizard closed it
    conn = sqlite3.connect(str(wizard_db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM aisg_wizard_sessions WHERE session_token = ?", (token,)
    ).fetchone()
    conn.close()

    assert row is not None, "Expected a session row in aisg_wizard_sessions"
    assert row["session_token"] == token
    assert row["use_case"] == "compliance"
    assert row["compliance_level"] == "IL4"
    assert row["ai_maturity"] == "pilot"
    assert row["cloud_provider"] == "aws_govcloud"
    assert row["generated_args_json"] is not None


# ---------------------------------------------------------------------------
# Check 3 — sprint_seeder inserts correct task count per maturity
# ---------------------------------------------------------------------------

# Expected counts using IL2 (no boundary task) and local cloud:
#   none    → Phase1(2) + Phase2(1) + Phase3(2) + Phase4(2) + Phase5(1) = 8
#   pilot   → Phase2(1) + Phase3(2) + Phase4(2) + Phase5(1) + Phase6(1) = 7
#   scaling → Phase2(1) + Phase3(2) + Phase4(2) + Phase5(1) + Phase6(2) = 8

@pytest.mark.parametrize("maturity,expected_count", [
    ("none", 8),
    ("pilot", 7),
    ("scaling", 8),
])
def test_sprint_seeder_task_count(kanban_db, maturity, expected_count):
    """seed_for_maturity() must insert the correct number of tasks per maturity."""
    inserted = seed_for_maturity(
        maturity=maturity,
        compliance_level="IL2",
        use_case="web_app",
        tech_stack="python",
        cloud_provider="local",
        db_conn=kanban_db,
    )

    assert len(inserted) == expected_count, (
        f"maturity={maturity!r}: expected {expected_count} tasks, got {len(inserted)}"
    )

    # Verify rows actually exist in the DB
    rows = kanban_db.execute("SELECT id FROM kanban_tasks").fetchall()
    assert len(rows) == expected_count


def test_sprint_seeder_il4_adds_boundary_task(kanban_db):
    """IL4 compliance adds a boundary task — phase 2 gets 2 tasks."""
    inserted = seed_for_maturity(
        maturity="none",
        compliance_level="IL4",
        db_conn=kanban_db,
    )
    # IL4 adds boundary: Phase1(2)+Phase2(2)+Phase3(2)+Phase4(2)+Phase5(1) = 9
    assert len(inserted) == 9, (
        f"Expected 9 tasks for maturity=none + IL4, got {len(inserted)}"
    )


# ---------------------------------------------------------------------------
# Check 4 — /ai-wizard page renders HTTP 200
# ---------------------------------------------------------------------------

def test_ai_wizard_page_200(aisg_app):
    """GET /ai-wizard must return HTTP 200."""
    client = aisg_app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-admin"

    resp = client.get("/ai-wizard")
    assert resp.status_code == 200, (
        f"Expected 200 from GET /ai-wizard, got {resp.status_code}"
    )
    body = resp.data.decode("utf-8", errors="replace")
    assert "AI Strategy Wizard" in body or "wizard" in body.lower()
