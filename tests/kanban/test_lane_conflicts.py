# CUI // SP-CTI
"""Sibling file contention detected at SEED time, not after the PR (rem-hyg-07).

Every case here is drawn from something measured on the live board on
2026-08-16, so a regression breaks a test that describes a real collision rather
than an invented one.
"""
from __future__ import annotations

import pytest

from tools.git import coordination_paths as cp
from tools.kanban import lane_conflicts as lc


# ────────────────────────────────────────────────────────────────────────────
# The coordination list is SHARED with pr_watcher, not copied
# ────────────────────────────────────────────────────────────────────────────

def test_pr_watcher_uses_the_shared_coordination_list():
    """A second divergent copy is worse than none — the two must be one object."""
    from tools.ci import pr_watcher as pw

    assert pw._is_additive_path is cp.is_coordination_path
    assert pw._is_generated_path is cp.is_generated_path
    assert pw._ADDITIVE_PATH_MARKERS is cp.COORDINATION_PATH_MARKERS


def test_lane_conflicts_uses_the_same_list():
    assert lc.is_coordination_path is cp.is_coordination_path


def test_coordination_paths_module_is_import_free():
    """Any caller — CLI, watcher, seeder, route — must be able to import it.

    Parsed rather than grepped: the module's own usage example is an import
    statement inside its docstring, and a line scan reads that as code.
    """
    import ast

    with open(cp.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and getattr(node, "module", None) != "__future__"
    ]
    assert imported == [], [ast.dump(n) for n in imported]


# ────────────────────────────────────────────────────────────────────────────
# Path extraction — the whole difficulty of the check
# ────────────────────────────────────────────────────────────────────────────

def test_a_bare_path_is_a_claim():
    assert lc.claimed_paths(
        "Gemini reports cachedContentTokenCount, and tools/llm/gemini_provider.py "
        "has zero references to it."
    ) == {"tools/llm/gemini_provider.py"}


def test_verification_commands_are_not_claims():
    """The dominant false-positive class: nearly every description ends with one."""
    for text in (
        "It must go RED against the merge base - python tools/ci/red_first_gate.py --gate.",
        "It has to fail against the pre-change tree (python tools/ci/red_first_gate.py --gate).",
        "that is the red-first proof (`tools/ci/red_first_gate.py --gate`).",
        "Run pytest tests/kanban/test_lane_conflicts.py -q afterwards.",
        "`python tools/workflow/coherence_checker.py --check capability_liveness --gate`",
    ):
        assert lc.claimed_paths(text) == set(), text


def test_a_file_both_run_and_edited_is_still_claimed():
    """Any ONE surviving occurrence makes it a claim — suppression is per-mention."""
    text = (
        "Extend tools/ci/red_first_gate.py with a new exemption reader.\n"
        "Verify with python tools/ci/red_first_gate.py --gate."
    )
    assert "tools/ci/red_first_gate.py" in lc.claimed_paths(text)


def test_citations_of_a_memo_are_not_claims():
    text = "FULL FINDING: docs/security/approval-gate-reachability.md section 5 (rem-cap-03)."
    assert lc.claimed_paths(text) == set()


def test_a_doc_with_a_write_verb_on_its_line_is_a_claim():
    """`docs/` is reference-class by DEFAULT, not unconditionally."""
    text = "OUTPUT: commit docs/testing/ungated_test_census.json - a machine-readable list."
    assert lc.claimed_paths(text) == {"docs/testing/ungated_test_census.json"}
    assert lc.claimed_paths(
        "Read docs/testing/ungated_test_census.json from rem-tst-01."
    ) == set()


def test_an_explicit_do_not_touch_is_not_a_claim():
    """rem-tst-01 says this about the two files rem-tst-02 genuinely edits."""
    text = "Do NOT change args/ci_test_files/core.txt or args/ci_test_backlog.txt in this task"
    assert lc.claimed_paths(text) == set()


