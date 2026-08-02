# CUI // SP-CTI
"""The citation-type vocabulary must agree everywhere it is written down.

`source_citation_registry.citation_type` was a hardcoded list in migration 149
AND in tools/db/schema/pg_consolidated.sql. Two copies of an enum drift; the repo
guardrail is "SQL CHECK constraints: derive from Python constants, never
hardcode", and these tests are what makes that guardrail enforceable rather than
aspirational.

The failure they prevent is specific: a value added to the Python constant but
not to the SQL means every INSERT of that type violates the CHECK — and
`register_citation` swallows database errors and returns "", so the citation
simply never exists and nothing raises. On a TRUST surface that is a silent
evidence hole.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tools.provenance import citation_types as ct

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── The constant ─────────────────────────────────────────────────────────────


def test_web_is_a_valid_citation_type():
    """oss-cite-01's whole point: a fetched page can be cited."""
    assert ct.is_valid("web")
    assert "web" in ct.CITATION_TYPES


def test_historical_types_are_preserved():
    """Removing one would orphan existing rows and break the CHECK."""
    for legacy in (
        "hitl", "rag", "prov_entity", "prov_activity", "canvas_ai",
        "slsa", "sbom", "compliance_evidence", "agent_decision", "manual",
    ):
        assert legacy in ct.CITATION_TYPES, f"{legacy} dropped from the vocabulary"


def test_vocabulary_has_no_duplicates():
    assert len(set(ct.CITATION_TYPES)) == len(ct.CITATION_TYPES)


def test_unknown_type_is_rejected():
    assert not ct.is_valid("url")        # tempting alias — not the chosen name
    assert not ct.is_valid("")


def test_check_constraint_sql_renders_every_type():
    sql = ct.check_constraint_sql()
    for t in ct.CITATION_TYPES:
        assert f"'{t}'" in sql, f"{t} missing from rendered CHECK"


# ── Agreement with the shipped SQL ───────────────────────────────────────────


def _quoted_values(text: str) -> set:
    return set(re.findall(r"'([a-z_]+)'", text))


def test_migration_295_check_matches_the_constant():
    """The migration's CHECK must be exactly what the constant renders."""
    path = REPO / "tools" / "db" / "migrations" / "295_web_citation_type_and_fetch_provenance.sql"
    sql = path.read_text(encoding="utf-8")

    m = re.search(r"CHECK \(citation_type IN \(([^)]+)\)\)", sql)
    assert m, "no rendered CHECK clause found in migration 295"
    assert _quoted_values(m.group(1)) == set(ct.CITATION_TYPES), (
        "migration 295's CHECK has drifted from CITATION_TYPES — regenerate it "
        "with tools.provenance.citation_types.check_constraint_sql()"
    )


def test_consolidated_schema_matches_the_constant():
    """pg_consolidated.sql is the squashed schema new deployments get.

    If it disagrees with the constant, a fresh database rejects citation types
    an upgraded one accepts.
    """
    path = REPO / "tools" / "db" / "schema" / "pg_consolidated.sql"
    sql = path.read_text(encoding="utf-8")

    m = re.search(
        r"CONSTRAINT source_citation_registry_citation_type_check CHECK "
        r"\(\(citation_type = ANY \(ARRAY\[([^\]]+)\]\)\)\)",
        sql,
    )
    assert m, "citation_type constraint not found in pg_consolidated.sql"
    assert _quoted_values(m.group(1)) == set(ct.CITATION_TYPES), (
        "pg_consolidated.sql has drifted from CITATION_TYPES"
    )


# ── The writer validates before it swallows ──────────────────────────────────


def test_register_citation_raises_on_an_unknown_type():
    """Must raise, not return "".

    register_citation catches database errors and returns an empty id. A typo'd
    type would otherwise be indistinguishable from a write that never happened,
    and callers rarely check the return value.
    """
    from tools.provenance.registry import register_citation

    with pytest.raises(ValueError, match="unknown citation_type"):
        register_citation(
            citation_type="webpage",     # plausible typo for 'web'
            source_table="x",
            source_record_id="y",
            source_hash="z",
        )


def test_register_citation_error_names_the_valid_set():
    from tools.provenance.registry import register_citation

    with pytest.raises(ValueError) as exc:
        register_citation("nope", "x", "y", "z")
    for t in ("web", "hitl", "rag"):
        assert t in str(exc.value)


# ── Append-only registration ─────────────────────────────────────────────────


def test_fetch_provenance_is_registered_append_only():
    """A fetch is an observation; rewriting it destroys the evidence.

    Re-fetching must append a row so drift is visible, which only holds if the
    table is on the append-only list the pre-tool hook enforces.
    """
    hook = (REPO / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
    assert "web_fetch_provenance" in hook, (
        "web_fetch_provenance missing from APPEND_ONLY_TABLES in "
        ".claude/hooks/pre_tool_use.py"
    )


def test_detail_table_mapping_points_at_a_real_table():
    assert ct.DETAIL_TABLES["web"] == "web_fetch_provenance"
    migration = (
        REPO / "tools" / "db" / "migrations"
        / "295_web_citation_type_and_fetch_provenance.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS web_fetch_provenance" in migration
