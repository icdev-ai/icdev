#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-06 — Component Name alternates, and Component Version unknown-marking.

Two field fixes from the 2026 SBOM Minimum Elements, pinned together because
they are the two halves of "say what you actually know about this component":

**Component Name (minor update)** — the data format must allow MULTIPLE entries
so a component known by more than one name is listed under all of them. ICDEV
emitted one name: whichever spelling its parser's normalization produced. The
alternates it already had in hand — the producer's own spelling, the
fully-qualified coordinate its ``name``/``group`` split destroys, the short form
of a path-shaped Go module — were dropped. These tests pin that they are emitted,
that they survive a round trip through the document, and that a *withheld* name
carries none of them.

**Component Version (major update)** — a version the producer did not supply must
be marked UNKNOWN. ICDEV wrote the literals ``"unspecified"`` and, for a Maven
dependency whose version lives in a parent POM, ``"managed"``. Neither is
machine-interpretable, and both collide with the Explicitly Identifying Unknown
Information element, which separates "we could not establish it" from "we are
withholding it". There is a test per parser that used to emit one — the
acceptance criterion names each — plus an end-to-end assertion that no legacy
literal survives anywhere in a generated document.
"""

import json
import sqlite3

import pytest

from tools.compliance.component_names import (
    NAME_DECLARED,
    NAME_KINDS,
    NAME_QUALIFIED,
    NAME_SHORT,
    PROPERTY_ALTERNATE_PREFIX,
    all_names,
    apply_names_to_cyclonedx,
    derive_names,
    name_properties,
    names_from_json,
    names_to_json,
    normalize_pypi,
    parse_names_from_cyclonedx,
    purl_ecosystem,
    purl_name,
    validate_names,
    validate_sbom_names,
)
from tools.compliance.sbom_generator import (
    _parse_cargo_toml,
    _parse_csproj,
    _parse_package_json,
    _parse_pom_xml,
    _parse_pyproject_toml,
    _parse_requirements_txt,
)
from tools.compliance.unknown_information import (
    FIELD_NAME,
    FIELD_VERSION,
    LEGACY_SENTINELS,
    REASON_CLASSIFICATION_RESTRICTED,
    REASON_DECLARED_WITHOUT_VERSION,
    REASON_NOT_PROVIDED_BY_PRODUCER,
    REASON_VERSION_MANAGED_BY_PARENT,
    UNKNOWN,
    Disclosure,
    is_legacy_sentinel,
)

#: The two literals the task exists to remove. Kept explicit rather than read
#: from LEGACY_SENTINELS so that shrinking that frozenset cannot quietly shrink
#: what this file checks for.
BANNED_VERSIONS = ("unspecified", "managed")


def _alt(names, kind):
    """The alternate of one kind, or None."""
    for entry in names["alternates"]:
        if entry["kind"] == kind:
            return entry["name"]
    return None


def _by_name(components, name):
    for component in components:
        if component["name"] == name:
            return component
    raise AssertionError(f"no component named {name!r} in {[c['name'] for c in components]}")


# ===========================================================================
# Component Version — one test per parser that used to emit a placeholder
# ===========================================================================


def test_requirements_txt_unpinned_dependency_is_unknown_not_unspecified(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("requests==2.31.0\nflask\n", encoding="utf-8")

    components = _parse_requirements_txt(path)

    flask = _by_name(components, "flask")
    assert flask["version"] == UNKNOWN
    assert flask["version_unknown_reason"] == REASON_DECLARED_WITHOUT_VERSION
    assert flask["version"] not in BANNED_VERSIONS
    # A version nobody stated is not a version segment of the purl either.
    assert flask["purl"] == "pkg:pypi/flask"
    # The pinned one is untouched.
    assert _by_name(components, "requests")["version"] == "2.31.0"


def test_pyproject_toml_unpinned_dependency_is_unknown_not_unspecified(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\ndependencies = ["requests>=2.31.0", "click"]\n',
        encoding="utf-8",
    )

    components = _parse_pyproject_toml(path)

    click = _by_name(components, "click")
    assert click["version"] == UNKNOWN
    assert click["version_unknown_reason"] == REASON_DECLARED_WITHOUT_VERSION
    assert click["purl"] == "pkg:pypi/click"


def test_package_json_wildcard_dependency_is_unknown_not_unspecified(tmp_path):
    path = tmp_path / "package.json"
    path.write_text(
        json.dumps({"dependencies": {"lodash": "*", "express": "^4.18.2"}}),
        encoding="utf-8",
    )

    components = _parse_package_json(path)

    lodash = _by_name(components, "lodash")
    assert lodash["version"] == UNKNOWN
    assert lodash["version_unknown_reason"] == REASON_DECLARED_WITHOUT_VERSION
    assert lodash["purl"] == "pkg:npm/lodash"
    assert _by_name(components, "express")["version"] == "4.18.2"


def test_cargo_toml_versionless_dependency_is_unknown_not_unspecified(tmp_path):
    path = tmp_path / "Cargo.toml"
    path.write_text(
        '[dependencies]\nserde = "1.0.196"\nrand = ""\n'
        'local_crate = { path = "../local" }\n',
        encoding="utf-8",
    )

    components = _parse_cargo_toml(path)

    for name in ("rand", "local_crate"):
        component = _by_name(components, name)
        assert component["version"] == UNKNOWN, name
        assert component["version_unknown_reason"] == REASON_DECLARED_WITHOUT_VERSION
        assert component["purl"] == f"pkg:cargo/{name}"
    assert _by_name(components, "serde")["version"] == "1.0.196"


def test_csproj_package_reference_without_a_version_is_unknown_not_unspecified(tmp_path):
    path = tmp_path / "App.csproj"
    path.write_text(
        "<Project>\n  <ItemGroup>\n"
        '    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n'
        # Central Package Management: the version lives in Directory.Packages.props.
        '    <PackageReference Include="Serilog" />\n'
        "  </ItemGroup>\n</Project>\n",
        encoding="utf-8",
    )

    components = _parse_csproj(path)

    serilog = _by_name(components, "Serilog")
    assert serilog["version"] == UNKNOWN
    assert serilog["version_unknown_reason"] == REASON_DECLARED_WITHOUT_VERSION
    assert serilog["purl"] == "pkg:nuget/Serilog"
    assert _by_name(components, "Newtonsoft.Json")["version"] == "13.0.3"


def test_pom_xml_dependency_managed_version_is_unknown_with_its_own_reason(tmp_path):
    path = tmp_path / "pom.xml"
    path.write_text(
        "<project><dependencies>\n"
        "<dependency><groupId>org.apache.logging.log4j</groupId>"
        "<artifactId>log4j-core</artifactId><version>2.20.0</version></dependency>\n"
        "<dependency><groupId>com.example</groupId>"
        "<artifactId>managed-lib</artifactId></dependency>\n"
        "</dependencies></project>\n",
        encoding="utf-8",
    )

    components = _parse_pom_xml(path)

    managed = _by_name(components, "managed-lib")
    assert managed["version"] == UNKNOWN
    assert managed["version"] not in BANNED_VERSIONS
    # "managed" was never even an unknown: it means the version is resolvable
    # from the parent POM, which is a different fact from "nobody pinned one",
    # and it is the fact an operator can act on.
    assert managed["version_unknown_reason"] == REASON_VERSION_MANAGED_BY_PARENT
    assert managed["purl"] == "pkg:maven/com.example/managed-lib"
    assert _by_name(components, "log4j-core")["version"] == "2.20.0"


def test_the_two_unknown_version_reasons_are_not_the_same_reason(tmp_path):
    """A declared-without-a-version and a parent-managed version differ."""
    assert REASON_DECLARED_WITHOUT_VERSION != REASON_VERSION_MANAGED_BY_PARENT


def test_ai_bom_generator_no_longer_writes_the_unspecified_literal(tmp_path):
    from tools.security.ai_bom_generator import AIBOMGenerator

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("torch\ntransformers==4.38.0\n", encoding="utf-8")

    components = AIBOMGenerator()._scan_requirements(project_dir)

    by_name = {c["component_name"]: c for c in components}
    assert "torch" in by_name, "torch is a known AI framework and must be scanned"
    assert by_name["torch"]["version"] == UNKNOWN
    assert by_name["torch"]["version"] not in BANNED_VERSIONS
    assert by_name["transformers"]["version"] == "4.38.0"


def test_ai_bom_risk_scoring_still_reads_a_pre_2026_stored_literal():
    """Rows written before this change still say "unspecified"; they still score high."""
    from tools.security.ai_bom_generator import AIBOMGenerator

    generator = AIBOMGenerator()
    legacy = {"component_type": "library", "provider": "", "component_name": "x", "version": "unspecified"}
    current = {"component_type": "library", "provider": "", "component_name": "x", "version": UNKNOWN}

    assert generator._assess_risk(legacy) == "high"
    assert generator._assess_risk(current) == "high"


def test_the_banned_literals_are_recognised_as_legacy_sentinels():
    """The validator's vocabulary knows both, so a regression is reported not ignored."""
    for literal in BANNED_VERSIONS:
        assert literal in LEGACY_SENTINELS
        assert is_legacy_sentinel(literal)
    assert not is_legacy_sentinel(UNKNOWN)


