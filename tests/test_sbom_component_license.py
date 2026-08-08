#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-04 — Component License.

The 2026 SBOM Minimum Elements make the Component License element mandatory and give it
four acceptable shapes: an SPDX identifier, a pointer to the full terms such as a URL, a
statement that PROPRIETARY conditions exist, or an explicit statement that the license is
unknown to the author. Its rationale is risk management — incorrect or fraudulent license
information affects an organization's ability to rely on the software or obtain support.

These tests pin the three things that makes true:

1. Every SPDX identifier ICDEV emits is genuinely on the SPDX License List. Anything that
   is not is carried as a license *name* or a URL, never laundered into the document as
   an identifier the recipient cannot resolve.
2. No component can leave the element off — every entry in `components[]`, and the
   document's own target component, carries a license value and a proprietary flag.
3. `sbom_components.license` is populated by a real generator run. That column has been
   dead since migration 209 because the generator never wrote the table at all.
"""

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.compliance import component_licenser as cl  # noqa: E402
from tools.compliance.sbom_generator import (  # noqa: E402
    _build_cyclonedx_sbom,
    _component_row_id,
    _generate_bom_ref,
    _parse_package_lock_json,
    generate_sbom,
)
from tools.compliance.spdx_license_data import (  # noqa: E402
    SPDX_EXCEPTION_IDS,
    SPDX_LICENSE_IDS,
)

ROOT_COPIES = (
    REPO_ROOT / "tools" / "compliance" / "component_licenser.py",
    REPO_ROOT / "tools" / "compliance" / "spdx_license_data.py",
)
MIRROR_COPIES = (
    REPO_ROOT / "icdev" / "tools" / "compliance" / "component_licenser.py",
    REPO_ROOT / "icdev" / "tools" / "compliance" / "spdx_license_data.py",
)


def _properties(cdx_component):
    return {p["name"]: p["value"] for p in cdx_component.get("properties", [])}


# ---------------------------------------------------------------------------
# The vendored SPDX License List
# ---------------------------------------------------------------------------


def test_spdx_list_carries_the_licenses_a_real_manifest_declares():
    """A list missing everyday identifiers would silently downgrade them to names."""
    for identifier in ("MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "GPL-3.0-or-later", "MPL-2.0"):
        assert identifier in SPDX_LICENSE_IDS, f"{identifier} missing from the SPDX License List"
    for exception in ("Classpath-exception-2.0", "LLVM-exception", "GCC-exception-3.1"):
        assert exception in SPDX_EXCEPTION_IDS, f"{exception} missing from the exception list"


def test_license_and_exception_identifiers_are_disjoint():
    """A name in both sets would validate on either side of WITH, which it must not."""
    assert not (SPDX_LICENSE_IDS & SPDX_EXCEPTION_IDS)


def test_identifiers_are_well_formed():
    """An SPDX id has no whitespace — the tokenizer splits on it."""
    for identifier in SPDX_LICENSE_IDS | SPDX_EXCEPTION_IDS:
        assert identifier == identifier.strip() and " " not in identifier, repr(identifier)


def test_case_folded_lookup_covers_every_identifier():
    """Case-insensitive matching must not lose an id to a fold collision."""
    assert len(cl.SPDX_LICENSE_IDS_BY_CASEFOLD) == len(SPDX_LICENSE_IDS)
    assert len(cl.SPDX_EXCEPTION_IDS_BY_CASEFOLD) == len(SPDX_EXCEPTION_IDS)


# ---------------------------------------------------------------------------
# Expression parsing — only real identifiers get through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared,expression",
    [
        ("MIT", "MIT"),
        ("mit", "MIT"),                                   # canonical spelling is emitted
        ("APACHE-2.0", "Apache-2.0"),
        ("(MIT)", "MIT"),                                 # redundant parentheses dropped
        ("MIT OR Apache-2.0", "MIT OR Apache-2.0"),
        ("MIT or Apache-2.0", "MIT OR Apache-2.0"),       # lowercase operators accepted
        ("MIT AND (ISC OR BSD-3-Clause)", "MIT AND (ISC OR BSD-3-Clause)"),
        ("MIT OR ISC AND BSD-3-Clause", "MIT OR ISC AND BSD-3-Clause"),
        ("GPL-2.0-only WITH Classpath-exception-2.0", "GPL-2.0-only WITH Classpath-exception-2.0"),
        ("GPL-2.0+", "GPL-2.0+"),                         # the "or later" operator survives
    ],
)
def test_valid_expressions_canonicalize(declared, expression):
    parsed = cl.parse_spdx_expression(declared)
    assert parsed["valid"], declared
    assert parsed["expression"] == expression


@pytest.mark.parametrize(
    "declared",
    [
        "Bogus-1.0",                       # not on the list
        "MIT WITH Nonexistent-exception",  # exception not on the list
        "MIT WITH Apache-2.0",             # a license is not an exception
        "Classpath-exception-2.0",         # an exception is not a license
        "MIT OR",                          # dangling operator
        "OR MIT",
        "(MIT OR Apache-2.0",              # unbalanced
        "MIT Apache-2.0",                  # missing operator
        "Apache License 2.0",              # a name, not an identifier
        "",
        None,
    ],
)
def test_invalid_expressions_are_rejected(declared):
    assert not cl.parse_spdx_expression(declared)["valid"]


def test_precedence_parenthesizes_only_where_it_changes_meaning():
    """OR binds loosest; re-rendering must not drop a parenthesis that is load-bearing."""
    assert cl.parse_spdx_expression("(MIT OR ISC) AND BSD-3-Clause")["expression"] == (
        "(MIT OR ISC) AND BSD-3-Clause"
    )
    assert cl.parse_spdx_expression("MIT OR (ISC AND BSD-3-Clause)")["expression"] == (
        "MIT OR ISC AND BSD-3-Clause"
    )


# ---------------------------------------------------------------------------
# ACCEPTANCE: every emitted SPDX id validates against the SPDX license list
# ---------------------------------------------------------------------------

#: Declarations drawn from real manifests, including the ones that must NOT become ids.
_DECLARATION_CORPUS = [
    "MIT",
    "mit",
    "Apache-2.0",
    "(MIT OR Apache-2.0)",
    "BSD-3-Clause AND ISC",
    "GPL-2.0-only WITH Classpath-exception-2.0",
    "LicenseRef-Acme-Commercial",
    "Apache License 2.0",
    "Bogus-1.0",
    "https://example.mil/terms",
    "UNLICENSED",
    "SEE LICENSE IN LICENSE.txt",
    "Proprietary",
    "NONE",
    "NOASSERTION",
    "UNKNOWN",
    "",
    None,
    {"type": "MIT", "url": "https://example.mil/mit"},
    [{"type": "MIT"}, {"type": "Apache-2.0"}],
]


@pytest.mark.parametrize("declared", _DECLARATION_CORPUS, ids=lambda d: repr(d)[:40])
def test_every_emitted_spdx_identifier_is_on_the_spdx_license_list(declared):
    """The acceptance criterion: nothing leaves here as an id the recipient cannot resolve."""
    result = cl.resolve_declared_license(declared)
    if result["declaration"] != cl.DECLARATION_SPDX:
        assert not result["spdx_ids"]
        return

    for identifier in result["spdx_ids"]:
        bare = identifier[:-1] if identifier.endswith("+") else identifier
        assert bare in SPDX_LICENSE_IDS or bare in SPDX_EXCEPTION_IDS, (
            f"{identifier!r} was emitted as an SPDX identifier but is not on the list"
        )

    # And the rendered expression contains nothing but list identifiers, custom
    # references and operators.
    for token in result["spdx_expression"].replace("(", " ").replace(")", " ").split():
        if token in ("AND", "OR", "WITH"):
            continue
        bare = token[:-1] if token.endswith("+") else token
        assert (
            bare in SPDX_LICENSE_IDS
            or bare in SPDX_EXCEPTION_IDS
            or cl.is_spdx_license_ref(token)
        ), f"{token!r} appears in an emitted SPDX expression but is not on the list"


@pytest.mark.parametrize("declared", ["Apache License 2.0", "Bogus-1.0", "MIT WITH Nope-1.0"])
def test_an_unrecognized_declaration_becomes_a_name_not_an_identifier(declared):
    """Data is never dropped, but it is never promoted to an SPDX id either."""
    result = cl.resolve_declared_license(declared)
    assert result["declaration"] == cl.DECLARATION_NAME
    assert result["name"] == declared
    assert cl.license_db_value(result) == declared
    assert cl.license_entries(result) == [{"license": {"name": declared}}]


# ---------------------------------------------------------------------------
# ACCEPTANCE: proprietary conditions, URLs, and the explicit unknown marker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared,evidence",
    [
        ("UNLICENSED", cl.EVIDENCE_NPM_UNLICENSED),
        ("SEE LICENSE IN LICENSE.md", cl.EVIDENCE_NPM_SEE_LICENSE_IN),
        ("LicenseRef-Acme-Commercial", cl.EVIDENCE_SPDX_CUSTOM_REF),
        ("DocumentRef-acme:LicenseRef-Internal", cl.EVIDENCE_SPDX_CUSTOM_REF),
        ("NONE", cl.EVIDENCE_NO_LICENSE_GRANTED),
        ("Proprietary", cl.EVIDENCE_VOCABULARY),
        ("Acme Commercial License", cl.EVIDENCE_VOCABULARY),
        ("All Rights Reserved", cl.EVIDENCE_VOCABULARY),
    ],
)
def test_proprietary_conditions_are_flagged_with_their_evidence(declared, evidence):
    result = cl.resolve_declared_license(declared)
    assert result["proprietary"] is True, declared
    assert result["proprietary_evidence"] == evidence
    assert _properties({"properties": cl.license_properties(result)})[cl.PROPERTY_PROPRIETARY] == "true"


@pytest.mark.parametrize("declared", ["MIT", "Apache-2.0 OR MIT", "BUSL-1.1"])
def test_a_published_spdx_license_is_not_flagged_proprietary(declared):
    """False means "no conditions the recipient cannot look up" — not "open source".

    BUSL-1.1 is source-available and restrictive, but it is on the SPDX List, so the
    recipient resolves its terms from the identifier. That is what the element is for.
    """
    result = cl.resolve_declared_license(declared)
    assert result["proprietary"] is False
    assert _properties({"properties": cl.license_properties(result)})[cl.PROPERTY_PROPRIETARY] == "false"


@pytest.mark.parametrize("declared", ["https://example.mil/terms", "Zope Public License"])
def test_an_undeterminable_proprietary_flag_is_stated_as_unknown_not_defaulted(declared):
    """Defaulting to False would assert the absence of conditions nobody checked for."""
    result = cl.resolve_declared_license(declared)
    assert result["proprietary"] is None
    assert _properties({"properties": cl.license_properties(result)})[cl.PROPERTY_PROPRIETARY] == cl.UNKNOWN


def test_a_url_points_at_the_full_terms():
    result = cl.resolve_declared_license("https://example.mil/terms")
    assert result["declaration"] == cl.DECLARATION_URL
    assert result["url"] == "https://example.mil/terms"
    assert cl.license_db_value(result) == "https://example.mil/terms"
    assert cl.license_entries(result)[0]["license"]["url"] == "https://example.mil/terms"


def test_a_url_declared_alongside_an_identifier_survives_as_a_property():
    """CycloneDX cannot carry a url beside an expression, so it travels as a property."""
    result = cl.resolve_declared_license({"type": "MIT", "url": "https://example.mil/mit"})
    assert result["declaration"] == cl.DECLARATION_SPDX
    assert cl.license_entries(result) == [{"expression": "MIT"}]
    assert _properties({"properties": cl.license_properties(result)})[cl.PROPERTY_URL] == (
        "https://example.mil/mit"
    )


@pytest.mark.parametrize(
    "declared,reason",
    [
        (None, cl.REASON_NOT_DECLARED),
        ("", cl.REASON_DECLARATION_EMPTY),
        ("   ", cl.REASON_DECLARATION_EMPTY),
        ("UNKNOWN", cl.REASON_DECLARED_NOASSERTION),
        ("NOASSERTION", cl.REASON_DECLARED_NOASSERTION),
    ],
)
def test_an_unknown_license_is_stated_explicitly_with_a_reason(declared, reason):
    """"If the author is unaware of the license, that must be stated explicitly."""
    result = cl.resolve_declared_license(declared)
    assert cl.is_unknown(result)
    assert result["reason"] == reason
    assert cl.license_db_value(result) == cl.UNKNOWN

    properties = _properties({"properties": cl.license_properties(result)})
    assert properties[cl.PROPERTY_LICENSE] == cl.UNKNOWN
    assert properties[cl.PROPERTY_UNKNOWN_REASON] == reason
    # CycloneDX has no way to spell "unknown" inside `licenses`, so it stays empty
    # rather than carrying an invented entry.
    assert cl.license_entries(result) == []


