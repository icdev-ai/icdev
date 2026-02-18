# CUI // SP-CTI
"""Subagent stop hook — logs delegated task completion. Always exits 0."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    try:
        input_data = json.load(sys.stdin)
        subagent_id = input_data.get("subagent_id", "")
        result = input_data.get("result", "")

        from send_event import get_session_id, store_event

        store_event(
            session_id=get_session_id(),
            hook_type="subagent_stop",
            payload={
                "subagent_id": subagent_id,
                "result_summary": str(result)[:2000],
            },
        )
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
