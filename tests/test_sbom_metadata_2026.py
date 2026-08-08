#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-01 — the SBOM Metadata block of the 2026 SBOM Minimum Elements.

Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)", CISA with
NSA, FBI and 16 international partners, 2026-07-29, v2.1. Gap analysis:
docs/compliance/sbom-2026-minimum-elements-gap-analysis.md sections 1.1 and 3.1.

Eight of the nine SBOM Metadata elements are the subject here. The ninth, SBOM Author
Signature, is sbx-sig-01 and is deliberately not asserted — a test that demanded it
would fail for work this card was never scoped to do.

Three of these pin specific defects the gap analysis named:

  * SBOM Tool Version was the literal "1.0.0", a constant that could never change and
    so identified no particular code delivery.
  * SBOM Timestamp was strftime("...Z"), which stamps a literal Z onto whatever clock
    it is handed.
  * SBOM Version was two counters that disagreed — the document always said 1 while
    sbom_records counted 1.0, 2.0, 3.0.
"""

import ast
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.compliance import sbom_generator as sbom
from tools.db.storage import get_connection

REPO_ROOT = Path(__file__).resolve().parent.parent

# RFC 9557 section 4.1 date-time, in the unsuffixed RFC 3339 profile CycloneDX
# requires: YYYY-MM-DDThh:mm:ss followed by Z or a numeric offset.
RFC_9557_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

PROJECT = {"id": "sbx-test", "name": "SBX Test Project"}


def _document(**kwargs):
    """Build a document straight from the builder, no database involved."""
    doc, _count = sbom._build_cyclonedx_sbom(PROJECT, [], **kwargs)
    return doc


def _property(doc, name):
    for prop in doc["metadata"].get("properties", []):
        if prop.get("name") == name:
            return prop.get("value")
    return None


# ---------------------------------------------------------------------------
# SBOM Tool Name and SBOM Tool Version
# ---------------------------------------------------------------------------


def test_tool_version_equals_the_package_version():
    """SBOM Tool Version must be derived from the delivery, not authored by hand."""
    from icdev._version import __version__ as package_version

    assert sbom._get_tool_version() == package_version

    tool = _document()["metadata"]["tools"][0]
    assert tool["name"] == sbom.SBOM_TOOL_NAME
    assert tool["version"] == package_version, "the emitted tool version is not the package version"


@pytest.mark.parametrize(
    "path",
    [
        REPO_ROOT / "tools" / "compliance" / "sbom_generator.py",
        REPO_ROOT / "icdev" / "tools" / "compliance" / "sbom_generator.py",
    ],
    ids=["root", "mirror"],
)
def test_the_literal_1_0_0_tool_version_is_gone(path):
    """The hardcoded "1.0.0" must not come back in either copy of the module.

    Asserting only that the emitted value equals the package version would still pass
    on the day the package itself ships 1.2.42 -> 1.0.0, so the literal is checked in
    the source too. Read from the AST rather than the text: docstrings and comments
    name the old value on purpose and must stay free to do so.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "1.0.0" and id(node) not in docstrings
    ]
    assert not offenders, f'{path.name} carries a literal "1.0.0" version at line(s) {offenders}'


def test_tool_version_falls_back_to_unknown_not_to_a_placeholder(monkeypatch):
    """When no version source resolves, the standard requires the author to say unknown."""

    def _no_source(*_args, **_kwargs):
        raise RuntimeError("no version source")

    monkeypatch.setattr(sbom.Path, "read_text", _no_source)
    monkeypatch.setitem(__import__("sys").modules, "icdev._version", None)
    monkeypatch.setattr("importlib.metadata.version", _no_source)

    assert sbom._get_tool_version() == "unknown"


# ---------------------------------------------------------------------------
# SBOM Timestamp — RFC 9557
# ---------------------------------------------------------------------------


def test_timestamp_parses_under_rfc9557():
    """The emitted timestamp must parse as an RFC 9557 date-time in UTC."""
    stamp = _document()["metadata"]["timestamp"]

    assert RFC_9557_DATE_TIME.match(stamp), f"{stamp!r} is not an RFC 9557 date-time"

    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "the timestamp carries no offset"
    assert parsed.utcoffset() == timedelta(0), "the timestamp is not UTC"

    # Round-trips: formatting the parsed value reproduces the string exactly.
    assert sbom._rfc9557_timestamp(parsed) == stamp


