#!/usr/bin/env python3
# CUI // SP-CTI
"""Build session context for Claude Code from project state (D190).

Detects the current project (via icdev.yaml or DB lookup), loads compliance
posture, dev profile, recent activity, and active intake sessions, then
outputs a structured markdown block for Claude consumption.

Detection order:
    1. icdev.yaml in cwd → manifest_loader.load_manifest()
    2. DB lookup by directory_path → project_status.get_project_status()
    3. Neither found → minimal context with setup instructions

The output is advisory (D189) — stdout markdown, not dynamic CLAUDE.md
injection (D190).

Usage:
    python tools/project/session_context_builder.py --format markdown
    python tools/project/session_context_builder.py --json
    python tools/project/session_context_builder.py --dir /path/to/project
    python tools/project/session_context_builder.py --init --json
"""

import argparse
import json
import sqlite3
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Import sibling tools
sys.path.insert(0, str(BASE_DIR))
from tools.project.manifest_loader import (
    load_manifest,
    detect_vcs_platform,
    _apply_defaults,
)


# ── Core API ────────────────────────────────────────────────────────────

def build_session_context(directory: str = None, db_path: str = None) -> dict:
    """Build comprehensive session context for Claude Code.

    Args:
        directory: Project directory (defaults to cwd).
        db_path: Path to icdev.db (defaults to data/icdev.db).

    Returns:
        dict with keys: project, compliance, dev_profile, recent_activity,
        recommended_workflows, intake_sessions, warnings, setup_needed,
        source.
    """
    db = Path(db_path) if db_path else DB_PATH
    cwd = Path(directory) if directory else Path.cwd()

    context = {
        "project": {},
        "compliance": {},
        "dev_profile": {},
        "recent_activity": [],
        "recommended_workflows": [],
        "intake_sessions": [],
        "warnings": [],
        "setup_needed": False,
        "source": "none",
    }

    # Step 1: detect the project
    detection = _detect_project(str(cwd), str(db))
    context["source"] = detection["source"]

    if detection["source"] == "yaml":
        config = detection["config"]
        context["project"] = {
            "id": config.get("project", {}).get("id", ""),
            "name": config.get("project", {}).get("name", ""),
            "type": config.get("project", {}).get("type", ""),
            "language": config.get("project", {}).get("language", ""),
            "impact_level": config.get("impact_level", ""),
            "classification": config.get("classification", {}).get("level", ""),
            "ato_status": config.get("compliance", {}).get("ato", {}).get("status", "none"),
            "directory": str(cwd),
        }
        # If we also found a DB record, merge richer data
        if detection.get("db_record"):
            context["project"]["db_project_id"] = detection["db_record"]["id"]
            _enrich_from_db(context, detection["db_record"]["id"], str(db))
        else:
            context["warnings"].append(
                "Project found in icdev.yaml but not registered in ICDEV database. "
                "Run `/icdev-init` or `python tools/project/session_context_builder.py --init` "
                "to register."
            )
        # Manifest warnings
        if detection.get("manifest_warnings"):
            context["warnings"].extend(detection["manifest_warnings"])

    elif detection["source"] == "db":
        rec = detection["db_record"]
        context["project"] = {
            "id": rec.get("id", ""),
            "name": rec.get("name", ""),
            "type": rec.get("type", ""),
            "language": rec.get("tech_stack_backend", ""),
            "impact_level": rec.get("impact_level", "IL4"),
            "classification": rec.get("classification", "CUI"),
            "ato_status": rec.get("ato_status", "none"),
            "directory": str(cwd),
            "db_project_id": rec["id"],
        }
        _enrich_from_db(context, rec["id"], str(db))

    else:
        # Neither yaml nor DB
        context["setup_needed"] = True
        context["warnings"].append(
            "No icdev.yaml found and current directory is not a registered ICDEV project."
        )

    # Suggest workflows based on context
    context["recommended_workflows"] = _suggest_workflows(context)

    return context


def _detect_project(directory: str, db_path: str) -> dict:
    """Detect whether this directory is an ICDEV project.

    Returns:
        dict with source ('yaml', 'db', 'none'), config, db_record,
        manifest_warnings.
    """
    result = {
        "source": "none",
        "config": None,
        "db_record": None,
        "manifest_warnings": [],
    }

    # Try icdev.yaml first
    manifest = load_manifest(directory=directory)
    if manifest["valid"]:
        result["source"] = "yaml"
        result["config"] = manifest["normalized"]
        result["manifest_warnings"] = manifest.get("warnings", [])
    elif manifest["raw"]:
        # Yaml exists but has errors — still use as source with warnings
        result["source"] = "yaml"
        result["config"] = _apply_defaults(deepcopy(manifest["raw"]))
        result["manifest_warnings"] = manifest.get("errors", []) + manifest.get("warnings", [])

    # Try DB lookup by directory path
    db_record = _find_project_by_directory(directory, db_path)
    if db_record:
        result["db_record"] = db_record
        if result["source"] == "none":
            result["source"] = "db"

    return result


