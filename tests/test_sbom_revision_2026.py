#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-prc-02 — Frequency and Accommodation of Updates to SBOM Data.

Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)", CISA with
NSA, FBI and 16 international partners, 2026-07-29, v2.1. Gap analysis:
docs/compliance/sbom-2026-minimum-elements-gap-analysis.md §3.3.

Three things are under test, matching the three acceptance criteria:

  1. Regenerating after a dependency change produces a new SBOM Version linked to its
     predecessor — the supersedes chain, and the content digest that distinguishes a
     substantive revision from a re-issue of identical content.

  2. A correction is a SUCCESSOR, never an edit. The row being corrected keeps every
     value it had; supersession is derived at read time. Proven two ways — by
     comparing the predecessor row before and after, and by recording every SQL
     statement `apply_correction` issues and asserting no UPDATE or DELETE among them.
     The audit trail gains rows and loses none.

  3. The staleness gate and the per-build claim agree, in args/security_gates.yaml and
     in CLAUDE.md. Including the defect that made the old check unable to return
     "satisfied" at all: it subtracted a naive datetime from an aware one, raised
     TypeError on every file, and swallowed it into "stale, age -1".
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.compliance import sbd_assessor
from tools.compliance import sbom_revision as rev
from tools.db.storage import get_connection

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJECT_ID = "sbx-prc-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _table_ddl(table_name):
    """Lift one CREATE TABLE out of the real schema rather than hand-copying it.

    A schema pasted into a test harness is how a test starts passing against a table
    shape production no longer has.
    """
    from tools.db.init_icdev_db import SCHEMA_SQL

    match = re.search(
        rf"^CREATE TABLE IF NOT EXISTS {table_name} \(.*?^\);",
        SCHEMA_SQL,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{table_name} not found in SCHEMA_SQL"
    return match.group(0)


# Columns the two prior sbx migrations added, which this task's code reads. Listed
# here rather than imported so the fixture does not depend on which sbx branches have
# merged; the columns this task itself adds come from the module constant.
_PRIOR_SBX_COLUMNS = ("sbom_version", "serial_number", "author_signature", "signature_algorithm")


@pytest.fixture
def sbom_db(tmp_path, monkeypatch):
    """projects + sbom_records + audit_trail, with the revision columns present.

    sbom_records gains this task's columns from SBOM_RECORD_REVISION_COLUMNS, so the
    fixture cannot drift away from the code under test.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "icdev.db"

    conn = get_connection(db_path=str(db_path))
    for table in ("projects", "sbom_records", "audit_trail"):
        conn.execute(_table_ddl(table))
    for column in _PRIOR_SBX_COLUMNS:
        conn.execute(f"ALTER TABLE sbom_records ADD COLUMN {column} TEXT")
    for column in rev.SBOM_RECORD_REVISION_COLUMNS:
        if column == "supersedes_sbom_id":
            conn.execute("ALTER TABLE sbom_records ADD COLUMN supersedes_sbom_id INTEGER")
        else:
            conn.execute(f"ALTER TABLE sbom_records ADD COLUMN {column} TEXT")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
        (PROJECT_ID, "SBX Revision Test", "api", str(project_dir)),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def conn(sbom_db):
    connection = get_connection(db_path=str(sbom_db))
    yield connection
    connection.close()


def _document(components=(), *, serial="urn:uuid:11111111-1111-4111-8111-111111111111", timestamp="2026-08-08T00:00:00Z", version=1):
    """A minimal CycloneDX-shaped document; only the fields under test matter."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "serialNumber": serial,
        "version": version,
        "metadata": {
            "timestamp": timestamp,
            "properties": [{"name": "icdev:sbom:version", "value": f"1.{version - 1}.0"}],
            "component": {"type": "application", "name": "SBX Revision Test"},
        },
        "components": [{"type": "library", "name": n, "version": v} for n, v in components],
    }


def _insert(conn, **overrides):
    """Insert one sbom_records row directly, so tests can build a chain cheaply."""
    fields = {
        "project_id": PROJECT_ID,
        "version": "1.0.0",
        "format": "cyclonedx",
        "file_path": "/x/sbom.cdx.json",
        "component_count": 1,
        "vulnerability_count": 0,
        "sbom_version": "1.0.0",
    }
    fields.update(overrides)
    columns = list(fields)
    conn.execute(
        f"INSERT INTO sbom_records ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})",
        tuple(fields[c] for c in columns),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM sbom_records WHERE project_id = %s ORDER BY id DESC LIMIT 1", (PROJECT_ID,)
    ).fetchone()
    return row["id"]


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM sbom_records ORDER BY id").fetchall()]


