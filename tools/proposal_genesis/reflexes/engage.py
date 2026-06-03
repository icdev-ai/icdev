#!/usr/bin/env python3
# CUI // SP-CTI
"""R4: Engage Reflex — CRM account/contact tracking + engagement scoring.

Scans tracked opportunities for associated accounts/contacts,
logs interactions from opportunity activity, and computes engagement
scores per account.  Scanner-tier only (zero Claude tokens).

Pipeline: Independent schedule (every 4h), not part of main chain.
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level constants — Engage Reflex (R4) thresholds, limits & scoring.
# Extracted from inline magic numbers (AI-ify opp 5402, hardcoded_threshold ->
# anomaly_detection). Overridable from proposal_genesis_config.yaml under
# reflexes.engage. Change config, not code.
# ---------------------------------------------------------------------------
_OPPS_WITHOUT_ACCOUNTS_LIMIT   = 20    # opps scanned for missing CRM accounts per run
_AUDIT_INTERACTION_LOOKBACK_DAYS = 1   # window of audit events mapped to interactions
_AUDIT_INTERACTION_LIMIT       = 50    # audit events pulled per run for interaction logging

# Engagement-score saturation points — count at which a component reaches 1.0.
_FREQUENCY_SATURATION_COUNT    = 10.0  # interactions for full frequency score
_PIPELINE_SATURATION_COUNT     = 5.0   # tracked opportunities for full pipeline score

# Engagement-score component weights (must sum to 1.0).
_ENGAGEMENT_WEIGHT_RECENCY     = 0.30
_ENGAGEMENT_WEIGHT_FREQUENCY   = 0.25
_ENGAGEMENT_WEIGHT_PIPELINE    = 0.25
_ENGAGEMENT_WEIGHT_WIN_RATE    = 0.20

_RECENCY_DECAY_DAYS            = 90.0  # recency score decays linearly to 0 over this many days


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Account discovery from opportunities
# ---------------------------------------------------------------------------


def _get_opportunities_without_accounts() -> List[Dict]:
    """Find tracked opportunities that don't yet have a CRM account."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT po.id, po.title, po.agency, po.naics_code, po.status
            FROM proposal_opportunities po
            LEFT JOIN pg_crm_accounts ca
                ON ca.name = po.agency AND ca.status = 'active'
            WHERE po.status IN ('tracking', 'drafting', 'reviewing')
            AND po.agency IS NOT NULL
            AND po.agency != ''
            AND ca.id IS NULL
            ORDER BY po.created_at DESC
            LIMIT {limit}
        """.format(limit=_OPPS_WITHOUT_ACCOUNTS_LIMIT)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _create_account_from_opportunity(opp: Dict) -> Optional[str]:
    """Create a CRM account from an opportunity's agency."""
    conn = get_connection()
    account_id = _generate_id("pgacct")
    now = _utcnow_iso()
    agency = opp.get("agency", "") or ""
    if not agency:
        return None

    try:
        # Check if account already exists (race condition guard)
        existing = conn.execute(
            "SELECT id FROM pg_crm_accounts WHERE name = ?",
            (agency,),
        ).fetchone()
        if existing:
            return existing["id"]

        naics = opp.get("naics_code", "") or ""
        account_type = _classify_account_type(agency)

        conn.execute(
            """
            INSERT INTO pg_crm_accounts
                (id, name, agency, account_type, naics_codes, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                account_id,
                agency,
                agency,
                account_type,
                naics,
                "active",
                now,
                now,
            ),
        )

        # Audit
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _generate_id("pgaudit"),
                "account_created",
                "engage",
                "green",
                opp["id"],
                json.dumps({"account_id": account_id, "agency": agency}),
                1,
                now,
            ),
        )
        conn.commit()
        return account_id
    except Exception:
        return None
    finally:
        conn.close()


def _classify_account_type(agency: str) -> str:
    """Classify account type from agency name."""
    agency_lower = (agency or "").lower()
    gov_keywords = [
        "department",
        "agency",
        "bureau",
        "office",
        "administration",
        "command",
        "corps",
        "center",
        "institute",
        "commission",
        "dod",
        "army",
        "navy",
        "air force",
        "space force",
        "dhs",
        "fbi",
        "cia",
        "nsa",
        "disa",
        "dla",
        "gsa",
        "va ",
        "hhs",
        "cms",
        "faa",
        "fema",
        "usda",
    ]
    if any(kw in agency_lower for kw in gov_keywords):
        return "government"
    return "other"


# ---------------------------------------------------------------------------
# Interaction logging from opportunity activity
# ---------------------------------------------------------------------------


def _get_recent_audit_interactions() -> List[Dict]:
    """Pull recent audit events that represent interactions.

    Maps audit event types to CRM interaction types.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.id, a.event_type, a.reflex_name, a.opportunity_id,
                   a.details, a.created_at,
                   po.agency
            FROM pg_proposal_genesis_audit a
            LEFT JOIN proposal_opportunities po ON po.id = a.opportunity_id
            WHERE a.opportunity_id IS NOT NULL
            AND a.created_at > datetime('now', '-{lookback} day')
            AND a.event_type IN (
                'pg.reflex.completed', 'capture_plan_created',
                'brief_generated', 'draft_completed', 'quality_checked'
            )
            ORDER BY a.created_at DESC
            LIMIT {limit}
        """.format(
            lookback=_AUDIT_INTERACTION_LOOKBACK_DAYS,
            limit=_AUDIT_INTERACTION_LIMIT,
        )).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _log_interaction(
    account_id: str,
    interaction_type: str,
    subject: str,
    opportunity_id: Optional[str] = None,
    notes: str = "",
) -> Optional[str]:
    """Log a CRM interaction."""
    conn = get_connection()
    interaction_id = _generate_id("pgint")
    now = _utcnow_iso()
    try:
        conn.execute(
            """
            INSERT INTO pg_crm_interactions
                (id, contact_id, account_id, interaction_type, subject,
                 notes, opportunity_id, interaction_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                interaction_id,
                "",  # No specific contact for system-generated interactions
                account_id,
                interaction_type,
                subject,
                notes,
                opportunity_id,
                now,
                now,
            ),
        )
        conn.commit()
        return interaction_id
    except Exception:
        return None
    finally:
        conn.close()


