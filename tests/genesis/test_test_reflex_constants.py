# CUI // SP-CTI
"""Tests for constant extraction in the Genesis Test reflex (reflexes/test.py)."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.test import (
    _DEFAULT_MAX_TESTS_PER_RUN,
    _MIN_MODULE_LINES,
    _MAX_FUNCS_PER_MODULE,
    _MAX_SIG_PARAMS_ASSERTED,
    _MAX_INVOCATION_PARAMS,
    _MAX_PARAM_VALUES,
    _MAX_CLASSES_PER_MODULE,
    _MAX_METHODS_ASSERTED,
    _MAX_CONSTANTS_ASSERTED,
    _EXTRACT_TIMEOUT_SEC,
    _RUN_TEST_TIMEOUT_SEC,
    _STDOUT_TAIL_CHARS,
    _STDERR_TAIL_CHARS,
    _ERROR_SNIPPET_CHARS,
    _find_untested_modules,
    _generate_param_fixture,
)


class TestTestReflexConstants:
    def test_discovery_thresholds_positive(self):
        assert _DEFAULT_MAX_TESTS_PER_RUN > 0
        assert _MIN_MODULE_LINES > 0

    def test_generation_caps_positive(self):
        for cap in (
            _MAX_FUNCS_PER_MODULE,
            _MAX_SIG_PARAMS_ASSERTED,
            _MAX_INVOCATION_PARAMS,
            _MAX_PARAM_VALUES,
            _MAX_CLASSES_PER_MODULE,
            _MAX_METHODS_ASSERTED,
            _MAX_CONSTANTS_ASSERTED,
        ):
            assert cap > 0

    def test_invocation_arity_within_param_value_cap(self):
        # Invocation tests only emit below _MAX_INVOCATION_PARAMS arity, and the
        # synthesised positional values must not exceed the value cap.
        assert _MAX_INVOCATION_PARAMS <= _MAX_PARAM_VALUES

    def test_subprocess_timeouts_ordered(self):
        # Running a generated file (which itself may spawn work) gets at least
        # as long as a single API-surface extraction.
        assert _EXTRACT_TIMEOUT_SEC > 0
        assert _RUN_TEST_TIMEOUT_SEC >= _EXTRACT_TIMEOUT_SEC

    def test_capture_tails_positive_and_ordered(self):
        assert _STDOUT_TAIL_CHARS > 0
        assert _STDERR_TAIL_CHARS > 0
        assert _ERROR_SNIPPET_CHARS > 0
        # stdout carries the full pytest report; stderr tail is the shorter tail.
        assert _STDOUT_TAIL_CHARS >= _STDERR_TAIL_CHARS


class TestTestReflexBehavior:
    def test_find_untested_modules_respects_max_results(self):
        found = _find_untested_modules(max_results=3)
        assert isinstance(found, list)
        assert len(found) <= 3

    def test_find_untested_modules_default_cap(self):
        found = _find_untested_modules()
        assert len(found) <= _DEFAULT_MAX_TESTS_PER_RUN

    def test_param_fixture_uses_default_when_present(self):
        assert _generate_param_fixture({"name": "x", "type": "int", "default": "42"}) == "42"

    def test_param_fixture_type_based(self):
        assert _generate_param_fixture({"name": "label", "type": "str"}) == '"test_label"'
        assert _generate_param_fixture({"name": "n", "type": "int"}) == "1"
        assert _generate_param_fixture({"name": "flag", "type": "bool"}) == "True"

    def test_param_fixture_name_heuristic_for_limit(self):
        # count/limit/max names fall back to a small positive int literal.
        assert _generate_param_fixture({"name": "max_results", "type": ""}) == "5"
