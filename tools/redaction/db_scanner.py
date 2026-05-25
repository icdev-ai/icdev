#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Database PII Scanner — scan proposal tables for unprotected sensitive fields.

Scans SQLite tables for potential PII/sensitive data by:
    1. Sampling text columns from specified tables
    2. Running each sample through the RedactionDetector
    3. Reporting columns with high PII density

Helps identify where sensitive data is stored without protection,
enabling targeted redaction policies.

CLI:
    python tools/redaction/db_scanner.py --scan --json
    python tools/redaction/db_scanner.py --scan --table proposal_knowledge_base --json
    python tools/redaction/db_scanner.py --scan --sample-size 50 --json
    python tools/redaction/db_scanner.py --health --json
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.redaction.detector import RedactionDetector  # noqa: E402

logger = get_logger("icdev.redaction.db_scanner")

# Tables known to contain sensitive proposal data
GOVCON_TABLES = [
    "proposal_opportunities",
    "proposal_section_drafts",
    "proposal_knowledge_base",
    "proposal_compliance_matrix",
    "rfp_shall_statements",
    "govcon_awards",
    "pg_crm_contacts",
    "pg_crm_interactions",
    "pg_crm_accounts",
    "pg_teaming_partners",
    "pg_teaming_assessments",
    "pg_teaming_workshare",
    "pg_win_loss_records",
    "pg_win_loss_lessons",
    "pg_cost_volumes",
    "pg_lcat_allocations",
    "pg_talent_signals",
    "pg_competitor_awards",
    "sam_gov_opportunities",
]


class DBScanner:
    """Scan database tables for PII/sensitive data."""

    def __init__(self, sample_size: int = 20):
        self._detector = RedactionDetector()
        self._sample_size = sample_size

    def scan(self, tables: Optional[List[str]] = None) -> Dict[str, Any]:
        """Scan tables for PII.

        Args:
            tables: Specific tables to scan (None = all GOVCON_TABLES).

        Returns:
            Scan results with per-table, per-column PII density.
        """
        tables = tables or GOVCON_TABLES
        conn = get_connection()
        results = {}

        for table in tables:
            try:
                table_result = self._scan_table(conn, table)
                if table_result:
                    results[table] = table_result
            except Exception as e:
                results[table] = {"error": str(e)}

        conn.close()

        # Summary
        total_columns = sum(
            len(r.get("columns", {})) for r in results.values() if isinstance(r, dict) and "columns" in r
        )
        pii_columns = sum(
            sum(1 for c in r.get("columns", {}).values() if c.get("pii_density", 0) > 0)
            for r in results.values()
            if isinstance(r, dict) and "columns" in r
        )

        return {
            "status": "ok",
            "tables_scanned": len(results),
            "total_text_columns": total_columns,
            "columns_with_pii": pii_columns,
            "tables": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _scan_table(self, conn, table: str) -> Optional[Dict[str, Any]]:
        """Scan a single table."""
        # Check if table exists
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not exists:
            return None

        # Get text columns
        pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
        text_columns = [
            col["name"]
            for col in pragma
            if col["type"].upper() in ("TEXT", "VARCHAR", "CLOB", "")
            and col["name"] not in ("id", "created_at", "updated_at", "status", "type")
        ]

        if not text_columns:
            return {"columns": {}, "row_count": 0}

        # Get row count
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec B608 — table from GOVCON_TABLES constant

        # Sample rows
        column_results = {}
        for col in text_columns:
            try:
                samples = conn.execute(  # nosec B608 — col/table from PRAGMA, not user input
                    f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL "  # nosec B608 -- table/column names are internal constants, not user input
                    f"AND {col} != '' ORDER BY RANDOM() LIMIT ?",
                    (self._sample_size,),
                ).fetchall()

                if not samples:
                    column_results[col] = {"pii_density": 0.0, "samples": 0}
                    continue

                # Detect PII in each sample
                pii_count = 0
                entity_types_found: Dict[str, int] = {}
                for row in samples:
                    text = str(row[0] or "")
                    if not text.strip():
                        continue
                    detections = self._detector.detect(text[:2000])
                    if detections:
                        pii_count += 1
                        for d in detections:
                            entity_types_found[d.entity_type] = entity_types_found.get(d.entity_type, 0) + 1

                density = pii_count / len(samples) if samples else 0.0
                column_results[col] = {
                    "pii_density": round(density, 3),
                    "samples_checked": len(samples),
                    "samples_with_pii": pii_count,
                    "entity_types": entity_types_found,
                }
            except Exception as e:
                column_results[col] = {"error": str(e)}

        return {
            "row_count": row_count,
            "columns": column_results,
        }

    def health(self) -> Dict[str, Any]:
        """Health check."""
        detector_health = self._detector.health()
        return {
            "status": "ok",
            "detector": detector_health,
            "govcon_tables": len(GOVCON_TABLES),
            "sample_size": self._sample_size,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Database PII Scanner")
    parser.add_argument("--scan", action="store_true", help="Scan tables for PII")
    parser.add_argument("--table", type=str, default=None, help="Specific table to scan")
    parser.add_argument("--sample-size", type=int, default=20, help="Sample rows per column (default: 20)")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()

    scanner = DBScanner(sample_size=args.sample_size)

    if args.health:
        result = scanner.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
        if args.gate and result["status"] != "ok":
            sys.exit(1)
        return

    if args.scan:
        tables = [args.table] if args.table else None
        result = scanner.scan(tables=tables)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Tables scanned: {result['tables_scanned']}")
            print(f"Text columns: {result['total_text_columns']}")
            print(f"Columns with PII: {result['columns_with_pii']}")
            for table, info in result.get("tables", {}).items():
                if isinstance(info, dict) and "columns" in info:
                    pii_cols = [c for c, v in info["columns"].items() if v.get("pii_density", 0) > 0]
                    if pii_cols:
                        print(f"\n  {table} ({info['row_count']} rows):")
                        for col in pii_cols:
                            v = info["columns"][col]
                            print(f"    {col}: {v['pii_density']:.0%} PII density ({v.get('entity_types', {})})")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