def _audit_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM audit_trail ORDER BY id").fetchall()]


# ---------------------------------------------------------------------------
# Content identity — what makes "the content changed" decidable
# ---------------------------------------------------------------------------


def test_content_digest_ignores_what_changes_on_every_emission():
    """Two SBOMs of the same tree digest the same despite different serial/time/version.

    This is the whole reason the digest is not the file hash: the file hash of two
    SBOMs of one unchanged tree never matches, so it could not answer the question.
    """
    first = _document([("flask", "3.0.0")], serial="urn:uuid:aaaaaaaa-1111-4111-8111-111111111111", timestamp="2026-08-01T00:00:00Z", version=1)
    second = _document([("flask", "3.0.0")], serial="urn:uuid:bbbbbbbb-2222-4222-8222-222222222222", timestamp="2026-08-08T12:34:56Z", version=4)

    assert first != second, "the two documents must actually differ, or this proves nothing"
    assert rev.content_digest(first) == rev.content_digest(second)


def test_content_digest_moves_when_a_component_changes():
    base = _document([("flask", "3.0.0")])
    assert rev.content_digest(base) != rev.content_digest(_document([("flask", "3.0.1")]))
    assert rev.content_digest(base) != rev.content_digest(_document([("flask", "3.0.0"), ("requests", "2.31.0")]))


def test_content_digest_does_not_mutate_its_input():
    doc = _document([("flask", "3.0.0")])
    before = json.dumps(doc, sort_keys=True)
    rev.content_digest(doc)
    assert json.dumps(doc, sort_keys=True) == before


# ---------------------------------------------------------------------------
# SBOM Version bump
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prior,correction,expected",
    [
        (None, False, "1.1.0"),
        ("", False, "1.1.0"),
        ("1.0.0", False, "1.1.0"),
        ("1.4.2", False, "1.5.0"),  # a build bump clears the patch
        ("1.0.0", True, "1.0.1"),
        ("1.4.2", True, "1.4.3"),
        ("3.0", False, "1.3.0"),  # legacy float: revision 3 is semver minor 2
        ("3.0", True, "1.2.1"),
        ("garbage", False, "1.1.0"),
    ],
)
def test_next_sbom_version(prior, correction, expected):
    """A correction is a patch of a published version; a build is a minor bump."""
    assert rev.next_sbom_version(prior, correction=correction) == expected


def test_sbom_version_major_is_pinned_to_one():
    """The standard: the major version of an SBOM following these elements should be 1."""
    assert rev.SBOM_VERSION_MAJOR == 1
    assert rev.next_sbom_version("1.9.9", correction=False).startswith("1.")


# ---------------------------------------------------------------------------
# Frequency: a new build means a new SBOM, linked to its predecessor
# ---------------------------------------------------------------------------


def test_first_sbom_has_no_predecessor(conn):
    plan = rev.plan_revision(conn, PROJECT_ID, _document([("flask", "3.0.0")]), build_id="build-1")
    assert plan["supersedes_sbom_id"] is None
    assert plan["revision_reason"] == rev.REVISION_INITIAL
    assert plan["source_revision"] == "build-1"


def test_regeneration_after_a_dependency_change_links_to_its_predecessor(conn):
    """The first acceptance criterion, end to end at the plan layer."""
    first = _document([("flask", "3.0.0")])
    first_id = _insert(conn, content_digest=rev.content_digest(first), source_revision="build-1", revision_reason=rev.REVISION_INITIAL)

    changed = _document([("flask", "3.0.1")])
    plan = rev.plan_revision(conn, PROJECT_ID, changed, build_id="build-2")

    assert plan["supersedes_sbom_id"] == first_id
    assert plan["content_changed"] is True
    assert plan["revision_reason"] == rev.REVISION_DEPENDENCY_CHANGE
    assert plan["predecessor_digest"] == rev.content_digest(first)
    assert plan["content_digest"] != plan["predecessor_digest"]


def test_reissue_of_identical_content_is_still_a_new_linked_sbom(conn):
    """Frequency requires a new SBOM per build even when nothing changed.

    The reason distinguishes it from a substantive revision; the link is there either
    way, because the successor is still the SBOM now in force.
    """
    doc = _document([("flask", "3.0.0")])
    first_id = _insert(conn, content_digest=rev.content_digest(doc), source_revision="build-1")

    plan = rev.plan_revision(conn, PROJECT_ID, doc, build_id="build-2")
    assert plan["supersedes_sbom_id"] == first_id
    assert plan["content_changed"] is False
    assert plan["revision_reason"] == rev.REVISION_NEW_BUILD


