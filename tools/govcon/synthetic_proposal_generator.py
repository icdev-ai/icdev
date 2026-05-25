"""Synthetic GovCon Proposal Generator.

Generates 50 fully fictional federal IT proposals for demo purposes.
NO real company names, contacts, addresses, or proprietary information.

Usage:
  python tools/govcon/synthetic_proposal_generator.py [--count 50] [--seed 42] [--json]
"""
from __future__ import annotations

import argparse
import json
import random
import string
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Seed — deterministic output across runs
# ---------------------------------------------------------------------------
_RNG = random.Random(42)

_NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Static lookup tables — all fictional / public-domain
# ---------------------------------------------------------------------------

_COMPANY_PREFIXES = [
    "Nexora", "BlueStar", "Ironclad", "Cascade", "Meridian", "Pinnacle",
    "Vanguard", "Axiom", "Clearwater", "Stratos", "Orion", "Centaur",
    "Apex", "Cobalt", "Sentinel", "Horizon", "Forefront", "Keystone",
    "Solaris", "Primus", "Arcturus", "Talon", "Vector", "Zenith",
    "Luminary", "Paragon", "Sterling", "Atlas", "Crestview", "Altitude",
    "Veridian", "Caliber", "Ridgeline", "Sunridge", "Cloudgate", "Bridgepoint",
    "Northmark", "Southgate", "Eastgate", "Westwood", "Greenfield", "Highpoint",
    "Clearpath", "Stronghold", "Fairwind", "Redwood", "Goldcrest", "Silverline",
    "Blackrock", "Whitewater",
]
_COMPANY_SUFFIXES = [
    "Federal Solutions LLC", "Systems Inc.", "Technology Group", "Consulting Corp.",
    "Services LLC", "Solutions Inc.", "Analytics Group", "Digital Corp.",
    "Technologies LLC", "Partners Inc.",
]
_FIRST_NAMES = [
    "James", "Sarah", "Michael", "Emily", "Robert", "Jennifer", "David",
    "Lisa", "John", "Karen", "Thomas", "Patricia", "Charles", "Barbara",
    "Christopher", "Susan", "Matthew", "Jessica", "Anthony", "Linda",
]
_LAST_NAMES = [
    "Thompson", "Williams", "Johnson", "Martinez", "Davis", "Wilson",
    "Anderson", "Taylor", "Brown", "Harris", "Clark", "Lewis", "Robinson",
    "Walker", "Hall", "Young", "Allen", "Hernandez", "King", "Wright",
]
_CITIES = [
    ("Arlington", "VA", "22201"), ("Falls Church", "VA", "22042"),
    ("McLean", "VA", "22102"), ("Bethesda", "MD", "20814"),
    ("Rockville", "MD", "20850"), ("Reston", "VA", "20190"),
    ("Vienna", "VA", "22180"), ("Herndon", "VA", "20170"),
    ("Fairfax", "VA", "22030"), ("Chantilly", "VA", "20151"),
    ("Columbia", "MD", "21044"), ("Silver Spring", "MD", "20910"),
]
_STREET_NAMES = [
    "Technology Drive", "Innovation Boulevard", "Federal Way", "Commerce Circle",
    "Enterprise Parkway", "Liberty Lane", "Freedom Drive", "Capital Court",
    "Government Center", "Defense Highway", "Cyber Court", "Security Plaza",
]
_AGENCIES = [
    ("Department of Defense", "Defense Information Systems Agency", "DOD"),
    ("Department of Homeland Security", "Cybersecurity and Infrastructure Security Agency", "DHS"),
    ("Department of Energy", "Office of the Chief Information Officer", "DOE"),
    ("Department of Health and Human Services", "Office of the Assistant Secretary for Health", "HHS"),
    ("Department of Veterans Affairs", "Office of Information and Technology", "VA"),
    ("General Services Administration", "Federal Acquisition Service", "GSA"),
]
_NAICS_BY_ARCHETYPE = {
    "cloud_migration":    ("541511", "Custom Computer Programming Services"),
    "cybersecurity":      ("541512", "Computer Systems Design Services"),
    "devsecops":          ("541519", "Other Computer Related Services"),
    "ai_ml":              ("541511", "Custom Computer Programming Services"),
    "help_desk":          ("611420", "Computer Training"),
}
_ARCHETYPES = list(_NAICS_BY_ARCHETYPE.keys())
_PROPOSAL_TYPES = ["FFP", "T_AND_M", "CPFF", "CPIF", "IDIQ_TO", "BPA_CALL"]
_CLASSIFICATION = "CUI"
_SET_ASIDE_TYPES = ["full_open", "small_business", "8a", "hubzone", "wosb", "sdvosb"]
_ARCHETYPE_DOMAIN = {
    "cloud_migration": "cloud",
    "cybersecurity":   "security",
    "devsecops":       "devsecops",
    "ai_ml":           "ai_ml",
    "help_desk":       "management",
}


# ---------------------------------------------------------------------------
# Helper generators
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.UUID(int=_RNG.getrandbits(128)))


def _cage_code() -> str:
    chars = string.digits + string.ascii_uppercase
    return "".join(_RNG.choices(chars, k=5))


def _uei() -> str:
    chars = string.digits + string.ascii_uppercase
    return "".join(_RNG.choices(chars, k=12))


def _duns() -> str:
    return "".join(_RNG.choices(string.digits, k=9))


def _phone() -> str:
    area = _RNG.randint(200, 899)
    exch = _RNG.randint(200, 999)
    line = _RNG.randint(1000, 9999)
    return f"({area}) {exch}-{line}"


def _email(first: str, last: str, company: str) -> str:
    domain = company.split()[0].lower().replace(",", "") + "federal.com"
    return f"{first[0].lower()}{last.lower()}@{domain}"


def _ts(days_offset: int = 0) -> str:
    dt = _NOW + timedelta(days=days_offset)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(days_offset: int = 0) -> str:
    return (_NOW + timedelta(days=days_offset)).strftime("%Y-%m-%d")


def _sol_number(agency_code: str, naics: str, seq: int) -> str:
    fy = "FY26"
    return f"{agency_code}-{fy}-{naics}-{seq:04d}"


def _company_name(idx: int) -> str:
    prefix = _COMPANY_PREFIXES[idx % len(_COMPANY_PREFIXES)]
    suffix = _COMPANY_SUFFIXES[idx % len(_COMPANY_SUFFIXES)]
    return f"{prefix} {suffix}"


def _person() -> tuple[str, str]:
    return _RNG.choice(_FIRST_NAMES), _RNG.choice(_LAST_NAMES)


def _address() -> tuple[str, str, str, str]:
    num = _RNG.randint(100, 9999)
    street = _RNG.choice(_STREET_NAMES)
    city, state, zipcode = _RNG.choice(_CITIES)
    return f"{num} {street}", city, state, zipcode


# ---------------------------------------------------------------------------
# Section content templates
# ---------------------------------------------------------------------------

