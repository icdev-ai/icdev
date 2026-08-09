#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-01 — the nine SBOM Metadata elements (§1.1).

Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)", CISA with
NSA, FBI and 16 international partners, 2026-07-29, v2.1. Gap analysis:
docs/compliance/sbom-2026-minimum-elements-gap-analysis.md §1.1 and §3.1.

Four things are under test, matching the four acceptance criteria:

  1. The conformance validator (sbx-sig-02, via `sbom_conformance_gate`) scores all
     nine metadata elements as `met` on a generated document. Eight come from the
     document; the ninth, SBOM Author Signature, is sbx-sig-01's detached file, so it
     is scored with one beside the artifact.

  2. The emitted Tool Version equals the package version and is not the literal
     "1.0.0" — checked both on the value and, by reading the source of BOTH copies,
     on the constant ever coming back.

  3. The Timestamp parses under RFC 9557. The old `strftime("...Z")` stamped a
     literal Z onto whatever it was handed, so a non-UTC clock produced a timestamp
     that was wrong and well-formed at once; that case is tested directly.

  4. The document's SBOM Version and the `sbom_records` row agree — driven through a
     real `generate_sbom` against a real database, across three successive
     generations, because the defect was that the document always said 1 while the
     column independently counted 1.0, 2.0, 3.0.
"""

import ast
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.compliance import sbom_conformance_gate as gate
from tools.compliance import sbom_generator as sg
from tools.compliance import sbom_revision as rev
from tools.compliance import spdx_writer as sw
from tools.db.storage import get_connection

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJECT_ID = "sbx-fld-01-test"


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


@pytest.fixture
def sbom_db(tmp_path, monkeypatch):
    """projects + sbom_records + audit_trail + sbom_components, migrated forward.

    The columns this task writes come from `SBOM_RECORD_METADATA_COLUMNS`, so the
    fixture cannot drift away from the code under test: adding an element to the
    constant without adding it to the migration fails here.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv(sg.SBOM_AUTHOR_ENV, raising=False)
    db_path = tmp_path / "icdev.db"

    conn = get_connection(db_path=str(db_path))
    for table in ("projects", "sbom_records", "audit_trail", "sbom_components"):
        conn.execute(_table_ddl(table))

    existing = {row[1] for row in conn.execute("PRAGMA table_info(sbom_records)").fetchall()}
    added = set(existing)
    for column in ("author_signature", "signature_algorithm") + sg.SBOM_RECORD_METADATA_COLUMNS:
        if column not in added:
            conn.execute(f"ALTER TABLE sbom_records ADD COLUMN {column} TEXT")
            added.add(column)
    for column in rev.SBOM_RECORD_REVISION_COLUMNS:
        if column in added:
            continue
        kind = "INTEGER" if column == "supersedes_sbom_id" else "TEXT"
        conn.execute(f"ALTER TABLE sbom_records ADD COLUMN {column} {kind}")
        added.add(column)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")

    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
        (PROJECT_ID, "SBX Metadata Test", "api", str(project_dir)),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def project():
    return {"id": PROJECT_ID, "name": "SBX Metadata Test", "directory_path": None}


@pytest.fixture
def components():
    return [
        {
            "name": "requests",
            "version": "2.31.0",
            "type": "library",
            "purl": "pkg:pypi/requests@2.31.0",
        }
    ]


