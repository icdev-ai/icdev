#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
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

logger = get_logger("icdev.chat_manager")

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


def _check_coworker_trigger(context_id: str, content: str, context: dict) -> None:
    """Store coworker_instance_id in context_config if the extension hook set one.

    Reads context.get('coworker_instance_id') — populated by ACE launch extension
    hooks — and persists it in chat_contexts.context_config JSON so the chat UI
    can render a 'View Co-Worker Team' button.  No-ops silently if not set.
    """
    coworker_instance_id = context.get("coworker_instance_id")
    if not coworker_instance_id:
        return
    try:
        conn = get_connection(db_path=str(DB_PATH))
        row = conn.execute(
            "SELECT context_config FROM chat_contexts WHERE id = %s",
            (context_id,),
        ).fetchone()
        config: dict = {}
        if row:
            raw = row[0]  # index access works for both tuple and sqlite3.Row
            if raw:
                try:
                    config = json.loads(raw)
                except Exception:
                    pass
        config["coworker_instance_id"] = str(coworker_instance_id)
        conn.execute(
            "UPDATE chat_contexts SET context_config = %s, updated_at = %s WHERE id = %s",
            (json.dumps(config), datetime.now(timezone.utc).isoformat(), context_id),
        )
        conn.commit()
        conn.close()
        logger.debug(
            "Linked coworker instance %s to context %s", coworker_instance_id, context_id
        )
    except Exception as exc:
        logger.debug("coworker trigger link skipped for context %s: %s", context_id, exc)


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
# RICOAS Adaptation 1 — Constitution preamble (live system state, ~600 tokens)
# ---------------------------------------------------------------------------

_RICOAS_CONSTITUTION_ENABLED = True  # gate: set ICDEV_RICOAS_CONSTITUTION=false to disable


def _build_ricoas_constitution(
    context_id: str,
    project_id: str = "",
    tenant_id: str = "",
) -> str:
    """Build a ~600-token RICOAS state block injected into the system prompt each turn.

    Queries live DB state across all 6 RICOAS dimensions. Failures per dimension
    are silently skipped — the block degrades gracefully to only populated dimensions.
    """
    import os
    if os.getenv("ICDEV_RICOAS_CONSTITUTION", "true").lower() in ("false", "0"):
        return ""

    lines: list[str] = ["[RICOAS CONTEXT — live system state]"]
    conn = None
    try:
        conn = get_connection()
    except Exception:
        return ""

    # R — Requirements: open intake_requirements count
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM intake_requirements WHERE status NOT IN ('done','dismissed')"
        ).fetchone()
        n = row[0] if row else 0
        if project_id:
            row2 = conn.execute(
                "SELECT COUNT(*) FROM intake_requirements WHERE status NOT IN ('done','dismissed') AND session_id LIKE %s",
                (f"%{project_id}%",),
            ).fetchone()  # noqa: S608
            n = row2[0] if row2 else n
        lines.append(f"R (Requirements): {n} open requirement(s)")
    except Exception:
        pass

    # I — Infrastructure: active canvas instances
    try:
        row = conn.execute(
            "SELECT COUNT(*), array_agg(DISTINCT status) FROM canvas_instances WHERE status != 'disabled'"
        ).fetchone()
        if row and row[0]:
            statuses = row[1] or []
            lines.append(f"I (Infrastructure): {row[0]} canvas instance(s) active — statuses: {', '.join(str(s) for s in statuses[:4])}")
    except Exception:
        pass

    # C — Compliance: catalogued controls by family (top 3 families)
    try:
        rows = conn.execute(
            "SELECT family, COUNT(*) as cnt FROM compliance_controls GROUP BY family ORDER BY cnt DESC LIMIT 3"
        ).fetchall()
        if rows:
            summary = "; ".join(f"{r[0]}:{r[1]}" for r in rows)
            lines.append(f"C (Compliance): controls catalogued — {summary}")
    except Exception:
        pass

    # O — Operational health: latest health metrics
    try:
        rows = conn.execute(
            "SELECT component, level, count FROM log_health_metrics ORDER BY ts DESC LIMIT 3"
        ).fetchall()
        if rows:
            parts = [f"{r[0]}={r[1]}({r[2]})" for r in rows]
            lines.append(f"O (Operational): {'; '.join(parts)}")
    except Exception:
        pass

    # A — Architecture: top KG entity types
    try:
        rows = conn.execute(
            "SELECT entity_type, COUNT(*) as cnt FROM kg_nodes GROUP BY entity_type ORDER BY cnt DESC LIMIT 3"
        ).fetchall()
        if rows:
            parts = [f"{r[0]}({r[1]})" for r in rows]
            lines.append(f"A (Architecture): KG node types — {', '.join(parts)}")
    except Exception:
        pass

    # S — Security: open STIG findings by severity + CVE count
    try:
        rows = conn.execute(
            "SELECT severity, COUNT(*) FROM stig_findings WHERE status IN ('Open','Not_Reviewed') GROUP BY severity"
        ).fetchall()
        cve_row = conn.execute("SELECT COUNT(*) FROM cve_triage").fetchone()
        stig_parts = [f"{r[0]}:{r[1]}" for r in rows] if rows else []
        cve_count = cve_row[0] if cve_row else 0
        sec_parts = []
        if stig_parts:
            sec_parts.append(f"STIG open — {', '.join(stig_parts)}")
        if cve_count:
            sec_parts.append(f"CVEs triaged: {cve_count}")
        if sec_parts:
            lines.append(f"S (Security): {'; '.join(sec_parts)}")
    except Exception:
        pass

    if len(lines) <= 1:
        return ""  # Nothing populated — skip injection

    return "\n".join(lines) + "\n"


