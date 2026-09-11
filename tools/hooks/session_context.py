# CUI // SP-CTI
"""The token-capped session-start context block (xrv-mem-01, the claude-mem shape).

ONE builder, TWO callers: ``.claude/hooks/session_start.py`` (Claude Code
injects a SessionStart hook's stdout as context) and
``tools/airgap/hook_compat.run_session_start`` (every non-Claude-Code
orchestrator). Both call :func:`build_block`; neither composes a line of its
own, so the two paths cannot drift apart.

WHY. CLAUDE.md's Session Start Protocol -- ``memory_read.py --format markdown``
and ``session_context_builder.py`` -- was a MANUAL step nobody ran, and
``.claude/settings.json`` wired every hook kind except SessionStart. This wires
the documented protocol to the hook the platform already provides, in a form
small enough to load on every session.

WHAT IT EMITS. An index, not the memory: the newest ``memory_entries`` as one
line each -- ``#<id> [<type>] <date> <headline>`` -- through
``memory_read.read_db_recent`` (the SAME seam the protocol command reads), plus
``session_context_builder.build_session_context``'s ``warnings`` when a
project was DETECTED. When ``source`` is ``none`` the builder's only warning is
"not a registered ICDEV project", which is the platform repo's normal state
and would tell every session to run ``/icdev-init``; it is skipped, and
``setup_needed`` is reported on the result instead of in the block.

BOUNDED THREE WAYS, and every bound is reported on the result rather than
producing a quietly short block:
  tokens   ``max_tokens`` (default 1500) through ``context_budget
           .estimate_tokens`` -- the platform's ONE estimator. Oldest entries
           are dropped first, then warnings; ``capped`` says it happened.
  time     ``budget_seconds`` (default 1.0) against ``started`` -- the CALLER's
           clock, so the hook measures from its own first line. Over budget
           the block is WITHHELD (``reason: over_budget``) and the elapsed time
           is on the result; a hook that cannot answer inside a second should
           not answer, but it must say so.
  errors   any exception in either read is ``reason: error:<Type>`` with an
           EMPTY block. A half-block reading "no recent memory" over a failed
           read would be a clean bill of health for a broken read.

``reason`` is one of REASONS, closed: ok | empty | over_budget |
over_token_cap | error:<Type>. ``tokens`` is None -- never 0 -- when no block
was composed.

MEASURED 2026-09-11 on the live PostgreSQL board (27,829 memory_entries):
``read_db_recent(20)`` 0.27s, ``build_session_context`` 0.01s, storage import
0.10s. The whole hook lands well inside the 1s budget with the interpreter
start on top.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Optional

DEFAULT_MAX_TOKENS = 1500
DEFAULT_BUDGET_SECONDS = 1.0
DEFAULT_DB_LIMIT = 20
HEADLINE_CHARS = 120

#: The closed reason vocabulary. ``error:<Type>`` is the one parametrised member.
REASONS = ("ok", "empty", "over_budget", "over_token_cap", "error")

_HEADER = "## ICDEV session context"
_INDEX_INTRO = (
    "Recent memory index (newest first; full read: "
    "`python tools/memory/memory_read.py --format markdown`):"
)
_WARNINGS_INTRO = "Project setup warnings (`python tools/project/session_context_builder.py`):"

# Keys a JSON-bodied entry is headlined from, in preference order. Measured
# 2026-09-11: the newest live entries are `lesson_learned` rows whose content
# is a JSON object with `category` first and `commit_summary` often empty.
_HEADLINE_KEYS = (
    "headline", "title", "summary", "lesson", "category", "commit_summary",
    "message", "description", "content", "text",
)

_WS = re.compile(r"\s+")


def _collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


def headline(content: Any, limit: int = HEADLINE_CHARS) -> str:
    """One line of at most ``limit`` chars for an entry's content.

    A JSON object body is headlined from its first non-empty string under
    ``_HEADLINE_KEYS``; anything else is its first non-empty line. Truncation
    is marked with an ellipsis so a cut line never reads as a whole one.
    """
    text = "" if content is None else str(content)
    stripped = text.strip()
    line = ""
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict):
            for key in _HEADLINE_KEYS:
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    line = _collapse(val)
                    break
    if not line:
        for raw in text.splitlines():
            if raw.strip():
                line = _collapse(raw)
                break
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _date_of(value: Any) -> str:
    if value is None:
        return "????-??-??"
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001 — a datetime-alike that cannot format
            pass
    return str(value)[:10]


def index_lines(rows) -> list[str]:
    """``read_db_recent`` rows -> index lines, newest first as the seam returns them.

    The row is ``(content, type, importance, created_at, classification,
    compartment, id)``; ``id`` was appended LAST by this card so the six
    positional columns every earlier consumer indexes keep their meaning.
    """
    lines = []
    for row in rows or ():
        content = row[0] if len(row) > 0 else ""
        type_ = row[1] if len(row) > 1 else "entry"
        created = row[3] if len(row) > 3 else None
        entry_id = row[6] if len(row) > 6 else None
        ident = f"#{entry_id}" if entry_id is not None else "#?"
        lines.append(f"- {ident} [{type_}] {_date_of(created)} {headline(content)}")
    return lines


def compose(entry_lines: list[str], warning_lines: list[str], *,
            max_tokens: int, estimate: Callable[[str], int]) -> tuple[Optional[str], Optional[int], bool]:
    """Assemble the block under ``max_tokens``.

    Returns ``(text, tokens, capped)``; ``text`` is None when nothing fits or
    there is nothing to say. Oldest entries go first (they are last in the
    list), then warnings from the end.
    """
    entries = list(entry_lines)
    warnings = list(warning_lines)
    capped = False
    while True:
        parts = [_HEADER]
        if entries:
            parts.append(_INDEX_INTRO)
            parts.extend(entries)
        if warnings:
            parts.append(_WARNINGS_INTRO)
            parts.extend(f"- {w}" for w in warnings)
        if len(parts) == 1:
            return None, None, capped
        text = "\n".join(parts) + "\n"
        tokens = estimate(text)
        if tokens <= max_tokens:
            return text, tokens, capped
        capped = True
        if entries:
            entries.pop()
        elif warnings:
            warnings.pop()
        else:  # pragma: no cover — len(parts) == 1 above returns first
            return None, None, capped


def build_block(
    *,
    directory: Optional[str] = None,
    db_limit: int = DEFAULT_DB_LIMIT,
    max_tokens: Optional[int] = None,
    budget_seconds: Optional[float] = None,
    started: Optional[float] = None,
    read_recent: Optional[Callable[..., Any]] = None,
    build_context: Optional[Callable[..., dict]] = None,
    estimate: Optional[Callable[[str], int]] = None,
) -> dict:
    """Build the session-start block. Never raises.

    ``read_recent`` / ``build_context`` / ``estimate`` default to the platform
    seams and exist so a test can hand in a failing or fixed one without a
    database. ``started`` is the caller's ``time.perf_counter()`` reading.
    """
    started = time.perf_counter() if started is None else started
    max_tokens = _env_int("ICDEV_SESSION_START_MAX_TOKENS", DEFAULT_MAX_TOKENS) if max_tokens is None else max_tokens
    budget_seconds = _env_float("ICDEV_SESSION_START_BUDGET_SECONDS", DEFAULT_BUDGET_SECONDS) if budget_seconds is None else budget_seconds

    result: dict[str, Any] = {
        "context": None,
        "emitted": False,
        "reason": "empty",
        "entries_read": None,
        "entries_shown": 0,
        "warnings_read": None,
        "warnings_shown": 0,
        "setup_needed": None,
        "project_source": None,
        "tokens": None,
        "max_tokens": max_tokens,
        "capped": False,
        "budget_seconds": budget_seconds,
        "elapsed_ms": None,
        "directory": directory or os.getcwd(),
    }
    try:
        if read_recent is None:
            from tools.memory.memory_read import read_db_recent as read_recent  # noqa: PLC0415
        if build_context is None:
            from tools.project.session_context_builder import build_session_context as build_context  # noqa: PLC0415
        if estimate is None:
            from tools.llm.context_budget import estimate_tokens as estimate  # noqa: PLC0415

        rows = list(read_recent(db_limit) or [])
        result["entries_read"] = len(rows)
        ctx = build_context(result["directory"]) or {}
        source = ctx.get("source")
        result["project_source"] = source
        result["setup_needed"] = ctx.get("setup_needed")
        warnings = [str(w) for w in (ctx.get("warnings") or []) if str(w).strip()]
        result["warnings_read"] = len(warnings)
        if source in (None, "none"):
            warnings = []

        entry_lines = index_lines(rows)
        text, tokens, capped = compose(entry_lines, warnings, max_tokens=max_tokens, estimate=estimate)
        result["capped"] = capped
        if text is None:
            result["reason"] = "over_token_cap" if capped else "empty"
        else:
            result["context"] = text
            result["tokens"] = tokens
            result["entries_shown"] = sum(1 for line in text.splitlines() if line.startswith("- #"))
            result["warnings_shown"] = sum(
                1 for line in text.splitlines() if line.startswith("- ") and not line.startswith("- #")
            )
            result["reason"] = "ok"
    except Exception as exc:  # noqa: BLE001 — a context hook never fails a session
        result["context"] = None
        result["tokens"] = None
        result["reason"] = f"error:{type(exc).__name__}"

    elapsed = time.perf_counter() - started
    result["elapsed_ms"] = round(elapsed * 1000, 1)
    if result["context"] is not None and elapsed > budget_seconds:
        result["context"] = None
        result["reason"] = "over_budget"
    result["emitted"] = result["context"] is not None
    return result


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def payload_for_event(result: dict) -> dict:
    """The hook_events payload: the measurement, never the block itself."""
    keys = (
        "emitted", "reason", "entries_read", "entries_shown", "warnings_read",
        "warnings_shown", "setup_needed", "project_source", "tokens",
        "max_tokens", "capped", "budget_seconds", "elapsed_ms",
    )
    return {k: result.get(k) for k in keys}


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m tools.hooks.session_context [--json] [--directory D]`` — print
    the block a session would receive, or the measurement."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the result dict, not the block")
    parser.add_argument("--directory", default=None, help="project directory (default: cwd)")
    parser.add_argument("--db-limit", type=int, default=DEFAULT_DB_LIMIT)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    result = build_block(directory=args.directory, db_limit=args.db_limit, started=started)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif result["context"]:
        print(result["context"], end="")
    else:
        print(f"(no block: {result['reason']})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    # `python -m tools.hooks.session_context` — the cwd is already the import root.
    raise SystemExit(main())
