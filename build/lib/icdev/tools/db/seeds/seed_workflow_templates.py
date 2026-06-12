# CUI // SP-CTI
"""Idempotent seed: system wf_templates (one per canvas + global) and
wf_document_templates (peer-review checklist, security sign-off, NDC naming).

Uses INSERT OR IGNORE — safe to run on every cold-start or re-run.

Run:
    python tools/db/seeds/seed_workflow_templates.py
    python tools/db/seeds/seed_workflow_templates.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_stages(build_role: str) -> str:
    stages = [
        {"name": "build",   "step_type": "automated"},
        {
            "name": "review",
            "step_type": "manual",
            "role": "reviewer",
            "required_docs": [
                {"doc_template_id": "sys-dt-peer-review-checklist", "required": True}
            ],
        },
        {
            "name": "approve",
            "step_type": "manual",
            "role": "manager",
            "required_docs": [
                {"doc_template_id": "sys-dt-security-sign-off", "required": False}
            ],
        },
    ]
    return json.dumps(stages)


def _build_roles(build_role: str) -> str:
    return json.dumps({"build": build_role, "review": "reviewer", "approve": "manager"})


# ── system wf_templates ───────────────────────────────────────────────────────

# Per-canvas build roles (must match constants.CANVAS_ROLE_DEFAULTS)
_CANVAS_BUILD_ROLES: dict[str | None, str] = {
    None:  "engineer",           # global default
    "NDC": "engineer",
    "PDC": "engineer",
    "IDC": "engineer",
    "SDC": "security_engineer",
    "BDC": "security_engineer",
    "DDC": "data_engineer",
    "ODC": "engineer",
}

_WF_TEMPLATES = [
    {
        "id":          f"sys-wft-{ct.lower() if ct else 'global'}",
        "name":        f"Default {'Global' if ct is None else ct} Workflow",
        "canvas_type": ct,
        "build_role":  role,
    }
    for ct, role in _CANVAS_BUILD_ROLES.items()
]


def seed_wf_templates(conn) -> int:
    """INSERT OR IGNORE one system default template per canvas type + global."""
    inserted = 0
    ts = _now()
    for tmpl in _WF_TEMPLATES:
        ct = tmpl["canvas_type"]
        br = tmpl["build_role"]
        conn.execute(
            """INSERT OR IGNORE INTO wf_templates
               (id, name, canvas_type, stages_json, roles_json, approval_policy,
                kickback_limit, is_default, is_system, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,1,1,'system',?,?)""",
            (
                tmpl["id"], tmpl["name"], ct,
                _build_stages(br), _build_roles(br),
                "any_one", 3, ts, ts,
            ),
        )
        inserted += 1
    return inserted


# ── system wf_document_templates ─────────────────────────────────────────────

_PEER_REVIEW_SCHEMA = {
    "fields": [
        {"key": "code_correctness",   "label": "Code is functionally correct",
         "type": "checkbox",  "required": True},
        {"key": "tests_present",      "label": "Tests are present and passing",
         "type": "checkbox",  "required": True},
        {"key": "security_reviewed",  "label": "Security implications reviewed",
         "type": "checkbox",  "required": True},
        {"key": "docs_updated",       "label": "Documentation is updated",
         "type": "checkbox",  "required": False},
        {"key": "naming_conventions", "label": "Naming conventions followed",
         "type": "checkbox",  "required": False},
        {"key": "reviewer_notes",     "label": "Reviewer notes",
         "type": "textarea",  "required": False},
    ]
}

_SECURITY_SIGN_OFF_SCHEMA = {
    "fields": [
        {"key": "classification",  "label": "Classification marking verified",
         "type": "checkbox",  "required": True},
        {"key": "threat_model",    "label": "Threat model reviewed",
         "type": "checkbox",  "required": True},
        {"key": "stig_review",     "label": "STIG/SRG applicability reviewed",
         "type": "checkbox",  "required": True},
        {"key": "pii_handling",    "label": "PII/CUI handling reviewed",
         "type": "checkbox",  "required": False},
        {"key": "residual_risk",   "label": "Residual risk",
         "type": "select",    "options": ["low", "medium", "high"], "required": True},
        {"key": "notes",           "label": "Security officer notes",
         "type": "textarea",  "required": False},
    ]
}

_NDC_NAMING_SCHEMA = {
    "description": "Network Device Naming Convention — ICDEV™ NDC standard v1",
    "rules": [
        "Hostname format: <env>-<role>-<site>-<seq>  (e.g. prod-fw-dc1-01)",
        "Environment codes: prod | stg | dev | lab",
        "Role codes: fw (firewall) | sw (switch) | rt (router) | lb (load balancer) | waf",
        "Sequence: 2-digit zero-padded",
        "Max 24 chars total",
    ],
}

_DOC_TEMPLATES = [
    {
        "id":              "sys-dt-peer-review-checklist",
        "name":            "Peer Review Checklist",
        "doc_type":        "checklist",
        "schema_json":     _PEER_REVIEW_SCHEMA,
        "canvas_type":     None,
        "stage_scope":     "review",
        "is_ai_reference": 0,
        "is_human_required": 1,
    },
    {
        "id":              "sys-dt-security-sign-off",
        "name":            "Security Sign-Off Form",
        "doc_type":        "form",
        "schema_json":     _SECURITY_SIGN_OFF_SCHEMA,
        "canvas_type":     None,
        "stage_scope":     "approve",
        "is_ai_reference": 0,
        "is_human_required": 1,
    },
    {
        "id":              "sys-dt-ndc-naming",
        "name":            "NDC Device Naming Convention",
        "doc_type":        "standard",
        "schema_json":     _NDC_NAMING_SCHEMA,
        "canvas_type":     "NDC",
        "stage_scope":     None,
        "is_ai_reference": 1,
        "is_human_required": 0,
    },
]


def seed_doc_templates(conn) -> int:
    """INSERT OR IGNORE system document templates."""
    inserted = 0
    ts = _now()
    for dt in _DOC_TEMPLATES:
        conn.execute(
            """INSERT OR IGNORE INTO wf_document_templates
               (id, name, doc_type, schema_json, canvas_type, stage_scope,
                is_ai_reference, is_human_required, version, is_system, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,'system',?)""",
            (
                dt["id"], dt["name"], dt["doc_type"],
                json.dumps(dt["schema_json"]),
                dt["canvas_type"], dt["stage_scope"],
                dt["is_ai_reference"], dt["is_human_required"],
                "1", ts,
            ),
        )
        inserted += 1
    return inserted


# ── public entry point ────────────────────────────────────────────────────────

def run(verbose: bool = True) -> dict:
    """Seed both wf_templates and wf_document_templates. Idempotent."""
    conn = get_connection()
    try:
        n_wf = seed_wf_templates(conn)
        n_dt = seed_doc_templates(conn)
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

    result = {"wf_templates_seeded": n_wf, "doc_templates_seeded": n_dt}
    if verbose:
        print(
            f"seed_workflow_templates: {n_wf} wf_templates, "
            f"{n_dt} wf_document_templates (INSERT OR IGNORE)"
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed system workflow templates (idempotent)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = run(verbose=not args.as_json)
    if args.as_json:
        print(json.dumps(result))
