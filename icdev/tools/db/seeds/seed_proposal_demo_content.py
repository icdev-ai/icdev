"""Seed two demo proposals with fully populated section tabs.

Populates Notes, Compliance, Findings, Dependencies, and History tabs
for two existing proposals so demos show rich, realistic content.

Idempotent: deletes rows tagged created_by='demo_seed' before re-inserting.

Usage:
  python tools/db/seeds/seed_proposal_demo_content.py [--json] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ─── Target proposals ────────────────────────────────────────────────────────
CONTRACTS = [
    {
        "opp_id": "fb1e143b-196f-4dfa-5cd8-fe1adafec8a9",
        "title": "DevSecOps Platform Implementation and Engineering Support",
        "agency": "Department of Defense",
        "domain": "devsecops",
        "sections": [
            {
                "id": "ca3c6f5f-5bb5-c40c-03cd-e2e321bddb41",
                "vol": "I",
                "label": "Technical Approach",
                "writer": "M. Nguyen",
                "reviewer": "T. Williams",
            },
            {
                "id": "20733f6d-0d6a-05b3-43ef-20162c9b662e",
                "vol": "II",
                "label": "Management Approach",
                "writer": "S. Chuon",
                "reviewer": "A. Patel",
            },
            {
                "id": "5cae9610-c72c-1fe3-72c2-2a1679eb4168",
                "vol": "III",
                "label": "Past Performance",
                "writer": "J. Kim",
                "reviewer": "M. Nguyen",
            },
        ],
    },
    {
        "opp_id": "fbf24050-a748-dbcf-ac61-9e630dde29a6",
        "title": "Cloud Migration and Modernization Support Services",
        "agency": "Department of Homeland Security",
        "domain": "cloud",
        "sections": [
            {
                "id": "bb026576-f512-c4c3-b253-d2186c4a37ea",
                "vol": "I",
                "label": "Technical Approach",
                "writer": "A. Patel",
                "reviewer": "S. Chuon",
            },
            {
                "id": "37bb3eec-4bf5-0b52-309d-258c27a0c3d7",
                "vol": "II",
                "label": "Management Approach",
                "writer": "T. Williams",
                "reviewer": "J. Kim",
            },
            {
                "id": "0cd620c2-0ea2-622b-5048-67babf7b539b",
                "vol": "III",
                "label": "Past Performance",
                "writer": "M. Nguyen",
                "reviewer": "A. Patel",
            },
        ],
    },
]

# ─── Notes content ────────────────────────────────────────────────────────────
NOTES = {
    "I": {
        "devsecops": (
            "Gold team flagged three areas requiring immediate attention before final submission:\n\n"
            "1. DEVSECOPS PIPELINE SPECIFICITY — Section L.3.1 response needs concrete pipeline toolchain "
            "callouts (GitLab CI/CD → Aqua Security → Anchore → SonarQube chain). Generic statements about "
            '"shift-left" are PWS L.3.1 non-compliant. Rewrite paragraphs 3–5 by EOD Thursday.\n\n'
            "2. IL5 BOUNDARY DIAGRAM — Evaluators will look for explicit IL5 boundary diagram with "
            "data-at-rest encryption callouts. Currently missing. M. Nguyen to produce by Wednesday.\n\n"
            "3. PAGE COUNT — Vol I is currently 42 pages against a 40-page limit. Cut the introductory "
            "capability overview on pp. 4–5 (evaluators don't read boilerplate). Priority: HIGH."
        ),
        "cloud": (
            "Pink team feedback addressed; gold team review scheduled for 2026-06-10.\n\n"
            "OPEN ITEMS:\n"
            "• Migration wave sequencing diagram needs to match the SOW milestones exactly — "
            "currently waves 3 & 4 are transposed vs. PWS Exhibit A.\n"
            "• CSP rate justification table is missing. DHS evaluators will score this as M.3.2 non-compliant.\n"
            "• Win theme #2 ('zero-downtime cutover') is unsupported — add evidence from DHS OIT-2024 reference.\n\n"
            "STRENGTHS TO PRESERVE:\n"
            "CloudGuard segmentation approach is a clear discriminator. Keep the graphic on p. 18."
        ),
    },
    "II": {
        "devsecops": (
            "Management approach review notes — internal working copy.\n\n"
            "STAFFING PLAN: PM + 3 Tech Leads + 12 FTEs confirmed. Teaming partner (Apex Systems) "
            "contributes 4 cleared FTEs for IL5 environment access. Confirm teaming cert by 2026-06-06.\n\n"
            "TRANSITION PLAN: 90-day transition from incumbent (Leidos) is tight. Gold team asked us to "
            "address incumbent cooperation risk explicitly. Draft language: 'Offeror has established "
            "coordination protocols with the incumbent contractor per FAR 52.242-15.'\n\n"
            "ORALS PREP: COR wants to see org chart during technical evaluation. Updated version with "
            "cleared personnel highlighted is at SharePoint: /proposals/devsecops/mgmt/orgchart-v3.pptx"
        ),
        "cloud": (
            "Management volume is on track. Key decisions logged here:\n\n"
            "KEY PERSON RISK: Sr. Cloud Architect (C. Park) is supporting two concurrent proposals. "
            "Capture manager has approved — document as 'part-time commitment, full-time at award' "
            "per agency precedent on similar IDIQ.\n\n"
            "SUBCONTRACTING PLAN: Small business goal 40%. Current plan: 42.3% SB utilization via "
            "TechVets LLC (SDVOSB) and CloudPath Inc. (HUBZone). Coordinate with contracts team for "
            "SB subcontracting plan attachment.\n\n"
            "RISK REGISTER: Three medium risks documented in Section M.4. Gold team wants a fourth "
            "entry covering supply chain risk per DFARS 252.239-7084."
        ),
    },
    "III": {
        "devsecops": (
            "Past performance section — volume lead notes.\n\n"
            "REFERENCES CONFIRMED (3 of 3):\n"
            "• DISA SIEM/SOC (2022–2025) — POC: Col. R. Foster, DSN 312-555-0142. Cleared to reference.\n"
            "• Army DevSecOps Factory, Fort Meade (2023–present) — POC: Ms. D. Chen, 301-555-0198. "
            "Confirm CPARs score before submission (last rating: Exceptional).\n"
            "• USAF BESPIN Squadron, Keesler AFB (2021–2023) — POC retired. Use contracting officer "
            "Jennifer Hall (jennifer.hall@us.af.mil) as alternate. *** Must verify still active email.\n\n"
            "RELEVANCE MAPPING: Gold team highlighted that Volume III currently doesn't explicitly map "
            "each reference to Section L.5 criteria. Add relevance matrix table (Reference × Criterion)."
        ),
        "cloud": (
            "Past performance references — three confirmed, all within 5-year recency window:\n\n"
            "REF 1: DHS CISA Cloud Enablement (2023–present) — $28M IDIQ. POC: Mr. K. Okonkwo "
            "(202-555-0177). CPARS Exceptional. STRONG relevance to PWS Sections 3 and 5.\n\n"
            "REF 2: GSA IT Modernization BPA, Task Order 7 (2022–2024) — $14.2M. POC: Ms. L. Torres. "
            "CPARS Very Good. Note: RFP says 'similar in scope and complexity' — make the size comparison "
            "explicit (our TOs were 80% of this RFP's value).\n\n"
            "REF 3: Treasury FISMA Cloud Migration (2021–2023) — $9.8M. POC: Mr. D. Singh. "
            "CPARS Exceptional. *** Confirm contract number before submission — GS-35F-0119T vs 0119P.\n\n"
            "ACTION: Relevance narrative for Ref 3 needs rewrite (currently copy-pasted from Ref 1)."
        ),
    },
}

# ─── Compliance matrix ────────────────────────────────────────────────────────
COMPLIANCE_ITEMS = {
    "devsecops": {
        "I": [
            ("L.3.1", "Offeror shall describe its DevSecOps platform architecture including CI/CD pipeline, "
             "container orchestration, and security scanning integration.", "L", "compliant",
             "Addressed in Section 3.1 with GitLab CI/CD + Kubernetes + Aqua Security toolchain diagram."),
            ("L.3.2", "Offeror shall provide a phased implementation plan with milestones and deliverables "
             "aligned to the PWS performance work statement.", "L", "partial",
             "Milestones present but Phases 3-4 lack explicit deliverable dates. Needs update."),
            ("L.3.3", "Offeror shall address IL5 authorization boundary and data-at-rest encryption approach "
             "with supporting architecture diagram.", "L", "non_compliant",
             "IL5 boundary diagram is missing. Critical gap — must be added before submission."),
            ("M.2.1", "Technical approach demonstrates understanding of the problem and provides an innovative "
             "solution that exceeds minimum requirements.", "M", "compliant",
             "Win theme: 'Factory Model' approach scored highest in pink team technical scoring."),
            ("M.2.2", "Proposed solution integrates security at every phase of the software development "
             "lifecycle (SDLC) with automated gate enforcement.", "M", "partial",
             "SDLC integration described; automated gate enforcement evidence is missing."),
            ("M.2.3", "Offeror demonstrates experience operating at Impact Level 5 environments.", "M", "compliant",
             "DISA and Army references both confirm IL5 operational experience."),
        ],
        "II": [
            ("L.4.1", "Offeror shall provide a staffing plan with named key personnel and their qualifications.", "L", "compliant",
             "PM, Tech Lead, and Security Architect named with resumes in Volume IV."),
            ("L.4.2", "Offeror shall describe transition plan from incumbent contractor including knowledge "
             "transfer and continuity of operations.", "L", "partial",
             "90-day transition plan included; incumbent cooperation risk not addressed."),
            ("L.4.3", "Small business subcontracting plan shall meet or exceed 40% SB utilization goal.", "L", "compliant",
             "42.3% SB utilization via TechVets LLC and CloudPath Inc."),
            ("M.3.1", "Management approach demonstrates a clear organizational structure with defined roles.", "M", "compliant",
             "Org chart provided; IL5-cleared personnel highlighted."),
            ("M.3.2", "Offeror demonstrates ability to manage program risk through documented risk "
             "register and mitigation strategies.", "M", "partial",
             "Three risks documented; supply chain risk entry missing per DFARS 252.239-7084."),
        ],
        "III": [
            ("L.5.1", "Offeror shall provide at least three relevant past performance references within "
             "the past five years.", "L", "compliant",
             "Three references provided: DISA, Army DevSecOps Factory, USAF BESPIN."),
            ("L.5.2", "Each reference shall include contracting officer contact information "
             "and CPARS ratings.", "L", "partial",
             "USAF BESPIN POC is retired — alternate CO contact needs verification."),
            ("L.5.3", "Past performance references shall be similar in scope, complexity, and dollar value "
             "to this requirement.", "L", "compliant",
             "All three references demonstrate IL5/DevSecOps at comparable scale ($15M–$45M)."),
            ("M.4.1", "Past performance demonstrates consistent performance excellence as rated by "
             "government CORs.", "M", "compliant",
             "Two Exceptional and one Very Good CPARS rating across three references."),
            ("M.4.2", "Relevance of past performance is explicitly mapped to PWS performance requirements.", "M", "non_compliant",
             "Relevance matrix table not included. Must add cross-reference table per evaluator guidance."),
        ],
    },
    "cloud": {
        "I": [
            ("L.3.1", "Offeror shall describe cloud migration methodology including assessment, planning, "
             "migration, and optimization phases.", "L", "compliant",
             "7-phase migration methodology described with Cloud Adoption Framework alignment."),
            ("L.3.2", "Migration wave sequencing shall align with PWS Exhibit A milestones and system "
             "dependencies.", "L", "non_compliant",
             "Waves 3 & 4 are transposed vs. PWS Exhibit A. Must correct before submission."),
            ("L.3.3", "Offeror shall provide CSP cost model with rate justification and TCO comparison.", "L", "non_compliant",
             "CSP rate justification table missing. M.3.2 non-compliance risk — critical gap."),
            ("M.2.1", "Technical solution demonstrates deep understanding of agency's current-state "
             "on-premises environment.", "M", "compliant",
             "Current-state assessment methodology leverages agency's own CMDB data for accuracy."),
            ("M.2.2", "Proposed cloud architecture provides zero-downtime migration capability with "
             "documented rollback procedures.", "M", "partial",
             "Zero-downtime approach described; rollback procedures lack specific RTO/RPO targets."),
            ("M.2.3", "Offeror demonstrates FedRAMP High authorization experience for proposed CSP.", "M", "compliant",
             "AWS GovCloud FedRAMP High ATO reference provided; team includes 3 certified AWS architects."),
        ],
        "II": [
            ("L.4.1", "Offeror shall provide key personnel qualifications including cloud architect, "
             "PM, and security lead.", "L", "partial",
             "Cloud Architect committed part-time during proposal phase — must document full-time at award."),
            ("L.4.2", "Staffing plan shall demonstrate ability to maintain continuity during peak "
             "migration periods.", "L", "compliant",
             "Surge capacity plan with subcontractor augmentation documented."),
            ("L.4.3", "Small business subcontracting plan shall achieve 40% SB goal.", "L", "compliant",
             "42.3% SB utilization confirmed via TechVets and CloudPath."),
            ("M.3.1", "Management plan demonstrates phased delivery approach with clear milestones and "
             "acceptance criteria.", "M", "compliant",
             "Quarterly delivery milestones with measurable acceptance criteria per CDRLs."),
            ("M.3.2", "Supply chain risk management approach addresses third-party components in the "
             "cloud solution stack.", "M", "non_compliant",
             "SCRM section missing. DFARS 252.239-7084 requires explicit supply chain risk entry."),
        ],
        "III": [
            ("L.5.1", "Offeror shall provide three past performance references with similar cloud "
             "migration scope.", "L", "compliant",
             "Three references: DHS CISA ($28M), GSA BPA TO-7 ($14.2M), Treasury FISMA ($9.8M)."),
            ("L.5.2", "Each reference shall include COR/COTR contact information and performance ratings.", "L", "partial",
             "Treasury contract number inconsistency (GS-35F-0119T vs 0119P) — must resolve before submission."),
            ("L.5.3", "Demonstrate relevance: scope, complexity, and dollar value comparable to this requirement.", "L", "partial",
             "Ref 3 relevance narrative is copied from Ref 1. Unique narrative required."),
            ("M.4.1", "Past performance reflects consistent Exceptional or Very Good CPARS ratings.", "M", "compliant",
             "Two Exceptional + one Very Good ratings across all three references."),
            ("M.4.2", "Relevance of past performance explicitly mapped to PWS task areas.", "M", "compliant",
             "Relevance matrix table cross-references each reference to PWS Sections 3, 5, and 7."),
        ],
    },
}

# ─── Findings ────────────────────────────────────────────────────────────────
FINDINGS_BY_VOL = {
    "II": [
        ("content_weakness", "major", "open",
         "Transition plan does not address incumbent knowledge transfer risk.",
         "Add explicit paragraph citing FAR 52.242-15 and describe protocol for accessing incumbent documentation."),
        ("compliance_gap", "critical", "in_progress",
         "Supply chain risk management entry missing from risk register.",
         "Add SCRM entry referencing DFARS 252.239-7084. Assign to contracts team by EOD Wednesday."),
        ("competitive_risk", "minor", "resolved",
         "Org chart does not distinguish cleared vs. uncleared personnel.",
         "Updated org chart v3 highlights TS/SCI-cleared roles in blue. Resolved 2026-06-01."),
        ("formatting", "minor", "resolved",
         "Page headers do not match agency style guide format (missing solicitation number).",
         "Headers corrected in all sections. Solicitation number added. Resolved 2026-05-30."),
        ("content_weakness", "major", "in_progress",
         "Small business narrative does not name specific SDVOSB/HUBZone firms.",
         "Name TechVets LLC and CloudPath Inc. explicitly with DUNS numbers."),
        ("pricing_concern", "minor", "open",
         "Labor category mapping for subcontractors does not match prime's rate card.",
         "Reconcile subcontractor rates against prime rate card before cost volume submission."),
    ],
    "III": [
        ("compliance_gap", "critical", "in_progress",
         "Past performance relevance matrix not included — required per Section L.5.3.",
         "Add cross-reference table mapping each reference to PWS performance requirements."),
        ("content_weakness", "major", "open",
         "USAF BESPIN reference uses retired POC — evaluator may be unable to verify performance.",
         "Substitute retired POC with contracting officer Jennifer Hall. Verify email is active."),
        ("content_weakness", "major", "in_progress",
         "Reference 3 relevance narrative is copy-pasted from Reference 1.",
         "Rewrite Reference 3 narrative focusing on FISMA compliance and Treasury-specific outcomes."),
        ("competitive_risk", "minor", "resolved",
         "Contract values are listed in ranges rather than actuals — may appear evasive.",
         "Replaced ranges with actual contract values where permitted by classification. Resolved 2026-05-29."),
        ("formatting", "minor", "resolved",
         "Date format inconsistent — some references use MM/DD/YYYY, others use YYYY-MM-DD.",
         "Standardized to MM/DD/YYYY across all references. Resolved 2026-05-28."),
    ],
}

# ─── Status history progression ─────────────────────────────────────────────
STATUS_PROGRESSION = [
    ("not_started", "outlining", -45, "Kickoff meeting completed; outline assigned to volume leads."),
    ("outlining", "drafting", -35, "Outline approved by capture manager. Writers assigned and notified."),
    ("drafting", "internal_review", -21, "First complete draft submitted for internal review."),
    ("internal_review", "pink_team_review", -14, "Internal review complete. Elevated to pink team gate."),
    ("pink_team_review", "rework", -10, "Pink team identified 4 major findings. Rework required."),
    ("rework", "red_team_review", -6, "Rework complete. Elevated to red team review."),
    ("red_team_review", "gold_team_review", -2, "Red team cleared. No blocking issues. Elevated to gold team."),
]

REVIEWERS = ["T. Williams", "A. Patel", "J. Kim", "M. Nguyen", "S. Chuon", "L. Torres"]


def _uid() -> str:
    return str(uuid.uuid4())


def _ts(days_offset: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days_offset)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


_ALL_SECTION_IDS = [
    sec["id"]
    for contract in CONTRACTS
    for sec in contract["sections"]
]
_ALL_OPP_IDS = [c["opp_id"] for c in CONTRACTS]


def _delete_demo_data(conn) -> dict:
    counts = {}

    # compliance — delete by section_id membership
    placeholders = ",".join(["%s"] * len(_ALL_SECTION_IDS))
    result = conn.execute(
        f"DELETE FROM proposal_compliance_matrix WHERE proposal_section_id IN ({placeholders})",
        _ALL_SECTION_IDS,
    )
    counts["compliance"] = result.rowcount if hasattr(result, "rowcount") else 0

    # dependencies — delete by section_id membership
    result = conn.execute(
        f"DELETE FROM proposal_section_dependencies WHERE section_id IN ({placeholders})",
        _ALL_SECTION_IDS,
    )
    counts["dependencies"] = result.rowcount if hasattr(result, "rowcount") else 0

    # history — changed_by column exists
    result = conn.execute(
        "DELETE FROM proposal_status_history WHERE changed_by = 'demo_seed'",
    )
    counts["history"] = result.rowcount if hasattr(result, "rowcount") else 0

    # reviews + findings — use lead_reviewer as demo tag
    opp_ph = ",".join(["%s"] * len(_ALL_OPP_IDS))
    result = conn.execute(
        f"""DELETE FROM proposal_review_findings
            WHERE review_id IN (
                SELECT id FROM proposal_reviews
                WHERE opportunity_id IN ({opp_ph}) AND lead_reviewer = 'demo_seed'
            )""",
        _ALL_OPP_IDS,
    )
    counts["findings"] = result.rowcount if hasattr(result, "rowcount") else 0

    result = conn.execute(
        f"DELETE FROM proposal_reviews WHERE opportunity_id IN ({opp_ph}) AND lead_reviewer = 'demo_seed'",
        _ALL_OPP_IDS,
    )
    counts["reviews"] = result.rowcount if hasattr(result, "rowcount") else 0

    return counts


def _seed_notes(conn, sec_id: str, vol: str, domain: str) -> None:
    notes_text = NOTES.get(vol, {}).get(domain, "")
    if notes_text:
        conn.execute(
            "UPDATE proposal_sections SET notes = %s WHERE id = %s",
            (notes_text, sec_id),
        )


def _seed_compliance(conn, opp_id: str, sec_id: str, vol: str, domain: str) -> int:
    items = COMPLIANCE_ITEMS.get(domain, {}).get(vol, [])
    for i, (ref, req_text, req_type, status, response) in enumerate(items):
        conn.execute(
            """INSERT INTO proposal_compliance_matrix (
                id, opportunity_id, section_ref, requirement_text, requirement_type,
                compliance_status, proposal_section_id, response_summary,
                sort_order, classification, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'CUI',%s,%s)""",
            (_uid(), opp_id, ref, req_text, req_type, status, sec_id,
             response, i + 1, _ts(-30), _ts(-1)),
        )
    return len(items)


def _seed_review_and_findings(conn, opp_id: str, sec_id: str, vol: str, domain: str) -> int:
    findings = FINDINGS_BY_VOL.get(vol, [])
    if not findings:
        return 0

    review_id = _uid()
    review_type = "gold_team" if vol == "I" else ("pink_team" if vol == "II" else "red_team")
    n_resolved = sum(1 for f in findings if f[2] == "resolved")
    conn.execute(
        """INSERT INTO proposal_reviews (
            id, opportunity_id, review_type, status, scheduled_date,
            started_at, completed_at, lead_reviewer, participants,
            summary, overall_rating, classification, created_at
        ) VALUES (%s,%s,%s,'completed',%s,%s,%s,%s,%s,%s,%s,'CUI',%s)""",
        (
            review_id, opp_id, review_type,
            _ts(-8), _ts(-7), _ts(-5),
            "demo_seed",            # lead_reviewer used as idempotency tag
            json.dumps(REVIEWERS[:4]),
            f"{review_type.replace('_', ' ').title()} review completed. "
            f"{len(findings)} findings logged; {n_resolved} resolved.",
            "pass_with_findings",
            _ts(-8),
        ),
    )

    count = 0
    for finding_type, severity, status, description, recommendation in findings:
        resolved_at = _ts(-3) if status == "resolved" else None
        conn.execute(
            """INSERT INTO proposal_review_findings (
                id, review_id, section_id, finding_type, severity,
                description, recommendation, status,
                assigned_to, resolved_at, classification, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CUI',%s)""",
            (
                _uid(), review_id, sec_id, finding_type, severity,
                description, recommendation, status,
                REVIEWERS[count % len(REVIEWERS)],
                resolved_at, _ts(-7),
            ),
        )
        count += 1

    return count


