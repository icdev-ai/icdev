# CUI // SP-CTI
"""The asset reconcile must run on databases that are already seeded.

aca-hon-05 added _reconcile_step_assets to attach newly-discovered
stepN_starter.py / stepN_test.py to step rows that INSERT OR IGNORE had already
written. It was called from _seed_steps — but seed_mission_catalog has a fast path
that returns as soon as `existing_count >= len(catalog)`, and _seed_steps is only
reached AFTER that return. So on exactly the databases the reconcile exists to
repair, it never ran.

Verified against the live instance after a dashboard restart: step_type
distribution unchanged (watch 118 / coding 16), steps with a stored test still 10,
and every Tier-1 mission still graded=0. The fix is inert-in-practice unless the
reconcile runs before the fast path.
"""
from __future__ import annotations

import inspect

from _academy_conn import academy_conn

import pytest

from apps.forge_academy import content_loader


def test_reconcile_runs_before_the_fast_path_return():
    """Source-level guard: the call must precede the already-seeded early return."""
    src = inspect.getsource(content_loader.seed_mission_catalog)
    assert "reconcile_all_step_assets" in src, "reconcile is not called from the seeder"
    call_at = src.index("reconcile_all_step_assets(")
    skip_at = src.index("already seeded")
    assert call_at < skip_at, (
        "the reconcile is placed after the already-seeded fast-path return, so it "
        "cannot run on an already-seeded database — which is its only purpose"
    )


def test_reconcile_all_step_assets_exists():
    assert callable(getattr(content_loader, "reconcile_all_step_assets", None))


@pytest.fixture
def conn():
    c = academy_conn(":memory:")
    c.executescript(
        """
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, is_active INTEGER DEFAULT 1);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT,
          step_type TEXT DEFAULT 'watch', content_path TEXT,
          starter_code_path TEXT DEFAULT '', test_code_path TEXT DEFAULT '');
        INSERT INTO fa_missions (id, slug, title) VALUES (1, 'm01-x', 'X');
        INSERT INTO fa_mission_steps (mission_id, step_num, title) VALUES (1, 1, 'A');
        """
    )
    return c


def test_reconcile_all_maps_slugs_to_mission_ids(conn):
    """It must resolve the discovered slug to the right mission row."""
    content_loader.reconcile_all_step_assets(conn, {
        "m01-x": [{
            "step_num": 1,
            "starter_code_path": "tier1/m01-x/steps/step1_starter.py",
            "test_code_path": "tier1/m01-x/steps/step1_test.py",
        }],
    })
    row = conn.execute(
        "SELECT step_type, test_code_path FROM fa_mission_steps WHERE mission_id=1"
    ).fetchone()
    assert row["step_type"] == "coding"
    assert row["test_code_path"] == "tier1/m01-x/steps/step1_test.py"


def test_an_unknown_slug_is_ignored(conn):
    content_loader.reconcile_all_step_assets(conn, {
        "no-such-mission": [{"step_num": 1, "test_code_path": "x_test.py"}],
    })
    row = conn.execute("SELECT step_type FROM fa_mission_steps WHERE mission_id=1").fetchone()
    assert row["step_type"] == "watch"


def test_reconcile_all_is_idempotent(conn):
    discovered = {
        "m01-x": [{
            "step_num": 1,
            "starter_code_path": "tier1/m01-x/steps/step1_starter.py",
            "test_code_path": "tier1/m01-x/steps/step1_test.py",
        }],
    }
    content_loader.reconcile_all_step_assets(conn, discovered)
    first = dict(conn.execute(
        "SELECT step_type, starter_code_path, test_code_path FROM fa_mission_steps "
        "WHERE mission_id=1"
    ).fetchone())
    content_loader.reconcile_all_step_assets(conn, discovered)
    assert dict(conn.execute(
        "SELECT step_type, starter_code_path, test_code_path FROM fa_mission_steps "
        "WHERE mission_id=1"
    ).fetchone()) == first


def test_reconcile_all_survives_a_missing_table(conn):
    """Seeding runs at startup on a cold DB; this must never break boot."""
    conn.execute("DROP TABLE fa_mission_steps")
    content_loader.reconcile_all_step_assets(conn, {
        "m01-x": [{"step_num": 1, "test_code_path": "x_test.py"}],
    })  # must not raise
