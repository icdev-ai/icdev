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


_DRAFT_CONTENT = {
    "cloud": [
        (
            "Technical Approach",
            """**1.0 Technical Approach — Cloud Migration and Modernization**

Our team proposes a proven, four-phase cloud migration methodology — Assess, Plan, Execute, and Validate — that minimizes operational risk while accelerating the path to a FedRAMP High-authorized cloud environment.

**Phase 1: Discovery and Assessment.** Within the first 60 days, Nexora Federal Solutions will deploy our Cloud Readiness Assessment Framework (CRAF) to inventory all 200 production workloads, classify each by data sensitivity, dependency complexity, and migration suitability. We will produce a Migration Decision Matrix categorizing workloads into Rehost (Lift-and-Shift), Replatform, Refactor, or Retain disposition buckets.

**Phase 2: Migration Planning.** Our certified AWS and Azure architects will design the target-state architecture aligned to the DoD Cloud Computing Security Requirements Guide (CC SRG) at Impact Level 4. We will establish the FedRAMP High authorization boundary, define the system security plan (SSP) outline, and coordinate with the Government's Authorizing Official (AO) on the ATO milestones.

**Phase 3: Phased Execution.** Migration waves will be executed in 30-day sprints using our Infrastructure-as-Code (IaC) automation toolchain (Terraform + Ansible). Each wave undergoes a pre-migration baseline capture, automated smoke testing post-migration, and a performance validation gate before the next wave commences. Zero-downtime migration will be achieved through blue-green deployment patterns for all Tier 1 applications.

**Phase 4: Validation and ATO Support.** We will conduct a full security control assessment against all NIST SP 800-53 Rev 5 High baseline controls, produce POA&M artifacts for any residual findings, and support the Agency's AO through the ATO package submission. Our target is ATO issuance within 12 months of contract award.""",
        ),
        (
            "Management Approach",
            """**2.0 Management Approach**

Nexora Federal Solutions will establish a dedicated Cloud Migration Program Management Office (PMO) led by our proposed Program Manager, a PMP-certified professional with 15 years of Federal IT modernization experience. Our management framework integrates Agile delivery with rigorous Earned Value Management (EVM) reporting to provide full visibility into schedule, cost, and technical performance.

**Governance.** A bi-weekly Program Review Board (PRB) will convene with Government and contractor leadership to review migration wave status, resolve blockers, and approve change requests. Risk and issue registers will be maintained in the Government-approved ITSM system and updated within 24 hours of any new risk identification.

**Staffing.** Our team of 22 FTEs is fully cleared and available for assignment within 30 days of award. Key personnel include the Program Manager, Cloud Architect Lead, Security Engineer, and DevSecOps Lead — all of whom have submitted letters of commitment and current resumes.

**Quality Assurance.** An independent QA Lead will review all deliverables against the CDRL requirements matrix prior to submission. Our defect density target is fewer than 2 defects per KLOC across all IaC code artifacts.""",
        ),
        (
            "Past Performance",
            """**3.0 Past Performance**

**Reference 1: DHS CBP Cloud Modernization (2022–2024)**
Contract Value: $24.7M | Agency: U.S. Customs and Border Protection
Nexora Federal Solutions successfully migrated 312 workloads to AWS GovCloud (US) achieving FedRAMP High ATO in 10 months. We delivered zero Sev-1 incidents during the migration window and received an Exceptional CPARS rating.

**Reference 2: DoD USTRANSCOM Infrastructure Modernization (2021–2023)**
Contract Value: $18.2M | Agency: U.S. Transportation Command
Led the migration of the JOPES legacy environment to Azure Government IL4, executing 8 migration waves over 14 months with 99.97% uptime maintained throughout. Government assessed performance as Very Good across all evaluation factors.

**Reference 3: HHS CMS Data Platform Cloud Migration (2020–2022)**
Contract Value: $31.4M | Agency: Centers for Medicare & Medicaid Services
Designed and executed a hybrid cloud architecture migrating CMS analytics workloads to AWS GovCloud, achieving FedRAMP Moderate ATO and reducing annual infrastructure costs by 34%. Past performance rating: Exceptional.""",
        ),
    ],
    "security": [
        (
            "Technical Approach",
            """**1.0 Technical Approach — Cybersecurity Assessment and Hardening**

CyberShield Federal LLC proposes a comprehensive, risk-based cybersecurity assessment methodology grounded in NIST SP 800-53 Rev 5 and aligned to the NIST Cybersecurity Framework (CSF). Our approach delivers an actionable security posture baseline, a prioritized remediation roadmap, and full ATO documentation support within the period of performance.

**Assessment Scope and Methodology.** Our Security Assessment Team will evaluate all 20 NIST SP 800-53 Rev 5 High baseline control families across all in-scope systems, including the six systems currently operating under IATO. We employ a combination of automated scanning (Tenable.sc, Qualys), manual control testing, and architecture review to ensure complete coverage.

**Vulnerability Management.** Identified findings will be categorized using CVSS v3.1 scores and imported directly into the Government's ITSM-integrated POAM tracking system. All Critical and High findings will receive remediation verification within 72 hours of contractor remediation action. Our average POAM closure rate on comparable programs is 94% within the initial 180-day remediation window.

**Continuous Monitoring.** We will implement a continuous monitoring capability using Splunk SIEM integrated with the agency SOC, providing real-time alerting on all CAT I vulnerabilities and automated weekly compliance dashboard reporting to the ISSM and AO.""",
        ),
        (
            "Management Approach",
            """**2.0 Management Approach**

Our Cybersecurity Assessment PMO is structured around three functional leads: Assessment Lead (CISSP), Remediation Lead (CISM), and Continuous Monitoring Lead (GCIH). All personnel hold active TS/SCI clearances and are immediately available for assignment.

**Staffing Plan.** We will place 14 security professionals on-site within 15 days of award, with full staffing complement (18 FTEs) achieved by day 30. Our 15% bench strength is maintained through our corporate cybersecurity talent pipeline of 40+ cleared professionals available for surge.

**Reporting.** Weekly vulnerability status reports will be delivered every Friday by COB. Monthly POAM status reviews will be conducted with the Government's ISSM. All deliverables comply with CDRL A001 through A006 schedule requirements.""",
        ),
        (
            "Past Performance",
            """**3.0 Past Performance**

**Reference 1: DoD DLA Enterprise Security Assessment (2023–2024)**
Contract Value: $9.8M | Agency: Defense Logistics Agency
Conducted FISMA High baseline assessment across 47 systems, identified and tracked 2,847 findings to closure at a 97% rate. ATO packages delivered for 12 systems. CPARS: Exceptional.

**Reference 2: VA OIT Cybersecurity Hardening (2022–2023)**
Contract Value: $14.3M | Agency: Department of Veterans Affairs
Implemented NIST CSF-aligned security controls across VA's national data center infrastructure. Reduced critical/high vulnerability count by 89% in 6 months. CPARS: Very Good.

**Reference 3: DHS ICE Network Security Assessment (2021–2022)**
Contract Value: $7.1M | Agency: U.S. Immigration and Customs Enforcement
Performed penetration testing and red team exercises across ICE enterprise network. Delivered 156-page remediation report accepted without revision. CPARS: Exceptional.""",
        ),
    ],
    "devsecops": [
        (
            "Technical Approach",
            """**1.0 Technical Approach — DevSecOps Pipeline Implementation**

DigitalFederal Systems proposes a hardened, DoD Enterprise DevSecOps Reference Design (DSOP)-compliant CI/CD pipeline that integrates security at every stage of the software development lifecycle. Our pipeline architecture is container-native, tool-agnostic at the application layer, and operates entirely within Government-approved IL4 infrastructure.

**Pipeline Architecture.** Our reference pipeline consists of: (1) Source Control — GitLab Ultimate on DoD Platform One; (2) SAST — SonarQube with OWASP ruleset plus Semgrep for IaC security; (3) SCA — Black Duck for open-source dependency scanning; (4) Container Scanning — Anchore Enterprise against the DoD Iron Bank hardened image registry; (5) DAST — OWASP ZAP automated integration testing; (6) Artifact Registry — Nexus Repository with signed artifact chain-of-custody; (7) Deployment — ArgoCD GitOps with automated drift detection.

**Shift-Left Security.** All 8 application teams (120 concurrent users) will receive a 2-day DevSecOps Fundamentals training course within the first 45 days. Security gates are non-bypassable in the pipeline — any Critical or High SAST/DAST finding blocks the merge request until resolved or formally risk-accepted by the ISSO.

**SBOM.** CycloneDX-format SBOMs will be generated automatically for all custom-developed software at each release. COTS components included in our managed toolchain are documented in the Tool Equivalency Matrix (Attachment A to our Technical Volume).""",
        ),
        (
            "Management Approach",
            """**2.0 Management Approach**

DigitalFederal Systems will operate under a DevOps Program Management framework combining SAFe Agile at the program level with Scrum at the team level. Our Program Manager holds SAFe Program Consultant (SPC) and PMP certifications with 12 years of Federal DevSecOps experience.

**Sprint Cadence.** Two-week sprints will be executed across all 8 application teams. Monthly Program Increment (PI) Planning sessions will synchronize team backlogs with Government priorities. Sprint velocity and defect escape rate metrics will be reported monthly per CDRL A003 format.

**Labor Rate Management.** All labor categories are fixed at negotiated rates with 3% annual escalation per option year exercise as confirmed in Section B.4. No Time-and-Materials components are included in this proposal.""",
        ),
        (
            "Past Performance",
            """**3.0 Past Performance**

**Reference 1: DISA DoD DevSecOps Platform Deployment (2023–2024)**
Contract Value: $22.1M | Agency: Defense Information Systems Agency
Deployed DoD Platform One IL5 DevSecOps environment supporting 14 program offices and 600+ developers. Reduced mean time to deploy from 6 weeks to 4 hours. CPARS: Exceptional.

**Reference 2: Army PEO Enterprise Software DevSecOps (2022–2023)**
Contract Value: $16.8M | Agency: Army Program Executive Office Enterprise Information Systems
Established GitLab-based CI/CD pipeline for GCSS-Army modernization, achieving ATO for containerized workloads in 8 months. Defect escape rate reduced by 73%. CPARS: Very Good.

**Reference 3: NGA Cloud-Native DevSecOps (2021–2022)**
Contract Value: $11.4M | Agency: National Geospatial-Intelligence Agency
Implemented multi-cloud DevSecOps pipeline spanning AWS IC and Azure Government, supporting 22 development teams. Zero Critical security findings escaped to production. CPARS: Exceptional.""",
        ),
    ],
    "ai_ml": [
        (
            "Technical Approach",
            """**1.0 Technical Approach — AI/ML Platform Development**

QuantumLeap Analytics proposes an end-to-end AI/ML platform architecture designed for operational resilience, model governance, and compliance with DoD AI Ethics Principles and NIST AI RMF. Our platform supports the full ML lifecycle — from data ingestion through model training, validation, deployment, and continuous monitoring.

**Platform Architecture.** The platform comprises four integrated layers: (1) Data Foundation — a Government-furnished data lake on AWS GovCloud with automated data quality validation and lineage tracking; (2) Training Infrastructure — GPU clusters (A100-series) with MLflow experiment tracking and DVC data versioning; (3) Model Registry — MLflow Model Registry with mandatory Model Cards (per Attachment 7 template) for each production model; (4) Inference Layer — dual deployment to NIPRNet (batch and API inference) and SIPR (inference-only, air-gap compatible).

**AI Governance.** Our AI Governance Plan addresses all Model Card requirements, bias detection via IBM AI Fairness 360, adversarial robustness testing via IBM ART, and quarterly NIST AI RMF Govern/Map/Measure/Manage cycle reviews. We will achieve AI RMF Tier 2 maturity at award and Tier 3 by end of base period per Attachment 8 milestones.

**MLOps.** Model versioning, A/B testing infrastructure, and data/concept drift monitoring via Evidently AI will be operational within 90 days of award. All training data is stored in Government-approved GovCloud storage with AES-256 encryption at rest and TLS 1.3 in transit.""",
        ),
        (
            "Management Approach",
            """**2.0 Management Approach**

QuantumLeap Analytics fields a multidisciplinary team led by a Program Manager (10 years Federal AI/ML, PMP) and a Lead Data Scientist (PhD, Computer Science, 8 publications in applied ML). Our 16-person team includes 4 PhDs, 6 MS-level data scientists, and 6 ML engineers — all cleared to TS/SCI.

**Delivery Model.** We operate two-week sprints with monthly model performance review boards attended by Government technical leads. All model promotion decisions are documented with rationale, test metrics, and ISSO approval prior to production deployment.

**IP and Data Rights.** Per the revised Section H.12, the Government retains unlimited rights to all models trained on Government-furnished data. QuantumLeap retains background IP in our reusable MLOps toolchain components, which are licensed to the Government for unlimited use during the period of performance.""",
        ),
        (
            "Past Performance",
            """**3.0 Past Performance**

**Reference 1: IC Element Computer Vision Platform (2023–2024)**
Contract Value: $28.4M | Agency: Intelligence Community Element (FOUO)
Developed and deployed 12 production ML models for imagery analysis. Average model accuracy 94.2%. All models delivered with DoD AI Ethics-compliant documentation. CPARS: Exceptional.

**Reference 2: DoD OUSD(R&E) AI Readiness Assessment Tool (2022–2023)**
Contract Value: $8.7M | Agency: Office of the Under Secretary of Defense for Research and Engineering
Built federated ML training infrastructure supporting 6 DoD Components. NIPRNet-to-SIPR model migration completed without security incidents. CPARS: Very Good.

**Reference 3: DHS CBP Predictive Analytics Platform (2021–2022)**
Contract Value: $19.3M | Agency: U.S. Customs and Border Protection
Deployed 8 ML models supporting border security operations with 97.8% uptime SLA achieved. FedRAMP High ATO secured for AI platform. CPARS: Exceptional.""",
        ),
    ],
    "management": [
        (
            "Technical Approach",
            """**1.0 Technical Approach — Help Desk and O&M Support**

FedSupport Services proposes a structured, ITIL v4-aligned service management approach that delivers measurable improvements to Tier 1, 2, and 3 Help Desk operations while ensuring seamless transition from the incumbent and achieving all SLA targets from Day 1.

**Service Delivery Model.** Our three-tier support model provides: Tier 1 — 24/7/365 Service Desk with IVR triage and live agent support targeting 85% First Contact Resolution (FCR); Tier 2 — Subject Matter Expert escalation for infrastructure, network, and application issues (MTTR target: 4 hours for P1, 8 hours for P2); Tier 3 — Engineering Support for complex break-fix, change management, and problem management.

**Tooling.** We will implement ServiceNow ITSM (Government-furnished, integrated with agency CMDB within 30 days of award) as the single pane of glass for all incident, problem, change, and request management. Knowledge base articles will be created for the top 50 ticket categories within 60 days of transition-in.

**Staffing Surge.** Our bench strength of 22% (above the 15% required) ensures we can absorb the Year 3 seat count growth to 4,200 without service degradation. Surge staffing is drawn from our corporate talent pool of 85 cleared Help Desk professionals.""",
        ),
        (
            "Management Approach",
            """**2.0 Management Approach**

FedSupport Services will staff a Service Delivery Manager (ITIL v4 Managing Professional certified) as the primary Government interface. Our transition-in plan ensures zero service disruption through a 60-day parallel operations period with the incumbent.

**SLA Performance.** Based on our track record across 12 Federal Help Desk contracts, we consistently achieve 98.2% FCR rate and 99.4% SLA compliance. Service credits per Section H.8 Table 2 (assessed against the full calendar month value) represent a maximum exposure of 2% of monthly contract value, which we have incurred on only 2 of 144 contract months across our portfolio.

**Reporting.** Monthly service reports will be delivered NLT the 5th business day of the following month per CDRL A001. Reports will include SLA scorecards, trend analysis, and a rolling 90-day improvement plan.""",
        ),
        (
            "Past Performance",
            """**3.0 Past Performance**

**Reference 1: GSA Enterprise Help Desk Services (2022–2024)**
Contract Value: $31.7M | Agency: General Services Administration
Provided Tier 1/2/3 support for 18,400 GSA users across 11 regional offices. Achieved 99.1% SLA compliance and 97.4% FCR. Customer satisfaction score: 4.6/5.0. CPARS: Exceptional.

**Reference 2: HHS NIH IT Service Desk (2021–2023)**
Contract Value: $19.8M | Agency: National Institutes of Health
Managed 22,000-seat Help Desk with ServiceNow ITSM. Reduced average handle time from 8.2 to 5.4 minutes in first 6 months. Zero SLA credits assessed in 24 months of performance. CPARS: Very Good.

**Reference 3: DoE NNSA IT Support Services (2020–2022)**
Contract Value: $14.2M | Agency: National Nuclear Security Administration
Delivered classified (SCI) and unclassified Help Desk services for 6,800 NNSA users. Achieved FISMA Moderate compliance for all ITSM tooling within 90 days of award. CPARS: Exceptional.""",
        ),
    ],
}

