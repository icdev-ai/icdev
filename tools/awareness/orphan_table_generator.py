#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 017 generator — auto-generate CREATE TABLE DDL for orphan tables.

Reads orphan_db_table findings from the gap detector (or re-runs the rule
live), then for each distinct table name:
  1. Greps every INSERT INTO <t> (col1, col2, ...) occurrence across tools/
  2. AST-walks Python files to extract column lists from string literals
  3. Infers column types from naming conventions (id->TEXT PK, _at->TEXT,
     _count/_score/_id suffix heuristics, etc.)
  4. Falls back to TEXT for unknown columns
  5. Outputs tools/db/migrations/017_orphan_tables/up.py with idempotent
     CREATE TABLE IF NOT EXISTS for every inferred table

This is a REVIEW GATE — the generated DDL must be reviewed before applying.
Run with --apply only after human review (or CI review gate passing).

Usage:
    python tools/awareness/orphan_table_generator.py --generate [--dry-run] [--json]
    python tools/awareness/orphan_table_generator.py --apply [--json]
    python tools/awareness/orphan_table_generator.py --stats [--json]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

MIGRATION_DIR = BASE_DIR / "tools" / "db" / "migrations" / "017_orphan_tables"
TOOLS_DIR = BASE_DIR / "tools"

# Tables provided by the DBMS — never create these
SYSTEM_TABLES: Set[str] = {
    "information_schema", "pg_catalog", "pg_tables", "pg_indexes",
    "pg_class", "pg_namespace", "sqlite_master", "sqlite_temp_master",
    "sqlite_sequence", "sys", "dual",
}

# ---------------------------------------------------------------------------
# Column type inference heuristics
# ---------------------------------------------------------------------------

def _infer_type(col: str) -> str:
    """Infer SQLite column type from column name heuristics."""
    col_lower = col.lower()

    # Primary key — always TEXT (ICDEV uses UUID/slug PKs)
    if col_lower == "id":
        return "TEXT PRIMARY KEY"

    # Timestamps — stored as ISO-8601 TEXT
    if col_lower.endswith(("_at", "_date", "_time", "_ts", "_timestamp")):
        return "TEXT"

    # Integer-like semantics
    if col_lower.endswith(("_count", "_num", "_number", "_size", "_length",
                           "_port", "_version", "_index", "_offset", "_limit",
                           "_total", "_bytes")):
        return "INTEGER DEFAULT 0"

    # Numeric/float semantics
    if col_lower.endswith(("_score", "_rate", "_ratio", "_pct", "_percent",
                           "_weight", "_confidence", "_threshold", "_avg",
                           "_mean", "_rating")):
        return "REAL DEFAULT 0.0"

    # Boolean semantics
    if col_lower.startswith(("is_", "has_", "can_", "enabled", "active",
                             "approved", "verified", "deleted", "hidden")):
        return "INTEGER DEFAULT 0"  # SQLite boolean = INTEGER
    if col_lower in ("enabled", "active", "approved", "verified",
                     "deleted", "hidden", "locked", "published"):
        return "INTEGER DEFAULT 0"

    # JSON blobs
    if col_lower in ("detail", "details", "metadata", "config", "settings",
                     "extra", "data", "payload", "context", "tags", "labels",
                     "params", "options", "attributes", "properties",
                     "capabilities", "agents", "components", "alerts"):
        return "TEXT DEFAULT '{}'"

    # Foreign keys (FK suffix)
    if col_lower.endswith("_id"):
        return "TEXT"

    # Status / classification / type enum-like columns
    if col_lower in ("status", "state", "type", "kind", "category",
                     "classification", "priority", "severity", "level",
                     "phase", "stage", "mode", "role", "purpose",
                     "source", "target", "action", "event_type",
                     "artifact_type", "prediction_type", "lens_name",
                     "resolution_type", "recommendation_type", "risk_type",
                     "probe_type", "task_type", "executor_type", "cloud_provider",
                     "impact_level", "framework", "method", "format",
                     "language", "engine", "provider", "region", "app_key",
                     "module_slug", "skill_name", "interest", "organization"):
        return "TEXT DEFAULT ''"

    # Content / text columns
    if col_lower in ("content", "body", "text", "description", "summary",
                     "message", "title", "name", "label", "slug", "key",
                     "value", "path", "url", "endpoint", "command",
                     "query", "output", "result", "recommendation",
                     "resolution_action", "error_pattern", "pattern_name",
                     "grievance", "notes", "reason", "hash",
                     "blueprint_hash", "query_hash", "full_blueprint",
                     "result_data", "detail", "report", "plan",
                     "instruction", "input_text", "output_text",
                     "error_message", "stack_trace", "log"):
        return "TEXT DEFAULT ''"

    # Anything else defaults to TEXT
    return "TEXT"


# ---------------------------------------------------------------------------
# Schema inference from INSERT INTO statements
# ---------------------------------------------------------------------------

