#!/usr/bin/env python3
# CUI // SP-CTI
"""Measure how often the network-egress check would fire (exa-bench-08).

``shared_checks.check_network_egress`` ships **monitor-only**. That is not
timidity — a guard that blocks real work gets switched off, and switching it off
is worse than never having shipped it. This tool is how the fire rate gets
measured before ``agent_egress.enforce`` is flipped in
``args/agent_egress_policy.yaml``.

Two sources, and they answer different questions:

``--sink`` (default)
    Summarize what the hook has ACTUALLY recorded on this machine
    (``.tmp/egress_findings.jsonl``). This is the honest ongoing signal: real
    commands, real sessions. It is empty on a fresh checkout, which is reported
    as "no operating history" rather than as a reassuring 0%.

``--corpus DIR``
    Replay historical Claude Code transcripts through the check to get a rate
    without waiting for the sink to fill. This is what produced the baseline in
    ``docs/security/agent-vendor-permission-bypass.md`` §4a: 0.093% of 78,903
    Bash calls. Defaults to ``~/.claude/projects`` when DIR is omitted.

The 1.83% -> 0.093% correction in that write-up is the argument for this tool
existing: 95% of the first pass was a single allowlist omission, and no amount
of unit testing would have surfaced it.

CLI:
    python tools/security/egress_fire_rate.py --json
    python tools/security/egress_fire_rate.py --corpus --json
    python tools/security/egress_fire_rate.py --corpus ~/.claude/projects --top 30
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Run by path: sys.path[0] is this file's directory, never the import root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.hooks import shared_checks  # noqa: E402


def _classify(command: str, policy: dict) -> tuple[str, list[str]]:
    """Verdict for one command, using the same logic the hook applies."""
    destinations = shared_checks.egress_destinations(command)
    if not destinations:
        return "clean", []
    unapproved = [
        h for h in destinations
        if shared_checks._suffix_match(h, policy.get("denied_hosts") or [])
        or not shared_checks._suffix_match(h, policy.get("allowed_hosts") or [])
    ]
    if not unapproved:
        return "allowlisted", []
    invoker = shared_checks._network_invoker(
        command, policy.get("network_invokers") or []
    )
    return ("egress" if invoker else "destination_only"), unapproved


def summarize_sink(root: Path, policy: dict) -> dict:
    """Read what the hook has recorded on this machine."""
    rel = str(policy.get("log_path") or ".tmp/egress_findings.jsonl")
    path = Path(rel) if Path(rel).is_absolute() else root / rel
    if not path.exists():
        return {
            "source": "sink", "path": str(path), "findings": 0,
            "telemetry_available": False,
            "note": (
                "no findings recorded yet — the hook has not observed an "
                "unapproved destination on this machine. That is an absence of "
                "history, NOT a measured 0% fire rate; use --corpus for a rate."
            ),
        }
    verdicts: Counter = Counter()
    hosts: Counter = Counter()
    invokers: Counter = Counter()
    rows = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        rows += 1
        verdicts[row.get("verdict", "?")] += 1
        invokers[row.get("invoker") or "(none)"] += 1
        for host in row.get("destinations") or []:
            hosts[host] += 1
    return {
        "source": "sink", "path": str(path), "findings": rows,
        "telemetry_available": rows > 0,
        "verdicts": dict(verdicts),
        "top_hosts": hosts.most_common(30),
        "invokers": dict(invokers),
        "note": (
            "findings only — the sink does not record allowed calls, so a "
            "share-of-all-calls rate is not computable from it. Use --corpus."
        ),
    }


def _iter_corpus_commands(corpus: Path):
    """Yield every Bash command in a Claude Code transcript tree."""
    for transcript in sorted(corpus.rglob("*.jsonl")):
        try:
            with transcript.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if '"tool_use"' not in line or "Bash" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    content = (obj.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue
                        if block.get("name") not in ("Bash", "BashOutput"):
                            continue
                        command = (block.get("input") or {}).get("command")
                        if isinstance(command, str) and command.strip():
                            yield command
        except Exception:  # noqa: BLE001 — an unreadable transcript is not fatal
            continue


def replay_corpus(corpus: Path, policy: dict, top: int = 30) -> dict:
    verdicts: Counter = Counter()
    hosts: Counter = Counter()
    samples: list[dict] = []
    total = 0
    for command in _iter_corpus_commands(corpus):
        total += 1
        verdict, unapproved = _classify(command, policy)
        verdicts[verdict] += 1
        if verdict == "egress":
            for host in unapproved:
                hosts[host] += 1
            if len(samples) < 25:
                samples.append({"hosts": unapproved, "command": command[:200]})
    fire = verdicts.get("egress", 0)
    return {
        "source": "corpus", "path": str(corpus),
        "commands": total,
        "verdicts": dict(verdicts),
        "block_rate_pct": round(100 * fire / total, 4) if total else 0.0,
        "top_unapproved_hosts": hosts.most_common(top),
        "samples": samples,
        "telemetry_available": total > 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--corpus", nargs="?", const="~/.claude/projects", default=None,
        help="replay a Claude Code transcript tree instead of reading the sink",
    )
    parser.add_argument("--sink", action="store_true", help="read the findings sink (default)")
    parser.add_argument("--top", type=int, default=30, help="how many hosts to list")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = shared_checks.default_repo_root()
    policy = shared_checks._load_egress_policy(root)
    if not policy:
        result = {
            "error": "egress policy not loaded",
            "hint": (
                "args/agent_egress_policy.yaml is missing, disabled, or PyYAML "
                "is unavailable — check_network_egress is inert in this checkout"
            ),
        }
        print(json.dumps(result, indent=2) if args.json else result["hint"])
        return 1

    if args.corpus is not None:
        corpus = Path(args.corpus).expanduser()
        if not corpus.exists():
            print(f"corpus not found: {corpus}", file=sys.stderr)
            return 1
        result = replay_corpus(corpus, policy, top=args.top)
    else:
        result = summarize_sink(root, policy)

    result["enforcing"] = bool(policy.get("enforce"))
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"source            : {result['source']} ({result['path']})")
    print(f"enforcing         : {result['enforcing']}")
    if result["source"] == "corpus":
        print(f"commands replayed : {result['commands']}")
        for name, count in sorted(result["verdicts"].items()):
            share = 100 * count / max(result["commands"], 1)
            print(f"  {name:>17} : {count:>7}  ({share:5.2f}%)")
        print(f"\nBLOCK rate if enforced: {result['block_rate_pct']}%")
        if result["top_unapproved_hosts"]:
            print("\ntop unapproved hosts:")
            for host, count in result["top_unapproved_hosts"]:
                print(f"  {count:>6}  {host}")
    else:
        print(f"findings recorded : {result['findings']}")
        if not result["telemetry_available"]:
            print(f"\n{result['note']}")
        else:
            for name, count in sorted(result.get("verdicts", {}).items()):
                print(f"  {name:>17} : {count}")
            print("\ntop hosts:")
            for host, count in result.get("top_hosts", []):
                print(f"  {count:>6}  {host}")
            print(f"\nnote: {result['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
