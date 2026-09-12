# CUI // SP-CTI
"""Behavioural waste survey over the Claude Code transcripts (xrv-cost-03).

WHY. codeburn's most useful numbers are not billing figures -- they are
BEHAVIOURAL: how often an edit landed without a retry, which files a session
read over and over, which definitions nobody ever invoked, and how much
configuration every session pays for before it does any work. None of those
had a number on this deployment. ``CLAUDE.md`` alone was measured at 352,915
bytes on 2026-09-11 and is loaded by EVERY session; nobody had put a per-day
figure on it.

WHAT IT REUSES, never copies:

* ``tools.hooks.fire_rate_survey.transcript_root`` / ``iter_transcripts`` /
  ``iter_tool_calls`` -- the ONE statement of where transcripts live, how a
  window and project filter select them, and how a ``tool_use`` block is read.
  ``iter_tool_calls`` walks one transcript to completion before the next and
  yields in line order, so grouping its output by ``session`` preserves the
  order the calls were made in -- which is what the retry rule depends on.
* ``tools.awareness.capability_consumption.collect`` -- the Studio-side MCP
  dispatch figure, printed BESIDE the transcript count and never merged.

FIVE MEASURES, each with its denominator stated.

1. ONE-SHOT RATE. **A RETRY is the same file Edited again after a Bash call in
   between.** That is codeburn's rule, stated verbatim, and it is narrower
   than "edited twice": ``Edit A, Edit A`` is one person writing two hunks and
   is NOT a retry, while ``Edit A, Bash, Edit A`` is an edit that was tried,
   checked, and had to be done again. ``Edit A, Bash, Edit B`` is zero retries
   -- the Bash call separates two unrelated edits, not two attempts at one.
   The denominator is edit CALLS (Edit/Write/NotebookEdit/MultiEdit); the rate
   is None over a session that edited nothing.

2. RE-READ FILES. The same path Read at least ``reread_threshold`` times in
   ONE session. Offenders are NAMED with their counts -- an aggregate "N
   re-reads" cannot tell a reader whether one file was read forty times or
   forty files were read three times, and those are different repairs.
   **It is a LOWER BOUND and the report says so with the numbers.** Only the
   ``Read`` tool names a path; this deployment's own harness instruction
   steers sessions to ``cat``/``head``/``sed -n`` through Bash, and a file
   read that way is a shell command no path can be attributed to. Measured
   2026-09-12 over 7 days of ICDev sessions: 17,625 Bash calls against 239
   Read calls, so the measure sees on the order of one read in seventy. The
   report carries ``read_tool_calls`` and ``run_tool_calls`` side by side so a
   reader can see the shortfall rather than read 4 offenders as 4 problems.

3. GHOST DEFINITIONS. ``.claude/commands/*.md``, ``.agents/skills/*/SKILL.md``
   and ``.claude/agents/*`` that no transcript in the window invoked. The
   verdict is ``not_invoked_in_window`` and NEVER ``dead``: a definition may
   be reached from another platform (Cursor, Copilot, the headless
   ``tools/skills/invoke.py`` runner, a cron job) that writes no transcript
   here, and this survey cannot see any of them. A definition ROOT that does
   not exist reports ``absent`` -- never zero ghosts, which would read as a
   clean bill of health for a tree nobody looked at.

4. CONFIG BLOAT. ``CLAUDE.md`` bytes, ``@``-imports expanded transitively, and
   tokens loaded per day. Token counts are ``len(text) // 4``, an
   APPROXIMATION, labelled as one on every field that carries it
   (``token_basis: chars_div_4``) -- no tokenizer is consulted, and quoting
   these as billed tokens is wrong.

5. MCP TOOL USAGE, from the transcripts. This exists because the obvious
   answer is STRUCTURALLY INFLATED and is not reusable:
   ``capability_consumption --class mcp_dispatch_tool`` reads
   ``studio_mcp_dispatch_audit``, a table with exactly two writers, both
   Studio-internal (``tools/studio/executors/mcp_executor.py`` and
   ``agent_tool_gate.py``). NOTHING under ``tools/mcp/`` -- the servers Claude
   Code actually talks to -- writes a dispatch row, so every MCP call made
   from a Claude Code session is invisible to it and a tool used daily reads
   ``inert``. The transcripts are the missing evidence: an MCP call appears in
   a ``tool_use`` block as ``mcp__<server>__<tool>``. Both numbers are
   reported, side by side, under ``transcript`` and ``studio_dispatch_only``,
   with the sentence that THEY MEASURE DIFFERENT CALLERS. They are never
   merged, never summed, and neither is presented as a correction of the
   other. This module does NOT edit that probe or the
   ``args/liveness_gate.yaml`` budget; an MCP-server-side audit write is a new
   writer to an append-only table and is its own card.

Every rate is None -- never 0.0 and never 100.0 -- over an empty denominator
(``args/perfect_score_gate.yaml`` is ratcheted to 0). A window holding no
transcript is ``unmeasurable``, which is its own verdict and never folds into
``clean``.

Report only, no ``--gate`` (kpr-fix-03): this measures the FLEET's behaviour,
not a diff, so a gate would fail commits for a condition the committer did not
cause. Exit 2 = the survey could not be produced, which is never the same as a
survey that found no waste.

Usage::

    python -m tools.cost.waste_survey --json
    python -m tools.cost.waste_survey --since-days 30 --project ICDev
    python -m tools.cost.waste_survey --no-studio --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from icdev.tools.hooks.fire_rate_survey import (
    iter_tool_calls,
    iter_transcripts,
    transcript_root,
)
# Aliased: several functions below take a `repo_root` PARAMETER (the
# checkout whose definitions are measured), which is not this repo's root.
from icdev.core.paths import repo_root as _icdev_repo_root

BASE_DIR = _icdev_repo_root(__file__)

#: Tool names that WRITE a file. The retry rule's denominator.
EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
#: Tool names that READ a file.
READ_TOOLS = frozenset({"Read"})
#: Tool names that RUN something. The separator the retry rule keys on: a
#: command between two edits of one file is the check that sent the second one.
RUN_TOOLS = frozenset({"Bash", "PowerShell", "BashOutput"})

#: A path Read at least this many times in one session is an offender.
DEFAULT_REREAD_THRESHOLD = 3
#: How many offenders / ghosts / tools a report names before truncating.
DEFAULT_MAX_LISTED = 25

#: Definition trees, each ``(kind, relative root, glob, name source)``.
#: ``name source`` is ``stem`` (the file's own name) or ``parent`` (its
#: directory's).
DEFINITION_ROOTS: Tuple[Tuple[str, str, str, str], ...] = (
    ("command", ".claude/commands", "*.md", "stem"),
    ("skill", ".agents/skills", "*/SKILL.md", "parent"),
    ("agent", ".claude/agents", "*.md", "stem"),
)

#: The authoritative slash-command marker Claude Code writes into a user turn.
_COMMAND_TAG_RE = re.compile(r"<command-name>\s*/?([A-Za-z0-9:_.-]+)\s*</command-name>")
#: A ``/name`` a user typed at the START of a line. Anchored on purpose: an
#: unanchored match reads every path in every prompt as an invocation.
_SLASH_LINE_RE = re.compile(r"(?m)^\s*/([A-Za-z0-9:_.-]{2,})\b")
#: ``@path`` on its own line -- CLAUDE.md's import syntax.
_AT_IMPORT_RE = re.compile(r"(?m)^\s*@([\w./\\-]+)\s*$")

_STUDIO_NOTE = (
    "The transcript figure and the Studio figure measure DIFFERENT CALLERS "
    "and are never merged."
)


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: Optional[int]) -> Optional[float]:
    """A percentage, or None when nothing was measured.

    The ONE place a percentage is computed here. ``pct if total else 100.0``
    (or ``else 0.0``) at this line is the defect
    ``args/perfect_score_gate.yaml`` is ratcheted to zero for: a survey that
    measured nothing must not draw a full green bar, nor a red one.
    """
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, 2)


TOKEN_BASIS = "chars_div_4"


def approx_tokens(text: str) -> int:
    """``len(text) // 4``. An APPROXIMATION -- no tokenizer is consulted.

    Every field carrying a value from here is labelled ``chars_div_4`` so a
    reader cannot mistake it for a billed token count.
    """
    return len(text) // 4


# ---------------------------------------------------------------------------
# 1. One-shot rate  /  2. re-reads
# ---------------------------------------------------------------------------


def calls_by_session(paths: Sequence[Path]) -> Dict[str, List[Tuple[str, dict]]]:
    """Ordered ``(tool_name, tool_input)`` per session."""
    out: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)
    for call in iter_tool_calls(paths):
        out[call.session].append((call.tool_name, call.tool_input))
    return dict(out)


def _target_path(tool_input: dict) -> str:
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return str(raw)


def one_shot(session_calls: Sequence[Tuple[str, dict]]) -> Dict[str, Any]:
    """Edits, retries and the one-shot rate for ONE session's ordered calls.

    A RETRY is the same file Edited again after a Bash call in between.
    ``Edit A, Bash, Edit A`` is one retry; ``Edit A, Bash, Edit B`` is none;
    ``Edit A, Edit A`` is none, because nothing ran between them to send the
    second edit back.
    """
    edits = 0
    retries = 0
    retried_files: Counter = Counter()
    #: Files edited so far -> whether something has RUN since that edit.
    ran_since: Dict[str, bool] = {}
    for name, tool_input in session_calls:
        if name in RUN_TOOLS:
            for key in ran_since:
                ran_since[key] = True
            continue
        if name not in EDIT_TOOLS:
            continue
        path = _target_path(tool_input)
        if not path:
            continue
        edits += 1
        if ran_since.get(path):
            retries += 1
            retried_files[path] += 1
        ran_since[path] = False
    return {
        "edit_calls": edits,
        "retries": retries,
        "one_shot_edits": edits - retries,
        "one_shot_rate_pct": _rate(edits - retries, edits),
        "retried_files": retried_files,
    }


def rereads(
    session_calls: Sequence[Tuple[str, dict]],
    threshold: int = DEFAULT_REREAD_THRESHOLD,
) -> Dict[str, int]:
    """Paths this session Read at least *threshold* times, path -> count."""
    counts: Counter = Counter()
    for name, tool_input in session_calls:
        if name not in READ_TOOLS:
            continue
        path = _target_path(tool_input)
        if path:
            counts[path] += 1
    return {p: n for p, n in counts.items() if n >= threshold}


# ---------------------------------------------------------------------------
# 3. Ghost definitions
# ---------------------------------------------------------------------------


def declared_definitions(repo_root: Path) -> Dict[str, Any]:
    """Every command / skill / agent this checkout declares, by kind.

    A root that does not exist is ``absent`` and contributes NO names -- never
    "zero ghosts", which would read as a clean bill of health for a tree that
    was never looked at.
    """
    kinds: Dict[str, Any] = {}
    for kind, rel, pattern, name_from in DEFINITION_ROOTS:
        root = repo_root / rel
        if not root.is_dir():
            kinds[kind] = {"root": rel, "state": "absent", "declared": []}
            continue
        names = set()
        for path in sorted(root.glob(pattern)):
            name = path.parent.name if name_from == "parent" else path.stem
            if name.lower() in {"readme", "manifest"}:
                continue
            names.add(name)
        kinds[kind] = {
            "root": rel,
            "state": "declared" if names else "empty",
            "declared": sorted(names),
        }
    return kinds


def _name_from_tool_use(block: dict) -> str:
    tool = block.get("name") or ""
    tool_input = block.get("input")
    if not isinstance(tool_input, dict):
        return ""
    if tool == "Skill":
        return str(tool_input.get("skill") or "")
    if tool == "SlashCommand":
        raw = str(tool_input.get("command") or "").lstrip("/").strip()
        return raw.split()[0] if raw else ""
    if tool in {"Agent", "Task"}:
        return str(tool_input.get("subagent_type") or "")
    return ""


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def invoked_names(paths: Sequence[Path]) -> Dict[str, Any]:
    """Names a transcript in the window shows being invoked.

    THREE detectors, and their limits are reported rather than implied:

    * ``Skill`` / ``SlashCommand`` / ``Agent`` ``tool_use`` blocks -- the
      structured, authoritative signal.
    * ``<command-name>/x</command-name>`` in a user turn -- Claude Code's own
      marker for a slash command the user typed.
    * a ``/x`` token at the START of a line in a user turn. Anchored on
      purpose: an unanchored ``/\\w+`` matches every path in every prompt.

    A definition reached through a channel that writes no transcript here is
    invisible to all three, which is why the verdict downstream is
    ``not_invoked_in_window`` and never ``dead``.
    """
    names: Counter = Counter()
    by_detector: Counter = Counter()
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    # A transcript is append-only JSONL written live, so a
                    # truncated final line is normal rather than corruption.
                    continue
                if not isinstance(record, dict):
                    continue
                content = (record.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue
                        got = _name_from_tool_use(block)
                        if got:
                            names[got] += 1
                            by_detector["tool_use"] += 1
                if record.get("type") != "user":
                    continue
                text = _user_text(content)
                if not text:
                    continue
                for match in _COMMAND_TAG_RE.findall(text):
                    names[match.lstrip("/")] += 1
                    by_detector["command_tag"] += 1
                for match in _SLASH_LINE_RE.findall(text):
                    names[match] += 1
                    by_detector["slash_line"] += 1
    return {"names": names, "by_detector": dict(by_detector)}


GHOST_CAVEAT = (
    "not_invoked_in_window is NOT dead: a definition may be reached from "
    "another platform (Cursor, Copilot, tools/skills/invoke.py, a cron job) "
    "that writes no Claude Code transcript, and this survey cannot see any "
    "of them."
)


def ghost_definitions(
    repo_root: Path,
    invoked: Counter,
    max_listed: int = DEFAULT_MAX_LISTED,
    sessions_observed: bool = True,
) -> Dict[str, Any]:
    """Declared definitions no transcript in the window invoked.

    *sessions_observed* False means the window held NO session. Every count is
    then None and the state is ``unmeasurable_no_sessions``: with nothing
    observed, nothing COULD have been invoked, so "93 of 93 definitions went
    uninvoked" is a 100.0% drawn over a denominator of zero sessions -- the
    defect ``args/perfect_score_gate.yaml`` is ratcheted to zero for. The
    declarations are still LISTED, because what the checkout declares is a
    fact about the checkout and does not depend on anyone having run a
    session; only the verdict is withheld.
    """
    declared = declared_definitions(repo_root)
    seen = {str(k).lower() for k in invoked}
    out: Dict[str, Any] = {"kinds": {}, "caveat": GHOST_CAVEAT}
    total_declared = 0
    total_ghost = 0
    measurable = False
    for kind, entry in declared.items():
        if not sessions_observed and entry["state"] != "absent":
            out["kinds"][kind] = {
                "root": entry["root"],
                "state": "unmeasurable_no_sessions",
                "declared": len(entry["declared"]),
                "not_invoked_in_window": None,
                "not_invoked_pct": None,
                "names": [], "names_truncated": False,
            }
            continue
        if entry["state"] == "absent":
            out["kinds"][kind] = {
                "root": entry["root"], "state": "absent",
                "declared": None, "not_invoked_in_window": None,
                "not_invoked_pct": None, "names": [], "names_truncated": False,
            }
            continue
        measurable = True
        names = entry["declared"]
        ghosts = sorted(n for n in names if n.lower() not in seen)
        total_declared += len(names)
        total_ghost += len(ghosts)
        out["kinds"][kind] = {
            "root": entry["root"],
            "state": entry["state"],
            "declared": len(names),
            "not_invoked_in_window": len(ghosts),
            "not_invoked_pct": _rate(len(ghosts), len(names)),
            "names": ghosts[:max_listed],
            "names_truncated": len(ghosts) > max_listed,
        }
    out["totals"] = {
        "declared": total_declared if measurable else None,
        "not_invoked_in_window": total_ghost if measurable else None,
        "not_invoked_pct": _rate(total_ghost, total_declared) if measurable else None,
    }
    out["sessions_observed"] = sessions_observed
    return out


# ---------------------------------------------------------------------------
# 4. Config bloat
# ---------------------------------------------------------------------------


def _expand_imports(repo_root: Path, text: str) -> Tuple[List[Dict[str, Any]], str]:
    """Resolve ``@path`` imports transitively. A missing target is REPORTED."""
    found: List[Dict[str, Any]] = []
    seen: set = set()
    pending = list(_AT_IMPORT_RE.findall(text))
    body: List[str] = []
    while pending:
        rel = pending.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        target = repo_root / rel
        if not target.is_file():
            found.append({"ref": rel, "state": "missing", "bytes": None,
                          "approx_tokens": None})
            continue
        try:
            sub = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            found.append({"ref": rel, "state": "unreadable", "bytes": None,
                          "approx_tokens": None})
            continue
        body.append(sub)
        found.append({
            "ref": rel, "state": "resolved",
            "bytes": len(sub.encode("utf-8")),
            "approx_tokens": approx_tokens(sub),
        })
        pending.extend(_AT_IMPORT_RE.findall(sub))
    return found, "\n".join(body)


def config_bloat(
    repo_root: Path,
    sessions: Optional[int],
    window_days: Optional[float],
) -> Dict[str, Any]:
    """CLAUDE.md bytes, expanded @-imports, and tokens loaded per day."""
    path = repo_root / "CLAUDE.md"
    if not path.is_file():
        return {
            "state": "absent", "path": "CLAUDE.md", "bytes": None,
            "approx_tokens": None, "approx_tokens_with_imports": None,
            "token_basis": TOKEN_BASIS, "imports": [],
            "tokens_loaded_in_window": None, "tokens_loaded_per_day": None,
            "sessions": sessions, "window_days": window_days,
            "sessions_basis": "transcript_mtime_in_window",
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    imports, imported_text = _expand_imports(repo_root, text)
    own_tokens = approx_tokens(text)
    total_tokens = own_tokens + approx_tokens(imported_text)
    in_window = total_tokens * sessions if sessions else None
    per_day = None
    if in_window is not None and window_days:
        per_day = round(in_window / float(window_days), 1)
    return {
        "state": "measured",
        "path": "CLAUDE.md",
        "bytes": len(text.encode("utf-8")),
        "approx_tokens": own_tokens,
        "approx_tokens_with_imports": total_tokens,
        "token_basis": TOKEN_BASIS,
        "imports": imports,
        "sessions": sessions,
        # mtime is a transcript's LAST write, so this is sessions ACTIVE in
        # the window. Naming the basis is what keeps it from being quoted as
        # sessions STARTED.
        "sessions_basis": "transcript_mtime_in_window",
        "window_days": window_days,
        "tokens_loaded_in_window": in_window,
        "tokens_loaded_per_day": per_day,
    }


# ---------------------------------------------------------------------------
# 5. MCP tools
# ---------------------------------------------------------------------------


def split_mcp_name(tool_name: str) -> Optional[Tuple[str, str]]:
    """``mcp__<server>__<tool>`` -> ``(server, tool)``, else None.

    The separator is the DOUBLE underscore and the tool is everything after
    the second one: a server name may contain hyphens (``icdev-unified``) and
    a tool name may itself contain ``__``.
    """
    if not tool_name.startswith("mcp__"):
        return None
    rest = tool_name[len("mcp__"):]
    if "__" not in rest:
        return None
    server, tool = rest.split("__", 1)
    if not server or not tool:
        return None
    return server, tool


def mcp_from_transcripts(
    by_session: Dict[str, List[Tuple[str, dict]]],
) -> Dict[str, Any]:
    """Per ``mcp__<server>__<tool>`` call counts over the window."""
    per_tool: Counter = Counter()
    per_server: Counter = Counter()
    for calls in by_session.values():
        for name, _ in calls:
            split = split_mcp_name(name)
            if split is None:
                continue
            server, tool = split
            per_tool[(server, tool)] += 1
            per_server[server] += 1
    return {"per_tool": per_tool, "per_server": per_server}


def _registry_names() -> Optional[List[str]]:
    try:
        from icdev.tools.mcp.tool_registry import TOOL_REGISTRY

        return sorted(TOOL_REGISTRY.keys())
    except Exception:  # noqa: BLE001 -- an unreadable registry is unmeasurable,
        # never an empty declared set, which would report every tool invoked.
        return None


def icdev_mcp_servers(repo_root: Path) -> Optional[List[str]]:
    """Servers in ``.mcp.json`` that SERVE ``TOOL_REGISTRY``, by their command.

    A BARE tool name is not enough to credit a registry entry, and this was
    measured rather than reasoned: ``TOOL_REGISTRY`` declares ``browser_click``,
    ``browser_navigate`` and ``browser_type``, and the Playwright MCP server
    declares tools of exactly those names. Matching on the bare name credited
    Playwright's 50 clicks and 176 navigations to ICDEV's registry and reported
    6 registry tools reached from Claude Code where 3 were -- an overstatement
    of the one number this section exists to produce.

    Derived from the checkout's OWN config (a server whose command line names
    ``tools/mcp``), never a hardcoded server name, so a deployment that renames
    or adds an ICDEV server needs no edit here. Returns None when the config
    cannot be read; the caller then falls back to bare matching and SAYS SO
    (``server_attribution``) rather than quietly reporting the inflated count.
    """
    path = repo_root / ".mcp.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    out = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        blob = " ".join(
            [str(entry.get("command") or "")]
            + [str(a) for a in (entry.get("args") or []) if isinstance(a, (str, int))]
        ).replace("\\", "/").lower()
        if "tools/mcp" in blob:
            out.append(str(name))
    return sorted(out)


def studio_dispatch_only(window_days: Optional[float]) -> Dict[str, Any]:
    """The Studio-side figure, printed BESIDE the transcript one.

    NOT a correction of it and never merged with it: this counts dispatches
    through the Studio tool gate, which no Claude Code session passes through.
    """
    note = (
        "studio_mcp_dispatch_audit has exactly two writers, both "
        "Studio-internal (tools/studio/executors/mcp_executor.py and "
        "agent_tool_gate.py). Nothing under tools/mcp/ writes a dispatch row, "
        "so no MCP call made from a Claude Code session is visible here. "
        + _STUDIO_NOTE
    )
    try:
        from icdev.tools.awareness.capability_consumption import collect

        report = collect(
            window_days=int(window_days) if window_days else None,
            only=["mcp_dispatch_tool"],
        )
    except Exception as exc:  # noqa: BLE001 -- an unreachable board is
        # unmeasurable, never a zero that would read as "nothing dispatched".
        return {"state": "unmeasurable", "reason": str(exc), "note": note}
    for entry in report.get("classes") or []:
        if entry.get("capability_class") != "mcp_dispatch_tool":
            continue
        if not entry.get("telemetry_available"):
            return {
                "state": "unmeasurable",
                "reason": entry.get("unmeasured_reason") or "telemetry unavailable",
                "note": note,
            }
        return {
            "state": "measured",
            "declared": entry.get("declared"),
            "consumed": entry.get("consumed"),
            "inert": entry.get("inert"),
            "events": entry.get("events"),
            "note": note,
        }
    return {"state": "unmeasurable", "reason": "class not returned", "note": note}


def mcp_usage(
    by_session: Dict[str, List[Tuple[str, dict]]],
    window_days: Optional[float],
    studio: bool = True,
    max_listed: int = DEFAULT_MAX_LISTED,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    observed = mcp_from_transcripts(by_session)
    per_tool = observed["per_tool"]
    registry = _registry_names()
    serving = icdev_mcp_servers(repo_root or BASE_DIR)
    if serving is None:
        # Bare matching credits another server's identically-named tool. It is
        # the fallback, and the report names it so nobody quotes the inflated
        # figure as the measured one.
        called_bare = {tool for (_server, tool) in per_tool}
        attribution = "bare_name_fallback"
    else:
        called_bare = {t for (s, t) in per_tool if s in set(serving)}
        attribution = "server_qualified"
    transcript: Dict[str, Any] = {
        "calls": sum(per_tool.values()),
        "distinct_tools": len(per_tool),
        "by_server": dict(observed["per_server"]),
        "server_attribution": attribution,
        "serving_servers": serving,
        "top_tools": [
            {"server": s, "tool": t, "calls": n}
            for (s, t), n in per_tool.most_common(max_listed)
        ],
    }
    if registry is None:
        transcript.update({
            "registry_state": "unreadable",
            "registry_declared": None,
            "registry_invoked_from_claude_code": None,
            "not_invoked_from_claude_code_in_window": None,
            "not_invoked_pct": None,
            "not_invoked_names": [],
            "not_invoked_names_truncated": False,
        })
    else:
        invoked = [n for n in registry if n in called_bare]
        ghosts = [n for n in registry if n not in called_bare]
        transcript.update({
            "registry_state": "read",
            "registry_declared": len(registry),
            "registry_invoked_from_claude_code": len(invoked),
            "not_invoked_from_claude_code_in_window": len(ghosts),
            "not_invoked_pct": _rate(len(ghosts), len(registry)),
            "not_invoked_names": ghosts[:max_listed],
            "not_invoked_names_truncated": len(ghosts) > max_listed,
        })
    out: Dict[str, Any] = {"transcript": transcript}
    out["studio_dispatch_only"] = (
        studio_dispatch_only(window_days) if studio
        else {"state": "not_consulted", "reason": "--no-studio",
              "note": _STUDIO_NOTE}
    )
    return out


# ---------------------------------------------------------------------------
# The survey
# ---------------------------------------------------------------------------


def survey(
    root: Optional[Path] = None,
    since_days: Optional[float] = 30.0,
    project: str = "",
    repo_root: Optional[Path] = None,
    reread_threshold: int = DEFAULT_REREAD_THRESHOLD,
    max_listed: int = DEFAULT_MAX_LISTED,
    studio: bool = True,
    transcripts: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    """Every measure over one window. Never raises on a bad transcript line."""
    root = root or transcript_root()
    repo_root = repo_root or BASE_DIR
    paths = (
        list(transcripts) if transcripts is not None
        else iter_transcripts(root=root, since_days=since_days,
                              project_filter=project)
    )
    generated = datetime.now(timezone.utc).isoformat()
    by_session = calls_by_session(paths)

    per_session: List[Dict[str, Any]] = []
    total_edits = 0
    total_retries = 0
    retried_files: Counter = Counter()
    offenders: List[Dict[str, Any]] = []
    sessions_with_edits = 0
    read_tool_calls = 0
    run_tool_calls = 0
    for session, calls in by_session.items():
        for name, _ in calls:
            if name in READ_TOOLS:
                read_tool_calls += 1
            elif name in RUN_TOOLS:
                run_tool_calls += 1
        shot = one_shot(calls)
        total_edits += shot["edit_calls"]
        total_retries += shot["retries"]
        retried_files.update(shot["retried_files"])
        if shot["edit_calls"]:
            sessions_with_edits += 1
        for path, count in rereads(calls, reread_threshold).items():
            offenders.append({"session": session, "path": path, "reads": count})
        per_session.append({
            "session": session,
            "tool_calls": len(calls),
            "edit_calls": shot["edit_calls"],
            "retries": shot["retries"],
            "one_shot_rate_pct": shot["one_shot_rate_pct"],
        })
    offenders.sort(key=lambda r: (-r["reads"], r["path"]))
    per_session.sort(key=lambda r: (-r["tool_calls"], r["session"]))

    invoked = invoked_names(paths)
    sessions = len(paths)
    measurable = bool(paths)

    report: Dict[str, Any] = {
        "generated_at": generated,
        "state": "measured" if measurable else "unmeasurable",
        "window": {
            "since_days": since_days,
            "project_filter": project,
            "transcript_root": str(root),
            "sessions": sessions,
            "sessions_basis": "transcript_mtime_in_window",
        },
        "one_shot": {
            "edit_calls": total_edits if measurable else None,
            "retries": total_retries if measurable else None,
            "one_shot_edits": (total_edits - total_retries) if measurable else None,
            "one_shot_rate_pct": _rate(total_edits - total_retries, total_edits),
            "sessions_with_edits": sessions_with_edits if measurable else None,
            "rule": (
                "A RETRY is the same file Edited again after a Bash call in "
                "between. Edit A, Bash, Edit A is one retry; Edit A, Bash, "
                "Edit B is none; Edit A, Edit A is none."
            ),
            "top_retried_files": [
                {"path": p, "retries": n}
                for p, n in retried_files.most_common(max_listed)
            ],
        },
        "rereads": {
            "threshold": reread_threshold,
            "offenders": len(offenders) if measurable else None,
            "top_offenders": offenders[:max_listed],
            "truncated": len(offenders) > max_listed,
            # A LOWER BOUND, and the two counts beside it say by how much.
            # This deployment's own harness instruction steers sessions to
            # `cat`/`sed -n` through Bash instead of the Read tool, and a file
            # read that way is a shell command this survey cannot attribute to
            # a path. Measured 2026-09-12 over 7 days: 17,625 Bash calls
            # against 239 Read calls.
            "read_tool_calls": read_tool_calls if measurable else None,
            "run_tool_calls": run_tool_calls if measurable else None,
            "basis": "read_tool_only",
            "coverage_note": (
                "Only the Read tool is counted. Sessions here are instructed "
                "to read files with cat/head/sed through Bash, and a file "
                "read that way cannot be attributed to a path, so this is a "
                "LOWER BOUND -- compare read_tool_calls against "
                "run_tool_calls to see by how much."
            ),
        },
        "definitions": ghost_definitions(
            repo_root, invoked["names"], max_listed,
            sessions_observed=measurable),
        "config_bloat": config_bloat(
            repo_root, sessions if measurable else None, since_days),
        "mcp": mcp_usage(by_session, since_days, studio, max_listed,
                         repo_root=repo_root),
        "sessions_detail": per_session[:max_listed],
    }
    report["definitions"]["detectors"] = invoked["by_detector"]
    if not measurable:
        report["unmeasurable_reason"] = (
            f"no transcript under {root} matched the window "
            f"(since_days={since_days}, project={project!r})"
        )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt(value: Any, suffix: str = "") -> str:
    return "?" if value is None else f"{value}{suffix}"


def to_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    win = report["window"]
    lines.append(
        f"Waste survey  state={report['state']}  "
        f"sessions={win['sessions']} (basis: {win['sessions_basis']})  "
        f"window={_fmt(win['since_days'])}d"
    )
    if report["state"] != "measured":
        lines.append(f"  UNMEASURABLE: {report.get('unmeasurable_reason', '')}")
        lines.append("  Unmeasurable is its own verdict. It is not a clean survey.")
        return "\n".join(lines)

    one = report["one_shot"]
    lines.append("")
    lines.append(
        f"ONE-SHOT   edits={_fmt(one['edit_calls'])} "
        f"retries={_fmt(one['retries'])} "
        f"rate={_fmt(one['one_shot_rate_pct'], '%')}"
    )
    lines.append(f"           rule: {one['rule']}")
    for row in one["top_retried_files"][:10]:
        lines.append(f"           {row['retries']:>3}x  {row['path']}")

    rr = report["rereads"]
    lines.append("")
    lines.append(
        f"RE-READS   offenders={_fmt(rr['offenders'])} "
        f"(same path Read >= {rr['threshold']}x in one session)"
    )
    lines.append(
        f"           LOWER BOUND: {_fmt(rr['read_tool_calls'])} Read-tool "
        f"calls against {_fmt(rr['run_tool_calls'])} shell calls. "
        f"{rr['coverage_note']}"
    )
    for row in rr["top_offenders"][:10]:
        lines.append(
            f"           {row['reads']:>3}x  {row['path']}  [{row['session'][:8]}]"
        )

    defs = report["definitions"]
    lines.append("")
    lines.append("DEFINITIONS not invoked in this window "
                 "(not_invoked_in_window, NEVER dead)")
    for kind, entry in defs["kinds"].items():
        if entry["state"] == "absent":
            lines.append(
                f"           {kind:<8} {entry['root']}: ABSENT "
                f"(no tree to measure -- not zero ghosts)"
            )
            continue
        if entry["state"] == "unmeasurable_no_sessions":
            lines.append(
                f"           {kind:<8} {entry['root']}: {entry['declared']} "
                f"declared, UNMEASURABLE (no session in the window, so "
                f"nothing could have been invoked)"
            )
            continue
        lines.append(
            f"           {kind:<8} {entry['not_invoked_in_window']}"
            f"/{entry['declared']} ({_fmt(entry['not_invoked_pct'], '%')}) "
            f"{entry['root']}"
        )
        if entry["names"]:
            lines.append(
                "                    " + ", ".join(entry["names"][:12])
                + (" ..." if entry["names_truncated"] else "")
            )
    lines.append(f"           caveat: {defs['caveat']}")

    cfg = report["config_bloat"]
    lines.append("")
    if cfg["state"] == "absent":
        lines.append("CONFIG     CLAUDE.md ABSENT")
    else:
        lines.append(
            f"CONFIG     CLAUDE.md {cfg['bytes']:,} bytes  "
            f"~{cfg['approx_tokens']:,} tokens ({cfg['token_basis']}, "
            f"an approximation)"
        )
        lines.append(
            f"           imports={len(cfg['imports'])}  with imports "
            f"~{cfg['approx_tokens_with_imports']:,} tokens"
        )
        lines.append(
            f"           loaded {_fmt(cfg['tokens_loaded_in_window'])} tokens "
            f"over {cfg['sessions']} sessions = "
            f"{_fmt(cfg['tokens_loaded_per_day'])} tokens/day"
        )

    mcp = report["mcp"]
    tr = mcp["transcript"]
    lines.append("")
    lines.append(
        f"MCP (transcripts)  calls={tr['calls']} "
        f"distinct={tr['distinct_tools']}  by_server={tr['by_server']}"
    )
    lines.append(
        f"                   registry declared={_fmt(tr['registry_declared'])} "
        f"invoked_from_claude_code="
        f"{_fmt(tr['registry_invoked_from_claude_code'])} "
        f"not_invoked="
        f"{_fmt(tr['not_invoked_from_claude_code_in_window'])}"
    )
    for row in tr["top_tools"][:10]:
        lines.append(
            f"                   {row['calls']:>4}x  mcp__{row['server']}__{row['tool']}"
        )
    studio = mcp["studio_dispatch_only"]
    if studio["state"] == "measured":
        lines.append(
            f"MCP (studio only)  declared={studio['declared']} "
            f"consumed={studio['consumed']} inert={studio['inert']} "
            f"events={studio['events']}"
        )
    else:
        lines.append(
            f"MCP (studio only)  {studio['state'].upper()}: "
            f"{studio.get('reason', '')}"
        )
    lines.append(f"                   {studio['note']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Behavioural waste over the Claude Code transcripts: "
                    "one-shot rate, re-read files, definitions nobody "
                    "invoked, CLAUDE.md bloat and MCP tool usage. "
                    "Report only -- deliberately no --gate.")
    ap.add_argument("--since-days", type=float, default=30.0,
                    help="transcript mtime window (default 30)")
    ap.add_argument("--project", default="",
                    help="substring of the encoded project directory name")
    ap.add_argument("--root", type=Path, default=None,
                    help="override the transcript root (default: ~/.claude/projects)")
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="checkout whose definitions and CLAUDE.md are measured")
    ap.add_argument("--reread-threshold", type=int,
                    default=DEFAULT_REREAD_THRESHOLD,
                    help=f"reads of one path in one session (default "
                         f"{DEFAULT_REREAD_THRESHOLD})")
    ap.add_argument("--max-listed", type=int, default=DEFAULT_MAX_LISTED)
    ap.add_argument("--no-studio", dest="studio", action="store_false",
                    default=True,
                    help="skip the Studio-side MCP figure (no database read)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = args.root or transcript_root()
    if not root.is_dir():
        payload = {"state": "unmeasurable",
                   "error": f"transcript root {root} does not exist"}
        print(json.dumps(payload, indent=2) if args.json
              else f"ERROR: transcript root {root} does not exist")
        return 2
    report = survey(
        root=root,
        since_days=args.since_days,
        project=args.project,
        repo_root=args.repo_root,
        reread_threshold=args.reread_threshold,
        max_listed=args.max_listed,
        studio=args.studio,
    )
    print(json.dumps(report, indent=2, default=str) if args.json
          else to_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
