# CUI // SP-CTI
"""
Session stop hook — captures session completion event and saves chat transcript.

Features:
    - Stores stop event in DB via send_event
    - Captures full session transcript from .jsonl to .tmp/sessions/{session_id}/chat.json

Always exits 0.
"""

import json
import os
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOOKS_DIR.parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SESSION_DIR = PROJECT_ROOT / ".tmp" / "sessions"


def capture_transcript(session_id: str, input_data: dict):
    """Capture the session transcript from .jsonl to session directory."""
    transcript_path = input_data.get("transcript_path", "")
    if not transcript_path or not os.path.exists(transcript_path):
        return

    try:
        session_dir = SESSION_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        chat_data = []
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        chat_data.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # Skip malformed lines

        chat_file = session_dir / "chat.json"
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(chat_data, f, indent=2)

    except Exception:
        pass  # Never fail the stop hook


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
                "transcript_captured": "transcript_path" in input_data,
            },
        )

        # Always attempt transcript capture
        if sid:
            capture_transcript(sid, input_data)

    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
