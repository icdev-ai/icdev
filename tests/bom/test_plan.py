# CUI // SP-CTI
"""What the evidence implies for the plan.

ICDEV does not schedule — Compass does, and these tests do not pretend otherwise.
They cover the half ICDEV owns: reading a tracker without damaging it, turning
findings into work, and wiring the dependency nobody wrote down.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from tools.bom.findings import Evidence, Finding
from tools.bom.plan import (
    TRACKER_HEADERS,
    WAVE_DECIDE,
    WAVE_ZERO,
    Task,
    gantt_slide,
    link_decisions_to_approval,
    phases_slide,
    read_tracker,
    tasks_from_findings,
    tracker_workbook,
)


def _tracker(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tasks"
    ws.append(TRACKER_HEADERS)
    for r in rows:
        ws.append(r)
    p = tmp_path / "tracker.xlsx"
    wb.save(p)
    return p


_ROWS = [
    ["Phase 1: Planning", "2.1", "Hardware Salvage Audit", "Inventory the fleet",
     "", "Started", "Larry", "2026-07-09 00:00:00", ""],
    ["Phase 1: Planning", "2.2", "Generate Bill of Materials", "Draft the BOM",
     "2.1", "Started", "Team", "TBD", "Pending discussion"],
    ["Phase 1: Planning", "2.4", "Get Leadership Approval", "Submit to finance",
     "2.2", "Not Started", "TBD", "TBD", ""],
]


class TestTheirPlanIsNotOursToRewrite:
    def test_every_task_is_carried_over(self, tmp_path):
        """A planning tool that quietly loses somebody's work is worse than no
        planning tool."""
        tasks = read_tracker(str(_tracker(tmp_path, _ROWS)))
        assert [t.task_id for t in tasks] == ["2.1", "2.2", "2.4"]
        assert tasks[1].comment == "Pending discussion"

    def test_dependencies_survive(self, tmp_path):
        tasks = read_tracker(str(_tracker(tmp_path, _ROWS)))
        assert tasks[1].deps == ["2.1"]

    def test_a_tbd_due_date_stays_tbd(self, tmp_path):
        """It is not a missing date. It is a date nobody has decided, and
        overwriting it with a guess would be inventing a commitment."""
        tasks = read_tracker(str(_tracker(tmp_path, _ROWS)))
        assert tasks[1].due_date == "TBD"

    def test_a_real_date_is_normalised_not_mangled(self, tmp_path):
        tasks = read_tracker(str(_tracker(tmp_path, _ROWS)))
        assert tasks[0].due_date == "2026-07-09"

    def test_the_round_trip_keeps_the_columns_compass_parses(self, tmp_path):
        tasks = read_tracker(str(_tracker(tmp_path, _ROWS)))
        wb = openpyxl.load_workbook(io.BytesIO(tracker_workbook(tasks)))
        assert [c.value for c in wb["Tasks"][1]] == TRACKER_HEADERS


class TestFindingsBecomeWork:
    def _findings(self):
        return [
            Finding(finding_type="intra_doc_double_count", kind="defect",
                    severity="critical", title="Counted twice",
                    evidence=[Evidence("bom.xlsx", "Networking", "A23", "")]),
            Finding(finding_type="unpriced_line_zeroed", kind="defect",
                    severity="high", title="Priced at nothing",
                    evidence=[Evidence("bom.xlsx", "BOM", "E9", "")]),
        ]

    def test_a_finding_becomes_a_task_that_cites_it(self):
        """A task nobody can trace is a task nobody will do."""
        tasks = tasks_from_findings(self._findings())
        dbl = next(t for t in tasks if "double-counted" in t.name)
        assert "bom.xlsx" in dbl.comment
        assert "A23" in dbl.comment

    def test_the_source_of_record_task_always_exists(self):
        """Whether or not a finding produced it. While several documents each claim
        to price the project there is no number to approve, and that is true even
        when nothing else is wrong."""
        tasks = tasks_from_findings([])
        assert any("source of record" in t.name for t in tasks)

    def test_owned_hardware_produces_a_start_now_phase(self):
        tasks = tasks_from_findings([], owned_units=12)
        wave0 = [t for t in tasks if t.phase == WAVE_ZERO]
        assert len(wave0) == 3
        assert any("12 owned servers" in t.name for t in wave0)
        assert any("refresh reserve" in t.name for t in wave0)

    def test_no_owned_hardware_means_no_start_now_phase(self):
        assert not [t for t in tasks_from_findings([]) if t.phase == WAVE_ZERO]

    def test_it_does_not_duplicate_the_procurement_task_they_already_have(self):
        """A planning tool whose first act is to duplicate the work already in the
        plan is not helping. Two tasks for one purchase order makes the schedule
        lie about how much work there is."""
        tasks = tasks_from_findings([], owned_units=12)
        assert not any("long-lead" in t.name.lower() for t in tasks)
        assert not any("place the" in t.name.lower() for t in tasks)


class TestTheDependencyNobodyWroteDown:
    """Leadership cannot approve a number that does not exist yet."""

    def test_the_approval_task_is_gated_on_the_decisions(self, tmp_path):
        existing = read_tracker(str(_tracker(tmp_path, _ROWS)))
        derived = tasks_from_findings([], owned_units=12)

        touched = link_decisions_to_approval(derived, existing)

        assert touched == ["2.4"]
        approval = next(t for t in existing if t.task_id == "2.4")
        decisions = [t.task_id for t in derived if t.phase == WAVE_DECIDE]
        assert set(decisions) <= set(approval.deps)
        # Their own dependency is untouched.
        assert "2.2" in approval.deps

    def test_it_says_WHY_in_the_comment(self, tmp_path):
        existing = read_tracker(str(_tracker(tmp_path, _ROWS)))
        link_decisions_to_approval(tasks_from_findings([]), existing)
        approval = next(t for t in existing if t.task_id == "2.4")
        assert "does not exist yet" in approval.comment

    def test_it_reports_what_it_rewired(self, tmp_path):
        """Silently editing somebody's plan is not something to do without saying
        so."""
        existing = read_tracker(str(_tracker(tmp_path, _ROWS)))
        assert link_decisions_to_approval(tasks_from_findings([]), existing) == ["2.4"]

    def test_a_plan_with_no_approval_task_is_left_alone(self, tmp_path):
        rows = [["P1", "1.1", "Build a thing", "d", "", "Not Started", "X", "TBD", ""]]
        existing = read_tracker(str(_tracker(tmp_path, rows)))
        assert link_decisions_to_approval(tasks_from_findings([]), existing) == []
        assert existing[0].deps == []


class TestSlidesFromCompassSchedule:
    """The schedule is COMPASS's answer. These slides render it; they do not
    compute it, and they must never assert anything it did not say."""

    _SCHED = {
        "anchor": "2026-07-13",
        "project_end": "2026-10-23",
        "tasks": {
            "0.1": {"start": "2026-07-13", "end": "2026-07-14",
                    "critical": False, "slack_days": 20},
            "2.1": {"start": "2026-07-13", "end": "2026-07-31",
                    "critical": True, "slack_days": 0},
        },
    }
    _TASKS = [
        Task(phase=WAVE_DECIDE, task_id="0.1", name="Decide"),
        Task(phase="Phase 1", task_id="2.1", name="Audit"),
    ]

    def test_the_gantt_marks_the_critical_path(self):
        """A Gantt that does not say what is on the critical path is a picture of a
        schedule rather than a schedule."""
        slide = gantt_slide(self._SCHED, self._TASKS, weeks=6)
        rows = slide["bullets"]["rows"]
        labels = {r[0] for r in rows}
        assert any(x.startswith("! ") and "2.1" in x for x in labels)
        assert not any(x.startswith("! ") and "0.1" in x for x in labels)

    def test_a_bar_spans_the_weeks_the_task_runs(self):
        slide = gantt_slide(self._SCHED, self._TASKS, weeks=6)
        audit = next(r for r in slide["bullets"]["rows"] if "2.1" in r[0])
        filled = [i for i, c in enumerate(audit[1:]) if c]
        assert filled == [0, 1, 2]   # 13 Jul -> 31 Jul spans three weeks

    def test_the_phases_slide_counts_what_compass_said(self):
        slide = phases_slide(self._SCHED, self._TASKS)
        rows = {r[0]: r for r in slide["bullets"]["rows"]}
        assert rows["Phase 1"][4] == "1"          # one critical task
        assert rows[WAVE_DECIDE[:36]][4] == "—"   # none

    def test_no_schedule_means_no_slide_rather_than_an_empty_one(self):
        assert gantt_slide({}, self._TASKS) is None
        assert phases_slide({}, self._TASKS) is None


class TestCompassNameGuard:
    def test_a_non_ascii_project_name_is_made_safe(self):
        """UPSTREAM BUG (compass PR #16): the project name goes into a
        Content-Disposition header, which is latin-1, so an em dash 500s the export.
        Guarded here until that lands — and it is a workaround, not a fix.
        """
        from tools.integrations.compass_client import _ascii_safe

        assert _ascii_safe("IRAD Lab — reconciled") == "IRAD Lab - reconciled"
        _ascii_safe("Réseau").encode("ascii")
        assert _ascii_safe("") == "Project"

    @pytest.mark.parametrize("name", ["Renewal – Phase 2", "Tom’s Lab", "Проект"])
    def test_the_result_always_survives_a_latin_1_header(self, name):
        from tools.integrations.compass_client import _ascii_safe

        _ascii_safe(name).encode("latin-1")
