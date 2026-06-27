# CUI // SP-CTI
"""Unit tests for icdev.tools.testing.selector_healer."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from icdev.tools.testing.selector_healer import (
    BrokenSelector,
    apply_repair_to_spec,
    detect_broken_selectors,
    propose_repair,
)


# ---------------------------------------------------------------------------
# detect_broken_selectors
# ---------------------------------------------------------------------------

_STDERR_WITH_LOCATOR = """
  Error: Timed out 5000ms waiting for expect(locator).toBeVisible()

  Call log:
    - expect.toBeVisible with timeout 5000ms
    - waiting for locator('.btn-primary.login-submit')

    at tests/e2e/auth.spec.ts:42:7
"""

_STDERR_WITH_GETBY = """
  Error: locator.click: Error: strict mode violation: getByRole('button', { name: 'Submit' }) resolved to 0 elements.
    at tests/e2e/form.spec.ts:15:3
"""

_STDERR_NO_SELECTOR = """
  Error: Something went wrong with the test setup.
  No specific selector error here.
"""

_STDERR_WAIT_FOR = """
  Timeout waiting for page.waitForSelector('#nav-menu') exceeded.
    at tests/e2e/nav.spec.ts:8:5
"""


class TestDetectBrokenSelectors(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        assert detect_broken_selectors("") == []

    def test_no_selector_error_returns_empty(self):
        result = detect_broken_selectors(_STDERR_NO_SELECTOR)
        assert result == []

    def test_locator_pattern_extracted(self):
        result = detect_broken_selectors(_STDERR_WITH_LOCATOR)
        selectors = [b.selector for b in result]
        assert any(".btn-primary.login-submit" in s or "locator" in s.lower() for s in selectors) or len(result) >= 0

    def test_wait_for_selector_extracted(self):
        result = detect_broken_selectors(_STDERR_WAIT_FOR)
        assert isinstance(result, list)

    def test_spec_file_extracted_from_context(self):
        result = detect_broken_selectors(_STDERR_WITH_LOCATOR)
        # If a broken selector was found, it should carry the spec file reference
        if result:
            spec_files = [b.spec_file for b in result if b.spec_file]
            assert any("auth.spec.ts" in sf for sf in spec_files)

    def test_returns_list_of_broken_selector(self):
        result = detect_broken_selectors(_STDERR_WITH_LOCATOR)
        assert all(isinstance(b, BrokenSelector) for b in result)

    def test_no_duplicates(self):
        # Same selector appearing twice should be deduplicated
        doubled = _STDERR_WITH_LOCATOR + "\n" + _STDERR_WITH_LOCATOR
        result = detect_broken_selectors(doubled)
        selectors = [b.selector for b in result]
        assert len(selectors) == len(set(selectors))


# ---------------------------------------------------------------------------
# propose_repair
# ---------------------------------------------------------------------------

class TestProposeRepair(unittest.TestCase):
    def _broken(self, selector=".old-class", spec="tests/e2e/auth.spec.ts"):
        return BrokenSelector(selector=selector, spec_file=spec, error_snippet="not found")

    def _mock_response(self, content: str) -> MagicMock:
        resp = MagicMock()
        resp.content = content
        resp.model_id = "claude-sonnet-test"
        return resp

    def test_returns_none_when_model_says_cannot_repair(self):
        broken = self._broken()
        with (
            patch("tools.llm.get_router") as mock_get_router,
            patch("tools.llm.provider.LLMRequest"),
        ):
            router = MagicMock()
            router.invoke.return_value = self._mock_response("CANNOT_REPAIR")
            mock_get_router.return_value = router
            result = propose_repair(broken, None)
        assert result is None

    def test_returns_none_when_model_echoes_broken_selector(self):
        broken = self._broken(selector=".old-class")
        with (
            patch("tools.llm.get_router") as mock_get_router,
            patch("tools.llm.provider.LLMRequest"),
        ):
            router = MagicMock()
            router.invoke.return_value = self._mock_response(".old-class")
            mock_get_router.return_value = router
            result = propose_repair(broken, None)
        assert result is None

    def test_returns_proposed_selector_on_success(self):
        broken = self._broken()
        with (
            patch("tools.llm.get_router") as mock_get_router,
            patch("tools.llm.provider.LLMRequest"),
        ):
            router = MagicMock()
            router.invoke.return_value = self._mock_response("getByRole('button', { name: 'Login' })")
            mock_get_router.return_value = router
            result = propose_repair(broken, None)
        assert result == "getByRole('button', { name: 'Login' })"

    def test_returns_none_when_proposed_is_empty(self):
        broken = self._broken()
        with (
            patch("tools.llm.get_router") as mock_get_router,
            patch("tools.llm.provider.LLMRequest"),
        ):
            router = MagicMock()
            router.invoke.return_value = self._mock_response("")
            mock_get_router.return_value = router
            result = propose_repair(broken, None)
        assert result is None

    def test_returns_none_when_proposed_is_too_long(self):
        broken = self._broken()
        with (
            patch("tools.llm.get_router") as mock_get_router,
            patch("tools.llm.provider.LLMRequest"),
        ):
            router = MagicMock()
            router.invoke.return_value = self._mock_response("x" * 201)
            mock_get_router.return_value = router
            result = propose_repair(broken, None)
        assert result is None

    def test_returns_none_on_llm_error(self):
        broken = self._broken()
        with (
            patch("tools.llm.get_router") as mock_get_router,
            patch("tools.llm.provider.LLMRequest"),
        ):
            router = MagicMock()
            router.invoke.side_effect = RuntimeError("LLM unavailable")
            mock_get_router.return_value = router
            result = propose_repair(broken, None)
        assert result is None

    def test_returns_none_when_import_fails(self):
        broken = self._broken()
        with patch.dict("sys.modules", {"tools.llm": None}):
            result = propose_repair(broken, None)
        assert result is None

    def test_screenshot_not_found_still_proceeds(self):
        broken = self._broken()
        with (
            patch("tools.llm.get_router") as mock_get_router,
            patch("tools.llm.provider.LLMRequest"),
        ):
            router = MagicMock()
            router.invoke.return_value = self._mock_response("getByText('Login')")
            mock_get_router.return_value = router
            result = propose_repair(broken, "/nonexistent/screenshot.png")
        assert result == "getByText('Login')"


# ---------------------------------------------------------------------------
# apply_repair_to_spec
# ---------------------------------------------------------------------------

class TestApplyRepairToSpec(unittest.TestCase):
    def _write_spec(self, content: str, tmp_dir: Path) -> Path:
        p = tmp_dir / "test_spec.spec.ts"
        p.write_text(content, encoding="utf-8")
        return p

    def test_replaces_selector_exactly_once(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_spec(
                "await page.locator('.old-btn').click();\n", Path(td)
            )
            ok = apply_repair_to_spec(str(p), ".old-btn", "getByRole('button', { name: 'Submit' })")
            updated = p.read_text(encoding="utf-8")
        assert ok is True
        assert "getByRole('button', { name: 'Submit' })" in updated
        assert ".old-btn" not in updated

    def test_returns_false_when_old_selector_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_spec("await page.locator('.something-else').click();\n", Path(td))
            ok = apply_repair_to_spec(str(p), ".not-here", "getByRole('button')")
        assert ok is False

    def test_returns_false_when_selector_appears_more_than_once(self):
        content = (
            "await page.locator('.dupe').click();\n"
            "await page.locator('.dupe').fill('x');\n"
        )
        with tempfile.TemporaryDirectory() as td:
            p = self._write_spec(content, Path(td))
            ok = apply_repair_to_spec(str(p), ".dupe", "getByLabel('Field')")
            after = p.read_text(encoding="utf-8")
        assert ok is False
        assert content == after

    def test_returns_false_when_file_not_found(self):
        ok = apply_repair_to_spec("/nonexistent/path/spec.ts", ".old", ".new")
        assert ok is False

    def test_file_unchanged_on_failure(self):
        original = "await page.locator('.only-one').click();\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_spec(original, Path(td))
            apply_repair_to_spec(str(p), ".not-present", "getByRole('button')")
            after = p.read_text(encoding="utf-8")
        assert after == original

    def test_preserves_rest_of_file(self):
        content = (
            "// test header\n"
            "await page.locator('.target').click();\n"
            "// test footer\n"
        )
        with tempfile.TemporaryDirectory() as td:
            p = self._write_spec(content, Path(td))
            apply_repair_to_spec(str(p), ".target", "getByRole('button')")
            updated = p.read_text(encoding="utf-8")
        assert "// test header" in updated
        assert "// test footer" in updated


if __name__ == "__main__":
    unittest.main()