def test_predecessor_without_a_digest_is_treated_as_changed(conn):
    """A row from before this migration cannot be compared, so do not claim a re-issue."""
    _insert(conn, content_digest=None)
    plan = rev.plan_revision(conn, PROJECT_ID, _document([("flask", "3.0.0")]))
    assert plan["content_changed"] is True


def test_plan_revision_rejects_an_unknown_reason(conn):
    with pytest.raises(ValueError, match="Unknown revision reason"):
        rev.plan_revision(conn, PROJECT_ID, _document(), reason="because")


def test_revision_chain_marks_supersession_at_read_time(conn):
    first_id = _insert(conn, sbom_version="1.0.0")
    second_id = _insert(conn, sbom_version="1.1.0", supersedes_sbom_id=first_id)
    third_id = _insert(conn, sbom_version="1.2.0", supersedes_sbom_id=second_id)

    chain = rev.revision_chain(conn, PROJECT_ID)
    by_id = {r["id"]: r for r in chain}

    assert by_id[first_id]["superseded"] is True
    assert by_id[first_id]["superseded_by_id"] == second_id
    assert by_id[first_id]["is_head"] is False
    assert by_id[second_id]["superseded"] is True
    assert by_id[third_id]["superseded"] is False
    assert by_id[third_id]["is_head"] is True
    assert rev.latest_record(conn, PROJECT_ID)["id"] == third_id


def test_superseded_is_derived_not_stored(conn):
    """There is no `superseded` column, and there must not be one.

    Marking a predecessor by writing to it would rewrite a document a recipient may
    already hold — see the module docstring. The mark is computed from the successors'
    forward links instead.
    """
    from tools.db.storage import column_exists

    _insert(conn)
    for forbidden in ("superseded", "superseded_at", "superseded_by", "superseded_by_id"):
        assert not column_exists(conn, "sbom_records", forbidden), (
            f"sbom_records.{forbidden} exists — supersession must stay derived, not stored"
        )
    assert "superseded" in rev.revision_chain(conn, PROJECT_ID)[0]


# ---------------------------------------------------------------------------
# Accommodation of Updates: the correction flow
# ---------------------------------------------------------------------------


class _RecordingConnection:
    """Pass-through wrapper that records every SQL statement executed through it."""

    def __init__(self, inner):
        self._inner = inner
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        return self._inner.execute(sql, params) if params is not None else self._inner.execute(sql)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_correction_inserts_a_successor_and_never_mutates_the_row_it_corrects(conn, tmp_path):
    """The second acceptance criterion.

    Marked, not mutated: the predecessor row is compared field-by-field before and
    after, and every statement the correction issues is inspected for an UPDATE or
    DELETE. The audit trail only grows.
    """
    original = _document([("flask", "3.0.0")])
    original_path = tmp_path / "sbom.cdx.json"
    original_path.write_text(json.dumps(original), encoding="utf-8")
    original_id = _insert(
        conn,
        file_path=str(original_path),
        sbom_version="1.3.0",
        content_digest=rev.content_digest(original),
        source_revision="build-7",
        revision_reason=rev.REVISION_NEW_BUILD,
    )

    before_rows = _rows(conn)
    before_audit = _audit_rows(conn)
    assert len(before_rows) == 1

    corrected = _document([("flask", "3.0.0")])
    corrected["components"][0]["publisher"] = "Pallets Projects"

    recording = _RecordingConnection(conn)
    result = rev.apply_correction(
        recording,
        PROJECT_ID,
        corrected,
        reason="Component Producer was recorded as unknown when it is identifiable",
        corrections=[{"field": "publisher", "component": "flask", "was": None, "now": "Pallets Projects"}],
    )

    # --- a successor row exists and points back at what it corrects ---
    after_rows = _rows(conn)
    assert len(after_rows) == 2
    successor = after_rows[1]
    assert successor["supersedes_sbom_id"] == original_id
    assert successor["revision_reason"] == rev.REVISION_CORRECTION
    assert successor["sbom_version"] == "1.3.1", "a correction is a patch bump of the version it corrects"
    assert result["superseded_sbom_id"] == original_id
    assert result["sbom_version"] == "1.3.1"
    # The legacy counter column does NOT move: a correction is not a new revision of
    # the software, and the generator still derives its next value from this column.
    assert successor["version"] == before_rows[0]["version"]

    # --- the corrected row is byte-for-byte what it was ---
    assert after_rows[0] == before_rows[0], "the SBOM being corrected was mutated"

    # --- and nothing tried to mutate it ---
    for statement in recording.statements:
        head = statement.strip().upper()
        assert not head.startswith("UPDATE"), f"correction issued an UPDATE: {statement}"
        assert not head.startswith("DELETE"), f"correction issued a DELETE: {statement}"

    # --- the audit trail grew and did not change ---
    after_audit = _audit_rows(conn)
    assert after_audit[: len(before_audit)] == before_audit, "audit_trail is append-only"
    new_events = after_audit[len(before_audit) :]
    assert [e["event_type"] for e in new_events] == ["sbom_corrected"]
    details = json.loads(new_events[0]["details"])
    assert details["supersedes_sbom_id"] == original_id
    assert details["corrected_sbom_version"] == "1.3.0"
    assert details["sbom_version"] == "1.3.1"
    assert details["corrections"][0]["field"] == "publisher"
    assert "Component Producer" in details["correction_reason"]

    # --- and the predecessor now reads as superseded, without having been written to ---
    chain = rev.revision_chain(conn, PROJECT_ID)
    assert chain[0]["superseded"] is True
    assert chain[0]["superseded_by_id"] == successor["id"]
    assert chain[1]["is_head"] is True

    # --- the corrected document is on disk, beside the original, not over it ---
    written = Path(result["file_path"])
    assert written.exists()
    assert written != original_path
    assert json.loads(original_path.read_text(encoding="utf-8")) == original
    assert json.loads(written.read_text(encoding="utf-8"))["components"][0]["publisher"] == "Pallets Projects"


