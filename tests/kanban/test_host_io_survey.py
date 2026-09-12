# CUI // SP-CTI
"""The host-load survey, and the decision that it must NOT become a gate.

This pins a NEGATIVE finding. `tools/kanban/host_io.py` exists so the next
person who reaches for "decline a dispatch when the host looks busy" gets the
answer in one command instead of rebuilding it -- the same shape and reason as
tools/kanban/landed_dispatch_survey.py, whose headline is "Surveyed; answer is
NO". Measured over 86 recorded adds on this host: the slow-add rule refuses 5
and prevents 0 parks, and the killed-add rule moves precision from a 15.12% base
rate to about 25% while refusing 8 good adds to catch 3. So the tests below
assert the tool reports honestly AND that nothing imports it as a gate.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.kanban import host_io

_TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _line(msg: str, when: datetime) -> str:
    return json.dumps({"ts": when.isoformat(), "level": "INFO",
                       "component": "tools.genesis.reflexes.kanban", "message": msg})


def _ok(task: str, secs: float, when: datetime, budget: int = 30) -> str:
    return _line(f"Created worktree for {task} at C:\\x\\{task} in {secs}s (budget {budget}s)", when)


def _killed(task: str, secs: float, when: datetime, budget: int = 30) -> str:
    return _line(
        f"git worktree add for {task} exceeded its {budget}s budget after {secs}s "
        f"and was KILLED with its checkout children (taskkill /T rc=0)", when)


def _log(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "kanban.ndjson"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ── absence is not emptiness ────────────────────────────────────────────────


def test_a_missing_log_is_unreadable_not_empty(tmp_path):
    """"We could not look" and "nothing happened" justify opposite decisions."""
    out = host_io.recent_adds(60, tmp_path / "nope.ndjson")
    assert out["readable"] is False
    assert out["adds"] == []
    assert "no add log" in out["reason"]


def test_survey_over_an_empty_log_is_unmeasurable_with_None_rates(tmp_path):
    """Never 0.0 over an empty denominator (args/perfect_score_gate.yaml is
    ratcheted to 0) -- a 0% fire rate would read as "measured and clean"."""
    out = host_io.survey(7.0, _log(tmp_path, [_line("nothing to do here", _now())]))
    assert out["state"] == "unmeasurable"
    assert out["fire_rate_pct"] is None
    assert out["would_wait"] is None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── parsing both shapes, and surviving a torn line ──────────────────────────


def test_both_log_shapes_parse_and_the_budget_comes_from_the_line(tmp_path):
    """The budget is read from the LINE, never imported: importing it from
    tools.genesis.reflexes.kanban would be circular and would drag 13,552 lines
    into a cheap predicate."""
    now = _now()
    p = _log(tmp_path, [
        _ok("a", 6.0, now - timedelta(minutes=5), budget=30),
        _killed("b", 41.7, now - timedelta(minutes=3), budget=30),
    ])
    adds = host_io.recent_adds(60, p)["adds"]
    assert len(adds) == 2
    by_task = {a["task_id"]: a for a in adds}
    assert by_task["a"]["seconds"] == 6.0 and by_task["a"]["killed"] is False
    assert by_task["a"]["slow"] is False
    assert by_task["b"]["killed"] is True and by_task["b"]["slow"] is True
    assert by_task["b"]["budget_seconds"] == 30


def test_a_torn_final_line_is_skipped_not_raised(tmp_path):
    """The log is appended to live, so a truncated last line is normal."""
    now = _now()
    p = tmp_path / "torn.ndjson"
    p.write_text(_ok("a", 6.0, now) + "\n" + '{"ts": "2026', encoding="utf-8")
    out = host_io.recent_adds(60, p)
    assert out["readable"] is True
    assert [a["task_id"] for a in out["adds"]] == ["a"]


def test_adds_outside_the_window_are_excluded(tmp_path):
    now = _now()
    p = _log(tmp_path, [_ok("old", 6.0, now - timedelta(hours=5)),
                        _ok("new", 7.0, now - timedelta(minutes=2))])
    assert [a["task_id"] for a in host_io.recent_adds(20, p)["adds"]] == ["new"]


# ── the verdicts ────────────────────────────────────────────────────────────


def test_a_quiet_log_is_unmeasurable_not_proceed(tmp_path):
    """No add in the window is no EVIDENCE, which is not the same claim as a
    quiet host."""
    p = _log(tmp_path, [_ok("a", 6.0, _now() - timedelta(hours=3))])
    out = host_io.assess(20, p)
    assert out["verdict"] == host_io.UNMEASURABLE


def test_a_recent_kill_says_wait_and_says_when_it_clears(tmp_path):
    p = _log(tmp_path, [_killed("b", 41.7, _now() - timedelta(seconds=5))])
    out = host_io.assess(20, p)
    assert out["verdict"] == host_io.WAIT
    assert out["resets_at"]
    assert out["basis"] == "recent_add_latency"


def test_fast_adds_proceed(tmp_path):
    p = _log(tmp_path, [_ok("a", 5.0, _now() - timedelta(minutes=2))])
    assert host_io.assess(20, p)["verdict"] == host_io.PROCEED


# ── the finding, pinned ─────────────────────────────────────────────────────


def test_the_survey_reports_both_hypotheses_and_refuses_to_arm(tmp_path):
    """The docstring quotes a table; the SHIPPED tool must re-derive it, or the
    published number and the code can drift apart."""
    now = _now()
    lines = [_ok(f"t{i}", 6.0, now - timedelta(minutes=90 - i)) for i in range(20)]
    lines.append(_killed("k", 40.0, now - timedelta(minutes=30)))
    out = host_io.survey(7.0, _log(tmp_path, lines))
    assert out["state"] == "measured"
    assert out["verdict"] == "do_not_arm"
    cooldowns = [r["cooldown_seconds"] for r in out["kill_predicts_kill"]]
    assert cooldowns == list(host_io.KILL_PREDICTS_COOLDOWNS)
    for row in out["kill_predicts_kill"]:
        # An unfired rule has NO precision; 0.0 would claim it fired and was wrong.
        assert row["precision_pct"] is None or 0.0 <= row["precision_pct"] <= 100.0
        if row["fires"] == 0:
            assert row["precision_pct"] is None


def test_a_refusal_is_split_into_right_and_wrong(tmp_path):
    """A fire rate alone cannot decide arming -- the landed_dispatch_survey rule.
    An add declined that WAS killed is a park prevented; one that succeeded is a
    cycle spent, and the two are never one number."""
    now = _now()
    out = host_io.survey(7.0, _log(tmp_path, [
        _killed("a", 40.0, now - timedelta(minutes=10)),
        _ok("b", 5.0, now - timedelta(minutes=10) + timedelta(seconds=20)),
    ]))
    assert out["declined_but_would_have_succeeded"] == 1
    assert out["declined_and_would_have_been_killed"] == 0


# ── it is a survey, not a gate, and that is the decision ────────────────────


def test_nothing_imports_host_io_as_a_dispatch_gate():
    """THE DECISION THIS FILE EXISTS TO PIN. The measurement says the signal does
    not predict, so wiring it into should_run would ship a check that refuses
    routine work to prevent almost nothing -- and a declared-but-inert capability
    is the defect this repo fights hardest. A behavioural test cannot see a
    future edit that wires it in, because the gate would still 'work'; only
    reading the source can."""
    for name in ("should_run.py", "backpressure.py", "dispatch_admission.py"):
        p = _TOOLS / "kanban" / name
        if not p.exists():
            continue
        assert "host_io" not in p.read_text(encoding="utf-8"), (
            f"{name} imports host_io. The survey says do_not_arm: 5 refusals, 0 parks "
            f"prevented; kill->kill precision ~25% against a 15.12% base rate. "
            f"Re-run `python -m tools.kanban.host_io --survey` before wiring it."
        )
    kanban = _TOOLS / "genesis" / "reflexes" / "kanban.py"
    assert "host_io" not in kanban.read_text(encoding="utf-8")


def test_the_module_takes_no_action_of_any_kind():
    """No task row is read or written. A survey that can act is not a survey."""
    src = (_TOOLS / "kanban" / "host_io.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"_move_task", "create_tasks", "execute", "commit"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            assert name not in banned, f"host_io calls {name}; it must only measure"
    for sql in ("UPDATE ", "INSERT ", "DELETE "):
        assert sql not in src.upper().replace("UPDATED", ""), f"host_io contains {sql}"
