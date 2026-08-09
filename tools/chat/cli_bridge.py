#!/usr/bin/env python3
# CUI // SP-CTI
"""CLI bridge — connects Claude CLI workflows to the ICDEV multi-stream chat API.

Creates and manages chat contexts programmatically so Claude CLI sessions are
visible (and interactive) in the /chat dashboard UI in real time.

Context IDs are persisted in ~/.claude/cli_chat_contexts.json keyed by a
caller-supplied persist_key so a workflow can resume the same context across
invocations.

Usage (called from skill Bash steps or any Python):
    python tools/chat/cli_bridge.py --create --title "Requirements: proj-123" \\
        --persist-key intake:proj-123 --json
    python tools/chat/cli_bridge.py --send "Gathered 12 requirements." \\
        --context ctx-abc --role assistant --json
    python tools/chat/cli_bridge.py --poll --context ctx-abc --since 3 \\
        --timeout 120 --json
    python tools/chat/cli_bridge.py --messages --context ctx-abc --since 0 --json
    python tools/chat/cli_bridge.py --url --context ctx-abc
    python tools/chat/cli_bridge.py --link-intake --context ctx-abc \\
        --intake-session sess-xyz --json
    python tools/chat/cli_bridge.py --close --context ctx-abc --json

Programmatic API (import):
    from tools.chat.cli_bridge import (
        create_context, send_message, get_messages,
        poll_until_response, close_context,
        get_or_create_context, link_intake_session, dashboard_url,
        get_coworker_instances, dashboard_url_coworker,
    )
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib import error as url_error
from urllib import request as url_request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("ICDEV_DASHBOARD_URL", "http://localhost:5050")
PERSIST_PATH = Path.home() / ".claude" / "cli_chat_contexts.json"
DEFAULT_USER_ID = "cli-user"
DEFAULT_POLL_INTERVAL = 2.0  # seconds
DEFAULT_TIMEOUT = 120  # seconds

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http(method: str, path: str, body: Optional[dict] = None, timeout: int = 15) -> dict:
    url = BASE_URL.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = url_request.Request(url, data=data, headers=headers, method=method)
    try:
        with url_request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except url_error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        try:
            return json.loads(raw)
        except Exception:
            return {"error": f"HTTP {exc.code}: {raw[:200]}"}
    except url_error.URLError as exc:
        return {"error": f"Dashboard unreachable ({exc.reason}). Is it running at {BASE_URL}?"}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _load_persist() -> dict:
    if PERSIST_PATH.exists():
        try:
            return json.loads(PERSIST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_persist(store: dict) -> None:
    PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERSIST_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8", newline="")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_context(
    title: str,
    user_id: str = DEFAULT_USER_ID,
    system_prompt: str = "",
    agent_model: str = "sonnet",
    project_id: str = "",
    tenant_id: str = "",
) -> dict:
    """Create a new chat context. Returns context dict with context_id."""
    return _http("POST", "/api/chat/contexts", {
        "user_id": user_id,
        "title": title,
        "system_prompt": system_prompt,
        "agent_model": agent_model,
        "project_id": project_id,
        "tenant_id": tenant_id,
    })


def send_message(context_id: str, content: str, role: str = "user") -> dict:
    """Send a message to a chat context. Returns turn info dict."""
    return _http("POST", f"/api/chat/{context_id}/send", {
        "content": content,
        "role": role,
    })


def get_messages(
    context_id: str,
    since_turn: int = 0,
    limit: int = 100,
) -> list:
    """Fetch messages for a context since a given turn number."""
    result = _http("GET", f"/api/chat/{context_id}/messages?since={since_turn}&limit={limit}")
    return result.get("messages", [])


def get_state(context_id: str, since_version: int = 0) -> dict:
    """Fetch dirty-tracking state for a context."""
    return _http("GET", f"/api/chat/{context_id}/state?since_version={since_version}")


def poll_until_response(
    context_id: str,
    sent_turn: int,
    timeout_s: float = DEFAULT_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> Optional[str]:
    """Block until an assistant message appears after sent_turn, then return content.

    Returns None if timeout expires without a response.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msgs = get_messages(context_id, since_turn=sent_turn + 1)
        for msg in msgs:
            if msg.get("role") == "assistant":
                return msg.get("content", "")
        time.sleep(poll_interval)
    return None


def close_context(context_id: str) -> dict:
    """Close/archive a chat context."""
    return _http("POST", f"/api/chat/{context_id}/close", {})


def link_intake_session(context_id: str, intake_session_id: str) -> dict:
    """Link an intake session to a chat context so extension hooks activate."""
    return _http("PATCH", f"/api/chat/{context_id}/link-intake", {
        "intake_session_id": intake_session_id,
    })


