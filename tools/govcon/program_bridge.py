#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Proposal-to-Program Knowledge Bridge -- auto-generate transition packages (Phase 4).

When an opportunity transitions from bid to award, this tool generates a
comprehensive structured transition package from all proposal data, giving
the program team everything they need to stand up execution.

Gathers from DB:
    - Opportunity details (proposal_opportunities)
    - Win themes and discriminators (pg_win_themes)
    - Key personnel mentioned in drafts (proposal_section_drafts)
    - Teaming workshare agreements (pg_teaming_workshare, pg_teaming_partners)
    - Pricing assumptions and BOE (pg_cost_volumes, pg_lcat_allocations)
    - Risk items from review findings (pg_review_findings)
    - CDRL requirements (proposal_compliance_matrix where requirement_type = 'C')
    - CMMC compliance status (pg_cmmc_supply_chain)
    - Bid decision rationale (pg_bid_decisions)

Generates markdown document with 11 sections and stores metadata in
pg_program_bridges table.

Tables used:
    - pg_program_bridges (write -- bridge metadata, allows UPDATE for refresh)
    - audit_trail (write -- NIST AU-2)

Usage:
    python tools/govcon/program_bridge.py --opportunity-id "opp-xxx" --generate --json
    python tools/govcon/program_bridge.py --opportunity-id "opp-xxx" --coverage --json
    python tools/govcon/program_bridge.py --list --json
    python tools/govcon/program_bridge.py --get --bridge-id "pgb-xxx" --json
    python tools/govcon/program_bridge.py --opportunity-id "opp-xxx" --refresh --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.program_bridge")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BRIDGE_DIR = _ROOT / "data" / "proposal_genesis" / "bridges"

# Bridge document sections in order
BRIDGE_SECTIONS = [
    "Executive Summary",
    "Customer Intelligence",
    "Win Strategy",
    "Technical Approach Summary",
    "Key Personnel & Staffing Plan",
    "Teaming Arrangements & Workshare",
    "Pricing Summary & BOE",
    "Compliance Commitments",
    "CDRL Delivery Schedule",
    "Risk Register",
    "Open Issues & Recommendations",
]

