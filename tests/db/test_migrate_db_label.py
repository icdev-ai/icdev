"""`migrate.py` must name the database a run ACTUALLY used (task-det-12a243722f).

On PostgreSQL the ``db_path`` handed to ``MigrationRunner`` is ignored --
``_get_connection()`` builds the connection from ``ICDEV_DATABASE_URL`` or the
``ICDEV_PG_*`` vars. Printing that path anyway names a database the run never
opened.

MEASURED 2026-09-05 on this deployment, before the fix::

    $ python tools/db/migrate.py --status
    Database: C:\\AI\\ICDev\\.tmp\\worktrees\\task-det-12a243722f\\data\\icdev.db
    Engine: postgresql
    Applied: 432  |  Pending: 0

That file did not exist on disk; the 432/0 came from PostgreSQL. The header
named one database and the numbers under it described another. This matters
because applying a migration is an irreversible deployment act, and it is the
remediation the ``migration_drift`` detector (autonomy-dep-01) sends an operator
to run -- so a wrong label there is a false report of WHERE the act landed, and
reads as the fabricated-clean shape ("the worktree DB says 0 pending").
"""

import importlib

import pytest

migrate = importlib.import_module("tools.db.migrate")

PG_VARS = (
    "ICDEV_DATABASE_URL",
    "ICDEV_PG_HOST",
    "ICDEV_PG_PORT",
    "ICDEV_PG_DATABASE",
)


@pytest.fixture
def clean_pg_env(monkeypatch):
    """Neutralise inherited PG env so each case measures only what it sets."""
    for var in PG_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_sqlite_label_is_the_path(clean_pg_env):
    """On SQLite the path IS the database, so it stays the label."""
    assert migrate._db_label("sqlite", r"C:\tmp\icdev.db") == r"C:\tmp\icdev.db"


def test_pg_label_never_names_the_ignored_path(clean_pg_env):
    """The defect: a PG run must not present its unused db_path as the database."""
    label = migrate._db_label("postgresql", r"C:\tmp\icdev.db")
    assert "icdev.db" not in label
    assert label.startswith("postgresql")


def test_pg_label_names_the_configured_target(clean_pg_env):
    clean_pg_env.setenv("ICDEV_PG_HOST", "dbhost")
    clean_pg_env.setenv("ICDEV_PG_PORT", "5433")
    clean_pg_env.setenv("ICDEV_PG_DATABASE", "icdev_it")

    assert migrate._db_label("postgresql", None) == "postgresql://dbhost:5433/icdev_it"


def test_dsn_is_named_never_echoed(clean_pg_env):
    """A DSN carries credentials -- name the source, never print the value."""
    clean_pg_env.setenv(
        "ICDEV_DATABASE_URL", "postgresql://user:hunter2@db.internal:5432/icdev"
    )

    label = migrate._db_label("postgresql", None)

    assert "hunter2" not in label
    assert "db.internal" not in label
    assert label == "postgresql (ICDEV_DATABASE_URL)"


def test_status_header_follows_the_engine(clean_pg_env):
    """`--status` renders the engine's target, not the constructor's path."""
    clean_pg_env.setenv("ICDEV_PG_HOST", "dbhost")
    clean_pg_env.setenv("ICDEV_PG_PORT", "5432")
    clean_pg_env.setenv("ICDEV_PG_DATABASE", "icdev_it")

    rendered = migrate._format_status(
        {
            "db_path": r"C:\tmp\icdev.db",
            "engine": "postgresql",
            "has_migrations_table": True,
            "current_version": "343",
            "applied_count": 432,
            "pending_count": 0,
            "applied": [],
            "pending": [],
            "issues": [],
        }
    )

    header = rendered.splitlines()[0]
    assert header == "Database: postgresql://dbhost:5432/icdev_it"
    assert "icdev.db" not in header
