# CUI // SP-CTI
"""Deterministic observation capture into the auto_capture buffer (xrv-mem-02).

WHY. ``tools/memory/auto_capture.py::capture`` -- content-hash dedup, the
``memory_buffer`` table, importance, truncation -- was complete and had NO
hook caller: its only runtime consumer, ``heartbeat_daemon
.check_memory_maintenance``, FLUSHES a buffer nothing filled, and
``.claude/hooks/post_tool_use.py`` persisted tool-input KEY NAMES only. The
observation-MINING half already exists in ``tools/genesis/pattern_detector``
(tool chains over ``hook_events``) feeding ``reflexes/synthesize`` goal
drafts -- never ``memory_entries``. This module is TWO SINKS onto that one
buffer and NOT a second miner:

  * :func:`observe_tool` / :func:`observe_prompt` derive an observation from a
    hook event WITHOUT a model call, for the per-event facts the chain miner
    structurally cannot see (a file path, a commit subject, a test summary,
    a stated decision). :func:`capture_observation` writes it through
    ``auto_capture.capture`` -- the existing seam, never a private INSERT.
  * :func:`buffer_procedural_patterns` takes the chains ``pattern_detector``
    ALREADY scored (the synthesize reflex hands it the same list it stores
    into ``genesis_tool_patterns``) and buffers one ``procedural`` row per
    chain above ``min_pattern_frequency``, so the miner's output reaches
    recall.

FOUR OBSERVATION KINDS, closed (``KINDS``): ``edit``, ``commit``,
``test_summary``, ``decision``. Each is read off PRIMARY evidence -- a
commit is recorded from git's own ``[branch sha] subject`` confirmation line
and never from the ``-m`` argument (a refused commit has an argument and no
commit); a test summary is pytest's own ``=== N passed ... ===`` line and
never the command (a command that ran is not a result).

``<private>...</private>`` SPANS ARE REMOVED BEFORE CAPTURE
(:func:`strip_private`), an unclosed ``<private>`` drops everything after it,
and an observation that is private through and through is SKIPPED
(``private_only``) -- the buffer never sees a byte of it. The hook event row
records only that a span was stripped, never what.

BOUNDED PER SESSION: ``auto_capture.max_per_session`` in
``args/memory_config.yaml`` (default 200). The counter lives in a per-session
file under ``.tmp/memory_capture/`` and NOT in ``memory_buffer``, because the
buffer auto-flushes at ``buffer_flush_threshold`` (100) rows and a row count
over a table that empties itself can never reach a cap of 200. Over budget is
``over_budget`` and writes nothing; ``.tmp`` is disposable, so a lost counter
resets the cap for that session -- stated, not hidden.

FLUSH IS NOT THIS MODULE'S. Delivery to ``memory_entries`` stays the existing
``memory_maintenance_reflex`` / ``maintenance_cron`` path
(``auto_capture.flush_buffer`` with no ``db_path``, i.e. the SAME board
``capture`` writes to). ``heartbeat_daemon`` flushes ``data/memory.db``
instead, a file this board does not use -- named, not repaired here.

Every write goes to ``get_connection()``'s board -- PostgreSQL on this host,
``data/icdev.db`` on a SQLite install. A SQLite board whose file does not
exist yet is ``no_board``: a hook must never CREATE a database as a side
effect (the worktree trap, where a checkout with no ``.env`` silently reads
a throwaway SQLite file).

Kill switch: ``ICDEV_MEMORY_CAPTURE=0``. Config: ``auto_capture.enabled``.

CLI (report only, no --gate):
    python -m tools.hooks.observation_capture --survey [--since-days 1] [--project ICDev] --json
    python -m tools.hooks.observation_capture --status --json
    python -m tools.hooks.observation_capture --procedural [--dry-run] --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

from icdev.core.paths import repo_root

REPO_ROOT = repo_root(__file__)
CONFIG_PATH = REPO_ROOT / "args" / "memory_config.yaml"
DEFAULT_STATE_DIR = REPO_ROOT / ".tmp" / "memory_capture"
DEFAULT_MAX_PER_SESSION = 200
DEFAULT_IMPORTANCE = 3

KINDS = ("edit", "commit", "test_summary", "decision")
STATUSES = (
    "captured", "duplicate", "private_only", "over_budget",
    "disabled", "no_board", "error",
)

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})

_PRIVATE_RE = re.compile(r"<private>.*?</private>", re.IGNORECASE | re.DOTALL)
_PRIVATE_OPEN_RE = re.compile(r"<private>.*\Z", re.IGNORECASE | re.DOTALL)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|(\s])git\s+(?:-[cC]\s+\S+\s+|--\S+\s+)*commit\b")
_COMMIT_LINE_RE = re.compile(
    r"^\[(?P<branch>[^\]\s]+)(?: \(root-commit\))? (?P<sha>[0-9a-f]{7,40})\] (?P<subject>.+?)\s*$",
    re.MULTILINE,
)
_PYTEST_RE = re.compile(r"(?:^|[;&|(\s])(?:python(?:3)?(?:\.exe)?\s+-m\s+)?pytest\b")
# pytest's summary line in BOTH shapes: `===== 3 passed, 1 failed in 0.4s =====`
# (default verbosity) and the bare `3 passed, 1 failed in 0.4s` that `-q` --
# this repo's convention -- prints. The first survey over 7 days of
# transcripts read 4 test summaries against 116 commits because the regex
# demanded the bars; every gated run here is `-q`.
_PYTEST_SUMMARY_RE = re.compile(
    r"^(?:=+\s)?(?P<summary>(?:no tests ran|\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|"
    r"deselected|warnings?|subtests? passed|subtests? failed))(?:, \d+ [a-z ]+?)*"
    r"(?: in \d+(?:\.\d+)?s(?: \([^)\n]*\))?)?)(?:\s=+)?\s*$",
    re.MULTILINE,
)
_DURATION_RE = re.compile(r"\s+in \d+(?:\.\d+)?s(?: \([^)]*\))?\s*$")
_DECISION_RE = re.compile(r"^\s*decision\s*:\s*", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

COMMAND_CHARS = 120


@dataclass
class Observation:
    kind: str
    content: str
    tool_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── private spans ──────────────────────────────────────────────────────


def strip_private(text: str) -> tuple[str, bool]:
    """Remove every ``<private>...</private>`` span; an unclosed tag drops the rest.

    Returns ``(clean_text, had_private)``. Whitespace around a removed span is
    collapsed so the remaining prose still reads.
    """
    if not text:
        return "", False
    clean, n_closed = _PRIVATE_RE.subn("", text)
    clean, n_open = _PRIVATE_OPEN_RE.subn("", clean)
    had = bool(n_closed or n_open)
    if had:
        clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    return clean, had


# ── observation derivation (deterministic, no model) ──────────────────


def _response_text(tool_response: Any) -> str:
    """The textual output of a tool, whatever shape the host handed it over in.

    Claude Code's PostToolUse stdin carries ``tool_response`` -- a dict with
    ``stdout``/``stderr`` for Bash, a string or a list of content blocks for
    others; the legacy hook read ``tool_output``, which is never populated
    (measured 2026-09-11: 0 of the last 200 Bash rows carried output).
    """
    if tool_response is None:
        return ""
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        parts = []
        for key in ("stdout", "stderr", "output", "content", "text"):
            val = tool_response.get(key)
            if isinstance(val, str) and val:
                parts.append(val)
            elif isinstance(val, list):
                parts.append(_response_text(val))
        return "\n".join(parts)
    if isinstance(tool_response, list):
        return "\n".join(_response_text(b) for b in tool_response)
    return str(tool_response)


def _relpath(path: str, project_root: Optional[Path]) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    p = Path(raw)
    if project_root is not None:
        try:
            p = p.resolve()
            return p.relative_to(Path(project_root).resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return p.as_posix()


def _first_line(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _truncate(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def observe_tool(
    tool_name: str,
    tool_input: Any,
    tool_response: Any = None,
    *,
    project_root: Optional[Path] = None,
) -> Optional[Observation]:
    """Derive an observation from one tool event, or None when there is none.

    Pure and deterministic: no database, no model, no clock.
    """
    if not isinstance(tool_input, dict):
        return None
    if tool_name in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        rel = _relpath(path, project_root)
        if not rel:
            return None
        content = f"edited {rel}"
        intent = _first_line(tool_input.get("description") or tool_input.get("intent"))
        if intent:
            content += f": {_truncate(intent, 200)}"
        return Observation("edit", content, tool_name, {"path": rel})
    if tool_name in SHELL_TOOLS:
        command = tool_input.get("command")
        if not isinstance(command, str) or not command:
            return None
        text = _ANSI_RE.sub("", _response_text(tool_response))
        if _GIT_COMMIT_RE.search(command):
            m = None
            for m in _COMMIT_LINE_RE.finditer(text):
                pass  # the LAST confirmation line is the commit the command made
            if m is None:
                return None  # no confirmation: refused, failed, or output withheld
            subject = _truncate(m.group("subject"), 200)
            return Observation(
                "commit",
                f"committed {m.group('sha')[:12]} on {m.group('branch')}: {subject}",
                tool_name,
                {"sha": m.group("sha"), "branch": m.group("branch")},
            )
        if _PYTEST_RE.search(command):
            last = None
            for last in _PYTEST_SUMMARY_RE.finditer(text):
                pass
            if last is None:
                return None
            summary = _truncate(last.group("summary"), 160)
            # The OUTCOME is the observation; the duration is noise that would
            # make every run of the same suite a distinct row (1,139 summaries
            # in 7 days of transcripts, most of them the same result again).
            outcome = _DURATION_RE.sub("", summary).strip()
            return Observation(
                "test_summary",
                f"pytest: {outcome} ({_truncate(command, COMMAND_CHARS)})",
                tool_name,
                {"summary": summary},
            )
    return None


def observe_prompt(prompt: Any) -> Optional[Observation]:
    """A user turn beginning ``decision:`` yields its first sentence."""
    if not isinstance(prompt, str):
        return None
    m = _DECISION_RE.match(prompt)
    if not m:
        return None
    body = prompt[m.end():].strip()
    if not body:
        return None
    first_para = body.split("\n\n", 1)[0].strip()
    sentence = _SENTENCE_END_RE.split(first_para, 1)[0].strip()
    # A sentence cut mid-private-span must still be private-stripped whole,
    # so the FULL paragraph is kept when the split lands inside a span.
    if sentence.lower().count("<private>") != sentence.lower().count("</private>"):
        sentence = first_para
    return Observation("decision", f"decision: {_truncate(sentence, 500)}", None, {})


# ── config / state ─────────────────────────────────────────────────────


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    cfg = {
        "enabled": True,
        "max_per_session": DEFAULT_MAX_PER_SESSION,
        "default_importance": DEFAULT_IMPORTANCE,
    }
    try:
        import yaml  # noqa: PLC0415

        p = Path(path) if path else CONFIG_PATH
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            ac = data.get("auto_capture") or {}
            for key in cfg:
                if key in ac and ac[key] is not None:
                    cfg[key] = ac[key]
    except Exception:  # noqa: BLE001 -- config is advisory; defaults are the contract
        pass
    return cfg


def _state_file(state_dir: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)[:120] or "no-session"
    return Path(state_dir) / f"{safe}.json"


def session_capture_count(session_id: str, state_dir: Optional[Path] = None) -> int:
    f = _state_file(state_dir or DEFAULT_STATE_DIR, session_id)
    try:
        return int(json.loads(f.read_text(encoding="utf-8")).get("captured", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _bump_session_count(session_id: str, state_dir: Path) -> int:
    f = _state_file(state_dir, session_id)
    n = session_capture_count(session_id, state_dir) + 1
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            json.dumps({"captured": n, "updated_at": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return n


def board_available(db_path: Optional[str] = None) -> tuple[bool, str]:
    """Is there a board to write to WITHOUT creating one?

    A SQLite path that does not exist yet is refused: creating a throwaway
    database from inside a hook is how a worktree with no ``.env`` grows a
    private ``data/icdev.db`` that nothing ever reads.
    """
    if db_path:
        return True, "explicit"
    try:
        from tools.db import storage  # noqa: PLC0415

        backend = storage.get_backend()
    except Exception:  # noqa: BLE001
        return True, "unmeasured"
    if backend != "sqlite":
        return True, backend
    path = Path(os.environ.get("ICDEV_DB_PATH", getattr(storage, "DB_PATH", "")))
    if path and path.exists():
        return True, "sqlite"
    return False, "sqlite_missing"


# ── capture ────────────────────────────────────────────────────────────


def capture_observation(
    obs: Optional[Observation],
    session_id: Optional[str],
    *,
    db_path: Optional[str] = None,
    max_per_session: Optional[int] = None,
    state_dir: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    source: str = "hook",
    hook: str = "post_tool_use",
) -> Optional[Dict[str, Any]]:
    """Strip private spans, honour the per-session cap, then ``auto_capture.capture``.

    Returns None when ``obs`` is None, else a dict carrying ``kind``,
    ``status`` (one of STATUSES) and ``private_stripped``. Never raises.
    """
    if obs is None:
        return None
    result: Dict[str, Any] = {"kind": obs.kind, "status": "error", "private_stripped": False}
    try:
        if os.environ.get("ICDEV_MEMORY_CAPTURE", "1") == "0":
            result["status"] = "disabled"
            return result
        cfg = config or load_config()
        if not cfg.get("enabled", True):
            result["status"] = "disabled"
            return result

        content, had_private = strip_private(obs.content)
        result["private_stripped"] = had_private
        if not content or not content.strip(" :.-"):
            result["status"] = "private_only"
            return result

        sid = session_id or "no-session"
        cap = max_per_session if max_per_session is not None else int(cfg.get("max_per_session") or 0)
        sdir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
        count = session_capture_count(sid, sdir)
        result["session_count"] = count
        if cap and count >= cap:
            result["status"] = "over_budget"
            return result

        ok, basis = board_available(db_path)
        result["board"] = basis
        if not ok:
            result["status"] = "no_board"
            return result

        from tools.memory.auto_capture import capture  # noqa: PLC0415

        meta = dict(obs.metadata or {})
        meta.update({"kind": obs.kind, "hook": hook, "private_stripped": had_private})
        out = capture(
            content=content,
            source=source,
            memory_type="event",
            importance=int(cfg.get("default_importance") or DEFAULT_IMPORTANCE),
            session_id=sid,
            tool_name=obs.tool_name,
            metadata=meta,
            db_path=db_path,
        )
        status = out.get("status", "error")
        result["status"] = status if status in STATUSES else "error"
        if status == "captured":
            result["buffer_id"] = out.get("buffer_id")
            result["session_count"] = _bump_session_count(sid, sdir)
        elif status == "error":
            result["error"] = str(out.get("error", ""))[:200]
        return result
    except Exception as exc:  # noqa: BLE001 -- a hook never fails a tool call
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return result


def capture_tool_event(
    tool_name: str,
    tool_input: Any,
    tool_response: Any = None,
    session_id: Optional[str] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """observe_tool + capture_observation, the ONE call both hook paths make."""
    obs = observe_tool(tool_name, tool_input, tool_response, project_root=kwargs.pop("project_root", REPO_ROOT))
    return capture_observation(obs, session_id, hook="post_tool_use", **kwargs)


def capture_prompt_event(prompt: Any, session_id: Optional[str] = None, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """observe_prompt + capture_observation, for the user_prompt_submit hook."""
    return capture_observation(observe_prompt(prompt), session_id, hook="user_prompt_submit", **kwargs)


# ── the second sink: pattern_detector's scored chains -> procedural rows ──


def chain_hash(chain: Sequence[str]) -> str:
    """The SAME hash ``pattern_detector.store_patterns`` keys ``genesis_tool_patterns`` on."""
    return hashlib.sha256(json.dumps(list(chain), sort_keys=True).encode()).hexdigest()[:16]


def procedural_content(pattern: Dict[str, Any]) -> str:
    chain = " -> ".join(str(t) for t in pattern.get("pattern") or [])
    freq = int(pattern.get("frequency") or 0)
    div = int(pattern.get("caller_diversity") or 0)
    return f"Recurring tool chain ({freq}x across {div} session(s)): {chain}"


def buffer_procedural_patterns(
    patterns: Optional[Iterable[Dict[str, Any]]] = None,
    *,
    db_path: Optional[str] = None,
    min_frequency: Optional[int] = None,
    detect: Optional[Callable[[], Dict[str, Any]]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Buffer one ``procedural`` memory per scored chain at or above the threshold.

    ``patterns`` is ``pattern_detector.detect_tool_patterns()["patterns"]`` --
    handed in by the synthesize reflex from the list it ALREADY mined, so no
    second miner runs. When None, the detector is run here (``detect``
    overrides it, for tests). Dedup is content-hash: within the buffer by
    ``capture``, and against ``memory_entries`` by the existing flush.
    """
    from tools.genesis.pattern_detector import _load_synthesize_config  # noqa: PLC0415

    threshold = int(min_frequency if min_frequency is not None else _load_synthesize_config()["min_pattern_frequency"])
    report: Dict[str, Any] = {
        "status": "ok",
        "min_frequency": threshold,
        "patterns_seen": 0,
        "buffered": 0,
        "duplicates": 0,
        "below_threshold": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    if patterns is None:
        try:
            if detect is None:
                from tools.genesis.pattern_detector import detect_tool_patterns  # noqa: PLC0415

                detect = detect_tool_patterns
            detection = detect() or {}
        except Exception as exc:  # noqa: BLE001
            report.update(status="unmeasurable", reason=f"{type(exc).__name__}: {exc}"[:200])
            return report
        if detection.get("error"):
            report.update(status="unmeasurable", reason=str(detection["error"])[:200])
            return report
        patterns = detection.get("patterns") or []

    rows = list(patterns)
    report["patterns_seen"] = len(rows)
    if not rows:
        report["status"] = "empty"
        return report

    from tools.memory.auto_capture import capture  # noqa: PLC0415

    for p in rows:
        if int(p.get("frequency") or 0) < threshold:
            report["below_threshold"] += 1
            continue
        if dry_run:
            report["buffered"] += 1
            continue
        h = chain_hash(p.get("pattern") or [])
        out = capture(
            content=procedural_content(p),
            source="auto",
            memory_type="procedural",
            importance=DEFAULT_IMPORTANCE,
            tool_name="pattern_detector",
            metadata={
                "kind": "procedural_chain",
                "chain_hash": h,
                "pattern_id": f"tpat-{h}",
                "frequency": p.get("frequency"),
                "caller_diversity": p.get("caller_diversity"),
                "composite_score": p.get("composite_score"),
            },
            db_path=db_path,
        )
        status = out.get("status")
        if status == "captured":
            report["buffered"] += 1
        elif status == "duplicate":
            report["duplicates"] += 1
        else:
            report["errors"] += 1
    return report


# ── survey: replay the shipped predicate over Claude Code transcripts ──


def survey_transcripts(
    since_days: float = 1.0,
    project_filter: str = "ICDev",
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Rows the hook WOULD capture per day, replayed over the session transcripts.

    The transcripts carry the full tool input AND the tool result
    (``toolUseResult``), which ``hook_events`` does not, so this is the SHIPPED
    :func:`observe_tool` over real events -- one computation, never a second
    copy of the rule. Dedup is by content hash across the whole window, as the
    buffer would dedup. ``rows_per_day`` is None, never 0, over no measured day.
    """
    from tools.hooks.fire_rate_survey import iter_transcripts  # noqa: PLC0415

    paths = iter_transcripts(root=root, since_days=since_days, project_filter=project_filter)
    by_kind: Dict[str, int] = {k: 0 for k in KINDS}
    distinct: set = set()
    per_day: Dict[str, set] = {}
    sessions: set = set()
    private_stripped = 0
    private_only = 0
    events = 0
    for path in paths:
        pending: Dict[str, tuple] = {}
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                content = (rec.get("message") or {}).get("content")
                day = str(rec.get("timestamp") or "")[:10]
                if rec.get("type") == "user" and isinstance((rec.get("message") or {}).get("content"), str):
                    obs = observe_prompt(rec["message"]["content"])
                    if obs:
                        _tally(obs, day, path.stem, by_kind, distinct, per_day, sessions)
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        events += 1
                        pending[str(block.get("id"))] = (block.get("name") or "", block.get("input"))
                    elif block.get("type") == "tool_result":
                        use = pending.pop(str(block.get("tool_use_id")), None)
                        if not use:
                            continue
                        obs = observe_tool(use[0], use[1], rec.get("toolUseResult"))
                        if obs is None:
                            continue
                        clean, had = strip_private(obs.content)
                        if had:
                            private_stripped += 1
                        if not clean.strip(" :.-"):
                            private_only += 1
                            continue
                        obs.content = clean
                        _tally(obs, day, path.stem, by_kind, distinct, per_day, sessions)
    days = sorted(per_day)
    per_day_counts = {d: len(v) for d, v in per_day.items()}
    total = len(distinct)
    return {
        "since_days": since_days,
        "project_filter": project_filter,
        "transcripts": len(paths),
        "sessions_with_observations": len(sessions),
        "tool_events": events,
        "observations_by_kind": by_kind,
        "distinct_observations": total,
        "private_stripped": private_stripped,
        "private_only_skipped": private_only,
        "days_measured": len(days),
        "per_day": per_day_counts,
        "rows_per_day": (round(total / len(days), 1) if days else None),
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }


def _tally(obs, day, session, by_kind, distinct, per_day, sessions):
    by_kind[obs.kind] = by_kind.get(obs.kind, 0) + 1
    h = hashlib.sha256(obs.content.encode("utf-8")).hexdigest()
    if h in distinct:
        return
    distinct.add(h)
    per_day.setdefault(day or "unknown", set()).add(h)
    sessions.add(session)


def buffer_state(db_path: Optional[str] = None) -> Dict[str, Any]:
    """What the buffer holds now, by source and by session (the AC's COUNT(*))."""
    from tools.memory.auto_capture import buffer_status  # noqa: PLC0415

    status = buffer_status(db_path=db_path)
    out = {"total_buffered": status.get("total_buffered"), "by_source": status.get("by_source", {})}
    try:
        from tools.memory.auto_capture import _get_connection  # noqa: PLC0415

        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT session_id, COUNT(*) AS n FROM memory_buffer GROUP BY session_id ORDER BY n DESC LIMIT 20"
            ).fetchall()
            out["by_session"] = {str(r["session_id"]): r["n"] for r in rows}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        out["by_session"] = None
        out["error"] = str(exc)[:200]
    return out


# ── CLI ────────────────────────────────────────────────────────────────


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic memory observation capture (xrv-mem-02)")
    ap.add_argument("--survey", action="store_true", help="replay observe_tool over Claude Code transcripts")
    ap.add_argument("--since-days", type=float, default=1.0)
    ap.add_argument("--project", default="ICDev", help="transcript project-dir substring filter")
    ap.add_argument("--status", action="store_true", help="memory_buffer contents by source / session")
    ap.add_argument("--procedural", action="store_true", help="buffer pattern_detector chains as procedural rows")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db-path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.survey:
        report = survey_transcripts(since_days=args.since_days, project_filter=args.project)
    elif args.procedural:
        report = buffer_procedural_patterns(db_path=args.db_path, dry_run=args.dry_run)
    else:
        report = buffer_state(db_path=args.db_path)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        for k, v in report.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
