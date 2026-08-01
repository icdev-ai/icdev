#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""FAR / DFARS Procurement Compliance Verifier.

For each initiative (opportunity / RFP / contract / new project intake), identify
applicable Federal Acquisition Regulation (FAR) parts / subparts, Defense FAR
Supplement (DFARS) clauses, and required procurement documentation. Emits a
pass/fail compliance gate for use by the GovCon / proposal pipeline.

Architecture (deterministic, air-gap safe — no LLM required):
    - Clause detection: regex-driven scan of solicitation text for FAR / DFARS
      clause citations and well-known regulatory keywords
    - Clause catalog: curated dict of clause_id -> {family, summary, required_docs}
    - Required documentation matrix: maps detected clauses to deliverables
      (representations, certs, small-business plan, CUI/CMMC plan, etc.)
    - Initiative verifier: end-to-end -- takes opp-id or raw text and returns
      a structured verification report with status (pass / warn / fail) and
      the documentation gap list
    - Persistence: writes results to pg_far_dfars_verification (SQLite fallback)

Usage:
    python tools/govcon/far_dfars_verifier.py --opportunity-id opp-123 \\
        --solicitation-text "..." --json
    python tools/govcon/far_dfars_verifier.py --initiative-name "ACME C5ISR" \\
        --solicitation-text "..." --json
    python tools/govcon/far_dfars_verifier.py --opportunity-id opp-123 --gate --json
    python tools/govcon/far_dfars_verifier.py --opportunity-id opp-123 \\
        --export --format md --json
    python tools/govcon/far_dfars_verifier.py --list-clauses --json
    python tools/govcon/far_dfars_verifier.py --list-clauses --family far_part_12 --json