def _resolved(project_dir):
    """Every component `resolve_project` produces, through the production wiring.

    `resolve_project` takes the declared-manifest parsers as an argument — an
    ecosystem with no lockfile resolves to nothing without them — and
    `generate_sbom` supplies `DECLARED_PARSERS`. A test that omits them would
    exercise a path the generator never takes.
    """
    from tools.compliance.dependency_resolver import resolve_project
    from tools.compliance.sbom_generator import DECLARED_PARSERS

    return resolve_project(project_dir, declared_parsers=DECLARED_PARSERS)["components"]


# ===========================================================================
# dependency_resolver — the path every declared manifest takes to an SBOM
# ===========================================================================


def test_adopting_a_declared_manifest_keeps_its_unknown_version_reason(tmp_path):
    """`_adopt_declared` rebuilt each component from a fixed field list.

    That dropped `version_unknown_reason`, and a declared manifest is the only
    route those parsers take to a real document — so every declared unknown
    arrived flattened to "nobody pinned one", losing the Maven case entirely.
    """
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies>"
        "<dependency><groupId>com.example</groupId>"
        "<artifactId>managed-lib</artifactId></dependency>"
        "</dependencies></project>",
        encoding="utf-8",
    )

    managed = _by_name(_resolved(tmp_path), "managed-lib")
    assert managed["version"] == UNKNOWN
    assert managed["version_unknown_reason"] == REASON_VERSION_MANAGED_BY_PARENT


