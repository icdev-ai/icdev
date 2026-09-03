# CUI // SP-CTI
"""DIC shared constants — import everywhere instead of hard-coding values."""
from __future__ import annotations

FRESHNESS_LEVELS: dict[str, int] = {"fresh": 7, "aging": 14, "stale": 30}  # days

WORKFLOW_STATES: list[str] = [
    "queued",
    "regenerating",
    "drafted",
    "pending_review",
    "approved",
    "rejected",
]

ORIGIN_TYPES: list[str] = ["human_authored", "ai_generated", "ai_assisted"]

CLASSIFICATION_LEVELS: list[str] = ["UNCLASSIFIED", "CUI", "SECRET", "TOP SECRET"]

ROLES: list[str] = ["admin", "reviewer", "editor", "viewer"]

SUPPORTED_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md",
    ".html", ".htm", ".png", ".jpg", ".jpeg", ".tiff",
}

CHUNK_EMBEDDING_BATCH_SIZE: int = 32
VECTOR_STORE_TIMEOUT: int = 30  # seconds

DIC_CANVAS_DB_ENV: str = "ICDEV_DIC_DB_URL"

# ── CoVe publish gate (agx-verify-01) ────────────────────────────────────────
#
# Chain-of-Verification re-interrogates each claim independently. It is the
# strongest anti-hallucination check available here and also the most
# expensive: it multiplies LLM calls per artifact, so it is OFF by default and
# enabled deliberately per deployment.
#
# ``cove_guard`` fails CLOSED on error — when the architecture raises (no
# provider reachable, budget exhausted) it returns blocked=True. That is right
# for a connected deployment and wrong for an air-gapped one, where it would
# block every approval on a check that never ran. Hence the second toggle: the
# operator declares whether an *unrunnable* gate blocks or warns. A gate that
# ran and found a defect always blocks regardless.
DIC_COVE_GATE_ENV: str = "ICDEV_DIC_COVE_GATE"          # "1" to enable; default off
DIC_COVE_ON_ERROR_ENV: str = "ICDEV_DIC_COVE_ON_ERROR"  # "block" | "warn" (default)
DIC_COVE_MAX_QUESTIONS_ENV: str = "ICDEV_DIC_COVE_MAX_QUESTIONS"  # default 5

# Append-only DIC tables. Nothing imports this today; the enforced list lives in
# .claude/hooks/pre_tool_use.py. Keep it TRUE anyway — a wrong entry here becomes
# a live bug the moment someone wires it up.
#
# dic_drift_events and dic_acoic_regen_queue were previously listed here and are
# NOT append-only: acoic.record_drift_event upserts drift events and marks them
# processed, and acoic._set_queue_state drives the regen queue's
# queued -> drafted -> approved state machine. They are mutable workflow tables
# holding current state, not audit logs. Enforcing append-only on them would
# break the HITL review flow. The audit evidence for those decisions is written
# to the append-only, hash-chained audit_trail instead (see
# acoic._review_fragment).
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "dic_versions",
    "dic_team_access",
    "dic_freshness_scans",
)

HANDOFF_SESSION_STATES: list[str] = ["open", "closed"]

HANDOFF_ITEM_KINDS: list[str] = ["interview", "generated_doc", "orphan_flag"]

HANDOFF_ITEM_STATES: list[str] = ["pending", "answered", "generated"]

# ── Tech Writer ───────────────────────────────────────────────────────────────

TEMPLATE_TYPES: list[str] = [
    "STANDARD_GUIDE",
    "SOP",
    "RUNBOOK",
    "ARCH_NETWORK",
    "ARCH_APPLICATION",
    "ARCH_SYSTEM",
    # rmf-wp-01: the whitepaper / RFP-volume document type. The CHECK on
    # dic_documents.template_type is REBUILT from this list by migration
    # 20260903100336 — add a type here and scaffold a migration that calls the
    # same rebuild; never respell the list in SQL.
    "WHITEPAPER",
]

WRITEGUARD_MODES: list[str] = [
    "default",
    "standard_guide",
    "architecture_doc",
    "sop_runbook",
    "policy_language",
    "runbook",
    # tools/writing/content_modes.py: "first paragraph contains an explicit
    # ask, recommendation, and quantified impact" — the whitepaper's BLUF.
    "bluf_exec",
]

TEMPLATE_TYPE_TO_WRITEGUARD_MODE: dict[str, str] = {
    "STANDARD_GUIDE": "standard_guide",
    "SOP": "sop_runbook",
    "RUNBOOK": "sop_runbook",
    "ARCH_NETWORK": "architecture_doc",
    "ARCH_APPLICATION": "architecture_doc",
    "ARCH_SYSTEM": "architecture_doc",
    "WHITEPAPER": "bluf_exec",
}

