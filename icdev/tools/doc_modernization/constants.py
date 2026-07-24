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
    "stale_reference",       # reference that no longer resolves / decommissioned (incl. past its sunset_date)
    "expiring_reference",    # reference within N days of its sunset_date (proactive temporal warning)
    "superseded_standard",   # NIST rev / RFC / STIG replaced by a successor
    "defacto_divergence",    # deployment reality disagrees with curated catalog
    "catalog_gap",           # heavily deployed item missing from curated catalog
    "unreflected_change",    # an approved change document supersedes this doc's claims
    "unverifiable_evidence", # document has no evidence anchors — currency uncheckable
]

# Currency verdict for an extracted entity. Mirrors the CHECK constraint in
# migration 257 (tools/db/migrations/257_doc_modernization.sql).
CURRENCY_VERDICTS: list[str] = [
    "current", "deprecated", "eol", "retired", "divergent", "unknown",
]

# Finding lifecycle (mirrors migration 257 CHECK). Transitions are APPEND-ONLY:
# a state change is a new row whose supersedes_id points at the previous row
# for the same dedupe_key.
FINDING_STATES: list[str] = [
    "open",
    "redline_drafted",
    "accepted",
    "rejected",
    "resolved",
    "superseded",
    "stale",
]

# Catalog entry lifecycle (docmod_catalog_entries.status).
CATALOG_STATUSES: list[str] = ["approved", "deprecated", "retired"]

# Catalog entry provenance (docmod_catalog_entries.source).
CATALOG_SOURCES: list[str] = ["manual", "imported", "promoted_from_defacto"]

SEVERITIES: list[str] = ["info", "low", "medium", "high", "critical"]

# docmod_eol_products.source CHECK vocabulary (migration 257).
EOL_PRODUCT_SOURCES: list[str] = ["endoflife.date", "seed", "manual"]

# docmod_scan_runs vocab (migration 257 CHECKs).
SCAN_SCOPE_TYPES: list[str] = ["all", "collection", "doc"]
SCAN_TRIGGERS: list[str] = ["manual", "reflex", "daemon", "api"]

# docmod_catalog_audit.event_type vocabulary (migration 257 comment).
CATALOG_AUDIT_EVENTS: list[str] = [
    "create", "update", "status_change", "import", "promote",
]

# New KG entity types contributed via text_network.EXTRA_ENTITY_PATTERNS.
KG_ENTITY_TYPES: list[str] = [
    "hardware_model",
    "software_product",
    "protocol",
    "crypto_algorithm",
    "system_reference",      # a system/CI a document makes claims about
    "evidence_anchor",       # a citation link, not a thing named in the prose
    "tool_reference",        # a tool/command/platform an SOP tells the reader to run
    "architecture_pattern",  # a named structural pattern/technology a design doc prescribes
]

# Tables that must never be UPDATEd/DELETEd (registered in
# .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES by migration 257's PR).
# Exception documented in scanner.py: docmod_scan_runs counters/finished_at
# receive one completion UPDATE because findings FK-reference the run row,
# which therefore must exist before the scan's outcome is known.
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "docmod_findings",
    "docmod_scan_runs",
    "docmod_catalog_audit",
)

# Confidence bands mirror tools/quality/citation_grounding.classify_confidence.
CONF_INCLUDE: float = 0.7
CONF_ABSTAIN: float = 0.4