def get_or_create_context(
    persist_key: str,
    title: str,
    **kwargs,
) -> str:
    """Return context_id for persist_key if still active, else create a new one.

    Stores mapping in ~/.claude/cli_chat_contexts.json for cross-invocation reuse.
    """
    store = _load_persist()
    entry = store.get(persist_key)
    if entry:
        ctx_id = entry["context_id"]
        # Verify it's still active
        state = get_state(ctx_id)
        if state.get("status") == "active":
            return ctx_id

    result = create_context(title=title, **kwargs)
    if "error" in result:
        raise RuntimeError(f"Failed to create chat context: {result['error']}")

    ctx_id = result["context_id"]
    store[persist_key] = {
        "context_id": ctx_id,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_persist(store)
    return ctx_id


def dashboard_url(context_id: str) -> str:
    """Return the browser URL for a chat context."""
    return f"{BASE_URL}/chat/{context_id}"


def get_coworker_instances(context_id: str) -> list[dict]:
    """Return ACE coworker instances triggered by a chat context.

    ACE persists ``trigger_ref`` inside ``ace_instances.config_json`` (not a
    top-level column), keys the instance by ``id``, and stores the assembled
    team in ``ace_coworkers``.  This matches a context to its instances by
    decoding each row's config_json and comparing ``trigger_ref``.

    Returns a list of ``{instance_id, state, created_at, team_manifest}`` where
    ``team_manifest`` is the list of ``{role_id, display_name}`` coworkers
    assembled for that instance.  ``ace_*`` are canvas tables (no
    classification/tenant_id columns) so the RLS-free canvas connection is used.

    Returns an empty list if the table does not exist yet or on any DB error.
    """
    try:
        from tools.db.storage import get_canvas_connection  # noqa: PLC0415
        from tools.db.storage import sql_placeholder  # noqa: PLC0415

        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            ph = sql_placeholder(conn)
            inst_rows = conn.execute(
                "SELECT id, state, created_at, config_json FROM ace_instances"
            ).fetchall()

            instances: list[dict] = []
            for row in inst_rows:
                try:
                    ctx = json.loads(row[3]) if row[3] else {}
                except (TypeError, ValueError):
                    ctx = {}
                if ctx.get("trigger_ref") != context_id:
                    continue
                instance_id = row[0]
                cw_rows = conn.execute(
                    f"SELECT role_id, display_name FROM ace_coworkers "
                    f"WHERE instance_id = {ph} ORDER BY created_at ASC",
                    (instance_id,),
                ).fetchall()
                instances.append({
                    "instance_id": instance_id,
                    "state": row[1],
                    "created_at": row[2],
                    "team_manifest": [
                        {"role_id": cw[0], "display_name": cw[1]} for cw in cw_rows
                    ],
                })
            return instances
        finally:
            conn.close()
    except Exception:
        return []


def dashboard_url_coworker(instance_id: str) -> str:
    """Return the browser URL for an ACE coworker instance."""
    return f"{BASE_URL}/coworker/{instance_id}"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="ICDEV multi-stream chat CLI bridge")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create a new chat context")
    group.add_argument("--send", metavar="CONTENT", help="Send a message to a context")
    group.add_argument("--poll", action="store_true", help="Poll for assistant response")
    group.add_argument("--messages", action="store_true", help="List messages for a context")
    group.add_argument("--url", action="store_true", help="Print dashboard URL for a context")
    group.add_argument("--link-intake", action="store_true", help="Link intake session to context")
    group.add_argument("--close", action="store_true", help="Close a chat context")

    p.add_argument("--context", metavar="CTX_ID", help="Chat context ID")
    p.add_argument("--title", default="CLI Chat", help="Context title (--create)")
    p.add_argument("--persist-key", metavar="KEY", help="Persistence key for get_or_create")
    p.add_argument("--user-id", default=DEFAULT_USER_ID, help="User ID")
    p.add_argument("--system-prompt", default="", help="System prompt (--create)")
    p.add_argument("--agent-model", default="sonnet", help="Agent model (--create)")
    p.add_argument("--project-id", default="", help="Project ID (--create)")
    p.add_argument("--role", default="user", help="Message role (--send)")
    p.add_argument("--since", type=int, default=0, help="Since turn number (--poll/--messages)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Poll timeout seconds")
    p.add_argument("--intake-session", metavar="SESS_ID", help="Intake session ID (--link-intake)")
    p.add_argument("--json", action="store_true", help="Output JSON")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    if args.create:
        if args.persist_key:
            try:
                ctx_id = get_or_create_context(
                    persist_key=args.persist_key,
                    title=args.title,
                    user_id=args.user_id,
                    system_prompt=args.system_prompt,
                    agent_model=args.agent_model,
                    project_id=args.project_id,
                )
                result = {"context_id": ctx_id, "url": dashboard_url(ctx_id)}
            except RuntimeError as exc:
                result = {"error": str(exc)}
        else:
            result = create_context(
                title=args.title,
                user_id=args.user_id,
                system_prompt=args.system_prompt,
                agent_model=args.agent_model,
                project_id=args.project_id,
            )
            if "context_id" in result:
                result["url"] = dashboard_url(result["context_id"])

    elif args.send:
        if not args.context:
            print(json.dumps({"error": "--context required for --send"}), file=sys.stderr)
            sys.exit(1)
        result = send_message(args.context, args.send, role=args.role)

    elif args.poll:
        if not args.context:
            print(json.dumps({"error": "--context required for --poll"}), file=sys.stderr)
            sys.exit(1)
        content = poll_until_response(
            args.context, sent_turn=args.since, timeout_s=args.timeout
        )
        result = {"content": content, "timed_out": content is None}

    elif args.messages:
        if not args.context:
            print(json.dumps({"error": "--context required for --messages"}), file=sys.stderr)
            sys.exit(1)
        msgs = get_messages(args.context, since_turn=args.since)
        result = {"messages": msgs, "total": len(msgs)}

    elif args.url:
        if not args.context:
            print(json.dumps({"error": "--context required for --url"}), file=sys.stderr)
            sys.exit(1)
        url = dashboard_url(args.context)
        if args.json:
            result = {"url": url}
        else:
            print(url)
            return

    elif args.link_intake:
        if not args.context or not args.intake_session:
            print(
                json.dumps({"error": "--context and --intake-session required"}),
                file=sys.stderr,
            )
            sys.exit(1)
        result = link_intake_session(args.context, args.intake_session)

    elif args.close:
        if not args.context:
            print(json.dumps({"error": "--context required for --close"}), file=sys.stderr)
            sys.exit(1)
        result = close_context(args.context)

    else:
        result = {"error": "unknown action"}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
