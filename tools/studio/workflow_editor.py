"""ICDEV Studio — Workflow Editor Backend.

CRUD operations for user-created workflows.  Visual DAG editor frontend
serialises to the same YAML format as args/workflow_templates/*.yaml so
workflows can be executed by tools/orchestration/workflow_composer.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ── Tool catalog ───────────────────────────────────────────

TOOL_CATEGORIES: dict[str, dict[str, Any]] = {
    "compliance": {
        "label": "Compliance",
        "icon": "shield",
        "color": "compliance",
        "tools": [
            {"id": "fips199_categorizer", "name": "FIPS 199 Categorize",
             "tool": "tools/compliance/fips199_categorizer.py",
             "description": "Security categorization per FIPS 199"},
            {"id": "multi_regime_assessor", "name": "Multi-Framework Assess",
             "tool": "tools/compliance/multi_regime_assessor.py",
             "description": "Assess against all compliance frameworks"},
            {"id": "ssp_generator", "name": "Generate SSP",
             "tool": "tools/compliance/ssp_generator.py",
             "description": "System Security Plan generation"},
            {"id": "poam_generator", "name": "Generate POA&M",
             "tool": "tools/compliance/poam_generator.py",
             "description": "Plan of Action & Milestones"},
            {"id": "stig_checker", "name": "STIG Check",
             "tool": "tools/compliance/stig_checker.py",
             "description": "DISA STIG compliance check"},
            {"id": "oscal_generator", "name": "OSCAL Export",
             "tool": "tools/compliance/oscal_generator.py",
             "description": "OSCAL-format compliance export"},
            {"id": "sbom_generator", "name": "Generate SBOM",
             "tool": "tools/compliance/sbom_generator.py",
             "description": "Software Bill of Materials"},
            {"id": "cmmc_assessor", "name": "CMMC Assess",
             "tool": "tools/compliance/cmmc_assessor.py",
             "description": "CMMC Level 2/3 assessment"},
            {"id": "fedramp_assessor", "name": "FedRAMP Assess",
             "tool": "tools/compliance/fedramp_assessor.py",
             "description": "FedRAMP Moderate/High assessment"},
        ],
    },
    "security": {
        "label": "Security",
        "icon": "lock",
        "color": "security",
        "tools": [
            {"id": "prompt_injection_detector", "name": "Prompt Injection Scan",
             "tool": "tools/security/prompt_injection_detector.py",
             "description": "Detect prompt injection attempts"},
            {"id": "ai_bom_generator", "name": "AI BOM",
             "tool": "tools/security/ai_bom_generator.py",
             "description": "AI Bill of Materials generation"},
            {"id": "agent_trust_scorer", "name": "Trust Score",
             "tool": "tools/security/agent_trust_scorer.py",
             "description": "Agent trust boundary scoring"},
            {"id": "credential_broker", "name": "Credential Check",
             "tool": "tools/security/credential_broker.py",
             "description": "Credential rotation and audit"},
        ],
    },
    "build": {
        "label": "Build & Test",
        "icon": "hammer",
        "color": "build",
        "tools": [
            {"id": "code_generator", "name": "Generate Code",
             "tool": "tools/builder/code_generator.py",
             "description": "AI-assisted code generation"},
            {"id": "test_orchestrator", "name": "Run Tests",
             "tool": "tools/testing/test_orchestrator.py",
             "description": "Full test suite execution"},
            {"id": "stub_detector", "name": "Detect Stubs",
             "tool": "tools/testing/stub_detector.py",
             "description": "Find stub/placeholder code"},
            {"id": "code_analyzer", "name": "Analyze Code",
             "tool": "tools/analysis/code_analyzer.py",
             "description": "Static code analysis"},
        ],
    },
    "deploy": {
        "label": "Deploy & Infra",
        "icon": "cloud",
        "color": "deploy",
        "tools": [
            {"id": "infra_status", "name": "Infra Status",
             "tool": "tools/infra/infra_status.py",
             "description": "Infrastructure health check"},
            {"id": "rollback", "name": "Rollback",
             "tool": "tools/infra/rollback.py",
             "description": "Deployment rollback"},
            {"id": "dependency_scanner", "name": "Dependency Scan",
             "tool": "tools/maintenance/dependency_scanner.py",
             "description": "Scan for outdated dependencies"},
            {"id": "vulnerability_checker", "name": "Vuln Check",
             "tool": "tools/maintenance/vulnerability_checker.py",
             "description": "CVE vulnerability scanning"},
        ],
    },
    "analysis": {
        "label": "Analysis & Research",
        "icon": "chart",
        "color": "analysis",
        "tools": [
            {"id": "knowledge_ingest", "name": "Knowledge Ingest",
             "tool": "tools/knowledge/knowledge_ingest.py",
             "description": "Ingest knowledge artifacts"},
            {"id": "pattern_detector", "name": "Pattern Detect",
             "tool": "tools/knowledge/pattern_detector.py",
             "description": "Detect code and process patterns"},
            {"id": "research_engine", "name": "Research",
             "tool": "tools/research/research_engine.py",
             "description": "Industry research engine"},
        ],
    },
}


def get_tool_catalog() -> dict:
    """Return the full tool catalog for the palette UI."""
    return TOOL_CATEGORIES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CRUD ───────────────────────────────────────────────────

def list_workflows(*, shared_only: bool = False) -> list[dict]:
    """List all studio workflows."""
    conn = get_connection()
    try:
        sql = "SELECT * FROM studio_workflows"
        if shared_only:
            sql += " WHERE shared = 1"
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_workflow(workflow_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflows WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_workflow(
    name: str,
    template_yaml: str,
    *,
    description: str = "",
    category: str = "custom",
    created_by: str = "studio",
) -> dict:
    """Create a new workflow from YAML template string."""
    # Validate YAML
    try:
        parsed = yaml.safe_load(template_yaml)
        if not isinstance(parsed, dict) or "steps" not in parsed:
            return {"status": "error", "error": "YAML must contain 'steps' key"}
    except yaml.YAMLError as exc:
        return {"status": "error", "error": f"Invalid YAML: {exc}"}

    wf_id = f"wf-{uuid.uuid4().hex[:12]}"
    now = _now_iso()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflows
               (workflow_id, name, description, template_yaml, category,
                created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (wf_id, name, description, template_yaml, category, created_by, now, now),
        )
        conn.commit()
        return {"status": "ok", "workflow_id": wf_id}
    finally:
        conn.close()


def update_workflow(
    workflow_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    template_yaml: str | None = None,
    category: str | None = None,
    shared: bool | None = None,
) -> dict:
    """Update an existing workflow."""
    existing = get_workflow(workflow_id)
    if not existing:
        return {"status": "error", "error": "Workflow not found"}

    if template_yaml is not None:
        try:
            parsed = yaml.safe_load(template_yaml)
            if not isinstance(parsed, dict) or "steps" not in parsed:
                return {"status": "error", "error": "YAML must contain 'steps' key"}
        except yaml.YAMLError as exc:
            return {"status": "error", "error": f"Invalid YAML: {exc}"}

    sets: list[str] = []
    vals: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        vals.append(name)
    if description is not None:
        sets.append("description = ?")
        vals.append(description)
    if template_yaml is not None:
        sets.append("template_yaml = ?")
        vals.append(template_yaml)
        sets.append("version = version + 1")
    if category is not None:
        sets.append("category = ?")
        vals.append(category)
    if shared is not None:
        sets.append("shared = ?")
        vals.append(1 if shared else 0)

    if not sets:
        return {"status": "ok", "workflow_id": workflow_id, "message": "Nothing to update"}

    sets.append("updated_at = ?")
    vals.append(_now_iso())
    vals.append(workflow_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE studio_workflows SET {', '.join(sets)} WHERE workflow_id = ?",  # noqa: S608  # nosec B608 — column names are hardcoded, not user input
            vals,
        )
        conn.commit()
        return {"status": "ok", "workflow_id": workflow_id}
    finally:
        conn.close()


def delete_workflow(workflow_id: str) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM studio_workflows WHERE workflow_id = ?",
            (workflow_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"status": "error", "error": "Workflow not found"}
        return {"status": "ok", "deleted": workflow_id}
    finally:
        conn.close()


def workflow_to_composer_format(workflow_id: str) -> dict | None:
    """Convert a studio workflow to the format expected by workflow_composer.py."""
    wf = get_workflow(workflow_id)
    if not wf:
        return None
    try:
        return yaml.safe_load(wf["template_yaml"])
    except yaml.YAMLError:
        return None


# ── Built-in template catalog ──────────────────────────────

def list_builtin_templates() -> list[dict]:
    """List YAML templates from args/workflow_templates/."""
    templates_dir = _ROOT / "args" / "workflow_templates"
    if not templates_dir.exists():
        return []

    results = []
    for f in sorted(templates_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            results.append({
                "id": f.stem,
                "name": data.get("description", f.stem),
                "category": data.get("category", "general"),
                "steps_count": len(data.get("steps", [])),
                "file_path": str(f),
                "source": "builtin",
            })
        except Exception:
            continue
    return results


# ── CLI ────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV Studio Workflow Editor")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("catalog", help="Show tool catalog")
    sub.add_parser("templates", help="List built-in templates")
    sub.add_parser("list", help="List studio workflows")

    p_get = sub.add_parser("get", help="Get workflow by ID")
    p_get.add_argument("workflow_id")

    args = parser.parse_args()

    result: Any = None
    if args.command == "catalog":
        result = get_tool_catalog()
    elif args.command == "templates":
        result = list_builtin_templates()
    elif args.command == "list":
        result = list_workflows()
    elif args.command == "get":
        result = get_workflow(args.workflow_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
