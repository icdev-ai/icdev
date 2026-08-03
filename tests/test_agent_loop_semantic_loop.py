# CUI // SP-CTI
"""Semantic loop detection for the agent loop (ars-loop-01).

Two properties are under test, and the second matters more than the first:

1. A loop of equivalent-but-not-identical actions is caught — early, and with a
   ``truncation_reason`` that says "loop", not "budget".
2. Legitimate iterative work of the same length is **not** caught. A false
   positive kills real work, which is worse than the loop it prevents.

The detector's own thresholds were tuned by replaying real transcripts through
``tools/llm/loop_detector_tune.py``; these tests pin the behaviour that tuning
settled on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from icdev.tools.llm.agent_loop import ResultSubtype, run_agent_loop
from icdev.tools.llm.loop_detector import (
    DEFAULT_CONFIG,
    ToolCallRecord,
    argument_similarity,
    detect_semantic_loop,
    load_detector_config,
    similarity,
)
from icdev.tools.llm.provider import LLMResponse


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeProvider:
    provider_name: str = "anthropic"


class ScriptedRouter:
    """Returns a scripted sequence: list-of-tool-calls, or str for a final answer."""

    def __init__(
        self,
        responses: list[Any],
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []
        self._provider = FakeProvider()
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    def get_provider_for_function(self, function: str):
        return self._provider, "fake-model", {"supports_tools": True}

    def invoke(self, function: str, request: Any) -> LLMResponse:
        entry = self._responses[len(self.calls)]
        self.calls.append(function)
        if isinstance(entry, str):
            return LLMResponse(
                content=entry,
                stop_reason="end_turn",
                provider="fake",
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            )
        return LLMResponse(
            content="",
            tool_calls=list(entry),
            stop_reason="tool_use",
            provider="fake",
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )


def _tool(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _records(*specs: tuple[int, str, dict[str, Any], str]) -> list[ToolCallRecord]:
    return [
        ToolCallRecord(turn=turn, name=name, arguments=args, result=result)
        for turn, name, args, result in specs
    ]


# ---------------------------------------------------------------------------
# Similarity primitive
# ---------------------------------------------------------------------------


class TestSimilarity:
    def test_path_spelling_variants_are_identical(self):
        assert similarity("tools/foo.py", "./tools/foo.py") == 1.0
        assert similarity("tools/foo.py", "tools\\foo.py") == 1.0
        assert similarity("tools/foo.py", "TOOLS/FOO.PY") == 1.0

    def test_argument_reordering_is_identical(self):
        assert similarity("pytest tests/test_a.py -v", "pytest -v tests/test_a.py") == 1.0

    def test_neighbouring_filenames_are_not_similar(self):
        # One character apart, so SequenceMatcher alone scores this ~0.98. The
        # token-set half of the metric is what keeps it below threshold.
        assert similarity("file1.txt", "file2.txt") < DEFAULT_CONFIG["similarity_threshold"]

    def test_a_long_shared_field_cannot_carry_a_changed_one(self):
        """Per-key comparison: the weakest field decides, not the concatenation.

        Found by replaying real transcripts — an ``Edit`` repeats a 100+ character
        absolute ``file_path`` while ``old_string``/``new_string`` change entirely,
        and comparing the flattened payload scored that over threshold.
        """
        path = "C:/AI/ICDev/tools/dashboard/templates/observability/page.html"
        left = {"file_path": path, "old_string": "<h1>Old heading</h1>", "new_string": "<h1>New</h1>"}
        right = {"file_path": path, "old_string": "def compute(a, b):", "new_string": "def compute(a, b, c):"}
        assert argument_similarity(left, right) < DEFAULT_CONFIG["similarity_threshold"]
        # The same payloads flattened would have looked equivalent.
        assert similarity(str(left), str(right)) > argument_similarity(left, right)

    def test_a_key_present_on_one_side_only_is_a_difference(self):
        assert argument_similarity({"cmd": "ls", "timeout": 5}, {"cmd": "ls"}) == 0.0

    def test_digits_survive_normalisation(self):
        # "5 failed" must never normalise to "2 failed" — that is the progress signal.
        assert similarity("5 failed, 3 passed", "2 failed, 6 passed") < 1.0


# ---------------------------------------------------------------------------
# Detector — true positives
# ---------------------------------------------------------------------------


class TestDetectorCatchesLoops:
    def test_same_file_reread_under_different_spellings(self):
        detection = detect_semantic_loop(
            _records(
                (0, "read", {"path": "tools/foo.py"}, "def foo():\n    return 1\n"),
                (1, "read", {"path": "./tools/foo.py"}, "def foo():\n    return 1\n"),
                (2, "read", {"path": "tools\\foo.py"}, "def foo():\n    return 1\n"),
            )
        )
        assert detection.detected is True
        assert detection.tool_name == "read"
        assert detection.cluster_size == 3
        assert detection.distinct_turns == 3
        assert detection.distinct_variants == 3

    def test_failing_command_rerun_with_cosmetic_variation(self):
        err = "ERROR: file or directory not found: tests/test_missing.py\n"
        detection = detect_semantic_loop(
            _records(
                (0, "run", {"cmd": "pytest tests/test_missing.py -v"}, err),
                (1, "run", {"cmd": "pytest -v tests/test_missing.py"}, err),
                (2, "run", {"cmd": "pytest  tests/test_missing.py  -v"}, err),
            )
        )
        assert detection.detected is True
        assert detection.reason

    def test_loop_survives_a_single_unrelated_call_in_the_window(self):
        content = "line one\nline two\n"
        detection = detect_semantic_loop(
            _records(
                (0, "read", {"path": "a/b.py"}, content),
                (1, "read", {"path": "./a/b.py"}, content),
                (2, "list", {"dir": "a"}, "b.py\nc.py\n"),
                (3, "read", {"path": "a//b.py"}, content),
                (4, "read", {"path": "A/B.PY"}, content),
            )
        )
        assert detection.detected is True
        assert detection.cluster_size == 4


# ---------------------------------------------------------------------------
# Detector — the false-positive guards
# ---------------------------------------------------------------------------


class TestDetectorSparesLegitimateWork:
    def test_iterative_test_fixing_is_not_a_loop(self):
        """Same command each turn, but the output changes — that is progress."""
        detection = detect_semantic_loop(
            _records(
                (0, "run", {"cmd": "pytest tests/test_a.py"}, "5 failed, 1 passed"),
                (1, "run", {"cmd": "pytest tests/test_a.py"}, "3 failed, 3 passed"),
                (2, "run", {"cmd": "pytest tests/test_a.py"}, "1 failed, 5 passed"),
                (3, "run", {"cmd": "pytest tests/test_a.py"}, "6 passed"),
            )
        )
        assert detection.detected is False

    def test_reading_different_files_is_not_a_loop(self):
        detection = detect_semantic_loop(
            _records(
                (0, "read", {"path": "mod1.py"}, "import os\n"),
                (1, "read", {"path": "mod2.py"}, "class Widget:\n    pass\n"),
                (2, "read", {"path": "mod3.py"}, "DEFAULTS = {'a': 1}\n"),
                (3, "read", {"path": "mod4.py"}, "def run():\n    return 42\n"),
            )
        )
        assert detection.detected is False

    def test_parallel_fan_out_in_one_turn_is_not_a_loop(self):
        """Four equivalent reads dispatched in a single turn is one action, not four."""
        content = "same content\n"
        detection = detect_semantic_loop(
            _records(
                (0, "read", {"path": "pkg/x.py"}, content),
                (0, "read", {"path": "./pkg/x.py"}, content),
                (0, "read", {"path": "pkg\\x.py"}, content),
                (0, "read", {"path": "PKG/X.PY"}, content),
            )
        )
        assert detection.detected is False
        assert detection.cluster_size == 4  # clustered, but all in one turn

    def test_repeat_interleaved_with_real_work_is_not_a_loop(self):
        """Re-listing a directory between genuine edits stays under coverage_ratio."""
        listing = "a.py\nb.py\n"
        detection = detect_semantic_loop(
            _records(
                (0, "list", {"dir": "src"}, listing),
                (1, "edit", {"path": "src/a.py", "text": "one"}, "written 1 line"),
                (2, "list", {"dir": "./src"}, listing),
                (3, "edit", {"path": "src/b.py", "text": "two"}, "written 7 lines"),
                (4, "list", {"dir": "src/"}, listing),
                (5, "edit", {"path": "src/c.py", "text": "three"}, "written 3 lines"),
            )
        )
        assert detection.detected is False

    def test_byte_identical_repeats_are_left_to_the_duplicate_guard(self):
        """min_distinct_variants=2: verbatim repetition is control 3's job, not this one."""
        detection = detect_semantic_loop(
            _records(
                (0, "read", {"path": "same.py"}, "content"),
                (1, "read", {"path": "same.py"}, "content"),
                (2, "read", {"path": "same.py"}, "content"),
                (3, "read", {"path": "same.py"}, "content"),
            )
        )
        assert detection.detected is False
        assert detection.distinct_variants == 1

    def test_repeated_edits_to_one_file_are_not_a_loop(self):
        """The real-transcript false positive, end to end.

        Five edits to the same file: identical `file_path`, changing content, and
        a tool result that is near-constant by design ("...updated successfully").
        Argument similarity carried by the shared path is what flagged this before
        per-key comparison landed.
        """
        path = "C:/AI/ICDev/tools/dashboard/templates/observability/page.html"
        detection = detect_semantic_loop(
            [
                ToolCallRecord(
                    turn=i,
                    name="Edit",
                    arguments={"file_path": path, "old_string": old, "new_string": new},
                    result=f"The file {path} has been updated successfully.",
                )
                for i, (old, new) in enumerate(
                    [
                        ("<h1>Dashboard</h1>", "<h1>Observability</h1>"),
                        ("{{ rows|length }}", "{{ rows|length }} of {{ total }}"),
                        ("def compute(a, b):", "def compute(a, b, c):"),
                        ("return None", "return summarise(rows)"),
                        ("# TODO", "# handled in obx-slo-02"),
                    ]
                )
            ]
        )
        assert detection.detected is False

    def test_below_minimum_history_never_fires(self):
        detection = detect_semantic_loop(
            _records(
                (0, "read", {"path": "a.py"}, "x"),
                (1, "read", {"path": "./a.py"}, "x"),
            )
        )
        assert detection.detected is False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestDetectorConfig:
    def test_config_loads_from_llm_config_yaml(self):
        cfg = load_detector_config()
        assert set(DEFAULT_CONFIG).issubset(cfg)
        assert 0.0 < cfg["similarity_threshold"] <= 1.0
        assert cfg["min_cluster_size"] >= 2

    def test_overrides_apply(self):
        records = _records(
            (0, "read", {"path": "a.py"}, "x"),
            (1, "read", {"path": "./a.py"}, "x"),
            (2, "read", {"path": "a.py "}, "x"),
        )
        assert detect_semantic_loop(records).detected is True
        # Demanding four distinct turns for the same three calls suppresses it.
        assert detect_semantic_loop(records, config={"min_distinct_turns": 4}).detected is False


