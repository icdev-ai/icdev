"""Tests for the _handle_rewrite auto-fix loop (max 3 attempts)."""

import unittest
from unittest.mock import patch


def _make_quality(passed: bool, score: float = 60.0) -> dict:
    return {"passed": passed, "overall_score": score, "details": {}, "recommendations": []}


class TestHandleRewriteLoop(unittest.TestCase):

    def _import_handle_rewrite(self):
        """Import _handle_rewrite without triggering heavy scheduler deps."""
        import importlib
        mod = importlib.import_module("tools.pulse.engine.scheduler")
        return mod._handle_rewrite, mod._MAX_REWRITE_ATTEMPTS

    def test_max_attempts_constant_is_three(self):
        _, max_attempts = self._import_handle_rewrite()
        self.assertEqual(max_attempts, 3)

    def test_passes_on_first_attempt_stops_loop(self):
        handle_rewrite, _ = self._import_handle_rewrite()

        with (
            patch("tools.pulse.engine.scheduler._auto_rewrite") as mock_ar,
            patch("tools.pulse.engine.scheduler._run_quality_check") as mock_qc,
        ):
            mock_ar.return_value = {"status": "rewritten", "body_markdown": "improved body"}
            mock_qc.return_value = _make_quality(passed=True, score=85.0)

            quality, rewrite_data = handle_rewrite(
                "run-1", "post-1", "original body", _make_quality(passed=False), True
            )

        self.assertTrue(quality["passed"])
        self.assertEqual(mock_ar.call_count, 1, "Should stop after first passing attempt")
        self.assertEqual(rewrite_data.get("rewrite_attempts"), 1)

    def test_loops_up_to_max_three_when_never_passes(self):
        handle_rewrite, _ = self._import_handle_rewrite()

        with (
            patch("tools.pulse.engine.scheduler._auto_rewrite") as mock_ar,
            patch("tools.pulse.engine.scheduler._run_quality_check") as mock_qc,
        ):
            mock_ar.return_value = {"status": "rewritten", "body_markdown": "still needs work"}
            mock_qc.return_value = _make_quality(passed=False, score=55.0)

            quality, rewrite_data = handle_rewrite(
                "run-2", "post-2", "original body", _make_quality(passed=False), True
            )

        self.assertFalse(quality["passed"])
        self.assertEqual(mock_ar.call_count, 3, "Should attempt exactly 3 times")
        self.assertEqual(rewrite_data.get("rewrite_attempts"), 3)

    def test_passes_on_second_attempt_stops_at_two(self):
        handle_rewrite, _ = self._import_handle_rewrite()

        quality_sequence = [
            _make_quality(passed=False, score=60.0),
            _make_quality(passed=True, score=88.0),
        ]

        with (
            patch("tools.pulse.engine.scheduler._auto_rewrite") as mock_ar,
            patch("tools.pulse.engine.scheduler._run_quality_check") as mock_qc,
        ):
            mock_ar.return_value = {"status": "rewritten", "body_markdown": "better body"}
            mock_qc.side_effect = quality_sequence

            quality, rewrite_data = handle_rewrite(
                "run-3", "post-3", "original body", _make_quality(passed=False), True
            )

        self.assertTrue(quality["passed"])
        self.assertEqual(mock_ar.call_count, 2)
        self.assertEqual(rewrite_data.get("rewrite_attempts"), 2)

    def test_failed_rewrite_breaks_loop(self):
        handle_rewrite, _ = self._import_handle_rewrite()

        with (
            patch("tools.pulse.engine.scheduler._auto_rewrite") as mock_ar,
            patch("tools.pulse.engine.scheduler._run_quality_check") as mock_qc,
        ):
            mock_ar.return_value = {"status": "rewrite_failed", "body_markdown": ""}
            mock_qc.return_value = _make_quality(passed=False)

            quality, rewrite_data = handle_rewrite(
                "run-4", "post-4", "original body", _make_quality(passed=False), True
            )

        self.assertEqual(mock_ar.call_count, 1, "Broken rewrite should abort loop")
        mock_qc.assert_not_called()

    def test_no_loop_when_already_passing(self):
        handle_rewrite, _ = self._import_handle_rewrite()

        with patch("tools.pulse.engine.scheduler._auto_rewrite") as mock_ar:
            quality, rewrite_data = handle_rewrite(
                "run-5", "post-5", "body", _make_quality(passed=True), True
            )

        mock_ar.assert_not_called()
        self.assertIsNone(rewrite_data)

    def test_no_loop_when_auto_rewrite_false(self):
        handle_rewrite, _ = self._import_handle_rewrite()

        with (
            patch("tools.pulse.engine.scheduler._auto_rewrite") as mock_ar,
            patch(
                "tools.pulse.engine.scheduler.rewrite_content",
                return_value={"status": "deterministic_fixes_applied"},
            ) as mock_rc,
        ):
            quality, rewrite_data = handle_rewrite(
                "run-6", "post-6", "body", _make_quality(passed=False), False
            )

        mock_ar.assert_not_called()
        mock_rc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