def _map_event_to_interaction(event_type: str) -> Optional[str]:
    """Map an audit event type to a CRM interaction type."""
    mapping = {
        "pg.reflex.completed": "other",
        "capture_plan_created": "rfi_response",
        "brief_generated": "other",
        "draft_completed": "rfi_response",
        "quality_checked": "other",
    }
    return mapping.get(event_type)


def _get_account_for_agency(agency: str) -> Optional[str]:
    """Look up account ID by agency name."""
    if not agency:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM pg_crm_accounts WHERE name = ? AND status = 'active'",
            (agency,),
        ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Engagement scoring
# ---------------------------------------------------------------------------


def _compute_win_rate(conn, agency_name: str) -> float:
    """Compute historical win rate for an agency from pg_win_loss_records.

    Returns 0.0-1.0 (wins / total outcomes).  Falls back to 0.0 if no
    win/loss data exists yet.
    """
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN wl.outcome = 'won' THEN 1 ELSE 0 END) as wins
            FROM pg_win_loss_records wl
            JOIN sam_gov_opportunities o ON o.id = wl.opportunity_id
            WHERE LOWER(o.agency) = LOWER(?)
            AND wl.outcome IN ('won', 'lost')
        """,
            (agency_name,),
        ).fetchone()
        if not row or row["total"] == 0:
            return 0.0
        return round(row["wins"] / row["total"], 3)
    except Exception:
        return 0.0


def _compute_engagement_scores() -> List[Dict]:
    """Compute engagement scores for all active accounts.

    Scoring dimensions (deterministic):
      - Interaction recency (30% weight): recent interactions score higher
      - Interaction frequency (25% weight): more interactions = higher engagement
      - Opportunity pipeline (25% weight): more tracked opportunities = higher
      - Win rate (20% weight): historical win rate for this account
    """
    conn = get_connection()
    scores = []
    try:
        accounts = conn.execute("SELECT id, name FROM pg_crm_accounts WHERE status = 'active'").fetchall()

        for acct in accounts:
            acct_id = acct["id"]
            acct_name = acct["name"]

            # Interaction count and recency
            interaction_stats = conn.execute(
                """
                SELECT COUNT(*) as cnt,
                       MAX(interaction_date) as last_date
                FROM pg_crm_interactions
                WHERE account_id = ?
            """,
                (acct_id,),
            ).fetchone()

            interaction_count = interaction_stats["cnt"] if interaction_stats else 0
            last_interaction = interaction_stats["last_date"] if interaction_stats else None

            # Opportunity count
            opp_stats = conn.execute(
                """
                SELECT COUNT(*) as cnt
                FROM proposal_opportunities
                WHERE agency = ?
                AND status IN ('tracking', 'drafting', 'reviewing')
            """,
                (acct_name,),
            ).fetchone()

            opportunity_count = opp_stats["cnt"] if opp_stats else 0

            # Compute score components
            recency_score = _score_recency(last_interaction)
            frequency_score = min(1.0, interaction_count / _FREQUENCY_SATURATION_COUNT)
            pipeline_score = min(1.0, opportunity_count / _PIPELINE_SATURATION_COUNT)
            win_rate = _compute_win_rate(conn, acct_name)

            # Weighted composite
            score = (
                recency_score * _ENGAGEMENT_WEIGHT_RECENCY
                + frequency_score * _ENGAGEMENT_WEIGHT_FREQUENCY
                + pipeline_score * _ENGAGEMENT_WEIGHT_PIPELINE
                + win_rate * _ENGAGEMENT_WEIGHT_WIN_RATE
            )

            breakdown = {
                "recency": round(recency_score, 3),
                "frequency": round(frequency_score, 3),
                "pipeline": round(pipeline_score, 3),
                "win_rate": round(win_rate, 3),
            }

            score_id = _generate_id("pgeng")
            now = _utcnow_iso()

            conn.execute(
                """
                INSERT INTO pg_crm_engagement_scores
                    (id, account_id, score, score_breakdown,
                     interaction_count, last_interaction_at,
                     opportunity_count, win_rate, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    score_id,
                    acct_id,
                    round(score, 3),
                    json.dumps(breakdown),
                    interaction_count,
                    last_interaction,
                    opportunity_count,
                    win_rate,
                    now,
                ),
            )

            scores.append(
                {
                    "account_id": acct_id,
                    "account_name": acct_name,
                    "score": round(score, 3),
                    "breakdown": breakdown,
                }
            )

        conn.commit()
        return scores
    except Exception:
        return []
    finally:
        conn.close()


