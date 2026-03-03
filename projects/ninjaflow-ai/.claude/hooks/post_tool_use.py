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


def dispatch_extension_hook(tool_name: str, tool_input: dict, tool_output: str):
    """Best-effort dispatch of TOOL_EXECUTE_AFTER extension point (Phase 44 Feature 2)."""
    try:
        from tools.extensions.extension_manager import extension_manager, ExtensionPoint
        extension_manager.dispatch(
            ExtensionPoint.TOOL_EXECUTE_AFTER,
            context_id=f"hook_{tool_name}",
            data={
                "tool_name": tool_name,
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

        session_id = get_session_id()
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
