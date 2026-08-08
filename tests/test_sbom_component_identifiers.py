#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-05 — Component Identifiers: all of them, not one.

The 2026 SBOM Minimum Elements require at least one common software identifier
per component and ALL of them where more than one exists. These tests pin the
three things the acceptance criteria name:

1. every component carries at least one identifier, and all derivable ones;
2. CPE is present wherever derivable — it is the NVD lookup key;
3. the multi-identifier structure round-trips through the validator AND
   through ``sbom_components.identifiers_json``.

The round-trip through the column is a real write against a real database, not
a serialize/deserialize of the same in-memory object: a column that does not
exist, or an INSERT that silently fails, must fail this file.
"""

import json
import sqlite3
import uuid

import pytest

from tools.compliance.sbom_identifiers import (
    ICDEV_SBOM_NAMESPACE,
    IDENTIFIER_TYPES,
    apply_identifiers_to_cyclonedx,
    component_id,
    derive_cpe,
    derive_commit_hash,
    derive_identifiers,
    identifiers_from_json,
    identifiers_to_json,
    parse_identifiers_from_cyclonedx,
    split_cpe,
    validate_identifier,
    validate_identifiers,
    validate_sbom_identifiers,
)


def _types(identifiers):
    return {entry["type"] for entry in identifiers}


def _value(identifiers, id_type):
    for entry in identifiers:
        if entry["type"] == id_type:
            return entry["value"]
    return None


PYPI = {
    "type": "library",
    "name": "requests",
    "version": "2.31.0",
    "purl": "pkg:pypi/requests@2.31.0",
    "group": "",
}

MAVEN = {
    "type": "library",
    "name": "log4j-core",
    "version": "2.14.1",
    "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1",
    "group": "org.apache.logging.log4j",
}

NPM_SCOPED = {
    "type": "library",
    "name": "core",
    "version": "7.24.0",
    "purl": "pkg:npm/%40babel/core@7.24.0",
    "group": "@babel",
}

GOLANG_PSEUDO = {
    "type": "library",
    "name": "github.com/spf13/cobra",
    "version": "v0.0.0-20191109021931-daa7c04131f5",
    "purl": "pkg:golang/github.com/spf13/cobra@v0.0.0-20191109021931-daa7c04131f5",
    "group": "",
}

NUGET = {
    "type": "library",
    "name": "Newtonsoft.Json",
    "version": "13.0.3",
    "purl": "pkg:nuget/Newtonsoft.Json@13.0.3",
    "group": "",
}


# ---------------------------------------------------------------------------
# 1. Every component carries at least one identifier; all derivable ones ship
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("component", [PYPI, MAVEN, NPM_SCOPED, GOLANG_PSEUDO, NUGET])
def test_every_component_carries_multiple_identifiers(component):
    identifiers = derive_identifiers(component)
    assert len(identifiers) > 1, "the 2026 element wants ALL identifiers, not one"
    assert {"purl", "cpe", "uuid", "organization"} <= _types(identifiers)


def test_component_with_no_purl_and_no_version_still_has_an_identifier():
    """The at-least-one rule has to hold for the worst input the parsers emit."""
    bare = {"type": "library", "name": "mystery-lib", "version": "unspecified", "group": ""}
    identifiers = derive_identifiers(bare)

    assert identifiers, "no identifier at all violates the minimum element"
    assert validate_identifiers(identifiers)["valid"]
    # purl is absent because the parser produced none — nothing is fabricated.
    assert "purl" not in _types(identifiers)
    assert {"cpe", "uuid", "organization"} <= _types(identifiers)


def test_a_component_with_nothing_but_a_name_still_validates():
    identifiers = derive_identifiers({"name": "x"})
    assert validate_identifiers(identifiers)["valid"]


def test_identifiers_are_deterministic_and_deduplicated():
    first = derive_identifiers(PYPI)
    second = derive_identifiers(dict(PYPI))
    assert first == second

    # A parser that already attached the purl must not produce a duplicate.
    with_existing = dict(PYPI)
    with_existing["identifiers"] = [{"type": "purl", "value": PYPI["purl"]}]
    assert derive_identifiers(with_existing) == first


def test_identifiers_are_emitted_in_a_fixed_order():
    identifiers = derive_identifiers(GOLANG_PSEUDO)
    order = [IDENTIFIER_TYPES.index(entry["type"]) for entry in identifiers]
    assert order == sorted(order)


def test_uuid_is_a_deterministic_rfc_9562_v5_uuid():
    value = _value(derive_identifiers(PYPI), "uuid")
    parsed = uuid.UUID(value)
    assert parsed.version == 5
    assert parsed == uuid.uuid5(ICDEV_SBOM_NAMESPACE, "/requests@2.31.0")


def test_organization_identifier_matches_the_component_primary_key():
    """The org identifier, the bom-ref and sbom_components.id are one value."""
    from tools.compliance.sbom_generator import _generate_bom_ref

    org = _value(derive_identifiers(PYPI), "organization")
    assert org.endswith(component_id(PYPI))
    assert _generate_bom_ref(PYPI) == component_id(PYPI)


# ---------------------------------------------------------------------------
# 2. CPE — present wherever derivable, and shaped for an NVD join
# ---------------------------------------------------------------------------


def test_cpe_is_a_well_formed_13_field_2_3_string():
    cpe = derive_cpe(PYPI)
    fields = split_cpe(cpe)
    assert len(fields) == 13
    assert fields[:3] == ["cpe", "2.3", "a"]
    assert fields[4] == "requests"
    assert fields[5] == "2.31.0"
    assert validate_identifier({"type": "cpe", "value": cpe}) is None


def test_cpe_vendor_comes_from_the_reverse_dns_maven_group():
    assert split_cpe(derive_cpe(MAVEN))[3] == "apache"


def test_cpe_vendor_comes_from_the_npm_scope():
    assert split_cpe(derive_cpe(NPM_SCOPED))[3] == "babel"


def test_cpe_vendor_and_product_come_from_the_go_module_path():
    fields = split_cpe(derive_cpe(GOLANG_PSEUDO))
    assert fields[3] == "spf13"
    assert fields[4] == "cobra"


def test_cpe_splits_a_dotted_nuget_id_into_vendor_and_product():
    fields = split_cpe(derive_cpe(NUGET))
    assert fields[3] == "newtonsoft"
    assert fields[4] == "json"


def test_unknown_vendor_is_the_any_wildcard_not_a_guess():
    """A guessed vendor narrows an NVD join and loses CVEs; ``*`` widens it."""
    assert split_cpe(derive_cpe(PYPI))[3] == "*"


@pytest.mark.parametrize("unresolved", ["unspecified", "managed", "", "unknown"])
def test_unresolved_versions_become_the_any_wildcard(unresolved):
    """The literal string 'unspecified' in a CPE version would match nothing."""
    cpe = derive_cpe({"type": "library", "name": "widget", "version": unresolved})
    assert split_cpe(cpe)[5] == "*"


def test_cpe_escapes_characters_outside_the_unreserved_set():
    cpe = derive_cpe({"type": "library", "name": "we:ird", "version": "1.0+build"})
    fields = split_cpe(cpe)
    assert fields[4] == "we\\:ird"
    assert fields[5] == "1.0\\+build"
    # And the escaped colon does not tear the string into 14 fields.
    assert len(fields) == 13
    assert validate_identifier({"type": "cpe", "value": cpe}) is None


def test_cpe_part_tracks_the_component_type():
    assert split_cpe(derive_cpe({"name": "rhel", "type": "os", "version": "9"}))[2] == "o"
    assert split_cpe(derive_cpe({"name": "bmc", "type": "firmware", "version": "1"}))[2] == "h"


def test_cpe_is_none_when_there_is_no_product_name():
    assert derive_cpe({"type": "library", "name": "", "version": "1.0"}) is None


# ---------------------------------------------------------------------------
# Intrinsic identifiers — derived only where real, never fabricated
# ---------------------------------------------------------------------------


def test_go_pseudo_version_yields_a_commit_hash_but_not_a_swhid():
    """An abbreviated 12-hex hash is a real commit id but not a valid SWHID."""
    identifiers = derive_identifiers(GOLANG_PSEUDO)
    assert _value(identifiers, "commit_hash") == "daa7c04131f5"
    assert "swhid" not in _types(identifiers)


def test_a_full_revision_yields_a_swhid():
    sha = "b" * 40
    identifiers = derive_identifiers({**PYPI, "commit_hash": sha})
    assert _value(identifiers, "swhid") == f"swh:1:rev:{sha}"
    assert validate_identifiers(identifiers)["valid"]


def test_a_release_version_yields_no_commit_hash():
    assert derive_commit_hash(PYPI) is None


def test_omnibor_is_passed_through_and_never_invented():
    assert "omnibor" not in _types(derive_identifiers(PYPI))

    gitoid = "gitoid:blob:sha256:" + "c" * 64
    identifiers = derive_identifiers({**PYPI, "gitoid": gitoid})
    assert _value(identifiers, "omnibor") == gitoid
    assert validate_identifiers(identifiers)["valid"]


def test_a_bare_sha256_gitoid_is_normalised_to_the_gitoid_uri():
    identifiers = derive_identifiers({**PYPI, "gitoid": "d" * 64})
    assert _value(identifiers, "omnibor") == "gitoid:blob:sha256:" + "d" * 64


# ---------------------------------------------------------------------------
# 3a. Round-trip through CycloneDX and the validator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec_version", ["1.4", "1.5", "1.6", "1.7"])
def test_identifiers_round_trip_through_cyclonedx_on_every_spec_version(spec_version):
    component = {**PYPI, "commit_hash": "a" * 40, "gitoid": "gitoid:blob:sha256:" + "e" * 64}
    identifiers = derive_identifiers(component)
    assert len(_types(identifiers)) >= 6

    cdx = apply_identifiers_to_cyclonedx({"name": "requests", "version": "2.31.0"}, identifiers, spec_version)
    assert parse_identifiers_from_cyclonedx(cdx) == identifiers


def test_native_cyclonedx_fields_are_used_where_the_spec_has_them():
    identifiers = derive_identifiers({**PYPI, "commit_hash": "a" * 40})

    on_16 = apply_identifiers_to_cyclonedx({}, identifiers, "1.6")
    assert on_16["purl"] == PYPI["purl"]
    assert on_16["cpe"].startswith("cpe:2.3:a:")
    assert on_16["swhid"] == ["swh:1:rev:" + "a" * 40]

    # 1.4 has no swhid field, so it must fall back to a property rather than
    # silently drop the identifier.
    on_14 = apply_identifiers_to_cyclonedx({}, identifiers, "1.4")
    assert "swhid" not in on_14
    assert {"icdev:identifier:swhid"} <= {p["name"] for p in on_14["properties"]}
    assert parse_identifiers_from_cyclonedx(on_14) == parse_identifiers_from_cyclonedx(on_16)


def test_a_second_cpe_is_carried_as_a_property_not_dropped():
    extra = "cpe:2.3:a:python:requests:2.31.0:*:*:*:*:*:*:*"
    identifiers = derive_identifiers({**PYPI, "identifiers": [{"type": "cpe", "value": extra}]})
    assert len([e for e in identifiers if e["type"] == "cpe"]) == 2

    cdx = apply_identifiers_to_cyclonedx({}, identifiers, "1.6")
    assert parse_identifiers_from_cyclonedx(cdx) == identifiers


def test_validator_rejects_a_component_with_no_identifiers():
    result = validate_identifiers([], component_label="ghost@1.0")
    assert not result["valid"]
    assert "ghost@1.0" in result["errors"][0]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "purl", "value": "requests@2.31.0"},
        {"type": "cpe", "value": "cpe:2.3:a:vendor:product"},
        {"type": "cpe", "value": "cpe:2.2:a:v:p:1:*:*:*:*:*:*:*"},
        {"type": "uuid", "value": "not-a-uuid"},
        {"type": "swhid", "value": "swh:1:rev:tooshort"},
        {"type": "omnibor", "value": "gitoid:blob:md5:deadbeef"},
        {"type": "commit_hash", "value": "zzzzzzz"},
        {"type": "organization", "value": "icdev component 1"},
        {"type": "purl", "value": ""},
    ],
)
def test_validator_rejects_malformed_identifiers(bad):
    assert validate_identifier(bad) is not None
    assert not validate_identifiers([bad])["valid"]


def test_sbom_level_validation_reports_cpe_coverage():
    components = []
    for source in (PYPI, MAVEN, GOLANG_PSEUDO):
        cdx = {"name": source["name"], "version": source["version"]}
        components.append(apply_identifiers_to_cyclonedx(cdx, derive_identifiers(source), "1.6"))

    report = validate_sbom_identifiers({"components": components})
    assert report["valid"], report["errors"]
    assert report["component_count"] == 3
    assert report["components_with_cpe"] == 3
    assert report["components_with_multiple_identifiers"] == 3
    assert report["identifier_totals"]["cpe"] == 3


def test_sbom_level_validation_fails_on_an_identifier_free_component():
    report = validate_sbom_identifiers({"components": [{"name": "orphan", "version": "1.0"}]})
    assert not report["valid"]
    assert report["components_with_cpe"] == 0


# ---------------------------------------------------------------------------
# 3b. Round-trip through sbom_components.identifiers_json — a real write
# ---------------------------------------------------------------------------


def test_identifiers_json_envelope_round_trips():
    identifiers = derive_identifiers(MAVEN)
    assert identifiers_from_json(identifiers_to_json(identifiers)) == identifiers


@pytest.mark.parametrize("empty", ["{}", "", None, "[]", "not json"])
def test_identifiers_json_tolerates_the_column_default_and_junk(empty):
    """The column default is '{}' — parsing it must yield an empty set."""
    assert identifiers_from_json(empty) == []


SBOM_COMPONENTS_DDL = """
CREATE TABLE sbom_components (
    id                   TEXT    PRIMARY KEY,
    component_name       TEXT    NOT NULL,
    version              TEXT,
    vendor               TEXT,
    component_type       TEXT    CHECK(component_type IN (
                                     'library', 'framework', 'container', 'os',
                                     'firmware', 'device', 'application', 'service', 'other')),
    purl                 TEXT,
    license              TEXT,
    classification       TEXT    NOT NULL DEFAULT 'CUI',
    created_at           TEXT    DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT    DEFAULT CURRENT_TIMESTAMP,
    producer             TEXT,
    hash_value           TEXT,
    hash_algorithm       TEXT,
    identifiers_json     TEXT    NOT NULL DEFAULT '{}',
    unknown_fields_json  TEXT    NOT NULL DEFAULT '{}',
    withheld_fields_json TEXT    NOT NULL DEFAULT '{}',
    tenant_id            TEXT
);
"""


class _ParamShim:
    """Translate the generator's %s placeholders for a raw sqlite3 connection.

    tests/conftest.py forces the SQLite backend, and storage.get_connection
    does this translation in production. Doing it here keeps this test a real
    write against a real table without dragging in the whole storage layer —
    and without letting the test assert its own no-op.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._conn.commit()


