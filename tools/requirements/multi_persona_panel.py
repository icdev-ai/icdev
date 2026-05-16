#!/usr/bin/env python3
# CUI // SP-CTI
"""Multi-persona parallel requirement generation panel.

Fires N LLM calls concurrently (one per selected persona) using
ThreadPoolExecutor. Each persona responds from its professional lens,
contributing requirements and follow-up questions independently.
Results are merged and returned as a structured panel response.

Usage:
    from tools.requirements.multi_persona_panel import run_panel

    results = run_panel(
        session_data=dict(session_row),
        message="We need an OSINT conflict monitoring system",
        conn=conn,
        personas=["developer", "analyst"],
    )
    # results: List[PersonaResult]
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Panel system prompt — injected on top of each persona's system_prompt
# ---------------------------------------------------------------------------

_PANEL_DIRECTIVE = """\
--- PANEL MODE ---
You are participating in a multi-expert requirements panel. Other domain
experts are reviewing the same message in parallel. Your job:

1. Respond ONLY from your professional domain lens — do not try to cover everything.
2. Generate 3-6 concrete draft requirements that YOUR domain would own.
   Format each as: "REQ: <imperative statement starting with 'The system shall' or 'Users must'>"
3. Ask ONE targeted follow-up question from your domain's perspective.
4. Be terse. 3-4 sentences max, then your REQs, then your question.

Other experts will cover their domains. Focus on yours.
"""

_PANEL_EXTRACTION_SYSTEM = """\
Extract requirements from this panel expert response.
Return a JSON array. Each item: {"text": "<requirement>", "type": "<functional|non_functional|security|compliance|data|integration|performance>", "priority": "<high|medium|low>"}.
Only extract explicit REQ: lines or clear imperative statements.
If none, return [].
Respond with JSON only, no markdown.
"""

# Color tokens per persona key (used by UI)
PERSONA_COLORS: Dict[str, str] = {
    "developer":          "#4a90d9",
    "analyst":            "#7c3aed",
    "pm":                 "#0891b2",
    "isso":               "#dc2626",
    "solutions_architect":"#059669",
    "co":                 "#d97706",
    "innovator":          "#db2777",
    "biz_dev":            "#6366f1",
}

DEFAULT_PANEL = ["developer", "analyst"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PersonaResult:
    persona: str
    display_name: str
    color: str
    response: str
    requirements: List[Dict]
    question: str
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class PanelResult:
    session_id: str
    message: str
    personas_run: List[str]
    results: List[PersonaResult]
    merged_requirements: List[Dict] = field(default_factory=list)
    total_requirements: int = 0
    panel_question: str = ""


# ---------------------------------------------------------------------------
# Core panel runner
# ---------------------------------------------------------------------------

def run_panel(
    session_data: Dict,
    message: str,
    conn: Any,
    personas: Optional[List[str]] = None,
) -> PanelResult:
    """Fire all personas in parallel and return merged PanelResult.

    Args:
        session_data: Row dict from intake_sessions.
        message:      Current customer message.
        conn:         Active DB connection (read-only — panel does not write).
        personas:     List of persona keys. Defaults to DEFAULT_PANEL.

    Returns:
        PanelResult with per-persona responses and merged requirements.
    """
    personas = personas or DEFAULT_PANEL
    session_id = session_data.get("id", "")

    with ThreadPoolExecutor(max_workers=len(personas), thread_name_prefix="panel") as ex:
        future_map = {
            ex.submit(_run_one_persona, session_data, message, conn, p): p
            for p in personas
        }
        results: List[PersonaResult] = []
        for future in as_completed(future_map):
            results.append(future.result())

    # Preserve the order requested by the caller
    order = {p: i for i, p in enumerate(personas)}
    results.sort(key=lambda r: order.get(r.persona, 99))

    merged = _merge_requirements(results)
    panel_q = _synthesize_panel_question(results)

    return PanelResult(
        session_id=session_id,
        message=message,
        personas_run=personas,
        results=results,
        merged_requirements=merged,
        total_requirements=len(merged),
        panel_question=panel_q,
    )


def persist_panel_requirements(panel: PanelResult, turn_number: int, db_path=None) -> int:
    """Write merged requirements to intake_requirements. Returns count written."""
    if not panel.merged_requirements:
        return 0

    conn = get_connection(db_path=str(db_path)) if db_path else None
    if conn is None:
        return 0

    try:
        written = 0
        now = datetime.now(timezone.utc).isoformat()
        for req in panel.merged_requirements:
            req_id = f"req-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT OR IGNORE INTO intake_requirements
                   (id, session_id, raw_text, requirement_type, priority,
                    turn_number, status, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'draft', 'panel', ?)""",
                (
                    req_id,
                    panel.session_id,
                    req["text"],
                    req.get("type", "functional"),
                    req.get("priority", "medium"),
                    turn_number,
                    now,
                ),
            )
            written += 1
        conn.commit()
        return written
    except Exception:
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Single-persona execution (runs in a thread)
# ---------------------------------------------------------------------------

