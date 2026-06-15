"""Tests for ACE foundation modules: role_loader, problem_classifier, team_assembler, db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from icdev.tools.ace.role_loader import RoleLoader, RoleNotFoundError
from icdev.tools.ace.problem_classifier import ProblemClassifierLens
from icdev.tools.ace.db.init_db import SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _roles_dir():
    return Path(__file__).parent.parent / "args" / "ace" / "roles"


@pytest.fixture()
def role_loader(_roles_dir):
    return RoleLoader(roles_dir=_roles_dir, hot_reload=False)


@pytest.fixture()
def ace_db_path(tmp_path):
    """Temp-file SQLite DB with ACE schema pre-applied."""
    db_path = tmp_path / "ace_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# 1. Role loader — loads YAML files
# ---------------------------------------------------------------------------

def test_role_loader_loads_yaml(role_loader):
    roles = {r.role_id for r in role_loader.list_roles()}
    assert "ai_developer" in roles
    assert "qa_manager" in roles


def test_role_loader_missing_raises(role_loader):
    with pytest.raises(RoleNotFoundError):
        role_loader.get_role("nonexistent_role_xyz")


# ---------------------------------------------------------------------------
# 2. Problem classifier — fallback + domain scoring
# ---------------------------------------------------------------------------

def test_problem_classifier_fallback(role_loader):
    """Short/ambiguous input falls back to default team [ai_developer, qa_manager]."""
    lens = ProblemClassifierLens("hi", role_loader=role_loader)
    manifest = lens.run()
    role_ids = {s.role_id for s in manifest.slots}
    assert "ai_developer" in role_ids
    assert "qa_manager" in role_ids


def test_problem_classifier_build_request(role_loader):
    """'Build a REST API' scores the build domain above zero and includes ai_developer."""
    text = "Build a REST API for managing user accounts."
    lens = ProblemClassifierLens(text, role_loader=role_loader)
    analysis = lens.analyze()
    assert analysis["keyword_scores"].get("build", 0.0) > 0.0

    manifest = lens.run()
    assert any(s.role_id == "ai_developer" for s in manifest.slots)


# ---------------------------------------------------------------------------
# 3. Team assembler — DB persistence
# ---------------------------------------------------------------------------

def test_team_assembler_creates_db_rows(monkeypatch, role_loader, ace_db_path):
    """TeamAssembler.assemble() inserts rows into ace_instances and ace_coworkers."""
    import icdev.tools.db.storage as _storage
    import icdev.tools.ace.db.init_db as _init_mod

    def _fake_conn(env_var=None):
        return sqlite3.connect(str(ace_db_path))

    monkeypatch.setattr(_storage, "get_canvas_connection", _fake_conn)
    monkeypatch.setattr(_init_mod, "init", lambda: None)

    from icdev.tools.ace.team_assembler import TeamAssembler
    from icdev.tools.ace.problem_classifier import TeamManifest, RoleSlot

    manifest = TeamManifest(slots=[
        RoleSlot(role_id="ai_developer", count=1),
        RoleSlot(role_id="qa_manager", count=1),
    ])
    assembler = TeamAssembler(role_loader=role_loader)
    instance = assembler.assemble(
        manifest=manifest,
        instance_id="test-inst-001",
        context={"problem_text": "Build a REST API", "name": "test-instance"},
    )

    conn = sqlite3.connect(str(ace_db_path))
    try:
        inst_rows = conn.execute(
            "SELECT id FROM ace_instances WHERE id = ?", (instance.instance_id,)
        ).fetchall()
        cw_rows = conn.execute(
            "SELECT id FROM ace_coworkers WHERE instance_id = ?", (instance.instance_id,)
        ).fetchall()
    finally:
        conn.close()

    assert len(inst_rows) == 1, "ace_instances row should be created"
    assert len(cw_rows) == 2, "one ace_coworkers row per slot expected"


# ---------------------------------------------------------------------------
# 4. DB schema — all tables created by init_db
# ---------------------------------------------------------------------------

def test_db_tables_exist():
    """init_db SCHEMA creates the 5 core ACE tables."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ace_%' ORDER BY name"
    ).fetchall()
    conn.close()

    table_names = {r[0] for r in rows}
    expected = {
        "ace_instances",
        "ace_coworkers",
        "ace_messages",
        "ace_artifacts",
        "ace_agent_workflows",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"
