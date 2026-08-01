"""ICDEV™ Studio — Form Builder Backend.

Drag-and-drop form designer for intake questionnaires, proposal templates,
compliance checklists, and custom data collection.  Forms serialize to
JSON Schema (draft-07) and auto-generate DB storage + API endpoints.

Architecture Decision D363: JSON Schema for portability.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "frm") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── Field type definitions ────────────────────────────────────────────

FIELD_TYPES: list[dict[str, Any]] = [
    {"type": "text", "label": "Text", "icon": "T", "schema_type": "string"},
    {"type": "textarea", "label": "Long Text", "icon": "P", "schema_type": "string"},
    {"type": "number", "label": "Number", "icon": "#", "schema_type": "number"},
    {"type": "date", "label": "Date", "icon": "D", "schema_type": "string", "format": "date"},
    {"type": "select", "label": "Dropdown", "icon": "V", "schema_type": "string", "has_options": True},
    {"type": "multiselect", "label": "Multi-Select", "icon": "M", "schema_type": "array", "has_options": True},
    {"type": "checkbox", "label": "Checkbox", "icon": "X", "schema_type": "boolean"},
    {"type": "email", "label": "Email", "icon": "@", "schema_type": "string", "format": "email"},
    {"type": "file", "label": "File Upload", "icon": "F", "schema_type": "string", "format": "uri"},
    {"type": "richtext", "label": "Rich Text", "icon": "R", "schema_type": "string"},
]

# ── Pre-built form templates ──────────────────────────────────────────

FORM_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "tpl-risk-assessment",
        "name": "Risk Assessment",
        "description": "Standard risk assessment questionnaire",
        "category": "compliance",
        "industry": "IT/Security",
        "fields": [
            {"id": "f1", "type": "text", "label": "System Name", "required": True},
            {
                "id": "f2",
                "type": "select",
                "label": "Impact Level",
                "options": ["IL2", "IL4", "IL5", "IL6"],
                "required": True,
            },
            {
                "id": "f3",
                "type": "select",
                "label": "Risk Level",
                "options": ["Critical", "High", "Medium", "Low"],
                "required": True,
            },
            {"id": "f4", "type": "textarea", "label": "Risk Description", "required": True},
            {"id": "f5", "type": "textarea", "label": "Mitigation Plan"},
            {"id": "f6", "type": "date", "label": "Target Remediation Date"},
            {"id": "f7", "type": "text", "label": "Assigned To"},
        ],
    },
    {
        "id": "tpl-change-request",
        "name": "Change Request",
        "description": "Configuration change request form",
        "category": "operations",
        "industry": "IT/Security",
        "fields": [
            {"id": "f1", "type": "text", "label": "Change Title", "required": True},
            {
                "id": "f2",
                "type": "select",
                "label": "Change Type",
                "options": ["Standard", "Normal", "Emergency"],
                "required": True,
            },
            {
                "id": "f3",
                "type": "select",
                "label": "Priority",
                "options": ["Critical", "High", "Medium", "Low"],
                "required": True,
            },
            {"id": "f4", "type": "textarea", "label": "Description", "required": True},
            {"id": "f5", "type": "textarea", "label": "Impact Analysis"},
            {"id": "f6", "type": "textarea", "label": "Rollback Plan"},
            {"id": "f7", "type": "date", "label": "Planned Date"},
            {"id": "f8", "type": "text", "label": "Approver"},
        ],
    },
    {
        "id": "tpl-compliance-checklist",
        "name": "Compliance Checklist",
        "description": "Pre-deployment compliance verification",
        "category": "compliance",
        "industry": "Government/Federal",
        "fields": [
            {"id": "f1", "type": "text", "label": "System Name", "required": True},
            {"id": "f2", "type": "checkbox", "label": "STIG checks passed"},
            {"id": "f3", "type": "checkbox", "label": "Vulnerability scan completed"},
            {"id": "f4", "type": "checkbox", "label": "SSP updated"},
            {"id": "f5", "type": "checkbox", "label": "POA&M items addressed"},
            {"id": "f6", "type": "checkbox", "label": "SBOM generated"},
            {"id": "f7", "type": "checkbox", "label": "CUI markings verified"},
            {"id": "f8", "type": "textarea", "label": "Notes"},
            {"id": "f9", "type": "text", "label": "Verified By", "required": True},
            {"id": "f10", "type": "date", "label": "Verification Date", "required": True},
        ],
    },
    {
        "id": "tpl-incident-report",
        "name": "Incident Report",
        "description": "Security or operational incident report",
        "category": "security",
        "industry": "IT/Security",
        "fields": [
            {"id": "f1", "type": "text", "label": "Incident Title", "required": True},
            {
                "id": "f2",
                "type": "select",
                "label": "Severity",
                "options": ["Critical", "High", "Medium", "Low"],
                "required": True,
            },
            {
                "id": "f3",
                "type": "select",
                "label": "Category",
                "options": ["Security", "Availability", "Performance", "Data", "Other"],
                "required": True,
            },
            {"id": "f4", "type": "textarea", "label": "Description", "required": True},
            {"id": "f5", "type": "textarea", "label": "Impact Assessment"},
            {"id": "f6", "type": "textarea", "label": "Root Cause"},
            {"id": "f7", "type": "textarea", "label": "Corrective Actions"},
            {"id": "f8", "type": "text", "label": "Reported By", "required": True},
        ],
    },
    # ── Government/Federal ────────────────────────────────────────────────
    {
        "id": "tpl-foia-request",
        "name": "FOIA Request",
        "description": "Freedom of Information Act request form",
        "category": "government",
        "industry": "Government/Federal",
        "fields": [
            {"id": "f1", "type": "text", "label": "Requester Name", "required": True},
            {"id": "f2", "type": "email", "label": "Requester Email", "required": True},
            {"id": "f3", "type": "text", "label": "Organization / Agency"},
            {"id": "f4", "type": "textarea", "label": "Description of Records Requested", "required": True},
            {"id": "f5", "type": "select", "label": "Delivery Format", "options": ["Electronic", "Paper", "Either"]},
            {"id": "f6", "type": "select", "label": "Fee Category", "options": ["Commercial", "Educational", "Media", "Other Non-Commercial"], "required": True},
            {"id": "f7", "type": "checkbox", "label": "Fee waiver requested"},
            {"id": "f8", "type": "textarea", "label": "Justification for Fee Waiver"},
            {"id": "f9", "type": "date", "label": "Date Submitted", "required": True},
        ],
    },
    {
        "id": "tpl-ato-evidence",
        "name": "ATO Evidence Collection",
        "description": "Authorization to Operate evidence intake",
        "category": "compliance",
        "industry": "Government/Federal",
        "fields": [
            {"id": "f1", "type": "text", "label": "System Name", "required": True},
            {"id": "f2", "type": "select", "label": "Impact Level", "options": ["IL2", "IL4", "IL5", "IL6"], "required": True},
            {"id": "f3", "type": "checkbox", "label": "SSP completed"},
            {"id": "f4", "type": "checkbox", "label": "POA&M items resolved"},
            {"id": "f5", "type": "checkbox", "label": "STIG checks passed"},
            {"id": "f6", "type": "checkbox", "label": "Vulnerability scan completed"},
            {"id": "f7", "type": "checkbox", "label": "SBOM generated"},
            {"id": "f8", "type": "checkbox", "label": "Pen test completed"},
            {"id": "f9", "type": "file", "label": "Upload SSP Document"},
            {"id": "f10", "type": "text", "label": "Authorizing Official", "required": True},
            {"id": "f11", "type": "date", "label": "ATO Target Date", "required": True},
        ],
    },
    {
        "id": "tpl-clearance-intake",
        "name": "Security Clearance Intake",
        "description": "Personnel security clearance request",
        "category": "government",
        "industry": "Government/Federal",
        "fields": [
            {"id": "f1", "type": "text", "label": "Full Legal Name", "required": True},
            {"id": "f2", "type": "date", "label": "Date of Birth", "required": True},
            {"id": "f3", "type": "select", "label": "Clearance Level Requested", "options": ["Secret", "Top Secret", "TS/SCI"], "required": True},
            {"id": "f4", "type": "text", "label": "Current Employer", "required": True},
            {"id": "f5", "type": "text", "label": "Position / Role", "required": True},
            {"id": "f6", "type": "select", "label": "Citizenship", "options": ["US Citizen", "Permanent Resident", "Other"]},
            {"id": "f7", "type": "checkbox", "label": "SF-86 completed"},
            {"id": "f8", "type": "text", "label": "Sponsoring Official"},
        ],
    },
    # ── Healthcare ────────────────────────────────────────────────────────
    {
        "id": "tpl-patient-intake",
        "name": "Patient Intake",
        "description": "New patient registration and medical history",
        "category": "healthcare",
        "industry": "Healthcare",
        "fields": [
            {"id": "f1", "type": "text", "label": "Patient Full Name", "required": True},
            {"id": "f2", "type": "date", "label": "Date of Birth", "required": True},
            {"id": "f3", "type": "select", "label": "Gender", "options": ["Male", "Female", "Non-binary", "Prefer not to say"]},
            {"id": "f4", "type": "text", "label": "Phone Number", "required": True},
            {"id": "f5", "type": "email", "label": "Email Address"},
            {"id": "f6", "type": "text", "label": "Insurance Provider"},
            {"id": "f7", "type": "text", "label": "Insurance ID"},
            {"id": "f8", "type": "text", "label": "Primary Care Physician"},
            {"id": "f9", "type": "textarea", "label": "Chief Complaint / Reason for Visit", "required": True},
            {"id": "f10", "type": "textarea", "label": "Known Allergies"},
            {"id": "f11", "type": "textarea", "label": "Current Medications"},
            {"id": "f12", "type": "checkbox", "label": "HIPAA Notice Acknowledged", "required": True},
        ],
    },
    {
        "id": "tpl-hipaa-audit",
        "name": "HIPAA Compliance Audit",
        "description": "Healthcare data privacy compliance checklist",
        "category": "compliance",
        "industry": "Healthcare",
        "fields": [
            {"id": "f1", "type": "text", "label": "Organization Name", "required": True},
            {"id": "f2", "type": "text", "label": "Auditor Name", "required": True},
            {"id": "f3", "type": "date", "label": "Audit Date", "required": True},
            {"id": "f4", "type": "checkbox", "label": "Privacy Officer designated"},
            {"id": "f5", "type": "checkbox", "label": "Business Associate Agreements in place"},
            {"id": "f6", "type": "checkbox", "label": "Risk analysis completed"},
            {"id": "f7", "type": "checkbox", "label": "Workforce training completed"},
            {"id": "f8", "type": "checkbox", "label": "Breach notification procedures documented"},
            {"id": "f9", "type": "checkbox", "label": "PHI access audit logs maintained"},
            {"id": "f10", "type": "textarea", "label": "Findings / Gaps"},
            {"id": "f11", "type": "select", "label": "Overall Posture", "options": ["Compliant", "Partially Compliant", "Non-Compliant"]},
        ],
    },
    # ── Finance/Banking ───────────────────────────────────────────────────
    {
        "id": "tpl-kyc-aml",
        "name": "KYC / AML Intake",
        "description": "Know Your Customer and Anti-Money Laundering intake",
        "category": "finance",
        "industry": "Finance/Banking",
        "fields": [
            {"id": "f1", "type": "text", "label": "Customer Full Name", "required": True},
            {"id": "f2", "type": "date", "label": "Date of Birth", "required": True},
            {"id": "f3", "type": "text", "label": "Tax ID / SSN"},
            {"id": "f4", "type": "text", "label": "Government ID Number", "required": True},
            {"id": "f5", "type": "select", "label": "ID Type", "options": ["Passport", "Driver's License", "National ID", "Other"]},
            {"id": "f6", "type": "text", "label": "Country of Residence", "required": True},
            {"id": "f7", "type": "select", "label": "Customer Risk Rating", "options": ["Low", "Medium", "High"]},
            {"id": "f8", "type": "checkbox", "label": "PEP (Politically Exposed Person) check cleared"},
            {"id": "f9", "type": "checkbox", "label": "Sanctions screening passed"},
            {"id": "f10", "type": "textarea", "label": "Source of Funds"},
            {"id": "f11", "type": "file", "label": "Upload ID Document"},
        ],
    },
    {
        "id": "tpl-sox-control",
        "name": "SOX Control Assessment",
        "description": "Sarbanes-Oxley internal control evaluation",
        "category": "compliance",
        "industry": "Finance/Banking",
        "fields": [
            {"id": "f1", "type": "text", "label": "Control ID", "required": True},
            {"id": "f2", "type": "text", "label": "Control Owner", "required": True},
            {"id": "f3", "type": "text", "label": "Process / Sub-process"},
            {"id": "f4", "type": "select", "label": "Control Type", "options": ["Preventive", "Detective", "Corrective"]},
            {"id": "f5", "type": "select", "label": "Frequency", "options": ["Daily", "Weekly", "Monthly", "Quarterly", "Annual"]},
            {"id": "f6", "type": "select", "label": "Assessment Result", "options": ["Effective", "Deficiency", "Material Weakness"]},
            {"id": "f7", "type": "textarea", "label": "Evidence Description", "required": True},
            {"id": "f8", "type": "date", "label": "Assessment Date", "required": True},
            {"id": "f9", "type": "text", "label": "Reviewed By"},
        ],
    },
    # ── Legal ─────────────────────────────────────────────────────────────
    {
        "id": "tpl-contract-review",
        "name": "Contract Review Checklist",
        "description": "Legal contract review and approval checklist",
        "category": "legal",
        "industry": "Legal",
        "fields": [
            {"id": "f1", "type": "text", "label": "Contract Title", "required": True},
            {"id": "f2", "type": "text", "label": "Counterparty Name", "required": True},
            {"id": "f3", "type": "select", "label": "Contract Type", "options": ["NDA", "MSA", "SOW", "License", "Employment", "Other"]},
            {"id": "f4", "type": "date", "label": "Effective Date"},
            {"id": "f5", "type": "date", "label": "Expiration Date"},
            {"id": "f6", "type": "checkbox", "label": "Indemnification clause reviewed"},
            {"id": "f7", "type": "checkbox", "label": "Limitation of liability reviewed"},
            {"id": "f8", "type": "checkbox", "label": "IP ownership clauses reviewed"},
            {"id": "f9", "type": "checkbox", "label": "Data privacy obligations reviewed"},
            {"id": "f10", "type": "checkbox", "label": "Termination provisions reviewed"},
            {"id": "f11", "type": "textarea", "label": "Legal Counsel Notes"},
            {"id": "f12", "type": "select", "label": "Approval Status", "options": ["Approved", "Approved with Changes", "Rejected", "Pending"]},
        ],
    },
    {
        "id": "tpl-nda-intake",
        "name": "NDA Intake",
        "description": "Non-Disclosure Agreement request and intake",
        "category": "legal",
        "industry": "Legal",
        "fields": [
            {"id": "f1", "type": "text", "label": "Requesting Party", "required": True},
            {"id": "f2", "type": "text", "label": "Disclosing Party", "required": True},
            {"id": "f3", "type": "select", "label": "NDA Type", "options": ["Mutual", "One-Way (Disclosing)", "One-Way (Receiving)"]},
            {"id": "f4", "type": "textarea", "label": "Purpose of Disclosure", "required": True},
            {"id": "f5", "type": "date", "label": "Effective Date"},
            {"id": "f6", "type": "select", "label": "Term", "options": ["1 Year", "2 Years", "3 Years", "5 Years", "Indefinite"]},
            {"id": "f7", "type": "email", "label": "Signatory Email", "required": True},
            {"id": "f8", "type": "text", "label": "Signatory Title"},
        ],
    },
    # ── HR ────────────────────────────────────────────────────────────────
    {
        "id": "tpl-employee-onboarding",
        "name": "Employee Onboarding",
        "description": "New employee onboarding checklist and information collection",
        "category": "hr",
        "industry": "HR",
        "fields": [
            {"id": "f1", "type": "text", "label": "Employee Full Name", "required": True},
            {"id": "f2", "type": "email", "label": "Personal Email", "required": True},
            {"id": "f3", "type": "text", "label": "Job Title", "required": True},
            {"id": "f4", "type": "text", "label": "Department", "required": True},
            {"id": "f5", "type": "text", "label": "Manager Name", "required": True},
            {"id": "f6", "type": "date", "label": "Start Date", "required": True},
            {"id": "f7", "type": "checkbox", "label": "I-9 / Employment Eligibility completed"},
            {"id": "f8", "type": "checkbox", "label": "Direct deposit form submitted"},
            {"id": "f9", "type": "checkbox", "label": "Benefits enrollment completed"},
            {"id": "f10", "type": "checkbox", "label": "IT access / equipment requested"},
            {"id": "f11", "type": "checkbox", "label": "Security awareness training scheduled"},
            {"id": "f12", "type": "checkbox", "label": "Employee handbook acknowledged"},
            {"id": "f13", "type": "textarea", "label": "Additional Notes"},
        ],
    },
    {
        "id": "tpl-performance-review",
        "name": "Performance Review",
        "description": "Annual or mid-year employee performance evaluation",
        "category": "hr",
        "industry": "HR",
        "fields": [
            {"id": "f1", "type": "text", "label": "Employee Name", "required": True},
            {"id": "f2", "type": "text", "label": "Reviewer Name", "required": True},
            {"id": "f3", "type": "select", "label": "Review Period", "options": ["Q1", "Q2", "Q3", "Q4", "Mid-Year", "Annual"]},
            {"id": "f4", "type": "select", "label": "Overall Rating", "options": ["Exceptional", "Exceeds Expectations", "Meets Expectations", "Needs Improvement", "Unsatisfactory"], "required": True},
            {"id": "f5", "type": "textarea", "label": "Key Accomplishments", "required": True},
            {"id": "f6", "type": "textarea", "label": "Areas for Improvement"},
            {"id": "f7", "type": "textarea", "label": "Development Goals for Next Period"},
            {"id": "f8", "type": "textarea", "label": "Manager Comments"},
            {"id": "f9", "type": "date", "label": "Review Date", "required": True},
        ],
    },
    # ── IT/Security ───────────────────────────────────────────────────────
    {
        "id": "tpl-change-advisory",
        "name": "Change Advisory Board",
        "description": "CAB change request for IT infrastructure changes",
        "category": "it_security",
        "industry": "IT/Security",
        "fields": [
            {"id": "f1", "type": "text", "label": "Change Request ID", "required": True},
            {"id": "f2", "type": "text", "label": "Requestor Name", "required": True},
            {"id": "f3", "type": "select", "label": "Change Type", "options": ["Standard", "Normal", "Emergency", "Expedited"]},
            {"id": "f4", "type": "select", "label": "Risk Level", "options": ["Low", "Medium", "High", "Critical"], "required": True},
            {"id": "f5", "type": "textarea", "label": "Change Description", "required": True},
            {"id": "f6", "type": "textarea", "label": "Business Justification", "required": True},
            {"id": "f7", "type": "textarea", "label": "Implementation Plan"},
            {"id": "f8", "type": "textarea", "label": "Rollback Plan", "required": True},
            {"id": "f9", "type": "textarea", "label": "Testing Plan"},
            {"id": "f10", "type": "date", "label": "Planned Start Date"},
            {"id": "f11", "type": "date", "label": "Planned End Date"},
            {"id": "f12", "type": "text", "label": "CAB Reviewer"},
        ],
    },
    {
        "id": "tpl-pentest-authorization",
        "name": "Penetration Test Authorization",
        "description": "Rules of engagement and authorization for a penetration test",
        "category": "security",
        "industry": "IT/Security",
        "fields": [
            {"id": "f1", "type": "text", "label": "System / Target Name", "required": True},
            {"id": "f2", "type": "text", "label": "System Owner", "required": True},
            {"id": "f3", "type": "text", "label": "Testing Firm / Tester", "required": True},
            {"id": "f4", "type": "date", "label": "Test Start Date", "required": True},
            {"id": "f5", "type": "date", "label": "Test End Date", "required": True},
            {"id": "f6", "type": "textarea", "label": "In-Scope IP Ranges / URLs", "required": True},
            {"id": "f7", "type": "textarea", "label": "Out-of-Scope Items"},
            {"id": "f8", "type": "multiselect", "label": "Test Types Authorized", "options": ["Network", "Web Application", "Social Engineering", "Physical", "Wireless", "Cloud"]},
            {"id": "f9", "type": "checkbox", "label": "DoS attacks authorized"},
            {"id": "f10", "type": "text", "label": "Emergency Contact (24/7)"},
            {"id": "f11", "type": "checkbox", "label": "Authorization Signed by ISSO/AO", "required": True},
        ],
    },
    # ── Construction/Engineering ──────────────────────────────────────────
    {
        "id": "tpl-site-inspection",
        "name": "Site Inspection",
        "description": "Construction or facility site inspection report",
        "category": "construction",
        "industry": "Construction/Engineering",
        "fields": [
            {"id": "f1", "type": "text", "label": "Site Name / Location", "required": True},
            {"id": "f2", "type": "date", "label": "Inspection Date", "required": True},
            {"id": "f3", "type": "text", "label": "Inspector Name", "required": True},
            {"id": "f4", "type": "select", "label": "Inspection Type", "options": ["Safety", "Quality", "Progress", "Final", "Compliance"]},
            {"id": "f5", "type": "textarea", "label": "Observations", "required": True},
            {"id": "f6", "type": "textarea", "label": "Non-Conformances Found"},
            {"id": "f7", "type": "select", "label": "Overall Status", "options": ["Pass", "Pass with Conditions", "Fail"], "required": True},
            {"id": "f8", "type": "file", "label": "Upload Photos / Documents"},
            {"id": "f9", "type": "text", "label": "Contractor Representative"},
        ],
    },
    # ── Education ─────────────────────────────────────────────────────────
    {
        "id": "tpl-irb-protocol",
        "name": "IRB Protocol Submission",
        "description": "Institutional Review Board research protocol intake",
        "category": "education",
        "industry": "Education",
        "fields": [
            {"id": "f1", "type": "text", "label": "Principal Investigator", "required": True},
            {"id": "f2", "type": "email", "label": "PI Email", "required": True},
            {"id": "f3", "type": "text", "label": "Study Title", "required": True},
            {"id": "f4", "type": "select", "label": "Review Type", "options": ["Exempt", "Expedited", "Full Board"]},
            {"id": "f5", "type": "textarea", "label": "Research Purpose / Aims", "required": True},
            {"id": "f6", "type": "number", "label": "Estimated Number of Subjects"},
            {"id": "f7", "type": "checkbox", "label": "Informed consent will be obtained"},
            {"id": "f8", "type": "checkbox", "label": "Data will be de-identified"},
            {"id": "f9", "type": "textarea", "label": "Potential Risks to Subjects"},
            {"id": "f10", "type": "date", "label": "Proposed Start Date"},
        ],
    },
    # ── Manufacturing ─────────────────────────────────────────────────────
    {
        "id": "tpl-quality-control",
        "name": "Quality Control Audit",
        "description": "Manufacturing quality control inspection checklist",
        "category": "manufacturing",
        "industry": "Manufacturing",
        "fields": [
            {"id": "f1", "type": "text", "label": "Product / Part Name", "required": True},
            {"id": "f2", "type": "text", "label": "Batch / Lot Number", "required": True},
            {"id": "f3", "type": "date", "label": "Inspection Date", "required": True},
            {"id": "f4", "type": "text", "label": "Inspector Name", "required": True},
            {"id": "f5", "type": "number", "label": "Sample Size"},
            {"id": "f6", "type": "number", "label": "Defects Found"},
            {"id": "f7", "type": "select", "label": "Disposition", "options": ["Accept", "Reject", "Rework", "Hold"], "required": True},
            {"id": "f8", "type": "textarea", "label": "Defect Description"},
            {"id": "f9", "type": "textarea", "label": "Corrective Action Required"},
        ],
    },
    {
        "id": "tpl-supplier-qualification",
        "name": "Supplier Qualification",
        "description": "New supplier evaluation and qualification form",
        "category": "manufacturing",
        "industry": "Manufacturing",
        "fields": [
            {"id": "f1", "type": "text", "label": "Supplier Name", "required": True},
            {"id": "f2", "type": "text", "label": "Contact Name", "required": True},
            {"id": "f3", "type": "email", "label": "Contact Email", "required": True},
            {"id": "f4", "type": "text", "label": "Products / Services Offered", "required": True},
            {"id": "f5", "type": "checkbox", "label": "ISO 9001 certified"},
            {"id": "f6", "type": "checkbox", "label": "Financial references provided"},
            {"id": "f7", "type": "checkbox", "label": "Insurance certificates provided"},
            {"id": "f8", "type": "select", "label": "Qualification Status", "options": ["Approved", "Conditional", "Rejected", "Pending"]},
            {"id": "f9", "type": "textarea", "label": "Evaluator Notes"},
        ],
    },
    # ── Consulting/PMO ────────────────────────────────────────────────────
    {
        "id": "tpl-project-kickoff",
        "name": "Project Kickoff",
        "description": "Project initiation and kickoff meeting form",
        "category": "pmo",
        "industry": "Consulting/PMO",
        "fields": [
            {"id": "f1", "type": "text", "label": "Project Name", "required": True},
            {"id": "f2", "type": "text", "label": "Project Manager", "required": True},
            {"id": "f3", "type": "text", "label": "Executive Sponsor"},
            {"id": "f4", "type": "text", "label": "Client / Customer"},
            {"id": "f5", "type": "date", "label": "Kickoff Date", "required": True},
            {"id": "f6", "type": "date", "label": "Target Completion Date"},
            {"id": "f7", "type": "textarea", "label": "Project Objectives", "required": True},
            {"id": "f8", "type": "textarea", "label": "In-Scope Deliverables"},
            {"id": "f9", "type": "textarea", "label": "Out-of-Scope Items"},
            {"id": "f10", "type": "textarea", "label": "Key Risks"},
            {"id": "f11", "type": "textarea", "label": "Success Criteria"},
        ],
    },
    {
        "id": "tpl-lessons-learned",
        "name": "Lessons Learned",
        "description": "Post-project lessons learned and retrospective",
        "category": "pmo",
        "industry": "Consulting/PMO",
        "fields": [
            {"id": "f1", "type": "text", "label": "Project Name", "required": True},
            {"id": "f2", "type": "text", "label": "Facilitator"},
            {"id": "f3", "type": "date", "label": "Session Date", "required": True},
            {"id": "f4", "type": "select", "label": "Project Outcome", "options": ["Successful", "Partially Successful", "Unsuccessful"]},
            {"id": "f5", "type": "textarea", "label": "What Went Well", "required": True},
            {"id": "f6", "type": "textarea", "label": "What Could Be Improved", "required": True},
            {"id": "f7", "type": "textarea", "label": "Action Items for Future Projects"},
            {"id": "f8", "type": "textarea", "label": "Recommended Process Changes"},
            {"id": "f9", "type": "text", "label": "Submitted By"},
        ],
    },
]


def get_field_types() -> list[dict]:
    return FIELD_TYPES


def get_form_templates() -> list[dict]:
    return FORM_TEMPLATES


# ── Form CRUD ─────────────────────────────────────────────────────────


def _fields_to_json_schema(fields: list[dict]) -> dict:
    """Convert field list to JSON Schema (draft-07)."""
    properties = {}
    required = []
    for field in fields:
        ftype = field.get("type", "text")
        prop: dict[str, Any] = {"title": field.get("label", field.get("id", ""))}

        type_def = next((t for t in FIELD_TYPES if t["type"] == ftype), None)
        if type_def:
            prop["type"] = type_def["schema_type"]
            if "format" in type_def:
                prop["format"] = type_def["format"]

        if field.get("options"):
            if ftype == "multiselect":
                prop["items"] = {"type": "string", "enum": field["options"]}
            else:
                prop["enum"] = field["options"]

        if field.get("placeholder"):
            prop["description"] = field["placeholder"]

        properties[field.get("id", f"field_{len(properties)}")] = prop

        if field.get("required"):
            required.append(field.get("id", f"field_{len(properties) - 1}"))

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": properties,
        "required": required,
    }


def create_form(
    name: str,
    fields: list[dict],
    *,
    description: str = "",
    created_by: str = "studio",
    status: str = "draft",
) -> dict:
    form_id = _new_id("frm")
    schema = _fields_to_json_schema(fields)
    schema["_fields"] = fields  # Store original field defs for UI reconstruction
    _valid = {"draft", "published", "archived"}
    safe_status = status if status in _valid else "draft"

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_forms
               (form_id, name, description, schema_json, created_by,
                created_at, updated_at, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (form_id, name, description, json.dumps(schema), created_by, _now_iso(), _now_iso(), safe_status),
        )
        conn.commit()
        return {"status": "ok", "form_id": form_id, "schema": schema}
    finally:
        conn.close()


def list_forms(*, status: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_forms"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_form(form_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM studio_forms WHERE form_id = %s", (form_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_form(
    form_id: str,
    *,
    name: str | None = None,
    fields: list[dict] | None = None,
    description: str | None = None,
    status: str | None = None,
) -> dict:
    existing = get_form(form_id)
    if not existing:
        return {"status": "error", "error": "Form not found"}

    sets: list[str] = []
    vals: list[Any] = []

    if name is not None:
        sets.append("name = ?")
        vals.append(name)
    if description is not None:
        sets.append("description = ?")
        vals.append(description)
    if fields is not None:
        schema = _fields_to_json_schema(fields)
        schema["_fields"] = fields
        sets.append("schema_json = ?")
        vals.append(json.dumps(schema))
        sets.append("version = version + 1")
    if status is not None:
        sets.append("status = ?")
        vals.append(status)

    if not sets:
        return {"status": "ok", "form_id": form_id}

    sets.append("updated_at = ?")
    vals.append(_now_iso())
    vals.append(form_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE studio_forms SET {', '.join(sets)} WHERE form_id = %s",  # nosec B608 — column names are hardcoded
            vals,
        )
        conn.commit()
        return {"status": "ok", "form_id": form_id}
    finally:
        conn.close()


def delete_form(form_id: str) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM studio_forms WHERE form_id = %s", (form_id,))
        conn.commit()
        return {"status": "ok"} if cur.rowcount else {"status": "error", "error": "Not found"}
    finally:
        conn.close()


# ── Submissions ───────────────────────────────────────────────────────


def _validate_submission(fields: list[dict], data: dict) -> list[str]:
    """Validate submitted *data* against the form's field definitions.

    Returns a list of human-readable error strings (empty == valid). Enforces
    required fields (present + non-empty) and per-type shape for values that
    are supplied. Extra keys not in the schema are ignored (lenient).
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Submission data must be an object."]

    def _empty(v) -> bool:
        return v is None or v == "" or v == [] or v == {}

    for field in fields:
        if not isinstance(field, dict):
            continue
        fid = field.get("id")
        if not fid:
            continue
        label = field.get("label") or fid
        ftype = field.get("type", "text")
        present = fid in data
        value = data.get(fid)

        if field.get("required") and (not present or _empty(value)):
            errors.append(f"'{label}' is required.")
            continue

        # Only shape-check values that were actually supplied and non-empty.
        if not present or _empty(value):
            continue

        if ftype == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                try:
                    float(value)
                except (TypeError, ValueError):
                    errors.append(f"'{label}' must be a number.")
        elif ftype == "email":
            if not isinstance(value, str) or "@" not in value or "." not in value.split("@")[-1]:
                errors.append(f"'{label}' must be a valid email address.")
        elif ftype == "checkbox":
            if not isinstance(value, bool) and value not in ("true", "false", "on", "off", 0, 1, "0", "1"):
                errors.append(f"'{label}' must be a boolean.")
        elif ftype == "select":
            opts = field.get("options") or []
            if opts and value not in opts:
                errors.append(f"'{label}' must be one of the allowed options.")
        elif ftype == "multiselect":
            opts = field.get("options") or []
            if not isinstance(value, list):
                errors.append(f"'{label}' must be a list of selections.")
            elif opts and any(v not in opts for v in value):
                errors.append(f"'{label}' contains a value that is not an allowed option.")

    return errors


