"""ICDEV™ Studio — Form Builder Backend.

Drag-and-drop form designer for intake questionnaires, proposal templates,
compliance checklists, and custom data collection.  Forms serialize to
JSON Schema (draft-07) and auto-generate DB storage + API endpoints.

Architecture Decision D363: JSON Schema for portability.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "frm") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── Field type definitions ────────────────────────────────────────────

FIELD_TYPES: list[dict[str, Any]] = [
    {"type": "text", "label": "Text", "icon": "T", "schema_type": "string"},
    {"type": "textarea", "label": "Long Text", "icon": "P", "schema_type": "string"},
    {"type": "number", "label": "Number", "icon": "#", "schema_type": "number"},
    {"type": "date", "label": "Date", "icon": "D", "schema_type": "string", "format": "date"},
    {"type": "select", "label": "Dropdown", "icon": "V", "schema_type": "string", "has_options": True},
    {"type": "multiselect", "label": "Multi-Select", "icon": "M", "schema_type": "array", "has_options": True},
    {"type": "checkbox", "label": "Checkbox", "icon": "X", "schema_type": "boolean"},
    {"type": "email", "label": "Email", "icon": "@", "schema_type": "string", "format": "email"},
    {"type": "file", "label": "File Upload", "icon": "F", "schema_type": "string", "format": "uri"},
    {"type": "richtext", "label": "Rich Text", "icon": "R", "schema_type": "string"},
]

# ── Pre-built form templates ──────────────────────────────────────────

FORM_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "tpl-risk-assessment",
        "name": "Risk Assessment",
        "description": "Standard risk assessment questionnaire",
        "category": "compliance",
        "fields": [
            {"id": "f1", "type": "text", "label": "System Name", "required": True},
            {
                "id": "f2",
                "type": "select",
                "label": "Impact Level",
                "options": ["IL2", "IL4", "IL5", "IL6"],
                "required": True,
            },
            {
                "id": "f3",
                "type": "select",
                "label": "Risk Level",
                "options": ["Critical", "High", "Medium", "Low"],
                "required": True,
            },
            {"id": "f4", "type": "textarea", "label": "Risk Description", "required": True},
            {"id": "f5", "type": "textarea", "label": "Mitigation Plan"},
            {"id": "f6", "type": "date", "label": "Target Remediation Date"},
            {"id": "f7", "type": "text", "label": "Assigned To"},
        ],
    },
    {
        "id": "tpl-change-request",
        "name": "Change Request",
        "description": "Configuration change request form",
        "category": "operations",
        "fields": [
            {"id": "f1", "type": "text", "label": "Change Title", "required": True},
            {
                "id": "f2",
                "type": "select",
                "label": "Change Type",
                "options": ["Standard", "Normal", "Emergency"],
                "required": True,
            },
            {
                "id": "f3",
                "type": "select",
                "label": "Priority",
                "options": ["Critical", "High", "Medium", "Low"],
                "required": True,
            },
            {"id": "f4", "type": "textarea", "label": "Description", "required": True},
            {"id": "f5", "type": "textarea", "label": "Impact Analysis"},
            {"id": "f6", "type": "textarea", "label": "Rollback Plan"},
            {"id": "f7", "type": "date", "label": "Planned Date"},
            {"id": "f8", "type": "text", "label": "Approver"},
        ],
    },
    {
        "id": "tpl-compliance-checklist",
        "name": "Compliance Checklist",
        "description": "Pre-deployment compliance verification",
        "category": "compliance",
        "fields": [
            {"id": "f1", "type": "text", "label": "System Name", "required": True},
            {"id": "f2", "type": "checkbox", "label": "STIG checks passed"},
            {"id": "f3", "type": "checkbox", "label": "Vulnerability scan completed"},
            {"id": "f4", "type": "checkbox", "label": "SSP updated"},
            {"id": "f5", "type": "checkbox", "label": "POA&M items addressed"},
            {"id": "f6", "type": "checkbox", "label": "SBOM generated"},
            {"id": "f7", "type": "checkbox", "label": "CUI markings verified"},
            {"id": "f8", "type": "textarea", "label": "Notes"},
            {"id": "f9", "type": "text", "label": "Verified By", "required": True},
            {"id": "f10", "type": "date", "label": "Verification Date", "required": True},
        ],
    },
    {
        "id": "tpl-incident-report",
        "name": "Incident Report",
        "description": "Security or operational incident report",
        "category": "security",
        "fields": [
            {"id": "f1", "type": "text", "label": "Incident Title", "required": True},
            {
                "id": "f2",
                "type": "select",
                "label": "Severity",
                "options": ["Critical", "High", "Medium", "Low"],
                "required": True,
            },
            {
                "id": "f3",
                "type": "select",
                "label": "Category",
                "options": ["Security", "Availability", "Performance", "Data", "Other"],
                "required": True,
            },
            {"id": "f4", "type": "textarea", "label": "Description", "required": True},
            {"id": "f5", "type": "textarea", "label": "Impact Assessment"},
            {"id": "f6", "type": "textarea", "label": "Root Cause"},
            {"id": "f7", "type": "textarea", "label": "Corrective Actions"},
            {"id": "f8", "type": "text", "label": "Reported By", "required": True},
        ],
    },
]


def get_field_types() -> list[dict]:
    return FIELD_TYPES


def get_form_templates() -> list[dict]:
    return FORM_TEMPLATES


# ── Form CRUD ─────────────────────────────────────────────────────────


def _fields_to_json_schema(fields: list[dict]) -> dict:
    """Convert field list to JSON Schema (draft-07)."""
    properties = {}
    required = []
    for field in fields:
        ftype = field.get("type", "text")
        prop: dict[str, Any] = {"title": field.get("label", field.get("id", ""))}

        type_def = next((t for t in FIELD_TYPES if t["type"] == ftype), None)
        if type_def:
            prop["type"] = type_def["schema_type"]
            if "format" in type_def:
                prop["format"] = type_def["format"]

        if field.get("options"):
            if ftype == "multiselect":
                prop["items"] = {"type": "string", "enum": field["options"]}
            else:
                prop["enum"] = field["options"]

        if field.get("placeholder"):
            prop["description"] = field["placeholder"]

        properties[field.get("id", f"field_{len(properties)}")] = prop

        if field.get("required"):
            required.append(field.get("id", f"field_{len(properties) - 1}"))

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": properties,
        "required": required,
    }


def create_form(
    name: str,
    fields: list[dict],
    *,
    description: str = "",
    created_by: str = "studio",
) -> dict:
    form_id = _new_id("frm")
    schema = _fields_to_json_schema(fields)
    schema["_fields"] = fields  # Store original field defs for UI reconstruction

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_forms
               (form_id, name, description, schema_json, created_by,
                created_at, updated_at, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft')""",
            (form_id, name, description, json.dumps(schema), created_by, _now_iso(), _now_iso()),
        )
        conn.commit()
        return {"status": "ok", "form_id": form_id, "schema": schema}
    finally:
        conn.close()