@pytest.fixture
def components_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "sbom.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SBOM_COMPONENTS_DDL)
    yield conn
    conn.close()


def test_identifiers_round_trip_through_the_sbom_components_column(components_db):
    """Write every derivable identifier to the real column and read it back."""
    from tools.compliance.sbom_generator import _persist_components

    sources = [
        {**PYPI},
        {**MAVEN},
        {**GOLANG_PSEUDO},
        {**NPM_SCOPED, "commit_hash": "f" * 40},
    ]
    expected = {}
    for source in sources:
        source["identifiers"] = derive_identifiers(source)
        expected[component_id(source)] = source["identifiers"]

    written = _persist_components(_ParamShim(components_db), sources)
    assert written == len(sources)

    rows = components_db.execute(
        "SELECT id, component_name, purl, identifiers_json FROM sbom_components"
    ).fetchall()
    assert len(rows) == len(sources)

    for row in rows:
        stored = identifiers_from_json(row["identifiers_json"])
        assert stored == expected[row["id"]]
        assert validate_identifiers(stored, row["component_name"])["valid"]
        # The whole point of the element: more than one identifier survives.
        assert len(stored) > 1
        assert any(entry["type"] == "cpe" for entry in stored)


def test_persisting_twice_updates_in_place_rather_than_duplicating(components_db):
    from tools.compliance.sbom_generator import _persist_components

    first = {**PYPI, "identifiers": derive_identifiers(PYPI)}
    _persist_components(_ParamShim(components_db), [first])

    upgraded = {**PYPI, "version": "2.32.0", "purl": "pkg:pypi/requests@2.32.0"}
    upgraded["identifiers"] = derive_identifiers(upgraded)
    _persist_components(_ParamShim(components_db), [upgraded])

    rows = components_db.execute("SELECT id, version, identifiers_json FROM sbom_components").fetchall()
    # Different coordinates are a different component — that is the catalog
    # semantics of sbom_components, which has no per-document key.
    assert len(rows) == 2

    # Re-running the generator on unchanged coordinates must not duplicate.
    _persist_components(_ParamShim(components_db), [dict(first)])
    assert components_db.execute("SELECT COUNT(*) FROM sbom_components").fetchone()[0] == 2


