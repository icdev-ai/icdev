# CUI // SP-CTI
"""hgx-exec-04: the executor parity benchmark — harness, corpus, and the no-flip guard.

Deterministic and offline. Every test here runs without an LLM, without the
network and without the kanban DB: the adapters are fakes, the git operations
run against a throwaway repo built in ``tmp_path``, and the grader is injected.
The one test that would spend real money is skipped unless
``ICDEV_PARITY_LIVE=1`` is set explicitly.

Two things are being pinned:

  1. The harness itself — corpus integrity, the changed-file computation the
     grade depends on, the score/aggregate math, and the report renderer.
  2. hgx-exec-04's EXPLICIT NON-GOAL — ``claude_cli`` stays first in the
     executor chain and ``KANBAN_RUBRIC_LOOP`` stays off by default. Those are
     acceptance criteria, so they are tests, not prose.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess  # nosec B404 — git only, fixed argv, shell=False
from pathlib import Path

import pytest
import yaml

ep = importlib.import_module("tools.workflow.executor_parity")

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _git(args, cwd, check=True):
    proc = subprocess.run(  # nosec B603 — fixed arg list, shell=False
        [shutil.which("git") or "git", *args],
        cwd=str(cwd),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr or proc.stdout}")
    return proc


def _write(path: Path, text: str) -> None:
    """utf-8 + newline='' — the same contract the harness holds itself to."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


