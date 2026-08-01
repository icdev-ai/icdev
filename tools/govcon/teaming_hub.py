#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Teaming Coordination Hub -- partner scoring, workshare, TA/NDA, OCI.

Provides deterministic analysis for teaming decisions on government
proposals.  Air-gap safe -- NO external API calls.

DB tables (read/write): pg_teaming_partners, pg_teaming_workshare
DB tables (read):       proposal_opportunities, govcon_awards
DB tables (write):      audit_trail

Uses existing DB schema:
  pg_teaming_partners  — global partner registry (name, type, certs)
  pg_teaming_workshare — per-opportunity allocation (workshare,
                          TA status, OCI risk, reliability)

Usage:
    python tools/govcon/teaming_hub.py \\
        --opportunity-id "opp-xxx" --add-partner \\
        --partner-name "ACME" \\
        --partner-type subcontractor --json
    python tools/govcon/teaming_hub.py \\
        --score --partner-id "tp-xxx" --json
    python tools/govcon/teaming_hub.py \\
        --opportunity-id "opp-xxx" --workshare --json
    python tools/govcon/teaming_hub.py \\
        --opportunity-id "opp-xxx" --ta-lifecycle --json
    python tools/govcon/teaming_hub.py \\
        --opportunity-id "opp-xxx" --oci-screen \\
        --partner-name "ACME" --json
    python tools/govcon/teaming_hub.py \\
        --opportunity-id "opp-xxx" --team-status --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.common.helpers import row_to_dict  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.teaming_hub")

# ── Constants ─────────────────────────────────────────────────

# pg_teaming_partners.partner_type CHECK values
PARTNER_TYPES = (
    "prime",
    "subcontractor",
    "consultant",
    "technology_partner",
    "mentor_protege",
)

# pg_teaming_workshare statuses
WS_STATUSES = (
    "proposed",
    "agreed",
    "contracted",
    "disputed",
)

TA_STATUSES = (
    "none",
    "draft",
    "negotiating",
    "executed",
    "expired",
)

OCI_RISK_LEVELS = (
    "none",
    "low",
    "medium",
    "high",
    "disqualifying",
)

# Partner score dimension weights (sum to 1.0)
SCORE_WEIGHTS = {
    "past_performance_match": 0.25,
    "capability_match": 0.20,
    "reliability": 0.20,
    "certification_match": 0.15,
    "geographic_fit": 0.10,
    "socioeconomic_fit": 0.10,
}

# FAR 52.219-9 small business minimum
SB_SUBCONTRACTING_MIN_PCT = 23.0


