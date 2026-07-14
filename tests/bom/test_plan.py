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
    apply_durations,
    apply_fixed_dates,
    check_horizons,
    dates_slide,
    digital_twin_tasks,
    gantt_slide,
    horizon_summary,
    link_decisions_to_approval,
    load_plan_config,
    normalize_deps,
    phases_slide,
    read_tracker,
    resolve_tbd,
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


_CFG = {
    "anchor": "2026-07-13",
    "plan_to": "late",
    "ranges": {
        "procurement": {"early_weeks": 4, "late_weeks": 6, "note": "PO execution."},
    },
    "fixed": {"2.3": {"due": "2026-07-15", "note": "Committed date."}},
    "durations": {"default": 5, "by_name": {"hardware salvage audit": 3}},
    "range_tasks": {"requisition & procurement": "procurement"},
    "default_horizon": "lab",
    "horizons": {
        "lab": {"label": "Lab", "must_finish_by": "2026-12-31", "covers": "*"},
        "dt_2027": {"label": "DT", "must_finish_by": "2027-12-31",
                    "covers": "phase:Phase 9"},
    },
    "digital_twin": {
        "unfunded_note": "Not in any BOM.",
        "waves": [
            {"phase": "Phase 8: Digital Twin (2026)", "after": "final security audit",
             "tasks": [{"name": "ICDEV digital twin", "duration_days": 15}]},
            {"phase": "Phase 9: Digital Twin (2027)", "after": "icdev digital twin",
             "tasks": [{"name": "Forward Networks", "duration_days": 20}]},
        ],
    },
}


class TestDurations:
    """A range is two statements: what somebody hopes for, and what they will not
    be surprised by. Which one the baseline commits to is a decision, not a
    detail."""

    def test_a_range_task_takes_the_late_end_by_default(self):
        t = Task(phase="P", task_id="2.5", name="Requisition & Procurement")
        apply_durations([t], _CFG)
        assert t.duration_days == 30      # 6 weeks x 5 workdays

    def test_planning_to_the_early_end_is_one_config_line(self):
        t = Task(phase="P", task_id="2.5", name="Requisition & Procurement")
        apply_durations([t], {**_CFG, "plan_to": "early"})
        assert t.duration_days == 20      # 4 weeks

    def test_the_upside_is_reported_not_banked(self):
        """The optimistic figure is stated out loud rather than quietly assumed."""
        t = Task(phase="P", task_id="2.5", name="Requisition & Procurement")
        out = apply_durations([t], _CFG)
        assert out["upside_days"] == 10   # the 2-week spread

    def test_a_named_duration_wins(self):
        t = Task(phase="P", task_id="2.1", name="Hardware Salvage Audit")
        apply_durations([t], _CFG)
        assert t.duration_days == 3

    def test_an_unestimated_task_is_reported_as_such(self):
        """It gets the default so the schedule can run — and it is NAMED, because
        an unestimated task is a question, not a five-day task."""
        t = Task(phase="P", task_id="9.9", name="Something nobody sized")
        out = apply_durations([t], _CFG)
        assert t.duration_days == 5
        assert out["unestimated"] == ["9.9"]

    def test_a_duration_already_set_is_left_alone(self):
        t = Task(phase="P", task_id="0.1", name="Decide", duration_days=1)
        apply_durations([t], _CFG)
        assert t.duration_days == 1


