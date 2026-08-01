# CUI // SP-CTI
"""Workflow Forms Canvas — constants."""
from __future__ import annotations

INDUSTRY_CATEGORIES: list[str] = [
    "All",
    "Government/Federal",
    "Healthcare",
    "Finance/Banking",
    "Legal",
    "HR",
    "IT/Security",
    "Construction/Engineering",
    "Education",
    "Manufacturing",
    "Consulting/PMO",
    "Compliance",
    "Operations",
]

EXPORT_FORMATS: list[dict] = [
    {"id": "pptx", "label": "PowerPoint (.pptx)", "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    {"id": "pdf",  "label": "PDF (.pdf)",          "mime": "application/pdf"},
    {"id": "docx", "label": "Word (.docx)",         "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
]

FORM_STATUS_LABELS: dict[str, str] = {
    "draft":     "Draft",
    "published": "Published",
    "archived":  "Archived",
}

WORKFLOW_STATUS_LABELS: dict[str, str] = {
    "draft":   "Draft",
    "active":  "Active",
    "paused":  "Paused",
    "retired": "Retired",
}

DEFAULT_PRIMARY_COLOR = "#1a365d"
DEFAULT_SECONDARY_COLOR = "#c8a951"

# ── Upload / extraction bounds (cnr-wfc-02) ───────────────────────────────
# Reject uploads larger than this before they are read into memory.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB
# Hard cap on extracted document text kept in memory / passed downstream.
MAX_EXTRACT_CHARS = 200_000
