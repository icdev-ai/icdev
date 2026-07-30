# CUI // SP-CTI
"""Already-seeded steps must pick up newly-discovered code assets.

_seed_steps uses INSERT OR IGNORE, so a row written before discover_steps learned
about stepN_starter.py / stepN_test.py (aca-hon-05) would keep its original values
forever. All 212 steps in production were seeded that way — step_type='watch',
empty asset paths — so without a reconcile pass the discovery fix is inert against
any existing database and Tier 1 stays ungradeable.

The pass is deliberately conservative because seeding runs on every dashboard
start: fill empty paths only, promote to 'coding' only when a test is attached,
never demote, never overwrite.
"""
from __future__ import annotations


from _academy_conn import academy_conn

import pytest

from apps.forge_academy.content_loader import _reconcile_step_assets


@pytest.fixture
def conn():
    c = academy_conn(":memory:")
    c.executescript(
        """
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT,
          step_type TEXT DEFAULT 'watch', content_path TEXT,
          starter_code_path TEXT DEFAULT '', test_code_path TEXT DEFAULT '',
          config_schema_json TEXT DEFAULT '{}', xp_partial INTEGER DEFAULT 50,
          skill_tag TEXT, hint_allowed INTEGER DEFAULT 1, estimated_seconds INTEGER);
        """
    )
    return c


def _row(conn, step_num=1):
    return conn.execute(
        "SELECT step_type, starter_code_path, test_code_path FROM fa_mission_steps "
        "WHERE mission_id=1 AND step_num=?",
        (step_num,),
    ).fetchone()


def _seed_row(conn, **over):
    cols = {
        "mission_id": 1, "step_num": 1, "title": "A", "step_type": "watch",
        "content_path": "tier1/m/steps/step1.md", "starter_code_path": "",
        "test_code_path": "",
    }
    cols.update(over)
    conn.execute(
        "INSERT INTO fa_mission_steps "
        "(mission_id, step_num, title, step_type, content_path, "
        " starter_code_path, test_code_path) VALUES (?,?,?,?,?,?,?)",
        tuple(cols[k] for k in (
            "mission_id", "step_num", "title", "step_type", "content_path",
            "starter_code_path", "test_code_path")),
    )
    conn.commit()


DISCOVERED = [{
    "step_num": 1,
    "starter_code_path": "tier1/m/steps/step1_starter.py",
    "test_code_path": "tier1/m/steps/step1_test.py",
}]


def test_existing_watch_step_is_promoted_and_wired(conn):
    """The production case: seeded as watch with no assets."""
    _seed_row(conn)
    _reconcile_step_assets(conn, 1, "m", DISCOVERED)
    row = _row(conn)
    assert row["step_type"] == "coding"
    assert row["test_code_path"] == "tier1/m/steps/step1_test.py"
    assert row["starter_code_path"] == "tier1/m/steps/step1_starter.py"


def test_reconcile_is_idempotent(conn):
    _seed_row(conn)
    _reconcile_step_assets(conn, 1, "m", DISCOVERED)
    first = dict(_row(conn))
    _reconcile_step_assets(conn, 1, "m", DISCOVERED)
    assert dict(_row(conn)) == first


def test_an_existing_asset_path_is_never_overwritten(conn):
    """BUILTIN_STEPS entries stay authoritative."""
    _seed_row(conn, step_type="coding", test_code_path="authored/keepme_test.py")
    _reconcile_step_assets(conn, 1, "m", DISCOVERED)
    row = _row(conn)
    assert row["test_code_path"] == "authored/keepme_test.py"
    # The empty starter is still filled in.
    assert row["starter_code_path"] == "tier1/m/steps/step1_starter.py"


def test_a_starter_alone_does_not_promote_to_coding(conn):
    """A coding step with no test can never be credited (aca-int-02)."""
    _seed_row(conn)
    _reconcile_step_assets(conn, 1, "m", [
        {"step_num": 1, "starter_code_path": "tier1/m/steps/step1_starter.py",
         "test_code_path": ""},
    ])
    row = _row(conn)
    assert row["step_type"] == "watch", "promoted without a test"
    assert row["starter_code_path"] == "tier1/m/steps/step1_starter.py"


def test_an_authored_type_is_never_demoted(conn):
    _seed_row(conn, step_type="design", test_code_path="authored/t.py")
    _reconcile_step_assets(conn, 1, "m", [
        {"step_num": 1, "starter_code_path": "", "test_code_path": ""},
    ])
    assert _row(conn)["step_type"] == "design"


def test_a_missing_row_is_skipped_quietly(conn):
    """Nothing seeded yet — the INSERT path handles that; reconcile must no-op."""
    _reconcile_step_assets(conn, 1, "m", DISCOVERED)
    assert _row(conn) is None


def test_steps_with_no_assets_are_left_alone(conn):
    _seed_row(conn)
    _reconcile_step_assets(conn, 1, "m", [{"step_num": 1}])
    row = _row(conn)
    assert row["step_type"] == "watch"
    assert row["test_code_path"] == ""
