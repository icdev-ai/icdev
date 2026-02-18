# CUI // SP-CTI
"""Post-tool-use hook — logs tool results to SQLite for observability. Always exits 0."""

import json
import sys
from pathlib import Path

# Add project root to path for send_event import
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


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
    except Exception:
        pass  # Never block tool execution

    sys.exit(0)


if __name__ == "__main__":
    main()
