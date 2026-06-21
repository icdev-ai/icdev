# CUI // SP-CTI
"""Tests for tools.ai_augmentation.nlp_extractor."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tools.ai_augmentation.nlp_extractor import (
    _read_snippet,
    enrich_regex_patterns,
)


# ── _read_snippet ─────────────────────────────────────────────────────────────

def test_read_snippet_returns_context_lines(tmp_path):
    src = tmp_path / "sample.py"
    src.write_text("\n".join(f"line{i}" for i in range(1, 21)), encoding="utf-8")
    snippet = _read_snippet(str(src), line_start=10, context=3)
    lines = snippet.splitlines()
    assert len(lines) > 0
    # Should include lines around line 10
    assert any("line10" in ln for ln in lines)


def test_read_snippet_missing_file_returns_empty():
    result = _read_snippet("/nonexistent/path.py", line_start=5, context=3)
    assert result == ""


# ── enrich_regex_patterns — pass-through cases ────────────────────────────────

def test_non_regex_patterns_unchanged():
    patterns = [
        {"pattern_type": "nested_conditionals", "pattern_detail": {"max_depth": 4}},
        {"pattern_type": "hardcoded_threshold", "pattern_detail": {"constants": [0.5]}},
    ]
    result = enrich_regex_patterns(list(patterns))
    assert result[0]["pattern_detail"] == {"max_depth": 4}
    assert result[1]["pattern_detail"] == {"constants": [0.5]}


def test_no_llm_env_skips_enrichment(monkeypatch):
    monkeypatch.setenv("ICDEV_NO_LLM", "true")
    patterns = [
        {"pattern_type": "regex_user_input", "pattern_detail": {"call": "re.search"},
         "module_path": "/fake/path.py", "line_start": 1},
    ]
    result = enrich_regex_patterns(patterns)
    assert "nlp" not in result[0]["pattern_detail"]


def test_max_enrich_cap_respected():
    patterns = [
        {"pattern_type": "regex_user_input", "pattern_detail": {"call": "re.match"},
         "module_path": "/fake/a.py", "line_start": i}
        for i in range(1, 6)
    ]
    enriched_calls = []

    def fake_call_llm(snippet, call_expr):
        enriched_calls.append(call_expr)
        return {"intent": "validation", "input_type": "generic",
                "is_user_facing": False, "nlp_alternative": "Use NLP."}

    with patch("tools.ai_augmentation.nlp_extractor._call_llm", side_effect=fake_call_llm):
        enrich_regex_patterns(patterns, max_enrich=2)

    assert len(enriched_calls) == 2


def test_llm_failure_leaves_pattern_unchanged():
    patterns = [
        {"pattern_type": "regex_user_input", "pattern_detail": {"call": "re.search"},
         "module_path": "/fake/path.py", "line_start": 1},
    ]
    with patch("tools.ai_augmentation.nlp_extractor._call_llm", return_value={}):
        result = enrich_regex_patterns(patterns, max_enrich=5)
    assert "nlp" not in result[0]["pattern_detail"]


def test_successful_enrichment_adds_nlp_key():
    patterns = [
        {"pattern_type": "regex_user_input", "pattern_detail": {"call": "re.match"},
         "module_path": "/fake/path.py", "line_start": 1},
    ]
    nlp_payload = {
        "intent": "validation",
        "input_type": "email",
        "is_user_facing": True,
        "nlp_alternative": "Use a pre-trained NER model to validate email addresses.",
    }
    with patch("tools.ai_augmentation.nlp_extractor._call_llm", return_value=nlp_payload):
        result = enrich_regex_patterns(patterns, max_enrich=5)
    assert result[0]["pattern_detail"]["nlp"] == nlp_payload


def test_mixed_patterns_only_regex_enriched():
    patterns = [
        {"pattern_type": "nested_conditionals", "pattern_detail": {}},
        {"pattern_type": "regex_user_input", "pattern_detail": {"call": "re.findall"},
         "module_path": "/fake/path.py", "line_start": 10},
    ]
    nlp_payload = {"intent": "extraction", "input_type": "url",
                   "is_user_facing": True, "nlp_alternative": "Use spaCy URL detection."}
    with patch("tools.ai_augmentation.nlp_extractor._call_llm", return_value=nlp_payload):
        result = enrich_regex_patterns(patterns, max_enrich=5)
    assert "nlp" not in result[0]["pattern_detail"]
    assert result[1]["pattern_detail"]["nlp"] == nlp_payload


def test_source_root_resolves_relative_path(tmp_path):
    src_file = tmp_path / "mymodule.py"
    src_file.write_text("import re\nre.search(r'.*', x)\n", encoding="utf-8")

    patterns = [
        {"pattern_type": "regex_user_input",
         "pattern_detail": {"call": "re.search"},
         "module_path": "mymodule.py",
         "line_start": 2},
    ]
    read_paths = []
    __import__("tools.ai_augmentation.nlp_extractor", fromlist=["_read_snippet"])

    def fake_read_snippet(module_path, line_start, context):
        read_paths.append(module_path)
        return ""

    with patch("tools.ai_augmentation.nlp_extractor._read_snippet", side_effect=fake_read_snippet):
        with patch("tools.ai_augmentation.nlp_extractor._call_llm", return_value={}):
            enrich_regex_patterns(patterns, source_root=str(tmp_path), max_enrich=5)

    assert read_paths and str(tmp_path) in read_paths[0]
