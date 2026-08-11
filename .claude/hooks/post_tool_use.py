# [TEMPLATE: CUI // SP-CTI]
"""Post-tool-use hook — logs tool results + dispatches extension hooks. Always exits 0."""

import json
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

        store_event(
            session_id=session_id,
            hook_type="post_tool_use",
            tool_name=tool_name,
            payload={
                "tool_input_keys": list(tool_input.keys()) if isinstance(tool_input, dict) else [],
                "output_length": len(str(tool_output)) if tool_output else 0,
                "output_summary": output_summary,
            },
        )

        # Dispatch Phase 44 extension hook (TOOL_EXECUTE_AFTER)
        dispatch_extension_hook(tool_name, tool_input, tool_output)

    except Exception:
        pass  # Never block tool execution

    sys.exit(0)


if __name__ == "__main__":
    main()
