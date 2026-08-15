# CUI // SP-CTI
"""tools/quality/outline_contract.py — required-section validation (trust-struct-02).

Red-first discipline (D396/D397): every test below is written so that it FAILS
if the check it covers is neutered. In particular ``test_*_would_fail_if_*``
name the specific defect the assertion exists to catch — a check that cannot
fail carries zero bits.
"""
from __future__ import annotations

import pytest

from tools.quality.outline_contract import (
    ISSUE_MISSING,
    ISSUE_OUT_OF_ORDER,
    ISSUE_UNKNOWN,
    OUTLINE_ISSUES,
    OutlineContract,
    check_outline,
    contract_from_sections,
    get_contract,
    heading_keys,
    list_contracts,
    normalize_heading,
    outline_findings,
    section_keys,
    section_label,
    strip_enumerator,
)

SSP = OutlineContract(
    artifact_type="test_ssp",
    required=("System Overview", "System Boundary", "Data Flows"),
    source="test",
)


def _sec(heading: str, content: str = "body") -> dict:
    return {"heading": heading, "content": content}


def _issues(findings: list[dict]) -> list[tuple[str, str]]:
    return [(f["issue"], f["item_number"]) for f in findings]


# ── Heading normalisation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("System Boundary", "system boundary"),
        ("## System Boundary", "system boundary"),
        ("  System   Boundary  ", "system boundary"),
        ("System Boundary:", "system boundary"),
        ("Data Flows & Information Types", "data flows and information types"),
        ("", ""),
    ],
)
def test_normalize_heading_removes_formatting_not_words(raw, expected):
    assert normalize_heading(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3. System Boundary", "System Boundary"),
        ("3.2.1 System Boundary", "System Boundary"),
        ("L.3.2 Technical Approach", "Technical Approach"),
        ("Part 2.1 Statefulness", "Statefulness"),
        ("Appendix A - Architecture", "Architecture"),
        ("Section 5: Findings", "Findings"),
        ("System Boundary", "System Boundary"),
    ],
)
def test_strip_enumerator(raw, expected):
    assert strip_enumerator(raw) == expected


def test_bare_enumerator_is_kept_because_rfi_parts_are_labelled_that_way():
    # "6.1" IS the heading for an RFI section. Stripping it would leave nothing
    # to match on and every RFI part would report missing_section.
    assert strip_enumerator("6.1") == "6.1"
    assert "6 1" in heading_keys("6.1")


def test_enumerator_stripping_does_not_eat_a_leading_digit_of_a_word():
    # Regression: without a boundary lookahead, "3D Printing" normalises to
    # "D Printing" and stops matching its own contract entry.
    assert strip_enumerator("3D Printing Overview") == "3D Printing Overview"


def test_heading_keys_carries_both_forms_so_a_contract_may_name_either():
    keys = heading_keys("3. System Boundary")
    assert "3 system boundary" in keys
    assert "system boundary" in keys


# ── Section model / list_sections payloads ────────────────────────────────────


def test_section_label_precedence_matches_the_other_guards():
    assert section_label({"item_number": "2.1", "title": "T", "id": "x"}) == "2.1"
    assert section_label({"heading": "H", "title": "T"}) == "H"
    assert section_label({"section_number": "L.3"}) == "L.3"
    assert section_label({}) == "?"


def test_section_matches_on_number_or_title():
    # An rfi_workbench_sections row carries both; a contract naming either hits.
    row = {"item_number": "6.1", "title": "Gap & Omission Questions"}
    keys = section_keys(row)
    assert "6 1" in keys
    assert "gap and omission questions" in keys


def test_opaque_id_is_never_treated_as_a_heading():
    row = {"id": "3f2a-uuid-9c", "heading": "Purpose"}
    assert "3f2a uuid 9c" not in section_keys(row)


# ── missing_section ───────────────────────────────────────────────────────────


def test_complete_ordered_draft_produces_no_findings():
    secs = [_sec("System Overview"), _sec("System Boundary"), _sec("Data Flows")]
    assert outline_findings(secs, SSP) == []


def test_markdown_and_numbering_noise_still_passes():
    secs = [
        _sec("## 1. System Overview"),
        _sec("## 2. System Boundary:"),
        _sec("## 3. Data Flows"),
    ]
    assert outline_findings(secs, SSP) == []