class TestResolvingTBD:
    _SCHED = {
        "anchor": "2026-07-13",
        "tasks": {
            "2.2": {"end": "2026-08-14", "critical": True, "slack_days": 0},
            "2.3": {"end": "2026-09-01", "critical": False, "slack_days": 9},
        },
    }

    def test_a_tbd_becomes_the_computed_date(self):
        t = Task(phase="P", task_id="2.2", name="BOM", due_date="TBD")
        resolve_tbd([t], self._SCHED, _CFG)
        assert t.due_date == "2026-08-14"

    def test_it_says_where_the_date_came_from(self):
        """A date that appeared from nowhere is a date nobody will defend."""
        t = Task(phase="P", task_id="2.2", name="BOM", due_date="TBD")
        resolve_tbd([t], self._SCHED, _CFG)
        assert "critical path" in t.comment

    def test_a_date_a_human_committed_to_is_never_overwritten(self):
        """2.3 is fixed at 15 Jul. The scheduler says 1 Sep. The promise wins —
        a computed date is a projection; a promise made in a meeting is not."""
        t = Task(phase="P", task_id="2.3", name="Present to Tom", due_date="TBD")
        apply_fixed_dates([t], _CFG)
        resolve_tbd([t], self._SCHED, _CFG)
        assert t.due_date == "2026-07-15"

    def test_an_existing_real_date_is_left_alone(self):
        t = Task(phase="P", task_id="2.2", name="BOM", due_date="2026-07-09")
        out = resolve_tbd([t], self._SCHED, _CFG)
        assert t.due_date == "2026-07-09"
        assert out["resolved"] == []

    def test_a_task_the_scheduler_never_saw_stays_tbd(self):
        """Silence is not a date."""
        t = Task(phase="P", task_id="9.9", name="Orphan", due_date="TBD")
        resolve_tbd([t], self._SCHED, _CFG)
        assert t.due_date == "TBD"


class TestDependenciesWrittenInEnglish:
    """People write "All Phase 3". A scheduler looks for a task with that name,
    finds none, and schedules the work on day one with no predecessors."""

    def _tasks(self):
        return [
            Task(phase="Phase 3: Network", task_id="4.1", name="Core switch"),
            Task(phase="Phase 3: Network", task_id="4.2", name="Firewall"),
            Task(phase="Phase 5: Readiness", task_id="6.6", name="Final Security Audit",
                 deps=["All Phase 3"]),
        ]

    def test_a_phase_reference_expands_to_its_tasks(self):
        tasks = self._tasks()
        out = normalize_deps(tasks)
        assert tasks[2].deps == ["4.1", "4.2"]
        assert out["expanded"][0]["wrote"] == "All Phase 3"

    def test_it_matches_the_phase_NAME_not_the_task_id_prefix(self):
        """The tracker numbers phases and tasks differently: "Phase 3" holds tasks
        4.x. Bucketing on the id prefix would silently produce an empty set."""
        tasks = self._tasks()
        normalize_deps(tasks)
        assert tasks[2].deps == ["4.1", "4.2"]

    def test_a_real_task_id_is_untouched(self):
        tasks = [
            Task(phase="P", task_id="2.1", name="A"),
            Task(phase="P", task_id="2.2", name="B", deps=["2.1"]),
        ]
        normalize_deps(tasks)
        assert tasks[1].deps == ["2.1"]

    def test_a_dependency_it_cannot_understand_is_REPORTED_not_dropped(self):
        """Deleting a constraint on somebody's behalf, silently, is the worst
        available option."""
        tasks = [Task(phase="P", task_id="1.1", name="A", deps=["ask Dave"])]
        out = normalize_deps(tasks)
        assert out["unresolved"] == [{"task_id": "1.1", "dep": "ask Dave"}]
        assert tasks[0].deps == []

    def test_a_task_never_depends_on_itself(self):
        tasks = [
            Task(phase="Phase 3: Network", task_id="4.1", name="A",
                 deps=["All Phase 3"]),
            Task(phase="Phase 3: Network", task_id="4.2", name="B"),
        ]
        normalize_deps(tasks)
        assert tasks[0].deps == ["4.2"]


class TestTheDigitalTwinIsOnNoBOM:
    def test_every_dt_task_says_it_is_unfunded(self):
        """That is not a caveat on the tasks. It is the reason they exist."""
        dt = digital_twin_tasks([], _CFG)
        assert dt and all("Not in any BOM" in t.comment for t in dt)

    def test_a_wave_hangs_off_the_task_it_named(self):
        prior = [Task(phase="Phase 5", task_id="6.6", name="Final Security Audit")]
        dt = digital_twin_tasks(prior, _CFG)
        assert dt[0].deps == ["6.6"]

    def test_the_second_wave_waits_on_the_first(self):
        dt = digital_twin_tasks([], _CFG)
        assert dt[1].deps == [dt[0].task_id]

    def test_a_missing_anchor_floats_the_wave_rather_than_inventing_one(self):
        """Pointing at a task id nobody has is worse than having no predecessor:
        the scheduler drops it either way, but only one of those is visible."""
        dt = digital_twin_tasks([], _CFG)
        assert dt[0].deps == []

    def test_task_ids_come_from_the_phase_number(self):
        dt = digital_twin_tasks([], _CFG)
        assert [t.task_id for t in dt] == ["8.1", "9.1"]


