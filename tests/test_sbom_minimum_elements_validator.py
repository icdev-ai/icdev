#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the SBOM 2026 minimum-elements conformance validator (sbx-sig-02).

The validator is the card's measurement instrument, so these tests pin the
measurements the rest of the card is defined against:

1. **The baseline.** Run against what the generator produced BEFORE the ``sbx``
   card, it must reproduce the gap analysis §3 matrix exactly — 2 of 17 data
   fields fully met, 0 of the practices. If this drifts, every "we improved X"
   claim on this card loses its reference point.
2. **The target.** Run against a document carrying every element the ``sbx-fld-*``
   and ``sbx-cov-*`` tasks are chartered to emit, it must report 17 of 17 and
   6 of 6. That fixture is not decoration: it is the executable specification
   those tasks build to, which is why it is asserted element-by-element.
3. **Third-party grading.** The standard targets organizations that procure
   software as much as those that produce it, so a vendor's SPDX document —
   which ICDEV did not generate and knows nothing else about — must grade.

Fixtures live in ``tests/fixtures/sbom/``.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.compliance import sbom_minimum_elements_validator as validator
from tools.compliance.sbom_minimum_elements_validator import (
    AMBIGUOUS_PLACEHOLDERS,
    DATA_FIELD_COUNT,
    ELEMENTS,
    PRACTICE_COUNT,
    STATUS_GAP,
    STATUS_MET,
    STATUS_PARTIAL,
    UNKNOWN_MARKERS,
    UnsupportedFormatError,
    validate,
    validate_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sbom"

BASELINE = FIXTURES / "baseline_cyclonedx_pre_sbx.cdx.json"
CONFORMANT = FIXTURES / "conformant_cyclonedx_1.6.cdx.json"
THIRD_PARTY_SPDX = FIXTURES / "third_party_spdx_2.3.spdx.json"


def _statuses(report):
    return {element["id"]: element["status"] for element in report["elements"]}


_DATA_FIELD_IDS = {
    element_id
    for element_id, category, _, _ in ELEMENTS
    if category == validator.CATEGORY_DATA_FIELD
}


# ─────────────────────────────────────────────────────────────────────────
# The element table itself
# ─────────────────────────────────────────────────────────────────────────


def test_element_table_matches_the_published_standard():
    """17 data fields and 6 applicable practices, no duplicates."""
    assert DATA_FIELD_COUNT == 17
    assert PRACTICE_COUNT == 6
    ids = [element_id for element_id, _, _, _ in ELEMENTS]
    assert len(ids) == len(set(ids)), "duplicate element id in ELEMENTS"


def test_access_control_is_not_scored():
    """The 2026 revision removed Access Control and folded it into Distribution."""
    ids = {element_id for element_id, _, _, _ in ELEMENTS}
    assert "access_control" not in ids
    report = validate_file(BASELINE)
    assert report["practices"]["removed_in_2026"] == ["Access Control"]
    # Both readings of the practice count have to reconcile without arithmetic:
    # the gap analysis says "0 of 7 practices" against the pre-2026 list.
    assert report["practices"]["listed_in_2021"] == 7
    assert report["practices"]["total"] == 6


# ─────────────────────────────────────────────────────────────────────────
# 1. The documented baseline
# ─────────────────────────────────────────────────────────────────────────


def test_baseline_reproduces_the_documented_gap_analysis_matrix():
    """Gap analysis §3: 2 fully met, 7 partial, 8 gap; 0 practices met."""
    report = validate_file(BASELINE)

    assert report["data_fields"] == {"met": 2, "partial": 7, "gap": 8, "total": 17}
    assert report["practices"]["met"] == 0
    assert report["conformant"] is False

    # The matrix in §3.1/§3.2 is over data fields only, so scope the
    # element-by-element comparison the same way the document does.
    data_field_ids = {
        element_id
        for element_id, category, _, _ in ELEMENTS
        if category == validator.CATEGORY_DATA_FIELD
    }
    statuses = {
        element_id: status
        for element_id, status in _statuses(report).items()
        if element_id in data_field_ids
    }

    # §3.1/§3.2 name exactly these two as MET.
    met = {element_id for element_id, status in statuses.items() if status == STATUS_MET}
    assert met == {"sbom_data_format_name", "sbom_tool_name"}

    # And exactly these seven as PARTIAL.
    partial = {element_id for element_id, status in statuses.items() if status == STATUS_PARTIAL}
    assert partial == {
        "sbom_data_format_version",
        "sbom_timestamp",
        "sbom_tool_version",
        "sbom_version",
        "component_identifiers",
        "component_name",
        "component_version",
    }


def test_live_generator_output_scores_the_current_declared_only_state(tmp_path):
    """What the generator actually emits today, on a project with no lockfile.

    This is the load-bearing half of the baseline check: the frozen fixture
    could drift away from the generator without either one being wrong. Here
    the document is built by the production code path — ``resolve_project``
    into ``_build_cyclonedx_sbom`` — with only the database write skipped.

    The pre-``sbx`` 2/17 is *not* asserted here any more, because it is no
    longer reachable: sbx-fld-01/sbx-fmt-01/sbx-sig-01 closed most of the SBOM
    Metadata block, so the same declared-only project now scores 12. That
    original measurement is preserved as a frozen fixture
    (``baseline_cyclonedx_pre_sbx.cdx.json``) and asserted by the test above —
    a document, not a number in a docstring, is what keeps it honest.

    The project deliberately has no lockfile, so resolution degrades to the
    declared parsers, and the interesting result is that this no longer costs
    the component-side elements their MET. Producer, both hash halves and
    License cannot be *populated* without resolved metadata, but sbx-prc-01
    makes the generator say so explicitly per field, and an element the
    document declares unknown is disclosed rather than omitted. That is the
    whole point of the Explicitly Identifying Unknown Information practice, so
    scoring it MET here is correct and not a leniency bug — the two remaining
    gaps are the ones no disclosure can excuse.
    """
    from tools.compliance.dependency_resolver import resolve_project
    from tools.compliance.sbom_generator import DECLARED_PARSERS, _build_cyclonedx_sbom

    project = tmp_path / "declared-only"
    project.mkdir()
    (project / "requirements.txt").write_text("flask==3.0.2\nrequests\n", encoding="utf-8")

    resolution = resolve_project(project, declared_parsers=DECLARED_PARSERS)
    document, _count = _build_cyclonedx_sbom(
        {"id": "demo", "name": "Demo"},
        resolution["components"],
        coverage=resolution["coverage"],
    )

    report = validate(document)
    statuses = _statuses(report)

    assert report["data_fields"] == {"met": 12, "partial": 3, "gap": 2, "total": 17}

    # Name the met set rather than only counting it, so a regression that
    # trades one element for another still fails.
    met = {
        element_id
        for element_id, status in statuses.items()
        if status == STATUS_MET and element_id in _DATA_FIELD_IDS
    }
    assert met == {
        "sbom_author",
        "sbom_data_format_name",
        "sbom_data_format_version",
        "sbom_generation_context",
        "sbom_tool_name",
        "sbom_tool_version",
        "sbom_version",
        "component_version",
        # Unresolvable, and therefore explicitly marked unknown — which is a
        # conforming answer, not a missing one. See the loop below.
        "component_producer",
        "component_hash_value",
        "component_hash_algorithm",
        "component_license",
    }

    # The two the pre-sbx baseline already had are still met — the metadata
    # work added to that set, it did not churn it.
    assert statuses["sbom_data_format_name"] == STATUS_MET
    assert statuses["sbom_tool_name"] == STATUS_MET

    # The four component elements a declared-only project cannot populate are
    # MET, not GAP, and the distinction is the whole point of the 2026 revision:
    # where the author cannot determine a value the standard requires it to be
    # marked explicitly unknown, and silence is what it forbids. sbx-fld-02/03/04
    # emit that marker into `icdev:component-*` properties, because CycloneDX's
    # `supplier`, `hashes[]` and `licenses[]` have no member that can hold it.
    # Grading only the native field would report the conforming answer as the
    # forbidden one — the same reading this file already applies to SPDX's
    # NOASSERTION in `test_spdx_noassertion_is_read_as_an_explicit_unknown...`.
    for element_id in ("component_producer", "component_hash_value",
                       "component_hash_algorithm", "component_license"):
        assert statuses[element_id] == STATUS_MET, element_id
        rationale = report["elements_by_id"][element_id]["rationale"].lower()
        assert "unknown" in rationale or "withheld" in rationale, element_id

    # sbx-cov-02 is the one genuinely outstanding component element: a flat
    # list expresses no relationship, and no marker can stand in for a graph.
    assert statuses["component_dependency_relationship"] == STATUS_GAP

    assert report["practices"]["met"] == 2


def test_coverage_practice_moved_off_gap_when_sbx_cov_01_landed(tmp_path):
    """sbx-cov-01 is visible in the score, and it is project-dependent.

    The gap analysis's headline "0 of 7 practices" predates sbx-cov-01. That
    task did not make Coverage MET for every project — it made the document
    *state* its completeness, so a resolved project now reaches MET and an
    unresolvable one reaches PARTIAL instead of GAP. Both are movement off the
    baseline and the test distinguishes them, because conflating them is how a
    partial win gets reported as a whole one.

    This deliberately no longer asserts that the other five practices are
    unmoved: sbx-prc-02 has since made Accommodation of Updates MET and
    sbx-fmt-01 has made Machine-Processable Data MET. Pinning "everything else
    is untouched" turned this into a tripwire for its own sibling tasks rather
    than a test of Coverage, so it now pins the resolved/degraded contrast,
    which is the claim actually attributable to sbx-cov-01.
    """
    from tools.compliance.dependency_resolver import resolve_project
    from tools.compliance.sbom_generator import DECLARED_PARSERS, _build_cyclonedx_sbom

    resolved = tmp_path / "resolved"
    resolved.mkdir()
    (resolved / "package.json").write_text(
        json.dumps({"name": "fixture", "version": "1.0.0", "dependencies": {"left-pad": "^1.3.0"}}),
        encoding="utf-8",
    )
    (resolved / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "fixture", "version": "1.0.0", "dependencies": {"left-pad": "^1.3.0"}},
                    "node_modules/left-pad": {"version": "1.3.0"},
                },
            }
        ),
        encoding="utf-8",
    )

    resolution = resolve_project(resolved, declared_parsers=DECLARED_PARSERS)
    document, _count = _build_cyclonedx_sbom(
        {"id": "demo", "name": "Demo"}, resolution["components"], coverage=resolution["coverage"]
    )
    statuses = _statuses(validate(document))
    assert statuses["coverage"] == STATUS_MET

    # The discriminating half: the SAME generator on a project that cannot
    # resolve scores Coverage PARTIAL, not MET and not the original GAP.
    # Asserting only the MET above would let a change that hard-codes Coverage
    # to MET pass, which is precisely the partial-win-reported-as-whole-win
    # failure this test exists to catch.
    declared_only = tmp_path / "declared-only-for-coverage"
    declared_only.mkdir()
    (declared_only / "requirements.txt").write_text("flask==3.0.2\n", encoding="utf-8")

    degraded = resolve_project(declared_only, declared_parsers=DECLARED_PARSERS)
    degraded_document, _ = _build_cyclonedx_sbom(
        {"id": "demo", "name": "Demo"}, degraded["components"], coverage=degraded["coverage"]
    )
    assert _statuses(validate(degraded_document))["coverage"] == STATUS_PARTIAL