# ── Helpers ───────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="tp"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details, opp_id=None):
    det = json.dumps(details) if isinstance(details, dict) else str(details)
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, "
            "action, details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                event_type,
                "teaming_hub",
                action,
                det,
                opp_id,
                None,
            ),
        )
    except Exception:
        # Fallback: schema may use created_at instead
        try:
            conn.execute(
                "INSERT INTO audit_trail "
                "(project_id, event_type, actor, "
                "action, details, session_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    opp_id,
                    event_type,
                    "teaming_hub",
                    action,
                    det,
                    None,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # audit is best-effort
            logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


# ── Core Functions ────────────────────────────────────────────


def add_partner(
    opportunity_id,
    partner_name,
    partner_type,
    socioeconomic_status=None,
    workshare_pct=None,
    cage=None,
):
    """Register a teaming partner for an opportunity.

    1. Upsert into pg_teaming_partners (global registry).
    2. Insert into pg_teaming_workshare (per-opp link).
    """
    conn = _get_db()
    now = _now()

    if partner_type not in PARTNER_TYPES:
        conn.close()
        return {
            "status": "error",
            "message": (f"Invalid partner_type '{partner_type}'. Must be one of {PARTNER_TYPES}"),
        }

    # Check if partner exists in global registry
    existing = conn.execute(
        "SELECT id FROM pg_teaming_partners WHERE name = %s",
        (partner_name,),
    ).fetchone()

    if existing:
        partner_id = existing["id"] if isinstance(existing, dict) else existing[0]
    else:
        partner_id = _gen_id("tp")
        certs = cage or ""
        conn.execute(
            "INSERT INTO pg_teaming_partners "
            "(id, name, partner_type, capabilities, "
            "past_performance, contract_vehicles, "
            "certifications, set_asides, status, "
            "created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                partner_id,
                partner_name,
                partner_type,
                "",
                "",
                "",
                certs,
                "",
                "active",
                now,
                now,
            ),
        )

    # Link to opportunity via pg_teaming_workshare
    ws_id = _gen_id("ws")
    conn.execute(
        "INSERT INTO pg_teaming_workshare "
        "(id, opportunity_id, partner_id, clin_number, "
        "workshare_pct, labor_categories, status, "
        "ta_status, ta_expiry_date, oci_risk, "
        "socioeconomic_status, reliability_score, "
        "created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            ws_id,
            opportunity_id,
            partner_id,
            None,
            workshare_pct or 0.0,
            None,
            "proposed",
            "none",
            None,
            "none",
            socioeconomic_status or "",
            None,
            now,
            now,
        ),
    )

    _audit(
        conn,
        "teaming.add_partner",
        f"Registered {partner_name} for {opportunity_id}",
        {
            "partner_id": partner_id,
            "workshare_id": ws_id,
            "opportunity_id": opportunity_id,
            "partner_name": partner_name,
            "partner_type": partner_type,
        },
        opp_id=opportunity_id,
    )

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "partner_id": partner_id,
        "workshare_id": ws_id,
        "opportunity_id": opportunity_id,
        "partner_name": partner_name,
        "partner_type": partner_type,
    }


