# [TEMPLATE: CUI // SP-CTI]
# ICDEV Conversation Manager — conversational CI/CD sessions (D135)

"""
Conversational feedback loop for CI/CD sessions.

Adapted from tools/requirements/intake_engine.py session pattern:
developers comment on issues/MRs or chat in Slack/Mattermost, and the
agent responds, iterates on code, addresses review comments.

Architecture Decisions:
    D135: Conversational CI/CD sessions adapt intake_engine.py DB-backed
          conversation pattern
    D137: Slack/Mattermost responses always use threads

Usage:
    from tools.ci.core.conversation_manager import ConversationManager
    mgr = ConversationManager()
    session = mgr.create_session("42", "run-abc", "github", 42)
    result = mgr.process_comment(session["id"], "fix this", "dev1")
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "icdev.db"

# Command signal mapping — keyword matching for quick commands
COMMAND_SIGNALS = {
    "fix this": "fix_code",
    "fix it": "fix_code",
    "change approach": "revise_plan",
    "change the approach": "revise_plan",
    "retry": "retry_last",
    "try again": "retry_last",
    "approve": "approve",
    "lgtm": "approve",
    "looks good": "approve",
    "reject": "reject",
    "explain": "explain",
    "explain this": "explain",
    "skip": "skip_phase",
    "skip phase": "skip_phase",
}


class ConversationManager:
    """Manages conversational CI/CD sessions."""

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DB_PATH)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create conversation tables if not exist."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ci_conversations (
                    id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    issue_number INTEGER,
                    channel_id TEXT,
                    thread_ts TEXT,
                    status TEXT CHECK(status IN (
                        'active', 'paused', 'completed', 'abandoned'
                    )),
                    total_turns INTEGER DEFAULT 0,
                    last_agent_action TEXT,
                    classification TEXT DEFAULT 'CUI',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_session
                ON ci_conversations(session_key, status)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ci_conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES ci_conversations(id),
                    turn_number INTEGER NOT NULL,
                    role TEXT CHECK(role IN ('developer', 'agent', 'system')),
                    content TEXT NOT NULL,
                    content_type TEXT CHECK(content_type IN (
                        'text', 'command', 'code_change', 'test_result',
                        'approval', 'rejection', 'status_update', 'error'
                    )),
                    action_taken TEXT,
                    comment_id TEXT,
                    metadata TEXT,
                    classification TEXT DEFAULT 'CUI',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turns_session
                ON ci_conversation_turns(session_id, turn_number)
            """)
            conn.commit()
            conn.close()
        except Exception:
            pass

    def create_session(
        self,
        session_key: str,
        run_id: str,
        platform: str,
        issue_number: int = None,
        channel_id: str = "",
        thread_ts: str = "",
    ) -> dict:
        """Create a new conversation session."""
        session_id = str(uuid.uuid4())[:12]

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO ci_conversations "
                "(id, session_key, run_id, platform, issue_number, "
                "channel_id, thread_ts, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
                (session_id, session_key, run_id, platform,
                 issue_number, channel_id, thread_ts),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Failed to create conversation session: {e}")

        return {
            "id": session_id,
            "session_key": session_key,
            "run_id": run_id,
            "platform": platform,
            "status": "active",
        }

    def get_active_session(self, session_key: str) -> Optional[dict]:
        """Get active conversation session for a session key."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT id, session_key, run_id, platform, issue_number, "
                "status, total_turns, last_agent_action "
                "FROM ci_conversations "
                "WHERE session_key = ? AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 1",
                (session_key,),
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    "id": row[0],
                    "session_key": row[1],
                    "run_id": row[2],
                    "platform": row[3],
                    "issue_number": row[4],
                    "status": row[5],
                    "total_turns": row[6],
                    "last_agent_action": row[7],
                }
        except Exception:
            pass
        return None

    def process_comment(
        self,
        session_id: str,
        comment_body: str,
        author: str,
        comment_id: str = "",
    ) -> dict:
        """Process a developer comment in an active conversation.

        Returns:
            {
                "action": str,  # fix_code, revise_plan, approve, etc.
                "response": str,  # Agent response text
                "files_changed": list,
                "status": str,
            }
        """
        # 1. Check for duplicate (dedup by comment_id)
        if comment_id and self._is_duplicate(session_id, comment_id):
            return {"action": "duplicate", "response": "", "files_changed": [], "status": "skipped"}

        # 2. Log developer turn
        turn_number = self._next_turn_number(session_id)
        signal = self._detect_signal(comment_body)
        content_type = "command" if signal else "text"

        self._log_turn(
            session_id, turn_number, "developer", comment_body,
            content_type, comment_id=comment_id,
        )

        # 3. Route to handler based on signal
        result = {
            "action": signal or "conversational",
            "response": "",
            "files_changed": [],
            "status": "processed",
        }

        if signal == "fix_code":
            result = self._handle_fix_code(session_id, comment_body, turn_number)
        elif signal == "revise_plan":
            result = self._handle_revise_plan(session_id, comment_body, turn_number)
        elif signal == "retry_last":
            result = self._handle_retry(session_id, turn_number)
        elif signal == "approve":
            result = self._handle_approve(session_id, turn_number)
        elif signal == "reject":
            result = self._handle_reject(session_id, turn_number)
        elif signal == "explain":
            result = self._handle_explain(session_id, comment_body, turn_number)
        elif signal == "skip_phase":
            result = self._handle_skip(session_id, turn_number)
        else:
            result = self._handle_conversational(session_id, comment_body, turn_number)

        # 4. Log agent response turn
        if result.get("response"):
            self._log_turn(
                session_id, turn_number + 1, "agent", result["response"],
                "text", action_taken=result.get("action"),
                metadata=json.dumps({"files_changed": result.get("files_changed", [])}),
            )

        # 5. Update session
        self._update_session(session_id, result.get("action", ""), turn_number + 1)

        return result

    def get_session_context(self, session_id: str, max_turns: int = 10) -> dict:
        """Get recent conversation context for agent prompt building."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT turn_number, role, content, content_type, action_taken "
                "FROM ci_conversation_turns "
                "WHERE session_id = ? ORDER BY turn_number DESC LIMIT ?",
                (session_id, max_turns),
            )
            turns = [
                {
                    "turn": row[0],
                    "role": row[1],
                    "content": row[2],
                    "type": row[3],
                    "action": row[4],
                }
                for row in cursor.fetchall()
            ]
            conn.close()

            # Reverse to chronological order
            turns.reverse()

            return {
                "session_id": session_id,
                "turns": turns,
                "turn_count": len(turns),
            }
        except Exception:
            return {"session_id": session_id, "turns": [], "turn_count": 0}

    def close_session(self, session_id: str, reason: str = "completed") -> dict:
        """Close a conversation session."""
        try:
            conn = sqlite3.connect(self.db_path)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE ci_conversations SET status = ?, updated_at = ? "
                "WHERE id = ?",
                (reason, now, session_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {"session_id": session_id, "status": reason}

    # ── Signal Detection ──────────────────────────────────────────────

    def _detect_signal(self, text: str) -> str:
        """Detect command signal from comment text."""
        text_lower = text.lower().strip()
        for keyword, signal in COMMAND_SIGNALS.items():
            if text_lower == keyword or text_lower.startswith(keyword + " "):
                return signal
        return ""

    # ── Handlers ──────────────────────────────────────────────────────

    def _handle_fix_code(self, session_id: str, comment: str, turn: int) -> dict:
        """Handle 'fix this' / 'fix it' command — invoke builder agent."""
        context = self.get_session_context(session_id)
        self._get_session(session_id)

        response_text = (
            f"Acknowledged fix request. Analyzing the issue and generating a fix...\n"
            f"Context: {len(context['turns'])} conversation turns."
        )

        # In full implementation: invoke builder agent with failure context,
        # auto-commit fix, push to branch. For now, return acknowledgment.
        return {
            "action": "fix_code",
            "response": response_text,
            "files_changed": [],
            "status": "acknowledged",
        }

    def _handle_revise_plan(self, session_id: str, comment: str, turn: int) -> dict:
        """Handle 'change approach' — invoke planner with new direction."""
        return {
            "action": "revise_plan",
            "response": "Acknowledged. Revising the plan with the new direction...",
            "files_changed": [],
            "status": "acknowledged",
        }

    def _handle_retry(self, session_id: str, turn: int) -> dict:
        """Handle 'retry' — re-run last failed phase."""
        return {
            "action": "retry_last",
            "response": "Retrying the last failed phase...",
            "files_changed": [],
            "status": "acknowledged",
        }

    def _handle_approve(self, session_id: str, turn: int) -> dict:
        """Handle 'approve' / 'lgtm' — mark pipeline as approved."""
        self.close_session(session_id, "completed")
        return {
            "action": "approve",
            "response": "Approved. Marking pipeline as completed.",
            "files_changed": [],
            "status": "completed",
        }

    def _handle_reject(self, session_id: str, turn: int) -> dict:
        """Handle 'reject' — pause pipeline for human review."""
        self.close_session(session_id, "paused")
        return {
            "action": "reject",
            "response": "Pipeline paused. Awaiting further instructions.",
            "files_changed": [],
            "status": "paused",
        }

    def _handle_explain(self, session_id: str, comment: str, turn: int) -> dict:
        """Handle 'explain' — generate explanation of recent changes."""
        context = self.get_session_context(session_id)
        return {
            "action": "explain",
            "response": f"Here's a summary of the {context['turn_count']} conversation turns so far...",
            "files_changed": [],
            "status": "processed",
        }

    def _handle_skip(self, session_id: str, turn: int) -> dict:
        """Handle 'skip' — skip current phase and continue."""
        return {
            "action": "skip_phase",
            "response": "Skipping current phase and continuing pipeline...",
            "files_changed": [],
            "status": "processed",
        }

    def _handle_conversational(self, session_id: str, comment: str, turn: int) -> dict:
        """Handle general conversational comment — forward to agent for response."""
        return {
            "action": "conversational",
            "response": "Received your message. Processing...",
            "files_changed": [],
            "status": "processed",
        }

    # ── DB Helpers ────────────────────────────────────────────────────

    def _is_duplicate(self, session_id: str, comment_id: str) -> bool:
        """Check if comment_id already processed (dedup)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT id FROM ci_conversation_turns "
                "WHERE session_id = ? AND comment_id = ?",
                (session_id, comment_id),
            )
            row = cursor.fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def _next_turn_number(self, session_id: str) -> int:
        """Get next turn number for a session."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT MAX(turn_number) FROM ci_conversation_turns "
                "WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            conn.close()
            return (row[0] or 0) + 1
        except Exception:
            return 1

    def _log_turn(
        self, session_id: str, turn_number: int, role: str,
        content: str, content_type: str, action_taken: str = None,
        comment_id: str = None, metadata: str = None,
    ):
        """Log a conversation turn (append-only)."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO ci_conversation_turns "
                "(session_id, turn_number, role, content, content_type, "
                "action_taken, comment_id, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, turn_number, role, content, content_type,
                 action_taken, comment_id, metadata),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _update_session(self, session_id: str, last_action: str, turn_count: int):
        """Update session metadata."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE ci_conversations SET total_turns = ?, "
                "last_agent_action = ?, updated_at = ? WHERE id = ?",
                (turn_count, last_action, now, session_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _get_session(self, session_id: str) -> Optional[dict]:
        """Get session by ID."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT id, session_key, run_id, platform, issue_number, status "
                "FROM ci_conversations WHERE id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "id": row[0], "session_key": row[1], "run_id": row[2],
                    "platform": row[3], "issue_number": row[4], "status": row[5],
                }
        except Exception:
            pass
        return None