def test_coordination_files_are_never_contention():
    text = "Append a row to tools/manifest/kanban.md and update tests/conftest.py."
    assert lc.claimed_paths(text) == set()


# ────────────────────────────────────────────────────────────────────────────
# The three suppressions found by running the check against the LIVE board.
# Every string below is real text from a real row on 2026-08-16, not a
# constructed example — each one produced a false finding before its rule.
# ────────────────────────────────────────────────────────────────────────────

def test_files_named_inside_a_measured_paragraph_are_evidence_not_claims():
    """rem-hyg-05's census: five specimens, none of them an edit target.

    An evidence paragraph enumerates the files a finding was observed IN. Read
    as claims they paired rem-hyg-05 against half the board.
    """
    text = (
        "MEASURED 2026-08-16 across tools/: 94 files contain a raw "
        "`INSERT INTO kanban_tasks` and do NOT call create_tasks. Twenty-one of "
        "the bypassers are the AUTONOMOUS path - tools/ace/controller.py, "
        "tools/chat/kanban_bridge.py, tools/awareness/suggested_card_writer.py."
    )
    assert lc.claimed_paths(text) == set()


def test_the_evidence_marker_must_be_capitalised_and_lead_the_line():
    """The whole discriminator. Lower-case "measured" mid-sentence is prose.

    Without this the rule would silence any sentence containing the word, which
    is how a suppression stops being a suppression and becomes a blindfold.
    """
    assert lc.claimed_paths(
        "We measured the cost and will edit tools/a/b.py."
    ) == {"tools/a/b.py"}
    assert lc.claimed_paths(
        "MEASURED 2026-08-16: the regression lives in tools/a/b.py."
    ) == set()


def test_an_evidence_paragraph_is_not_rescued_by_a_write_verb():
    """Deliberately unlike the `docs/` rule, and the reason is in the data.

    An evidence paragraph narrates writes that ALREADY happened, so honouring a
    write verb would defeat the rule on precisely the sentences it exists for.
    """
    text = "MEASURED 2026-08-16: a session added tools/a/b.py and landed it in #1684."
    assert lc.claimed_paths(text) == set()


def test_a_precedent_to_follow_is_not_a_file_to_edit():
    """rem-hyg-05 vs rem-tst-01, over two files rem-hyg-05 never opens."""
    text = (
        "Follow args/ci_test_backlog.txt + args/test_gating_gate.yaml: the "
        "census only ever SHRINKS and the ceiling may only go DOWN."
    )
    assert lc.claimed_paths(text) == set()


def test_a_cross_reference_to_a_code_path_is_a_citation():
    """rem-hyg-04 vs hcx-live-02 — the board's only `live` pair, and it was wrong.

    The brief named citations using a `docs/` example, which the reference-prefix
    rule already covered. Pointed at a CODE path no prefix can catch it.
    """
    text = (
        "NEVER a shell operator and never a config key the code does not read - "
        "see the inert hook_points: block in args/extension_config.yaml."
    )
    assert lc.claimed_paths(text) == set()


def test_a_cross_reference_marker_does_not_reach_across_a_sentence():
    """Bounded, so "see" in an earlier sentence cannot silence a later claim."""
    text = "See the memo for background. Add the reader to tools/a/b.py."
    assert "tools/a/b.py" in lc.claimed_paths(text)


def test_each_suppression_reports_itself_by_name():
    """A dropped path says WHICH rule dropped it — the classes are never merged."""
    kinds = {m.path: m.kind for m in lc.mentions(
        "MEASURED 2026-08-16: the defect is in tools/ev/a.py.\n"
        "Follow args/ci_test_backlog.txt for the census shape.\n"
        "See the inert block in args/extension_config.yaml.\n"
        "Rewrite tools/real/target.py.\n"
    )}
    assert kinds["tools/ev/a.py"] == lc.MENTION_EVIDENCE
    assert kinds["args/ci_test_backlog.txt"] == lc.MENTION_PRECEDENT
    assert kinds["args/extension_config.yaml"] == lc.MENTION_CITATION
    assert kinds["tools/real/target.py"] == lc.MENTION_CLAIM


