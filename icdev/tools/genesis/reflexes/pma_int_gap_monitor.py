#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex: PMA INT Gap Monitor.

Fires weekly. Three sequential passes:

  1. Gap scan — calls get_persistent_gaps(days=14) to surface exploitation lines
     with unresolved INT gaps older than 14 days.

  2. Collection requirement generation — calls generate_collection_requirements()
     for each gap.  Dedup key: coverage_id + discipline + status != 'cancelled'.
     New critical or high requirements also seed a kanban task.

  3. Risk register — for each critical gap older than 30 days with no 'tasked'
     collection requirement, creates a 'compliance' risk via risk_manager.py.
"""
IMPLEMENTATION_STATUS = "full"

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

_DISPATCH_SOURCE = "pma_int_gap_monitor"


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute PMA INT Gap Monitor reflex."""
    results: Dict[str, Any] = {
        "gaps_found": 0,
        "requirements_inserted": 0,
        "kanban_tasks_seeded": 0,
        "compliance_risks_created": 0,
        "errors": [],
    }

    try:
        from tools.pma.int_gap_monitor import (
            _ensure_tables,
            generate_collection_requirements,
            get_persistent_gaps,
            get_untasked_critical_gaps_over_30_days,
        )

        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: background reflex; pma tables have no tenant_id/classification

        _ensure_tables(conn)

        now = datetime.now(timezone.utc).isoformat()

        # ── Pass 1 + 2: Persistent gap scan + requirement generation ──────────
        gaps = get_persistent_gaps(days=14, conn=conn)
        results["gaps_found"] = len(gaps)

        for gap in gaps:
            req = generate_collection_requirements(gap, conn)
            if req["inserted"]:
                results["requirements_inserted"] += 1
                priority = req.get("priority", "medium")
                if priority in ("critical", "high"):
                    _seed_kanban_task(conn, gap, req["requirement_id"], now, results)

        conn.commit()

        # ── Pass 3: Compliance risk for unmitigated critical gaps > 30 days ──
        critical_gaps = get_untasked_critical_gaps_over_30_days(conn, gaps=gaps)
        for gap in critical_gaps:
            _seed_compliance_risk(gap, results)

        conn.commit()
        conn.close()

        _write_memory_log(results)
        return {
            "success": True,
            "metric_value": results["requirements_inserted"],
            "details": results,
        }

    except Exception as exc:
        results["errors"].append(str(exc))
        return {"success": False, "metric_value": 0, "details": results, "error": str(exc)}


def _seed_kanban_task(conn, gap: Dict, req_id: str, now: str, results: Dict) -> None:
    """Seed a kanban task for a new critical/high collection requirement. Skips duplicates."""
    line_title = gap.get("exploitation_line_title", "Unknown")
    discipline = gap.get("discipline", "")
    title = f"INT collection req ({discipline}): {line_title}"[:120]

    existing = conn.execute(
        "SELECT id FROM kanban_tasks "
        "WHERE title = %s AND dispatch_source = %s AND status NOT IN ('done', 'dismissed')",
        (title, _DISPATCH_SOURCE),
    ).fetchone()
    if existing:
        return

    task_id = f"pma-igap-{uuid.uuid4().hex[:8]}"
    description = (
        f"Collection Requirement ID: {req_id}\n"
        f"Exploitation Line: {line_title}\n"
        f"Discipline: {discipline}\n"
        f"Gap Severity: {gap.get('severity', 'unknown').upper()}\n"
        f"Gap Description: {gap.get('gap_description', '')}\n"
        f"Action: Task this collection requirement to an appropriate INT collector."
    )
    tags = json.dumps(
        {
            "gap_id": gap["id"],
            "coverage_id": gap["exploitation_line_id"],
            "discipline": discipline,
            "severity": gap.get("severity"),
            "requirement_id": req_id,
            "contract_id": gap.get("contract_id"),
        }
    )
    priority = gap.get("severity", "medium")
    if priority == "critical":
        priority = "high"  # kanban priority values: low/medium/high/critical

    conn.execute(
        """
        INSERT INTO kanban_tasks
            (id, task_type, title, description, status, priority,
             tags, dispatch_source, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'suggested', %s, %s, %s, %s, %s)
        """,
        (
            task_id,
            "feature",
            title,
            description[:500],
            priority,
            tags,
            _DISPATCH_SOURCE,
            now,
            now,
        ),
    )
    results["kanban_tasks_seeded"] += 1


def _seed_compliance_risk(gap: Dict, results: Dict) -> None:
    """Create a compliance risk for a critical gap persisting > 30 days without a tasked requirement."""
    try:
        from tools.govcon.risk_manager import create_risk

        line_title = gap.get("exploitation_line_title", gap["exploitation_line_id"])
        title = f"INT gap compliance: no tasked collection req for '{line_title}' ({gap['discipline']})"

        resp = create_risk(
            {
                "contract_id": gap.get("contract_id") or "PMA-PORTFOLIO",
                "title": title[:200],
                "category": "compliance",
                "probability": 4,
                "impact": 4,
                "status": "open",
                "mitigation": None,
                "owner": None,
                "metadata": {
                    "gap_id": gap["id"],
                    "exploitation_line_id": gap["exploitation_line_id"],
                    "discipline": gap["discipline"],
                    "gap_severity": gap.get("severity"),
                    "source": _DISPATCH_SOURCE,
                },
            }
        )
        if resp.get("status") == "ok":
            results["compliance_risks_created"] += 1
    except Exception as exc:
        results["errors"].append(f"Risk entry failed for gap {gap['id']}: {exc}")


def _write_memory_log(results: Dict) -> None:
    try:
        from tools.memory.memory_write import write_memory

        write_memory(
            content=(
                f"PMA INT Gap Monitor: {results['gaps_found']} persistent gaps found, "
                f"{results['requirements_inserted']} collection requirements inserted, "
                f"{results['kanban_tasks_seeded']} kanban tasks seeded, "
                f"{results['compliance_risks_created']} compliance risks created."
            ),
            memory_type="event",
        )
    except Exception:
        pass


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    result = run({}, None)
    print(json.dumps(result, indent=2, default=str))
