# CUI // SP-CTI
"""Federal Network Peering Agreement — Technical and Legal Review (Step 9).

Reviews a draft peering agreement for technical accuracy, legal compliance,
and enforceability before routing to final approval.

Workflow: processify-wfl-f77f
Step 9 of N: … → Draft Preparation → [Technical & Legal Review] → Approval

DB: nc_agreement_reviews
Classification: CUI // SP-CTI
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKFLOW_ID = "processify-wfl-f77f"
STEP_NUMBER = 9
STEP_NAME = "technical_legal_review"
CLASSIFICATION = "CUI // SP-CTI"

REVIEW_OUTCOMES = (
    "approved",                # No issues — ready for signature
    "approved_with_conditions", # Minor issues noted; may proceed with fixes
    "requires_revision",       # Material issues; draft must be revised
    "rejected",                # Fatal defects; workflow must restart
)

FINDING_SEVERITIES = (
    "critical",   # Blocks approval
    "major",      # Requires revision or waiver
    "minor",      # Should be corrected; does not block
    "info",       # Informational; no action required
)

FINDING_CATEGORIES = (
    "technical",
    "legal",
    "compliance",
    "completeness",
)

# Valid ASN ranges (RFC 4893 / IANA)
_ASN_MIN = 1
_ASN_MAX = 4_294_967_295
_ASN_PRIVATE_16_START = 64_512
_ASN_PRIVATE_16_END = 65_535
_ASN_PRIVATE_32_START = 4_200_000_000
_ASN_PRIVATE_32_END = 4_294_967_295

# Acceptable SLA bounds for federal interconnects (DISA SRG / FedRAMP)
_SLA_LATENCY_MAX_MS = 100        # >100 ms is unusual for federal PNI
_SLA_PACKET_LOSS_MAX_PCT = 0.1   # >0.1% is unacceptable for mission-critical
_SLA_UPTIME_MIN_PCT = 99.5       # <99.5% violates most federal SLAs
_SLA_UPTIME_MAX_PCT = 100.0

_CONTRACT_TERM_MAX_MONTHS = 60   # >5-year terms require re-compete scrutiny

# Required legal entity fields for federal parties
_REQUIRED_FEDERAL_FIELDS = (
    "party_a_org",
    "party_a_cage_code",
    "party_a_agency",
    "party_a_poc_name",
    "party_a_poc_email",
    "party_a_legal_entity",
    "party_b_org",
    "party_b_poc_name",
    "party_b_poc_email",
    "mission_justification",
    "governing_law",
    "notices_address_a",
    "notices_address_b",
)

# Required sections that must be non-empty in a formal contract
_REQUIRED_SECTIONS = (
    "parties",
    "definitions",
    "peering_terms",
    "routing_policy",
    "security_compliance",
    "escalation_procedures",
    "dispute_resolution",
    "signatures",
)

# Compliance frameworks that must be referenced in security_compliance section
_COMPLIANCE_KEYWORDS = (
    "FISMA",
    "FedRAMP",
    "NIST",
    "FAR",
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS nc_agreement_reviews (
    id                  TEXT PRIMARY KEY,
    draft_id            TEXT NOT NULL,
    workflow_id         TEXT NOT NULL DEFAULT 'processify-wfl-f77f',
    step_number         INTEGER NOT NULL DEFAULT 9,

    reviewer_name       TEXT NOT NULL DEFAULT '',
    reviewer_role       TEXT NOT NULL DEFAULT '',

    outcome             TEXT NOT NULL DEFAULT 'requires_revision',
    technical_score     INTEGER NOT NULL DEFAULT 0,   -- 0-100
    legal_score         INTEGER NOT NULL DEFAULT 0,    -- 0-100
    compliance_score    INTEGER NOT NULL DEFAULT 0,    -- 0-100
    overall_score       INTEGER NOT NULL DEFAULT 0,    -- 0-100

    findings            TEXT NOT NULL DEFAULT '[]',   -- JSON array
    summary             TEXT NOT NULL DEFAULT '',
    recommendations     TEXT NOT NULL DEFAULT '[]',   -- JSON array

    classification      TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nar_draft    ON nc_agreement_reviews(draft_id);
CREATE INDEX IF NOT EXISTS idx_nar_outcome  ON nc_agreement_reviews(outcome);
CREATE INDEX IF NOT EXISTS idx_nar_workflow ON nc_agreement_reviews(workflow_id);
"""

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS nc_agreement_reviews (
    id                  TEXT PRIMARY KEY,
    draft_id            TEXT NOT NULL,
    workflow_id         TEXT NOT NULL DEFAULT 'processify-wfl-f77f',
    step_number         INTEGER NOT NULL DEFAULT 9,

    reviewer_name       TEXT NOT NULL DEFAULT '',
    reviewer_role       TEXT NOT NULL DEFAULT '',

    outcome             TEXT NOT NULL DEFAULT 'requires_revision',
    technical_score     INTEGER NOT NULL DEFAULT 0,
    legal_score         INTEGER NOT NULL DEFAULT 0,
    compliance_score    INTEGER NOT NULL DEFAULT 0,
    overall_score       INTEGER NOT NULL DEFAULT 0,

    findings            TEXT NOT NULL DEFAULT '[]',
    summary             TEXT NOT NULL DEFAULT '',
    recommendations     TEXT NOT NULL DEFAULT '[]',

    classification      TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_finding(
    category: str,
    severity: str,
    code: str,
    message: str,
    field: str = "",
    value: Any = None,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "category": category,
        "severity": severity,
        "code": code,
        "message": message,
        "field": field,
        "value": str(value) if value is not None else "",
    }


def _score_from_findings(findings: list[dict]) -> int:
    """Return 0–100 score; each critical deducts 30, major 15, minor 5."""
    deductions = 0
    for f in findings:
        sev = f.get("severity", "info")
        if sev == "critical":
            deductions += 30
        elif sev == "major":
            deductions += 15
        elif sev == "minor":
            deductions += 5
    return max(0, 100 - deductions)


def _is_private_asn(asn: int) -> bool:
    return (
        _ASN_PRIVATE_16_START <= asn <= _ASN_PRIVATE_16_END
        or _ASN_PRIVATE_32_START <= asn <= _ASN_PRIVATE_32_END
    )


# ---------------------------------------------------------------------------
# Technical review checks
# ---------------------------------------------------------------------------

def run_technical_checks(draft: dict[str, Any]) -> list[dict]:
    findings: list[dict] = []

    # ASN validation
    for party, field in (("Party A", "party_a_asn"), ("Party B", "party_b_asn")):
        asn = draft.get(field)
        if asn is None:
            findings.append(_make_finding(
                "technical", "major", "TECH-001",
                f"{party} ASN is missing — required for BGP session establishment.",
                field,
            ))
        else:
            try:
                asn_int = int(asn)
            except (TypeError, ValueError):
                findings.append(_make_finding(
                    "technical", "critical", "TECH-002",
                    f"{party} ASN is not a valid integer.",
                    field, asn,
                ))
                continue

            if not (_ASN_MIN <= asn_int <= _ASN_MAX):
                findings.append(_make_finding(
                    "technical", "critical", "TECH-003",
                    f"{party} ASN {asn_int} is outside the valid range "
                    f"({_ASN_MIN}–{_ASN_MAX}).",
                    field, asn_int,
                ))
            elif _is_private_asn(asn_int):
                findings.append(_make_finding(
                    "technical", "major", "TECH-004",
                    f"{party} ASN {asn_int} is a private/reserved ASN.  "
                    "Federal internet-facing peering must use a registered public ASN.",
                    field, asn_int,
                ))

    # Routing protocol
    protocol = draft.get("routing_protocol", "")
    if not protocol:
        findings.append(_make_finding(
            "technical", "critical", "TECH-005",
            "Routing protocol is unspecified — BGP is required for inter-agency peering.",
            "routing_protocol",
        ))
    elif protocol.upper() not in ("BGP", "EBGP", "IBGP"):
        findings.append(_make_finding(
            "technical", "major", "TECH-006",
            f"Routing protocol '{protocol}' is non-standard for federal peering; "
            "BGP/eBGP is required.",
            "routing_protocol", protocol,
        ))

    # Prefix limit
    prefix_limit = draft.get("prefix_limit")
    if prefix_limit is None:
        findings.append(_make_finding(
            "technical", "major", "TECH-007",
            "Prefix limit is not specified — omission allows unbounded route advertisement "
            "and increases DDoS/route-leak risk.",
            "prefix_limit",
        ))
    elif int(prefix_limit) <= 0:
        findings.append(_make_finding(
            "technical", "critical", "TECH-008",
            f"Prefix limit must be a positive integer; received {prefix_limit}.",
            "prefix_limit", prefix_limit,
        ))

    # Port speed
    port_speed = draft.get("port_speed", "")
    if not port_speed:
        findings.append(_make_finding(
            "technical", "minor", "TECH-009",
            "Port speed is unspecified.  Specify capacity (e.g. '10G', '100G') "
            "to define baseline throughput commitments.",
            "port_speed",
        ))

    # Interconnection points
    ixp_raw = draft.get("interconnection_points", "[]")
    try:
        ixp_list = json.loads(ixp_raw) if isinstance(ixp_raw, str) else ixp_raw
    except (json.JSONDecodeError, TypeError):
        ixp_list = []
    if not ixp_list:
        findings.append(_make_finding(
            "technical", "major", "TECH-010",
            "No interconnection points (IXP/PNI locations) are listed.  "
            "Physical peering location(s) are required for implementation.",
            "interconnection_points",
        ))

    # SLA: latency
    latency = draft.get("sla_latency_ms")
    if latency is not None:
        try:
            lat_int = int(latency)
            if lat_int > _SLA_LATENCY_MAX_MS:
                findings.append(_make_finding(
                    "technical", "major", "TECH-011",
                    f"SLA latency target {lat_int} ms exceeds recommended federal "
                    f"threshold of {_SLA_LATENCY_MAX_MS} ms for mission-critical interconnects.",
                    "sla_latency_ms", lat_int,
                ))
        except (TypeError, ValueError):
            findings.append(_make_finding(
                "technical", "minor", "TECH-012",
                "SLA latency value is not a valid integer.",
                "sla_latency_ms", latency,
            ))

    # SLA: packet loss
    pkt_loss = draft.get("sla_packet_loss_pct")
    if pkt_loss is not None:
        try:
            pkt_float = float(pkt_loss)
            if pkt_float > _SLA_PACKET_LOSS_MAX_PCT:
                findings.append(_make_finding(
                    "technical", "major", "TECH-013",
                    f"SLA packet loss {pkt_float}% exceeds the maximum allowable "
                    f"{_SLA_PACKET_LOSS_MAX_PCT}% for federal networks.",
                    "sla_packet_loss_pct", pkt_float,
                ))
        except (TypeError, ValueError):
            findings.append(_make_finding(
                "technical", "minor", "TECH-014",
                "SLA packet loss value is not a valid number.",
                "sla_packet_loss_pct", pkt_loss,
            ))

    # SLA: uptime
    uptime = draft.get("sla_uptime_pct")
    if uptime is not None:
        try:
            up_float = float(uptime)
            if up_float < _SLA_UPTIME_MIN_PCT:
                findings.append(_make_finding(
                    "technical", "major", "TECH-015",
                    f"SLA uptime {up_float}% is below the minimum {_SLA_UPTIME_MIN_PCT}% "
                    "required for federal interconnects (DISA SRG).",
                    "sla_uptime_pct", up_float,
                ))
            if up_float > _SLA_UPTIME_MAX_PCT:
                findings.append(_make_finding(
                    "technical", "minor", "TECH-016",
                    f"SLA uptime {up_float}% exceeds 100%; likely a data entry error.",
                    "sla_uptime_pct", up_float,
                ))
        except (TypeError, ValueError):
            findings.append(_make_finding(
                "technical", "minor", "TECH-017",
                "SLA uptime value is not a valid number.",
                "sla_uptime_pct", uptime,
            ))

    # Effective / expiry dates
    effective = draft.get("effective_date", "")
    expiry = draft.get("expiry_date", "")
    if not effective:
        findings.append(_make_finding(
            "technical", "major", "TECH-018",
            "Effective date is missing — agreement cannot be implemented without a "
            "binding start date.",
            "effective_date",
        ))
    if not expiry:
        findings.append(_make_finding(
            "technical", "minor", "TECH-019",
            "Expiry date is missing.  Open-ended agreements complicate renewal tracking; "
            "a defined term is recommended.",
            "expiry_date",
        ))
    if effective and expiry:
        if expiry <= effective:
            findings.append(_make_finding(
                "technical", "critical", "TECH-020",
                f"Expiry date ({expiry}) is not after effective date ({effective}).",
                "expiry_date",
            ))

    # Contract term length
    term = draft.get("contract_term_months")
    if term is not None:
        try:
            term_int = int(term)
            if term_int > _CONTRACT_TERM_MAX_MONTHS:
                findings.append(_make_finding(
                    "technical", "minor", "TECH-021",
                    f"Contract term of {term_int} months exceeds {_CONTRACT_TERM_MAX_MONTHS} "
                    "months; long terms may require additional acquisition authority review.",
                    "contract_term_months", term_int,
                ))
        except (TypeError, ValueError):
            pass

    return findings


# ---------------------------------------------------------------------------
# Legal review checks
# ---------------------------------------------------------------------------

def run_legal_checks(draft: dict[str, Any]) -> list[dict]:
    findings: list[dict] = []

    # Required fields completeness
    for field in _REQUIRED_FEDERAL_FIELDS:
        val = draft.get(field, "")
        if not val:
            findings.append(_make_finding(
                "legal", "major", "LEGAL-001",
                f"Required field '{field}' is empty.  Federal peering agreements "
                "must fully identify all parties and their legal authority.",
                field,
            ))

    # Governing law must be Federal
    governing_law = draft.get("governing_law", "")
    if governing_law and governing_law.strip().lower() not in ("federal", "u.s. federal"):
        findings.append(_make_finding(
            "legal", "major", "LEGAL-002",
            f"Governing law '{governing_law}' is not 'Federal'.  Inter-agency "
            "agreements are subject to federal law per FAR 1.102.",
            "governing_law", governing_law,
        ))

    # Classification marking
    classification = draft.get("classification", "")
    if not classification:
        findings.append(_make_finding(
            "legal", "critical", "LEGAL-003",
            "Classification marking is absent.  All federal network agreements "
            "must bear a classification marking (at minimum CUI).",
            "classification",
        ))

    # Mission justification (required for federal service contracts)
    mission = draft.get("mission_justification", "")
    if len(mission) < 50:
        findings.append(_make_finding(
            "legal", "major", "LEGAL-004",
            "Mission justification is too brief or missing (< 50 characters).  "
            "FAR 6.302 requires documented justification for non-competitive acquisitions.",
            "mission_justification", mission,
        ))

    # Dispute resolution cross-reference
    dispute_id = draft.get("dispute_proc_id", "")
    if not dispute_id:
        findings.append(_make_finding(
            "legal", "major", "LEGAL-005",
            "Dispute procedure ID is not linked.  The agreement must reference the "
            "executed dispute resolution procedure from Step 7.",
            "dispute_proc_id",
        ))

    # Escalation procedure cross-reference
    esc_id = draft.get("escalation_proc_id", "")
    if not esc_id:
        findings.append(_make_finding(
            "legal", "major", "LEGAL-006",
            "Escalation procedure ID is not linked.  Cross-reference to the Step 7 "
            "escalation matrix is required for enforceability.",
            "escalation_proc_id",
        ))

    # Both parties need legal entity names
    for party_label, field in (
        ("Party A", "party_a_legal_entity"),
        ("Party B", "party_b_legal_entity"),
    ):
        if not draft.get(field, ""):
            findings.append(_make_finding(
                "legal", "major", "LEGAL-007",
                f"{party_label} legal entity name is missing.  Contracts must identify "
                "the contracting entity (agency, bureau, or component) explicitly.",
                field,
            ))

    # Financial terms — flag if monthly_cost is missing for transit type
    peering_type = draft.get("peering_type", "")
    monthly_cost = draft.get("monthly_cost")
    if peering_type == "transit" and (monthly_cost is None or float(monthly_cost or 0) <= 0):
        findings.append(_make_finding(
            "legal", "major", "LEGAL-008",
            "Peering type is 'transit' but monthly cost is zero or unspecified.  "
            "Transit agreements are not settlement-free; financial terms are required.",
            "monthly_cost", monthly_cost,
        ))

    # Document must not be in a terminal status for review
    status = draft.get("status", "")
    if status in ("executed", "terminated", "superseded"):
        findings.append(_make_finding(
            "legal", "info", "LEGAL-009",
            f"Draft status is '{status}' — this agreement has already reached a "
            "terminal state and may not require further review.",
            "status", status,
        ))

    return findings


# ---------------------------------------------------------------------------
# Compliance review checks
# ---------------------------------------------------------------------------

def run_compliance_checks(
    draft: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
) -> list[dict]:
    findings: list[dict] = []
    sections = sections or []

    # Verify required sections exist and have content
    section_map = {s.get("section_name", ""): s.get("content", "") for s in sections}

    for sec in _REQUIRED_SECTIONS:
        if sec not in section_map or not section_map[sec].strip():
            findings.append(_make_finding(
                "compliance", "major", "COMP-001",
                f"Required section '{sec}' is missing or empty in the draft document.  "
                "All formal federal agreements must include this section.",
                "sections", sec,
            ))

    # Check security_compliance section references key frameworks
    sec_compliance_text = section_map.get("security_compliance", "")
    for keyword in _COMPLIANCE_KEYWORDS:
        if keyword not in sec_compliance_text:
            findings.append(_make_finding(
                "compliance", "minor", "COMP-002",
                f"Security/compliance section does not reference '{keyword}'.  "
                "Federal agreements should cite applicable compliance frameworks.",
                "sections.security_compliance", keyword,
            ))

    # CAGE code required for any federal agency (Party A)
    cage = draft.get("party_a_cage_code", "")
    if not cage:
        findings.append(_make_finding(
            "compliance", "major", "COMP-003",
            "Party A CAGE code is missing.  CAGE codes are required for all federal "
            "procurement and acquisition records (DFARS 204.7202).",
            "party_a_cage_code",
        ))
    elif len(cage) != 5 or not cage[0].isdigit():
        findings.append(_make_finding(
            "compliance", "minor", "COMP-004",
            f"CAGE code '{cage}' does not match the expected 5-character format "
            "(first character numeric).",
            "party_a_cage_code", cage,
        ))

    # Request ID cross-reference (traceability to the original service request)
    if not draft.get("request_id", ""):
        findings.append(_make_finding(
            "compliance", "minor", "COMP-005",
            "Original request ID is not linked.  Traceability to the initiating "
            "service request is required for audit purposes (NIST AU-9).",
            "request_id",
        ))

    return findings


# ---------------------------------------------------------------------------
# Overall review orchestrator
# ---------------------------------------------------------------------------

def review_draft(
    draft: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
    reviewer_name: str = "",
    reviewer_role: str = "",
) -> dict[str, Any]:
    """Run all technical, legal, and compliance checks on *draft*.

    Returns a review record ready for persistence via ``save_review()``.
    """
    tech_findings = run_technical_checks(draft)
    legal_findings = run_legal_checks(draft)
    comp_findings = run_compliance_checks(draft, sections)

    all_findings = tech_findings + legal_findings + comp_findings

    tech_score = _score_from_findings(tech_findings)
    legal_score = _score_from_findings(legal_findings)
    comp_score = _score_from_findings(comp_findings)
    overall_score = (tech_score + legal_score + comp_score) // 3

    # Determine outcome
    criticals = [f for f in all_findings if f["severity"] == "critical"]
    majors = [f for f in all_findings if f["severity"] == "major"]

    if criticals:
        outcome = "rejected" if len(criticals) >= 3 else "requires_revision"
    elif majors:
        outcome = "approved_with_conditions" if len(majors) <= 2 else "requires_revision"
    else:
        outcome = "approved"

    # Build recommendations
    recommendations: list[str] = []
    for f in all_findings:
        if f["severity"] in ("critical", "major"):
            recommendations.append(f"[{f['code']}] {f['message']}")

    # Narrative summary
    summary_lines = [
        f"Review completed for draft '{draft.get('id', 'unknown')}'.",
        f"Peering type: {draft.get('peering_type', 'unspecified')}  |  "
        f"Format: {draft.get('document_format', 'unspecified')}.",
        f"Scores — Technical: {tech_score}/100  Legal: {legal_score}/100  "
        f"Compliance: {comp_score}/100  Overall: {overall_score}/100.",
        f"Findings: {len(criticals)} critical, {len(majors)} major, "
        f"{len([f for f in all_findings if f['severity']=='minor'])} minor.",
        f"Outcome: {outcome.upper().replace('_', ' ')}.",
    ]
    if criticals:
        summary_lines.append(
            "CRITICAL issues must be resolved before the agreement proceeds to signature."
        )
    elif majors:
        summary_lines.append(
            "Major issues should be addressed; conditional approval may be granted "
            "with written acknowledgement from both POCs."
        )
    else:
        summary_lines.append(
            "No blocking issues found.  Agreement is recommended for final approval."
        )

    now = _now_iso()
    return {
        "id": str(uuid.uuid4()),
        "draft_id": draft.get("id", ""),
        "workflow_id": WORKFLOW_ID,
        "step_number": STEP_NUMBER,
        "reviewer_name": reviewer_name,
        "reviewer_role": reviewer_role,
        "outcome": outcome,
        "technical_score": tech_score,
        "legal_score": legal_score,
        "compliance_score": comp_score,
        "overall_score": overall_score,
        "findings": json.dumps(all_findings),
        "summary": "\n".join(summary_lines),
        "recommendations": json.dumps(recommendations),
        "classification": CLASSIFICATION,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def init_db(conn: Any) -> None:
    """Create nc_agreement_reviews table if it does not exist."""
    is_pg = hasattr(conn, "server_version")
    schema = SCHEMA_PG if is_pg else SCHEMA_SQLITE
    with conn.cursor() as cur:
        cur.execute(schema)
    conn.commit()


def save_review(conn: Any, review: dict[str, Any]) -> str:
    """Insert a review record and return its id."""
    sql = """
        INSERT INTO nc_agreement_reviews
            (id, draft_id, workflow_id, step_number,
             reviewer_name, reviewer_role,
             outcome, technical_score, legal_score, compliance_score, overall_score,
             findings, summary, recommendations,
             classification, created_at, updated_at)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    is_pg = hasattr(conn, "server_version")
    if not is_pg:
        sql = sql.replace("%s", "?")
    with conn.cursor() as cur:
        cur.execute(sql, [
            review["id"],
            review["draft_id"],
            review["workflow_id"],
            review["step_number"],
            review["reviewer_name"],
            review["reviewer_role"],
            review["outcome"],
            review["technical_score"],
            review["legal_score"],
            review["compliance_score"],
            review["overall_score"],
            review["findings"],
            review["summary"],
            review["recommendations"],
            review["classification"],
            review["created_at"],
            review["updated_at"],
        ])
    conn.commit()
    return review["id"]


def get_review(conn: Any, review_id: str) -> dict[str, Any] | None:
    """Fetch a review record by id."""
    sql = "SELECT * FROM nc_agreement_reviews WHERE id = %s"
    is_pg = hasattr(conn, "server_version")
    if not is_pg:
        sql = sql.replace("%s", "?")
    with conn.cursor() as cur:
        cur.execute(sql, [review_id])
        row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def list_reviews_for_draft(conn: Any, draft_id: str) -> list[dict[str, Any]]:
    """Return all reviews for a given draft id, newest first."""
    sql = """
        SELECT * FROM nc_agreement_reviews
        WHERE draft_id = %s
        ORDER BY created_at DESC
    """
    is_pg = hasattr(conn, "server_version")
    if not is_pg:
        sql = sql.replace("%s", "?")
    with conn.cursor() as cur:
        cur.execute(sql, [draft_id])
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def get_latest_outcome(conn: Any, draft_id: str) -> str | None:
    """Return the outcome of the most recent review for *draft_id*."""
    reviews = list_reviews_for_draft(conn, draft_id)
    if not reviews:
        return None
    return reviews[0]["outcome"]


# ---------------------------------------------------------------------------
# Convenience: review summary report (plaintext)
# ---------------------------------------------------------------------------

def format_review_report(review: dict[str, Any]) -> str:
    """Render a human-readable CUI-marked review report."""
    findings_raw = review.get("findings", "[]")
    findings = json.loads(findings_raw) if isinstance(findings_raw, str) else findings_raw
    recs_raw = review.get("recommendations", "[]")
    recs = json.loads(recs_raw) if isinstance(recs_raw, str) else recs_raw

    lines = [
        f"{'=' * 70}",
        f"  {CLASSIFICATION}",
        f"  FEDERAL NETWORK PEERING AGREEMENT — TECHNICAL & LEGAL REVIEW",
        f"  Workflow: {review.get('workflow_id')}  |  Step: {review.get('step_number')}",
        f"  Review ID:  {review.get('id')}",
        f"  Draft ID:   {review.get('draft_id')}",
        f"  Reviewer:   {review.get('reviewer_name')} ({review.get('reviewer_role')})",
        f"  Generated:  {review.get('created_at')}",
        f"{'=' * 70}",
        "",
        f"OUTCOME: {review.get('outcome', '').upper().replace('_', ' ')}",
        "",
        "SCORES",
        f"  Technical   : {review.get('technical_score')}/100",
        f"  Legal       : {review.get('legal_score')}/100",
        f"  Compliance  : {review.get('compliance_score')}/100",
        f"  Overall     : {review.get('overall_score')}/100",
        "",
        "EXECUTIVE SUMMARY",
    ]
    for ln in review.get("summary", "").splitlines():
        lines.append(f"  {ln}")

    if findings:
        lines += ["", "FINDINGS"]
        for f in findings:
            lines.append(
                f"  [{f['severity'].upper():8}] {f['code']} | {f.get('field','')} | {f['message']}"
            )

    if recs:
        lines += ["", "RECOMMENDATIONS"]
        for i, r in enumerate(recs, 1):
            lines.append(f"  {i}. {r}")

    lines += ["", f"{'=' * 70}", f"  {CLASSIFICATION}", f"{'=' * 70}"]
    return "\n".join(lines)
