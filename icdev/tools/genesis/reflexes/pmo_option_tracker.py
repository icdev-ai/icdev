#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex: PMO Option Period Tracker.

Runs daily at 06:30. Checks all pending option periods for approaching
exercise deadlines and escalates via kanban + notifications.

Risk tiers:
    ≤30 days → critical (CAT2 escalation + kanban high)
    ≤60 days → warning  (kanban high)
    ≤90 days → watch    (kanban medium)
"""
IMPLEMENTATION_STATUS = "full"

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute option period tracker reflex."""
    results = {
        "options_checked": 0,
        "alerts_created": 0,
        "critical": 0,
        "warning": 0,
        "watch": 0,
        "errors": [],
    }

    try:
        from tools.govcon.option_period_tracker import get_portfolio_countdown
        from tools.db.storage import get_connection
        import os

        countdown = get_portfolio_countdown()
        options = countdown.get("options", [])
        results["options_checked"] = len(options)

        if not options:
            return {"success": True, "metric_value": 0, "details": results}

        DB_PATH = str(BASE_DIR / "data" / "icdev.db")
        conn = get_connection(db_path=DB_PATH)
        conn.set_security_context(None)

        today = date.today().isoformat()

        for opt in options:
            tier = opt.get("risk_tier")
            if not tier:
                continue

            days = opt.get("days_to_deadline")
            contract_number = opt.get("contract_number", "N/A")
            contract_title = opt.get("contract_title", "Unknown Contract")
            option_num = opt.get("option_number", "?")
            deadline = opt.get("exercise_deadline", "N/A")
            ceiling = opt.get("ceiling_value", 0)
            option_id = opt.get("id")

            # Check if we already created a kanban task for this option+tier today
            # (avoid duplicating alerts on every run)
            existing = conn.execute(
                """SELECT id FROM kanban_tasks
                   WHERE dispatch_source = 'pmo_option_tracker'
                     AND status NOT IN ('done', 'dismissed')
                     AND title LIKE ?""",
                (f"%Option {option_num}%{contract_number}%",),
            ).fetchone()
            if existing:
                continue

            import uuid as _uuid2
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            task_id = f"pmo-opt-{_uuid2.uuid4().hex[:10]}"

            priority = "high" if tier in ("critical", "warning") else "medium"
            title = f"Option Exercise Window: {contract_number} Option {option_num} — {days}d remaining"
            description = (
                f"Contract: {contract_title} | Option {option_num}\n"
                f"Exercise Deadline: {deadline} ({days} days remaining)\n"
                f"Ceiling Value: ${ceiling:,.0f}\n"
                f"Risk Tier: {tier.upper()}\n"
                f"Action: Review contract health and determine if option should be exercised. "
                f"Visit /api/cpmp/options/{option_id}/recommend for AI go/no-go assessment."
            )
            tags = json.dumps({
                "option_id": option_id,
                "contract_id": opt.get("contract_id"),
                "contract_number": contract_number,
                "option_number": option_num,
                "exercise_deadline": deadline,
                "days_to_deadline": days,
                "ceiling_value": ceiling,
                "risk_tier": tier,
            })

            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, task_type, title, description, status, priority, target_date,
                    tags, dispatch_source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'suggested', ?, ?, ?, 'pmo_option_tracker', ?, ?)""",
                (
                    task_id,
                    "chore",
                    title,
                    description,
                    priority,
                    deadline,
                    tags,
                    now,
                    now,
                ),
            )

            results["alerts_created"] += 1
            results[tier] = results.get(tier, 0) + 1

            # CAT2 escalation for critical (≤30 days)
            if tier == "critical":
                try:
                    from tools.notification_service.alert_service import escalate_cat1
                    escalate_cat1(
                        finding_title=title,
                        severity="CAT2",
                        details={
                            "option_id": option_id,
                            "contract_number": contract_number,
                            "deadline": deadline,
                            "days_remaining": days,
                        },
                    )
                except Exception as ne:
                    results["errors"].append(f"Escalation failed for option {option_id}: {ne}")

        conn.commit()
        conn.close()

        # Write to memory
        try:
            from tools.memory.memory_write import write_memory
            write_memory(
                content=(
                    f"PMO Option Tracker [{today}]: Checked {results['options_checked']} pending options. "
                    f"Critical: {results['critical']}, Warning: {results['warning']}, "
                    f"Watch: {results['watch']}. Alerts created: {results['alerts_created']}."
                ),
                memory_type="event",
            )
        except Exception:
            pass

        return {
            "success": True,
            "metric_value": results["alerts_created"],
            "details": results,
        }

    except Exception as e:
        results["errors"].append(str(e))
        return {"success": False, "metric_value": 0, "details": results, "error": str(e)}
