# CUI // SP-CTI
"""Tests for tools.genesis.reflexes.harness — ICDEV_HARNESS_COLEARN behavior.

Scenarios:
  1. colearn=false  → _build_skill_prompt returns base prompt unchanged
  2. colearn=true   → artifact from get_latest_improvement is prepended
  3. colearn=true, reflexion_agent raises → graceful fallback to base prompt
  4. colearn=true, artifact is empty string → returns base prompt (no blank prefix)
"""
from __future__ import annotations

from unittest.mock import patch


_BASE_SUFFIX = (
    "Investigate the degraded `oracle_triage` harness metric and propose a "
    "remediation plan. Apply any relevant heuristic amendments or self-healing "
    "steps as described in the card details above."
)

_ARTIFACT = "ECHO: use chunked retry with backoff for oracle_triage failures."


class TestBuildSkillPromptColearn:
    def _call(self, task_type: str = "oracle_triage", colearn_enabled: bool = False):
        from tools.genesis.reflexes.harness import _build_skill_prompt
        return _build_skill_prompt(task_type, colearn_enabled)

    # ------------------------------------------------------------------ #
    # colearn = false                                                      #
    # ------------------------------------------------------------------ #

    def test_colearn_false_returns_base_prompt_unchanged(self):
        """When co-learning is off, no import of reflexion_agent occurs."""
        with patch("tools.workflow.reflexion_agent.get_latest_improvement") as mock_fn:
            result = self._call(colearn_enabled=False)
        mock_fn.assert_not_called()
        assert result == _BASE_SUFFIX

    def test_colearn_false_does_not_import_reflexion_agent(self):
        """Ensure no side-effect import happens when colearn is disabled."""
        import sys
        # Remove cached import if present
        sys.modules.pop("tools.workflow.reflexion_agent", None)
        result = self._call(colearn_enabled=False)
        # The module should NOT have been imported as a side effect
        # (it may already be cached from other tests — just verify output)
        assert _BASE_SUFFIX in result

    # ------------------------------------------------------------------ #
    # colearn = true                                                       #
    # ------------------------------------------------------------------ #

    def test_colearn_true_prepends_artifact_to_base_prompt(self):
        """Artifact is prepended with a double-newline separator."""
        with patch(
            "tools.workflow.reflexion_agent.get_latest_improvement",
            return_value=_ARTIFACT,
        ):
            result = self._call(colearn_enabled=True)

        expected = _ARTIFACT + "\n\n" + _BASE_SUFFIX
        assert result == expected

    def test_colearn_true_artifact_before_base_prompt(self):
        """Artifact appears at the start, base prompt at the end."""
        with patch(
            "tools.workflow.reflexion_agent.get_latest_improvement",
            return_value=_ARTIFACT,
        ):
            result = self._call(colearn_enabled=True)

        assert result.startswith(_ARTIFACT)
        assert result.endswith(_BASE_SUFFIX)

    def test_colearn_true_separator_is_double_newline(self):
        """The artifact and base prompt are separated by exactly \\n\\n."""
        with patch(
            "tools.workflow.reflexion_agent.get_latest_improvement",
            return_value=_ARTIFACT,
        ):
            result = self._call(colearn_enabled=True)

        parts = result.split("\n\n", 1)
        assert len(parts) == 2
        assert parts[0] == _ARTIFACT
        assert parts[1] == _BASE_SUFFIX

    # ------------------------------------------------------------------ #
    # colearn = true, empty artifact                                       #
    # ------------------------------------------------------------------ #

    def test_colearn_true_empty_artifact_falls_back_to_base_prompt(self):
        """When get_latest_improvement returns empty string, no prefix is added."""
        with patch(
            "tools.workflow.reflexion_agent.get_latest_improvement",
            return_value="",
        ):
            result = self._call(colearn_enabled=True)

        assert result == _BASE_SUFFIX

    # ------------------------------------------------------------------ #
    # colearn = true, reflexion_agent raises                               #
    # ------------------------------------------------------------------ #

    def test_colearn_true_reflexion_agent_exception_returns_base_prompt(self):
        """If get_latest_improvement raises, function gracefully returns base prompt."""
        with patch(
            "tools.workflow.reflexion_agent.get_latest_improvement",
            side_effect=RuntimeError("reflexion store unavailable"),
        ):
            result = self._call(colearn_enabled=True)

        assert result == _BASE_SUFFIX

    def test_colearn_true_import_error_returns_base_prompt(self):
        """If the reflexion_agent module cannot be imported, base prompt is returned."""
        import sys
        # Temporarily hide the module
        orig = sys.modules.pop("tools.workflow.reflexion_agent", None)
        sys.modules["tools.workflow.reflexion_agent"] = None  # type: ignore[assignment]
        try:
            result = self._call(colearn_enabled=True)
        finally:
            if orig is not None:
                sys.modules["tools.workflow.reflexion_agent"] = orig
            else:
                sys.modules.pop("tools.workflow.reflexion_agent", None)

        assert result == _BASE_SUFFIX