def score_partner(partner_id):
    """Score a partner on 6 dimensions (weighted composite).

    Dimensions:
        past_performance_match (0.25)
        capability_match       (0.20)
        reliability            (0.20)
        certification_match    (0.15)
        geographic_fit         (0.10)
        socioeconomic_fit      (0.10)
    """
    conn = _get_db()

    row = conn.execute(
        "SELECT * FROM pg_teaming_partners WHERE id = %s",
        (partner_id,),
    ).fetchone()

    if not row:
        conn.close()
        return {
            "status": "error",
            "message": f"Partner {partner_id} not found",
        }

    p = row_to_dict(row)
    partner_name = p.get("name", "")

    # Find opportunity via workshare link
    ws = conn.execute(
        "SELECT opportunity_id FROM pg_teaming_workshare WHERE partner_id = %s LIMIT 1",
        (partner_id,),
    ).fetchone()
    opp_id = None
    opp = None
    if ws:
        opp_id = ws["opportunity_id"] if isinstance(ws, dict) else ws[0]
        try:
            opp_row = conn.execute(
                "SELECT * FROM proposal_opportunities WHERE id = %s",
                (opp_id,),
            ).fetchone()
            if opp_row:
                opp = row_to_dict(opp_row)
        except Exception:
            pass

    scores = {}

    # 1. Past performance — govcon_awards on similar NAICS
    pp_score = 0.3
    try:
        awards = conn.execute(
            "SELECT naics_code FROM govcon_awards WHERE awardee_name LIKE %s",
            (f"%{partner_name}%",),
        ).fetchall()
        if awards:
            pp_score = min(1.0, 0.3 + len(awards) * 0.1)
            if opp and opp.get("naics_code"):
                match = [a for a in awards if row_to_dict(a).get("naics_code") == opp["naics_code"]]
                if match:
                    pp_score = min(1.0, pp_score + 0.3)
    except Exception:
        pass
    scores["past_performance_match"] = round(pp_score, 3)

    # 2. Capability match
    cap_score = 0.3
    if opp:
        try:
            cnt_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM govcon_awards WHERE awardee_name LIKE %s AND naics_code = %s",
                (f"%{partner_name}%", opp.get("naics_code") or ""),
            ).fetchone()
            if cnt_row:
                cnt = cnt_row["cnt"] if isinstance(cnt_row, dict) else cnt_row[0]
                if cnt > 0:
                    cap_score = min(1.0, 0.5 + cnt * 0.1)
        except Exception:
            pass
    scores["capability_match"] = round(cap_score, 3)

    # 3. Reliability — prior teaming records
    rel_score = 0.5
    try:
        prior = conn.execute(
            "SELECT COUNT(*) as cnt FROM pg_teaming_workshare WHERE partner_id = %s",
            (partner_id,),
        ).fetchone()
        if prior:
            cnt = prior["cnt"] if isinstance(prior, dict) else prior[0]
            if cnt > 1:
                rel_score = min(1.0, 0.5 + cnt * 0.1)
    except Exception:
        pass
    scores["reliability"] = round(rel_score, 3)

    # 4. Certification match
    cert_score = 0.3
    certs = (p.get("certifications") or "").lower()
    if "cmmc" in certs:
        cert_score += 0.3
    if any(
        kw in certs
        for kw in (
            "secret",
            "ts",
            "ts/sci",
            "clearance",
        )
    ):
        cert_score += 0.3
    scores["certification_match"] = round(
        min(1.0, cert_score),
        3,
    )

    # 5. Geographic fit
    scores["geographic_fit"] = 0.5

    # 6. Socioeconomic fit
    socio_score = 0.5
    ws_rows = conn.execute(
        "SELECT socioeconomic_status FROM pg_teaming_workshare WHERE partner_id = %s",
        (partner_id,),
    ).fetchall()
    for wr in ws_rows:
        se = (row_to_dict(wr).get("socioeconomic_status") or "").lower()
        if se and se not in ("", "none", "large_business"):
            socio_score = 0.7
            break
    scores["socioeconomic_fit"] = round(socio_score, 3)

    # Composite
    composite = sum(scores[d] * SCORE_WEIGHTS[d] for d in SCORE_WEIGHTS)
    composite = round(composite, 3)

    # Update reliability in workshare records
    now = _now()
    conn.execute(
        "UPDATE pg_teaming_workshare SET reliability_score = %s, updated_at = %s WHERE partner_id = %s",
        (composite, now, partner_id),
    )

    _audit(
        conn,
        "teaming.score_partner",
        f"Scored {partner_name}: {composite}",
        {
            "partner_id": partner_id,
            "composite": composite,
            "scores": scores,
        },
        opp_id=opp_id,
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "partner_id": partner_id,
        "partner_name": partner_name,
        "composite_score": composite,
        "dimension_scores": scores,
        "weights": SCORE_WEIGHTS,
    }


