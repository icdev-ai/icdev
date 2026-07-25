# CUI // SP-CTI
"""State-mutating built-in tools for the SAG runtime (sag-reg-02).

These are the write-side counterparts to the read-only starter toolset in
``builtin_tools.py``. They are declared with the :func:`tools.agent_runtime.discovery.tool`
decorator (``read_only=False``) so discovery picks them up and the dispatch layer
routes them through the **safety gate** before execution — file writes and command
execution must never run unguarded.

- ``write_file``  — write UTF-8 text to a path under the repo root (``..`` rejected).
- ``run_command`` — run an allowlisted ``python tools/ | python -m tools | python -c``
  command, reusing :func:`tools.skills.invoke.run_command` so the terminal surface
  shares one allowlist with the headless skill runner.

Each function keeps the agent-loop-friendly shape (returns a ``str``, never raises)
and accepts an optional ``stop_event`` that the dispatch layer may inject.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from tools.agent_runtime.discovery import tool
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.mutating_tools")

_MAX_WRITE_BYTES = 500_000


def _find_repo_root(start: Path) -> Path:
    sentinels = ("pyproject.toml", ".git", "CLAUDE.md")
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in sentinels):
            return parent
    return start.parents[1] if len(start.parents) > 1 else start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)


def _resolve_in_repo(rel: str) -> Optional[Path]:
    if not rel:
        return None
    candidate = (_REPO_ROOT / rel).resolve()
    try:
        candidate.relative_to(_REPO_ROOT)
    except ValueError:
        return None
    return candidate


@tool(read_only=False)
def write_file(
    path: str,
    content: str,
    stop_event: "threading.Event | None" = None,
) -> str:
    """Write UTF-8 text to a file under the repository root, creating parents.

    Args:
        path: Path relative to the repository root. ``..`` escapes are rejected.
        content: The full UTF-8 text to write (overwrites any existing file).
    """
    del stop_event  # not used; accepted so dispatch may inject it uniformly
    p = str(path or "").strip()
    if not p:
        return "error: 'path' is required"
    resolved = _resolve_in_repo(p)
    if resolved is None:
        return f"error: path escapes repository root: {p!r}"
    if resolved.is_dir():
        return f"error: '{p}' is a directory"
    data = (content or "").encode("utf-8")
    if len(data) > _MAX_WRITE_BYTES:
        return f"error: content exceeds {_MAX_WRITE_BYTES} bytes"
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        return f"error writing {p}: {exc}"
    return f"wrote {len(data)} bytes to {p}"


@tool(read_only=False)
def run_command(
    command: str,
    stop_event: "threading.Event | None" = None,
) -> str:
    """Run one allowlisted shell command and return its output as JSON.

    Only ``python tools/``, ``python -m tools``, and ``python -c`` prefixes are
    permitted (shared with tools/skills/invoke.py). Anything else is refused.

    Args:
        command: The full command line to execute.
    """
    del stop_event
    cmd = str(command or "").strip()
    if not cmd:
        return "error: 'command' is required"
    try:
        from tools.skills.invoke import run_command as _invoke_run

        result = _invoke_run(cmd, [])
    except Exception as exc:  # noqa: BLE001
        return f"error running command: {exc}"
    return json.dumps(result, default=str)[:_MAX_WRITE_BYTES]
