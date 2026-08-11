#!/usr/bin/env python3
# CUI // SP-CTI
"""agov-det-05 — agent_findings is append-only, and its INSERT matches the live schema.

Three separate claims, each of which has failed silently somewhere in this repo
before:

1. The pre-tool-use hook refuses an UPDATE/DELETE against `agent_findings`.
   Registering a table in APPEND_ONLY_TABLES is a one-line edit that nothing
   else verifies, and an unregistered audit table is indistinguishable from a
   registered one until someone edits a row.
2. Every column the writer names exists in the LIVE schema. `CREATE TABLE IF NOT
   EXISTS` never alters an existing table, so a column added to the DDL of an
   already-created table is absent at runtime; the INSERT then raises inside a
   best-effort `except` and the feature reports success while persisting
   nothing. That is how `module_budget_usage` held zero rows.
3. The migration's DDL and the conftest fixture agree. A fixture that disagrees
   with production rewards exactly the INSERTs that are dead in production.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sqlite3

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
MIGRATION = (
    REPO_ROOT
    / "tools"
    / "db"
    / "migrations"
    / "20260809201320_agov_agent_findings"
    / "up.py"
)

TABLE = "agent_findings"


def _load_hook():
    spec = importlib.util.spec_from_file_location("agov_pre_tool_use", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. Append-only enforcement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "statement",
    [
        f"UPDATE {TABLE} SET severity='low' WHERE finding_id='x'",
        f"update {TABLE} set decision='observed'",
        f"DELETE FROM {TABLE} WHERE finding_id='x'",
        f"DROP TABLE {TABLE}",
        f"TRUNCATE TABLE {TABLE}",
    ],
)
def test_hook_refuses_mutation_of_agent_findings(statement: str):
    """An UPDATE against agent_findings must be refused by the append-only check."""
    mod = _load_hook()
    assert (
        mod.is_append_only_table_modification(
            "Bash", {"command": f"sqlite3 data/icdev.db \"{statement}\""}
        )
        is True
    ), f"append-only check let through: {statement}"


def test_hook_allows_insert_and_select_on_agent_findings():
    """Append-only means append. Blocking the writer would be the other failure."""
    mod = _load_hook()
    for statement in (
        f"INSERT INTO {TABLE} (finding_id) VALUES ('x')",
        f"SELECT * FROM {TABLE} WHERE session_id='s'",
    ):
        assert (
            mod.is_append_only_table_modification("Bash", {"command": statement}) is False
        ), f"append-only check wrongly blocked: {statement}"


def test_agent_findings_is_registered_in_the_canonical_list():
    """The list is DATA in this file — CLAUDE.md, the child-app generator and
    coherence_checker's autofix all read it from here."""
    source = HOOK.read_text(encoding="utf-8")
    body = source.split("APPEND_ONLY_TABLES = [", 1)[1]
    assert f'"{TABLE}",' in body, f"{TABLE} is not in APPEND_ONLY_TABLES"


# ---------------------------------------------------------------------------
# 2. INSERT columns vs the live schema
# ---------------------------------------------------------------------------
def _ddl_columns(sql: str) -> list[str]:
    body = sql.split("(", 1)[1].rsplit(")", 1)[0]
    cols = []
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        name = line.split()[0]
        if name.upper() in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"):
            continue
        cols.append(name.lower())
    return cols


