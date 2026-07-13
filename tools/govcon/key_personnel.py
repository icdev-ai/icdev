# CUI // SP-CTI
"""Proposal Key Personnel registry — the BID side's person -> LCAT mapping.

The bid side had no such table (prem-pstaff-01): ``pg_lcat_allocations`` is
task -> LCAT -> FTE with no people in it, and ``personnel_manager.py``
(``pma_personnel``) is post-award, keyed on contract_id. So
``program_bridge._gather_key_personnel`` regex-scraped names out of draft prose.
``proposal_key_personnel`` (migration 266) replaces that guess with a registry.

EVERY MAPPING CARRIES ITS EVIDENCE. ``register_person`` refuses an unevidenced
person -> LCAT mapping instead of storing it with an empty evidence field: the
mapping is what a proposal asserts to the government about who will do the work,
and an unevidenced assertion is one nobody can defend at debrief. This is the
same rule the win-theme registry enforces on uncited themes, and the DB CHECK on
``evidence_json`` is the last line of it.

Tables used:
    - proposal_key_personnel (upsert on (opportunity_id, person_ref))
    - audit_trail (append-only write — NIST AU-2)

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
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# The qualification verdicts compass's qualification.py produces. Mirrored by the
# CHECK constraint in migration 266 — change both together.
VERDICTS = ("qualified", "gap", "exceeds")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, action: str, details: str) -> None:
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), _now(), "govcon.key_personnel", "key_personnel",
             action, details, "proposal_genesis"),
        )
    except Exception:
        pass


def normalize_evidence(evidence: Any) -> List[Dict[str, str]]:
    """Normalize caller evidence into ``[{"claim", "source"}, ...]``.

    Accepts the two shapes a capture tool actually has on hand: structured
    citation rows, or one block of rendered text. Rows without a claim are
    dropped — they cite nothing. An empty result means UNEVIDENCED, and
    ``register_person`` refuses on it.
    """
    if isinstance(evidence, dict):
        evidence = [evidence]
    if isinstance(evidence, list):
        rows = []
        for item in evidence:
            if isinstance(item, dict):
                claim = str(item.get("claim") or "").strip()
                if claim:
                    rows.append({"claim": claim,
                                 "source": str(item.get("source") or "").strip()})
            elif str(item or "").strip():
                rows.append({"claim": str(item).strip(), "source": ""})
        return rows
    text = str(evidence or "").strip()
    return [{"claim": text, "source": ""}] if text else []


def register_person(
    opportunity_id: str,
    name: str,
    proposed_lcat: str,
    evidence: Any,
    *,
    person_ref: Optional[str] = None,
    qualification_verdict: str = "qualified",
    source: Optional[str] = None,
    tenant_id: str = "default",
    classification: str = "CUI",
) -> Dict[str, Any]:
    """Register (or re-register) an EVIDENCED person -> LCAT mapping for a bid.

    Re-pushing the same person for the same opportunity updates the row in place;
    ``person_ref`` is the caller's stable identifier, and falls back to the name
    so a caller without one still converges on a single row per person.

    Returns ``{"status": "refused", ...}`` — never a stored row — when the
    mapping carries no evidence.
    """
    name = str(name or "").strip()
    proposed_lcat = str(proposed_lcat or "").strip()
    if not opportunity_id or not name or not proposed_lcat:
        return {"status": "error",
                "message": "opportunity_id, name, and proposed_lcat are required"}
    if qualification_verdict not in VERDICTS:
        return {"status": "error",
                "message": f"qualification_verdict must be one of {VERDICTS}"}

    rows = normalize_evidence(evidence)
    if not rows:
        return {
            "status": "refused",
            "name": name,
            "reason": ("no qualifying evidence — an unevidenced person -> LCAT "
                       "mapping is an assertion nobody can defend at debrief"),
        }

    person_ref = str(person_ref or "").strip() or f"name:{name.lower()}"
    evidence_json = json.dumps(rows)
    record_id = f"pkp-{uuid.uuid4().hex[:12]}"
    now = _now()

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO proposal_key_personnel "
            "(id, opportunity_id, person_ref, name, proposed_lcat, "
            " qualification_verdict, evidence_json, source, tenant_id, "
            " classification, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (opportunity_id, person_ref) DO UPDATE SET "
            "  name = EXCLUDED.name, "
            "  proposed_lcat = EXCLUDED.proposed_lcat, "
            "  qualification_verdict = EXCLUDED.qualification_verdict, "
            "  evidence_json = EXCLUDED.evidence_json, "
            "  source = EXCLUDED.source, "
            "  updated_at = EXCLUDED.updated_at",
            (record_id, opportunity_id, person_ref, name, proposed_lcat,
             qualification_verdict, evidence_json, source, tenant_id,
             classification, now, now),
        )
        _audit(conn, "register_person",
               f"{name} -> {proposed_lcat} ({qualification_verdict}) "
               f"for {opportunity_id}, {len(rows)} evidence item(s)")
        conn.commit()
    except Exception as exc:
        conn.close()
        return {"status": "error", "message": str(exc)}
    conn.close()

    return {
        "status": "ok",
        "person_id": record_id,
        "person_ref": person_ref,
        "name": name,
        "proposed_lcat": proposed_lcat,
        "qualification_verdict": qualification_verdict,
        "evidence_count": len(rows),
    }


def list_key_personnel(opportunity_id: str) -> List[Dict[str, Any]]:
    """List the registered person -> LCAT mappings for an opportunity."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, opportunity_id, person_ref, name, proposed_lcat, "
            "       qualification_verdict, evidence_json, source, created_at, updated_at "
            "FROM proposal_key_personnel WHERE opportunity_id = %s ORDER BY name",
            (opportunity_id,),
        ).fetchall()
    finally:
        conn.close()

    people = []
    for row in rows:
        person = dict(row)
        try:
            person["evidence"] = json.loads(person.pop("evidence_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            person["evidence"] = []
        people.append(person)
    return people


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Proposal key personnel registry (person -> LCAT, evidenced)")
    parser.add_argument("--opportunity-id", required=True)
    parser.add_argument("--list", action="store_true", help="List registered personnel")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    people = list_key_personnel(args.opportunity_id)
    result = {"status": "ok", "opportunity_id": args.opportunity_id,
              "personnel": people, "count": len(people)}
    if args.json or not args.list:
        print(json.dumps(result, indent=2, default=str))
    else:
        for person in people:
            print(f"  {person['name']:30s} {person['proposed_lcat']:35s} "
                  f"{person['qualification_verdict']}")


if __name__ == "__main__":
    main()
