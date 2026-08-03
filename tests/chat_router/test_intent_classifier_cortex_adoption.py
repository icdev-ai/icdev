# CUI // SP-CTI
"""The chat intent classifier's LLM fallback must not touch a provider directly.

`cxo-adopt-05` exists because `_llm_classify` held a provider handle
(`router.get_provider_for_function(...)` -> `provider.complete(...)`) and fired
it on user-authored chat text from `/api/chat/route-intent`. That path skips the
gateway pre-check, redaction, the budget/rate gate, the provider fallback chain
and the append-only audit row.

These tests pin the wiring — the call goes through the GOVERNED
`cortex_api.classify` facade — plus the two contracts the dashboard depends on:
the `{mode, canvas_type, confidence, reason}` shape, and degradation to intake
on any failure.
"""

from __future__ import annotations

import importlib

import pytest

intent_classifier = importlib.import_module("tools.chat_router.intent_classifier")
cortex_api = importlib.import_module("tools.cortex.api")
CortexResult = importlib.import_module("tools.cortex.schemas").CortexResult

# Low keyword signal -> classify() hands off to _llm_classify.
AMBIGUOUS = "can you take a look at this thing for me please"

EXPECTED_KEYS = {"mode", "canvas_type", "confidence", "reason"}


def _stub_classify(monkeypatch, result, recorder=None):
    """Replace the governed facade on the module object (shim-aware patch)."""

    def fake_classify(text, labels, ctx=None, **kwargs):
        if recorder is not None:
            recorder.update({"text": text, "labels": labels, "ctx": ctx})
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cortex_api, "classify", fake_classify)


# --------------------------------------------------------------------------
# Wiring: the facade is the only LLM seam
# --------------------------------------------------------------------------
def test_llm_fallback_calls_cortex_classify_with_mode_labels():
    """Every mode is offered as a label, keyed to chat-intent for budget attribution."""
    seen: dict = {}
    with pytest.MonkeyPatch.context() as mp:
        _stub_classify(mp, CortexResult(text="network-design", provider="anthropic"), recorder=seen)
        result = intent_classifier._llm_classify("segment the site network")

    assert seen["labels"] == sorted(intent_classifier._LABEL_TO_MODE)
    assert seen["ctx"].agent_id == "chat-intent", "budget/rate attribution key must be set"
    assert seen["text"] == "segment the site network"
    assert result["mode"] == "ndc"


def test_every_label_maps_to_a_real_mode():
    """The label taxonomy must stay a total mapping onto the emitted mode taxonomy."""
    modes = set(intent_classifier._LABEL_TO_MODE.values())
    assert modes == intent_classifier.CANVAS_MODES | {intent_classifier.INTAKE_MODE}
    assert intent_classifier._LABEL_TO_MODE[intent_classifier._NO_CANVAS_LABEL] == "intake"
    assert all(label == label.lower() for label in intent_classifier._LABEL_TO_MODE)


def test_classify_routes_low_confidence_message_through_cortex():
    """The hand-off from the keyword scorer to the facade is live, not dead code."""
    seen: dict = {}
    with pytest.MonkeyPatch.context() as mp:
        _stub_classify(mp, CortexResult(text="cloud-migration", provider="anthropic"), recorder=seen)
        result = intent_classifier.classify(AMBIGUOUS)

    assert seen["text"] == AMBIGUOUS
    assert result["mode"] == "cam"


def test_no_canvas_label_keeps_the_message_in_intake():
    """The escape hatch exists so an off-topic message is not assigned a canvas."""
    with pytest.MonkeyPatch.context() as mp:
        _stub_classify(mp, CortexResult(text="none-of-the-above", provider="anthropic"))
        result = intent_classifier._llm_classify("translate this paragraph into Spanish")

    assert result["mode"] == "intake"
    assert result["canvas_type"] is None


def test_cortex_classify_facade_is_governed():
    """The audit row is a property of the facade — assert we adopted the governed one."""
    assert getattr(cortex_api.classify, "__cortex_governed__", False) is True
    assert cortex_api.classify.__cortex_operation__ == "cortex.classify"


