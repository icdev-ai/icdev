# CUI // SP-CTI
"""
Subagent stop hook — logs delegated task completion and captures subagent transcript.

Features:
    - Stores subagent stop event in DB via send_event
    - Captures subagent transcript to .tmp/sessions/{session_id}/subagent_{id}_chat.json

Always exits 0.
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SESSION_DIR = PROJECT_ROOT / ".tmp" / "sessions"


def capture_transcript(session_id: str, subagent_id: str, input_data: dict):
    """Capture the subagent transcript from .jsonl to session directory."""
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
                        pass

        # Use subagent_id in filename to distinguish multiple subagents
        safe_id = (subagent_id or "unknown").replace("/", "_")[:50]
        chat_file = session_dir / f"subagent_{safe_id}_chat.json"
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(chat_data, f, indent=2)

    except Exception:
        pass


def main():
    try:
        input_data = json.load(sys.stdin)
        session_id = input_data.get("session_id", "")
        subagent_id = input_data.get("subagent_id", "")
        result = input_data.get("result", "")

        from send_event import get_session_id, store_event

        sid = session_id or get_session_id()
        store_event(
            session_id=sid,
            hook_type="subagent_stop",
            payload={
                "subagent_id": subagent_id,
                "result_summary": str(result)[:2000],
                "transcript_captured": "transcript_path" in input_data,
            },
        )

        # Always attempt transcript capture
        if sid:
            capture_transcript(sid, subagent_id, input_data)

    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