def test_the_npm_license_array_is_a_choice():
    """npm documents the legacy array as "the user may choose", so OR is not a guess."""
    result = cl.resolve_declared_license([{"type": "MIT"}, {"type": "Apache-2.0"}])
    assert result["spdx_expression"] == "MIT OR Apache-2.0"


def test_license_db_value_is_never_empty():
    """`sbom_components.license` is NOT NULL in spirit: the element may not be omitted."""
    for declared in _DECLARATION_CORPUS:
        value = cl.license_db_value(cl.resolve_declared_license(declared))
        assert isinstance(value, str) and value.strip(), repr(declared)


# ---------------------------------------------------------------------------
# The target component's own license, read from the project's manifest
# ---------------------------------------------------------------------------


def test_pyproject_license_is_read(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nlicense = "Apache-2.0"\n', encoding="utf-8"
    )
    assert cl.project_license_from_manifests(tmp_path) == "Apache-2.0"


def test_pyproject_license_file_becomes_a_pointer_to_the_terms(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nlicense = { file = "LICENSE" }\n', encoding="utf-8"
    )
    declared = cl.project_license_from_manifests(tmp_path)
    assert cl.resolve_declared_license(declared)["proprietary"] is True


def test_package_json_license_is_read(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"license": "MIT"}), encoding="utf-8")
    assert cl.project_license_from_manifests(tmp_path) == "MIT"