# ─────────────────────────────────────────────────────────────────────────
# 2. The target — what sbx-fld-* and sbx-cov-* must produce
# ─────────────────────────────────────────────────────────────────────────


def test_conformant_document_scores_seventeen_of_seventeen():
    report = validate_file(CONFORMANT)
    assert report["data_fields"] == {"met": 17, "partial": 0, "gap": 0, "total": 17}
    assert report["practices"]["met"] == 6
    assert report["conformant"] is True
    assert report["score"]["weighted_pct"] == 100.0


@pytest.mark.parametrize("element_id", [element_id for element_id, _, _, _ in ELEMENTS])
def test_every_element_is_individually_reachable(element_id):
    """No element is unreachable — each is MET on the conformant fixture.

    A scorer that can never return MET would silently cap the card at 16/17,
    and the aggregate assertion above would not say which one.
    """
    assert _statuses(validate_file(CONFORMANT))[element_id] == STATUS_MET


def test_unknown_and_withheld_are_scored_as_different_states():
    """sbx-prc-01's core requirement, enforced at the grader.

    The conformant fixture carries one explicitly-unknown producer and one
    explicitly-withheld license, and both count as conforming. Replacing
    either with an ambiguous placeholder must break the practice.
    """
    raw = json.loads(CONFORMANT.read_text(encoding="utf-8"))
    statuses = _statuses(validate(raw))
    assert statuses["component_producer"] == STATUS_MET
    assert statuses["component_license"] == STATUS_MET
    assert statuses["explicitly_identifying_unknown_information"] == STATUS_MET

    raw["components"][1]["supplier"]["name"] = "unspecified"
    degraded = _statuses(validate(raw))
    assert degraded["component_producer"] == STATUS_PARTIAL
    assert degraded["explicitly_identifying_unknown_information"] == STATUS_GAP


