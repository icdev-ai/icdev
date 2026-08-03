#!/usr/bin/env python3
# CUI // SP-CTI
"""Replay real agent transcripts through the semantic loop detector (ars-loop-01).

A loop detector that has only ever seen synthetic loops is untuned: the failure
mode that matters is the *false positive*, and a false positive kills legitimate
iterative work. This tool replays recorded transcripts through
:func:`icdev.tools.llm.loop_detector.detect_semantic_loop` exactly as the agent
loop would — evaluating the window after every tool call — and reports every
window that would have been flagged, so the thresholds can be judged against
real work rather than against invented examples.

Supported transcript formats (auto-detected per file):

* **Claude Code JSONL** — one JSON object per line with ``message.content``
  blocks; ``tool_use`` blocks carry ``name``/``input``, ``tool_result`` blocks
  carry the output keyed by ``tool_use_id``.
* **Agent-loop JSON** — a ``tool_call_log`` list as produced by
  :class:`icdev.tools.llm.agent_loop.AgentLoopResult` (``{turn, name, input,
  result, error}``), either at the top level or under a ``tool_call_log`` key.

Usage::

    python tools/llm/loop_detector_tune.py --transcripts ~/.claude/projects/<proj>
    python tools/llm/loop_detector_tune.py --transcripts <dir> --threshold 0.9 --json
    python tools/llm/loop_detector_tune.py --transcripts <dir> --show 20

Exit code is 0 unless ``--max-flag-rate`` is given and exceeded, so the sweep can
also be used as a regression gate once a corpus is pinned.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

_HERE = Path(__file__).resolve()
for _parent in _HERE.parents:
    if (_parent / "icdev" / "__init__.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from icdev.tools.llm.loop_detector import (  # noqa: E402
    DEFAULT_CONFIG,
    ToolCallRecord,
    detect_semantic_loop,
)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


def _block_text(content: Any) -> str:
    """Flatten a tool_result ``content`` field (str, or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def parse_claude_jsonl(path: Path) -> list[ToolCallRecord]:
    """Parse a Claude Code session transcript into tool-call records.

    ``tool_use`` and its matching ``tool_result`` arrive in separate lines, so
    calls are buffered by id and emitted in call order once the result lands.
    """
    pending: dict[str, ToolCallRecord] = {}
    order: list[str] = []
    turn = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:  # noqa: BLE001 — a truncated tail line is normal
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            emitted_this_message = False
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    call_id = str(block.get("id") or "")
                    if not call_id:
                        continue
                    if not emitted_this_message:
                        turn += 1
                        emitted_this_message = True
                    pending[call_id] = ToolCallRecord(
                        turn=turn,
                        name=str(block.get("name") or ""),
                        arguments=block.get("input") or {},
                    )
                    order.append(call_id)
                elif btype == "tool_result":
                    call_id = str(block.get("tool_use_id") or "")
                    record = pending.get(call_id)
                    if record is None:
                        continue
                    record.result = _block_text(block.get("content"))
                    record.is_error = bool(block.get("is_error"))
    return [pending[cid] for cid in order if cid in pending]


def parse_agent_loop_json(path: Path) -> list[ToolCallRecord]:
    """Parse an :class:`AgentLoopResult`-shaped ``tool_call_log`` JSON file."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = json.load(fh)
    log = raw.get("tool_call_log") if isinstance(raw, dict) else raw
    if not isinstance(log, list):
        return []
    records = []
    for item in log:
        if not isinstance(item, dict):
            continue
        records.append(
            ToolCallRecord(
                turn=int(item.get("turn", 0) or 0),
                name=str(item.get("name") or ""),
                arguments=item.get("input") or {},
                result=str(item.get("result") or ""),
                is_error=bool(item.get("error")),
            )
        )
    return records


def load_transcript(path: Path) -> list[ToolCallRecord]:
    """Dispatch on suffix; unreadable or unrecognised files yield no records."""
    try:
        if path.suffix == ".jsonl":
            return parse_claude_jsonl(path)
        if path.suffix == ".json":
            return parse_agent_loop_json(path)
    except Exception as exc:  # noqa: BLE001 — a bad file must not abort the sweep
        print(f"warn: {path.name}: {exc}", file=sys.stderr)
    return []


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def replay(records: list[ToolCallRecord], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate the detector after every tool call, as the live loop does.

    Returns one entry per *distinct* flagged run (consecutive flagged windows on
    the same tool are collapsed) — the live loop terminates on the first one, so
    counting every subsequent window would inflate the false-positive rate.
    """
    flags: list[dict[str, Any]] = []
    last_flag_index = -99
    last_tool = ""
    for idx in range(1, len(records) + 1):
        detection = detect_semantic_loop(records[:idx], config=config)
        if not detection.detected:
            continue
        if detection.tool_name == last_tool and idx - last_flag_index <= 1:
            last_flag_index = idx
            continue
        last_flag_index = idx
        last_tool = detection.tool_name
        info = detection.as_dict()
        info["at_call_index"] = idx
        info["at_turn"] = records[idx - 1].turn
        info["sample_arguments"] = [
            str(rec.arguments)[:160] for rec in records[max(0, idx - detection.cluster_size): idx]
        ]
        flags.append(info)
    return flags


def iter_transcripts(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.suffix in (".jsonl", ".json") and path.is_file():
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--transcripts",
        required=True,
        help="Transcript file or directory (*.jsonl Claude Code, *.json agent-loop)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max transcripts to scan (0 = all)")
    parser.add_argument("--window", type=int, default=DEFAULT_CONFIG["window"])
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_CONFIG["similarity_threshold"]
    )
    parser.add_argument(
        "--min-cluster", type=int, default=DEFAULT_CONFIG["min_cluster_size"]
    )
    parser.add_argument(
        "--min-turns", type=int, default=DEFAULT_CONFIG["min_distinct_turns"]
    )
    parser.add_argument("--coverage", type=float, default=DEFAULT_CONFIG["coverage_ratio"])
    parser.add_argument(
        "--show", type=int, default=10, help="Print details for the first N flags"
    )
    parser.add_argument(
        "--max-flag-rate",
        type=float,
        default=None,
        help="Exit 1 if the flagged-session rate exceeds this fraction (regression gate)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    config = {
        "enabled": True,
        "window": args.window,
        "similarity_threshold": args.threshold,
        "min_cluster_size": args.min_cluster,
        "min_distinct_turns": args.min_turns,
        "coverage_ratio": args.coverage,
        "result_sample_chars": DEFAULT_CONFIG["result_sample_chars"],
    }

    root = Path(args.transcripts).expanduser()
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 2

    sessions = 0
    sessions_with_calls = 0
    total_calls = 0
    flagged_sessions: list[dict[str, Any]] = []

    for path in iter_transcripts(root):
        if args.limit and sessions >= args.limit:
            break
        sessions += 1
        records = load_transcript(path)
        if not records:
            continue
        sessions_with_calls += 1
        total_calls += len(records)
        flags = replay(records, config)
        if flags:
            flagged_sessions.append({"transcript": path.name, "flags": flags})

    rate = len(flagged_sessions) / sessions_with_calls if sessions_with_calls else 0.0
    summary = {
        "config": config,
        "transcripts_scanned": sessions,
        "transcripts_with_tool_calls": sessions_with_calls,
        "tool_calls_scanned": total_calls,
        "flagged_transcripts": len(flagged_sessions),
        "flag_rate": round(rate, 4),
        "flags": flagged_sessions if args.json else flagged_sessions[: args.show],
    }

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"transcripts scanned : {sessions}")
        print(f"  with tool calls   : {sessions_with_calls}")
        print(f"tool calls scanned  : {total_calls}")
        print(f"flagged transcripts : {len(flagged_sessions)}  ({rate:.2%})")
        print(
            "config              : window={window} threshold={similarity_threshold} "
            "min_cluster={min_cluster_size} min_turns={min_distinct_turns} "
            "coverage={coverage_ratio}".format(**config)
        )
        for entry in flagged_sessions[: args.show]:
            print(f"\n--- {entry['transcript']}")
            for flag in entry["flags"][:3]:
                print(
                    f"  turn {flag['at_turn']:>3} {flag['tool_name']:<18} "
                    f"cluster={flag['cluster_size']}/{flag['window_size']} "
                    f"turns={flag['distinct_turns']} sim={flag['mean_similarity']}"
                )
                for sample in flag["sample_arguments"]:
                    print(f"      {sample}")

    if args.max_flag_rate is not None and rate > args.max_flag_rate:
        print(
            f"FAIL: flag rate {rate:.2%} exceeds --max-flag-rate {args.max_flag_rate:.2%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