def test_missing_section_is_reported_with_the_shared_finding_shape():
    secs = [_sec("System Overview"), _sec("Data Flows")]
    findings = outline_findings(secs, SSP)
    assert _issues(findings) == [(ISSUE_MISSING, "System Boundary")]
    f = findings[0]
    assert set(f) == {"item_number", "issue", "detail"}
    assert isinstance(f["detail"], list)
    assert all(isinstance(d, str) for d in f["detail"])


def test_empty_draft_reports_every_required_section_missing():
    findings = outline_findings([], SSP)
    assert [f["issue"] for f in findings] == [ISSUE_MISSING] * 3


def test_a_section_with_no_content_still_counts_as_present():
    # The outline check is about SHAPE. An empty required section is a
    # placeholder/citation defect, and those guards own it — reporting it here
    # too would double-count one defect across two gates.
    secs = [_sec("System Overview", ""), _sec("System Boundary", ""), _sec("Data Flows", "")]
    assert outline_findings(secs, SSP) == []


# ── unknown_section ───────────────────────────────────────────────────────────


def test_invented_section_is_reported():
    secs = [
        _sec("System Overview"), _sec("System Boundary"),
        _sec("Data Flows"), _sec("Marketing Blurb"),
    ]
    assert _issues(outline_findings(secs, SSP)) == [(ISSUE_UNKNOWN, "Marketing Blurb")]


def test_optional_section_is_not_unknown_and_not_required():
    contract = OutlineContract(
        artifact_type="t",
        required=("Purpose", "Procedure"),
        optional=("References",),
    )
    secs = [_sec("Purpose"), _sec("Procedure"), _sec("References")]
    assert outline_findings(secs, contract) == []
    # ...and its absence is not a missing_section.
    assert outline_findings([_sec("Purpose"), _sec("Procedure")], contract) == []


def test_allow_unknown_suppresses_only_unknown_not_missing():
    contract = OutlineContract(
        artifact_type="t", required=("Purpose", "Procedure"), allow_unknown=True,
    )
    secs = [_sec("Purpose"), _sec("Improvised Section")]
    assert _issues(outline_findings(secs, contract)) == [(ISSUE_MISSING, "Procedure")]


def test_would_fail_if_allow_unknown_silently_disabled_the_whole_check():
    # allow_unknown is the RFI escape hatch. If it ever short-circuited the
    # whole validator, this draft — which is missing both required sections —
    # would report clean.
    contract = OutlineContract(
        artifact_type="t", required=("Purpose", "Procedure"), allow_unknown=True,
    )
    assert len(outline_findings([_sec("Something Else")], contract)) == 2


# ── section_out_of_order ──────────────────────────────────────────────────────


def test_swapped_sections_report_out_of_order():
    secs = [_sec("System Boundary"), _sec("System Overview"), _sec("Data Flows")]
    findings = outline_findings(secs, SSP)
    assert [f["issue"] for f in findings] == [ISSUE_OUT_OF_ORDER]
    assert findings[0]["item_number"] in {"System Overview", "System Boundary"}


def test_one_displaced_section_is_reported_once_not_cascaded():
    contract = OutlineContract(artifact_type="t", required=tuple("ABCDE"))
    # A moved to the end: B C D E A. A left-to-right scan would flag B,C,D,E.
    secs = [_sec(h) for h in "BCDEA"]
    findings = [f for f in outline_findings(secs, contract) if f["issue"] == ISSUE_OUT_OF_ORDER]
    assert len(findings) == 1
    assert findings[0]["item_number"] == "A"


def test_unordered_contract_accepts_any_order():
    contract = OutlineContract(
        artifact_type="t", required=("Data Flows", "System Overview"), ordered=False,
    )
    secs = [_sec("System Overview"), _sec("Data Flows")]
    assert outline_findings(secs, contract) == []


def test_missing_sections_do_not_manufacture_order_findings():
    # Only sections actually present can be out of order.
    secs = [_sec("Data Flows")]
    findings = outline_findings(secs, SSP)
    assert {f["issue"] for f in findings} == {ISSUE_MISSING}


def test_one_section_cannot_satisfy_two_different_required_headings():
    # A row carries several identifying fields, so a contract naming a section
    # BOTH by number and by title could otherwise have one block of prose
    # satisfy both entries — and a genuinely absent section reports present.
    contract = OutlineContract(artifact_type="t", required=("6.1", "Gap Questions"))
    secs = [{"item_number": "6.1", "title": "Gap Questions", "content": "body"}]
    assert _issues(outline_findings(secs, contract)) == [(ISSUE_MISSING, "Gap Questions")]