def test_pom_xml_license_carries_its_url(tmp_path):
    (tmp_path / "pom.xml").write_text(
        "<project><licenses><license>"
        "<name>Apache License, Version 2.0</name>"
        "<url>https://www.apache.org/licenses/LICENSE-2.0.txt</url>"
        "</license></licenses></project>",
        encoding="utf-8",
    )
    result = cl.resolve_declared_license(cl.project_license_from_manifests(tmp_path))
    assert result["url"] == "https://www.apache.org/licenses/LICENSE-2.0.txt"


def test_a_broken_manifest_does_not_raise(tmp_path):
    """SBOM generation must not fail because a project's own metadata is malformed."""
    (tmp_path / "package.json").write_text("{ not json", encoding="utf-8")
    assert cl.project_license_from_manifests(tmp_path) is None
    assert cl.project_license_from_manifests(tmp_path / "does-not-exist") is None


def test_icdev_declares_its_own_license():
    """The repo's own SBOM must not report its target component as unknown."""
    result = cl.resolve_declared_license(cl.project_license_from_manifests(REPO_ROOT))
    assert result["declaration"] == cl.DECLARATION_SPDX
    assert result["spdx_expression"] == "Apache-2.0"


# ---------------------------------------------------------------------------
# The generator: nothing may leave the element off
# ---------------------------------------------------------------------------

