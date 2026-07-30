# CUI // SP-CTI
"""The catalogue must correct itself on an already-seeded database.

Three defects in this project traced to one cause: seed_mission_catalog returned
early once the mission count matched the catalogue, so everything after that point
only ever ran on a FRESH database.

  * the step-asset reconcile never ran (fixed once in #1015, then again in #1016
    when it turned out not to commit)
  * mission_type stayed stale on 13 rows
  * and — found by restarting after #1026 and checking the database rather than the
    logs — the ON CONFLICT upsert that keeps title/tagline in step with the
    catalogue never ran either, so 34 derived missions kept the mechanical titles
    they were first written with ('Chromadb Rag', 'Ciso Capstone', 'Advanced Rag')
    even though discovery had been fixed to produce the authored ones.

Removing the fast path removes the class. These tests pin that, plus the retirement
of rows discovery no longer produces.
"""
from __future__ import annotations

import inspect

from _academy_conn import academy_conn

import pytest

from apps.forge_academy import content_loader as cl


def _code_lines(func) -> str:
    """Source with comment lines removed — the comments explain the removed bug."""
    return "\n".join(
        line for line in inspect.getsource(func).splitlines()
        if not line.strip().startswith("#")
    )


def test_the_seeder_has_no_fast_path_return():
    """The single cause behind three separate stale-data defects."""
    code = _code_lines(cl.seed_mission_catalog)
    assert "existing_count" not in code, (
        "the mission-count fast path is back; it skips the upsert that keeps the "
        "catalogue truthful on an already-seeded database"
    )
    assert "already seeded" not in code


def test_the_upsert_still_refreshes_the_visible_fields():
    src = inspect.getsource(cl.seed_mission_catalog)
    assert "ON CONFLICT(slug) DO UPDATE SET" in src
    for field in ("title=excluded.title", "tagline=excluded.tagline"):
        assert field in src, f"{field} must be refreshed for the catalogue to self-correct"


# ---------------------------------------------------------------------------
# retire_superseded_missions
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    c = academy_conn(":memory:")
    c.executescript(
        """
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY, slug TEXT UNIQUE, title TEXT,
          is_active INTEGER DEFAULT 1, source_credit TEXT DEFAULT '');
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY, mission_id INTEGER,
          step_type TEXT);
        INSERT INTO fa_missions (id, slug, title, source_credit)
          VALUES (1, 'still-derived', 'Still', 'derived from authored content');
        INSERT INTO fa_missions (id, slug, title, source_credit)
          VALUES (2, 'gone-derived', 'Gone', 'derived from authored content');
        INSERT INTO fa_missions (id, slug, title, source_credit)
          VALUES (3, 'handwritten', 'Curated', '');
        """
    )
    c.commit()
    return c


def test_retires_a_derived_row_discovery_no_longer_produces(conn, monkeypatch):
    monkeypatch.setattr(cl, "discover_missions", lambda d=None: [{"slug": "still-derived"}])
    assert cl.retire_superseded_missions(conn, discovered={}) == 1
    rows = {r["slug"]: r["is_active"] for r in conn.execute(
        "SELECT slug, is_active FROM fa_missions").fetchall()}
    assert rows["gone-derived"] == 0, "the superseded duplicate must be retired"
    assert rows["still-derived"] == 1


def test_never_retires_a_hand_written_mission(conn, monkeypatch):
    """Removing a curated entry is a content decision, not a start-up inference."""
    monkeypatch.setattr(cl, "discover_missions", lambda d=None: [])
    cl.retire_superseded_missions(conn, discovered={})
    active = conn.execute(
        "SELECT is_active FROM fa_missions WHERE slug='handwritten'").fetchone()[0]
    assert active == 1


def test_retirement_persists_and_is_idempotent(conn, monkeypatch):
    monkeypatch.setattr(cl, "discover_missions", lambda d=None: [{"slug": "still-derived"}])
    assert cl.retire_superseded_missions(conn, discovered={}) == 1
    assert cl.retire_superseded_missions(conn, discovered={}) == 0


def test_retirement_deactivates_rather_than_deletes(conn, monkeypatch):
    """Learner progress and the audit trail must survive."""
    monkeypatch.setattr(cl, "discover_missions", lambda d=None: [])
    cl.retire_superseded_missions(conn, discovered={})
    assert conn.execute("SELECT COUNT(*) FROM fa_missions").fetchone()[0] == 3


def test_survives_a_missing_table(conn, monkeypatch):
    conn.execute("DROP TABLE fa_missions")
    monkeypatch.setattr(cl, "discover_missions", lambda d=None: [])
    assert cl.retire_superseded_missions(conn, discovered={}) == 0  # must not raise


# ---------------------------------------------------------------------------
# Migration 314 — the hand-written duplicate
# ---------------------------------------------------------------------------

def test_migration_314_retires_only_the_stepless_duplicate():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sql = next((root / "tools" / "db" / "migrations").glob("314_*.sql")).read_text(
        encoding="utf-8"
    )
    c = academy_conn(":memory:")
    c.executescript(
        """
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY, slug TEXT, title TEXT,
          is_active INTEGER DEFAULT 1, prereq_slugs_json TEXT DEFAULT '[]');
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY, mission_id INTEGER);
        INSERT INTO fa_missions (id, slug, title) VALUES (1,'m-leader-02-roi','AI ROI Framework');
        INSERT INTO fa_missions (id, slug, title) VALUES (2,'m-leadership-01-ai-roi','AI ROI Framework');
        INSERT INTO fa_mission_steps (mission_id) VALUES (2);
        INSERT INTO fa_missions (id, slug, title, prereq_slugs_json)
          VALUES (3,'m-leader-03-exec-dash','Exec Dash','["m-leader-02-roi"]');
        """
    )
    c.commit()
    c.executescript(sql)

    rows = {r["slug"]: r for r in c.execute(
        "SELECT slug, is_active, prereq_slugs_json FROM fa_missions").fetchall()}
    assert rows["m-leader-02-roi"]["is_active"] == 0, "the stepless duplicate must retire"
    assert rows["m-leadership-01-ai-roi"]["is_active"] == 1, "the real mission must survive"
    assert "m-leader-02-roi" not in rows["m-leader-03-exec-dash"]["prereq_slugs_json"], (
        "a prerequisite pointing at a retired mission can never be satisfied"
    )

    # Idempotent.
    c.executescript(sql)
    assert c.execute(
        "SELECT is_active FROM fa_missions WHERE slug='m-leader-02-roi'").fetchone()[0] == 0


def test_migration_314_spares_a_duplicate_that_has_steps():
    """Only the EMPTY twin is retired; never one a learner could be mid-way through."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sql = next((root / "tools" / "db" / "migrations").glob("314_*.sql")).read_text(
        encoding="utf-8"
    )
    c = academy_conn(":memory:")
    c.executescript(
        """
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY, slug TEXT, title TEXT,
          is_active INTEGER DEFAULT 1, prereq_slugs_json TEXT DEFAULT '[]');
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY, mission_id INTEGER);
        INSERT INTO fa_missions (id, slug, title) VALUES (1,'m-leader-02-roi','AI ROI Framework');
        INSERT INTO fa_mission_steps (mission_id) VALUES (1);
        """
    )
    c.commit()
    c.executescript(sql)
    assert c.execute(
        "SELECT is_active FROM fa_missions WHERE slug='m-leader-02-roi'").fetchone()[0] == 1
