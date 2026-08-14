# CUI // SP-CTI
"""Tests for the one contract validator for LLM output (trust-struct-01).

Two halves: the validator itself, and proof that each of the three surfaces it
replaced still degrades the way it always did — the three hand-rolled extractors
had subtly different fallbacks, and the point of consolidating them is that the
fallback is now DECLARED, not that it changed.
"""
from __future__ import annotations

import json

import pytest

from tools.quality.structured_output import (
    ADDITIONAL_PROPERTY,
    EMPTY_OUTPUT,
    ENUM_VIOLATION,
    MISSING_REQUIRED,
    REPAIR_FAILED,
    REPAIR_UNAVAILABLE,
    TOO_FEW_ITEMS,
    TYPE_MISMATCH,
    UNPARSEABLE,
    ContractError,
    OutputContract,
    coerce_or_reject,
    enum_field,
    extract_json_payload,
    repair_once,
    validate_against_contract,
)

VERDICT = OutputContract(
    {
        "type": "object",
        "required": ["verdict"],
        "properties": {
            "verdict": enum_field(["pass", "fail", "not_applicable"], fail_closed="fail"),
            "rationale": {"type": "string", "fail_closed": ""},
        },
    },
    name="test.verdict",
)

LABELS = OutputContract(
    {
        "type": "object",
        "required": ["labels"],
        "properties": {
            "labels": {
                "type": "array",
                "minItems": 1,
                "items": enum_field(["grounded", "partial", "ungrounded"], fail_closed="ungrounded"),
            }
        },
    },
    name="test.labels",
)


class _Resp:
    def __init__(self, content):
        self.content = content
        self.model_id = "fake"


# ── Contract construction is itself checked ──────────────────────────────────


def test_unsupported_keyword_raises_at_construction():
    """Silently ignoring a keyword would report conformance never checked."""
    with pytest.raises(ContractError) as exc:
        OutputContract({"type": "string", "minLength": 3})
    assert "minLength" in str(exc.value)


def test_annotation_keywords_are_allowed():
    OutputContract({"type": "string", "description": "a note", "title": "T"})


def test_unknown_type_name_raises():
    with pytest.raises(ContractError):
        OutputContract({"type": "stringy"})


def test_fail_closed_outside_the_enum_raises():
    with pytest.raises(ContractError):
        OutputContract({"type": "string", "enum": ["a", "b"], "fail_closed": "z"})


def test_required_name_with_no_declared_property_raises():
    with pytest.raises(ContractError):
        OutputContract({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]})


def test_contract_error_is_a_value_error():
    """Callers already catching ValueError on a bad contract keep working."""
    assert issubclass(ContractError, ValueError)


def test_prompt_fragment_hides_the_fail_closed_policy():
    """fail_closed is OUR fallback, not an instruction inviting the sentinel."""
    fragment = VERDICT.prompt_fragment()
    assert "fail_closed" not in fragment
    assert "not_applicable" in fragment


# ── Parsing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        '{"verdict": "pass"}',
        '```json\n{"verdict": "pass"}\n```',
        '```\n{"verdict": "pass"}\n```',
        'Sure! {"verdict": "pass"} — hope that helps.',
    ],
)
def test_extracts_the_three_shapes_models_actually_emit(raw):
    obj, findings = coerce_or_reject(raw, VERDICT)
    assert obj == {"verdict": "pass"}
    assert findings == []


def test_array_root_is_preferred_when_the_contract_declares_one():
    contract = OutputContract({"type": "array", "items": {"type": "number"}})
    assert extract_json_payload("noise [1, 2, 3] noise", prefer="array") == [1, 2, 3]
    obj, findings = coerce_or_reject("here: [1, 2, 3]", contract)
    assert obj == [1, 2, 3] and findings == []


def test_unparseable_fails_closed():
    obj, findings = coerce_or_reject("I cannot help with that.", VERDICT)
    assert obj is None
    assert [f["code"] for f in findings] == [UNPARSEABLE]


def test_empty_output_fails_closed():
    obj, findings = coerce_or_reject("   ", VERDICT)
    assert obj is None
    assert [f["code"] for f in findings] == [EMPTY_OUTPUT]


# ── Validation ───────────────────────────────────────────────────────────────


def test_validate_is_pure_and_reports_every_defect():
    findings = validate_against_contract({"verdict": "maybe", "rationale": 7}, VERDICT)
    codes = {f["code"] for f in findings}
    assert codes == {ENUM_VIOLATION, TYPE_MISMATCH}
    assert {f["path"] for f in findings} == {"$.verdict", "$.rationale"}
    assert all(f["repaired"] is False for f in findings)


def test_missing_required_property_is_a_finding():
    findings = validate_against_contract({"rationale": "x"}, VERDICT)
    assert [f["code"] for f in findings] == [MISSING_REQUIRED]
    assert findings[0]["path"] == "$.verdict"


def test_booleans_are_not_numbers():
    contract = OutputContract({"type": "object", "properties": {"n": {"type": "number"}}})
    assert validate_against_contract({"n": True}, contract)[0]["code"] == TYPE_MISMATCH
    assert validate_against_contract({"n": 1.5}, contract) == []


