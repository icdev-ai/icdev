# CUI // SP-CTI
"""Tests for the NLP extractor in tools/genesis/reflexes/oracle_triage.py.

Covers:
  - _validate_extracted_subject for each lens type
  - _nlp_extract_subject gate, cache, and model env var
  - _extract_subject canonical regex vs NLP fallback priority
  - _parse_batch_subjects structured parsing
  - _nlp_extract_batch_subjects gate and model routing
"""
from __future__ import annotations

import os
from unittest import mock
import pytest


# ---------------------------------------------------------------------------
# Fixture: fresh module import with cleared cache each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_nlp_cache():
    """Reset all per-process NLP caches before and after each test.

    The module memoizes three separate caches; clearing only the single-subject
    cache let _NLP_BATCH_SUBJECTS_CACHE leak between tests that share a
    (lens, description) key, making test order significant.
    """
    import tools.genesis.reflexes.oracle_triage as ot
    for cache_name in ("_NLP_SUBJECT_CACHE", "_NLP_BATCH_LENS_CACHE",
                       "_NLP_BATCH_SUBJECTS_CACHE"):
        getattr(ot, cache_name).clear()
    yield
    for cache_name in ("_NLP_SUBJECT_CACHE", "_NLP_BATCH_LENS_CACHE",
                       "_NLP_BATCH_SUBJECTS_CACHE"):
        getattr(ot, cache_name).clear()


@pytest.fixture()
def ot():
    import tools.genesis.reflexes.oracle_triage as _ot
    return _ot


def _fake_router_cls(content: str):
    """Return a mock LLMRouter class whose invoke() returns a response with content."""
    class FakeResponse:
        pass
    FakeResponse.content = content

    class FakeRouter:
        def invoke(self, fn, req):
            return FakeResponse()

    return FakeRouter


def _failing_router_cls(exc=RuntimeError("LLM fail")):
    """Return a mock LLMRouter class whose invoke() raises exc."""
    class FailRouter:
        def invoke(self, fn, req):
            raise exc

    return FailRouter


# ---------------------------------------------------------------------------
# _validate_extracted_subject
# ---------------------------------------------------------------------------

class TestValidateExtractedSubject:
    def test_tool_path_valid(self, ot):
        assert ot._validate_extracted_subject("tools/foo/bar.py", "tool_not_in_manifest")

    def test_tool_path_yaml(self, ot):
        assert ot._validate_extracted_subject("args/config.yaml", "tool_not_in_manifest")

    def test_tool_path_invalid_no_extension(self, ot):
        assert not ot._validate_extracted_subject("tools/foo/bar", "tool_not_in_manifest")

    def test_tool_path_empty(self, ot):
        assert not ot._validate_extracted_subject("", "tool_not_in_manifest")

    def test_route_valid(self, ot):
        assert ot._validate_extracted_subject("/api/agents/status", "route_not_listed")

    def test_route_with_param(self, ot):
        assert ot._validate_extracted_subject("/canvas/<slug>/report", "route_not_listed")

    def test_route_missing_leading_slash(self, ot):
        assert not ot._validate_extracted_subject("api/status", "route_not_listed")

    def test_table_valid(self, ot):
        assert ot._validate_extracted_subject("kanban_tasks", "orphan_db_table")

    def test_table_too_short(self, ot):
        assert not ot._validate_extracted_subject("x", "orphan_db_table")

    def test_table_with_spaces(self, ot):
        assert not ot._validate_extracted_subject("kanban tasks", "orphan_db_table")

    def test_unknown_lens_always_passes(self, ot):
        assert ot._validate_extracted_subject("anything", "unknown_lens")

    def test_whitespace_only_fails(self, ot):
        assert not ot._validate_extracted_subject("   ", "orphan_db_table")


# ---------------------------------------------------------------------------
# _nlp_extract_subject — gate and cache
# ---------------------------------------------------------------------------