def track_workshare(opportunity_id):
    """Validate workshare allocation for an opportunity.

    Checks total = 100% (+/- 2%), FAR 52.219-9 SB plan.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT ws.id, ws.partner_id, ws.workshare_pct, "
        "ws.socioeconomic_status, ws.status, "
        "tp.name, tp.partner_type "
        "FROM pg_teaming_workshare ws "
        "LEFT JOIN pg_teaming_partners tp "
        "ON ws.partner_id = tp.id "
        "WHERE ws.opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "total_pct": 0.0,
            "compliant": False,
            "issues": ["No partners for this opportunity"],
        }

    partners = [row_to_dict(r) for r in rows]
    total_pct = sum(float(p.get("workshare_pct") or 0) for p in partners)

    issues = []
    warnings = []

    if total_pct < 98.0:
        remaining = 100.0 - total_pct
        issues.append(f"Under-allocated: {total_pct:.1f}% assigned, {remaining:.1f}% unaccounted")
    elif total_pct > 102.0:
        issues.append(f"Over-allocated: {total_pct:.1f}% exceeds 100%")

    # FAR 52.219-9 check
    sb_pct = sum(
        float(p.get("workshare_pct") or 0)
        for p in partners
        if (p.get("socioeconomic_status") or "") not in ("", "none", "large_business")
    )
    if total_pct > 0 and sb_pct < SB_SUBCONTRACTING_MIN_PCT:
        issues.append(f"FAR 52.219-9: SB at {sb_pct:.1f}% (min {SB_SUBCONTRACTING_MIN_PCT:.0f}%)")

    no_ws = [p.get("name", "?") for p in partners if not p.get("workshare_pct")]
    if no_ws:
        warnings.append("No workshare: " + ", ".join(no_ws))

    breakdown = [
        {
            "partner_name": p.get("name"),
            "partner_type": p.get("partner_type"),
            "socioeconomic_status": (p.get("socioeconomic_status")),
            "workshare_pct": float(p.get("workshare_pct") or 0),
        }
        for p in partners
    ]

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "total_pct": round(total_pct, 1),
        "compliant": len(issues) == 0,
        "partner_count": len(partners),
        "breakdown": breakdown,
        "issues": issues,
        "warnings": warnings,
    }


def manage_ta_lifecycle(opportunity_id):
    """Check TA/NDA status for all partners.

    Flags expiring/expired/missing TAs and exclusivity
    conflicts.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT ws.id, ws.partner_id, ws.ta_status, "
        "ws.ta_expiry_date, tp.name "
        "FROM pg_teaming_workshare ws "
        "LEFT JOIN pg_teaming_partners tp "
        "ON ws.partner_id = tp.id "
        "WHERE ws.opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "partners": [],
            "message": "No partners registered",
        }

    now_dt = datetime.now(timezone.utc)
    results = []

    for r in rows:
        rd = row_to_dict(r)
        pid = rd.get("partner_id", "")
        name = rd.get("name", pid)
        ta_st = rd.get("ta_status", "none")
        ta_exp = rd.get("ta_expiry_date")

        days_left = None
        if ta_exp:
            try:
                exp_str = ta_exp.replace("Z", "+00:00")
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(
                        tzinfo=timezone.utc,
                    )
                days_left = (exp_dt - now_dt).days
            except (ValueError, TypeError):
                pass

        # Check conflicts
        conflicts = []
        try:
            others = conn.execute(
                "SELECT opportunity_id, ta_status "
                "FROM pg_teaming_workshare "
                "WHERE partner_id = %s "
                "AND opportunity_id != %s",
                (pid, opportunity_id),
            ).fetchall()
            for o in others:
                od = row_to_dict(o)
                if od.get("ta_status") in (
                    "executed",
                    "negotiating",
                ):
                    conflicts.append(
                        {
                            "competing_opportunity": (od.get("opportunity_id")),
                            "conflict_type": "exclusivity",
                        }
                    )
        except Exception:
            pass

        flags = []
        if ta_st == "none":
            flags.append("missing_ta")
        elif ta_st == "expired":
            flags.append("expired_ta")
        elif ta_st == "executed" and days_left is not None:
            if days_left < 0:
                flags.append("expired_ta")
            elif days_left <= 30:
                flags.append("expiring_ta")
        if conflicts:
            flags.append("exclusivity_conflict")

        results.append(
            {
                "partner_id": pid,
                "partner_name": name,
                "ta_status": ta_st,
                "ta_expiry_date": ta_exp,
                "days_until_expiry": days_left,
                "conflicts": conflicts,
                "flags": flags,
            }
        )

    conn.close()

    expired = sum(1 for r in results if "expired_ta" in r["flags"])
    expiring = sum(1 for r in results if "expiring_ta" in r["flags"])
    missing = sum(1 for r in results if "missing_ta" in r["flags"])
    conflict_n = sum(1 for r in results if "exclusivity_conflict" in r["flags"])

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "partners": results,
        "summary": {
            "total": len(results),
            "expired": expired,
            "expiring_within_30d": expiring,
            "missing": missing,
            "exclusivity_conflicts": conflict_n,
        },
    }


