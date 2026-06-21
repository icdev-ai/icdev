# CUI // SP-CTI
"""CPMP pm-skills wiring — deterministic mappers for A2A event bridging.

Maps pm-skills outputs (sprint, retro, OKR, WWAS) to CPMP canvas inputs
(CDRL, CPARS, EVM, milestone acceptance).

Callable from A2A event handlers; no LLM required.
"""

from datetime import datetime, timezone


def sprint_to_cdrl(sprint: dict, conn=None) -> dict:
    sprint_number = sprint.get("sprint_number", 0)
    goal = sprint.get("goal", "")
    velocity = sprint.get("velocity", 0)
    completed_items = sprint.get("completed_items", [])
    end_date = sprint.get("end_date", "")

    cdrl_number = f"A{sprint_number:03d}"
    title = goal if goal else f"Sprint {sprint_number} Deliverables"

    due_date = end_date
    if not due_date:
        due_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    deliverable_type = "Technical Report"
    if any("demo" in str(item).lower() for item in completed_items):
        deliverable_type = "Software Product"
    elif any("doc" in str(item).lower() for item in completed_items):
        deliverable_type = "Technical Data Package"

    status = "Delivered" if completed_items else "Pending"
    if velocity == 0:
        status = "At Risk"

    return {
        "cdrl_number": cdrl_number,
        "title": title,
        "due_date": due_date,
        "status": status,
        "deliverable_type": deliverable_type,
    }


def retro_to_cpars(retro: dict, conn=None) -> dict:
    went_well = retro.get("went_well", [])
    improve = retro.get("improve", [])
    action_items = retro.get("action_items", [])

    quality_positives = [i for i in went_well if any(
        kw in str(i).lower() for kw in ("quality", "test", "defect", "bug", "review")
    )]
    quality_issues = [i for i in improve if any(
        kw in str(i).lower() for kw in ("quality", "test", "defect", "bug", "review")
    )]

    schedule_positives = [i for i in went_well if any(
        kw in str(i).lower() for kw in ("on time", "schedule", "sprint", "velocity", "deadline")
    )]
    schedule_issues = [i for i in improve if any(
        kw in str(i).lower() for kw in ("late", "delay", "schedule", "velocity", "backlog")
    )]

    cost_issues = [i for i in improve if any(
        kw in str(i).lower() for kw in ("cost", "budget", "resource", "overrun", "hours")
    )]
    cost_positives = [i for i in went_well if any(
        kw in str(i).lower() for kw in ("cost", "budget", "resource", "efficient")
    )]

    management_positives = [i for i in went_well if any(
        kw in str(i).lower() for kw in ("communication", "team", "process", "planning", "leadership")
    )]
    management_issues = [i for i in improve if any(
        kw in str(i).lower() for kw in ("communication", "process", "planning", "coordination")
    )]

    small_biz_items = [i for i in went_well + improve if any(
        kw in str(i).lower() for kw in ("subcontract", "small business", "partner", "vendor")
    )]

    def _narrative(positives: list, negatives: list, label: str) -> str:
        parts = []
        if positives:
            parts.append("Strengths: " + "; ".join(str(p) for p in positives[:3]))
        if negatives:
            parts.append("Areas for improvement: " + "; ".join(str(n) for n in negatives[:3]))
        if not parts:
            parts.append(f"No significant {label.lower()} findings this sprint.")
        return " | ".join(parts)

    action_summary = ""
    if action_items:
        action_summary = " Corrective actions: " + "; ".join(str(a) for a in action_items[:5])

    return {
        "quality": _narrative(quality_positives, quality_issues, "Quality") + action_summary,
        "schedule": _narrative(schedule_positives, schedule_issues, "Schedule"),
        "cost": _narrative(cost_positives, cost_issues, "Cost"),
        "management": _narrative(management_positives, management_issues, "Management"),
        "small_business": _narrative(small_biz_items, [], "Small Business") if small_biz_items
                          else "No small business subcontracting activity this sprint.",
    }


def okr_to_evm(okrs: list, conn=None) -> dict:
    milestones = []
    total_key_results = 0

    earliest_date = None
    latest_date = None

    for okr in okrs:
        objective = okr.get("objective", "")
        key_results = okr.get("key_results", [])
        target_date = okr.get("target_date", "")

        total_key_results += len(key_results)

        if target_date:
            try:
                td = datetime.fromisoformat(target_date)
                if earliest_date is None or td < earliest_date:
                    earliest_date = td
                if latest_date is None or td > latest_date:
                    latest_date = td
            except ValueError:
                pass

        for i, kr in enumerate(key_results):
            kr_str = str(kr)
            milestone_id = f"MS-{len(milestones) + 1:03d}"

            due = target_date
            if not due:
                due = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            milestones.append({
                "milestone_id": milestone_id,
                "objective": objective,
                "key_result": kr_str,
                "due_date": due,
                "weight": round(1.0 / max(len(key_results), 1), 4),
            })

    bcws_estimate = float(total_key_results) * 1.0

    performance_baseline_date = (
        earliest_date.strftime("%Y-%m-%d")
        if earliest_date
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )

    return {
        "milestones": milestones,
        "bcws_estimate": bcws_estimate,
        "performance_baseline_date": performance_baseline_date,
    }


def wwas_to_milestone(wwas: dict, conn=None) -> dict:
    items = wwas.get("items", [])
    sprint = wwas.get("sprint", 0)
    acceptance_criteria = wwas.get("acceptance_criteria", [])

    milestone_id = f"MILE-S{sprint:03d}"

    shipped_strs = [str(i) for i in items]
    description = (
        f"Sprint {sprint} acceptance: " + ", ".join(shipped_strs[:5])
        if shipped_strs
        else f"Sprint {sprint} — no items shipped."
    )

    met = [c for c in acceptance_criteria if any(
        str(item).lower() in str(c).lower() or str(c).lower() in str(item).lower()
        for item in items
    )]

    if not acceptance_criteria:
        acceptance_status = "Not Evaluated"
    elif len(met) == len(acceptance_criteria):
        acceptance_status = "Accepted"
    elif met:
        acceptance_status = "Partially Accepted"
    else:
        acceptance_status = "Rejected"

    evidence = [
        {"type": "shipped_item", "value": str(item)}
        for item in items[:10]
    ]

    return {
        "milestone_id": milestone_id,
        "description": description,
        "acceptance_status": acceptance_status,
        "evidence": evidence,
    }
