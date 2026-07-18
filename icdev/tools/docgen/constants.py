# CUI // SP-CTI
"""IDR / DocGen shared constants.

Single source of truth for upload validation. The upload-type CHECK constraint
in the SQL migrations MUST match ``UPLOAD_TYPES`` (CLAUDE.md: "SQL CHECK
constraints: derive from Python constants, never hardcode").
"""
from __future__ import annotations

import os

# Upload types accepted by the analyzer router (workflow.stage2_analyze_upload).
# Keep in lockstep with the idr_uploads.upload_type CHECK constraint.
UPLOAD_TYPES: tuple[str, ...] = (
    "diagram",
    "doc",
    "config",
    "iac",
    "supplement",
    "email",
)

# Extension allowlist for uploaded files (cnr-doc-03). Executables / scripts and
# anything outside the documented analyzer input set are rejected at ingress.
ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset({
    # documents
    ".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".csv",
    # diagrams / images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".drawio", ".vsdx", ".xml",
    # config / IaC / structured data
    ".cfg", ".conf", ".ini", ".json", ".yaml", ".yml", ".tf", ".tfvars",
    ".toml", ".hcl", ".log",
    # email
    ".eml", ".msg",
})


def max_upload_bytes() -> int:
    """Per-file upload size cap in bytes.

    Defaults to 25 MiB. ``DOCGEN_MAX_UPLOAD_BYTES`` (or the platform-wide
    ``ICDEV_MAX_UPLOAD_BYTES`` set by cnr-plat-02) overrides it.
    """
    for env in ("DOCGEN_MAX_UPLOAD_BYTES", "ICDEV_MAX_UPLOAD_BYTES"):
        raw = os.environ.get(env)
        if raw:
            try:
                val = int(raw)
                if val > 0:
                    return val
            except ValueError:
                pass
    return 25 * 1024 * 1024
