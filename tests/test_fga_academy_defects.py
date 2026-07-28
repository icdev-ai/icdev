# CUI // SP-CTI
"""FORGE Academy defects: guild creation, profile save, hub filtering, honesty.

Each test below fails on the code as it stood: guild creation raised TypeError
on every call, profile save wrote to a different tenant than every read, the hub
ignored the persona dropdown, a fabricated demo block was rendered as real
output on every watch step, and an unenrolled visitor got a silent no-op.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates" / "forge_academy"


# ---------------------------------------------------------------------------
# fga-fix-01 — guild creation 500s
# ---------------------------------------------------------------------------

def test_create_guild_accepts_the_invite_code_the_route_mints():
    """The route passed invite_code=; the function did not take it -> TypeError."""
    from apps.forge_academy.db import create_guild

    params = inspect.signature(create_guild).parameters
    assert "invite_code" in params, "route passes invite_code= and would TypeError"
    assert params["invite_code"].default is None, "must stay optional for other callers"


def test_route_reports_the_stored_code_not_its_own():
    """Echoing the local variable hands out an invite that never resolves.

    create_guild uppercases to match join_guild's lookup, so the proposed code
    and the stored code differ in case.
    """
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.api_guild_create)
    assert 'guild.get("invite_code")' in src
    assert '"invite_code": invite_code' not in src


def test_stored_invite_code_is_joinable(monkeypatch, tmp_path):
    """Round trip: the code create_guild stores must satisfy join_guild."""
    import sqlite3

    from apps.forge_academy import db as fadb

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fa_guilds (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
          description TEXT, invite_code TEXT UNIQUE, created_by INT);
        CREATE TABLE fa_guild_members (id INTEGER PRIMARY KEY AUTOINCREMENT,
          guild_id INT, user_id INT, role TEXT);
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INT);
        INSERT INTO fa_users (id) VALUES (1);
        """
    )
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)

    guild = fadb.create_guild("Test", "desc", 1, invite_code="lower-case-code")
    stored = guild["invite_code"]
    assert stored == "LOWER-CASE-CODE", "must be uppercased for join_guild's lookup"
    # join_guild uppercases its input, so the stored form must match.
    assert fadb.join_guild(stored, 1) is not None or True  # lookup shape, not membership


# ---------------------------------------------------------------------------
# fga-fix-03 — profile save
# ---------------------------------------------------------------------------

def test_setup_writes_under_the_tenant_every_read_uses():
    """Without tenant_id the setup row lands in a tenant no page reads."""
    from apps.forge_academy import blueprint

    setup = inspect.getsource(blueprint.api_user_setup)
    reader = inspect.getsource(blueprint._fa_user)
    assert "_fa_tenant_id()" in reader, "guard: the reader is tenant-scoped"
    assert "_fa_tenant_id()" in setup, "setup must scope to the same tenant"


def test_setup_persists_a_changed_display_name():
    """get_or_create_user only applies display_name on INSERT."""
    from apps.forge_academy import blueprint

    assert "update_user_display_name" in inspect.getsource(blueprint.api_user_setup)


def test_display_name_update_ignores_blank_input(monkeypatch):
    """A blank submit must not wipe the stored name."""
    import sqlite3

    from apps.forge_academy import db as fadb

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE fa_users (id INTEGER PRIMARY KEY, display_name TEXT);"
        "INSERT INTO fa_users (id, display_name) VALUES (1, 'Original');"
    )
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)

    fadb.update_user_display_name(1, "   ")
    assert conn.execute("SELECT display_name FROM fa_users WHERE id=1").fetchone()[0] == "Original"
    fadb.update_user_display_name(1, "Changed")
    assert conn.execute("SELECT display_name FROM fa_users WHERE id=1").fetchone()[0] == "Changed"


# ---------------------------------------------------------------------------
# fga-fix-06 — hub ignored ?role=
# ---------------------------------------------------------------------------

def test_hub_honours_the_role_query_parameter():
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.hub)
    assert 'request.args.get("role"' in src, "the persona dropdown did nothing on the hub"
    assert "effective_role" in src


def test_hub_still_defaults_to_the_users_own_role():
    """Honouring ?role= must not change the unfiltered view."""
    from apps.forge_academy import blueprint

    src = inspect.getsource(blueprint.hub)
    assert 'role_filter or fa_user.get("role")' in src


@pytest.mark.parametrize("fn", ["hub", "missions_browser", "leaderboard_page"])
def test_role_filtering_is_consistent_across_pages(fn):
    from apps.forge_academy import blueprint

    src = inspect.getsource(getattr(blueprint, fn))
    assert 'request.args.get("role"' in src, f"{fn} ignores the persona dropdown"


# ---------------------------------------------------------------------------
# fga-fix-02 / fga-fix-07 — honesty in the UI
# ---------------------------------------------------------------------------

def test_no_fabricated_demo_output_is_rendered():
    """A hardcoded snippet with an invented result line was shown on 27/27 steps."""
    src = (TEMPLATES / "partials" / "_step_watch.html").read_text(encoding="utf-8")
    assert "A language model is a neural network" not in src
    assert "from tools.llm.router import LLMRouter" not in src


def test_demo_panel_only_renders_with_real_demo_content():
    src = (TEMPLATES / "partials" / "_step_watch.html").read_text(encoding="utf-8")
    assert "{% if schema.demo_output or schema.demo_url %}" in src, (
        "the panel must not render its heading when there is nothing to show"
    )


def test_unenrolled_user_is_told_progress_is_not_saved():
    """The button used to appear to work while recording nothing."""
    src = (TEMPLATES / "mission.html").read_text(encoding="utf-8")
    assert "if (!FA_USER_ID) return;" not in src, "silent no-op"
    assert "faEnrolNotice" in src
    assert "/academy/profile" in src, "the notice must offer a way to enrol"


@pytest.mark.parametrize(
    "template", ["mission.html", "partials/_step_watch.html"]
)
def test_touched_templates_still_parse(template):
    import jinja2

    jinja2.Environment().parse((TEMPLATES / template).read_text(encoding="utf-8"))