def screen_oci(opportunity_id, partner_name):
    """OCI (Organizational Conflict of Interest) screening.

    Checks incumbent status, advisory OCI, impaired
    objectivity. Risk: none/low/medium/high/disqualifying.
    """
    conn = _get_db()

    oci_factors = []
    mitigations = []

    opp = None
    try:
        opp_row = conn.execute(
            "SELECT * FROM proposal_opportunities WHERE id = %s",
            (opportunity_id,),
        ).fetchone()
        if opp_row:
            opp = row_to_dict(opp_row)
    except Exception:
        pass

    # 1. Incumbent check
    is_incumbent = False
    try:
        if opp and opp.get("solicitation_number"):
            sol = opp["solicitation_number"]
            irow = conn.execute(
                "SELECT COUNT(*) as cnt FROM govcon_awards WHERE awardee_name LIKE %s AND solicitation_number LIKE %s",
                (f"%{partner_name}%", f"%{sol}%"),
            ).fetchone()
            if irow:
                cnt = irow["cnt"] if isinstance(irow, dict) else irow[0]
                if cnt > 0:
                    is_incumbent = True

        if not is_incumbent and opp and opp.get("agency"):
            arow = conn.execute(
                "SELECT COUNT(*) as cnt "
                "FROM govcon_awards "
                "WHERE awardee_name LIKE %s "
                "AND agency LIKE %s "
                "AND naics_code = %s",
                (
                    f"%{partner_name}%",
                    f"%{opp['agency']}%",
                    opp.get("naics_code") or "",
                ),
            ).fetchone()
            if arow:
                cnt = arow["cnt"] if isinstance(arow, dict) else arow[0]
                if cnt > 0:
                    is_incumbent = True
    except Exception:
        pass

    if is_incumbent:
        oci_factors.append(
            {
                "type": "unequal_access",
                "description": (f"{partner_name} appears to be incumbent or holds related contract"),
                "severity": "high",
            }
        )
        mitigations.append("Implement information barrier (firewall) plan")
        mitigations.append("Document prior contract deliverables as publicly available")

    # 2. Advisory OCI
    try:
        if opp and opp.get("agency"):
            adv = conn.execute(
                "SELECT title FROM govcon_awards "
                "WHERE awardee_name LIKE %s "
                "AND agency LIKE %s "
                "AND (title LIKE '%advisory%' "
                "OR title LIKE '%consulting%' "
                "OR title LIKE '%evaluation%' "
                "OR title LIKE '%assessment%')",
                (
                    f"%{partner_name}%",
                    f"%{opp['agency']}%",
                ),
            ).fetchall()
            if adv:
                agency = opp.get("agency", "agency")
                oci_factors.append(
                    {
                        "type": "biased_ground_rules",
                        "description": (f"{partner_name} has advisory contracts with {agency}"),
                        "severity": "medium",
                    }
                )
                mitigations.append("Obtain CO waiver for advisory OCI")
                mitigations.append("Demonstrate advisory work is unrelated to this procurement")
    except Exception:
        pass

    # 3. Impaired objectivity — competing proposals
    try:
        competing = conn.execute(
            "SELECT ws.opportunity_id, po.title "
            "FROM pg_teaming_workshare ws "
            "LEFT JOIN pg_teaming_partners tp "
            "ON ws.partner_id = tp.id "
            "LEFT JOIN proposal_opportunities po "
            "ON ws.opportunity_id = po.id "
            "WHERE tp.name = %s "
            "AND ws.opportunity_id != %s "
            "AND po.status IN ("
            "'intake','bid_no_bid','go','writing')",
            (partner_name, opportunity_id),
        ).fetchall()

        for c in competing:
            cd = row_to_dict(c)
            oci_factors.append(
                {
                    "type": "impaired_objectivity",
                    "description": (f"{partner_name} teamed on {cd.get('opportunity_id')}: {cd.get('title', 'N/A')}"),
                    "severity": "medium",
                }
            )
        if len(competing) > 1:
            mitigations.append("Require exclusivity agreement")
        if competing:
            mitigations.append("Document team arrangements and non-compete boundaries")
    except Exception:
        pass

    # Determine risk level
    if not oci_factors:
        risk = "none"
    else:
        sevs = [f["severity"] for f in oci_factors]
        if "disqualifying" in sevs:
            risk = "disqualifying"
        elif sevs.count("high") >= 2:
            risk = "disqualifying"
        elif "high" in sevs:
            risk = "high"
        elif "medium" in sevs:
            risk = "medium"
        else:
            risk = "low"

    # Update OCI risk in workshare
    try:
        conn.execute(
            "UPDATE pg_teaming_workshare "
            "SET oci_risk = %s, updated_at = %s "
            "WHERE opportunity_id = %s "
            "AND partner_id IN ("
            "SELECT id FROM pg_teaming_partners "
            "WHERE name = %s)",
            (risk, _now(), opportunity_id, partner_name),
        )
    except Exception:
        pass

    _audit(
        conn,
        "teaming.oci_screen",
        f"OCI screen {partner_name}: risk={risk}",
        {
            "opportunity_id": opportunity_id,
            "partner": partner_name,
            "risk": risk,
            "factors": len(oci_factors),
        },
        opp_id=opportunity_id,
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "partner": partner_name,
        "oci_risk": risk,
        "oci_factors": oci_factors,
        "mitigation_recommendations": mitigations,
    }