def test_prose_that_looks_like_a_path_is_not_one():
    """Version numbers, money and percentages must not become files."""
    assert lc.claimed_paths(
        "60% of traffic runs on qwen3.5; 95.5% of calls are under 1024 tokens, saving $0.00."
    ) == set()


def test_mentions_report_why_each_occurrence_was_dropped():
    kinds = {m.kind for m in lc.mentions(
        "Edit tools/a/b.py.\n"
        "Run python tools/c/d.py --gate.\n"
        "See docs/x/y.md.\n"
        "Append to tools/manifest/kanban.md.\n"
    )}
    assert kinds == {
        lc.MENTION_CLAIM, lc.MENTION_COMMAND,
        lc.MENTION_CITATION, lc.MENTION_COORDINATION,
    }


def test_the_write_verb_window_is_the_line_and_only_the_line():
    """A documented limit of the heuristic, pinned so it cannot drift silently.

    A write verb rescues a reference-class path from the citation class only on
    its OWN line. Widening that window to N characters straddles unrelated
    sentences and starts inventing claims; narrowing it below the line loses
    "OUTPUT: commit docs/...". Descriptions on this board are line-broken prose,
    so the line is the unit that matches how they are actually written.

    Where an explicit cross-reference marker is present, it settles the question
    ahead of the line-wide write verb: "See X. Also update Y." claims only Y,
    because "See" governs X directly while "update" belongs to a later sentence.
    The residual limit is pinned below — WITHOUT such a marker, a single-line
    paragraph that merely names a memo and edits something else still
    over-claims. That is a known false positive, not a surprise.
    """
    assert lc.claimed_paths("See docs/x/y.md.\nAlso update tools/a/b.py.") == {
        "tools/a/b.py"
    }
    assert lc.claimed_paths("See docs/x/y.md. Also update tools/a/b.py.") == {
        "tools/a/b.py"
    }
    # The hole that remains: no cross-reference marker, so the line-wide write
    # verb still rescues the memo. Asserted so a future reader sees the edge of
    # the heuristic instead of assuming prose parsing is solved.
    assert lc.claimed_paths("The format of docs/x/y.md. Also update tools/a/b.py.") == {
        "docs/x/y.md", "tools/a/b.py"
    }


# ────────────────────────────────────────────────────────────────────────────
# Dependency closure — BOTH mechanisms, because _deps_satisfied ANDs them
# ────────────────────────────────────────────────────────────────────────────

def test_closure_is_transitive():
    closure = lc.dependency_closure({"a": {"b"}, "b": {"c"}})
    assert closure["a"] == {"b", "c"}


def test_closure_survives_a_cycle():
    """Nothing in the schema forbids one; saturate, do not blow the stack."""
    closure = lc.dependency_closure({"a": {"b"}, "b": {"a"}})
    assert closure["a"] == {"b"}
    assert closure["b"] == {"a"}


def test_serialization_is_detected_in_either_direction():
    closure = lc.dependency_closure({"a": {"b"}})
    assert lc.is_serialized("a", "b", closure)
    assert lc.is_serialized("b", "a", closure)
    assert not lc.is_serialized("a", "z", closure)


def _task(tid, desc, status="backlog", deps=(), title=""):
    return lc.Task(id=tid, title=title, description=desc, status=status,
                   depends_on=set(deps))


def test_a_dependency_suppresses_the_pair():
    """rem-tst-03 depends on rem-tst-02 precisely BECAUSE both edit that file."""
    tasks = [
        _task("rem-tst-02", "Move entries out of args/ci_test_backlog.txt.", "scheduled"),
        _task("rem-tst-03", "Same procedure on args/ci_test_backlog.txt.",
              "backlog", deps=["rem-tst-02"]),
    ]
    lc.resolve_paths(tasks)
    edges = {"rem-tst-03": {"rem-tst-02"}}
    conflicts = lc.find_conflicts(tasks, lc.dependency_closure(edges),
                                 {"rem-tst-02": "scheduled", "rem-tst-03": "backlog"})
    assert conflicts == []