def test_withheld_without_a_query_process_is_not_fully_met():
    """The element requires a documented route for recipients to ask."""
    raw = json.loads(CONFORMANT.read_text(encoding="utf-8"))
    raw["metadata"]["properties"] = [
        prop
        for prop in raw["metadata"]["properties"]
        if prop["name"] != validator.PROP_UNKNOWN_CONTACT
    ]
    assert _statuses(validate(raw))["explicitly_identifying_unknown_information"] == STATUS_PARTIAL


def test_prc_01_vocabularies_do_not_overlap():
    """The three vocabularies must partition, or a value would score two ways."""
    assert not (UNKNOWN_MARKERS & validator.WITHHELD_MARKERS)
    assert not (UNKNOWN_MARKERS & AMBIGUOUS_PLACEHOLDERS)
    assert not (validator.WITHHELD_MARKERS & AMBIGUOUS_PLACEHOLDERS)
    # The two literals the current generator emits must be graded ambiguous,
    # not unknown — that is the whole point of the distinction.
    assert "unspecified" in AMBIGUOUS_PLACEHOLDERS
    assert "managed" in AMBIGUOUS_PLACEHOLDERS


# ─────────────────────────────────────────────────────────────────────────
# 3. Third-party grading
# ─────────────────────────────────────────────────────────────────────────