_TECH_TEMPLATES: Dict[str, List[str]] = {
    "cloud_migration": [
        """{company} proposes a phased cloud migration strategy for {agency} leveraging AWS GovCloud (IL4/IL5 compliant) environments. Our approach begins with a comprehensive application portfolio assessment using the 7Rs framework (Retire, Retain, Rehost, Replatform, Refactor, Re-architect, Repurchase) to prioritize workloads for migration.

Phase 1 (Discovery & Assessment, Months 1-3): We will conduct automated discovery using AWS Application Discovery Service and document all 47 identified legacy applications. Our team will produce a Cloud Readiness Report with dependency mapping, security posture analysis aligned to NIST SP 800-53 Rev 5, and total cost of ownership (TCO) projections demonstrating 32% cost reduction over 5 years.

Phase 2 (Foundation & Landing Zone, Months 2-4): We deploy a hardened AWS Landing Zone using Terraform IaC, enforcing STIG-compliant EC2 baselines, VPC network segmentation, and AWS Config rules for continuous compliance monitoring. All infrastructure code is version-controlled in GitLab with automated policy-as-code validation using Checkov.

Phase 3 (Migration Waves, Months 3-12): Using an Agile migration methodology, we execute 4 migration waves prioritized by business criticality and technical complexity. Each wave includes pre-migration testing, cutover execution during approved maintenance windows, and post-migration validation against acceptance criteria documented in our Test and Evaluation Master Plan (TEMP).

Our team maintains an Authority to Operate (ATO) throughout the migration using a continuous ATO (cATO) approach, leveraging automated SCAP scanning and real-time POAM tracking in eMASS. All data transfers use FIPS 140-2 validated encryption in transit and at rest.""",

        """{company} will deliver a zero-downtime cloud modernization program for {agency} using a hybrid-cloud architecture spanning AWS GovCloud and on-premises data centers during transition. Our technical approach centers on containerization and orchestration, transforming legacy monoliths into cloud-native microservices.

Technical Architecture: We deploy a Kubernetes-based platform (Amazon EKS, STIG-hardened) with Istio service mesh for mTLS inter-service communication. Container images are built using hardened base images from the DoD Platform One Iron Bank registry, scanned with Twistlock for vulnerabilities before deployment, and signed with Notary for supply chain integrity.

Infrastructure as Code: All infrastructure is defined in Terraform modules following the FORGE IaC pattern, stored in GitLab with automated policy enforcement. Ansible playbooks handle OS-level hardening to DISA STIG benchmarks. Our Atlantis-based GitOps workflow ensures all changes pass automated security gates before applying to any environment.

Data Management: We implement a tiered data migration strategy using AWS Database Migration Service (DMS) for relational databases and AWS DataSync for file-based workloads. All PII and CUI data is classified using AWS Macie and subject to enhanced access controls with CloudTrail audit logging.

Observability: We deploy a centralized observability stack using Prometheus, Grafana, and the ELK stack, with SIEM integration to the agency's existing SOC. Mean Time to Detect (MTTD) is targeted at under 15 minutes for critical alerts.""",

        """{company} proposes a risk-managed cloud migration roadmap for {agency} grounded in the NIST SP 800-146 Cloud Computing Synopsis and the FedRAMP Authorization process. Our differentiated approach combines automated migration tooling with deep federal compliance expertise to accelerate ATO while maintaining mission continuity.

Cloud Strategy: We adopt a multi-cloud governance model with AWS GovCloud as the primary IaaS provider and Azure Government as disaster recovery, achieving an RPO of 4 hours and RTO of 2 hours for Tier-1 workloads. Network connectivity is established via AWS Direct Connect (10 Gbps dedicated circuit) to the agency's existing MPLS backbone.

Security Architecture: Our Zero Trust Architecture (ZTA) implementation follows NIST SP 800-207 principles, deploying Zscaler Private Access for identity-centric network access, CrowdStrike Falcon for endpoint detection and response, and Palo Alto Prisma Cloud for cloud security posture management (CSPM). Privileged Access Management (PAM) is enforced through CyberArk, with all privileged sessions recorded and auditable.

DevSecOps Integration: We embed security throughout the migration pipeline via a GitLab Ultimate CI/CD platform integrated with static application security testing (SAST), dependency scanning (SCA), container image scanning, and infrastructure-as-code (IaC) scanning. Security findings feed automatically into our POAM management system with SLA-based remediation tracking.""",
    ],
    "cybersecurity": [
        """{company} proposes a comprehensive cybersecurity assessment and hardening program for {agency} aligned to the NIST Cybersecurity Framework (CSF) 2.0 and NIST SP 800-53 Rev 5 control families. Our approach delivers measurable risk reduction through structured assessment, prioritized remediation, and continuous monitoring.

Assessment Methodology: Phase 1 begins with a 90-day comprehensive security assessment encompassing: (1) Vulnerability Assessment and Penetration Testing (VAPT) across 15 identified network segments using a combination of automated scanning (Tenable Nessus, Qualys) and manual exploitation techniques; (2) STIG compliance review across 847 applicable controls for Windows Server 2019, RHEL 8, Cisco IOS-XE, and Oracle Database; and (3) Architecture review against CISA Zero Trust Maturity Model (ZTMM) Level 3 target state.

Findings Remediation: Our team categorizes findings using the CVSS v3.1 scoring framework: Critical (0 open beyond 24 hours), High (remediated within 30 days), Medium (90 days), Low (180 days). All findings are tracked in a centralized POAM with automated evidence collection integrated into the agency's existing eMASS instance.

Continuous Monitoring: We implement a 24/7/365 Security Operations Center (SOC) capability using a SIEM platform (Splunk Enterprise Security) with 150+ custom use cases mapped to MITRE ATT&CK TTPs observed in federal agency threat intelligence. Mean Time to Respond (MTTR) for P1 incidents is contractually guaranteed at under 1 hour.""",

        """{company} delivers a defense-in-depth cybersecurity hardening program for {agency} leveraging our cleared personnel (50+ personnel holding Secret/Top Secret clearances) and our accredited Cyber Range for realistic threat simulation. Our technical approach addresses all 16 CIS Critical Security Controls with measurable compliance metrics.

Red Team Operations: We conduct quarterly adversarial simulations using our in-house Red Team, certified by the National Cyber Range (NCR). Exercises simulate APT-level threat actors including techniques documented in MITRE ATT&CK for Enterprise (v14) with particular focus on Initial Access (T1566 Phishing, T1190 Exploit Public-Facing Application), Lateral Movement, and Impact tactics relevant to federal agencies.

Identity and Access Management: We deploy a hardened Active Directory environment with tiered administration (Tier 0 domain controllers, Tier 1 servers, Tier 2 workstations), enforce multi-factor authentication (MFA) for all privileged and remote access using PIV/CAC cards compliant with HSPD-12, and implement Microsoft Privileged Identity Management (PIM) for just-in-time privileged access.

Endpoint Security: We deploy an EDR/XDR platform (CrowdStrike Falcon Complete) across all 2,400+ endpoints, achieving 99.2% coverage within 30 days. Custom detection logic addresses agency-specific threat indicators, with automated containment for confirmed compromises reducing dwell time to under 4 hours.""",

        """{company} offers {agency} a full-spectrum cybersecurity assessment combining automated tooling with manual expert analysis to deliver actionable hardening recommendations with quantified risk reduction. Our ISO 27001-certified process and CMMC Level 3 compliance demonstrate our commitment to security discipline.

Threat Intelligence Integration: We integrate real-time threat intelligence from CISA AIS, DIBNet Cyber Threat Intelligence, and our proprietary threat feeds into all assessment activities. Threat profiles are mapped to the agency's specific mission systems, enabling prioritized remediation that addresses the most likely attack vectors first.

Application Security: For web applications and APIs, we conduct OWASP Top 10 assessments, authenticated and unauthenticated penetration testing, and static/dynamic application security testing (SAST/DAST). Source code reviews are performed for all in-house developed applications using Checkmarx and manual review of security-critical functions.

Supply Chain Risk Management: We implement NIST SP 800-161 Rev 1 supply chain risk management practices, conducting hardware/software Bill of Materials (SBOM) analysis using Syft and Grype, vendor risk assessments for all third-party software, and continuous monitoring of CVE databases for known exploited vulnerabilities (KEV catalog).""",
    ],
    "devsecops": [
        """{company} proposes a state-of-the-art DevSecOps platform implementation for {agency} that reduces software delivery cycle times by 60% while maintaining strict compliance with DISA DevSecOps Reference Design and DoD Enterprise DevSecOps Reference Design (DSOP). Our solution transforms the current SDLC from quarterly releases to weekly deployments with full audit traceability.

Platform Architecture: We deploy a GitLab Ultimate-based CI/CD platform on AWS GovCloud (IL4) with the following pipeline stages: (1) Pre-commit hooks enforcing code style and secret scanning; (2) SAST using SonarQube and Semgrep; (3) Software Composition Analysis (SCA) with Dependency-Track and OSS Index; (4) Container image building using Kaniko on hardened runners; (5) Image scanning with Anchore Enterprise against DoD Iron Bank policies; (6) IaC scanning with Checkov; (7) DAST in staging using OWASP ZAP; (8) Performance testing; and (9) Production deployment with automated rollback triggers.

Policy as Code: We implement Open Policy Agent (OPA) Gatekeeper for Kubernetes admission control, enforcing 47 custom policies aligned to NIST SP 800-204 (Security Strategies for Microservices-Based Application Systems). All policy violations are documented in a real-time compliance dashboard accessible to the agency's AO.

SBOM Generation: Every build artifact generates a CycloneDX-format SBOM with component inventory, license analysis, and vulnerability correlation. SBOMs are stored in a dedicated registry and accessible via REST API for supply chain risk analysis and STIG compliance mapping.""",

        """{company} will establish a mature DevSecOps capability for {agency} aligned to the Software Acquisition Pathway (SWP) defined in DoDI 5000.87 and the DoD Software Modernization Strategy. Our approach establishes a Product Delivery Team (PDT) model with embedded security and compliance engineers, eliminating the handoff gaps that create security debt in traditional waterfall SDLC processes.

Agile Delivery Framework: We operate on 2-week Sprints using SAFe (Scaled Agile Framework) for coordinating 4 Agile Release Trains (ARTs), each comprising 6-8 team members. PI Planning events occur quarterly, aligning development priorities with agency mission requirements and compliance milestones. Our Definition of Done (DoD) includes automated security gate passage as a hard requirement.

Container Platform: We deploy a hardened OpenShift Container Platform 4.x on-premises cluster (3 master + 6 worker nodes) with Red Hat Advanced Cluster Security (RHACS) for container security, network policy enforcement, and runtime threat detection. All base images are sourced from the DoD Iron Bank and validated against the Container Hardening Guide.

Automated Compliance: We integrate our DevSecOps pipeline with the agency's GRC tool (Archer) via REST API, enabling real-time control test results to flow into continuous ATO evidence packages. InSpec profiles derived from DISA STIGs automate 78% of control testing, reducing manual assessment effort by 65 person-hours per Sprint.""",

        """{company} delivers a comprehensive DevSecOps transformation program for {agency} that addresses the full software factory lifecycle from developer workstations to production deployment. Our proven methodology has been validated across 3 prior federal agency implementations, achieving CMMC Level 2 certification and FedRAMP-authorized infrastructure within 90 days.

Source Control and Collaboration: We deploy a hardened GitLab instance with branch protection rules enforcing mandatory code review (minimum 2 approvers for main branch), commit signing with GPG keys, and integration with the agency's PIV/CAC authentication via SAML 2.0. All code changes generate immutable audit logs satisfying NIST AU-2 through AU-12 control requirements.

Infrastructure Automation: Terraform Cloud Enterprise manages all infrastructure provisioning with Sentinel policy-as-code enforcement, remote state storage with encryption, and workspace-based environment promotion (dev→staging→production). Ansible Tower automates OS hardening playbooks (1,200+ tasks) run quarterly or triggered by STIG update releases.

Observability and Feedback Loops: We implement OpenTelemetry-based distributed tracing across all microservices, feeding into Grafana dashboards that display DORA metrics (Deployment Frequency, Lead Time for Changes, Change Failure Rate, MTTR). These metrics are reviewed in monthly Program Review meetings with agency leadership, establishing a data-driven continuous improvement culture.""",
    ],
    "ai_ml": [
        """{company} proposes an enterprise AI/ML Platform for {agency} that enables mission-critical artificial intelligence capabilities while maintaining full compliance with OMB M-25-21 (Responsible Use of AI in the Federal Government) and NIST AI Risk Management Framework (AI RMF 1.0). Our platform provides end-to-end ML lifecycle management from data ingestion through model deployment and monitoring.

Platform Architecture: We deploy a Kubernetes-native ML platform using Kubeflow (v1.8) on AWS GovCloud, providing: (1) JupyterHub for collaborative data science with PIV/CAC authentication; (2) MLflow for experiment tracking, model versioning, and artifact management; (3) Feast feature store for consistent feature engineering across training and inference; (4) Seldon Core for model serving with A/B testing and canary deployments; and (5) Evidently AI for production model monitoring with automated drift detection.

AI Governance: We implement a comprehensive AI governance framework with a Model Risk Management (MRM) process that includes: algorithmic impact assessments prior to deployment, bias detection across 12 protected demographic categories using Fairlearn, explainability reports using SHAP values for all production models, and quarterly model performance reviews with the agency's AI Ethics Board.

Data Pipeline: We build federated learning pipelines that enable model training across multiple classified enclaves without centralizing sensitive data. Our differential privacy implementation (epsilon=1.0) ensures individual records cannot be reverse-engineered from model parameters, satisfying Privacy Act of 1974 requirements for AI systems processing PII.""",

        """{company} delivers a mission-focused AI/ML capability for {agency} centered on three high-priority use cases identified in the agency's AI Strategy: (1) Natural Language Processing for automated document classification and information extraction; (2) Anomaly detection for security monitoring and fraud prevention; and (3) Predictive analytics for resource optimization and demand forecasting.

Model Development: Our team of 8 cleared data scientists (all holding Secret clearances) develops models using a structured MLOps pipeline. All training data undergoes rigorous data governance review including data lineage documentation, bias assessment, and privacy impact analysis. Models are trained on AWS SageMaker with integrated hyperparameter optimization and automatic model tuning.

FedRAMP Compliance: Our AI platform operates within a FedRAMP High-authorized environment (AWS GovCloud, Authorization P-ATO: ATL-2018-0025). All model artifacts are stored in encrypted S3 buckets with object-level versioning and immutable audit logging. Model cards following the Google Model Card format are published for every production model, documenting intended use, limitations, performance metrics, and fairness evaluations.

Human-in-the-Loop: For all consequential AI decisions (determinations affecting individuals, resource allocation above $1M threshold, mission-critical recommendations), we implement mandatory human review workflows with full decision audit trails. Our AI oversight dashboard provides agency leadership real-time visibility into AI system performance, usage patterns, and anomalous behavior flags.""",

        """{company} offers {agency} a mature, cloud-native AI/ML platform with proven scalability to process 50TB+ of agency data assets. Our approach prioritizes explainability, security, and continuous compliance to ensure AI systems remain trustworthy throughout their operational lifecycle.

Data Architecture: We build a secure data lakehouse architecture on AWS using S3 (data lake), AWS Glue (ETL and data catalog), and Amazon Redshift (analytical warehouse). All data is tagged with CUI classification markings automatically using our custom classification ML model trained on agency data taxonomy. Data quality monitoring via Great Expectations provides automated data contracts that prevent model degradation from upstream data changes.

MLOps Pipeline: Our CI/CD pipeline for ML extends traditional DevSecOps practices with ML-specific stages: data validation, feature engineering, model training, evaluation gate (performance + fairness thresholds), security scanning of model artifacts, and staged rollout with automated rollback. The pipeline processes 15+ model training runs daily, with full provenance tracking from raw data to deployed model.

Edge Deployment: For disconnected and tactical edge environments, we package models using ONNX format for hardware-agnostic deployment, achieving inference latency under 50ms on NVIDIA Jetson AGX Orin platforms. Model synchronization to edge nodes uses a delta-update protocol that reduces bandwidth requirements by 94% compared to full model transfers.""",
    ],
    "help_desk": [
        """{company} proposes a comprehensive IT Service Management (ITSM) and Help Desk solution for {agency} delivering Tier 1, 2, and 3 support services with guaranteed SLA performance. Our solution is built on ITIL v4 best practices and powered by ServiceNow ITSM (FedRAMP High authorized) to provide a seamless, measurable user experience.

Service Catalog: We deliver 47 standardized service offerings covering: workstation support (Windows 10/11, macOS), network connectivity issues (VPN, remote access), application support (Microsoft 365, custom agency applications), mobile device management (Microsoft Intune, Apple Business Manager), and hardware procurement and lifecycle management. All services have documented resolution time SLAs: P1 (4 hours), P2 (8 hours), P3 (24 hours), P4 (72 hours).

Staffing Model: Our Help Desk team comprises 25 FTE positions: 12 Tier 1 agents (CompTIA A+ certified, SECRET clearance), 8 Tier 2 technicians (CompTIA Network+/Security+ certified), and 5 Tier 3 engineers (Microsoft MCSE/MCSA certified, holds active SECRET clearances). All personnel undergo quarterly security awareness training and annual background re-investigations.

Knowledge Management: We deploy a centralized knowledge base in ServiceNow containing 800+ documented resolution procedures, reducing mean time to resolve (MTTR) by 35% through first-call resolution. Knowledge articles are reviewed quarterly and updated within 24 hours of system changes. Self-service portal deflects 28% of tickets, reducing cost per ticket by $12.40.""",

        """{company} delivers an enterprise-class Help Desk and End User Computing (EUC) support program for {agency} leveraging our ISO 20000-certified service delivery processes and a 100% US-based workforce with required security clearances. Our solution integrates AI-assisted ticket routing with human expertise to deliver superior user experience metrics.

Omnichannel Support: Users access support through phone (toll-free, <30 second average speed of answer), web portal (24/7 self-service), email (2-hour response SLA), and live chat (integrated into Microsoft Teams). Our ACD (Automatic Call Distribution) system uses NLP-based intent classification to route 89% of calls directly to the appropriate support tier without transfer, reducing handle time by 22%.

Continuous Improvement: We operate a formal CSI (Continual Service Improvement) program with monthly Service Review Boards presenting trend analysis, root cause analysis for recurring incidents, and improvement roadmap updates. Our Shift-Left initiative trains Tier 1 agents to resolve 65% of issues without escalation, measured monthly against baseline and reported in the Monthly Service Report (MSR) delivered to the Contracting Officer Representative (COR).

Security Compliance: All Help Desk operations comply with agency security policies. Technicians access systems exclusively through a privileged access workstation (PAW) architecture with CyberArk session recording. Remote support sessions use Bomgar (BeyondTrust) with automatic session termination after 30 minutes of inactivity and full session recording retained for 2 years in immutable storage.""",

        """{company} offers {agency} a modernized O&M and Help Desk solution that reduces operational costs while improving service quality through intelligent automation and a high-retention workforce strategy. Our 8(a) certified team brings 12 years of continuous federal IT service delivery experience.

Workforce Strategy: We address the federal IT talent challenge through a structured career development program that certifies 100% of Help Desk staff in at least one industry certification annually (CompTIA, Microsoft, ITIL). Our average employee tenure of 4.2 years (vs. industry average of 1.8 years) results in superior institutional knowledge and reduced transition overhead. All positions are filled with US citizens holding minimum SECRET clearances.

Automation and AI: We deploy an AI-powered virtual agent (ServiceNow Virtual Agent platform) trained on agency-specific FAQs and procedures, deflecting 32% of routine requests without human intervention. Automated remediation scripts resolve 41% of Tier 1 issues (password resets, account unlocks, printer issues) with zero-touch resolution, reducing labor cost by $280,000 annually at current ticket volumes.

Asset Management: We implement a Configuration Management Database (CMDB) using ServiceNow Discovery, maintaining 98%+ accuracy for all 8,400+ managed assets. Automated discovery runs every 4 hours, detecting unauthorized hardware additions and configuration drift within a single business day. Asset lifecycle management tracks warranty status, refresh cycles, and disposal in compliance with NIST SP 800-88 media sanitization requirements.""",
    ],
}

