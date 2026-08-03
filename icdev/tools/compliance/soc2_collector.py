#!/usr/bin/env python3
# CUI // SP-CTI
"""SOC 2 evidence collector — auto-harvest from audit_trail and component_audit_log.

Usage:
    python tools/compliance/soc2_collector.py --tenant-id <tid> [--since 2026-01-01] [--json]
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


_CONTROLS_YAML = Path(__file__).parent / "soc2_controls.yaml"


def _load_controls() -> dict[str, Any]:
    with open(_CONTROLS_YAML, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("controls", {})


def collect_evidence(
    tenant_id: str,
    framework: str = "soc2",
    since: str | None = None,
) -> dict[str, Any]:
    """Harvest evidence from audit_trail and component_audit_log.

    Returns:
        {collected: N, skipped: N, controls_covered: [...]}
    """
    from tools.db.storage import get_connection

    controls = _load_controls()

    # Build reverse index: event_type -> [control_id, ...]
    event_to_controls: dict[str, list[str]] = {}
    component_to_controls: dict[str, list[str]] = {}
    for ctrl_id, ctrl in controls.items():
        for et in ctrl.get("event_types", []):
            event_to_controls.setdefault(et, []).append(ctrl_id)
        for ck in ctrl.get("component_keys", []):
            component_to_controls.setdefault(ck, []).append(ctrl_id)

    collected = 0
    skipped = 0
    controls_covered: set[str] = set()

    with get_connection() as conn:
        # ── Harvest from audit_trail ──────────────────────────────────────────
        # audit_trail's real columns are (id, project_id, event_type, actor,
        # action, details, ..., created_at). This query used to select
        # `resource`/`recorded_at` and filter on `tenant_id` -- three columns
        # the table has never had -- so on the primary PostgreSQL backend it
        # raised every time and the `except` below turned that into "no audit
        # evidence found". The collector reported success while harvesting
        # nothing from its main source. It looked correct only because the
        # SQLite test fixture declared those same fictional columns.
        #
        # Note audit_trail carries no tenant column at all, so this source is
        # platform-wide rather than per-tenant; the sibling component_audit_log
        # harvest below is likewise unscoped. Tenant-scoping the evidence
        # sources is a real gap, but it needs a schema decision, not a rewrite
        # of this query.
        # Each source names its own timestamp column: audit_trail.created_at,
        # component_audit_log.recorded_at. One shared clause cannot serve both.
        audit_since = f" AND created_at >= '{since}'" if since else ""
        component_since = f" AND recorded_at >= '{since}'" if since else ""
        try:
            audit_rows = conn.execute(
                f"SELECT id, event_type, action, created_at FROM audit_trail "
                f"WHERE 1=1{audit_since} ORDER BY created_at DESC LIMIT 5000",
            ).fetchall()
        except Exception:
            audit_rows = []

        for row in audit_rows:
            row_id, event_type, action, recorded_at = (
                row[0], row[1], row[2], row[3]
            )
            # Match on event_type -- the enumerated column, constrained by
            # audit_trail_event_type_check and generated from
            # tools.audit.audit_logger.VALID_EVENT_TYPES. `action` is free text
            # ("Built RTM for 10 requirements"), so it is only a fallback for
            # controls whose configured values are verb-shaped.
            resource = event_type
            matched_controls = (
                event_to_controls.get(event_type)
                or event_to_controls.get(action)
                or []
            )
            for ctrl_id in matched_controls:
                ev_id = str(uuid.uuid4())
                try:
                    conn.execute(
                        "INSERT INTO evidence_items "
                        "(id, control_id, framework, tenant_id, evidence_type, "
                        "source_table, source_row_id, summary, collected_at, collector) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            ev_id, ctrl_id, framework, tenant_id, "log",
                            "audit_trail", row_id,
                            f"{action} on {resource or 'unknown'} at {recorded_at}",
                            datetime.now(timezone.utc).isoformat(),
                            "soc2_collector",
                        ),
                    )
                    conn.commit()
                    collected += 1
                    controls_covered.add(ctrl_id)
                except Exception:
                    skipped += 1

        # ── Harvest from component_audit_log ──────────────────────────────────
        try:
            comp_rows = conn.execute(
                f"SELECT id, component_key, event_type, recorded_at FROM component_audit_log "
                f"WHERE 1=1{component_since} ORDER BY recorded_at DESC LIMIT 5000",
            ).fetchall()
        except Exception:
            comp_rows = []

        for row in comp_rows:
            row_id, comp_key, event_type, recorded_at = (
                row[0], row[1], row[2], row[3]
            )
            matched = (
                component_to_controls.get(comp_key, [])
                + event_to_controls.get(event_type, [])
            )
            seen: set[str] = set()
            for ctrl_id in matched:
                if ctrl_id in seen:
                    continue
                seen.add(ctrl_id)
                ev_id = str(uuid.uuid4())
                try:
                    conn.execute(
                        "INSERT INTO evidence_items "
                        "(id, control_id, framework, tenant_id, evidence_type, "
                        "source_table, source_row_id, summary, collected_at, collector) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            ev_id, ctrl_id, framework, tenant_id, "log",
                            "component_audit_log", row_id,
                            f"{event_type} on {comp_key} at {recorded_at}",
                            datetime.now(timezone.utc).isoformat(),
                            "soc2_collector",
                        ),
                    )
                    conn.commit()
                    collected += 1
                    controls_covered.add(ctrl_id)
                except Exception:
                    skipped += 1

    return {
        "collected": collected,
        "skipped": skipped,
        "controls_covered": sorted(controls_covered),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="SOC 2 evidence collector")
    p.add_argument("--tenant-id", default="default")
    p.add_argument("--framework", default="soc2")
    p.add_argument("--since", default=None, help="ISO date lower bound, e.g. 2026-01-01")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    result = collect_evidence(args.tenant_id, framework=args.framework, since=args.since)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Collected: {result['collected']}  Skipped: {result['skipped']}")
        print(f"Controls covered ({len(result['controls_covered'])}): "
              f"{', '.join(result['controls_covered'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