def submit_form(form_id: str, data: dict, *, submitted_by: str = "user") -> dict:
    form = get_form(form_id)
    if not form:
        return {"status": "error", "error": "Form not found"}

    # Server-side validation against the stored form schema.
    try:
        schema = json.loads(form.get("schema_json", "{}") or "{}")
        fields = schema.get("_fields", []) or []
    except (ValueError, TypeError):
        fields = []
    validation_errors = _validate_submission(fields, data)
    if validation_errors:
        return {
            "status": "error",
            "error": "Submission failed validation",
            "validation_errors": validation_errors,
        }

    sub_id = _new_id("sub")
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_form_submissions
               (submission_id, form_id, data_json, submitted_by, submitted_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (sub_id, form_id, json.dumps(data), submitted_by, _now_iso()),
        )
        conn.commit()
        return {"status": "ok", "submission_id": sub_id}
    finally:
        conn.close()


def list_submissions(form_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM studio_form_submissions WHERE form_id = %s ORDER BY submitted_at DESC",
            (form_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio Form Builder")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("field-types", help="List available field types")
    sub.add_parser("templates", help="List form templates")
    sub.add_parser("list", help="List saved forms")

    p_get = sub.add_parser("get", help="Get form by ID")
    p_get.add_argument("form_id")

    args = parser.parse_args()
    result: Any = None

    if args.command == "field-types":
        result = get_field_types()
    elif args.command == "templates":
        result = get_form_templates()
    elif args.command == "list":
        result = list_forms()
    elif args.command == "get":
        result = get_form(args.form_id)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
