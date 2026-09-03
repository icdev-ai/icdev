# CUI // SP-CTI
"""RMF cycle time — TWO clocks, each with its own denominator, never merged.

rmf-cyc-01.

    automation_time    OURS. From the first automated artifact produced for a
                       package to the moment that package left our hands. This
                       is the quantity the "72 hours" claim is about.

    decision_latency   THE AO's. From the moment the package was submitted to
                       the Authorizing Official to the moment a decision came
                       back. Nothing the platform does moves it.

WHY THEY ARE NEVER ADDED
------------------------
A single end-to-end figure spanning both is unfalsifiable as a statement about
automation. Halve the automation and the headline can get WORSE because an AO
went on leave; change nothing and it can improve. Every claim of the form
"we took this from months to 72 hours" that quotes one number is quoting a
number nobody can attribute. So this module emits exactly two clocks, each
carrying the count it was computed over, and NO field anywhere is their sum.
``tests/test_rmf_cycle_time.py`` asserts that against a fixture where both are
measured and different — a structural test on key names would pass a payload
that spelled the blend under an innocent name.

WHAT "UNMEASURED" MEANS HERE
----------------------------
Every rate and every statistic is ``None``, never ``0.0`` and never ``100.0``,
when its denominator is empty (args/perfect_score_gate.yaml, ratcheted to 0 by
rem-hyg-13). A zero here would read as "instant", which is the single most
flattering possible reading of an unmeasured automation clock. The states are
kept apart because they send a reader to different places:

    never_recorded   no stage rows at all — nothing has ever produced an
                     artifact through the recorder. NOT a clean bill of health.
    unmeasurable     rows exist but no project has both ends of either clock.
    partial          one clock measured, the other not.
    measured         both clocks have a denominator.

THE BASELINE
------------
``baseline_source`` carries BOTH derivations and never merges them: the DECLARED
baseline from args/rmf_cycle_baseline.yaml (with its ``kind`` — a ``claimed``
figure is never presented as evidence) and a ``measured_here`` baseline
re-derived from this deployment's own MANUALLY actioned stages. A comparison is
emitted only when the declared baseline is quantified AND does not itself
include the AO's queue; otherwise ``comparison`` is None and the reason is
named. Refusing to divide is the point — a baseline that contains
decision_latency divided by an automation-only clock is the blend, wearing a
percentage.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.ato_compliance.dashboard import RMF_STAGES  # noqa: E402
from tools.compliance.rmf_stage_recorder import (  # noqa: E402
    ARTIFACT_STAGE,
    actor_kind,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.compliance.rmf_cycle_time")

# xit-decl-03: the ONE root resolver.
BASE_DIR = repo_root(__file__)
BASELINE_CONFIG = BASE_DIR / "args" / "rmf_cycle_baseline.yaml"

# The stage on which the hand-off to the decider is recorded. Everything before
# its submitted_at is automation_time; the span after it is decision_latency.
DECISION_STAGE = "authorize"

# RMF steps for which this platform has a wired automated producer. Derived from
# the recorder's declaration rather than restated, so wiring a sixth producer
# cannot leave this list behind.
AUTOMATED_STAGES = frozenset(ARTIFACT_STAGE.values())

# The steps NOTHING here produces. Reported by name on every run: an automation
# clock that silently began at whichever step happened to be wired is measuring
# a shorter job than the one being claimed.
STAGES_WITHOUT_PRODUCER = tuple(s for s in RMF_STAGES if s not in AUTOMATED_STAGES)


# ---------------------------------------------------------------------------
# Honesty rails
# ---------------------------------------------------------------------------

def _median(values: list[float]) -> float | None:
    """Median, or None over an empty sample. NEVER 0.0."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return round(ordered[mid], 2)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 2)


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile, or None over an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * len(ordered) + 0.5)) - 1))
    return round(ordered[idx], 2)


