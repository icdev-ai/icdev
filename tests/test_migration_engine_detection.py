#!/usr/bin/env python3
"""MigrationRunner engine selection — CUI // SP-CTI.

`engine` drives `_filter_sql`. A value that disagrees with the live backend
does not raise: it silently drops every statement written for the real engine,
applies the other one's, and then records the migration as applied with ~0ms
elapsed. Success and total no-op are indistinguishable from the return value.

That is exactly what happened while unshadowing
`297_web_citation_type_and_fetch_provenance.sql`: a bare `MigrationRunner()`
defaulted to sqlite against a PostgreSQL database, dropped all three
`@pg-only` blocks, reported `success: True, execution_time_ms: 0`, and wrote a
`schema_migrations` row for a migration that had done nothing.
"""
from __future__ import annotations

import pytest

from tools.db.migration_runner import MigrationRunner, _detect_engine


def test_bare_construction_matches_the_live_backend():
    """The default must follow the database, not a hardcoded guess."""
    assert MigrationRunner().engine == _detect_engine()


def test_explicit_engine_is_still_honoured():
    """Callers that know better keep control (bootstrap_pg, migrate.py)."""
    assert MigrationRunner(engine="postgresql").engine == "postgresql"
    assert MigrationRunner(engine="sqlite").engine == "sqlite"


def test_mismatched_engine_warns(caplog):
    """A silent no-op must at least be loud."""
    from tools.db import migration_runner as mr

    actual = _detect_engine()
    wrong = "sqlite" if actual == "postgresql" else "postgresql"

    prior = mr.logger.propagate
    mr.logger.propagate = True  # icdev_logger sets propagate=False
    try:
        with caplog.at_level("WARNING", logger=mr.logger.name):
            MigrationRunner(engine=wrong)
    finally:
        mr.logger.propagate = prior

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "an engine/backend mismatch must warn"
    assert any("DROPPED" in r.getMessage() for r in warnings)


@pytest.mark.parametrize("engine,directive,should_survive", [
    ("postgresql", "@pg-only", True),
    ("postgresql", "@sqlite-only", False),
    ("sqlite", "@sqlite-only", True),
    ("sqlite", "@pg-only", False),
])
def test_filter_sql_respects_the_engine(engine, directive, should_survive):
    """Pin the filter semantics both ways round.

    The bug was not in this function — it filtered correctly for the engine it
    was told about. The defect was being told the wrong engine.
    """
    sql = f"-- {directive}\nALTER TABLE t ADD COLUMN c TEXT;\n"
    out = MigrationRunner(engine=engine)._filter_sql(sql)
    assert ("ALTER TABLE" in out) is should_survive


def test_all_directive_resets_inclusion():
    sql = "-- @pg-only\nSELECT 1;\n-- @all\nSELECT 2;\n"
    out = MigrationRunner(engine="sqlite")._filter_sql(sql)
    assert "SELECT 1" not in out
    assert "SELECT 2" in out
