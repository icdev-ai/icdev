#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 023: Create ad_news_patterns table.

Stores detected patterns across news items (e.g. recurring catalysts, severity
clusters, correlated headlines) for the FathomDesk Trading Oracle.

Table: ad_news_patterns
  - Append-only (NIST AU) — rows are never updated or deleted.
  - severity CHECK constraint values enumerated in NEWS_PATTERN_SEVERITIES.
"""

MIGRATION_ID = "023"
MIGRATION_NAME = "ad_news_patterns"
DESCRIPTION = (
    "Create ad_news_patterns — append-only table for detected patterns across "
    "news items. Supports the FathomDesk Trading Oracle pattern-detection layer."
)

# Single source of truth for severity values.
# The CHECK constraint below uses values from this tuple.
NEWS_PATTERN_SEVERITIES = ("info", "warn", "critical")


def up(conn) -> dict:
    """Apply migration: create ad_news_patterns table (idempotent)."""
    severity_check = ", ".join(f"'{s}'" for s in NEWS_PATTERN_SEVERITIES)

    actions = []
    errors = []

    create_stmt = f"""CREATE TABLE IF NOT EXISTS ad_news_patterns (
        id                TEXT PRIMARY KEY,
        pattern_type      TEXT,
        category          TEXT,
        severity          TEXT CHECK(severity IN ({severity_check})),
        evidence_item_ids TEXT,
        window_start      TEXT,
        window_end        TEXT,
        recommendation    TEXT,
        created_at        TEXT NOT NULL
    )"""

    try:
        conn.execute(create_stmt)
        actions.append("created_or_verified_ad_news_patterns")
    except Exception as exc:
        errors.append(f"ad_news_patterns: {exc}")

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_ad_news_patterns_cat_date ON ad_news_patterns (category, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ad_news_patterns_sev_date ON ad_news_patterns (severity, created_at DESC)",
    ]
    for idx_stmt in indexes:
        try:
            conn.execute(idx_stmt)
        except Exception as exc:
            errors.append(f"index_skipped: {exc}")

    conn.commit()
    return {
        "status": "applied" if not errors else "partial",
        "actions": actions,
        "errors": errors,
    }
