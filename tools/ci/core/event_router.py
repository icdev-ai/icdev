# CUI // SP-CTI
# ICDEV Event Router — central routing with lane-aware session queue (D133)

"""
Central event router for all CI/CD trigger sources.

Receives EventEnvelope objects and dispatches to the correct workflow.
Implements lane-aware session queue: one active agent run per session_key,
with queuing for subsequent events.

Architecture Decisions:
    D132: Unified EventEnvelope normalization
    D133: Lane-aware session queue — one active run per issue/MR

Usage:
    from tools.ci.core.event_router import EventRouter
    from tools.ci.core.event_envelope import EventEnvelope

    router = EventRouter()
    envelope = EventEnvelope.from_github_webhook(payload, "issues")
    result = router.route(envelope)
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.ci.core.event_envelope import EventEnvelope

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "icdev.db"

# Valid workflows (mirrors workflow_ops.AVAILABLE_ICDEV_WORKFLOWS)
AVAILABLE_WORKFLOWS = {
    "icdev_plan", "icdev_build", "icdev_test", "icdev_review",
    "icdev_comply", "icdev_secure", "icdev_deploy", "icdev_document",
    "icdev_patch", "icdev_plan_build", "icdev_plan_build_test",
    "icdev_plan_build_test_review", "icdev_sdlc",
    "icdev_intake", "icdev_modernize", "icdev_maintain",
}

# Workflows that require a prior run_id
REQUIRE_RUN_ID = {"icdev_build", "icdev_review"}


def _load_routing_config() -> dict:
    """Load routing config from cicd_config.yaml."""
    import yaml
    config_path = PROJECT_ROOT / "args" / "cicd_config.yaml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            return data.get("cicd", {}).get("routing", {})
        except Exception:
            pass
    return {}


class EventRouter:
    """Routes EventEnvelopes to workflow scripts or conversation handler."""

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DB_PATH)
        self._config = _load_routing_config()
        self._ensure_tables()

    def _ensure_tables(self):
        """Create ci_pipeline_runs table if not exists."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ci_pipeline_runs (
                    id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    workflow TEXT NOT NULL,
                    status TEXT CHECK(status IN (
                        'queued', 'running', 'completed', 'failed', 'recovering'
                    )),
                    trigger_source TEXT,
                    event_id TEXT,
                    classification TEXT DEFAULT 'CUI',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pipeline_session
                ON ci_pipeline_runs(session_key, status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pipeline_run
                ON ci_pipeline_runs(run_id)
            """)
            # Event queue for lane-aware processing
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ci_event_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    status TEXT CHECK(status IN (
                        'queued', 'processing', 'processed', 'dropped'
                    )),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_session
                ON ci_event_queue(session_key, status)
            """)
            conn.commit()
            conn.close()
        except Exception:
            pass  # DB may not exist yet in test environments

    def route(self, envelope: EventEnvelope) -> dict:
        """Route an event envelope to the appropriate handler.

        Returns:
            {
                "action": "launched" | "queued" | "ignored",
                "workflow": str,
                "run_id": str,
                "reason": str,
            }
        """
        # 1. Drop bot messages
        if envelope.is_bot:
            return {"action": "ignored", "reason": "bot_message"}

        # 2. Validate workflow command
        workflow = envelope.workflow_command
        if workflow and workflow not in AVAILABLE_WORKFLOWS:
            return {"action": "ignored", "reason": f"unknown_workflow: {workflow}"}

        # 3. Check run_id constraints
        if workflow in REQUIRE_RUN_ID and not envelope.run_id:
            return {
                "action": "ignored",
                "reason": f"{workflow} requires run_id",
            }

        # 4. Check lane-aware queue: is there an active run for this session?
        active_run = self._get_active_run(envelope.session_key)
        if active_run:
            if not workflow:
                # No command — route to conversational handler (D135)
                conv_result = self._route_to_conversation(envelope, active_run)
                if conv_result:
                    return conv_result
                # Fall through to queue if conversation handler not available
                return self._queue_followup(envelope, active_run)
            else:
                # New command while pipeline running — queue it
                return self._queue_followup(envelope, active_run)

        # 5. If no workflow detected and no active session, check for default
        if not workflow:
            # Check if content contains "icdev" keyword (generic trigger)
            if "icdev" in envelope.content.lower():
                workflow = self._config.get("default_workflow", "icdev_plan")
            else:
                return {"action": "ignored", "reason": "no_workflow_detected"}

        # 6. Generate or use provided run_id
        from tools.testing.utils import make_run_id
        run_id = envelope.run_id or make_run_id()

        # 7. Create pipeline run record
        self._create_pipeline_run(envelope, workflow, run_id)

        # 8. Create/update state
        from tools.ci.modules.state import ICDevState
        state = ICDevState.load(run_id)
        state.update(
            run_id=run_id,
            issue_number=envelope.session_key,
            platform=envelope.platform,
            project_id=envelope.metadata.get("project_id", ""),
        )
        state.save("event_router")

        # 9. Launch workflow
        self._launch_workflow(workflow, envelope.session_key, run_id, envelope.platform)

        return {
            "action": "launched",
            "workflow": workflow,
            "run_id": run_id,
            "session_key": envelope.session_key,
            "reason": f"Triggered {workflow} from {envelope.source}",
        }

    def _get_active_run(self, session_key: str) -> Optional[str]:
        """Check for active pipeline run on this session."""
        if not session_key:
            return None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT run_id FROM ci_pipeline_runs "
                "WHERE session_key = ? AND status IN ('running', 'recovering') "
                "ORDER BY created_at DESC LIMIT 1",
                (session_key,),
            )
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def _route_to_conversation(
        self, envelope: EventEnvelope, active_run_id: str
    ) -> Optional[dict]:
        """Route a non-command comment to ConversationManager (D135).

        Returns result dict if handled, None to fall through to queue.
        """
        # Only handle comment/message event types
        if envelope.event_type not in (
            "issue_comment", "mr_comment", "chat_message",
        ):
            return None

        try:
            from tools.ci.core.conversation_manager import ConversationManager
            from tools.ci.core.comment_handler import CommentHandler

            mgr = ConversationManager(db_path=self.db_path)

            # Find or create conversation session for this run
            session = mgr.get_active_session(envelope.session_key)
            if not session:
                session = mgr.create_session(
                    session_key=envelope.session_key,
                    run_id=active_run_id,
                    platform=envelope.platform,
                    issue_number=(
                        int(envelope.session_key)
                        if envelope.session_key.isdigit() else None
                    ),
                    channel_id=envelope.metadata.get("channel_id", ""),
                    thread_ts=envelope.metadata.get(
                        "thread_ts",
                        envelope.metadata.get("root_id", ""),
                    ),
                )

            # Process the comment
            comment_id = envelope.metadata.get("comment_id", envelope.event_id)
            result = mgr.process_comment(
                session_id=session["id"],
                comment_body=envelope.content,
                author=envelope.author,
                comment_id=comment_id,
            )

            # Post agent response back to platform
            if result.get("response"):
                handler = CommentHandler()
                handler.post_response(envelope, result["response"])

            return {
                "action": "conversation",
                "session_id": session["id"],
                "signal": result.get("action", "conversational"),
                "active_run_id": active_run_id,
                "reason": f"Routed to conversation ({result.get('action', 'conversational')})",
            }

        except ImportError:
            return None
        except Exception as e:
            print(f"Warning: Conversation routing failed: {e}")
            return None

    def _queue_followup(self, envelope: EventEnvelope, active_run_id: str) -> dict:
        """Queue an event for later processing (lane-aware)."""
        max_queued = self._config.get("max_queued_events_per_session", 20)

        try:
            conn = sqlite3.connect(self.db_path)
            # Check queue depth
            cursor = conn.execute(
                "SELECT COUNT(*) FROM ci_event_queue "
                "WHERE session_key = ? AND status = 'queued'",
                (envelope.session_key,),
            )
            count = cursor.fetchone()[0]
            if count >= max_queued:
                conn.close()
                return {
                    "action": "ignored",
                    "reason": f"queue_full (max {max_queued})",
                }

            conn.execute(
                "INSERT INTO ci_event_queue (session_key, event_id, envelope_json, status) "
                "VALUES (?, ?, ?, 'queued')",
                (
                    envelope.session_key,
                    envelope.event_id,
                    json.dumps(envelope.to_dict()),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        return {
            "action": "queued",
            "reason": f"active_run {active_run_id} on session {envelope.session_key}",
            "active_run_id": active_run_id,
        }

    def _create_pipeline_run(
        self, envelope: EventEnvelope, workflow: str, run_id: str
    ):
        """Record a new pipeline run in the database."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO ci_pipeline_runs "
                "(id, session_key, run_id, platform, workflow, status, trigger_source, event_id) "
                "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
                (
                    envelope.event_id,
                    envelope.session_key,
                    run_id,
                    envelope.platform,
                    workflow,
                    envelope.source,
                    envelope.event_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _launch_workflow(
        self, workflow: str, issue_number: str, run_id: str, platform: str
    ):
        """Launch a workflow script as a background subprocess."""
        script_path = PROJECT_ROOT / "tools" / "ci" / "workflows" / f"{workflow}.py"

        if not script_path.exists():
            print(f"Workflow script not found: {script_path}")
            return

        from tools.testing.utils import get_safe_subprocess_env

        cmd = [sys.executable, str(script_path), str(issue_number), run_id]

        print(f"Launching {workflow} for session {issue_number} (platform: {platform})")

        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=get_safe_subprocess_env(),
            stdin=subprocess.DEVNULL,
        )

    def update_pipeline_status(self, run_id: str, status: str):
        """Update pipeline run status (called by workflow scripts on completion)."""
        try:
            conn = sqlite3.connect(self.db_path)
            now = datetime.now(timezone.utc).isoformat()
            completed_at = now if status in ("completed", "failed") else None
            conn.execute(
                "UPDATE ci_pipeline_runs SET status = ?, completed_at = ? "
                "WHERE run_id = ? AND status IN ('running', 'recovering')",
                (status, completed_at, run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def process_queued_events(self, session_key: str) -> list:
        """Process queued events for a session after pipeline completion.

        Called after a pipeline finishes to drain queued events.
        Returns list of routing results.
        """
        results = []
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT id, envelope_json FROM ci_event_queue "
                "WHERE session_key = ? AND status = 'queued' "
                "ORDER BY created_at ASC",
                (session_key,),
            )
            queued = cursor.fetchall()
            conn.close()

            for queue_id, envelope_json in queued:
                try:
                    data = json.loads(envelope_json)
                    envelope = EventEnvelope(**data)

                    # Mark as processing
                    self._update_queue_status(queue_id, "processing")

                    # Re-route through normal flow
                    result = self.route(envelope)
                    results.append(result)

                    # Mark as processed
                    self._update_queue_status(queue_id, "processed")

                except Exception as e:
                    print(f"Warning: Failed to process queued event {queue_id}: {e}")
                    self._update_queue_status(queue_id, "dropped")

        except Exception:
            pass
        return results

    def _update_queue_status(self, queue_id: int, status: str):
        """Update queue entry status."""
        try:
            conn = sqlite3.connect(self.db_path)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE ci_event_queue SET status = ?, processed_at = ? "
                "WHERE id = ?",
                (status, now, queue_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