class TestHorizonsAreCheckedNotEnforced:
    """A planner that compresses a schedule to hit a date somebody wanted has not
    made the date achievable. It has only moved when you find out."""

    _SCHED = {
        "anchor": "2026-07-13",
        "tasks": {
            "6.2": {"end": "2027-01-01"},          # lab horizon: one day over
            "9.1": {"end": "2027-03-01"},          # dt_2027 horizon: fine
        },
    }
    _TASKS = [
        Task(phase="Phase 5: Readiness", task_id="6.2", name="Cyber Range"),
        Task(phase="Phase 9: Digital Twin (2027)", task_id="9.1", name="Forward Networks"),
    ]

    def test_a_breach_is_reported_with_the_size_of_the_miss(self):
        out = check_horizons(self._TASKS, self._SCHED, _CFG)
        assert len(out) == 1
        assert out[0]["task_id"] == "6.2"
        assert out[0]["over_by_days"] == 1

    def test_a_later_horizon_claims_its_own_phase(self):
        """Phase 9 is 2027 work. Judging it against the 2026 date would invent a
        failure that nobody asked for."""
        assert all(b["task_id"] != "9.1"
                   for b in check_horizons(self._TASKS, self._SCHED, _CFG))

    def test_the_summary_says_which_horizons_hold(self):
        rows = {h["horizon"]: h for h in horizon_summary(self._TASKS, self._SCHED, _CFG)}
        assert rows["lab"]["meets"] is False
        assert rows["dt_2027"]["meets"] is True

    def test_no_schedule_means_no_verdict_rather_than_a_pass(self):
        assert check_horizons(self._TASKS, {}, _CFG) == []

    def test_the_working_dates_slide_states_the_miss_and_the_upside(self):
        slide = dates_slide(self._SCHED, self._TASKS, _CFG, upside_days=30,
                            audience="working")
        assert "1 day(s) past target" in slide["speaker_notes"]
        assert "30 working days come back" in slide["speaker_notes"]
        assert any(r[-1] == "over" for r in slide["bullets"]["rows"])

    def test_the_leadership_dates_slide_does_not_put_the_miss_on_the_wall(self):
        """A column headed "over" invites a conversation about a one-day slip in a
        plan that runs eighteen months. The miss is not hidden — it is in the
        working deck, the tracker, and the speaker notes, which is where a
        presenter can answer it if asked."""
        slide = dates_slide(self._SCHED, self._TASKS, _CFG, upside_days=30,
                            audience="leadership")
        assert "Verdict" not in slide["bullets"]["headers"]
        assert not any("over" in str(c) for r in slide["bullets"]["rows"] for c in r)
        # Still in the notes. The presenter is not being kept in the dark.
        assert "past target" in slide["speaker_notes"]


class TestTheShippedPlanConfigIsUsable:
    """The real args/bom_plan.yaml, not a fixture. A config file that no longer
    parses is a config file nobody notices is broken."""

    def test_it_loads(self):
        cfg = load_plan_config()
        assert cfg["anchor"] and cfg["plan_to"] in ("late", "early")

    def test_every_range_task_names_a_range_that_exists(self):
        cfg = load_plan_config()
        for name, key in (cfg.get("range_tasks") or {}).items():
            assert key in cfg["ranges"], f"{name} points at a range that is not there"

    def test_every_null_duration_is_covered_by_a_range(self):
        """A null means "ask the ranges". If no range claims it, the task silently
        takes the default and the range you wrote down does nothing."""
        cfg = load_plan_config()
        rt = {k.lower() for k in (cfg.get("range_tasks") or {})}
        for name, days in (cfg["durations"]["by_name"] or {}).items():
            if days is None:
                assert name.lower() in rt, f"{name!r} is null but no range claims it"

    def test_every_horizon_has_a_date(self):
        cfg = load_plan_config()
        for key, spec in cfg["horizons"].items():
            assert spec.get("must_finish_by"), f"horizon {key} has no date"


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
