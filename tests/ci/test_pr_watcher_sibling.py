# CUI // SP-CTI
"""Tests for the pr_watcher sibling-file-conflict guard (kph).

Unit-tests the pure helpers, then an end-to-end poll_once with an injected
pr_list_runner supplying two open PRs that race on the same source file.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import tools.ci.pr_watcher as pw
from tests.ci.test_pr_watcher import _fake_connection_factory, _FakeRow, _green_pr_state


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_is_additive_path_excludes_coordination_files():
    assert pw._is_additive_path("tools/manifest/cortex.md")
    assert pw._is_additive_path("tests/conftest.py")
    assert pw._is_additive_path(".claude/hooks/pre_tool_use.py")
    assert pw._is_additive_path("args/component_registry.yaml")
    # source files are NOT additive
    assert not pw._is_additive_path("tools/cortex/blueprint.py")
    assert not pw._is_additive_path("tools/db/migrations/263_x.sql")


def test_generated_artifacts_are_not_collisions():
    """A derived file must never serialize merges — it deadlocked the board once.

    On 2026-08-09 every open PR regenerated
    docs/research/external-benchmark-map.generated.md, so hold_on_sibling_conflict
    made each PR a sibling of every other and refused all six while both daemons
    ran healthily. A conflict in generated output is not a disagreement: rerunning
    the generator over the merged tree produces the right content, so there is
    nothing to arbitrate and nothing a serialized merge protects.
    """
    assert pw._is_generated_path("docs/research/external-benchmark-map.generated.md")
    assert pw._is_additive_path("docs/research/external-benchmark-map.generated.md")
    assert pw._is_generated_path("tools/x/generated/schema.json")
    # The marker must be specific enough not to swallow hand-written sources.
    assert not pw._is_generated_path("tools/builder/db_init_generator.py")
    assert not pw._is_additive_path("tools/builder/db_init_generator.py")
    assert not pw._is_generated_path("tools/cortex/blueprint.py")


def test_a_shared_generated_file_alone_does_not_hold_a_pr():
    """The end-to-end shape of the deadlock: two PRs, one shared generated file."""
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    file_map = {
        "https://x/pull/1": {"docs/research/map.generated.md", "tools/a/one.py"},
        "https://x/pull/2": {"docs/research/map.generated.md", "tools/b/two.py"},
    }
    assert w._sibling_conflicts("https://x/pull/1", file_map) == {}


def test_sibling_conflicts_flags_shared_source_file():
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    file_map = {
        "pr/1": {"tools/cortex/blueprint.py", "tools/manifest/cortex.md"},
        "pr/2": {"tools/cortex/blueprint.py", "tests/conftest.py"},
        "pr/3": {"tools/other/thing.py"},
    }
    conflicts = w._sibling_conflicts("pr/1", file_map)
    assert conflicts == {"pr/2": {"tools/cortex/blueprint.py"}}


def test_sibling_conflicts_ignores_additive_only_overlap():
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    file_map = {
        "pr/1": {"tools/manifest/cortex.md", "tests/conftest.py"},
        "pr/2": {"tools/manifest/cortex.md", "tests/conftest.py"},
    }
    assert w._sibling_conflicts("pr/1", file_map) == {}


def test_open_pr_files_parses_gh_output():
    payload = json.dumps([
        {"url": "pr/1", "files": [{"path": "a.py"}, {"path": "b.py"}]},
        {"url": "pr/2", "files": [{"path": "a.py"}]},
    ])

    def fake_list(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    w = pw.PRWatcher(config={}, get_connection=lambda: None, pr_list_runner=fake_list)
    fm = w._open_pr_files()
    assert fm == {"pr/1": {"a.py", "b.py"}, "pr/2": {"a.py"}}


def test_open_pr_files_degrades_on_error():
    def boom(cmd, **kw):
        raise RuntimeError("gh missing")

    w = pw.PRWatcher(config={}, get_connection=lambda: None, pr_list_runner=boom)
    assert w._open_pr_files() == {}


# ---------------------------------------------------------------------------
# poll_once integration
# ---------------------------------------------------------------------------
_CANDIDATE = "https://github.com/o/r/pull/500"
_SIBLING = "https://github.com/o/r/pull/501"


def _build_watcher(*, hold, merge_calls, shared_file="tools/cortex/blueprint.py"):
    tasks = [_FakeRow(id="task-s", title="T", description="",
                      status="in_progress", executor_url=_CANDIDATE)]
    state_map = {_CANDIDATE: _green_pr_state("main")}

    def fake_merge(cmd, **kw):
        merge_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_list(cmd, **kw):
        payload = json.dumps([
            {"url": _CANDIDATE, "files": [{"path": shared_file}]},
            {"url": _SIBLING, "files": [{"path": shared_file}]},
        ])
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    return pw.PRWatcher(
        config={
            "auto_merge_enabled": True,
            "auto_merge_require_approval": False,
            "max_resume_cycles_per_task": 5,
            "sibling_conflict_check": True,
            "hold_on_sibling_conflict": hold,
        },
        # A PASSED done-verification, so the enforced done-gate is satisfied and
        # the SIBLING guard is what decides — which is what these tests assert.
        # Without it, a session that inherits KANBAN_PIPELINE_ENFORCE=1 from .env
        # (every kanban-scheduler session does) holds every merge on "awaiting
        # ICDEV done-verification" and all four poll_once tests below fail, while
        # a developer shell with the variable unset sees them pass. Same fix
        # test_pr_watcher.py already carries for the base-branch guard tests.
        get_connection=_fake_connection_factory(
            tasks,
            verifications=[{"task_id": "task-s", "result": "pass", "review_passed": 1}],
        ),
        queue_message=lambda *a, **kw: {"queued": True},
        fetch_state=lambda url, **kw: state_map[url],
        fetch_logs=lambda url, **kw: "",
        auto_merge_runner=fake_merge,
        pr_list_runner=fake_list,
        default_branch_resolver=lambda: "main",
    )


def test_sibling_conflict_warns_but_merges_by_default():
    merge_calls = []
    w = _build_watcher(hold=False, merge_calls=merge_calls)
    report = w.poll_once()
    action = report.actions[-1]
    assert action.action == "merge"          # warn-only: merge still proceeds
    assert merge_calls                        # auto-merge was invoked


def test_sibling_conflict_holds_when_configured():
    merge_calls = []
    w = _build_watcher(hold=True, merge_calls=merge_calls)
    report = w.poll_once()
    action = report.actions[-1]
    assert action.action == "wait"
    assert "sibling file conflict" in action.reason
    assert not merge_calls                     # merge was NOT invoked


def test_sibling_conflict_emits_lesson(monkeypatch):
    # The sibling guard must record a SIBLING_FILE_CONFLICT lesson (recurrence).
    calls = {}
    import tools.workflow.lesson_learned as ll

    monkeypatch.setattr(ll, "analyze_task",
                        lambda tid, outcome=None: calls.setdefault("outcome", outcome) or ("lesson", tid))
    monkeypatch.setattr(ll, "write_lesson", lambda lesson: calls.setdefault("written", lesson))

    merge_calls = []
    w = _build_watcher(hold=False, merge_calls=merge_calls)
    w.poll_once()
    assert calls.get("outcome") == "sibling_file_conflict"
    assert "written" in calls


def test_no_conflict_when_files_differ():
    merge_calls = []
    w = _build_watcher(hold=True, merge_calls=merge_calls, shared_file="tools/cortex/blueprint.py")
    # Override the list runner so the sibling touches a DIFFERENT file.
    def fake_list(cmd, **kw):
        payload = json.dumps([
            {"url": _CANDIDATE, "files": [{"path": "tools/cortex/blueprint.py"}]},
            {"url": _SIBLING, "files": [{"path": "tools/other/unrelated.py"}]},
        ])
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")
    w._pr_list_runner = fake_list
    report = w.poll_once()
    assert report.actions[-1].action == "merge"
    assert merge_calls