_DRAFT_STATUSES = [
    ("approved", 0.92, 0.88),
    ("reviewed", 0.78, 0.71),
    ("draft", 0.65, 0.58),
]


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
        conn.execute(f"DELETE FROM proposal_section_drafts WHERE opportunity_id IN ({placeholders})", opp_ids)
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

    inserted = {"compliance": 0, "reviews": 0, "findings": 0, "questions": 0, "responses": 0, "amendments": 0, "drafts": 0}

    for opp_id, domain in opps:
        domain_key = domain if domain in _COMPLIANCE_TEMPLATES else "management"
        compliance_templates = _COMPLIANCE_TEMPLATES[domain_key]
        questions_templates = _QUESTIONS.get(domain_key, _QUESTIONS["management"])
        responses_templates = _QUESTION_RESPONSES.get(domain_key, _QUESTION_RESPONSES["management"])

        amendments_templates = _AMENDMENTS.get(domain_key, _AMENDMENTS["management"])

        draft_templates = _DRAFT_CONTENT.get(domain_key, _DRAFT_CONTENT["management"])

        if dry_run:
            inserted["compliance"] += len(compliance_templates)
            inserted["reviews"] += 2
            inserted["findings"] += 4
            inserted["questions"] += len(questions_templates)
            inserted["responses"] += 3
            inserted["amendments"] += len(amendments_templates)
            inserted["drafts"] += len(draft_templates)
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

        # Section drafts
        for i, (section_label, draft_text) in enumerate(draft_templates):
            sec_id = sec_ids[i] if i < len(sec_ids) else (sec_ids[0] if sec_ids else None)
            status, conf_score, confidence = _DRAFT_STATUSES[i % len(_DRAFT_STATUSES)]
            meta = {"best_coverage": round(confidence * 0.95, 2), "rag_chunks_used": _RNG.randint(3, 8)}
            conn.execute(
                """INSERT INTO proposal_section_drafts
                    (id, section_id, opportunity_id, draft_content, draft_method,
                     confidence_score, confidence, domain_category, generation_model,
                     status, metadata, classification, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), sec_id, opp_id, draft_text, "two_tier",
                 conf_score, confidence, domain_key, "qwen3+claude-sonnet-4-5",
                 status, json.dumps(meta), "CUI", now, now)
            )
            inserted["drafts"] += 1

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
            print(f"  Drafts:           {ins['drafts']}")


if __name__ == "__main__":
    _main()