@pytest.fixture
def tiny_repo(tmp_path):
    """A throwaway git repo with one commit, usable on Windows and POSIX."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    # A worktree-less repo may inherit no identity from the host (CI runners
    # have none), so commit identity is set locally rather than assumed.
    _git(["config", "user.email", "parity@example.invalid"], repo)
    _git(["config", "user.name", "parity"], repo)
    _write(repo / "kept.txt", "original\n")
    _write(repo / "edited.txt", "before\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    return repo, base


class _FakeAdapter:
    """Stands in for an AgentAdapter; records the session it was handed."""

    def __init__(self, name="fake", completed=True, available=True, structured=None,
                 on_invoke=None, error=""):
        self.name = name
        self._completed = completed
        self._available = available
        self._structured = structured or {}
        self._on_invoke = on_invoke
        self._error = error
        self.sessions = []

    def available(self):
        return self._available

    def prepare_prompt(self, session):
        return session.prompt

    def invoke(self, session):
        from tools.agents.adapter_base import AgentResult

        self.sessions.append(session)
        if self._on_invoke:
            self._on_invoke(session)
        return AgentResult(
            task_id=session.task_id,
            adapter_name=self.name,
            completed=self._completed,
            exit_code=0 if self._completed else 1,
            output="done",
            error=self._error,
            structured=dict(self._structured),
        )

    def detect_completion(self, output):
        return self._completed

    def parse_response(self, raw):
        return {"content": raw, "tool_calls": [], "diff": ""}


def _run(task_id="t1", executor="fake", **kw):
    """Build a ParityRun without touching git or the grader."""
    return ep.ParityRun(task_id=task_id, executor=executor, **kw)


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------


def test_corpus_loads_ten_self_contained_tasks():
    tasks = ep.load_corpus()
    assert len(tasks) >= 10, "the card asks for a fixed corpus of ~10 tasks"
    ids = [t.task_id for t in tasks]
    assert len(ids) == len(set(ids)), f"duplicate corpus ids: {ids}"
    for task in tasks:
        assert task.prompt.strip(), f"{task.task_id} has an empty prompt"
        assert task.acceptance, f"{task.task_id} declares no acceptance criteria"
        assert task.base_commit != task.reference_commit
        for sha in (task.base_commit, task.reference_commit):
            assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), sha


def test_corpus_prompt_never_leaks_the_reference_diff():
    """A prompt that names the patch measures reading comprehension, not building."""
    for task in ep.load_corpus():
        text = task.instruction()
        assert task.reference_commit not in text
        assert "diff --git" not in text
        assert "\n+++ " not in text


def test_corpus_base_commits_resolve_in_this_clone():
    if shutil.which("git") is None:  # pragma: no cover — git is present in CI
        pytest.skip("git unavailable")
    if _git(["rev-parse", "--is-inside-work-tree"], REPO_ROOT, check=False).returncode != 0:
        pytest.skip("not a git checkout")
    missing = [
        t.task_id for t in ep.load_corpus()
        if not ep.commit_exists(t.base_commit, REPO_ROOT)
    ]
    assert not missing, f"corpus base commits absent from this clone: {missing}"


def test_instruction_carries_task_and_criteria():
    task = ep.CorpusTask(
        task_id="x-1", title="T", prompt="do the thing",
        base_commit="a" * 40, reference_commit="b" * 40,
        acceptance=["first", "second"],
    )
    text = task.instruction()
    assert "x-1" in text and "do the thing" in text
    assert "- first" in text and "- second" in text


def test_corpus_path_resolves_from_file_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ep.DEFAULT_CORPUS_PATH.is_file()
    assert len(ep.load_corpus()) >= 10


# ---------------------------------------------------------------------------
# changed-file computation — the input the grade depends on
# ---------------------------------------------------------------------------


def test_changed_files_sees_uncommitted_and_untracked_work(tiny_repo):
    """The executors never commit, so a commit-range diff would report nothing.

    This is the whole reason the harness computes its own set: with an empty
    changed-file list every gate is scoped to nothing and passes vacuously.
    """
    repo, base = tiny_repo
    assert ep.changed_files_since(base, repo) == []

    _write(repo / "edited.txt", "after\n")
    _write(repo / "brand_new.py", "x = 1\n")

    files = ep.changed_files_since(base, repo)
    assert files == ["brand_new.py", "edited.txt"]

    # And the range form the production thunk uses reports nothing at all here.
    range_diff = _git(["diff", "--name-only", f"{base}...HEAD"], repo).stdout.strip()
    assert range_diff == ""


def test_changed_files_uses_forward_slashes(tiny_repo):
    repo, base = tiny_repo
    _write(repo / "pkg" / "mod.py", "y = 2\n")
    assert "pkg/mod.py" in ep.changed_files_since(base, repo)


def test_changed_files_never_raises_on_a_bad_ref(tiny_repo):
    repo, _base = tiny_repo
    assert ep.changed_files_since("nope-not-a-ref", repo) == []


# ---------------------------------------------------------------------------
# worktree lifecycle
# ---------------------------------------------------------------------------


def test_replay_worktree_path_is_sanctioned_and_disjoint():
    from tools.git.worktree_paths import is_sanctioned

    a = ep.replay_worktree_path("sbx-gov-03", "claude_cli")
    b = ep.replay_worktree_path("sbx-gov-03", "local_agent")
    assert a != b, "the two executors must not share a worktree"
    assert is_sanctioned(a) and is_sanctioned(b)
    # `verify`, not `kanban`/`cli` — a benchmark tree can never land where a live
    # task or an interactive session owns the path.
    assert f"{os.sep}verify{os.sep}" in str(a)


def test_remove_worktree_is_idempotent_on_a_missing_path(tmp_path):
    assert ep.remove_worktree(tmp_path / "never-existed") is True


# ---------------------------------------------------------------------------
# one replay
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_worktree(monkeypatch, tiny_repo):
    """Point create/remove at the tiny repo so run_one never touches real git."""
    repo, base = tiny_repo
    monkeypatch.setattr(ep, "create_replay_worktree", lambda task, executor: repo)
    removed = []
    monkeypatch.setattr(ep, "remove_worktree", lambda p, attempts=3: removed.append(p) or True)
    task = ep.CorpusTask(
        task_id="corp-1", title="T", prompt="do it",
        base_commit=base, reference_commit="b" * 40,
        reference_files=["edited.txt"], acceptance=["it is done"],
    )
    return task, repo, removed


def test_run_one_records_gate_verdict_cost_and_files(monkeypatch, stub_worktree):
    task, repo, removed = stub_worktree
    monkeypatch.setattr(
        ep, "grade_worktree",
        lambda *a, **k: (True, "satisfied", "All delivery-pipeline gates passed."),
    )
    adapter = _FakeAdapter(
        name="fake", completed=True,
        structured={"total_cost_usd": 0.42, "input_tokens": 100,
                    "output_tokens": 20, "turns": 5},
        on_invoke=lambda s: _write(repo / "edited.txt", "after\n"),
    )

    run = ep.run_one(task, "fake", adapter=adapter, timeout_seconds=60)

    assert run.status == "ok"
    assert run.graded_pass is True
    assert run.self_reported_complete is True
    assert run.cost_usd == 0.42 and run.turns == 5
    assert run.files == ["edited.txt"]
    assert run.reference_overlap == 1
    assert run.overclaimed is False
    assert removed, "the replay worktree must be destroyed"


def test_run_one_flags_an_executor_that_claims_done_but_fails_the_gates(
    monkeypatch, stub_worktree
):
    task, repo, _removed = stub_worktree
    monkeypatch.setattr(
        ep, "grade_worktree", lambda *a, **k: (False, "needs_revision", "ruff found 3 issues"),
    )
    adapter = _FakeAdapter(
        completed=True, on_invoke=lambda s: _write(repo / "edited.txt", "after\n")
    )

    run = ep.run_one(task, "fake", adapter=adapter, timeout_seconds=60)

    assert run.self_reported_complete is True
    assert run.graded_pass is False
    assert run.overclaimed is True


def test_run_one_refuses_to_pass_a_worktree_nothing_touched(monkeypatch, stub_worktree):
    """Gates are scoped to changed files; no changes means every gate is vacuous."""
    task, _repo, _removed = stub_worktree
    monkeypatch.setattr(
        ep, "grade_worktree", lambda *a, **k: (True, "satisfied", "All gates passed"),
    )
    adapter = _FakeAdapter(completed=True)  # edits nothing

    run = ep.run_one(task, "fake", adapter=adapter, timeout_seconds=60)

    assert run.files_changed == 0
    assert run.graded_pass is False
    assert run.verdict == "no_op"
    assert run.overclaimed is True


def test_run_one_records_a_grader_error_as_ungraded_not_as_a_failure(
    monkeypatch, stub_worktree
):
    task, repo, _removed = stub_worktree
    monkeypatch.setattr(
        ep, "grade_worktree", lambda *a, **k: (None, "grader_error", "gate suite error: boom"),
    )
    adapter = _FakeAdapter(
        completed=False, on_invoke=lambda s: _write(repo / "edited.txt", "after\n")
    )

    run = ep.run_one(task, "fake", adapter=adapter, timeout_seconds=60)

    assert run.graded_pass is None
    assert run.verdict == "grader_error"
    assert ep.summarize([run])["fake"]["grader_errors"] == 1
    assert ep.summarize([run])["fake"]["gate_pass_rate"] is None


def test_run_one_reports_an_unavailable_adapter_without_building(monkeypatch):
    created = []
    monkeypatch.setattr(
        ep, "create_replay_worktree",
        lambda *a: created.append(a) or Path("."),
    )
    task = ep.CorpusTask(
        task_id="c", title="T", prompt="p", base_commit="a" * 40, reference_commit="b" * 40
    )

    run = ep.run_one(task, "fake", adapter=_FakeAdapter(available=False))

    assert run.status == "unavailable"
    assert created == [], "an unavailable adapter must not cost a worktree"


def test_run_one_records_a_degraded_prose_run(monkeypatch, stub_worktree):
    """AgentLoopUnsupported degrades inside the adapter; the harness must not
    mistake that for a harness failure."""
    task, _repo, _removed = stub_worktree
    monkeypatch.setattr(ep, "grade_worktree", lambda *a, **k: (False, "needs_revision", "x"))
    adapter = _FakeAdapter(
        completed=False,
        structured={"degraded": True, "degraded_reason": "provider cannot do tool use"},
    )

    run = ep.run_one(task, "fake", adapter=adapter, timeout_seconds=60)

    assert run.status == "ok"
    assert run.degraded is True
    assert "tool use" in run.degraded_reason
    assert ep.summarize([run])["fake"]["degraded_runs"] == 1


def test_run_one_survives_an_adapter_that_raises(monkeypatch, stub_worktree):
    task, _repo, removed = stub_worktree

    class _Exploding(_FakeAdapter):
        def invoke(self, session):
            raise RuntimeError("provider exploded")

    run = ep.run_one(task, "fake", adapter=_Exploding(), timeout_seconds=60)

    assert run.status == "harness_error"
    assert "provider exploded" in run.error
    assert removed, "a failed replay still cleans up its worktree"


def test_session_forbids_committing_pushing_and_touching_the_board(
    monkeypatch, stub_worktree
):
    task, _repo, _removed = stub_worktree
    monkeypatch.setattr(ep, "grade_worktree", lambda *a, **k: (False, "needs_revision", ""))
    adapter = _FakeAdapter()

    ep.run_one(task, "fake", adapter=adapter, timeout_seconds=60, max_turns=7)

    session = adapter.sessions[0]
    system = session.system_prompt.lower()
    for forbidden in ("git commit", "git push", "pull request", "kanban board"):
        assert forbidden in system, f"benchmark prompt does not forbid {forbidden!r}"
    assert session.max_turns == 7, "both executors must get the same turn budget"


def test_both_executors_get_the_identical_instruction(monkeypatch, stub_worktree):
    task, _repo, _removed = stub_worktree
    monkeypatch.setattr(ep, "grade_worktree", lambda *a, **k: (False, "needs_revision", ""))
    a, b = _FakeAdapter(name="a"), _FakeAdapter(name="b")

    ep.run_one(task, "a", adapter=a, timeout_seconds=60)
    ep.run_one(task, "b", adapter=b, timeout_seconds=60)

    assert a.sessions[0].prompt == b.sessions[0].prompt
    assert a.sessions[0].system_prompt == b.sessions[0].system_prompt


# ---------------------------------------------------------------------------
# the wall-clock ceiling the harness imposes on both executors
# ---------------------------------------------------------------------------


def test_invoke_bounded_returns_a_prompt_result_untouched():
    import threading

    class _Session:
        task_id = "t"
        timeout_seconds = 30

    adapter = _FakeAdapter(completed=True)
    box = ep.invoke_bounded(adapter, _Session(), threading.Event())

    assert box.timed_out is False and box.still_running is False
    assert box.result.completed is True


def test_invoke_bounded_signals_stop_then_reports_the_timeout():
    """`local_agent` only derives budget shares from timeout_seconds; the ceiling
    has to be imposed from outside or the wall-clock columns are not comparable."""
    import threading

    released = threading.Event()

    class _Slow(_FakeAdapter):
        def invoke(self, session):
            # Honours the cooperative stop signal, like local_agent does.
            session.metadata["stop_event"].wait(10)
            released.set()
            from tools.agents.adapter_base import AgentResult

            return AgentResult(task_id="t", adapter_name="slow", completed=False)

    class _Session:
        task_id = "t"
        timeout_seconds = 0
        metadata = {"stop_event": threading.Event()}

    session = _Session()
    box = ep.invoke_bounded(
        adapter=_Slow(), session=session,
        stop_event=session.metadata["stop_event"],
        grace_seconds=0.2, stop_grace_seconds=5.0,
    )

    assert box.timed_out is True
    assert box.still_running is False, "the adapter honoured the stop signal"
    assert released.is_set()


def test_invoke_bounded_flags_an_adapter_that_ignores_the_stop_signal():
    import threading

    class _Runaway(_FakeAdapter):
        def invoke(self, session):
            time_to_wait = threading.Event()
            time_to_wait.wait(30)  # never set — ignores stop_event entirely

    class _Session:
        task_id = "t"
        timeout_seconds = 0
        metadata = {"stop_event": threading.Event()}

    session = _Session()
    box = ep.invoke_bounded(
        adapter=_Runaway(), session=session,
        stop_event=session.metadata["stop_event"],
        grace_seconds=0.2, stop_grace_seconds=0.2,
    )

    assert box.timed_out is True and box.still_running is True
    assert box.result is None


def test_run_one_grades_the_tree_a_timed_out_executor_left_behind(
    monkeypatch, stub_worktree
):
    task, repo, removed = stub_worktree
    monkeypatch.setattr(ep, "grade_worktree", lambda *a, **k: (False, "needs_revision", "x"))
    monkeypatch.setattr(
        ep, "invoke_bounded",
        lambda adapter, session, stop_event, **kw: (
            _write(repo / "edited.txt", "half-finished\n"),
            ep._Invocation(result=None, timed_out=True, still_running=False),
        )[1],
    )

    run = ep.run_one(task, "fake", adapter=_FakeAdapter(), timeout_seconds=5)

    assert run.timed_out is True
    assert run.status == "ok", "a timeout is a result, not a harness failure"
    assert run.files_changed == 1, "whatever it managed to write is still graded"
    assert "ceiling" in run.error
    assert removed, "a timed-out (but finished) thread still gets its tree cleaned up"


def test_run_one_leaks_the_worktree_of_a_thread_still_writing(
    monkeypatch, stub_worktree
):
    """Deleting a tree underneath a live thread turns one slow executor into a
    stream of unrelated I/O errors."""
    task, _repo, removed = stub_worktree
    monkeypatch.setattr(ep, "grade_worktree", lambda *a, **k: (False, "needs_revision", "x"))
    monkeypatch.setattr(
        ep, "invoke_bounded",
        lambda *a, **k: ep._Invocation(result=None, timed_out=True, still_running=True),
    )

    run = ep.run_one(task, "fake", adapter=_FakeAdapter(), timeout_seconds=5)

    assert run.abandoned is True
    assert removed == [], "the tree is leaked on purpose while the thread lives"


# ---------------------------------------------------------------------------
# module budget — the ceiling only ONE executor is subject to
# ---------------------------------------------------------------------------


def test_lifted_module_budget_is_process_local_and_restores():
    tracker = importlib.import_module("tools.budget.module_budget_tracker")
    before = tracker._module_budget_config_cache

    with ep.lifted_module_budget(active=True):
        assert tracker._module_budget_config_cache == {"enabled": False}

    assert tracker._module_budget_config_cache is before


def test_lifted_module_budget_is_a_no_op_when_inactive():
    tracker = importlib.import_module("tools.budget.module_budget_tracker")
    before = tracker._module_budget_config_cache

    with ep.lifted_module_budget(active=False):
        assert tracker._module_budget_config_cache is before

    assert tracker._module_budget_config_cache is before


def test_run_parity_records_whether_the_budget_was_lifted(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ep, "run_one",
        lambda task, executor, **kw: _run(task.task_id, executor, graded_pass=True),
    )
    monkeypatch.setattr(ep, "module_budget_status", lambda: {"action": "block"})
    tasks = [ep.CorpusTask(task_id="t", title="T", prompt="p",
                           base_commit="a" * 40, reference_commit="b" * 40)]

    report = ep.run_parity(tasks, ["a"], lift_module_budget=True)

    assert report["meta"]["module_budget_lifted"] is True
    assert report["meta"]["module_budget_at_start"] == {"action": "block"}
    md = ep.render_markdown(report)
    assert "Module-budget enforcement was suspended" in md


def test_module_budget_is_never_lifted_by_default(monkeypatch):
    monkeypatch.setattr(
        ep, "run_one",
        lambda task, executor, **kw: _run(task.task_id, executor, graded_pass=True),
    )
    monkeypatch.setattr(ep, "module_budget_status", lambda: {"action": "allow"})
    tasks = [ep.CorpusTask(task_id="t", title="T", prompt="p",
                           base_commit="a" * 40, reference_commit="b" * 40)]

    report = ep.run_parity(tasks, ["a"])

    assert report["meta"]["module_budget_lifted"] is False
    assert "Module-budget enforcement was suspended" not in ep.render_markdown(report)


# ---------------------------------------------------------------------------
# aggregation + report
# ---------------------------------------------------------------------------


def test_summarize_computes_rates_wall_clock_and_cost():
    runs = [
        _run("t1", "cli", graded_pass=True, self_reported_complete=True,
             build_seconds=100.0, cost_usd=1.0, files_changed=3, input_tokens=10,
             output_tokens=2),
        _run("t2", "cli", graded_pass=False, self_reported_complete=True,
             build_seconds=200.0, cost_usd=3.0, files_changed=1),
        _run("t1", "own", graded_pass=False, self_reported_complete=False,
             build_seconds=50.0, cost_usd=0.25, files_changed=2),
        _run("t2", "own", status="unavailable", available=False),
    ]

    s = ep.summarize(runs)

    assert s["cli"]["gate_pass_rate"] == 0.5
    assert s["cli"]["self_report_rate"] == 1.0
    assert s["cli"]["overclaimed"] == 1
    assert s["cli"]["wall_clock_mean_s"] == 150.0
    assert s["cli"]["wall_clock_median_s"] == 150.0
    assert s["cli"]["cost_total_usd"] == 4.0
    assert s["own"]["attempted"] == 1
    assert s["own"]["unavailable"] == 1
    assert s["own"]["gate_pass_rate"] == 0.0


def test_summarize_marks_cost_as_unreported_rather_than_zero():
    """An adapter that reports no cost must not read as a free executor."""
    runs = [_run("t1", "cli", graded_pass=True, build_seconds=10.0, cost_usd=0.0)]
    assert ep.summarize(runs)["cli"]["cost_reported"] is False

    md = ep.render_markdown(ep.build_report(runs, {"corpus_id": "c", "task_count": 1}))
    assert "not reported" in md


def test_render_markdown_reports_both_executors_rate_clock_and_cost():
    runs = [
        _run("t1", "claude_cli", graded_pass=True, self_reported_complete=True,
             build_seconds=120.0, cost_usd=0.9, files_changed=3),
        _run("t1", "local_agent", graded_pass=False, self_reported_complete=True,
             build_seconds=300.0, cost_usd=0.2, files_changed=1),
    ]
    md = ep.render_markdown(ep.build_report(runs, {"corpus_id": "x", "task_count": 1}))

    assert "claude_cli" in md and "local_agent" in md
    assert "Gate-pass rate" in md
    assert "Wall clock, mean (s)" in md
    assert "Cost, total (USD)" in md
    assert "`t1`" in md


def test_report_round_trips_through_json(tmp_path):
    runs = [_run("t1", "cli", graded_pass=True, build_seconds=1.0)]
    out = tmp_path / "nested" / "report.json"
    ep.write_json(out, ep.build_report(runs, {"corpus_id": "x", "task_count": 1}))
    with open(out, "r", encoding="utf-8", newline="") as fh:
        loaded = json.load(fh)
    assert loaded["summary"]["cli"]["gate_passes"] == 1
    assert loaded["runs"][0]["task_id"] == "t1"


def test_run_parity_is_task_major_so_a_partial_run_still_pairs(monkeypatch, tmp_path):
    """Interrupted halfway, the results must still compare A against B per task."""
    order = []
    monkeypatch.setattr(
        ep, "run_one",
        lambda task, executor, **kw: order.append((task.task_id, executor))
        or _run(task.task_id, executor, graded_pass=True, build_seconds=1.0),
    )
    tasks = [
        ep.CorpusTask(task_id=f"t{i}", title="T", prompt="p",
                      base_commit="a" * 40, reference_commit="b" * 40)
        for i in range(3)
    ]
    out = tmp_path / "partial.json"

    ep.run_parity(tasks, ["a", "b"], out_path=out)

    assert order == [("t0", "a"), ("t0", "b"), ("t1", "a"), ("t1", "b"),
                     ("t2", "a"), ("t2", "b")]
    assert out.is_file(), "results are flushed as they complete"


def test_cli_dry_run_builds_nothing(monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("--dry-run must not create a worktree")

    monkeypatch.setattr(ep, "create_replay_worktree", _boom)
    monkeypatch.setattr(ep, "run_parity", _boom)

    assert ep.main(["--dry-run", "--limit", "2", "--no-dotenv"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["tasks"]) == 2
    assert set(payload["adapters"]) == {"claude_cli", "local_agent"}


def test_cli_requires_an_explicit_run(monkeypatch):
    monkeypatch.setattr(ep, "run_parity", lambda *a, **k: pytest.fail("ran without --run"))
    with pytest.raises(SystemExit):
        ep.main(["--no-dotenv"])


# ---------------------------------------------------------------------------
# .env import
# ---------------------------------------------------------------------------


def test_load_env_file_never_imports_the_rubric_loop_flag(tmp_path, monkeypatch):
    """The benchmark must not be able to flip the kanban executor, even by accident."""
    env = tmp_path / ".env"
    _write(env, "SOME_PROVIDER_KEY=abc\nKANBAN_RUBRIC_LOOP=true\n")
    monkeypatch.delenv("KANBAN_RUBRIC_LOOP", raising=False)
    monkeypatch.delenv("SOME_PROVIDER_KEY", raising=False)

    assert ep.load_env_file(env) == 1
    assert os.environ["SOME_PROVIDER_KEY"] == "abc"
    assert "KANBAN_RUBRIC_LOOP" not in os.environ


def test_load_env_file_never_overrides_an_existing_value(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    _write(env, "SOME_PROVIDER_KEY=from_file\n")
    monkeypatch.setenv("SOME_PROVIDER_KEY", "from_shell")

    ep.load_env_file(env)

    assert os.environ["SOME_PROVIDER_KEY"] == "from_shell"


def test_load_env_file_tolerates_a_missing_file(tmp_path):
    assert ep.load_env_file(tmp_path / "absent.env") == 0


# ---------------------------------------------------------------------------
# claude_cli adapter: the cost column the comparison needs
# ---------------------------------------------------------------------------


def test_claude_cli_parses_cost_and_tokens_from_the_json_envelope():
    mod = importlib.import_module("tools.agents.adapters.claude_cli")
    envelope = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "num_turns": 9, "session_id": "s-1", "total_cost_usd": 1.2345,
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 5,
                  "output_tokens": 7},
        "result": "Task completed",
    })

    text, structured = mod._parse_cli_json(envelope)

    assert text == "Task completed"
    assert structured["total_cost_usd"] == 1.2345
    assert structured["input_tokens"] == 15
    assert structured["output_tokens"] == 7
    assert structured["turns"] == 9


def test_claude_cli_degrades_to_plain_text_when_the_envelope_is_absent():
    """An older CLI keeps working — it just contributes no cost data."""
    mod = importlib.import_module("tools.agents.adapters.claude_cli")

    text, structured = mod._parse_cli_json("just some prose\n")

    assert text == "just some prose\n"
    assert structured == {}


# ---------------------------------------------------------------------------
# EXPLICIT NON-GOAL — hgx-exec-04 must not flip anything
# ---------------------------------------------------------------------------


def test_executor_fallback_chain_order_is_unchanged():
    with open(REPO_ROOT / "args" / "strategos_config.yaml", "r",
              encoding="utf-8", newline="") as fh:
        cfg = yaml.safe_load(fh) or {}
    chain = (cfg.get("executor") or {}).get("fallback_chain")
    assert chain == ["claude_cli", "gitlab", "github_actions", "ollama_local"], (
        "hgx-exec-04 is a measurement task: the executor chain order is a "
        "separate, later human decision made against the published numbers"
    )


def test_rubric_loop_still_defaults_off(monkeypatch):
    monkeypatch.delenv("KANBAN_RUBRIC_LOOP", raising=False)
    kanban = importlib.import_module("tools.genesis.reflexes.kanban")
    assert kanban._rubric_loop_enabled() is False


def test_benchmark_never_reads_or_writes_the_rubric_loop_flag():
    """Grep the harness: the flag may be named, never read and never assigned."""
    for path in (
        REPO_ROOT / "tools" / "workflow" / "executor_parity.py",
        REPO_ROOT / "icdev" / "tools" / "workflow" / "executor_parity.py",
    ):
        with open(path, "r", encoding="utf-8", newline="") as fh:
            source = fh.read()
        for banned in (
            'os.environ["KANBAN_RUBRIC_LOOP"]',
            'os.environ.get("KANBAN_RUBRIC_LOOP"',
            'setenv("KANBAN_RUBRIC_LOOP"',
        ):
            assert banned not in source, f"{path} touches the flag: {banned}"
        # The only load-bearing mention is the denylist that keeps a .env value
        # for it out of os.environ.
        assert '_ENV_DENYLIST = frozenset({"KANBAN_RUBRIC_LOOP"})' in source


def test_harness_is_mirrored_to_the_icdev_package():
    canonical = REPO_ROOT / "tools" / "workflow" / "executor_parity.py"
    mirror = REPO_ROOT / "icdev" / "tools" / "workflow" / "executor_parity.py"
    assert mirror.is_file()
    assert canonical.read_bytes() == mirror.read_bytes()


# ---------------------------------------------------------------------------
# live run — opt-in only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("ICDEV_PARITY_LIVE", "").strip().lower() not in ("1", "true", "yes"),
    reason="live executor replay costs real money and minutes; set ICDEV_PARITY_LIVE=1",
)
def test_live_single_task_replay_produces_a_scored_run():  # pragma: no cover - opt-in
    ep.load_env_file()
    task = next(t for t in ep.load_corpus() if t.task_id == "cxo-doc-01")
    executor = os.environ.get("ICDEV_PARITY_EXECUTOR", "claude_cli")

    run = ep.run_one(task, executor, timeout_seconds=300, max_turns=15)

    assert run.status in ("ok", "unavailable")
    if run.status == "ok":
        assert run.graded_pass in (True, False, None)
        assert run.build_seconds > 0
