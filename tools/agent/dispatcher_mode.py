#!/usr/bin/env python3
# CUI // SP-CTI
"""Dispatcher-Only Orchestrator Mode (Phase 61, D-DISP-1).

Enforces the GOTCHA principle that the Orchestration layer delegates work
to domain agents rather than executing tools directly. When enabled,
the orchestrator is restricted to dispatch-related tools only (task_dispatch,
agent_status, agent_mailbox, workflow_status, prompt_chain_execute).

Configuration:
    Global toggle: ``args/agent_config.yaml`` > agents.orchestrator.dispatcher_mode.enabled
    Per-project override: ``dispatcher_mode_overrides`` table in icdev.db

Usage::

    from tools.agent.dispatcher_mode import is_dispatcher_mode, is_tool_allowed

    if is_dispatcher_mode(project_id="proj-123"):
        if not is_tool_allowed("scaffold"):
            # Route to builder-agent instead
            ...

CLI::

    python tools/agent/dispatcher_mode.py --status --json
    python tools/agent/dispatcher_mode.py --enable --project-id proj-123 --created-by admin
    python tools/agent/dispatcher_mode.py --disable --project-id proj-123
"""

import argparse
import json
import logging
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "agent_config.yaml"

logger = logging.getLogger("icdev.dispatcher_mode")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(db_path: Path = None):
    """Create the dispatcher_mode_overrides table if it does not exist."""
    conn = _get_db(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dispatcher_mode_overrides (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                custom_dispatch_tools TEXT DEFAULT '[]',
                custom_blocked_tools TEXT DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_by TEXT NOT NULL DEFAULT 'system'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dispatcher_mode_project
            ON dispatcher_mode_overrides(project_id)
        """)
        conn.commit()
    finally:
        conn.close()


def _audit(event_type: str, actor: str, action: str,
           project_id: str = None, details: dict = None,
           db_path: Path = None):
    """Best-effort audit trail logging."""
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type=event_type,
            actor=actor,
            action=action,
            project_id=project_id,
            details=details,
            classification="CUI",
            db_path=db_path,
        )
    except Exception as exc:
        logger.debug("Audit logging failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def _load_dispatcher_config() -> dict:
    """Load dispatcher_mode config from agent_config.yaml.

    Returns:
        Dict with keys: enabled, dispatch_only_tools, blocked_when_dispatching.
        Returns defaults if config file is missing or malformed.
    """
    defaults = {
        "enabled": False,
        "dispatch_only_tools": [
            "task_dispatch",
            "agent_status",
            "agent_mailbox",
            "workflow_status",
            "prompt_chain_execute",
        ],
        "blocked_when_dispatching": [
            "scaffold", "generate_code", "write_tests", "run_tests",
            "lint", "format", "ssp_generate", "poam_generate",
            "stig_check", "sbom_generate", "terraform_plan",
            "terraform_apply", "ansible_run", "k8s_deploy",
        ],
    }

    if not CONFIG_PATH.exists():
        logger.debug("Agent config not found at %s — using defaults", CONFIG_PATH)
        return defaults

    try:
        import yaml
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        orchestrator = config.get("agents", {}).get("orchestrator", {})
        dm = orchestrator.get("dispatcher_mode", {})

        if not dm:
            return defaults

        return {
            "enabled": dm.get("enabled", False),
            "dispatch_only_tools": dm.get("dispatch_only_tools", defaults["dispatch_only_tools"]),
            "blocked_when_dispatching": dm.get("blocked_when_dispatching", defaults["blocked_when_dispatching"]),
        }
    except ImportError:
        logger.warning("PyYAML not installed — using dispatcher_mode defaults")
        return defaults
    except Exception as exc:
        logger.warning("Failed to load agent_config.yaml: %s — using defaults", exc)
        return defaults


# ---------------------------------------------------------------------------
# Per-project override queries
# ---------------------------------------------------------------------------
def _get_project_override(project_id: str, db_path: Path = None) -> Optional[dict]:
    """Look up a per-project dispatcher mode override.

    Args:
        project_id: The project to check.
        db_path: Optional database path override.

    Returns:
        Dict with override data, or None if no override exists.
    """
    if not project_id:
        return None

    _ensure_table(db_path)
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM dispatcher_mode_overrides WHERE project_id = ?",
            (project_id,),
        )
        row = c.fetchone()
        if row is None:
            return None

        result = dict(row)
        for json_field in ("custom_dispatch_tools", "custom_blocked_tools"):
            if result.get(json_field) and isinstance(result[json_field], str):
                try:
                    result[json_field] = json.loads(result[json_field])
                except json.JSONDecodeError:
                    result[json_field] = []
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_dispatcher_mode(project_id: str = None, db_path: Path = None) -> bool:
    """Check whether dispatcher-only mode is active.

    Resolution order:
    1. Per-project override in DB (if project_id provided)
    2. Global config in agent_config.yaml

    Args:
        project_id: Optional project ID to check for per-project override.
        db_path: Optional database path override.

    Returns:
        True if dispatcher mode is enabled (globally or for the project).
    """
    # Check per-project override first
    if project_id:
        override = _get_project_override(project_id, db_path=db_path)
        if override is not None:
            return bool(override.get("enabled", False))

    # Fall back to global config
    config = _load_dispatcher_config()
    return config.get("enabled", False)


def get_dispatch_tools(project_id: str = None, db_path: Path = None) -> List[str]:
    """Get the list of tools the orchestrator IS allowed to use in dispatcher mode.

    Args:
        project_id: Optional project ID for per-project overrides.
        db_path: Optional database path override.

    Returns:
        List of allowed tool names.
    """
    config = _load_dispatcher_config()
    dispatch_tools = list(config.get("dispatch_only_tools", []))

    # Merge per-project custom dispatch tools
    if project_id:
        override = _get_project_override(project_id, db_path=db_path)
        if override and override.get("custom_dispatch_tools"):
            for tool in override["custom_dispatch_tools"]:
                if tool not in dispatch_tools:
                    dispatch_tools.append(tool)

    return dispatch_tools


def get_blocked_tools(project_id: str = None, db_path: Path = None) -> List[str]:
    """Get the list of tools blocked for the orchestrator in dispatcher mode.

    Args:
        project_id: Optional project ID for per-project overrides.
        db_path: Optional database path override.

    Returns:
        List of blocked tool names.
    """
    config = _load_dispatcher_config()
    blocked_tools = list(config.get("blocked_when_dispatching", []))

    # Merge per-project custom blocked tools
    if project_id:
        override = _get_project_override(project_id, db_path=db_path)
        if override and override.get("custom_blocked_tools"):
            for tool in override["custom_blocked_tools"]:
                if tool not in blocked_tools:
                    blocked_tools.append(tool)

    return blocked_tools


def is_tool_allowed(tool_name: str, project_id: str = None,
                    db_path: Path = None) -> bool:
    """Check if a specific tool is allowed for the orchestrator.

    When dispatcher mode is disabled, all tools are allowed.
    When enabled, only dispatch_only_tools are allowed, and
    blocked_when_dispatching tools are explicitly denied.

    Args:
        tool_name: The tool name to check.
        project_id: Optional project ID for per-project overrides.
        db_path: Optional database path override.

    Returns:
        True if the tool is allowed; False if blocked.
    """
    if not is_dispatcher_mode(project_id=project_id, db_path=db_path):
        return True

    dispatch_tools = get_dispatch_tools(project_id=project_id, db_path=db_path)
    blocked_tools = get_blocked_tools(project_id=project_id, db_path=db_path)

    # Explicitly blocked
    if tool_name in blocked_tools:
        return False

    # Explicitly allowed
    if tool_name in dispatch_tools:
        return True

    # Not in either list — default deny in dispatcher mode
    return False


def filter_tools_for_dispatcher(tool_list: List[str],
                                project_id: str = None,
                                db_path: Path = None) -> List[str]:
    """Filter a tool list to only include tools allowed in dispatcher mode.

    When dispatcher mode is disabled, returns the full list unchanged.

    Args:
        tool_list: List of tool names to filter.
        project_id: Optional project ID for per-project overrides.
        db_path: Optional database path override.

    Returns:
        Filtered list containing only allowed tools.
    """
    if not is_dispatcher_mode(project_id=project_id, db_path=db_path):
        return list(tool_list)

    return [
        tool for tool in tool_list
        if is_tool_allowed(tool, project_id=project_id, db_path=db_path)
    ]


def get_redirect_agent(tool_name: str) -> Optional[str]:
    """Suggest which domain agent should handle a blocked tool.

    Maps blocked tools to their natural domain agent for redirection.

    Args:
        tool_name: The blocked tool name.

    Returns:
        Agent ID string, or None if no mapping exists.
    """
    tool_agent_map = {
        # Builder domain
        "scaffold": "builder-agent",
        "generate_code": "builder-agent",
        "write_tests": "builder-agent",
        "run_tests": "builder-agent",
        "lint": "builder-agent",
        "format": "builder-agent",
        # Compliance domain
        "ssp_generate": "compliance-agent",
        "poam_generate": "compliance-agent",
        "stig_check": "compliance-agent",
        "sbom_generate": "compliance-agent",
        # Infrastructure domain
        "terraform_plan": "infra-agent",
        "terraform_apply": "infra-agent",
        "ansible_run": "infra-agent",
        "k8s_deploy": "infra-agent",
    }
    return tool_agent_map.get(tool_name)


# ---------------------------------------------------------------------------
# DB operations (enable/disable per-project)
# ---------------------------------------------------------------------------
def enable_for_project(project_id: str, created_by: str = "system",
                       custom_dispatch_tools: List[str] = None,
                       custom_blocked_tools: List[str] = None,
                       db_path: Path = None) -> dict:
    """Enable dispatcher mode for a specific project.

    Args:
        project_id: The project to enable dispatcher mode for.
        created_by: Who is enabling this override.
        custom_dispatch_tools: Additional dispatch-only tools for this project.
        custom_blocked_tools: Additional blocked tools for this project.
        db_path: Optional database path override.

    Returns:
        Dict with override details.
    """
    _ensure_table(db_path)
    conn = _get_db(db_path)
    try:
        override_id = f"dmo-{uuid.uuid4().hex[:12]}"
        now = _now()

        conn.execute(
            """INSERT INTO dispatcher_mode_overrides
               (id, project_id, enabled, custom_dispatch_tools,
                custom_blocked_tools, created_at, created_by)
               VALUES (?, ?, 1, ?, ?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
               enabled = 1,
               custom_dispatch_tools = excluded.custom_dispatch_tools,
               custom_blocked_tools = excluded.custom_blocked_tools,
               created_at = excluded.created_at,
               created_by = excluded.created_by""",
            (
                override_id,
                project_id,
                json.dumps(custom_dispatch_tools or []),
                json.dumps(custom_blocked_tools or []),
                now,
                created_by,
            ),
        )
        conn.commit()

        result = {
            "id": override_id,
            "project_id": project_id,
            "enabled": True,
            "custom_dispatch_tools": custom_dispatch_tools or [],
            "custom_blocked_tools": custom_blocked_tools or [],
            "created_at": now,
            "created_by": created_by,
        }

        _audit(
            event_type="dispatcher_mode.enabled",
            actor=created_by,
            action=f"Enabled dispatcher mode for project {project_id}",
            project_id=project_id,
            details=result,
            db_path=db_path,
        )

        return result
    finally:
        conn.close()


def disable_for_project(project_id: str, disabled_by: str = "system",
                        db_path: Path = None) -> dict:
    """Disable dispatcher mode for a specific project.

    Args:
        project_id: The project to disable dispatcher mode for.
        disabled_by: Who is disabling this override.
        db_path: Optional database path override.

    Returns:
        Dict with result status.
    """
    _ensure_table(db_path)
    conn = _get_db(db_path)
    try:
        conn.execute(
            """UPDATE dispatcher_mode_overrides
               SET enabled = 0, created_by = ?, created_at = ?
               WHERE project_id = ?""",
            (disabled_by, _now(), project_id),
        )
        conn.commit()

        result = {
            "project_id": project_id,
            "enabled": False,
            "disabled_by": disabled_by,
        }

        _audit(
            event_type="dispatcher_mode.disabled",
            actor=disabled_by,
            action=f"Disabled dispatcher mode for project {project_id}",
            project_id=project_id,
            details=result,
            db_path=db_path,
        )

        return result
    finally:
        conn.close()


def get_status(project_id: str = None, db_path: Path = None) -> dict:
    """Get the current dispatcher mode status.

    Args:
        project_id: Optional project ID for per-project status.
        db_path: Optional database path override.

    Returns:
        Dict with global config, per-project override (if any),
        effective mode, and tool lists.
    """
    config = _load_dispatcher_config()
    override = None
    if project_id:
        override = _get_project_override(project_id, db_path=db_path)

    effective = is_dispatcher_mode(project_id=project_id, db_path=db_path)

    return {
        "global_config": config,
        "project_override": override,
        "effective_dispatcher_mode": effective,
        "dispatch_only_tools": get_dispatch_tools(project_id=project_id, db_path=db_path),
        "blocked_tools": get_blocked_tools(project_id=project_id, db_path=db_path),
        "classification": "CUI",
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """CLI for dispatcher mode management."""
    parser = argparse.ArgumentParser(
        description="ICDEV Dispatcher Mode — restrict orchestrator to delegation-only (Phase 61)"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current dispatcher mode status",
    )
    parser.add_argument(
        "--enable", action="store_true",
        help="Enable dispatcher mode for a project",
    )
    parser.add_argument(
        "--disable", action="store_true",
        help="Disable dispatcher mode for a project",
    )
    parser.add_argument(
        "--check-tool",
        help="Check if a specific tool is allowed",
    )
    parser.add_argument("--project-id", help="Project ID for per-project operations")
    parser.add_argument("--created-by", default="admin", help="Who is making this change")
    parser.add_argument(
        "--custom-dispatch-tools",
        help="JSON array of additional dispatch-only tools",
    )
    parser.add_argument(
        "--custom-blocked-tools",
        help="JSON array of additional blocked tools",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--human", action="store_true", help="Output as human-readable text")
    parser.add_argument("--db-path", help="Override database path")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = Path(args.db_path) if args.db_path else None

    if args.status:
        result = get_status(project_id=args.project_id, db_path=db_path)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            effective = result["effective_dispatcher_mode"]
            print("ICDEV Dispatcher Mode Status")
            print("Classification: CUI // SP-CTI")
            print()
            print(f"  Global enabled: {result['global_config']['enabled']}")
            if result["project_override"]:
                ov = result["project_override"]
                print(f"  Project override ({args.project_id}): enabled={ov['enabled']}")
            else:
                print(f"  Project override: (none)")
            print(f"  Effective mode: {'DISPATCHER-ONLY' if effective else 'FULL ACCESS'}")
            print()
            print("  Dispatch-only tools:")
            for t in result["dispatch_only_tools"]:
                print(f"    - {t}")
            print()
            print("  Blocked tools:")
            for t in result["blocked_tools"]:
                print(f"    - {t}")

    elif args.enable:
        if not args.project_id:
            print("Error: --project-id required for --enable", file=sys.stderr)
            sys.exit(1)

        custom_dispatch = None
        if args.custom_dispatch_tools:
            custom_dispatch = json.loads(args.custom_dispatch_tools)

        custom_blocked = None
        if args.custom_blocked_tools:
            custom_blocked = json.loads(args.custom_blocked_tools)

        result = enable_for_project(
            project_id=args.project_id,
            created_by=args.created_by,
            custom_dispatch_tools=custom_dispatch,
            custom_blocked_tools=custom_blocked,
            db_path=db_path,
        )
        result["classification"] = "CUI"

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Dispatcher mode ENABLED for project: {args.project_id}")
            print(f"  Created by: {result['created_by']}")
            print("Classification: CUI // SP-CTI")

    elif args.disable:
        if not args.project_id:
            print("Error: --project-id required for --disable", file=sys.stderr)
            sys.exit(1)

        result = disable_for_project(
            project_id=args.project_id,
            disabled_by=args.created_by,
            db_path=db_path,
        )
        result["classification"] = "CUI"

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Dispatcher mode DISABLED for project: {args.project_id}")
            print("Classification: CUI // SP-CTI")

    elif args.check_tool:
        allowed = is_tool_allowed(
            args.check_tool,
            project_id=args.project_id,
            db_path=db_path,
        )
        redirect = get_redirect_agent(args.check_tool) if not allowed else None

        result = {
            "tool_name": args.check_tool,
            "allowed": allowed,
            "dispatcher_mode_active": is_dispatcher_mode(
                project_id=args.project_id, db_path=db_path
            ),
            "redirect_agent": redirect,
            "classification": "CUI",
        }

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status_str = "ALLOWED" if allowed else "BLOCKED"
            print(f"Tool: {args.check_tool} -> {status_str}")
            if redirect:
                print(f"  Redirect to: {redirect}")
            print("Classification: CUI // SP-CTI")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
