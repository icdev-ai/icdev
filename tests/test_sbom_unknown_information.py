#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-prc-01 — Explicitly Identifying Unknown Information: unknown vs withheld.

The 2026 Minimum Elements renamed *Known Unknowns* and split the single flag in
two: a field that is not provided is either **unknown to the author** or
**withheld by the author**, and those are now distinct states. ICDEV wrote both
as the literal ``"unspecified"``.

The tests that matter here are the negatives. It is easy to emit two states; the
work is making them impossible to confuse — a withheld reason cannot be filed as
an unknown, a withheld field is never counted as an unknown, and an SBOM that
withholds anything without naming a way to ask about it does not validate.
"""

import json
from pathlib import Path

import pytest

from tools.compliance import unknown_information as ui

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# the vocabularies keep the two states apart by construction
# ---------------------------------------------------------------------------


def test_the_two_reason_vocabularies_are_disjoint():
    # This is the whole guarantee. If a code appeared in both sets, a reader
    # could not tell which state a document meant, and every other test here
    # would be checking a distinction that does not exist.
    assert ui.UNKNOWN_REASONS & ui.WITHHELD_REASONS == set()


def test_a_reason_code_identifies_its_own_state():
    assert ui.state_of_reason(ui.REASON_NOT_PROVIDED_BY_PRODUCER) == ui.UNKNOWN
    assert ui.state_of_reason(ui.REASON_EXPORT_CONTROLLED) == ui.WITHHELD
    assert ui.state_of_reason("invented-reason") is None


def test_the_field_vocabulary_covers_all_seventeen_elements():
    assert len(ui.METADATA_FIELDS) == 9
    assert len(ui.COMPONENT_FIELDS) == 8
    assert len(set(ui.FIELDS)) == 17
    assert ui.ESSENTIAL_COMPONENT_FIELDS <= set(ui.COMPONENT_FIELDS)


def test_a_withheld_reason_cannot_be_recorded_as_unknown():
    disclosure = ui.Disclosure()
    with pytest.raises(ValueError, match="not an unknown-reason"):
        disclosure.unknown(ui.FIELD_VERSION, ui.REASON_CLASSIFICATION_RESTRICTED)


def test_an_unknown_reason_cannot_be_recorded_as_withheld():
    disclosure = ui.Disclosure()
    with pytest.raises(ValueError, match="not a withheld-reason"):
        disclosure.withheld(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)


def test_a_field_outside_the_minimum_elements_is_rejected():
    with pytest.raises(ValueError, match="not a 2026 minimum-elements field"):
        ui.Disclosure().unknown("release_notes", ui.REASON_METADATA_ABSENT)


# ---------------------------------------------------------------------------
# a field is in one state or the other, never both
# ---------------------------------------------------------------------------


def test_withholding_a_field_evicts_its_unknown_record():
    disclosure = ui.Disclosure()
    disclosure.unknown(ui.FIELD_VERSION, ui.REASON_DECLARED_WITHOUT_VERSION)
    disclosure.withheld(ui.FIELD_VERSION, ui.REASON_OPERATIONAL_SECURITY)

    assert disclosure.state_of(ui.FIELD_VERSION) == ui.WITHHELD
    assert disclosure.unknown_fields == {}
    assert disclosure.withheld_fields == {ui.FIELD_VERSION: ui.REASON_OPERATIONAL_SECURITY}


def test_marking_a_field_unknown_evicts_its_withheld_record():
    disclosure = ui.Disclosure()
    disclosure.withheld(ui.FIELD_LICENSE, ui.REASON_PROPRIETARY)
    disclosure.unknown(ui.FIELD_LICENSE, ui.REASON_METADATA_ABSENT)

    assert disclosure.state_of(ui.FIELD_LICENSE) == ui.UNKNOWN
    assert disclosure.withheld_fields == {}


def test_a_withheld_field_carries_no_free_text_detail():
    # Explaining a redaction inside the document the redaction protects undoes
    # it. The reason code is the category; specifics go through the enquiry route.
    disclosure = ui.Disclosure()
    disclosure.unknown(ui.FIELD_VERSION, ui.REASON_DECLARED_WITHOUT_VERSION, detail="no pin in requirements.txt")
    assert disclosure.details[ui.FIELD_VERSION]

    disclosure.withheld(ui.FIELD_VERSION, ui.REASON_CLASSIFICATION_RESTRICTED)
    assert ui.FIELD_VERSION not in disclosure.details


# ---------------------------------------------------------------------------
# rendering: both states are representable and distinguishable in CycloneDX
# ---------------------------------------------------------------------------


def test_both_states_render_to_distinct_cyclonedx_properties():
    disclosure = ui.Disclosure()
    disclosure.unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)
    disclosure.withheld(ui.FIELD_HASH_VALUE, ui.REASON_EXPORT_CONTROLLED)

    rendered = {p["name"]: p["value"] for p in disclosure.properties()}
    assert rendered["icdev:unknown:version"] == ui.REASON_NOT_PROVIDED_BY_PRODUCER
    assert rendered["icdev:withheld:hash_value"] == ui.REASON_EXPORT_CONTROLLED
    # No property name can be read as the other state.
    assert "icdev:unknown:hash_value" not in rendered
    assert "icdev:withheld:version" not in rendered


def test_the_sentinel_differs_per_state():
    disclosure = ui.Disclosure()
    disclosure.unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)
    assert disclosure.value_for(ui.FIELD_VERSION, "1.0.0") == "unknown"

    disclosure.withheld(ui.FIELD_VERSION, ui.REASON_CLASSIFICATION_RESTRICTED)
    assert disclosure.value_for(ui.FIELD_VERSION, "1.0.0") == "withheld"

    # A disclosed field keeps its real value.
    assert disclosure.value_for(ui.FIELD_NAME, "flask") == "flask"


def test_properties_round_trip_through_cyclonedx():
    disclosure = ui.Disclosure()
    disclosure.unknown(ui.FIELD_PRODUCER, ui.REASON_PRODUCER_NOT_IDENTIFIABLE, detail="forge-host-is-not-a-producer")
    disclosure.withheld(ui.FIELD_LICENSE, ui.REASON_CONTRACTUAL_RESTRICTION)

    component = ui.apply_to_cyclonedx({"name": "alpha", "version": "1.0.0"}, disclosure)
    recovered = ui.Disclosure.from_cyclonedx(component)

    assert recovered == disclosure
    assert recovered.details[ui.FIELD_PRODUCER] == "forge-host-is-not-a-producer"


def test_properties_are_ordered_so_regeneration_is_byte_stable():
    first = ui.Disclosure()
    first.unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)
    first.unknown(ui.FIELD_LICENSE, ui.REASON_METADATA_ABSENT)

    second = ui.Disclosure()
    second.unknown(ui.FIELD_LICENSE, ui.REASON_METADATA_ABSENT)
    second.unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)

    assert first.properties() == second.properties()


def test_db_values_are_two_columns_and_round_trip():
    disclosure = ui.Disclosure()
    disclosure.unknown(ui.FIELD_VERSION, ui.REASON_DECLARED_WITHOUT_VERSION)
    disclosure.withheld(ui.FIELD_HASH_VALUE, ui.REASON_OPERATIONAL_SECURITY)

    unknown_json, withheld_json = disclosure.db_values()
    assert json.loads(unknown_json) == {"version": ui.REASON_DECLARED_WITHOUT_VERSION}
    assert json.loads(withheld_json) == {"hash_value": ui.REASON_OPERATIONAL_SECURITY}

    assert ui.Disclosure.from_db_values(unknown_json, withheld_json) == disclosure


def test_empty_db_values_match_the_migration_column_default():
    # sbx-fnd-02 declared both columns NOT NULL DEFAULT '{}'.
    assert ui.Disclosure().db_values() == ("{}", "{}")


def test_from_db_values_survives_unreadable_json():
    assert ui.Disclosure.from_db_values("not json", None).is_empty()


# ---------------------------------------------------------------------------
# SPDX: the distinction survives a format that has only NOASSERTION
# ---------------------------------------------------------------------------


def test_spdx_keeps_the_states_distinguishable_in_the_annotation():
    disclosure = ui.Disclosure()
    disclosure.unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)
    disclosure.withheld(ui.FIELD_LICENSE, ui.REASON_PROPRIETARY)

    mapping = {entry["field"]: entry for entry in ui.spdx_mapping(disclosure)}

    # SPDX has one marker, so the native value cannot carry the distinction...
    assert mapping["version"]["value"] == "NOASSERTION"
    assert mapping["license"]["value"] == "NOASSERTION"

    # ...and the annotation does, in a fixed form a parser can match.
    assert "UNKNOWN to the SBOM author" in mapping["version"]["comment"]
    assert "WITHHELD by the SBOM author" in mapping["license"]["comment"]
    assert "enquiry process" in mapping["license"]["comment"]
    assert mapping["version"]["state"] == ui.UNKNOWN
    assert mapping["license"]["state"] == ui.WITHHELD


# ---------------------------------------------------------------------------
# the producer bridge (sbx-fld-02 slots in without re-stating itself)
# ---------------------------------------------------------------------------


def test_an_unidentifiable_producer_becomes_an_unknown_field():
    from tools.compliance import component_producer as cp

    disclosure = ui.disclosure_from_producer(cp.unknown_producer(cp.REASON_FORGE_HOST))
    assert disclosure.unknown_fields == {ui.FIELD_PRODUCER: ui.REASON_PRODUCER_NOT_IDENTIFIABLE}
    # The producer element's own, finer-grained reason is preserved as detail.
    assert disclosure.details[ui.FIELD_PRODUCER] == cp.REASON_FORGE_HOST


def test_an_identified_producer_discloses_nothing():
    from tools.compliance import component_producer as cp

    result = cp._known_producer("Acme Widgets, Inc.", cp.SOURCE_PYTHON_DIST)
    assert ui.disclosure_from_producer(result).is_empty()


# ---------------------------------------------------------------------------
# policy: the enquiry route and declared withholdings
# ---------------------------------------------------------------------------


def _policy_file(tmp_path, body):
    path = tmp_path / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_shipped_policy_states_an_enquiry_process_and_withholds_nothing():
    policy = ui.load_disclosure_policy(env={})
    assert policy["enquiry"]["process"].strip()
    assert policy["enquiry"]["response_target_days"] > 0
    assert policy["withhold"] == {"document": [], "components": []}
    assert ui.policy_defects() == []


def test_a_missing_policy_file_still_yields_an_enquiry_route(tmp_path):
    # An SBOM that withholds a field and names no way to ask about it is what
    # the standard forbids, so the route can never come back empty.
    policy = ui.load_disclosure_policy(path=tmp_path / "absent.yaml", env={})
    assert policy["enquiry"]["process"] == ui.DEFAULT_ENQUIRY_PROCESS


def test_environment_overrides_the_enquiry_contact(tmp_path):
    policy = ui.load_disclosure_policy(
        path=tmp_path / "absent.yaml",
        env={ui.ENV_ENQUIRY_CONTACT: "sbom@example.mil", ui.ENV_ENQUIRY_URI: "https://example.mil/sbom"},
    )
    rendered = {p["name"]: p["value"] for p in ui.enquiry_properties(policy)}
    assert rendered[ui.PROPERTY_ENQUIRY_CONTACT] == "sbom@example.mil"
    assert rendered[ui.PROPERTY_ENQUIRY_URI] == "https://example.mil/sbom"


def test_a_rule_carrying_an_unknown_reason_is_dropped_not_applied(tmp_path):
    # `not-provided-by-producer` belongs to the other state. Applying it as a
    # withholding would be exactly the conflation this element exists to remove.
    path = _policy_file(
        tmp_path,
        "withhold:\n"
        "  components:\n"
        "    - field: version\n"
        "      reason: not-provided-by-producer\n",
    )
    policy = ui.load_disclosure_policy(path=path, env={})
    assert policy["withhold"]["components"] == []

    defects = ui.policy_defects(path)
    assert any("is not a withheld-reason" in d and "an unknown-reason" in d for d in defects)


def test_a_component_rule_matches_on_purl_prefix(tmp_path):
    path = _policy_file(
        tmp_path,
        "withhold:\n"
        "  components:\n"
        "    - match: {purl: 'pkg:maven/mil.example.restricted/'}\n"
        "      field: version\n"
        "      reason: classification-restricted\n",
    )
    policy = ui.load_disclosure_policy(path=path, env={})

    restricted = ui.apply_component_policy({"purl": "pkg:maven/mil.example.restricted/crypto@1.0"}, policy)
    assert restricted.withheld_fields == {ui.FIELD_VERSION: ui.REASON_CLASSIFICATION_RESTRICTED}

    other = ui.apply_component_policy({"purl": "pkg:pypi/flask@3.0.0"}, policy)
    assert other.is_empty()


def test_an_unmatched_key_is_a_non_match_not_a_wildcard(tmp_path):
    path = _policy_file(
        tmp_path,
        "withhold:\n"
        "  components:\n"
        "    - match: {ecosystem: golang, name: internal-crypto}\n"
        "      field: hash_value\n"
        "      reason: export-controlled\n",
    )
    policy = ui.load_disclosure_policy(path=path, env={})

    assert ui.apply_component_policy({"ecosystem": "golang", "name": "other"}, policy).is_empty()
    assert not ui.apply_component_policy({"ecosystem": "golang", "name": "internal-crypto"}, policy).is_empty()


# ---------------------------------------------------------------------------
# completeness: withholding essential component data
# ---------------------------------------------------------------------------


def test_withholding_an_essential_field_marks_the_document_incomplete():
    withholds_license = ui.Disclosure().withheld(ui.FIELD_LICENSE, ui.REASON_PROPRIETARY)
    rendered = {p["name"]: p["value"] for p in ui.completeness_properties([withholds_license])}
    assert rendered[ui.PROPERTY_DISCLOSURE_COMPLETENESS] == ui.COMPLETENESS_INCOMPLETE_WITHHELD


def test_withholding_a_non_essential_field_leaves_the_document_complete():
    withholds_hash = ui.Disclosure().withheld(ui.FIELD_HASH_VALUE, ui.REASON_OPERATIONAL_SECURITY)
    rendered = {p["name"]: p["value"] for p in ui.completeness_properties([withholds_hash])}
    assert rendered[ui.PROPERTY_DISCLOSURE_COMPLETENESS] == ui.COMPLETENESS_COMPLETE


def test_unknowns_never_make_the_document_incomplete():
    # An SBOM full of honest unknowns is a data-quality signal; the standard's
    # "may be considered incomplete" sentence is about *withholding*.
    unknowns = ui.Disclosure().unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)
    rendered = {p["name"]: p["value"] for p in ui.completeness_properties([unknowns])}
    assert rendered[ui.PROPERTY_DISCLOSURE_COMPLETENESS] == ui.COMPLETENESS_COMPLETE


def test_the_document_totals_are_two_numbers_that_are_never_summed():
    disclosures = [
        ui.Disclosure().unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER),
        ui.Disclosure().unknown(ui.FIELD_LICENSE, ui.REASON_METADATA_ABSENT),
        ui.Disclosure().withheld(ui.FIELD_HASH_VALUE, ui.REASON_EXPORT_CONTROLLED),
    ]
    rendered = {p["name"]: p["value"] for p in ui.completeness_properties(disclosures)}
    assert rendered[ui.PROPERTY_FIELDS_UNKNOWN] == "2"
    assert rendered[ui.PROPERTY_FIELDS_WITHHELD] == "1"


# ---------------------------------------------------------------------------
# the validator
# ---------------------------------------------------------------------------


def _document(components, metadata_properties=None):
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {
            "properties": metadata_properties
            if metadata_properties is not None
            else ui.enquiry_properties(ui.load_disclosure_policy(env={})),
            "component": {"type": "application", "bom-ref": "target", "name": "app", "version": "1.0.0"},
        },
        "components": components,
    }


def _component(name="alpha", version="1.0.0", properties=None):
    component = {"type": "library", "bom-ref": name, "name": name, "version": version}
    if properties:
        component["properties"] = properties
    return component


def test_a_conforming_document_validates():
    unknown = ui.Disclosure().unknown(ui.FIELD_VERSION, ui.REASON_DECLARED_WITHOUT_VERSION)
    withheld = ui.Disclosure().withheld(ui.FIELD_HASH_VALUE, ui.REASON_EXPORT_CONTROLLED)
    sbom = _document(
        [
            _component("alpha", "unknown", unknown.properties()),
            _component("beta", "2.0.0", withheld.properties()),
        ]
    )

    errors, summary = ui.validate_sbom_disclosure(sbom)
    assert errors == []
    assert summary["fields_unknown"] == 1
    assert summary["fields_withheld"] == 1
    assert summary["components_with_unknown"] == 1
    assert summary["components_with_withheld"] == 1


def test_the_summary_never_counts_a_withheld_field_as_an_unknown():
    withheld = ui.Disclosure().withheld(ui.FIELD_VERSION, ui.REASON_CLASSIFICATION_RESTRICTED)
    sbom = _document([_component("alpha", "withheld", withheld.properties())])

    errors, summary = ui.validate_sbom_disclosure(sbom)
    assert errors == []
    assert summary["fields_unknown"] == 0
    assert summary["components_with_unknown"] == 0
    assert summary["fields_withheld"] == 1
    # The two counts are reported separately; nothing in the summary adds them.
    assert "fields_undisclosed" not in summary


def test_a_withheld_reason_filed_under_the_unknown_prefix_fails():
    sbom = _document(
        [
            _component(
                "alpha",
                "unknown",
                [{"name": "icdev:unknown:version", "value": ui.REASON_EXPORT_CONTROLLED}],
            )
        ]
    )
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("is a withheld-reason" in e for e in errors)


def test_an_unknown_reason_filed_under_the_withheld_prefix_fails():
    sbom = _document(
        [
            _component(
                "alpha",
                "withheld",
                [{"name": "icdev:withheld:version", "value": ui.REASON_METADATA_ABSENT}],
            )
        ]
    )
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("is a unknown-reason" in e or "is an unknown-reason" in e or "unknown-reason" in e for e in errors)


def test_a_field_marked_both_states_fails():
    sbom = _document(
        [
            _component(
                "alpha",
                "unknown",
                [
                    {"name": "icdev:unknown:version", "value": ui.REASON_METADATA_ABSENT},
                    {"name": "icdev:withheld:version", "value": ui.REASON_PROPRIETARY},
                ],
            )
        ]
    )
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("marked both unknown and withheld" in e for e in errors)


def test_the_pre_2026_literal_fails_validation():
    sbom = _document([_component("alpha", "unspecified")])
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("conflates" in e and "unspecified" in e for e in errors)


def test_the_maven_managed_literal_fails_validation():
    sbom = _document([_component("alpha", "managed")])
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("managed" in e for e in errors)


def test_a_bare_sentinel_with_no_property_fails():
    sbom = _document([_component("alpha", "unknown")])
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("no icdev:unknown:version property states why" in e for e in errors)


def test_a_sentinel_disagreeing_with_its_property_fails():
    sbom = _document(
        [
            _component(
                "alpha",
                "unknown",
                [{"name": "icdev:withheld:version", "value": ui.REASON_PROPRIETARY}],
            )
        ]
    )
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("sentinel but the field is declared" in e for e in errors)


def test_a_declared_unknown_carrying_a_real_value_fails():
    sbom = _document(
        [
            _component(
                "alpha",
                "1.0.0",
                [{"name": "icdev:unknown:version", "value": ui.REASON_METADATA_ABSENT}],
            )
        ]
    )
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("but version carries the value '1.0.0'" in e for e in errors)


def test_an_unrecognised_field_name_fails():
    sbom = _document(
        [_component("alpha", "1.0.0", [{"name": "icdev:unknown:relase_notes", "value": ui.REASON_METADATA_ABSENT}])]
    )
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("names no 2026 minimum-elements field" in e for e in errors)


def test_withholding_without_an_enquiry_process_fails():
    withheld = ui.Disclosure().withheld(ui.FIELD_HASH_VALUE, ui.REASON_OPERATIONAL_SECURITY)
    sbom = _document([_component("alpha", "1.0.0", withheld.properties())], metadata_properties=[])

    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any(ui.PROPERTY_ENQUIRY_PROCESS in e for e in errors)


def test_an_unknown_without_an_enquiry_process_is_fine():
    # Nothing is being held back, so there is nothing to ask for.
    unknown = ui.Disclosure().unknown(ui.FIELD_VERSION, ui.REASON_NOT_PROVIDED_BY_PRODUCER)
    sbom = _document([_component("alpha", "unknown", unknown.properties())], metadata_properties=[])

    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert errors == []


def test_a_document_understating_its_withheld_count_fails():
    withheld = ui.Disclosure().withheld(ui.FIELD_HASH_VALUE, ui.REASON_EXPORT_CONTROLLED)
    metadata_properties = ui.enquiry_properties(ui.load_disclosure_policy(env={})) + [
        {"name": ui.PROPERTY_FIELDS_WITHHELD, "value": "0"},
    ]
    sbom = _document([_component("alpha", "1.0.0", withheld.properties())], metadata_properties)

    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any(f"{ui.PROPERTY_FIELDS_WITHHELD}='0'" in e for e in errors)


def test_a_document_claiming_completeness_while_withholding_essentials_fails():
    withheld = ui.Disclosure().withheld(ui.FIELD_LICENSE, ui.REASON_PROPRIETARY)
    metadata_properties = ui.enquiry_properties(ui.load_disclosure_policy(env={})) + [
        {"name": ui.PROPERTY_DISCLOSURE_COMPLETENESS, "value": ui.COMPLETENESS_COMPLETE},
    ]
    sbom = _document([_component("alpha", "1.0.0", withheld.properties())], metadata_properties)

    errors, summary = ui.validate_sbom_disclosure(sbom)
    assert any(ui.PROPERTY_DISCLOSURE_COMPLETENESS in e for e in errors)
    assert summary["disclosure_completeness"] == ui.COMPLETENESS_INCOMPLETE_WITHHELD
    assert summary["essential_fields_withheld"] == ["alpha:license"]


def test_explaining_a_redaction_in_the_document_fails():
    sbom = _document(
        [
            _component(
                "alpha",
                "1.0.0",
                [
                    {"name": "icdev:withheld:hash_value", "value": ui.REASON_OPERATIONAL_SECURITY},
                    {"name": "icdev:unknown-detail:hash_value", "value": "sha256 of the classified payload"},
                ],
            )
        ]
    )
    errors, _ = ui.validate_sbom_disclosure(sbom)
    assert any("explains the redaction" in e for e in errors)


# ---------------------------------------------------------------------------
# the generator emits the convention
# ---------------------------------------------------------------------------


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_declared_parsers_no_longer_emit_the_conflating_literal(tmp_path):
    from tools.compliance import sbom_generator

    requirements = _write(tmp_path / "requirements.txt", "flask==3.0.0\nrequests\n")
    components = {c["name"]: c for c in sbom_generator._parse_requirements_txt(requirements)}

    assert components["flask"]["version"] == "3.0.0"
    assert components["requests"]["version"] == ui.UNKNOWN
    assert components["requests"]["version_unknown_reason"] == ui.REASON_DECLARED_WITHOUT_VERSION
    # An unpinned dependency must not carry a purl claiming a version.
    assert components["requests"]["purl"] == "pkg:pypi/requests"


def test_a_maven_version_held_by_a_parent_pom_has_its_own_reason(tmp_path):
    from tools.compliance import sbom_generator

    pom = _write(
        tmp_path / "pom.xml",
        "<project><dependencies>"
        "<dependency><groupId>com.example</groupId><artifactId>alpha</artifactId></dependency>"
        "</dependencies></project>",
    )
    component = sbom_generator._parse_pom_xml(pom)[0]

    assert component["version"] == ui.UNKNOWN
    assert component["version_unknown_reason"] == ui.REASON_VERSION_MANAGED_BY_PARENT
    assert component["purl"] == "pkg:maven/com.example/alpha"


def test_the_conflating_literal_is_no_longer_a_value_in_either_copy():
    # The literal is what this card removes; a regression would reintroduce a
    # value that says neither unknown nor withheld. Checked against the parsed
    # constants rather than the raw text, so the prose above (and the module
    # docstring, which has to name what it replaced) does not fail the test.
    import ast

    for name in ("tools", "icdev/tools"):
        path = REPO_ROOT / name / "compliance" / "sbom_generator.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        }
        assert "unspecified" not in literals, f"{path} still emits the pre-2026 literal"
        assert "managed" not in literals, f"{path} still emits the pre-2026 literal"


def test_generated_document_states_both_states_and_the_enquiry_route(tmp_path):
    from tools.compliance import sbom_generator

    project = {"id": "prc-01", "name": "Fixture", "directory_path": None}
    components = [
        {"type": "library", "name": "alpha", "version": "1.0.0", "purl": "pkg:pypi/alpha@1.0.0", "ecosystem": "python"},
        {
            "type": "library",
            "name": "beta",
            "version": ui.UNKNOWN,
            "version_unknown_reason": ui.REASON_DECLARED_WITHOUT_VERSION,
            "purl": "pkg:pypi/beta",
            "ecosystem": "python",
        },
    ]
    policy = ui.load_disclosure_policy(path=tmp_path / "absent.yaml", env={})
    policy["withhold"]["components"] = [
        {"match": {"name": "alpha"}, "field": "hash_value", "reason": ui.REASON_EXPORT_CONTROLLED}
    ]

    sbom, _ = sbom_generator._build_cyclonedx_sbom(
        project, components, spec_version="1.6", disclosure_policy=policy
    )

    by_name = {c["name"]: c for c in sbom["components"]}
    alpha = ui.Disclosure.from_cyclonedx(by_name["alpha"])
    beta = ui.Disclosure.from_cyclonedx(by_name["beta"])

    assert alpha.withheld_fields == {ui.FIELD_HASH_VALUE: ui.REASON_EXPORT_CONTROLLED}
    assert alpha.state_of(ui.FIELD_VERSION) is None
    assert beta.unknown_fields[ui.FIELD_VERSION] == ui.REASON_DECLARED_WITHOUT_VERSION
    assert by_name["beta"]["version"] == "unknown"

    metadata = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    assert metadata[ui.PROPERTY_ENQUIRY_PROCESS]
    assert metadata[ui.PROPERTY_FIELDS_WITHHELD] == "1"
    # hash_value is not essential data, so the document stays complete.
    assert metadata[ui.PROPERTY_DISCLOSURE_COMPLETENESS] == ui.COMPLETENESS_COMPLETE

    errors, summary = ui.validate_sbom_disclosure(sbom)
    assert errors == []
    assert summary["fields_withheld"] == 1


def test_a_withheld_version_replaces_the_value_in_the_document(tmp_path):
    from tools.compliance import sbom_generator

    project = {"id": "prc-01", "name": "Fixture", "directory_path": None}
    components = [
        {"type": "library", "name": "alpha", "version": "1.0.0", "purl": "pkg:pypi/alpha@1.0.0", "ecosystem": "python"}
    ]
    policy = ui.load_disclosure_policy(path=tmp_path / "absent.yaml", env={})
    policy["withhold"]["components"] = [
        {"field": "version", "reason": ui.REASON_CLASSIFICATION_RESTRICTED}
    ]

    sbom, _ = sbom_generator._build_cyclonedx_sbom(
        project, components, spec_version="1.6", disclosure_policy=policy
    )

    alpha = sbom["components"][0]
    assert alpha["version"] == "withheld"
    metadata = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    # version is essential component data, so the document says so.
    assert metadata[ui.PROPERTY_DISCLOSURE_COMPLETENESS] == ui.COMPLETENESS_INCOMPLETE_WITHHELD
    assert ui.validate_sbom_disclosure(sbom)[0] == []


def test_the_producer_element_joins_the_uniform_convention(tmp_path):
    from tools.compliance import sbom_generator

    project = {"id": "prc-01", "name": "Fixture", "directory_path": None}
    components = [
        {"type": "library", "name": "alpha", "version": "1.0.0", "purl": "pkg:pypi/alpha@1.0.0", "ecosystem": "python"}
    ]
    policy = ui.load_disclosure_policy(path=tmp_path / "absent.yaml", env={})

    sbom, _ = sbom_generator._build_cyclonedx_sbom(
        project, components, spec_version="1.6", disclosure_policy=policy
    )

    alpha = sbom["components"][0]
    values = {p["name"]: p["value"] for p in alpha["properties"]}
    # sbx-fld-02's own properties are untouched...
    assert values["icdev:component-provenance"] == "unknown"
    assert values["icdev:component-producer"] == "unknown"
    # ...and the same fact is readable through this card's uniform prefixes.
    assert values["icdev:unknown:producer"] == ui.REASON_PRODUCER_NOT_IDENTIFIABLE

    from tools.compliance import component_producer as cp

    assert cp.validate_sbom_producers(sbom)[0] == []
    assert ui.validate_sbom_disclosure(sbom)[0] == []


def test_a_generated_sbom_validates_end_to_end(icdev_db, tmp_path):
    """The full path: real database, real project, real file on disk.

    ``_build_cyclonedx_sbom`` is exercised above with a hand-built project dict;
    this proves the policy actually loads and the properties actually reach the
    artifact when the generator runs the way its ~25 call sites run it.
    """
    import sqlite3

    from tools.compliance import sbom_generator

    project = tmp_path / "prc-project"
    _write(project / "requirements.txt", "flask==3.0.0\nrequests\n")

    conn = sqlite3.connect(str(icdev_db))
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        ("prc-e2e", "Disclosure Fixture", "api", str(project)),
    )
    conn.commit()
    conn.close()

    out_file = tmp_path / "disclosure.cdx.json"
    sbom_generator.generate_sbom(project_id="prc-e2e", output_path=str(out_file), db_path=icdev_db)
    sbom = json.loads(out_file.read_text(encoding="utf-8"))

    by_name = {c["name"]: c for c in sbom["components"]}
    assert by_name["flask"]["version"] == "3.0.0"
    assert by_name["requests"]["version"] == "unknown"
    assert (
        ui.Disclosure.from_cyclonedx(by_name["requests"]).unknown_fields[ui.FIELD_VERSION]
        == ui.REASON_DECLARED_WITHOUT_VERSION
    )

    metadata = {p["name"]: p["value"] for p in sbom["metadata"]["properties"]}
    # The enquiry route sits alongside the markings that make it necessary.
    assert metadata["icdev:classification"] == "CUI // SP-CTI"
    assert metadata["icdev:distribution"].startswith("Distribution D")
    assert metadata[ui.PROPERTY_ENQUIRY_PROCESS]
    assert metadata[ui.PROPERTY_ENQUIRY_RESPONSE_DAYS] == "30"
    assert metadata[ui.PROPERTY_FIELDS_WITHHELD] == "0"
    assert metadata[ui.PROPERTY_DISCLOSURE_COMPLETENESS] == ui.COMPLETENESS_COMPLETE

    assert ui.validate_sbom_disclosure(sbom)[0] == []


# ---------------------------------------------------------------------------
# mirror parity
# ---------------------------------------------------------------------------


def test_root_and_mirror_stay_in_sync():
    root = REPO_ROOT / "tools" / "compliance" / "unknown_information.py"
    mirror = REPO_ROOT / "icdev" / "tools" / "compliance" / "unknown_information.py"
    assert mirror.exists(), "icdev/tools/compliance/unknown_information.py is missing"
    assert root.read_bytes() == mirror.read_bytes()
