# CUI // SP-CTI
"""Document Modernization Engine (docmod) — shared constants.

Import these everywhere instead of hard-coding values; SQL CHECK constraints
and validation derive from them.
"""
from __future__ import annotations

# Finding taxonomy — what kind of staleness a scanner detected.
FINDING_TYPES: list[str] = [
    "eol_hardware",          # device model past EOL/EOS
    "eol_software",          # software product/cycle past EOL
    "deprecated_tech",       # rulebook match (TLS 1.1, telnet, MD5, ...)
    "stale_reference",       # reference that no longer resolves / decommissioned
    "superseded_standard",   # NIST rev / RFC / STIG replaced by a successor
    "defacto_divergence",    # deployment reality disagrees with curated catalog
    "catalog_gap",           # heavily deployed item missing from curated catalog
]

# Currency verdict for an extracted entity.
CURRENCY_VERDICTS: list[str] = ["current", "aging", "deprecated", "eol", "unknown"]

# Finding lifecycle. Transitions are APPEND-ONLY: a state change is a new row
# whose supersedes_id points at the previous row for the same dedupe_key.
FINDING_STATES: list[str] = [
    "open",
    "redline_drafted",
    "accepted",
    "rejected",
    "superseded",
]

# Catalog entry lifecycle (docmod_catalog_entries.status).
CATALOG_STATUSES: list[str] = ["approved", "deprecated", "retired"]

# Catalog entry provenance (docmod_catalog_entries.source).
CATALOG_SOURCES: list[str] = ["manual", "imported", "promoted_from_defacto"]

SEVERITIES: list[str] = ["low", "medium", "high", "critical"]

# New KG entity types contributed via text_network.EXTRA_ENTITY_PATTERNS.
KG_ENTITY_TYPES: list[str] = [
    "hardware_model",
    "software_product",
    "protocol",
    "crypto_algorithm",
]

# Tables that must never be UPDATEd/DELETEd (mirrored into
# .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES).
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "docmod_findings",
    "docmod_scan_runs",
    "docmod_catalog_audit",
)

# Confidence bands mirror tools/quality/citation_grounding.classify_confidence.
CONF_INCLUDE: float = 0.7
CONF_ABSTAIN: float = 0.4
