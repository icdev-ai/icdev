# CUI // SP-CTI
"""What the evidence implies for the plan.

ICDEV does not schedule. Compass does, and Compass's scheduler was modelled on
exactly the kind of lab-tracker spreadsheet this programme already keeps — phases
grouping tasks, hierarchical ids ("2.3"), free-text assignees, due dates that are
frequently "TBD", semicolon-separated dependencies. It already computes the
forward pass, the slack, the critical path and the dependency cycles, and exports
a round-trip-stable workbook.

Writing a second scheduler here, beside a working one, inside the same ecosystem,
behind the same licence, would be the exact duplication the integration exists to
prevent. So the division is:

    ICDEV     reads the documents, reconciles the bill of materials, finds what
              is wrong with it, and works out what that MEANS for the plan.
    COMPASS   schedules.

This module is the first half. It turns findings into tasks, and it does one thing
that is a judgement rather than an arithmetic:

**IT PULLS DEVELOPMENT TO THE FRONT.**

The existing tracker gates every piece of software work behind Phase 5 — the
sandbox, the model-training environment, the cyber range — so nothing gets built
until facilities, network and virtualization are all finished. But hardware the
programme ALREADY OWNS can carry a virtualized environment on day one. That does
not need a procurement cycle, a facility, or a purchase order; it needs somebody
to turn it on.

So a Wave 0 is proposed that runs in PARALLEL with procurement rather than behind
it. It is the difference between "we start when the lab is ready" and "we start
now, and the lab catches up" — and on a nine-month programme that is most of a
quarter of engineering.

Every derived task says where it came from. A task nobody can trace is a task
nobody will do.

Public API::

    tracker_workbook(rows) -> bytes            # what Compass will accept
    tasks_from_findings(findings, ...) -> list[Task]
    gantt_slide(schedule, tasks) -> dict       # an ICDEV slide spec
    phases_slide(schedule, tasks, rollup) -> dict
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from tools.bom.findings import Finding

# The tracker's own column vocabulary. Compass parses this; do not "improve" it.
TRACKER_HEADERS = [
    "Phase", "Task ID", "Task Name", "Description", "Dependencies",
    "Status", "Assignee", "Due Date", "Comment",
]

WAVE_ZERO = "Phase 0: Start Now (owned hardware)"
WAVE_DECIDE = "Phase 0: Decisions Blocking the Ask"


@dataclass
class Task:
    phase: str
    task_id: str
    name: str
    description: str = ""
    deps: list[str] = field(default_factory=list)
    status: str = "Not Started"
    assignee: str = "TBD"
    due_date: str = "TBD"
    comment: str = ""
    duration_days: int | None = None

    def row(self) -> list[Any]:
        return [
            self.phase, self.task_id, self.name, self.description,
            "; ".join(self.deps), self.status, self.assignee,
            self.due_date, self.comment,
        ]

    def as_compass(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "phase_name": self.phase,
            "deps": self.deps,
            "status": self.status,
            "assignee": self.assignee,
            "comment": self.comment,
        }
        if self.duration_days:
            out["duration_days"] = self.duration_days
        return out


def read_tracker(path: str, sheet: str = "Tasks") -> list[Task]:
    """Read an existing tracker. THEIR plan, carried over verbatim.

    The team's own tasks are not ours to rewrite. We add to them, we resequence
    them, and we never silently drop one — a planning tool that quietly loses
    somebody's work is worse than no planning tool.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet not in wb.sheetnames:
        return []
    ws = wb[sheet]

    rows = [
        [("" if v is None else str(v).strip()) for v in row]
        for row in ws.iter_rows(values_only=True)
        if any(v is not None for v in row)
    ]
    if not rows:
        return []

    head = [h.lower() for h in rows[0]]

    def col(*names: str) -> int:
        for n in names:
            if n in head:
                return head.index(n)
        return -1

    ix = {k: col(*v) for k, v in {
        "phase": ("phase",), "task_id": ("task id", "id"), "name": ("task name", "name"),
        "description": ("description",), "deps": ("dependencies", "depends on"),
        "status": ("status",), "assignee": ("assignee", "owner"),
        "due": ("due date", "due"), "comment": ("comment", "comments"),
    }.items()}

    def cell(row: list[str], key: str) -> str:
        i = ix[key]
        return row[i].strip() if 0 <= i < len(row) else ""

    out: list[Task] = []
    for row in rows[1:]:
        tid = cell(row, "task_id")
        if not tid:
            continue
        raw_deps = cell(row, "deps")
        deps = [
            d.strip() for d in raw_deps.replace(",", ";").split(";")
            if d.strip() and d.strip().lower() not in ("none", "n/a", "-")
        ]
        due = cell(row, "due")
        # "2026-07-09 00:00:00" -> "2026-07-09"; "TBD" stays "TBD".
        if due and due[:4].isdigit():
            due = due[:10]
        out.append(Task(
            phase=cell(row, "phase"), task_id=tid, name=cell(row, "name"),
            description=cell(row, "description"), deps=deps,
            status=cell(row, "status") or "Not Started",
            assignee=cell(row, "assignee") or "TBD",
            due_date=due or "TBD", comment=cell(row, "comment"),
        ))
    return out


