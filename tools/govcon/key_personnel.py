#!/usr/bin/env python3
# CUI // SP-CTI
"""Proposal key personnel — the BID side's person -> LCAT registry.

prem-pstaff-01. Before this, the bid side had no people table at all:

  * ``pg_lcat_allocations`` is task -> LCAT -> FTE. It never names a human.
  * ``pma_personnel`` (tools/govcon/personnel_manager.py) is POST-AWARD, keyed on
    ``contract_id`` — it cannot hold anyone until after we have already won.
  * So ``program_bridge._gather_key_personnel`` **regex-scraped capitalised names out
    of the free text of proposal_section_drafts**, deduped them, kept the ones longer
    than four characters, and swallowed every exception into an empty list. That fed
    the "Key Personnel & Staffing Plan" section of a real bid.

A name scraped out of prose carries no LCAT, no qualification verdict, and no
evidence. It cannot be defended at debrief, and it cannot be priced.

## Every mapping carries its evidence, or it is refused

``register_person`` REFUSES an unevidenced person -> LCAT mapping. It does not store
it with an empty evidence field and hope someone fills it in later.

This is the same defect class as an uncited win theme (see tools/quality/
citation_grounding.py and the pg_win_themes intake): an assertion that reaches a
proposal with nothing behind it is one nobody can defend when the customer asks
"why is this person a Senior Systems Engineer?". The refusal is enforced twice, on
purpose — in ``register_person`` so the caller gets a reason, and by a CHECK
constraint in migration 266 so no other writer can bypass it.

Tables:
  - proposal_key_personnel (upsert on (opportunity_id, person_ref))
  - audit_trail (append-only — NIST AU-2)

Usage:
    python tools/govcon/key_personnel.py --opportunity-id opp-1 --list --json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.key_personnel")

# The verdicts compass's tools/staffing/qualification.py produces. The CHECK
# constraint in migration 266 is DERIVED from this tuple rather than repeating it —
# per CLAUDE.md, a CHECK and a Python constant that state the same rule twice will
# eventually state it differently.
QUALIFICATION_VERDICTS = ("qualified", "gap", "exceeds")

# Where the mapping came from. `scraped` exists so the legacy program_bridge path can
# be represented honestly if anyone ever backfills it — NOT so new writes can use it.
PERSON_SOURCES = ("compass", "manual", "resume_match", "scraped")

_TABLE = "proposal_key_personnel"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sql_in_list(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def table_ddl() -> str:
    """CREATE TABLE for proposal_key_personnel, with CHECKs derived from the constants.

    ``classification`` and ``tenant_id`` are present FROM THE START, not retrofitted:
    get_connection() injects an RLS predicate over them, and a table that lacks them
    raises UndefinedColumn on every query the moment it is read through the normal
    path (that retrofit is exactly what migration 245 had to do to every other
    pg_*/proposal_* table).

    The evidence CHECK is the refusal, in the schema. register_person() also refuses,
    but a constraint cannot be forgotten by a future writer.
    """
    return f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id                    TEXT PRIMARY KEY,
    opportunity_id        TEXT NOT NULL,
    person_ref            TEXT NOT NULL,
    name                  TEXT NOT NULL,
    proposed_lcat         TEXT NOT NULL,
    qualification_verdict TEXT NOT NULL
        CHECK (qualification_verdict IN ({_sql_in_list(QUALIFICATION_VERDICTS)})),
    evidence_json         TEXT NOT NULL
        CHECK (evidence_json <> '' AND evidence_json <> '[]'),
    source                TEXT
        CHECK (source IS NULL OR source IN ({_sql_in_list(PERSON_SOURCES)})),
    key_person            INTEGER NOT NULL DEFAULT 0,
    gaps_json             TEXT NOT NULL DEFAULT '[]',
    tenant_id             TEXT NOT NULL DEFAULT 'default',
    classification        TEXT NOT NULL DEFAULT 'CUI',
    created_at            TIMESTAMP,
    updated_at            TIMESTAMP,
    UNIQUE (opportunity_id, person_ref)
)
"""


