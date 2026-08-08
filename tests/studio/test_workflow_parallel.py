"""Wave-parallel DAG dispatch in the Studio workflow runner (hgx-par-01).

`_worker` used to walk a FLATTENED topological order one step at a time, so a
template authored as a fan-out ran serially. It now walks a prepared
`graphlib.TopologicalSorter` with `get_ready()`/`done()` inside a bounded
ThreadPoolExecutor — decisions D40 (graphlib) and D36 (threads, not asyncio).

The load-bearing guarantee is the DEFAULT: `max_parallel` unset means 1, and at
1 the executed order must be byte-for-byte what `_resolve_dag` (the old
`static_order()` walk) produced. `test_every_shipped_template_*` asserts that
across every template in the tree, which is the before/after diff.

Every DB write and the gate wait are stubbed: these tests exercise dispatch
order and concurrency, not persistence, so they must not need a seeded database.
"""
# CUI // SP-CTI

from __future__ import annotations

import importlib
import queue
import threading
import time
from pathlib import Path

import pytest
import yaml

# The root `tools.` namespace is a shim over `icdev.tools.`, so the module OBJECT
# an import binds is not necessarily the one a string-form patch would reach.
# Resolve it once and patch attributes on that object, so the stubs land on the
# same globals `_worker` reads.
runner = importlib.import_module("tools.studio.workflow_runner")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIRS = (
    _REPO_ROOT / "args" / "workflow_templates",
    _REPO_ROOT / "context" / "workflow_templates",
)


# ── Harness ────────────────────────────────────────────────

class _Recorder:
    """Records the order and wall-clock span of every step the runner executes.

    Timed with ``time.perf_counter``, not ``time.monotonic``. Both are
    monotonic, but on Windows ``monotonic`` is GetTickCount64 — a ~15.6 ms tick
    — while ``perf_counter`` is QueryPerformanceCounter, sub-microsecond. Two
    steps that genuinely start milliseconds apart therefore get the IDENTICAL
    monotonic timestamp on Windows, and a `start_b > start_a` assertion fails on
    a run where nothing was wrong. On Linux the same clock has ns resolution, so
    the flake is invisible there. Caught by the windows-latest CI tier
    (hgx-port-02) on its first run: `assert (9775.625 > 9775.625 or False)`.
    """

    def __init__(self) -> None:
        self.order: list[str] = []
        self.spans: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def exec_step(self, step: dict, project_id: str, run_id: str = "") -> dict:
        step_id = step["id"]
        with self._lock:
            self.order.append(step_id)
        start = time.perf_counter()
        time.sleep(float(step.get("_test_sleep", 0) or 0))
        end = time.perf_counter()
        with self._lock:
            self.spans[step_id] = (start, end)
        node_type = step.get("node_type", "tool")
        return {
            "step_id": step_id,
            "step_name": step.get("name", step_id),
            "tool": step.get("tool", ""),
            "status": "awaiting_approval" if node_type in ("human", "approval") else "success",
            "stdout": None,
            "stderr": None,
            "exit_code": 0,
            "duration_ms": int((end - start) * 1000),
        }

    def overlaps(self, a: str, b: str) -> bool:
        a_start, a_end = self.spans[a]
        b_start, b_end = self.spans[b]
        return a_start < b_end and b_start < a_end