def test_third_party_spdx_document_is_graded():
    """A vendor SPDX 2.3 file ICDEV did not generate scores on every element."""
    report = validate_file(THIRD_PARTY_SPDX)

    assert report["document"]["format_name"] == "SPDX"
    assert report["document"]["format_version"] == "SPDX-2.3"
    assert report["document"]["component_count"] == 4
    assert len(report["elements"]) == DATA_FIELD_COUNT + PRACTICE_COUNT

    statuses = _statuses(report)
    # Things this vendor does well.
    assert statuses["sbom_author"] == STATUS_MET  # Organization: creator, not the Tool:
    assert statuses["component_producer"] == STATUS_MET  # incl. one NOASSERTION
    assert statuses["component_dependency_relationship"] == STATUS_MET  # relationships[]
    assert statuses["component_identifiers"] == STATUS_MET  # purl + cpe23Type
    assert statuses["distribution_and_delivery"] == STATUS_MET  # versioned namespace
    # ...and things it does not.
    assert statuses["sbom_author_signature"] == STATUS_GAP
    assert statuses["sbom_generation_context"] == STATUS_GAP
    assert statuses["coverage"] == STATUS_GAP
    # One package is hashed with SHA-1, which IANA names and NIST does not approve.
    assert statuses["component_hash_algorithm"] == STATUS_PARTIAL


def test_spdx_noassertion_is_read_as_an_explicit_unknown_not_as_absent():
    """SPDX's own unknown spelling has to satisfy the element, not fail it."""
    raw = json.loads(THIRD_PARTY_SPDX.read_text(encoding="utf-8"))
    blob = next(p for p in raw["packages"] if p["name"] == "vendored-telemetry-agent")
    assert blob["supplier"] == "NOASSERTION"
    assert _statuses(validate(raw))["component_producer"] == STATUS_MET

    # Dropping the field entirely is a different, worse state.
    del blob["supplier"]
    assert _statuses(validate(raw))["component_producer"] == STATUS_PARTIAL


def test_prc_01_disclosure_vocabulary_agrees_with_the_validator():
    """The two unknown/withheld vocabularies must not drift apart.

    sbx-prc-01 (``unknown_information``) writes the markers and this validator
    grades them, so a disagreement means ICDEV emits a disclosure its own
    conformance tool scores as a gap — the failure would surface as an
    unexplained score drop, not as an error.

    The gap analysis originally directed sbx-prc-01 to *import* these sets.
    It landed restating them instead, and the restatement currently agrees.
    Rather than leave that as prose nobody enforces, this pins the three
    relationships that actually have to hold. Deliberately a compatibility
    assertion and not an equality one: the validator reads third-party
    documents, so it must know spellings (``noassertion``, ``redacted``) that
    ICDEV never emits. Only the containment direction is required.
    """
    from tools.compliance import unknown_information as prc01

    # 1. What prc-01 writes, the validator reads as the state prc-01 meant.
    assert prc01.SENTINELS[prc01.UNKNOWN] in UNKNOWN_MARKERS
    assert prc01.SENTINELS[prc01.WITHHELD] in validator.WITHHELD_MARKERS

    # 2. Every legacy placeholder prc-01 retired is one the validator still
    #    penalises, so retiring a value cannot quietly upgrade a document.
    assert set(prc01.LEGACY_SENTINELS) <= set(AMBIGUOUS_PLACEHOLDERS)

    # 3. The two states stay disjoint on both sides. sbx-prc-01's whole point
    #    is that "we don't know" and "we won't say" are different answers.
    assert not (UNKNOWN_MARKERS & validator.WITHHELD_MARKERS)
    assert not (set(prc01.UNKNOWN_REASONS) & set(prc01.WITHHELD_REASONS))


def test_spdx_organization_prefix_is_stripped_from_the_producer():
    raw = json.loads(THIRD_PARTY_SPDX.read_text(encoding="utf-8"))
    normalized = validator.read_document(raw)
    producers = {c.producer for c in normalized.components}
    assert "The OpenSSL Project" in producers
    assert not any(p.startswith(("Organization:", "Person:")) for p in producers)