# docgen (IDR) doc_type -> Tech Writer template. Single source of truth for the
# docgen -> Tech Writer bridge; the client no longer carries its own map.
DOCGEN_DOCTYPE_TO_TEMPLATE: dict[str, str] = {
    "runbook": "RUNBOOK",
    "playbook": "RUNBOOK",
    "design_doc": "ARCH_SYSTEM",
    "api_design_doc": "ARCH_APPLICATION",
    "security_design": "ARCH_SYSTEM",
    "migration_plan": "RUNBOOK",
    "standard_guide": "STANDARD_GUIDE",
    "baseline": "STANDARD_GUIDE",
    "sop": "SOP",
    "policy_doc": "STANDARD_GUIDE",
    "change_request": "SOP",
    "assessment": "STANDARD_GUIDE",
    "adr": "ARCH_SYSTEM",
    # ATO structured types
    "ato_ssp": "ARCH_SYSTEM",
    "ssp": "ARCH_SYSTEM",
    "stig_checklist": "STANDARD_GUIDE",
    "poam": "STANDARD_GUIDE",
    "boundary_narrative": "ARCH_NETWORK",
    "evidence_package": "STANDARD_GUIDE",
    "whitepaper": "WHITEPAPER",
}
DOCGEN_DEFAULT_TEMPLATE: str = "ARCH_SYSTEM"

# Template id -> the section skeleton instantiation creates for it. Lives here
# rather than in blueprint.py (trust-struct-02) because it is now read by two
# consumers: the /api/templates/<id>/instantiate route, and
# tools/quality/outline_contract.py, which validates a draft against the same
# skeleton and must not import Flask to do it. One declaration, two readers —
# a required-section contract that is a COPY of what instantiation creates is
# a contract that goes stale the first time either side is edited.
TEMPLATE_SECTIONS: dict[str, list[str]] = {
    "acoic": ["Impact Assessment", "Affected Controls", "Regeneration Plan", "SSP Fragment", "Approval Gate"],
    "freshness-audit": ["Audit Scope", "Stale Document Inventory", "Remediation Plan", "Owner Assignments", "Timeline"],
    "airgap-ingest": ["Source Directory", "Ingest Pipeline Steps", "Verification Checklist", "Rollback Plan"],
    "hitl-review": ["Review Queue", "Reviewer Assignments", "Acceptance Criteria", "Escalation Path"],
    "sop-refresh": ["Current Procedure", "Change Summary", "Updated Steps", "Validation Criteria", "Rollback Plan"],
    "knowledge-handoff": ["SME Profile", "Knowledge Areas", "Interview Agenda", "Captured Artifacts", "Successor Onboarding"],
    # Tech Writer templates (migration 230)
    "STANDARD_GUIDE": [
        "Executive Summary", "Scope and Applicability", "Cloud Provider Overview",
        "Connectivity Patterns", "Security Controls", "Implementation Steps",
        "Operational Procedures", "Troubleshooting", "References",
    ],
    "SOP": [
        "Purpose", "Scope", "Responsibilities", "Prerequisites",
        "Procedure", "Verification", "Rollback", "References",
    ],
    "RUNBOOK": [
        "Overview", "Prerequisites", "Pre-flight Checks",
        "Procedure", "Verification Steps", "Rollback", "Escalation Path",
    ],
    "ARCH_NETWORK": [
        "Architecture Overview", "Network Topology", "Segmentation Strategy",
        "Traffic Flows", "Security Controls", "Diagrams", "Decision Log",
    ],
    "ARCH_APPLICATION": [
        "System Context", "Component Diagram", "API Contracts",
        "Data Flow", "Security Considerations", "Deployment Architecture", "Decision Log",
    ],
    "ARCH_SYSTEM": [
        "Mission and Goals", "Stakeholders", "System Boundary",
        "Key Components", "Interfaces", "Quality Attributes", "Decision Log",
    ],
    # rmf-wp-01. Nine sections, deliberately MORE than the six the freeform
    # outline used to be sliced at: a skeleton that fit under the old cap could
    # never have shown the cap was gone. "Evidence and Measured Results" is
    # where the honesty rails bite — a whitepaper claim without a denominator
    # is a claim the RMF card series refuses everywhere else.
    "WHITEPAPER": [
        "Executive Summary", "Problem Statement", "Background and Context",
        "Proposed Approach", "Technical Architecture", "Evidence and Measured Results",
        "Security and Compliance Considerations", "Recommendations and Next Steps",
        "References",
    ],
}