def test_correction_records_that_content_changed(conn, tmp_path):
    original = _document([("flask", "3.0.0")])
    _insert(
        conn,
        file_path=str(tmp_path / "sbom.cdx.json"),
        content_digest=rev.content_digest(original),
    )
    corrected = _document([("flask", "3.0.0")])
    corrected["components"][0]["licenses"] = [{"license": {"id": "BSD-3-Clause"}}]

    result = rev.apply_correction(conn, PROJECT_ID, corrected, reason="license was missing")
    assert result["content_changed"] is True
    assert result["content_digest"] != rev.content_digest(original)


def test_detail_discovered_is_a_distinct_reason(conn, tmp_path):
    """The standard splits a correction from newly discovered detail; so does the chain."""
    _insert(conn, file_path=str(tmp_path / "sbom.cdx.json"))
    result = rev.apply_correction(
        conn,
        PROJECT_ID,
        _document([("flask", "3.0.0")]),
        reason="upstream published the component hash after our build",
        reason_code=rev.REVISION_DETAIL_DISCOVERED,
    )
    assert result["revision_reason"] == rev.REVISION_DETAIL_DISCOVERED
    row = _rows(conn)[-1]
    assert row["revision_reason"] == rev.REVISION_DETAIL_DISCOVERED


def test_correction_requires_a_stated_reason(conn, tmp_path):
    """"Recipients may weigh SBOM errors in their risk decisions" — so say what was wrong."""
    _insert(conn, file_path=str(tmp_path / "sbom.cdx.json"))
    with pytest.raises(ValueError, match="must state what was wrong"):
        rev.apply_correction(conn, PROJECT_ID, _document(), reason="   ")


def test_correction_refuses_a_non_corrective_reason_code(conn, tmp_path):
    _insert(conn, file_path=str(tmp_path / "sbom.cdx.json"))
    with pytest.raises(ValueError, match="reason_code must be one of"):
        rev.apply_correction(conn, PROJECT_ID, _document(), reason="x", reason_code=rev.REVISION_NEW_BUILD)


def test_correction_with_nothing_to_correct_is_refused(conn):
    with pytest.raises(ValueError, match="nothing to correct"):
        rev.apply_correction(conn, PROJECT_ID, _document(), reason="x")


