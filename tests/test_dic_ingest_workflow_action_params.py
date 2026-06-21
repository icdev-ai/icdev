"""Tests for the NLP-based workflow action parameter extractor in the DIC ingest orchestrator.

aiify-rm-a3344-phase-109: regex_user_input -> nlp_extractor.

Pins the load-bearing guarantees of ``_ai_extract_workflow_action_params``:

* blank action_description returns None immediately (no LLM call);
* proposed field values must be grounded in the action description + doc_context —
  values the model invents that do not appear in the input are stripped;
* below _ACTION_PARAMS_MIN_CONFIDENCE the whole suggestion is dropped (HITL path);
* action_type is constrained to "assignment"|"removal"|"notification"|"custom";
* unknown action_type values are coerced to "custom";
* assignments/removals lists are de-duplicated and count-capped;
* wildcard "*" removals are accepted without grounding;
* proposed fields outside _ALLOWED_ACTION_FIELDS are dropped;
* fields outside the caller-supplied available_fields list are filtered;
* any LLM failure degrades silently to None (air-gap safe);
* pure notification/custom actions (no assignments/removals) are accepted.
"""
from __future__ import annotations

import importlib
import json
import sys

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")

_extract = ingest._ai_extract_workflow_action_params


# ---------------------------------------------------------------------------
# Stand-in router (same pattern as test_dic_ingest_label_match_criteria.py)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Minimal LLMRouter stub that returns a configurable JSON blob."""

    _content = "{}"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router._content = "{}"
    yield


def _patch_router(monkeypatch, content: str):
    _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