def tracker_workbook(tasks: Iterable[Task]) -> bytes:
    """A single-sheet workbook Compass will accept.

    Compass reads the FIRST worksheet. A real tracker has half a dozen sheets and
    the tasks are rarely on the first one — so ICDEV, which is the thing that
    reads documents, hands over just the grid.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tasks"
    ws.append(TRACKER_HEADERS)
    for i, _ in enumerate(TRACKER_HEADERS, start=1):
        c = ws.cell(1, i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")

    for t in tasks:
        ws.append(t.row())

    for col, width in zip("ABCDEFGHI", (34, 9, 40, 64, 16, 13, 14, 13, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Findings become work ─────────────────────────────────────────────────────

def tasks_from_findings(
    findings: Iterable[Finding],
    *,
    owned_units: int = 0,
    owned_note: str = "",
    long_lead_days: int = 42,
) -> list[Task]:
    """The plan the evidence implies.

    Two groups, and the split is the argument:

    **Decisions blocking the ask.** Every one of these is somebody answering a
    question, not somebody building something. They cost days and they gate a
    number that leadership is being asked to approve — which makes them the
    cheapest and most urgent work on the programme.

    **Start now.** Hardware the programme already owns, doing useful work on day
    one, while procurement runs in parallel behind it.
    """
    findings = list(findings)
    out: list[Task] = []
    n = 0

    def add(phase: str, name: str, desc: str, *, days: int, comment: str,
            deps: list[str] | None = None, assignee: str = "TBD") -> str:
        nonlocal n
        n += 1
        tid = f"0.{n}"
        out.append(Task(
            phase=phase, task_id=tid, name=name, description=desc,
            deps=deps or [], assignee=assignee, duration_days=days,
            comment=comment,
        ))
        return tid

    def have(*types: str) -> list[Finding]:
        return [f for f in findings if f.finding_type in types]

    # ── The decisions ───────────────────────────────────────────────────────
    if any(f.finding_type == "intra_doc_double_count" for f in findings):
        f = have("intra_doc_double_count")[0]
        ev = f.evidence[0] if f.evidence else None
        add(
            WAVE_DECIDE,
            "Resolve the double-counted line",
            f.title,
            days=1,
            comment=(
                f"Evidence: {ev.source_document}!{ev.sheet}!{ev.locator}. "
                if ev else ""
            ) + "Confirm whether both category subtotals include this figure.",
        )

    if have("hardcoded_rollup", "stale_rollup"):
        cells = ", ".join(
            f"{f.evidence[0].sheet}!{f.evidence[0].locator}"
            for f in have("hardcoded_rollup", "stale_rollup")[:6] if f.evidence
        )
        add(
            WAVE_DECIDE,
            "Repair the summary formulas",
            "Typed-in numbers sitting where formulas belong. Editing the "
            "underlying sheets does not move them, so the headline total has "
            "silently stopped tracking its own inputs.",
            days=1,
            comment=f"Cells: {cells}",
        )

    if have("unpriced_line_zeroed"):
        f = have("unpriced_line_zeroed")[0]
        ev = f.evidence[0] if f.evidence else None
        add(
            WAVE_DECIDE,
            "Price the line that is costing nothing",
            "A quantity with no unit price. The formula multiplies it to zero, "
            "so it sits inside the total contributing nothing — and the total is "
            "understated by whatever the item is actually worth.",
            days=2,
            comment=f"{ev.source_document}!{ev.sheet}!{ev.locator}" if ev else "",
        )

    if have("asset_count_disputed", "baseline_asset_gap", "unverified_existing_asset"):
        f = have("asset_count_disputed", "baseline_asset_gap",
                 "unverified_existing_asset")[0]
        add(
            WAVE_DECIDE,
            "Walk the rack and settle the asset count",
            "The design leans on more owned units than the inventory records. A "
            "serial number proves a machine exists; its absence proves nothing, so "
            "this cannot be settled from a spreadsheet. Somebody has to go and "
            "look. The refresh reserve is sized from this number, so it has to be "
            "right.",
            days=2,
            comment=f.title,
            assignee="Larry",
        )

    if have("scope_priced_only_by_weak_source", "scope_declared_unpriced",
            "scope_declared_undesigned"):
        f = have("scope_priced_only_by_weak_source", "scope_declared_unpriced",
                 "scope_declared_undesigned")[0]
        add(
            WAVE_DECIDE,
            "Scope and price the declared-but-undesigned workstream",
            "Work we have said we are doing that appears in no agreed design and "
            "in no authoritative bill of materials. It reads as covered on a "
            "spreadsheet and it is not.",
            days=5,
            comment=f.title,
        )

    if have("capex_opex_conflation"):
        add(
            WAVE_DECIDE,
            "Separate recurring costs from capital",
            "Charges stated per period are carried as one-off amounts. The "
            "multi-year commitment is understated by however many periods nobody "
            "multiplied by.",
            days=1,
            comment=f"{len(have('capex_opex_conflation'))} line(s) affected",
        )

    if have("hidden_content"):
        add(
            WAVE_DECIDE,
            "Confirm the constraints found in hidden content",
            "Material found in the documents but not on the page — a screenshot in "
            "a sheet that renders empty, notes nobody opens. Constraints of this "
            "kind are unpriced by definition, because nobody reading the workbook "
            "ever saw them.",
            days=2,
            comment=have("hidden_content")[0].title,
        )

    # The one that has to exist whether or not a finding produced it: while several
    # documents each claim to price this project, there IS no number to approve.
    add(
        WAVE_DECIDE,
        "Nominate a source of record for each area of scope",
        "Several documents each claim to price this project. Until one governs "
        "each area, the totals are a SUM of competing estimates rather than an "
        "estimate of anything — and that is the arithmetic that produced the "
        "spread in the first place. This decision takes an afternoon and it "
        "unblocks the entire ask.",
        days=1,
        comment="Blocks leadership approval.",
        assignee="Larry",
    )

    # ── Start now ───────────────────────────────────────────────────────────
    if owned_units:
        stand_up = add(
            WAVE_ZERO,
            f"Stand up the virtual environment on the {owned_units} owned servers",
            "Hardware the programme already owns, doing useful work on day one. "
            "This needs no purchase order, no facility and no procurement cycle — "
            "it needs somebody to turn it on.",
            days=10,
            comment=owned_note or "Zero capital cost. Runs in parallel with procurement.",
            assignee="Larry",
        )
        bring_in = add(
            WAVE_ZERO,
            "Bring the platform in-house and start development",
            "The team begins building while the lab buildout runs behind it. This "
            "is the difference between starting when the lab is ready and starting "
            "now — on a nine-month programme, most of a quarter of engineering.",
            days=20,
            comment="Does not wait on Phase 1-4.",
            deps=[stand_up],
        )
        add(
            WAVE_ZERO,
            "Earmark the hardware refresh reserve",
            "The owned fleet is out of warranty. It is carrying the programme at "
            "zero capital cost today and it will fail. Ask for the replacement now, "
            "sized from the VERIFIED unit count, rather than discover it later.",
            days=3,
            comment="Sized from the asset count above — so that count has to be right.",
            deps=[],
        )
        _ = bring_in

    # NOTE what is deliberately NOT added here: a "place the long-lead order" task.
    #
    # The team's own plan already has one — procurement, sitting on the critical
    # path where it belongs. Adding a second would not shorten anything; it would
    # put two tasks in the tracker for one purchase order and make the schedule
    # lie about how much work there is. A planning tool whose first act is to
    # duplicate the work already in the plan is not helping.
    #
    # What the evidence DOES add is the dependency nobody had written down, and
    # link_decisions_to_approval() below is where that happens.
    _ = long_lead_days
    return out


# Words that identify the task a plan already has for "somebody signs this off".
_APPROVAL = ("approval", "approve", "leadership", "sign-off", "signoff", "funding")


def link_decisions_to_approval(derived: list[Task], existing: list[Task]) -> list[str]:
    """Make the approval task wait for the decisions that produce the number.

    This is the whole argument, expressed as a dependency.

    The team's plan has a task called something like "Get Leadership Approval",
    and it sits in the schedule as though the thing being approved already exists.
    It does not. Several documents each claim to price this project, so until
    somebody says which one governs, there IS no number — and an approval task
    with nothing to approve is a date in a spreadsheet, not a plan.

    Wiring the approval to the decisions stops the schedule pretending. What that
    then DOES to the critical path is the scheduler's answer, not ours — and on the
    evidence it is a good one: the decisions are short, so they finish inside the
    slack the plan already has and cost the programme nothing at all.

    That is a better argument than the one we set out to make. These decisions are
    FREE. A few days of somebody answering questions, no schedule impact, and until
    they are done there is no number for leadership to approve. There is no version
    of this project in which not doing them first is the cheaper option.

    (An earlier draft of this docstring asserted that the rewiring would put Phase 0
    on the critical path. It does not, and saying so would have been the tool
    telling a story the schedule does not support — which is the one thing this
    whole engine exists to stop.)

    Returns the ids of the existing tasks that were rewired, because silently
    editing somebody's plan is not something to do without saying so.
    """
    gating = [
        t.task_id for t in derived if t.phase == WAVE_DECIDE
    ]
    if not gating:
        return []

    touched: list[str] = []
    for t in existing:
        if not any(w in t.name.lower() for w in _APPROVAL):
            continue
        for g in gating:
            if g not in t.deps:
                t.deps.append(g)
        note = "Cannot approve a number that does not exist yet — gated on the Phase 0 decisions."
        t.comment = f"{t.comment}  {note}".strip() if t.comment else note
        touched.append(t.task_id)

    return touched


# ── Slides, from Compass's schedule ──────────────────────────────────────────

def _parse(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def gantt_slide(sched: dict, tasks: Iterable[Task], *, weeks: int = 16) -> dict | None:
    """A Gantt, drawn as a table because a table is what a deck can carry.

    One column per week; a bar is a run of filled cells. Critical-path tasks are
    marked, because a Gantt that does not say what is on the critical path is a
    picture of a schedule rather than a schedule.
    """
    rows_in = (sched or {}).get("tasks") or {}
    anchor = _parse((sched or {}).get("anchor"))
    if not rows_in or anchor is None:
        return None

    by_id = {t.task_id: t for t in tasks}
    start_of = {}
    for tid, info in rows_in.items():
        s, e = _parse(info.get("start")), _parse(info.get("end"))
        if s and e:
            start_of[tid] = (s, e, bool(info.get("critical")))

    if not start_of:
        return None

    week0 = anchor - timedelta(days=anchor.weekday())
    headers = ["Task", *[f"W{i + 1}" for i in range(weeks)]]

    ordered = sorted(
        start_of.items(),
        key=lambda kv: (kv[1][0], kv[0]),
    )

    rows: list[list[str]] = []
    for tid, (s, e, critical) in ordered:
        t = by_id.get(tid)
        label = f"{tid} {t.name if t else ''}"[:34]
        if critical:
            label = "! " + label

        cells = []
        for w in range(weeks):
            ws = week0 + timedelta(weeks=w)
            we = ws + timedelta(days=6)
            cells.append("███" if (s <= we and e >= ws) else "")
        rows.append([label, *cells])

    return {
        "slide_type": "table",
        "title": f"Schedule — anchored {anchor:%d %b %Y}, ends {sched.get('project_end', '?')}",
        "bullets": {
            "headers": headers,
            "rows": rows,
            "footer": ["! = on the critical path", *[""] * weeks],
        },
        "speaker_notes": (
            "Computed by Compass from the dependency graph, not asserted. The "
            "critical path is what determines the end date: every day lost on a "
            "marked task is a day lost on the programme, and every day lost on an "
            "unmarked one is free. That distinction is the only reason to have a "
            "schedule at all."
        ),
    }


def phases_slide(sched: dict, tasks: Iterable[Task]) -> dict | None:
    """What each phase is, when it runs, and what is holding it up."""
    rows_in = (sched or {}).get("tasks") or {}
    if not rows_in:
        return None

    tasks = list(tasks)
    by_id = {t.task_id: t for t in tasks}

    phases: dict[str, dict[str, Any]] = {}
    for tid, info in rows_in.items():
        t = by_id.get(tid)
        if t is None:
            continue
        p = phases.setdefault(t.phase or "(unphased)", {
            "count": 0, "start": None, "end": None, "critical": 0,
        })
        p["count"] += 1
        s, e = _parse(info.get("start")), _parse(info.get("end"))
        if s and (p["start"] is None or s < p["start"]):
            p["start"] = s
        if e and (p["end"] is None or e > p["end"]):
            p["end"] = e
        if info.get("critical"):
            p["critical"] += 1

    if not phases:
        return None

    rows = []
    for name, p in sorted(phases.items(), key=lambda kv: (kv[1]["start"] or date.max)):
        rows.append([
            name[:36],
            f"{p['start']:%d %b}" if p["start"] else "—",
            f"{p['end']:%d %b}" if p["end"] else "—",
            str(p["count"]),
            str(p["critical"]) if p["critical"] else "—",
        ])

    return {
        "slide_type": "table",
        "title": "The phases",
        "bullets": {
            "headers": ["Phase", "Starts", "Ends", "Tasks", "On critical path"],
            "rows": rows,
            "footer": [],
        },
        "speaker_notes": (
            "Phase 0 is the change. The existing plan gates every piece of "
            "software work behind Phase 5, so nothing gets built until facilities, "
            "network and virtualization are all finished. Hardware we already own "
            "can carry a virtual environment on day one — it needs no purchase "
            "order and no facility. So Phase 0 runs in PARALLEL with procurement "
            "rather than behind it, and the team starts building while the lab "
            "catches up."
        ),
    }