def test_a_correction_never_writes_a_semver_into_the_legacy_counter_column(conn, tmp_path):
    """Regression: `CAST('1.1.1' AS REAL)` is a hard error on PostgreSQL.

    The generator derives the next revision from `MAX(CAST(version AS REAL))` until
    sbx-fld-01 replaces that query, so a three-part version in `version` would break
    the *next* generation — on PG only, and nowhere near the code that wrote it.
    """
    _insert(conn, file_path=str(tmp_path / "sbom.cdx.json"), version="2.0", sbom_version=None)
    rev.apply_correction(conn, PROJECT_ID, _document([("a", "1")]), reason="fix")

    for row in _rows(conn):
        assert row["version"].count(".") <= 1, (
            f"sbom_records.version holds {row['version']!r}; the legacy counter column "
            "must stay float-castable while the generator CASTs it"
        )
        float(row["version"])  # what PostgreSQL will attempt


def test_correction_chains(conn, tmp_path):
    """Correcting a correction supersedes the correction, not the original."""
    _insert(conn, file_path=str(tmp_path / "sbom.cdx.json"), sbom_version="1.2.0")
    first = rev.apply_correction(conn, PROJECT_ID, _document([("a", "1")]), reason="first fix")
    second = rev.apply_correction(conn, PROJECT_ID, _document([("a", "2")]), reason="second fix")

    assert first["sbom_version"] == "1.2.1"
    assert second["sbom_version"] == "1.2.2"
    assert second["superseded_sbom_id"] == first["sbom_record_id"]

    chain = rev.revision_chain(conn, PROJECT_ID)
    assert [r["superseded"] for r in chain] == [True, True, False]


def test_the_correction_event_types_are_admitted_by_the_audit_constraint():
    """The failure this pins is invisible: the write is rejected and swallowed.

    `audit_trail.event_type` carries a CHECK generated from
    `tools.audit.audit_logger.VALID_EVENT_TYPES`. Before this task the vocabulary
    had `sbom_generated` and nothing else, so `sbom_corrected` was refused by the
    constraint, the refusal was caught by the writer's best-effort `except`, and
    `apply_correction` reported success having recorded no event at all. Migration
    20260808064841 rebuilds the deployed constraint from the constant.
    """
    from tools.audit.audit_logger import VALID_EVENT_TYPES, event_type_check_sql

    for event_type in ("sbom_revised", "sbom_corrected"):
        assert event_type in VALID_EVENT_TYPES
        assert f"'{event_type}'" in event_type_check_sql()

    migration = REPO_ROOT / "tools" / "db" / "migrations" / "20260808064841_audit_event_types_sbom_revision"
    assert (migration / "up.py").exists(), (
        "adding an event type without a migration that calls rebuild_event_type_constraint "
        "leaves the deployed CHECK rejecting it"
    )
    body = (migration / "up.py").read_text(encoding="utf-8")
    assert "rebuild_event_type_constraint" in body
    for event_type in VALID_EVENT_TYPES:
        assert f"'{event_type}'" not in body, "the migration must derive the list, not respell it"


def test_the_correction_event_is_actually_recorded(conn, tmp_path):
    """End-to-end: an accepted event type means a row, not a warning on stderr."""
    _insert(conn, file_path=str(tmp_path / "sbom.cdx.json"))
    rev.apply_correction(conn, PROJECT_ID, _document([("a", "1")]), reason="producer was wrong")

    events = [e for e in _audit_rows(conn) if e["event_type"] == "sbom_corrected"]
    assert len(events) == 1, "the correction event was rejected by the CHECK and swallowed"


def test_revision_reason_vocabulary_is_a_python_constant():
    """The DDL carries no CHECK against this list; the constant is the vocabulary.

    Same call fnd-02 made for sbom_dependencies.relationship_type. If a CHECK is ever
    added it must be generated from here, not hand-written.
    """
    assert set(rev.REVISION_REASONS_CORRECTIVE) <= set(rev.REVISION_REASONS)
    migration = (
        REPO_ROOT / "tools" / "db" / "migrations" / "20260808063350_sbom_revision_frequency" / "up.sql"
    ).read_text(encoding="utf-8")
    # Comments explain the decision; the DDL statements must not respell it.
    statements = [line for line in migration.splitlines() if line.strip() and not line.lstrip().startswith("--")]
    assert any("revision_reason" in line for line in statements)
    for line in statements:
        assert "CHECK" not in line.upper(), f"revision_reason must not carry a hand-written CHECK: {line}"
    for reason in rev.REVISION_REASONS:
        assert f"'{reason}'" not in migration, f"the vocabulary is respelled in DDL: {reason}"


# ---------------------------------------------------------------------------
# Frequency evaluation — the reconciled gate
# ---------------------------------------------------------------------------