def test_extra_properties_are_allowed_unless_explicitly_forbidden():
    assert validate_against_contract({"verdict": "pass", "extra": 1}, VERDICT) == []
    strict = OutputContract(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"verdict": enum_field(["pass", "fail"])},
        }
    )
    findings = validate_against_contract({"verdict": "pass", "extra": 1}, strict)
    assert [f["code"] for f in findings] == [ADDITIONAL_PROPERTY]


def test_enum_tokens_are_normalized_not_repaired():
    """strip+lower matches every consumer (map_axis, map_grounding_enum)."""
    obj, findings = coerce_or_reject('{"verdict": "  PASS "}', VERDICT)
    assert obj == {"verdict": "pass"}
    assert findings == []


def test_array_item_paths_are_indexed():
    findings = validate_against_contract({"labels": ["grounded", "bogus"]}, LABELS)
    assert [f["path"] for f in findings] == ["$.labels[1]"]


def test_min_items_violation():
    findings = validate_against_contract({"labels": []}, LABELS)
    assert [f["code"] for f in findings] == [TOO_FEW_ITEMS]


# ── reject vs coerce ─────────────────────────────────────────────────────────


def test_reject_is_the_default_mode():
    obj, findings = coerce_or_reject('{"verdict": "maybe"}', VERDICT)
    assert obj is None and findings[0]["code"] == ENUM_VIOLATION


def test_coerce_substitutes_only_the_declared_sentinel():
    obj, findings = coerce_or_reject('{"verdict": "maybe"}', VERDICT, mode="coerce")
    assert obj == {"verdict": "fail"}
    assert findings[0]["repaired"] is True
    assert findings[0]["substituted"] == "fail"


def test_coerce_fills_a_missing_required_field_that_declares_a_sentinel():
    obj, findings = coerce_or_reject("{}", VERDICT, mode="coerce")
    assert obj == {"verdict": "fail"}
    assert findings[0]["code"] == MISSING_REQUIRED and findings[0]["repaired"] is True


def test_coerce_never_returns_a_partly_valid_object():
    """One unrepairable defect rejects the WHOLE payload, not just the field."""
    contract = OutputContract(
        {
            "type": "object",
            "required": ["verdict", "score"],
            "properties": {
                "verdict": enum_field(["pass", "fail"], fail_closed="fail"),
                "score": {"type": "number"},  # no sentinel -> unrepairable
            },
        }
    )
    obj, findings = coerce_or_reject('{"verdict": "maybe", "score": "high"}', contract, mode="coerce")
    assert obj is None
    assert {f["code"] for f in findings} == {ENUM_VIOLATION, TYPE_MISMATCH}


def test_coerce_does_not_pad_an_array_to_satisfy_min_items():
    """Inventing entries would manufacture judgements; length is caller policy."""
    obj, findings = coerce_or_reject('{"labels": []}', LABELS, mode="coerce")
    assert obj is None
    assert [f["code"] for f in findings] == [TOO_FEW_ITEMS]


def test_coerce_repairs_each_bad_array_item():
    obj, findings = coerce_or_reject(
        '{"labels": ["grounded", "bogus", 7]}', LABELS, mode="coerce"
    )
    assert obj == {"labels": ["grounded", "ungrounded", "ungrounded"]}
    assert len(findings) == 2 and all(f["repaired"] for f in findings)


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        coerce_or_reject('{"verdict": "pass"}', VERDICT, mode="fix_it")


def test_input_object_is_not_mutated():
    payload = {"labels": ["bogus"]}
    coerce_or_reject(json.dumps(payload), LABELS, mode="coerce")
    assert payload == {"labels": ["bogus"]}


# ── repair_once ──────────────────────────────────────────────────────────────


class _CountingRouter:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, function, request, **kwargs):
        self.calls += 1
        return _Resp(self.replies.pop(0) if self.replies else "")


def test_repair_not_attempted_when_the_first_response_conforms():
    router = _CountingRouter(['{"verdict": "fail"}'])
    obj, findings = repair_once('{"verdict": "pass"}', VERDICT, router=router, function="f")
    assert obj == {"verdict": "pass"} and findings == [] and router.calls == 0


def test_repair_retries_exactly_once_and_succeeds():
    router = _CountingRouter(['{"verdict": "fail", "rationale": "fixed"}'])
    obj, findings = repair_once("not json", VERDICT, router=router, function="f")
    assert obj == {"verdict": "fail", "rationale": "fixed"}
    assert router.calls == 1
    assert findings[0]["code"] == UNPARSEABLE and findings[0]["attempt"] == 1


def test_repair_stops_after_one_failed_retry():
    router = _CountingRouter(["still not json", '{"verdict": "pass"}'])
    obj, findings = repair_once("not json", VERDICT, router=router, function="f")
    assert obj is None
    assert router.calls == 1, "a second retry would be an unbounded spend"
    assert {f["attempt"] for f in findings} == {1, 2}