def _score_recency(last_interaction_at: Optional[str]) -> float:
    """Score interaction recency.  1.0 = today, decays over 90 days."""
    if not last_interaction_at:
        return 0.0
    try:
        last = datetime.fromisoformat(last_interaction_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_ago = (now - last).total_seconds() / 86400.0
        if days_ago <= 0:
            return 1.0
        if days_ago >= _RECENCY_DECAY_DAYS:
            return 0.0
        return round(1.0 - (days_ago / _RECENCY_DECAY_DAYS), 3)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Manual account CRUD
# ---------------------------------------------------------------------------


def create_account(
    name: str,
    agency: str = "",
    sub_agency: str = "",
    account_type: str = "government",
    website: str = "",
    naics_codes: str = "",
    set_asides: str = "",
    notes: str = "",
    status: str = "active",
) -> Dict[str, Any]:
    """Manually create a CRM account."""
    valid_types = ("government", "prime", "subcontractor", "partner", "other")
    valid_statuses = ("active", "inactive", "prospect")
    if account_type not in valid_types:
        return {"success": False, "error": f"account_type must be one of {valid_types}"}
    if status not in valid_statuses:
        return {"success": False, "error": f"status must be one of {valid_statuses}"}
    if not name.strip():
        return {"success": False, "error": "name is required"}

    conn = get_connection()
    account_id = _generate_id("pgacct")
    now = _utcnow_iso()
    try:
        conn.execute(
            """
            INSERT INTO pg_crm_accounts
                (id, name, agency, sub_agency, account_type, website,
                 naics_codes, set_asides, notes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                account_id,
                name.strip(),
                agency,
                sub_agency,
                account_type,
                website,
                naics_codes,
                set_asides,
                notes,
                status,
                now,
                now,
            ),
        )
        conn.commit()
        return {"success": True, "account_id": account_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def update_account(account_id: str, **fields) -> Dict[str, Any]:
    """Update a CRM account. Pass only fields to change."""
    allowed = {
        "name",
        "agency",
        "sub_agency",
        "account_type",
        "website",
        "naics_codes",
        "set_asides",
        "notes",
        "status",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return {"success": False, "error": "no valid fields to update"}

    valid_types = ("government", "prime", "subcontractor", "partner", "other")
    valid_statuses = ("active", "inactive", "prospect")
    if "account_type" in updates and updates["account_type"] not in valid_types:
        return {"success": False, "error": f"account_type must be one of {valid_types}"}
    if "status" in updates and updates["status"] not in valid_statuses:
        return {"success": False, "error": f"status must be one of {valid_statuses}"}

    updates["updated_at"] = _utcnow_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [account_id]

    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE pg_crm_accounts SET {set_clause} WHERE id = ?",
            values,  # nosec B608 — columns from hardcoded allowlist
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"success": False, "error": "account not found"}
        return {"success": True, "account_id": account_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def list_accounts(status: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """List CRM accounts, optionally filtered by status."""
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM pg_crm_accounts WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pg_crm_accounts ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_account(account_id: str) -> Optional[Dict]:
    """Get a single CRM account by ID."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM pg_crm_accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Manual contact CRUD
# ---------------------------------------------------------------------------

VALID_INFLUENCE_LEVELS = (
    "decision_maker",
    "influencer",
    "evaluator",
    "end_user",
    "unknown",
)


def create_contact(
    account_id: str,
    name: str,
    title: str = "",
    email: str = "",
    phone: str = "",
    role_in_procurement: str = "",
    influence_level: str = "unknown",
    notes: str = "",
) -> Dict[str, Any]:
    """Create a CRM contact under an account."""
    if not name.strip():
        return {"success": False, "error": "name is required"}
    if influence_level not in VALID_INFLUENCE_LEVELS:
        return {"success": False, "error": f"influence_level must be one of {VALID_INFLUENCE_LEVELS}"}

    # Verify account exists
    acct = get_account(account_id)
    if not acct:
        return {"success": False, "error": f"account {account_id} not found"}

    conn = get_connection()
    contact_id = _generate_id("pgcon")
    now = _utcnow_iso()
    try:
        conn.execute(
            """
            INSERT INTO pg_crm_contacts
                (id, account_id, name, title, email, phone,
                 role_in_procurement, influence_level, notes,
                 last_contact_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                contact_id,
                account_id,
                name.strip(),
                title,
                email,
                phone,
                role_in_procurement,
                influence_level,
                notes,
                None,
                now,
                now,
            ),
        )
        conn.commit()
        return {"success": True, "contact_id": contact_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def update_contact(contact_id: str, **fields) -> Dict[str, Any]:
    """Update a CRM contact. Pass only fields to change."""
    allowed = {
        "name",
        "title",
        "email",
        "phone",
        "role_in_procurement",
        "influence_level",
        "notes",
        "account_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return {"success": False, "error": "no valid fields to update"}

    if "influence_level" in updates and updates["influence_level"] not in VALID_INFLUENCE_LEVELS:
        return {"success": False, "error": f"influence_level must be one of {VALID_INFLUENCE_LEVELS}"}

    updates["updated_at"] = _utcnow_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [contact_id]

    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE pg_crm_contacts SET {set_clause} WHERE id = ?",
            values,  # nosec B608 — columns from hardcoded allowlist
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"success": False, "error": "contact not found"}
        return {"success": True, "contact_id": contact_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def delete_contact(contact_id: str) -> Dict[str, Any]:
    """Delete a CRM contact."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM pg_crm_contacts WHERE id = ?", (contact_id,))
        conn.commit()
        if cur.rowcount == 0:
            return {"success": False, "error": "contact not found"}
        return {"success": True, "contact_id": contact_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def list_contacts(account_id: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """List CRM contacts, optionally filtered by account."""
    conn = get_connection()
    try:
        if account_id:
            rows = conn.execute(
                "SELECT c.*, a.name AS account_name "
                "FROM pg_crm_contacts c "
                "LEFT JOIN pg_crm_accounts a ON a.id = c.account_id "
                "WHERE c.account_id = ? ORDER BY c.updated_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT c.*, a.name AS account_name "
                "FROM pg_crm_contacts c "
                "LEFT JOIN pg_crm_accounts a ON a.id = c.account_id "
                "ORDER BY c.updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_contact(contact_id: str) -> Optional[Dict]:
    """Get a single CRM contact by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT c.*, a.name AS account_name "
            "FROM pg_crm_contacts c "
            "LEFT JOIN pg_crm_accounts a ON a.id = c.account_id "
            "WHERE c.id = ?",
            (contact_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Manual interaction logging
# ---------------------------------------------------------------------------

VALID_INTERACTION_TYPES = (
    "meeting",
    "call",
    "email",
    "conference",
    "site_visit",
    "rfi_response",
    "industry_day",
    "other",
)


def log_manual_interaction(
    account_id: str,
    interaction_type: str,
    subject: str,
    contact_id: str = "",
    notes: str = "",
    opportunity_id: str = "",
    interaction_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Log a manual CRM interaction (meeting, call, email, etc.)."""
    if interaction_type not in VALID_INTERACTION_TYPES:
        return {"success": False, "error": f"interaction_type must be one of {VALID_INTERACTION_TYPES}"}
    if not subject.strip():
        return {"success": False, "error": "subject is required"}

    # Verify account exists
    acct = get_account(account_id)
    if not acct:
        return {"success": False, "error": f"account {account_id} not found"}

    conn = get_connection()
    interaction_id = _generate_id("pgint")
    now = _utcnow_iso()
    date = interaction_date or now
    try:
        conn.execute(
            """
            INSERT INTO pg_crm_interactions
                (id, contact_id, account_id, interaction_type, subject,
                 notes, opportunity_id, interaction_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                interaction_id,
                contact_id,
                account_id,
                interaction_type,
                subject.strip(),
                notes,
                opportunity_id,
                date,
                now,
            ),
        )

        # Update contact last_contact_at if a contact was specified
        if contact_id:
            conn.execute(
                "UPDATE pg_crm_contacts SET last_contact_at = ?, updated_at = ? WHERE id = ?",
                (date, now, contact_id),
            )

        conn.commit()
        return {"success": True, "interaction_id": interaction_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Engage Reflex (R4).

    Steps:
      1. Discover accounts from tracked opportunities (auto-create CRM accounts)
      2. Log interactions from recent audit events
      3. Compute engagement scores for all active accounts
      4. Audit all decisions

    Returns standard reflex result dict.
    """
    accounts_created = 0
    interactions_logged = 0
    errors = 0

    # Step 1: Create accounts from opportunities
    opps_without_accounts = _get_opportunities_without_accounts()
    for opp in opps_without_accounts:
        acct_id = _create_account_from_opportunity(opp)
        if acct_id:
            accounts_created += 1
        else:
            errors += 1

    # Step 2: Log interactions from audit events
    audit_events = _get_recent_audit_interactions()
    for event in audit_events:
        agency = event.get("agency")
        if not agency:
            continue
        acct_id = _get_account_for_agency(agency)
        if not acct_id:
            continue
        interaction_type = _map_event_to_interaction(event.get("event_type", ""))
        if not interaction_type:
            continue
        subject = f"{event.get('event_type', 'activity')} for {agency}"
        result = _log_interaction(
            account_id=acct_id,
            interaction_type=interaction_type,
            subject=subject,
            opportunity_id=event.get("opportunity_id"),
            notes=event.get("details", ""),
        )
        if result:
            interactions_logged += 1

    # Step 3: Compute engagement scores
    scores = _compute_engagement_scores()
    accounts_scored = len(scores)

    return {
        "success": True,
        "metric_value": float(accounts_scored),
        "details": {
            "accounts_created": accounts_created,
            "accounts_scored": accounts_scored,
            "interactions_logged": interactions_logged,
            "errors": errors,
        },
    }