def test_adopting_a_declared_manifest_carries_the_producers_spelling(tmp_path):
    (tmp_path / "requirements.txt").write_text("Flask_Login==0.6.3\n", encoding="utf-8")

    component = _by_name(_resolved(tmp_path), "flask-login")
    assert component["declared_name"] == "Flask_Login"


def test_a_python_lock_keeps_the_spelling_it_read(tmp_path):
    """`Pipfile.lock` read the producer's spelling and then discarded it."""
    (tmp_path / "Pipfile.lock").write_text(
        json.dumps({"default": {"Flask-Login": {"version": "==0.6.3", "hashes": []}}}),
        encoding="utf-8",
    )

    component = _by_name(_resolved(tmp_path), "flask-login")
    assert component["declared_name"] == "Flask-Login"
    assert _alt(derive_names(component), NAME_DECLARED) == "Flask-Login"


def test_a_component_with_no_recorded_spelling_has_an_empty_declared_name(tmp_path):
    (tmp_path / "go.mod").write_text(
        "module example.com/app\n\nrequire (\n\tgithub.com/spf13/cobra v1.8.0\n)\n",
        encoding="utf-8",
    )

    component = _by_name(_resolved(tmp_path), "github.com/spf13/cobra")
    assert component["declared_name"] == ""
    assert _alt(derive_names(component), NAME_DECLARED) is None


# ===========================================================================
# Component Name — derivation
# ===========================================================================


def test_python_declared_spelling_is_kept_as_an_alternate():
    names = derive_names(
        {
            "name": "flask-login",
            "declared_name": "Flask_Login",
            "purl": "pkg:pypi/flask-login@0.6.3",
            "group": "",
        }
    )

    assert names["primary"] == "flask-login"
    assert _alt(names, NAME_DECLARED) == "Flask_Login"
    assert "flask-login" not in [entry["name"] for entry in names["alternates"]]


def test_npm_scoped_package_regains_its_qualified_name():
    """`name`/`group` splits `@babel/core` apart; the element wants it back."""
    names = derive_names(
        {"name": "core", "group": "@babel", "purl": "pkg:npm/%40babel/core@7.24.0"}
    )

    assert names["primary"] == "core"
    assert _alt(names, NAME_QUALIFIED) == "@babel/core"


def test_maven_coordinate_is_written_with_a_colon_not_a_slash():
    names = derive_names(
        {
            "name": "log4j-core",
            "group": "org.apache.logging.log4j",
            "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.20.0",
        }
    )

    assert _alt(names, NAME_QUALIFIED) == "org.apache.logging.log4j:log4j-core"


