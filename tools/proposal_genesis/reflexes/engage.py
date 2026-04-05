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

from tools.db.storage import get_connection


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
            LIMIT 20
        """).fetchall()
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

        conn.execute("""
            INSERT INTO pg_crm_accounts
                (id, name, agency, account_type, naics_codes, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            account_id, agency, agency, account_type, naics,
            "active", now, now,
        ))

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
        "department", "agency", "bureau", "office", "administration",
        "command", "corps", "center", "institute", "commission",
        "dod", "army", "navy", "air force", "space force",
        "dhs", "fbi", "cia", "nsa", "disa", "dla", "gsa",
        "va ", "hhs", "cms", "faa", "fema", "usda",
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
            AND a.created_at > datetime('now', '-1 day')
            AND a.event_type IN (
                'pg.reflex.completed', 'capture_plan_created',
                'brief_generated', 'draft_completed', 'quality_checked'
            )
            ORDER BY a.created_at DESC
            LIMIT 50
        """).fetchall()
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
        conn.execute("""
            INSERT INTO pg_crm_interactions
                (id, contact_id, account_id, interaction_type, subject,
                 notes, opportunity_id, interaction_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            interaction_id,
            "",  # No specific contact for system-generated interactions
            account_id,
            interaction_type,
            subject,
            notes,
            opportunity_id,
            now,
            now,
        ))
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
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN wl.outcome = 'won' THEN 1 ELSE 0 END) as wins
            FROM pg_win_loss_records wl
            JOIN sam_gov_opportunities o ON o.id = wl.opportunity_id
            WHERE LOWER(o.agency) = LOWER(?)
            AND wl.outcome IN ('won', 'lost')
        """, (agency_name,)).fetchone()
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
        accounts = conn.execute(
            "SELECT id, name FROM pg_crm_accounts WHERE status = 'active'"
        ).fetchall()

        for acct in accounts:
            acct_id = acct["id"]
            acct_name = acct["name"]

            # Interaction count and recency
            interaction_stats = conn.execute("""
                SELECT COUNT(*) as cnt,
                       MAX(interaction_date) as last_date
                FROM pg_crm_interactions
                WHERE account_id = ?
            """, (acct_id,)).fetchone()

            interaction_count = interaction_stats["cnt"] if interaction_stats else 0
            last_interaction = interaction_stats["last_date"] if interaction_stats else None

            # Opportunity count
            opp_stats = conn.execute("""
                SELECT COUNT(*) as cnt
                FROM proposal_opportunities
                WHERE agency = ?
                AND status IN ('tracking', 'drafting', 'reviewing')
            """, (acct_name,)).fetchone()

            opportunity_count = opp_stats["cnt"] if opp_stats else 0

            # Compute score components
            recency_score = _score_recency(last_interaction)
            frequency_score = min(1.0, interaction_count / 10.0)
            pipeline_score = min(1.0, opportunity_count / 5.0)
            win_rate = _compute_win_rate(conn, acct_name)

            # Weighted composite
            score = (
                recency_score * 0.30
                + frequency_score * 0.25
                + pipeline_score * 0.25
                + win_rate * 0.20
            )

            breakdown = {
                "recency": round(recency_score, 3),
                "frequency": round(frequency_score, 3),
                "pipeline": round(pipeline_score, 3),
                "win_rate": round(win_rate, 3),
            }

            score_id = _generate_id("pgeng")
            now = _utcnow_iso()

            conn.execute("""
                INSERT INTO pg_crm_engagement_scores
                    (id, account_id, score, score_breakdown,
                     interaction_count, last_interaction_at,
                     opportunity_count, win_rate, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                score_id, acct_id, round(score, 3),
                json.dumps(breakdown),
                interaction_count, last_interaction,
                opportunity_count, win_rate, now,
            ))

            scores.append({
                "account_id": acct_id,
                "account_name": acct_name,
                "score": round(score, 3),
                "breakdown": breakdown,
            })

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
        if days_ago >= 90:
            return 0.0
        return round(1.0 - (days_ago / 90.0), 3)
    except (ValueError, TypeError):
        return 0.0


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

    total = accounts_created + interactions_logged + accounts_scored

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
