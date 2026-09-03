#!/usr/bin/env python3
# CUI // SP-CTI
"""ZIG -> ZTA posture-evidence bridge (rmf-zt-02).

WHAT THIS IS FOR. ``zta_posture_evidence`` is empty, and the seven ZIG pillar
orchestrators in ``tools/security_canvas/*_pillar_orchestrator.py`` are the one
place in this tree that computes a real per-pillar signal: each of them calls
``zig_activity_tracker.set_activity_status(activity_id, 'complete', <note>)``
with a note describing what it actually deployed and counted. This module
carries those notes across into ``zta_posture_evidence`` so the ZTA scorer has
something evidence-backed to score.

WHAT IT REFUSES TO DO, and why that matters more than what it does. A ZIG
completion row is only evidence if it carries an ``evidence_note``. A row marked
``complete`` with no note is a TICK, and copying it into ``evidence_data`` would
launder a checkbox into evidence — manufacturing exactly the false confidence
rmf-zt-02 exists to remove. So ``backfill()`` writes a row ONLY for a completion
that carries a real note, and reports the rest as ``self_attested`` instead.

MEASURED on the live PostgreSQL board 2026-09-02, and this is the whole reason
the refusal is not theoretical:

    zig_activity_completions   91 rows, ALL status='complete',
                               ALL completed_by='seed-script',
                               0 carrying an evidence_note
    zig_capabilities           42 rows, ALL implementation_status='implemented',
                               0 carrying an evidence_note

So the ZIG side scores 100% and is itself a checkbox list: the completions were
written by a seed script, not by an orchestrator run. ``backfill()`` therefore
writes NOTHING on this board today and says so with the numbers. The repair is
to RUN the orchestrators (``python -m tools.security_canvas.network_pillar_orchestrator``
and its six siblings), after which their notes land here automatically.

A survey whose only honest answer is "the source is empty" is the answer. It is
not the same as a backfill that ran and found nothing to do, and it is very much
not the same as a backfill that wrote 91 empty rows and reported success.

Usage:
    python -m tools.devsecops.zta_zig_backfill --survey --json
    python -m tools.devsecops.zta_zig_backfill --backfill --project-id <id> --dry-run
    python -m tools.devsecops.zta_zig_backfill --backfill --project-id <id> --write
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.devsecops.zta_maturity_scorer import PILLARS, has_evidence_data  # noqa: E402

# The ZIG pillar slug -> ZTA pillar key mapping. This is the SAME map
# zig_assessor._try_zta_bridge uses in the other direction; the two halves of one
# bridge must not disagree about which pillar is which.
ZIG_TO_ZTA = {
    "user": "user_identity",
    "device": "device",
    "network": "network",
    "application": "application_workload",
    "data": "data",
    "visibility": "visibility_analytics",
    "automation": "automation_orchestration",
}

# Rows this module writes are namespaced, so a human reading zta_posture_evidence
# can always tell a bridged ZIG signal from a directly collected one.
EVIDENCE_TYPE_PREFIX = "zig:"


def _zig_conn():
    """Connection to the ZIG (canvas) tables.

    zig_* tables carry no classification/tenant_id column, so they must be
    reached through the canvas connection or the global RLS predicate raises
    UndefinedColumn on every query.
    """
    from tools.security_canvas.db.init_db import get_connection

    return get_connection()


def _zta_conn():
    from tools.db.storage import get_connection

    return get_connection()


def collect_zig_signals() -> dict:
    """Read the ZIG side and split each pillar's completions into two counts.

    Returns a dict keyed by ZTA pillar::

        {
          "network": {
            "zig_slug": "network",
            "capabilities": 6,
            "activities": 14,
            "evidence_backed": [ {activity_id, capability_id, note, completed_by}, ... ],
            "self_attested": 14,        # complete, but carrying NO note
            "incomplete": 0,
          },
          ...
        }

    ``evidence_backed`` is a LIST (the notes are the evidence and are carried
    whole); ``self_attested`` is a COUNT, because there is nothing to carry.
    Merging them into one "complete" number is the defect this module refuses.
    """
    out = {
        zta: {
            "zig_slug": zig,
            "capabilities": 0,
            "activities": 0,
            "evidence_backed": [],
            "self_attested": 0,
            "incomplete": 0,
        }
        for zig, zta in ZIG_TO_ZTA.items()
    }

    conn = _zig_conn()
    try:
        caps = conn.execute(
            "SELECT id, pillar_slug, implementation_status, evidence_note "
            "FROM zig_capabilities"
        ).fetchall()
        cap_pillar = {c["id"]: c["pillar_slug"] for c in caps}
        for c in caps:
            zta = ZIG_TO_ZTA.get(c["pillar_slug"])
            if zta:
                out[zta]["capabilities"] += 1

        acts = conn.execute(
            "SELECT id, capability_id FROM zig_activities"
        ).fetchall()
        act_cap = {a["id"]: a["capability_id"] for a in acts}
        for a in acts:
            zta = ZIG_TO_ZTA.get(cap_pillar.get(a["capability_id"], ""))
            if zta:
                out[zta]["activities"] += 1

        comps = conn.execute(
            "SELECT activity_id, status, evidence_note, completed_by, completed_at "
            "FROM zig_activity_completions"
        ).fetchall()
    finally:
        conn.close()

    for row in comps:
        cap_id = act_cap.get(row["activity_id"])
        zta = ZIG_TO_ZTA.get(cap_pillar.get(cap_id, ""))
        if not zta:
            continue
        if row["status"] != "complete":
            out[zta]["incomplete"] += 1
        elif has_evidence_data(row["evidence_note"]):
            out[zta]["evidence_backed"].append(
                {
                    "activity_id": row["activity_id"],
                    "capability_id": cap_id,
                    "note": row["evidence_note"],
                    "completed_by": row["completed_by"],
                    "completed_at": row["completed_at"],
                }
            )
        else:
            # Marked done with nothing behind it. NOT evidence.
            out[zta]["self_attested"] += 1

    return out


def survey() -> dict:
    """Report what the ZIG side can supply as ZTA evidence, writing nothing."""
    try:
        signals = collect_zig_signals()
    except Exception as exc:  # noqa: BLE001 - an unreadable source is not an empty one
        return {
            "state": "unreadable",
            "error": f"ZIG tables unreadable: {exc}",
            "backfillable": None,
            "self_attested": None,
        }

    backfillable = sum(len(v["evidence_backed"]) for v in signals.values())
    attested = sum(v["self_attested"] for v in signals.values())
    activities = sum(v["activities"] for v in signals.values())

    if activities == 0:
        state = "no_zig_data"
    elif backfillable == 0:
        # The live board's case: the ZIG programme is complete on paper and
        # carries no evidence at all.
        state = "self_attested_only"
    else:
        state = "backfillable"

    return {
        "state": state,
        "declared_pillars": len(PILLARS),
        "zig_activities": activities,
        # Two numbers, never merged — the same rule as the scorer itself.
        "backfillable": backfillable,
        "self_attested": attested,
        "per_pillar": {
            k: {
                "zig_slug": v["zig_slug"],
                "capabilities": v["capabilities"],
                "activities": v["activities"],
                "evidence_backed": len(v["evidence_backed"]),
                "self_attested": v["self_attested"],
                "incomplete": v["incomplete"],
            }
            for k, v in signals.items()
        },
        "note": (
            "No ZIG completion carries an evidence_note, so there is nothing to "
            "backfill. Run the seven pillar orchestrators "
            "(tools/security_canvas/*_pillar_orchestrator.py) — they write a real "
            "note with every set_activity_status call. Copying a note-less "
            "completion would turn a checkbox into evidence."
            if state == "self_attested_only"
            else ""
        ),
    }


def backfill(project_id: str, write: bool = False) -> dict:
    """Write one zta_posture_evidence row per EVIDENCE-BACKED ZIG completion.

    A completion with no ``evidence_note`` is never written — not as a row with
    NULL ``evidence_data``, and not as a row at all. Writing it would move a tick
    from one table to another and make the emptiness harder to see, not easier.

    Idempotent: rows are keyed ``zig:<activity_id>`` per project and re-running
    updates the note in place rather than adding a duplicate.
    """
    result = survey()
    result["project_id"] = project_id
    result["written"] = 0
    result["skipped_self_attested"] = result.get("self_attested")
    result["dry_run"] = not write

    if result["state"] != "backfillable":
        result["outcome"] = "nothing_to_backfill"
        return result

    signals = collect_zig_signals()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for zta_pillar, data in signals.items():
        for ev in data["evidence_backed"]:
            rows.append(
                (
                    f"{EVIDENCE_TYPE_PREFIX}{ev['activity_id']}",
                    json.dumps(
                        {
                            "source": "zig_activity_completion",
                            "zta_pillar": zta_pillar,
                            "zig_pillar": data["zig_slug"],
                            "capability_id": ev["capability_id"],
                            "activity_id": ev["activity_id"],
                            "note": ev["note"],
                            "completed_by": ev["completed_by"],
                            "completed_at": ev["completed_at"],
                        }
                    ),
                )
            )

    result["candidates"] = len(rows)
    if not write:
        result["outcome"] = "dry_run"
        return result

    conn = _zta_conn()
    try:
        for etype, payload in rows:
            existing = conn.execute(
                "SELECT id FROM zta_posture_evidence "
                "WHERE project_id = %s AND evidence_type = %s",
                (project_id, etype),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE zta_posture_evidence "
                    "SET evidence_data = %s, status = %s, collected_at = %s "
                    "WHERE id = %s",
                    (payload, "current", now, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO zta_posture_evidence "
                    "(id, project_id, evidence_type, evidence_data, status, collected_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        f"zev-{uuid.uuid4().hex[:12]}",
                        project_id,
                        etype,
                        payload,
                        "current",
                        now,
                    ),
                )
            result["written"] += 1
        conn.commit()
    finally:
        conn.close()

    result["outcome"] = "written"
    return result


def main():
    parser = argparse.ArgumentParser(description="ZIG -> ZTA posture-evidence bridge")
    parser.add_argument("--survey", action="store_true", help="Report what ZIG can supply")
    parser.add_argument("--backfill", action="store_true", help="Backfill zta_posture_evidence")
    parser.add_argument("--project-id", help="Project to backfill into")
    parser.add_argument("--write", action="store_true", help="Actually write rows")
    parser.add_argument("--dry-run", action="store_true", help="Default; writes nothing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.backfill:
        if not args.project_id:
            parser.error("--backfill requires --project-id")
        result = backfill(args.project_id, write=args.write and not args.dry_run)
    else:
        result = survey()

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"ZIG -> ZTA evidence bridge: {result['state']}")
    if result.get("error"):
        print(f"  ERROR: {result['error']}")
        return
    print(f"  ZIG activities:            {result.get('zig_activities')}")
    print(f"  Evidence-backed (usable):  {result.get('backfillable')}")
    print(f"  Self-attested (refused):   {result.get('self_attested')}")
    if result.get("note"):
        print(f"\n  {result['note']}")
    if args.backfill:
        print(f"\n  Outcome: {result.get('outcome')}  written={result.get('written')}")


if __name__ == "__main__":
    main()