def test_repair_prompt_carries_the_schema_and_the_defects():
    captured = {}

    class _Capturing:
        def invoke(self, function, request, **kwargs):
            captured["prompt"] = request.messages[0]["content"]
            return _Resp('{"verdict": "fail"}')

    repair_once('{"verdict": "maybe"}', VERDICT, router=_Capturing(), function="f")
    assert "$.verdict" in captured["prompt"]
    assert '"enum"' in captured["prompt"]


def test_router_error_is_a_finding_not_an_exception():
    class _Dead:
        def invoke(self, function, request, **kwargs):
            raise RuntimeError("provider down")

    obj, findings = repair_once("not json", VERDICT, router=_Dead(), function="f")
    assert obj is None
    assert findings[-1]["code"] == REPAIR_FAILED


def test_no_router_reports_repair_unavailable():
    obj, findings = repair_once("not json", VERDICT, router=None, function="f")
    assert obj is None
    assert findings[-1]["code"] == REPAIR_UNAVAILABLE


# ── The three replaced call sites keep their old degrade behaviour ───────────


def test_content_grounding_llm_path_maps_unknown_tokens_to_ungrounded():
    from tools.quality.content_grounding import ground_content

    out = "The sky is blue. Water is wet."
    result = ground_content(
        out,
        ["The sky is blue.", "Water is wet."],
        method="llm",
        llm_invoke=lambda _p: json.dumps({"labels": ["grounded", "bogus_token"]}),
    )
    assert result["method"] == "llm"
    assert result["score"] == 0.5  # 1.0 + 0.0 over two claims
    assert "Water is wet." in result["ungrounded_claims"]


def test_content_grounding_llm_path_pads_a_short_label_list():
    from tools.quality.content_grounding import ground_content

    result = ground_content(
        "The sky is blue. Water is wet.",
        ["The sky is blue."],
        method="llm",
        llm_invoke=lambda _p: json.dumps({"labels": ["grounded"]}),
    )
    assert result["method"] == "llm" and result["sentence_count"] == 2
    assert result["score"] == 0.5


def test_content_grounding_falls_back_to_the_heuristic_on_unparseable_output():
    from tools.quality.content_grounding import ground_content

    result = ground_content(
        "The sky is blue.",
        ["The sky is blue."],
        method="llm",
        llm_invoke=lambda _p: "I refuse.",
    )
    assert result["method"] == "heuristic"


def test_critique_rule_fails_closed_on_garbage_for_a_block_rule():
    from tools.quality.constitutional_ai import Rule, critique_rule

    class _Garbage:
        def invoke(self, function, request, **kwargs):
            return _Resp("I cannot evaluate this.")

    rule = Rule(id="R1", severity="block", principle="Must carry a CUI marking")
    result = critique_rule("artifact", rule, router=_Garbage())
    assert result.verdict == "fail"
    assert "contract_violation" in result.rationale


def test_critique_rule_does_not_manufacture_a_failure_for_a_warn_rule():
    from tools.quality.constitutional_ai import Rule, critique_rule

    class _Garbage:
        def invoke(self, function, request, **kwargs):
            return _Resp("I cannot evaluate this.")

    rule = Rule(id="R2", severity="warn", principle="Prefer active voice")
    result = critique_rule("artifact", rule, router=_Garbage())
    assert result.verdict == "not_applicable"


def test_critique_rule_coerces_an_unknown_verdict_token():
    from tools.quality.constitutional_ai import Rule, critique_rule

    class _Odd:
        def invoke(self, function, request, **kwargs):
            return _Resp(json.dumps({"verdict": "probably fine", "rationale": "hmm"}))

    rule = Rule(id="R3", severity="block", principle="Must cite a control")
    result = critique_rule("artifact", rule, router=_Odd())
    assert result.verdict == "fail"
    assert result.rationale == "hmm", "a repaired verdict must not discard the model's prose"


def test_reflect_document_degrades_every_axis_to_partial_on_garbage():
    from tools.rag.reflective_reranker import reflect_document

    class _Garbage:
        def invoke(self, function, request, **kwargs):
            return _Resp("no json here")

    out = reflect_document("q", "d", router=_Garbage())
    assert out["relevant"] == "partial" and out["useful"] == "partial"
    assert out["score"] == 0.5


def test_reflect_document_degrades_only_the_missing_axis():
    from tools.rag.reflective_reranker import reflect_document

    class _Partial:
        def invoke(self, function, request, **kwargs):
            return _Resp(json.dumps({"relevant": "yes"}))  # 'useful' absent

    out = reflect_document("q", "d", router=_Partial())
    assert out["relevant"] == "yes" and out["useful"] == "partial"


def test_reflect_document_accepts_uppercase_axis_tokens():
    from tools.rag.reflective_reranker import reflect_document

    class _Shouty:
        def invoke(self, function, request, **kwargs):
            return _Resp(json.dumps({"relevant": "YES", "useful": "No"}))

    out = reflect_document("q", "d", router=_Shouty())
    assert out["relevant"] == "yes" and out["useful"] == "no"