_INSERT_RE = re.compile(
    r"INSERT\s+(?:OR\s+(?:REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\s+)?"
    r"INTO\s+([a-zA-Z_]\w*)\s*\(([^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)

_SQL_HEAD_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*"
    r"(?:"
    r"CREATE\s+(?:TABLE|UNIQUE\s+INDEX|INDEX|VIEW|TRIGGER)\b|"
    r"INSERT\s+INTO\s+\w|"
    r"INSERT\s+OR\s+(?:REPLACE|IGNORE)\s+INTO\s+\w|"
    r"UPDATE\s+\w+\s+SET\b|"
    r"DELETE\s+FROM\s+\w|"
    r"SELECT\s+(?:\*|DISTINCT\b|ALL\b|COUNT\s*\(|[\w\.]+\s*(?:,|\(|FROM\b))|"
    r"WITH\s+\w+\s+AS\s*\(|"
    r"ALTER\s+TABLE\s+\w|"
    r"DROP\s+(?:TABLE|INDEX|VIEW|TRIGGER)\s+(?:IF\s+EXISTS\s+)?\w|"
    r"REPLACE\s+INTO\s+\w|"
    r"PRAGMA\s+\w"
    r")",
    re.IGNORECASE,
)


def _is_likely_sql(s: str) -> bool:
    if not s or len(s) < 15:
        return False
    return bool(_SQL_HEAD_RE.match(s))


def _extract_string_literals(py_path: Path) -> List[str]:
    src = py_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [src]
    strings: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    return strings


def _walk_py(base: Path):
    _excluded = {"__pycache__", ".git", "node_modules", "venv", "env", ".tmp"}
    for p in base.rglob("*.py"):
        if any(part in _excluded for part in p.parts):
            continue
        yield p


def _infer_columns_for_table(table: str) -> List[Tuple[str, str]]:
    """Scan all SQL string literals in tools/ and extract INSERT column lists.

    Returns a list of (column_name, sqlite_type) tuples. If no INSERT is found,
    returns a minimal fallback schema: (id TEXT PRIMARY KEY, created_at TEXT).
    """
    seen: Dict[str, str] = {}  # col_name -> type (first occurrence wins)

    for py in _walk_py(TOOLS_DIR):
        try:
            literals = _extract_string_literals(py)
        except Exception:
            continue
        for lit in literals:
            if not _is_likely_sql(lit):
                continue
            for m in _INSERT_RE.finditer(lit):
                if m.group(1).lower() != table.lower():
                    continue
                raw_cols = m.group(2)
                cols = [
                    c.strip().strip('"').strip("'").strip("`")
                    for c in raw_cols.split(",")
                ]
                for col in cols:
                    col = col.strip()
                    if col and col not in seen:
                        seen[col] = _infer_type(col)

    if not seen:
        # No INSERT found — provide minimal schema.  Tables reached only via
        # SELECT or UPDATE still need to exist on fresh installs.
        seen["id"] = "TEXT PRIMARY KEY"
        seen["created_at"] = "TEXT"

    # Ensure id is always first if present
    result: List[Tuple[str, str]] = []
    if "id" in seen:
        result.append(("id", seen["id"]))
    for col, col_type in seen.items():
        if col != "id":
            result.append((col, col_type))

    return result


# ---------------------------------------------------------------------------
# Orphan table discovery
# ---------------------------------------------------------------------------