def _stats(values: list[float]) -> dict:
    """Summary of a sample. Every statistic is None over an empty denominator."""
    return {
        "count": len(values),
        "median_hours": _median(values),
        "p90_hours": _percentile(values, 90),
        "min_hours": round(min(values), 2) if values else None,
        "max_hours": round(max(values), 2) if values else None,
    }


def _parse_ts(value: Any) -> datetime | None:
    """Parse a stored timestamp. Returns None rather than guessing.

    The column is TEXT and its writers are not all this module's: PostgreSQL's
    own ``now()::text`` default produces ``2026-09-02 23:39:31.123456+00``,
    while the recorder writes ISO-8601 with a ``T``. Both are read; anything
    else is None, which propagates to "unmeasured" rather than to a wrong span.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace(" ", "T", 1) if " " in text and "T" not in text else text
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hours_between(start: datetime | None, end: datetime | None) -> float | None:
    """Elapsed hours, or None if either end is missing or the span is negative.

    A negative span means the recorded order is impossible (a decision dated
    before its own submission). That is a data defect, and reporting it as a
    small positive number by taking an absolute value would hide it inside the
    very statistic it corrupts.
    """
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    if delta < 0:
        return None
    return delta / 3600.0


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def load_baseline_config(path: Path | None = None) -> dict:
    """Read args/rmf_cycle_baseline.yaml. An unreadable file is NOT a default."""
    cfg_path = path or BASELINE_CONFIG
    try:
        import yaml

        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError("baseline config is not a mapping")
        return data
    except Exception as exc:
        logger.warning("baseline config unreadable (%s): %s", cfg_path, exc)
        return {"_unreadable": str(exc)}


def _get_connection(db_path: str | None = None):
    from tools.db.storage import get_connection

    return get_connection(db_path=db_path) if db_path else get_connection()


def _load_stage_rows(conn, window_days: int | None) -> list[dict] | None:
    """Read every stage row, or None if the table cannot be read at all.

    None and [] are different answers: None means the substrate is absent or
    unreadable (a fresh worktree, an unmigrated database) and [] means the table
    was read and is empty. Collapsing them would let "we could not look" render
    as "there is nothing there".
    """
    from tools.db.storage import table_exists

    try:
        if not table_exists(conn, "rmf_workflow_stages"):
            return None
        rows = conn.execute(
            "SELECT project_id, stage, status, started_at, submitted_at, "
            "completed_at, actor, evidence_ref, updated_at "
            "FROM rmf_workflow_stages"
        ).fetchall()
    except Exception as exc:
        logger.warning("rmf_workflow_stages unreadable: %s", exc)
        return None

    out = [dict(r) for r in rows]
    if window_days is None:
        return out

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    windowed = []
    for row in out:
        # A row is IN the window if anything about it moved inside it. Filtering
        # on started_at alone would drop a long-running package that is exactly
        # the case a cycle-time report exists to surface.
        stamps = [
            _parse_ts(row.get(k))
            for k in ("updated_at", "completed_at", "submitted_at", "started_at")
        ]
        stamps = [s for s in stamps if s is not None]
        if stamps and max(stamps) >= cutoff:
            windowed.append(row)
    return windowed


# ---------------------------------------------------------------------------
# The two clocks
# ---------------------------------------------------------------------------

def _automation_sample(project_id: str, rows: list[dict]) -> dict:
    """Measure automation_time for ONE project, or say why it could not be.

    START is the earliest ``started_at`` on any stage written by an AUTOMATED
    actor. END has two possible bases and which one was used is always stated:

      submitted      the package's ``submitted_at`` — authoritative, because it
                     is the actual moment the work left our hands.
      last_artifact  the latest ``updated_at`` among automated stages, used when
                     no submission has been recorded. A LOWER BOUND: the package
                     may still be in preparation, so the number can only grow.

    They are never averaged together into one figure without the basis, because
    a board of unsubmitted packages would otherwise report a flattering
    "automation time" that is really "time so far".
    """
    automated = [r for r in rows if actor_kind(r.get("actor")) == "automated"]
    if not automated:
        kinds = {actor_kind(r.get("actor")) for r in rows}
        reason = "unknown_actor" if "unknown" in kinds and "human" not in kinds else "no_automated_stage"
        return {"project_id": project_id, "hours": None, "reason": reason}

    starts = [_parse_ts(r.get("started_at")) for r in automated]
    starts = [s for s in starts if s is not None]
    if not starts:
        return {"project_id": project_id, "hours": None, "reason": "no_started_at"}
    start = min(starts)

    submitted = None
    for row in rows:
        if row.get("stage") == DECISION_STAGE:
            submitted = _parse_ts(row.get("submitted_at"))
            break

    if submitted is not None:
        end, basis = submitted, "submitted"
    else:
        ends = [_parse_ts(r.get("updated_at")) for r in automated]
        ends = [e for e in ends if e is not None]
        if not ends:
            return {"project_id": project_id, "hours": None, "reason": "no_end_timestamp"}
        end, basis = max(ends), "last_artifact"

    hours = _hours_between(start, end)
    if hours is None:
        return {"project_id": project_id, "hours": None, "reason": "negative_span"}
    return {
        "project_id": project_id,
        "hours": round(hours, 2),
        "end_basis": basis,
        "stages": sorted({r["stage"] for r in automated}),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def _decision_sample(project_id: str, rows: list[dict]) -> dict:
    """Measure decision_latency for ONE project, or say why it could not be."""
    authorize = next((r for r in rows if r.get("stage") == DECISION_STAGE), None)
    if authorize is None:
        return {"project_id": project_id, "hours": None, "reason": "no_authorize_stage"}

    submitted = _parse_ts(authorize.get("submitted_at"))
    decided = _parse_ts(authorize.get("completed_at"))

    if submitted is None and decided is None:
        return {"project_id": project_id, "hours": None, "reason": "not_submitted"}
    if submitted is None:
        # A recorded decision with no recorded submission. NOT zero latency —
        # the AO's queue was real and simply was not observed.
        return {"project_id": project_id, "hours": None, "reason": "decided_without_submission"}
    if decided is None:
        return {"project_id": project_id, "hours": None, "reason": "awaiting_decision"}

    hours = _hours_between(submitted, decided)
    if hours is None:
        return {"project_id": project_id, "hours": None, "reason": "negative_span"}
    return {
        "project_id": project_id,
        "hours": round(hours, 2),
        "status": authorize.get("status"),
        "notes": authorize.get("notes"),
    }


def _manual_baseline_sample(project_id: str, rows: list[dict]) -> dict:
    """The SAME automation measurement taken over MANUALLY actioned stages.

    This is the ``measured_here`` control group: what the identical span costs
    when a person does it on this deployment. It shares no code path with the
    declared baseline in the YAML, and it is the only baseline here that is
    evidence rather than an assertion.
    """
    manual = [r for r in rows if actor_kind(r.get("actor")) == "human"]
    if not manual:
        return {"project_id": project_id, "hours": None, "reason": "no_manual_stage"}
    starts = [_parse_ts(r.get("started_at")) for r in manual]
    starts = [s for s in starts if s is not None]
    ends = [_parse_ts(r.get("updated_at")) for r in manual]
    ends = [e for e in ends if e is not None]
    if not starts or not ends:
        return {"project_id": project_id, "hours": None, "reason": "no_timestamps"}
    hours = _hours_between(min(starts), max(ends))
    if hours is None:
        return {"project_id": project_id, "hours": None, "reason": "negative_span"}
    return {"project_id": project_id, "hours": round(hours, 2)}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _bucket_reasons(samples: list[dict]) -> dict:
    out: dict[str, int] = {}
    for s in samples:
        if s.get("hours") is None:
            out[s.get("reason", "unknown")] = out.get(s.get("reason", "unknown"), 0) + 1
    return out


def _baseline_section(cfg: dict, manual_samples: list[dict], automation: dict) -> dict:
    """Both derivations of the baseline, side by side, never merged."""
    declared_raw = cfg.get("baseline") or {}
    unreadable = cfg.get("_unreadable")

    declared = {
        "id": declared_raw.get("id"),
        "kind": declared_raw.get("kind"),
        "label": declared_raw.get("label"),
        "value_hours": declared_raw.get("value_hours"),
        "value_note": declared_raw.get("value_note"),
        "includes_decision_latency": declared_raw.get("includes_decision_latency"),
        "citation": declared_raw.get("citation"),
        "as_of": declared_raw.get("as_of"),
        "verified": declared_raw.get("verified"),
        "config_unreadable": unreadable,
    } if (declared_raw or unreadable) else None

    measured_cfg = cfg.get("measured_here") or {}
    min_projects = measured_cfg.get("min_projects", 2)
    manual_hours = [s["hours"] for s in manual_samples if s.get("hours") is not None]
    measured_here = _stats(manual_hours)
    measured_here["source"] = "rmf_workflow_stages rows with a human:* actor"
    measured_here["min_projects"] = min_projects
    if len(manual_hours) < min_projects:
        # Below the floor every statistic is withdrawn — a median over one
        # project is an anecdote wearing a statistic's name.
        for key in ("median_hours", "p90_hours", "min_hours", "max_hours"):
            measured_here[key] = None
        measured_here["state"] = "below_min_projects" if manual_hours else "no_manual_history"
    else:
        measured_here["state"] = "measured"

    comparison = None
    refused: list[str] = []
    rules = cfg.get("comparison") or {}
    automation_median = automation.get("median_hours")

    if automation_median is None:
        refused.append("automation_time_unmeasured")
    if declared is None:
        refused.append("no_declared_baseline")
    else:
        if rules.get("require_quantified_baseline", True) and declared.get("value_hours") is None:
            refused.append("baseline_unquantified")
        if (
            rules.get("refuse_when_baseline_includes_decision_latency", True)
            and declared.get("includes_decision_latency")
        ):
            # The refusal that IS the card. A wall-clock ATO duration contains
            # the AO's queue; automation_time does not. Dividing one by the
            # other is the blend, expressed as a ratio.
            refused.append("baseline_includes_decision_latency")

    if not refused and declared is not None:
        baseline_hours = float(declared["value_hours"])
        comparison = {
            "baseline_hours": baseline_hours,
            "automation_median_hours": automation_median,
            "reduction_factor": round(baseline_hours / automation_median, 2)
            if automation_median
            else None,
            "baseline_kind": declared.get("kind"),
            "baseline_verified": declared.get("verified"),
        }

    return {
        "declared": declared,
        "measured_here": measured_here,
        "comparison": comparison,
        "comparison_refused": refused or None,
    }


def collect_report(
    *,
    conn: Any = None,
    db_path: str | None = None,
    window_days: int | None = None,
    project_id: str | None = None,
    config_path: Path | None = None,
) -> dict:
    """Produce the cycle-time report. Two clocks, two denominators, no blend."""
    _conn = conn or _get_connection(db_path)
    close_after = conn is None
    try:
        rows = _load_stage_rows(_conn, window_days)
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass

    cfg = load_baseline_config(config_path)
    target_cfg = cfg.get("target") or {}
    target_hours = target_cfg.get("automation_hours")

    base: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "project_filter": project_id,
        "stages_without_producer": list(STAGES_WITHOUT_PRODUCER),
        "target": {
            "automation_hours": target_hours,
            "kind": target_cfg.get("kind"),
            "claimed_by": target_cfg.get("claimed_by"),
            "verified": target_cfg.get("verified"),
        },
    }

    if rows is None:
        base.update(
            {
                "state": "substrate_absent",
                "projects": 0,
                "automation_time": None,
                "decision_latency": None,
                "baseline_source": None,
                "note": (
                    "rmf_workflow_stages is absent or unreadable on this database. "
                    "This is not a clean bill of health — nothing was measured."
                ),
            }
        )
        return base

    if project_id:
        rows = [r for r in rows if r.get("project_id") == project_id]

    by_project: dict[str, list[dict]] = {}
    for row in rows:
        by_project.setdefault(row["project_id"], []).append(row)

    if not by_project:
        base.update(
            {
                "state": "never_recorded",
                "projects": 0,
                "automation_time": None,
                "decision_latency": None,
                "baseline_source": None,
                "note": (
                    "rmf_workflow_stages holds no rows in scope. No artifact has been "
                    "produced through the recorder, so neither clock has a denominator. "
                    "Not a clean bill of health."
                ),
            }
        )
        return base

    automation_samples = [_automation_sample(p, r) for p, r in sorted(by_project.items())]
    decision_samples = [_decision_sample(p, r) for p, r in sorted(by_project.items())]
    manual_samples = [_manual_baseline_sample(p, r) for p, r in sorted(by_project.items())]

    auto_hours = [s["hours"] for s in automation_samples if s.get("hours") is not None]
    dec_hours = [s["hours"] for s in decision_samples if s.get("hours") is not None]

    # SUBMITTED-basis samples are the only COMPLETE measurements: the package
    # demonstrably left our hands, so the span is final. A last_artifact sample
    # is a LOWER BOUND — the package may still be in preparation and its number
    # can only ever grow.
    submitted_hours = [
        s["hours"]
        for s in automation_samples
        if s.get("hours") is not None and s.get("end_basis") == "submitted"
    ]

    automation = _stats(auto_hours)
    automation["unit"] = "hours"
    automation["scope"] = "first automated artifact -> package leaves our hands"
    automation["end_basis_counts"] = {
        basis: sum(1 for s in automation_samples if s.get("end_basis") == basis)
        for basis in ("submitted", "last_artifact")
    }
    # True when ANY contributing sample was bounded by its latest artifact rather
    # than by a submission. The headline median is then a floor, not a duration.
    automation["is_lower_bound"] = automation["end_basis_counts"]["last_artifact"] > 0
    automation["submitted_only"] = _stats(submitted_hours)
    automation["unmeasurable"] = _bucket_reasons(automation_samples)
    automation["target_hours"] = target_hours

    # THE TARGET IS JUDGED ONLY ON COMPLETED PACKAGES, and this is not a detail.
    # Two artifacts produced sixteen milliseconds apart give a lower bound of
    # 0.0 hours, and scoring that against a 72-hour target returns True — a
    # perfect result for a package nobody has finished assembling, which is the
    # empty-denominator defect wearing a duration. A lower bound can only grow,
    # so it can never support "under the target"; it is None instead.
    submitted_median = automation["submitted_only"]["median_hours"]
    automation["meets_target"] = (
        None
        if submitted_median is None or target_hours is None
        else submitted_median <= float(target_hours)
    )
    automation["meets_target_basis"] = "submitted_only"
    automation["samples"] = automation_samples

    decision = _stats(dec_hours)
    decision["unit"] = "hours"
    decision["scope"] = "package submitted to the AO -> decision recorded"
    decision["basis"] = "most_recent_submission"
    decision["owner"] = "authorizing_official"
    decision["unmeasurable"] = _bucket_reasons(decision_samples)
    decision["awaiting_decision"] = sum(
        1 for s in decision_samples if s.get("reason") == "awaiting_decision"
    )
    decision["samples"] = decision_samples

    if auto_hours and dec_hours:
        state = "measured"
    elif auto_hours or dec_hours:
        state = "partial"
    else:
        state = "unmeasurable"

    base.update(
        {
            "state": state,
            "projects": len(by_project),
            "stage_rows": len(rows),
            "automation_time": automation,
            "decision_latency": decision,
            "baseline_source": _baseline_section(cfg, manual_samples, automation),
            "blended_figure": None,
            "blend_note": (
                "automation_time and decision_latency are never summed. They are owned "
                "by different parties; a single span across both cannot attribute a "
                "change to either."
            ),
        }
    )
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    return "unmeasured" if value is None else str(value)


def format_report(report: dict) -> str:
    lines: list[str] = []
    lines.append("RMF CYCLE TIME — two clocks, never merged (rmf-cyc-01)")
    lines.append(f"  generated_at : {report['generated_at']}")
    lines.append(f"  state        : {report['state']}")
    lines.append(f"  projects     : {report.get('projects', 0)}")
    if report.get("stages_without_producer"):
        lines.append(
            "  no producer  : " + ", ".join(report["stages_without_producer"])
            + "  (these steps are never written by automation here)"
        )
    if report.get("note"):
        lines.append(f"  note         : {report['note']}")

    for key, title in (
        ("automation_time", "AUTOMATION TIME (ours — the 72h claim)"),
        ("decision_latency", "DECISION LATENCY (the AO's queue)"),
    ):
        section = report.get(key)
        lines.append("")
        lines.append(title)
        if not section:
            lines.append("  unmeasured — no denominator")
            continue
        lines.append(f"  scope        : {section['scope']}")
        lines.append(f"  n            : {section['count']}")
        lines.append(f"  median hours : {_fmt(section['median_hours'])}")
        lines.append(f"  p90 hours    : {_fmt(section['p90_hours'])}")
        lines.append(f"  min / max    : {_fmt(section['min_hours'])} / {_fmt(section['max_hours'])}")
        if key == "automation_time":
            lines.append(f"  end basis    : {section['end_basis_counts']}")
            if section["is_lower_bound"]:
                lines.append(
                    "                 (a last_artifact sample contributes — the "
                    "median above is a FLOOR, not a duration)"
                )
            lines.append(
                f"  submitted n  : {section['submitted_only']['count']}  "
                f"median={_fmt(section['submitted_only']['median_hours'])}h"
            )
            lines.append(
                f"  target       : {_fmt(section['target_hours'])}h  "
                f"met={_fmt(section['meets_target'])} "
                f"(judged on submitted packages only)"
            )
        else:
            lines.append(f"  awaiting AO  : {section['awaiting_decision']}")
        if section["unmeasurable"]:
            lines.append(f"  unmeasurable : {section['unmeasurable']}")

    baseline = report.get("baseline_source")
    lines.append("")
    lines.append("BASELINE SOURCE")
    if not baseline:
        lines.append("  unmeasured — nothing to compare against")
    else:
        declared = baseline.get("declared") or {}
        lines.append(
            f"  declared     : {declared.get('id')} [{declared.get('kind')}] "
            f"value={_fmt(declared.get('value_hours'))}h "
            f"includes_AO_queue={_fmt(declared.get('includes_decision_latency'))}"
        )
        if declared.get("value_note"):
            lines.append(f"                 {declared['value_note']}")
        measured = baseline.get("measured_here") or {}
        lines.append(
            f"  measured here: n={measured.get('count')} "
            f"median={_fmt(measured.get('median_hours'))}h "
            f"[{measured.get('state')}]"
        )
        if baseline.get("comparison"):
            comp = baseline["comparison"]
            lines.append(
                f"  comparison   : {comp['baseline_hours']}h -> "
                f"{comp['automation_median_hours']}h "
                f"({comp['reduction_factor']}x, baseline kind={comp['baseline_kind']})"
            )
        else:
            lines.append(
                "  comparison   : REFUSED — " + ", ".join(baseline.get("comparison_refused") or [])
            )
    lines.append("")
    lines.append(
        "  automation_time + decision_latency is never computed. Different owners; "
        "one span across both attributes a change to neither."
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="RMF cycle time — automation_time and decision_latency, separately"
    )
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    parser.add_argument("--window-days", type=int, default=None, help="Restrict to recent activity")
    parser.add_argument("--project-id", "--project", dest="project_id", help="One project")
    parser.add_argument("--db", dest="db_path", help="Database path override")
    args = parser.parse_args(argv)

    try:
        report = collect_report(
            db_path=args.db_path,
            window_days=args.window_days,
            project_id=args.project_id,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"rmf_cycle_time: could not produce the report: {exc}", file=sys.stderr)
        # Exit 2 = the report could not be produced, which is never the same as
        # a report that found nothing.
        return 2

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