def _make_json(
    action_type="assignment",
    assignments=None,
    removals=None,
    rationale="assign correspondent and tag",
    confidence=0.85,
) -> str:
    return json.dumps({
        "action_type": action_type,
        "assignments": assignments if assignments is not None else [
            {"field": "correspondent", "value": "acme"},
            {"field": "tag", "value": "invoice"},
        ],
        "removals": removals if removals is not None else [],
        "rationale": rationale,
        "confidence": confidence,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_blank_description_returns_none():
    """Blank / whitespace-only action_description must return None without LLM call."""
    assert _extract("") is None
    assert _extract("   ") is None


def test_successful_assignment(monkeypatch):
    """Happy-path: valid assignment action with grounded values returns a dict."""
    _patch_router(monkeypatch, _make_json(
        action_type="assignment",
        assignments=[{"field": "correspondent", "value": "acme"}, {"field": "tag", "value": "invoice"}],
        confidence=0.85,
    ))
    result = _extract("assign correspondent to acme and tag as invoice")
    assert result is not None
    assert result["action_type"] == "assignment"
    assert result["confidence"] == pytest.approx(0.85, abs=1e-4)
    assert any(a["field"] == "correspondent" and a["value"] == "acme" for a in result["assignments"])
    assert any(a["field"] == "tag" and a["value"] == "invoice" for a in result["assignments"])
    assert result["origin"] == "ai_generated"


def test_low_confidence_returns_none(monkeypatch):
    """Confidence below _ACTION_PARAMS_MIN_CONFIDENCE must return None."""
    _patch_router(monkeypatch, _make_json(confidence=0.30))
    result = _extract("assign correspondent to acme and tag as invoice")
    assert result is None


def test_confidence_at_threshold_passes(monkeypatch):
    """Confidence exactly at _ACTION_PARAMS_MIN_CONFIDENCE must pass the gate."""
    threshold = ingest._ACTION_PARAMS_MIN_CONFIDENCE
    _patch_router(monkeypatch, _make_json(
        assignments=[{"field": "tag", "value": "invoice"}],
        confidence=threshold,
    ))
    result = _extract("tag as invoice")
    assert result is not None


def test_ungrounded_value_stripped(monkeypatch):
    """Values not appearing in the action description + context must be stripped."""
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": [
            {"field": "correspondent", "value": "acme"},
            {"field": "tag", "value": "xyzzyphan"},  # not in input
        ],
        "removals": [],
        "rationale": "assign",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("assign correspondent to acme corp")
    assert result is not None
    fields_and_values = [(a["field"], a["value"]) for a in result["assignments"]]
    assert ("tag", "xyzzyphan") not in fields_and_values
    assert any(f == "correspondent" for f, _ in fields_and_values)


def test_all_values_ungrounded_and_no_assignments_returns_none(monkeypatch):
    """When every value fails grounding and there are no removals, result is None."""
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": [{"field": "correspondent", "value": "xyzzyphan"}],
        "removals": [],
        "rationale": "assign",
        "confidence": 0.90,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("assign a correspondent")
    assert result is None


def test_unknown_action_type_coerced_to_custom(monkeypatch):
    """Unknown action_type values must be coerced to 'custom', not rejected."""
    payload = json.dumps({
        "action_type": "download_pdf",  # not in allowed set
        "assignments": [],
        "removals": [],
        "rationale": "download",
        "confidence": 0.75,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("download the matched document as pdf")
    assert result is not None
    assert result["action_type"] == "custom"


def test_removal_action(monkeypatch):
    """Removal action with grounded field values must be returned correctly."""
    payload = json.dumps({
        "action_type": "removal",
        "assignments": [],
        "removals": [{"field": "tag", "value": "draft"}],
        "rationale": "remove draft tag",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("remove the draft tag from the document")
    assert result is not None
    assert result["action_type"] == "removal"
    assert any(r["field"] == "tag" and r["value"] == "draft" for r in result["removals"])


def test_wildcard_removal_accepted(monkeypatch):
    """Wildcard '*' removals must be accepted without grounding check."""
    payload = json.dumps({
        "action_type": "removal",
        "assignments": [],
        "removals": [{"field": "tag", "value": "*"}],
        "rationale": "clear all tags",
        "confidence": 0.78,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("clear all existing tags from the document")
    assert result is not None
    assert any(r["value"] == "*" for r in result["removals"])


def test_unknown_field_dropped(monkeypatch):
    """Fields not in _ALLOWED_ACTION_FIELDS must be silently removed."""
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": [
            {"field": "tag", "value": "invoice"},
            {"field": "webhook_url", "value": "invoice"},  # not in allowed fields
        ],
        "removals": [],
        "rationale": "tag and webhook",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("tag as invoice and call webhook")
    assert result is not None
    assert all(a["field"] != "webhook_url" for a in result["assignments"])


def test_available_fields_filter(monkeypatch):
    """Proposed fields outside caller-supplied available_fields must be filtered."""
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": [
            {"field": "tag", "value": "invoice"},
            {"field": "correspondent", "value": "acme"},
        ],
        "removals": [],
        "rationale": "assign",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _extract(
        "assign correspondent to acme and tag as invoice",
        available_fields=["tag"],  # only "tag" is available
    )
    assert result is not None
    assert all(a["field"] == "tag" for a in result["assignments"])


def test_assignments_count_capped(monkeypatch):
    """Output assignments must not exceed _ACTION_PARAMS_MAX_ASSIGNMENTS."""
    cap = ingest._ACTION_PARAMS_MAX_ASSIGNMENTS
    words = [f"word{i}" for i in range(cap + 5)]
    desc = " ".join(words)
    assignments = [{"field": "tag", "value": w} for w in words]
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": assignments,
        "removals": [],
        "rationale": "assign many tags",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _extract(f"tag as {desc}")
    assert result is not None
    assert len(result["assignments"]) <= cap


def test_duplicate_assignments_deduped(monkeypatch):
    """Duplicate (field, value) pairs must be de-duplicated."""
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": [
            {"field": "tag", "value": "invoice"},
            {"field": "tag", "value": "invoice"},  # dup
        ],
        "removals": [],
        "rationale": "tag as invoice",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("tag as invoice")
    assert result is not None
    assert len(result["assignments"]) == 1


def test_llm_failure_returns_none(monkeypatch):
    """Any LLM exception must degrade to None without raising."""
    class _BrokenRouter:
        def __init__(self, *a, **k):
            pass
        def invoke(self, *a, **k):
            raise RuntimeError("network failure")

    monkeypatch.setattr(router_mod, "LLMRouter", _BrokenRouter)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _BrokenRouter)
    for _key, _mod in list(sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _BrokenRouter)

    result = _extract("assign correspondent to acme")
    assert result is None


def test_malformed_json_returns_none(monkeypatch):
    """Non-JSON LLM response must degrade to None."""
    _patch_router(monkeypatch, "not valid json at all")
    result = _extract("assign correspondent to acme")
    assert result is None


def test_fenced_json_accepted(monkeypatch):
    """JSON wrapped in markdown fences must still be parsed correctly."""
    inner = _make_json(
        assignments=[{"field": "tag", "value": "invoice"}],
        confidence=0.80,
    )
    _patch_router(monkeypatch, f"```json\n{inner}\n```")
    result = _extract("tag as invoice")
    assert result is not None
    assert any(a["field"] == "tag" for a in result["assignments"])


def test_doc_context_extends_haystack(monkeypatch):
    """Values from doc_context (not in action_description) must survive grounding."""
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": [{"field": "correspondent", "value": "acmecorp"}],
        "removals": [],
        "rationale": "keep existing correspondent",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    # "acmecorp" only appears in doc_context, not in the description.
    result = _extract(
        "keep the existing correspondent",
        doc_context={"correspondent": "acmecorp"},
    )
    assert result is not None
    assert any(a["value"] == "acmecorp" for a in result["assignments"])


def test_notification_action_no_assignments_accepted(monkeypatch):
    """Notification actions with no assignments or removals must still pass."""
    payload = json.dumps({
        "action_type": "notification",
        "assignments": [],
        "removals": [],
        "rationale": "send alert",
        "confidence": 0.70,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("send a notification when this document is ingested")
    assert result is not None
    assert result["action_type"] == "notification"


def test_rationale_length_capped(monkeypatch):
    """Rationale must be capped at 300 characters."""
    long_rationale = "x" * 500
    payload = json.dumps({
        "action_type": "assignment",
        "assignments": [{"field": "tag", "value": "invoice"}],
        "removals": [],
        "rationale": long_rationale,
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _extract("tag as invoice")
    assert result is not None
    assert len(result["rationale"]) <= 300