def _stub_persistence(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(runner, "_update_run_status", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_update_step_record", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_remember_canvas", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_remember_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_load_prior_steps", lambda run_id: {})
    monkeypatch.setattr(runner, "_notify_approval_gate", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "_create_step_record",
        lambda run_id, step_id, *a, **k: f"sr-{step_id}",
    )
    monkeypatch.setattr(runner, "_exec_step", recorder.exec_step)


def _drain(run_queue: "queue.Queue") -> list[dict]:
    events = []
    while True:
        try:
            events.append(run_queue.get_nowait())
        except queue.Empty:
            return events


def _run(
    monkeypatch: pytest.MonkeyPatch,
    template_yaml: str,
    *,
    gate_decision=("approved", "ok"),
    run_id: str = "run-test",
) -> tuple[_Recorder, list[dict]]:
    """Execute a template through the real `_worker`, with persistence stubbed."""
    recorder = _Recorder()
    _stub_persistence(monkeypatch, recorder)
    if gate_decision is not None:
        monkeypatch.setattr(runner, "_await_gate", lambda *a, **k: gate_decision)
    run_queue: queue.Queue = queue.Queue(maxsize=500)
    runner._worker(
        run_id, "wf-test", {"template_yaml": template_yaml, "name": "test"},
        "default", run_queue,
    )
    return recorder, _drain(run_queue)


# ── max_parallel parsing ───────────────────────────────────

@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ({}, 1),                            # unset -> sequential, the default
        ({"max_parallel": 1}, 1),
        ({"max_parallel": 4}, 4),
        ({"max_parallel": "3"}, 3),
        ({"max_parallel": 0}, 1),           # nonsense degrades, never raises
        ({"max_parallel": -7}, 1),
        ({"max_parallel": None}, 1),
        ({"max_parallel": "banana"}, 1),
        ({"max_parallel": 500}, runner._MAX_PARALLEL_CAP),
    ],
)
def test_max_parallel_parsing(declared, expected):
    assert runner._max_parallel(declared) == expected


def test_no_shipped_template_declares_max_parallel():
    """Every shipped template must stay sequential until one opts in."""
    declaring = []
    for directory in _TEMPLATE_DIRS:
        for path in sorted(directory.glob("*.yaml")):
            with open(path, encoding="utf-8", newline="") as fh:
                data = yaml.safe_load(fh) or {}
            if isinstance(data, dict) and "max_parallel" in data:
                declaring.append(path.name)
    assert declaring == []


# ── The before/after order diff ────────────────────────────

def _shipped_templates() -> list[tuple[str, list]]:
    found = []
    for directory in _TEMPLATE_DIRS:
        for path in sorted(directory.glob("*.yaml")):
            with open(path, encoding="utf-8", newline="") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                continue
            steps = data.get("steps") or []
            if steps and all(isinstance(s, dict) and s.get("id") for s in steps):
                found.append((f"{directory.name}/{path.name}", steps))
    return found


def test_shipped_template_corpus_is_present():
    """A silently empty corpus would make the order test vacuously pass."""
    assert len(_shipped_templates()) >= 55


@pytest.mark.parametrize(
    ("name", "steps"), _shipped_templates(), ids=[n for n, _ in _shipped_templates()],
)
def test_every_shipped_template_runs_in_static_order(monkeypatch, name, steps):
    """With max_parallel unset, dispatch order == the old `static_order()` walk."""
    expected = [sid for sid in runner._resolve_dag(steps) if any(s["id"] == sid for s in steps)]
    recorder, _events = _run(monkeypatch, yaml.safe_dump({"steps": steps}, sort_keys=False))
    assert recorder.order == expected, f"{name}: dispatch order changed"


# ── Fan-out ────────────────────────────────────────────────

_DIAMOND = """
max_parallel: 3
steps:
  - id: root
    name: Root
    tool: tools/x.py
  - id: coa_a
    tool: tools/x.py
    depends_on: [root]
    _test_sleep: 0.4
  - id: coa_b
    tool: tools/x.py
    depends_on: [root]
    _test_sleep: 0.4
  - id: coa_c
    tool: tools/x.py
    depends_on: [root]
    _test_sleep: 0.4
  - id: join
    tool: tools/x.py
    depends_on: [coa_a, coa_b, coa_c]
"""