_PROJECT = {"id": "demo", "name": "Demo", "directory_path": ""}

_COMPONENTS = [
    {"type": "library", "name": "left-pad", "version": "1.3.0", "purl": "pkg:npm/left-pad@1.3.0",
     "group": "", "declared_license": "MIT"},
    {"type": "library", "name": "acme-sdk", "version": "2.0.0", "purl": "pkg:npm/acme-sdk@2.0.0",
     "group": "", "declared_license": "UNLICENSED"},
    {"type": "library", "name": "flask", "version": "3.0.0", "purl": "pkg:pypi/flask@3.0.0",
     "group": ""},
]


def test_every_component_in_a_generated_document_carries_the_element():
    sbom, _ = _build_cyclonedx_sbom(_PROJECT, _COMPONENTS)

    for component in sbom["components"]:
        properties = _properties(component)
        assert properties.get(cl.PROPERTY_LICENSE), component["name"]
        assert properties.get(cl.PROPERTY_PROPRIETARY) in ("true", "false", cl.UNKNOWN)
        assert properties.get(cl.PROPERTY_DECLARATION)

    by_name = {c["name"]: c for c in sbom["components"]}
    assert by_name["left-pad"]["licenses"] == [{"expression": "MIT"}]
    assert _properties(by_name["acme-sdk"])[cl.PROPERTY_PROPRIETARY] == "true"
    # No declaration anywhere in a requirements.txt — explicitly unknown, not omitted.
    assert _properties(by_name["flask"])[cl.PROPERTY_LICENSE] == cl.UNKNOWN
    assert _properties(by_name["flask"])[cl.PROPERTY_UNKNOWN_REASON] == cl.REASON_NOT_DECLARED
    assert "licenses" not in by_name["flask"]


