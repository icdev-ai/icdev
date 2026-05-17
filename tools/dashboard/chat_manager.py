#!/usr/bin/env python3
# CUI // SP-CTI
"""Multi-stream parallel chat manager (Phase 44 — D257-D260, D265-D267).

Thread-per-context execution model for parallel chat sessions. Built on
threading + sqlite3 primitives. Category-level concept shared with Agent
Zero's DeferredTask (MIT) but implemented independently — different
concurrency model (threading vs asyncio+Future), different persistence
(SQLite vs in-memory), zero class/method overlap. See OPT-73 audit report.
Contexts scoped to (user_id, tenant_id). Max 5 concurrent per user.
Intervention via atomic field, checked at 3 points per agent loop iteration.

Enhanced integrations (Phase 44+):
- RAG context injection: auto-retrieves relevant knowledge before LLM call (D-RAG-2)
- History compression: 3-tier budget (50/30/20) for long conversations (D271-D274)
- Context pressure monitoring: stuck detection + pressure alerts (D-GSD-4 to D-GSD-6)
- Bayesian teaching: info-gain advisory for compliance ordering (D-BT-1)
- Intake enrichment: RICOAS session linking + readiness scoring

Usage:
    from tools.dashboard.chat_manager import chat_manager

    ctx = chat_manager.create_context("user-1", "tenant-1", "My Chat")
    chat_manager.send_message(ctx["context_id"], "Hello!", role="user")
    chat_manager.intervene(ctx["context_id"], "Stop and do this instead")
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from tools.db.storage import get_connection
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from tools.dashboard.config import DEFAULT_CLASSIFICATION

logger = logging.getLogger("icdev.chat_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Max concurrent contexts per user
MAX_CONCURRENT_PER_USER = 5


# ---------------------------------------------------------------------------
# Extension hook integration (Feature 2)
# ---------------------------------------------------------------------------


def _dispatch_hook(hook_name: str, context: dict) -> dict:
    """Dispatch extension hook if available."""
    try:
        from tools.extensions.extension_manager import extension_manager, ExtensionPoint

        ep = ExtensionPoint(hook_name)
        return extension_manager.dispatch(ep, context)
    except (ImportError, ValueError):
        return context


def _fire_intake_hook(context_id: str, content: str) -> None:
    """Run the requirement intake hook in a background daemon thread (non-blocking)."""
    def _run() -> None:
        try:
            from tools.chat.requirement_intake_hook import process_message_for_intake
            result = process_message_for_intake(context_id, content)
            if result.get("hitl_instance_id"):
                logger.info(
                    "Intake hook: %d requirement(s) queued for HITL review "
                    "(instance=%s) context=%s",
                    result.get("requirements_found", 0),
                    result["hitl_instance_id"],
                    context_id,
                )
        except Exception as exc:
            logger.debug("Intake hook error for context %s: %s", context_id, exc)

    threading.Thread(target=_run, daemon=True).start()


def _mark_dirty(context_id: str, change_type: str, data: Optional[dict] = None):
    """Mark context dirty on state tracker if available (Feature 4)."""
    try:
        from tools.dashboard.state_tracker import state_tracker

        state_tracker.mark_dirty(context_id, change_type, data)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# RAG context retrieval (D-RAG-2)
# ---------------------------------------------------------------------------


def _rag_retrieve(query: str, project_id: str = "", tenant_id: str = "") -> list:
    """Retrieve relevant RAG context for a chat message.

    Returns list of dicts with 'content', 'source_type', 'score' keys.
    Gracefully returns empty list if RAG is unavailable.
    """
    try:
        from tools.rag.retriever import RAGRetriever

        retriever = RAGRetriever(tenant_id=tenant_id)
        results = retriever.search(query, top_k=3, project_id=project_id)
        return [
            {
                "content": r.content[:800],
                "source_type": getattr(r, "source_type", "unknown"),
                "score": round(getattr(r, "final_score", getattr(r, "score", 0.0)), 3),
                "chunk_id": getattr(r, "chunk_id", ""),
            }
            for r in results
            if getattr(r, "final_score", getattr(r, "score", 0.0)) >= 0.3
        ]
    except (ImportError, Exception) as exc:
        logger.debug("RAG retrieval unavailable: %s", exc)
        return []


# ---------------------------------------------------------------------------
# History compression (D271-D274)
# ---------------------------------------------------------------------------


def _compress_history(messages: list, budget_tokens: int = 3000) -> list:
    """Compress conversation history using 3-tier budget if it exceeds budget.

    Falls back to returning messages unchanged if compressor unavailable.
    """
    try:
        from tools.memory.history_compressor import HistoryCompressor

        compressor = HistoryCompressor()
        return compressor.compress(messages, budget_tokens=budget_tokens)
    except (ImportError, Exception) as exc:
        logger.debug("History compression unavailable: %s", exc)
        return messages


# ---------------------------------------------------------------------------
# Context pressure monitoring (D-GSD-4 to D-GSD-6)
# ---------------------------------------------------------------------------

_PRESSURE_CHECK_INTERVAL = 10  # turns between pressure checks


def _check_context_pressure(session_id: str) -> Optional[dict]:
    """Check context pressure and stuck detection.

    Returns dict with warnings or None if healthy.
    """
    try:
        from tools.agent.context_pressure import health_check

        result = health_check(session_id=session_id)
        if not result.get("overall_healthy", True):
            return {
                "pressure_level": result.get("context_pressure", {}).get("pressure_level", "unknown"),
                "is_stuck": result.get("stuck_detection", {}).get("is_stuck", False),
                "recommendation": result.get("stuck_detection", {}).get("recommendation", ""),
                "tokens_used": result.get("context_pressure", {}).get("estimated_tokens", 0),
            }
        return None
    except (ImportError, Exception) as exc:
        logger.debug("Context pressure check unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# ChatContext — per-context state
# ---------------------------------------------------------------------------


class ChatContext:
    """Represents a single chat stream with its own message queue and thread."""

    def __init__(
        self,
        context_id: str,
        user_id: str,
        tenant_id: str = "",
        title: str = "",
        project_id: str = "",
        agent_model: str = "sonnet",
        system_prompt: str = "",
    ):
        self.context_id = context_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.title = title
        self.project_id = project_id
        self.agent_model = agent_model
        self.system_prompt = system_prompt

        self.status = "active"  # active, paused, completed, error, archived
        self.message_queue: deque = deque()
        self.turn_number = 0
        self.dirty_version = 0
        self.is_processing = False
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.last_activity_at = self.created_at

        # Intervention (D265-D267)
        self._intervention_lock = threading.Lock()
        self._intervention_message: Optional[str] = None
        self._checkpoint: Optional[dict] = None

        # Intake session link (RICOAS integration)
        self._intake_session_id: str = ""

        # Agent thread
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def set_intervention(self, message: str) -> None:
        """Thread-safe set intervention message."""
        with self._intervention_lock:
            self._intervention_message = message

    def check_intervention(self) -> Optional[str]:
        """Check-and-clear intervention (returns message or None)."""
        with self._intervention_lock:
            msg = self._intervention_message
            self._intervention_message = None
            return msg

    def save_checkpoint(self, data: dict) -> None:
        """Save current progress as checkpoint."""
        self._checkpoint = {
            "turn_number": self.turn_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

    def to_dict(self) -> dict:
        return {
            "context_id": self.context_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "title": self.title,
            "project_id": self.project_id,
            "agent_model": self.agent_model,
            "status": self.status,
            "message_count": self.turn_number,
            "dirty_version": self.dirty_version,
            "queue_depth": len(self.message_queue),
            "is_processing": self.is_processing,
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
        }


# ---------------------------------------------------------------------------
# ChatManager — singleton managing all chat contexts
# ---------------------------------------------------------------------------


class ChatManager:
    """Manages multi-stream parallel chat contexts.

    Singleton pattern — use the module-level ``chat_manager`` instance.
    """

    def __init__(self):
        self._contexts: Dict[str, ChatContext] = {}
        self._lock = threading.Lock()
        self._last_rag_sources: Dict[str, list] = {}  # context_id -> last RAG results

    # ------------------------------------------------------------------
    # Context CRUD
    # ------------------------------------------------------------------

    def create_context(
        self,
        user_id: str,
        tenant_id: str = "",
        title: str = "",
        project_id: str = "",
        agent_model: str = "sonnet",
        system_prompt: str = "",
    ) -> dict:
        """Create a new chat context. Returns context dict."""
        with self._lock:
            # Check concurrent limit per user
            user_contexts = [c for c in self._contexts.values() if c.user_id == user_id and c.status == "active"]
            if len(user_contexts) >= MAX_CONCURRENT_PER_USER:
                return {
                    "error": f"Max {MAX_CONCURRENT_PER_USER} concurrent contexts per user",
                    "active_count": len(user_contexts),
                }

        context_id = f"ctx-{uuid.uuid4().hex[:12]}"
        ctx = ChatContext(
            context_id=context_id,
            user_id=user_id,
            tenant_id=tenant_id,
            title=title or f"Chat {context_id[-6:]}",
            project_id=project_id,
            agent_model=agent_model,
            system_prompt=system_prompt,
        )

        with self._lock:
            self._contexts[context_id] = ctx

        # Persist to DB
        self._db_create_context(ctx)

        # Start agent loop thread
        ctx._thread = threading.Thread(
            target=self._agent_loop,
            args=(context_id,),
            daemon=True,
        )
        ctx._thread.start()

        # Dispatch hook
        _dispatch_hook("agent_start", {"context_id": context_id, "user_id": user_id})
        _mark_dirty(context_id, "context_created", ctx.to_dict())

        logger.info("Created chat context %s for user %s", context_id, user_id)
        return ctx.to_dict()

    def list_contexts(
        self,
        user_id: str = "",
        tenant_id: str = "",
        include_closed: bool = False,
    ) -> List[dict]:
        """List chat contexts, optionally filtered."""
        with self._lock:
            results = []
            for ctx in self._contexts.values():
                if user_id and ctx.user_id != user_id:
                    continue
                if tenant_id and ctx.tenant_id != tenant_id:
                    continue
                if not include_closed and ctx.status in ("completed", "archived"):
                    continue
                results.append(ctx.to_dict())
            return results

    def get_context(self, context_id: str) -> Optional[dict]:
        """Get a single context by ID."""
        with self._lock:
            ctx = self._contexts.get(context_id)
            return ctx.to_dict() if ctx else None

    def close_context(self, context_id: str) -> dict:
        """Close/archive a chat context."""
        with self._lock:
            ctx = self._contexts.get(context_id)
            if ctx:
                ctx.status = "completed"
                ctx._stop_event.set()

        # Always persist to DB — handles contexts not in memory (e.g. after restart)
        self._db_update_status(context_id, "completed")
        _dispatch_hook("agent_end", {"context_id": context_id})
        _mark_dirty(context_id, "context_closed")

        logger.info("Closed chat context %s", context_id)
        return {"context_id": context_id, "status": "completed"}

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def send_message(
        self,
        context_id: str,
        content: str,
        role: str = "user",
    ) -> dict:
        """Send a message to a context. Queued if busy."""
        with self._lock:
            ctx = self._contexts.get(context_id)
            if not ctx:
                return {"error": "Context not found"}
            if ctx.status not in ("active", "paused"):
                return {"error": f"Context is {ctx.status}"}

        # Dispatch pre-hook
        hook_ctx = _dispatch_hook(
            "chat_message_before",
            {
                "context_id": context_id,
                "content": content,
                "role": role,
            },
        )
        content = hook_ctx.get("content", content)
        role = hook_ctx.get("role", role)

        # Record message in DB
        ctx.turn_number += 1
        turn = ctx.turn_number
        self._db_insert_message(context_id, turn, role, content)

        # Queue for processing
        ctx.message_queue.append(
            {
                "turn_number": turn,
                "role": role,
                "content": content,
            }
        )
        ctx.last_activity_at = datetime.now(timezone.utc).isoformat()

        if role == "user":
            _fire_intake_hook(context_id, content)

        _mark_dirty(
            context_id,
            "new_message",
            {
                "turn_number": turn,
                "role": role,
                "content": content[:200],
            },
        )

        return {
            "context_id": context_id,
            "turn_number": turn,
            "role": role,
            "queued": ctx.is_processing,
            "queue_depth": len(ctx.message_queue),
        }

    def intervene(self, context_id: str, message: str) -> dict:
        """Mid-stream intervention (D265-D267).

        Sets atomic intervention flag checked at 3 points in agent loop.
        """
        with self._lock:
            ctx = self._contexts.get(context_id)
            if not ctx:
                return {"error": "Context not found"}

        ctx.set_intervention(message)

        # Record intervention message
        ctx.turn_number += 1
        turn = ctx.turn_number
        self._db_insert_message(
            context_id,
            turn,
            "intervention",
            message,
            content_type="intervention",
        )

        _mark_dirty(
            context_id,
            "intervention",
            {
                "turn_number": turn,
                "message": message[:200],
            },
        )

        logger.info("Intervention set on context %s", context_id)
        return {
            "context_id": context_id,
            "turn_number": turn,
            "intervention_set": True,
        }

    def get_messages(
        self,
        context_id: str,
        since_turn: int = 0,
        limit: int = 100,
    ) -> List[dict]:
        """Get messages for a context, optionally since a turn number."""
        try:
            conn = get_connection(db_path=str(DB_PATH))
            rows = conn.execute(
                """SELECT turn_number, role, content, content_type,
                          is_compressed, compression_tier, classification, created_at
                   FROM chat_messages
                   WHERE context_id = ? AND turn_number > ?
                   ORDER BY turn_number
                   LIMIT ?""",
                (context_id, since_turn, limit),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    # ------------------------------------------------------------------
    # Agent loop (background thread per context)
    # ------------------------------------------------------------------

    def _agent_loop(self, context_id: str) -> None:
        """Background worker thread for a chat context.

        Processes queued messages and checks for interventions.
        """
        ctx = self._contexts.get(context_id)
        if not ctx:
            return

        while not ctx._stop_event.is_set():
            # Intervention check point 1: before queue pop
            intervention = ctx.check_intervention()
            if intervention:
                self._handle_intervention(ctx, intervention)
                continue

            # Pop next message from queue
            if not ctx.message_queue:
                time.sleep(0.1)  # Small sleep to prevent busy-wait
                continue

            msg = ctx.message_queue.popleft()
            ctx.is_processing = True
            _mark_dirty(context_id, "processing_started", {"turn": msg["turn_number"]})

            # Create a task record
            task_id = f"task-{uuid.uuid4().hex[:12]}"
            self._db_create_task(task_id, context_id, "message", msg["content"])

            try:
                # Context pressure check (D-GSD-4 to D-GSD-6)
                if ctx.turn_number > 0 and ctx.turn_number % _PRESSURE_CHECK_INTERVAL == 0:
                    pressure = _check_context_pressure(ctx.context_id)
                    if pressure:
                        ctx.turn_number += 1
                        severity = "high" if pressure.get("is_stuck") else "medium"
                        pressure_msg = "[Context Health] "
                        if pressure.get("is_stuck"):
                            pressure_msg += f"Stuck detected. {pressure.get('recommendation', '')}"
                        else:
                            pressure_msg += (
                                f"Pressure level: {pressure.get('pressure_level', 'unknown')}. "
                                f"Estimated tokens: {pressure.get('tokens_used', 0)}"
                            )
                        self._db_insert_message(
                            context_id,
                            ctx.turn_number,
                            "system",
                            pressure_msg,
                            content_type="context_health",
                        )
                        _mark_dirty(
                            context_id,
                            "context_health",
                            {
                                "severity": severity,
                                "pressure_level": pressure.get("pressure_level"),
                                "is_stuck": pressure.get("is_stuck", False),
                            },
                        )

                # Intervention check point 2: before LLM call
                intervention = ctx.check_intervention()
                if intervention:
                    ctx.save_checkpoint({"interrupted_message": msg})
                    ctx.message_queue.appendleft(msg)  # Re-queue
                    self._handle_intervention(ctx, intervention)
                    continue

                # Process message through LLM
                response = self._process_message(ctx, msg)

                # Intervention check point 3: after LLM response
                intervention = ctx.check_intervention()
                if intervention:
                    # Save current response as checkpoint
                    ctx.save_checkpoint(
                        {
                            "interrupted_message": msg,
                            "partial_response": response,
                        }
                    )
                    self._handle_intervention(ctx, intervention)
                    continue

                # Record assistant response
                ctx.turn_number += 1
                self._db_insert_message(
                    context_id,
                    ctx.turn_number,
                    "assistant",
                    response,
                )

                # Dispatch post-hook — check for advisories (D325, D327)
                hook_result = _dispatch_hook(
                    "chat_message_after",
                    {
                        "context_id": context_id,
                        "role": "assistant",
                        "content": response,
                        "turn_number": ctx.turn_number,
                        "project_id": getattr(ctx, "project_id", ""),
                        "user_query": msg.get("content", ""),
                        "rag_sources": self._last_rag_sources.pop(context_id, []),
                        "intake_session_id": getattr(ctx, "_intake_session_id", ""),
                    },
                )

                # Generic advisory injection — handles all extension advisory types
                if isinstance(hook_result, dict):
                    self._inject_advisories(ctx, context_id, hook_result)

                self._db_complete_task(task_id, response)
                _mark_dirty(
                    context_id,
                    "new_message",
                    {
                        "turn_number": ctx.turn_number,
                        "role": "assistant",
                        "content": response[:200],
                    },
                )

            except Exception as exc:
                logger.error("Error processing message in %s: %s", context_id, exc)
                ctx.turn_number += 1
                error_msg = f"Error: {type(exc).__name__}: {exc}"
                self._db_insert_message(
                    context_id,
                    ctx.turn_number,
                    "system",
                    error_msg,
                    content_type="error",
                )
                self._db_fail_task(task_id, str(exc))
                _mark_dirty(context_id, "error", {"error": error_msg[:200]})

            finally:
                ctx.is_processing = False
                ctx.last_activity_at = datetime.now(timezone.utc).isoformat()

        logger.info("Agent loop exited for context %s", context_id)

    def _process_message(self, ctx: ChatContext, msg: dict) -> str:
        """Process a single message through LLM router.

        Enhanced pipeline:
        1. Build conversation history
        2. Compress history if over budget (D271-D274)
        3. Retrieve RAG context for user query (D-RAG-2)
        4. Inject RAG context into system prompt
        5. Call LLM via router
        6. Track RAG sources in metadata

        Falls back to echo response if LLM is unavailable.
        """
        try:
            from tools.llm.router import LLMRouter

            router = LLMRouter()

            # Build conversation history for context
            messages = self.get_messages(ctx.context_id, since_turn=0, limit=50)
            conversation = []
            system_content = ctx.system_prompt or ""

            # --- RAG context injection (D-RAG-2) ---
            user_content = msg.get("content", "")
            rag_results = []
            if user_content and msg.get("role") == "user":
                rag_results = _rag_retrieve(
                    query=user_content,
                    project_id=ctx.project_id,
                    tenant_id=ctx.tenant_id,
                )
                if rag_results:
                    rag_context = "\n\n[Relevant Knowledge (RAG)]\n"
                    for i, r in enumerate(rag_results, 1):
                        rag_context += f"  [{i}] ({r['source_type']}, score={r['score']}): {r['content'][:400]}\n"
                    system_content += rag_context

            if system_content:
                conversation.append({"role": "system", "content": system_content})

            for m in messages:
                r = m.get("role", "user")
                if r == "intervention":
                    r = "user"
                if r in ("user", "assistant", "system"):
                    conversation.append({"role": r, "content": m["content"]})

            # --- History compression (D271-D274) ---
            # Compress if conversation exceeds ~3000 tokens (~12000 chars)
            total_chars = sum(len(m.get("content", "")) for m in conversation)
            if total_chars > 12000:
                # Keep system prompt separate, compress the rest
                sys_msgs = [m for m in conversation if m["role"] == "system" and m is conversation[0]]
                chat_msgs = conversation[1:] if sys_msgs else conversation
                compressed = _compress_history(chat_msgs, budget_tokens=3000)
                conversation = sys_msgs + compressed

            from tools.llm.provider import LLMRequest

            request = LLMRequest(
                messages=conversation,
                model=ctx.agent_model,
            )
            response = router.invoke("chat_response", request)
            result = response.content if response.content else str(response)

            # Store RAG sources in metadata for attribution display
            if rag_results:
                self._last_rag_sources[ctx.context_id] = rag_results

            return result

        except (ImportError, Exception) as exc:
            logger.debug("LLM unavailable for chat: %s — using echo fallback", exc)
            content = msg.get("content", "")
            return f"[Agent {ctx.agent_model}] Acknowledged: {content[:500]}"

    # ------------------------------------------------------------------
    # Generic advisory injection
    # ------------------------------------------------------------------

    # Advisory type registry: key suffix in hook_result -> (label, content_type, dirty_type)
    _ADVISORY_TYPES = {
        "governance_advisory": ("[AI Governance Advisory]", "governance_advisory", "governance_advisory"),
        "workflow_advisory": ("[Workflow Status]", "workflow_status", "workflow_status"),
        "bayesian_advisory": ("[Bayesian Learning]", "bayesian_advisory", "bayesian_advisory"),
        "rag_advisory": ("[Knowledge Sources]", "rag_attribution", "rag_attribution"),
        "code_quality_advisory": ("[Code Quality]", "code_quality_advisory", "code_quality_advisory"),
        "genesis_advisory": ("[Genesis Insight]", "genesis_advisory", "genesis_advisory"),
        "intake_advisory": ("[Intake Enrichment]", "intake_advisory", "intake_advisory"),
        "migration_advisory": ("[Modernization Advisory]", "migration_advisory", "migration_advisory"),
    }

    def _inject_advisories(self, ctx: "ChatContext", context_id: str, hook_result: dict) -> None:
        """Inject all advisory system messages from hook results.

        Handles all registered advisory types generically, avoiding
        hardcoded per-type handling blocks.
        """
        for key, (label, content_type, dirty_type) in self._ADVISORY_TYPES.items():
            advisory = hook_result.get(key)
            if not advisory:
                continue
            ctx.turn_number += 1
            msg_text = advisory.get("message", "") if isinstance(advisory, dict) else str(advisory)
            action = advisory.get("action", "") if isinstance(advisory, dict) else ""
            advisory_content = f"{label} {msg_text}"
            if action:
                advisory_content += f"\nAction: {action}"
            self._db_insert_message(
                context_id,
                ctx.turn_number,
                "system",
                advisory_content,
                content_type=content_type,
            )
            dirty_data = {}
            if isinstance(advisory, dict):
                dirty_data = {
                    k: v
                    for k, v in advisory.items()
                    if k in ("gap_id", "severity", "total_gaps", "loop_id", "score", "source_count", "fitness_domain")
                }
            _mark_dirty(context_id, dirty_type, dirty_data)

    def _handle_intervention(self, ctx: ChatContext, message: str) -> None:
        """Process an intervention message with priority."""
        logger.info("Processing intervention in context %s", ctx.context_id)

        # Process intervention through LLM
        response = self._process_message(
            ctx,
            {
                "content": f"[INTERVENTION] {message}",
                "role": "user",
            },
        )

        # Record intervention response
        ctx.turn_number += 1
        self._db_insert_message(
            ctx.context_id,
            ctx.turn_number,
            "assistant",
            response,
            content_type="text",
        )

        _mark_dirty(
            ctx.context_id,
            "intervention_response",
            {
                "turn_number": ctx.turn_number,
                "content": response[:200],
            },
        )

    # ------------------------------------------------------------------
    # Database operations
    # ------------------------------------------------------------------

    def _get_db(self) -> sqlite3.Connection:
        conn = get_connection(db_path=str(DB_PATH))
        return conn

    def _db_create_context(self, ctx: ChatContext) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO chat_contexts
                   (id, user_id, tenant_id, title, status, project_id,
                    agent_model, system_prompt, dirty_version, message_count,
                    classification, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ctx.context_id,
                    ctx.user_id,
                    ctx.tenant_id,
                    ctx.title,
                    ctx.status,
                    ctx.project_id,
                    ctx.agent_model,
                    ctx.system_prompt,
                    0,
                    0,
                    DEFAULT_CLASSIFICATION,
                    ctx.created_at,
                    ctx.created_at,
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug("DB write skipped (table may not exist): %s", exc)

    def _db_update_status(self, context_id: str, status: str) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                "UPDATE chat_contexts SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(timezone.utc).isoformat(), context_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    def _db_insert_message(
        self,
        context_id: str,
        turn_number: int,
        role: str,
        content: str,
        content_type: str = "text",
        metadata: Optional[dict] = None,
    ) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO chat_messages
                   (context_id, turn_number, role, content, content_type,
                    metadata, classification, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    context_id,
                    turn_number,
                    role,
                    content,
                    content_type,
                    json.dumps(metadata) if metadata else None,
                    DEFAULT_CLASSIFICATION,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute(
                "UPDATE chat_contexts SET message_count = ?, dirty_version = dirty_version + 1, updated_at = ? WHERE id = ?",
                (turn_number, datetime.now(timezone.utc).isoformat(), context_id),
            )
            conn.commit()
            conn.close()
            with self._lock:
                _ctx = self._contexts.get(context_id)
                if _ctx:
                    _ctx.dirty_version += 1
        except sqlite3.OperationalError as exc:
            logger.debug("DB message insert skipped: %s", exc)

    def _db_create_task(self, task_id: str, context_id: str, task_type: str, input_text: str) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO chat_tasks
                   (id, context_id, task_type, status, input_text,
                    classification, created_at)
                   VALUES (?, ?, ?, 'processing', ?, ?, ?)""",
                (
                    task_id,
                    context_id,
                    task_type,
                    input_text[:2000],
                    DEFAULT_CLASSIFICATION,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    def _db_complete_task(self, task_id: str, output_text: str) -> None:
        try:
            conn = self._get_db()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE chat_tasks SET status = 'completed', output_text = ?, completed_at = ? WHERE id = ?",
                (output_text[:5000], now, task_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    def _db_fail_task(self, task_id: str, error: str) -> None:
        try:
            conn = self._get_db()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE chat_tasks SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                (error[:2000], now, task_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(self) -> dict:
        """Return diagnostic info for monitoring."""
        with self._lock:
            return {
                "total_contexts": len(self._contexts),
                "active_contexts": sum(1 for c in self._contexts.values() if c.status == "active"),
                "processing": sum(1 for c in self._contexts.values() if c.is_processing),
                "total_queued": sum(len(c.message_queue) for c in self._contexts.values()),
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
chat_manager = ChatManager()
