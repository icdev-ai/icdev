# CUI // SP-CTI
"""CLX — Control Loop Discipline.

Three pieces a measured loop needs and ICDEV lacked: a sensor that reports
structured violations without repairing them, flow control that stops the loop
outrunning its own review, and durable context so a correction survives the run
it was made in.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.kanban import backpressure
from tools.quality.sensor import ReviewLoopSensor, Violation, _looks_like_path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Sensor (clx-sense-01)
# ---------------------------------------------------------------------------

class _StubFinding:
    def __init__(self, gate, file, message, code="", line=0, fixable=False):
        self.gate, self.file, self.message = gate, file, message
        self.code, self.line, self.fixable = code, line, fixable


class _StubGate:
    def __init__(self, name, blocking, findings, skipped=False):
        self.name, self.blocking, self.findings, self.skipped = (
            name, blocking, findings, skipped,
        )


class _StubIteration:
    def __init__(self, gates):
        self.gates = gates


class _StubReport:
    def __init__(self, iterations):
        self.iterations = iterations


def _sensor_with(report, monkeypatch):
    import tools.quality.review_loop as rl

    class _Loop:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            _Loop.last = kwargs

        def run(self, base=None):
            return report

    monkeypatch.setattr(rl, "ReviewLoop", _Loop)
    return _Loop


def test_sensor_never_autofixes(monkeypatch):
    """A sensor that changes what it measures cannot inform a controller."""
    loop_cls = _sensor_with(_StubReport([]), monkeypatch)
    ReviewLoopSensor().measure()
    assert loop_cls.last["autofix"] is False
    assert loop_cls.last["config"]["max_iterations"] == 1


def test_sensor_maps_findings_to_violations(monkeypatch):
    report = _StubReport([
        _StubIteration([
            _StubGate("ruff", True, [
                _StubFinding("ruff", "tools/x.py", "unused import", code="F401",
                             line=12, fixable=True),
            ]),
        ]),
    ])
    _sensor_with(report, monkeypatch)
    (v,) = ReviewLoopSensor().measure()
    assert v.file == "tools/x.py"
    assert v.line == 12
    assert v.rule == "F401"
    assert v.gate == "ruff"
    assert v.severity == "blocking"
    assert v.fixable is True
    assert v.file_scoped is True


def test_sensor_uses_the_findings_own_gate_label(monkeypatch):
    """GateResult.name is caller-supplied and not always the gate's id.

    gate_coherence used to leak its loop variable into that field, labelling the
    result with whichever check happened to be last.
    """
    report = _StubReport([
        _StubIteration([
            _StubGate("Canvas Connection Placeholder Style", True, [
                _StubFinding("coherence", "Schema-Code Coherence", "2 mismatches",
                             code="schema_code"),
            ]),
        ]),
    ])
    _sensor_with(report, monkeypatch)
    (v,) = ReviewLoopSensor().measure()
    assert v.gate == "coherence"


def test_repo_level_findings_are_marked_not_file_scoped(monkeypatch):
    report = _StubReport([
        _StubIteration([
            _StubGate("coherence", True, [
                _StubFinding("coherence", "Log Standard Compliance", "16 tools",
                             code="log_standard"),
            ]),
        ]),
    ])
    _sensor_with(report, monkeypatch)
    (v,) = ReviewLoopSensor().measure()
    assert v.file_scoped is False


def test_skipped_gates_contribute_nothing(monkeypatch):
    report = _StubReport([
        _StubIteration([
            _StubGate("sipa", True, [_StubFinding("sipa", "a.py", "x")], skipped=True),
        ]),
    ])
    _sensor_with(report, monkeypatch)
    assert ReviewLoopSensor().measure() == []


def test_violations_rank_blocking_and_located_first():
    advisory = Violation("z.py", 1, "R", "m", "g", severity="advisory")
    blocking_repo = Violation("A Check", 0, "R", "m", "g", severity="blocking",
                              file_scoped=False)
    blocking_file = Violation("a.py", 5, "R", "m", "g", severity="blocking")
    ordered = sorted([advisory, blocking_repo, blocking_file], key=lambda v: v.rank)
    assert ordered == [blocking_file, blocking_repo, advisory]


@pytest.mark.parametrize(
    "value,expected",
    [("tools/x.py", True), ("a\\b.py", True), ("notes.md", True),
     ("Schema-Code Coherence", False), ("", False)],
)
def test_path_detection(value, expected):
    assert _looks_like_path(value) is expected


def test_coherence_gate_result_is_named_for_the_gate():
    """Regression guard for the shadowing bug the sensor exposed."""
    import inspect

    import tools.quality.review_loop as rl

    src = inspect.getsource(rl.gate_coherence)
    assert "check_name = (" in src, "the per-check name must not rebind `name`"


# ---------------------------------------------------------------------------
# Backpressure (clx-flow-01)
# ---------------------------------------------------------------------------

def test_backpressure_is_off_unless_enabled(monkeypatch):
    """Throttling autonomous throughput is an operator's decision."""
    monkeypatch.delenv(backpressure.ENV_ENABLED, raising=False)
    assert backpressure.is_enabled() is False
    # Must be a pure passthrough when disabled — safe to add at the call site.
    monkeypatch.setattr(backpressure, "count_unreviewed", lambda **_: 99)
    assert backpressure.apply_backpressure(3) == 3


