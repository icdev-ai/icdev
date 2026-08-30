#!/usr/bin/env python3
# CUI // SP-CTI
"""How often would each PreToolUse check have fired on real work? (exa-bench-05)

``.claude/settings.json`` wired the PreToolUse hook as ``python ... || true``, so
its exit-2 refusals never reached Claude Code. Removing that wrapper converts
every advisory check into a hard block for every session on the host at once,
and several of them (``direct_sqlite_usage``, ``file_access_tiers``,
``write_outside_worktree``) had never been under load as blocks.

This module is the measurement that has to come first: replay the tool calls
real sessions actually made through the real checks, and count what each one
would have refused.

Where the corpus comes from — and why not ``hook_events``
--------------------------------------------------------
``hook_events`` is the obvious source and it is the wrong one.
``.claude/hooks/post_tool_use.py`` persists ``{"tool_input_keys": [...],
"output_length": N, "output_summary": ...}`` — the KEY NAMES of the tool input,
never the values. The Bash ``command``, the Edit ``file_path``, the WebFetch
``url`` are not in the table, and every check here is a predicate over exactly
those operands. A replay driven from ``hook_events`` would report zero fires for
every check, which reads as "these checks are safe to enable" when it means
"this table cannot answer the question".

So the corpus is the Claude Code session transcripts under
``~/.claude/projects/**/*.jsonl``, which record each ``tool_use`` block with its
full input. That is the same stream the hook saw, replayed offline.
:func:`hook_events_operand_availability` reports the table's real state rather
than letting it contribute a misleading zero.

Replayable vs trigger-only
--------------------------
Most checks are pure predicates over ``(tool_name, tool_input)`` and are
replayed for real: their ``fired`` counts are measurements. Two
(``network_egress``, ``write_outside_worktree``) are replayed with their
enforcement switch forced on, so that measuring a check never depends on which
way that check's own default happens to be set — ``network_egress`` ships
monitor-only and at its default refuses nothing, which would read as a 0% and
therefore as "safe to arm".

Three cannot be replayed at all, and are reported as ``trigger_only`` with the
condition stated:

``branch_deletion``
    Fires only when the targeted remote branch still holds unmerged commits — a
    live ``git cherry`` against refs that, for a months-old transcript, no longer
    exist. The trigger (does this command delete a remote branch at all) is
    replayed; the verdict is not. ``--live-git`` opts into the real comparison.
``review_loop_precommit``
    Executing it runs ruff over the staged tree and ``git add``s what it
    rewrites. A survey must not mutate the index, so this is never executed. It
    also cannot block at all unless ``ICDEV_REVIEW_LOOP_BLOCK=1``.
``agent_rules``
    Monitor-only by construction: a rule refuses only when it sets
    ``enforce: true`` AND lives in ``args/agent_rules_enforce/``. Evaluating it
    would also write an ``agent_findings`` row per matched call. The directory's
    real contents are reported instead.

Counting a trigger as a fire would overstate the risk; counting it as zero would
hide it. Both are named for what they are.

Usage
-----
    python tools/hooks/fire_rate_survey.py --json
    python tools/hooks/fire_rate_survey.py --markdown --since-days 30
    python tools/hooks/fire_rate_survey.py --check env_file_access --samples 25
    python tools/hooks/fire_rate_survey.py --gate --max-fire-rate 0.0

Samples are truncated and are session operands, i.e. potentially CUI. ``--json``
omits them unless ``--samples N`` is passed explicitly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

#: Truncation applied to every sample before it leaves this module.
SAMPLE_CHARS = 160


# ── The checks, loaded the way the hook loads them ────────────────────────
#
# By path, from `.claude/hooks/pre_tool_use.py`, rather than importing
# `tools.hooks.shared_checks` directly. The hook owns APPEND_ONLY_TABLES and
# loads shared_checks by path itself; measuring through the entry point means
# the survey cannot drift from what Claude Code actually runs, and sidesteps the
# question of whether the `icdev/` mirror agrees with the root copy.

def _load_hook_module(repo_root: Path):
    path = repo_root / ".claude" / "hooks" / "pre_tool_use.py"
    if not path.exists():
        raise FileNotFoundError(f"PreToolUse hook not found at {path}")
    spec = importlib.util.spec_from_file_location("icdev_pre_tool_use_survey", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class Check:
    """One PreToolUse check, and whether a survey may execute it."""

    name: str
    #: Pure predicate returning a block reason (or None). Absent => trigger_only.
    predicate: Optional[Callable[[str, dict], Optional[str]]] = None
    #: Pure predicate for "the call reached this check's expensive part".
    trigger: Optional[Callable[[str, dict], Optional[str]]] = None
    #: True when the verdict depends on WHERE the call ran, so the replay must
    #: anchor on the transcript's own ``cwd`` instead of this checkout. Without
    #: it `write_outside_worktree` scores every call made from another worktree
    #: as an escape: 3,029 fires (3.1%) against 2,526 (2.61%) anchored correctly.
    needs_cwd: bool = False
    blocks_when: str = "the predicate matches"
    note: str = ""

    @property
    def mode(self) -> str:
        return "replayed" if self.predicate is not None else "trigger_only"


def build_checks(repo_root: Path, live_git: bool = False) -> List[Check]:
    """Every check ``pre_tool_use.main()`` runs, in the order it runs them.

    ``tests/hooks/test_fire_rate_survey.py`` pins this against the hook's own
    ``HOOK_CHECKS`` declaration: a check the hook can refuse with but that the
    survey never replays is a check enabled on an unmeasured fire rate, which is
    the thing this tool exists to prevent.
    """
    hook = _load_hook_module(repo_root)
    sc = hook.shared_checks

    def _env(tool: str, ti: dict) -> Optional[str]:
        return sc.ENV_FILE_BLOCK_REASON if hook.is_env_file_access(tool, ti) else None

    def _rm(tool: str, ti: dict) -> Optional[str]:
        if tool != "Bash":
            return None
        if hook.is_dangerous_rm_command(ti.get("command", "") or ""):
            return sc.DANGEROUS_RM_BLOCK_REASON
        return None

    def _append_only(tool: str, ti: dict) -> Optional[str]:
        if hook.is_append_only_table_modification(tool, ti):
            return sc.APPEND_ONLY_BLOCK_REASON
        return None

    def _sqlite(tool: str, ti: dict) -> Optional[str]:
        if hook.is_direct_sqlite_usage(tool, ti):
            return sc.DIRECT_SQLITE_BLOCK_REASON
        return None

    def _tiers(tool: str, ti: dict) -> Optional[str]:
        return hook.check_file_access_tiers(tool, ti) or None

    def _worktree(tool: str, ti: dict) -> Optional[str]:
        return hook.check_worktree_path(tool, ti) or None

    def _write_boundary(tool: str, ti: dict, anchor: Path) -> Optional[str]:
        # Anchor on the worktree the call was MADE from, not on this checkout:
        # the check asks "is this write inside the SESSION's worktree", and
        # every corpus session had a different one.
        #
        # Enforcing mode is forced rather than assumed, so that measuring this
        # check never depends on which way WRITE_BOUNDARY_DEFAULT_MODE happens
        # to be set — a survey that reports 0% because the thing it is measuring
        # is switched off is the `hook_events` failure mode one layer down.
        prior = os.environ.get(sc.WRITE_BOUNDARY_GUARD_ENV)
        os.environ[sc.WRITE_BOUNDARY_GUARD_ENV] = "enforce"
        try:
            return sc.check_write_outside_worktree(tool, ti, repo_root=anchor) or None
        finally:
            if prior is None:
                os.environ.pop(sc.WRITE_BOUNDARY_GUARD_ENV, None)
            else:
                os.environ[sc.WRITE_BOUNDARY_GUARD_ENV] = prior

    def _branch_trigger(tool: str, ti: dict) -> Optional[str]:
        if tool != "Bash":
            return None
        targets = sc.remote_branch_delete_targets(ti.get("command", "") or "")
        return f"deletes remote branch(es): {', '.join(targets)}" if targets else None

    def _branch_live(tool: str, ti: dict) -> Optional[str]:
        return sc.check_branch_deletion(tool, ti, repo_root=repo_root) or None

    def _gh_merge_trigger(tool: str, ti: dict) -> Optional[str]:
        if tool != "Bash":
            return None
        found = sc.gh_pr_merge_invocations(ti.get("command", "") or "")
        if not found:
            return None
        named = ", ".join(str(f.get("selector") or "<current branch>") for f in found)
        return f"invokes `gh pr merge` on: {named}"

    def _gh_merge_live(tool: str, ti: dict) -> Optional[str]:
        return sc.check_gh_pr_merge_bypass(tool, ti, repo_root=repo_root) or None

    def _commit_trigger(tool: str, ti: dict) -> Optional[str]:
        if tool != "Bash":
            return None
        return "git commit" if "git commit" in (ti.get("command", "") or "") else None

    def _git_danger(tool: str, ti: dict) -> Optional[str]:
        return hook.check_git_danger(tool, ti) or None

    def _egress(tool: str, ti: dict) -> Optional[str]:
        # Forced into its ENFORCING mode for the replay. Left at the shipped
        # default it returns None for everything and the survey would report a
        # 0% fire rate for a check that has not been measured at all — the
        # `hook_events` failure mode this whole tool exists to avoid.
        prior = os.environ.get("ICDEV_EGRESS_GUARD_ENFORCE")
        os.environ["ICDEV_EGRESS_GUARD_ENFORCE"] = "1"
        try:
            return sc.check_network_egress(tool, ti, repo_root=repo_root) or None
        finally:
            if prior is None:
                os.environ.pop("ICDEV_EGRESS_GUARD_ENFORCE", None)
            else:
                os.environ["ICDEV_EGRESS_GUARD_ENFORCE"] = prior

    return [
        Check("env_file_access", predicate=_env,
              blocks_when="a Read/Edit/Write path contains '.env' (except '.env.sample'), "
                          "or a Bash command mentions '.env'"),
        Check("dangerous_rm", predicate=_rm,
              blocks_when="a Bash command is a recursive/forced rm"),
        Check("git_danger", predicate=_git_danger,
              blocks_when="a Bash command matches GIT_DANGER_PATTERNS "
                          "(reset --hard, clean -fdx, push --force, branch -D, rebase -i)"),
        Check("append_only_write", predicate=_append_only,
              blocks_when="a Bash command UPDATE/DELETE/DROP/TRUNCATEs a protected table"),
        Check("direct_sqlite_usage", predicate=_sqlite,
              blocks_when="an Edit/Write under tools/ introduces sqlite3.connect(), or a "
                          "Bash one-liner opens icdev.db directly"),
        Check("file_access_tiers", predicate=_tiers,
              blocks_when="the path is in a D-ORCH-8 zero_access/read_only/no_delete tier"),
        Check("write_outside_worktree", predicate=_write_boundary, needs_cwd=True,
              blocks_when="a write resolves outside the session worktree, the main "
                          "checkout and the sanctioned scratch roots",
              note="anchored on the worktree containing each transcript's cwd, not "
                   "on this checkout; where that worktree no longer exists the "
                   "verdict is not reproducible and the call counts as a fire, so "
                   "this is an UPPER bound"),
        Check("branch_deletion",
              predicate=_branch_live if live_git else None,
              trigger=_branch_trigger,
              blocks_when="the targeted remote branch holds commits not on origin/main",
              note="live verdict needs refs that may no longer exist; --live-git to try"),
        Check("worktree_path", predicate=_worktree,
              blocks_when="a `git worktree add` targets a path outside the sanctioned roots"),
        Check("gh_pr_merge_bypass",
              predicate=_gh_merge_live if live_git else None,
              trigger=_gh_merge_trigger,
              blocks_when="the `gh pr merge` targets a PR whose head branch is "
                          "kanban/<task-id>",
              note="the trigger is the UPPER BOUND on this check's fire rate — it "
                   "can refuse nothing that is not a `gh pr merge`, and only the "
                   "kanban-LINKED subset of those. The live verdict resolves a PR "
                   "number/URL through the forge and reads HEAD of the merge "
                   "directory, neither of which a months-old transcript can "
                   "reproduce; --live-git to try"),
        Check("network_egress", predicate=_egress,
              blocks_when="a command reaches a host that is neither local nor in "
                          "agent_egress.allowed_hosts",
              note="replayed with ICDEV_EGRESS_GUARD_ENFORCE=1; ships monitor-only, "
                   "so this is the rate it WOULD refuse at if enforcement is flipped"),
        Check("agent_rules", trigger=None,
              blocks_when="a rule sets enforce:true AND lives in args/agent_rules_enforce/",
              note="not evaluated: evaluating writes an agent_findings row per match"),
        Check("review_loop_precommit", trigger=_commit_trigger,
              blocks_when="ICDEV_REVIEW_LOOP_BLOCK=1 and staged files have unfixable lint",
              note="never executed: running it applies ruff autofixes and re-stages files"),
    ]


# ── The corpus ────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    session: str
    tool_name: str
    tool_input: dict
    #: The directory the call was actually made from, as the transcript records
    #: it. Load-bearing for any check whose verdict depends on WHERE it runs:
    #: `write_outside_worktree` judges a path against the session's worktree, so
    #: replaying another worktree's calls against this checkout scores every one
    #: of them as an escape: 3,029 fires (3.1%) against 2,526 (2.61%) anchored
    #: on the worktree that actually contained each call.
    cwd: str = ""


def transcript_root() -> Path:
    """Directory Claude Code writes session transcripts to."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(configured) if configured else Path.home() / ".claude"
    return base / "projects"


