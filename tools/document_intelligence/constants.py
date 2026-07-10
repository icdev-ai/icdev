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

APPEND_ONLY_TABLES: tuple[str, ...] = (
    "dic_versions",
    "dic_drift_events",
    "dic_acoic_regen_queue",
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
]

WRITEGUARD_MODES: list[str] = [
    "default",
    "standard_guide",
    "architecture_doc",
    "sop_runbook",
    "policy_language",
    "runbook",
]

TEMPLATE_TYPE_TO_WRITEGUARD_MODE: dict[str, str] = {
    "STANDARD_GUIDE": "standard_guide",
    "SOP": "sop_runbook",
    "RUNBOOK": "sop_runbook",
    "ARCH_NETWORK": "architecture_doc",
    "ARCH_APPLICATION": "architecture_doc",
    "ARCH_SYSTEM": "architecture_doc",
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
}
DOCGEN_DEFAULT_TEMPLATE: str = "ARCH_SYSTEM"