def test_diamond_branches_overlap_and_join_waits(monkeypatch):
    recorder, events = _run(monkeypatch, _DIAMOND)

    assert set(recorder.order) == {"root", "coa_a", "coa_b", "coa_c", "join"}
    assert recorder.order[0] == "root"
    assert recorder.order[-1] == "join"

    # The three independent branches overlap in time — pairwise, so a merely
    # faster-but-still-serial walk cannot pass this.
    assert recorder.overlaps("coa_a", "coa_b")
    assert recorder.overlaps("coa_b", "coa_c")
    assert recorder.overlaps("coa_a", "coa_c")

    # The join is a plain step with three depends_on entries — no barrier
    # primitive — and graphlib holds it until all three are done.
    join_start = recorder.spans["join"][0]
    for branch in ("coa_a", "coa_b", "coa_c"):
        assert recorder.spans[branch][1] <= join_start

    assert [e for e in events if e["type"] == "run_complete"][0]["status"] == "success"


def test_diamond_is_serial_when_max_parallel_is_absent(monkeypatch):
    """Same template minus the max_parallel key: no overlap at all."""
    data = yaml.safe_load(_DIAMOND)
    data.pop("max_parallel")
    recorder, _events = _run(monkeypatch, yaml.safe_dump(data, sort_keys=False))

    assert recorder.order == runner._resolve_dag(data["steps"])
    assert not recorder.overlaps("coa_a", "coa_b")
    assert not recorder.overlaps("coa_b", "coa_c")


def test_run_started_announces_the_concurrency(monkeypatch):
    _recorder, events = _run(monkeypatch, _DIAMOND)
    started = [e for e in events if e["type"] == "run_started"][0]
    assert started["max_parallel"] == 3
    assert started["total_steps"] == 5


# ── Human gate inside a wave ───────────────────────────────

_GATE_IN_WAVE = """
max_parallel: 3
steps:
  - id: root
    tool: tools/x.py
  - id: gate
    node_type: human
    role: approver
    depends_on: [root]
  - id: sib_one
    tool: tools/x.py
    depends_on: [root]
  - id: sib_two
    tool: tools/x.py
    depends_on: [root]
  - id: join
    tool: tools/x.py
    depends_on: [gate, sib_one, sib_two]
"""


def test_human_gate_parks_only_its_own_branch(monkeypatch):
    """A parked gate must hold its own thread, not the pool.

    The fake gate refuses to resolve until BOTH siblings have finished. If
    parking blocked the pool, the siblings could never run, the wait would time
    out, and the run would come back failed — so this test cannot pass by
    accident.
    """
    recorder = _Recorder()
    _stub_persistence(monkeypatch, recorder)

    siblings_done = threading.Event()
    real_exec = recorder.exec_step

    def exec_and_signal(step, project_id, run_id=""):
        result = real_exec(step, project_id, run_id)
        if {"sib_one", "sib_two"}.issubset(set(recorder.spans)):
            siblings_done.set()
        return result

    monkeypatch.setattr(runner, "_exec_step", exec_and_signal)
    monkeypatch.setattr(
        runner, "_await_gate",
        lambda *a, **k: ("approved", "ok") if siblings_done.wait(timeout=30) else (None, ""),
    )

    run_queue: queue.Queue = queue.Queue(maxsize=500)
    runner._worker("run-gate", "wf-test", {"template_yaml": _GATE_IN_WAVE, "name": "t"},
                   "default", run_queue)
    events = _drain(run_queue)

    assert siblings_done.is_set(), "siblings never ran — the gate blocked the pool"
    gate_start = recorder.spans["gate"][0]
    for sibling in ("sib_one", "sib_two"):
        assert recorder.spans[sibling][0] > gate_start or recorder.overlaps(sibling, "gate")
    assert recorder.order[-1] == "join", "the join must still wait for the approved gate"
    assert [e for e in events if e["type"] == "run_complete"][0]["status"] == "success"
    assert any(e["type"] == "step_awaiting_approval" and e["step_id"] == "gate" for e in events)


