# CUI // SP-CTI
"""Dispatch refuses a card whose in-flight sibling owns a file it declares (mfx-sib-01).

RED AT THE MERGE BASE: ``tools.kanban.sibling_overlap`` does not exist there, so
every test in this module fails at import. The behaviour under test — a hold at
the DISPATCH door rather than at the merge door — has no earlier implementation
to fall back to.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban import sibling_overlap as so  # noqa: E402


# The shapes below are the rmf-ui cards' own prose, trimmed to the sentence the
# parser reads. Two cards adding a page to the SAME canvas blueprint.
_RMF_UI_11 = (
    "Migrate /compliance onto the Boundary canvas",
    "Add a new route in tools/boundary_canvas/blueprint.py rendering a new "
    "template bdc_canvas/compliance.html, include includes/iqe_query_widget.html, "
    "and add the page to the Pages line in .claude/commands/start.md.",
)
_RMF_UI_16 = (
    "Migrate /ai-accountability onto the Security canvas",
    "Add a new route in tools/security_canvas/blueprint.py rendering a new "
    "template sdc_canvas/ai_accountability.html, include "
    "includes/iqe_query_widget.html, and add the page to the Pages line in "
    ".claude/commands/start.md.",
)
_RMF_UI_09 = (
    "Migrate /compliance-debt onto the Boundary canvas",
    "Add a new route in tools/boundary_canvas/blueprint.py rendering a new "
    "template bdc_canvas/compliance_debt.html.",
)
_UNRELATED = (
    "Measure cache effectiveness per provider",
    "Create a new module tools/cache_savings/by_provider.py reading ai_telemetry.",
)


def _card(task_id, shape, status="scheduled"):
    return {"id": task_id, "title": shape[0], "description": shape[1],
            "status": status}


class _FakeConn:
    """Answers the one query ``_in_flight_by_epic`` makes."""

    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, sql, params=()):
        statuses = set(params)
        self.last_sql = sql
        matched = [r for r in self._rows if r.get("status") in statuses]
        return _FakeCursor(matched)

    def close(self):
        self.closed = True


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


# ── the predicate ─────────────────────────────────────────────────────────────

class TestEpic:
    def test_epic_of_strips_the_trailing_number(self):
        assert so.epic_of("rmf-ui-16") == "rmf-ui"
        assert so.epic_of("mfx-sib-01") == "mfx-sib"

    def test_an_opaque_machine_id_has_no_epic(self):
        # `task-<hex>` is what the dashboard's create-task API generates. It has
        # no siblings by construction, so it must never be held.
        assert so.epic_of("task-3bc9eb0918") is None
        assert so.epic_of("") is None
        assert so.epic_of("nodashes") is None


class TestOverlap:
    def test_two_cards_on_the_same_canvas_blueprint_overlap(self):
        shared = so.overlap(*_RMF_UI_11, *_RMF_UI_09)
        assert "tools/boundary_canvas/blueprint.py" in shared

    def test_additive_paths_are_never_the_overlap(self):
        # Both cards declare `.claude/commands/start.md`, which is a coordination
        # path in the SAME list pr_watcher's merge-door guard reads. Serializing
        # on it would serialize every card that adds a page.
        shared = so.overlap(*_RMF_UI_11, *_RMF_UI_16)
        assert ".claude/commands/start.md" not in shared
        assert "tools/boundary_canvas/blueprint.py" not in shared

    def test_a_card_declaring_nothing_shared_does_not_overlap(self):
        assert so.overlap(*_RMF_UI_11, *_UNRELATED) == set()

    def test_the_additive_filter_uses_the_merge_doors_own_predicate(self):
        # Not a second copy: the same function pr_watcher re-exports as
        # `_is_additive_path`. If these two ever diverge, the dispatch door and
        # the merge door disagree about what a collision is.
        from tools.ci import pr_watcher

        assert pr_watcher._is_additive_path is so.is_coordination_path


# ── the admission ─────────────────────────────────────────────────────────────

class TestFindHolds:
    def test_holds_a_card_behind_an_in_flight_sibling(self):
        conn = _FakeConn([_card("rmf-ui-09", _RMF_UI_09, "in_progress")])
        holds = so.find_holds([_card("rmf-ui-11", _RMF_UI_11)], conn=conn)
        assert "rmf-ui-11" in holds
        hold = holds["rmf-ui-11"]
        assert hold.sibling_id == "rmf-ui-09"
        assert hold.sibling_status == "in_progress"
        assert "tools/boundary_canvas/blueprint.py" in hold.shared_paths
        # The reason is stated, not a bare "held".
        assert "rmf-ui-09" in hold.reason
        assert "tools/boundary_canvas/blueprint.py" in hold.reason

    def test_a_pr_opened_sibling_still_holds(self):
        # The branch exists and its diff is written; a second card built on top
        # of it collides exactly as hard as one racing an in_progress sibling.
        conn = _FakeConn([_card("rmf-ui-09", _RMF_UI_09, "pr_opened")])
        holds = so.find_holds([_card("rmf-ui-11", _RMF_UI_11)], conn=conn)
        assert "rmf-ui-11" in holds

    def test_a_scheduled_sibling_does_not_hold(self):
        # Two waiting cards holding each other is a deadlock, not serialization.
        conn = _FakeConn([_card("rmf-ui-09", _RMF_UI_09, "scheduled")])
        assert so.find_holds([_card("rmf-ui-11", _RMF_UI_11)], conn=conn) == {}

    def test_a_done_sibling_releases_the_hold(self):
        conn = _FakeConn([_card("rmf-ui-09", _RMF_UI_09, "done")])
        assert so.find_holds([_card("rmf-ui-11", _RMF_UI_11)], conn=conn) == {}

    def test_a_different_epic_is_not_a_sibling(self):
        # cef-bck-01 and rmf-ui-11 could share a file and still be unrelated work;
        # the rule is scoped to one epic on purpose, because that is the
        # population the survey measured.
        conn = _FakeConn([_card("cef-bck-01", _RMF_UI_09, "in_progress")])
        assert so.find_holds([_card("rmf-ui-11", _RMF_UI_11)], conn=conn) == {}

    def test_an_unrelated_card_in_the_same_epic_is_not_held(self):
        conn = _FakeConn([_card("rmf-ui-09", _RMF_UI_09, "in_progress")])
        assert so.find_holds([_card("rmf-ui-40", _UNRELATED)], conn=conn) == {}

    def test_a_card_declaring_no_artifact_is_never_held(self):
        # declared_artifacts reads prose and under-approximates. A card that
        # names no path is unmeasurable, and unmeasurable must not mean held.
        conn = _FakeConn([_card("rmf-ui-09", _RMF_UI_09, "in_progress")])
        blank = {"id": "rmf-ui-11", "title": "tidy up", "description": ""}
        assert so.find_holds([blank], conn=conn) == {}

    def test_an_unreadable_board_fails_open(self):
        class _Broken:
            def execute(self, *a, **k):
                raise RuntimeError("board unreachable")

            def close(self):
                pass

        assert so.find_holds([_card("rmf-ui-11", _RMF_UI_11)], conn=_Broken()) == {}

    def test_no_candidates_asks_the_board_nothing(self):
        assert so.find_holds([], conn=None) == {}


# ── the dispatch door ─────────────────────────────────────────────────────────

class TestDispatchAdmission:
    """``_get_due_tasks`` must drop a held task BEFORE truncating to slots."""

    @staticmethod
    def _reflex():
        return importlib.import_module("tools.genesis.reflexes.kanban")

    def test_held_task_is_dropped_from_the_dispatch_window(self, monkeypatch):
        reflex = self._reflex()
        held = _card("rmf-ui-11", _RMF_UI_11)
        keep = _card("rmf-ui-40", _UNRELATED)
        hold = so.Hold(
            task_id="rmf-ui-11", sibling_id="rmf-ui-09",
            sibling_status="in_progress", epic="rmf-ui",
            shared_paths=("tools/boundary_canvas/blueprint.py",),
        )
        monkeypatch.setattr(
            "tools.kanban.sibling_overlap.find_holds",
            lambda tasks, conn=None: {"rmf-ui-11": hold},
        )
        recorded = []
        monkeypatch.setattr(
            reflex, "_record_status_transition",
            lambda *a, **k: recorded.append((a, k)),
        )
        reflex._SIBLING_HOLD_RECORDED.clear()

        kept = reflex._drop_sibling_overlapped([held, keep])

        assert [t["id"] for t in kept] == ["rmf-ui-40"]
        # The WAIT is recorded with its reason, and the card is NOT moved.
        assert len(recorded) == 1
        args, kwargs = recorded[0]
        assert args[0] == "rmf-ui-11"
        assert args[1] == "scheduled" and args[2] == "scheduled"
        assert kwargs["actor"] == "sibling-serializer"
        assert "rmf-ui-09" in kwargs["reason"]

    def test_the_wait_row_is_written_once_per_episode_not_once_per_cycle(
        self, monkeypatch
    ):
        # A hold that lasts an afternoon is ONE wait. Writing it every 60s would
        # bury the transition log under a fact that has not changed.
        reflex = self._reflex()
        hold = so.Hold(
            task_id="rmf-ui-11", sibling_id="rmf-ui-09",
            sibling_status="in_progress", epic="rmf-ui",
            shared_paths=("tools/boundary_canvas/blueprint.py",),
        )
        monkeypatch.setattr(
            "tools.kanban.sibling_overlap.find_holds",
            lambda tasks, conn=None: {"rmf-ui-11": hold},
        )
        recorded = []
        monkeypatch.setattr(
            reflex, "_record_status_transition",
            lambda *a, **k: recorded.append(a[0]),
        )
        reflex._SIBLING_HOLD_RECORDED.clear()

        tasks = [_card("rmf-ui-11", _RMF_UI_11)]
        for _ in range(5):
            reflex._drop_sibling_overlapped(list(tasks))

        assert recorded == ["rmf-ui-11"]

    def test_the_toggle_stands_it_down(self, monkeypatch):
        reflex = self._reflex()
        monkeypatch.setenv("KANBAN_SERIALIZE_SIBLINGS", "0")
        monkeypatch.setattr(
            "tools.kanban.sibling_overlap.find_holds",
            lambda tasks, conn=None: (_ for _ in ()).throw(
                AssertionError("must not be asked when disabled")),
        )
        tasks = [_card("rmf-ui-11", _RMF_UI_11)]
        assert reflex._drop_sibling_overlapped(list(tasks)) == tasks

    def test_it_is_on_by_default_and_config_says_so(self, monkeypatch):
        import yaml

        monkeypatch.delenv("KANBAN_SERIALIZE_SIBLINGS", raising=False)
        reflex = self._reflex()
        assert reflex._serialize_overlapping_siblings_enabled() is True
        cfg = yaml.safe_load(
            (_REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
        kanban_cfg = cfg["reflexes"]["kanban"]
        assert kanban_cfg["serialize_overlapping_siblings"] is True

    def test_a_broken_predicate_fails_open(self, monkeypatch):
        reflex = self._reflex()
        monkeypatch.delenv("KANBAN_SERIALIZE_SIBLINGS", raising=False)
        monkeypatch.setattr(
            "tools.kanban.sibling_overlap.find_holds",
            lambda tasks, conn=None: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        tasks = [_card("rmf-ui-11", _RMF_UI_11)]
        assert reflex._drop_sibling_overlapped(list(tasks)) == tasks

    def test_run_resets_the_hold_count_to_unmeasured_each_cycle(self):
        # A cycle that never looked is not a cycle that found nothing. `run()`
        # clears the count FIRST, so a stood-down or at-capacity cycle reports
        # null rather than the previous cycle's list — and `sibling_holds` in the
        # payload is None, never 0, in that case.
        import inspect

        reflex = self._reflex()
        source = inspect.getsource(reflex.run)
        assert "_LAST_SIBLING_HOLDS = None" in source
        reset_at = source.index("_LAST_SIBLING_HOLDS = None")
        # ... before anything that could dispatch.
        assert reset_at < source.index("_get_due_tasks")

        payload = inspect.getsource(reflex.run)
        assert "None if _LAST_SIBLING_HOLDS is None else len(_LAST_SIBLING_HOLDS)" in payload

    def test_a_measured_cycle_with_no_holds_reports_an_empty_list(self, monkeypatch):
        reflex = self._reflex()
        monkeypatch.delenv("KANBAN_SERIALIZE_SIBLINGS", raising=False)
        monkeypatch.setattr(
            "tools.kanban.sibling_overlap.find_holds", lambda tasks, conn=None: {})
        monkeypatch.setattr(reflex, "_LAST_SIBLING_HOLDS", None)

        reflex._drop_sibling_overlapped([_card("rmf-ui-11", _RMF_UI_11)])

        assert reflex._LAST_SIBLING_HOLDS == []

    def test_a_failed_measurement_reports_null_not_zero(self, monkeypatch):
        reflex = self._reflex()
        monkeypatch.delenv("KANBAN_SERIALIZE_SIBLINGS", raising=False)
        monkeypatch.setattr(reflex, "_LAST_SIBLING_HOLDS", [])
        monkeypatch.setattr(
            "tools.kanban.sibling_overlap.find_holds",
            lambda tasks, conn=None: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        reflex._drop_sibling_overlapped([_card("rmf-ui-11", _RMF_UI_11)])

        assert reflex._LAST_SIBLING_HOLDS is None

    def test_the_admission_runs_before_the_slot_truncation(self):
        # Ordering is the whole point of filtering here rather than at dispatch:
        # a held task that keeps its place consumes a slot it can never use, and
        # everything behind it starves. Same defect _drop_respawn_guarded fixed.
        source = (
            _REPO_ROOT / "tools" / "genesis" / "reflexes" / "kanban.py"
        ).read_text(encoding="utf-8")
        drop_at = source.index("result = _drop_sibling_overlapped(result)")
        cap_at = source.index("result = result[:available_slots]")
        assert drop_at < cap_at


# ── the survey ────────────────────────────────────────────────────────────────

class TestSurvey:
    def test_an_empty_window_is_unmeasurable_never_a_clean_zero(self):
        class _EmptyConn:
            def execute(self, sql, params=()):
                return _FakeCursor([])

            def close(self):
                pass

        result = so.survey(window_days=30, conn=_EmptyConn())
        assert result["status"] == "unmeasurable"
        # A rate over an empty denominator is None, never 0.0 (rem-hyg-13).
        assert result["fire_rate_pct"] is None
        assert result["held"] is None

    def test_the_survey_replays_the_shipped_predicate(self):
        # Not a second copy of the rule: if the survey computed its own overlap
        # it would prove nothing about the rule that ships.
        import inspect

        source = inspect.getsource(so.survey)
        assert "overlap(" in source