def _run_one_persona(
    session_data: Dict,
    message: str,
    conn: Any,
    persona_key: str,
) -> PersonaResult:
    import time
    t0 = time.monotonic()

    color = PERSONA_COLORS.get(persona_key, "#6b7280")

    try:
        from tools.requirements.intake_engine import (
            _load_persona,
            _build_conversation_history,
        )
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        persona = _load_persona(persona_key)
        if not persona:
            return PersonaResult(
                persona=persona_key,
                display_name=persona_key,
                color=color,
                response="",
                requirements=[],
                question="",
                error=f"Unknown persona: {persona_key}",
            )

        display_name = persona.get("display_name", persona_key)
        system_prompt = _build_panel_system_prompt(persona, session_data)
        messages = _build_conversation_history(session_data.get("id", ""), conn, message)

        router = get_router()
        req = LLMRequest(
            messages=messages,
            system_prompt=system_prompt,
            max_tokens=600,
            temperature=0.7,
            agent_id=f"icdev-panel-{persona_key}",
            project_id=session_data.get("project_id", ""),
            classification=session_data.get("classification", "CUI"),
        )
        response = router.invoke("panel_persona_response", req)
        content = (response.content or "").strip() if response else ""

        requirements = _extract_reqs_from_response(content, session_data.get("classification", "CUI"))
        question = _extract_question(content)

        return PersonaResult(
            persona=persona_key,
            display_name=display_name,
            color=color,
            response=content,
            requirements=requirements,
            question=question,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    except Exception as exc:
        return PersonaResult(
            persona=persona_key,
            display_name=persona_key,
            color=color,
            response="",
            requirements=[],
            question="",
            error=str(exc),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_panel_system_prompt(persona: Dict, session_data: Dict) -> str:
    ctx_parts = [
        persona.get("system_prompt", ""),
        "",
        _PANEL_DIRECTIVE,
        "--- Session Context ---",
        f"Classification: {session_data.get('classification', 'CUI')}",
        f"Impact Level: {session_data.get('impact_level', 'IL4')}",
    ]
    try:
        ctx = json.loads(session_data.get("context_summary") or "{}")
        if ctx.get("goal"):
            ctx_parts.append(f"Goal: {ctx['goal']}")
        if ctx.get("selected_frameworks"):
            ctx_parts.append(f"Frameworks: {', '.join(ctx['selected_frameworks'])}")
    except (ValueError, TypeError):
        pass
    return "\n".join(ctx_parts)


# ---------------------------------------------------------------------------
# Requirement and question extraction
# ---------------------------------------------------------------------------

def _extract_reqs_from_response(text: str, classification: str) -> List[Dict]:
    """Extract REQ: lines first; fall back to LLM extraction."""
    results = []
    seen = set()

    # Fast path: parse explicit REQ: markers written by the persona
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("REQ:"):
            req_text = stripped[4:].strip()
            key = req_text.lower()[:60]
            if req_text and key not in seen:
                seen.add(key)
                req_type = _infer_type(req_text)
                results.append({"text": req_text, "type": req_type, "priority": "medium"})

    if results:
        return results

    # Slow path: ask LLM to extract from freeform text
    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        router = get_router()
        req = LLMRequest(
            messages=[{"role": "user", "content": text}],
            system_prompt=_PANEL_EXTRACTION_SYSTEM,
            max_tokens=512,
            temperature=0.1,
            classification=classification,
        )
        resp = router.invoke("requirement_extraction", req)
        if not resp or not resp.content:
            return []
        content = resp.content.strip().lstrip("```json").rstrip("```").strip()
        parsed = json.loads(content)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get("text", "").strip():
                    key = item["text"].lower()[:60]
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "text": item["text"].strip(),
                            "type": item.get("type", "functional"),
                            "priority": item.get("priority", "medium"),
                        })
    except Exception:
        pass

    return results


def _extract_question(text: str) -> str:
    """Return the last sentence ending with '?' from the response."""
    for sentence in reversed(text.replace("\n", " ").split("?")):
        candidate = sentence.strip()
        if len(candidate) > 20:
            return candidate.split(".")[-1].strip() + "?"
    return ""


def _infer_type(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in ("encrypt", "auth", "access control", "rbac", "audit", "cac", "piv", "stig")):
        return "security"
    if any(k in lower for k in ("comply", "nist", "fedramp", "cmmc", "ato", "control", "regulation")):
        return "compliance"
    if any(k in lower for k in ("latency", "throughput", "response time", "uptime", "availability", "sla")):
        return "performance"
    if any(k in lower for k in ("ingest", "feed", "api", "webhook", "sync", "integrate", "connect")):
        return "integration"
    if any(k in lower for k in ("store", "retain", "database", "schema", "data model", "backup")):
        return "data"
    if any(k in lower for k in ("shall not", "must not", "scale", "monitor", "log", "alert")):
        return "non_functional"
    return "functional"


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def _merge_requirements(results: List[PersonaResult]) -> List[Dict]:
    """Deduplicate requirements across all personas by fuzzy key."""
    merged: List[Dict] = []
    seen: set = set()
    for result in results:
        for req in result.requirements:
            key = req["text"].lower()[:50]
            if key not in seen:
                seen.add(key)
                merged.append({**req, "source_persona": result.persona})
    return merged


def _synthesize_panel_question(results: List[PersonaResult]) -> str:
    """Pick the highest-value question from all persona questions."""
    questions = [r.question for r in results if r.question and not r.error]
    if not questions:
        return ""
    # Prefer the longest (most specific) question
    return max(questions, key=len)