def test_the_target_component_carries_the_element_too(tmp_path):
    (tmp_path / "pyproject.toml").write_text('license = "Apache-2.0"\n', encoding="utf-8")
    project = {"id": "demo", "name": "Demo", "directory_path": str(tmp_path)}

    sbom, _ = _build_cyclonedx_sbom(project, [])
    target = sbom["metadata"]["component"]

    assert target["licenses"] == [{"expression": "Apache-2.0"}]
    assert _properties(target)[cl.PROPERTY_LICENSE] == "Apache-2.0"
    assert _properties(target)[cl.PROPERTY_PROPRIETARY] == "false"


def test_a_project_without_a_manifest_gets_an_explicit_unknown_target(tmp_path):
    project = {"id": "demo", "name": "Demo", "directory_path": str(tmp_path)}

    target = _build_cyclonedx_sbom(project, [])[0]["metadata"]["component"]

    assert "licenses" not in target
    assert _properties(target)[cl.PROPERTY_LICENSE] == cl.UNKNOWN


def test_package_lock_declares_licenses(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "root"},
                    "node_modules/left-pad": {"version": "1.3.0", "license": "MIT"},
                    "node_modules/acme": {"version": "1.0.0", "license": "UNLICENSED"},
                    "node_modules/nolicense": {"version": "0.1.0"},
                },
            }
        ),
        encoding="utf-8",
    )

    by_name = {c["name"]: c for c in _parse_package_lock_json(lock)}

    assert by_name["left-pad"]["declared_license"] == "MIT"
    assert by_name["acme"]["declared_license"] == "UNLICENSED"
    assert by_name["nolicense"]["declared_license"] is None


def test_the_row_id_and_the_bom_ref_describe_the_same_component():
    """A stored row must be correlatable with the document entry that describes it."""
    component = _COMPONENTS[0]
    assert _component_row_id(component).startswith(_generate_bom_ref(component))


# ---------------------------------------------------------------------------
# ACCEPTANCE: sbom_components.license is populated by a real generation run
# ---------------------------------------------------------------------------

_MINIMAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI',
    status TEXT NOT NULL DEFAULT 'active',
    directory_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    version TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'cyclonedx',
    file_path TEXT NOT NULL,
    component_count INTEGER,
    vulnerability_count INTEGER,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT
);
CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT    CHECK(component_type IN (
                                'library', 'framework', 'container', 'os',
                                'firmware', 'device', 'application', 'service', 'other')),
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    producer             TEXT,
    hash_value           TEXT,
    hash_algorithm       TEXT,
    identifiers_json     TEXT NOT NULL DEFAULT '{}',
    unknown_fields_json  TEXT NOT NULL DEFAULT '{}',
    withheld_fields_json TEXT NOT NULL DEFAULT '{}',
    tenant_id            TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def generated_sbom(tmp_path, monkeypatch):
    """Run the real generator against a real database and a real project directory."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nlicense = "Apache-2.0"\n', encoding="utf-8"
    )
    # A Python manifest declares no per-dependency license — the explicit-unknown path.
    (project_dir / "requirements.txt").write_text("flask==3.0.0\nrequests>=2.31\n", encoding="utf-8")
    # An npm lockfile does — the SPDX and proprietary paths.
    (project_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo"},
                    "node_modules/left-pad": {"version": "1.3.0", "license": "MIT"},
                    "node_modules/dual": {"version": "2.0.0", "license": "(MIT OR Apache-2.0)"},
                    "node_modules/acme-sdk": {"version": "3.0.0", "license": "UNLICENSED"},
                    "node_modules/mystery": {"version": "0.0.1"},
                },
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        ("demo", "Demo", "cli", str(project_dir)),
    )
    conn.commit()
    conn.close()

    def run():
        out_file = tmp_path / "sbom.cdx.json"
        generate_sbom("demo", output_path=str(out_file), db_path=db_path)
        document = json.loads(out_file.read_text(encoding="utf-8"))
        rows = (
            sqlite3.connect(db_path)
            .execute("SELECT component_name, version, license, purl FROM sbom_components")
            .fetchall()
        )
        return SimpleNamespace(
            document=document,
            rows={name: {"version": v, "license": lic, "purl": purl} for name, v, lic, purl in rows},
            regenerate=run,
        )

    return run()


def test_generation_populates_sbom_components_license(generated_sbom):
    """The acceptance criterion. This column has been dead since migration 209."""
    rows = generated_sbom.rows

    assert rows, "the generator wrote no sbom_components rows at all"
    assert rows["left-pad"]["license"] == "MIT"
    assert rows["dual"]["license"] == "MIT OR Apache-2.0"
    assert rows["acme-sdk"]["license"] == "UNLICENSED"
    # Declared nowhere — explicitly unknown, never NULL.
    assert rows["mystery"]["license"] == cl.UNKNOWN
    assert rows["flask"]["license"] == cl.UNKNOWN


def test_the_document_and_the_table_agree_on_which_components_exist(generated_sbom):
    """Both sides run the same dedupe, so a row cannot describe a component the document
    left out — or omit one it published."""
    assert {c["name"] for c in generated_sbom.document["components"]} == set(generated_sbom.rows)


def test_no_persisted_component_has_an_empty_license(generated_sbom):
    empty = [
        name for name, row in generated_sbom.rows.items() if not (row["license"] or "").strip()
    ]
    assert not empty, f"components persisted with no license value: {empty}"


def test_regenerating_updates_rows_instead_of_duplicating_them(generated_sbom):
    """The row id is deterministic, so a second run updates rather than accumulates."""
    second = generated_sbom.regenerate()

    assert set(second.rows) == set(generated_sbom.rows)
    assert second.rows == generated_sbom.rows


def test_every_component_in_the_generated_document_carries_a_license(generated_sbom):
    document = generated_sbom.document

    for component in document["components"]:
        properties = _properties(component)
        assert properties.get(cl.PROPERTY_LICENSE), component["name"]
        assert properties.get(cl.PROPERTY_PROPRIETARY) in ("true", "false", cl.UNKNOWN)

    target_properties = _properties(document["metadata"]["component"])
    assert target_properties[cl.PROPERTY_LICENSE] == "Apache-2.0"


def test_the_generated_document_emits_only_validated_spdx_expressions(generated_sbom):
    document = generated_sbom.document

    expressions = [
        entry["expression"]
        for component in document["components"] + [document["metadata"]["component"]]
        for entry in component.get("licenses", [])
        if "expression" in entry
    ]
    assert expressions, "no SPDX expression was emitted at all"
    for expression in expressions:
        for token in expression.replace("(", " ").replace(")", " ").split():
            if token in ("AND", "OR", "WITH"):
                continue
            bare = token[:-1] if token.endswith("+") else token
            assert (
                bare in SPDX_LICENSE_IDS
                or bare in SPDX_EXCEPTION_IDS
                or cl.is_spdx_license_ref(token)
            ), f"{token!r} is not on the SPDX License List"


# The PostgreSQL side of the upsert lives in tests/pg_tier/, not here: this module's
# generator fixture forces SQLite, and the PG tier allowlist only admits files that
# exercise the ambient backend.

# ---------------------------------------------------------------------------
# Mirror parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "root,mirror", list(zip(ROOT_COPIES, MIRROR_COPIES)), ids=[p.stem for p in ROOT_COPIES]
)
def test_root_and_mirror_stay_in_sync(root, mirror):
    """Mirror-only or root-only authoring silently drifts the two copies apart."""
    assert mirror.exists(), f"{mirror} is missing — the icdev/ package would not import it"
    assert root.read_text(encoding="utf-8") == mirror.read_text(encoding="utf-8"), (
        f"{root.name} and its icdev/ mirror have diverged — author changes in both."
    )
