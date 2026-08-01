# CUI // SP-CTI
"""nav-misc-01 V&V: Updates badge is per-user (not cookie-only).

Before this change the "Updates" nav badge compared an ``icdev_seen_version``
cookie to the brand version — per-browser, lost on cookie clear, not synced
across a user's devices. The fix persists the last-seen version per user in
``user_preferences`` (via ``tools.auth.onboarding``) for logged-in users while
preserving the cookie fallback for anonymous visitors.

These tests exercise the real building blocks:
  * ``tools.dashboard.app._current_user_id`` — resolves ``g.current_user`` id.
  * ``tools.auth.onboarding.{get,set}_last_seen_version`` — per-user persistence.
  * ``tools.dashboard.brand.get_brand`` — the brand version compared against.

Acceptance:
  1. A logged-in user who visits /updates has the badge cleared in a *fresh*
     session with no cookie (server-side persistence, cross-device).
  2. A subsequent version bump makes the badge return for that user.
  3. Anonymous visitors keep the cookie-based behavior.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Isolated SQLite user_preferences DB (mirrors tests/test_ecr_cl.py)
# ---------------------------------------------------------------------------

def _sqlite_db(tmp_path: Path) -> str:
    db = tmp_path / "nav_misc_01.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id          TEXT PRIMARY KEY,
            tenant_id        TEXT NOT NULL DEFAULT 'default',
            onboarding_state TEXT NOT NULL DEFAULT '{}',
            updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
        """
    )
    conn.commit()
    conn.close()
    return str(db)


def _reload_onboarding(monkeypatch: pytest.MonkeyPatch, db_path: str):
    """Point storage + onboarding at the isolated sqlite DB and return the module."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", db_path)
    # Must NOT set ICDEV_USER_PREFS_ENABLED — get_canvas_connection() would treat
    # its value as a sqlite path. Unset => default ICDEV_DB_PATH is used.
    monkeypatch.delenv("ICDEV_USER_PREFS_ENABLED", raising=False)

    import tools.db.storage as _storage
    importlib.reload(_storage)
    import tools.auth.onboarding as _onb
    importlib.reload(_onb)
    return _onb


def _brand_version() -> str:
    from tools.dashboard.brand import get_brand
    return get_brand(reload=True).get("version", "")


# ---------------------------------------------------------------------------
# _current_user_id — real helper from tools.dashboard.app
# ---------------------------------------------------------------------------

def _make_g_app():
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def test_current_user_id_anonymous_returns_none():
    """No g.current_user (anonymous request) -> None, so the cookie path is used."""
    from tools.dashboard.app import _current_user_id
    from flask import g

    app = _make_g_app()
    with app.test_request_context("/"):
        # g.current_user unset
        assert _current_user_id() is None
        g.current_user = None
        assert _current_user_id() is None


def test_current_user_id_logged_in_returns_id():
    """A dict g.current_user with an id -> that id (str)."""
    from tools.dashboard.app import _current_user_id
    from flask import g

    app = _make_g_app()
    with app.test_request_context("/"):
        g.current_user = {"id": 42, "email": "a@b.mil"}
        assert _current_user_id() == "42"


def test_current_user_id_logged_in_no_id_returns_none():
    """A user dict with no id falls back to None (anonymous-equivalent)."""
    from tools.dashboard.app import _current_user_id
    from flask import g

    app = _make_g_app()
    with app.test_request_context("/"):
        g.current_user = {"email": "a@b.mil"}
        assert _current_user_id() is None


# ---------------------------------------------------------------------------
# Badge decision + /updates recording — real onboarding + brand modules,
# wired the same way create_app() wires the context processor and route.
# ---------------------------------------------------------------------------

def _unseen(onb, brand_version: str, user_id: str | None, cookie: str) -> bool:
    """Replicate the app.py context-processor decision using the real modules."""
    if not brand_version:
        return False
    if user_id:
        seen = onb.get_last_seen_version(user_id) or ""
    else:
        seen = cookie
    return seen != brand_version


def test_logged_in_user_visiting_updates_clears_badge_in_fresh_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #1: after a logged-in user visits /updates, a brand-new session
    with NO cookie still shows no badge (persisted server-side, per user)."""
    db_path = _sqlite_db(tmp_path)
    onb = _reload_onboarding(monkeypatch, db_path)
    bv = _brand_version()
    assert bv, "brand version must be non-empty"
    uid = "user-nav-misc-01"

    # Brand-new user: badge shows.
    assert _unseen(onb, bv, uid, cookie="") is True

    # Visiting /updates records the current version for this user (server-side).
    onb.set_last_seen_version(uid, bv)

    # Fresh session — no cookie at all — badge is cleared for THIS user.
    assert _unseen(onb, bv, uid, cookie="") is False


def test_version_bump_reshows_badge_for_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #2: once the version bumps past what the user acknowledged,
    the badge returns."""
    db_path = _sqlite_db(tmp_path)
    onb = _reload_onboarding(monkeypatch, db_path)
    uid = "user-bump"

    onb.set_last_seen_version(uid, "1.0.0")
    assert _unseen(onb, "1.0.0", uid, cookie="") is False   # up to date
    assert _unseen(onb, "1.1.0", uid, cookie="") is True    # new release -> badge


def test_anonymous_falls_back_to_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #3: anonymous visitors (user_id is None) still use the cookie."""
    db_path = _sqlite_db(tmp_path)
    onb = _reload_onboarding(monkeypatch, db_path)
    bv = _brand_version()
    assert bv

    # No cookie -> badge shown for anonymous.
    assert _unseen(onb, bv, user_id=None, cookie="") is True
    # Cookie matches brand version -> badge hidden.
    assert _unseen(onb, bv, user_id=None, cookie=bv) is False
    # Stale cookie -> badge shown again.
    assert _unseen(onb, bv, user_id=None, cookie="0.0.1") is True


def test_per_user_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One user acknowledging a release does not clear the badge for another."""
    db_path = _sqlite_db(tmp_path)
    onb = _reload_onboarding(monkeypatch, db_path)
    bv = _brand_version()
    assert bv

    onb.set_last_seen_version("user-a", bv)
    assert _unseen(onb, bv, "user-a", cookie="") is False   # acknowledged
    assert _unseen(onb, bv, "user-b", cookie="") is True    # untouched -> badge
