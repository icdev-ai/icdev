#!/usr/bin/env python3
# CUI // SP-CTI
"""Governed ICDEV workflows exposed as /v1 "models" (owui-flow-01, D-OWUI-1).

A chat client picks ``icdev/intake`` or ``icdev/kanban`` in its model list and
reaches an ICDEV WORKFLOW instead of a language model. The point is reach, not a
second implementation: each workflow calls the existing ICDEV tool and answers
with what it did plus a deep link into the dashboard for anything a human must
decide. Nothing here bypasses a gate:

  intake  -> tools/chat/requirement_intake_hook.process_message_for_intake:
             requirements are extracted, decomposed and STAGED for HITL review.
             No kanban row is written; promotion happens only on approval.
  kanban  -> read-only board counts by status (optionally one project prefix
             from args/projects.yaml). Never claims, moves or seeds.

Role gating is declared per model (``required_roles`` in args/openai_compat.yaml)
and enforced by the caller before :func:`run` -- the board is platform-wide,
so an unscoped kanban model would show every tenant the internal board.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# kax-conflict-04: run by path, sys.path[0] is this file's own directory -- bootstrap
# the import root before the first first-party import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

BASE_DIR = repo_root(__file__)
logger = get_logger("icdev.saas.openai_workflows")


class WorkflowError(RuntimeError):
    """A workflow could not run; the message is safe to show the user."""


def _dashboard() -> str:
    return (os.environ.get("ICDEV_DASHBOARD_URL") or "http://localhost:5050").rstrip("/")


def _last_user(messages: List[Dict[str, str]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            return m["content"]
    return ""


def conversation_key(tenant_id: str, user_id: str, messages: List[Dict[str, str]],
                     chat_id: Optional[str] = None) -> str:
    """A stable per-conversation context id for the intake session.

    Open WebUI can forward its chat id (``X-OpenWebUI-Chat-Id``); without it the
    FIRST user turn anchors the conversation, so later turns join one session.
    """
    if chat_id:
        raw = f"{tenant_id}|{user_id}|{chat_id}"
    else:
        first = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        raw = f"{tenant_id}|{user_id}|{first}"
    return "owui-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------
def run_intake(messages: List[Dict[str, str]], *, context_id: str,
               process: Optional[Callable[..., dict]] = None) -> Dict[str, Any]:
    if process is None:
        from tools.chat.requirement_intake_hook import process_message_for_intake as process
    text = _last_user(messages)
    if not text:
        raise WorkflowError("send the requirement text as a user message")
    result = process(context_id, text) or {}

    if result.get("skipped"):
        return {"text": ("I didn't find a requirement statement in that message. Phrase it as "
                         "\"The system shall ...\" or describe a capability the system must provide."),
                "details": {"skipped": True}}
    if result.get("error"):
        raise WorkflowError(f"intake failed: {result['error']}")
    found = int(result.get("requirements_found") or 0)
    if not found:
        return {"text": "No new requirements were extracted from that message.",
                "details": {"requirements_found": 0, "session_id": result.get("session_id")}}
    url = _dashboard() + str(result.get("review_url") or "")
    return {
        "text": (f"Captured {found} requirement(s) and decomposed them into SAFe work items. "
                 f"Nothing reaches the kanban board until a human approves it.\n\n"
                 f"Review and approve: {url}"),
        "details": {"requirements_found": found, "session_id": result.get("session_id"),
                    "hitl_instance_id": result.get("hitl_instance_id"), "review_url": url},
    }


# ---------------------------------------------------------------------------
# kanban (read-only)
# ---------------------------------------------------------------------------
def _project_prefixes() -> Dict[str, str]:
    import yaml

    try:
        data = yaml.safe_load((BASE_DIR / "args" / "projects.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    return {str(p["task_prefix"]): str(p.get("name") or p.get("key"))
            for p in data.get("projects") or [] if p.get("task_prefix")}


def run_kanban(messages: List[Dict[str, str]], *, get_conn=None,
               prefixes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    text = _last_user(messages).lower()
    prefixes = _project_prefixes() if prefixes is None else prefixes
    # Longest match wins, so `aadc-enh-` beats `aadc-`.
    prefix = next((p for p in sorted(prefixes, key=len, reverse=True)
                   if re.search(r"(?<![a-z0-9-])" + re.escape(p.lower()), text)), None)

    if get_conn is None:
        from tools.db.storage import get_connection as get_conn
    conn = get_conn()
    try:
        if prefix:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM kanban_tasks WHERE id LIKE %s "
                                "GROUP BY status", (prefix + "%",)).fetchall()
        else:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM kanban_tasks GROUP BY status").fetchall()
    finally:
        conn.close()
    counts = {str(r["status"]): int(r["n"]) for r in rows}
    total = sum(counts.values())
    scope = f"{prefixes[prefix]} (`{prefix}`)" if prefix else "the whole board"
    lines = [f"Kanban status for {scope}: {total} task(s)."]
    lines += [f"- {status}: {n}" for status, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    lines.append(f"\nBoard: {_dashboard()}/")
    return {"text": "\n".join(lines), "details": {"prefix": prefix, "counts": counts, "total": total}}


WORKFLOWS: Dict[str, Callable[..., Dict[str, Any]]] = {"intake": run_intake, "kanban": run_kanban}


def run(workflow: str, messages: List[Dict[str, str]], *, tenant_id: str, user_id: str,
        chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Run a declared workflow. Raises :class:`WorkflowError` with a user-safe message."""
    if workflow == "intake":
        return run_intake(messages, context_id=conversation_key(tenant_id, user_id, messages, chat_id))
    if workflow == "kanban":
        return run_kanban(messages)
    raise WorkflowError(f"unknown workflow {workflow!r}")