def _code_strings(copy_path):
    """Every string literal in a copy of the generator, minus the docstrings.

    Read through the AST rather than with grep, so a docstring that *describes* the
    defect — "this was hardcoded 1.0.0", "the query used GLOB" — does not read as the
    defect. The prose is the reason the guard is legible; it must not be its subject.
    """
    tree = ast.parse((REPO_ROOT / copy_path).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def _records(db_path):
    conn = get_connection(db_path=str(db_path))
    try:
        rows = conn.execute(
            "SELECT * FROM sbom_records WHERE project_id = %s ORDER BY id",
            (PROJECT_ID,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# =====================================================================================
# Criterion 1 — the validator scores all nine metadata elements as met
# =====================================================================================


def test_all_nine_metadata_elements_score_met(project, components, tmp_path):
    """The acceptance criterion, in the validator's own terms.

    The ninth element is a detached `<sbom>.sig.json` that sbx-sig-01 writes, which the
    scorer resolves from the artifact path — so the document is put on disk with one
    beside it. Without a path, an SBOM that really is signed scores as unsigned.
    """
    sbom, _count = sg._build_cyclonedx_sbom(project, components)

    out_file = tmp_path / "sbom.cdx.json"
    out_file.write_text(json.dumps(sbom), encoding="utf-8")
    Path(str(out_file) + ".sig.json").write_text("{}", encoding="utf-8")

    scored = gate._score_structural(sbom, sbom_path=out_file)

    gaps = [name for name in gate.METADATA_ELEMENTS if scored["elements"][name] != gate.MET]
    assert gaps == [], f"metadata elements still scored as gaps: {gaps}"
    assert len(gate.METADATA_ELEMENTS) == 9


def test_eight_of_nine_are_met_by_the_document_alone(project, components):
    """Only the signature needs anything outside the document; the rest are in it."""
    sbom, _count = sg._build_cyclonedx_sbom(project, components)
    scored = gate._score_structural(sbom)

    met = [name for name in gate.METADATA_ELEMENTS if scored["elements"][name] == gate.MET]
    assert set(gate.METADATA_ELEMENTS) - set(met) == {"sbom_author_signature"}


# =====================================================================================
# SBOM Author — the entity, explicitly not the tool
# =====================================================================================


def test_sbom_author_is_an_entity_and_not_the_tool_vendor(project, components):
    """metadata.tools[].vendor is the TOOL's vendor and does not satisfy this element.

    The two are emitted as separate statements and the vendor field is left exactly
    as it was, so a reader can tell which is which.
    """
    sbom, _count = sg._build_cyclonedx_sbom(project, components, sbom_author="Acme, Incorporated")
    metadata = sbom["metadata"]

    assert metadata["authors"] == [{"name": "Acme, Incorporated"}]
    assert metadata["tools"][0]["vendor"] == sg.SBOM_TOOL_VENDOR
    assert metadata["tools"][0]["vendor"] != metadata["authors"][0]["name"]


def test_sbom_author_resolution_order(project, components, monkeypatch):
    """Explicit argument, then $ICDEV_SBOM_AUTHOR, then the full-name default."""
    monkeypatch.setenv(sg.SBOM_AUTHOR_ENV, "Environment Entity")
    assert sg._get_sbom_author("Argument Entity") == "Argument Entity"
    assert sg._get_sbom_author(None) == "Environment Entity"
    assert sg._get_sbom_author("   ") == "Environment Entity"

    monkeypatch.delenv(sg.SBOM_AUTHOR_ENV)
    assert sg._get_sbom_author(None) == sg.DEFAULT_SBOM_AUTHOR


def test_the_default_author_is_a_full_name_not_an_acronym():
    """The standard asks for full names, no acronyms unless the acronym is official."""
    assert sg.DEFAULT_SBOM_AUTHOR == "Intelligent Certified Development Platform"
    assert "ICDEV" not in sg.DEFAULT_SBOM_AUTHOR


def test_the_author_reaches_the_spdx_serialization_too(project, components):
    """Both named formats must carry the element, or only one of them conforms."""
    sbom, _count = sg._build_cyclonedx_sbom(project, components, sbom_author="Acme, Incorporated")
    spdx = sw.to_spdx(sbom)

    assert "Organization: Acme, Incorporated" in spdx["creationInfo"]["creators"]
    assert sw.validate_spdx(spdx)["valid"]
    assert sw.compare_element_coverage(sbom, spdx)["parity"]


# =====================================================================================
# Criterion 2 — SBOM Tool Version is derived, and "1.0.0" is gone
# =====================================================================================


def test_tool_version_equals_the_package_version(project, components):
    """The emitted Tool Version identifies this code delivery, not a constant."""
    from icdev._version import __version__ as package_version

    sbom, _count = sg._build_cyclonedx_sbom(project, components)
    emitted = sbom["metadata"]["tools"][0]["version"]

    assert emitted == package_version
    assert emitted != "1.0.0"
    assert sg._get_tool_version() == package_version


def test_the_package_version_and_pyproject_agree():
    """The derivation's first two sources must not disagree, or the element is a coin toss."""
    from icdev._version import __version__ as package_version

    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert declared, "pyproject.toml declares no version"
    assert declared.group(1) == package_version


@pytest.mark.parametrize(
    "copy_path",
    [
        "tools/compliance/sbom_generator.py",
        "icdev/tools/compliance/sbom_generator.py",
    ],
)
def test_the_hardcoded_tool_version_literal_is_gone_from_both_copies(copy_path):
    """A regression guard, not a style check.

    The value was `"version": "1.0.0"` inside the tools[] block — a literal that could
    never change and therefore misidentified every delivery. Both copies are read
    because the icdev/ mirror is what a pip install ships.
    """
    assert "1.0.0" not in _code_strings(copy_path), f"{copy_path} still binds a 1.0.0 literal"


def test_tool_version_falls_back_to_a_stated_unknown(monkeypatch):
    """When no source can answer, the standard requires saying so — not a placeholder.

    Every source is broken at once: the package import, the installed-distribution
    lookup and the pyproject read. The answer must be the string "unknown", never
    something that reads like a real release.
    """
    import builtins

    real_import = builtins.__import__

    def _no_version(name, *args, **kwargs):
        if name in ("icdev._version", "importlib.metadata"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_version)
    monkeypatch.setattr(sg, "BASE_DIR", Path("/nonexistent-sbx-fld-01"))

    assert sg._get_tool_version() == "unknown"


def test_tool_name_and_vendor_are_still_stated(project, components):
    """SBOM Tool Name was already met; deriving the version must not cost it."""
    tool = _build_metadata(project, components)["tools"][0]
    assert tool["name"] == sg.SBOM_TOOL_NAME
    assert tool["vendor"] == sg.SBOM_TOOL_VENDOR


def _build_metadata(project, components, **kwargs):
    sbom, _count = sg._build_cyclonedx_sbom(project, components, **kwargs)
    return sbom["metadata"]


# =====================================================================================
# Criterion 3 — the Timestamp parses under RFC 9557
# =====================================================================================


def test_the_emitted_timestamp_parses_under_rfc_9557(project, components):
    """RFC 9557's Internet-Extended-Date/Time; an unsuffixed RFC 3339 string conforms.

    Parsed, not pattern-matched: a regex would accept "2026-13-45T99:99:99Z".
    """
    timestamp = _build_metadata(project, components)["timestamp"]

    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert timestamp.endswith("Z")
    assert "." not in timestamp  # whole seconds, as SPDX 2.3's `created` requires


def test_the_timestamp_has_no_bracketed_suffix(project, components):
    """RFC 9557 allows `[UTC]`; CycloneDX's `format: date-time` would reject it.

    Conforming to the RFC by breaking both schemas would be a worse answer than the
    one being replaced.
    """
    timestamp = _build_metadata(project, components)["timestamp"]
    assert "[" not in timestamp and "]" not in timestamp


def test_a_non_utc_clock_is_converted_not_relabelled():
    """The defect in the format this replaces, stated as a test.

    `strftime("%Y-%m-%dT%H:%M:%SZ")` stamped a literal Z onto whatever it was handed,
    so 12:00 in UTC+05:00 was published as 12:00Z — five hours wrong, and well-formed.
    """
    aware = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))

    assert aware.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-08-08T12:00:00Z"  # the old bug
    assert sg._rfc9557_timestamp(aware) == "2026-08-08T07:00:00Z"


def test_a_naive_datetime_is_refused():
    """An unlabelled clock cannot be published as UTC; guessing is what caused the bug."""
    with pytest.raises(ValueError, match="timezone-aware"):
        sg._rfc9557_timestamp(datetime(2026, 8, 8, 12, 0, 0))


def test_microseconds_are_dropped_rather_than_emitted():
    aware = datetime(2026, 8, 8, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert sg._rfc9557_timestamp(aware) == "2026-08-08T12:00:00Z"


# =====================================================================================
# SBOM Generation Context — knowable now, and simply unstated before
# =====================================================================================


def test_generation_context_is_stated_in_the_standards_own_term(project, components):
    """The property carries "before build"; only it carries the standard's wording."""
    metadata = _build_metadata(project, components)
    values = {prop["name"]: prop["value"] for prop in metadata["properties"]}

    assert values[sg.PROPERTY_GENERATION_CONTEXT] == "before build"
    assert sg.SBOM_GENERATION_CONTEXT == "before build"


@pytest.mark.parametrize("spec_version", ["1.5", "1.6", "1.7"])
def test_lifecycles_carries_the_context_where_the_spec_has_the_field(project, components, spec_version):
    """CycloneDX's own vocabulary, for a consumer that reads fields and not properties."""
    metadata = _build_metadata(project, components, spec_version=spec_version)
    assert metadata["lifecycles"] == [{"phase": "pre-build"}]


def test_a_1_4_document_omits_lifecycles_and_keeps_the_property(project, components):
    """metadata.lifecycles arrived in 1.5. Emitting it on 1.4 would fail that schema."""
    metadata = _build_metadata(project, components, spec_version="1.4")

    assert "lifecycles" not in metadata
    values = {prop["name"]: prop["value"] for prop in metadata["properties"]}
    assert values[sg.PROPERTY_GENERATION_CONTEXT] == "before build"


# =====================================================================================
# SBOM Data Format Name / Version
# =====================================================================================


def test_data_format_name_and_version_are_stated(project, components):
    sbom, _count = sg._build_cyclonedx_sbom(project, components)
    assert sbom["bomFormat"] == sg.SBOM_DATA_FORMAT_NAME == "CycloneDX"
    assert sbom["specVersion"] == sg.CYCLONEDX_SPEC_VERSION


def test_the_default_data_format_version_is_not_a_deprecated_one():
    """1.4 is a 2022 spec; the standard cites ECMA-424 of December 2025 (1.7)."""
    assert sg.CYCLONEDX_SPEC_VERSION != "1.4"
    assert sg._spec_version_tuple(sg.CYCLONEDX_SPEC_VERSION) >= (1, 6)
    # ...and the older versions stay selectable for consumers pinned to them.
    assert "1.4" in sg.CYCLONEDX_SUPPORTED_VERSIONS


# =====================================================================================
# Criterion 4 — the document's SBOM Version and the sbom_records row agree
# =====================================================================================


def test_document_version_and_record_agree_across_regenerations(sbom_db):
    """The defect: the document always said 1 while the column counted 1.0, 2.0, 3.0.

    Three generations, because the two counters agreed by accident on the first one.
    """
    for expected_revision in (1, 2, 3):
        out = sg.generate_sbom(
            PROJECT_ID,
            db_path=sbom_db,
            output_path=None,
        )
        document = json.loads(Path(out).read_text(encoding="utf-8"))
        rows = _records(sbom_db)

        assert len(rows) == expected_revision
        row = rows[-1]

        # The integer the document carries IS the row's revision, not a constant 1.
        assert document["version"] == expected_revision
        assert row["version"] == f"{expected_revision}.0"

        # ...and the semver spelling is the same value in both places.
        semver = {p["name"]: p["value"] for p in document["metadata"]["properties"]}[
            sg.PROPERTY_SBOM_VERSION
        ]
        assert row["sbom_version"] == semver
        assert semver.startswith(f"{rev.SBOM_VERSION_MAJOR}.")


def test_the_persisted_metadata_elements_match_the_document(sbom_db):
    """Every element the document states is the element the row records."""
    out = sg.generate_sbom(PROJECT_ID, db_path=sbom_db)
    document = json.loads(Path(out).read_text(encoding="utf-8"))
    row = _records(sbom_db)[-1]
    metadata = document["metadata"]

    assert row["sbom_author"] == metadata["authors"][0]["name"]
    assert row["data_format_name"] == document["bomFormat"]
    assert row["data_format_version"] == document["specVersion"]
    assert row["generation_context"] == sg.SBOM_GENERATION_CONTEXT
    assert row["tool_name"] == metadata["tools"][0]["name"]
    assert row["tool_version"] == metadata["tools"][0]["version"]
    assert row["serial_number"] == document["serialNumber"]


def test_a_legacy_float_row_continues_the_counter_rather_than_restarting(sbom_db):
    """An existing project has "1.0", "2.0", "3.0" rows. Revision 4 must follow.

    Restarting at 1 would make two documents claim the same version.
    """
    conn = get_connection(db_path=str(sbom_db))
    for legacy in ("1.0", "2.0", "3.0"):
        conn.execute(
            "INSERT INTO sbom_records (project_id, version, format, file_path, "
            "component_count, vulnerability_count) VALUES (%s, %s, %s, %s, %s, %s)",
            (PROJECT_ID, legacy, "cyclonedx", f"/x/sbom-{legacy}.json", 1, 0),
        )
    conn.commit()
    conn.close()

    out = sg.generate_sbom(PROJECT_ID, db_path=sbom_db)
    document = json.loads(Path(out).read_text(encoding="utf-8"))

    assert document["version"] == 4
    assert _records(sbom_db)[-1]["version"] == "4.0"


@pytest.mark.parametrize(
    "stored,expected",
    [
        (None, 0),
        ("", 0),
        ("1.0", 1),
        ("3.0", 3),
        ("1.0.0", 1),  # semver: revision is minor + 1
        ("1.2.0", 3),
        ("1.2.1", 3),  # a correction does not advance the revision
        ("garbage", 0),
    ],
)
def test_revision_is_read_from_both_spellings(stored, expected):
    """Both column spellings map to one counter, so it stays monotonic across the change."""
    assert sg._revision_of(stored) == expected


def test_the_version_query_is_not_sqlite_dialect():
    """The query this replaced was GLOB + CAST(... AS REAL) on a PostgreSQL-primary DB.

    `translate_sql` rewrites GLOB to a POSIX `~` whose `[0-9]*` matches every string,
    and `CAST('1.0.0' AS REAL)` is a hard error on PostgreSQL — so the counter was
    both wrong and fatal there. It is computed in Python now.
    """
    for copy_path in ("tools/compliance/sbom_generator.py", "icdev/tools/compliance/sbom_generator.py"):
        for literal in _code_strings(copy_path):
            assert "GLOB" not in literal, f"{copy_path} still issues a GLOB predicate"
            assert "AS REAL" not in literal, f"{copy_path} still CASTs a version to REAL"


# =====================================================================================
# Degradation — a database that has not run the sbx-fnd-02 migration
# =====================================================================================


def test_missing_metadata_columns_are_reported_not_fatal(tmp_path, monkeypatch, capsys):
    """The row still lands, and stderr names exactly what could not be persisted.

    Raising here would lose an artifact that is already on disk and that ~25 call
    sites consume; silence would report success while persisting nothing.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv(sg.SBOM_AUTHOR_ENV, raising=False)
    db_path = tmp_path / "legacy.db"

    conn = get_connection(db_path=str(db_path))
    for table in ("projects", "sbom_records", "audit_trail", "sbom_components"):
        conn.execute(_table_ddl(table))
    # The sbx-sig-01 columns are present; this database is behind on sbx-fnd-02 only,
    # which is the case the degradation path exists for.
    for column in ("author_signature", "signature_algorithm"):
        conn.execute(f"ALTER TABLE sbom_records ADD COLUMN {column} TEXT")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
        (PROJECT_ID, "SBX Metadata Test", "api", str(project_dir)),
    )
    conn.commit()
    conn.close()

    out = sg.generate_sbom(PROJECT_ID, db_path=db_path)
    captured = capsys.readouterr()

    # The document is complete regardless of what the schema can hold.
    document = json.loads(Path(out).read_text(encoding="utf-8"))
    assert document["metadata"]["authors"][0]["name"] == sg.DEFAULT_SBOM_AUTHOR

    assert "sbom_author" in captured.err
    assert "migrate.py" in captured.err
    assert _records(db_path)[-1]["version"] == "1.0"


# =====================================================================================
# The two copies stay in sync
# =====================================================================================


def test_the_root_copy_and_the_icdev_mirror_are_identical():
    """`icdev/` is what a pip install ships; a fix that lands in one only is not a fix."""
    root = (REPO_ROOT / "tools/compliance/sbom_generator.py").read_text(encoding="utf-8")
    mirror = (REPO_ROOT / "icdev/tools/compliance/sbom_generator.py").read_text(encoding="utf-8")
    assert root == mirror