def _load_migration():
    spec = importlib.util.spec_from_file_location("agov_findings_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _static_insert_columns() -> list[str]:
    """The column list of the writer's INSERT, read out of the source.

    Deliberately parsed from the text rather than from findings.COLUMNS: the
    constant and the statement are two things that can drift, and the whole
    failure mode being guarded against is a mismatch nobody looks at.
    """
    from tools.agent_detect import findings as findings_mod

    source = pathlib.Path(findings_mod.__file__).read_text(encoding="utf-8")
    # The statement is built from adjacent string literals; join them first.
    flat = re.sub(r'"\s*\n\s*"', "", source)
    match = re.search(
        r"INSERT INTO\s+agent_findings\s*\((?P<cols>[^)]*)\)", flat, re.IGNORECASE
    )
    assert match, "no static INSERT INTO agent_findings found in findings.py"
    return [c.strip().lower() for c in match.group("cols").split(",") if c.strip()]


def test_writer_insert_columns_exist_in_the_migration_ddl():
    mod = _load_migration()
    for ddl in (mod._DDL, mod._DDL_PG):
        declared = set(_ddl_columns(ddl))
        missing = [c for c in _static_insert_columns() if c not in declared]
        assert not missing, f"INSERT names columns absent from the DDL: {missing}"


def test_writer_columns_constant_matches_its_own_insert():
    from tools.agent_detect.findings import COLUMNS

    assert list(COLUMNS) == _static_insert_columns(), (
        "findings.COLUMNS and the INSERT statement disagree — the row is built "
        "from the constant and bound positionally, so a drift here writes every "
        "value into the wrong column."
    )


def test_every_select_matches_the_columns_constant():
    """All four read statements must select COLUMNS, in COLUMNS' order.

    `list_findings` zips each row against COLUMNS positionally. A query whose
    column order drifted would not raise — it would silently return findings
    with `severity` in the `title` field, which is the kind of wrong that gets
    believed.
    """
    from tools.agent_detect.findings import COLUMNS, _QUERIES

    assert len(_QUERIES) == 4, "expected the (session_id?, rule_id?) matrix"
    for key, sql in _QUERIES.items():
        selected = re.search(r"SELECT\s+(?P<cols>.*?)\s+FROM", sql, re.DOTALL).group("cols")
        cols = [c.strip().lower() for c in selected.split(",")]
        assert cols == list(COLUMNS), f"query {key} selects {cols}, not COLUMNS"
        # One placeholder per bound param: the filters plus LIMIT.
        assert sql.count("%s") == sum(key) + 1, f"query {key} has the wrong placeholder count"


def test_sqlite_and_postgres_ddl_declare_the_same_columns():
    mod = _load_migration()
    assert _ddl_columns(mod._DDL) == _ddl_columns(mod._DDL_PG), (
        "the SQLite and PostgreSQL DDL disagree; PostgreSQL is the primary "
        "backend and the SQLite branch is the init/test fallback, so a column "
        "present in only one is a table that behaves differently per backend."
    )


def test_conftest_fixture_matches_the_migration_ddl():
    """A fixture that drifts from production makes dead INSERTs look healthy."""
    mod = _load_migration()
    schema = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS agent_findings \((?P<body>.*?)\n\);",
        schema,
        re.DOTALL,
    )
    assert match, "agent_findings is not in MINIMAL_ICDEV_SCHEMA in tests/conftest.py"
    fixture_cols = _ddl_columns("x (" + match.group("body") + ")")
    assert fixture_cols == _ddl_columns(mod._DDL), (
        f"conftest fixture columns {fixture_cols} != migration columns "
        f"{_ddl_columns(mod._DDL)}"
    )


def test_insert_columns_match_information_schema(tmp_path):
    """The acceptance check, run against a real table built by the migration.

    Applies the migration's own DDL to a throwaway database and compares the
    INSERT's column list against what the engine reports it actually has —
    `PRAGMA table_info` here, `information_schema.columns` on PostgreSQL, which
    is the pair `coherence_checker.check_insert_schema_parity` reads.
    """
    mod = _load_migration()
    db = tmp_path / "findings.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(mod._DDL)
        conn.commit()
        live = {row[1].lower() for row in conn.execute(f"PRAGMA table_info({TABLE})")}
    finally:
        conn.close()

    missing = [c for c in _static_insert_columns() if c not in live]
    assert not missing, f"INSERT names columns the live table does not have: {missing}"


