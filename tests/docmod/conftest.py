# CUI // SP-CTI
"""Per-file SQLite isolation for the docmod test suite.

The docmod tests talk to the ambient `data/icdev.db` through
`tools.db.storage.get_connection()`. Several files declare the SAME tables with
DIFFERENT shapes via `CREATE TABLE IF NOT EXISTS` — most notably
`dic_chunk_links`, which `test_pg_fixes_and_flow` deliberately shapes with a
`link_id` primary key while other files use `id`. Whichever file runs first
wins the shape; a later file's query against the column it expected then raises,
and because that failure happens mid-statement it can leak an open SQLite write
lock that deadlocks the *next* file's `DELETE FROM` cleanup — turning an
order-dependent failure into a whole-suite hang (only visible on a cold DB;
a warm DB masked it, and CI's 12-file Test allowlist never ran the full suite).

Give every docmod test *file* its own throwaway SQLite database. Each file's
`CREATE TABLE IF NOT EXISTS` then builds exactly the shape it wrote, no file can
observe another's tables or data, and a failure can never leak a lock across
files. `ICDEV_DB_PATH` is honored by `storage.get_connection()` at connect time.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="module")
def _isolate_docmod_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("docmod_db") / "icdev.db"
    prev_env = os.environ.get("ICDEV_DB_PATH")
    os.environ["ICDEV_DB_PATH"] = str(db_path)

    # storage.DB_PATH is bound at import; several call sites fall back to it, so
    # keep it in step with the env var for this module's lifetime.
    prev_mod = None
    storage = None
    try:
        import tools.db.storage as storage
        prev_mod = storage.DB_PATH
        storage.DB_PATH = str(db_path)
    except Exception:
        storage = None

    try:
        yield
    finally:
        if prev_env is None:
            os.environ.pop("ICDEV_DB_PATH", None)
        else:
            os.environ["ICDEV_DB_PATH"] = prev_env
        if storage is not None and prev_mod is not None:
            storage.DB_PATH = prev_mod
