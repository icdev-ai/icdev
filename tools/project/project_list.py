#!/usr/bin/env python3
"""List all ICDEV-managed projects from the database.

Supports two output formats:
  - brief: compact table view for terminal display
  - detailed: full JSON output with all fields

Usage:
    python tools/project/project_list.py --format brief
    python tools/project/project_list.py --format detailed
    python tools/project/project_list.py --status active --format brief
"""

import argparse
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def list_projects(status_filter: str = None, output_format: str = "brief") -> dict:
    """List projects from the database.

    Args:
        status_filter: Optional filter by status (active, archived, suspended).
        output_format: 'brief' for table, 'detailed' for full JSON.

    Returns:
        dict with 'projects' list and 'total' count.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()

        projects = []
        for row in rows:
            project = {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "type": row["type"],
                "classification": row["classification"],
                "status": row["status"],
                "tech_stack": {
                    "backend": row["tech_stack_backend"] or "",
                    "frontend": row["tech_stack_frontend"] or "",
                    "database": row["tech_stack_database"] or "",
                },
                "directory_path": row["directory_path"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            projects.append(project)
    finally:
        conn.close()

    return {"projects": projects, "total": len(projects)}


def format_brief(data: dict) -> str:
    """Format project list as an aligned table for terminal display."""
    projects = data["projects"]
    if not projects:
        return "No projects found."

    # Column widths (minimum)
    col_id = 8       # Show first 8 chars of UUID
    col_name = 30
    col_type = 16
    col_class = 8
    col_status = 10
    col_created = 19

    # Header
    lines = []
    header = (
        f"{'ID':<{col_id}}  "
        f"{'Name':<{col_name}}  "
        f"{'Type':<{col_type}}  "
        f"{'Class':<{col_class}}  "
        f"{'Status':<{col_status}}  "
        f"{'Created':<{col_created}}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for p in projects:
        short_id = p["id"][:8]
        name = p["name"][:col_name]
        ptype = p["type"][:col_type]
        cls = p["classification"][:col_class]
        status = p["status"][:col_status]
        created = (p["created_at"] or "")[:col_created]

        row = (
            f"{short_id:<{col_id}}  "
            f"{name:<{col_name}}  "
            f"{ptype:<{col_type}}  "
            f"{cls:<{col_class}}  "
            f"{status:<{col_status}}  "
            f"{created:<{col_created}}"
        )
        lines.append(row)

    lines.append("")
    lines.append(f"Total: {data['total']} project(s)")

    # Add tech stack summary if any project has tech info
    has_tech = any(
        p["tech_stack"]["backend"] or p["tech_stack"]["frontend"] or p["tech_stack"]["database"]
        for p in projects
    )
    if has_tech:
        lines.append("")
        lines.append("Tech stacks:")
        for p in projects:
            ts = p["tech_stack"]
            parts = []
            if ts["backend"]:
                parts.append(f"BE: {ts['backend']}")
            if ts["frontend"]:
                parts.append(f"FE: {ts['frontend']}")
            if ts["database"]:
                parts.append(f"DB: {ts['database']}")
            if parts:
                lines.append(f"  {p['id'][:8]} - {', '.join(parts)}")

    return "\n".join(lines)


def format_detailed(data: dict) -> str:
    """Format project list as pretty-printed JSON."""
    return json.dumps(data, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(
        description="List all ICDEV-managed projects"
    )
    parser.add_argument(
        "--format", choices=["brief", "detailed", "json"], default="brief",
        help="Output format (brief=table, detailed/json=full JSON)"
    )
    parser.add_argument(
        "--status", choices=["active", "archived", "suspended"],
        help="Filter by project status"
    )
    args = parser.parse_args()

    data = list_projects(status_filter=args.status)

    if args.format == "brief":
        print(format_brief(data))
    else:
        print(format_detailed(data))


if __name__ == "__main__":
    main()