def _audit(conn, action: str, details: str) -> None:
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (_now(), "govcon.key_personnel", "key_personnel",
             action, details, "proposal_genesis"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Audit is best-effort here; the caller's write must not fail because the
        # audit table is unavailable. The row itself is the record of truth.
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def normalize_evidence(evidence: Any) -> List[Dict[str, str]]:
    """Normalize caller evidence into ``[{"claim", "source"}, ...]``.

    Accepts the two shapes a capture tool actually has on hand: structured citation
    rows, or one block of rendered text. A row with no ``claim`` cites nothing and is
    dropped. An EMPTY result means unevidenced — and register_person refuses on it.
    """
    if isinstance(evidence, dict):
        evidence = [evidence]

    if isinstance(evidence, list):
        rows: List[Dict[str, str]] = []
        for item in evidence:
            if isinstance(item, dict):
                claim = str(item.get("claim") or "").strip()
                if claim:
                    rows.append({
                        "claim": claim,
                        "source": str(item.get("source") or "").strip(),
                    })
            else:
                text = str(item or "").strip()
                if text:
                    rows.append({"claim": text, "source": ""})
        return rows

    text = str(evidence or "").strip()
    return [{"claim": text, "source": ""}] if text else []


def register_person(
    *,
    opportunity_id: str,
    person_ref: str,
    name: str,
    proposed_lcat: str,
    qualification_verdict: str,
    evidence: Any,
    source: str = "compass",
    key_person: bool = False,
    gaps: Any = None,
    tenant_id: str = "default",
    classification: str = "CUI",
    conn=None,
) -> Dict[str, Any]:
    """Register (or re-register) an EVIDENCED person -> LCAT mapping for a bid.

    Returns ``{"status": "registered", "id": ...}`` on success, or
    ``{"status": "refused", "reason": ...}`` — and stores NOTHING — when the mapping
    is unevidenced or the verdict is not one compass actually produces.

    Refusing is the feature. A person proposed for a labour category with no evidence
    behind the claim is an assertion that will not survive a debrief, and storing it
    with an empty evidence field just moves the problem downstream into the proposal.

    ``gaps`` travel WITH a ``gap`` verdict, deliberately. A person with a gap can still
    be the right person to bid — but the bid side has to see the gap when they make
    that call and price the risk, not discover it at the debrief. A verdict of "gap"
    with the gaps thrown away is barely better than no verdict at all.
    """
    opportunity_id = (opportunity_id or "").strip()
    person_ref = (person_ref or "").strip()
    name = (name or "").strip()
    proposed_lcat = (proposed_lcat or "").strip()
    verdict = (qualification_verdict or "").strip().lower()

    for field, value in (
        ("opportunity_id", opportunity_id),
        ("person_ref", person_ref),
        ("name", name),
        ("proposed_lcat", proposed_lcat),
    ):
        if not value:
            return {"status": "refused", "reason": f"{field} is required"}

    if verdict not in QUALIFICATION_VERDICTS:
        return {
            "status": "refused",
            "reason": (
                f"qualification_verdict must be one of {list(QUALIFICATION_VERDICTS)}, "
                f"got {qualification_verdict!r}"
            ),
        }

    rows = normalize_evidence(evidence)
    if not rows:
        return {
            "status": "refused",
            "reason": (
                f"'{name}' -> '{proposed_lcat}' carries NO evidence. An unevidenced "
                "person->LCAT mapping is refused, not stored empty: it would reach the "
                "proposal as an assertion nobody can defend at debrief."
            ),
        }

    if source not in PERSON_SOURCES:
        return {
            "status": "refused",
            "reason": f"source must be one of {list(PERSON_SOURCES)}, got {source!r}",
        }

    close_after = conn is None
    conn = conn or get_connection()
    try:
        now = _now()
        evidence_json = json.dumps(rows, ensure_ascii=False)
        gaps_json = json.dumps(list(gaps or []), ensure_ascii=False)
        key_flag = 1 if key_person else 0
        existing = conn.execute(
            f"SELECT id FROM {_TABLE} WHERE opportunity_id = %s AND person_ref = %s",  # nosec B608
            (opportunity_id, person_ref),
        ).fetchone()

        if existing:
            row_id = dict(existing)["id"]
            conn.execute(
                f"UPDATE {_TABLE} SET name = %s, proposed_lcat = %s, "  # nosec B608
                "qualification_verdict = %s, evidence_json = %s, source = %s, "
                "key_person = %s, gaps_json = %s, updated_at = %s WHERE id = %s",
                (name, proposed_lcat, verdict, evidence_json, source,
                 key_flag, gaps_json, now, row_id),
            )
            action = "reregistered"
        else:
            row_id = str(uuid.uuid4())
            conn.execute(
                f"INSERT INTO {_TABLE} (id, opportunity_id, person_ref, name, proposed_lcat, "  # nosec B608
                "qualification_verdict, evidence_json, source, key_person, gaps_json, "
                "tenant_id, classification, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, opportunity_id, person_ref, name, proposed_lcat, verdict,
                 evidence_json, source, key_flag, gaps_json, tenant_id, classification,
                 now, now),
            )
            action = "registered"

        _audit(
            conn,
            action,
            f"{name} -> {proposed_lcat} ({verdict}) for {opportunity_id} "
            f"[{len(rows)} evidence item(s), source={source}]",
        )
        conn.commit()
        return {
            "status": "registered",
            "id": row_id,
            "action": action,
            "evidence_count": len(rows),
        }
    finally:
        if close_after:
            try:
                conn.close()
            except Exception:
                pass


def list_key_personnel(opportunity_id: str, conn=None) -> List[Dict[str, Any]]:
    """List the registered person -> LCAT mappings for an opportunity."""
    close_after = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            f"SELECT id, opportunity_id, person_ref, name, proposed_lcat, "  # nosec B608
            f"qualification_verdict, evidence_json, source, key_person, gaps_json, "
            f"created_at, updated_at "
            f"FROM {_TABLE} WHERE opportunity_id = %s ORDER BY name",
            (opportunity_id,),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
            except Exception:
                d["evidence"] = []
            try:
                d["gaps"] = json.loads(d.pop("gaps_json", None) or "[]")
            except Exception:
                d["gaps"] = []
            d["key_person"] = bool(d.get("key_person"))
            out.append(d)
        return out
    finally:
        if close_after:
            try:
                conn.close()
            except Exception:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Proposal key personnel registry")
    ap.add_argument("--opportunity-id", required=True)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list:
        people = list_key_personnel(args.opportunity_id)
        if args.json:
            print(json.dumps({"opportunity_id": args.opportunity_id, "people": people}, indent=2))
        else:
            for p in people:
                print(f"  {p['name']:28} {p['proposed_lcat']:28} {p['qualification_verdict']:10} "
                      f"({len(p['evidence'])} evidence)")
            if not people:
                print("  (none registered)")


if __name__ == "__main__":
    main()