def test_a_junction_only_dependency_also_suppresses_the_pair():
    """`kanban_task_deps` alone is a serialization — _deps_satisfied ANDs both."""
    tasks = [
        _task("x-a-01", "Edit tools/x/mod.py.", "scheduled"),
        _task("x-a-02", "Edit tools/x/mod.py.", "scheduled", deps=["x-a-01"]),
    ]
    lc.resolve_paths(tasks)
    conflicts = lc.find_conflicts(
        tasks, lc.dependency_closure({"x-a-02": {"x-a-01"}}),
        {"x-a-01": "scheduled", "x-a-02": "scheduled"},
    )
    assert conflicts == []


# ────────────────────────────────────────────────────────────────────────────
# Ranking — a latent pair must not read like a live one
# ────────────────────────────────────────────────────────────────────────────

def test_both_dispatchable_is_live():
    tasks = [
        _task("x-a-01", "Edit tools/x/mod.py.", "scheduled"),
        _task("x-b-01", "Also edit tools/x/mod.py.", "in_progress"),
    ]
    lc.resolve_paths(tasks)
    conflicts = lc.find_conflicts(tasks, {}, {"x-a-01": "scheduled", "x-b-01": "in_progress"})
    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == lc.SEVERITY_LIVE
    assert conflicts[0]["shared_files"] == ["tools/x/mod.py"]


def test_an_unsatisfied_dependency_makes_the_pair_latent():
    """A backlog task whose dependency is unsatisfied cannot race."""
    tasks = [
        _task("x-a-01", "Edit tools/x/mod.py.", "scheduled"),
        _task("x-b-01", "Also edit tools/x/mod.py.", "backlog", deps=["x-gate-00"]),
    ]
    lc.resolve_paths(tasks)
    conflicts = lc.find_conflicts(
        tasks, lc.dependency_closure({"x-b-01": {"x-gate-00"}}),
        {"x-a-01": "scheduled", "x-b-01": "backlog", "x-gate-00": "in_progress"},
    )
    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == lc.SEVERITY_LATENT


def test_live_pairs_sort_before_latent_ones():
    tasks = [
        _task("x-a-01", "Edit tools/x/mod.py.", "scheduled"),
        _task("x-b-01", "Edit tools/x/mod.py.", "scheduled"),
        _task("x-c-01", "Edit tools/x/mod.py.", "backlog", deps=["x-gate-00"]),
    ]
    lc.resolve_paths(tasks)
    conflicts = lc.find_conflicts(
        tasks, lc.dependency_closure({"x-c-01": {"x-gate-00"}}),
        {"x-a-01": "scheduled", "x-b-01": "scheduled", "x-c-01": "backlog",
         "x-gate-00": "in_progress"},
    )
    assert [c["severity"] for c in conflicts][0] == lc.SEVERITY_LIVE
    assert conflicts[-1]["severity"] == lc.SEVERITY_LATENT


def test_suggested_is_quarantine_not_a_queue():
    tasks = [
        _task("x-a-01", "Edit tools/x/mod.py.", "scheduled"),
        _task("x-b-01", "Edit tools/x/mod.py.", "suggested"),
    ]
    lc.resolve_paths(tasks)
    conflicts = lc.find_conflicts(tasks, {}, {"x-a-01": "scheduled", "x-b-01": "suggested"})
    assert conflicts[0]["severity"] == lc.SEVERITY_LATENT


# ────────────────────────────────────────────────────────────────────────────
# Gate sentinels are never built, so their RISK: text is not contention
# ────────────────────────────────────────────────────────────────────────────

