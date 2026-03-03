#!/usr/bin/env python3
# CUI // SP-CTI
"""Ingest patterns and failures into the knowledge base.
Writes to knowledge_patterns and failure_log tables in icdev.db."""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_PATTERN_TYPES = (
    "error", "performance", "security", "deployment", "infrastructure",
    "configuration", "dependency", "resource", "network", "database",
)


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Ingest a known pattern
# ---------------------------------------------------------------------------
def ingest_pattern(
    pattern_type: str,
    signature: str,
    description: str,
    root_cause: str,
    remediation: str = None,
    auto_healable: bool = False,
    confidence: float = 0.5,
    db_path: Path = None,
) -> int:
    """Insert a pattern into knowledge_patterns. Returns the pattern ID.

    Args:
        pattern_type: Category of pattern (error, performance, security, etc.)
        signature: Unique string identifying this pattern (e.g., regex or key phrase)
        description: Human-readable description of the pattern
        root_cause: What causes this pattern to appear
        remediation: JSON string or dict describing how to fix it
        auto_healable: Whether this pattern can be auto-remediated
        confidence: Initial confidence score (0.0 to 1.0)
        db_path: Override database path
    """
    if pattern_type not in VALID_PATTERN_TYPES:
        raise ValueError(f"Invalid pattern_type '{pattern_type}'. Valid: {VALID_PATTERN_TYPES}")

    if isinstance(remediation, dict):
        remediation = json.dumps(remediation)

    conn = _get_db(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()

        # Check for existing pattern with same signature
        existing = conn.execute(
            "SELECT id, occurrence_count FROM knowledge_patterns WHERE pattern_signature = ?",
            (signature,),
        ).fetchone()

        if existing:
            # Update existing pattern — bump occurrence count
            conn.execute(
                """UPDATE knowledge_patterns
                   SET occurrence_count = occurrence_count + 1,
                       last_occurrence = ?,
                       updated_at = ?,
                       description = COALESCE(?, description),
                       root_cause = COALESCE(?, root_cause),
                       remediation = COALESCE(?, remediation)
                   WHERE id = ?""",
                (now, now, description, root_cause, remediation, existing["id"]),
            )
            conn.commit()
            print(f"[knowledge] Updated existing pattern #{existing['id']} "
                  f"(occurrences: {existing['occurrence_count'] + 1})")
            return existing["id"]

        # Insert new pattern
        cursor = conn.execute(
            """INSERT INTO knowledge_patterns
               (pattern_type, pattern_signature, description, root_cause, remediation,
                confidence, occurrence_count, last_occurrence, auto_healable,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (
                pattern_type,
                signature,
                description,
                root_cause,
                remediation,
                confidence,
                now,
                auto_healable,
                now,
                now,
            ),
        )
        conn.commit()
        pattern_id = cursor.lastrowid
        return pattern_id

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ingest a failure event
# ---------------------------------------------------------------------------
def ingest_failure(
    project_id: str,
    source: str,
    error_type: str,
    error_message: str,
    stack_trace: str = None,
    context: dict = None,
    db_path: Path = None,
) -> int:
    """Insert a failure into failure_log. Returns the failure ID.

    Args:
        project_id: Which project the failure belongs to
        source: Where the failure was detected (e.g., 'monitoring', 'deployment', 'health_check')
        error_type: Category of error (e.g., 'ConnectionTimeout', 'OOMKilled', 'HTTP500')
        error_message: The actual error message
        stack_trace: Optional stack trace
        context: Optional dict with additional context (environment, service, etc.)
        db_path: Override database path
    """
    conn = _get_db(db_path)
    try:
        context_json = json.dumps(context) if context else None

        cursor = conn.execute(
            """INSERT INTO failure_log
               (project_id, source, error_type, error_message, stack_trace,
                context, resolved, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                project_id,
                source,
                error_type,
                error_message,
                stack_trace,
                context_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        failure_id = cursor.lastrowid
        return failure_id

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bulk ingest
# ---------------------------------------------------------------------------
def ingest_patterns_from_file(file_path: str, db_path: Path = None) -> list:
    """Ingest multiple patterns from a JSON file.
    Expects a list of objects with keys matching ingest_pattern parameters."""
    with open(file_path, "r", encoding="utf-8") as f:
        patterns = json.load(f)

    ids = []
    for p in patterns:
        pid = ingest_pattern(
            pattern_type=p["pattern_type"],
            signature=p["signature"],
            description=p["description"],
            root_cause=p["root_cause"],
            remediation=p.get("remediation"),
            auto_healable=p.get("auto_healable", False),
            confidence=p.get("confidence", 0.5),
            db_path=db_path,
        )
        ids.append(pid)
    return ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Ingest patterns and failures into knowledge base")
    sub = parser.add_subparsers(dest="command", help="Command")

    # Pattern subcommand
    pat = sub.add_parser("pattern", help="Ingest a known pattern")
    pat.add_argument("--type", required=True, choices=VALID_PATTERN_TYPES, help="Pattern type")
    pat.add_argument("--signature", required=True, help="Pattern signature (unique identifier)")
    pat.add_argument("--description", required=True, help="Human-readable description")
    pat.add_argument("--root-cause", required=True, help="Root cause explanation")
    pat.add_argument("--remediation", help="Remediation as JSON string")
    pat.add_argument("--auto-healable", action="store_true", help="Can be auto-remediated")
    pat.add_argument("--confidence", type=float, default=0.5, help="Initial confidence (0.0–1.0)")

    # Failure subcommand
    fail = sub.add_parser("failure", help="Ingest a failure event")
    fail.add_argument("--project-id", "--project", required=True, help="Project ID", dest="project_id")
    fail.add_argument("--source", required=True, help="Failure source (monitoring, deployment, etc.)")
    fail.add_argument("--error-type", required=True, help="Error type")
    fail.add_argument("--error-message", required=True, help="Error message")
    fail.add_argument("--stack-trace", help="Stack trace")
    fail.add_argument("--context", help="Additional context as JSON string")

    # Bulk subcommand
    bulk = sub.add_parser("bulk", help="Bulk ingest patterns from JSON file")
    bulk.add_argument("--file", required=True, help="JSON file path")

    # Legacy flat CLI for backward compatibility
    parser.add_argument("--type", dest="legacy_type", choices=VALID_PATTERN_TYPES, help="(legacy) Pattern type")
    parser.add_argument("--description", dest="legacy_desc", help="(legacy) Description")
    parser.add_argument("--root-cause", dest="legacy_rc", help="(legacy) Root cause")
    parser.add_argument("--remediation", dest="legacy_rem", help="(legacy) Remediation JSON")
    parser.add_argument("--db-path", help="Database path override")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    if args.command == "pattern":
        remediation = args.remediation
        if remediation:
            try:
                remediation = json.loads(remediation)
            except json.JSONDecodeError:
                pass  # Keep as string

        pid = ingest_pattern(
            pattern_type=args.type,
            signature=args.description[:100],  # Use description prefix as signature if not explicit
            description=args.description,
            root_cause=args.root_cause,
            remediation=remediation,
            auto_healable=args.auto_healable,
            confidence=args.confidence,
            db_path=db_path,
        )
        print(f"[knowledge] Pattern #{pid} ingested: {args.description[:60]}")

    elif args.command == "failure":
        context = json.loads(args.context) if args.context else None
        fid = ingest_failure(
            project_id=args.project_id,
            source=args.source,
            error_type=args.error_type,
            error_message=args.error_message,
            stack_trace=args.stack_trace,
            context=context,
            db_path=db_path,
        )
        print(f"[knowledge] Failure #{fid} recorded: {args.error_type} — {args.error_message[:60]}")

    elif args.command == "bulk":
        ids = ingest_patterns_from_file(args.file, db_path)
        print(f"[knowledge] Bulk ingested {len(ids)} patterns: {ids}")

    elif args.legacy_type and args.legacy_desc:
        # Legacy flat CLI
        remediation = args.legacy_rem
        if remediation:
            try:
                remediation = json.loads(remediation)
            except json.JSONDecodeError:
                pass

        pid = ingest_pattern(
            pattern_type=args.legacy_type,
            signature=args.legacy_desc[:100],
            description=args.legacy_desc,
            root_cause=args.legacy_rc or "Unknown",
            remediation=remediation,
            db_path=db_path,
        )
        print(f"[knowledge] Pattern #{pid} ingested: {args.legacy_desc[:60]}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
