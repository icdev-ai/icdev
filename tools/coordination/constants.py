# CUI // SP-CTI
"""Shared constants + identity helpers for cross-session coordination."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# All coordination scratch state lives here (filelocks + lease metadata).
COORD_DIR = BASE_DIR / ".tmp" / "coordination"
LEASE_DIR = COORD_DIR / "leases"

# Default TTLs (seconds).
SESSION_TTL_SECONDS = 900          # a session with no heartbeat for 15m is stale
FILE_LEASE_TTL_SECONDS = 600       # a soft file-edit claim lasts 10m
SERVICE_LEASE_TTL_SECONDS = 300    # a service-restart claim lasts 5m
HEARTBEAT_INTERVAL_SECONDS = 120   # how often a live session should refresh

# Resource namespaces (logical lease names).
RES_SERVICE = "service"            # service:dashboard, service:genesis-daemon
RES_FILE = "file"                  # file:tools/x.py  (soft / warn-only)
RES_GIT = "git"                    # git:repo         (hard — commit serialization)
RES_MIGRATION = "migration"        # migration:schema (hard — DDL serialization)
RES_KANBAN = "kanban"              # kanban:task:<id>, kanban:runner:global
                                   # (hard — exactly one owner per task, so the
                                   # autonomous runner and an interactive CLI
                                   # session never double-build the same task)

# Hard-enforced namespaces (acquire-or-queue); others are advisory/warn-only.
HARD_NAMESPACES = {RES_SERVICE, RES_GIT, RES_MIGRATION, RES_KANBAN}

# PostgreSQL advisory-lock key namespace salt (keeps our keys from colliding
# with any other advisory-lock user).
ADVISORY_SALT = "icdev-coord"


def get_session_id() -> str:
    """Stable id for the current agent session (LLM-agnostic).

    Reuses the airgap hook-compat resolver (CLAUDE_SESSION_ID / ICDEV_SESSION_ID,
    else a generated local-<uuid>). Falls back to a process-derived id if that
    module is unavailable.
    """
    try:
        from tools.airgap.hook_compat import get_session_id as _gsid
        return _gsid()
    except Exception:
        sid = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("ICDEV_SESSION_ID")
        if sid:
            return sid
        return f"pid-{os.getpid()}"


def get_agent_type() -> str:
    """Best-effort agent/tool identity for display ('claude', 'cursor', …)."""
    explicit = os.environ.get("ICDEV_AGENT")
    if explicit:
        return explicit.strip().lower()
    if os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDECODE"):
        return "claude"
    if os.environ.get("CURSOR_SESSION_ID") or os.environ.get("CURSOR"):
        return "cursor"
    return "unknown"