def test_timestamp_rejects_a_naive_datetime():
    """A naive clock must raise rather than be stamped Z and silently mislabelled."""
    with pytest.raises(ValueError, match="timezone-aware"):
        sbom._rfc9557_timestamp(datetime(2026, 8, 8, 3, 42, 20))


def test_timestamp_converts_a_non_utc_datetime_instead_of_relabelling_it():
    """The defect this replaces: strftime wrote Z onto whatever offset it was given."""
    tokyo = timezone(timedelta(hours=9))
    moment = datetime(2026, 8, 8, 12, 0, 0, tzinfo=tokyo)

    assert sbom._rfc9557_timestamp(moment) == "2026-08-08T03:00:00Z"


# ---------------------------------------------------------------------------
# SBOM Author
# ---------------------------------------------------------------------------


def test_author_is_an_entity_and_not_the_tool():
    """metadata.tools[].vendor is the tool vendor; the standard says that is not the author."""
    author = _document()["metadata"]["authors"][0]["name"]

    assert author.strip()
    assert author != sbom.SBOM_TOOL_NAME
    assert author != sbom.SBOM_TOOL_VENDOR
    # "Full names, no acronyms unless official" — the default spells the name out.
    assert " " in author


def test_author_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(sbom.SBOM_AUTHOR_ENV, "Defense Information Systems Agency")
    assert _document()["metadata"]["authors"][0]["name"] == "Defense Information Systems Agency"


def test_an_explicit_author_argument_outranks_the_environment(monkeypatch):
    monkeypatch.setenv(sbom.SBOM_AUTHOR_ENV, "From The Environment")
    doc = _document(sbom_author="United States Space Force")
    assert doc["metadata"]["authors"][0]["name"] == "United States Space Force"


# ---------------------------------------------------------------------------
# SBOM Data Format Name and Version
# ---------------------------------------------------------------------------


def test_data_format_name_is_cyclonedx():
    assert _document()["bomFormat"] == sbom.SBOM_DATA_FORMAT_NAME == "CycloneDX"


def test_the_default_spec_version_is_no_longer_the_deprecated_1_4():
    """1.4 is a 2022 spec; the standard says deprecated data-format versions should not be used."""
    assert sbom.CYCLONEDX_SPEC_VERSION != "1.4"
    assert sbom._spec_version_tuple(sbom.CYCLONEDX_SPEC_VERSION) >= (1, 5)
    assert sbom.CYCLONEDX_SPEC_VERSION in sbom.CYCLONEDX_SUPPORTED_VERSIONS

    doc = _document()
    assert doc["specVersion"] == sbom.CYCLONEDX_SPEC_VERSION
    assert doc["$schema"] == sbom.CYCLONEDX_SUPPORTED_VERSIONS[sbom.CYCLONEDX_SPEC_VERSION]
    assert sbom.CYCLONEDX_SCHEMA == sbom.CYCLONEDX_SUPPORTED_VERSIONS[sbom.CYCLONEDX_SPEC_VERSION]


@pytest.mark.parametrize("spec_version", ["1.4", "1.5", "1.6", "1.7"])
def test_every_spec_version_stays_selectable(spec_version):
    """Raising the default must not remove a version a downstream reader is pinned to."""
    doc = _document(spec_version=spec_version)
    assert doc["specVersion"] == spec_version
    assert doc["$schema"] == sbom.CYCLONEDX_SUPPORTED_VERSIONS[spec_version]


# ---------------------------------------------------------------------------
# SBOM Generation Context
# ---------------------------------------------------------------------------


def test_generation_context_is_before_build():
    """The generator reads source manifests and never opens an artifact."""
    assert _property(_document(), "icdev:sbom-generation-context") == "before build"


@pytest.mark.parametrize("spec_version", ["1.5", "1.6", "1.7"])
def test_lifecycles_carry_the_context_where_the_spec_supports_it(spec_version):
    doc = _document(spec_version=spec_version)
    assert doc["metadata"]["lifecycles"] == [{"phase": "pre-build"}]


