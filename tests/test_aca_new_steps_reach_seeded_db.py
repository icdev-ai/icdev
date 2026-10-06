# CUI // SP-CTI
"""A step authored AFTER its mission was catalogued must reach the database.

seed_mission_catalog seeds a mission's steps only while the mission has none, and on
a fully-catalogued database it returns early before that loop. So a new step added
to an existing mission never got a row. Measured on the live board 2026-10-06:
eight authored steps were unreachable — m01 step 6 (a graded lab), m02 steps 3-5,
m03 steps 2-3 and m04 steps 2-3 — while the PRs that added them reported them live.

Against a throwaway SQLite database built by the academy's own migrate() +
seed_mission_catalog(), so the tables are the ones production creates. The read-back
uses a SECOND connection: an uncommitted insert is visible on the writing
connection and would pass a same-connection check (see test_aca_reconcile_commit).
"""
from __future__ import annotations

import sqlite3

import pytest

SLUG = "m01-llm-fundamentals"
NEW_STEP = 6  # authored on disk (step6_token_budget_lab.md + starter + test)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "academy.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    from apps.forge_academy import content_loader, db
    from tools.db.storage import get_connection

    db.migrate()
    content_loader.seed_mission_catalog()
    # Recreate the live shape: a database catalogued before step 6 was authored.
    conn = get_connection()
    conn.execute(
        "DELETE FROM fa_mission_steps WHERE step_num=? AND mission_id="
        "(SELECT id FROM fa_missions WHERE slug=?)",
        (NEW_STEP, SLUG),
    )
    conn.commit()
    return path


def _steps(path) -> dict:
    other = sqlite3.connect(str(path))
    try:
        return {
            num: (title, step_type, test_path)
            for num, title, step_type, test_path in other.execute(
                "SELECT s.step_num, s.title, s.step_type, s.test_code_path "
                "FROM fa_mission_steps s JOIN fa_missions m ON m.id=s.mission_id "
                "WHERE m.slug=?",
                (SLUG,),
            )
        }
    finally:
        other.close()


def test_the_fixture_reproduces_the_missing_step(seeded_db):
    assert NEW_STEP not in _steps(seeded_db)


def test_a_restart_seeds_the_new_step_and_persists_it(seeded_db):
    from apps.forge_academy import content_loader

    before = _steps(seeded_db)
    content_loader.seed_mission_catalog()  # what a dashboard restart runs
    after = _steps(seeded_db)

    assert NEW_STEP in after, "the authored step never reached the database"
    title, step_type, test_path = after[NEW_STEP]
    assert step_type == "coding" and test_path.endswith("step6_test.py")
    # Existing rows are left exactly as they were.
    assert {n: after[n] for n in before} == before


def test_the_reconcile_is_idempotent(seeded_db):
    from apps.forge_academy import content_loader
    from tools.db.storage import get_connection

    assert content_loader.reconcile_missing_steps(get_connection()) >= 1
    assert content_loader.reconcile_missing_steps(get_connection()) == 0