def _build_full_constitution(ctx) -> str:
    """Assemble the full RICOAS constitution block for a context.

    Combines: RICOAS state + scope constraints + axis instruction (if set).
    """
    parts = []
    ricoas = _build_ricoas_constitution(ctx.context_id, ctx.project_id, ctx.tenant_id)
    if ricoas:
        parts.append(ricoas)
    scope = _build_scope_constraint(ctx.project_id)
    if scope:
        parts.append(scope)
    axis = _axis_instruction(getattr(ctx, "ricoas_axis", ""))
    if axis:
        parts.append(axis + "\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# RICOAS Adaptation 2 — KG-backed invisible context retrieval (~400 tokens)
# ---------------------------------------------------------------------------


def _kg_context_retrieve(user_message: str, top_k: int = 5) -> str:
    """Return a hidden KG context block for the user message.

    Uses awareness_kg_nodes (self-awareness graph) with keyword matching.
    Returns empty string if KG is unavailable.
    """
    import os
    if os.getenv("ICDEV_KG_CONTEXT", "true").lower() in ("false", "0"):
        return ""
    if not user_message.strip():
        return ""

    try:
        from tools.knowledge_graph.graph_rag import retrieve

        result = retrieve(query=user_message, top_k=top_k, compress=False)
        ctx_text = result.get("context", "")
        if not ctx_text or result.get("status") == "error":
            return ""
        # Trim to ~400 tokens (~1600 chars)
        trimmed = ctx_text[:1600]
        return f"[KG CONTEXT]\n{trimmed}\n"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# RICOAS Adaptation 3 — Persistent corrections store
# ---------------------------------------------------------------------------

import re as _re

_CORRECTION_PATTERNS = [
    _re.compile(r"\bactually\b", _re.IGNORECASE),
    _re.compile(r"\bno,?\s+(?:we|our|it|that|this)\b", _re.IGNORECASE),
    _re.compile(r"\bthat(?:'s| is) (?:wrong|incorrect|not right|not what)\b", _re.IGNORECASE),
    _re.compile(r"\byou(?:'re| are) (?:wrong|incorrect|mistaken)\b", _re.IGNORECASE),
    _re.compile(r"\bI (?:meant|mean|said)\b", _re.IGNORECASE),
    _re.compile(r"\bcorrection[:\s]\b", _re.IGNORECASE),
]

_CORRECTIONS_TABLE_CREATED = False


def _ensure_corrections_table() -> None:
    global _CORRECTIONS_TABLE_CREATED
    if _CORRECTIONS_TABLE_CREATED:
        return
    try:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_corrections (
                id SERIAL PRIMARY KEY,
                context_id TEXT NOT NULL,
                correction_text TEXT NOT NULL,
                turn_number INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_corrections_ctx ON chat_corrections(context_id, created_at DESC)"
        )
        conn.commit()
        _CORRECTIONS_TABLE_CREATED = True
    except Exception:
        _CORRECTIONS_TABLE_CREATED = True  # don't retry on failure


def _detect_and_store_correction(context_id: str, content: str, turn_number: int = 0) -> None:
    """If content matches a correction signal, persist it to chat_corrections."""
    if not any(p.search(content) for p in _CORRECTION_PATTERNS):
        return
    try:
        _ensure_corrections_table()
        conn = get_connection()
        conn.execute(
            "INSERT INTO chat_corrections (context_id, correction_text, turn_number, created_at) VALUES (%s, %s, %s, %s)",
            (context_id, content[:500], turn_number, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        conn.commit()
        logger.debug("[RICOAS] Stored correction for context %s", context_id)
        # ── LESSONS LEARNED: chat correction ─────────────────────────────────
        try:
            from tools.workflow.lesson_learned import write_chat_correction_lesson
            entry_id = write_chat_correction_lesson(context_id, turn_number, content)
            if entry_id:
                logger.debug("[LESSON] Chat correction logged as %s", entry_id)
        except Exception as _ll_exc:
            logger.warning("[LESSON] Could not log chat correction: %s", _ll_exc)
    except Exception as exc:
        logger.debug("[RICOAS] Could not store correction: %s", exc)


def _get_recent_corrections(context_id: str, limit: int = 5) -> str:
    """Return last N corrections for this context, formatted for system prompt prepend."""
    try:
        _ensure_corrections_table()
        conn = get_connection()
        rows = conn.execute(
            "SELECT correction_text, created_at FROM chat_corrections WHERE context_id = %s ORDER BY created_at DESC LIMIT %s",
            (context_id, limit),
        ).fetchall()
        if not rows:
            return ""
        corrections = [
            f"  • {r[0] if isinstance(r, (list,tuple)) else r['correction_text']}"
            for r in reversed(rows)
        ]
        return "[CORRECTIONS FROM THIS SESSION]\n" + "\n".join(corrections) + "\n"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# RICOAS Adaptation 4 (priority 2) — Scope constraint injection
# ---------------------------------------------------------------------------


def _build_scope_constraint(project_id: str = "") -> str:
    """Return a [SCOPE] block listing active canvas types for this project.

    Injected into the constitution so the LLM knows it must not modify code
    outside the active canvases.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT canvas FROM canvas_instances WHERE status != 'disabled' LIMIT 12"
        ).fetchall()
        if not rows:
            return ""
        types = [r[0] if isinstance(r, (list, tuple)) else r["canvas"] for r in rows if r[0]]
        if not types:
            return ""
        return "[SCOPE] Active canvas types: " + ", ".join(types) + ". Do not modify code outside these canvases.\n"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# RICOAS Adaptation 3 (priority 3) — Live compliance doc injection
# ---------------------------------------------------------------------------

_COMPLIANCE_TERMS = _re.compile(
    r"\b(fedramp|cmmc|nist|stig|800-53|800-171|fisma|ato|poam|control|ac-\d|au-\d|cm-\d|ia-\d|sc-\d)\b",
    _re.IGNORECASE,
)


def _live_compliance_context(user_message: str) -> str:
    """If query mentions compliance terms, inject live control summaries from DB.

    Returns a [COMPLIANCE CONTEXT] block, or empty string if not applicable.
    """
    if not _COMPLIANCE_TERMS.search(user_message):
        return ""
    try:
        conn = get_connection()
        # Top control families by count — gives LLM a live catalog fingerprint
        rows = conn.execute(
            "SELECT family, COUNT(*) as cnt, MIN(impact_level) as lvl "
            "FROM compliance_controls GROUP BY family ORDER BY cnt DESC LIMIT 8"
        ).fetchall()
        if not rows:
            return ""
        parts = []
        for r in rows:
            fam = r[0] if isinstance(r, (list, tuple)) else r["family"]
            cnt = r[1] if isinstance(r, (list, tuple)) else r["cnt"]
            lvl = r[2] if isinstance(r, (list, tuple)) else r["lvl"]
            parts.append(f"{fam}({cnt} controls, {lvl or 'unset'})")
        return "[COMPLIANCE CONTEXT — live catalog]\n" + "; ".join(parts) + "\n"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# RICOAS Adaptation 5 (priority 4) — IaC context injection at session start
# ---------------------------------------------------------------------------


def _build_iac_context(project_id: str = "") -> str:
    """Return a summary of IaC templates/modules available for this project.

    Called once at context creation and cached in system_prompt.
    """
    try:
        conn = get_connection()
        # Look for IaC-related tables
        iac_tables = []
        for t in ("iac_templates", "iac_modules", "iac_resources", "infrastructure_templates"):
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()  # noqa: S608
                count = row[0] if row else 0
                if count > 0:
                    iac_tables.append(f"{t}({count})")
            except Exception:
                pass
        if not iac_tables:
            return ""
        return "[IaC CONTEXT] Available templates/modules: " + ", ".join(iac_tables) + "\n"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# RICOAS Adaptation 2 (priority 5) — Two-phase inception/construction workflow
# ---------------------------------------------------------------------------

_INTENT_BUILD_PATTERNS = _re.compile(
    r"\b(build|create|implement|add|develop|write|generate|scaffold|make|set up|setup)\b.{0,40}"
    r"\b(feature|endpoint|page|table|module|service|component|canvas|route|api|function)\b",
    _re.IGNORECASE | _re.DOTALL,
)

_INCEPTION_INSTRUCTION = (
    "[INCEPTION PHASE] The user wants to build something new. "
    "Before generating any code or artifacts: (1) confirm the exact scope — "
    "what file(s) will change and what won't; (2) state your interpretation of "
    "the requirements in 2-3 bullet points; (3) ask one clarifying question if "
    "anything is ambiguous. Only proceed to implementation after the user confirms "
    "your interpretation is correct."
)


def _check_inception_needed(ctx, user_message: str) -> bool:
    """Return True if we should inject inception-phase instruction this turn.

    Only fires if: build-intent detected AND early in conversation AND not yet complete.
    """
    if ctx._inception_complete:
        return False
    if ctx.turn_number > 6:  # past early conversation — skip
        ctx._inception_complete = True
        return False
    return bool(_INTENT_BUILD_PATTERNS.search(user_message))


def _mark_inception_complete(ctx) -> None:
    ctx._inception_complete = True


# ---------------------------------------------------------------------------
# RICOAS Adaptation 1 (priority 6) — Axis-separated thread contexts
# ---------------------------------------------------------------------------

_AXIS_INSTRUCTIONS: dict[str, str] = {
    "R": (
        "[AXIS: Requirements] Focus exclusively on requirements: intake, clarification, "
        "prioritization, and acceptance criteria. Do not generate code or architecture "
        "decisions in this thread."
    ),
    "I": (
        "[AXIS: Infrastructure] Focus on infrastructure, deployment, IaC, and canvas "
        "wiring. Do not modify business logic or data models."
    ),
    "C": (
        "[AXIS: Compliance] Focus on compliance controls, STIG findings, POA&M entries, "
        "and evidence collection. Reference live control catalog above."
    ),
    "O": (
        "[AXIS: Operations] Focus on health monitoring, incident response, SLOs, and "
        "runbooks. Prioritize non-destructive actions."
    ),
    "A": (
        "[AXIS: Architecture] Focus on system design, component dependencies, and "
        "KG-driven architectural decisions. Reference KG context above."
    ),
    "S": (
        "[AXIS: Security] Focus on security findings, CVE triage, ZTA controls, and "
        "threat modeling. Never suggest bypassing security gates."
    ),
}


def _axis_instruction(axis: str) -> str:
    return _AXIS_INSTRUCTIONS.get(axis.upper(), "")


# ---------------------------------------------------------------------------
# RICOAS Adaptation — error spiral circuit breaker (wires to ctx.set_intervention)
# ---------------------------------------------------------------------------

_ERROR_SPIRAL_THRESHOLD = 3  # consecutive identical/error responses before auto-intervene
_ERROR_FINGERPRINT_LEN = 80  # chars to compare for duplicate detection


def _is_error_response(response: str) -> bool:
    """Heuristic: treat echo-fallback or leading error markers as error responses."""
    stripped = response.strip()
    return (
        stripped.startswith("[Agent ") and "Acknowledged:" in stripped
    ) or stripped.lower().startswith("error:")


def _response_fingerprint(response: str) -> str:
    return response.strip()[:_ERROR_FINGERPRINT_LEN]


def _check_error_spiral(ctx, response: str) -> bool:
    """Update spiral counter; return True if threshold crossed (caller should intervene)."""
    fp = _response_fingerprint(response)
    is_err = _is_error_response(response)
    is_dup = fp == ctx._last_response_fingerprint and fp != ""

    if is_err or is_dup:
        ctx._consecutive_error_count += 1
    else:
        ctx._consecutive_error_count = 0

    ctx._last_response_fingerprint = fp
    return ctx._consecutive_error_count >= _ERROR_SPIRAL_THRESHOLD


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


_CODE_REQUEST_RE = _re.compile(
    r"\b(write|create|implement|generate|build|refactor|fix|patch|migrate|translate)\b"
    r"[^.?!]{0,60}\b(code|function|method|class|module|script|endpoint|route|api|"
    r"query|sql|test|component|service|handler|parser|migration)\b"
    r"|```",
    _re.IGNORECASE,
)


def _is_code_request(message: str) -> bool:
    """Heuristic: is the user asking for code generation/implementation?

    Conservative — fires only on explicit code-action phrasing or a fenced block.
    """
    return bool(_CODE_REQUEST_RE.search(message or ""))


def _resolve_chat_reasoning_mode(reasoning_mode: str, user_content: str, router) -> str:
    """Resolve the effective CoT/CoD mode for a chat turn.

    off → off; on → advisor picks (never off, code requests only);
    auto → advisor decides, code requests only. Section kill-switch wins.
    """
    if reasoning_mode not in ("on", "auto"):
        return "off"
    if not _is_code_request(user_content):
        return "off"
    try:
        from tools.llm.reasoned_codegen import section_enabled, MODE_OFF, MODE_COT

        if not section_enabled(router):
            return MODE_OFF
        from tools.llm.reasoned_codegen_advisor import recommend

        rec = recommend("code_generation", user_content, router=router)
        mode = rec.get("mode", MODE_OFF)
        if reasoning_mode == "on" and mode == MODE_OFF:
            mode = MODE_COT
        return mode
    except Exception:
        return "cot" if reasoning_mode == "on" else "off"


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
        reasoning_mode: str = "off",
    ):
        self.context_id = context_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.title = title
        self.project_id = project_id
        self.agent_model = agent_model
        self.system_prompt = system_prompt
        # Reasoned codegen for code requests: off | auto | on
        # (auto → advisor decides; on → force CoT/CoD; respects section kill-switch)
        self.reasoning_mode = reasoning_mode if reasoning_mode in ("off", "auto", "on") else "off"

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

        # RICOAS Adaptation 6 — axis-separated focus (R/I/C/O/A/S or "")
        self.ricoas_axis: str = ""

        # RICOAS Adaptation 5 — two-phase inception state
        self._inception_complete: bool = False

        # RICOAS Adaptation 1 — error spiral circuit breaker
        self._consecutive_error_count: int = 0
        self._last_response_fingerprint: str = ""

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
            "reasoning_mode": self.reasoning_mode,
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
        ricoas_axis: str = "",
        reasoning_mode: str = "off",
    ) -> dict:
        """Create a new chat context. Returns context dict.

        Args:
            ricoas_axis: Optional RICOAS dimension focus (R/I/C/O/A/S).
                         Injects axis-specific instructions and restricts scope.
            reasoning_mode: Reasoned codegen for code requests — off | auto | on.
                         auto = advisor decides; on = force CoT/CoD. The
                         args/llm_config.yaml reasoned_codegen kill-switch wins.
        """
        with self._lock:
            # Check concurrent limit per user
            user_contexts = [c for c in self._contexts.values() if c.user_id == user_id and c.status == "active"]
            if len(user_contexts) >= MAX_CONCURRENT_PER_USER:
                return {
                    "error": f"Max {MAX_CONCURRENT_PER_USER} concurrent contexts per user",
                    "active_count": len(user_contexts),
                }

        context_id = f"ctx-{uuid.uuid4().hex[:12]}"

        # RICOAS: IaC context injection at session start
        iac_block = _build_iac_context(project_id)
        if iac_block:
            system_prompt = iac_block + "\n" + system_prompt if system_prompt else iac_block

        ctx = ChatContext(
            context_id=context_id,
            user_id=user_id,
            tenant_id=tenant_id,
            title=title or f"Chat {context_id[-6:]}",
            project_id=project_id,
            agent_model=agent_model,
            system_prompt=system_prompt,
            reasoning_mode=reasoning_mode,
        )
        ctx.ricoas_axis = ricoas_axis.upper()[:1] if ricoas_axis else ""

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
        """Get a single context by ID (memory-first, DB fallback for post-restart lookups)."""
        with self._lock:
            ctx = self._contexts.get(context_id)
            if ctx:
                return ctx.to_dict()
        return self._db_get_context(context_id)

    def _db_get_context(self, context_id: str) -> Optional[dict]:
        """Reconstruct a minimal context dict from DB for contexts not in memory."""
        try:
            conn = self._get_db()
            row = conn.execute(
                "SELECT id, user_id, tenant_id, title, status, project_id, "
                "agent_model, system_prompt, context_config, dirty_version, "
                "message_count, classification, created_at, updated_at "
                "FROM chat_contexts WHERE id = %s",
                (context_id,),
            ).fetchone()
            conn.close()
            if not row:
                return None
            reasoning_mode = "off"
            try:
                cfg = json.loads(row["context_config"]) if row["context_config"] else {}
                reasoning_mode = cfg.get("reasoning_mode", "off")
            except Exception:
                pass
            return {
                "context_id": row["id"],
                "user_id": row["user_id"] or "",
                "tenant_id": row["tenant_id"] or "",
                "title": row["title"] or "",
                "project_id": row["project_id"] or "",
                "agent_model": row["agent_model"] or "sonnet",
                "reasoning_mode": reasoning_mode,
                "status": row["status"] or "active",
                "message_count": row["message_count"] or 0,
                "dirty_version": row["dirty_version"] or 0,
                "queue_depth": 0,
                "state_updates": {"up_to_date": True, "changes": []},
            }
        except Exception:
            return None

    def set_reasoning_mode(self, context_id: str, reasoning_mode: str) -> dict:
        """Update a session's reasoned-codegen mode mid-conversation (off|auto|on)."""
        mode = reasoning_mode if reasoning_mode in ("off", "auto", "on") else "off"
        with self._lock:
            ctx = self._contexts.get(context_id)
        if ctx is None:
            return {"error": "context not found", "context_id": context_id}
        ctx.reasoning_mode = mode
        try:
            conn = self._get_db()
            conn.execute(
                "UPDATE chat_contexts SET context_config = %s, updated_at = %s WHERE id = %s",
                (
                    json.dumps({"reasoning_mode": mode}),
                    datetime.now(timezone.utc).isoformat(),
                    context_id,
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug("reasoning_mode persist skipped: %s", exc)
        _mark_dirty(context_id, "reasoning_mode_changed", {"reasoning_mode": mode})
        return {"context_id": context_id, "reasoning_mode": mode}

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
            # ACE co-worker trigger. Two paths, deliberately different:
            #
            #   explicit  "@team <problem>"  -> launch immediately. An explicit
            #             command IS the approval, and this is a pinned
            #             regression contract (tests/test_ace_chat_trigger.py).
            #   implicit  4+ RICOAS signals  -> PROPOSE. A heuristic must not
            #             spawn agents that hold read/write/execute agency; the
            #             user gets an action card and decides.
            if not hook_ctx.get("coworker_instance_id"):
                try:
                    from icdev.tools.ace import chat_trigger as _ct

                    _trigger = _ct.detect_ace_trigger(content)
                    if _trigger == "explicit":
                        _ace_id = _ct.maybe_launch_ace(context_id, content)
                        if _ace_id:
                            hook_ctx["coworker_instance_id"] = _ace_id
                    elif _trigger == "implicit":
                        _proposal = _ct.build_team_proposal(context_id, content)
                        if _proposal:
                            self._post_action_card(context_id, _proposal)
                except Exception as _ace_exc:
                    logger.debug("ACE trigger skipped: %s", _ace_exc)
            _check_coworker_trigger(context_id, content, hook_ctx)
            # RICOAS Adaptation 3: detect and persist corrections
            _detect_and_store_correction(context_id, content, turn_number=turn)
            # RICOAS: if user confirms inception interpretation, mark complete
            with self._lock:
                _ctx = self._contexts.get(context_id)
            if _ctx and not _ctx._inception_complete:
                _confirm = content.strip().lower()
                if any(_confirm.startswith(w) for w in ("yes", "correct", "looks good", "proceed", "go ahead", "that's right")):
                    _mark_inception_complete(_ctx)

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
                   WHERE context_id = %s AND turn_number > %s
                   ORDER BY turn_number
                   LIMIT %s""",
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

                # CLI bridge deferred this turn to a background job. The PENDING
                # placeholder is already persisted by _process_message; finish
                # the task and let the worker post the real answer later, rather
                # than blocking or double-inserting an assistant message.
                if isinstance(response, dict) and response.get("status") == "pending":
                    self._db_complete_task(task_id, response.get("message", ""))
                    continue

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

                # --- RICOAS: error spiral circuit breaker ---
                if _check_error_spiral(ctx, response):
                    spiral_msg = (
                        f"[Auto-Intervention] Error spiral detected after "
                        f"{ctx._consecutive_error_count} consecutive identical/error responses. "
                        "Stopping to prevent runaway loop. Please rephrase your request."
                    )
                    logger.warning("[RICOAS] Error spiral on context %s — auto-intervening", context_id)
                    ctx._consecutive_error_count = 0
                    ctx.set_intervention(spiral_msg)

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

                # A-4 — Episodic memory: save this exchange so future retrieval has it
                try:
                    from tools.memory.memory_write import write_to_db as _mem_write_a4
                    _user_q = msg.get("content", "")
                    _mem_write_a4(
                        content=f"User: {_user_q[:400]} | Assistant: {response[:400]}",
                        entry_type="event",
                        importance=3,
                        source="hook",
                        tier="episodic",
                        session_ref=context_id,
                    )
                except Exception:
                    pass

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

        When the CLI bridge is active and a request outruns the soft-wait, the
        router raises ``CLIJobDeferred``. We catch it specifically, persist a
        PENDING assistant placeholder carrying the ``job_id``, and return a
        ``{"status": "pending", ...}`` dict so the caller switches to background
        mode instead of blocking. The echo fallback is reserved for the case
        where the bridge is disabled / no LLM is reachable.
        """
        # Resolve CLIJobDeferred lazily; if the bridge isn't installed the name
        # becomes an empty tuple so the ``except`` clauses below never match and
        # the existing echo fallback handles unavailability unchanged.
        try:
            from tools.llm.cli_bridge.cli_provider import CLIJobDeferred
        except Exception:  # bridge not installed/disabled
            CLIJobDeferred = ()

        try:
            from tools.llm.router import LLMRouter

            router = LLMRouter()

            # Build conversation history for context
            messages = self.get_messages(ctx.context_id, since_turn=0, limit=50)
            conversation = []
            system_content = ctx.system_prompt or ""

            # --- RICOAS: full constitution (state + scope + axis) ---
            constitution = _build_full_constitution(ctx)
            if constitution:
                system_content = constitution + "\n" + system_content

            # --- RICOAS Adaptation 3: prepend recent corrections ---
            corrections_block = _get_recent_corrections(ctx.context_id)
            if corrections_block:
                system_content = corrections_block + "\n" + system_content

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

                # --- RICOAS: KG invisible context ---
                kg_block = _kg_context_retrieve(user_content)
                if kg_block:
                    system_content += "\n" + kg_block

                # --- RICOAS: live compliance doc injection ---
                compliance_block = _live_compliance_context(user_content)
                if compliance_block:
                    system_content += "\n" + compliance_block

                # --- RICOAS: two-phase inception instruction ---
                if _check_inception_needed(ctx, user_content):
                    system_content += "\n" + _INCEPTION_INSTRUCTION + "\n"

                # --- Episodic/semantic memory retrieval (A-5) ---
                try:
                    from tools.memory.hybrid_search import search as _mem_search
                    _chat_top_k = 3
                    try:
                        import yaml as _yaml
                        import os as _os
                        _cfg_path = _os.path.join(_os.path.dirname(__file__), "..", "..", "args", "llm_config.yaml")
                        with open(_cfg_path, encoding="utf-8") as _f:
                            _lcfg = _yaml.safe_load(_f)
                        _chat_top_k = int(_lcfg.get("agent_loop", {}).get("memory", {}).get("chat_top_k", 3))
                    except Exception:
                        pass
                    _mem_hits = _mem_search(user_content, limit=_chat_top_k, tier="episodic|semantic")
                    if _mem_hits:
                        mem_block = "\n\n[Retrieved Memory]\n"
                        for h in _mem_hits:
                            mem_block += f"  - [{h['type']}] {h['content'][:300]}\n"
                        system_content += mem_block
                except Exception:
                    pass

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
            # Reasoned codegen: for code requests, when the session opted in
            # (reasoning_mode auto|on) and the section kill-switch is on, route
            # generation through CoT/CoD instead of a plain chat response.
            reasoning_mode = _resolve_chat_reasoning_mode(
                getattr(ctx, "reasoning_mode", "off"), user_content, router,
            )
            if reasoning_mode != "off":
                try:
                    from tools.llm.reasoned_codegen import generate_reasoned_code

                    rc = generate_reasoned_code(
                        function="code_generation",
                        request=request,
                        router=router,
                        mode=reasoning_mode,
                        project_id=ctx.project_id,
                    )
                    result = rc.code if rc and rc.code else ""
                    if not result:
                        response = router.invoke("chat_response", request)
                        result = response.content if response.content else str(response)
                except CLIJobDeferred:
                    raise  # let the outer handler switch to background mode
                except Exception as exc:
                    logger.debug("reasoned codegen failed (%s) — plain chat", exc)
                    response = router.invoke("chat_response", request)
                    result = response.content if response.content else str(response)
            else:
                response = router.invoke("chat_response", request)
                result = response.content if response.content else str(response)

            # Store RAG sources in metadata for attribution display
            if rag_results:
                self._last_rag_sources[ctx.context_id] = rag_results

            return result

        except CLIJobDeferred as exc:
            # CLI bridge accepted the request but it outran the soft-wait. Post a
            # PENDING placeholder (carrying job_id) and hand control back so the
            # request doesn't block; the backend worker posts the real answer.
            logger.info(
                "CLI job %s deferred to background for context %s",
                getattr(exc, "job_id", ""),
                ctx.context_id,
            )
            return self._persist_pending_placeholder(ctx, getattr(exc, "job_id", ""))

        except (ImportError, Exception) as exc:
            logger.debug("LLM unavailable for chat: %s — using echo fallback", exc)
            content = msg.get("content", "")
            return f"[Agent {ctx.agent_model}] Acknowledged: {content[:500]}"

    _PENDING_PLACEHOLDER_TEXT = (
        "Working… running in background; I'll post the result here."
    )

    def _persist_pending_placeholder(self, ctx: "ChatContext", job_id: str) -> dict:
        """Persist a PENDING assistant placeholder for a deferred CLI job.

        Writes an assistant message (``content_type="pending"``) carrying the
        ``job_id`` in metadata so the UI can render a "still working" bubble and
        a worker can later overwrite/append the real answer. Returns the pending
        descriptor the agent loop / caller surfaces instead of blocking.
        """
        ctx.turn_number += 1
        self._db_insert_message(
            ctx.context_id,
            ctx.turn_number,
            "assistant",
            self._PENDING_PLACEHOLDER_TEXT,
            content_type="pending",
            metadata={"status": "pending", "job_id": job_id},
        )
        _mark_dirty(
            ctx.context_id,
            "new_message",
            {
                "turn_number": ctx.turn_number,
                "role": "assistant",
                "content": self._PENDING_PLACEHOLDER_TEXT,
                "content_type": "pending",
                "status": "pending",
                "job_id": job_id,
            },
        )
        return {
            "status": "pending",
            "job_id": job_id,
            "message": self._PENDING_PLACEHOLDER_TEXT,
        }

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

        # If the CLI bridge deferred to a background job, the PENDING placeholder
        # is already persisted — nothing more to record here.
        if isinstance(response, dict) and response.get("status") == "pending":
            return

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
                    agent_model, system_prompt, context_config, dirty_version,
                    message_count, classification, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    ctx.context_id,
                    ctx.user_id,
                    ctx.tenant_id,
                    ctx.title,
                    ctx.status,
                    ctx.project_id,
                    ctx.agent_model,
                    ctx.system_prompt,
                    json.dumps({"reasoning_mode": ctx.reasoning_mode}),
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
                "UPDATE chat_contexts SET status = %s, updated_at = %s WHERE id = %s",
                (status, datetime.now(timezone.utc).isoformat(), context_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    def _post_action_card(self, context_id: str, card: dict) -> None:
        """Insert an interactive card into the conversation.

        Stored as a normal ``chat_messages`` row with
        ``content_type='action_card'`` — a value the CHECK constraint already
        permits, so this needs no migration. The payload lives in ``metadata``;
        ``content`` carries a plain-markdown fallback so a surface that does not
        know about cards still renders something meaningful instead of a blank
        turn.

        Posted as ``system`` rather than ``assistant`` on purpose: it is not a
        model turn, and it must not be fed back as conversational context.
        """
        try:
            roles = card.get("roles") or []
            names = ", ".join(r.get("display_name") or r.get("role_id") for r in roles)
            fallback = (
                "**Subject-matter experts available.** "
                + (f"Proposed team: {names}. " if names else "")
                + "Reply `@team <goal>` to start one."
            )
            with self._lock:
                ctx = self._contexts.get(context_id)
                if ctx is None:
                    return
                ctx.turn_number += 1
                turn = ctx.turn_number
            self._db_insert_message(
                context_id, turn, "system", fallback,
                content_type="action_card", metadata=card,
            )
            _mark_dirty(context_id, "action_card", {"card": card.get("card")})
        except Exception as exc:  # noqa: BLE001 — a card must never break the turn
            logger.debug("action card post skipped: %s", exc)

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
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
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
                "UPDATE chat_contexts SET message_count = %s, dirty_version = dirty_version + 1, updated_at = %s WHERE id = %s",
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
                   VALUES (%s, %s, %s, 'processing', %s, %s, %s)""",
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
                "UPDATE chat_tasks SET status = 'completed', output_text = %s, completed_at = %s WHERE id = %s",
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
                "UPDATE chat_tasks SET status = 'failed', error_message = %s, completed_at = %s WHERE id = %s",
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