def test_go_module_path_yields_its_short_name():
    names = derive_names(
        {"name": "github.com/spf13/cobra", "purl": "pkg:golang/github.com/spf13/cobra@v1.8.0", "group": ""}
    )

    assert names["primary"] == "github.com/spf13/cobra"
    assert _alt(names, NAME_SHORT) == "cobra"


def test_a_derivation_that_reproduces_the_primary_name_is_not_an_alternate():
    names = derive_names(
        {"name": "requests", "declared_name": "requests", "purl": "pkg:pypi/requests@2.31.0", "group": ""}
    )

    assert names["primary"] == "requests"
    assert names["alternates"] == []


def test_alternates_are_deduplicated_by_value():
    """Two derivations producing one string yield one alternate, not two."""
    names = derive_names(
        {"name": "core", "group": "@babel", "declared_name": "@babel/core", "purl": "pkg:npm/%40babel/core@7.24.0"}
    )

    values = [entry["name"] for entry in names["alternates"]]
    assert values.count("@babel/core") == 1
    # The first kind to produce it keeps it, in NAME_KINDS order.
    assert _alt(names, NAME_DECLARED) == "@babel/core"


def test_derivation_is_deterministic():
    component = {
        "name": "flask-login",
        "declared_name": "Flask.Login",
        "purl": "pkg:pypi/flask-login@0.6.3",
        "group": "",
    }
    assert derive_names(component) == derive_names(component)


def test_a_component_with_no_name_derives_nothing():
    names = derive_names({"name": "", "purl": "pkg:pypi/@1.0"})
    assert names["primary"] == ""


def test_every_alternate_kind_is_in_the_declared_vocabulary():
    names = derive_names(
        {
            "name": "core",
            "group": "@babel",
            "declared_name": "Babel_Core",
            "purl": "pkg:npm/%40babel/core@7.24.0",
        }
    )
    for entry in names["alternates"]:
        assert entry["kind"] in NAME_KINDS


def test_all_names_puts_the_primary_first():
    names = derive_names(
        {"name": "core", "group": "@babel", "purl": "pkg:npm/%40babel/core@7.24.0"}
    )
    assert all_names(names)[0] == "core"
    assert "@babel/core" in all_names(names)


def test_pep503_normalization_collapses_every_separator():
    assert normalize_pypi("Ruamel.YAML") == "ruamel-yaml"
    assert normalize_pypi("zope__interface") == "zope-interface"
    assert normalize_pypi("  Flask-Login ") == "flask-login"


def test_purl_helpers_decode_a_scoped_npm_name():
    assert purl_ecosystem("pkg:npm/%40babel/core@7.24.0") == "npm"
    assert purl_name("pkg:npm/%40babel/core@7.24.0") == "core"
    assert purl_ecosystem("pkg:pypi/requests@2.31.0") == "python"
    assert purl_name("pkg:golang/github.com/spf13/cobra@v1.8.0") == "cobra"
    assert purl_name("not-a-purl") == ""


# ===========================================================================
# Component Name — emission, round trip, and the withheld case
# ===========================================================================


def test_alternate_names_round_trip_through_a_cyclonedx_component():
    names = derive_names(
        {
            "name": "core",
            "group": "@babel",
            "declared_name": "Babel_Core",
            "purl": "pkg:npm/%40babel/core@7.24.0",
        }
    )
    cdx = {"type": "library", "name": names["primary"], "version": "7.24.0"}

    apply_names_to_cyclonedx(cdx, names)
    parsed = parse_names_from_cyclonedx(cdx)

    assert parsed["primary"] == names["primary"]
    assert sorted(
        (e["kind"], e["name"]) for e in parsed["alternates"]
    ) == sorted((e["kind"], e["name"]) for e in names["alternates"])


def test_the_carrier_really_does_allow_multiple_entries():
    """The obligation is on the FORMAT: more than one name must fit."""
    names = {
        "primary": "core",
        "alternates": [
            {"name": "@babel/core", "kind": NAME_QUALIFIED},
            {"name": "Babel_Core", "kind": NAME_DECLARED},
        ],
    }
    cdx = {"name": "core"}
    apply_names_to_cyclonedx(cdx, names)

    emitted = [p for p in cdx["properties"] if p["name"].startswith(PROPERTY_ALTERNATE_PREFIX)]
    assert len(emitted) == 2
    assert {p["value"] for p in emitted} == {"@babel/core", "Babel_Core"}