def test_gate_sentinels_are_excluded():
    tasks = [
        _task("x-gate-00", "RISK: an unattended session would edit tools/x/mod.py.",
              "in_progress", title="MANUAL-MODE GATE"),
        _task("x-a-01", "Edit tools/x/mod.py.", "scheduled"),
    ]
    lc.resolve_paths(tasks)
    conflicts = lc.find_conflicts(tasks, {}, {"x-gate-00": "in_progress",
                                              "x-a-01": "scheduled"})
    assert conflicts == []


def test_a_second_gate_is_excluded_too():
    """`is_manual_gate` is wide on purpose — hgx-gate-01 was the case that bit."""
    tasks = [
        _task("x-gate-01", "RISK: dispatching this edits tools/x/mod.py unattended.",
              "in_progress"),
        _task("x-a-01", "Edit tools/x/mod.py.", "scheduled"),
    ]
    lc.resolve_paths(tasks)
    assert lc.find_conflicts(tasks, {}, {"x-gate-01": "in_progress",
                                         "x-a-01": "scheduled"}) == []


def test_terminal_tasks_cannot_race():
    tasks = [
        _task("x-a-01", "Edit tools/x/mod.py.", "done"),
        _task("x-b-01", "Edit tools/x/mod.py.", "scheduled"),
    ]
    lc.resolve_paths(tasks)
    assert lc.find_conflicts(tasks, {}, {"x-a-01": "done", "x-b-01": "scheduled"}) == []


# ────────────────────────────────────────────────────────────────────────────
# Branch evidence — against origin/main, NEVER branch-to-branch
# ────────────────────────────────────────────────────────────────────────────

def test_branch_paths_diffs_against_the_base_not_another_branch():
    """merge-tree between two tips reports conflicts the forge will never see."""
    calls = []

    class _Proc:
        returncode = 0
        stdout = "tools/x/mod.py\ntools/manifest/kanban.md\n"

    def runner(argv, **kw):
        calls.append(argv)
        return _Proc()

    paths = lc.branch_paths("x-a-01", runner=runner)
    diff = next(c for c in calls if "diff" in c)
    assert diff[-1] == "origin/main...kanban/x-a-01"
    assert "..." in diff[-1] and ".." in diff[-1]
    # No argv anywhere names two task branches.
    for argv in calls:
        assert sum(1 for part in argv if "kanban/" in str(part)) <= 1
    # Coordination files are dropped from branch evidence too.
    assert paths == {"tools/x/mod.py"}


def test_no_branch_is_none_not_an_empty_set():
    """"no branch yet" and "a branch that changed nothing" are different facts."""
    class _Proc:
        returncode = 1
        stdout = ""

    assert lc.branch_paths("x-a-01", runner=lambda argv, **kw: _Proc()) is None


def test_branch_evidence_replaces_prose_rather_than_joining_it():
    class _Proc:
        returncode = 0
        stdout = "tools/actual/changed.py\n"

    task = _task("x-a-01", "Edit tools/claimed/in_prose.py.")
    lc.resolve_paths([task], from_branches=True, runner=lambda argv, **kw: _Proc())
    assert task.paths == {"tools/actual/changed.py"}
    assert task.evidence == lc.EVIDENCE_BRANCH


def test_a_task_with_no_branch_keeps_prose_and_says_so():
    class _Proc:
        returncode = 1
        stdout = ""

    task = _task("x-a-01", "Edit tools/claimed/in_prose.py.")
    lc.resolve_paths([task], from_branches=True, runner=lambda argv, **kw: _Proc())
    assert task.paths == {"tools/claimed/in_prose.py"}
    assert task.evidence == lc.EVIDENCE_PROSE


# ────────────────────────────────────────────────────────────────────────────
# Seed-time entry point — report only, fail open
# ────────────────────────────────────────────────────────────────────────────

class _FakeConn:
    """A board with one live task that already claims tools/x/mod.py."""

    def __init__(self, rows=None, junction=None):
        self.rows = rows if rows is not None else [{
            "id": "x-a-01", "title": "", "description": "Edit tools/x/mod.py.",
            "status": "scheduled", "depends_on_task_id": None,
        }]
        self.junction = junction or []

    def execute(self, sql, params=None):
        conn = self

        class _Cur:
            def fetchall(self_inner):
                return conn.junction if "kanban_task_deps" in sql else conn.rows

        return _Cur()

    def close(self):
        pass


