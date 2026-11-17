# CUI // SP-CTI
"""The decisions a person takes on modernisation work, read back from the ledger (icx-mdz-01).

Every modernisation decision runs through a declared template under ``args/workflow_hitl_templates/``
(``tools/workflow_hitl/declared.py``). For four of them the decided step IS the record, so there is no second table to
drift from it:

    decl-wft-mdz-7r-strategy             the 7R strategy ICDEV proposes for an application (subject: the assessment id).
                                         A PROPOSAL to ICDEV[OS]: its mission owner accepts a strategy through the
                                         signed migration-assessment workflow, and ``icos_decisions.py may-plan`` is
                                         the only answer to "may a migration be planned".
    decl-wft-mdz-cam-refactor-approve    a person's review of one CAM job's generated output (subject: the job id).
    decl-wft-mdz-process-review          (icx-mdz-03) a person's review of one extracted process at its step-signature
                                         digest (subject: "<app id>:<process id>"); the exporter reads it.
    decl-wft-mdz-capacity-review         (icx-mdz-05) a person's review of one declared team capacity at its digest
                                         (subject: "capacity:<application name or legacy id>"); wave_schedule dates
                                         the application's waves only from an approved one.

The other three (AI-Ify promotion and scan deletion, Process-Ify observed-draft review) gate an effect their own
stores keep; their steps are read here only to show who took them.

Library, no CLI. Reads only; the steps are taken by the routes that run their effects.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

TEMPLATE_7R = "decl-wft-mdz-7r-strategy"
TEMPLATE_CAM = "decl-wft-mdz-cam-refactor-approve"
TEMPLATE_AIIFY_PROMOTE = "decl-wft-mdz-aiify-promote"
TEMPLATE_AIIFY_DELETE = "decl-wft-mdz-aiify-scan-delete"
TEMPLATE_OBSERVED = "decl-wft-mdz-processify-observed-approve"
TEMPLATE_PROCESS = "decl-wft-mdz-process-review"
TEMPLATE_CAPACITY = "decl-wft-mdz-capacity-review"

STRATEGIES = ("rehost", "replatform", "refactor", "rearchitect", "repurchase", "retire", "retain")

#: What the 7R record is, said once so the page, the API and the MCP tool say the same thing.
SEVEN_R_BINDING = ("a proposal to ICDEV[OS]: its mission owner accepts a strategy through the signed "
                   "migration-assessment workflow; plan a migration only when icos_decisions.py may-plan allows it")

_COLS = ("step_id", "subject", "surface", "actor", "actor_role", "decision", "affirmative", "reason",
         "instance_id", "payload_json", "created_at")


def decided(template_id: str, subjects: Optional[Iterable[str]] = None, conn: Any = None) -> dict[str, dict]:
    """``{subject: the latest decided step}`` for one template, optionally only for ``subjects``.

    Raises when the ledger cannot be read: a caller that shows "no decision" must know it looked.
    """
    from tools.db.storage import get_connection

    want = {str(s) for s in subjects} if subjects is not None else None
    own = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            "SELECT " + ", ".join(_COLS) + " FROM wf_decision_steps "  # nosec B608 — constant columns
            "WHERE template_id = %s AND event = 'decided' ORDER BY created_at, event_id",
            (template_id,),
        ).fetchall()
    finally:
        if own:
            conn.close()
    out: dict[str, dict] = {}
    for r in rows:
        row = dict(zip(_COLS, tuple(r)))
        if want is not None and row["subject"] not in want:
            continue
        try:
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
        except ValueError:
            row["payload"] = {}
        row["affirmative"] = bool(row["affirmative"])
        row["created_at"] = str(row["created_at"] or "")
        out[row["subject"]] = row          # ordered by time: the last one wins
    return out


def strategy_record(row: Optional[dict]) -> Optional[dict]:
    """The page/API shape of one 7R record, or None when no person recorded one."""
    if not row:
        return None
    payload = row.get("payload") or {}
    return {"step_id": row["step_id"], "decision": row["decision"], "recorded": row["affirmative"],
            "strategy": payload.get("strategy") if row["affirmative"] else None,
            "recommended": payload.get("recommended_strategy"), "application": payload.get("legacy_app_id"),
            "by": row["actor"], "role": row["actor_role"], "reason": row["reason"], "at": row["created_at"],
            "binding": False, "means": SEVEN_R_BINDING}


def job_review(row: Optional[dict]) -> Optional[dict]:
    """The page/API shape of one CAM job review, or None when no person reviewed it."""
    if not row:
        return None
    return {"step_id": row["step_id"], "decision": row["decision"], "approved": row["affirmative"],
            "by": row["actor"], "role": row["actor_role"], "reason": row["reason"], "at": row["created_at"],
            "artefacts": (row.get("payload") or {}).get("artefacts") or []}