_dep_id_counter = [1000]   # mutable counter for deps integer PK
_hist_id_counter = [1000]  # mutable counter for history integer PK


def _dep_id() -> int:
    _dep_id_counter[0] += 1
    return _dep_id_counter[0]


def _seed_dependencies(conn, sections: list[dict]) -> int:
    count = 0
    dep_pairs = [
        (sections[1]["id"], sections[0]["id"], "content", "drafting"),
        (sections[2]["id"], sections[0]["id"], "approval", "internal_review"),
        (sections[2]["id"], sections[1]["id"], "data", "drafting"),
    ]
    for sec_id, depends_on, dep_type, req_status in dep_pairs:
        conn.execute(
            """INSERT INTO proposal_section_dependencies (
                id, section_id, depends_on_section_id, dependency_type,
                required_status, classification, created_at
            ) VALUES (%s,%s,%s,%s,%s,'CUI',%s)""",
            (_dep_id(), sec_id, depends_on, dep_type, req_status, _ts(-30)),
        )
        count += 1
    return count


def _hist_id() -> int:
    _hist_id_counter[0] += 1
    return _hist_id_counter[0]


def _seed_history(conn, sections: list[dict]) -> int:
    count = 0
    for sec in sections:
        sec_id = sec["id"]
        for i, (old_s, new_s, days_offset, reason) in enumerate(STATUS_PROGRESSION):
            conn.execute(
                """INSERT INTO proposal_status_history (
                    id, entity_type, entity_id, old_status, new_status,
                    changed_by, reason, classification, created_at
                ) VALUES (%s,'section',%s,%s,%s,%s,%s,'CUI',%s)""",
                (_hist_id(), sec_id, old_s, new_s, "demo_seed", reason, _ts(days_offset)),
            )
            count += 1
    return count