def _get_orphan_tables() -> List[Dict[str, Any]]:
    """Re-run the gap detector orphan_db_table rule and return all findings
    (bypassing the 25-result cap by calling the internal function directly).
    """
    try:
        # Re-implement without the 25-result cap
        from tools.awareness.gap_detector import (  # noqa: PLC0415
            _extract_py_string_literals,
            _is_likely_sql,
            _strip_sql_comments,
        )
    except ImportError as exc:
        print(f"[ERROR] Cannot import gap_detector: {exc}", file=sys.stderr)
        return []

    import re as _re

    create_re = _re.compile(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-zA-Z_]\w*)",
        _re.IGNORECASE,
    )
    insert_re = _re.compile(
        r"INSERT\s+(?:OR\s+(?:REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\s+)?"
        r"INTO\s+([a-zA-Z_]\w*)\s*"
        r"(?=\(|VALUES\b|SELECT\b|DEFAULT\s+VALUES\b)",
        _re.IGNORECASE,
    )
    from_re = _re.compile(r"\bFROM\s+([a-zA-Z_]\w*)\b", _re.IGNORECASE)

    created: Set[str] = set()
    referenced: Dict[str, str] = {}

    for py in _walk_py(TOOLS_DIR):
        try:
            for sql in _extract_py_string_literals(py):
                if not _is_likely_sql(sql):
                    continue
                sql_clean = _strip_sql_comments(sql)
                for m in create_re.finditer(sql_clean):
                    created.add(m.group(1).lower())
        except Exception:
            continue

    for sql_path in TOOLS_DIR.rglob("*.sql"):
        if any(part in {"__pycache__", ".git"} for part in sql_path.parts):
            continue
        try:
            src = _strip_sql_comments(
                sql_path.read_text(encoding="utf-8", errors="replace")
            )
            for m in create_re.finditer(src):
                created.add(m.group(1).lower())
        except Exception:
            continue

    for py in _walk_py(TOOLS_DIR):
        rel = str(py.relative_to(BASE_DIR)).replace("\\", "/")
        try:
            for sql in _extract_py_string_literals(py):
                if not _is_likely_sql(sql):
                    continue
                sql_clean = _strip_sql_comments(sql)
                for m in insert_re.finditer(sql_clean):
                    t = m.group(1).lower()
                    if t not in referenced:
                        referenced[t] = rel
                for m in from_re.finditer(sql_clean):
                    t = m.group(1).lower()
                    if t not in referenced:
                        referenced[t] = rel
        except Exception:
            continue

    for sql_path in TOOLS_DIR.rglob("*.sql"):
        if any(part in {"__pycache__", ".git"} for part in sql_path.parts):
            continue
        rel = str(sql_path.relative_to(BASE_DIR)).replace("\\", "/")
        try:
            src = _strip_sql_comments(
                sql_path.read_text(encoding="utf-8", errors="replace")
            )
            for m in insert_re.finditer(src):
                t = m.group(1).lower()
                if t not in referenced:
                    referenced[t] = rel
            for m in from_re.finditer(src):
                t = m.group(1).lower()
                if t not in referenced:
                    referenced[t] = rel
        except Exception:
            continue

    findings = []
    for t, ref_file in sorted(referenced.items()):
        if t in created:
            continue
        if t in SYSTEM_TABLES:
            continue
        if len(t) < 3:
            continue
        findings.append({"table": t, "first_reference": ref_file})

    return findings


# ---------------------------------------------------------------------------
# Migration generator
# ---------------------------------------------------------------------------