# Name-like patterns for key personnel extraction from draft text
_NAME_PATTERN = re.compile(
    r"(?:(?:Mr|Ms|Mrs|Dr|Prof)\.?\s+)?([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str = "pgb") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, action: str, details: str = "", opportunity_id: str = "") -> None:
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                "govcon.program_bridge",
                "program_bridge",
                action,
                details,
                opportunity_id or None,
                "proposal_genesis",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _ensure_tables(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pg_program_bridges (
            id                  TEXT PRIMARY KEY,
            opportunity_id      TEXT NOT NULL,
            version             INTEGER DEFAULT 1,
            sections_populated  INTEGER,
            total_sections      INTEGER DEFAULT 11,
            data_coverage_pct   REAL,
            file_path           TEXT,
            status              TEXT DEFAULT 'generated',
            created_at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pgb_opp ON pg_program_bridges(opportunity_id);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data gathering helpers (each returns a dict with data + record_count)
# ---------------------------------------------------------------------------
def _gather_opportunity(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch opportunity details."""
    row = conn.execute("SELECT * FROM proposal_opportunities WHERE id = %s", (opp_id,)).fetchone()
    if not row:
        return {"data": None, "record_count": 0}
    cols = [d[0] for d in conn.execute("SELECT * FROM proposal_opportunities LIMIT 0").description]
    return {"data": dict(zip(cols, row)), "record_count": 1}


def _gather_win_themes(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch win themes and discriminators."""
    try:
        rows = conn.execute(
            "SELECT * FROM pg_win_themes WHERE opportunity_id = %s AND status = 'active' ORDER BY priority",
            (opp_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM pg_win_themes LIMIT 0").description]
        return {"data": [dict(zip(cols, r)) for r in rows], "record_count": len(rows)}
    except Exception:
        return {"data": [], "record_count": 0}


def _gather_key_personnel(conn, opp_id: str) -> Dict[str, Any]:
    """Key personnel for the bid — from the REGISTRY, falling back to the old scrape.

    prem-pstaff-01/02. This used to be nothing but a regex over proposal prose:
    ``_NAME_PATTERN`` matches a capitalised bigram, so "Program Manager", "Technical
    Approach" and "Agile Delivery" all came back as people. The result was a bare list
    of strings — no LCAT, no qualification verdict, no evidence — and it fed the "Key
    Personnel & Staffing Plan" section of a real proposal.

    ``proposal_key_personnel`` is now the source of truth: a named person, a proposed
    labour category, a qualification verdict, and the resume evidence behind it. Every
    row in it is evidenced, because the intake refuses unevidenced mappings and a CHECK
    constraint backs that up.

    The regex is kept ONLY as a fallback for opportunities that pre-date the registry,
    and it now says so: those entries are marked ``source: "scraped"`` and carry
    ``evidence: []``, so a reader can see at a glance which names are defensible and
    which were guessed at by a pattern match. Silently mixing the two would be worse
    than either.
    """
    try:
        from tools.govcon.key_personnel import list_key_personnel

        people = list_key_personnel(opp_id, conn=conn)
        if people:
            return {
                "data": [
                    {
                        "name": p["name"],
                        "proposed_lcat": p["proposed_lcat"],
                        "qualification_verdict": p["qualification_verdict"],
                        "evidence": p.get("evidence") or [],
                        "source": p.get("source") or "compass",
                    }
                    for p in people
                ],
                "record_count": len(people),
                "source": "proposal_key_personnel",
            }
    except Exception:
        # Registry unavailable (table not migrated yet) — fall through to the scrape
        # rather than losing the section entirely.
        pass

    try:
        rows = conn.execute(
            "SELECT draft_content FROM proposal_section_drafts WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchall()
        names: list[str] = []
        seen: set[str] = set()
        for (content,) in rows:
            if not content:
                continue
            matches = _NAME_PATTERN.findall(content)
            for name in matches:
                name_clean = name.strip()
                if name_clean not in seen and len(name_clean) > 4:
                    seen.add(name_clean)
                    names.append(name_clean)
        return {
            "data": [
                {
                    "name": n,
                    "proposed_lcat": "",
                    "qualification_verdict": "",
                    "evidence": [],
                    "source": "scraped",
                }
                for n in names
            ],
            "record_count": len(names),
            "source": "regex_scrape_legacy",
        }
    except Exception:
        return {"data": [], "record_count": 0, "source": "unavailable"}


def _gather_teaming(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch teaming workshare agreements and partner info."""
    results: list[dict] = []
    try:
        rows = conn.execute(
            "SELECT w.*, p.name AS partner_name, p.capabilities, p.certifications "
            "FROM pg_teaming_workshare w "
            "LEFT JOIN pg_teaming_partners p ON w.partner_id = p.id "
            "WHERE w.opportunity_id = %s",
            (opp_id,),
        ).fetchall()
        if rows:
            cols = [
                d[0]
                for d in conn.execute(
                    "SELECT w.*, p.name AS partner_name, p.capabilities, p.certifications "
                    "FROM pg_teaming_workshare w "
                    "LEFT JOIN pg_teaming_partners p ON w.partner_id = p.id LIMIT 0"
                ).description
            ]
            results = [dict(zip(cols, r)) for r in rows]
    except Exception:
        pass
    # Fallback: try pg_teaming_partners alone
    if not results:
        try:
            rows = conn.execute(
                "SELECT tp.* FROM pg_teaming_assessments ta "
                "JOIN pg_teaming_partners tp ON ta.partner_id = tp.id "
                "WHERE ta.opportunity_id = %s",
                (opp_id,),
            ).fetchall()
            if rows:
                cols = [d[0] for d in conn.execute("SELECT * FROM pg_teaming_partners LIMIT 0").description]
                results = [dict(zip(cols, r)) for r in rows]
        except Exception:
            pass
    return {"data": results, "record_count": len(results)}


def _gather_pricing(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch cost volumes and LCAT allocations."""
    result: Dict[str, Any] = {"volumes": [], "lcats": []}
    try:
        rows = conn.execute("SELECT * FROM pg_cost_volumes WHERE opportunity_id = %s", (opp_id,)).fetchall()
        if rows:
            cols = [d[0] for d in conn.execute("SELECT * FROM pg_cost_volumes LIMIT 0").description]
            result["volumes"] = [dict(zip(cols, r)) for r in rows]
            # Gather LCAT allocations for each cost volume
            for vol in result["volumes"]:
                lcat_rows = conn.execute(
                    "SELECT * FROM pg_lcat_allocations WHERE cost_volume_id = %s",
                    (vol["id"],),
                ).fetchall()
                if lcat_rows:
                    lcat_cols = [d[0] for d in conn.execute("SELECT * FROM pg_lcat_allocations LIMIT 0").description]
                    result["lcats"].extend([dict(zip(lcat_cols, r)) for r in lcat_rows])
    except Exception:
        pass
    count = len(result["volumes"]) + len(result["lcats"])
    return {"data": result, "record_count": count}


def _gather_risks(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch critical/major review findings as risk items."""
    try:
        rows = conn.execute(
            "SELECT * FROM pg_review_findings "
            "WHERE opportunity_id = %s AND severity IN ('critical', 'major') "
            "ORDER BY severity, created_at",
            (opp_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM pg_review_findings LIMIT 0").description]
        return {"data": [dict(zip(cols, r)) for r in rows], "record_count": len(rows)}
    except Exception:
        return {"data": [], "record_count": 0}


def _gather_cdrls(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch CDRL requirements from THE compliance matrix (requirement_type = 'C').

    proposal_compliance_matrix, since rmf-rfp-01 folded pg_compliance_matrix
    into it; Section C rows are what compliance_matrix_builder.parse_section_c
    extracts.
    """
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_compliance_matrix "
            "WHERE opportunity_id = %s AND requirement_type = 'C' "
            "ORDER BY sort_order, section_ref",
            (opp_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM proposal_compliance_matrix LIMIT 0").description]
        return {"data": [dict(zip(cols, r)) for r in rows], "record_count": len(rows)}
    except Exception:
        return {"data": [], "record_count": 0}


def _gather_cmmc(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch CMMC supply chain compliance status."""
    try:
        rows = conn.execute(
            "SELECT * FROM pg_cmmc_supply_chain WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM pg_cmmc_supply_chain LIMIT 0").description]
        return {"data": [dict(zip(cols, r)) for r in rows], "record_count": len(rows)}
    except Exception:
        return {"data": [], "record_count": 0}


def _gather_bid_decisions(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch bid decision rationale."""
    try:
        rows = conn.execute(
            "SELECT * FROM pg_bid_decisions WHERE opportunity_id = %s ORDER BY created_at DESC",
            (opp_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM pg_bid_decisions LIMIT 0").description]
        return {"data": [dict(zip(cols, r)) for r in rows], "record_count": len(rows)}
    except Exception:
        return {"data": [], "record_count": 0}


def _gather_drafts(conn, opp_id: str) -> Dict[str, Any]:
    """Fetch approved/reviewed proposal section drafts for technical summary."""
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_section_drafts "
            "WHERE opportunity_id = %s AND status IN ('approved', 'reviewed') "
            "ORDER BY created_at",
            (opp_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM proposal_section_drafts LIMIT 0").description]
        return {"data": [dict(zip(cols, r)) for r in rows], "record_count": len(rows)}
    except Exception:
        return {"data": [], "record_count": 0}


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------
def _render_executive_summary(opp: Optional[Dict], bid: List[Dict]) -> str:
    lines = ["## Executive Summary\n"]
    if opp:
        lines.append(f"**Solicitation:** {opp.get('solicitation_number', 'N/A')}")
        lines.append(f"**Title:** {opp.get('title', 'N/A')}")
        lines.append(f"**Agency:** {opp.get('agency', 'N/A')}")
        sub = opp.get("sub_agency")
        if sub:
            lines.append(f"**Sub-Agency:** {sub}")
        lines.append(f"**Status:** {opp.get('status', 'N/A')}")
        lines.append(f"**Proposal Type:** {opp.get('proposal_type', 'N/A')}")
        val_low = opp.get("estimated_value_low")
        val_high = opp.get("estimated_value_high")
        if val_low or val_high:
            lines.append(f"**Estimated Value:** ${val_low or 0:,.0f} - ${val_high or 0:,.0f}")
        lines.append(f"**Classification:** {opp.get('classification', 'CUI')}")
    if bid:
        latest = bid[0]
        lines.append(f"\n**Bid Decision:** {latest.get('decision', 'N/A')}")
        if latest.get("rationale"):
            lines.append(f"**Rationale:** {latest['rationale']}")
        if latest.get("win_probability") is not None:
            lines.append(f"**Win Probability:** {latest['win_probability']:.0%}")
    lines.append("")
    return "\n".join(lines)


def _render_customer_intel(opp: Optional[Dict]) -> str:
    lines = ["## Customer Intelligence\n"]
    if opp:
        lines.append(f"- **Agency:** {opp.get('agency', 'N/A')}")
        sub = opp.get("sub_agency")
        if sub:
            lines.append(f"- **Sub-Agency:** {sub}")
        lines.append(f"- **Capture Manager:** {opp.get('capture_manager', 'N/A')}")
        lines.append(f"- **Proposal Manager:** {opp.get('proposal_manager', 'N/A')}")
        lines.append(f"- **Domain:** {opp.get('domain', 'general')}")
        lines.append(f"- **Set-Aside:** {opp.get('set_aside_type', 'N/A')}")
        lines.append(f"- **NAICS:** {opp.get('naics_code', 'N/A')}")
    else:
        lines.append("*No opportunity data available.*")
    lines.append("")
    return "\n".join(lines)


def _render_win_strategy(themes: List[Dict]) -> str:
    lines = ["## Win Strategy\n"]
    if not themes:
        lines.append("*No win themes or discriminators recorded.*\n")
        return "\n".join(lines)
    wt = [t for t in themes if t.get("theme_type") == "win_theme"]
    disc = [t for t in themes if t.get("theme_type") == "discriminator"]
    ghosts = [t for t in themes if t.get("theme_type") == "ghost_strategy"]
    if wt:
        lines.append("### Win Themes\n")
        for i, t in enumerate(wt, 1):
            lines.append(f"{i}. **{t['theme_statement']}**")
            if t.get("supporting_evidence"):
                lines.append(f"   - Evidence: {t['supporting_evidence']}")
            if t.get("target_eval_factor"):
                lines.append(f"   - Targets: {t['target_eval_factor']}")
    if disc:
        lines.append("\n### Discriminators\n")
        for d in disc:
            lines.append(f"- {d['theme_statement']}")
    if ghosts:
        lines.append("\n### Ghost Competitor Strategies\n")
        for g in ghosts:
            comp = g.get("ghost_competitor", "Unknown")
            lines.append(f"- **vs {comp}:** {g['theme_statement']}")
    lines.append("")
    return "\n".join(lines)


def _render_technical_approach(drafts: List[Dict]) -> str:
    lines = ["## Technical Approach Summary\n"]
    if not drafts:
        lines.append("*No approved/reviewed technical drafts available.*\n")
        return "\n".join(lines)
    for d in drafts[:5]:  # Limit to top 5 drafts
        content = (d.get("draft_content") or "")[:500]
        if content:
            lines.append(f"- {content.strip()[:200]}...")
    lines.append("")
    return "\n".join(lines)


def _render_key_personnel(names: List[str]) -> str:
    lines = ["## Key Personnel & Staffing Plan\n"]
    if not names:
        lines.append("*No key personnel identified in proposal drafts.*\n")
        return "\n".join(lines)
    lines.append("| # | Name |")
    lines.append("|---|------|")
    for i, name in enumerate(names[:20], 1):
        lines.append(f"| {i} | {name} |")
    lines.append("")
    return "\n".join(lines)


def _render_teaming(teaming: List[Dict]) -> str:
    lines = ["## Teaming Arrangements & Workshare\n"]
    if not teaming:
        lines.append("*No teaming arrangements recorded.*\n")
        return "\n".join(lines)
    lines.append("| Partner | Workshare % | Status | OCI Risk |")
    lines.append("|---------|------------|--------|----------|")
    for t in teaming:
        name = t.get("partner_name") or t.get("name", "Unknown")
        pct = t.get("workshare_pct", "N/A")
        status = t.get("status") or t.get("ta_status", "N/A")
        oci = t.get("oci_risk", "N/A")
        lines.append(f"| {name} | {pct} | {status} | {oci} |")
    lines.append("")
    return "\n".join(lines)


def _render_pricing(pricing_data: Dict[str, Any]) -> str:
    lines = ["## Pricing Summary & BOE\n"]
    volumes = pricing_data.get("volumes", [])
    lcats = pricing_data.get("lcats", [])
    if not volumes:
        lines.append("*No cost volume data available.*\n")
        return "\n".join(lines)
    for vol in volumes:
        lines.append(f"### {vol.get('contract_type', 'N/A').upper()} Contract\n")
        lines.append(f"- **Pricing Strategy:** {vol.get('pricing_strategy', 'N/A')}")
        tep = vol.get("total_evaluated_price")
        if tep:
            lines.append(f"- **Total Evaluated Price:** ${tep:,.2f}")
        dlc = vol.get("direct_labor_cost")
        if dlc:
            lines.append(f"- **Direct Labor Cost:** ${dlc:,.2f}")
        for rate_name in ("fringe_rate", "overhead_rate", "g_and_a_rate", "fee_rate"):
            val = vol.get(rate_name)
            if val is not None:
                label = rate_name.replace("_", " ").title()
                lines.append(f"- **{label}:** {val:.2%}")
        sc = vol.get("subcontractor_cost")
        if sc:
            lines.append(f"- **Subcontractor Cost:** ${sc:,.2f}")
        odc = vol.get("odc_cost")
        if odc:
            lines.append(f"- **ODC Cost:** ${odc:,.2f}")
        pop = vol.get("period_of_performance")
        if pop:
            lines.append(f"- **Period of Performance:** {pop}")
        lines.append(f"- **Status:** {vol.get('status', 'draft')}")
    if lcats:
        lines.append("\n### Labor Category Allocations\n")
        lines.append("| Category | SOC Code | FTE | Hourly Rate | Annual Cost |")
        lines.append("|----------|----------|-----|-------------|-------------|")
        for lc in lcats:
            cat = lc.get("labor_category", "N/A")
            soc = lc.get("bls_soc_code", "N/A")
            fte = lc.get("fte_count", "N/A")
            rate = lc.get("hourly_rate")
            rate_str = f"${rate:,.2f}" if rate else "N/A"
            annual = lc.get("annual_cost")
            annual_str = f"${annual:,.2f}" if annual else "N/A"
            lines.append(f"| {cat} | {soc} | {fte} | {rate_str} | {annual_str} |")
    lines.append("")
    return "\n".join(lines)


def _render_compliance(cmmc: List[Dict]) -> str:
    lines = ["## Compliance Commitments\n"]
    if not cmmc:
        lines.append("*No CMMC compliance data recorded.*\n")
        return "\n".join(lines)
    lines.append("### CMMC Supply Chain Status\n")
    lines.append("| Team Member | Role | Required Level | Actual Level | SPRS | Status |")
    lines.append("|-------------|------|---------------|--------------|------|--------|")
    for m in cmmc:
        lines.append(
            f"| {m.get('team_member_name', 'N/A')} "
            f"| {m.get('role', 'N/A')} "
            f"| L{m.get('required_cmmc_level', '?')} "
            f"| L{m.get('actual_cmmc_level', '?')} "
            f"| {m.get('sprs_score', 'N/A')} "
            f"| {m.get('compliance_status', 'unknown')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_cdrls(cdrls: List[Dict]) -> str:
    lines = ["## CDRL Delivery Schedule\n"]
    if not cdrls:
        lines.append("*No CDRL requirements identified in compliance matrix.*\n")
        return "\n".join(lines)
    lines.append("| Req ID | Requirement | Volume | Section | Status |")
    lines.append("|--------|-------------|--------|---------|--------|")
    for c in cdrls:
        lines.append(
            f"| {c.get('requirement_id', 'N/A')} "
            f"| {(c.get('requirement_text', '') or '')[:60]} "
            f"| {c.get('assigned_volume', 'N/A')} "
            f"| {c.get('assigned_section', 'N/A')} "
            f"| {c.get('compliance_status', 'gap')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_risks(risks: List[Dict]) -> str:
    lines = ["## Risk Register\n"]
    if not risks:
        lines.append("*No critical/major review findings recorded.*\n")
        return "\n".join(lines)
    lines.append("| Severity | Category | Finding | Status |")
    lines.append("|----------|----------|---------|--------|")
    for r in risks:
        text = (r.get("finding_text") or "")[:80]
        lines.append(
            f"| {r.get('severity', 'N/A')} "
            f"| {r.get('category', 'N/A')} "
            f"| {text} "
            f"| {r.get('resolution_status', 'open')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_open_issues(opp: Optional[Dict], risks: List[Dict]) -> str:
    lines = ["## Open Issues & Recommendations\n"]
    open_risks = [r for r in risks if r.get("resolution_status") == "open"]
    if open_risks:
        lines.append(f"- **{len(open_risks)} open review finding(s)** require resolution before kickoff.")
    if opp and opp.get("amendment_count", 0) > 0:
        lines.append(
            f"- **{opp['amendment_count']} amendment(s)** processed -- verify all changes are reflected in final proposal."
        )
    if opp and opp.get("question_count", 0) > 0:
        lines.append(f"- **{opp['question_count']} Q&A item(s)** submitted -- confirm agency responses incorporated.")
    if not open_risks and (not opp or opp.get("amendment_count", 0) == 0):
        lines.append("*No open issues identified.*")
    lines.append("\n### Recommendations\n")
    lines.append("1. Schedule kickoff meeting within 10 business days of award notification.")
    lines.append("2. Verify all key personnel are available per proposed staffing plan.")
    lines.append("3. Initiate CMMC compliance verification for all team members.")
    lines.append("4. Stand up CDRL tracking in the post-award management portal.")
    lines.append("5. Conduct program-level risk review using findings from this bridge document.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
def get_data_coverage(opportunity_id: str) -> Dict[str, Any]:
    """Check what data is available for bridge generation.

    Returns per-section data availability and overall coverage percentage.
    """
    conn = _get_db()
    _ensure_tables(conn)

    section_map = [
        ("Executive Summary", "proposal_opportunities", _gather_opportunity),
        ("Customer Intelligence", "proposal_opportunities", _gather_opportunity),
        ("Win Strategy", "pg_win_themes", _gather_win_themes),
        ("Technical Approach Summary", "proposal_section_drafts", _gather_drafts),
        ("Key Personnel & Staffing Plan", "proposal_section_drafts", _gather_key_personnel),
        ("Teaming Arrangements & Workshare", "pg_teaming_workshare", _gather_teaming),
        ("Pricing Summary & BOE", "pg_cost_volumes", _gather_pricing),
        ("Compliance Commitments", "pg_cmmc_supply_chain", _gather_cmmc),
        ("CDRL Delivery Schedule", "pg_compliance_matrix", _gather_cdrls),
        ("Risk Register", "pg_review_findings", _gather_risks),
        ("Open Issues & Recommendations", "pg_review_findings", _gather_risks),
    ]

    sections: list[dict] = []
    populated = 0
    for name, source_table, gatherer in section_map:
        result = gatherer(conn, opportunity_id)
        count = result.get("record_count", 0)
        available = count > 0
        if available:
            populated += 1
        sections.append(
            {
                "name": name,
                "data_available": available,
                "source_table": source_table,
                "record_count": count,
            }
        )

    conn.close()
    overall = (populated / len(section_map) * 100) if section_map else 0.0
    return {
        "opportunity_id": opportunity_id,
        "sections": sections,
        "sections_populated": populated,
        "total_sections": len(section_map),
        "overall_coverage_pct": round(overall, 1),
    }


def generate_bridge(opportunity_id: str) -> Dict[str, Any]:
    """Generate comprehensive transition package from proposal data.

    Returns bridge metadata with markdown content.
    """
    conn = _get_db()
    _ensure_tables(conn)

    # Gather all data
    opp_result = _gather_opportunity(conn, opportunity_id)
    opp = opp_result["data"]

    themes_result = _gather_win_themes(conn, opportunity_id)
    themes = themes_result["data"]

    personnel_result = _gather_key_personnel(conn, opportunity_id)
    personnel = personnel_result["data"]

    teaming_result = _gather_teaming(conn, opportunity_id)
    teaming = teaming_result["data"]

    pricing_result = _gather_pricing(conn, opportunity_id)
    pricing = pricing_result["data"]

    risks_result = _gather_risks(conn, opportunity_id)
    risks = risks_result["data"]

    cdrls_result = _gather_cdrls(conn, opportunity_id)
    cdrls = cdrls_result["data"]

    cmmc_result = _gather_cmmc(conn, opportunity_id)
    cmmc = cmmc_result["data"]

    bid_result = _gather_bid_decisions(conn, opportunity_id)
    bid = bid_result["data"]

    drafts_result = _gather_drafts(conn, opportunity_id)
    drafts = drafts_result["data"]

    # Count populated sections
    data_counts = [
        opp_result["record_count"],  # Executive Summary
        opp_result["record_count"],  # Customer Intelligence
        themes_result["record_count"],  # Win Strategy
        drafts_result["record_count"],  # Technical Approach
        personnel_result["record_count"],  # Key Personnel
        teaming_result["record_count"],  # Teaming
        pricing_result["record_count"],  # Pricing
        cmmc_result["record_count"],  # Compliance
        cdrls_result["record_count"],  # CDRLs
        risks_result["record_count"],  # Risks
        risks_result["record_count"],  # Open Issues
    ]
    sections_populated = sum(1 for c in data_counts if c > 0)
    total_sections = len(BRIDGE_SECTIONS)
    coverage_pct = round(sections_populated / total_sections * 100, 1)

    # Render markdown
    title = opp.get("title", opportunity_id) if opp else opportunity_id
    header = f"# Proposal-to-Program Transition Bridge\n\n**Opportunity:** {title}\n**Generated:** {_now()}\n\n---\n\n"

    body_parts = [
        _render_executive_summary(opp, bid),
        _render_customer_intel(opp),
        _render_win_strategy(themes),
        _render_technical_approach(drafts),
        _render_key_personnel(personnel),
        _render_teaming(teaming),
        _render_pricing(pricing),
        _render_compliance(cmmc),
        _render_cdrls(cdrls),
        _render_risks(risks),
        _render_open_issues(opp, risks),
    ]

    footer = "\n---\n\n*CUI // SP-CTI — This document contains Controlled Unclassified Information.*\n"

    markdown = header + "\n".join(body_parts) + footer

    # Get next version
    existing = conn.execute(
        "SELECT MAX(version) FROM pg_program_bridges WHERE opportunity_id = %s",
        (opportunity_id,),
    ).fetchone()
    version = (existing[0] or 0) + 1 if existing and existing[0] else 1

    # Save to file
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    file_path = BRIDGE_DIR / f"{opportunity_id}-bridge-v{version}.md"
    file_path.write_text(markdown, encoding="utf-8", newline="")

    # Store metadata
    bridge_id = _gen_id()
    conn.execute(
        "INSERT INTO pg_program_bridges (id, opportunity_id, version, sections_populated, "
        "total_sections, data_coverage_pct, file_path, status, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            bridge_id,
            opportunity_id,
            version,
            sections_populated,
            total_sections,
            coverage_pct,
            str(file_path),
            "generated",
            _now(),
        ),
    )

    _audit(
        conn,
        "bridge_generated",
        json.dumps(
            {
                "bridge_id": bridge_id,
                "version": version,
                "sections_populated": sections_populated,
                "coverage_pct": coverage_pct,
            }
        ),
        opportunity_id,
    )

    conn.commit()
    conn.close()

    return {
        "bridge_id": bridge_id,
        "opportunity_id": opportunity_id,
        "version": version,
        "markdown": markdown,
        "sections_populated": sections_populated,
        "total_sections": total_sections,
        "data_coverage_pct": coverage_pct,
        "file_path": str(file_path),
    }


def list_bridges(limit: int = 20) -> Dict[str, Any]:
    """List all generated bridges with status."""
    conn = _get_db()
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM pg_program_bridges ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM pg_program_bridges LIMIT 0").description]
    bridges = [dict(zip(cols, r)) for r in rows]
    conn.close()
    return {"bridges": bridges, "count": len(bridges)}


def get_bridge(bridge_id: str) -> Dict[str, Any]:
    """Retrieve a specific bridge document."""
    conn = _get_db()
    _ensure_tables(conn)
    row = conn.execute("SELECT * FROM pg_program_bridges WHERE id = %s", (bridge_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": "Bridge not found", "bridge_id": bridge_id}
    cols = [d[0] for d in conn.execute("SELECT * FROM pg_program_bridges LIMIT 0").description]
    bridge = dict(zip(cols, row))
    # Read markdown content from file
    fp = bridge.get("file_path")
    markdown = ""
    if fp:
        p = Path(fp)
        if p.exists():
            markdown = p.read_text(encoding="utf-8")
    bridge["markdown"] = markdown
    conn.close()
    return bridge


def refresh_bridge(opportunity_id: str) -> Dict[str, Any]:
    """Regenerate bridge with latest data (creates new version)."""
    return generate_bridge(opportunity_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Proposal-to-Program Knowledge Bridge — generate transition packages",
    )
    parser.add_argument("--opportunity-id", help="Opportunity ID")
    parser.add_argument("--generate", action="store_true", help="Generate bridge document")
    parser.add_argument("--list", action="store_true", help="List all bridges")
    parser.add_argument("--get", action="store_true", help="Retrieve a specific bridge")
    parser.add_argument("--bridge-id", help="Bridge ID for --get")
    parser.add_argument("--refresh", action="store_true", help="Refresh bridge with latest data")
    parser.add_argument("--coverage", action="store_true", help="Check data coverage")
    parser.add_argument("--limit", type=int, default=20, help="Limit for --list")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result: Dict[str, Any] = {"error": "No action specified"}

    if args.generate:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --generate"}
        else:
            result = generate_bridge(args.opportunity_id)
    elif args.list:
        result = list_bridges(limit=args.limit)
    elif args.get:
        if not args.bridge_id:
            result = {"error": "--bridge-id required for --get"}
        else:
            result = get_bridge(args.bridge_id)
    elif args.refresh:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --refresh"}
        else:
            result = refresh_bridge(args.opportunity_id)
    elif args.coverage:
        if not args.opportunity_id:
            result = {"error": "--opportunity-id required for --coverage"}
        else:
            result = get_data_coverage(args.opportunity_id)

    if args.json:
        # Strip markdown from JSON output to keep it concise
        if "markdown" in result and args.generate:
            result["markdown_length"] = len(result.get("markdown", ""))
            result.pop("markdown", None)
        print(json.dumps(result, indent=2, default=str))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
        elif "bridges" in result:
            print(f"Bridges: {result['count']}")
            for b in result["bridges"]:
                print(
                    f"  {b['id']}  opp={b['opportunity_id']}  v{b['version']}  "
                    f"coverage={b['data_coverage_pct']}%  {b['status']}  {b['created_at']}"
                )
        elif "sections" in result and "overall_coverage_pct" in result:
            print(f"Data Coverage for {result['opportunity_id']}: {result['overall_coverage_pct']}%")
            for s in result["sections"]:
                mark = "Y" if s["data_available"] else "-"
                print(f"  [{mark}] {s['name']} ({s['source_table']}: {s['record_count']} records)")
        elif "bridge_id" in result:
            print(f"Bridge generated: {result['bridge_id']}")
            print(f"  Version: {result.get('version', 1)}")
            print(f"  Sections: {result.get('sections_populated', 0)}/{result.get('total_sections', 11)}")
            print(f"  Coverage: {result.get('data_coverage_pct', 0)}%")
            print(f"  File: {result.get('file_path', 'N/A')}")
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
# CUI // SP-CTI