def test_persisted_json_is_valid_json_with_the_documented_envelope(components_db):
    from tools.compliance.sbom_generator import _persist_components

    source = {**PYPI, "identifiers": derive_identifiers(PYPI)}
    _persist_components(_ParamShim(components_db), [source])

    raw = components_db.execute("SELECT identifiers_json FROM sbom_components").fetchone()[0]
    payload = json.loads(raw)
    assert set(payload) == {"identifiers"}
    assert all(set(entry) == {"type", "value"} for entry in payload["identifiers"])


def test_components_without_identifiers_are_not_persisted(components_db):
    from tools.compliance.sbom_generator import _persist_components

    assert _persist_components(_ParamShim(components_db), [{**PYPI}]) == 0
    assert components_db.execute("SELECT COUNT(*) FROM sbom_components").fetchone()[0] == 0


def test_unknown_component_type_is_recorded_as_other_not_rejected(components_db):
    """component_type is CHECK-constrained; an odd type must not lose the row."""
    from tools.compliance.sbom_generator import _persist_components

    odd = {"type": "plugin", "name": "weird", "version": "1.0", "group": ""}
    odd["identifiers"] = derive_identifiers(odd)
    _persist_components(_ParamShim(components_db), [odd])

    row = components_db.execute("SELECT component_type FROM sbom_components").fetchone()
    assert row["component_type"] == "other"


