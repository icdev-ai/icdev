# CUI // SP-CTI
"""Notification hook — logs user notifications/interactions. Always exits 0."""

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOOKS_DIR.parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    try:
        input_data = json.load(sys.stdin)
        message = input_data.get("message", "")

        from send_event import get_session_id, store_event

        store_event(
            session_id=get_session_id(),
            hook_type="notification",
            payload={"message": str(message)[:2000]},
        )
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
