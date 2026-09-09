# CUI // SP-CTI
"""One table, one shape — the gate that ends a defect fixed four times by hand.

Every one of these was green locally and red only in CI, and each was repaired
by patching the single column CI happened to name:

    NOT NULL constraint failed: dic_chunk_links.doc_id
    NOT NULL constraint failed: dic_chunk_links.chunk_index
    NOT NULL constraint failed: dic_sections.doc_id
    no such column: dic_presence_sessions.expires_at

`CREATE TABLE IF NOT EXISTS` never ALTERS an existing table, so two definitions
of one table means whichever runs first wins. The tests below are mostly about
what the census must NOT report, because a gate that cries wolf on a fixture
legitimately declaring three of forty columns gets switched off in a week.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci import schema_drift_census as sdc  # noqa: E402

RECORD = """
CREATE TABLE public.widgets (
    widget_id text NOT NULL,
    owner text NOT NULL,
    label text,
    note text DEFAULT 'x'::text NOT NULL,
    created_at text NOT NULL
);
"""


def _defs(text: str, path="tests/fake.py"):
    out, seen = [], {}
    for m in sdc._CREATE_HEAD.finditer(text):
        body = sdc._balanced_body(text, m.end() - 1)
        if body is None:
            continue
        td = sdc.parse_table(m.group("name"), body,
                             path, text.count("\n", 0, m.start()) + 1)
        if td:
            td.ordinal = seen.get(td.table, 0)
            seen[td.table] = td.ordinal + 1
            out.append(td)
    return out


@pytest.fixture
def record():
    rec = {}
    for td in _defs(RECORD, path="tools/db/schema/pg_consolidated.sql"):
        rec.setdefault(td.table, td)
    return rec


# ── the body must actually be read ────────────────────────────────────────

class TestTheBodyIsTakenByCountingParens:
    """The first draft used a non-greedy `(.*?)`, which stops at the first `)`
    -- and the first `)` in real DDL is inside a type. It reported ZERO
    definitions across 12,372 sites and looked clean."""

    def test_a_type_with_parens_does_not_end_the_body(self):
        d = _defs("CREATE TABLE t (a VARCHAR(255) NOT NULL, b NUMERIC(12,2));")
        assert len(d) == 1
        assert sorted(d[0].columns) == ["a", "b"]

    def test_a_nested_check_does_not_end_the_body(self):
        d = _defs("CREATE TABLE t (a text CHECK (a IN ('x','y')), b text NOT NULL);")
        assert sorted(d[0].columns) == ["a", "b"]

    def test_a_paren_inside_a_string_literal_closes_nothing(self):
        d = _defs("CREATE TABLE t (a text DEFAULT ')(', b text NOT NULL);")
        assert sorted(d[0].columns) == ["a", "b"]

    def test_an_unclosed_statement_is_skipped_not_guessed(self):
        assert _defs("CREATE TABLE t (a text, b text") == []

    def test_ddl_embedded_in_python_is_found(self):
        src = 'SQL = """\nCREATE TABLE IF NOT EXISTS t (\n  a text NOT NULL\n)\n"""\n'
        assert [d.table for d in _defs(src)] == ["t"]

    def test_a_schema_qualified_name_reduces_to_the_bare_table(self):
        assert _defs("CREATE TABLE public.t (a text);")[0].table == "t"


# ── what counts as required ───────────────────────────────────────────────

class TestRequiredMeansAnInsertMustSupplyIt:

    def test_not_null_without_a_default_is_required(self, record):
        cols = record["widgets"].columns
        assert cols["widget_id"].required and cols["owner"].required

    def test_not_null_WITH_a_default_is_not(self, record):
        """The database fills it. A definition omitting it still takes an
        INSERT written against the record."""
        assert record["widgets"].columns["note"].not_null
        assert not record["widgets"].columns["note"].required

    def test_a_nullable_column_is_not(self, record):
        assert not record["widgets"].columns["label"].required


# ── the finding, and what it refuses to report ────────────────────────────

class TestItReportsOnlyWhatWouldActuallyBreak:

    def test_omitting_a_required_column_is_reported(self, record):
        d = _defs("CREATE TABLE widgets (widget_id text, owner text);")
        offs = sdc.find_offences(d, record)
        assert len(offs) == 1
        assert offs[0].missing_required == ("created_at",)
        assert "omits NOT NULL created_at" in offs[0].reason()

    def test_omitting_only_NULLABLE_columns_is_NOT_reported(self, record):
        """A fixture needing three of forty columns is doing the right thing.
        A gate that demanded all forty would be switched off in a week."""
        d = _defs("CREATE TABLE widgets ("
                  "widget_id text NOT NULL, owner text NOT NULL, created_at text NOT NULL);")
        assert sdc.find_offences(d, record) == []

    def test_omitting_a_defaulted_column_is_NOT_reported(self, record):
        d = _defs("CREATE TABLE widgets ("
                  "widget_id text NOT NULL, owner text NOT NULL, created_at text NOT NULL);")
        assert sdc.find_offences(d, record) == []

    def test_a_table_the_record_does_not_name_is_NOT_reported(self, record):
        """No schema of record, nothing to compare against. Silence is the
        honest answer, not a guess about which definition is canonical."""
        assert sdc.find_offences(_defs("CREATE TABLE unknown_t (a text);"), record) == []

    def test_the_record_never_reports_itself(self, record):
        d = _defs(RECORD, path="tools/db/schema/pg_consolidated.sql")
        assert sdc.find_offences(d, record) == []


class TestPrimaryKeyDisagreement:

    def test_a_contradicting_key_is_reported(self):
        rec = {t.table: t for t in _defs(
            "CREATE TABLE t (a text PRIMARY KEY, b text);",
            path="tools/db/schema/pg_consolidated.sql")}
        offs = sdc.find_offences(_defs("CREATE TABLE t (a text, b text PRIMARY KEY);"), rec)
        assert len(offs) == 1 and offs[0].pk_conflict

    def test_a_key_the_record_does_not_state_is_NOT_a_conflict(self):
        """pg_dump writes the key as a separate ALTER TABLE, so the record's
        inline pk is empty for most tables. Reporting against an empty
        expectation mislabelled 493 of 584 findings."""
        rec = {t.table: t for t in _defs(
            "CREATE TABLE t (a text NOT NULL, b text);",
            path="tools/db/schema/pg_consolidated.sql")}
        offs = sdc.find_offences(_defs("CREATE TABLE t (a text NOT NULL PRIMARY KEY, b text);"), rec)
        assert offs == [], "an unstated key cannot be contradicted"


# ── the census identity ───────────────────────────────────────────────────

class TestTheSiteIdentitySurvivesUnrelatedEdits:
    """A census keyed on `path:line` renames every entry below any edit, so a
    one-line change reports a screenful of new drift and the gate gets turned
    off. This is the property that makes it liveable."""

    def test_the_site_carries_no_line_number(self):
        d = _defs("\n\n\nCREATE TABLE t (a text);")[0]
        assert d.line > 1
        assert str(d.line) not in d.site
        assert d.site == "tests/fake.py::t"

    def test_two_definitions_of_one_table_in_one_file_stay_distinct(self):
        """`init_db.py` files here carry the same DDL twice — the pair must be
        tellable apart without the line number."""
        d = _defs("CREATE TABLE t (a text);\nCREATE TABLE t (a text, b text);")
        assert [x.site for x in d] == ["tests/fake.py::t", "tests/fake.py::t#1"]


# ── the gate itself ───────────────────────────────────────────────────────

class TestTheGate:

    def test_the_committed_census_matches_the_tree(self):
        """`--check` is what CI runs; this is the same question asked here so a
        drift lands as a named test failure and not only as a red job."""
        assert sdc.main(["--check"]) == 0

    def test_the_census_file_is_present_and_parses(self):
        assert sdc.CENSUS_FILE.exists()
        entries = sdc.load_census()
        assert entries, "an empty census would make the gate vacuous"
        assert all("::" in e for e in entries)

    def test_every_census_entry_is_still_a_real_site(self):
        """The census only shrinks, so a stale entry is not merely untidy: it
        is a slot a NEW offender can occupy silently."""
        live = {o.site for o in sdc.find_offences(sdc.scan(), sdc.record_schema())}
        stale = sorted(set(sdc.load_census()) - live)
        assert not stale, (
            f"{len(stale)} census entr(y/ies) no longer drift — run "
            f"`python tools/ci/schema_drift_census.py --write`: {stale[:5]}"
        )

    def test_a_missing_schema_of_record_is_exit_2_not_a_clean_pass(self, monkeypatch, tmp_path):
        """Cannot-run must never read as clean. A census whose anchor vanished
        reporting zero offenders is the exact silent failure it exists to
        close."""
        monkeypatch.setattr(sdc, "RECORD_SQL", tmp_path / "nope.sql")
        assert sdc.main(["--check"]) == 2