def test_frequency_is_not_satisfied_with_no_sbom(conn):
    result = rev.evaluate_frequency(conn, PROJECT_ID)
    assert result["status"] == "not_satisfied"
    assert result["conditions"] == [rev.CONDITION_MISSING]


def test_frequency_is_satisfied_when_the_sbom_matches_the_current_build(conn):
    _insert(conn, source_revision="deadbeef" * 5, generated_at=datetime.now(timezone.utc).isoformat())
    result = rev.evaluate_frequency(conn, PROJECT_ID, build_id="deadbeef" * 5)
    assert result["status"] == "satisfied"
    assert result["conditions"] == []


def test_frequency_fails_when_the_build_moved_on(conn):
    """The per-build rule, which the 30-day threshold could never express.

    A fresh SBOM for yesterday's build is stale in the only sense that matters here.
    """
    _insert(conn, source_revision="aaaa1111", generated_at=datetime.now(timezone.utc).isoformat())
    result = rev.evaluate_frequency(conn, PROJECT_ID, build_id="bbbb2222")
    assert result["status"] == "not_satisfied"
    assert rev.CONDITION_NOT_CURRENT_BUILD in result["conditions"]
    assert rev.CONDITION_STALE not in result["conditions"], "the file is fresh; only the build is wrong"


def test_frequency_flags_an_unknowable_build_as_partial_not_pass(conn):
    """Unobserved is not the same as conformant, and not the same as a failure."""
    _insert(conn, source_revision=None, generated_at=datetime.now(timezone.utc).isoformat())
    result = rev.evaluate_frequency(conn, PROJECT_ID, build_id="bbbb2222")
    assert result["status"] == "partially_satisfied"
    assert rev.CONDITION_BUILD_UNKNOWN in result["conditions"]


def test_frequency_still_applies_the_age_backstop(conn):
    old = datetime.now(timezone.utc) - timedelta(days=45)
    _insert(conn, source_revision="aaaa1111", generated_at=old.isoformat())
    result = rev.evaluate_frequency(conn, PROJECT_ID, build_id="aaaa1111", max_age_days=30)
    assert result["status"] == "not_satisfied"
    assert result["conditions"] == [rev.CONDITION_STALE]
    assert result["age_days"] >= 45


def test_frequency_reads_the_threshold_from_the_gate_config(conn):
    """The threshold is configuration, and the code must not carry its own copy."""
    old = datetime.now(timezone.utc) - timedelta(days=40)
    _insert(conn, source_revision="aaaa1111", generated_at=old.isoformat())
    assert rev.evaluate_frequency(conn, PROJECT_ID, build_id="aaaa1111")["status"] == "not_satisfied"
    lenient = rev.evaluate_frequency(conn, PROJECT_ID, build_id="aaaa1111", max_age_days=90)
    assert lenient["status"] == "satisfied"


@pytest.mark.parametrize("gate", ["sbd", "swft"])
def test_gate_threshold_actually_reads_the_file(gate):
    """Guard against a loader that only ever returns its own default.

    `sbom_max_age_days` happens to equal DEFAULT_MAX_AGE_DAYS, so asserting 30 on
    a real read and on a total failure to read are the same assertion. Reading a
    key that exists only in the file, and one that exists nowhere, separates them.
    """
    assert rev.gate_threshold("sbom_required_per_build", gate=gate) is True
    assert rev.gate_threshold("no_such_threshold", gate=gate, default="fallback") == "fallback"
    assert rev._load_max_age_days(gate) == 30


def test_frequency_reports_the_superseded_count(conn):
    first = _insert(conn, source_revision="a", generated_at=datetime.now(timezone.utc).isoformat())
    _insert(conn, source_revision="a", supersedes_sbom_id=first, generated_at=datetime.now(timezone.utc).isoformat())
    result = rev.evaluate_frequency(conn, PROJECT_ID, build_id="a")
    assert result["sbom_count"] == 2
    assert result["superseded_count"] == 1


# ---------------------------------------------------------------------------
# SBD-21: the check that enforces it
# ---------------------------------------------------------------------------


def test_sbd21_uses_the_recorded_build_when_a_database_is_available(conn, tmp_path):
    _insert(conn, source_revision="aaaa1111", generated_at=datetime.now(timezone.utc).isoformat())
    result = sbd_assessor._check_sbom_freshness(str(tmp_path), conn=conn, project_id=PROJECT_ID)
    # tmp_path is not a git repo, so build identity is unknown — partial, not a pass.
    assert result["status"] == "partially_satisfied"
    assert rev.CONDITION_BUILD_UNKNOWN in result["details"]


