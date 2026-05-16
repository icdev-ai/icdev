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
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

# Matches "REQ:" anywhere on a line — handles bullets (- * 1.), bold (**REQ:**), indented
_RE_REQ_MARKER = re.compile(r'(?:[-*\d.)>\s]|\*{1,2})*\s*REQ\s*:+\s*(.+)', re.IGNORECASE)
# Matches imperative requirement statements without a REQ: prefix
_RE_IMPERATIVE = re.compile(
    r'(?:The (?:system|platform|tool|service|solution) (?:shall|must|will)\b|'
    r'(?:Users?|Administrators?|Operators?) (?:must|shall)\b).{15,}',
    re.IGNORECASE,
)

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
<!-- cache_breakpoint -->"""

_PANEL_EXTRACTION_SYSTEM = """\
Extract all system requirements from this expert panel response.
Return a JSON array. Each element: {"text": "<requirement>", "type": "<functional|non_functional|security|compliance|data|integration|performance>", "priority": "<high|medium|low>"}.
Look for:
- Lines prefixed with REQ: (with or without bullets/numbers/markdown)
- Imperative statements: "The system shall...", "The system must...", "Users must...", "The platform shall..."
- Any concrete capability, constraint, or behaviour the system is expected to have.
If nothing is found, return [].
Respond with raw JSON only — no markdown fences.
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
    # Enrich merged requirements with ontology concept IRIs
    if merged:
        try:
            from tools.requirements.ontology_enricher import enrich_requirements
            enrich_requirements(merged, session_context=session_data)
        except Exception:
            pass
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

    # Always get a connection — for PostgreSQL backend db_path is ignored by get_connection
    conn = get_connection(db_path=str(db_path)) if db_path else get_connection()
    conn.set_security_context(None)

    try:
        written = 0
        inserted_ids: list = []
        now = datetime.now(timezone.utc).isoformat()
        for req in panel.merged_requirements:
            req_id = f"req-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT OR IGNORE INTO intake_requirements
                   (id, session_id, raw_text, requirement_type, priority,
                    source_turn, source_document, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'panel', 'draft', ?)""",
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
            inserted_ids.append(req_id)
            written += 1
        conn.commit()

        # Blockchain provenance anchor — non-blocking, best-effort
        if inserted_ids:
            _anchor_requirements_async(panel.session_id, inserted_ids, panel.merged_requirements, turn_number)

        return written
    except Exception:
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Blockchain anchoring helper
# ---------------------------------------------------------------------------

def _anchor_requirements_async(
    session_id: str,
    req_ids: list,
    requirements: list,
    turn_number: int,
) -> None:
    """Fire-and-forget blockchain anchor in a daemon thread. Never raises."""
    import threading

    def _do_anchor():
        try:
            import hashlib
            from tools.blockchain.chain_anchor import ChainAnchor
            # Build a deterministic Merkle-like root from requirement texts
            combined = "\n".join(sorted(r.get("text", "") for r in requirements))
            merkle_root = hashlib.sha256(combined.encode("utf-8")).hexdigest()
            metadata = {
                "source": "panel_requirements",
                "session_id": session_id,
                "turn_number": turn_number,
                "requirement_count": len(req_ids),
                "req_ids": req_ids[:10],  # first 10 for metadata
            }
            ChainAnchor().anchor_merkle_root(merkle_root, metadata)
        except Exception:
            pass  # Non-critical — never let this break the panel flow

    t = threading.Thread(target=_do_anchor, daemon=True)
    t.start()


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
            effort="medium",          # Extended thinking budget where supported
            cache_control="ephemeral", # Prompt caching — stable persona base is cached
            agent_id=f"icdev-panel-{persona_key}",
            project_id=session_data.get("project_id", ""),
            classification=session_data.get("classification", "CUI"),
        )
        response = router.invoke("panel_persona_response", req)
        content = (response.content or "").strip() if response else ""

        requirements = _extract_reqs_from_response(content, session_data.get("classification", "CUI"))
        # Enrich extracted requirements with ontology type IRIs and concept tags
        if requirements:
            try:
                from tools.requirements.ontology_enricher import enrich_requirements
                enrich_requirements(requirements)
            except Exception:
                pass
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
    # Stable persona base (cacheable across turns) — cache_breakpoint separates it from dynamic context
    stable_part = "\n".join([
        persona.get("system_prompt", ""),
        "",
        _PANEL_DIRECTIVE,
    ])
    # Dynamic session context (changes per session — placed after breakpoint)
    ctx_parts = [
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
    return stable_part + "\n" + "\n".join(ctx_parts)


# ---------------------------------------------------------------------------
# Requirement and question extraction
# ---------------------------------------------------------------------------

def _extract_reqs_from_response(text: str, classification: str) -> List[Dict]:
    """Extract requirements from a persona response.

    Priority order:
    1. Lines containing REQ: (handles bullets, numbers, bold markdown wrappers)
    2. Bare imperative statements ("The system shall ...", "Users must ...")
    3. LLM slow-path extraction for freeform prose that matches neither above
    """
    results: List[Dict] = []
    seen: set = set()

    def _add(req_text: str) -> None:
        req_text = re.sub(r'[\*_`]+$', '', req_text).strip().rstrip(".")
        if not req_text or len(req_text) < 10:
            return
        key = req_text.lower()[:60]
        if key not in seen:
            seen.add(key)
            results.append({"text": req_text, "type": _infer_type(req_text), "priority": "medium"})

    # Pass 1 — lines with explicit REQ: marker (any position on line)
    for line in text.splitlines():
        m = _RE_REQ_MARKER.search(line)
        if m:
            _add(m.group(1).strip())

    # Pass 2 — imperative statements that survived without a REQ: marker
    if not results:
        for line in text.splitlines():
            m = _RE_IMPERATIVE.search(line)
            if m:
                # Grab from the match start to end of line
                _add(line[m.start():].strip())

    if results:
        return results

    # Pass 3 (slow) — LLM extraction for fully freeform prose
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
        raw = resp.content.strip()
        # Strip optional markdown fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        parsed = json.loads(raw.strip())
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get("text", "").strip():
                    req_text = item["text"].strip()
                    key = req_text.lower()[:60]
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "text": req_text,
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