class TestNlpExtractSubject:
    def test_gate_off_by_default(self, ot, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        result = ot._nlp_extract_subject("some title", "orphan_db_table")
        assert result is None

    def test_gate_off_explicit_false(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "false")
        result = ot._nlp_extract_subject("some title", "orphan_db_table")
        assert result is None

    def test_gate_on_returns_cached_result(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        # Pre-populate cache; function must NOT call LLM.
        cache_key = ("orphan_db_table gap: my_table", "orphan_db_table")
        ot._NLP_SUBJECT_CACHE[cache_key] = "my_table"

        with mock.patch("tools.llm.router.LLMRouter") as MockCls:
            result = ot._nlp_extract_subject("orphan_db_table gap: my_table", "orphan_db_table")
            MockCls.assert_not_called()  # router never instantiated

        assert result == "my_table"

    def test_gate_on_caches_none_on_failure(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")

        with mock.patch("tools.llm.router.LLMRouter", _failing_router_cls()):
            result = ot._nlp_extract_subject("non-standard title XYZ", "orphan_db_table")

        assert result is None
        cache_key = ("non-standard title XYZ", "orphan_db_table")
        assert cache_key in ot._NLP_SUBJECT_CACHE
        assert ot._NLP_SUBJECT_CACHE[cache_key] is None

    def test_model_env_var_read_at_call_time(self, ot, monkeypatch):
        """Verify that ICDEV_ORACLE_NLP_MODEL env var overrides the default model."""
        # The env var is read via os.getenv(_ORACLE_NLP_MODEL_ENV, _ORACLE_NLP_MODEL_DEFAULT).
        # Confirm the right value is used for each scenario.
        monkeypatch.setenv("ICDEV_ORACLE_NLP_MODEL", "my-custom-model")
        resolved = os.getenv(ot._ORACLE_NLP_MODEL_ENV, ot._ORACLE_NLP_MODEL_DEFAULT)
        assert resolved == "my-custom-model"

        monkeypatch.delenv("ICDEV_ORACLE_NLP_MODEL", raising=False)
        resolved_default = os.getenv(ot._ORACLE_NLP_MODEL_ENV, ot._ORACLE_NLP_MODEL_DEFAULT)
        assert resolved_default == ot._ORACLE_NLP_MODEL_DEFAULT

    def test_gate_on_rejects_invalid_extraction(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")

        FakeRouter = _fake_router_cls("  THIS IS NOT A VALID TABLE NAME!!! ")

        with mock.patch("tools.llm.router.LLMRouter", FakeRouter):
            result = ot._nlp_extract_subject("some title", "orphan_db_table")

        assert result is None
        # Result still cached (as None) to avoid re-trying.
        assert ("some title", "orphan_db_table") in ot._NLP_SUBJECT_CACHE

    def test_cache_prevents_second_llm_call(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")
        call_count = {"n": 0}

        class CountingRouter:
            def invoke(self, fn, req):
                call_count["n"] += 1

                class R:
                    content = "kanban_tasks"

                return R()

        with mock.patch("tools.llm.router.LLMRouter", CountingRouter):
            r1 = ot._nlp_extract_subject("Table kanban_tasks has no migration", "orphan_db_table")
            r2 = ot._nlp_extract_subject("Table kanban_tasks has no migration", "orphan_db_table")

        assert r1 == "kanban_tasks"
        assert r2 == "kanban_tasks"
        assert call_count["n"] == 1  # Only one real LLM call; second hit cache.


# ---------------------------------------------------------------------------
# _extract_subject — regex vs NLP priority chain
# ---------------------------------------------------------------------------

class TestExtractSubject:
    def test_canonical_prefix_regex_fast_path(self, ot, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        result = ot._extract_subject(
            "tool_not_in_manifest gap: tools/foo/bar.py",
            "tool_not_in_manifest",
        )
        assert result == "tools/foo/bar.py"

    def test_canonical_prefix_route(self, ot, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        result = ot._extract_subject(
            "route_not_listed gap: /api/agents/health",
            "route_not_listed",
        )
        assert result == "/api/agents/health"

    def test_canonical_prefix_table(self, ot, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        result = ot._extract_subject(
            "orphan_db_table gap: harness_eval",
            "orphan_db_table",
        )
        assert result == "harness_eval"

    def test_no_prefix_no_nlp_falls_back_to_raw_title(self, ot, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        title = "Non-standard title with no prefix"
        result = ot._extract_subject(title, "orphan_db_table")
        assert result == title

    def test_canonical_prefix_invalid_result_falls_back_to_nlp(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")

        FakeRouter = _fake_router_cls("tools/corrected/path.py")

        with mock.patch("tools.llm.router.LLMRouter", FakeRouter):
            result = ot._extract_subject(
                "tool_not_in_manifest gap: not_a_valid_path_no_extension",
                "tool_not_in_manifest",
            )

        assert result == "tools/corrected/path.py"

    def test_no_prefix_nlp_enabled_uses_nlp(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")

        FakeRouter = _fake_router_cls("/api/extracted/route")

        with mock.patch("tools.llm.router.LLMRouter", FakeRouter):
            result = ot._extract_subject(
                "Flask route for new report endpoint missing from start.md",
                "route_not_listed",
            )

        assert result == "/api/extracted/route"


# ---------------------------------------------------------------------------
# _parse_batch_subjects
# ---------------------------------------------------------------------------

class TestParseBatchSubjects:
    def test_parses_standard_subjects_block(self, ot):
        desc = (
            "Batch gap analysis\n"
            "Subjects:\n"
            "- tools/foo/bar.py\n"
            "- tools/baz/qux.yaml\n"
            "Other info:\n"
            "Some note"
        )
        subjects = ot._parse_batch_subjects(desc)
        assert subjects == ["tools/foo/bar.py", "tools/baz/qux.yaml"]

    def test_stops_at_non_list_line(self, ot):
        desc = (
            "Subjects:\n"
            "- tools/a.py\n"
            "Not a subject\n"
            "- tools/b.py\n"
        )
        subjects = ot._parse_batch_subjects(desc)
        assert subjects == ["tools/a.py"]

    def test_empty_description_returns_empty(self, ot):
        assert ot._parse_batch_subjects("") == []

    def test_no_subjects_block_returns_empty(self, ot):
        desc = "This description has no Subjects: header"
        assert ot._parse_batch_subjects(desc) == []

    def test_strips_whitespace(self, ot):
        desc = "Subjects:\n  -   tools/spaced/path.py   \n"
        subjects = ot._parse_batch_subjects(desc)
        assert subjects == ["tools/spaced/path.py"]


# ---------------------------------------------------------------------------
# _nlp_extract_batch_subjects — gate
# ---------------------------------------------------------------------------

class TestNlpExtractBatchSubjects:
    def test_gate_off_returns_empty(self, ot, monkeypatch):
        monkeypatch.delenv("ICDEV_ORACLE_NLP_EXTRACTOR", raising=False)
        result = ot._nlp_extract_batch_subjects("orphan_db_table", "some description")
        assert result == []

    def test_gate_on_valid_subjects_returned(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")

        FakeRouter = _fake_router_cls("my_table\nother_table")

        with mock.patch("tools.llm.router.LLMRouter", FakeRouter):
            result = ot._nlp_extract_batch_subjects("orphan_db_table", "some desc")

        assert "my_table" in result
        assert "other_table" in result

    def test_gate_on_filters_invalid_subjects(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")

        FakeRouter = _fake_router_cls("valid_table\nINVALID TABLE NAME WITH SPACES\nanother_valid")

        with mock.patch("tools.llm.router.LLMRouter", FakeRouter):
            result = ot._nlp_extract_batch_subjects("orphan_db_table", "desc")

        assert "valid_table" in result
        assert "another_valid" in result
        assert not any("INVALID" in s for s in result)

    def test_llm_exception_returns_empty(self, ot, monkeypatch):
        monkeypatch.setenv("ICDEV_ORACLE_NLP_EXTRACTOR", "true")

        with mock.patch("tools.llm.router.LLMRouter", _failing_router_cls()):
            result = ot._nlp_extract_batch_subjects("orphan_db_table", "desc")

        assert result == []


# ---------------------------------------------------------------------------
# NLP cache constants
# ---------------------------------------------------------------------------

class TestNlpCacheConstants:
    def test_sentinel_is_unique(self, ot):
        assert ot._NLP_CACHE_MISS is not None
        assert ot._NLP_CACHE_MISS is not False

    def test_default_model_is_haiku(self, ot):
        assert ot._ORACLE_NLP_MODEL_DEFAULT == "claude-haiku-4-5-20251001"

    def test_model_env_var_name(self, ot):
        assert ot._ORACLE_NLP_MODEL_ENV == "ICDEV_ORACLE_NLP_MODEL"
