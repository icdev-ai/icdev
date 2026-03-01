#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Question Exporter — export questions to formatted HTML for government submission.

Generates a print-friendly HTML document matching government Q&A submission
format.  Includes CUI banner, solicitation number, title, company name,
date, and numbered questions organized by category.

Follows CDRL generator pattern (D-QTG-4).

Usage:
    python tools/govcon/question_exporter.py --export --opp-id <id> --json
    python tools/govcon/question_exporter.py --export --opp-id <id> --output /path/to/output.html
    python tools/govcon/question_exporter.py --export --opp-id <id> --status approved --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))


# =========================================================================
# HELPERS
# =========================================================================

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="question_exporter"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "govcon.question_export", actor, action, details, "govcon"),
        )
    except Exception:
        pass


# =========================================================================
# CATEGORY DISPLAY NAMES
# =========================================================================

CATEGORY_LABELS = {
    "scope": "Scope",
    "evaluation_criteria": "Evaluation Criteria",
    "technical_requirements": "Technical Requirements",
    "contract_terms": "Contract Terms",
    "compliance_security": "Compliance / Security",
    "small_business": "Small Business",
}

PRIORITY_LABELS = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


# =========================================================================
# HTML TEMPLATE
# =========================================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Questions to Government — {solicitation}</title>
<style>
    @media print {{
        body {{ margin: 0.5in; }}
        .no-print {{ display: none; }}
    }}
    body {{
        font-family: "Times New Roman", Times, serif;
        font-size: 12pt;
        line-height: 1.5;
        color: #000;
        max-width: 8.5in;
        margin: 0 auto;
        padding: 20px;
    }}
    .cui-banner {{
        background: #b22234;
        color: #fff;
        text-align: center;
        padding: 6px;
        font-weight: bold;
        font-size: 11pt;
        letter-spacing: 1px;
        margin-bottom: 20px;
    }}
    .header {{
        text-align: center;
        margin-bottom: 30px;
    }}
    .header h1 {{
        font-size: 16pt;
        margin: 0 0 5px 0;
    }}
    .header h2 {{
        font-size: 14pt;
        margin: 0 0 5px 0;
        font-weight: normal;
    }}
    .meta-table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 30px;
    }}
    .meta-table td {{
        padding: 4px 8px;
        border: 1px solid #999;
    }}
    .meta-table td:first-child {{
        font-weight: bold;
        width: 200px;
        background: #f0f0f0;
    }}
    .category-header {{
        background: #1a3a5c;
        color: #fff;
        padding: 8px 12px;
        font-size: 13pt;
        font-weight: bold;
        margin: 25px 0 10px 0;
    }}
    .question-table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 15px;
    }}
    .question-table th {{
        background: #e8e8e8;
        padding: 6px 8px;
        border: 1px solid #999;
        text-align: left;
        font-size: 11pt;
    }}
    .question-table td {{
        padding: 6px 8px;
        border: 1px solid #999;
        vertical-align: top;
        font-size: 11pt;
    }}
    .question-table td:first-child {{
        text-align: center;
        width: 50px;
    }}
    .question-table td:nth-child(2) {{
        width: 100px;
    }}
    .question-table td:nth-child(4) {{
        width: 120px;
    }}
    .priority-high {{ color: #b22234; font-weight: bold; }}
    .priority-medium {{ color: #e67e22; }}
    .priority-low {{ color: #27ae60; }}
    .summary {{
        margin-top: 30px;
        padding: 10px;
        background: #f9f9f9;
        border: 1px solid #ddd;
    }}
    .footer {{
        text-align: center;
        margin-top: 40px;
        font-size: 10pt;
        color: #666;
    }}
</style>
</head>
<body>
<div class="cui-banner">CUI // SP-CTI</div>

<div class="header">
    <h1>Questions to Government</h1>
    <h2>{title}</h2>
</div>

<table class="meta-table">
    <tr><td>Solicitation Number</td><td>{solicitation}</td></tr>
    <tr><td>Title</td><td>{title}</td></tr>
    <tr><td>Submitted By</td><td>{company}</td></tr>
    <tr><td>Date</td><td>{date}</td></tr>
    <tr><td>Total Questions</td><td>{total_questions}</td></tr>
</table>

{category_sections}

<div class="summary">
    <strong>Summary:</strong> {total_questions} question(s) submitted across {category_count} categories.
    {priority_summary}
</div>

<div class="cui-banner">CUI // SP-CTI</div>

<div class="footer">
    Generated by ICDEV GovProposal — {generated_at}
</div>
</body>
</html>"""

CATEGORY_SECTION_TEMPLATE = """<div class="category-header">{category_label}</div>
<table class="question-table">
    <thead>
        <tr>
            <th>#</th>
            <th>Priority</th>
            <th>Question</th>
            <th>RFP Reference</th>
        </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
</table>"""

ROW_TEMPLATE = """        <tr>
            <td>{number}</td>
            <td class="priority-{priority}">{priority_label}</td>
            <td>{question_text}</td>
            <td>{rfp_ref}</td>
        </tr>"""


# =========================================================================
# CORE EXPORT
# =========================================================================

def export_questions(opp_id, status_filter=None, output_path=None, company_name=None):
    """Export questions to formatted HTML document.

    Args:
        opp_id: Opportunity ID
        status_filter: Only include questions with this status (e.g. 'approved')
        output_path: File path to write HTML (if None, returns HTML string)
        company_name: Company name for the header

    Returns:
        dict with status, html content, and output path.
    """
    conn = _get_db()
    try:
        # Get opportunity info
        opp = conn.execute(
            "SELECT id, title, solicitation_number FROM proposal_opportunities WHERE id = ?",
            (opp_id,),
        ).fetchone()
        if not opp:
            return {"status": "error", "message": f"Opportunity {opp_id} not found"}

        # Query questions
        query = "SELECT * FROM proposal_questions WHERE opportunity_id = ?"
        params = [opp_id]
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        query += " ORDER BY category ASC, question_number ASC"

        questions = conn.execute(query, params).fetchall()

        if not questions:
            return {
                "status": "ok",
                "message": "No questions found matching criteria",
                "count": 0,
            }

        # Group by category
        by_category = {}
        for q in questions:
            cat = q["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(dict(q))

        # Build category sections HTML
        category_sections = []
        global_num = 1
        for cat in ["scope", "evaluation_criteria", "technical_requirements",
                     "contract_terms", "compliance_security", "small_business"]:
            if cat not in by_category:
                continue
            rows_html = []
            for q in by_category[cat]:
                rows_html.append(ROW_TEMPLATE.format(
                    number=global_num,
                    priority=q["priority"],
                    priority_label=PRIORITY_LABELS.get(q["priority"], q["priority"]),
                    question_text=_escape_html(q["question_text"]),
                    rfp_ref=_escape_html(q.get("rfp_section_ref") or "N/A"),
                ))
                global_num += 1

            category_sections.append(CATEGORY_SECTION_TEMPLATE.format(
                category_label=CATEGORY_LABELS.get(cat, cat),
                rows="\n".join(rows_html),
            ))

        # Priority summary
        priority_counts = {"high": 0, "medium": 0, "low": 0}
        for q in questions:
            p = q["priority"]
            if p in priority_counts:
                priority_counts[p] += 1
        priority_summary = (
            f"High priority: {priority_counts['high']}, "
            f"Medium: {priority_counts['medium']}, "
            f"Low: {priority_counts['low']}"
        )

        # Render full HTML
        html = HTML_TEMPLATE.format(
            solicitation=_escape_html(opp["solicitation_number"] or "N/A"),
            title=_escape_html(opp["title"] or "Untitled"),
            company=_escape_html(company_name or "ICDEV"),
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            total_questions=len(questions),
            category_sections="\n".join(category_sections),
            category_count=len(by_category),
            priority_summary=priority_summary,
            generated_at=_now(),
        )

        # Write to file if output_path provided
        if output_path:
            safe_base = BASE_DIR / ".tmp" / "exports"
            out = (safe_base / Path(output_path).name).resolve()
            if not str(out).startswith(str(safe_base.resolve())):
                return {"status": "error", "message": "Invalid output path"}
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            _audit(conn, "questions_exported",
                   f"opp={opp_id}, count={len(questions)}, path={output_path}")
            conn.commit()
            return {
                "status": "ok",
                "count": len(questions),
                "output_path": str(out),
                "categories": len(by_category),
                "priority_summary": priority_summary,
            }

        # Return HTML in result
        _audit(conn, "questions_exported",
               f"opp={opp_id}, count={len(questions)}, inline")
        conn.commit()
        return {
            "status": "ok",
            "count": len(questions),
            "html": html,
            "categories": len(by_category),
            "priority_summary": priority_summary,
        }
    finally:
        conn.close()


def _escape_html(text):
    """Minimal HTML escaping."""
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


# =========================================================================
# CLI
# =========================================================================

def _build_parser():
    p = argparse.ArgumentParser(description="Question Exporter — HTML for government submission (D-QTG-4)")
    p.add_argument("--export", action="store_true", help="Export questions to HTML")
    p.add_argument("--opp-id", required=True, help="Opportunity ID")
    p.add_argument("--status", help="Filter by question status (e.g. 'approved')")
    p.add_argument("--output", help="Output file path (if omitted, returns HTML in JSON)")
    p.add_argument("--company", help="Company name for document header", default="ICDEV")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--human", action="store_true", help="Human-readable output")
    return p


def main():
    args = _build_parser().parse_args()

    if args.export:
        result = export_questions(
            opp_id=args.opp_id,
            status_filter=args.status,
            output_path=args.output,
            company_name=args.company,
        )
    else:
        result = {"status": "error", "message": "Specify --export"}

    if args.human:
        status = result.get("status", "unknown")
        print(f"\n{'='*60}")
        print(f"  Question Exporter — {status.upper()}")
        print(f"{'='*60}")
        if status == "error":
            print(f"  ERROR: {result.get('message', '')}")
        else:
            print(f"  Questions exported: {result.get('count', 0)}")
            print(f"  Categories: {result.get('categories', 0)}")
            print(f"  {result.get('priority_summary', '')}")
            if result.get("output_path"):
                print(f"  Output: {result['output_path']}")
        print()
    else:
        # Don't include full HTML in JSON output — too large
        out = {k: v for k, v in result.items() if k != "html"}
        if "html" in result:
            out["html_length"] = len(result["html"])
            out["html_preview"] = result["html"][:200] + "..."
        print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