# ---------------------------------------------------------------------------
# End-to-end through the generator's document builder
# ---------------------------------------------------------------------------


def test_generated_sbom_document_passes_the_identifier_validator():
    from tools.compliance.sbom_generator import _build_cyclonedx_sbom

    components = [dict(PYPI), dict(MAVEN), dict(GOLANG_PSEUDO), dict(NUGET)]
    sbom, count = _build_cyclonedx_sbom({"id": "p1", "name": "Proj"}, components, spec_version="1.6")

    assert count == 4
    report = validate_sbom_identifiers(sbom)
    assert report["valid"], report["errors"]
    assert report["components_with_cpe"] == 4
    assert report["components_with_multiple_identifiers"] == 4

    # The builder also leaves the identifier list on the component dicts so the
    # generator can persist them — that link is what makes the DB round-trip work.
    assert all(c.get("identifiers") for c in components)


def test_generated_sbom_still_carries_purl_at_the_top_level_of_a_component():
    """Existing consumers read component['purl']; that must not have moved."""
    from tools.compliance.sbom_generator import _build_cyclonedx_sbom

    sbom, _ = _build_cyclonedx_sbom({"id": "p1", "name": "Proj"}, [dict(PYPI)])
    assert sbom["components"][0]["purl"] == PYPI["purl"]
    assert sbom["components"][0]["cpe"].startswith("cpe:2.3:a:")


