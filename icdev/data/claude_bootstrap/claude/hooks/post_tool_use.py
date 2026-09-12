# [TEMPLATE: CUI // SP-CTI]
"""Post-tool-use hook — logs tool results + dispatches extension hooks. Always exits 0."""

import json
import os
import sys
from pathlib import Path

# Add hooks dir + project root to path for send_event and tools imports
HOOKS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOOKS_DIR.parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Tools the awareness subscriber actually handles — must stay in sync with
# _TRACKED_TOOLS in tools/awareness/hooks.py.
_AWARENESS_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})

# Tools an observation can be derived from (xrv-mem-02). Kept as a literal
# here so a Read/Grep/WebFetch call never pays the library import; the
# authoritative sets are observation_capture.EDIT_TOOLS / SHELL_TOOLS and a
# test asserts this literal equals their union.
_CAPTURE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit", "Bash", "PowerShell"})


# ── kpr-watch-19: the mid-run resume inbox ──────────────────────────────────
#
# The queue at `.tmp/kanban/messages/<task>.jsonl` had exactly one consumer --
# the text-only LLMRouter executor loop -- while 99.73% of dispatched tasks run
# `claude_cli`, which never looked. 911 undrained pr_watcher messages, 0 drain
# receipts, measured 2026-09-12. This hook is the consumer on the path that
# actually runs, and PostToolUse is the mid-run window: a message queued while
# the session is alive is delivered within one tool call.
#
# THE PATH IS RE-SPELLED HERE WITH STDLIB ONLY, ON PURPOSE. This fires on every
# tool call and `import tools.*` costs ~137ms (measured 2026-09-12: 40ms bare
# interpreter, 177ms with the `tools` package shim), which the overwhelmingly
# common answer -- "no file, nothing to do" -- cannot justify paying. So the
# existence check is one env read and one stat, and the authority
# (`tools.airgap.hook_compat.message_queue_dir`) is imported only once there is
# something to deliver. The cost of that bargain is a second spelling of the
# path; the mitigation is `tests/ci/test_resume_inbox.py`, which asserts the two
# resolve to the same directory -- the same bargain `_CAPTURE_TOOLS` above
# already strikes with observation_capture.


