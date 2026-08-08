#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fmt-01 — the second data format, and the CycloneDX default uplift.

The 2026 Minimum Elements name **SPDX (ISO/IEC 5962:2021)** and **CycloneDX
(ECMA-424)** as the two widely used SBOM formats and ask for support of all of
them. ICDEV emitted CycloneDX only and ``generate_sbom`` raised on anything
else, while the customer-facing GovCon content already claimed both.

The acceptance criterion is *parity*: the same project emitted in either format
must score identically. These tests assert that in the only way that survives
the remaining `sbx` element tasks — not by listing today's 17 elements, but by
asserting that every element statement the CycloneDX document makes is present
in the SPDX one and vice versa. A future task that adds a field to the
CycloneDX document and forgets SPDX fails here.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from tools.compliance import component_producer, spdx_writer as sw
from tools.compliance.sbom_generator import (
    CYCLONEDX_SCHEMA,
    CYCLONEDX_SPEC_VERSION,
    CYCLONEDX_SUPPORTED_VERSIONS,
    FORMAT_CYCLONEDX,
    FORMAT_EXTENSIONS,
    FORMAT_SPDX,
    SUPPORTED_FORMATS,
    _build_cyclonedx_sbom,
    generate_sbom,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

ROOT_WRITER = REPO_ROOT / "tools" / "compliance" / "spdx_writer.py"
MIRROR_WRITER = REPO_ROOT / "icdev" / "tools" / "compliance" / "spdx_writer.py"
ROOT_SCHEMA = REPO_ROOT / "context" / "compliance" / "schemas" / "spdx-2.3.schema.json"
MIRROR_SCHEMA = REPO_ROOT / "icdev" / "context" / "compliance" / "schemas" / "spdx-2.3.schema.json"

PROJECT = {"id": "sbx-demo", "name": "SBX Demo App", "directory_path": None}

COMPONENTS = [
    {
        "type": "library",
        "name": "requests",
        "version": "2.31.0",
        "purl": "pkg:pypi/requests@2.31.0",
        "scope": "required",
        "group": "",
        "ecosystem": "python",
    },
    {
        "type": "library",
        "name": "left-pad",
        "version": "1.3.0",
        "purl": "pkg:npm/left-pad@1.3.0",
        "scope": "optional",
        "group": "",
        "ecosystem": "npm",
    },
]


def _cyclonedx(spec_version=None, components=None):
    document, _ = _build_cyclonedx_sbom(
        PROJECT,
        components if components is not None else COMPONENTS,
        spec_version=spec_version,
        schema=CYCLONEDX_SUPPORTED_VERSIONS.get(spec_version) if spec_version else None,
    )
    return document


def _with_dependency_graph(document):
    """Attach the dependency edges sbx-cov-02 emits."""
    root = document["metadata"]["component"]["bom-ref"]
    refs = [component["bom-ref"] for component in document["components"]]
    document["dependencies"] = [
        {"ref": root, "dependsOn": refs},
        {"ref": refs[0], "dependsOn": [refs[1]]},
        {"ref": refs[1], "dependsOn": []},
    ]
    return document, root, refs


# =====================================================================================
# CycloneDX default uplift
# =====================================================================================


def test_the_cyclonedx_default_is_no_longer_the_2022_spec():
    """1.4 is a 2022 spec; the standard warns against deprecated format versions."""
    assert CYCLONEDX_SPEC_VERSION != "1.4"
    assert CYCLONEDX_SPEC_VERSION in CYCLONEDX_SUPPORTED_VERSIONS
    assert CYCLONEDX_SCHEMA == CYCLONEDX_SUPPORTED_VERSIONS[CYCLONEDX_SPEC_VERSION]


def test_the_default_is_at_least_the_version_that_can_name_a_producer():
    """Below 1.6 the only organizational field is `supplier`.

    Component Producer replaced Supplier Name precisely because "supplier"
    conflates producer and distributor; ``component.manufacturer`` — the field
    whose definition matches the element — exists from CycloneDX 1.6.
    """
    major, minor = (int(part) for part in CYCLONEDX_SPEC_VERSION.split("."))
    assert (major, minor) >= (1, 6)


@pytest.mark.parametrize("spec_version", ["1.4", "1.5", "1.6", "1.7"])
def test_every_spec_version_from_1_4_to_1_7_remains_selectable(spec_version):
    """Consumers whose tooling has not caught up must keep working."""
    assert spec_version in CYCLONEDX_SUPPORTED_VERSIONS

    document = _cyclonedx(spec_version=spec_version)
    assert document["specVersion"] == spec_version
    assert document["$schema"] == CYCLONEDX_SUPPORTED_VERSIONS[spec_version]


def test_generating_without_an_override_uses_the_new_default():
    document = _cyclonedx()
    assert document["specVersion"] == CYCLONEDX_SPEC_VERSION

    # At the new default a known producer lands in `manufacturer` — the field
    # that means "the organization that created the component" — rather than in
    # `supplier`, which is where 1.4/1.5 had to put it.
    component = component_producer.apply_producer_to_cyclonedx(
        {},
        component_producer._known_producer("Acme Widgets, Inc.", component_producer.SOURCE_OPERATOR),
        CYCLONEDX_SPEC_VERSION,
    )
    assert component["manufacturer"] == {"name": "Acme Widgets, Inc."}
    assert "supplier" not in component


# =====================================================================================
# The SPDX document validates against the official schema
# =====================================================================================


def test_the_vendored_schema_is_the_official_spdx_2_3_schema():
    schema = json.loads(ROOT_SCHEMA.read_text(encoding="utf-8"))
    assert "spdx" in str(schema.get("$id", "")).lower()
    assert set(schema["required"]) == {"SPDXID", "creationInfo", "dataLicense", "name", "spdxVersion"}
    assert "packages" in schema["properties"]
    assert "relationships" in schema["properties"]


def test_the_generated_spdx_document_validates_against_the_official_schema():
    document, _, _ = _with_dependency_graph(_cyclonedx())
    spdx = sw.to_spdx(document)

    result = sw.validate_spdx(spdx)
    assert result["valid"], "\n".join(result["errors"])


def test_validation_reports_a_broken_document_rather_than_passing_it():
    """A validator that approves everything is worse than no validator."""
    spdx = sw.to_spdx(_cyclonedx())
    del spdx["creationInfo"]

    result = sw.validate_spdx(spdx)
    assert not result["valid"]
    assert result["errors"]


def test_the_document_declares_spdx_2_3_and_the_mandated_data_license():
    spdx = sw.to_spdx(_cyclonedx())
    assert spdx["spdxVersion"] == "SPDX-2.3"
    assert spdx["dataLicense"] == "CC0-1.0"
    assert spdx["SPDXID"] == "SPDXRef-DOCUMENT"


def test_the_two_serializations_of_one_build_share_a_namespace():
    """The SPDX namespace is the CycloneDX serial number.

    Two files, one SBOM — a recipient holding both must be able to tell they
    describe the same build rather than two independent ones.
    """
    document = _cyclonedx()
    spdx = sw.to_spdx(document)
    assert spdx["documentNamespace"] == document["serialNumber"]


def test_spdx_identifiers_are_unique_and_well_formed():
    document = _cyclonedx(
        components=COMPONENTS
        + [
            # Same name, different version: two components under the Coverage
            # element, so two packages with two identifiers.
            {
                "type": "library",
                "name": "left-pad",
                "version": "1.2.0",
                "purl": "pkg:npm/left-pad@1.2.0",
                "scope": "optional",
                "group": "",
                "ecosystem": "npm",
            },
        ]
    )
    spdx = sw.to_spdx(document)

    ids = [package["SPDXID"] for package in spdx["packages"]]
    assert len(ids) == len(set(ids))
    for spdx_id in ids:
        assert spdx_id.startswith("SPDXRef-")
        assert all(character.isalnum() or character in ".-" for character in spdx_id)


# =====================================================================================
# Element parity — the acceptance criterion
# =====================================================================================


def test_cyclonedx_and_spdx_carry_identical_element_statements():
    document, _, _ = _with_dependency_graph(_cyclonedx())
    spdx = sw.to_spdx(document)

    result = sw.compare_element_coverage(document, spdx)
    assert result["element_count"] > 0
    assert result["parity"], json.dumps(result, indent=2)


def test_a_field_dropped_from_the_spdx_document_breaks_parity():
    """The parity check has to be able to fail, or it proves nothing."""
    document = _cyclonedx()
    spdx = sw.to_spdx(document)
    # Simulate a future element that reached CycloneDX and not SPDX.
    document["metadata"]["properties"].append({"name": "icdev:sbom:author", "value": "Acme, Inc."})

    result = sw.compare_element_coverage(document, spdx)
    assert not result["parity"]
    assert any("icdev:sbom:author=Acme, Inc." in entry for entry in result["missing_in_spdx"])


def test_the_coverage_aggregate_survives_the_translation():
    """SPDX 2.3 has no `compositions`; losing it would cost the Coverage element."""
    document = _cyclonedx()
    spdx = sw.to_spdx(document)

    payloads = sw._read_annotation_payloads(spdx)
    compositions = [p[sw.ANNOTATION_COMPOSITIONS_KEY] for p in payloads if sw.ANNOTATION_COMPOSITIONS_KEY in p]
    assert compositions == [document["compositions"]]

    statuses = {
        entry["name"]: entry["value"]
        for entries in sw.spdx_property_index(spdx).values()
        for entry in entries
    }
    assert "icdev:sbom:coverage" in statuses


def test_the_classification_marking_travels_to_the_spdx_document():
    spdx = sw.to_spdx(_cyclonedx())
    statements = {
        f"{entry['name']}={entry['value']}"
        for entries in sw.spdx_property_index(spdx).values()
        for entry in entries
    }
    assert "icdev:classification=CUI // SP-CTI" in statements


# =====================================================================================
# Component Producer, License, Hashes
# =====================================================================================


def test_the_component_producer_becomes_the_spdx_originator():
    """SPDX separates `originator` (created it) from `supplier` (handed it over).

    Component Producer means the entity that creates, defines and identifies the
    component, so it is the originator — mapping it to `supplier` would restore
    exactly the ambiguity the 2026 standard removed.
    """
    package = sw._package(
        {"name": "alpha", "version": "1.0.0", "manufacturer": {"name": "Acme Widgets, Inc."}},
        "SPDXRef-alpha",
        "Tool: t-1",
        "2026-01-01T00:00:00Z",
    )
    assert package["originator"] == "Organization: Acme Widgets, Inc."
    assert "supplier" not in package

    # 1.4/1.5 write the producer to `supplier` because they have no
    # `manufacturer`; it is still the producer and still the originator.
    legacy = sw._package(
        {"name": "beta", "version": "1.0.0", "supplier": {"name": "Beta Foundation"}},
        "SPDXRef-beta",
        "Tool: t-1",
        "2026-01-01T00:00:00Z",
    )
    assert legacy["originator"] == "Organization: Beta Foundation"


def test_a_component_of_unknown_provenance_says_so_in_both_formats():
    """NOASSERTION natively, and the machine-readable reason in the properties."""
    document = _cyclonedx()
    spdx = sw.to_spdx(document)

    package = next(p for p in spdx["packages"] if p["name"] == "requests")
    assert package["originator"] == sw.NOASSERTION

    properties = {
        entry["name"]: entry["value"]
        for payload in sw._read_annotation_payloads(package)
        for entry in payload.get(sw.ANNOTATION_PROPERTIES_KEY, [])
    }
    assert properties["icdev:component-provenance"] == "unknown"
    assert properties["icdev:component-producer-unknown-reason"]


def test_licenses_and_hashes_translate_to_their_native_spdx_fields():
    """Component License and Component Hash Value/Algorithm (sbx-fld-03/04).

    Those tasks add the CycloneDX fields; the mapping has to be waiting for them
    or the SPDX document would score lower the day they land.
    """
    package = sw._package(
        {
            "name": "gamma",
            "version": "3.0.0",
            "licenses": [{"license": {"id": "Apache-2.0"}}],
            "hashes": [{"alg": "SHA-256", "content": "AB" * 32}],
        },
        "SPDXRef-gamma",
        "Tool: t-1",
        "2026-01-01T00:00:00Z",
    )
    assert package["licenseDeclared"] == "Apache-2.0"
    # ICDEV reports the declared license; it does not conclude one for the recipient.
    assert package["licenseConcluded"] == sw.NOASSERTION
    assert package["checksums"] == [{"algorithm": "SHA256", "checksumValue": "ab" * 32}]


def test_the_purl_becomes_a_package_manager_external_reference():
    spdx = sw.to_spdx(_cyclonedx())
    package = next(p for p in spdx["packages"] if p["name"] == "left-pad")
    assert {
        "referenceCategory": "PACKAGE-MANAGER",
        "referenceType": "purl",
        "referenceLocator": "pkg:npm/left-pad@1.3.0",
    } in package["externalRefs"]


# =====================================================================================
# Dependency relationships
# =====================================================================================


def test_dependency_edges_become_spdx_relationship_entries():
    document, root, refs = _with_dependency_graph(_cyclonedx())
    spdx = sw.to_spdx(document)

    ids = {package["name"]: package["SPDXID"] for package in spdx["packages"]}
    edges = {
        (relationship["spdxElementId"], relationship["relatedSpdxElement"]): relationship["relationshipType"]
        for relationship in spdx["relationships"]
    }

    assert edges[("SPDXRef-DOCUMENT", ids["SBX Demo App"])] == "DESCRIBES"
    assert edges[(ids["SBX Demo App"], ids["requests"])] == "DEPENDS_ON"
    assert edges[(ids["SBX Demo App"], ids["left-pad"])] == "DEPENDS_ON"
    assert edges[(ids["requests"], ids["left-pad"])] == "DEPENDS_ON"


def test_no_dependency_graph_means_no_invented_relationships():
    """Parity cuts both ways.

    Until sbx-cov-02 emits the CycloneDX `dependencies` array, the CycloneDX
    document asserts no dependency edges. Synthesizing a root-depends-on-
    everything graph on the SPDX side would make the two score differently on
    Component Dependency Relationship — and would assert an edge nothing
    resolved.
    """
    spdx = sw.to_spdx(_cyclonedx())
    assert [r["relationshipType"] for r in spdx["relationships"]] == ["DESCRIBES"]


def test_relationship_translation_ignores_edges_to_components_that_are_not_there():
    document = _cyclonedx()
    document["dependencies"] = [{"ref": "does-not-exist", "dependsOn": ["also-missing"]}]

    spdx = sw.to_spdx(document)
    assert [r["relationshipType"] for r in spdx["relationships"]] == ["DESCRIBES"]
    assert sw.validate_spdx(spdx)["valid"]


# =====================================================================================
# SBOM Author is not the tool vendor
# =====================================================================================


def test_the_tool_vendor_is_not_claimed_as_the_sbom_author():
    """The standard is explicit that the tool vendor is not the SBOM Author."""
    spdx = sw.to_spdx(_cyclonedx())
    creators = spdx["creationInfo"]["creators"]

    assert any(creator.startswith("Tool: icdev-sbom-generator") for creator in creators)
    assert not any(creator.startswith("Organization:") for creator in creators)
    # The vendor is still recorded — as the vendor of the tool, in free text.
    assert "ICDEV" in spdx["creationInfo"]["comment"]


def test_a_real_author_becomes_the_organization_creator():
    """When sbx-fld-01 lands the SBOM Author element, SPDX has a home for it."""
    document = _cyclonedx()
    document["metadata"]["properties"].append({"name": "icdev:sbom:author", "value": "Acme, Inc."})

    spdx = sw.to_spdx(document)
    assert "Organization: Acme, Inc." in spdx["creationInfo"]["creators"]
    assert sw.validate_spdx(spdx)["valid"]


# =====================================================================================
# The generator's format surface
# =====================================================================================


def test_both_named_formats_are_supported_and_swid_is_not():
    """SWID tags were removed from the accepted format list in 2026."""
    assert set(SUPPORTED_FORMATS) == {FORMAT_CYCLONEDX, FORMAT_SPDX}
    assert "swid" not in SUPPORTED_FORMATS


def test_generate_sbom_rejects_a_format_the_standard_does_not_name(icdev_db):
    with pytest.raises(ValueError, match="Unsupported SBOM format"):
        generate_sbom("sbx-demo", sbom_format="swid", db_path=icdev_db)


def _seed_project(db_path, tmp_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        ("sbx-demo", "SBX Demo App", "api", str(tmp_path)),
    )
    conn.commit()
    conn.close()


def test_generate_sbom_writes_a_valid_spdx_document_and_records_the_format(icdev_db, tmp_path):
    """End to end: --format spdx produces a schema-valid file and one row."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    _seed_project(icdev_db, project_dir)

    path = generate_sbom("sbx-demo", sbom_format=FORMAT_SPDX, db_path=icdev_db)

    assert path.endswith("." + FORMAT_EXTENSIONS[FORMAT_SPDX])
    spdx = json.loads(Path(path).read_text(encoding="utf-8"))
    assert spdx["spdxVersion"] == "SPDX-2.3"
    assert sw.validate_spdx(spdx)["valid"]
    assert any(package["name"] == "requests" for package in spdx["packages"])

    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT format, file_path FROM sbom_records WHERE project_id = 'sbx-demo'").fetchall()
    conn.close()
    assert [row["format"] for row in rows] == [FORMAT_SPDX]
    assert rows[0]["file_path"] == path


def test_the_same_project_in_both_formats_scores_identically(icdev_db, tmp_path):
    """The acceptance criterion, over a real generation rather than a fixture."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("requests==2.31.0\nurllib3==2.2.1\n", encoding="utf-8")
    _seed_project(icdev_db, project_dir)

    cyclonedx_path = generate_sbom("sbx-demo", sbom_format=FORMAT_CYCLONEDX, db_path=icdev_db)
    spdx_path = generate_sbom("sbx-demo", sbom_format=FORMAT_SPDX, db_path=icdev_db)

    cyclonedx = json.loads(Path(cyclonedx_path).read_text(encoding="utf-8"))
    spdx = json.loads(Path(spdx_path).read_text(encoding="utf-8"))

    result = sw.compare_element_coverage(cyclonedx, spdx)
    assert result["element_count"] > 0
    assert result["parity"], json.dumps(result, indent=2)

    # Same components, both ways round. The SPDX document adds one package for
    # the document's own target component, which CycloneDX carries as
    # metadata.component rather than as an entry in `components`.
    assert len(spdx["packages"]) == len(cyclonedx["components"]) + 1


def test_an_spdx_document_is_signed_like_a_cyclonedx_one(icdev_db, tmp_path, monkeypatch):
    """SBOM Author Signature (sbx-sig-01) has to reach the second format too.

    An SPDX document that carried no signature would fail that element while
    the CycloneDX document of the same build passed it — which is the parity
    criterion breaking across a task boundary rather than inside one.
    """
    from tools.compliance.sbom_signer import signature_path_for, verify_sbom
    from tools.crypto.key_manager import generate_keypair

    keys = generate_keypair(tmp_path / "keys", "ecdsa-p256")
    monkeypatch.setenv("ICDEV_SBOM_SIGNING_KEY_PATH", keys["private_key"])

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    _seed_project(icdev_db, project_dir)

    path = Path(generate_sbom("sbx-demo", sbom_format=FORMAT_SPDX, db_path=icdev_db))

    assert signature_path_for(path).exists()
    assert verify_sbom(path, expected_fp=keys["public_key_fp"])["verified"] is True


# =====================================================================================
# The MCP surface
# =====================================================================================


def test_the_mcp_tool_offers_both_formats():
    from tools.mcp.tool_registry import TOOL_REGISTRY

    schema = TOOL_REGISTRY["sbom_generate"]["input_schema"]
    assert set(schema["properties"]["format"]["enum"]) == set(SUPPORTED_FORMATS)
    # generate_sbom reads the project's directory_path from the database; it
    # has never taken a directory, so project_id is what the tool needs.
    assert schema["required"] == ["project_id"]


def test_the_mcp_handler_calls_the_generator_with_arguments_it_accepts(monkeypatch):
    """The handler passed project_dir positionally *and* project_id by keyword.

    That is a TypeError on every invocation -- `generate_sbom`'s first
    positional parameter *is* project_id -- so the MCP tool could never have
    produced an SBOM.
    """
    import tools.compliance.sbom_generator as generator
    from tools.mcp import compliance_server

    calls = {}

    def fake_generate_sbom(project_id, sbom_format="cyclonedx", **kwargs):
        calls["project_id"] = project_id
        calls["format"] = sbom_format
        return "/tmp/sbom.spdx.json"

    monkeypatch.setattr(generator, "generate_sbom", fake_generate_sbom)

    result = compliance_server.handle_sbom_generate({"project_id": "sbx-demo", "format": FORMAT_SPDX})

    assert calls == {"project_id": "sbx-demo", "format": FORMAT_SPDX}
    assert result["sbom_path"] == "/tmp/sbom.spdx.json"
    assert result["format"] == FORMAT_SPDX


def test_the_mcp_handler_requires_a_project_id():
    from tools.mcp import compliance_server

    with pytest.raises(ValueError, match="project_id"):
        compliance_server.handle_sbom_generate({"project_dir": "/some/path"})


# =====================================================================================
# Root / mirror parity
# =====================================================================================


@pytest.mark.parametrize(
    "root,mirror",
    [(ROOT_WRITER, MIRROR_WRITER), (ROOT_SCHEMA, MIRROR_SCHEMA)],
    ids=["writer", "schema"],
)
def test_root_and_icdev_mirror_stay_in_sync(root, mirror):
    assert mirror.exists(), f"{mirror} is missing — the packaged copy would not resolve"
    assert root.read_bytes() == mirror.read_bytes(), f"{root.name} drifted from its icdev/ mirror"


def test_the_mirror_resolves_its_own_schema():
    """The packaged copy resolves BASE_DIR to icdev/, not to the checkout root."""
    from icdev.tools.compliance import spdx_writer as mirrored

    assert mirrored.SCHEMA_PATH.exists()
    assert mirrored.validate_spdx(sw.to_spdx(_cyclonedx()))["valid"]


def test_the_schema_is_looked_for_in_every_layout_it_can_ship_in():
    """Checkout, icdev/ mirror and pip install place the context layer differently.

    A validator that cannot find its schema fails every document, so the miss
    must not depend on which layout is running.
    """
    candidates = [str(candidate) for candidate in sw._schema_candidates()]
    assert any(candidate.endswith(str(Path("data") / "context" / "compliance" / "schemas" / "spdx-2.3.schema.json")) for candidate in candidates)
    assert any(Path(candidate).exists() for candidate in candidates)