def test_a_repeated_heading_is_reported_as_unknown_not_silently_accepted():
    contract = OutlineContract(artifact_type="t", required=("Purpose", "Scope"))
    secs = [_sec("Purpose"), _sec("Purpose")]
    findings = outline_findings(secs, contract)
    assert (ISSUE_MISSING, "Scope") in _issues(findings)
    assert (ISSUE_UNKNOWN, "Purpose") in _issues(findings)


def test_every_issue_emitted_is_in_the_declared_vocabulary():
    secs = [_sec("Data Flows"), _sec("System Overview"), _sec("Invented")]
    for f in outline_findings(secs, SSP):
        assert f["issue"] in OUTLINE_ISSUES


# ── Contract resolution — reuses the existing declarations ────────────────────


def test_ato_contract_comes_from_the_docgen_section_model():
    from tools.docgen.domain_profiles import ATO_DOC_TYPES

    contract = get_contract("ato_ssp")
    assert contract is not None
    assert list(contract.required) == ATO_DOC_TYPES["ato_ssp"]["sections"]
    assert "domain_profiles" in contract.source


def test_dic_template_contract_comes_from_dic_constants():
    from tools.document_intelligence.constants import TEMPLATE_SECTIONS

    contract = get_contract("SOP")
    assert contract is not None
    assert list(contract.required) == TEMPLATE_SECTIONS["SOP"]


def test_dic_blueprint_and_the_contract_read_the_same_object_not_a_copy():
    # The defect this guards: instantiation creates one section list while the
    # gate validates against a stale duplicate, and neither side goes red.
    from tools.document_intelligence.blueprint import _TEMPLATE_SECTIONS
    from tools.document_intelligence.constants import TEMPLATE_SECTIONS

    assert _TEMPLATE_SECTIONS is TEMPLATE_SECTIONS


def test_docgen_doc_type_aliases_onto_its_tech_writer_template():
    from tools.document_intelligence.constants import TEMPLATE_SECTIONS

    contract = get_contract("runbook")
    assert contract is not None
    assert list(contract.required) == TEMPLATE_SECTIONS["RUNBOOK"]


def test_ato_doc_type_prefers_its_own_sections_over_the_template_alias():
    # "ato_ssp" appears in BOTH ATO_DOC_TYPES and DOCGEN_DOCTYPE_TO_TEMPLATE
    # (-> ARCH_SYSTEM). Its own section list is the authoritative one.
    from tools.document_intelligence.constants import TEMPLATE_SECTIONS

    contract = get_contract("ato_ssp")
    assert list(contract.required) != TEMPLATE_SECTIONS["ARCH_SYSTEM"]


def test_unknown_artifact_type_resolves_to_none_never_a_fabricated_skeleton():
    assert get_contract("no_such_artifact_type") is None
    assert get_contract("") is None


def test_no_contract_means_unmeasured_not_clean():
    report = check_outline([_sec("Anything")], None)
    assert report["measurable"] is False
    assert report["findings"] == []
    assert report["reason"]


def test_list_contracts_covers_all_three_declaration_sources():
    types = set(list_contracts())
    assert "ato_ssp" in types          # docgen ATO_DOC_TYPES
    assert "SOP" in types              # DIC TEMPLATE_SECTIONS
    assert "runbook" in types          # docgen doc_type alias
    assert "rfi_response" in types     # RFI floor


# ── RFI: derived, with an invariant floor ─────────────────────────────────────


def test_rfi_contract_is_the_floor_the_workbench_always_seeds():
    from tools.govcon.rfi_workbench import _APPENDIX_SECTIONS, _PART6_SECTIONS

    contract = get_contract("rfi_response")
    expected = [r[1] for r in list(_PART6_SECTIONS) + list(_APPENDIX_SECTIONS)]
    assert list(contract.required) == expected


def test_rfi_contract_tolerates_per_solicitation_questionnaire_parts():
    # Part 2.1 of one RFI is not Part 2.1 of the next. Flagging parsed
    # questionnaire parts as invented would fail every real session — the
    # ungated-applicability failure, not a finding.
    contract = get_contract("rfi_response")
    assert contract.allow_unknown is True
    secs = [{"item_number": "2.1", "title": "Custom Mission Question"}]
    secs += [{"item_number": n} for n in ("6.1", "6.2", "6.3", "6.4", "A", "B")]
    assert outline_findings(secs, contract) == []