def test_module_holds_no_direct_provider_handle():
    """Regression guard for the defect itself."""
    from pathlib import Path

    source = Path(intent_classifier.__file__).read_text(encoding="utf-8")
    assert "get_provider_for_function" not in source
    assert "provider.complete(" not in source
    assert "from tools.llm" not in source
    assert "import LLMRouter" not in source


# --------------------------------------------------------------------------
# Contract: the returned shape is unchanged
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stub",
    [
        CortexResult(text="security-design", provider="anthropic"),
        CortexResult(text="requirements-intake", provider="anthropic"),
        CortexResult(text="none-of-the-above", provider="anthropic"),
        CortexResult(text="not-a-label", provider="anthropic"),
        CortexResult(text="", provider="anthropic"),
        CortexResult(text="business-design", provider="deterministic"),
        RuntimeError("router exhausted"),
    ],
)
def test_shape_is_stable_across_every_outcome(stub):
    with pytest.MonkeyPatch.context() as mp:
        _stub_classify(mp, stub)
        result = intent_classifier._llm_classify(AMBIGUOUS)

    assert set(result) == EXPECTED_KEYS
    assert result["mode"] in intent_classifier.CANVAS_MODES | {intent_classifier.INTAKE_MODE}
    assert isinstance(result["confidence"], float)
    assert isinstance(result["reason"], str)
    # canvas_type is None for intake, the mode otherwise.
    if result["mode"] == intent_classifier.INTAKE_MODE:
        assert result["canvas_type"] is None
    else:
        assert result["canvas_type"] == result["mode"]


def test_canvas_mode_maps_to_canvas_type():
    with pytest.MonkeyPatch.context() as mp:
        _stub_classify(mp, CortexResult(text=" Observability-Design ", provider="anthropic"))
        result = intent_classifier._llm_classify(AMBIGUOUS)

    assert result == {
        "mode": "odc",
        "canvas_type": "odc",
        "confidence": 0.78,
        "reason": "LLM: cortex.classify chose observability-design via anthropic",
    }


# --------------------------------------------------------------------------
# Contract: failure still degrades to intake
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stub",
    [
        RuntimeError("router exhausted"),
        ImportError("cortex unavailable"),
        CortexResult(text="not-a-label", provider="anthropic"),
        CortexResult(text="", provider="anthropic"),
    ],
)
def test_failure_degrades_to_intake(stub):
    with pytest.MonkeyPatch.context() as mp:
        _stub_classify(mp, stub)
        result = intent_classifier._llm_classify(AMBIGUOUS)

    assert result["mode"] == "intake"
    assert result["canvas_type"] is None
    assert result["confidence"] == 0.50


def test_deterministic_degradation_does_not_invent_a_canvas():
    """Air-gap: query_classifier's taxonomy is not the canvas taxonomy.

    cortex_api.classify falls through to ``labels[0]`` (alphabetically
    "business-design") when its heuristic label maps to none of ours. Accepting
    that verbatim would route every ambiguous air-gap message to one arbitrary
    canvas, so a deterministic answer is treated as "no LLM signal".
    """
    with pytest.MonkeyPatch.context() as mp:
        _stub_classify(mp, CortexResult(text="business-design", provider="deterministic"))
        result = intent_classifier._llm_classify(AMBIGUOUS)

    assert result["mode"] == "intake"
    assert result["canvas_type"] is None
    assert result["confidence"] == 0.50


# --------------------------------------------------------------------------
# The keyword fast path is untouched — the facade must not be consulted.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("message", "mode"),
    [
        ("we need to migrate the legacy oracle database", "cam"),
        ("draw up the firewall design and vlan segmentation", "ndc"),
        ("I need to build a new inventory app", "intake"),
        ("", "intake"),
    ],
)
def test_keyword_fast_path_never_calls_the_llm(message, mode):
    def explode(*args, **kwargs):
        raise AssertionError("keyword path must not reach the LLM")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cortex_api, "classify", explode)
        result = intent_classifier.classify(message)

    assert result["mode"] == mode