def test_applying_names_does_not_move_the_primary_name():
    """`name` feeds the bom-ref; changing which spelling wins renumbers history."""
    cdx = {"name": "core"}
    apply_names_to_cyclonedx(
        cdx, {"primary": "core", "alternates": [{"name": "@babel/core", "kind": NAME_QUALIFIED}]}
    )
    assert cdx["name"] == "core"


def test_a_component_with_no_alternates_gains_no_properties():
    cdx = {"name": "requests"}
    apply_names_to_cyclonedx(cdx, {"primary": "requests", "alternates": []})
    assert "properties" not in cdx


def test_a_withheld_name_publishes_no_alternate_names():
    """Four other spellings of a redacted name would undo the redaction."""
    disclosure = Disclosure().withheld(FIELD_NAME, REASON_CLASSIFICATION_RESTRICTED)
    names = {"primary": "core", "alternates": [{"name": "@babel/core", "kind": NAME_QUALIFIED}]}

    assert name_properties(names, disclosure) == []

    cdx = {"name": disclosure.value_for(FIELD_NAME, "core")}
    apply_names_to_cyclonedx(cdx, names, disclosure)
    assert "properties" not in cdx
    assert "@babel/core" not in json.dumps(cdx)


def test_an_unknown_name_publishes_no_alternate_names():
    disclosure = Disclosure().unknown(FIELD_NAME, REASON_NOT_PROVIDED_BY_PRODUCER)
    names = {"primary": "core", "alternates": [{"name": "@babel/core", "kind": NAME_QUALIFIED}]}
    assert name_properties(names, disclosure) == []


def test_a_disclosure_that_touches_only_the_version_leaves_names_alone():
    disclosure = Disclosure().unknown(FIELD_VERSION, REASON_DECLARED_WITHOUT_VERSION)
    names = {"primary": "core", "alternates": [{"name": "@babel/core", "kind": NAME_QUALIFIED}]}
    assert len(name_properties(names, disclosure)) == 1


def test_names_round_trip_through_json():
    names = derive_names(
        {"name": "core", "group": "@babel", "declared_name": "Babel_Core", "purl": "pkg:npm/%40babel/core@7.24.0"}
    )
    restored = names_from_json(names_to_json(names), primary=names["primary"])

    assert restored["primary"] == names["primary"]
    assert sorted((e["kind"], e["name"]) for e in restored["alternates"]) == sorted(
        (e["kind"], e["name"]) for e in names["alternates"]
    )


def test_names_json_is_stable_across_runs():
    names = derive_names(
        {"name": "core", "group": "@babel", "declared_name": "Babel_Core", "purl": "pkg:npm/%40babel/core@7.24.0"}
    )
    assert names_to_json(names) == names_to_json(names)


def test_names_from_json_degrades_rather_than_raising():
    for raw in (None, "", "{", "not json", "[]"):
        assert names_from_json(raw, primary="x") == {"primary": "x", "alternates": []}


# ===========================================================================
# Validation
# ===========================================================================


def test_validate_names_rejects_a_nameless_component():
    assert validate_names({"primary": "", "alternates": []}, "c")


def test_validate_names_rejects_an_alternate_that_repeats_the_primary():
    errors = validate_names(
        {"primary": "core", "alternates": [{"name": "core", "kind": NAME_DECLARED}]}, "c"
    )
    assert any("repeats the primary" in e for e in errors)


def test_validate_names_rejects_an_unrecognised_kind():
    errors = validate_names(
        {"primary": "core", "alternates": [{"name": "x", "kind": "nickname"}]}, "c"
    )
    assert any("unrecognised alternate-name kind" in e for e in errors)


def test_validate_names_accepts_a_derived_set():
    names = derive_names(
        {"name": "core", "group": "@babel", "declared_name": "Babel_Core", "purl": "pkg:npm/%40babel/core@7.24.0"}
    )
    assert validate_names(names, "c") == []


# ===========================================================================
# End to end: a real generate_sbom() run over a real database
# ===========================================================================

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
    generated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    classification      TEXT NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT,
    sbom_author         TEXT,
    author_signature    TEXT,
    signature_algorithm TEXT,
    data_format_name    TEXT,
    data_format_version TEXT,
    generation_context  TEXT,
    tool_name           TEXT,
    tool_version        TEXT,
    sbom_version        TEXT,
    serial_number       TEXT,
    supersedes_sbom_id  INTEGER REFERENCES sbom_records(id),
    content_digest      TEXT,
    source_revision     TEXT,
    revision_reason     TEXT
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


