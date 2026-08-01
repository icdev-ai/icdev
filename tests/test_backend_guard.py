# CUI // SP-CTI
"""A long-lived server must refuse to serve from the SQLite fallback.

e2p-back-04. `.env` alone cannot enforce this — see tools/db/backend_guard.py —
so the refusal lives in code, and these pin the exact conditions under which it
fires. Getting the conditions wrong in either direction is costly: too eager and
the pytest suite (which legitimately forces sqlite) cannot run; too lax and a
server quietly serves 500s from a database nothing maintains.
"""
from __future__ import annotations

import pytest

from tools.db.backend_guard import (
    ESCAPE_HATCH,
    SqliteServerRefused,
    assert_primary_backend,
    check_primary_backend,
)

PG_ONLY = {"ICDEV_PG_NO_FALLBACK": "true"}


# ── It fires ───────────────────────────────────────────────────────────────

def test_refuses_sqlite_pin_when_install_is_pg_only():
    msg = check_primary_backend("dashboard", {**PG_ONLY, "ICDEV_STORAGE_BACKEND": "sqlite"})
    assert msg and "refuses to start" in msg


def test_refusal_names_the_component():
    msg = check_primary_backend("api_gateway", {**PG_ONLY, "ICDEV_STORAGE_BACKEND": "sqlite"})
    assert msg.startswith("api_gateway")


def test_refusal_explains_the_fix_and_the_escape_hatch():
    """A guard nobody can act on just gets deleted."""
    msg = check_primary_backend("dashboard", {**PG_ONLY, "ICDEV_STORAGE_BACKEND": "sqlite"})
    assert "postgresql" in msg
    assert ESCAPE_HATCH in msg


def test_assert_raises():
    with pytest.raises(SqliteServerRefused):
        assert_primary_backend("dashboard", {**PG_ONLY, "ICDEV_STORAGE_BACKEND": "sqlite"})


@pytest.mark.parametrize("flag", ["true", "TRUE", "1", "yes", "on"])
def test_no_fallback_flag_is_read_loosely(flag):
    msg = check_primary_backend(
        "dashboard", {"ICDEV_PG_NO_FALLBACK": flag, "ICDEV_STORAGE_BACKEND": "sqlite"})
    assert msg, f"{flag!r} should count as PG-only"


# ── It stays quiet ─────────────────────────────────────────────────────────

def test_allows_postgresql():
    assert check_primary_backend("dashboard", {**PG_ONLY, "ICDEV_STORAGE_BACKEND": "postgresql"}) is None


def test_allows_unset_backend():
    """Unset means postgresql — the documented default."""
    assert check_primary_backend("dashboard", dict(PG_ONLY)) is None


def test_allows_sqlite_when_the_install_did_not_ask_for_pg_only():
    """An air-gap / demo / laptop SQLite deployment is a legitimate choice."""
    assert check_primary_backend("dashboard", {"ICDEV_STORAGE_BACKEND": "sqlite"}) is None


@pytest.mark.parametrize("flag", ["", "false", "0", "no"])
def test_allows_sqlite_when_no_fallback_is_off(flag):
    assert check_primary_backend(
        "dashboard", {"ICDEV_PG_NO_FALLBACK": flag, "ICDEV_STORAGE_BACKEND": "sqlite"}) is None


def test_escape_hatch_opts_out():
    """The E2E suite carries this until e2p-back-03 moves it to PostgreSQL."""
    assert check_primary_backend(
        "dashboard",
        {**PG_ONLY, "ICDEV_STORAGE_BACKEND": "sqlite", ESCAPE_HATCH: "1"},
    ) is None


def test_pytest_suite_is_unaffected():
    """tests/conftest.py forces sqlite for every test and must keep working.

    The guard is opt-in per caller — only servers call assert_primary_backend —
    but even by env alone a pytest run must not trip it, because conftest does
    not set ICDEV_PG_NO_FALLBACK.
    """
    import os
    assert os.environ.get("ICDEV_STORAGE_BACKEND") == "sqlite", "conftest should force sqlite"
    assert check_primary_backend("pytest", dict(os.environ)) is None


# ── The mechanism this exists because of ───────────────────────────────────

def test_an_explicit_pin_is_not_a_fallback():
    """Why ICDEV_PG_NO_FALLBACK could not catch this on its own.

    get_connection() only consults that flag inside its postgresql branch, when
    PG was chosen and the connection failed. A pin returns SQLite before the
    flag is ever read — which is exactly the hole this guard closes.
    """
    env = {**PG_ONLY, "ICDEV_STORAGE_BACKEND": "sqlite"}
    assert check_primary_backend("dashboard", env), (
        "a pin must be refused even though ICDEV_PG_NO_FALLBACK is set, because "
        "get_connection() never reaches its no-fallback branch for a pin"
    )
