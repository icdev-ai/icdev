# CUI // SP-CTI
"""Built-in report type definitions — section schema and routing hints."""
from __future__ import annotations

import dataclasses
import json
from typing import List

from tools.db.storage import get_connection


@dataclasses.dataclass
class ReportSection:
    key: str
    name: str
    description: str
    sort_order: int
    required: bool = True
    source_hints: List[str] = dataclasses.field(default_factory=list)
    min_chunks: int = 1
    max_chunks: int = 8
    max_words: int = 800


REPORT_TYPES = {
    "standard_audit",
    "compliance_assessment",
    "security_review",
}


def get_sections(report_type: str) -> List[ReportSection]:
    """Return ordered section definitions for a report type from DB (seeded by migration 080)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM wf_report_section_defs WHERE report_type=%s ORDER BY sort_order",
            (report_type,),
        ).fetchall()
        return [
            ReportSection(
                key=r["section_key"],
                name=r["section_name"],
                description=r["description"],
                sort_order=r["sort_order"],
                required=bool(r["required"]),
                source_hints=json.loads(r["source_hints"] or "[]"),
                min_chunks=r["min_chunks"],
                max_chunks=r["max_chunks"],
                max_words=r["max_words"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def list_report_types() -> List[dict]:
    """Return all distinct report types with section counts."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT report_type, COUNT(*) AS section_count
               FROM wf_report_section_defs GROUP BY report_type ORDER BY report_type"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_custom_report_type(
    report_type: str,
    sections: List[dict],
) -> int:
    """Insert custom report type section definitions. Returns count inserted."""
    conn = get_connection()
    try:
        inserted = 0
        for i, sec in enumerate(sections):
            conn.execute(
                """INSERT OR IGNORE INTO wf_report_section_defs
                   (id, report_type, section_key, section_name, description,
                    sort_order, required, source_hints, min_chunks, max_chunks, max_words)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"csd-{report_type[:6]}-{i:02d}",
                    report_type,
                    sec["section_key"],
                    sec["section_name"],
                    sec.get("description", sec["section_name"]),
                    sec.get("sort_order", i + 1),
                    int(sec.get("required", True)),
                    json.dumps(sec.get("source_hints", [])),
                    sec.get("min_chunks", 1),
                    sec.get("max_chunks", 8),
                    sec.get("max_words", 800),
                ),
            )
            inserted += 1
        try:
            conn.commit()
        except Exception:
            pass
        return inserted
    finally:
        conn.close()