@pytest.fixture
def generated_sbom(tmp_path):
    """Drive the real generator over a tree that exercises both halves of the task.

    Every ecosystem here contributes either an alternate name, an unresolved
    version, or both.
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text(
        "requests==2.31.0\nFlask_Login\n", encoding="utf-8"
    )
    (project_dir / "package.json").write_text(
        json.dumps({"dependencies": {"@babel/core": "7.24.0", "lodash": "*"}}),
        encoding="utf-8",
    )
    (project_dir / "pom.xml").write_text(
        "<project><dependencies>"
        "<dependency><groupId>com.example</groupId>"
        "<artifactId>managed-lib</artifactId></dependency>"
        "</dependencies></project>",
        encoding="utf-8",
    )
    (project_dir / "go.mod").write_text(
        "module example.com/app\n\nrequire (\n\tgithub.com/spf13/cobra v1.8.0\n)\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SBOM_COMPONENTS_DDL)
    conn.execute(
        "INSERT INTO projects (id, name, directory_path) VALUES (?, ?, ?)",
        ("proj-1", "Proj One", str(project_dir)),
    )
    conn.commit()
    conn.close()

    from tools.compliance.sbom_generator import generate_sbom

    out_file = tmp_path / "sbom.cdx.json"
    generate_sbom(
        project_id="proj-1",
        output_path=str(out_file),
        db_path=db_path,
        spec_version="1.6",
    )
    return json.loads(out_file.read_text(encoding="utf-8"))


def test_no_legacy_version_literal_survives_anywhere_in_a_generated_document(generated_sbom):
    """The headline acceptance criterion, checked over the serialized bytes."""
    for component in generated_sbom["components"]:
        assert not is_legacy_sentinel(component.get("version")), component
        assert component.get("version") not in BANNED_VERSIONS

    target = generated_sbom["metadata"]["component"]
    assert not is_legacy_sentinel(target.get("version"))


def test_an_unresolved_version_is_the_unknown_sentinel_with_a_reason(generated_sbom):
    unresolved = [c for c in generated_sbom["components"] if c.get("version") == UNKNOWN]
    assert unresolved, "the fixture declares dependencies with no version"

    for component in unresolved:
        reasons = [
            p["value"]
            for p in component.get("properties") or []
            if p["name"] == f"icdev:unknown:{FIELD_VERSION}"
        ]
        assert reasons, f"{component['name']} states no unknown-reason"


def test_a_generated_document_carries_alternate_names(generated_sbom):
    errors, summary = validate_sbom_names(generated_sbom)
    assert errors == []
    assert summary["components_with_alternates"] > 0
    assert summary["alternate_names"] >= summary["components_with_alternates"]


def test_the_scoped_npm_component_carries_its_qualified_name(generated_sbom):
    core = _by_name(generated_sbom["components"], "core")
    names = parse_names_from_cyclonedx(core)
    assert "@babel/core" in all_names(names)


def test_the_go_module_carries_its_short_name(generated_sbom):
    cobra = _by_name(generated_sbom["components"], "github.com/spf13/cobra")
    assert "cobra" in all_names(parse_names_from_cyclonedx(cobra))


def test_the_python_declared_spelling_survives_normalization(generated_sbom):
    flask_login = _by_name(generated_sbom["components"], "flask-login")
    assert "Flask_Login" in all_names(parse_names_from_cyclonedx(flask_login))


def test_alternates_round_trip_out_of_the_generated_document(generated_sbom):
    """What the document says is what a recipient reads back — on every component."""
    for component in generated_sbom["components"]:
        parsed = parse_names_from_cyclonedx(component)
        assert parsed["primary"] == component["name"]
        emitted = [
            p["value"]
            for p in component.get("properties") or []
            if p["name"].startswith(PROPERTY_ALTERNATE_PREFIX)
        ]
        assert [e["name"] for e in parsed["alternates"]] == emitted


def test_the_generated_document_still_passes_the_disclosure_validator(generated_sbom):
    """The legacy-literal check lives here; a regression fails the whole gate."""
    from tools.compliance.unknown_information import validate_sbom_disclosure

    errors, _summary = validate_sbom_disclosure(generated_sbom)
    assert errors == [], errors
