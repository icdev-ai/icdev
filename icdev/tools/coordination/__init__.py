# CUI // SP-CTI
"""ICDEV cross-session coordination.

LLM-agnostic primitives so multiple concurrent agent sessions (Claude Code,
Cursor, …) running against the same repo + PostgreSQL DB can see each other and
avoid collisions:

  * session_registry — who is active right now (agent_sessions table + heartbeat)
  * leases           — filelock-backed named resource leases (service:/file:/git:/migration:)
  * dblock           — PostgreSQL advisory locks (serialize migrations / hot-table critical sections)
  * gitlock          — serialize git auto-commits behind a repo-wide lock

Everything is enforced inside the shared ICDEV tools/DB path so a non-Claude
agent that runs ICDEV tools is coordinated even with zero Claude hooks. Claude
sessions additionally auto-register/release via .claude/hooks/.

See docs: goals/session_coordination.md ; memory kanban-tasks-lock-storm.
"""
from __future__ import annotations

__all__ = [
    "session_registry",
    "leases",
    "dblock",
    "gitlock",
]