def get_team_status(opportunity_id):
    """Full team status for an opportunity.

    Aggregates partner scores, workshare, TA status,
    OCI risk. Computes team health score.
    """
    conn = _get_db()

    rows = conn.execute(
        "SELECT ws.*, tp.name, tp.partner_type, "
        "tp.certifications "
        "FROM pg_teaming_workshare ws "
        "LEFT JOIN pg_teaming_partners tp "
        "ON ws.partner_id = tp.id "
        "WHERE ws.opportunity_id = %s",
        (opportunity_id,),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "team_size": 0,
            "partners": [],
            "team_health": 0.0,
            "issues": ["No partners registered"],
        }

    partners = [row_to_dict(r) for r in rows]
    total_ws = 0.0
    composites = []
    issues = []
    members = []

    for p in partners:
        ws = float(p.get("workshare_pct") or 0)
        total_ws += ws
        rel = p.get("reliability_score")
        if rel is not None:
            composites.append(float(rel))

        members.append(
            {
                "partner_id": p.get("partner_id"),
                "partner_name": p.get("name"),
                "partner_type": p.get("partner_type"),
                "socioeconomic_status": (p.get("socioeconomic_status")),
                "workshare_pct": ws,
                "reliability_score": (float(rel) if rel is not None else None),
                "ta_status": p.get("ta_status", "none"),
                "ta_expiry_date": p.get("ta_expiry_date"),
                "oci_risk": p.get("oci_risk", "none"),
            }
        )

        ta = p.get("ta_status", "none")
        if ta in ("none", "expired"):
            name = p.get("name", "?")
            issues.append(f"{name}: TA is {ta}")
        oci = p.get("oci_risk", "none")
        if oci in ("high", "disqualifying"):
            name = p.get("name", "?")
            issues.append(f"{name}: OCI risk {oci}")
        if not p.get("workshare_pct"):
            name = p.get("name", "?")
            issues.append(f"{name}: No workshare")

    if total_ws > 0 and abs(total_ws - 100.0) > 2.0:
        issues.append(f"Total workshare {total_ws:.1f}% (should be ~100%)")

    # Team health components
    avg_comp = sum(composites) / len(composites) if composites else 0.5
    if 98.0 <= total_ws <= 102.0:
        ws_h = 1.0
    else:
        ws_h = max(
            0.0,
            1.0 - abs(total_ws - 100.0) / 50.0,
        )

    ta_ok = sum(1 for p in partners if p.get("ta_status") == "executed")
    ta_h = ta_ok / len(partners) if partners else 0.0

    oci_ok = sum(1 for p in partners if p.get("oci_risk") in ("none", "low"))
    oci_h = oci_ok / len(partners) if partners else 1.0

    health = avg_comp * 0.30 + ws_h * 0.20 + ta_h * 0.25 + oci_h * 0.25

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "team_size": len(partners),
        "partners": members,
        "workshare_total_pct": round(total_ws, 1),
        "team_health": round(health, 3),
        "health_components": {
            "avg_partner_score": round(avg_comp, 3),
            "workshare_compliance": round(ws_h, 3),
            "ta_coverage": round(ta_h, 3),
            "oci_clean": round(oci_h, 3),
        },
        "issues": issues,
        "issue_count": len(issues),
    }