def seed(dry_run: bool = False) -> dict:
    if dry_run:
        total_sec = sum(len(c["sections"]) for c in CONTRACTS)
        return {
            "dry_run": True,
            "would_process": {
                "contracts": len(CONTRACTS),
                "sections": total_sec,
                "compliance_items_per_section": "5-6",
                "history_entries_per_section": len(STATUS_PROGRESSION),
                "dependencies_per_contract": 3,
            },
        }

    conn = get_connection()

    # Seed integer PK counters from current max values
    max_dep_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM proposal_section_dependencies"
    ).fetchone()[0]
    _dep_id_counter[0] = int(max_dep_id)

    max_hist_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM proposal_status_history"
    ).fetchone()[0]
    _hist_id_counter[0] = int(max_hist_id)

    deleted = _delete_demo_data(conn)

    inserted = {
        "notes_updated": 0,
        "compliance_items": 0,
        "reviews": 0,
        "findings": 0,
        "dependencies": 0,
        "history": 0,
    }

    for contract in CONTRACTS:
        opp_id = contract["opp_id"]
        domain = contract["domain"]
        sections = contract["sections"]

        for sec in sections:
            sec_id = sec["id"]
            vol = sec["vol"]

            _seed_notes(conn, sec_id, vol, domain)
            inserted["notes_updated"] += 1

            inserted["compliance_items"] += _seed_compliance(conn, opp_id, sec_id, vol, domain)

            if vol in FINDINGS_BY_VOL:
                cnt = _seed_review_and_findings(conn, opp_id, sec_id, vol, domain)
                if cnt:
                    inserted["reviews"] += 1
                    inserted["findings"] += cnt

        inserted["dependencies"] += _seed_dependencies(conn, sections)
        inserted["history"] += _seed_history(conn, sections)

    conn.commit()
    return {
        "status": "ok",
        "deleted": deleted,
        "inserted": inserted,
        "contracts": [c["title"] for c in CONTRACTS],
        "urls": [
            f"/proposals/{c['opp_id']}/sections/{c['sections'][0]['id']}"
            for c in CONTRACTS
        ],
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo proposal section content")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = seed(dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        if args.dry_run:
            d = result["would_process"]
            print(f"[dry-run] Would process {d['contracts']} contracts, "
                  f"{d['sections']} sections, "
                  f"~{d['history_entries_per_section']} history entries/section")
        else:
            ins = result["inserted"]
            print(f"Seeded: {ins['notes_updated']} sections with notes, "
                  f"{ins['compliance_items']} compliance items, "
                  f"{ins['findings']} findings, "
                  f"{ins['dependencies']} dependencies, "
                  f"{ins['history']} history entries")
            print("\nDemo URLs:")
            for url in result["urls"]:
                print(f"  http://localhost:5050{url}")


if __name__ == "__main__":
    _main()
