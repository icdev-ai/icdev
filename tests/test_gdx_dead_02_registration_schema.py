# CUI // SP-CTI
"""gdx-dead-02 — the GameDay registration / snake-draft feature is KEPT-BUT-UNWIRED.

Two tables — ``ttx_registrations`` and ``ttx_formation_plan`` — sit in
``tools/db/schema/pg_consolidated.sql`` with no reader, no writer and no migration.
gdx-dead-02 decided to RETAIN them rather than drop them, agreeing with gdx-mir-02,
which promoted the matching ``register.html`` / ``registrations.html`` templates into
``tools/`` for the same reason: the design is complete and dropping tables that no
migration creates would mean a destructive migration to remove something inert.

That decision is only stable if it stays legible. These tests pin all three sides so
the next reader cannot silently reach the opposite conclusion:

  1. the DDL is still there, and still carries the UNWIRED marker explaining why;
  2. the feature doc still says "designed, not built" and does not re-credit the
     unwired tables with NIST AC-2 / SI-10 coverage;
  3. the tables are still genuinely unwired — if someone wires them, this test fails
     and tells them to retire the marker rather than leaving a stale "UNWIRED" lie.

Pure filesystem/grep assertions: no DB fixture, because the whole point is that these
tables exist in no runtime database.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "tools" / "db" / "schema" / "pg_consolidated.sql"
ICDEV_SCHEMA = REPO_ROOT / "icdev" / "tools" / "db" / "schema" / "pg_consolidated.sql"
FEATURE_DOC = REPO_ROOT / "docs" / "features" / "phase-wge-wargame-enhancements.md"

TABLES = ("ttx_registrations", "ttx_formation_plan")

# Trees that would constitute "wiring": runtime code, not schema/docs/tests.
CODE_ROOTS = ("tools", "icdev/tools", "apps", "icdev/apps")

# Schema snapshots legitimately name the tables; so does this test and the doc.
_ALLOWED_SUFFIXES = ("pg_consolidated.sql",)


def _iter_code_files():
    for root in CODE_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            yield path


# ---------------------------------------------------------------------------
# 1. The DDL is retained, and says why
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("schema_path", [SCHEMA, ICDEV_SCHEMA], ids=["tools", "icdev"])
@pytest.mark.parametrize("table", TABLES)
def test_ddl_retained(schema_path, table):
    """gdx-dead-02 chose KEEP. The CREATE TABLE must still be present."""
    if not schema_path.exists():
        pytest.skip(f"{schema_path} not present in this tree")
    sql = schema_path.read_text(encoding="utf-8")
    assert f"CREATE TABLE public.{table} (" in sql, (
        f"{table} DDL is gone from {schema_path.name}. gdx-dead-02 decided to RETAIN "
        f"these tables (agreeing with gdx-mir-02, which kept the matching templates). "
        f"If that decision is being reversed, reverse it on BOTH sides — drop the "
        f"templates and update the feature doc in the same change."
    )


@pytest.mark.parametrize("schema_path", [SCHEMA, ICDEV_SCHEMA], ids=["tools", "icdev"])
@pytest.mark.parametrize("table", TABLES)
def test_ddl_carries_unwired_marker(schema_path, table):
    """Dead DDL without an explanation is what misleads the next schema reader."""
    if not schema_path.exists():
        pytest.skip(f"{schema_path} not present in this tree")
    sql = schema_path.read_text(encoding="utf-8")
    idx = sql.index(f"CREATE TABLE public.{table} (")
    preamble = sql[max(0, idx - 1400): idx]
    assert "UNWIRED BY DESIGN" in preamble, (
        f"The comment block above {table} lost its 'UNWIRED BY DESIGN' marker. "
        f"Without it this reads as an ordinary table and the next reader re-derives "
        f"the whole gdx-dead-02 investigation from scratch."
    )
    assert "gdx-reg-01" in preamble, (
        f"The {table} comment must keep pointing at gdx-reg-01, the card that owns "
        f"wiring-or-retiring this feature."
    )


# ---------------------------------------------------------------------------
# 2. The feature doc does not claim the feature ships
# ---------------------------------------------------------------------------

def test_feature_doc_marks_feature_unbuilt():
    doc = FEATURE_DOC.read_text(encoding="utf-8")
    assert "## Registration & team formation: designed, not built" in doc, (
        "The feature doc lost its designed-not-built section. That section is the only "
        "place the two-sided history (penta-gd-03 deleted the code, gdx-mir-02 kept the "
        "templates and DDL) is written down."
    )
    assert "DESIGNED, NOT WIRED" in doc


def test_feature_doc_does_not_credit_unwired_tables_for_ac2():
    """A compliance doc must not cite a table nothing writes to as control evidence.

    The AC-2 mapping table originally credited ``ttx_registrations`` with satisfying
    account creation, and the DELETE endpoint with account removal. Neither exists.
    """
    doc = FEATURE_DOC.read_text(encoding="utf-8")
    ac2 = doc[doc.index("### AC-2"): doc.index("### AU-2")]
    assert "Partial coverage" in ac2, "AC-2 must declare the registration coverage gap"
    assert "Not satisfied" in ac2, "AC-2 must keep its explicit not-satisfied gap table"
    # The mapping rows that claimed coverage must not have come back.
    assert "Account creation: every participant is registered" not in ac2, (
        "AC-2 is again crediting ttx_registrations with account creation. Nothing "
        "writes to that table."
    )


# ---------------------------------------------------------------------------
# 3. The tables are still genuinely unwired
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", TABLES)
def test_tables_have_no_runtime_reader_or_writer(table):
    """If this fails, the feature got wired — which is good news, not a defect.

    Retire the UNWIRED markers and this module rather than working around it.
    """
    offenders = []
    pattern = re.compile(rf"\b{table}\b")
    for path in _iter_code_files():
        if path.name.endswith(_ALLOWED_SUFFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())

    assert not offenders, (
        f"{table} now has runtime references: {offenders}. gdx-dead-02 recorded it as "
        f"UNWIRED across the schema comments and the feature doc. If the feature was "
        f"built (gdx-reg-01), remove the 'UNWIRED BY DESIGN' comment blocks in "
        f"pg_consolidated.sql, update the feature doc's designed-not-built section and "
        f"its AC-2/SI-10 gap tables, add the tables to apps/ai_gameday/db.py _DDL plus "
        f"a migration, and delete this module. A stale 'UNWIRED' marker is worse than "
        f"none."
    )


@pytest.mark.parametrize("table", TABLES)
def test_no_migration_creates_the_tables(table):
    """Documents the sharp edge: these tables do NOT exist on a fresh database.

    They arrive only via the consolidated snapshot, so any future wiring must land a
    migration too. If a migration appears, the comment blocks and the feature doc both
    overstate the problem and should be updated.
    """
    hits = []
    for root in ("tools/db/migrations", "icdev/tools/db/migrations"):
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in list(base.rglob("*.sql")) + list(base.rglob("*.py")):
            try:
                if table in path.read_text(encoding="utf-8"):
                    hits.append(path.relative_to(REPO_ROOT).as_posix())
            except (UnicodeDecodeError, OSError):
                continue
    assert not hits, (
        f"A migration now creates {table} ({hits}). Update the 'no migration creates "
        f"them' claim in pg_consolidated.sql and the feature doc — it is now false."
    )