def test_backpressure_holds_dispatch_at_the_ceiling(monkeypatch):
    monkeypatch.setenv(backpressure.ENV_ENABLED, "1")
    monkeypatch.setenv(backpressure.ENV_MAX, "3")
    monkeypatch.setattr(backpressure, "count_unreviewed", lambda **_: 3)
    assert backpressure.apply_backpressure(3) == 0


def test_backpressure_narrows_dispatch_to_headroom(monkeypatch):
    monkeypatch.setenv(backpressure.ENV_ENABLED, "1")
    monkeypatch.setenv(backpressure.ENV_MAX, "3")
    monkeypatch.setattr(backpressure, "count_unreviewed", lambda **_: 2)
    assert backpressure.apply_backpressure(3) == 1


def test_backpressure_allows_full_dispatch_when_nothing_is_pending(monkeypatch):
    monkeypatch.setenv(backpressure.ENV_ENABLED, "1")
    monkeypatch.setattr(backpressure, "count_unreviewed", lambda **_: 0)
    assert backpressure.apply_backpressure(3) == 3


def test_unreviewed_statuses_cover_the_awaiting_merge_lifecycle():
    """pr_opened is the whole point — it is what MAX_IN_PROGRESS misses."""
    assert "pr_opened" in backpressure.UNREVIEWED_STATUSES
    for state in ("ci_failed", "changes_requested", "merge_conflict"):
        assert state in backpressure.UNREVIEWED_STATUSES
    # in_progress is already capped by MAX_IN_PROGRESS; double-counting it would
    # halve the effective concurrency.
    assert "in_progress" not in backpressure.UNREVIEWED_STATUSES


def test_counting_failure_fails_open(monkeypatch):
    """Flow control must never be able to wedge the dispatcher."""
    import importlib

    def _boom(*_a, **_kw):
        raise RuntimeError("database unreachable")

    # `tools.*` re-exports `icdev.tools.*`; a string-form target resolves through
    # the shim and binds a different module object than the one under test.
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", _boom)
    assert backpressure.count_unreviewed() == 0


def test_bad_ceiling_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(backpressure.ENV_MAX, "not-a-number")
    assert backpressure.max_unreviewed() == backpressure.DEFAULT_MAX_UNREVIEWED


def test_status_reports_the_flow_picture(monkeypatch):
    monkeypatch.setenv(backpressure.ENV_ENABLED, "1")
    monkeypatch.setenv(backpressure.ENV_MAX, "2")
    monkeypatch.setattr(backpressure, "count_unreviewed", lambda **_: 2)
    s = backpressure.status()
    assert s["holding"] is True
    assert s["headroom"] == 0
    assert s["enabled"] is True