_KNOWN_SCHEMAS: Dict[str, List[Tuple[str, str]]] = {
    # Schemas derived from INSERT INTO statements found in tools/
    # These override the auto-inferred schemas for accuracy.
    "agent_chat_turns": [
        ("id", "TEXT PRIMARY KEY"),
        ("session_id", "TEXT NOT NULL"),
        ("role", "TEXT DEFAULT 'agent'"),
        ("content", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "agent_registry": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT DEFAULT ''"),
        ("url", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'unknown'"),
        ("last_heartbeat", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "app_blueprints": [
        ("id", "TEXT PRIMARY KEY"),
        ("app_name", "TEXT DEFAULT ''"),
        ("classification", "TEXT DEFAULT 'public'"),
        ("impact_level", "TEXT DEFAULT 'IL4'"),
        ("capabilities", "TEXT DEFAULT '[]'"),
        ("agents", "TEXT DEFAULT '[]'"),
        ("cloud_provider", "TEXT DEFAULT 'aws'"),
        ("blueprint_hash", "TEXT DEFAULT ''"),
        ("generated_at", "TEXT"),
        ("full_blueprint", "TEXT DEFAULT '{}'"),
    ],
    "awareness_component_health": [
        ("id", "TEXT PRIMARY KEY"),
        ("node_id", "TEXT NOT NULL"),
        ("probe_type", "TEXT NOT NULL"),
        ("status", "TEXT DEFAULT 'unknown'"),
        ("detail", "TEXT DEFAULT '{}'"),
        ("probed_at", "TEXT"),
    ],
    "contact_submissions": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT DEFAULT ''"),
        ("email", "TEXT DEFAULT ''"),
        ("organization", "TEXT DEFAULT ''"),
        ("role", "TEXT DEFAULT ''"),
        ("interest", "TEXT DEFAULT ''"),
        ("message", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'new'"),
        ("notes", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "dh_enrichment_cache": [
        ("id", "TEXT PRIMARY KEY"),
        ("source", "TEXT DEFAULT ''"),
        ("query_hash", "TEXT DEFAULT ''"),
        ("result_data", "TEXT DEFAULT '{}'"),
        ("expires_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "ft_training_pairs": [
        ("id", "TEXT PRIMARY KEY"),
        ("dataset_id", "TEXT DEFAULT ''"),
        ("instruction", "TEXT DEFAULT ''"),
        ("input_text", "TEXT DEFAULT ''"),
        ("output_text", "TEXT DEFAULT ''"),
        ("purpose", "TEXT DEFAULT 'general'"),
        ("approved", "INTEGER DEFAULT 0"),
        ("source", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "harness_trace_recommendations": [
        ("id", "TEXT PRIMARY KEY"),
        ("session_id", "TEXT DEFAULT ''"),
        ("recommendation_type", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'info'"),
        ("message", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "ndc_audit": [
        ("id", "TEXT PRIMARY KEY"),
        ("design_id", "TEXT DEFAULT ''"),
        # "user" is a PostgreSQL reserved word — must stay quoted everywhere
        # (CREATE here, INSERT in tools/network/blueprint_helpers.py).
        ('"user"', "TEXT DEFAULT ''"),
        ("action", "TEXT DEFAULT ''"),
        ("detail", "TEXT DEFAULT ''"),
        ("classification", "TEXT DEFAULT 'public'"),
        ("created_at", "TEXT"),
    ],
    "notifications": [
        ("id", "TEXT PRIMARY KEY"),
        ("title", "TEXT DEFAULT ''"),
        ("message", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'info'"),
        ("source", "TEXT DEFAULT ''"),
        ("read_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "proposal_compliance_items": [
        ("id", "TEXT PRIMARY KEY"),
        ("section_id", "TEXT DEFAULT ''"),
        ("requirement_id", "TEXT DEFAULT ''"),
        ("requirement_text", "TEXT DEFAULT ''"),
        ("compliance_status", "TEXT DEFAULT 'not_started'"),
        ("compliance_notes", "TEXT DEFAULT ''"),
        ("evidence_reference", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "risk_monitor_history": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("risk_type", "TEXT DEFAULT ''"),
        ("risk_score", "REAL DEFAULT 0.0"),
        ("severity", "TEXT DEFAULT 'low'"),
        ("components", "TEXT DEFAULT '[]'"),
        ("alerts", "TEXT DEFAULT '[]'"),
        ("calculated_at", "TEXT"),
    ],
    "self_healing_patterns": [
        ("id", "TEXT PRIMARY KEY"),
        ("pattern_name", "TEXT DEFAULT ''"),
        ("error_pattern", "TEXT DEFAULT ''"),
        ("resolution_type", "TEXT DEFAULT ''"),
        ("resolution_action", "TEXT DEFAULT ''"),
        ("confidence", "REAL DEFAULT 0.0"),
        ("success_count", "INTEGER DEFAULT 0"),
        ("failure_count", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    # Tables that only appear in SELECT — minimal schema
    "ai_model_cards": [
        ("id", "TEXT PRIMARY KEY"),
        ("model_name", "TEXT DEFAULT ''"),
        ("provider", "TEXT DEFAULT ''"),
        ("version", "TEXT DEFAULT ''"),
        ("description", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
    ],
    "ato_packages": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("authorization_status", "TEXT DEFAULT 'pending'"),
        ("impact_level", "TEXT DEFAULT 'IL4'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "autoresearch_experiments": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("result", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
    ],
    "boundary_assessments": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("findings", "TEXT DEFAULT '[]'"),
        ("created_at", "TEXT"),
    ],
    "code_quality_findings": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("rule_id", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'low'"),
        ("message", "TEXT DEFAULT ''"),
        ("file_path", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "compliance_artifacts": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("artifact_type", "TEXT DEFAULT ''"),
        ("content", "TEXT DEFAULT ''"),
        ("classification", "TEXT DEFAULT 'public'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "control_mappings": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("control_id", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'not_implemented'"),
        ("evidence", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "cpmp_compliance_items": [
        ("id", "TEXT PRIMARY KEY"),
        ("contract_id", "TEXT DEFAULT ''"),
        ("control_id", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'not_assessed'"),
        ("notes", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "creative_gaps": [
        ("id", "TEXT PRIMARY KEY"),
        ("gap_type", "TEXT DEFAULT ''"),
        ("description", "TEXT DEFAULT ''"),
        ("priority", "TEXT DEFAULT 'medium'"),
        ("status", "TEXT DEFAULT 'open'"),
        ("created_at", "TEXT"),
    ],
    "crosswalk_results": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("source_framework", "TEXT DEFAULT ''"),
        ("target_framework", "TEXT DEFAULT ''"),
        ("mapping", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
    ],
    "decision_records": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("title", "TEXT DEFAULT ''"),
        ("decision", "TEXT DEFAULT ''"),
        ("rationale", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'proposed'"),
        ("created_at", "TEXT"),
    ],
    "deploy_history": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("environment", "TEXT DEFAULT 'staging'"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("deployed_by", "TEXT DEFAULT ''"),
        ("deployed_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "genesis_knowledge_packets": [
        ("id", "TEXT PRIMARY KEY"),
        ("app_key", "TEXT DEFAULT 'icdev'"),
        ("packet_type", "TEXT DEFAULT ''"),
        ("content", "TEXT DEFAULT '{}'"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("created_at", "TEXT"),
    ],
    "genesis_runs": [
        ("id", "TEXT PRIMARY KEY"),
        ("app_key", "TEXT DEFAULT 'icdev'"),
        ("status", "TEXT DEFAULT 'running'"),
        ("started_at", "TEXT"),
        ("completed_at", "TEXT"),
        ("result", "TEXT DEFAULT '{}'"),
    ],
    "gitlab_pipeline_runs": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("pipeline_id", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("branch", "TEXT DEFAULT 'main'"),
        ("started_at", "TEXT"),
        ("finished_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "intake_gaps": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("gap_type", "TEXT DEFAULT ''"),
        ("description", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'medium'"),
        ("status", "TEXT DEFAULT 'open'"),
        ("created_at", "TEXT"),
    ],
    "legacy_apps": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT DEFAULT ''"),
        ("language", "TEXT DEFAULT ''"),
        ("framework", "TEXT DEFAULT ''"),
        ("metadata", "TEXT DEFAULT '{}'"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
    ],
    "mkt_feedback": [
        ("id", "TEXT PRIMARY KEY"),
        ("module_slug", "TEXT DEFAULT ''"),
        ("rating", "REAL DEFAULT 0.0"),
        ("comment", "TEXT DEFAULT ''"),
        ("user_id", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "mkt_licenses": [
        ("id", "TEXT PRIMARY KEY"),
        ("module_slug", "TEXT DEFAULT ''"),
        ("tenant_id", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'active'"),
        ("issued_at", "TEXT"),
        ("expires_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "nc_governance_reviews": [
        ("id", "TEXT PRIMARY KEY"),
        ("design_id", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("reviewer", "TEXT DEFAULT ''"),
        ("notes", "TEXT DEFAULT ''"),
        ("reviewed_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "ndc_designs": [
        ("id", "TEXT PRIMARY KEY"),
        ("name", "TEXT DEFAULT ''"),
        ("classification", "TEXT DEFAULT 'public'"),
        ("status", "TEXT DEFAULT 'draft'"),
        ("metadata", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "page_agent_routes": [
        ("id", "TEXT PRIMARY KEY"),
        ("keyword", "TEXT DEFAULT ''"),
        ("route", "TEXT DEFAULT ''"),
        ("agent_name", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "pg_contract_vehicles": [
        ("id", "TEXT PRIMARY KEY"),
        ("vehicle_name", "TEXT DEFAULT ''"),
        ("vehicle_type", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
    ],
    "pg_cost_line_items": [
        ("id", "TEXT PRIMARY KEY"),
        ("cost_volume_id", "TEXT DEFAULT ''"),
        ("category", "TEXT DEFAULT ''"),
        ("description", "TEXT DEFAULT ''"),
        ("amount", "REAL DEFAULT 0.0"),
        ("unit", "TEXT DEFAULT ''"),
        ("quantity", "REAL DEFAULT 1.0"),
        ("created_at", "TEXT"),
    ],
    "pg_extension": [
        ("name", "TEXT PRIMARY KEY"),
        ("default_version", "TEXT DEFAULT ''"),
        ("installed_version", "TEXT DEFAULT ''"),
        ("comment", "TEXT DEFAULT ''"),
    ],
    "pg_proposal_knowledge_base": [
        ("id", "TEXT PRIMARY KEY"),
        ("proposal_id", "TEXT DEFAULT ''"),
        ("content", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT ''"),
        ("embedding", "TEXT DEFAULT '[]'"),
        ("created_at", "TEXT"),
    ],
    "pg_stat_user_indexes": [
        ("indexrelid", "INTEGER PRIMARY KEY"),
        ("schemaname", "TEXT DEFAULT ''"),
        ("relname", "TEXT DEFAULT ''"),
        ("indexrelname", "TEXT DEFAULT ''"),
        ("idx_scan", "INTEGER DEFAULT 0"),
        ("idx_tup_read", "INTEGER DEFAULT 0"),
        ("idx_tup_fetch", "INTEGER DEFAULT 0"),
    ],
    "pg_stat_user_tables": [
        ("relid", "INTEGER PRIMARY KEY"),
        ("schemaname", "TEXT DEFAULT ''"),
        ("relname", "TEXT DEFAULT ''"),
        ("seq_scan", "INTEGER DEFAULT 0"),
        ("seq_tup_read", "INTEGER DEFAULT 0"),
        ("n_live_tup", "INTEGER DEFAULT 0"),
        ("n_dead_tup", "INTEGER DEFAULT 0"),
        ("last_vacuum", "TEXT"),
        ("last_analyze", "TEXT"),
    ],
    "pg_training_pair_sources": [
        ("id", "TEXT PRIMARY KEY"),
        ("source_type", "TEXT NOT NULL"),
        ("source_id", "TEXT NOT NULL"),
        ("pair_count", "INTEGER NOT NULL DEFAULT 0"),
        ("content_hash", "TEXT NOT NULL"),
        ("created_at", "TEXT"),
    ],
    "posts": [
        ("id", "TEXT PRIMARY KEY"),
        ("title", "TEXT DEFAULT ''"),
        ("slug", "TEXT DEFAULT ''"),
        ("body", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'draft'"),
        ("published_at", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "project_team_members": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("name", "TEXT DEFAULT ''"),
        ("role", "TEXT DEFAULT ''"),
        ("email", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'active'"),
        ("created_at", "TEXT"),
    ],
    "rag_evaluations": [
        ("id", "TEXT PRIMARY KEY"),
        ("query_id", "TEXT DEFAULT ''"),
        ("ndcg_score", "REAL DEFAULT 0.0"),
        ("mrr_score", "REAL DEFAULT 0.0"),
        ("precision_at_k", "REAL DEFAULT 0.0"),
        ("created_at", "TEXT"),
    ],
    "research_cache": [
        ("id", "TEXT PRIMARY KEY"),
        ("query_hash", "TEXT DEFAULT ''"),
        ("query", "TEXT DEFAULT ''"),
        ("result", "TEXT DEFAULT '{}'"),
        ("expires_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "schedule_log": [
        ("id", "TEXT PRIMARY KEY"),
        ("job_name", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("message", "TEXT DEFAULT ''"),
        ("ran_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "security_findings": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'low'"),
        ("status", "TEXT DEFAULT 'open'"),
        ("title", "TEXT DEFAULT ''"),
        ("description", "TEXT DEFAULT ''"),
        ("rule_id", "TEXT DEFAULT ''"),
        ("file_path", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
    ],
    "security_scan_results": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("scanner", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'low'"),
        ("status", "TEXT DEFAULT 'open'"),
        ("result", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
    ],
    "ssp_controls": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("control_id", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'not_implemented'"),
        ("implementation", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "test_results": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("test_type", "TEXT DEFAULT 'unit'"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("passed", "INTEGER DEFAULT 0"),
        ("failed", "INTEGER DEFAULT 0"),
        ("coverage", "REAL DEFAULT 0.0"),
        ("ran_at", "TEXT"),
        ("created_at", "TEXT"),
    ],
    "topic_clusters": [
        ("id", "TEXT PRIMARY KEY"),
        ("topic", "TEXT DEFAULT ''"),
        ("cluster_label", "TEXT DEFAULT ''"),
        ("keywords", "TEXT DEFAULT '[]'"),
        ("post_count", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT"),
    ],
    "tour_config": [
        ("id", "TEXT PRIMARY KEY"),
        ("selector", "TEXT DEFAULT ''"),
        ("title", "TEXT DEFAULT ''"),
        ("description", "TEXT DEFAULT ''"),
        ("step_order", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT"),
    ],
    "vulnerability_records": [
        ("id", "TEXT PRIMARY KEY"),
        ("project_id", "TEXT DEFAULT ''"),
        ("cve_id", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'low'"),
        ("status", "TEXT DEFAULT 'not_affected'"),
        ("description", "TEXT DEFAULT ''"),
        ("affected_component", "TEXT DEFAULT ''"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    "workflow_tasks": [
        ("id", "TEXT PRIMARY KEY"),
        ("loop_id", "TEXT DEFAULT ''"),
        ("task_type", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("payload", "TEXT DEFAULT '{}'"),
        ("result", "TEXT DEFAULT '{}'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ],
    # MCIP DAT canvas — migration 207_mcip_dat_tables.sql
    "mcip_dat_events": [
        ("id", "TEXT PRIMARY KEY"),
        ("source_type", "TEXT NOT NULL"),
        ("content_hash", "TEXT NOT NULL"),
        ("sender", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("recipient", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("classification", "TEXT NOT NULL DEFAULT 'CUI'"),
        ("tension_signal", "REAL NOT NULL DEFAULT 0.0"),
        ("ingested_at", "TEXT NOT NULL"),
    ],
    "mcip_dti_scores": [
        ("id", "TEXT PRIMARY KEY"),
        ("score", "REAL NOT NULL"),
        ("cable_sub", "REAL NOT NULL DEFAULT 0.0"),
        ("unsc_sub", "REAL NOT NULL DEFAULT 0.0"),
        ("backchannel_sub", "REAL NOT NULL DEFAULT 0.0"),
        ("event_count", "INTEGER NOT NULL DEFAULT 0"),
        ("computed_at", "TEXT NOT NULL"),
    ],
}


def _build_create_sql(table: str, columns: List[Tuple[str, str]]) -> str:
    """Build an idempotent CREATE TABLE IF NOT EXISTS statement."""
    lines = [f"CREATE TABLE IF NOT EXISTS {table} ("]
    col_parts = []
    for col, col_type in columns:
        col_parts.append(f"    {col} {col_type}")
    lines.append(",\n".join(col_parts))
    lines.append(")")
    return "\n".join(lines) + ";"


def generate_migration(dry_run: bool = False) -> Dict[str, Any]:
    """Generate migration 017 DDL from orphan table findings."""
    orphans = _get_orphan_tables()

    tables_ddl: List[Dict[str, Any]] = []
    for finding in orphans:
        table = finding["table"]
        ref_file = finding["first_reference"]

        # Use known schema if available, else auto-infer
        if table in _KNOWN_SCHEMAS:
            columns = _KNOWN_SCHEMAS[table]
            source = "known_schema"
        else:
            columns = _infer_columns_for_table(table)
            source = "auto_inferred"

        create_sql = _build_create_sql(table, columns)
        tables_ddl.append({
            "table": table,
            "first_reference": ref_file,
            "columns": [{"name": c, "type": t} for c, t in columns],
            "create_sql": create_sql,
            "schema_source": source,
        })

    if dry_run:
        return {
            "status": "dry_run",
            "orphan_count": len(orphans),
            "tables": tables_ddl,
        }

    # Write migration files
    MIGRATION_DIR.mkdir(parents=True, exist_ok=True)

    up_py = _render_up_py(tables_ddl)
    down_py = _render_down_py(tables_ddl)
    meta_json = _render_meta_json(len(tables_ddl))

    (MIGRATION_DIR / "up.py").write_text(up_py, encoding="utf-8")
    (MIGRATION_DIR / "down.py").write_text(down_py, encoding="utf-8")
    (MIGRATION_DIR / "meta.json").write_text(meta_json, encoding="utf-8")

    # Ensure __init__.py exists for the migrations package
    init_path = BASE_DIR / "tools" / "db" / "migrations" / "__init__.py"
    if not init_path.exists():
        init_path.write_text("", encoding="utf-8")

    # Create migration dir __init__.py
    (MIGRATION_DIR / "__init__.py").write_text("", encoding="utf-8")

    return {
        "status": "generated",
        "migration_dir": str(MIGRATION_DIR),
        "orphan_count": len(orphans),
        "tables": tables_ddl,
        "files_written": [
            str(MIGRATION_DIR / "up.py"),
            str(MIGRATION_DIR / "down.py"),
            str(MIGRATION_DIR / "meta.json"),
        ],
    }


def _render_up_py(tables_ddl: List[Dict[str, Any]]) -> str:
    """Render the up.py migration file."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    table_count = len(tables_ddl)

    # Build _ORPHAN_TABLES list body
    table_names_lines = "\n".join(f'    "{t["table"]}",' for t in tables_ddl)

    # Build the CREATE SQL block
    create_blocks = []
    for t in tables_ddl:
        sql = t["create_sql"]
        ref = t["first_reference"]
        source = t["schema_source"]
        create_blocks.append(
            f"    # {t['table']} — first referenced in {ref} [{source}]\n"
            f"    {json.dumps(sql)},"
        )
    create_stmts_str = "\n".join(create_blocks)

    return (
        "#!/usr/bin/env python3\n"
        "# CUI // SP-CTI\n"
        '"""Migration 017: auto-generate CREATE TABLE for orphan tables.\n'
        "\n"
        f"Generated {now} by tools/awareness/orphan_table_generator.py.\n"
        "\n"
        "Resolves orphan_db_table findings from the Internal Awareness Engine\n"
        "gap detector. These tables are written by tool code (INSERT INTO / SELECT)\n"
        "but had no CREATE TABLE statement in any migration, so fresh sqlite\n"
        "deployments would 500 on first use.\n"
        "\n"
        f"This migration covers {table_count} distinct orphan tables.\n"
        "All CREATE TABLE statements are idempotent (IF NOT EXISTS).\n"
        "\n"
        "REVIEW GATE: Human review required before applying. Run:\n"
        "    python tools/awareness/orphan_table_generator.py --stats --json\n"
        '    python tools/db/migrate.py --up --dry-run\n'
        '"""\n'
        "\n"
        "import sqlite3\n"
        "\n"
        'MIGRATION_ID = "017"\n'
        'MIGRATION_NAME = "orphan_tables"\n'
        "DESCRIPTION = (\n"
        f'    "Create {table_count} orphan tables identified by the Internal Awareness Engine "\n'
        '    "gap detector. Resolves schema drift — tables referenced by tool code but missing "\n'
        '    "from any migration, causing fresh sqlite deploys to 500 on first use."\n'
        ")\n"
        "\n"
        "_ORPHAN_TABLES = [\n"
        f"{table_names_lines}\n"
        "]\n"
        "\n"
        "_CREATE_STMTS = [\n"
        f"{create_stmts_str}\n"
        "]\n"
        "\n"
        "\n"
        "def up(conn: sqlite3.Connection) -> dict:\n"
        '    """Apply migration: create all orphan tables (idempotent)."""\n'
        "    created = 0\n"
        "    errors = []\n"
        "\n"
        "    for table, stmt in zip(_ORPHAN_TABLES, _CREATE_STMTS):\n"
        "        try:\n"
        "            conn.execute(stmt)\n"
        "            created += 1\n"
        "        except sqlite3.OperationalError as exc:\n"
        '            errors.append(f"{table}: {exc}")\n'
        "\n"
        "    conn.commit()\n"
        "    return {\n"
        '        "status": "applied" if not errors else "partial",\n'
        '        "created_or_verified": created,\n'
        '        "errors": errors,\n'
        '        "total": len(_ORPHAN_TABLES),\n'
        "    }\n"
    )


def _render_down_py(tables_ddl: List[Dict[str, Any]]) -> str:  # nosec B608
    """Render the down.py rollback file.

    Note: B608 false-positive — this function generates source code text,
    it does not execute any SQL itself. The rendered code uses a hardcoded
    list of table names (not user input).
    """
    table_names_lines = "\n".join(f'    "{t["table"]}",' for t in tables_ddl)
    parts = [  # nosec B608 -- renders source code text, no SQL execution here
        "#!/usr/bin/env python3\n",
        "# CUI // SP-CTI\n",
        '"""Migration 017 rollback: drop orphan tables created by up.py.\n',
        "\n",
        "WARNING: This is destructive — only run in development environments.\n",
        '"""\n',
        "\n",
        "import sqlite3\n",
        "\n",
        'MIGRATION_ID = "017"\n',
        'MIGRATION_NAME = "orphan_tables"\n',
        "\n",
        "_ORPHAN_TABLES = [\n",
        f"{table_names_lines}\n",
        "]\n",
        "\n",
        "\n",
        "def down(conn: sqlite3.Connection) -> dict:\n",
        '    """Drop all tables created by migration 017."""\n',
        "    dropped = 0\n",
        "    skipped = 0\n",
        "    # Backend-aware existence probe: prefer the shared translation-independent\n",
        "    # helper (works on PostgreSQL and SQLite); fall back to a direct\n",
        "    # sqlite_master query only if the storage layer is unavailable.\n",
        "    try:\n",
        "        from tools.db.storage import table_exists as _table_exists\n",
        "    except Exception:\n",
        "        def _table_exists(conn, name):\n",
        "            row = conn.execute(\n",
        "                \"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?\",\n",
        "                (name,),\n",
        "            ).fetchone()\n",
        "            return row is not None\n",
        "    for table in _ORPHAN_TABLES:\n",
        "        if not _table_exists(conn, table):\n",
        "            skipped += 1\n",
        "            continue\n",
        '        conn.execute(f"DROP TABLE IF EXISTS {table}")  # nosec B608 -- hardcoded list\n',
        "        dropped += 1\n",
        "    conn.commit()\n",
        '    return {"status": "applied", "dropped": dropped, "skipped": skipped}\n',
    ]
    return "".join(parts)


def _render_meta_json(table_count: int) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta = {
        "id": "017",
        "name": "orphan_tables",
        "description": (
            f"Auto-generate CREATE TABLE for {table_count} orphan tables "
            "identified by the Internal Awareness Engine gap detector. "
            "Generated by tools/awareness/orphan_table_generator.py. "
            "Resolves schema drift — tables referenced by tool code but missing "
            "from any migration, causing fresh sqlite deploys to 500 on first use."
        ),
        "created_at": now,
        "phase": "Phase 68",
        "generator": "tools/awareness/orphan_table_generator.py",
    }
    return json.dumps(meta, indent=2)


def apply_migration() -> Dict[str, Any]:
    """Apply migration 017 against the sqlite icdev.db."""
    db_path = BASE_DIR / "data" / "icdev.db"
    if not db_path.exists():
        return {"status": "error", "reason": "icdev.db not found"}

    up_path = MIGRATION_DIR / "up.py"
    if not up_path.exists():
        return {"status": "error", "reason": "Migration 017 not generated yet. Run --generate first."}

    import importlib.util
    spec = importlib.util.spec_from_file_location("migration_017", up_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    conn = None
    try:
        from tools.db.storage import get_connection
        conn = get_connection(db_path=str(db_path))
        result = mod.up(conn)
        return {"status": "ok", "db": str(db_path), "result": result}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        if conn:
            conn.close()


def get_stats() -> Dict[str, Any]:
    """Return current orphan table stats without writing anything."""
    orphans = _get_orphan_tables()
    migration_exists = (MIGRATION_DIR / "up.py").exists()
    return {
        "orphan_count": len(orphans),
        "migration_generated": migration_exists,
        "migration_dir": str(MIGRATION_DIR),
        "tables": [{"table": o["table"], "first_reference": o["first_reference"]} for o in orphans],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="orphan_table_generator",
        description="Migration 017 generator — auto-generate CREATE TABLE for orphan tables",
    )
    parser.add_argument("--generate", action="store_true", help="Generate migration 017 files")
    parser.add_argument("--apply", action="store_true", help="Apply migration 017 to sqlite icdev.db")
    parser.add_argument("--stats", action="store_true", help="Show orphan table stats")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")

    args = parser.parse_args()

    if args.stats:
        result = get_stats()
    elif args.generate:
        result = generate_migration(dry_run=args.dry_run)
    elif args.apply:
        result = apply_migration()
    else:
        parser.print_help()
        return

    if args.json_out:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if args.stats:
            print(f"Orphan tables: {result['orphan_count']}")
            print(f"Migration generated: {result['migration_generated']}")
            for t in result["tables"]:
                print(f"  {t['table']} <- {t['first_reference']}")
        elif args.generate:
            print(f"Status: {result['status']}")
            if result["status"] != "dry_run":
                print(f"Files written to: {result.get('migration_dir', 'N/A')}")
            print(f"Orphan tables: {result['orphan_count']}")
            for t in result.get("tables", []):
                print(f"  {t['table']} [{t['schema_source']}] -> {len(t['columns'])} columns")
        else:
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
