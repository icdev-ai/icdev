# CUI // SP-CTI
"""Transcript cost reader -- per-session USD by model and task type (xrv-cost-01).

WHY. ICDEV has four token ledgers (``ai_telemetry``, ``agent_token_usage``,
``module_budget_usage``, the proxy ledgers) and NONE sees a Claude Code
session: the ``claude_cli`` adapter bypasses ``router.invoke``, so every
claude-cli call carries no cost row. The transcripts on disk
(``~/.claude/projects/<slug>/<session>.jsonl``) DO carry ``message.usage`` on
every assistant record. This module reads that, prices what the pricing table
declares, and REFUSES to price anything else.

WHAT IT REUSES, never copies:

* ``tools.hooks.fire_rate_survey.transcript_root`` / ``iter_transcripts`` --
  the one statement of where transcripts live and how a window/project filter
  selects them (file mtime and encoded-cwd substring).
* ``tools.llm.cost_intelligence._model_pricing`` -- the ``args/llm_config.yaml``
  per-model ``input_per_1k`` / ``output_per_1k`` table, resolved from a
  transcript's model id through ``_model_id_to_name``.

FOUR HONESTY RULES:

1. A message is counted ONCE. Claude Code writes one message across several
   JSONL lines (thinking, text and tool_use each on their own line, all
   carrying the same ``message.id`` and the same ``usage``); the LAST line
   wins for usage, and the content blocks are unioned so the task type is
   judged on the whole message.
2. An unknown model prices to ``None`` and is counted under ``unpriced``,
   NEVER $0. A model whose table entry carries ``0.0 / 0.0`` on a non-local
   provider is unpriced too (``cost_intelligence.COST_BASIS_UNPRICED``); a
   local provider's $0 is the true cost (``local_zero``).
3. Cache tokens are reported RAW beside the priced figure. Anthropic bills
   cache reads and cache writes at rates the table does not declare, so
   ``cost_usd`` covers ``input_tokens`` and ``output_tokens`` only and the
   report says so (``priced_tokens``). Do not invent those rates here.
4. Every total is ``None``, never 0, over zero assistant records, and every
   rate is ``None`` over an empty denominator.

Task-type classification is DETERMINISTIC, at parse time, from the tool names
and the assistant text of each message -- no model call. The rules are the one
module-level table ``TASK_TYPE_RULES``; the first matching row wins.

Usage::

    python -m tools.cost.session_cost --survey --since-days 7 --project ICDev --json
    python -m tools.cost.session_cost --session <session-id> --json

Report only. Exit 2 when the transcript root is absent or the named session
cannot be found -- a survey that could not be produced is never a clean one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from icdev.tools.hooks.fire_rate_survey import iter_transcripts, transcript_root
from icdev.tools.llm.cost_intelligence import (
    COST_BASIS_LOCAL_ZERO,
    COST_BASIS_PRICED,
    COST_BASIS_UNPRICED,
    _is_local_model,
    _load_config,
    _model_id_to_name,
    _model_pricing,
)

TASK_TYPES = (
    "coding", "debugging", "testing", "exploration", "planning",
    "delegation", "git_ops", "conversation", "general",
)

#: The classification table. One row per rule, evaluated in order; the FIRST
#: row that matches names the message's task type. ``tools`` matches a tool
#: name used in the message, ``bash`` a regex over a Bash/PowerShell command,
#: ``keywords`` a lowercase substring of the assistant's own text. A message
#: with no tool call and only text is ``conversation``; a message matching no
#: row is ``general``.
TASK_TYPE_RULES: List[Dict[str, Any]] = [
    {"task_type": "delegation", "tools": {"Agent", "Task", "Workflow", "SendMessage"}},
    {"task_type": "git_ops", "bash": re.compile(r"(^|[;&|(]\s*)(git|gh)\s", re.I)},
    {"task_type": "testing",
     "bash": re.compile(r"\b(pytest|behave|playwright|npm\s+test|vitest|jest|"
                        r"red_first_gate|isolation_run)\b", re.I),
     "keywords": ("failing test", "red -> green", "red first")},
    # A shell that WRITES is coding too: under bypass-permissions Bash does the
    # editing (sed -i, heredocs, redirects), and reading those as `general`
    # hid ~10k of ~19k messages on the first live run.
    {"task_type": "coding",
     "tools": {"Edit", "Write", "MultiEdit", "NotebookEdit"},
     "bash": re.compile(r"(\bsed\s+-i\b|<<\s*-?\s*['\"]?\w|(^|[^2&>])>{1,2}\s*[^&\s]|"
                        r"\btee\b|\bmkdir\b|\bcp\b|\bmv\b|\brm\b|\btouch\b)")},
    {"task_type": "debugging",
     "keywords": ("traceback", "stack trace", "root cause", "reproduce",
                  "regression", "debug")},
    {"task_type": "planning",
     "tools": {"EnterPlanMode", "ExitPlanMode", "TodoWrite"},
     "keywords": ("implementation plan", "plan of record", "acceptance criteria",
                  "approach:", "design:")},
    {"task_type": "exploration",
     "tools": {"Read", "Grep", "Glob", "LS", "ToolSearch", "WebFetch", "WebSearch"},
     "bash": re.compile(r"(^|[;&|(]\s*)(ls|cat|grep|rg|find|head|tail|wc|tree|"
                        r"sed\s+-n|type|Get-Content|Get-ChildItem|Select-String)\b",
                        re.I)},
]

_SHELL_TOOLS = {"Bash", "PowerShell"}
_USAGE_FIELDS = (
    "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    "output_tokens",
)
#: Only this much assistant text is scanned for keywords per message: a long
#: answer is classified by its opening, and the cap keeps a survey over
#: hundreds of transcripts bounded.
MAX_KEYWORD_CHARS = 4000
#: A `partial` bucket has priced AND unpriced messages, so its cost_usd is a
#: LOWER BOUND on what was spent.
COST_BASIS_PARTIAL = "partial"


# ── Reading ───────────────────────────────────────────────────────────────


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def iter_usage(paths: Sequence[Path]) -> Iterator[Dict[str, Any]]:
    """One usage record per assistant MESSAGE across *paths*.

    Deduped on ``message.id`` (falling back to the record ``uuid``): Claude
    Code writes a message across several lines and the last wins for usage,
    while the tool names and text seen on every line are unioned so the task
    type is judged on the whole message. Torn or malformed lines are skipped
    exactly as ``fire_rate_survey.iter_tool_calls`` skips them -- a transcript
    is appended live and a partial final line is normal.

    Yields ``{session, project, ts, model, input_tokens,
    cache_read_input_tokens, cache_creation_input_tokens, output_tokens,
    message_id, cwd, tools, text, task_type}``.
    """
    for path in paths:
        session = path.stem
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        messages: Dict[str, Dict[str, Any]] = {}
        with handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(record, dict) or record.get("type") != "assistant":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                message_id = str(message.get("id") or record.get("uuid") or "")
                if not message_id:
                    continue
                entry = messages.get(message_id)
                if entry is None:
                    entry = {
                        "session": session,
                        "project": path.parent.name,
                        "message_id": message_id,
                        "ts": "",
                        "cwd": "",
                        "model": "",
                        "tools": [],
                        "text": "",
                    }
                    messages[message_id] = entry
                entry["ts"] = str(record.get("timestamp") or entry["ts"])
                entry["cwd"] = str(record.get("cwd") or entry["cwd"])
                entry["model"] = str(message.get("model") or entry["model"])
                for field_name in _USAGE_FIELDS:
                    entry[field_name] = _as_int(usage.get(field_name))
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        name = str(block.get("name") or "")
                        tool_input = block.get("input")
                        command = ""
                        if name in _SHELL_TOOLS and isinstance(tool_input, dict):
                            command = str(tool_input.get("command") or "")
                        entry["tools"].append({"name": name, "command": command})
                    elif block.get("type") == "text":
                        if len(entry["text"]) < MAX_KEYWORD_CHARS:
                            entry["text"] += " " + str(block.get("text") or "")
        for entry in messages.values():
            entry["task_type"] = classify_task_type(entry["tools"], entry["text"])
            yield entry


# ── Classification ────────────────────────────────────────────────────────


def classify_task_type(tools: Sequence[Dict[str, str]], text: str = "") -> str:
    """First matching row of ``TASK_TYPE_RULES``; ``conversation`` for a
    text-only message; ``general`` when nothing matches."""
    names = {t.get("name", "") for t in tools}
    commands = [t.get("command", "") for t in tools if t.get("command")]
    lowered = (text or "")[:MAX_KEYWORD_CHARS].lower()
    for rule in TASK_TYPE_RULES:
        if names & rule.get("tools", set()):
            return rule["task_type"]
        pattern = rule.get("bash")
        if pattern is not None and any(pattern.search(c) for c in commands):
            return rule["task_type"]
        if any(k in lowered for k in rule.get("keywords", ())):
            return rule["task_type"]
    if not tools and lowered.strip():
        return "conversation"
    return "general"


# ── Pricing ───────────────────────────────────────────────────────────────


def price_table(config: Optional[dict] = None) -> Dict[str, Dict[str, Any]]:
    """``{transcript_model_id: {input_per_1k, output_per_1k, basis, config_name}}``.

    Built from ``cost_intelligence._model_pricing`` (the declared table) keyed
    through ``_model_id_to_name`` so a transcript's ``claude-opus-4-8`` finds
    the ``claude-opus`` entry. A model absent from the table is simply absent
    here, and ``price_message`` reports it ``unpriced``.
    """
    cfg = config if config is not None else _load_config()
    pricing = _model_pricing(cfg)
    table: Dict[str, Dict[str, Any]] = {}
    for model_id, name in _model_id_to_name(cfg).items():
        if not model_id or name not in pricing:
            continue
        rates = pricing[name]
        if _is_local_model(cfg, name):
            basis = COST_BASIS_LOCAL_ZERO
        elif rates["input_per_1k"] <= 0.0 and rates["output_per_1k"] <= 0.0:
            basis = COST_BASIS_UNPRICED
        else:
            basis = COST_BASIS_PRICED
        table[model_id] = {**rates, "basis": basis, "config_name": name}
    return table


def price_message(usage: Dict[str, Any],
                  table: Dict[str, Dict[str, Any]]) -> Tuple[Optional[float], str]:
    """``(cost_usd, basis)`` for one message. ``None`` when unpriced.

    Prices ``input_tokens`` and ``output_tokens`` only -- the table declares
    no cache-read or cache-write rate, and pricing those at the input rate
    would fabricate a figure in the direction that overstates spend.
    """
    entry = table.get(usage.get("model") or "")
    if entry is None or entry["basis"] == COST_BASIS_UNPRICED:
        return None, COST_BASIS_UNPRICED
    if entry["basis"] == COST_BASIS_LOCAL_ZERO:
        return 0.0, COST_BASIS_LOCAL_ZERO
    cost = (usage.get("input_tokens", 0) / 1000.0) * entry["input_per_1k"]
    cost += (usage.get("output_tokens", 0) / 1000.0) * entry["output_per_1k"]
    return round(cost, 6), COST_BASIS_PRICED


# ── Aggregation ───────────────────────────────────────────────────────────


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """A percentage, or ``None`` over an empty denominator -- never 0.0 or
    100.0 for something that was not measured."""
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, 2)


def _new_bucket() -> Dict[str, Any]:
    return {
        "messages": 0,
        "input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": None,
        "cost_basis": None,
        "priced_messages": 0,
        "unpriced_messages": 0,
        "_bases": set(),
    }


def _add(bucket: Dict[str, Any], usage: Dict[str, Any],
         cost: Optional[float], basis: str) -> None:
    bucket["messages"] += 1
    for field_name in _USAGE_FIELDS:
        bucket[field_name] += usage.get(field_name, 0)
    if cost is None:
        bucket["unpriced_messages"] += 1
    else:
        bucket["priced_messages"] += 1
        bucket["cost_usd"] = round((bucket["cost_usd"] or 0.0) + cost, 6)
    bucket["_bases"].add(basis)


def _finish(bucket: Dict[str, Any]) -> Dict[str, Any]:
    bases = bucket.pop("_bases", set())
    if bucket["priced_messages"] == 0:
        bucket["cost_basis"] = COST_BASIS_UNPRICED if bucket["messages"] else None
        bucket["cost_usd"] = None
    elif bucket["unpriced_messages"]:
        bucket["cost_basis"] = COST_BASIS_PARTIAL
    elif bases == {COST_BASIS_LOCAL_ZERO}:
        bucket["cost_basis"] = COST_BASIS_LOCAL_ZERO
    else:
        bucket["cost_basis"] = COST_BASIS_PRICED
    prompt = (bucket["input_tokens"] + bucket["cache_read_input_tokens"]
              + bucket["cache_creation_input_tokens"])
    bucket["cache_read_share_pct"] = _rate(bucket["cache_read_input_tokens"], prompt)
    return bucket


def summarize(usages: Sequence[Dict[str, Any]], table: Dict[str, Dict[str, Any]],
              *, sessions_scanned: int, transcripts_unreadable: int) -> Dict[str, Any]:
    """Per-session rows plus totals by model and by task type."""
    sessions: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    by_task_type: Dict[str, Dict[str, Any]] = {}
    totals = _new_bucket()
    for usage in usages:
        cost, basis = price_message(usage, table)
        sid = usage["session"]
        row = sessions.get(sid)
        if row is None:
            row = {
                "session": sid,
                "project": usage.get("project", ""),
                "cwd": usage.get("cwd", ""),
                "first_ts": usage.get("ts", ""),
                "last_ts": usage.get("ts", ""),
                "by_model": {},
                "task_types": Counter(),
                **_new_bucket(),
            }
            sessions[sid] = row
        ts = usage.get("ts", "")
        if ts and (not row["first_ts"] or ts < row["first_ts"]):
            row["first_ts"] = ts
        if ts and ts > row["last_ts"]:
            row["last_ts"] = ts
        if usage.get("cwd") and not row["cwd"]:
            row["cwd"] = usage["cwd"]
        model = usage.get("model") or "(unknown)"
        _add(row, usage, cost, basis)
        _add(row["by_model"].setdefault(model, _new_bucket()), usage, cost, basis)
        _add(by_model.setdefault(model, _new_bucket()), usage, cost, basis)
        _add(by_task_type.setdefault(usage["task_type"], _new_bucket()),
             usage, cost, basis)
        _add(totals, usage, cost, basis)
        row["task_types"][usage["task_type"]] += 1

    rows = []
    for row in sessions.values():
        counts = row.pop("task_types")
        row["task_type"] = counts.most_common(1)[0][0] if counts else None
        row["task_type_messages"] = dict(counts)
        row["by_model"] = {m: _finish(b) for m, b in row["by_model"].items()}
        rows.append(_finish(row))
    rows.sort(key=lambda r: r["last_ts"], reverse=True)

    finished_totals = _finish(totals)
    if finished_totals["messages"] == 0:
        # Zero assistant records: nothing was measured, so nothing is 0.
        for field_name in _USAGE_FIELDS:
            finished_totals[field_name] = None
        finished_totals["messages"] = None
        finished_totals["priced_messages"] = None
        finished_totals["unpriced_messages"] = None
    unpriced_models = sorted(
        m for m, b in by_model.items() if b["priced_messages"] == 0
    )
    return {
        "state": ("unmeasurable" if finished_totals["messages"] is None
                  else "measured"),
        "sessions_scanned": sessions_scanned,
        "sessions_with_usage": len(rows),
        "transcripts_unreadable": transcripts_unreadable,
        "unpriced": finished_totals["unpriced_messages"],
        "unpriced_models": unpriced_models,
        "totals": finished_totals,
        "by_model": {m: _finish(b) for m, b in sorted(by_model.items())},
        "by_task_type": {t: _finish(b) for t, b in sorted(by_task_type.items())},
        "sessions": rows,
        "priced_tokens": "input_tokens + output_tokens only; cache read/creation "
                         "tokens are reported raw and carry no declared rate",
        "pricing_source": "args/llm_config.yaml models.*.pricing",
    }


def survey(*, root: Optional[Path] = None, since_days: Optional[float] = 7.0,
           project: str = "", config: Optional[dict] = None,
           transcripts: Optional[Sequence[Path]] = None) -> Dict[str, Any]:
    """The survey. Raises ``FileNotFoundError`` when the root is absent."""
    root = root or transcript_root()
    if transcripts is None:
        if not root.is_dir():
            raise FileNotFoundError(f"transcript root not found: {root}")
        transcripts = iter_transcripts(root=root, since_days=since_days,
                                       project_filter=project)
    unreadable = 0
    readable: List[Path] = []
    for path in transcripts:
        try:
            with path.open("r", encoding="utf-8", errors="replace"):
                pass
            readable.append(path)
        except OSError:
            unreadable += 1
    report = summarize(list(iter_usage(readable)), price_table(config),
                       sessions_scanned=len(readable),
                       transcripts_unreadable=unreadable)
    report.update({
        "transcript_root": str(root),
        "since_days": since_days,
        "project_filter": project,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
    return report


def find_session(session_id: str, root: Optional[Path] = None) -> Optional[Path]:
    """The transcript for *session_id* under *root*, or ``None``."""
    root = root or transcript_root()
    if not root.is_dir():
        return None
    matches = sorted(root.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


# ── CLI ───────────────────────────────────────────────────────────────────


def _fmt_cost(bucket: Dict[str, Any]) -> str:
    if bucket["cost_usd"] is None:
        return "unpriced"
    return f"${bucket['cost_usd']:.4f} ({bucket['cost_basis']})"


def _human(report: Dict[str, Any]) -> str:
    lines = [
        f"Transcript cost survey -- root {report.get('transcript_root')}",
        f"  sessions scanned {report['sessions_scanned']}, with usage "
        f"{report['sessions_with_usage']}, unreadable {report['transcripts_unreadable']}",
        f"  state {report['state']}; unpriced messages {report['unpriced']} "
        f"(models: {', '.join(report['unpriced_models']) or 'none'})",
        f"  {report['priced_tokens']}",
        "",
        "  by model:",
    ]
    for model, b in report["by_model"].items():
        lines.append(
            f"    {model:<28} msgs {b['messages']:>6}  in {b['input_tokens']:>9}  "
            f"cache_read {b['cache_read_input_tokens']:>11}  "
            f"cache_write {b['cache_creation_input_tokens']:>11}  "
            f"out {b['output_tokens']:>8}  {_fmt_cost(b)}")
    lines.append("  by task type:")
    for task_type, b in report["by_task_type"].items():
        lines.append(f"    {task_type:<14} msgs {b['messages']:>6}  "
                     f"out {b['output_tokens']:>8}  {_fmt_cost(b)}")
    lines.append("  sessions (newest first, at most 40):")
    for row in report["sessions"][:40]:
        lines.append(f"    {row['session'][:12]}  {row['last_ts'][:19]:<19}  "
                     f"{row['task_type'] or '-':<12} msgs {row['messages']:>5}  "
                     f"{_fmt_cost(row)}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Per-session USD by model and task type, read from the "
                    "Claude Code transcripts. Report only.")
    ap.add_argument("--survey", action="store_true", help="scan the transcript root")
    ap.add_argument("--session", metavar="ID", help="one session's transcript")
    ap.add_argument("--since-days", type=float, default=7.0,
                    help="transcript mtime window (default 7)")
    ap.add_argument("--project", default="",
                    help="substring of the encoded project directory name")
    ap.add_argument("--root", type=Path, default=None,
                    help="override the transcript root (default: ~/.claude/projects)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.survey and not args.session:
        ap.error("one of --survey or --session is required")
    root = args.root or transcript_root()
    try:
        if args.session:
            path = find_session(args.session, root)
            if path is None:
                raise FileNotFoundError(
                    f"session {args.session} not found under {root}")
            report = survey(root=root, since_days=None, transcripts=[path])
        else:
            report = survey(root=root, since_days=args.since_days,
                            project=args.project)
    except FileNotFoundError as exc:
        payload = {"error": str(exc), "state": "unmeasurable"}
        print(json.dumps(payload, indent=2) if args.json else f"ERROR: {exc}")
        return 2
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
