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
        p.write_text(content, encoding="utf-8", newline="")
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

def _llm_call(router, messages: list[dict], mode: str = "off") -> str:
    """One agentic reasoning turn, routed through reasoned_codegen.

    ``mode`` ("off"|"cot"|"cod") is resolved once per run by ``_resolve_reasoned_mode``
    (the --reasoned option + advisor). No per-turn verifier/critique is attached:
    the artifact here is a tool-call decision, and the loop self-validates with
    ruff/pytest via ``run_command``. When mode is "off" the wrapper is a
    byte-identical passthrough to ``router.invoke("code_generation", ...)``.
    """
    from tools.llm.provider import LLMRequest
    from tools.llm.reasoned_codegen import generate_reasoned_code

    request = LLMRequest(messages=messages, system_prompt=_SYSTEM_PROMPT)
    result = generate_reasoned_code(
        function="code_generation", request=request, router=router, mode=mode,
    )
    return result.code if result else ""


def _resolve_reasoned_mode(router, reasoned: str, task_type: str, task_desc: str) -> str:
    """Resolve the per-run reasoning mode from the --reasoned option.

    - "off"  → off (force).
    - "on"   → enable; advisor picks cot/cod (never off — user forced on).
    - "auto" → advisor decides (may be off).

    The section-level kill-switch (reasoned_codegen.enabled:false) always wins.
    """
    from tools.llm.reasoned_codegen import MODE_OFF, MODE_COT, section_enabled

    if not section_enabled(router):
        print("[agentic_runner] reasoned codegen disabled by config kill-switch", flush=True)
        return MODE_OFF
    if reasoned == "off":
        return MODE_OFF
    if reasoned not in ("on", "auto"):
        return MODE_OFF

    try:
        from tools.llm.reasoned_codegen_advisor import recommend

        rec = recommend(
            "code_generation", task_desc,
            context={"task_type": task_type}, router=router,
        )
        mode = rec.get("mode", MODE_OFF)
        if reasoned == "on" and mode == MODE_OFF:
            mode = MODE_COT  # user explicitly forced enable
        print(f"[agentic_runner] reasoned={reasoned} → mode={mode} "
              f"(advisor: {rec.get('rationale', '')})", flush=True)
        return mode
    except Exception as e:
        # Advisor failure must not break codegen; fall back per option.
        print(f"[agentic_runner] advisor unavailable ({e})", flush=True)
        return MODE_COT if reasoned == "on" else MODE_OFF


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

def run_agentic_loop(task_id: str, task_desc: str, task_type: str, max_turns: int = 6,
                     reasoned: str = "auto") -> list[str]:
    """Run the agentic loop. Returns list of changed file paths.

    ``reasoned`` ("auto"|"on"|"off") controls reasoned codegen: ``auto`` (default)
    asks the advisor whether CoT/CoD pays off for this task; ``on``/``off`` force it.
    """
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
    except Exception as e:
        print(f"[agentic_runner] LLMRouter unavailable: {e}", flush=True)
        sys.exit(1)

    reasoned_mode = _resolve_reasoned_mode(router, reasoned, task_type, task_desc)

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

        raw = _llm_call(router, messages, mode=reasoned_mode)
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
    parser.add_argument(
        "--reasoned", choices=["auto", "on", "off"], default="auto",
        help="Reasoned codegen (CoT/CoD): auto=advisor decides, on/off=force. "
             "Section kill-switch in args/llm_config.yaml always wins.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    changed = run_agentic_loop(
        task_id=args.task_id,
        task_desc=args.task_desc,
        task_type=args.task_type,
        max_turns=args.max_turns,
        reasoned=args.reasoned,
    )

    if args.json:
        print(json.dumps({"changed_files": changed, "task_id": args.task_id}))

    sys.exit(0)


if __name__ == "__main__":
    main()
