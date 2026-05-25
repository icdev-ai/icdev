# CUI // SP-CTI
"""Autonomous Coder CLI — run an agentic coding session from the command line."""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path


def _print_result(result, verbose: bool = False, out_file: str | None = None) -> None:
    sep = "=" * 60
    print(sep)
    print(f"Session : {result.session_id}")
    print(f"Status  : {result.status.upper()}")
    print(f"Elapsed : {result.elapsed_s}s")
    print(sep)

    if result.error:
        print(f"ERROR: {result.error}")
        return

    print(f"\nPLAN ({len(result.plan.get('steps', []))} steps):")
    for i, step in enumerate(result.plan.get("steps", []), 1):
        print(f"  {i}. {step}")

    print(f"\nVALIDATION: {result.validation.verdict.upper()} — score {result.validation.score}/100")
    if result.validation.issues:
        for issue in result.validation.issues:
            print(f"  ! {issue}")
    if result.validation.suggestion:
        print(f"  Suggestion: {result.validation.suggestion}")

    print("\nGENERATED CODE:")
    print("-" * 40)
    print(result.code)
    print("-" * 40)

    if verbose:
        print("\nAUDIT LOG:")
        for entry in result.audit_log:
            ts = entry["created_at"][11:19]
            payload = entry.get("payload") or ""
            print(f"  [{ts}] {entry['agent']}: {entry['event']}  {payload[:80]}")

    if out_file:
        Path(out_file).write_text(result.code, encoding="utf-8")
        print(f"\nCode saved to: {out_file}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous Coder — agentic AI coding pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python -m apps.autonomous_coder.main "write a quicksort in Python"
              python -m apps.autonomous_coder.main --backend stub "build a REST API endpoint"
              python -m apps.autonomous_coder.main --out result.py "parse CSV and compute averages"
        """),
    )
    parser.add_argument("task", nargs="?", help="Coding task description")
    parser.add_argument("--backend", choices=["auto", "icdev", "ollama", "stub"], default="auto")
    parser.add_argument("--out", metavar="FILE", help="Save generated code to file")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show audit log")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument("--max-duration", type=float, default=120)
    args = parser.parse_args(argv)

    if not args.task:
        # Interactive mode
        print("Autonomous Coder — agentic AI coding pipeline")
        print("Type your coding task (or 'quit' to exit):\n")
        try:
            task = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if task.lower() in ("quit", "exit", "q"):
            return 0
    else:
        task = args.task

    print(f"\nProcessing task: {task!r}")
    print("Backend:", args.backend)
    print()

    import apps.autonomous_coder as ac
    result = ac.run(
        task,
        backend=args.backend,
        max_steps=args.max_steps,
        max_tokens=args.max_tokens,
        max_duration_s=args.max_duration,
    )

    if args.json:
        output = {
            "session_id": result.session_id,
            "status": result.status,
            "elapsed_s": result.elapsed_s,
            "plan": result.plan,
            "code": result.code,
            "validation": {
                "verdict": result.validation.verdict,
                "score": result.validation.score,
                "issues": result.validation.issues,
            },
            "error": result.error,
        }
        print(json.dumps(output, indent=2))
    else:
        _print_result(result, verbose=args.verbose, out_file=args.out)

    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