def test_cyclonedx_1_4_omits_lifecycles_but_keeps_the_property():
    """metadata.lifecycles arrived in 1.5; a 1.4 document must not carry a field its schema rejects."""
    doc = _document(spec_version="1.4")
    assert "lifecycles" not in doc["metadata"]
    assert _property(doc, "icdev:sbom-generation-context") == "before build"


# ---------------------------------------------------------------------------
# SBOM Version and serial number
# ---------------------------------------------------------------------------


def test_sbom_version_is_semver_with_major_pinned_to_one():
    for revision, expected in [(1, "1.0.0"), (2, "1.1.0"), (7, "1.6.0")]:
        assert sbom._semver_for_revision(revision) == expected
        assert expected.split(".")[0] == "1"


def test_the_document_integer_and_the_semver_are_one_number():
    doc = _document(document_version=4)
    assert doc["version"] == 4
    assert _property(doc, "icdev:sbom-version") == "1.3.0"
    assert sbom._revision_from_version(_property(doc, "icdev:sbom-version")) == doc["version"]


@pytest.mark.parametrize(
    "stored,revision",
    [("1.0.0", 1), ("1.2.0", 3), ("3.0", 3), ("1.0", 1), ("", 0), (None, 0), ("junk", 0)],
)
def test_revision_parsing_spans_both_version_spellings(stored, revision):
    """Legacy "<N>.0" rows and new "1.<N>.0" rows must both map onto the same counter."""
    assert sbom._revision_from_version(stored) == revision


def test_serial_number_conforms_to_rfc9562():
    serial = _document()["serialNumber"]
    assert serial.startswith("urn:uuid:")

    parsed = uuid.UUID(serial[len("urn:uuid:") :])
    assert parsed.version == 4, "expected the RFC 9562 section 5.4 random UUID"
    # RFC 9562 keeps the variant bits RFC 4122 defined; Python names the constant
    # after the obsoleted RFC.
    assert parsed.variant == uuid.RFC_4122


# ---------------------------------------------------------------------------
# The block as a whole — what sbx-sig-02 will score
# ---------------------------------------------------------------------------

# element name -> how to read it out of the emitted document. SBOM Author Signature
# is absent on purpose: it is sbx-sig-01's element, not this card's.
METADATA_ELEMENT_READERS = {
    "SBOM Author": lambda d: d["metadata"]["authors"][0]["name"],
    "SBOM Data Format Name": lambda d: d["bomFormat"],
    "SBOM Data Format Version": lambda d: d["specVersion"],
    "SBOM Generation Context": lambda d: _property(d, "icdev:sbom-generation-context"),
    "SBOM Timestamp": lambda d: d["metadata"]["timestamp"],
    "SBOM Tool Name": lambda d: d["metadata"]["tools"][0]["name"],
    "SBOM Tool Version": lambda d: d["metadata"]["tools"][0]["version"],
    "SBOM Version": lambda d: _property(d, "icdev:sbom-version"),
}


@pytest.mark.parametrize("element", sorted(METADATA_ELEMENT_READERS))
def test_every_in_scope_metadata_element_resolves(element):
    """Each of the eight elements sbx-fld-01 owns must read out non-empty."""
    value = METADATA_ELEMENT_READERS[element](_document())
    assert value is not None and str(value).strip(), f"{element} is absent or empty"


# ---------------------------------------------------------------------------
# Document / sbom_records agreement — the disagreement this card exists to remove
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