# ─────────────────────────────────────────────────────────────────────────
# Format handling
# ─────────────────────────────────────────────────────────────────────────


def test_unsupported_formats_are_declined_by_name_not_scored_as_gaps():
    """Grading a format the reader cannot honestly read would invent findings."""
    with pytest.raises(UnsupportedFormatError, match="SPDX 3"):
        validator.detect_format({"@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld"})

    with pytest.raises(UnsupportedFormatError, match="Unrecognised"):
        validator.detect_format({"softwareIdentity": {"name": "swid-tag"}})


def test_superseded_format_versions_are_flagged_on_both_formats():
    baseline = _statuses(validate_file(BASELINE))
    assert baseline["sbom_data_format_version"] == STATUS_PARTIAL  # CycloneDX 1.4
    assert baseline["machine_processable_data"] == STATUS_PARTIAL

    raw = json.loads(THIRD_PARTY_SPDX.read_text(encoding="utf-8"))
    raw["spdxVersion"] = "SPDX-2.2"
    statuses = _statuses(validate(raw))
    assert statuses["sbom_data_format_version"] == STATUS_PARTIAL
    assert statuses["machine_processable_data"] == STATUS_PARTIAL


def test_rfc_9557_annotation_is_what_separates_met_from_partial():
    raw = json.loads(CONFORMANT.read_text(encoding="utf-8"))
    assert _statuses(validate(raw))["sbom_timestamp"] == STATUS_MET

    raw["metadata"]["timestamp"] = "2026-08-08T05:30:58Z"  # RFC 3339 only
    assert _statuses(validate(raw))["sbom_timestamp"] == STATUS_PARTIAL

    raw["metadata"]["timestamp"] = "08/08/2026 05:30"
    assert _statuses(validate(raw))["sbom_timestamp"] == STATUS_GAP


def test_hash_algorithm_naming_is_folded_across_both_format_spellings():
    """CycloneDX writes SHA-256, SPDX writes SHA256, the registry entry is one."""
    assert validator._normalize_hash_name("SHA-256") == validator._normalize_hash_name("SHA256")
    assert validator._normalize_hash_name("sha_512") == "SHA512"
    assert validator._normalize_hash_name("SHA-256") in validator.NIST_APPROVED_HASHES
    assert "MD5" in validator.IANA_HASH_NAMES
    assert "MD5" not in validator.NIST_APPROVED_HASHES


def test_unapproved_signature_algorithm_is_partial_not_met():
    raw = json.loads(CONFORMANT.read_text(encoding="utf-8"))
    raw["signature"]["algorithm"] = "HS256"  # symmetric; not an author attribution
    assert _statuses(validate(raw))["sbom_author_signature"] == STATUS_PARTIAL

    del raw["signature"]
    assert _statuses(validate(raw))["sbom_author_signature"] == STATUS_GAP


def test_dependency_graph_must_cover_every_component():
    """A partly-wired graph is not a graph a recipient can trust."""
    raw = json.loads(CONFORMANT.read_text(encoding="utf-8"))
    raw["dependencies"] = [{"ref": "icdev-platform", "dependsOn": ["pkg-flask-3.0.2"]}]
    assert _statuses(validate(raw))["component_dependency_relationship"] == STATUS_PARTIAL


def test_weakest_composition_decides_the_coverage_verdict():
    """One incomplete assembly makes the component set incomplete."""
    raw = json.loads(CONFORMANT.read_text(encoding="utf-8"))
    raw["compositions"].append({"aggregate": "incomplete", "assemblies": ["pkg-flask-3.0.2"]})
    assert _statuses(validate(raw))["coverage"] == STATUS_PARTIAL


# ─────────────────────────────────────────────────────────────────────────
# CLI contract — sbx-gov-01 gates on these exit codes
# ─────────────────────────────────────────────────────────────────────────


def _run_cli(*args):
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "compliance" / "sbom_minimum_elements_validator.py"),
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )


def test_cli_emits_parseable_json():
    result = _run_cli("--sbom", str(CONFORMANT), "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["conformant"] is True
    assert report["standard_version"] == "2.1"
    assert report["classification"] == "CUI // SP-CTI"


def test_cli_exit_codes_are_gateable():
    """sbx-gov-01 blocks on these, so they are part of the contract."""
    assert _run_cli("--sbom", str(CONFORMANT), "--json", "--require-conformant").returncode == 0
    assert _run_cli("--sbom", str(BASELINE), "--json", "--require-conformant").returncode == 1
    assert _run_cli("--sbom", str(BASELINE), "--json", "--min-score", "80").returncode == 1
    assert _run_cli("--sbom", str(BASELINE), "--json", "--min-score", "10").returncode == 0
    # A missing or unreadable document is exit 2 — distinguishable from a low
    # score, so a gate cannot mistake "no SBOM" for "a bad SBOM".
    assert _run_cli("--sbom", str(FIXTURES / "does-not-exist.json"), "--json").returncode == 2


def test_cli_human_output_carries_classification_markings():
    result = _run_cli("--sbom", str(BASELINE))
    assert result.returncode == 0
    assert result.stdout.startswith("CUI // SP-CTI")
    assert result.stdout.rstrip().endswith("CUI // SP-CTI")


# ─────────────────────────────────────────────────────────────────────────
# MCP gateway registration
#
# Registering a tool is two edits in two files that nothing checks agrees:
# tool_registry.py names a module and a handler, gap_handlers.py defines it.
# A typo in either is invisible until a caller asks for the tool.
# ─────────────────────────────────────────────────────────────────────────

MCP_TOOL_NAME = "sbom_validate_minimum_elements"


def test_mcp_registry_entry_resolves_to_a_real_handler():
    import importlib

    from tools.mcp.tool_registry import TOOL_REGISTRY

    assert MCP_TOOL_NAME in TOOL_REGISTRY, f"{MCP_TOOL_NAME} not registered in tool_registry.py"
    entry = TOOL_REGISTRY[MCP_TOOL_NAME]
    assert entry["category"] == "compliance"

    module = importlib.import_module(entry["module"])
    handler = getattr(module, entry["handler"], None)
    assert callable(handler), f"{entry['module']}.{entry['handler']} is not callable"

    required = entry["input_schema"]["required"]
    assert required == ["sbom_path"], "third-party SBOMs have no project_id, so only the path is required"


def test_mcp_handler_returns_the_report_and_names_its_failures():
    from tools.mcp.gap_handlers import handle_sbom_validate_minimum_elements as handler

    report = handler({"sbom_path": str(THIRD_PARTY_SPDX)})
    assert report["document"]["format_name"] == "SPDX"
    assert report["data_fields"]["total"] == DATA_FIELD_COUNT
    assert report["conformant"] is False

    # An unreadable document must not come back looking like a zero score,
    # or a caller cannot tell "no SBOM" from "a bad SBOM".
    missing = handler({"sbom_path": str(FIXTURES / "nope.json")})
    assert "error" in missing
    assert "data_fields" not in missing

    with pytest.raises(ValueError, match="sbom_path"):
        handler({})


# ─────────────────────────────────────────────────────────────────────────
# Interop with sbx-gov-01's gate
#
# tools/compliance/sbom_conformance_gate.py was written against this module
# before either had landed, and delegates to it "the moment that module is
# importable". Nothing in either module's own tests would catch the two
# disagreeing, and the failure is silent rather than loud: the gate falls back
# to its narrow built-in scorer and reports a plausible number that is not
# this validator's. So the contract it reaches for is asserted here.
# ─────────────────────────────────────────────────────────────────────────

#: The names sbx-gov-01 looks up, copied from its DATA_FIELD_ELEMENTS.
GOV_01_DATA_FIELD_ELEMENTS = (
    "sbom_author",
    "sbom_author_signature",
    "sbom_data_format_name",
    "sbom_data_format_version",
    "sbom_generation_context",
    "sbom_timestamp",
    "sbom_tool_name",
    "sbom_tool_version",
    "sbom_version",
    "component_producer",
    "component_dependency_relationship",
    "component_hash_value",
    "component_hash_algorithm",
    "component_identifiers",
    "component_license",
    "component_name",
    "component_version",
)


def test_gate_entry_point_name_is_exposed():
    """sbx-gov-01 imports `validate_sbom`, not `validate_file`.

    It calls that name with an already-parsed document, having read the file
    itself for its own path-dependent checks — so accepting only a path is not
    enough, and the two shapes must agree on the verdict. This asserted alias
    identity once; that passed while the gate died on ``Path(dict)``.
    """
    from_path = validator.validate_sbom(THIRD_PARTY_SPDX)
    assert from_path["data_fields"]["total"] == DATA_FIELD_COUNT

    parsed = json.loads(Path(THIRD_PARTY_SPDX).read_text(encoding="utf-8"))
    from_document = validator.validate_sbom(parsed)
    assert from_document["data_fields"] == from_path["data_fields"]
    assert from_document["practices"]["met"] == from_path["practices"]["met"]


def test_gov_01_adapter_logic_finds_every_element_it_looks_for():
    """Replay the gate's extraction against a real report.

    The gate reads `elements` expecting a dict keyed by element name, then
    `elements_met` / `elements_total` for the aggregate. A list-of-dicts fails
    its `isinstance(..., dict)` check and scores nothing — which is exactly
    the silent miss this test exists to prevent.
    """
    report = validate_file(CONFORMANT)

    elements = report["elements_by_id"]
    assert isinstance(elements, dict)
    recognized = [name for name in GOV_01_DATA_FIELD_ELEMENTS if name in elements]
    assert len(recognized) == len(GOV_01_DATA_FIELD_ELEMENTS), (
        "element vocabulary drifted from sbx-gov-01's: missing "
        f"{sorted(set(GOV_01_DATA_FIELD_ELEMENTS) - set(elements))}"
    )
    for name in GOV_01_DATA_FIELD_ELEMENTS:
        assert elements[name]["status"] == STATUS_MET

    assert report["elements_met"] == report["elements_total"] == 23


def test_gate_aggregate_counts_practices_too():
    """The gate defers the aggregate to this module because it scores all 23."""
    report = validate_file(BASELINE)
    assert report["elements_total"] == DATA_FIELD_COUNT + PRACTICE_COUNT
    assert report["elements_met"] == (
        report["data_fields"]["met"] + report["practices"]["met"]
    )
    assert report["elements_met"] == 2  # 2 data fields, 0 practices


def test_keyed_and_list_element_views_cannot_drift():
    report = validate_file(THIRD_PARTY_SPDX)
    assert len(report["elements_by_id"]) == len(report["elements"])
    for element in report["elements"]:
        assert report["elements_by_id"][element["id"]] is element


# ─────────────────────────────────────────────────────────────────────────
# Security gate registration
# ─────────────────────────────────────────────────────────────────────────


def test_conformance_gate_is_declared_with_thresholds():
    """sbx-gov-01 wires these in; the vocabulary is agreed here first."""
    import yaml

    gates = yaml.safe_load(
        (REPO_ROOT / "args" / "security_gates.yaml").read_text(encoding="utf-8")
    )
    section = gates.get("sbom_conformance")
    assert section is not None, "sbom_conformance section missing from args/security_gates.yaml"
    assert "sbom_conformance_below_floor" in section["blocking"]
    assert "sbom_conformance_regressed" in section["blocking"]

    thresholds = section["thresholds"]
    # The fully-met thresholds must equal the standard, not a softened target.
    assert thresholds["min_data_fields_met"] == DATA_FIELD_COUNT
    assert thresholds["min_practices_met"] == PRACTICE_COUNT
    # A vendor's SBOM is an accept/reject decision, not a build ICDEV controls.
    assert thresholds["min_third_party_weighted_score"] < thresholds["min_weighted_score"]


# ─────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────


def test_recorded_assessment_round_trips(icdev_db):
    """--record appends one row that a gate can read a prior score from."""
    report = validate_file(CONFORMANT)
    validator.record_assessment(report, project_id="sbx-test", db_path=icdev_db)

    from tools.db.storage import get_connection

    conn = get_connection(str(icdev_db))
    try:
        row = conn.execute(
            """SELECT project_id, format_name, format_version, data_fields_met,
                      practices_met, weighted_score, conformant, elements_json,
                      validator_version, classification
               FROM sbom_conformance_assessments WHERE project_id = %s""",
            ("sbx-test",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["format_name"] == "CycloneDX"
    assert row["format_version"] == "1.6"
    assert row["data_fields_met"] == 17
    assert row["practices_met"] == 6
    assert row["weighted_score"] == 100.0
    assert row["conformant"] == 1
    assert row["classification"] == "CUI"
    assert len(json.loads(row["elements_json"])) == DATA_FIELD_COUNT + PRACTICE_COUNT


def test_importing_the_validator_does_not_require_a_database():
    """sbx-fmt-02 imports this from four assessors; none of them wants a DB.

    ``get_connection`` is imported inside ``record_assessment`` for exactly
    this reason, and a module-level import would undo it silently.
    """
    source = (
        REPO_ROOT / "tools" / "compliance" / "sbom_minimum_elements_validator.py"
    ).read_text(encoding="utf-8")
    header = source.split("def record_assessment", 1)[0]
    assert "from tools.db.storage import" not in header
    assert "from tools.db.storage import get_connection" in source


# ─────────────────────────────────────────────────────────────────────────
# The migration behind that persistence
#
# Same discipline as tests/test_sbom_2026_schema.py: apply the REAL migration
# file rather than trusting the conftest copy, then assert the two agree. A
# hand-copied schema in the harness is how a test starts passing against a
# table shape that no longer exists in production.
# ─────────────────────────────────────────────────────────────────────────

MIGRATION_VERSION = "20260808053058"

#: The shape sbx-fnd-02 leaves behind, which this migration lands on top of.
PRE_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS sbom_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT    NOT NULL,
    version         TEXT,
    format          TEXT,
    file_path       TEXT,
    component_count INTEGER DEFAULT 0,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    generated_at    TEXT    DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def migrated_sqlite_db(tmp_path, monkeypatch):
    """A pre-migration SQLite database with the real migration applied."""
    import sqlite3

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.migration_runner import MigrationRunner

    db_path = tmp_path / "sbom_conformance.db"
    seed = sqlite3.connect(str(db_path))
    seed.executescript(PRE_MIGRATION_DDL)
    seed.commit()
    seed.close()

    runner = MigrationRunner(db_path=db_path, engine="sqlite")
    runner.ensure_migrations_table()
    migration = next(
        (m for m in runner.discover_migrations() if m["version"] == MIGRATION_VERSION), None
    )
    assert migration is not None, (
        f"migration {MIGRATION_VERSION} is not discoverable — a directory with neither "
        "up.sql nor up.py is skipped silently by discover_migrations and never runs"
    )
    result = runner.apply_migration(migration)
    assert result["success"], f"migration failed on SQLite: {result.get('error')}"
    return db_path


def test_migration_creates_the_assessment_table(migrated_sqlite_db):
    import sqlite3

    conn = sqlite3.connect(str(migrated_sqlite_db))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sbom_conformance_assessments)")}
    finally:
        conn.close()

    # RLS columns are the ones whose absence only shows up in a request
    # context — every query from the browser would raise UndefinedColumn.
    assert {"classification", "tenant_id"} <= columns
    assert {
        "document_sha256",
        "data_fields_met",
        "practices_met",
        "weighted_score",
        "conformant",
        "elements_json",
    } <= columns


def test_conftest_schema_matches_the_migrated_schema(migrated_sqlite_db, tmp_path):
    """MINIMAL_ICDEV_SCHEMA must declare exactly what the migration produces."""
    import sqlite3

    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    live = sqlite3.connect(str(migrated_sqlite_db))
    conftest_db = sqlite3.connect(str(tmp_path / "conftest_shape.db"))
    try:
        conftest_db.executescript(MINIMAL_ICDEV_SCHEMA)
        conftest_db.commit()
        migrated_columns = {
            row[1] for row in live.execute("PRAGMA table_info(sbom_conformance_assessments)")
        }
        conftest_columns = {
            row[1] for row in conftest_db.execute("PRAGMA table_info(sbom_conformance_assessments)")
        }
    finally:
        live.close()
        conftest_db.close()

    assert conftest_columns == migrated_columns, (
        "conftest MINIMAL_ICDEV_SCHEMA and migration "
        f"{MIGRATION_VERSION} disagree on sbom_conformance_assessments: "
        f"only in conftest {sorted(conftest_columns - migrated_columns)}, "
        f"only in migration {sorted(migrated_columns - conftest_columns)}"
    )


def test_assessment_table_is_registered_as_append_only():
    """Compliance evidence: a past score is the basis of a past decision."""
    hook = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
    assert "sbom_conformance_assessments" in hook, (
        "sbom_conformance_assessments must be in APPEND_ONLY_TABLES in "
        ".claude/hooks/pre_tool_use.py"
    )


def test_migration_is_mirrored_into_the_icdev_package():
    """The runner reads its own mirror; an unmirrored migration never runs."""
    root = REPO_ROOT / "tools" / "db" / "migrations" / f"{MIGRATION_VERSION}_sbom_conformance_assessments"
    mirror = REPO_ROOT / "icdev" / "tools" / "db" / "migrations" / f"{MIGRATION_VERSION}_sbom_conformance_assessments"
    assert mirror.is_dir(), f"migration not mirrored to {mirror}"
    for name in ("up.sql", "down.sql", "meta.json"):
        assert (root / name).read_bytes() == (mirror / name).read_bytes(), f"{name} drifted"


# ─────────────────────────────────────────────────────────────────────────
# Mirror parity
# ─────────────────────────────────────────────────────────────────────────


def test_root_and_icdev_copies_are_identical():
    """Same rule as sbom_generator.py: both namespaces, one behaviour."""
    root = REPO_ROOT / "tools" / "compliance" / "sbom_minimum_elements_validator.py"
    mirror = REPO_ROOT / "icdev" / "tools" / "compliance" / "sbom_minimum_elements_validator.py"
    assert mirror.is_file(), "icdev/ mirror missing — run companion sync"
    assert root.read_bytes() == mirror.read_bytes()


def test_both_import_namespaces_expose_the_api():
    import importlib

    for module_name in (
        "tools.compliance.sbom_minimum_elements_validator",
        "icdev.tools.compliance.sbom_minimum_elements_validator",
    ):
        module = importlib.import_module(module_name)
        assert callable(module.validate)
        assert callable(module.validate_file)
        assert len(module.ELEMENTS) == 23