def test_sbd21_file_scan_no_longer_reports_every_sbom_as_age_minus_one(tmp_path):
    """Regression: aware `now` minus naive `utcfromtimestamp` raised TypeError.

    It was swallowed by `except Exception`, every file landed in stale_files with age
    -1, and the check could never return anything but "all stale".
    """
    (tmp_path / "sbom.cdx.json").write_text("{}", encoding="utf-8")
    result = sbd_assessor._check_sbom_freshness(str(tmp_path))
    assert "-1d old" not in result["details"], "the naive/aware subtraction is back"
    assert "0d old" in result["details"]
    assert result["status"] == "partially_satisfied"


def test_sbd21_reports_no_sbom_as_not_satisfied(tmp_path):
    result = sbd_assessor._check_sbom_freshness(str(tmp_path))
    assert result["status"] == "not_satisfied"


def test_sbd21_threshold_comes_from_the_gate_config():
    assert sbd_assessor._load_sbom_max_age_days() == 30


def test_sbd21_is_registered_as_database_aware():
    """The dispatch is an explicit allowlist, so this is the thing that can rot."""
    assert "SBD-21" in sbd_assessor.DB_AWARE_CHECKS
    assert sbd_assessor.AUTO_CHECKS["SBD-21"] is sbd_assessor._check_sbom_freshness


# ---------------------------------------------------------------------------
# The reconciliation itself — config and CLAUDE.md must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [REPO_ROOT / "args" / "security_gates.yaml", REPO_ROOT / "icdev" / "args" / "security_gates.yaml"],
    ids=["root", "mirror"],
)
def test_gate_config_expresses_the_per_build_rule(path):
    """The gate config must state the rule, not only the backstop.

    Note the two nesting shapes: sbd's thresholds sit under the file-level
    `thresholds:` block while swft carries its own. That asymmetry is why
    `gate_threshold` tries both — a loader that guessed one silently returned its
    default, which reads exactly like having found 30 in the file.
    """
    import yaml

    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    sbd_thresholds = config["thresholds"]["sbd"]
    assert sbd_thresholds["sbom_required_per_build"] is True
    assert sbd_thresholds["sbom_max_age_days"] == 30
    assert rev.CONDITION_NOT_CURRENT_BUILD in config["sbd"]["warning"]
    assert rev.CONDITION_STALE in config["sbd"]["warning"]

    swft = config["swft"]
    assert swft["thresholds"]["sbom_required_per_build"] is True
    assert swft["thresholds"]["sbom_max_age_days"] == 30
    assert rev.CONDITION_NOT_CURRENT_BUILD in swft["warning"]


def test_every_condition_the_evaluator_emits_is_declared_in_the_gate_config():
    """A condition string nothing declares is a gate that only looks like one."""
    import yaml

    config = yaml.safe_load((REPO_ROOT / "args" / "security_gates.yaml").read_text(encoding="utf-8"))
    declared = set(config["sbd"]["blocking"]) | set(config["sbd"]["warning"])
    for condition in (rev.CONDITION_NOT_CURRENT_BUILD, rev.CONDITION_STALE, rev.CONDITION_BUILD_UNKNOWN):
        assert condition in declared, f"{condition} is returned by evaluate_frequency but declared nowhere"


def test_claude_md_no_longer_claims_a_gate_it_does_not_have():
    """The bare "SBOM regenerated on every build" line asserted more than was enforced."""
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "- SBOM regenerated on every build; containers non-root" not in text
    assert "sbom_max_age_days" in text and "backstop" in text
    assert "supersedes_sbom_id" in text
    assert "source_revision" in text


def test_gap_analysis_records_both_practices_as_met():
    """The §3.3 conformance rows, not §1.3's restatement of the standard.

    Both sections have a "| Frequency |" row and only one of them is a claim about
    ICDEV, so the search is scoped to the matrix.
    """
    text = (REPO_ROOT / "docs" / "compliance" / "sbom-2026-minimum-elements-gap-analysis.md").read_text(
        encoding="utf-8"
    )
    start = text.index("### 3.3 Practices and Processes")
    matrix = text[start : text.index("## 4.", start)].splitlines()

    for row in ("| Accommodation of Updates |", "| Frequency |"):
        line = next(line for line in matrix if line.startswith(row))
        assert "sbx-prc-02" in line, f"{row.strip('|').strip()} row does not cite sbx-prc-02"
        assert "**MET" in line, f"{row.strip('|').strip()} is still recorded as a gap"

    assert "### 2.9 Frequency and Accommodation of Updates (sbx-prc-02)" in text