def iter_transcripts(
    root: Optional[Path] = None,
    since_days: Optional[float] = 30.0,
    project_filter: str = "",
) -> List[Path]:
    """Session transcripts to replay, newest first.

    *since_days* filters on file mtime — a transcript's last write is when the
    session last ran. *project_filter* is a case-insensitive substring of the
    encoded project directory name (Claude Code encodes the cwd into it), so
    ``--project ICDev`` keeps this repo's sessions and drops unrelated ones.
    """
    root = root or transcript_root()
    if not root.is_dir():
        return []
    cutoff = (time.time() - since_days * 86400) if since_days else None
    needle = project_filter.lower()
    out = []
    for path in root.glob("*/*.jsonl"):
        if needle and needle not in path.parent.name.lower():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            continue
        out.append((mtime, path))
    return [p for _, p in sorted(out, reverse=True)]


def iter_tool_calls(paths: Sequence[Path]) -> Iterator[ToolCall]:
    """Every ``tool_use`` block in *paths*.

    A transcript is append-only JSONL written live, so a truncated final line is
    normal rather than corruption — malformed lines are skipped, not raised.
    """
    for path in paths:
        session = path.stem
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                content = (record.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                cwd = str(record.get("cwd") or "")
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_input = block.get("input")
                    yield ToolCall(
                        session=session,
                        tool_name=block.get("name") or "",
                        tool_input=tool_input if isinstance(tool_input, dict) else {},
                        cwd=cwd,
                    )


def operand_of(call: ToolCall) -> str:
    """The single string a reader needs to judge a fire. Truncated."""
    ti = call.tool_input
    raw = (
        ti.get("command")
        or ti.get("file_path")
        or ti.get("notebook_path")
        or ti.get("url")
        or ""
    )
    raw = " ".join(str(raw).split())
    return raw[:SAMPLE_CHARS] + ("…" if len(raw) > SAMPLE_CHARS else "")


# ── hook_events, reported honestly ────────────────────────────────────────


def hook_events_operand_availability(limit: int = 5000) -> Dict[str, Any]:
    """Whether ``hook_events`` could drive this replay. It cannot; say why.

    Returns ``telemetry_available: False`` when the table is absent rather than
    an operand count of zero, so an unreachable database never reads as "the
    table is there and holds nothing".
    """
    result: Dict[str, Any] = {
        "telemetry_available": False,
        "usable_as_corpus": False,
        "rows_sampled": 0,
        "rows_carrying_an_operand": 0,
        "reason": "",
    }
    try:
        from tools.db.storage import get_connection
    except Exception as exc:  # noqa: BLE001 — a missing storage layer is a finding
        result["reason"] = f"storage layer unavailable: {exc}"
        return result

    operand_keys = ("command", "file_path", "notebook_path", "url", "tool_input")
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT payload FROM hook_events "
                "WHERE hook_type = %s ORDER BY id DESC LIMIT %s",
                ("post_tool_use", limit),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — absent table => not measurable
        result["reason"] = f"hook_events not queryable: {type(exc).__name__}"
        return result

    result["telemetry_available"] = True
    result["rows_sampled"] = len(rows)
    carrying = 0
    for row in rows:
        payload = row[0] if not isinstance(row, dict) else row.get("payload")
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and any(k in parsed for k in operand_keys):
            carrying += 1
    result["rows_carrying_an_operand"] = carrying
    result["usable_as_corpus"] = carrying > 0
    result["reason"] = (
        f"{carrying}/{len(rows)} sampled post_tool_use rows carry an operand. "
        "post_tool_use.py persists tool_input KEY NAMES only, so a replay driven "
        "from this table would report zero fires for every check regardless of "
        "what the sessions did."
    )
    return result


# ── The survey ────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    mode: str
    blocks_when: str
    note: str
    fired: int = 0
    distinct: int = 0
    sessions: int = 0
    samples: List[Dict[str, str]] = field(default_factory=list)

    def fire_rate(self, total: int) -> float:
        return round(self.fired / total, 6) if total else 0.0


_ANCHOR_CACHE: Dict[str, Optional[Path]] = {}


def _worktree_anchor(cwd: str) -> Optional[Path]:
    """The worktree root containing *cwd*, or None.

    The live hook anchors on ``Path(__file__).parents[2]`` — the root of the
    worktree holding ``.claude/hooks/`` — not on the process's cwd. A session
    that ran from ``<wt>/tools/db/migrations/x`` was still judged against
    ``<wt>``, so a replay that anchors on the raw cwd invents violations the
    hook never saw (115 of them, all writes from a session into its OWN
    worktree). Walk up to the ``.git`` that makes it a checkout.
    """
    if not cwd:
        return None
    if cwd in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[cwd]
    found: Optional[Path] = None
    try:
        here = Path(cwd)
        for candidate in (here, *here.parents):
            if (candidate / ".git").exists():
                found = candidate
                break
    except OSError:
        found = None
    _ANCHOR_CACHE[cwd] = found
    return found


def survey(
    repo_root: Optional[Path] = None,
    since_days: Optional[float] = 30.0,
    project_filter: str = "",
    samples: int = 0,
    live_git: bool = False,
    transcripts: Optional[Sequence[Path]] = None,
    only: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay the corpus through every check and count what each would refuse."""
    root = Path(repo_root) if repo_root else BASE_DIR
    checks = build_checks(root, live_git=live_git)
    if only:
        checks = [c for c in checks if c.name == only]
        if not checks:
            raise SystemExit(f"unknown check: {only}")

    paths = (
        list(transcripts)
        if transcripts is not None
        else iter_transcripts(since_days=since_days, project_filter=project_filter)
    )

    results = {
        c.name: CheckResult(c.name, c.mode, c.blocks_when, c.note) for c in checks
    }
    seen: Dict[str, set] = {c.name: set() for c in checks}
    fired_sessions: Dict[str, set] = {c.name: set() for c in checks}
    total = 0
    tools_seen: Dict[str, int] = {}
    sessions: set = set()

    for call in iter_tool_calls(paths):
        total += 1
        sessions.add(call.session)
        tools_seen[call.tool_name] = tools_seen.get(call.tool_name, 0) + 1
        for check in checks:
            fn = check.predicate or check.trigger
            if fn is None:
                continue
            try:
                if check.needs_cwd:
                    reason = fn(call.tool_name, call.tool_input,
                               _worktree_anchor(call.cwd) or root)
                else:
                    reason = fn(call.tool_name, call.tool_input)
            except Exception:  # noqa: BLE001 — a check that raises is itself a finding
                reason = None
            if not reason:
                continue
            res = results[check.name]
            res.fired += 1
            fired_sessions[check.name].add(call.session)
            operand = operand_of(call)
            key = f"{call.tool_name}\x00{operand}"
            if key not in seen[check.name]:
                seen[check.name].add(key)
                if samples and len(res.samples) < samples:
                    res.samples.append(
                        {"tool": call.tool_name, "operand": operand,
                         "reason": " ".join(str(reason).split())[:SAMPLE_CHARS]}
                    )

    for name, res in results.items():
        res.distinct = len(seen[name])
        res.sessions = len(fired_sessions[name])

    return {
        "corpus": {
            "source": "claude_code_transcripts",
            "root": str(transcript_root()),
            "transcripts": len(paths),
            "sessions": len(sessions),
            "tool_calls": total,
            "window_days": since_days,
            "project_filter": project_filter or None,
            "tool_mix": dict(sorted(tools_seen.items(), key=lambda kv: -kv[1])[:12]),
        },
        "hook_events": hook_events_operand_availability(),
        "checks": [
            {
                "check": r.name,
                "mode": r.mode,
                "fired": r.fired,
                "fire_rate": r.fire_rate(total),
                "distinct_operands": r.distinct,
                "sessions_affected": r.sessions,
                "blocks_when": r.blocks_when,
                **({"note": r.note} if r.note else {}),
                **({"samples": r.samples} if r.samples else {}),
            }
            for r in (results[c.name] for c in checks)
        ],
    }


def to_markdown(report: Dict[str, Any]) -> str:
    corpus = report["corpus"]
    lines = [
        "# PreToolUse check fire-rate survey",
        "",
        f"- corpus: {corpus['tool_calls']:,} tool calls across "
        f"{corpus['sessions']} sessions "
        f"({corpus['transcripts']} transcripts, last {corpus['window_days']} days)",
        f"- hook_events usable as corpus: {report['hook_events']['usable_as_corpus']} "
        f"— {report['hook_events']['reason']}",
        "",
        "| check | mode | fired | fire rate | distinct | sessions | blocks when |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for c in report["checks"]:
        lines.append(
            f"| `{c['check']}` | {c['mode']} | {c['fired']} | "
            f"{c['fire_rate'] * 100:.3f}% | {c['distinct_operands']} | "
            f"{c['sessions_affected']} | {c['blocks_when']} |"
        )
    for c in report["checks"]:
        if c.get("samples"):
            lines += ["", f"### {c['check']} — sample operands", ""]
            lines += [f"- `{s['tool']}` `{s['operand']}`" for s in c["samples"]]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fire-rate survey for the PreToolUse hook's checks (exa-bench-05)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--markdown", action="store_true", help="markdown table")
    parser.add_argument("--since-days", type=float, default=30.0,
                        help="transcript mtime window (0 = no limit)")
    parser.add_argument("--project", default="",
                        help="substring of the encoded project dir name")
    parser.add_argument("--check", default=None, help="survey a single check")
    parser.add_argument("--samples", type=int, default=0,
                        help="include up to N distinct sample operands per check")
    parser.add_argument("--live-git", action="store_true",
                        help="evaluate branch_deletion against live refs")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 if any replayed check exceeds --max-fire-rate")
    parser.add_argument("--max-fire-rate", type=float, default=0.0,
                        help="gate threshold as a fraction of all tool calls")
    args = parser.parse_args(argv)

    report = survey(
        since_days=args.since_days or None,
        project_filter=args.project,
        samples=args.samples,
        live_git=args.live_git,
        only=args.check,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(to_markdown(report))

    if args.gate:
        over = [
            c for c in report["checks"]
            if c["mode"] == "replayed" and c["fire_rate"] > args.max_fire_rate
        ]
        if over:
            print(
                "GATE FAILED: "
                + ", ".join(f"{c['check']} {c['fire_rate'] * 100:.3f}%" for c in over),
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
