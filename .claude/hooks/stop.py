# CUI // SP-CTI
"""Session stop hook — captures session completion event. Always exits 0."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    try:
        input_data = json.load(sys.stdin)
        session_id = input_data.get("session_id", "")
        stop_reason = input_data.get("reason", "unknown")

        from send_event import get_session_id, store_event

        sid = session_id or get_session_id()
        store_event(
            session_id=sid,
            hook_type="stop",
            payload={
                "stop_reason": stop_reason,
                "session_id": sid,
            },
        )
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