def _main_checkout(anchor: Path):
    """The main worktree's root, read from the anchor's `.git` -- no subprocess.

    A linked worktree's `.git` is a FILE reading `gitdir: <main>/.git/worktrees/
    <name>`. Mirrors tools/hooks/shared_checks.py::main_checkout. Returns None
    for a plain checkout, where the anchor already IS the main checkout.
    """
    try:
        dot_git = anchor / ".git"
        if not dot_git.is_file():
            return None
        text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(text.split(":", 1)[1].strip()).expanduser()
        if not gitdir.is_absolute():
            gitdir = anchor / gitdir
        for parent in gitdir.resolve().parents:
            if parent.name == ".git":
                return parent.parent
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _queue_dir(anchor: Path) -> Path:
    """Where this anchor's task messages wait -- the MAIN checkout's queue.

    pr_watcher enqueues from the main checkout and a dispatched worker reads
    from its worktree; resolving this per-checkout is why they never met.
    """
    override = (os.environ.get("ICDEV_MESSAGE_QUEUE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    try:
        anchor = anchor.resolve()
    except (OSError, ValueError, RuntimeError):
        pass
    return (_main_checkout(anchor) or anchor) / ".tmp" / "kanban" / "messages"


def _queue_dir_for_cwd() -> Path:
    return _queue_dir(PROJECT_ROOT)


def _queue_file(task_id: str, anchor=None) -> Path:
    base = _queue_dir_for_cwd() if anchor is None else _queue_dir(Path(anchor))
    return base / "{}.jsonl".format(task_id)


def drain_resume_inbox() -> str:
    """Deliver anything queued for the running task. Returns "" for nothing.

    Never raises: an inbox failure must not disturb the tool call it rides on.
    """
    try:
        task_id = (os.environ.get("ICDEV_DISPATCH_TASK_ID") or "").strip()
        if not task_id:
            return ""
        # The fast path ends here for every session that has no mail: one stat.
        if not _queue_file(task_id).exists():
            return ""
        from tools.hooks.resume_inbox import deliver

        return deliver(task_id, source="post_tool_use").text or ""
    except Exception:
        return ""


def capture_memory_observation(tool_name: str, tool_input, tool_response, session_id: str):
    """Deterministic memory capture into the auto_capture buffer (xrv-mem-02).

    Edit/Write -> ``edited <relpath>``; Bash ``git commit`` -> the commit
    subject git confirmed; Bash pytest -> pytest's own summary line. No model
    call. ``<private>...</private>`` spans are removed BEFORE capture and a
    wholly private observation is skipped; the per-session cap
    (``memory_config.yaml: auto_capture.max_per_session``) skips silently.
    Returns the capture verdict dict (recorded on the hook_events payload) or
    None when the event yields no observation. Never raises.
    """
    if tool_name not in _CAPTURE_TOOLS:
        return None
    try:
        from tools.hooks.observation_capture import capture_tool_event

        return capture_tool_event(tool_name, tool_input, tool_response, session_id)
    except Exception:
        return None  # a context capture never fails a tool call


def dispatch_extension_hook(tool_name: str, tool_input: dict, tool_output: str):
    """Best-effort dispatch of TOOL_EXECUTE_AFTER extension point (Phase 44 Feature 2).

    Fixed 2026-04-11 (Phase 1e): the prior call used keyword args
    ``context_id=``/``data=`` that do not match the dispatch signature
    ``dispatch(hook_point, context: dict)``, so the call silently
    raised TypeError and no handlers ever fired. Now passes a proper
    context dict including the full tool_input so subscribers (like
    the awareness component indexer) can extract file paths.
    """
    # Only import the awareness subscriber for the tools it actually handles.
    # ``tools/awareness/hooks.py`` filters on _TRACKED_TOOLS = {Edit, Write,
    # NotebookEdit, MultiEdit}, so importing it for a Read or a Bash call paid
    # ~90 ms of import cost — on every tool call — to register a handler that
    # would immediately filter the event out.
    if tool_name in _AWARENESS_TOOLS:
        try:
            import tools.awareness.hooks  # noqa: F401  — registers subscriber on first import
        except Exception:
            pass  # Awareness hook optional

    try:
        from tools.extensions.extension_manager import extension_manager, ExtensionPoint
        # Fire-and-forget: observational hooks (like the awareness
        # component indexer) run in a background daemon thread so
        # tool execution is NEVER blocked waiting for them. The
        # hook has a 30ms target but a 150ms p50 in practice —
        # moving to async dispatch makes that invisible to users.
        extension_manager.dispatch_async(
            ExtensionPoint.TOOL_EXECUTE_AFTER,
            {
                "tool_name": tool_name,
                "tool_input": tool_input if isinstance(tool_input, dict) else {},
                "tool_input_keys": list(tool_input.keys()) if isinstance(tool_input, dict) else [],
                "output_length": len(str(tool_output)) if tool_output else 0,
            },
        )
    except (ImportError, AttributeError):
        pass  # Extension manager not available — skip silently
    except Exception:
        pass  # Never block tool execution


def main():
    try:
        input_data = json.load(sys.stdin)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        # Claude Code sends the tool's result as ``tool_response`` (a dict with
        # stdout/stderr for Bash); ``tool_output`` is the legacy name this hook
        # read and it is NEVER populated (0 of the last 200 Bash rows carried an
        # output, measured 2026-09-11). Both are read; the first wins.
        tool_output = input_data.get("tool_response")
        if tool_output is None:
            tool_output = input_data.get("tool_output", "")

        # Import here to avoid issues if DB doesn't exist yet
        from send_event import get_session_id, store_event

        # The session id Claude Code passes on stdin wins. get_session_id()
        # falls back to a fresh uuid4 when CLAUDE_SESSION_ID is unset, and this
        # hook runs as a new interpreter per tool call — so calling it directly
        # minted a NEW session for every event. Measured 2026-08-11: 9,803 of
        # 9,816 sessions in hook_events held exactly one event, which silently
        # disables anything keyed on a session (AGOV sequence rules need >=2
        # events in one session to fire; the CASE timeline/bundle is per
        # session). Same `payload or get_session_id()` order stop.py,
        # subagent_stop.py, pre_compact.py and user_prompt_submit.py already use.
        session_id = input_data.get("session_id") or get_session_id()
        # Truncate large outputs to prevent DB bloat
        output_summary = str(tool_output)[:2000] if tool_output else ""

        # xrv-mem-02: capture BEFORE the event row so the row can carry the
        # capture verdict (kind + status), which is what makes the capture
        # measurable from hook_events afterwards. Never the content.
        memory_capture = capture_memory_observation(tool_name, tool_input, tool_output, session_id)

        payload = {
            "tool_input_keys": list(tool_input.keys()) if isinstance(tool_input, dict) else [],
            "output_length": len(str(tool_output)) if tool_output else 0,
            "output_summary": output_summary,
        }
        if memory_capture:
            payload["memory_capture"] = {
                "kind": memory_capture.get("kind"),
                "status": memory_capture.get("status"),
                "private_stripped": memory_capture.get("private_stripped", False),
            }

        store_event(
            session_id=session_id,
            hook_type="post_tool_use",
            tool_name=tool_name,
            payload=payload,
        )

        # Dispatch Phase 44 extension hook (TOOL_EXECUTE_AFTER)
        dispatch_extension_hook(tool_name, tool_input, tool_output)

    except Exception:
        pass  # Never block tool execution

    # kpr-watch-19: deliver any message queued for this task mid-run. Outside
    # the try above so a logging failure cannot swallow the delivery -- the
    # words reaching the model are the point, the hook_events row is not.
    # Verified on this deployment 2026-09-12 (live session kpr-watch-19) that
    # PostToolUse additionalContext reaches the model, before it was wired.
    inbox = drain_resume_inbox()
    if inbox:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": inbox}}))

    sys.exit(0)


if __name__ == "__main__":
    main()