_MGMT_TEMPLATES: Dict[str, List[str]] = {
    "cloud_migration": [
        """{company} will manage the cloud migration program for {agency} using a hybrid PMO structure that combines program-level oversight with Agile team execution. Our Program Manager (PM), {pm_name}, holds a PMP certification and has led 3 prior federal cloud migration programs totaling over $47M in contract value.

Program Governance: We establish a Program Management Office (PMO) with weekly status meetings (30-minute standup), bi-weekly Steering Committee briefings for agency leadership, and monthly Program Review Boards (PRBs) with the Contracting Officer (CO). All program documentation is maintained in SharePoint (O365 Government) with role-based access control.

Risk Management: We maintain a Risk Register updated weekly, with 23 pre-identified risks across 5 categories (technical, schedule, resource, external, compliance). For each risk, we document probability (1-5), impact (1-5), risk score, mitigation strategy, and contingency plan. Critical risks (score ≥ 15) are escalated to agency leadership within 24 hours.

Staffing Plan: The program is staffed with 18 FTE: 1 PM, 1 Deputy PM, 2 Cloud Architects, 3 Cloud Engineers, 2 DevSecOps Engineers, 2 Security/Compliance Specialists, 1 Data Architect, 2 Migration Specialists, 1 Change Management Lead, 1 Training Specialist, and 2 Project Coordinators. All key personnel have been pre-identified and are available within 14 days of contract award.

Transition Plan: We commit to a 30-day phase-in with no service disruption. During phase-in, our team shadows current operations, documents undocumented processes, and validates our program management tools against agency standards.""",

        """{company} applies a disciplined, risk-informed program management approach to deliver cloud migration outcomes for {agency} on time and within budget. Our PM, {pm_name}, maintains a PgMP certification and direct Sponsor communication authority to resolve issues without delay.

Quality Assurance: We maintain an ISO 9001:2015-certified Quality Management System (QMS) with a dedicated Quality Assurance (QA) function performing monthly process audits, deliverable reviews against acceptance criteria, and customer satisfaction surveys (target: >4.2/5.0 satisfaction score). Non-conformances are tracked to root cause and closed within defined timeframes.

Subcontractor Management: We engage 2 mentor-protégé subcontractors for specialized capabilities (network engineering, change management), each with defined SOWs, performance metrics, and monthly reporting requirements. Prime contractor retains 51%+ of work per small business subcontracting plan commitments.

Performance Measurement: We implement Earned Value Management (EVM) at the Task Order level, reporting CPI and SPI monthly. Threshold values of CPI/SPI < 0.90 trigger corrective action plans within 5 business days. Our EVM system is integrated with agency financial systems for transparent cost visibility.""",
    ],
    "cybersecurity": [
        """{company} manages the cybersecurity assessment program for {agency} using a security-clearance-verified PMO with direct reporting authority to the agency's Chief Information Security Officer (CISO). Our PM, {pm_name}, holds a CISSP certification and has managed 5 prior cybersecurity assessment contracts for DoD and civilian agencies.

Program Controls: We utilize a formal Integrated Master Schedule (IMS) maintained in Microsoft Project, baseline-controlled and updated bi-weekly. The IMS is linked to our risk register and resource plan, enabling early identification of schedule impacts. Contractor Performance Assessment Reporting System (CPARS) ratings are monitored quarterly, with corrective actions initiated immediately upon any rating below "Satisfactory."

Cleared Personnel Management: All cybersecurity personnel maintain active clearances appropriate to the systems accessed (Secret minimum, TS/SCI for sensitive assessments). We manage the clearance lifecycle through our Facility Security Officer (FSO), ensuring no cleared personnel gaps exceed 48 hours. Interim clearance procedures are documented for new hires with existing federal clearance investigations.

Communication Plan: Daily operational reports are delivered to the agency ISSO via encrypted email. Weekly status reports (1-page format) are provided to the CISO. Monthly Program Review Packages include trend analysis, findings summary, and remediation progress metrics. All sensitive findings are transmitted via SIPRNet or equivalent government-furnished secure channel.""",

        """{company} provides rigorous program management oversight for the cybersecurity assessment and hardening contract at {agency}, ensuring all assessment activities are properly planned, executed, and documented to satisfy RMF Step 4 (Assessment) requirements and support ATO package submission.

Independent Quality Review: We establish a separate Independent Verification and Validation (IV&V) function staffed by our CISA-certified engineers, providing objective assessment of findings accuracy, control test methodology, and documentation quality. IV&V findings are reported directly to the agency CO to ensure independence from the assessment team.

Transition Planning: We conduct a structured 45-day transition for all cybersecurity tool accesses, documentation handoffs, and stakeholder relationships. Our Knowledge Transfer Plan ensures 100% continuity of security monitoring and incident response capability during transition, with parallel operations for the first 30 days.

Deliverable Management: All CDRLs are tracked in a Deliverable Action Items List (DAIL) with submission dates, review cycles, and approval status. We deliver 100% of CDRLs on schedule, with a quality review gate (internal peer review + PM approval) before each submission.""",
    ],
    "devsecops": [
        """{company} structures the DevSecOps program for {agency} around an empowered Product Owner model, where agency personnel participate as Product Owners in all Sprint ceremonies to ensure mission alignment throughout development. Our PM, {pm_name}, holds SAFe SPC (SAFe Practice Consultant) certification and has established DevSecOps cultures at 4 prior federal organizations.

Agile Governance: We implement lightweight governance appropriate to Agile delivery: a Scaled Agile Steering Committee meets quarterly for portfolio-level prioritization, Program Increment (PI) Planning occurs every 12 weeks, and weekly team-level coordination occurs through Scrum-of-Scrums. We maintain an Epic-Feature-Story hierarchy in Jira (FedRAMP-authorized) with full traceability to requirements.

Change Management: Our dedicated Organizational Change Management (OCM) program uses the Prosci ADKAR model to guide agency personnel through the DevSecOps transformation. We conduct 8 workshops, provide 40 hours of developer training on new tools and practices, and establish a Developer Experience (DX) team to reduce friction in adopting new processes.

Security Culture: We institutionalize security through "Security Champions" — one designated engineer per team who receives advanced security training, participates in threat modeling sessions, and serves as the team's security advocate. Security Champions attend monthly cross-team forums to share findings and propagate best practices.""",

        """{company} manages the DevSecOps transformation program for {agency} using a servant-leadership model that maximizes team autonomy while maintaining program-level accountability. Our lightweight governance framework ensures compliance without bureaucratic overhead that impedes delivery velocity.

Vendor Management: We manage 3 SaaS platform vendors (GitLab, Snyk, Artifactory) under our unified vendor management framework, conducting quarterly business reviews (QBRs) with each vendor, tracking SLA performance, and escalating issues to executive level when SLAs are missed for 2 consecutive months.

Training Program: We deliver a 40-hour DevSecOps Practitioner curriculum covering secure coding practices (OWASP Top 10), CI/CD pipeline operation, container security, and IaC best practices. Training is delivered in a blended format (16 hours in-person, 24 hours self-paced e-learning) and repeated semi-annually for new team members.

Metrics and Reporting: We report 6 DORA metrics and 8 security metrics monthly in our Program Health Dashboard: Deployment Frequency, Lead Time, Change Failure Rate, MTTR, Open Critical Vulnerabilities (target: 0), Mean Time to Patch (target: <30 days), SBOM coverage (target: 100%), and License Compliance Rate (target: 100%).""",
    ],
    "ai_ml": [
        """{company} manages the AI/ML platform program for {agency} through a dedicated AI Program Management Office (AI-PMO) that coordinates technical delivery with ethical oversight, compliance activities, and stakeholder communication. Our PM, {pm_name}, is a certified PMP and holds the Google Professional Machine Learning Engineer certification.

AI Governance Committee: We establish an AI Governance Committee (AGC) co-chaired by agency CTO and {company} Program Manager, meeting monthly to review: model performance metrics, bias and fairness assessments, incident reports, new model deployment approvals, and alignment with updated OMB AI guidance. Committee minutes and decisions are documented in a governance log accessible to oversight bodies.

Data Stewardship: We designate a Data Steward for each data domain, responsible for maintaining data dictionaries, monitoring data quality metrics, approving data access requests, and ensuring compliance with Privacy Act system of records notices (SORNs). Data stewardship activities are documented in monthly Data Quality Reports.

Risk Management: AI-specific risks (model drift, data poisoning, adversarial attacks, explainability gaps) are documented in a dedicated AI Risk Register with probability-impact scoring and mitigation strategies. We conduct quarterly AI Red Team exercises simulating adversarial attacks on production models, with findings remediated within 60 days.""",

        """{company} applies responsible AI program management principles to govern the AI/ML platform delivery for {agency}, ensuring that all AI systems are developed and deployed with appropriate human oversight, transparency, and accountability. Our approach aligns with the White House Executive Order 14110 on Safe, Secure, and Trustworthy Artificial Intelligence.

Responsible AI Framework: We implement a 4-gate model review process: (1) Data Ethics Gate — privacy impact assessment and bias baseline; (2) Algorithm Gate — technical performance thresholds and explainability requirements; (3) Security Gate — adversarial robustness testing and supply chain review; (4) Deployment Gate — human oversight plan and rollback criteria. All gates require documented sign-off from agency representatives.

Stakeholder Engagement: We conduct monthly Stakeholder Working Groups with representatives from agency mission offices, IT, security, legal, and privacy functions. Working Group outputs inform backlog prioritization, governance policy updates, and user acceptance testing planning. We maintain a Stakeholder Register with communication preferences and engagement frequency for 23 identified stakeholders.

Intellectual Property: All custom AI models, training datasets, and pipeline code developed under this contract are delivered as government-owned intellectual property with full documentation enabling internal maintenance or re-competition. No proprietary model weights or closed-source components are embedded without agency approval and documented license terms.""",
    ],
    "help_desk": [
        """{company} manages the Help Desk and O&M contract for {agency} using a Transition-In Management Plan and an Operations Management Plan that together provide end-to-end lifecycle governance of all service delivery activities. Our PM, {pm_name}, is an ITIL v4 Managing Professional certified and has managed similar programs serving 10,000+ end users.

Service Level Management: We establish a formal SLA Management process with weekly SLA exception reviews (any metric below threshold triggers a 48-hour corrective action plan), monthly Customer Service Reviews with agency leadership, and quarterly Service Improvement Plans addressing systematic performance gaps. All SLA data is accessible via a real-time dashboard shared with the COR.

Workforce Retention: We implement a comprehensive workforce retention strategy including: competitive base salaries benchmarked to OPM GS equivalents, annual performance bonuses tied to customer satisfaction scores, quarterly skills development budget ($2,500 per technician annually), and a defined career ladder from Help Desk Analyst to Senior Systems Engineer. Our target voluntary turnover rate is <10% annually.

Security Management: We conduct annual security refresher training for all personnel, enforce mandatory reporting of security incidents within 1 hour of discovery, and maintain an incident response retainer with our cybersecurity subcontractor for escalated security events. All remote access sessions are recorded and retained for 1 year in compliance with NIST SP 800-137 continuous monitoring requirements.""",

        """{company} delivers reliable, compliant O&M services to {agency} through a mature service management framework that has achieved Customer Satisfaction scores of 4.5/5.0 or above on 3 consecutive CPARS evaluations. Our PM, {pm_name}, maintains ITIL v4 Strategic Leader certification and direct accountability for all KPIs.

Cost Management: We apply Lean principles to service delivery, continuously analyzing ticket data to identify automation opportunities and knowledge base gaps. Our documented cost optimization plan projects $340,000 in efficiency savings over the 5-year IDIQ through: increased self-service deflection (target: 35%), automation of repetitive tasks (target: 45% of Tier 1 tickets), and reduced rework through improved first-call resolution (target: 78% FCR).

Business Continuity: We maintain a fully documented Business Continuity Plan (BCP) and Disaster Recovery Plan (DRP) for all Help Desk services, with recovery time objectives (RTO) of 4 hours for full service restoration. Backup staffing contracts with 2 cleared staffing agencies ensure we can surge capacity by 50% within 48 hours during peak demand or staff attrition events.

Vendor Relationships: We manage 4 software vendor relationships (ServiceNow, Microsoft, Bomgar, SolarWinds) with documented support contracts, escalation procedures, and license optimization reviews. Annual license true-up exercises ensure we pay only for active users, generating an average of $85,000 in annual savings through right-sizing.""",
    ],
}