# ---------------------------------------------------------------------------
# Mirror parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "tools/compliance/sbom_revision.py",
        "tools/compliance/sbom_generator.py",
        "tools/compliance/sbd_assessor.py",
    ],
)
def test_icdev_mirror_matches_root(relative):
    """Authored in both trees — a drifting mirror is how a feature vanishes from a wheel."""
    root = (REPO_ROOT / relative).read_text(encoding="utf-8")
    mirror = (REPO_ROOT / "icdev" / relative).read_text(encoding="utf-8")
    assert root == mirror, f"icdev/{relative} has drifted from {relative}"


def test_migration_is_mirrored():
    name = "20260808063350_sbom_revision_frequency"
    for filename in ("up.sql", "down.sql", "meta.json"):
        root = (REPO_ROOT / "tools" / "db" / "migrations" / name / filename).read_text(encoding="utf-8")
        mirror = (REPO_ROOT / "icdev" / "tools" / "db" / "migrations" / name / filename).read_text(encoding="utf-8")
        assert root == mirror, f"the migration runner reads its own mirror; {filename} has drifted"


# ---------------------------------------------------------------------------
# Generator integration — the row the generator actually writes
# ---------------------------------------------------------------------------


def test_generator_persists_the_revision_link(sbom_db, tmp_path, monkeypatch):
    """Two generations against one project: the second links back to the first."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_BUILD_ID", "build-alpha")
    from tools.compliance import sbom_generator

    first_path = sbom_generator.generate_sbom(
        project_id=PROJECT_ID, output_path=str(tmp_path / "one.cdx.json"), db_path=sbom_db
    )
    monkeypatch.setenv("ICDEV_BUILD_ID", "build-beta")
    sbom_generator.generate_sbom(
        project_id=PROJECT_ID, output_path=str(tmp_path / "two.cdx.json"), db_path=sbom_db
    )

    connection = get_connection(db_path=str(sbom_db))
    try:
        rows = [dict(r) for r in connection.execute("SELECT * FROM sbom_records ORDER BY id").fetchall()]
        chain = rev.revision_chain(connection, PROJECT_ID)
    finally:
        connection.close()

    assert len(rows) == 2
    assert rows[0]["supersedes_sbom_id"] is None
    assert rows[0]["revision_reason"] == rev.REVISION_INITIAL
    assert rows[0]["source_revision"] == "build-alpha"
    assert rows[1]["supersedes_sbom_id"] == rows[0]["id"]
    assert rows[1]["source_revision"] == "build-beta"
    assert rows[0]["content_digest"] and rows[0]["content_digest"].startswith("sha256:")

    # Same empty project directory both times, so the content is identical — which the
    # digest is what proves, and which makes this a re-issue rather than a revision.
    assert rows[0]["content_digest"] == rows[1]["content_digest"]
    assert rows[1]["revision_reason"] == rev.REVISION_NEW_BUILD

    assert chain[0]["superseded"] is True
    assert chain[1]["is_head"] is True
    assert Path(first_path).exists()


def test_generator_degrades_when_the_migration_has_not_run(tmp_path, monkeypatch, capsys):
    """A database without these columns still records its row, and says what it dropped."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "unmigrated.db"
    connection = get_connection(db_path=str(db_path))
    for table in ("projects", "sbom_records", "audit_trail"):
        connection.execute(_table_ddl(table))
    connection.execute("ALTER TABLE sbom_records ADD COLUMN author_signature TEXT")
    connection.execute("ALTER TABLE sbom_records ADD COLUMN signature_algorithm TEXT")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    connection.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
        (PROJECT_ID, "SBX", "api", str(project_dir)),
    )
    connection.commit()
    connection.close()

    from tools.compliance import sbom_generator

    sbom_generator.generate_sbom(
        project_id=PROJECT_ID, output_path=str(tmp_path / "x.cdx.json"), db_path=db_path
    )

    assert "revision chain has a break in it" in capsys.readouterr().err

    connection = get_connection(db_path=str(db_path))
    try:
        assert connection.execute("SELECT COUNT(*) AS c FROM sbom_records").fetchone()["c"] == 1
    finally:
        connection.close()