def test_scoped_npm_packages_get_a_valid_purl(tmp_path):
    """Regression found by the identifier validator, not by inspection.

    ``@babel/core`` was encoded ``pkg:npm/@babel%2Fcore@7.24.0`` — the ``/``
    namespace separator escaped and the reserved ``@`` left bare, i.e. exactly
    backwards. PURL is one of the identifiers the 2026 element names, so an
    invalid one fails the element outright.
    """
    from tools.compliance.sbom_generator import _parse_package_json, _parse_package_lock_json

    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps({"dependencies": {"@babel/core": "^7.24.0", "express": "4.19.2"}}), encoding="utf-8"
    )
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps({"packages": {"node_modules/@babel/core": {"version": "7.24.0"}}}), encoding="utf-8"
    )

    purls = {c["purl"] for c in _parse_package_json(manifest)}
    purls |= {c["purl"] for c in _parse_package_lock_json(lock)}

    assert "pkg:npm/%40babel/core@7.24.0" in purls
    assert "pkg:npm/express@4.19.2" in purls
    for purl in purls:
        assert validate_identifier({"type": "purl", "value": purl}) is None, purl


def test_go_mod_require_block_does_not_emit_a_phantom_component(tmp_path):
    """Regression found while validating identifiers end to end.

    ``^require\\s+(\\S+)\\s+(\\S+)`` matches across the newline after
    ``require (``, so a require BLOCK produced an extra component named "("
    whose version was the first module path. It then acquired a full identifier
    set — a phantom component is worse once every component is identified.
    """
    from tools.compliance.sbom_generator import _parse_go_mod

    go_mod = tmp_path / "go.mod"
    go_mod.write_text(
        "module example.com/app\n\ngo 1.22\n\n"
        "require (\n\tgithub.com/spf13/cobra v1.8.0\n\tgithub.com/pkg/errors v0.9.1\n)\n\n"
        "require github.com/stretchr/testify v1.9.0\n",
        encoding="utf-8",
    )

    components = _parse_go_mod(go_mod)
    assert sorted(c["name"] for c in components) == [
        "github.com/pkg/errors",
        "github.com/spf13/cobra",
        "github.com/stretchr/testify",
    ]
    assert all(c["version"].startswith("v") for c in components)


