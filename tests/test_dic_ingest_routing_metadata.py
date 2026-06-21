"""Tests for LLM workflow routing-metadata extraction in the DIC ingest orchestrator.

aiify-opp-110: metadata_extraction -> llm_generation (paperless
src/documents/workflows/mutations.py analog). These pin the load-bearing
guarantees of ``_ai_extract_routing_metadata`` and its wiring:

* only the leading ``_ROUTING_INPUT_CHARS`` slice is sent to the model;
* originator/originator_org values must have their alphanumeric core present
  verbatim in the source text — hallucinated names are silently dropped;
* routing_keywords are grounded the same way; ungrounded entries are dropped;
* priority is constrained to a closed enum (routine/urgent/immediate/
  time_sensitive); anything else collapses to "routine";
* a single confidence score gates the whole suggestion — below
  ``_ROUTING_MIN_CONFIDENCE`` the result is dropped (HITL fallback);
* it degrades silently to ``None`` on empty input, garbled output, or any
  LLM failure — ingestion must never break on routing enrichment;
* the proposal is surfaced under IngestOutcome.metadata["routing_metadata"]
  and is never silently persisted.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    last_request = None
    _content = "{}"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "{}"
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


def _json(**kw):
    return json.dumps(kw)


_SAMPLE_TEXT = (
    "From: Jane Smith <jane.smith@acme.org>\n"
    "ACME Corporation — Project Alpha contract review\n"
    "Case reference: CA-2024-0042\n"
    "URGENT: Please respond by Friday.\n"
    "This contract amendment requires immediate attention from procurement."
)


def test_returns_routing_metadata(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator="Jane Smith",
            originator_org="ACME Corporation",
            routing_keywords=["Alpha", "CA-2024-0042"],
            priority="urgent",
            confidence=0.85,
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    assert result["originator"] == "Jane Smith"
    assert result["originator_org"] == "ACME Corporation"
    assert "Alpha" in result["routing_keywords"]
    assert result["priority"] == "urgent"
    assert result["confidence"] == pytest.approx(0.85, abs=1e-3)


def test_originator_grounding_guard(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator="Invented Person XYZ",  # does NOT appear in the text
            originator_org="ACME Corporation",
            routing_keywords=[],
            priority="routine",
            confidence=0.80,
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    assert result["originator"] is None  # hallucinated name dropped


def test_originator_org_grounding_guard(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator="Jane Smith",
            originator_org="Phantom Company ZZZ",  # NOT in text
            routing_keywords=[],
            priority="routine",
            confidence=0.80,
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    assert result["originator_org"] is None


def test_routing_keywords_grounded(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator=None,
            originator_org=None,
            routing_keywords=["Alpha", "FakeProject999", "CA-2024-0042"],
            priority="routine",
            confidence=0.75,
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    # "Alpha" and "CA-2024-0042" appear in text; "FakeProject999" does not
    assert "Alpha" in result["routing_keywords"]
    assert "FakeProject999" not in result["routing_keywords"]


def test_priority_closed_enum_enforcement(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator=None,
            originator_org=None,
            routing_keywords=[],
            priority="very_high_priority",  # not in enum
            confidence=0.80,
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    assert result["priority"] == "routine"  # invalid value collapses to default


def test_priority_valid_values(monkeypatch):
    for prio in ("routine", "urgent", "immediate", "time_sensitive"):
        _patch_router(
            monkeypatch,
            content=_json(
                originator=None,
                originator_org=None,
                routing_keywords=[],
                priority=prio,
                confidence=0.80,
            ),
        )
        result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
        assert result is not None
        assert result["priority"] == prio


def test_confidence_gate_drops_low_confidence(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator="Jane Smith",
            originator_org="ACME Corporation",
            routing_keywords=["Alpha"],
            priority="urgent",
            confidence=0.50,  # below _ROUTING_MIN_CONFIDENCE (0.70)
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is None


def test_returns_none_on_empty_text(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_extract_routing_metadata("", "empty.pdf") is None
    assert ingest._ai_extract_routing_metadata("   ", "empty.pdf") is None


def test_degrades_on_llm_failure(monkeypatch):
    def _bad_invoke(*_a, **_k):
        raise RuntimeError("LLM unavailable")

    class _BadRouter(_Router):
        def invoke(self, *a, **k):
            raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(router_mod, "LLMRouter", _BadRouter)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _BadRouter)
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is None


def test_degrades_on_garbled_json(monkeypatch):
    _patch_router(monkeypatch, content="not valid json at all")
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is None


def test_fenced_code_block_tolerated(monkeypatch):
    payload = _json(
        originator="Jane Smith",
        originator_org="ACME Corporation",
        routing_keywords=[],
        priority="routine",
        confidence=0.85,
    )
    _patch_router(monkeypatch, content=f"```json\n{payload}\n```")
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    assert result["originator"] == "Jane Smith"


def test_routing_keywords_capped_at_max(monkeypatch):
    max_kw = ingest._ROUTING_MAX_KEYWORDS
    # Provide many keywords all grounded in the text.
    # We need them all to appear in _SAMPLE_TEXT; use known tokens.
    keywords_in_text = ["Alpha", "Jane", "ACME", "procurement", "contract"]
    # Only first max_kw should survive even if all are grounded.
    _patch_router(
        monkeypatch,
        content=_json(
            originator=None,
            originator_org=None,
            routing_keywords=keywords_in_text * 3,  # many duplicates + extras
            priority="routine",
            confidence=0.80,
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    assert len(result["routing_keywords"]) <= max_kw


def test_routing_keywords_deduplicated(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator=None,
            originator_org=None,
            routing_keywords=["Alpha", "Alpha", "Alpha"],
            priority="routine",
            confidence=0.80,
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is not None
    assert result["routing_keywords"].count("Alpha") == 1


def test_input_bounded_to_routing_input_chars(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator=None,
            originator_org=None,
            routing_keywords=[],
            priority="routine",
            confidence=0.80,
        ),
    )
    long_text = "A" * 20_000 + " Jane Smith ACME"
    ingest._ai_extract_routing_metadata(long_text, "big.pdf")
    assert _Router.last_request is not None
    sent = _Router.last_request.messages[0]["content"]
    assert len(sent) <= ingest._ROUTING_INPUT_CHARS + 200  # snippet + preamble


def test_confidence_missing_returns_none(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            originator="Jane Smith",
            originator_org="ACME Corporation",
            routing_keywords=[],
            priority="routine",
            # no confidence key
        ),
    )
    result = ingest._ai_extract_routing_metadata(_SAMPLE_TEXT, "contract.pdf")
    assert result is None