def test_rfi_contract_still_fails_when_the_mandatory_floor_is_dropped():
    contract = get_contract("rfi_response")
    secs = [{"item_number": n} for n in ("2.1", "6.1", "6.2", "6.4", "A", "B")]
    assert _issues(outline_findings(secs, contract)) == [(ISSUE_MISSING, "6.3")]


def test_contract_from_sections_builds_the_exact_per_session_skeleton():
    seeded = [
        {"item_number": "1.1", "title": "Entity Data"},
        {"item_number": "2.1", "title": "Current TRL"},
    ]
    contract = contract_from_sections(seeded, "rfi:session-x")
    assert contract.required == ("1.1", "2.1")
    assert contract.source == "derived:rfi:session-x"
    assert outline_findings(seeded, contract) == []
    assert _issues(outline_findings(seeded[:1], contract)) == [(ISSUE_MISSING, "2.1")]


# ── Report form ───────────────────────────────────────────────────────────────


def test_check_outline_counts_each_issue_class():
    secs = [_sec("Data Flows"), _sec("System Overview"), _sec("Invented")]
    report = check_outline(secs, SSP)
    assert report["measurable"] is True
    assert report["required"] == 3
    assert report["missing"] == 1        # System Boundary
    assert report["unknown"] == 1        # Invented
    assert report["out_of_order"] == 1
    assert report["present"] == 2
    assert report["section_count"] == 3
    assert report["contract"]["artifact_type"] == "test_ssp"


def test_check_outline_on_a_clean_draft():
    secs = [_sec("System Overview"), _sec("System Boundary"), _sec("Data Flows")]
    report = check_outline(secs, SSP)
    assert report["findings"] == []
    assert report["present"] == report["required"] == 3


# ── The guard is registered — trust-struct-03 wired it ────────────────────────


def test_structure_guard_is_declared_now_that_it_is_wired():
    # PUBLISH_GATES gains a value only in the phase that can EMIT it, together
    # with the migration widening idr_publish_audit.gate. trust-struct-03 is
    # that phase: this module is now reachable from stage 1, so the gate value
    # its findings would be recorded under has to exist.
    from tools.quality.citation_grounding import PUBLISH_GATES

    assert "structure_guard" in PUBLISH_GATES


def test_this_module_is_reachable_from_the_gate():
    """The registration above is only honest if something actually calls in.

    A gate value whose module nothing consumes is the declared-but-unconsumed
    defect wearing the fix's clothes, so this asserts the path end to end
    rather than the constant alone.
    """
    from tools.quality.trust_gate import TrustGate

    contract = get_contract("ato_ssp")
    sections = [{"heading": h} for h in contract.required[:1]]
    result = TrustGate("compliance_evidence").evaluate(
        "prose", sections=sections, artifact_type="ato_ssp", run_stage2=False,
    ).stage1["structure_guard"]
    assert {f.issue for f in result.findings} == {ISSUE_MISSING}


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_lists_contracts(capsys):
    from tools.quality.outline_contract import main

    assert main(["--list", "--json"]) == 0
    assert "ato_ssp" in capsys.readouterr().out


def test_cli_gate_exits_nonzero_on_findings(tmp_path, capsys):
    import json

    from tools.quality.outline_contract import main

    payload = tmp_path / "sections.json"
    payload.write_text(
        json.dumps({"sections": [{"heading": "System Overview"}]}), encoding="utf-8"
    )
    rc = main([
        "--artifact-type", "ato_ssp",
        "--sections-file", str(payload),
        "--json", "--gate",
    ])
    assert rc == 1
    assert ISSUE_MISSING in capsys.readouterr().out


def test_cli_gate_exits_zero_when_the_outline_is_complete(tmp_path):
    import json

    from tools.docgen.domain_profiles import ATO_DOC_TYPES
    from tools.quality.outline_contract import main

    payload = tmp_path / "sections.json"
    payload.write_text(
        json.dumps([{"heading": h} for h in ATO_DOC_TYPES["ato_ssp"]["sections"]]),
        encoding="utf-8",
    )
    rc = main([
        "--artifact-type", "ato_ssp",
        "--sections-file", str(payload),
        "--json", "--gate",
    ])
    assert rc == 0
