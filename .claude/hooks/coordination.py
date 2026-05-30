#!/usr/bin/env python3
# CUI // SP-CTI
"""Claude Code hook: auto-coordinate this session with other concurrent sessions.

Wired in .claude/settings.json for three events (via --event):
  * user_prompt_submit — heartbeat + reap stale + surface other active sessions
                         and any conflicting file claims (added to context).
  * pre_tool_use       — on Edit/Write/MultiEdit, WARN (non-blocking) if another
                         session holds the file, then refresh our own soft claim.
  * stop               — release this session's leases + mark it ended.

All logic lives in tools/coordination/ (LLM-agnostic); this hook just auto-invokes
it for Claude sessions. Always exits 0 — coordination must never block the agent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _rel(file_path: str) -> str:
    try:
        return str(Path(file_path).resolve().relative_to(BASE_DIR)).replace("\\", "/")
    except Exception:
        return file_path.replace("\\", "/")


def main() -> int:
    event = "status"
    for i, a in enumerate(sys.argv):
        if a == "--event" and i + 1 < len(sys.argv):
            event = sys.argv[i + 1]

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    # Propagate Claude's session id so coordination keys on the real session.
    sid = data.get("session_id")
    if sid:
        os.environ.setdefault("CLAUDE_SESSION_ID", sid)
        os.environ.setdefault("ICDEV_SESSION_ID", sid)
    os.environ.setdefault("ICDEV_AGENT", "claude")

    try:
        from tools.coordination import leases, session_registry as reg
    except Exception:
        return 0  # coordination unavailable — never block

    try:
        if event == "user_prompt_submit":
            reg.heartbeat()
            reg.reap_stale()
            others = reg.others()
            me = reg.get_session_id()
            others_held = [
                lz for lz in leases.list_leases()
                if lz.get("holder_session") != me
                and str(lz.get("resource", "")).startswith(("service:", "file:"))
            ]
            if others:
                lines = ["[coordination] Other active agent sessions:"]
                for s in others:
                    lines.append(f"  - {s.get('session_id')} ({s.get('agent_type')}): {s.get('current_intent') or 'no stated intent'}")
                for lz in others_held:
                    lines.append(f"  - holds {lz.get('resource')} ({lz.get('holder_agent')}): {lz.get('intent')}")
                lines.append("  Avoid editing files/services another session is actively working.")
                print("\n".join(lines))

        elif event == "pre_tool_use":
            tool = data.get("tool_name", "")
            if tool in ("Edit", "Write", "MultiEdit"):
                fp = (data.get("tool_input") or {}).get("file_path", "")
                if fp:
                    res = f"file:{_rel(fp)}"
                    h = leases.holder(res)
                    if h and h.get("holder_session") != reg.get_session_id():
                        sys.stderr.write(
                            f"[coordination] WARNING: {_rel(fp)} is claimed by session "
                            f"{h.get('holder_session')} ({h.get('holder_agent')}): "
                            f"{h.get('intent')}. Concurrent edits may be overwritten.\n"
                        )
                    # Refresh our own soft claim (warn-only; never blocks).
                    leases.acquire(res, intent="editing", block=False)

        elif event == "stop":
            leases.release_all_for_session()
            reg.end_session()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
