#!/usr/bin/env python3
# CUI // SP-CTI
"""Agentic task runner — LLM-backed executor for GitHub Actions.

Uses the existing LLMRouter (Ollama / Anthropic / any configured provider)
to run an agentic loop: read files, apply fixes, validate with ruff/pytest.
Replaces static ANVIL wrappers when an LLM key is available in CI.

Usage (from GA workflow):
    python tools/anvil/agentic_runner.py \\
        --task-id  "ci-fix-12345" \\
        --task-desc "CI workflow: icdev-ci.yml ..." \\
        --task-type fix \\
        [--max-turns 4]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# ── helpers ──────────────────────────────────────────────────────────────────

def _run(cmd: str, cwd: Path = BASE_DIR) -> tuple[int, str]:
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,  # nosec: B602 — cmd is constructed internally
        capture_output=True, text=True, timeout=120,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return f"[read error: {e}]"


def _write_file(path: str, content: str) -> str:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"[wrote {len(content)} chars to {path}]"
    except Exception as e:
        return f"[write error: {e}]"


# ── tool dispatch ─────────────────────────────────────────────────────────────

TOOLS = {
    "read_file":   {"desc": "Read a file. Args: {path}"},
    "write_file":  {"desc": "Write/overwrite a file. Args: {path, content}"},
    "run_command": {"desc": "Run a shell command (read-only or lint/test). Args: {command}"},
    "done":        {"desc": "Finish. Args: {summary, changed_files: [list of paths]}"},
}

_ALLOWED_CMD_PREFIXES = (
    "python ", "python3 ", "ruff ", "pytest ", "git diff", "git status",
    "git log", "ls ", "cat ", "head ", "grep ",
)

def _dispatch_tool(name: str, args: dict) -> str:
    if name == "read_file":
        return _read_file(args.get("path", ""))
    if name == "write_file":
        return _write_file(args.get("path", ""), args.get("content", ""))
    if name == "run_command":
        cmd = args.get("command", "")
        if not any(cmd.startswith(p) for p in _ALLOWED_CMD_PREFIXES):
            return f"[blocked: only read/lint/test commands allowed, got: {cmd!r}]"
        rc, out = _run(cmd)
        return f"[exit {rc}]\n{out[:3000]}"
    if name == "done":
        return "__DONE__"
    return f"[unknown tool: {name}]"


# ── LLM call ─────────────────────────────────────────────────────────────────

def _llm_call(router, messages: list[dict]) -> str:
    from tools.llm.provider import LLMRequest
    resp = router.invoke("code_generation", LLMRequest(
        messages=messages,
        system_prompt=_SYSTEM_PROMPT,
    ))
    return resp.content if resp else ""


_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert software engineer fixing bugs and CI failures in the ICDEV codebase.
    You work in an agentic loop: you call tools, receive results, then decide next steps.

    Available tools (respond ONLY with valid JSON tool calls):
    - read_file(path): read a source file
    - write_file(path, content): write the entire corrected file
    - run_command(command): run ruff/pytest/git diff to validate
    - done(summary, changed_files): finish when the fix is verified

    Rules:
    1. Always read the file before writing it.
    2. Make minimal targeted changes — do not rewrite unrelated code.
    3. After writing, run `ruff check <path> --select E,F,W --ignore E402,E501,E701,E702,E721,E722,E731,E741,F404`
       to confirm no new lint errors.
    4. Call done() only after validation passes.
    5. If there is nothing to fix (already fixed), call done() immediately.

    Respond with a JSON object:
    {"tool": "<name>", "args": {<args>}}
""")


# ── agentic loop ──────────────────────────────────────────────────────────────

def run_agentic_loop(task_id: str, task_desc: str, task_type: str, max_turns: int = 6) -> list[str]:
    """Run the agentic loop. Returns list of changed file paths."""
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
    except Exception as e:
        print(f"[agentic_runner] LLMRouter unavailable: {e}", flush=True)
        sys.exit(1)

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Task ID: {task_id}\nTask type: {task_type}\n\n"
                f"Task description / error context:\n{task_desc[:6000]}\n\n"
                "Analyze the error, find the root cause, fix it, validate with ruff, then call done()."
            ),
        }
    ]

    changed_files: list[str] = []

    for turn in range(max_turns):
        print(f"\n[agentic_runner] turn {turn + 1}/{max_turns}", flush=True)

        raw = _llm_call(router, messages)
        if not raw:
            print("[agentic_runner] empty LLM response — stopping", flush=True)
            break

        print(f"[agentic_runner] LLM response:\n{raw[:800]}", flush=True)

        # Parse JSON tool call — strip markdown fences if present
        json_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        # Extract first JSON object
        m = re.search(r"\{.*\}", json_text, re.DOTALL)
        if not m:
            print("[agentic_runner] no JSON tool call found — treating as done", flush=True)
            break

        try:
            call = json.loads(m.group())
        except json.JSONDecodeError as e:
            print(f"[agentic_runner] JSON parse error: {e} — stopping", flush=True)
            break

        tool_name = call.get("tool", "")
        tool_args = call.get("args", {})
        print(f"[agentic_runner] calling tool: {tool_name}({list(tool_args.keys())})", flush=True)

        result = _dispatch_tool(tool_name, tool_args)

        if result == "__DONE__":
            changed_files = call.get("args", {}).get("changed_files", [])
            summary = call.get("args", {}).get("summary", "")
            print(f"[agentic_runner] done — {summary}", flush=True)
            print(f"[agentic_runner] changed files: {changed_files}", flush=True)
            break

        if tool_name == "write_file":
            p = tool_args.get("path", "")
            if p and p not in changed_files:
                changed_files.append(p)

        # Feed result back into message history
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": f"Tool result:\n{result}"})

    return changed_files


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agentic LLM task runner for GA")
    parser.add_argument("--task-id",   required=True)
    parser.add_argument("--task-desc", required=True)
    parser.add_argument("--task-type", default="fix")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    changed = run_agentic_loop(
        task_id=args.task_id,
        task_desc=args.task_desc,
        task_type=args.task_type,
        max_turns=args.max_turns,
    )

    if args.json:
        print(json.dumps({"changed_files": changed, "task_id": args.task_id}))

    sys.exit(0)


if __name__ == "__main__":
    main()
