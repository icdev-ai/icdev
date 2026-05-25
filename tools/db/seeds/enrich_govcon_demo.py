"""Enrich synthetic GovCon demo proposals with compliance items, reviews, findings, and questions.

Idempotent — clears and re-inserts all demo-enrichment rows each run.
Run: python tools/db/seeds/enrich_govcon_demo.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import uuid
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection

_RNG = random.Random(99)
_NOW = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Compliance requirement templates by archetype/domain
# ---------------------------------------------------------------------------
_COMPLIANCE_TEMPLATES = {
    "cloud": [
        ("L.3.1", "L", "The offeror shall describe their cloud migration methodology, including assessment, planning, execution, and validation phases."),
        ("L.3.2", "L", "The offeror shall provide a detailed technical approach for migrating legacy on-premises workloads to FedRAMP-authorized cloud environments."),
        ("L.3.3", "L", "The offeror shall describe their approach to ensuring data integrity and zero data loss during migration activities."),
        ("M.2.1", "M", "The Government will evaluate the offeror's demonstrated experience with cloud migrations of similar scope and complexity."),
        ("M.2.2", "M", "The Government will evaluate the offeror's technical approach for achieving FedRAMP High authorization within 12 months."),
        ("M.2.3", "M", "The Government will evaluate the offeror's past performance on cloud migration projects for Federal agencies."),
        ("N.1.1", "N", "The contract will include provisions for SLA enforcement with 99.9% uptime requirements."),
        ("N.1.2", "N", "All work products shall be delivered in accordance with the PWS Section 4 deliverable schedule."),
        ("N.1.3", "other", "The offeror acknowledges the requirement for DoD-approved cloud service providers (IL4/IL5)."),
    ],
    "security": [
        ("L.3.1", "L", "The offeror shall describe their cybersecurity assessment methodology aligned with NIST SP 800-53 Rev 5 controls."),
        ("L.3.2", "L", "The offeror shall provide a Cybersecurity Architecture Plan demonstrating defense-in-depth across all system layers."),
        ("L.3.3", "L", "The offeror shall describe their vulnerability management process including continuous monitoring and POAM management."),
        ("M.2.1", "M", "The Government will evaluate the offeror's experience conducting FISMA assessments for systems at FIPS 199 High impact level."),
        ("M.2.2", "M", "The Government will evaluate the offeror's certifications including CMMC Level 3 and relevant team member certifications (CISSP, CISM)."),
        ("M.2.3", "M", "The Government will evaluate the offeror's demonstrated experience with ATOs on systems comparable in complexity."),
        ("N.1.1", "N", "All personnel with access to classified data must hold active TS/SCI clearances within 30 days of award."),
        ("N.1.2", "N", "Penetration testing reports shall be delivered within 15 business days of assessment completion."),
        ("N.1.3", "other", "The contractor shall maintain an approved ISSO and ISSM throughout the period of performance."),
    ],
    "devsecops": [
        ("L.3.1", "L", "The offeror shall describe their DevSecOps pipeline architecture integrating SAST, DAST, SCA, and container scanning tools."),
        ("L.3.2", "L", "The offeror shall provide a detailed CI/CD implementation plan leveraging approved DoD Enterprise DevSecOps Reference Design."),
        ("L.3.3", "L", "The offeror shall describe shift-left security practices including developer training, code review gates, and automated policy enforcement."),
        ("M.2.1", "M", "The Government will evaluate the offeror's demonstrated experience deploying DevSecOps pipelines in classified IL4/IL5 environments."),
        ("M.2.2", "M", "The Government will evaluate the offeror's toolchain integration experience (GitLab, SonarQube, Prisma Cloud, Anchore)."),
        ("M.2.3", "M", "The Government will evaluate the offeror's approach to achieving Authority to Operate for containerized workloads."),
        ("N.1.1", "N", "All source code repositories shall be maintained within Government-approved version control systems."),
        ("N.1.2", "N", "Sprint velocity and defect escape rate metrics shall be reported monthly in accordance with CDRL A003."),
        ("N.1.3", "other", "The contractor shall provide SBOM artifacts for all software deliverables within 5 business days of release."),
    ],
    "ai_ml": [
        ("L.3.1", "L", "The offeror shall describe their AI/ML platform architecture including data ingestion, model training, and inference pipeline components."),
        ("L.3.2", "L", "The offeror shall provide an AI Governance Plan addressing model explainability, bias detection, and adversarial robustness."),
        ("L.3.3", "L", "The offeror shall describe their approach to responsible AI development in compliance with DoD AI Ethics Principles."),
        ("M.2.1", "M", "The Government will evaluate the offeror's experience developing and deploying ML models in operationally constrained environments."),
        ("M.2.2", "M", "The Government will evaluate the offeror's approach to MLOps including model versioning, A/B testing, and drift monitoring."),
        ("M.2.3", "M", "The Government will evaluate the offeror's team qualifications including PhDs, published research, and relevant certifications."),
        ("N.1.1", "N", "All training data shall be stored in approved GovCloud storage with encryption at rest and in transit."),
        ("N.1.2", "N", "Model performance metrics shall be reported quarterly in accordance with the Data Science Reporting CDRL."),
        ("N.1.3", "other", "The contractor shall comply with DoD Instruction 3000.09 for Autonomous and Semi-Autonomous Weapons Systems where applicable."),
    ],
    "management": [
        ("L.3.1", "L", "The offeror shall describe their Help Desk service management methodology aligned with ITIL v4 best practices."),
        ("L.3.2", "L", "The offeror shall provide a staffing plan demonstrating surge capacity with minimum 15% bench strength."),
        ("L.3.3", "L", "The offeror shall describe their approach to knowledge management, ticket resolution, and continuous service improvement."),
        ("M.2.1", "M", "The Government will evaluate the offeror's demonstrated experience managing Tier 1/2/3 support operations for Federal agencies."),
        ("M.2.2", "M", "The Government will evaluate the offeror's SLA track record including FCR rate, MTTR, and customer satisfaction scores."),
        ("M.2.3", "M", "The Government will evaluate the offeror's transition-in plan demonstrating minimal service disruption."),
        ("N.1.1", "N", "All Help Desk personnel must complete background investigation within 45 days of award."),
        ("N.1.2", "N", "Monthly service reports shall be delivered NLT the 5th business day of the following month per CDRL A001."),
        ("N.1.3", "other", "The contractor shall maintain a Government-accessible ITSM ticketing system integrated with agency CMDB."),
    ],
}

_COMPLIANCE_STATUSES = [
    ("compliant", 4), ("partial", 2), ("not_addressed", 2), ("compliant", 1),
]

_REVIEW_FINDINGS = {
    "pink_team": [
        ("content_weakness", "major", "Technical approach section lacks specificity on FedRAMP boundary documentation. Recommend adding two pages on cloud authorization boundary definition."),
        ("competitive_risk", "minor", "Win theme messaging is generic — does not differentiate from likely competitors. Revise to emphasize unique accelerators."),
        ("compliance_gap", "major", "Section L.3.2 response does not address the 12-month ATO timeline explicitly. Add milestone chart."),
        ("formatting", "minor", "Volume page count is 2 pages over limit. Condense past performance references to stay within bounds."),
    ],
    "red_team": [
        ("content_weakness", "critical", "Past performance section references a project where the agency mission does not align with the solicitation. Replace with stronger relevant example."),
        ("compliance_gap", "major", "Management approach does not address transition-out requirements per Section L.4.3. Add dedicated subsection."),
        ("competitive_risk", "major", "Price-to-win analysis indicates proposed rates are 8% above likely competitor range. Review labor mix and indirect rates."),
        ("content_weakness", "minor", "Resumes for three key personnel do not demonstrate required 8 years of relevant experience. Revise or substitute."),
    ],
}

_QUESTIONS = {
    "cloud": [
        ("technical_requirements", "high", "Section L.3.2 — Will the Government accept FedRAMP Moderate authorization for non-sensitive workloads, or is FedRAMP High required for all systems?", "L.3.2"),
        ("scope", "high", "PWS Section 3.2 — Please clarify whether the 200 VMs listed in Attachment 1 include disaster recovery instances or only production workloads.", "PWS 3.2"),
        ("evaluation_criteria", "medium", "Section M.2.1 — Does 'similar scope' require a minimum contract value threshold, or is it based on technical complexity?", "M.2.1"),
        ("contract_terms", "medium", "Section H.17 — Please confirm the period of performance option years are exercised annually and not subject to re-competition.", "H.17"),
        ("compliance_security", "high", "Attachment 3 — Is a DoD-issued IL4 Provisional Authorization sufficient, or is a full ATO required prior to system migration?", "Attachment 3"),
    ],
    "security": [
        ("technical_requirements", "high", "Section L.3.1 — Does the NIST SP 800-53 Rev 5 assessment scope include all 20 control families, or only the high-impact baseline?", "L.3.1"),
        ("scope", "high", "PWS 2.4 — Are there any systems currently operating under IATO that must be included in the assessment scope?", "PWS 2.4"),
        ("evaluation_criteria", "medium", "Section M.2.2 — Will team CMMC Level 2 certification meet the requirement, or is Level 3 mandatory for all personnel?", "M.2.2"),
        ("contract_terms", "medium", "Section I.11 — Please confirm whether the Government-Furnished Equipment (GFE) list in Attachment 2 is complete.", "I.11"),
        ("compliance_security", "high", "Section H.4 — Are personnel required to hold active clearances at award, or may they begin the investigation process post-award?", "H.4"),
    ],
    "devsecops": [
        ("technical_requirements", "high", "Section L.3.1 — Does the DoD DSOP reference design mandate specific tool versions, or may the offeror propose equivalent open-source alternatives?", "L.3.1"),
        ("scope", "high", "PWS 4.1 — Please confirm the number of distinct application teams that will consume the DevSecOps pipeline services.", "PWS 4.1"),
        ("evaluation_criteria", "medium", "Section M.2.2 — Will demonstrated experience with GitLab Ultimate fulfill the requirement, or is a DoD Iron Bank hardened registry required?", "M.2.2"),
        ("contract_terms", "medium", "Section B.4 — Is labor rate escalation capped at CPI or a negotiated fixed percentage per option year?", "B.4"),
        ("compliance_security", "high", "Attachment 5 — Are SBOM requirements in scope for COTS tools included in the pipeline, or only custom-developed software?", "Attachment 5"),
    ],
    "ai_ml": [
        ("technical_requirements", "high", "Section L.3.2 — Does the AI Governance Plan requirement include a Model Cards deliverable for each production model?", "L.3.2"),
        ("scope", "high", "PWS 3.5 — Please clarify whether the 'operationally constrained environment' includes disconnected/SIPR deployments.", "PWS 3.5"),
        ("evaluation_criteria", "medium", "Section M.2.3 — Does the PhD requirement apply to the Program Manager or only to data scientists?", "M.2.3"),
        ("contract_terms", "medium", "Section H.12 — Who owns the IP rights to ML models trained exclusively on Government-furnished data?", "H.12"),
        ("compliance_security", "high", "Attachment 6 — Are there specific NIST AI RMF maturity targets expected at contract award versus end of base period?", "Attachment 6"),
    ],
    "management": [
        ("technical_requirements", "high", "Section L.3.1 — Does ITIL v4 Foundation certification satisfy the service management requirement, or is ITIL v4 Managing Professional required?", "L.3.1"),
        ("scope", "high", "PWS 2.1 — Please confirm the total user seat count in Attachment 1 reflects projected Year 3 growth.", "PWS 2.1"),
        ("evaluation_criteria", "medium", "Section M.2.2 — Is the FCR rate evaluated against industry benchmarks or the incumbent's historical performance?", "M.2.2"),
        ("contract_terms", "medium", "Section H.8 — Are service credits assessed against the full month's value or the specific period of non-compliance?", "H.8"),
        ("compliance_security", "high", "Attachment 2 — Are contractors required to use Government-furnished ITSM tooling, or may they propose a commercially hosted solution?", "Attachment 2"),
    ],
}

_AMENDMENTS = {
    "cloud": [
        (1, "Amendment 0001 — FedRAMP Authorization Clarification",
         "Clarifies FedRAMP authorization level requirements. FedRAMP High is required for all systems processing CUI. Updates Attachment 3 with revised authorization boundary diagrams.",
         "Updated Section L.3.2 to specify FedRAMP High only. Attachment 3 revised to include boundary documentation template. Three questions formally answered (see Q&A log)."),
        (2, "Amendment 0002 — DR Scope and Period of Performance Revision",
         "Expands the migration scope to include disaster recovery instances in Option Year 2. Clarifies period of performance and adds surge pricing provisions for emergency migrations.",
         "PWS Section 3.2 revised to exclude DR VMs from base period. PoP Table updated. Section H.17 revised to confirm annual option exercise cadence. Two additional evaluation sub-factors added to Section M."),
    ],
    "security": [
        (1, "Amendment 0001 — Control Family Scope Expansion",
         "Expands NIST SP 800-53 Rev 5 assessment scope to include all 20 control families at High baseline. Revises PWS Section 2.3 and adds six IATO systems to Attachment 4.",
         "PWS 2.3 rewritten to include full control family list. Attachment 4 revised with IATO system inventory. CMMC Level 3 requirement made explicit for all CUI-handling personnel."),
        (2, "Amendment 0002 — Clearance Requirements and Deliverable Schedule",
         "Mandates active TS/SCI clearances at award for all personnel with classified system access. Updates CDRL schedule and adds interim reporting milestones.",
         "Section H.4 revised — clearances required at award, not post-award. CDRL A001 revised with 15-day delivery window. Section I.11 GFE list confirmed complete with no additions."),
    ],
    "devsecops": [
        (1, "Amendment 0001 — Toolchain and Container Registry Requirements",
         "Mandates DoD Iron Bank hardened images for all container workloads. Allows equivalent open-source tools subject to Government approval with tool equivalency matrix submission.",
         "Section L.3.1 revised to require tool equivalency matrix. Iron Bank requirement codified in Section H (new paragraph H.22). SBOM scope clarified — COTS tools excluded from custom SBOM requirement."),
        (2, "Amendment 0002 — Application Team Count and Labor Rate Provisions",
         "Confirms 8 distinct application teams and 120 concurrent pipeline users. Clarifies labor rate escalation cap at fixed 3% per option year.",
         "PWS 4.1 updated with confirmed team count. Section B.4 revised — 3% fixed escalation cap applied uniformly across all labor categories. Sprint reporting cadence changed from bi-weekly to monthly per CDRL A003."),
    ],
    "ai_ml": [
        (1, "Amendment 0001 — Model Cards and AI Governance Deliverables",
         "Adds Model Cards as a mandatory deliverable for each production model. Provides Model Card template in Attachment 7. Clarifies SIPR deployment scope limited to inference only.",
         "Section L.3.2 revised — Model Cards added to CDRL schedule as CDRL A007. Attachment 7 (Model Card template) added. PWS 3.5 updated to confirm training on NIPRNet, inference on SIPR. PhD requirement scoped to Lead Data Scientist."),
        (2, "Amendment 0002 — AI RMF Maturity Targets and IP Provisions",
         "Establishes NIST AI RMF maturity targets at award (Tier 2) and end of base period (Tier 3). Clarifies Government IP ownership for models trained on GFD.",
         "Section H.12 revised — Government retains unlimited rights to all models trained on Government-furnished data. AI RMF milestone chart added as Attachment 8. DoD Instruction 3000.09 applicability confirmed for autonomous inference agents."),
    ],
    "management": [
        (1, "Amendment 0001 — Seat Count Update and ITIL Certification Requirements",
         "Updates projected seat count to 4,200 for Year 3. Clarifies ITIL v4 Managing Professional required for Service Delivery Manager; Foundation acceptable for Tier 1/2 technicians.",
         "Attachment 1 revised with Year 3 seat projections. Section L.3.2 staffing plan requirements updated to reflect new seat count. ITIL certification requirements tiered by role in revised Section M.2.1."),
        (2, "Amendment 0002 — Service Credit Calculation and ITSM Integration",
         "Clarifies service credits assessed against full calendar month value. Mandates Government-accessible ITSM ticketing integrated with agency CMDB within 30 days of award.",
         "Section H.8 Table 2 revised with full-month credit calculation methodology. Attachment 2 ITSM integration requirements added. 45-day background investigation window confirmed for all personnel."),
    ],
}

_QUESTION_RESPONSES = {
    "cloud": [
        "FedRAMP High authorization is required for all systems processing CUI. Moderate authorization will not be accepted. See updated Attachment 3.",
        "The 200 VMs include production instances only. DR instances (estimated 60) are out of scope for the base period migration but must be included in Option Year 2.",
        "Similar scope is defined as contracts valued at $5M or greater with at least 100 VMs migrated. This requirement is firm.",
    ],
    "security": [
        "All 20 control families in the NIST SP 800-53 Rev 5 High baseline are in scope. See revised PWS Section 2.3 (Amendment 1).",
        "Six systems are operating under IATO. Offerors must include these systems in their assessment approach. Attachment 4 (revised) lists all systems.",
        "CMMC Level 3 is required for all personnel with access to CUI. Level 2 is insufficient.",
    ],
    "devsecops": [
        "Equivalent open-source tools are acceptable subject to Government approval. Offerors must provide a tool equivalency matrix as part of their technical approach.",
        "There are 8 distinct application teams. Estimated concurrent users of the pipeline are 120.",
        "DoD Iron Bank hardened images are mandatory for all container workloads. GitLab CE images from public Docker Hub are not permitted.",
    ],
    "ai_ml": [
        "Yes, Model Cards are required for each production model. The template is in Attachment 7 (added per this amendment).",
        "The disconnected SIPR deployment requirement applies to inference only, not model training. Training will occur on NIPRNet.",
        "The PhD requirement applies specifically to the Lead Data Scientist position only. The Program Manager role requires 10 years relevant experience.",
    ],
    "management": [
        "ITIL v4 Managing Professional is required for the Service Delivery Manager. Foundation level satisfies the requirement for Tier 1/2 technicians.",
        "Attachment 1 has been updated to reflect Year 3 projections of 4,200 seats. See Amendment 1.",
        "Service credits are assessed against the full calendar month value. The credit schedule is in Section H.8, Table 2 (revised).",
    ],
}


def _weighted_status() -> str:
    pool = []
    for status, weight in _COMPLIANCE_STATUSES:
        pool.extend([status] * weight)
    return _RNG.choice(pool)


def enrich(dry_run: bool = False) -> dict:
    conn = get_connection()
    now = _NOW

    opps = conn.execute(
        "SELECT id, domain FROM proposal_opportunities WHERE created_by = 'synthetic_demo' ORDER BY created_at"
    ).fetchall()

    if not opps:
        return {"status": "no_opportunities", "message": "Run seed_govcon_proposals.py first"}

    # Clear previous enrichment data for synthetic_demo opportunities
    opp_ids = [o[0] for o in opps]
    placeholders = ",".join(["%s"] * len(opp_ids))

    if not dry_run:
        conn.execute(f"DELETE FROM proposal_review_findings WHERE review_id IN (SELECT id FROM proposal_reviews WHERE opportunity_id IN ({placeholders}))", opp_ids)
        conn.execute(f"DELETE FROM proposal_question_responses WHERE opportunity_id IN ({placeholders})", opp_ids)
        conn.execute(f"DELETE FROM proposal_questions WHERE opportunity_id IN ({placeholders})", opp_ids)
        conn.execute(f"DELETE FROM proposal_reviews WHERE opportunity_id IN ({placeholders})", opp_ids)
        conn.execute(f"DELETE FROM proposal_compliance_matrix WHERE opportunity_id IN ({placeholders})", opp_ids)
        conn.execute(f"DELETE FROM proposal_amendments WHERE opportunity_id IN ({placeholders})", opp_ids)
        # Update sections with richer metadata
        conn.execute(
            f"""UPDATE proposal_sections SET
                writer = CASE section_number
                    WHEN '1' THEN 'Emily Martinez'
                    WHEN '2' THEN 'Robert Brown'
                    WHEN '3' THEN 'Sarah Johnson'
                    ELSE 'TBD'
                END,
                priority = CASE section_number
                    WHEN '1' THEN 'critical_path'
                    WHEN '2' THEN 'high'
                    WHEN '3' THEN 'standard'
                    ELSE 'standard'
                END,
                word_limit = CASE section_number
                    WHEN '1' THEN 8000
                    WHEN '2' THEN 4000
                    WHEN '3' THEN 3000
                    ELSE 2000
                END,
                current_word_count = CASE section_number
                    WHEN '1' THEN 7842
                    WHEN '2' THEN 3956
                    WHEN '3' THEN 2911
                    ELSE 1800
                END,
                status = 'gold_team_review',
                due_date = '2026-06-15',
                updated_at = %s
            WHERE opportunity_id IN ({placeholders})""",
            [now] + opp_ids
        )

    inserted = {"compliance": 0, "reviews": 0, "findings": 0, "questions": 0, "responses": 0, "amendments": 0}

    for opp_id, domain in opps:
        domain_key = domain if domain in _COMPLIANCE_TEMPLATES else "management"
        compliance_templates = _COMPLIANCE_TEMPLATES[domain_key]
        questions_templates = _QUESTIONS.get(domain_key, _QUESTIONS["management"])
        responses_templates = _QUESTION_RESPONSES.get(domain_key, _QUESTION_RESPONSES["management"])

        amendments_templates = _AMENDMENTS.get(domain_key, _AMENDMENTS["management"])

        if dry_run:
            inserted["compliance"] += len(compliance_templates)
            inserted["reviews"] += 2
            inserted["findings"] += 4
            inserted["questions"] += len(questions_templates)
            inserted["responses"] += 3
            inserted["amendments"] += len(amendments_templates)
            continue

        # Compliance matrix
        for i, (ref, req_type, text) in enumerate(compliance_templates):
            status = "compliant" if i < 4 else ("partial" if i < 6 else "not_addressed")
            response = (
                f"Addressed in Volume I, Section {i+1}.{i+1}. Our approach leverages proven methodologies "
                f"to fully satisfy this requirement." if status == "compliant" else
                (f"Partially addressed. Volume I provides high-level approach; detailed procedures to be provided at PDR." if status == "partial" else "")
            )
            conn.execute(
                """INSERT INTO proposal_compliance_matrix
                    (id, opportunity_id, section_ref, requirement_text, requirement_type,
                     compliance_status, response_summary, sort_order, classification, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), opp_id, ref, text, req_type,
                 status, response, i + 1, "CUI", now, now)
            )
            inserted["compliance"] += 1

        # Get section IDs
        sections = conn.execute(
            "SELECT id, section_number FROM proposal_sections WHERE opportunity_id = %s ORDER BY section_number",
            (opp_id,)
        ).fetchall()
        sec_ids = [s[0] for s in sections]
        sec1_id = sec_ids[0] if sec_ids else None

        # Pink team review (completed)
        pink_id = str(uuid.uuid4())
        pink_date = (date.today() - timedelta(days=21)).isoformat()
        conn.execute(
            """INSERT INTO proposal_reviews
                (id, opportunity_id, review_type, status, scheduled_date, completed_at,
                 lead_reviewer, participants, summary, overall_rating, classification, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pink_id, opp_id, "pink_team", "completed", pink_date, pink_date,
             "Dr. Patricia Wells",
             "Emily Martinez, Robert Brown, Sarah Johnson, James Liu",
             "Pink Team review completed. Technical approach is strong but needs targeted improvements in discriminator messaging and page count compliance.",
             "pass_with_findings", "CUI", now)
        )
        inserted["reviews"] += 1

        for finding_type, severity, description in _REVIEW_FINDINGS["pink_team"]:
            status = "resolved" if severity == "minor" else "in_progress"
            conn.execute(
                """INSERT INTO proposal_review_findings
                    (id, review_id, section_id, finding_type, severity, description,
                     recommendation, status, assigned_to, classification, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), pink_id, sec1_id, finding_type, severity, description,
                 "Address prior to Red Team review.", status,
                 "Emily Martinez", "CUI", now)
            )
            inserted["findings"] += 1

        # Red team review (completed)
        red_id = str(uuid.uuid4())
        red_date = (date.today() - timedelta(days=7)).isoformat()
        conn.execute(
            """INSERT INTO proposal_reviews
                (id, opportunity_id, review_type, status, scheduled_date, completed_at,
                 lead_reviewer, participants, summary, overall_rating, classification, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (red_id, opp_id, "red_team", "completed", red_date, red_date,
             "Col. (Ret.) Marcus Stone",
             "Emily Martinez, Robert Brown, Sarah Johnson, Patricia Wells, James Liu, Angela Torres",
             "Red Team review identified critical past performance and pricing competitiveness issues requiring immediate attention before Gold Team.",
             "major_rework", "CUI", now)
        )
        inserted["reviews"] += 1

        for finding_type, severity, description in _REVIEW_FINDINGS["red_team"]:
            status = "in_progress" if severity in ("critical", "major") else "resolved"
            conn.execute(
                """INSERT INTO proposal_review_findings
                    (id, review_id, section_id, finding_type, severity, description,
                     recommendation, status, assigned_to, classification, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), red_id, sec1_id, finding_type, severity, description,
                 "Must be resolved before final submission.", status,
                 "Robert Brown" if finding_type == "content_weakness" else "Sarah Johnson",
                 "CUI", now)
            )
            inserted["findings"] += 1

        # Questions
        for i, (category, priority, question_text, rfp_ref) in enumerate(questions_templates):
            q_id = str(uuid.uuid4())
            q_num = i + 1
            has_response = i < 3
            status = "answered" if has_response else ("submitted" if i == 3 else "approved")
            content_hash = hashlib.sha256(question_text.encode()).hexdigest()
            conn.execute(
                """INSERT INTO proposal_questions
                    (id, opportunity_id, question_number, question_text, category, priority,
                     source, rfp_section_ref, status, content_hash, created_by, classification, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (q_id, opp_id, q_num, question_text, category, priority,
                 "auto", rfp_ref, status, content_hash,
                 "synthetic_demo", "CUI", now, now)
            )
            inserted["questions"] += 1

            if has_response and i < len(responses_templates):
                conn.execute(
                    """INSERT INTO proposal_question_responses
                        (id, question_id, opportunity_id, response_text, response_date,
                         impacts_requirements, recorded_by, classification, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (str(uuid.uuid4()), q_id, opp_id,
                     responses_templates[i],
                     (date.today() - timedelta(days=10)).isoformat(),
                     1, "synthetic_demo", "CUI", now)
                )
                inserted["responses"] += 1

        # Amendments
        for ver_num, title, description, diff_summary in amendments_templates:
            amd_date = (date.today() - timedelta(days=21 - (ver_num * 7))).isoformat()
            conn.execute(
                """INSERT INTO proposal_amendments
                    (id, opportunity_id, version_number, title, description, amendment_date,
                     source_type, amendment_text, diff_summary, changes_detected,
                     uploaded_by, classification, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), opp_id, ver_num, title, description, amd_date,
                 "text", description, diff_summary, _RNG.randint(3, 12),
                 "synthetic_demo", "CUI", now)
            )
            inserted["amendments"] += 1

    if not dry_run:
        conn.commit()

    return {
        "status": "ok" if not dry_run else "dry_run",
        "opportunities_enriched": len(opps),
        "inserted": inserted,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Enrich GovCon demo data")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    result = enrich(dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Status: {result['status']}")
        if not args.dry_run:
            print(f"Enriched: {result['opportunities_enriched']} opportunities")
            ins = result["inserted"]
            print(f"  Compliance items: {ins['compliance']}")
            print(f"  Reviews:          {ins['reviews']}")
            print(f"  Findings:         {ins['findings']}")
            print(f"  Questions:        {ins['questions']}")
            print(f"  Responses:        {ins['responses']}")
            print(f"  Amendments:       {ins['amendments']}")


if __name__ == "__main__":
    _main()