@pytest.fixture
def sbom_db(tmp_path, monkeypatch):
    """A database with projects + sbom_records, carrying the 2026 metadata columns.

    The base tables come from init_icdev_db.SCHEMA_SQL and the added columns from
    sbom_generator.SBOM_RECORD_2026_COLUMNS, so neither can drift away from the code
    under test. Goes through storage.get_connection so DML meets the same %s -> ?
    translation production does.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "icdev.db"

    conn = get_connection(db_path=str(db_path))
    for table in ("projects", "sbom_records", "audit_trail"):
        conn.execute(_table_ddl(table))
    for column in sbom.SBOM_RECORD_2026_COLUMNS:
        conn.execute(f"ALTER TABLE sbom_records ADD COLUMN {column} TEXT")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("flask==3.0.0\nrequests>=2.31.0\n", encoding="utf-8")

    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
        ("sbx-test", "SBX Test Project", "api", str(project_dir)),
    )
    conn.commit()
    conn.close()

    yield db_path


def _generate(db_path, tmp_path, name):
    out_file = tmp_path / name
    sbom_generator_path = sbom.generate_sbom(
        project_id="sbx-test", output_path=str(out_file), db_path=db_path
    )
    return json.loads(Path(sbom_generator_path).read_text(encoding="utf-8"))


def _row(db_path):
    conn = get_connection(db_path=str(db_path))
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM sbom_records WHERE project_id = %s ORDER BY id", ("sbx-test",)
            ).fetchall()
        ]
    finally:
        conn.close()


def test_document_sbom_version_and_the_sbom_records_row_agree(sbom_db, tmp_path):
    """The two counters that used to disagree must now be the same number."""
    first = _generate(sbom_db, tmp_path, "first.cdx.json")
    rows = _row(sbom_db)
    assert len(rows) == 1
    assert first["version"] == 1
    assert _property(first, "icdev:sbom-version") == "1.0.0"
    assert rows[0]["version"] == "1.0.0"
    assert rows[0]["sbom_version"] == "1.0.0"

    second = _generate(sbom_db, tmp_path, "second.cdx.json")
    rows = _row(sbom_db)
    assert len(rows) == 2
    assert second["version"] == 2
    assert _property(second, "icdev:sbom-version") == "1.1.0"
    assert rows[1]["version"] == "1.1.0"
    assert rows[1]["sbom_version"] == "1.1.0"

    # Both spellings of every row round-trip back onto the document integer.
    for row, document in [(rows[0], first), (rows[1], second)]:
        assert sbom._revision_from_version(row["sbom_version"]) == document["version"]


def test_the_persisted_metadata_matches_the_emitted_document(sbom_db, tmp_path):
    """Every 2026 metadata element in the document is the one recorded in the row."""
    document = _generate(sbom_db, tmp_path, "meta.cdx.json")
    row = _row(sbom_db)[0]

    assert row["sbom_author"] == document["metadata"]["authors"][0]["name"]
    assert row["data_format_name"] == document["bomFormat"]
    assert row["data_format_version"] == document["specVersion"]
    assert row["generation_context"] == _property(document, "icdev:sbom-generation-context")
    assert row["tool_name"] == document["metadata"]["tools"][0]["name"]
    assert row["tool_version"] == document["metadata"]["tools"][0]["version"]
    assert row["serial_number"] == document["serialNumber"]


def test_legacy_float_versions_keep_the_revision_counter_monotonic(sbom_db, tmp_path):
    """A project whose rows predate this card must not restart at 1.0.0."""
    conn = get_connection(db_path=str(sbom_db))
    for legacy in ("1.0", "2.0", "3.0"):
        conn.execute(
            "INSERT INTO sbom_records (project_id, version, format, file_path) VALUES (%s, %s, %s, %s)",
            ("sbx-test", legacy, "cyclonedx", f"/legacy/{legacy}.json"),
        )
    conn.commit()
    conn.close()

    document = _generate(sbom_db, tmp_path, "after-legacy.cdx.json")

    assert document["version"] == 4
    assert _property(document, "icdev:sbom-version") == "1.3.0"
    assert _row(sbom_db)[-1]["version"] == "1.3.0"


def test_a_database_without_the_2026_columns_still_records_a_row(tmp_path, monkeypatch, capsys):
    """A checkout that has not run the migration must degrade loudly, not silently."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "old.db"

    conn = get_connection(db_path=str(db_path))
    for table in ("projects", "sbom_records", "audit_trail"):
        conn.execute(_table_ddl(table))  # no ALTER: the pre-migration shape
    project_dir = tmp_path / "old-project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
        ("sbx-test", "SBX Test Project", "api", str(project_dir)),
    )
    conn.commit()
    conn.close()

    document = _generate(db_path, tmp_path, "old.cdx.json")

    rows = _row(db_path)
    assert len(rows) == 1, "the row must still be written"
    assert rows[0]["version"] == "1.0.0"
    assert _property(document, "icdev:sbom-version") == "1.0.0"

    warning = capsys.readouterr().err
    assert "sbom_records is missing" in warning
    assert "sbom_author" in warning