# ── CLI ───────────────────────────────────────────────────────


def _build_parser():
    p = argparse.ArgumentParser(
        prog="teaming_hub",
        description=("Teaming Coordination Hub -- partner scoring, workshare, TA/NDA, OCI"),
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--add-partner",
        action="store_true",
        help="Register a teaming partner",
    )
    g.add_argument(
        "--score",
        action="store_true",
        help="Score a partner",
    )
    g.add_argument(
        "--workshare",
        action="store_true",
        help="Validate workshare allocation",
    )
    g.add_argument(
        "--ta-lifecycle",
        action="store_true",
        help="Check TA/NDA lifecycle",
    )
    g.add_argument(
        "--oci-screen",
        action="store_true",
        help="OCI risk screening",
    )
    g.add_argument(
        "--team-status",
        action="store_true",
        help="Full team status",
    )

    p.add_argument("--opportunity-id", help="Opportunity ID")
    p.add_argument("--partner-id", help="Partner ID")
    p.add_argument("--partner-name", help="Partner name")
    p.add_argument(
        "--partner-type",
        choices=PARTNER_TYPES,
        help="Partner type",
    )
    p.add_argument(
        "--socioeconomic-status",
        help="Socioeconomic status",
    )
    p.add_argument(
        "--workshare-pct",
        type=float,
        help="Workshare percentage",
    )
    p.add_argument("--cage", help="CAGE code")
    p.add_argument("--json", action="store_true")
    p.add_argument("--human", action="store_true")
    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()
    result = {}

    if args.add_partner:
        if not args.opportunity_id or not args.partner_name or not args.partner_type:
            parser.error("--add-partner requires --opportunity-id, --partner-name, --partner-type")
        result = add_partner(
            opportunity_id=args.opportunity_id,
            partner_name=args.partner_name,
            partner_type=args.partner_type,
            socioeconomic_status=args.socioeconomic_status,
            workshare_pct=args.workshare_pct,
            cage=args.cage,
        )

    elif args.score:
        if not args.partner_id:
            parser.error("--score requires --partner-id")
        result = score_partner(args.partner_id)

    elif args.workshare:
        if not args.opportunity_id:
            parser.error("--workshare requires --opportunity-id")
        result = track_workshare(args.opportunity_id)

    elif args.ta_lifecycle:
        if not args.opportunity_id:
            parser.error("--ta-lifecycle requires --opportunity-id")
        result = manage_ta_lifecycle(args.opportunity_id)

    elif args.oci_screen:
        if not args.opportunity_id or not args.partner_name:
            parser.error("--oci-screen requires --opportunity-id and --partner-name")
        result = screen_oci(
            args.opportunity_id,
            args.partner_name,
        )

    elif args.team_status:
        if not args.opportunity_id:
            parser.error("--team-status requires --opportunity-id")
        result = get_team_status(args.opportunity_id)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        for k, v in result.items():
            if isinstance(v, (list, dict)):
                print(f"{k}:")
                print(json.dumps(v, indent=2, default=str))
            else:
                print(f"{k}: {v}")
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