def list_forms(*, status: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_forms"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_form(form_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM studio_forms WHERE form_id = %s", (form_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_form(
    form_id: str,
    *,
    name: str | None = None,
    fields: list[dict] | None = None,
    description: str | None = None,
    status: str | None = None,
) -> dict:
    existing = get_form(form_id)
    if not existing:
        return {"status": "error", "error": "Form not found"}

    sets: list[str] = []
    vals: list[Any] = []

    if name is not None:
        sets.append("name = ?")
        vals.append(name)
    if description is not None:
        sets.append("description = ?")
        vals.append(description)
    if fields is not None:
        schema = _fields_to_json_schema(fields)
        schema["_fields"] = fields
        sets.append("schema_json = ?")
        vals.append(json.dumps(schema))
        sets.append("version = version + 1")
    if status is not None:
        sets.append("status = ?")
        vals.append(status)

    if not sets:
        return {"status": "ok", "form_id": form_id}

    sets.append("updated_at = ?")
    vals.append(_now_iso())
    vals.append(form_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE studio_forms SET {', '.join(sets)} WHERE form_id = %s",  # nosec B608 — column names are hardcoded
            vals,
        )
        conn.commit()
        return {"status": "ok", "form_id": form_id}
    finally:
        conn.close()


def delete_form(form_id: str) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM studio_forms WHERE form_id = %s", (form_id,))
        conn.commit()
        return {"status": "ok"} if cur.rowcount else {"status": "error", "error": "Not found"}
    finally:
        conn.close()


# ── Submissions ───────────────────────────────────────────────────────


def submit_form(form_id: str, data: dict, *, submitted_by: str = "user") -> dict:
    form = get_form(form_id)
    if not form:
        return {"status": "error", "error": "Form not found"}

    sub_id = _new_id("sub")
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_form_submissions
               (submission_id, form_id, data_json, submitted_by, submitted_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (sub_id, form_id, json.dumps(data), submitted_by, _now_iso()),
        )
        conn.commit()
        return {"status": "ok", "submission_id": sub_id}
    finally:
        conn.close()


def list_submissions(form_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM studio_form_submissions WHERE form_id = %s ORDER BY submitted_at DESC",
            (form_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio Form Builder")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("field-types", help="List available field types")
    sub.add_parser("templates", help="List form templates")
    sub.add_parser("list", help="List saved forms")

    p_get = sub.add_parser("get", help="Get form by ID")
    p_get.add_argument("form_id")

    args = parser.parse_args()
    result: Any = None

    if args.command == "field-types":
        result = get_field_types()
    elif args.command == "templates":
        result = get_form_templates()
    elif args.command == "list":
        result = list_forms()
    elif args.command == "get":
        result = get_form(args.form_id)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