# ---------------------------------------------------------------------------
# End-to-end through run_agent_loop — the acceptance criteria
# ---------------------------------------------------------------------------


_LOOP_SPELLINGS = [
    "tools/foo.py",
    "./tools/foo.py",
    "tools\\foo.py",
    "TOOLS/foo.py",
    "tools//foo.py",
    "./TOOLS/FOO.PY",
    "tools/./foo.py",
    "Tools/Foo.py",
]


class TestAgentLoopIntegration:
    def test_synthetic_loop_caught_before_the_token_ceiling(self):
        """Equivalent-but-not-identical calls end the run as a loop, not as budget exhaustion."""
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": path}}]
            for i, path in enumerate(_LOOP_SPELLINGS)
        ]
        router = ScriptedRouter(calls + ["done"], input_tokens=2000, output_tokens=500)

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("read")],
            tool_handlers={"read": lambda inp, stop: "def foo():\n    return 1\n"},
            max_iterations=len(_LOOP_SPELLINGS) + 1,
            max_total_tokens=100_000,
            memory_enabled=False,
        )

        assert result.result_subtype == ResultSubtype.error_semantic_loop
        assert result.truncation_reason == "semantic_loop"
        assert result.truncated is True
        # Distinguishable from genuine budget exhaustion — both the reason and
        # the numbers say so: the run stopped far short of the ceiling.
        assert result.truncation_reason != "max_total_tokens"
        assert result.total_input_tokens + result.total_output_tokens < 100_000 * 0.25
        assert result.turns < len(_LOOP_SPELLINGS)
        assert result.loop_detection["detected"] is True
        assert result.loop_detection["tool_name"] == "read"

    def test_stall_guard_does_not_fire_first(self):
        """Each call is novel by exact match, so stall_threshold is not what catches this."""
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": path}}]
            for i, path in enumerate(_LOOP_SPELLINGS)
        ]
        router = ScriptedRouter(calls + ["done"])
        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("read")],
            tool_handlers={"read": lambda inp, stop: "identical output"},
            max_iterations=len(_LOOP_SPELLINGS) + 1,
            stall_threshold=3,
            memory_enabled=False,
        )
        assert result.result_subtype == ResultSubtype.error_semantic_loop

    def test_legitimate_iterative_task_of_similar_length_completes(self):
        """Same shape and length as the loop above — edits with real results — runs to done."""
        targets = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
        calls = [
            [{"id": f"c{i}", "name": "edit", "input": {"path": f"src/{name}.py", "body": name}}]
            for i, name in enumerate(targets)
        ]
        router = ScriptedRouter(calls + ["all modules updated"], input_tokens=2000, output_tokens=500)

        outputs = iter(
            [
                "wrote 12 lines to src/alpha.py",
                "wrote 48 lines to src/beta.py; 2 imports added",
                "wrote 7 lines to src/gamma.py",
                "wrote 91 lines to src/delta.py; reformatted",
                "wrote 3 lines to src/epsilon.py",
                "wrote 64 lines to src/zeta.py; 1 conflict resolved",
                "wrote 25 lines to src/eta.py",
                "wrote 5 lines to src/theta.py",
            ]
        )

        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("edit")],
            tool_handlers={"edit": lambda inp, stop: next(outputs)},
            max_iterations=len(targets) + 1,
            max_total_tokens=100_000,
            stall_threshold=100,
            memory_enabled=False,
        )

        assert result.result_subtype == ResultSubtype.success
        assert result.truncation_reason == "completed"
        assert result.final_content == "all modules updated"
        assert result.loop_detection == {}

    def test_detection_reaches_the_harness_decision_row(self, monkeypatch):
        """The loop/budget distinction has to survive into telemetry, not just the return value."""
        import icdev.tools.llm.agent_loop as _al

        captured: list[dict[str, Any]] = []
        monkeypatch.setattr(
            _al,
            "_record_codegen_decision",
            lambda **kw: captured.append(kw),
        )

        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": path}}]
            for i, path in enumerate(_LOOP_SPELLINGS)
        ]
        router = ScriptedRouter(calls + ["done"])
        run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("read")],
            tool_handlers={"read": lambda inp, stop: "identical output"},
            max_iterations=len(_LOOP_SPELLINGS) + 1,
            memory_enabled=False,
        )

        assert len(captured) == 1
        recorded = captured[0]["result"]
        assert recorded.result_subtype == ResultSubtype.error_semantic_loop
        assert recorded.truncation_reason == "semantic_loop"
        assert recorded.loop_detection["detected"] is True

    def test_detection_can_be_disabled_per_call(self):
        calls = [
            [{"id": f"c{i}", "name": "read", "input": {"path": path}}]
            for i, path in enumerate(_LOOP_SPELLINGS)
        ]
        router = ScriptedRouter(calls + ["done"])
        result = run_agent_loop(
            router,
            system_prompt="s",
            user_prompt="u",
            tools=[_tool("read")],
            tool_handlers={"read": lambda inp, stop: "identical output"},
            max_iterations=len(_LOOP_SPELLINGS) + 1,
            stall_threshold=100,
            loop_detection={"enabled": False},
            memory_enabled=False,
        )
        assert result.result_subtype == ResultSubtype.success
        assert result.done is True
