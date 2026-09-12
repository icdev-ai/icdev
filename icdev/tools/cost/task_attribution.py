# CUI // SP-CTI
"""Per-kanban-task cost attribution and "spend that shipped" (xrv-cost-02).

WHY. ``tools/agents/adapters/claude_cli.py::_parse_cli_json`` already reads
the ``--output-format json`` envelope (total_cost_usd, tokens, session_id,
duration_api_ms) and the adapter passes that flag on every dispatch. The kanban
runner writes that envelope to the task log and never read it back:
``agent_token_usage.task_id`` existed and was unpopulated -- MEASURED
2026-09-11 on the live PG board, 0 rows carry a task_id -- and
``agent_sessions`` has no cost column. So the board could not answer "what did
this card cost" or "did that spend ship".

TWO HALVES, ONE MODULE, so the reflex calls ONE function inside ONE try:

* WRITE, at reap. ``record_task_cost`` reads the LAST line of the task log,
  parses it through the adapter's own ``_parse_cli_json`` (never a second
  parser), and writes exactly one ``agent_token_usage`` row through
  ``token_tracker.log_usage`` with ``task_id`` set. An envelope that is not
  there records NOTHING; an envelope with no usage records NOTHING -- a zero
  row would read as "this dispatch was free", which is a claim, not an absence.
  ``cost_estimate_usd`` is the CLI's ``total_cost_usd`` AS REPORTED, never
  re-priced here: the pricing table on this host prices no Claude Code model
  (xrv-cost-01), so a local re-derivation would write ``None`` or $0 over a
  number the transport already gave.
* READ, for the CLI. ``task_report`` joins the rows for one task to the
  outcome through ``landed_check`` (task -> main) and ``pr_linker``
  (task -> PR), and ``survey_by_verdict`` sums cost per verdict over every
  attributed task.

THE VERDICT SET IS CLOSED (``VERDICTS``) and ``unmeasurable`` is its own
member, never folded into any other:

    shipped       landed on the default branch, and no revert names it
    reverted      landed, then a commit reverting a landed commit also landed
    abandoned     the task is TERMINAL on the board and nothing landed
    in_flight     the task is not terminal and nothing has landed yet
    unmeasurable  the ref / git / board could not be read, the revert probe
                  could not run, or the forge and git DISAGREE (a MERGED PR
                  beside a default branch carrying no commit naming the task)

A disagreement is unresolved, never a pick between two -- the same rule
dwr-anchor-06 applies to a finding with two origins.

The token column is ONE column. ``agent_token_usage`` has ``input_tokens`` and
no cache split, so the row carries the envelope's ``prompt_tokens_total``
(input + cache read + cache creation -- everything the session processed).
The disjoint split lives in the envelope and in ``ai_telemetry``, not here.

Report only. Nothing here changes a task's status, merges, or deletes a row.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from icdev.tools.agent.token_tracker import log_usage
from icdev.tools.agents.adapters.claude_cli import _parse_cli_json
from icdev.tools.db.storage import column_exists, get_connection
from icdev.tools.kanban.landed_check import (
    BLOCKING_CONFIDENCE,
    _grep_pattern,
    _run_git,
    check_landed_bulk,
    default_branch,
)
from icdev.tools.kanban.pr_linker import (
    TERMINAL_STATUSES,
    _pr_number_of,
    fetch_pr_state,
)
from icdev.tools.kanban.repo_registry import resolve_task_repo
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cost.task_attribution")

AGENT_ID = "kanban-scheduler"
FALLBACK_MODEL_ID = "claude-cli"

VERDICT_SHIPPED = "shipped"
VERDICT_REVERTED = "reverted"
VERDICT_ABANDONED = "abandoned"
VERDICT_IN_FLIGHT = "in_flight"
VERDICT_UNMEASURABLE = "unmeasurable"
VERDICTS = (
    VERDICT_SHIPPED,
    VERDICT_REVERTED,
    VERDICT_ABANDONED,
    VERDICT_IN_FLIGHT,
    VERDICT_UNMEASURABLE,
)

# Why a reap recorded nothing. Every one is a NAMED absence, never a zero row.
REASON_NO_LOG = "no_log"
REASON_NO_ENVELOPE = "no_envelope"
REASON_NO_USAGE = "no_usage"
REASON_RECORDED = "recorded"

PR_STATE_NOT_CONSULTED = "not_consulted"

# How a bucket's USD figure was arrived at. `unpriced` is NEVER $0.00 (xrv-cost-04).
COST_BASIS_PRICED = "priced"
COST_BASIS_PARTIAL = "partial"
COST_BASIS_UNPRICED = "unpriced"

_SESSION_LINE_RE = re.compile(r"^\s*\{")


# ── WRITE half: the envelope at reap ────────────────────────────────────────


def read_envelope(task_log: Path) -> Dict[str, Any]:
    """The structured envelope from the LAST JSON line of *task_log*, or ``{}``.

    stderr is merged into the log ahead of stdout, so the file may carry
    warnings before the envelope; the CLI writes the envelope as ONE line, last.
    A log whose last non-empty line is not a JSON object has no envelope --
    a killed process (timeout) leaves exactly that, and it is reported as such.
    """
    try:
        text = Path(task_log).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in reversed(text.splitlines()):
        if line.strip():
            if not _SESSION_LINE_RE.match(line):
                return {}
            _text, structured = _parse_cli_json(line)
            return structured
    return {}


def envelope_has_usage(envelope: Dict[str, Any]) -> bool:
    """True when the envelope reports ANY tokens or ANY cost.

    A well-formed envelope over a session that never reached the model (an
    immediate CLI error) carries zeros everywhere; that is "no usage", not
    "cost $0", and writing it would fabricate a free dispatch.
    """
    if not envelope:
        return False
    counters = (
        envelope.get("prompt_tokens_total"),
        envelope.get("input_tokens"),
        envelope.get("output_tokens"),
        envelope.get("cache_read_input_tokens"),
        envelope.get("cache_creation_input_tokens"),
    )
    if any(isinstance(c, (int, float)) and c > 0 for c in counters):
        return True
    cost = envelope.get("total_cost_usd")
    return isinstance(cost, (int, float)) and cost > 0


def record_task_cost(
    task_id: str,
    task_log: Path,
    *,
    project_id: str = "",
    db_path: Optional[Path] = None,
    log_usage_fn: Optional[Callable[..., int]] = None,
) -> Dict[str, Any]:
    """Write ONE ``agent_token_usage`` row for *task_id* from its task log.

    Returns a dict carrying ``recorded`` (bool), ``reason`` (one of the
    ``REASON_*`` names), and on success ``row_id``, ``cost_usd``, ``model_id``
    and ``session_id``. Raises nothing the caller must guard against beyond
    what ``log_usage`` itself raises -- the reflex wraps the whole call.
    """
    writer = log_usage_fn or log_usage
    log_path = Path(task_log)
    if not log_path.is_file():
        return {"recorded": False, "reason": REASON_NO_LOG, "task_id": task_id}
    envelope = read_envelope(log_path)
    if not envelope:
        return {"recorded": False, "reason": REASON_NO_ENVELOPE, "task_id": task_id}
    if not envelope_has_usage(envelope):
        return {
            "recorded": False, "reason": REASON_NO_USAGE, "task_id": task_id,
            "session_id": envelope.get("session_id") or "",
        }
    model_id = str(envelope.get("model") or "").strip() or FALLBACK_MODEL_ID
    cost = envelope.get("total_cost_usd")
    cost = float(cost) if isinstance(cost, (int, float)) else 0.0
    kwargs: Dict[str, Any] = dict(
        agent_id=AGENT_ID,
        project_id=str(project_id or ""),
        model_id=model_id,
        input_tokens=int(envelope.get("prompt_tokens_total") or 0),
        output_tokens=int(envelope.get("output_tokens") or 0),
        thinking_tokens=int(envelope.get("thinking_tokens") or 0),
        duration_ms=int(envelope.get("duration_api_ms") or 0),
        task_id=str(task_id),
        cost_estimate_usd=cost,
    )
    if db_path is not None:
        kwargs["db_path"] = Path(db_path)
    row_id = writer(**kwargs)
    return {
        "recorded": True,
        "reason": REASON_RECORDED,
        "task_id": task_id,
        "row_id": row_id,
        "cost_usd": cost,
        "model_id": model_id,
        "session_id": envelope.get("session_id") or "",
        "input_tokens": kwargs["input_tokens"],
        "output_tokens": kwargs["output_tokens"],
        "is_error": bool(envelope.get("is_error")),
    }


# ── READ half: rows, outcome, verdict ───────────────────────────────────────


def _connect(db_path: Optional[Path]):
    if db_path is not None:
        return get_connection(db_path=str(db_path))
    return get_connection()


def _table_exists(conn, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} WHERE 1=0")  # nosec B608 -- literal table names only
        return True
    except Exception:  # noqa: BLE001 -- an absent table is the answer, not an error
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


def usage_rows(task_id: str, *, db_path: Optional[Path] = None) -> Optional[List[Dict[str, Any]]]:
    """The ``agent_token_usage`` rows for one task; ``None`` when unreadable."""
    try:
        with _connect(db_path) as conn:
            if not _table_exists(conn, "agent_token_usage"):
                return []
            rows = conn.execute(
                "SELECT id, agent_id, project_id, model_id, input_tokens, "
                "output_tokens, thinking_tokens, duration_ms, cost_estimate_usd, "
                "created_at FROM agent_token_usage WHERE task_id = %s ORDER BY id",
                (str(task_id),),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 -- unreadable is None, never []
        logger.warning("task_attribution: usage rows for %s unreadable: %s", task_id, exc)
        return None
    return [dict(r) for r in rows]


def row_is_unpriced(row: Dict[str, Any]) -> bool:
    """True when this dispatch's USD figure was never REPORTED (xrv-cost-04).

    ``record_task_cost`` writes the CLI envelope's ``total_cost_usd`` and falls
    back to ``0.0`` when the envelope carries no cost field, so an ABSENT price
    reaches the ledger as a zero rather than as a NULL. Summing it as 0
    understates the total in the direction that makes spend look cheaper than
    it was -- the ``args/perfect_score_gate.yaml`` defect wearing a dollar sign.

    A row with NO tokens is never written at all (``envelope_has_usage``
    refuses it), so "no dollars beside real tokens" is the whole unpriced class
    and a genuinely free local dispatch cannot fall into it.
    """
    cost = row.get("cost_estimate_usd")
    if cost is None:
        return True
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        return True
    tokens = int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
    return cost <= 0.0 and tokens > 0


def cost_basis(priced_rows: int, unpriced_rows: int) -> Optional[str]:
    """``priced`` | ``partial`` | ``unpriced``; None over no rows at all."""
    if priced_rows and unpriced_rows:
        return COST_BASIS_PARTIAL
    if priced_rows:
        return COST_BASIS_PRICED
    if unpriced_rows:
        return COST_BASIS_UNPRICED
    return None


def attributed_totals(*, db_path: Optional[Path] = None,
                      window_days: Optional[float] = None) -> Optional[Dict[str, Dict[str, Any]]]:
    """``{task_id: {rows, cost_usd, ...}}`` over every attributed row; None if unreadable."""
    try:
        with _connect(db_path) as conn:
            if not _table_exists(conn, "agent_token_usage"):
                return {}
            sql = ("SELECT task_id, model_id, input_tokens, output_tokens, "
                   "cost_estimate_usd, created_at FROM agent_token_usage "
                   "WHERE task_id IS NOT NULL AND task_id <> ''")
            params: List[Any] = []
            if window_days is not None:
                since = datetime.now(timezone.utc) - timedelta(days=float(window_days))
                sql += " AND created_at >= %s"
                params.append(since.isoformat())
            rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("task_attribution: attributed totals unreadable: %s", exc)
        return None
    totals: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        r = dict(r)
        bucket = totals.setdefault(str(r["task_id"]), {
            "rows": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
            "models": set(), "last_at": "", "priced_rows": 0, "unpriced_rows": 0,
        })
        bucket["rows"] += 1
        if row_is_unpriced(r):
            bucket["unpriced_rows"] += 1
        else:
            bucket["priced_rows"] += 1
            bucket["cost_usd"] += float(r.get("cost_estimate_usd") or 0.0)
        bucket["input_tokens"] += int(r.get("input_tokens") or 0)
        bucket["output_tokens"] += int(r.get("output_tokens") or 0)
        bucket["models"].add(str(r.get("model_id") or ""))
        bucket["last_at"] = max(bucket["last_at"], str(r.get("created_at") or ""))
    for bucket in totals.values():
        bucket["models"] = sorted(m for m in bucket["models"] if m)
        bucket["cost_basis"] = cost_basis(bucket["priced_rows"], bucket["unpriced_rows"])
        # A task whose every dispatch is unpriced has NO cost, not a cost of
        # zero: $0.00 beside 28M tokens reads as a free build.
        bucket["cost_usd"] = (round(bucket["cost_usd"], 6)
                              if bucket["priced_rows"] else None)
    return totals


def board_rows(task_ids: Sequence[str], *, db_path: Optional[Path] = None
               ) -> Optional[Dict[str, Dict[str, Any]]]:
    """``{task_id: {status, executor_url, project_id}}``; None when the board is unreadable.

    A task absent from the board is absent from the map -- that is "not on the
    board", distinct from an unreadable board.
    """
    ids = [str(t) for t in task_ids if str(t or "").strip()]
    if not ids:
        return {}
    try:
        with _connect(db_path) as conn:
            if not _table_exists(conn, "kanban_tasks"):
                return None
            has_project = column_exists(conn, "kanban_tasks", "project_id")
            cols = "id, status, executor_url" + (", project_id" if has_project else "")
            out: Dict[str, Dict[str, Any]] = {}
            for i in range(0, len(ids), 200):
                chunk = ids[i:i + 200]
                marks = ",".join(["%s"] * len(chunk))
                rows = conn.execute(
                    f"SELECT {cols} FROM kanban_tasks WHERE id IN ({marks})",  # nosec B608 -- placeholders only
                    tuple(chunk),
                ).fetchall()
                for r in rows:
                    r = dict(r)
                    out[str(r["id"])] = {
                        "status": r.get("status"),
                        "executor_url": r.get("executor_url") or "",
                        "project_id": r.get("project_id") if has_project else None,
                    }
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("task_attribution: board unreadable: %s", exc)
        return None


def revert_probe_bulk(shas_by_task: Dict[str, Sequence[str]], *, repo_root, target: str,
                      task_ids: Optional[Iterable[str]] = None) -> Optional[Dict[str, bool]]:
    """Which tasks have a REVERT of a landed commit on *target*; None if git failed.

    A revert is a commit whose body says ``This reverts commit <sha>`` for one of
    the task's landed shas, or whose subject starts ``Revert`` and names the task
    id at a boundary (``landed_check._grep_pattern``, the one boundary rule).
    One ``git log`` for the whole set, so a survey over N tasks costs one call.
    """
    tasks = {str(t) for t in (task_ids or shas_by_task.keys())}
    out = {t: False for t in tasks}
    patterns: List[str] = []
    for shas in shas_by_task.values():
        for sha in shas:
            if sha and re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
                patterns.append(f"This reverts commit {sha}")
    for tid in tasks:
        patterns.append(rf"^Revert .*{_grep_pattern(tid)}")
    if not patterns:
        return out
    args = ["log", "-E", "--format=%H\x1f%s\x1f%b\x1e"]
    for p in patterns:
        args.append(f"--grep={p}")
    args.append(target)
    proc = _run_git(args, repo_root, timeout=120)
    if proc is None or proc.returncode != 0:
        return None
    for record in proc.stdout.split("\x1e"):
        parts = record.strip("\n\r").split("\x1f")
        if len(parts) < 2:
            continue
        subject = parts[1].strip()
        body = parts[2] if len(parts) > 2 else ""
        for tid in tasks:
            if any(sha and f"This reverts commit {sha}" in body
                   for sha in shas_by_task.get(tid, ())):
                out[tid] = True
            elif subject.startswith("Revert") and re.search(_grep_pattern(tid), subject):
                out[tid] = True
    return out


def classify_outcome(*, landed: Dict[str, Any], status: Optional[str],
                     reverted: Optional[bool], pr_state: Optional[str],
                     board_readable: bool = True) -> Dict[str, Any]:
    """The verdict from PRIMARY facts. Pure; every caller reaches it.

    ``landed`` is one ``landed_check`` report; ``status`` the board status or
    None when the task is not on the board; ``reverted`` the revert probe's
    answer (None = the probe could not run); ``pr_state`` MERGED/CLOSED/OPEN,
    None (unknowable) or ``PR_STATE_NOT_CONSULTED``.
    """
    reasons: List[str] = []
    if not board_readable:
        return {"verdict": VERDICT_UNMEASURABLE, "reasons": ["board_unreadable"]}
    if not landed.get("checked"):
        return {"verdict": VERDICT_UNMEASURABLE,
                "reasons": [f"landed_check:{landed.get('reason') or 'unchecked'}"]}
    if landed.get("landed"):
        if reverted is None:
            return {"verdict": VERDICT_UNMEASURABLE, "reasons": ["revert_probe_failed"]}
        if reverted:
            return {"verdict": VERDICT_REVERTED, "reasons": ["revert_commit_on_default_branch"]}
        return {"verdict": VERDICT_SHIPPED, "reasons": [f"landed:{landed.get('confidence')}"]}
    # Nothing landed under the task's id.
    if pr_state == "MERGED":
        return {"verdict": VERDICT_UNMEASURABLE,
                "reasons": ["forge_merged_but_default_branch_names_no_commit"]}
    if status is None:
        return {"verdict": VERDICT_UNMEASURABLE, "reasons": ["task_not_on_board"]}
    if pr_state == "OPEN":
        reasons.append("pr_open")
        return {"verdict": VERDICT_IN_FLIGHT, "reasons": reasons}
    if str(status) in TERMINAL_STATUSES:
        reasons.append(f"terminal:{status}")
        if pr_state == "CLOSED":
            reasons.append("pr_closed_unmerged")
        return {"verdict": VERDICT_ABANDONED, "reasons": reasons}
    reasons.append(f"status:{status}")
    return {"verdict": VERDICT_IN_FLIGHT, "reasons": reasons}


def _pct(numerator: float, denominator: float) -> Optional[float]:
    """A percentage, floored to one decimal; None over an empty denominator.

    Floored, not rounded: 99.96 must not read as a perfect score
    (args/perfect_score_gate.yaml), and 100.0 is reserved for a share that IS 100.
    """
    if denominator is None or denominator <= 0:
        return None
    return math.floor(numerator / denominator * 1000) / 10


def _group_by_repo(task_ids: Iterable[str]) -> Dict[Any, List[str]]:
    groups: Dict[Any, List[str]] = {}
    for tid in task_ids:
        target = resolve_task_repo(tid)
        key = (str(target.root) if target.root else None, target.base_branch)
        groups.setdefault(key, []).append(tid)
    return groups


def outcomes_for(task_ids: Sequence[str], *, db_path: Optional[Path] = None,
                 consult_forge: bool = False,
                 landed_fn: Callable[..., Dict[str, dict]] = None,
                 revert_fn: Callable[..., Optional[Dict[str, bool]]] = None,
                 pr_state_fn: Callable[..., Optional[str]] = None,
                 ) -> Dict[str, Dict[str, Any]]:
    """One outcome per task id: landed report, board status, revert, PR state, verdict."""
    landed_fn = landed_fn or check_landed_bulk
    revert_fn = revert_fn or revert_probe_bulk
    pr_state_fn = pr_state_fn or fetch_pr_state
    ids = [str(t) for t in task_ids]
    board = board_rows(ids, db_path=db_path)
    board_readable = board is not None
    board = board or {}
    out: Dict[str, Dict[str, Any]] = {}
    for (root, base_branch), group in _group_by_repo(ids).items():
        if root is None:
            landed = {t: {"checked": False, "reason": "external repo root not configured",
                          "landed": False, "commits": [], "confidence": None}
                      for t in group}
            reverted: Optional[Dict[str, bool]] = {}
        else:
            branch = base_branch or default_branch(root)
            landed = landed_fn(group, repo_root=root, branch=branch)
            shas = {t: [c.get("sha") for c in (landed.get(t) or {}).get("commits", [])
                        if c.get("evidence") in BLOCKING_CONFIDENCE]
                    for t in group if (landed.get(t) or {}).get("landed")}
            reverted = revert_fn(shas, repo_root=root, target=f"origin/{branch}",
                                 task_ids=list(shas)) if shas else {}
        for tid in group:
            row = board.get(tid)
            status = row.get("status") if row else None
            pr_url = (row or {}).get("executor_url") or ""
            pr_number = _pr_number_of(pr_url)
            if not consult_forge:
                pr_state: Optional[str] = PR_STATE_NOT_CONSULTED
            elif pr_number:
                pr_state = pr_state_fn(pr_number)
            else:
                pr_state = None
            rep = landed.get(tid) or {"checked": False, "reason": "no report", "landed": False,
                                       "commits": [], "confidence": None}
            rev = None if reverted is None else (reverted.get(tid, False) if rep.get("landed") else False)
            verdict = classify_outcome(landed=rep, status=status, reverted=rev,
                                       pr_state=pr_state, board_readable=board_readable)
            out[tid] = {
                "task_id": tid,
                "verdict": verdict["verdict"],
                "reasons": verdict["reasons"],
                "status": status,
                "on_board": row is not None,
                "project_id": (row or {}).get("project_id"),
                "pr_url": pr_url,
                "pr_state": pr_state,
                "landed": bool(rep.get("landed")),
                "landed_checked": bool(rep.get("checked")),
                "landed_confidence": rep.get("confidence"),
                "landed_commits": list(rep.get("commits") or [])[:5],
                "reverted": rev,
                "repo_root": root,
            }
    return out


def task_report(task_id: str, *, db_path: Optional[Path] = None, consult_forge: bool = True,
                **probe_fns) -> Dict[str, Any]:
    """The ``--task`` report: rows, cost and the verdict for one task."""
    rows = usage_rows(task_id, db_path=db_path)
    outcome = outcomes_for([task_id], db_path=db_path, consult_forge=consult_forge,
                           **probe_fns)[str(task_id)]
    unpriced = None if rows is None else sum(1 for r in rows if row_is_unpriced(r))
    if rows is None:
        cost_state, cost, basis = "unmeasurable", None, None
    elif not rows:
        cost_state, cost, basis = "no_rows", None, None
    else:
        priced = [float(r["cost_estimate_usd"]) for r in rows if not row_is_unpriced(r)]
        basis = cost_basis(len(priced), unpriced)
        # Every dispatch unpriced: no dollars were ever reported, so there is
        # no figure -- $0.00 would read as a free build (xrv-cost-04).
        cost_state = "measured" if priced else COST_BASIS_UNPRICED
        cost = round(sum(priced), 6) if priced else None
    report = {
        "task_id": str(task_id),
        "cost_usd": cost,
        "cost_state": cost_state,
        "cost_basis": basis,
        "unpriced_dispatches": unpriced,
        "dispatches": None if rows is None else len(rows),
        "rows": rows or [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report.update({k: v for k, v in outcome.items() if k != "task_id"})
    return report


def survey_by_verdict(*, db_path: Optional[Path] = None, window_days: Optional[float] = None,
                      consult_forge: bool = False, **probe_fns) -> Dict[str, Any]:
    """The ``--survey --by-verdict`` report: cost summed per verdict.

    A board with no attributed rows is ``unmeasurable`` with ``by_verdict``
    None -- never a $0 total under five empty buckets.
    """
    totals = attributed_totals(db_path=db_path, window_days=window_days)
    base = {
        "window_days": window_days,
        "forge_consulted": bool(consult_forge),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if totals is None:
        return {**base, "state": VERDICT_UNMEASURABLE, "reason": "ledger_unreadable",
                "tasks": None, "total_cost_usd": None, "by_verdict": None,
                "shipped_cost_share_pct": None}
    if not totals:
        return {**base, "state": VERDICT_UNMEASURABLE, "reason": "no_attributed_rows",
                "tasks": 0, "total_cost_usd": None, "by_verdict": None,
                "shipped_cost_share_pct": None}
    outcomes = outcomes_for(sorted(totals), db_path=db_path, consult_forge=consult_forge,
                            **probe_fns)
    by_verdict: Dict[str, Dict[str, Any]] = {
        v: {"tasks": 0, "dispatches": 0, "cost_usd": None, "task_ids": [],
            "unpriced_dispatches": 0} for v in VERDICTS
    }
    tasks_out: List[Dict[str, Any]] = []
    unpriced_dispatches = 0
    unpriced_tasks = 0
    for tid, bucket in sorted(totals.items()):
        verdict = outcomes[tid]["verdict"]
        b = by_verdict[verdict]
        b["tasks"] += 1
        b["dispatches"] += bucket["rows"]
        b["unpriced_dispatches"] += bucket["unpriced_rows"]
        unpriced_dispatches += bucket["unpriced_rows"]
        if bucket["cost_basis"] == COST_BASIS_UNPRICED:
            unpriced_tasks += 1
        # A task with NO priced dispatch contributes no dollars, and adding 0.0
        # for it would turn an absent price into a measured zero one line later.
        if bucket["cost_usd"] is not None:
            b["cost_usd"] = round((b["cost_usd"] or 0.0) + bucket["cost_usd"], 6)
        tasks_out.append({
            "task_id": tid, "verdict": verdict, "reasons": outcomes[tid]["reasons"],
            "status": outcomes[tid]["status"], "cost_usd": bucket["cost_usd"],
            "cost_basis": bucket["cost_basis"],
            "dispatches": bucket["rows"], "unpriced_dispatches": bucket["unpriced_rows"],
            "models": bucket["models"], "last_at": bucket["last_at"],
        })
        b["task_ids"].append(tid)
    priced = [b["cost_usd"] for b in totals.values() if b["cost_usd"] is not None]
    total_cost = round(sum(priced), 6) if priced else None
    measured = [by_verdict[v]["cost_usd"] for v in VERDICTS
                if v != VERDICT_UNMEASURABLE and by_verdict[v]["cost_usd"] is not None]
    measured_cost = sum(measured) if measured else 0.0
    shipped_cost = by_verdict[VERDICT_SHIPPED]["cost_usd"] or 0.0
    return {
        **base,
        "state": "measured",
        "tasks": len(totals),
        "total_cost_usd": total_cost,
        "measured_cost_usd": round(measured_cost, 6),
        "unmeasurable_cost_usd": by_verdict[VERDICT_UNMEASURABLE]["cost_usd"],
        # Dispatches whose USD figure was never reported. Counted apart from
        # every verdict: an unpriced dispatch still SHIPPED or was still
        # ABANDONED, so folding it into `unmeasurable` would make the verdict
        # answer a question about the price rather than about the outcome.
        "unpriced_dispatches": unpriced_dispatches,
        "unpriced_tasks": unpriced_tasks,
        # Share of the MEASURED spend that shipped. None when nothing was
        # measured -- an all-unmeasurable board is not "0% shipped".
        "shipped_cost_share_pct": _pct(shipped_cost, measured_cost),
        "by_verdict": by_verdict,
        "tasks_detail": tasks_out,
    }


def human_task(report: Dict[str, Any]) -> str:
    if report["cost_usd"] is not None:
        cost = "$%.4f" % report["cost_usd"]
    elif report["cost_state"] == "unmeasurable":
        cost = "unmeasurable"
    elif report["cost_state"] == COST_BASIS_UNPRICED:
        cost = "unpriced"
    else:
        cost = "no rows"
    lines = [
        f"Task {report['task_id']}: verdict {report['verdict']} "
        f"({', '.join(report['reasons'])})",
        f"  cost {cost} over {report['dispatches'] if report['dispatches'] is not None else '?'} "
        f"dispatch(es); status {report['status'] or 'not on board'}; "
        f"PR {report['pr_url'] or '-'} state {report['pr_state'] or 'unknown'}",
        f"  landed {report['landed']} (checked {report['landed_checked']}, "
        f"confidence {report['landed_confidence']}); reverted {report['reverted']}",
    ]
    for r in report["rows"]:
        lines.append(f"    row {r['id']}  {r['model_id']:<26} in {r['input_tokens']:>10} "
                     f"out {r['output_tokens']:>8}  ${float(r['cost_estimate_usd'] or 0):.4f}  "
                     f"{str(r['created_at'])[:19]}")
    return "\n".join(lines)


def human_survey(report: Dict[str, Any]) -> str:
    if report["state"] != "measured":
        return (f"Spend by verdict: {report['state']} ({report.get('reason')}); "
                f"tasks {report['tasks']}, total cost {report['total_cost_usd']}")
    lines = [
        f"Spend by verdict over {report['tasks']} attributed task(s), "
        f"window {report['window_days'] or 'all'} days, forge consulted {report['forge_consulted']}",
        "  total %s; measured $%.4f; shipped share %s%%" % (
            "unpriced" if report["total_cost_usd"] is None
            else "$%.4f" % report["total_cost_usd"],
            report["measured_cost_usd"],
            "?" if report["shipped_cost_share_pct"] is None
            else report["shipped_cost_share_pct"]),
        "  unpriced dispatches %s over %s wholly unpriced task(s)" % (
            report.get("unpriced_dispatches"), report.get("unpriced_tasks")),
    ]
    for v in VERDICTS:
        b = report["by_verdict"][v]
        cost = "-" if b["cost_usd"] is None else f"${b['cost_usd']:.4f}"
        lines.append(f"    {v:<13} tasks {b['tasks']:>4}  dispatches {b['dispatches']:>4}  {cost}")
    return "\n".join(lines)


def to_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)