"""
import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))


# =========================================================================
# CLAUSE CATALOG
# =========================================================================
#
# Curated subset of FAR / DFARS clauses most commonly invoked on federal
# procurements. Each entry maps clause_id -> family, summary, trigger
# keywords, and required documentation deliverables.
#
# References:
#   - FAR Parts 1-53 (https://www.acquisition.gov/far)
#   - DFARS Parts 201-273 (https://www.acquisition.gov/dfars)
#   - AFARS / DAFFARS (Army supplement) for service-specific requirements

_CLAUSE_CATALOG: Dict[str, Dict[str, Any]] = {
    # ── FAR Part 6 — Competition Requirements ─────────────────────────
    "FAR-6.302-1": {
        "family": "far_part_6",
        "title": "Only one responsible source (Sole Source)",
        "summary": (
            "Authorizes contracting without full and open competition when "
            "supplies/services are available from only one responsible source."
        ),
        "triggers": ["sole source", "only one source", "non-competitive"],
        "required_docs": [
            "J&A (Justification & Approval) — FAR 6.303",
            "Notice to FedBizOpps (FBO) — FAR 5.201",
            "Source Determination Memo",
        ],
        "severity": "high",
    },
    "FAR-6.302-5": {
        "family": "far_part_6",
        "title": "Authorized or required by statute",
        "summary": "Non-competitive acquisition authorized by statute.",
        "triggers": ["authorized by statute"],
        "required_docs": ["J&A citing the statute", "Legal review"],
        "severity": "high",
    },
    # ── FAR Part 7 — Acquisition Planning ─────────────────────────────
    "FAR-7.105": {
        "family": "far_part_7",
        "title": "Contents of written acquisition plans",
        "summary": "Acquisition plan required for major / complex / non-commercial buys.",
        "triggers": ["acquisition plan", "acquisition planning", "AP"],
        "required_docs": [
            "Written Acquisition Plan (AP) — FAR 7.105(b)(1..18)",
            "Source Selection Plan (if applicable)",
        ],
        "severity": "medium",
    },
    # ── FAR Part 8 — Required Sources of Supplies and Services ────────
    "FAR-8.405-2": {
        "family": "far_part_8",
        "title": "Federal Supply Schedule (FSS/GSA) Ordering",
        "summary": "Ordering procedures against GSA / VA / FSS schedules.",
        "triggers": ["GSA schedule", "FSS", "MAS", "federal supply schedule"],
        "required_docs": [
            "RFQ to ≥3 schedule holders (fair opportunity)",
            "Basis for award documentation — FAR 8.405-2(d)",
        ],
        "severity": "medium",
    },
    # ── FAR Part 9 — Contractor Qualifications ────────────────────────
    "FAR-9.1": {
        "family": "far_part_9",
        "title": "Responsible Prospective Contractors",
        "summary": "Determination of contractor responsibility (financial, operational, compliance).",
        "triggers": ["responsibility determination", "responsible contractor"],
        "required_docs": [
            "Contractor Responsibility Determination — FAR 9.104",
            "SAM.gov active registration",
            "No exclusions in SAM (FAPIIS / DPAP)",
        ],
        "severity": "high",
    },
    "FAR-9.5": {
        "family": "far_part_9",
        "title": "Organizational and Consultant Conflicts of Interest (OCCI)",
        "summary": "OCCI analysis and mitigation plan required.",
        "triggers": ["conflict of interest", "OCCI", "organizational conflict"],
        "required_docs": [
            "OCCI Analysis & Mitigation Plan — FAR 9.504",
            "Contractor OCCI certification",
        ],
        "severity": "high",
    },
    # ── FAR Part 12 — Commercial Products and Services ───────────────
    "FAR-12": {
        "family": "far_part_12",
        "title": "Acquisition of Commercial Products and Services",
        "summary": "Commercial item determination, simplified procedures, market research.",
        "triggers": ["commercial", "commercial item", "COTS"],
        "required_docs": [
            "Commercial Item Determination (CID) memo — FAR 12.102",
            "Market Research Report — FAR 10.002",
            "Customary commercial practice waiver rationale (if any)",
        ],
        "severity": "medium",
    },
    # ── FAR Part 15 — Contracting by Negotiation ─────────────────────
    "FAR-15.303": {
        "family": "far_part_15",
        "title": "Responsibilities (Source Selection Authority)",
        "summary": "Formal source selection process with SSA designation.",
        "triggers": ["source selection", "SSA", "best-value tradeoff"],
        "required_docs": [
            "Source Selection Plan (SSP)",
            "SSA Appointment Memo",
            "Award Decision Document (ADD) — FAR 15.308",
        ],
        "severity": "high",
    },
    # ── FAR Part 16 — Contract Types ─────────────────────────────────
    "FAR-16.304": {
        "family": "far_part_16",
        "title": "Cost-Plus-Fixed-Fee (CPFF) Contracts",
        "summary": "CPFF requires DCAA-compliant accounting system and CAS coverage.",
        "triggers": ["CPFF", "cost-plus-fixed-fee"],
        "required_docs": [
            "DCAA accounting system adequacy determination",
            "Cost Accounting Standards (CAS) Disclosure Statement",
            "DD Form 1547 (if DoD)",
        ],
        "severity": "high",
    },
    "FAR-16.401": {
        "family": "far_part_16",
        "title": "General (Incentive Contracts)",
        "summary": "Incentive/Award-Fee contracts require objective measurement basis.",
        "triggers": ["incentive", "award fee", "award-fee"],
        "required_docs": [
            "Award Fee Plan — FAR 16.401(e)(3)",
            "Performance Evaluation Plan",
        ],
        "severity": "high",
    },
    # ── FAR Part 19 — Small Business Programs ────────────────────────
    "FAR-19.502-1": {
        "family": "far_part_19",
        "title": "Setting Aside Acquisitions (Small Business)",
        "summary": "Mandatory / discretionary small-business set-aside rules.",
        "triggers": ["set-aside", "small business set-aside"],
        "required_docs": [
            "Market Research supporting set-aside decision",
            "Rule of Two analysis — FAR 19.502-2",
            "SBA Certificate of Competency (if SBA declined)",
        ],
        "severity": "high",
    },
    "FAR-19.502-4": {
        "family": "far_part_19",
        "title": "Set-Asides for Small Disadvantaged Businesses (8a)",
        "summary": "8(a) sole-source or competitive set-aside requirements.",
        "triggers": ["8(a)", "8a", "small disadvantaged", "SBA 8a"],
        "required_docs": [
            "SBA 8(a) Partnership Agreement",
            "SBA Acceptance Letter (if sole-source)",
        ],
        "severity": "high",
    },
    "FAR-19.502-5": {
        "family": "far_part_19",
        "title": "Set-Asides for Women-Owned Small Business (WOSB)",
        "summary": "WOSB / EDWOSB set-aside rules.",
        "triggers": ["WOSB", "EDWOSB", "women-owned"],
        "required_docs": ["SBA WOSB certification"],
        "severity": "high",
    },
    "FAR-19.502-7": {
        "family": "far_part_19",
        "title": "Set-Asides for Service-Disabled Veteran-Owned (SDVOSB)",
        "summary": "SDVOSB / VOSB set-aside under Vets First.",
        "triggers": ["SDVOSB", "VOSB", "veteran-owned", "Vets First"],
        "required_docs": ["VA CVE verification"],
        "severity": "high",
    },
    "FAR-19.7": {
        "family": "far_part_19",
        "title": "Small Business Subcontracting Program",
        "summary": "Subcontracting plan required for large-business prime contracts > $750K.",
        "triggers": ["subcontracting plan", "subcontract plan"],
        "required_docs": [
            "Small Business Subcontracting Plan — FAR 19.704",
            "Individual Subcontracting Reports (ISR) in eSRS",
            "Summary Subcontracting Reports (SSR)",
        ],
        "severity": "high",
    },
    # ── FAR Part 22 — Application of Labor Laws ──────────────────────
    "FAR-22.10": {
        "family": "far_part_22",
        "title": "Service Contract Act (SCA)",
        "summary": "SCA wage determinations required for service contracts > $2,500.",
        "triggers": ["service contract act", "SCA", "wage determination", "WD"],
        "required_docs": [
            "SCA Wage Determination (WD) — WD publication number",
            "SF-98 / e98 wage determination request",
            "Payroll certifications (WH-347)",
        ],
        "severity": "high",
    },
    "FAR-22.6": {
        "family": "far_part_22",
        "title": "Walsh-Healey Public Contracts Act",
        "summary": "Manufacturing/furnishing contracts > $15K subject to WHA.",
        "triggers": ["Walsh-Healey", "WHA", "public contracts act"],
        "required_docs": ["WHA conformance certificate"],
        "severity": "medium",
    },
    "FAR-22.8": {
        "family": "far_part_22",
        "title": "Equal Employment Opportunity (EEO)",
        "summary": "EEO compliance and affirmative action requirements.",
        "triggers": ["EEO", "equal employment", "affirmative action", "EEO-1"],
        "required_docs": [
            "EEO-1 Report (annual)",
            "Affirmative Action Plan (AAP) if 50+ employees",
        ],
        "severity": "medium",
    },
    # ── FAR Part 24 — Environmental Protection ────────────────────────
    "FAR-24.1": {
        "family": "far_part_24",
        "title": "Environmental Protection (Section C.2.1d.x of DFARS)",
        "summary": "Environmental, energy, and sustainability compliance.",
        "triggers": ["environmental", "sustainability", "energy efficiency"],
        "required_docs": ["Environmental compliance plan"],
        "severity": "low",
    },
    # ── FAR Part 25 — Foreign Acquisition ────────────────────────────
    "FAR-25.103": {
        "family": "far_part_25",
        "title": "Buy American Act (BAA)",
        "summary": "Buy American restrictions on supplies > micro-purchase threshold.",
        "triggers": ["buy american", "BAA", "domestic end product"],
        "required_docs": [
            "Buy American Act Trade Agreements Certificate — FAR 52.225-2",
            "Component origin test (≥ 55% domestic)",
        ],
        "severity": "high",
    },
    "FAR-25.402": {
        "family": "far_part_25",
        "title": "Trade Agreements (TAA)",
        "summary": "TAA compliance for designated country end products.",
        "triggers": ["TAA", "trade agreements", "designated country"],
        "required_docs": [
            "TAA Certificate — FAR 52.225-6",
            "Country-of-origin verification",
        ],
        "severity": "high",
    },
    # ── FAR Part 27 — Patents, Data, Copyrights ──────────────────────
    "FAR-27.4": {
        "family": "far_part_27",
        "title": "Rights in Data and Copyrights (Data Rights)",
        "summary": "DFARS 252.227-7013/7014 rights in technical data and computer software.",
        "triggers": ["data rights", "technical data", "limited rights", "government purpose rights", "GPR"],
        "required_docs": [
            "Data Rights Assertions (DRA) / markings",
            "DFARS 252.227-7013 / 7014 / 7017 incorporated",
        ],
        "severity": "high",
    },
    "FAR-27.3": {
        "family": "far_part_27",
        "title": "Patent Rights — Government Funded Inventions",
        "summary": "Bayh-Dole patent disclosure and march-in rights.",
        "triggers": ["patent rights", "bayh-dole", "march-in rights", "invention disclosure"],
        "required_docs": [
            "Invention Disclosure (DD Form 882)",
            "Patent rights clause FAR 52.227-11",
        ],
        "severity": "medium",
    },
    # ── FAR Part 30 — Cost Accounting Standards ──────────────────────
    "FAR-30.2": {
        "family": "far_part_30",
        "title": "Cost Accounting Standards (CAS)",
        "summary": "CAS coverage and Disclosure Statement requirements.",
        "triggers": ["CAS", "cost accounting standards", "DS-1", "DS-2"],
        "required_docs": [
            "CAS Disclosure Statement (DS-1 or DS-2)",
            "CAS compliance matrix",
            "CASB-administered exemptions list (if any)",
        ],
        "severity": "high",
    },
    # ── FAR Part 31 — Contract Cost Principles ───────────────────────
    "FAR-31": {
        "family": "far_part_31",
        "title": "Contract Cost Principles and Procedures",
        "summary": "Allowable cost determinations under cost-reimbursement contracts.",
        "triggers": ["allowable costs", "cost principles"],
        "required_docs": ["Incurred Cost Submissions (ICS)", "DCMA/DCAA audit support"],
        "severity": "medium",
    },
    # ── FAR Part 32 — Contract Financing ─────────────────────────────
    "FAR-32.7": {
        "family": "far_part_32",
        "title": "Contract Funding (Limitation of Costs / Funds)",
        "summary": "Funding limitations and incremental funding provisions.",
        "triggers": ["limitation of funds", "LOF", "limitation of costs", "LOC"],
        "required_docs": ["Funds cited in Schedule (SLIN) — FAR 32.702"],
        "severity": "medium",
    },
    # ── FAR Part 35 — Research and Development ───────────────────────
    "FAR-35": {
        "family": "far_part_35",
        "title": "Research and Development Contracting",
        "summary": "R&D contracting streamlines and BAA pattern.",
        "triggers": ["research and development", "R&D", "prototype"],
        "required_docs": ["R&D work plan", "Prototype agreement (if OTA)"],
        "severity": "medium",
    },
    # ── FAR Part 36 — Construction and Architect-Engineer ────────────
    "FAR-36.2": {
        "family": "far_part_36",
        "title": "Architect-Engineer Selection (Brooks Act)",
        "summary": "Qualifications-based selection for A-E services.",
        "triggers": ["architect-engineer", "Brooks Act", "A-E"],
        "required_docs": ["A-E Selection Documentation — FAR 36.602"],
        "severity": "medium",
    },
    # ── FAR Part 37 — Service Contracting ────────────────────────────
    "FAR-37.1": {
        "family": "far_part_37",
        "title": "Service Contracts — Performance Work Statement",
        "summary": "Service contracts require PWS and QASP (when applicable).",
        "triggers": ["performance work statement", "PWS", "QASP", "service contract"],
        "required_docs": [
            "Performance Work Statement (PWS)",
            "Quality Assurance Surveillance Plan (QASP) — FAR 46.4",
        ],
        "severity": "high",
    },
    # ── FAR Part 42 — Contract Administration ────────────────────────
    "FAR-42.11": {
        "family": "far_part_42",
        "title": "Production Surveillance and Reporting",
        "summary": "COR designation and surveillance plan.",
        "triggers": ["COR", "contracting officer representative", "surveillance plan"],
        "required_docs": ["COR Appointment Letter — DFARS 201.602-2", "Surveillance Plan"],
        "severity": "high",
    },
    "FAR-42.15": {
        "family": "far_part_42",
        "title": "Contractor Performance Assessment Reporting (CPARs)",
        "summary": "Past performance evaluations in CPARs.",
        "triggers": ["CPARs", "past performance evaluation", "CPAR"],
        "required_docs": [
            "CPAR inputs — FAR 42.1503",
            "Contractor Performance Assessment Report",
        ],
        "severity": "high",
    },
    # ── FAR Part 45 — Government Property ────────────────────────────
    "FAR-45": {
        "family": "far_part_45",
        "title": "Government Property",
        "summary": "Government-furnished property (GFP) / contractor-acquired property.",
        "triggers": ["government property", "GFP", "contractor-acquired property"],
        "required_docs": ["Property Management Plan — FAR 45.4"],
        "severity": "medium",
    },
    # ── FAR Part 46 — Quality Assurance ──────────────────────────────
    "FAR-46": {
        "family": "far_part_46",
        "title": "Quality Assurance",
        "summary": "Inspection, acceptance, and quality requirements.",
        "triggers": ["quality assurance", "inspection", "acceptance"],
        "required_docs": ["Quality Assurance Plan", "Inspection/Test procedures"],
        "severity": "medium",
    },
    # ── FAR Part 52 — Solicitation Provisions / Contract Clauses ─────
    "FAR-52.212-4": {
        "family": "far_part_52",
        "title": "Contract Terms and Conditions — Commercial Products",
        "summary": "Standard commercial-item clause matrix.",
        "triggers": ["52.212-4", "commercial item terms"],
        "required_docs": ["Completed clause matrix — FAR 52.212-4(k)"],
        "severity": "medium",
    },
    "FAR-52.222-50": {
        "family": "far_part_52",
        "title": "Combating Trafficking in Persons",
        "summary": "Mandatory compliance with FAR 52.222-50.",
        "triggers": ["trafficking in persons", "anti-trafficking"],
        "required_docs": [
            "FAR 52.222-50 compliance plan",
            "Employee awareness training records",
        ],
        "severity": "high",
    },
    "FAR-52.222-54": {
        "family": "far_part_52",
        "title": "Employment Eligibility Verification (E-Verify)",
        "summary": "E-Verify required for prime contracts ≥ $150K with period ≥ 120 days.",
        "triggers": ["E-Verify", "employment eligibility"],
        "required_docs": ["E-Verify enrollment confirmation — FAR 52.222-54"],
        "severity": "high",
    },
    "FAR-52.232-33": {
        "family": "far_part_52",
        "title": "Payment by Electronic Funds Transfer (EFT)",
        "summary": "EFT payment registration in SAM.",
        "triggers": ["EFT", "electronic funds transfer"],
        "required_docs": ["SAM.gov banking information — FAR 52.232-33"],
        "severity": "low",
    },
    "FAR-52.204-21": {
        "family": "far_part_52",
        "title": "Basic Safeguarding of Covered Contractor Information Systems",
        "summary": "15 basic security controls for covered contractor systems.",
        "triggers": ["52.204-21", "basic safeguarding", "covered contractor information"],
        "required_docs": ["Basic Safeguarding Compliance Plan — 15 controls"],
        "severity": "high",
    },
    # ── DFARS Clauses ────────────────────────────────────────────────
    "DFARS-252.204-7012": {
        "family": "dfars_part_204",
        "title": "Safeguarding Covered Defense Information and Cyber Incident Reporting",
        "summary": "NIST SP 800-171 + cyber incident reporting within 72 hours.",
        "triggers": ["252.204-7012", "CDI", "covered defense information", "NIST 800-171"],
        "required_docs": [
            "System Security Plan (SSP) — NIST 800-171 r2 110 controls",
            "Plan of Action & Milestones (POAM)",
            "Cyber Incident Reporting Plan (72-hour)",
            "NDA / CUI markings per 32 CFR Part 2002",
        ],
        "severity": "critical",
    },
    "DFARS-252.204-7018": {
        "family": "dfars_part_204",
        "title": "Compliance with Safeguarding Covered Defense Information Controls",
        "summary": "Requires NIST 800-171 self-assessment score in SPRS.",
        "triggers": ["252.204-7018", "SPRS score", "NIST 800-171 assessment"],
        "required_docs": [
            "NIST 800-171 self-assessment in SPRS (Supplier Performance Risk System)",
            "Basic/Medium/High SPRS score recorded",
        ],
        "severity": "critical",
    },
    "DFARS-252.204-7020": {
        "family": "dfars_part_204",
        "title": "NIST SP 800-171 DoD Assessment Requirements",
        "summary": "DoD-level assessment of NIST 800-171 implementation.",
        "triggers": ["252.204-7020", "DoD assessment"],
        "required_docs": ["Medium / High confidence DoD assessment (DIBCAC)"],
        "severity": "critical",
    },
    "DFARS-252.204-7021": {
        "family": "dfars_part_204",
        "title": "Cybersecurity Maturity Model Certification (CMMC) Requirements",
        "summary": "CMMC level required at award + maintenance.",
        "triggers": ["252.204-7021", "CMMC", "C3PAO", "maturity model"],
        "required_docs": [
            "C3PAO CMMC Level 1/2/3 certificate",
            "Affirmation in SPRS",
        ],
        "severity": "critical",
    },
    "DFARS-252.211-7003": {
        "family": "dfars_part_211",
        "title": "Item Unique Identification and Valuation",
        "summary": "IUID marking for delivered items > $5K (or specified).",
        "triggers": ["IUID", "unique item identification", "252.211-7003"],
        "required_docs": ["IUID marking plan + WAWF UID registration"],
        "severity": "medium",
    },
    "DFARS-252.219-7003": {
        "family": "dfars_part_219",
        "title": "Small Business Subcontracting Plan (DoD)",
        "summary": "DoD-specific small-business subcontracting plan elements.",
        "triggers": ["252.219-7003", "DoD subcontracting plan"],
        "required_docs": ["DoD Subcontracting Plan — DFARS 219.708(b)"],
        "severity": "high",
    },
    "DFARS-252.225-7001": {
        "family": "dfars_part_225",
        "title": "Buy American and Balance of Payments Program",
        "summary": "DoD BAA and BOP program — domestic non-availability determinations.",
        "triggers": ["252.225-7001", "Buy American", "balance of payments"],
        "required_docs": ["BAA Trade Agreements Certificate"],
        "severity": "high",
    },
    "DFARS-252.225-7002": {
        "family": "dfars_part_225",
        "title": "Qualifying Country Sources as Subcontractors",
        "summary": "Qualifying-country source approvals.",
        "triggers": ["qualifying country", "252.225-7002"],
        "required_docs": ["Qualifying-country source list"],
        "severity": "medium",
    },
    "DFARS-252.225-7012": {
        "family": "dfars_part_225",
        "title": "Preference for Certain Domestic Commodities (Berry Amendment)",
        "summary": "Berry Amendment compliance for textiles/food/specialty metals.",
        "triggers": ["Berry Amendment", "252.225-7012", "specialty metals"],
        "required_docs": ["Berry Amendment Compliance Certification"],
        "severity": "high",
    },
    "DFARS-252.225-7013": {
        "family": "dfars_part_225",
        "title": "Duty-Free Entry (DFE) — NAFTA/USMCA",
        "summary": "Duty-free entry qualification documentation.",
        "triggers": ["duty-free entry", "252.225-7013"],
        "required_docs": ["DFE eligibility claim — DFARS 252.225-7013(c)"],
        "severity": "medium",
    },
    "DFARS-252.225-7047": {
        "family": "dfars_part_225",
        "title": "Export-Controlled Items",
        "summary": "Compliance with U.S. export control laws and ITAR/EAR.",
        "triggers": ["export-controlled", "export control", "ITAR", "EAR"],
        "required_docs": [
            "Export Control Plan — ITAR / EAR",
            "DDTC registration (if ITAR)",
            "Technology Control Plan (TCP)",
        ],
        "severity": "critical",
    },
    "DFARS-252.227-7013": {
        "family": "dfars_part_227",
        "title": "Rights in Technical Data — Noncommercial Items",
        "summary": "Noncommercial technical data rights markings.",
        "triggers": ["252.227-7013", "technical data — noncommercial"],
        "required_docs": ["DFARS Data Rights Markings / Asserted Restrictions List"],
        "severity": "high",
    },
    "DFARS-252.227-7014": {
        "family": "dfars_part_227",
        "title": "Rights in Noncommercial Computer Software and Documentation",
        "summary": "Noncommercial software data rights markings.",
        "triggers": ["252.227-7014", "noncommercial software"],
        "required_docs": ["Software rights assertions (limited, GPR, etc.)"],
        "severity": "high",
    },
    "DFARS-252.227-7017": {
        "family": "dfars_part_227",
        "title": "Identification and Assertion of Use, Release, or Disclosure Restrictions",
        "summary": "Use, Release, Disclosure Restrictions (U/DR/D).",
        "triggers": ["252.227-7017", "U/DR/D", "assertion of restrictions"],
        "required_docs": ["U/DR/D Assertions List — Form 2310/2311"],
        "severity": "high",
    },
    "DFARS-252.232-7003": {
        "family": "dfars_part_232",
        "title": "Electronic Submission of Payment Requests (WAWF)",
        "summary": "WAWF submission for DoD invoices.",
        "triggers": ["WAWF", "252.232-7003"],
        "required_docs": ["WAWF vendor registration / CAGE in WAWF"],
        "severity": "medium",
    },
    "DFARS-252.232-7010": {
        "family": "dfars_part_232",
        "title": "Performance-Based Payments (PBPL) — Advance Payment Schedule",
        "summary": "Performance-based payment schedule.",
        "triggers": ["performance-based payments", "252.232-7010"],
        "required_docs": ["PBPL schedule — FAR 32.1004"],
        "severity": "medium",
    },
    "DFARS-252.239-7010": {
        "family": "dfars_part_239",
        "title": "Cloud Computing Services (DoD)",
        "summary": "DoD cloud SRG / FedRAMP equivalent (IL4/IL5/IL6) required.",
        "triggers": ["cloud computing", "252.239-7010", "cloud SRG"],
        "required_docs": [
            "DoD Cloud SRG Authorization",
            "FedRAMP authorization (or JAB/Agency)",
            "DoD Provisional Authorization (PA) at IL4/IL5/IL6",
        ],
        "severity": "critical",
    },
    "DFARS-252.245-7001": {
        "family": "dfars_part_245",
        "title": "Tagging, Labeling, and Marking of GFP",
        "summary": "DoD GFP tagging/labeling/marking requirements.",
        "triggers": ["252.245-7001", "tagging labeling", "GFP"],
        "required_docs": ["GFP property plan — DFARS 245.3"],
        "severity": "medium",
    },
    "DFARS-252.246-7007": {
        "family": "dfars_part_246",
        "title": "Contractor Counterfeit Electronic Part Detection and Avoidance",
        "summary": "Counterfeit parts detection/avoidance system.",
        "triggers": ["counterfeit parts", "252.246-7007"],
        "required_docs": [
            "Counterfeit Parts Avoidance Plan — DFARS 252.246-7007(c)",
            "AS5553 / AS6081 conformance evidence",
        ],
        "severity": "high",
    },
    "DFARS-252.247-7023": {
        "family": "dfars_part_247",
        "title": "Transportation of Supplies by Sea (Cargo Preference)",
        "summary": "Cargo Preference Act — U.S.-flag vessels for ocean transport.",
        "triggers": ["cargo preference", "U.S.-flag", "252.247-7023"],
        "required_docs": ["U.S.-flag carrier documentation"],
        "severity": "medium",
    },
}


# Curated set of base FAR / DFARS parts (top-level part applies if any subpart
# is detected).  The verifier detects specific clause numbers and rolls up
# coverage to the family level.
_PART_FAMILIES: Dict[str, str] = {
    "far_part_6": "FAR Part 6 — Competition Requirements",
    "far_part_7": "FAR Part 7 — Acquisition Planning",
    "far_part_8": "FAR Part 8 — Required Sources",
    "far_part_9": "FAR Part 9 — Contractor Qualifications",
    "far_part_12": "FAR Part 12 — Commercial Products and Services",
    "far_part_15": "FAR Part 15 — Contracting by Negotiation",
    "far_part_16": "FAR Part 16 — Types of Contracts",
    "far_part_19": "FAR Part 19 — Small Business Programs",
    "far_part_22": "FAR Part 22 — Application of Labor Laws",
    "far_part_24": "FAR Part 24 — Environmental Protection",
    "far_part_25": "FAR Part 25 — Foreign Acquisition",
    "far_part_27": "FAR Part 27 — Patents, Data, Copyrights",
    "far_part_30": "FAR Part 30 — Cost Accounting Standards",
    "far_part_31": "FAR Part 31 — Contract Cost Principles",
    "far_part_32": "FAR Part 32 — Contract Financing",
    "far_part_35": "FAR Part 35 — Research and Development",
    "far_part_36": "FAR Part 36 — Construction / A-E",
    "far_part_37": "FAR Part 37 — Service Contracting",
    "far_part_42": "FAR Part 42 — Contract Administration",
    "far_part_45": "FAR Part 45 — Government Property",
    "far_part_46": "FAR Part 46 — Quality Assurance",
    "far_part_52": "FAR Part 52 — Solicitation Provisions / Clauses",
    "dfars_part_204": "DFARS Part 204 — Administrative Matters (Cybersecurity)",
    "dfars_part_211": "DFARS Part 211 — Describing Agency Needs",
    "dfars_part_219": "DFARS Part 219 — Small Business Programs",
    "dfars_part_225": "DFARS Part 225 — Foreign Acquisition",
    "dfars_part_227": "DFARS Part 227 — Patents, Data, Copyrights",
    "dfars_part_232": "DFARS Part 232 — Contract Financing",
    "dfars_part_239": "DFARS Part 239 — Information Resources / Cloud",
    "dfars_part_245": "DFARS Part 245 — Government Property",
    "dfars_part_246": "DFARS Part 246 — Quality Assurance",
    "dfars_part_247": "DFARS Part 247 — Transportation",
}


# =========================================================================
# DATA STRUCTURES
# =========================================================================


@dataclass
class DetectedClause:
    """A FAR / DFARS clause detected in the solicitation text."""
    clause_id: str
    family: str
    title: str
    summary: str
    severity: str
    trigger_evidence: List[str] = field(default_factory=list)
    required_docs: List[str] = field(default_factory=list)
    source: str = "trigger"   # "explicit_citation" or "trigger"


@dataclass
class VerificationReport:
    """End-to-end verification result for an initiative."""
    opportunity_id: str
    initiative_name: str
    generated_at: str
    applicable_far_parts: List[str] = field(default_factory=list)
    applicable_dfars_parts: List[str] = field(default_factory=list)
    detected_clauses: List[DetectedClause] = field(default_factory=list)
    required_documentation: List[str] = field(default_factory=list)
    documentation_gaps: List[str] = field(default_factory=list)
    status: str = "pass"          # pass | warn | fail
    rationale: str = ""
    input_hash: str = ""
    total_clauses_detected: int = 0
    critical_clauses: int = 0
    high_severity_clauses: int = 0

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        return out


# =========================================================================
# REGEX CATALOG
# =========================================================================

# Detect explicit clause citations like 252.204-7012, FAR 52.212-4, FAR 19.502-1
_RE_CLAUSE_CITATION = re.compile(
    r"\b(FAR|DFARS|DFARs?)\s*(?:clause\s+)?"
    r"(\d{1,3})[\.–—\-](\d{1,4})(?:[\.–—\-](\d{1,4}))?",
    re.IGNORECASE,
)

# Top-level part reference like "FAR Part 12" or "DFARS Part 225"
_RE_PART_REFERENCE = re.compile(
    r"\b(FAR|DFARS|DFARs?)\s+[Pp]art\s+(\d{1,3})\b",
)

# Standard H-clause headings like "H.1 52.212-4 Contract Terms and Conditions"
_RE_H_CLAUSE = re.compile(
    r"^[Hh]\.?\s*\d*\.?\s*(\d{1,3})[\.\-](\d{1,4})(?:[\.\-](\d{1,4}))?",
    re.MULTILINE,
)


# =========================================================================
# HELPERS
# =========================================================================


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str = "fdv") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _audit(conn, action: str, details: str = "", actor: str = "far_dfars_verifier") -> None:
    """Append-only audit trail entry (NIST AU)."""
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), _now(), "govcon.far_dfars_verification", actor, action, details, "govcon"),
        )
    except Exception:
        pass


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def _ensure_table(conn) -> None:
    """Create the verification table on demand (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pg_far_dfars_verification (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            initiative_name TEXT,
            input_hash TEXT,
            applicable_far_parts TEXT,
            applicable_dfars_parts TEXT,
            detected_clauses TEXT,
            required_documentation TEXT,
            documentation_gaps TEXT,
            status TEXT,
            rationale TEXT,
            total_clauses_detected INTEGER,
            critical_clauses INTEGER,
            high_severity_clauses INTEGER,
            created_at TEXT
        )
        """
    )


# =========================================================================
# DETECTION ENGINE
# =========================================================================


def _clause_id_from_citation(prefix: str, n1: str, n2: str, n3: Optional[str]) -> str:
    """Build a normalized clause_id like 'FAR-12' or 'DFARS-252.204-7012'.

    Catalog keys use '-' as the final separator (matches FAR / DFARS citation
    convention), e.g. 'DFARS-252.204-7012', not 'DFARS-252.204.7012'.
    """
    base = f"{prefix.upper()}-{n1}.{n2}"
    if n3:
        base += f"-{n3}"
    return base


def _normalize_id(citation: str) -> str:
    """Normalize a clause citation to catalog form (UPPERCASE-PART.SUBPART)."""
    return citation.replace(" ", "").upper().replace("–", "-").replace("—", "-")


def _detect_explicit_citations(text: str) -> List[Tuple[str, str]]:
    """Find explicit FAR/DFARS clause citations.

    Returns:
        List of (clause_id, source_text) tuples.
    """
    citations: List[Tuple[str, str]] = []
    for m in _RE_CLAUSE_CITATION.finditer(text):
        prefix = m.group(1)
        n1, n2, n3 = m.group(2), m.group(3), m.group(4)
        if prefix.upper().startswith("FAR"):
            clause_id = _clause_id_from_citation("FAR", n1, n2, n3)
        else:
            clause_id = _clause_id_from_citation("DFARS", n1, n2, n3)
        citations.append((clause_id, m.group(0)))
    for m in _RE_H_CLAUSE.finditer(text):
        n1, n2, n3 = m.group(1), m.group(2), m.group(3)
        clause_id = _clause_id_from_citation("FAR", n1, n2, n3)
        citations.append((clause_id, m.group(0)))
    return citations


def _detect_top_level_parts(text: str) -> List[str]:
    """Find references like 'FAR Part 12' or 'DFARS Part 225'.

    Returns:
        List of family keys (e.g. 'far_part_12', 'dfars_part_225').
    """
    parts: List[str] = []
    for m in _RE_PART_REFERENCE.finditer(text):
        prefix = m.group(1).upper()
        n = m.group(2)
        if prefix.startswith("FAR"):
            family = f"far_part_{int(n)}"
        else:
            family = f"dfars_part_{int(n)}"
        if family in _PART_FAMILIES and family not in parts:
            parts.append(family)
    return parts


def _detect_triggered_clauses(text: str) -> List[Tuple[str, str]]:
    """Find clauses triggered by keyword matches in the body text.

    Triggers are matched as whole words / phrases using word boundaries so
    short tokens like "AP" or "IT" do not produce false positives from words
    like "applies" or "items".

    Returns:
        List of (clause_id, matched_phrase) tuples.
    """
    hits: List[Tuple[str, str]] = []
    for clause_id, spec in _CLAUSE_CATALOG.items():
        for trigger in spec.get("triggers", []):
            if not trigger:
                continue
            # Build a word-boundary pattern; allow hyphens inside phrase
            pattern = r"(?<![A-Za-z0-9])" + re.escape(trigger) + r"(?![A-Za-z0-9])"
            if re.search(pattern, text, re.IGNORECASE):
                hits.append((clause_id, trigger))
                break
    return hits


def detect_clauses(text: str) -> List[DetectedClause]:
    """Detect all applicable FAR / DFARS clauses from solicitation text.

    Combines:
      1. Explicit citations (FAR X.Y or DFARS X.Y.Z)
      2. Top-level Part references
      3. Keyword triggers
    """
    detected: Dict[str, DetectedClause] = {}

    # (1) explicit citations
    for clause_id, src in _detect_explicit_citations(text):
        spec = _CLAUSE_CATALOG.get(clause_id)
        if not spec:
            continue  # Unknown citation, skip
        existing = detected.get(clause_id)
        if existing is None:
            detected[clause_id] = DetectedClause(
                clause_id=clause_id,
                family=spec["family"],
                title=spec["title"],
                summary=spec["summary"],
                severity=spec["severity"],
                trigger_evidence=[src],
                required_docs=list(spec["required_docs"]),
                source="explicit_citation",
            )
        else:
            if src not in existing.trigger_evidence:
                existing.trigger_evidence.append(src)

    # (2) top-level part references (add family-level coverage)
    for family in _detect_top_level_parts(text):
        # For each clause in that family, mark the "Part X" family-level clause
        family_clause_id = family.split("_part_")[-1].replace("_", ".")
        for clause_id, spec in _CLAUSE_CATALOG.items():
            if spec["family"] == family:
                if clause_id not in detected:
                    detected[clause_id] = DetectedClause(
                        clause_id=clause_id,
                        family=spec["family"],
                        title=spec["title"],
                        summary=spec["summary"],
                        severity=spec["severity"],
                        trigger_evidence=[f"Part {family_clause_id} reference"],
                        required_docs=list(spec["required_docs"]),
                        source="part_reference",
                    )

    # (3) keyword triggers
    for clause_id, phrase in _detect_triggered_clauses(text):
        spec = _CLAUSE_CATALOG.get(clause_id)
        if not spec:
            continue
        existing = detected.get(clause_id)
        if existing is None:
            detected[clause_id] = DetectedClause(
                clause_id=clause_id,
                family=spec["family"],
                title=spec["title"],
                summary=spec["summary"],
                severity=spec["severity"],
                trigger_evidence=[phrase],
                required_docs=list(spec["required_docs"]),
                source="trigger",
            )
        else:
            if phrase not in existing.trigger_evidence:
                existing.trigger_evidence.append(phrase)
            if existing.source == "explicit_citation":
                pass  # do not downgrade source attribution
            else:
                existing.source = existing.source

    return list(detected.values())


# =========================================================================
# VERIFICATION
# =========================================================================


def verify_initiative(
    opportunity_id: str,
    solicitation_text: str = "",
    initiative_name: str = "",
    provided_docs: Optional[List[str]] = None,
) -> VerificationReport:
    """Run the FAR / DFARS procurement compliance verification for an initiative.

    Args:
        opportunity_id: Identifier for the RFP / opportunity / initiative.
        solicitation_text: Raw RFP / SOW / intake text (may be empty).
        initiative_name: Optional human-readable initiative name.
        provided_docs: Optional list of documents already prepared by the
            contractor. Used to compute documentation_gaps.

    Returns:
        VerificationReport with applicable FAR/DFARS parts, detected clauses,
        required documentation, and pass/warn/fail status.
    """
    provided = {d.strip().lower() for d in (provided_docs or [])}
    clauses = detect_clauses(solicitation_text or "")

    applicable_far: List[str] = []
    applicable_dfars: List[str] = []
    required_docs: List[str] = []
    gaps: List[str] = []

    critical_count = 0
    high_count = 0

    for clause in clauses:
        family = clause.family
        if family.startswith("far_part_") and family not in applicable_far:
            applicable_far.append(family)
        elif family.startswith("dfars_part_") and family not in applicable_dfars:
            applicable_dfars.append(family)

        for doc in clause.required_docs:
            if doc not in required_docs:
                required_docs.append(doc)
            # Documentation gap check (heuristic — check first 25 chars of doc)
            doc_key = doc[:25].lower()
            if provided and not any(doc_key in p for p in provided):
                if doc not in gaps:
                    gaps.append(doc)

        if clause.severity == "critical":
            critical_count += 1
        elif clause.severity == "high":
            high_count += 1

    # Apply gate logic
    if critical_count > 0:
        status = "fail"
        rationale = (
            f"{critical_count} critical clause(s) detected (e.g. NIST 800-171 / "
            f"CMMC / ITAR / DoD Cloud). Resolution mandatory before award."
        )
    elif high_count > 0:
        status = "warn"
        rationale = (
            f"{high_count} high-severity clause(s) detected; review required "
            f"to ensure compliance documentation is current."
        )
    elif clauses:
        status = "warn"
        rationale = (
            f"{len(clauses)} clause(s) detected; verify all required documentation "
            f"is in place before award."
        )
    else:
        status = "pass"
        rationale = "No FAR / DFARS clauses detected in supplied text. Manual review recommended."

    if not provided:
        gaps = []  # no provided docs means we cannot evaluate gaps
    elif not gaps:
        # all required docs covered
        pass

    return VerificationReport(
        opportunity_id=opportunity_id,
        initiative_name=initiative_name or opportunity_id,
        generated_at=_now(),
        applicable_far_parts=[_PART_FAMILIES.get(f, f) for f in applicable_far],
        applicable_dfars_parts=[_PART_FAMILIES.get(f, f) for f in applicable_dfars],
        detected_clauses=clauses,
        required_documentation=required_docs,
        documentation_gaps=gaps,
        status=status,
        rationale=rationale,
        input_hash=_content_hash(solicitation_text or ""),
        total_clauses_detected=len(clauses),
        critical_clauses=critical_count,
        high_severity_clauses=high_count,
    )


def save_verification(report: VerificationReport) -> str:
    """Persist a verification report. Returns the verification_id."""
    conn = _get_db()
    try:
        _ensure_table(conn)
        verification_id = _gen_id("fdv")
        conn.execute(
            "INSERT INTO pg_far_dfars_verification "
            "(id, opportunity_id, initiative_name, input_hash, "
            "applicable_far_parts, applicable_dfars_parts, detected_clauses, "
            "required_documentation, documentation_gaps, status, rationale, "
            "total_clauses_detected, critical_clauses, high_severity_clauses, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                verification_id,
                report.opportunity_id,
                report.initiative_name,
                report.input_hash,
                json.dumps(report.applicable_far_parts),
                json.dumps(report.applicable_dfars_parts),
                json.dumps([asdict(c) for c in report.detected_clauses]),
                json.dumps(report.required_documentation),
                json.dumps(report.documentation_gaps),
                report.status,
                report.rationale,
                report.total_clauses_detected,
                report.critical_clauses,
                report.high_severity_clauses,
                report.generated_at,
            ),
        )
        _audit(conn, "save_verification",
               f"opportunity_id={report.opportunity_id} status={report.status} "
               f"clauses={report.total_clauses_detected}")
        conn.commit()
        return verification_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_latest_verification(opportunity_id: str) -> Optional[Dict[str, Any]]:
    """Load the most recent verification for an opportunity."""
    conn = _get_db()
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM pg_far_dfars_verification "
            "WHERE opportunity_id = %s ORDER BY created_at DESC LIMIT 1",
            (opportunity_id,),
        )
        rows = list(row) if hasattr(row, "__iter__") else []
        if not rows:
            return None
        rec = rows[0]
        if isinstance(rec, dict):
            return rec
        # SQLite Row supports both index and key access
        return dict(rec)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =========================================================================
# OUTPUT FORMATTING
# =========================================================================


def render_markdown(report: VerificationReport) -> str:
    """Render the report as a markdown summary (for --export --format md)."""
    lines = []
    lines.append("# FAR / DFARS Compliance Verification\n")
    lines.append(f"- **Opportunity / Initiative ID:** `{report.opportunity_id}`")
    lines.append(f"- **Initiative Name:** {report.initiative_name}")
    lines.append(f"- **Generated:** {report.generated_at}")
    lines.append(f"- **Status:** **{report.status.upper()}** — {report.rationale}\n")

    lines.append("## Applicable FAR Parts")
    if report.applicable_far_parts:
        for p in report.applicable_far_parts:
            lines.append(f"- {p}")
    else:
        lines.append("_None detected_")
    lines.append("")

    lines.append("## Applicable DFARS Parts")
    if report.applicable_dfars_parts:
        for p in report.applicable_dfars_parts:
            lines.append(f"- {p}")
    else:
        lines.append("_None detected_")
    lines.append("")

    lines.append(f"## Detected Clauses ({report.total_clauses_detected})")
    if report.detected_clauses:
        lines.append("| Clause | Family | Severity | Source | Title |")
        lines.append("|---|---|---|---|---|")
        for c in report.detected_clauses:
            lines.append(
                f"| `{c.clause_id}` | {c.family} | {c.severity} | {c.source} | {c.title} |"
            )
    else:
        lines.append("_None_")
    lines.append("")

    lines.append("## Required Documentation")
    if report.required_documentation:
        for d in report.required_documentation:
            lines.append(f"- {d}")
    else:
        lines.append("_None_")
    lines.append("")

    if report.documentation_gaps:
        lines.append("## Documentation Gaps (provided docs do not cover)")
        for g in report.documentation_gaps:
            lines.append(f"- ⚠ {g}")
    return "\n".join(lines) + "\n"


# =========================================================================
# CLI
# =========================================================================


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="far_dfars_verifier",
        description="Verify FAR / DFARS procurement compliance for an initiative.",
    )
    p.add_argument("--opportunity-id", help="Initiative / opportunity ID")
    p.add_argument("--initiative-name", default="", help="Human-readable initiative name")
    p.add_argument("--solicitation-text", default="", help="Raw RFP / SOW text")
    p.add_argument(
        "--provided-doc",
        action="append",
        default=[],
        help="Document already prepared (may be repeated for each provided doc)",
    )
    p.add_argument("--save", action="store_true", help="Persist the verification result to DB")
    p.add_argument("--gate", action="store_true", help="Apply pass/warn/fail gate (exit 1 on fail)")
    p.add_argument("--list-clauses", action="store_true", help="List the catalog of clauses")
    p.add_argument("--family", default=None, help="Filter --list-clauses by family (e.g. dfars_part_204)")
    p.add_argument("--export", action="store_true", help="Export latest verification for the opp")
    p.add_argument("--format", choices=("json", "md"), default="json")
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.json:
        out: Dict[str, Any] = {"success": True}

    if args.list_clauses:
        catalog = {}
        for cid, spec in _CLAUSE_CATALOG.items():
            if args.family and spec["family"] != args.family:
                continue
            catalog[cid] = spec
        if args.json:
            out["clause_count"] = len(catalog)
            out["clauses"] = catalog
            out["families"] = _PART_FAMILIES
            print(json.dumps(out, indent=2, default=str))
        else:
            print(f"Clause catalog ({len(catalog)} entries):")
            for cid, spec in catalog.items():
                print(f"  {cid}  [{spec['family']} / {spec['severity']}]  {spec['title']}")
        return 0

    if not args.opportunity_id and not args.export:
        print("ERROR: --opportunity-id is required (or pass --export with one)", file=sys.stderr)
        return 2

    if args.export:
        rec = load_latest_verification(args.opportunity_id)
        if rec is None:
            print(json.dumps({"success": False, "error": "no verification found"}, indent=2))
            return 1
        if args.format == "md":
            # Re-render from stored JSON
            clauses_raw = rec.get("detected_clauses") or "[]"
            if isinstance(clauses_raw, str):
                try:
                    clauses_raw = json.loads(clauses_raw)
                except Exception:
                    clauses_raw = []
            applicable_far = rec.get("applicable_far_parts") or "[]"
            if isinstance(applicable_far, str):
                try:
                    applicable_far = json.loads(applicable_far)
                except Exception:
                    applicable_far = []
            applicable_dfars = rec.get("applicable_dfars_parts") or "[]"
            if isinstance(applicable_dfars, str):
                try:
                    applicable_dfars = json.loads(applicable_dfars)
                except Exception:
                    applicable_dfars = []
            required = rec.get("required_documentation") or "[]"
            if isinstance(required, str):
                try:
                    required = json.loads(required)
                except Exception:
                    required = []
            gaps = rec.get("documentation_gaps") or "[]"
            if isinstance(gaps, str):
                try:
                    gaps = json.loads(gaps)
                except Exception:
                    gaps = []
            report = VerificationReport(
                opportunity_id=rec.get("opportunity_id", args.opportunity_id),
                initiative_name=rec.get("initiative_name", ""),
                generated_at=rec.get("created_at", _now()),
                applicable_far_parts=list(applicable_far),
                applicable_dfars_parts=list(applicable_dfars),
                detected_clauses=[DetectedClause(**c) for c in clauses_raw if isinstance(c, dict)],
                required_documentation=list(required),
                documentation_gaps=list(gaps),
                status=rec.get("status", "pass"),
                rationale=rec.get("rationale", ""),
                input_hash=rec.get("input_hash", ""),
                total_clauses_detected=rec.get("total_clauses_detected", 0),
                critical_clauses=rec.get("critical_clauses", 0),
                high_severity_clauses=rec.get("high_severity_clauses", 0),
            )
            print(render_markdown(report))
        else:
            print(json.dumps({"success": True, "verification": rec}, indent=2, default=str))
        return 0

    report = verify_initiative(
        opportunity_id=args.opportunity_id,
        solicitation_text=args.solicitation_text,
        initiative_name=args.initiative_name,
        provided_docs=args.provided_doc or None,
    )

    verification_id = None
    if args.save:
        verification_id = save_verification(report)

    if args.gate:
        if report.status == "fail":
            print(json.dumps({
                "success": False,
                "gate": "fail",
                "report": report.to_dict(),
                "verification_id": verification_id,
            }, indent=2, default=str))
            return 1
        elif report.status == "warn":
            print(json.dumps({
                "success": True,
                "gate": "warn",
                "report": report.to_dict(),
                "verification_id": verification_id,
            }, indent=2, default=str))
            return 0
        else:
            print(json.dumps({
                "success": True,
                "gate": "pass",
                "report": report.to_dict(),
                "verification_id": verification_id,
            }, indent=2, default=str))
            return 0

    if args.json:
        out["report"] = report.to_dict()
        if verification_id:
            out["verification_id"] = verification_id
        print(json.dumps(out, indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
