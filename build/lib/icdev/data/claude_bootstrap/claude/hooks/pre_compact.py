# [TEMPLATE: CUI // SP-CTI]
"""
PreCompact hook — logs context compaction events for session debugging.

Tracks when Claude's context window is compressed, useful for:
- Understanding session behavior in long workflows
- Debugging when Claude loses context mid-session
- Audit completeness (knowing what was compressed away)

Always exits 0.
"""

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
        session_id = input_data.get("session_id", "")

        from send_event import get_session_id, store_event

        sid = session_id or get_session_id()
        store_event(
            session_id=sid,
            hook_type="pre_compact",
            payload={
                "session_id": sid,
                "summary_hint": input_data.get("summary", ""),
            },
        )
    except Exception:
        pass  # Never block compaction

    sys.exit(0)


if __name__ == "__main__":
    main()