def test_rejected_gate_stops_the_rest_of_the_run(monkeypatch):
    """A rejected gate leaves later steps unexecuted, exactly as the old break did."""
    recorder, events = _run(
        monkeypatch,
        "steps:\n"
        "  - {id: first, tool: tools/x.py}\n"
        "  - {id: gate, node_type: approval, depends_on: [first]}\n"
        "  - {id: after, tool: tools/x.py, depends_on: [gate]}\n"
        "  - {id: unrelated, tool: tools/x.py, depends_on: [first]}\n",
        gate_decision=("rejected", "no"),
    )
    assert "after" not in recorder.order
    assert "unrelated" not in recorder.order
    assert recorder.order == ["first", "gate"]
    assert [e for e in events if e["type"] == "run_complete"][0]["status"] == "failed"


# ── Graph edge cases ───────────────────────────────────────

def test_dangling_depends_on_is_retired_not_executed(monkeypatch):
    """A depends_on naming no step must not stall the walk (or run anything)."""
    recorder, events = _run(
        monkeypatch,
        "max_parallel: 2\n"
        "steps:\n"
        "  - {id: a, tool: tools/x.py, depends_on: [ghost]}\n"
        "  - {id: b, tool: tools/x.py, depends_on: [a]}\n",
    )
    assert recorder.order == ["a", "b"]
    assert [e for e in events if e["type"] == "run_complete"][0]["status"] == "success"


def test_cycle_fails_the_run_before_any_step(monkeypatch):
    recorder, events = _run(
        monkeypatch,
        "steps:\n"
        "  - {id: a, tool: tools/x.py, depends_on: [b]}\n"
        "  - {id: b, tool: tools/x.py, depends_on: [a]}\n",
    )
    assert recorder.order == []
    assert [e for e in events if e["type"] == "error"][0]["message"].startswith(
        "Circular dependency detected"
    )


def test_failed_step_does_not_block_its_wave(monkeypatch):
    """A failing tool step marks the run failed but still runs the others.

    This is the pre-existing contract (only a gate short-circuits the walk); the
    parallel loop must not quietly start cancelling downstream work.
    """
    recorder = _Recorder()
    _stub_persistence(monkeypatch, recorder)
    real_exec = recorder.exec_step

    def failing(step, project_id, run_id=""):
        result = real_exec(step, project_id, run_id)
        if step["id"] == "boom":
            result["status"] = "failed"
            result["exit_code"] = 1
        return result

    monkeypatch.setattr(runner, "_exec_step", failing)
    run_queue: queue.Queue = queue.Queue(maxsize=500)
    runner._worker(
        "run-fail", "wf-test",
        {"template_yaml": "max_parallel: 2\nsteps:\n"
                          "  - {id: boom, tool: tools/x.py}\n"
                          "  - {id: other, tool: tools/x.py}\n"
                          "  - {id: last, tool: tools/x.py, depends_on: [boom, other]}\n",
         "name": "t"},
        "default", run_queue,
    )
    events = _drain(run_queue)
    assert set(recorder.order) == {"boom", "other", "last"}
    assert [e for e in events if e["type"] == "run_complete"][0]["status"] == "failed"


# ── SSE sequencing ─────────────────────────────────────────

def test_step_events_carry_a_monotonic_sequence_not_a_list_position(monkeypatch):
    """Emission is unordered under fan-out, so `index` is replaced by `seq`."""
    _recorder, events = _run(monkeypatch, _DIAMOND)
    step_events = [e for e in events if e["type"] in ("step_started", "step_done")]

    assert step_events, "no step events emitted"
    assert all("index" not in e for e in step_events)

    seqs = [e["seq"] for e in step_events]
    assert sorted(seqs) == list(range(len(seqs))), "sequence numbers must be unique and dense"

    # Arrival order is deliberately NOT asserted: two threads that allocate seq
    # 5 and 6 may reach the queue in either order. What the number guarantees is
    # a total order over dispatch — in particular a step's start precedes its own
    # completion, which a list position could not express once waves overlap.
    for step_id in ("root", "coa_a", "coa_b", "coa_c", "join"):
        by_type = {e["type"]: e["seq"] for e in step_events if e["step_id"] == step_id}
        assert by_type["step_started"] < by_type["step_done"]

    assert all(e["total"] == 5 for e in step_events)