# ---------------------------------------------------------------------------
# 3. The writer itself
# ---------------------------------------------------------------------------
def test_build_finding_round_trips_through_the_real_table(tmp_path):
    """Prove the row the writer builds actually binds against the real DDL.

    Building the row and inserting it are tested together on purpose: a test
    that only checked the dict would pass while the INSERT raised on arity.
    """
    from tools.agent_detect.findings import COLUMNS, build_finding

    mod = _load_migration()
    conn = sqlite3.connect(tmp_path / "findings.db")
    try:
        conn.execute(mod._DDL)
        row = build_finding(
            rule_id="chains.secret_read_then_egress",
            rule_version="1",
            severity="critical",
            title="Read credential material, then sent data to an external host",
            session_id="session-abc",
            actor="claude",
            project_id="icdev",
            event_ids=["evt-1", "evt-2"],
            tags=["T1552.001", "T1041"],
        )
        conn.execute(
            f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join(['?'] * len(COLUMNS))})",
            tuple(row[c] for c in COLUMNS),
        )
        conn.commit()

        stored = conn.execute(
            f"SELECT event_ids, enforced, decision, classification FROM {TABLE}"
        ).fetchone()
    finally:
        conn.close()

    # Ordered, and read back with json.loads in Python — never json_each.
    assert json.loads(stored[0]) == ["evt-1", "evt-2"]
    assert not stored[1], "a shipped rule is monitor-only, so enforced must be false"
    assert stored[2] == "observed"
    assert stored[3] == "CUI"


def test_finding_id_is_stable_for_the_same_observation():
    """Re-observing one chain must not append a second row.

    agov-det-06 calls this from the pre-tool-use hook, which runs before every
    tool call, so a sequence rule keeps matching the same chain as the window
    slides. A random id there would append one row per subsequent tool call.
    """
    from tools.agent_detect.findings import build_finding

    kwargs = dict(
        rule_id="chains.secret_read_then_egress",
        rule_version="1",
        session_id="session-abc",
        event_ids=["evt-1", "evt-2"],
    )
    assert build_finding(**kwargs)["finding_id"] == build_finding(**kwargs)["finding_id"]

    # A different session is a different observation...
    other = dict(kwargs, session_id="session-xyz")
    assert build_finding(**other)["finding_id"] != build_finding(**kwargs)["finding_id"]

    # ...and so is the same pair of events in the other order.
    reordered = dict(kwargs, event_ids=["evt-2", "evt-1"])
    assert build_finding(**reordered)["finding_id"] != build_finding(**kwargs)["finding_id"]


def test_bad_vocabulary_is_normalised_not_dropped():
    """The caller is a detection path; losing the signal is worse than fixing it."""
    from tools.agent_detect.findings import build_finding

    # Case is normalised, not rejected — "HIGH" is the severity the author meant.
    assert build_finding(rule_id="r", severity="HIGH")["severity"] == "high"

    # A genuinely unrecognised value falls back rather than dropping the finding
    # or writing a value the column's readers do not know how to rank.
    row = build_finding(rule_id="r", severity="catastrophic", decision="nonsense")
    assert row["severity"] == "info"
    assert row["decision"] == "observed"


def test_record_finding_never_raises(monkeypatch):
    """The hook must survive a database that is down, missing or misconfigured."""
    import importlib

    from tools.agent_detect import findings as findings_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    # Patched via importlib + setattr, not monkeypatch's dotted-string form:
    # `tools.` is a backward-compat shim onto `icdev.tools.`, so the string form
    # resolves the attribute against a different module object than the lazy
    # `from tools.db.storage import ...` inside record_finding actually binds.
    for dotted, name in (
        ("tools.db.storage", "get_connection"),
        ("tools.airgap.hook_compat", "store_event"),
    ):
        monkeypatch.setattr(importlib.import_module(dotted), name, boom)

    result = findings_mod.record_finding(
        findings_mod.build_finding(rule_id="secrets.env_file_read")
    )
    assert result["persisted"] is False
    assert result["sink"] == "none"


def test_severity_vocabulary_is_the_single_source():
    """The migration carries no CHECK constraint; this constant is the vocabulary."""
    from tools.agent_detect.findings import SEVERITIES

    mod = _load_migration()
    for ddl in (mod._DDL, mod._DDL_PG):
        assert "CHECK" not in ddl.upper(), (
            "a CHECK constraint here is a second copy of the vocabulary that "
            "will drift from SEVERITIES and fail INSERTs inside a best-effort "
            "except — CLAUDE.md: derive from Python constants, never hardcode"
        )
    assert SEVERITIES == ("info", "low", "medium", "high", "critical")
