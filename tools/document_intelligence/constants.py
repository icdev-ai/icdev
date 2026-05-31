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