def test_check_batch_flags_a_collision_with_the_live_board():
    findings = lc.check_batch(
        [{"id": "x-b-01", "description": "Also edit tools/x/mod.py."}],
        conn=_FakeConn(),
    )
    assert len(findings) == 1
    assert findings[0]["tasks"] == ["x-a-01", "x-b-01"]
    assert findings[0]["shared_files"] == ["tools/x/mod.py"]


def test_check_batch_flags_a_collision_inside_the_batch():
    findings = lc.check_batch(
        [
            {"id": "y-a-01", "description": "Edit tools/y/mod.py."},
            {"id": "y-a-02", "description": "Edit tools/y/mod.py."},
        ],
        conn=_FakeConn(rows=[]),
    )
    assert [f["tasks"] for f in findings] == [["y-a-01", "y-a-02"]]


def test_check_batch_honours_a_scalar_dependency_inside_the_batch():
    findings = lc.check_batch(
        [
            {"id": "y-a-01", "description": "Edit tools/y/mod.py."},
            {"id": "y-a-02", "description": "Edit tools/y/mod.py.",
             "depends_on_task_id": "y-a-01"},
        ],
        conn=_FakeConn(rows=[]),
    )
    assert findings == []


def test_check_batch_does_not_report_preexisting_board_collisions():
    """A batch must not be blamed for two rows that were already fighting."""
    board = _FakeConn(rows=[
        {"id": "x-a-01", "title": "", "description": "Edit tools/x/mod.py.",
         "status": "scheduled", "depends_on_task_id": None},
        {"id": "x-a-02", "title": "", "description": "Edit tools/x/mod.py.",
         "status": "scheduled", "depends_on_task_id": None},
    ])
    findings = lc.check_batch(
        [{"id": "z-a-01", "description": "Edit tools/z/other.py."}], conn=board
    )
    assert findings == []


def test_check_batch_fails_open_on_an_unreadable_board():
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("no board")

        def close(self):
            pass

    assert lc.check_batch([{"id": "x-b-01", "description": "Edit tools/x/mod.py."}],
                          conn=_Broken()) == []


def test_check_batch_ignores_an_empty_batch():
    assert lc.check_batch([]) == []


def test_report_fails_open_and_says_it_was_not_measured():
    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("no board")

        def close(self):
            pass

    result = lc.report(conn=_Broken())
    assert result["measured"] is False
    assert result["conflicts"] == []


# ────────────────────────────────────────────────────────────────────────────
# The seeder calls it, before any insert, and never breaks on it
# ────────────────────────────────────────────────────────────────────────────

class _NoBoardWrite(Exception):
    """Sentinel: raised by the first thing create_tasks does that touches the DB."""


def test_task_factory_runs_the_lane_check_before_any_board_write(monkeypatch):
    """Order matters: a refusal added later must not be able to half-land a batch.

    ``init_kanban_tables`` is the first DB call in ``create_tasks``; making it
    raise proves the lane check already ran, and keeps this test off the live
    board entirely.
    """
    import tools.kanban.init_db as init_db
    import tools.kanban.task_factory as tf

    seen = {}

    def _fake_check_batch(specs, **kw):
        seen["specs"] = list(specs)
        raise RuntimeError("boom")   # a broken check must not break seeding

    monkeypatch.setattr(lc, "check_batch", _fake_check_batch)
    monkeypatch.setattr(init_db, "init_kanban_tables",
                        lambda *a, **k: (_ for _ in ()).throw(_NoBoardWrite()))

    with pytest.raises(_NoBoardWrite):
        tf.create_tasks([{"id": "x-b-01", "title": "t",
                          "description": "Edit tools/x/m.py."}])
    assert seen["specs"][0]["id"] == "x-b-01"