def test_dispatcher_calls_backpressure():
    """The gate is wired into the slot calculation, not just available."""
    src = (REPO_ROOT / "tools" / "genesis" / "reflexes" / "kanban.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "from tools.kanban.backpressure import apply_backpressure" in src
    assert "available_slots = apply_backpressure(available_slots)" in src


# ---------------------------------------------------------------------------
# Loop context: feedback + golden patterns (clx-fb-01, clx-gold-01)
# ---------------------------------------------------------------------------

def test_feedback_round_trips(tmp_path, monkeypatch):
    from tools.agent_runtime import loop_context as lc

    monkeypatch.setattr(lc, "FEEDBACK_DIR", tmp_path)
    assert lc.append_feedback("demo", "always check X") is True
    text = lc.load_feedback("demo")
    assert "always check X" in text
    # Append-only: a second correction must not replace the first.
    lc.append_feedback("demo", "and also Y")
    text = lc.load_feedback("demo")
    assert "always check X" in text and "and also Y" in text


def test_missing_feedback_is_empty_not_an_error(tmp_path, monkeypatch):
    from tools.agent_runtime import loop_context as lc

    monkeypatch.setattr(lc, "FEEDBACK_DIR", tmp_path)
    assert lc.load_feedback("never-written") == ""


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".", "..", "x\\y"])
def test_identifiers_cannot_escape_the_directory(bad, tmp_path, monkeypatch):
    from tools.agent_runtime import loop_context as lc

    monkeypatch.setattr(lc, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(lc, "GOLDEN_DIR", tmp_path)
    assert lc.feedback_path(bad) is None
    assert lc.load_feedback(bad) == ""
    assert lc.load_pattern(bad) == ""
    assert lc.append_feedback(bad, "x") is False


def test_feedback_is_truncated_rather_than_crowding_out_the_task(tmp_path, monkeypatch):
    from tools.agent_runtime import loop_context as lc

    monkeypatch.setattr(lc, "FEEDBACK_DIR", tmp_path)
    (tmp_path / "big.md").write_text("x" * (lc.MAX_FEEDBACK_CHARS + 500), encoding="utf-8")
    out = lc.load_feedback("big")
    assert len(out) <= lc.MAX_FEEDBACK_CHARS + 20
    assert out.endswith("(truncated)")


def test_seeded_golden_patterns_are_present_and_paired():
    from tools.agent_runtime.loop_context import list_patterns, load_pattern

    names = list_patterns()
    assert "pg_placeholders" in names
    assert "canvas_db_connection" in names
    for name in ("pg_placeholders", "canvas_db_connection"):
        body = load_pattern(name)
        assert "before.py" in body and "after.py" in body, "a pattern needs both sides"
        assert "notes.md" in body


def test_build_loop_context_is_empty_when_there_is_nothing_to_say():
    from tools.agent_runtime.loop_context import build_loop_context

    assert build_loop_context(loop_id="", patterns=[]) == ""
    assert build_loop_context(loop_id="no-such-loop", patterns=["no-such-pattern"]) == ""


def test_build_loop_context_combines_feedback_and_patterns(tmp_path, monkeypatch):
    from tools.agent_runtime import loop_context as lc

    monkeypatch.setattr(lc, "FEEDBACK_DIR", tmp_path)
    lc.append_feedback("demo", "remember the mirror")
    block = lc.build_loop_context(loop_id="demo", patterns=["pg_placeholders"])
    assert "## Loop feedback" in block
    assert "remember the mirror" in block
    assert "## Golden pattern: pg_placeholders" in block


def test_golden_patterns_live_under_the_forge_context_layer():
    """context/ is FORGE's Context layer; .icdev/ would sit outside the framework."""
    assert (REPO_ROOT / "context" / "golden_patterns").is_dir()
    assert not (REPO_ROOT / ".icdev").exists()
