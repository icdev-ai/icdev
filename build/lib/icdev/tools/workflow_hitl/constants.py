# CUI // SP-CTI
"""HITL Workflow Management — constants, enums, and defaults."""
from __future__ import annotations

from enum import Enum


class ApprovalPolicy(str, Enum):
    ANY_ONE    = "any_one"      # any one team member with the role can approve
    ALL_MUST   = "all_must"     # every team member with the role must approve
    MAJORITY   = "majority"     # >50% of role members must approve
    SEQUENTIAL = "sequential"   # members approve in order (assigned_to tracks current)


class StageType(str, Enum):
    AUTOMATED       = "automated"
    MANUAL          = "manual"
    EXTERNAL_EMAIL  = "external_email"
    EXTERNAL_TICKET = "external_ticket"
    EXTERNAL_WIKI   = "external_wiki"


class WfStatus(str, Enum):
    ACTIVE           = "active"
    WAITING_EXTERNAL = "waiting_external"
    APPROVED         = "approved"
    REJECTED         = "rejected"
    ESCALATED        = "escalated"
    CANCELLED        = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING     = "pending"
    APPROVED    = "approved"
    KICKBACK    = "kickback"
    CONDITIONAL = "conditional"
    ESCALATED   = "escalated"
    SKIPPED     = "skipped"


class DocType(str, Enum):
    CHECKLIST     = "checklist"
    FORM          = "form"
    SOP_REFERENCE = "sop_reference"
    STANDARD      = "standard"    # AI-referenced design/naming/coding standard


# Default role labels per canvas type (engineer role)
CANVAS_ROLE_DEFAULTS: dict[str, str] = {
    "NDC": "engineer",
    "PDC": "engineer",
    "IDC": "engineer",
    "SDC": "security_engineer",
    "BDC": "security_engineer",
    "DDC": "data_engineer",
    "ODC": "engineer",
}

# Default workflow stages per canvas (minimal: build → review → approve)
DEFAULT_STAGES: list[dict] = [
    {"name": "build",   "step_type": "automated"},
    {"name": "review",  "step_type": "manual",    "role": "reviewer",
     "required_docs": [{"doc_template_id": "sys-dt-peer-review-checklist", "required": True}]},
    {"name": "approve", "step_type": "manual",    "role": "manager",
     "required_docs": [{"doc_template_id": "sys-dt-security-sign-off", "required": False}]},
]

DEFAULT_ROLES: dict[str, str] = {
    "build":   "engineer",
    "review":  "reviewer",
    "approve": "manager",
}

FEEDBACK_TYPES = ["correctness", "completeness", "clarity", "safety", "compliance"]
IMPROVEMENT_TAGS = [
    "missing_tests", "scope_creep", "incomplete_spec", "unclear_requirements",
    "missing_docs", "security_gap", "performance_issue", "naming_violation",
]

# Ontology mappings for the Knowledge Graph ontology bridge
WORKFLOW_HITL_ONTOLOGY_MAP: dict[str, str] = {
    "approval_gate":       "https://icdev.dev/ontology/pipeline#ApprovalGate",
    "review_stage":        "https://icdev.dev/ontology/pipeline#ReviewStage",
    "human_task":          "https://icdev.dev/ontology/pipeline#HumanTask",
    "automated_step":      "https://icdev.dev/ontology/pipeline#AutomatedStep",
    "feedback_loop":       "https://icdev.dev/ontology/pipeline#FeedbackLoop",
    "compliance_checkpoint": "https://icdev.dev/ontology/compliance#ComplianceCheckpoint",
}