def _find_project_by_directory(directory: str, db_path: str) -> dict:
    """Look up a project in the DB by directory_path."""
    db = Path(db_path)
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM projects WHERE directory_path = ? LIMIT 1",
            (directory,),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def _enrich_from_db(context: dict, project_id: str, db_path: str):
    """Enrich context with compliance, dev profile, activity, and intake data."""
    context["compliance"] = _get_compliance_summary(project_id, db_path)
    context["dev_profile"] = _get_dev_profile_summary(project_id, db_path)
    context["recent_activity"] = _get_recent_activity(project_id, db_path=db_path)
    context["intake_sessions"] = _get_active_intake_sessions(project_id, db_path)


# ── Compliance Summary ──────────────────────────────────────────────────

def _get_compliance_summary(project_id: str, db_path: str) -> dict:
    """Get compliance posture summary for a project."""
    summary = {
        "frameworks": [],
        "ssp_version": None,
        "ssp_status": "not_generated",
        "open_poams": 0,
        "stig_cat1": 0,
        "stig_cat2": 0,
        "controls_implemented": 0,
        "controls_total": 0,
        "cato_readiness": None,
    }
    db = Path(db_path)
    if not db.exists():
        return summary

    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        # SSP
        ssp = conn.execute(
            "SELECT version, status FROM ssp_documents WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if ssp:
            summary["ssp_version"] = ssp["version"]
            summary["ssp_status"] = ssp["status"]

        # POAMs open count
        poam = conn.execute(
            "SELECT COUNT(*) as cnt FROM poam_items WHERE project_id = ? AND status = 'open'",
            (project_id,),
        ).fetchone()
        if poam:
            summary["open_poams"] = poam["cnt"]

        # STIG CAT1/CAT2
        stig_rows = conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM stig_findings WHERE project_id = ? AND status IN ('Open', 'open') GROUP BY severity",
            (project_id,),
        ).fetchall()
        for r in stig_rows:
            sev = (r["severity"] or "").lower()
            if sev in ("cat1", "cat i", "1"):
                summary["stig_cat1"] += r["cnt"]
            elif sev in ("cat2", "cat ii", "2"):
                summary["stig_cat2"] += r["cnt"]

        # Controls
        controls = conn.execute(
            "SELECT implementation_status, COUNT(*) as cnt FROM project_controls WHERE project_id = ? GROUP BY implementation_status",
            (project_id,),
        ).fetchall()
        for r in controls:
            summary["controls_total"] += r["cnt"]
            if r["implementation_status"] == "implemented":
                summary["controls_implemented"] += r["cnt"]

        # Frameworks (from framework_applicability table if exists)
        try:
            fw_rows = conn.execute(
                "SELECT framework_id FROM framework_applicability WHERE project_id = ? AND status = 'confirmed'",
                (project_id,),
            ).fetchall()
            summary["frameworks"] = [r["framework_id"] for r in fw_rows]
        except sqlite3.OperationalError:
            # Table may not exist
            pass

        # cATO readiness
        try:
            cato = conn.execute(
                "SELECT readiness_score FROM cato_evidence WHERE project_id = ? ORDER BY assessed_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if cato:
                summary["cato_readiness"] = cato["readiness_score"]
        except sqlite3.OperationalError:
            pass

        conn.close()
    except Exception:
        pass

    return summary


# ── Dev Profile Summary ─────────────────────────────────────────────────

def _get_dev_profile_summary(project_id: str, db_path: str) -> dict:
    """Get resolved dev profile key dimensions."""
    summary = {}
    db = Path(db_path)
    if not db.exists():
        return summary

    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT dimensions FROM dev_profiles WHERE scope = 'project' AND scope_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row and row["dimensions"]:
            dims = json.loads(row["dimensions"])
            # Extract key dimensions for summary
            lang = dims.get("language", {})
            style = dims.get("style", {})
            testing = dims.get("testing", {})
            security = dims.get("security", {})
            summary = {
                "language": lang.get("primary", ""),
                "min_version": lang.get("min_version", ""),
                "line_length": style.get("line_length", ""),
                "naming_convention": style.get("naming_convention", ""),
                "test_framework": testing.get("framework", ""),
                "min_coverage": testing.get("min_coverage", ""),
                "crypto_standard": security.get("crypto_standard", ""),
            }
        conn.close()
    except Exception:
        pass

    return summary


# ── Recent Activity ─────────────────────────────────────────────────────

def _get_recent_activity(project_id: str, limit: int = 5, db_path: str = None) -> list:
    """Get last N audit trail entries for a project."""
    db = Path(db_path) if db_path else DB_PATH
    if not db.exists():
        return []

    entries = []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT event_type, actor, action, created_at FROM audit_trail WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        for r in rows:
            entries.append({
                "event_type": r["event_type"],
                "actor": r["actor"],
                "action": r["action"],
                "timestamp": r["created_at"],
            })
        conn.close()
    except Exception:
        pass

    return entries


# ── Active Intake Sessions ──────────────────────────────────────────────

def _get_active_intake_sessions(project_id: str, db_path: str) -> list:
    """Get active (non-completed) intake sessions."""
    db = Path(db_path)
    if not db.exists():
        return []

    sessions = []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, customer_name, status, readiness_score, created_at FROM intake_sessions WHERE project_id = ? AND status != 'completed' ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        for r in rows:
            sessions.append({
                "session_id": r["id"],
                "customer_name": r["customer_name"],
                "status": r["status"],
                "readiness_score": r["readiness_score"],
                "created_at": r["created_at"],
            })
        conn.close()
    except Exception:
        pass

    return sessions


# ── Workflow Suggestions ────────────────────────────────────────────────

def _suggest_workflows(context: dict) -> list:
    """Deterministic rules to suggest next actions."""
    suggestions = []
    project = context.get("project", {})
    compliance = context.get("compliance", {})
    intake = context.get("intake_sessions", [])

    if context.get("setup_needed"):
        suggestions.append({
            "command": "/icdev-init",
            "reason": "No project detected — initialize a new ICDEV project",
        })
        return suggestions

    # No DB record but has yaml
    if context.get("source") == "yaml" and not project.get("db_project_id"):
        suggestions.append({
            "command": "/icdev-init",
            "reason": "icdev.yaml found but project not registered in DB",
        })

    # No SSP generated
    if compliance.get("ssp_status") in (None, "not_generated"):
        suggestions.append({
            "command": "/icdev-comply",
            "reason": "No SSP generated — generate ATO compliance artifacts",
        })

    # Open POAMs
    open_poams = compliance.get("open_poams", 0)
    if open_poams > 0:
        suggestions.append({
            "command": "/icdev-comply",
            "reason": f"{open_poams} open POAM item(s) — address findings",
        })

    # STIG CAT1 findings
    cat1 = compliance.get("stig_cat1", 0)
    if cat1 > 0:
        suggestions.append({
            "command": "/icdev-secure",
            "reason": f"{cat1} CAT1 STIG finding(s) — critical, blocks deployment",
        })

    # Active intake sessions
    if intake:
        for s in intake:
            suggestions.append({
                "command": "/icdev-intake",
                "reason": f"Active intake session ({s.get('customer_name', 'unknown')}) — resume requirements gathering",
            })

    # No recent test activity — check if any test events exist
    activity = context.get("recent_activity", [])
    test_events = [a for a in activity if "test" in a.get("event_type", "").lower()]
    if not test_events and project.get("db_project_id"):
        suggestions.append({
            "command": "/icdev-test",
            "reason": "No recent test activity — run test suite",
        })

    return suggestions


# ── Markdown Formatter ──────────────────────────────────────────────────

def _format_markdown(context: dict) -> str:
    """Format context as structured markdown for Claude consumption."""
    lines = []

    if context.get("setup_needed"):
        lines.append("## ICDEV Project Context")
        lines.append("")
        lines.append("**No ICDEV project detected in this directory.**")
        lines.append("")
        lines.append("To get started:")
        lines.append("1. Create an `icdev.yaml` manifest (see `docs/dx/icdev-yaml-spec.md`)")
        lines.append("2. Run `/icdev-init` to initialize the project")
        lines.append("")
        if context.get("warnings"):
            for w in context["warnings"]:
                lines.append(f"> {w}")
            lines.append("")
        return "\n".join(lines)

    project = context.get("project", {})
    compliance = context.get("compliance", {})
    dev_profile = context.get("dev_profile", {})
    activity = context.get("recent_activity", [])
    intake = context.get("intake_sessions", [])
    suggestions = context.get("recommended_workflows", [])

    # Header
    name = project.get("name", "Unknown")
    lines.append(f"## ICDEV Project Context")
    lines.append("")

    # Project info
    lines.append(f"### Project: {name}")
    proj_id = project.get("id", "N/A")
    proj_type = project.get("type", "N/A")
    lang = project.get("language", "")
    il = project.get("impact_level", "N/A")
    classification = project.get("classification", "N/A")
    ato = project.get("ato_status", "none")
    lines.append(f"- **ID**: {proj_id} | **Type**: {proj_type} | **Language**: {lang}")
    lines.append(f"- **Impact Level**: {il} | **Classification**: {classification} | **ATO Status**: {ato}")
    lines.append("")

    # Compliance
    if compliance:
        lines.append("### Compliance Posture")
        frameworks = compliance.get("frameworks", [])
        fw_str = ", ".join(frameworks) if frameworks else "none configured"
        ssp_ver = compliance.get("ssp_version")
        ssp_status = compliance.get("ssp_status", "not_generated")
        ssp_str = f"v{ssp_ver} ({ssp_status})" if ssp_ver else ssp_status
        open_poams = compliance.get("open_poams", 0)
        cat1 = compliance.get("stig_cat1", 0)
        cat2 = compliance.get("stig_cat2", 0)
        implemented = compliance.get("controls_implemented", 0)
        total = compliance.get("controls_total", 0)
        cato = compliance.get("cato_readiness")

        lines.append(f"- **Frameworks**: {fw_str}")
        lines.append(f"- **SSP**: {ssp_str} | **Open POAMs**: {open_poams}")
        lines.append(f"- **STIG**: {cat1} CAT1, {cat2} CAT2 | **Controls**: {implemented}/{total} implemented")
        if cato is not None:
            lines.append(f"- **cATO Readiness**: {cato:.0%}" if isinstance(cato, (int, float)) else f"- **cATO Readiness**: {cato}")
        lines.append("")

    # Dev Profile
    if dev_profile and any(dev_profile.values()):
        lines.append("### Dev Profile")
        lang_p = dev_profile.get("language", "")
        ver = dev_profile.get("min_version", "")
        ll = dev_profile.get("line_length", "")
        naming = dev_profile.get("naming_convention", "")
        tf = dev_profile.get("test_framework", "")
        cov = dev_profile.get("min_coverage", "")
        crypto = dev_profile.get("crypto_standard", "")

        if lang_p:
            lang_str = f"{lang_p} {ver}".strip() if ver else lang_p
            lines.append(f"- **Language**: {lang_str}")
        if ll or naming:
            style_parts = []
            if ll:
                style_parts.append(f"{ll}-char lines")
            if naming:
                style_parts.append(naming)
            lines.append(f"- **Style**: {', '.join(style_parts)}")
        if tf or cov:
            test_parts = []
            if tf:
                test_parts.append(tf)
            if cov:
                test_parts.append(f">= {cov}% coverage")
            lines.append(f"- **Testing**: {', '.join(test_parts)}")
        if crypto:
            lines.append(f"- **Crypto**: {crypto}")
        lines.append("")

    # Recent Activity
    if activity:
        lines.append("### Recent Activity")
        for a in activity[:5]:
            ts = a.get("timestamp", "")
            if ts:
                ts = ts[:10]  # date only
            evt = a.get("event_type", "")
            action = a.get("action", "")
            actor = a.get("actor", "")
            lines.append(f"- [{ts}] {evt}: {action} ({actor})")
        lines.append("")

    # Active Intake Sessions
    if intake:
        lines.append("### Active Intake Sessions")
        for s in intake:
            name_s = s.get("customer_name", "unknown")
            status = s.get("status", "")
            score = s.get("readiness_score")
            score_str = f", readiness: {score:.0%}" if isinstance(score, (int, float)) and score else ""
            lines.append(f"- **{name_s}** — {status}{score_str}")
        lines.append("")

    # Suggested Actions
    if suggestions:
        lines.append("### Suggested Actions")
        for s in suggestions:
            cmd = s.get("command", "")
            reason = s.get("reason", "")
            lines.append(f"- Run `{cmd}` — {reason}")
        lines.append("")

    # Warnings
    if context.get("warnings"):
        lines.append("### Warnings")
        for w in context["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


# ── Init from Manifest ──────────────────────────────────────────────────

def init_from_manifest(directory: str = None, db_path: str = None) -> dict:
    """Create a DB project record from icdev.yaml.

    Bridges the gap between 'icdev.yaml exists' and 'project is in the DB'.
    Also creates a dev profile if the manifest specifies a profile template.

    Args:
        directory: Project directory containing icdev.yaml.
        db_path: Path to icdev.db.

    Returns:
        dict with project_id, created, dev_profile_created, errors.
    """
    import uuid

    db = Path(db_path) if db_path else DB_PATH
    cwd = Path(directory) if directory else Path.cwd()

    result = {
        "project_id": None,
        "created": False,
        "dev_profile_created": False,
        "errors": [],
    }

    # Load manifest
    manifest = load_manifest(directory=str(cwd))
    if not manifest["valid"]:
        result["errors"] = manifest["errors"]
        return result

    raw = manifest["raw"]
    config = manifest["normalized"]
    project = config.get("project", {})
    name = project.get("name", "unnamed")
    project_type = project.get("type", "webapp")
    language = project.get("language", "python")
    il = config.get("impact_level", "IL4")
    classification = config.get("classification", {}).get("level", "CUI")
    ato_status = config.get("compliance", {}).get("ato", {}).get("status", "none")
    frameworks = config.get("compliance", {}).get("frameworks", [])
    cloud = config.get("deployment", {}).get("cloud", "aws_govcloud")

    if not db.exists():
        result["errors"].append(f"Database not found: {db}")
        return result

    # Check if already registered
    existing = _find_project_by_directory(str(cwd), str(db))
    if existing:
        result["project_id"] = existing["id"]
        result["errors"].append(f"Project already registered with ID {existing['id']}")
        return result

    # Create project record
    project_id = project.get("id", f"proj-{name.lower().replace(' ', '-')}")
    db_id = str(uuid.uuid4())

    try:
        conn = sqlite3.connect(str(db))
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO projects
               (id, name, description, type, classification, status,
                tech_stack_backend, directory_path, created_by,
                impact_level, cloud_environment, target_frameworks,
                ato_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 'icdev-yaml',
                       ?, ?, ?, ?, ?, ?)""",
            (
                db_id, name, config.get("description", ""),
                project_type, classification,
                language, str(cwd), il, cloud,
                ",".join(frameworks), ato_status,
                now, now,
            ),
        )

        # Audit trail
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, classification)
                   VALUES (?, 'project.init_from_manifest', 'session_context_builder',
                           ?, ?, ?)""",
                (
                    db_id,
                    f"Initialized project '{name}' from icdev.yaml",
                    json.dumps({"manifest_path": str(cwd / "icdev.yaml"), "impact_level": il}),
                    classification,
                ),
            )
        except Exception:
            pass

        conn.commit()
        result["project_id"] = db_id
        result["created"] = True

        # Create dev profile only if user explicitly specified a template in raw yaml
        profile_template = raw.get("profile", {}).get("template") if isinstance(raw.get("profile"), dict) else None
        if profile_template:
            try:
                conn.execute(
                    """INSERT INTO dev_profiles
                       (scope, scope_id, version, template, dimensions, created_by, created_at)
                       VALUES ('project', ?, 1, ?, '{}', 'icdev-yaml', ?)""",
                    (db_id, profile_template, now),
                )
                conn.commit()
                result["dev_profile_created"] = True
            except Exception:
                pass

        conn.close()
    except Exception as exc:
        result["errors"].append(f"Database error: {exc}")

    return result


# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build session context for Claude Code (D190)"
    )
    parser.add_argument("--dir", help="Project directory (defaults to cwd)")
    parser.add_argument("--db", help="Path to icdev.db")
    parser.add_argument("--format", choices=["markdown", "json"],
                        default="markdown", help="Output format")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON (shortcut for --format json)")
    parser.add_argument("--init", action="store_true",
                        help="Initialize DB project record from icdev.yaml")
    args = parser.parse_args()

    if args.init:
        result = init_from_manifest(directory=args.dir, db_path=args.db)
        if args.json or args.format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            if result["created"]:
                print(f"Project registered: {result['project_id']}")
                if result["dev_profile_created"]:
                    print("Dev profile created from template.")
            else:
                for err in result["errors"]:
                    print(f"ERROR: {err}")
        sys.exit(0 if result.get("created") or result.get("project_id") else 1)

    context = build_session_context(directory=args.dir, db_path=args.db)

    if args.json or args.format == "json":
        print(json.dumps(context, indent=2, default=str))
    else:
        print(_format_markdown(context))


if __name__ == "__main__":
    main()
