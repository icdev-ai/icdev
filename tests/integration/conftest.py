# CUI // SP-CTI
"""Shared fixtures for the runtime-telemetry integration tests.

``runtime_invocations`` (migration 341) has two readers that must never
disagree: ``icdev runtime top`` and the dashboard Runtime Performance panel
(``/api/runtime-invocations/summary``). Both are supposed to bottom out in
``invocation_recorder.summary()`` and ``tools.cli.runtime.normalise()`` — but
"supposed to" is exactly the claim the unit tests cannot check, because each of
them stands its own reader up against its own separately seeded database.

So these fixtures build ONE real SQLite database, apply the real migration to
it, seed it through the real ``record()`` / ``open_invocation()`` recorder, and
hand both readers the same file. Nothing is mocked: no patched connection
factory, no hand-built rollup, no substituted ``summary()``. A divergence
between the CLI and the panel — a second rollup query, a different limit floor,
a rounding change applied on one side only — then shows up as a failing
comparison instead of as two green suites describing different worlds.

The database is redirected with ``ICDEV_DB_PATH`` + a pinned sqlite backend
rather than by patching ``get_connection``, so the production connection path
runs for real — including ``StorageConnection``'s ``%s`` -> ``?`` translation,
which the rollup query depends on. Handing runtime code a raw ``sqlite3``
connection instead would make that query raise ``near "%": syntax error``, and
``summary()`` swallows the failure into an empty list, so the test would assert
its own no-op.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

from tools.observability import invocation_recorder as R

# This directory, so `from _runtime_expectations import ...` resolves in the
# test modules as well as here. Mirrors what tests/conftest.py does for
# `_sql_compat`; pytest's prepend import mode usually adds it anyway, but
# "usually" depends on how the suite was launched.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Resolved by GLOB, not a pinned number: this migration has been renumbered
#: three times (329 -> 333 -> 341) and every rename broke a hardcoded path.
_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "tools/db/migrations")
    .glob("*_runtime_invocations/up.py")
)


def _pin_sqlite(db, monkeypatch) -> None:
    """Point every ``get_connection()`` in this process at *db*."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    # Telemetry is opt-out via env. A worktree that inherited
    # ICDEV_OBS_INVOCATIONS=0 would seed nothing at all, and every consistency
    # assertion below would then hold trivially over two empty results.
    monkeypatch.delenv("ICDEV_OBS_INVOCATIONS", raising=False)
    # Process-level flag: once any earlier test touches a database without the
    # table, the recorder short-circuits for the rest of the session and writes
    # nothing here. Reset it rather than depend on collection order.
    monkeypatch.setattr(R, "_table_missing", False, raising=False)

    import tools.db.storage as storage

    monkeypatch.setattr(storage, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)


@pytest.fixture()
def unmigrated_db(tmp_path, monkeypatch):
    """A database the migration never touched — which is not an empty table.

    The two are the same value out of ``summary()`` (it returns ``[]`` on
    failure), and telling them apart is the reason both readers name a backend.
    """
    db = tmp_path / "unmigrated.db"
    _pin_sqlite(db, monkeypatch)
    return db


@pytest.fixture()
def runtime_db(tmp_path, monkeypatch):
    """Isolated SQLite database with migration 341 applied for real."""
    db = tmp_path / "runtime.db"
    _pin_sqlite(db, monkeypatch)

    spec = importlib.util.spec_from_file_location("m341_integration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.up()
    return db


@pytest.fixture()
def seeded(runtime_db):
    """Invocations written through the real recorder, matching ``EXPECTED``."""
    for _ in range(3):
        with R.record(R.SURFACE_MCP, "rag_search"):
            pass
    with pytest.raises(ValueError):
        with R.record(R.SURFACE_MCP, "rag_search"):
            raise ValueError("boom")
    with R.record(R.SURFACE_MCP, "kg_search"):
        pass
    with R.record(R.SURFACE_AGENT, "builder"):
        pass
    # Opened and deliberately never closed: `open_invocation` is the shape the
    # kanban runner needs for a build that outlives one scheduler cycle. It
    # leaves a `running` row with no duration, which is how both readers get a
    # NULL avg/max — the value they are most likely to render differently.
    R.open_invocation(R.SURFACE_PERSONA, "analyst")
    return runtime_db


@pytest.fixture(scope="module")
def panel_app():
    """Flask app built with the REAL blueprint registration.

    Module-scoped because registration imports every dashboard API module and
    does not touch the database — connections are opened per request, so each
    test still reads through its own function-scoped database fixture. Using
    the real registration rather than mounting the blueprint by hand is what
    puts the panel's hardcoded ``/api/runtime-invocations/summary`` path inside
    the scope of these tests.
    """
    from tools.dashboard.api import register_api_blueprints

    app = Flask(__name__)
    register_api_blueprints(app)
    return app


@pytest.fixture()
def panel_client(panel_app, runtime_db):
    """Test client for the panel API, reading the fixture database."""
    return panel_app.test_client()


@pytest.fixture()
def cli(capsys):
    """Driver for ``icdev runtime top`` through the real CLI dispatcher.

    Goes through ``tools.cli.__main__`` rather than calling the subcommand
    module directly, so a broken dispatcher entry ("unknown subcommand") fails
    here too — that is the command an operator actually types.
    """
    from tools.cli.__main__ import main as icdev_main

    class _Cli:
        @staticmethod
        def json(*argv):
            assert icdev_main(["runtime", "top", "--json", *argv]) == 0
            return json.loads(capsys.readouterr().out)

        @staticmethod
        def table(*argv):
            assert icdev_main(["runtime", "top", "--no-color", *argv]) == 0
            return capsys.readouterr()

    return _Cli()