# ---------------------------------------------------------------------------
# End-to-end through generate_sbom() and the real storage layer
# ---------------------------------------------------------------------------

E2E_DDL = (
    SBOM_COMPONENTS_DDL
    + """
CREATE TABLE projects (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    directory_path TEXT
);
CREATE TABLE sbom_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          TEXT NOT NULL,
    version             TEXT NOT NULL,
    format              TEXT NOT NULL DEFAULT 'cyclonedx',
    file_path           TEXT NOT NULL,
    component_count     INTEGER,
    vulnerability_count INTEGER,
    generated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE audit_trail (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT,
    event_type     TEXT,
    actor          TEXT,
    action         TEXT,
    details        TEXT,
    affected_files TEXT,
    classification TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
)


@pytest.fixture
def e2e_project(tmp_path):
    """A real database plus a real project tree, driven through get_connection.

    This is the check the raw-sqlite fixtures above cannot make: generate_sbom
    goes through tools.db.storage, so the %s placeholders and the ON CONFLICT
    upsert are exercised by the production translation path rather than by a
    shim in this file.
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("requests==2.31.0\nflask>=3.0.0\n", encoding="utf-8")
    (project_dir / "go.mod").write_text(
        "module example.com/app\n\nrequire (\n"
        "\tgithub.com/spf13/cobra v0.0.0-20191109021931-daa7c04131f5\n)\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(E2E_DDL)
    conn.execute(
        "INSERT INTO projects (id, name, directory_path) VALUES (?, ?, ?)",
        ("proj-1", "Proj One", str(project_dir)),
    )
    conn.commit()
    conn.close()
    return db_path, project_dir


def test_generate_sbom_writes_identifiers_all_the_way_to_the_database(e2e_project, tmp_path):
    from tools.compliance.sbom_generator import generate_sbom

    db_path, _ = e2e_project
    out_file = tmp_path / "sbom.cdx.json"

    generate_sbom(
        project_id="proj-1",
        output_path=str(out_file),
        db_path=db_path,
        spec_version="1.6",
    )

    sbom = json.loads(out_file.read_text(encoding="utf-8"))
    report = validate_sbom_identifiers(sbom)
    assert report["valid"], report["errors"]
    assert report["component_count"] == 3
    assert report["components_with_cpe"] == 3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT component_name, purl, identifiers_json FROM sbom_components"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 3, "generate_sbom must persist a row per component"

    by_name = {row["component_name"]: identifiers_from_json(row["identifiers_json"]) for row in rows}
    assert set(by_name) == {"requests", "flask", "github.com/spf13/cobra"}
    for name, stored in by_name.items():
        assert validate_identifiers(stored, name)["valid"]
        assert {"purl", "cpe", "uuid", "organization"} <= _types(stored)

    # The Go pseudo-version's commit hash survived the whole path.
    assert _value(by_name["github.com/spf13/cobra"], "commit_hash") == "daa7c04131f5"

    # And what the document says matches what the database says, component for
    # component — the two round-trips agree.
    from_document = {
        c["name"]: parse_identifiers_from_cyclonedx(c) for c in sbom["components"]
    }
    assert from_document == by_name


def test_generate_sbom_is_idempotent_against_sbom_components(e2e_project, tmp_path):
    """A second run must update the catalog in place, not double it."""
    from tools.compliance.sbom_generator import generate_sbom

    db_path, _ = e2e_project
    for run in (1, 2):
        generate_sbom(
            project_id="proj-1",
            output_path=str(tmp_path / f"sbom-{run}.cdx.json"),
            db_path=db_path,
        )

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM sbom_components").fetchone()[0] == 3
    finally:
        conn.close()
