# CUI // SP-CTI
"""Tests for the NLP extractor augmentation of the Log Analyzer.

Covers the ``nlp_extractor`` paradigm added to ``tools/monitor/log_analyzer.py``:
the optional clustering of log lines that match no known regex pattern into
emergent error categories, plus its graceful-degradation contract.

The module does ``from tools.llm.router import LLMRouter`` *inside* the function,
so the router is patched via ``importlib`` + ``setattr`` on the ``tools.llm.router``
module object (the shim and ``icdev.tools.*`` are distinct objects — the pytest
string form would patch the wrong one and let the real router through).
"""

import importlib

from tools.monitor import log_analyzer as la


# ---------------------------------------------------------------------------
# _unmatched_messages — the "unknown" bucket the extractor reasons over
# ---------------------------------------------------------------------------
def test_unmatched_excludes_regex_matches():
    logs = [
        {"message": "NullPointerException at Foo.bar"},  # matches DEFAULT_PATTERNS
        {"message": "widget reconciliation drifted by 3 units"},  # novel
    ]
    out = la._unmatched_messages(logs)
    assert out == ["widget reconciliation drifted by 3 units"]


def test_unmatched_dedupes_and_preserves_order():
    logs = [
        {"message": "alpha glitch"},
        {"message": "beta glitch"},
        {"message": "alpha glitch"},  # duplicate
        {"msg": ""},  # empty ignored
    ]
    assert la._unmatched_messages(logs) == ["alpha glitch", "beta glitch"]


def test_unmatched_truncates_to_200_chars():
    logs = [{"message": "x" * 500}]
    assert len(la._unmatched_messages(logs)[0]) == 200


# ---------------------------------------------------------------------------
# _parse_ai_patterns — tolerant JSON sanitizer
# ---------------------------------------------------------------------------
def test_parse_plain_array():
    raw = '[{"name": "DiskDrift", "description": "d", "count": 2, "sample_messages": ["a", "b"]}]'
    out = la._parse_ai_patterns(raw)
    assert out[0]["name"] == "DiskDrift"
    assert out[0]["count"] == 2
    assert out[0]["source"] == "ai"


def test_parse_strips_markdown_fence():
    raw = '```json\n[{"name": "Foo", "count": 1}]\n```'
    out = la._parse_ai_patterns(raw)
    assert len(out) == 1 and out[0]["name"] == "Foo"


def test_parse_isolates_array_amid_prose():
    raw = 'Here you go:\n[{"name": "Bar"}]\nHope that helps!'
    out = la._parse_ai_patterns(raw)
    assert out[0]["name"] == "Bar"


def test_parse_drops_nameless_and_nondict_items():
    raw = '[{"name": ""}, "junk", {"description": "no name"}, {"name": "Keep"}]'
    out = la._parse_ai_patterns(raw)
    assert [p["name"] for p in out] == ["Keep"]


def test_parse_bad_json_returns_empty():
    assert la._parse_ai_patterns("not json at all") == []
    assert la._parse_ai_patterns('{"name": "obj-not-array"}') == []


def test_parse_coerces_bad_count_to_zero():
    raw = '[{"name": "X", "count": "lots"}]'
    assert la._parse_ai_patterns(raw)[0]["count"] == 0


def test_parse_caps_sample_messages_at_three():
    raw = '[{"name": "X", "sample_messages": ["a", "b", "c", "d", "e"]}]'
    assert len(la._parse_ai_patterns(raw)[0]["sample_messages"]) == 3


# ---------------------------------------------------------------------------
# _ai_extract_log_patterns — graceful degradation contract
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, content):
        self.content = content


def _patch_router(monkeypatch, *, response=None, raises=None):
    """Install a fake LLMRouter on the tools.llm.router module object."""
    import sys as _sys
    router_mod = importlib.import_module("tools.llm.router")

    class _FakeRouter:
        def invoke(self, function, request):
            if raises is not None:
                raise raises
            return response

    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    # Patch ALL known router aliases — the _ToolsRedirect shim causes
    # `from tools.llm.router import LLMRouter` to resolve to different
    # module objects depending on full-suite import ordering.
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _FakeRouter)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _FakeRouter)


def test_extract_empty_messages_returns_none():
    assert la._ai_extract_log_patterns([]) is None


def test_extract_happy_path(monkeypatch):
    payload = '[{"name": "QuotaDrift", "count": 4}, {"name": "RetryStorm", "count": 9}]'
    _patch_router(monkeypatch, response=_FakeResp(payload))
    out = la._ai_extract_log_patterns(["some weird line"])
    # Sorted by count descending.
    assert [p["name"] for p in out] == ["RetryStorm", "QuotaDrift"]


def test_extract_llm_exception_degrades_to_none(monkeypatch):
    _patch_router(monkeypatch, raises=RuntimeError("LLM down"))
    assert la._ai_extract_log_patterns(["x"]) is None


def test_extract_empty_response_returns_none(monkeypatch):
    _patch_router(monkeypatch, response=_FakeResp(""))
    assert la._ai_extract_log_patterns(["x"]) is None


def test_extract_no_patterns_returns_none(monkeypatch):
    _patch_router(monkeypatch, response=_FakeResp("[]"))
    assert la._ai_extract_log_patterns(["x"]) is None


# ---------------------------------------------------------------------------
# analyze_logs wiring — deterministic by default, opt-in AI
# ---------------------------------------------------------------------------
def test_analyze_logs_default_has_empty_ai_patterns():
    # No source reachable → empty logs; key must still be present and empty.
    result = la.analyze_logs(source="elk", query="error", time_range="1h", elk_url="http://127.0.0.1:1")
    assert result["ai_extracted_patterns"] == []


def test_analyze_logs_ai_extraction_param_accepted():
    # Off-network call; extractor sees no unmatched lines → stays empty, no raise.
    result = la.analyze_logs(
        source="elk", query="error", time_range="1h", elk_url="http://127.0.0.1:1", use_ai_extraction=True
    )
    assert result["ai_extracted_patterns"] == []
