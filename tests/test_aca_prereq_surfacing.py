# CUI // SP-CTI
"""aca-ux-06 — show prerequisites, so a 122-mission catalogue has a visible order.

86 of 122 active missions declare prereq_slugs_json and nothing rendered it. The
browser's whole card vocabulary was Done / Active / Start, so a learner facing 46
role-filtered missions had no way to tell which ones they were ready for, or what to
do first. The data was there and unused; prereq integrity is clean (zero dangling
slugs, after migration 314 repointed the one that would have dangled).

Consistent with aca-ux-04: an unmet prerequisite is SURFACED, not enforced. Tier
gating already decided that locked content stays readable and simply earns nothing,
and a second, stricter blocking rule on the same screen would be incoherent.
"""
from __future__ import annotations


from _academy_conn import academy_conn

import pytest


@pytest.fixture
def fa_conn(monkeypatch):
    from apps.forge_academy import db as fadb

    conn = academy_conn(":memory:")
    conn.executescript(
        """
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
          display_name TEXT, role TEXT DEFAULT 'swe', xp INTEGER DEFAULT 0,
          level TEXT DEFAULT 'recruit', tenant_id TEXT);
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, tier INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
          prereq_slugs_json TEXT DEFAULT '[]');
        CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, mission_id INTEGER, status TEXT DEFAULT 'not_started',
          completed_at TEXT, UNIQUE(user_id, mission_id));
        INSERT INTO fa_users (id, username, display_name) VALUES (1,'l','L');
        INSERT INTO fa_missions (id, slug, title, prereq_slugs_json)
          VALUES (1,'m-first','First','[]');
        INSERT INTO fa_missions (id, slug, title, prereq_slugs_json)
          VALUES (2,'m-second','Second','["m-first"]');
        INSERT INTO fa_missions (id, slug, title, prereq_slugs_json)
          VALUES (3,'m-third','Third','["m-first","m-second"]');
        INSERT INTO fa_missions (id, slug, title, prereq_slugs_json)
          VALUES (4,'m-broken','Broken','["m-does-not-exist"]');
        """
    )
    conn.commit()
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


def _complete(conn, mission_id):
    conn.execute(
        "INSERT OR REPLACE INTO fa_mission_progress "
        "(user_id, mission_id, status, completed_at) VALUES (1,?, 'completed','now')",
        (mission_id,),
    )
    conn.commit()


def _missions(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, slug, title, prereq_slugs_json FROM fa_missions ORDER BY id"
    ).fetchall()]


def test_a_mission_with_no_prereqs_reports_none(fa_conn):
    from apps.forge_academy.db import mission_prereq_state

    state = mission_prereq_state(1, _missions(fa_conn))
    assert state[1] == {"prereqs": [], "unmet": 0, "ready": True}


def test_an_unmet_prereq_is_reported_with_its_title(fa_conn):
    """A slug is not a thing to show a learner."""
    from apps.forge_academy.db import mission_prereq_state

    entry = mission_prereq_state(1, _missions(fa_conn))[2]
    assert entry["ready"] is False
    assert entry["unmet"] == 1
    assert entry["prereqs"] == [
        {"slug": "m-first", "title": "First", "satisfied": False}
    ]


def test_completing_the_prereq_marks_it_satisfied(fa_conn):
    from apps.forge_academy.db import mission_prereq_state

    _complete(fa_conn, 1)
    entry = mission_prereq_state(1, _missions(fa_conn))[2]
    assert entry["ready"] is True
    assert entry["unmet"] == 0
    assert entry["prereqs"][0]["satisfied"] is True


def test_partially_met_prereqs_are_counted(fa_conn):
    from apps.forge_academy.db import mission_prereq_state

    _complete(fa_conn, 1)
    entry = mission_prereq_state(1, _missions(fa_conn))[3]
    assert entry["unmet"] == 1
    assert [p["satisfied"] for p in entry["prereqs"]] == [True, False]


def test_an_unknown_prereq_slug_does_not_crash_the_catalogue(fa_conn):
    """Integrity is clean today; a future typo must degrade, not 500."""
    from apps.forge_academy.db import mission_prereq_state

    entry = mission_prereq_state(1, _missions(fa_conn))[4]
    assert entry["prereqs"][0]["slug"] == "m-does-not-exist"
    assert entry["prereqs"][0]["satisfied"] is False
    assert entry["prereqs"][0]["title"] == "m-does-not-exist", (
        "fall back to the slug when the mission is unknown"
    )


def test_unparseable_prereq_json_is_survivable(fa_conn):
    from apps.forge_academy.db import mission_prereq_state

    fa_conn.execute("UPDATE fa_missions SET prereq_slugs_json='not json' WHERE id=2")
    fa_conn.commit()
    entry = mission_prereq_state(1, _missions(fa_conn))[2]
    assert entry["prereqs"] == []
    assert entry["ready"] is True


def test_no_queries_per_mission(fa_conn):
    """The browser renders up to 122 cards; this must not be N+1."""
    from apps.forge_academy import db as fadb

    calls = {"n": 0}
    real = fa_conn.execute

    class Counting:
        def execute(self, *a, **k):
            calls["n"] += 1
            return real(*a, **k)

        def commit(self):
            fa_conn.commit()

    monkey = Counting()
    fadb.get_connection = lambda: monkey  # noqa: E731
    try:
        fadb.mission_prereq_state(1, _missions(fa_conn))
    finally:
        fadb.get_connection = lambda: fa_conn  # noqa: E731
    assert calls["n"] <= 3, f"expected a constant number of queries, made {calls['n']}"


# ---------------------------------------------------------------------------
# The browser uses it
# ---------------------------------------------------------------------------

def test_browser_route_passes_prereq_state():
    import inspect

    from apps.forge_academy import blueprint

    assert "mission_prereq_state" in inspect.getsource(blueprint.missions_browser)


def test_the_card_renders_prerequisites():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "tools" / "dashboard" / "templates" / "forge_academy"
            / "missions.html").read_text(encoding="utf-8")
    assert "prereq" in html.lower(), "86 missions declare prereqs and none were shown"


def test_an_unmet_prereq_does_not_block_the_card_link():
    """aca-ux-04 chose locked-but-readable; prerequisites follow the same rule."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    html = (root / "tools" / "dashboard" / "templates" / "forge_academy"
            / "missions.html").read_text(encoding="utf-8")
    assert "window.location='/academy/mission/{{ m.slug }}'" in html, (
        "the card must still navigate; prerequisites are advisory, not a gate"
    )