_PP_TEMPLATES: Dict[str, List[str]] = {
    "cloud_migration": [
        """{company} brings directly relevant past performance from 3 completed and 1 active federal cloud migration programs totaling $89.4M in contract value. Below are our most relevant references:

Contract 1 — DOD DISA Cloud Modernization (2022-2024, $28.5M): We migrated 120+ legacy applications to AWS GovCloud for a DoD agency, achieving IL4 ATO for all workloads within 18 months. Key outcomes: 99.97% uptime maintained throughout migration, 34% reduction in infrastructure cost, and zero security incidents during data migration. Delivery was completed 45 days ahead of schedule.

Contract 2 — DHS CBP Application Modernization (2021-2023, $18.7M): We containerized and migrated 47 mission-critical border management applications to a Kubernetes platform, enabling daily deployment cycles (vs. quarterly releases previously). CISA conducted an assessment and recognized this program as a Federal Cloud Modernization Success Story.

Contract 3 — HHS CMS Cloud Foundation (2023-2024, $22.2M, Active): We are currently providing cloud foundation services for a Health and Human Services agency, managing a 3,200-node AWS GovCloud environment processing 2.1 billion API calls monthly. Our team maintains a FedRAMP High ATO and has achieved continuous ATO (cATO) designation, reducing re-assessment burden by 70%.

All references are available for agency contact and have provided written authorization for past performance disclosure. Our CPARS ratings on these contracts are: Exceptional (2), Very Good (1), with zero Marginal or Unsatisfactory ratings.""",

        """{company} demonstrates directly relevant experience through the following past performance references, each reflecting the technical complexity and compliance requirements of this solicitation:

Federal Cloud Infrastructure Program (2020-2024, $31.2M, DoE) — Designed and built an IL5 cloud platform for a Department of Energy laboratory, migrating 89 research applications including 3 classified workloads. Implemented a hybrid-cloud architecture spanning AWS GovCloud and on-premises HPC clusters. Achieved ATO with 0 open CAT 1 or CAT 2 findings at initial assessment. Performance rating: Exceptional.

Agency IT Modernization Contract (2022-2024, $14.5M, VA) — Migrated the Department of Veterans Affairs regional benefits processing system to AWS GovCloud, supporting 2.8M veterans. Zero downtime achieved through blue-green deployment strategy. Delivered 28% application performance improvement measured by page load time. CPARS: Very Good.

Cloud Center of Excellence (2023-Present, $9.8M, GSA) — Established a Cloud Center of Excellence for a civilian agency, providing cloud governance, cost optimization, and migration support to 14 internal program offices. Achieved $2.1M in year-1 cloud cost optimization through Reserved Instance and Savings Plan optimization. Performance rating: Exceptional.""",
    ],
    "cybersecurity": [
        """{company} presents the following past performance references demonstrating our cybersecurity assessment and hardening capabilities for federal agencies:

DoD Component Cybersecurity Assessment (2022-2024, $12.8M) — Conducted a comprehensive RMF-based security assessment of 3 major DoD information systems, identifying 847 vulnerabilities (23 Critical, 156 High, 412 Medium, 256 Low). Remediated 94% of Critical and High findings within SLA. Delivered complete ATO package to DAA within 12 months, achieving 3-year ATO. CPARS: Exceptional.

DHS CISA Security Hardening (2021-2023, $9.4M) — Performed STIG compliance remediation for 1,200+ systems across 8 data centers, achieving 97.3% compliance (from 61% baseline) within 18 months. Implemented automated SCAP scanning and continuous monitoring dashboard. Conducted 4 penetration testing engagements with zero critical findings escaping to production. CPARS: Very Good.

Intelligence Community Cyber Defense (2023-Present, $18.2M, Active) — Providing 24/7 SOC services for an IC component agency, managing 15 billion log events daily through Splunk Enterprise Security. Achieved MTTD of 8.3 minutes (target: 15 minutes) and MTTR of 47 minutes (target: 4 hours). Zero successful APT intrusions since program inception. CPARS: Exceptional.

All key personnel proposed for this contract performed on one or more of the above references, ensuring direct institutional knowledge transfer to the agency.""",

        """{company} has the following directly relevant past performance validating our cybersecurity assessment approach:

Federal Agency Penetration Testing BPA (2020-2024, $7.2M cumulative, DoD) — Executed 28 penetration testing engagements across 14 federal clients under an agency-wide BPA. Average findings per engagement: 4.2 Critical, 18.7 High vulnerabilities. 100% of engagements delivered final reports within the contracted 30-day window. No client-reported quality issues. CPARS: Exceptional (3 years).

CMMC Readiness and Certification Support (2022-2024, $5.8M) — Prepared 47 defense contractors for CMMC Level 2 certification, achieving 100% first-attempt certification rate. Developed a proprietary CMMC gap assessment methodology now used across 3 additional contracts. Customer satisfaction: 4.8/5.0.

Zero Trust Architecture Implementation (2023-Present, $11.3M, DHS) — Designing and implementing a NIST SP 800-207 compliant ZTA for a DHS component, including identity-centric access control, micro-segmentation, and continuous validation. Phase 1 (Identity Pillar) completed 3 weeks ahead of schedule with 100% of success criteria met. CPARS: Exceptional.""",
    ],
    "devsecops": [
        """{company} presents 3 directly relevant past performance references demonstrating our ability to establish and mature DevSecOps capabilities in federal environments:

DoD Software Factory Establishment (2022-2024, $24.1M) — Established a Platform One-style software factory for a DoD program office, enabling 12 software programs to adopt continuous delivery. Deployment frequency improved from quarterly to daily for 8 programs. Security vulnerability backlog reduced by 78% in year 1. SBOM generation automated for 100% of releases. CPARS: Exceptional.

Civilian Agency CI/CD Modernization (2021-2023, $11.6M, HHS) — Transformed the software delivery process for a major HHS program from waterfall (18-month release cycles) to Agile CI/CD (2-week sprints, weekly releases). Implemented GitLab Ultimate pipeline with SAST, DAST, SCA, and IaC scanning integrated into every commit. Change failure rate reduced from 23% to 4.1%. CPARS: Very Good.

GSA Cloud.gov Platform Engineering (2023-Present, $8.9M, Active) — Providing platform engineering support for a civilian agency's containerized application platform, managing 180+ deployed applications across development, staging, and production environments. Platform availability: 99.95% SLA achieved 14 consecutive months. Developer satisfaction: 4.6/5.0. CPARS: Exceptional.

Our proposed PM and Lead DevSecOps Architect were key personnel on Contract 1 above, bringing direct institutional knowledge to this engagement.""",

        """{company} references the following past performance demonstrating our DevSecOps expertise and federal delivery track record:

Army Agile Software Development (2021-2024, $16.8M) — Delivered 7 software capability releases for an Army program using SAFe Agile methodology, all within schedule and cost baseline. Automated security gate integrated into pipeline detected and prevented 3 zero-day vulnerabilities from reaching production. All releases included compliant SBOMs and received AO sign-off within 5 business days of release. CPARS: Exceptional.

NIST DevSecOps Maturity Assessment (2022-2023, $2.1M) — Assessed DevSecOps maturity of 22 federal agencies against the NIST DevSecOps Reference Architecture, delivering individualized roadmaps and a cross-agency benchmarking report. 19 of 22 agencies implemented priority recommendations within 6 months of report delivery.

Navy Software Assurance (2023-Present, $13.4M, Active) — Providing software assurance and pipeline security engineering for a Navy tactical system program. Identified and remediated 2,847 vulnerabilities across 1.2M lines of code, reducing technical debt score by 61%. All pipeline gates pass with zero CAT 1 finding exceptions. CPARS: Exceptional.""",
    ],
    "ai_ml": [
        """{company} presents past performance references demonstrating our AI/ML platform delivery capabilities in mission-critical federal environments:

DoD AI Enablement Platform (2022-2024, $31.5M) — Designed and deployed an enterprise MLOps platform for a DoD combatant command, enabling 14 AI programs to deploy models 8x faster than the previous manual process. Platform processes 40TB+ of ISR data monthly. Model drift detection system prevented 3 production degradation events. All models include documented model cards and fairness assessments. CPARS: Exceptional.

HHS NLP Document Processing (2021-2023, $8.7M) — Developed and deployed 6 NLP models for automated processing of Medicare/Medicaid documentation, reducing manual review time by 64% (saving 22,000 person-hours annually). Model performance: 94.3% F1 score on held-out test set. Full explainability implementation using SHAP values for CMS auditor review. CPARS: Very Good.

DoE Predictive Maintenance AI (2023-Present, $14.2M, Active) — Building predictive maintenance ML models for critical energy infrastructure, achieving 91% precision in identifying equipment failures 14 days in advance. Federated learning implementation protects sensitive facility data while enabling cross-site model improvement. CPARS: Exceptional (Year 1).

Our Chief AI Scientist proposed for this contract developed the NLP architecture in Reference 2 and the federated learning framework in Reference 3, ensuring direct technical continuity.""",

        """{company} demonstrates directly relevant AI/ML experience through the following federal program references:

Intelligence Analysis Automation (2021-2024, $22.4M, DoD) — Deployed computer vision and NLP models to automate intelligence report processing, reducing analyst review time by 58% while maintaining 99.1% agreement rate with human analysts. Adversarial robustness testing conducted quarterly with zero successful evasion attacks detected. System processes 180,000+ documents daily. CPARS: Exceptional.

Fraud Detection AI Platform (2022-2023, $6.3M, HHS) — Implemented ensemble ML models for Medicare fraud detection, identifying $47.3M in fraudulent claims in Year 1 (ROI: 7.5x contract value). Bias assessment confirmed equal performance across all 8 demographic groups. Full audit trail for every model decision satisfies legal discovery requirements. CPARS: Exceptional.

VA Predictive Analytics (2023-Present, $9.1M, Active) — Building predictive models for veteran health risk stratification and resource demand forecasting. 14 models currently in production, all with documented governance artifacts and COR approval. Mental health risk model achieving 87% sensitivity on holdout validation data, outperforming published clinical benchmark. CPARS: Exceptional (mid-term).""",
    ],
    "help_desk": [
        """{company} provides the following past performance demonstrating our IT Help Desk and O&M service delivery excellence:

Federal Agency Enterprise Help Desk (2019-2024, $42.1M, DoD) — Delivered Tier 1-3 Help Desk services for a DoD component agency serving 18,500 users across 23 locations. Maintained 99.1% SLA compliance across all P1-P4 metrics for 5 consecutive years. Customer satisfaction (CSAT) averaged 4.4/5.0. Implemented AI-powered ticket routing reducing average handle time by 18%. CPARS: Exceptional (4 consecutive years).

HHS End User Computing Support (2021-2023, $14.7M) — Provided EUC support for a health agency during COVID-19 remote work surge, scaling from 3,200 to 8,100 supported users within 45 days without SLA degradation. Deployed Microsoft Intune MDM to 6,200 personal devices, achieving 97% compliance with security policies within 90 days. CPARS: Exceptional.

GSA Field IT Support (2022-Present, $9.8M, Active) — Delivering Tier 2-3 field support to 47 GSA facilities nationwide, supporting 12,400 users. First Call Resolution rate: 71.3% (target: 65%). Asset lifecycle management maintains 99.4% CMDB accuracy. All P1 incidents resolved within 4-hour SLA 97.8% of the time. CPARS: Exceptional.

Our proposed Help Desk Manager, {pm_name}, served as Service Delivery Manager on Contract 1 for the full 5-year period of performance, providing direct institutional knowledge of large-scale federal Help Desk operations.""",

        """{company} references the following past performance validating our O&M and Help Desk service delivery approach for federal agencies:

VA Medical Center IT Support (2020-2024, $19.3M) — Provided 24/7/365 Help Desk and field support for 4 VA medical centers, supporting 7,200 clinical and administrative users with zero tolerance for downtime during patient care hours. Achieved 99.7% availability for critical clinical systems. All staff maintain PIV/CAC compliance and annual HIPAA training certification. CPARS: Exceptional.

USCIS Enterprise Service Desk (2021-2023, $11.2M) — Transitioned and operated the USCIS enterprise service desk serving 22,000 users, executing a zero-defect transition that maintained full service continuity from day 1. Implemented ServiceNow ITSM platform migration during operations without SLA impact. CPARS: Very Good.

DoE National Lab IT Operations (2023-Present, $16.7M, Active) — Supporting IT operations for a national laboratory complex with classified and unclassified networks, managing 9,400 endpoints including specialized scientific computing workstations. Security patching compliance: 98.7% within 30-day SLA. CPARS: Exceptional (Year 1). """,
    ],
}


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(count: int = 50, seed: int = 42) -> List[Dict[str, Any]]:
    """Generate synthetic GovCon proposals.

    Returns list of dicts, each with keys: opportunity, volumes, sections.
    No real company, contact, or address data is used.
    """
    _RNG.seed(seed)
    proposals: List[Dict[str, Any]] = []

    proposals_per_archetype = count // len(_ARCHETYPES)

    for arch_idx, archetype in enumerate(_ARCHETYPES):
        naics_code, _ = _NAICS_BY_ARCHETYPE[archetype]
        agency_pool = _AGENCIES.copy()
        _RNG.shuffle(agency_pool)

        for i in range(proposals_per_archetype):
            global_idx = arch_idx * proposals_per_archetype + i
            opp_id = _uid()
            agency_name, sub_agency, agency_code = agency_pool[i % len(agency_pool)]
            sol_num = _sol_number(agency_code, naics_code, (arch_idx * 100) + i + 1)

            company_name = _company_name(global_idx)
            pm_first, pm_last = _person()
            cm_first, cm_last = _person()
            pm_name = f"{pm_first} {pm_last}"
            cm_name = f"{cm_first} {cm_last}"
            addr_street, addr_city, addr_state, addr_zip = _address()
            pm_email = _email(pm_first, pm_last, company_name)
            cage = _cage_code()
            uei = _uei()

            title_map = {
                "cloud_migration": "Cloud Migration and Modernization Support Services",
                "cybersecurity": "Cybersecurity Assessment, Testing, and Hardening Services",
                "devsecops": "DevSecOps Platform Implementation and Engineering Support",
                "ai_ml": "Artificial Intelligence and Machine Learning Platform Development",
                "help_desk": "IT Help Desk and End User Computing Support Services",
            }

            val_low = _RNG.choice([1_000_000, 2_000_000, 3_000_000, 5_000_000])
            val_high = val_low * _RNG.choice([2, 3, 5])
            due_offset = _RNG.randint(30, 120)

            opp = {
                "id": opp_id,
                "solicitation_number": sol_num,
                "title": title_map[archetype],
                "agency": agency_name,
                "sub_agency": sub_agency,
                "naics_code": naics_code,
                "estimated_value_low": float(val_low),
                "estimated_value_high": float(val_high),
                "proposal_type": _RNG.choice(_PROPOSAL_TYPES),
                "status": "submitted",
                "bid_decision": "go",
                "bid_decision_rationale": f"Strong past performance alignment with {archetype.replace('_', ' ')} requirements",
                "capture_manager": cm_name,
                "proposal_manager": pm_name,
                "domain": _ARCHETYPE_DOMAIN[archetype],
                "classification": _CLASSIFICATION,
                "due_date": _date(due_offset),
                "due_time": "17:00",
                "set_aside_type": _RNG.choice(_SET_ASIDE_TYPES),
                "created_by": "synthetic_demo",
                "created_at": _ts(-_RNG.randint(30, 90)),
                "updated_at": _ts(-_RNG.randint(0, 29)),
                "amendment_count": 0,
                "question_count": _RNG.randint(3, 15),
            }

            # 3 volumes per proposal
            vol_types = [
                ("technical", f"Volume I: Technical Approach — {title_map[archetype]}"),
                ("management", f"Volume II: Management Approach — {title_map[archetype]}"),
                ("past_performance", f"Volume III: Past Performance — {title_map[archetype]}"),
            ]
            volumes = []
            sections = []
            for v_num, (v_type, v_title) in enumerate(vol_types, start=1):
                vol_id = _uid()
                volumes.append({
                    "id": vol_id,
                    "opportunity_id": opp_id,
                    "volume_number": v_num,
                    "volume_type": v_type,
                    "title": v_title,
                    "description": f"{v_type} volume for solicitation {sol_num}",
                    "page_limit": _RNG.choice([25, 30, 50, 75]),
                    "word_limit": None,
                    "sort_order": v_num,
                    "status": "final",
                    "classification": _CLASSIFICATION,
                    "created_at": opp["created_at"],
                    "updated_at": opp["updated_at"],
                })

                # Pick a content variant for this section
                variant_idx = i % 3  # cycle through 3 variants
                if v_type == "technical":
                    pool = _TECH_TEMPLATES[archetype]
                    content = pool[variant_idx % len(pool)].format(
                        company=company_name, agency=agency_name, pm_name=pm_name
                    )
                    section_type_tag = "technical_approach"
                elif v_type == "management":
                    pool = _MGMT_TEMPLATES[archetype]
                    content = pool[variant_idx % len(pool)].format(
                        company=company_name, agency=agency_name, pm_name=pm_name
                    )
                    section_type_tag = "management_approach"
                else:
                    pool = _PP_TEMPLATES[archetype]
                    content = pool[variant_idx % len(pool)].format(
                        company=company_name, agency=agency_name, pm_name=pm_name
                    )
                    section_type_tag = "past_performance"

                sections.append({
                    "id": _uid(),
                    "volume_id": vol_id,
                    "opportunity_id": opp_id,
                    "parent_section_id": None,
                    "section_number": str(v_num),
                    "title": v_title,
                    "description": content,
                    "writer": pm_name,
                    "writer_email": pm_email,
                    "reviewer": cm_name,
                    "page_limit": volumes[-1]["page_limit"],
                    "word_limit": None,
                    "current_word_count": len(content.split()),
                    "current_page_count": max(1, len(content.split()) // 300),
                    "priority": "high",
                    "status": "submitted",
                    "due_date": opp["due_date"],
                    "content_path": None,
                    "notes": section_type_tag,
                    "sort_order": v_num,
                    "classification": _CLASSIFICATION,
                    "created_at": opp["created_at"],
                    "updated_at": opp["updated_at"],
                })

            proposals.append({
                "opportunity": opp,
                "volumes": volumes,
                "sections": sections,
                "_meta": {
                    "archetype": archetype,
                    "company_name": company_name,
                    "cage_code": cage,
                    "uei": uei,
                    "address": f"{addr_street}, {addr_city}, {addr_state} {addr_zip}",
                    "pm_name": pm_name,
                    "pm_email": pm_email,
                },
            })

    return proposals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic GovCon proposals")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    proposals = generate(count=args.count, seed=args.seed)

    if args.json:
        print(json.dumps({
            "total": len(proposals),
            "archetypes": _ARCHETYPES,
            "sections_per_proposal": 3,
            "total_sections": len(proposals) * 3,
            "sample_solicitation": proposals[0]["opportunity"]["solicitation_number"],
        }, indent=2))
    else:
        print(f"Generated {len(proposals)} synthetic proposals ({len(proposals) * 3} sections)")
        for arch in _ARCHETYPES:
            n = sum(1 for p in proposals if p["opportunity"]["domain"] == arch)
            print(f"  {arch}: {n} proposals")


if __name__ == "__main__":
    _main()
