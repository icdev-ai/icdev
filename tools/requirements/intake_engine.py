#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Conversational requirements intake engine.

Creates and manages intake sessions, processes customer turns, extracts
requirements, detects gaps/ambiguities, and scores readiness.

Usage:
    # Create new session
    python tools/requirements/intake_engine.py --project-id proj-123 \\
        --customer-name "Jane Smith" --customer-org "PEO IEW&S" --impact-level IL4 --json

    # Process a customer turn
    python tools/requirements/intake_engine.py --session-id sess-abc \\
        --message "We need a mission planning tool with 200 users" --json

    # Resume paused session
    python tools/requirements/intake_engine.py --session-id sess-abc --resume --json

    # Score readiness
    python tools/requirements/intake_engine.py --session-id sess-abc --score-readiness --json

    # Export requirements
    python tools/requirements/intake_engine.py --session-id sess-abc --export --json
"""

import argparse
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:
        return -1


def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="sess"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _load_config():
    """Load RICOAS configuration."""
    config_path = BASE_DIR / "args" / "ricoas_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except ImportError:
            pass
    # Fallback defaults
    return {
        "ricoas": {
            "readiness_threshold": 0.7,
            "readiness_weights": {
                "completeness": 0.25,
                "clarity": 0.25,
                "feasibility": 0.20,
                "compliance": 0.15,
                "testability": 0.15,
            },
            "intake_agent": {
                "max_conversation_turns": 200,
                "auto_readiness_score_interval": 3,
            },
        }
    }


def _load_persona(role, custom_role_description=None):
    """Load persona definition for a given role from role_personas.yaml.

    Args:
        role: The role key (e.g. 'developer', 'pm', 'isso').
        custom_role_description: Optional description for custom roles.

    Returns:
        dict with persona fields (system_prompt, opening_question, etc.)
        or None if persona file is missing.
    """
    persona_path = BASE_DIR / "args" / "role_personas.yaml"
    if not persona_path.exists():
        return None
    try:
        import yaml
        with open(persona_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except ImportError:
        return None

    personas = data.get("personas", {})

    # Check for built-in persona
    if role in personas:
        return personas[role]

    # Custom role — synthesize persona from meta template
    if custom_role_description:
        custom_cfg = data.get("custom_role", {})
        meta_prompt = custom_cfg.get("meta_system_prompt", "")
        opening_prompt = custom_cfg.get("opening_prompt", "")
        return {
            "display_name": role.replace("_", " ").title(),
            "system_prompt": meta_prompt.replace("{role_name}", role).replace(
                "{role_description}", custom_role_description
            ),
            "opening_question": opening_prompt.replace("{role_name}", role).replace(
                "{role_description}", custom_role_description
            ),
            "priority_topics": [],
            "follow_up_patterns": [],
        }

    # Fallback to developer persona
    return personas.get("developer")


# ---------------------------------------------------------------------------
# LLM-powered persona response generation
# ---------------------------------------------------------------------------

# Conditional LLM imports — LLM may not be available in air-gapped envs
_HAS_LLM = False
try:
    from tools.llm import get_router
    from tools.llm.provider import LLMRequest as _LLMRequest
    _HAS_LLM = True
except ImportError:
    pass


def _generate_persona_response(session_data, message, signals, conn):
    """Generate an LLM-powered persona response for the intake conversation.

    Builds a system prompt from the session's persona, conversation history,
    and current turn signals, then calls the LLM router.

    Args:
        session_data: dict of the intake_sessions row.
        message: The customer message for this turn.
        signals: dict with keys like extracted_reqs, ambiguities,
                 boundary_flags, gap_signals, devsecops_signals,
                 zta_signals, mosa_signals, readiness_update.
        conn: Active database connection.

    Returns:
        str with the persona response, or None if LLM is unavailable
        or any error occurs (caller should fall back to deterministic response).
    """
    if not _HAS_LLM:
        return None

    try:
        # Load context from session
        context_raw = session_data.get("context_summary") or "{}"
        try:
            ctx = json.loads(context_raw)
        except (json.JSONDecodeError, TypeError):
            ctx = {}

        role = ctx.get("role", "developer")
        custom_desc = ctx.get("custom_role_description", "")
        persona = _load_persona(role, custom_desc)
        if not persona:
            return None

        # Build conversation history (last 10 turns)
        session_id = session_data.get("id", "")
        history_rows = conn.execute(
            """SELECT turn_number, role, content
               FROM intake_conversation
               WHERE session_id = ?
               ORDER BY turn_number DESC LIMIT 10""",
            (session_id,),
        ).fetchall()
        history_rows = list(reversed(history_rows))

        conversation_messages = []
        for row in history_rows:
            r = dict(row)
            msg_role = "assistant" if r["role"] == "analyst" else "user"
            if r["role"] == "system":
                continue
            conversation_messages.append({
                "role": msg_role,
                "content": r["content"],
            })
        # Add current customer message
        conversation_messages.append({
            "role": "user",
            "content": message,
        })

        # Build system prompt with persona + session context
        goal = ctx.get("goal", "build")
        selected_fw = ctx.get("selected_frameworks", [])
        req_count = signals.get("total_requirements", 0)
        readiness = signals.get("readiness_update")

        system_parts = [
            persona.get("system_prompt", ""),
            "",
            "--- Session Context ---",
            f"Goal: {goal}",
            f"Classification: {session_data.get('classification', 'CUI')}",
            f"Impact Level: {session_data.get('impact_level', 'IL5')}",
        ]
        if selected_fw:
            system_parts.append(f"Selected Frameworks: {', '.join(selected_fw)}")
        system_parts.append(f"Requirements captured so far: {req_count}")

        # Inject active elicitation technique (BMAD pattern)
        active_tech_prompt = ctx.get("active_technique_prompt")
        if active_tech_prompt:
            system_parts.append("")
            system_parts.append("--- Active Elicitation Technique ---")
            system_parts.append(active_tech_prompt)
            system_parts.append(
                "IMPORTANT: Frame your response using the active technique above. "
                "Ask questions that align with the technique's approach."
            )

        if readiness:
            system_parts.append(
                f"Readiness score: {readiness.get('overall', 0):.0%}"
            )
            # Show per-dimension scores so the agent targets weak areas
            dims = {
                "completeness": readiness.get("completeness", 0),
                "clarity": readiness.get("clarity", 0),
                "feasibility": readiness.get("feasibility", 0),
                "compliance": readiness.get("compliance", 0),
                "testability": readiness.get("testability", 0),
            }
            dim_strs = [f"  {k}: {v:.0%}" for k, v in dims.items()]
            system_parts.append("Readiness by dimension:")
            system_parts.extend(dim_strs)
            # Identify the weakest dimension and hint what to ask
            weakest = min(dims, key=dims.get)
            dim_probes = {
                "completeness": (
                    "Completeness is low — ask about requirement types not yet "
                    "covered (e.g., performance, security, data, integration, "
                    "usability, deployment). Probe for missing user roles, "
                    "workflows, or edge cases."
                ),
                "clarity": (
                    "Clarity is low — some requirements use vague language. "
                    "Ask the customer to quantify terms (e.g., 'how many users?', "
                    "'what response time?', 'what does success look like?')."
                ),
                "feasibility": (
                    "Feasibility is low — ask about constraints: available "
                    "timeline, team size, technology limitations, existing "
                    "systems to integrate with, hosting environment."
                ),
                "compliance": (
                    "Compliance is low — ask about security requirements: "
                    "authentication (CAC/PIV, MFA), encryption (FIPS 140-2), "
                    "audit logging, access controls, data handling rules, "
                    "or any specific NIST/STIG/FedRAMP controls."
                ),
                "testability": (
                    "Testability is low — ask the customer to define acceptance "
                    "criteria: 'How would you verify this works?', 'What does "
                    "a successful outcome look like?', 'What are the pass/fail "
                    "conditions?'"
                ),
            }
            system_parts.append("")
            system_parts.append(f"PRIORITY: {dim_probes.get(weakest, '')}")

        # Add conversation coverage analysis
        cov = signals.get("coverage")
        if cov:
            system_parts.append("")
            system_parts.append("--- Conversation Coverage ---")
            system_parts.append(cov["summary"])
            system_parts.append(
                "IMPORTANT: Do NOT ask generic questions. Analyze what the customer "
                "has already told you and ask about a SPECIFIC missing topic from "
                "the list above. Reference what they said to show you were listening."
            )

        # Add URL content fetched from customer message
        url_contents = signals.get("url_contents", [])
        if url_contents:
            system_parts.append("")
            system_parts.append("--- URLs Referenced by Customer ---")
            for uc in url_contents:
                system_parts.append(f"URL: {uc['url']}")
                if uc.get("title"):
                    system_parts.append(f"Title: {uc['title']}")
                system_parts.append(f"Content: {uc['summary']}")
                system_parts.append("")
            system_parts.append(
                "IMPORTANT: The customer shared URL(s). Review the content above and "
                "reference relevant details in your response. Extract any requirements "
                "or context from the linked content. Show the customer you reviewed "
                "their link."
            )

        # Add uploaded document context
        doc_rows = conn.execute(
            "SELECT file_name, document_type, extracted_requirements_count, "
            "extracted_sections FROM intake_documents WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        if doc_rows:
            system_parts.append("")
            system_parts.append("--- Uploaded Documents ---")
            for dr in doc_rows:
                d = dict(dr)
                system_parts.append(
                    f"Document: {d['file_name']} (type: {d['document_type']}, "
                    f"{d['extracted_requirements_count']} requirements extracted)"
                )
                if d.get("extracted_sections"):
                    try:
                        sections = json.loads(d["extracted_sections"])
                        if isinstance(sections, dict):
                            if sections.get("description"):
                                system_parts.append(
                                    f"  Content: {sections['description'][:300]}"
                                )
                            if sections.get("category"):
                                system_parts.append(
                                    f"  Category: {sections['category']}"
                                )
                    except (json.JSONDecodeError, TypeError):
                        pass
            # Include document-extracted requirements as context
            doc_reqs = conn.execute(
                "SELECT raw_text, requirement_type FROM intake_requirements "
                "WHERE session_id = ? AND source_document IS NOT NULL "
                "ORDER BY created_at LIMIT 20",
                (session_id,),
            ).fetchall()
            if doc_reqs:
                system_parts.append("Requirements from documents:")
                for dr in doc_reqs:
                    d = dict(dr)
                    system_parts.append(
                        f"  - [{d['requirement_type'].upper()}] {d['raw_text'][:120]}"
                    )
            system_parts.append(
                "Reference the uploaded document content when asking follow-up "
                "questions. Use extracted requirements as context to ask deeper, "
                "more specific questions about the customer's needs."
            )

        # Add signal summary for this turn
        signal_notes = []
        extracted_reqs = signals.get("extracted_reqs", [])
        if extracted_reqs:
            signal_notes.append(
                f"Extracted {len(extracted_reqs)} requirement(s) this turn."
            )
        ambiguities = signals.get("ambiguities", [])
        if ambiguities:
            terms = [a["phrase"] for a in ambiguities]
            signal_notes.append(
                f"Ambiguous terms detected: {', '.join(terms)}"
            )
        boundary_flags = signals.get("boundary_flags", [])
        if boundary_flags:
            tiers = [f["tier"] for f in boundary_flags]
            signal_notes.append(
                f"ATO boundary flags: {', '.join(tiers)}"
            )
        gap_signals = signals.get("gap_signals", [])
        if gap_signals:
            signal_notes.append(
                f"Gap signals: {'; '.join(gap_signals[:3])}"
            )
        if signal_notes:
            system_parts.append("")
            system_parts.append("--- This Turn ---")
            system_parts.extend(signal_notes)

        # Structured clarification questions (D159, spec-kit Pattern 4)
        clarifications = signals.get("clarification_signals", [])
        if clarifications:
            system_parts.append("")
            system_parts.append("--- Priority Clarification Questions ---")
            for cq in clarifications[:3]:
                system_parts.append(
                    f"  [P{cq.get('priority', '?')}] {cq.get('question', '')}"
                )
            system_parts.append(
                "IMPORTANT: Weave ONE of these clarification questions into your "
                "response naturally. Do not ask all at once."
            )

        # Parallel execution opportunities (D161, spec-kit Pattern 7)
        parallel_opps = signals.get("parallel_opportunities", [])
        if parallel_opps:
            system_parts.append("")
            system_parts.append("--- Parallel Execution Opportunities ---")
            system_parts.append(
                f"Detected {len(parallel_opps)} group(s) of independent tasks "
                "that could run concurrently."
            )
            system_parts.append(
                "Mention this when discussing implementation timeline."
            )

        system_parts.append("")
        system_parts.append(
            "Respond in character. Acknowledge what the customer said, reference "
            "any extracted requirements or issues, and ask a follow-up question "
            "that drives toward completeness. Keep the response concise (2-4 paragraphs)."
        )

        system_prompt = "\n".join(system_parts)

        router = get_router()
        request = _LLMRequest(
            messages=conversation_messages,
            system_prompt=system_prompt,
            max_tokens=1024,
            temperature=0.7,
            agent_id="icdev-requirements-analyst",
            project_id=session_data.get("project_id", ""),
            classification=session_data.get("classification", "CUI"),
        )
        response = router.invoke("intake_persona_response", request)
        if response and response.content:
            return response.content.strip()
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(
    project_id: str,
    customer_name: str,
    customer_org: str = None,
    impact_level: str = "IL5",
    classification: str = None,
    created_by: str = "icdev-requirements-analyst",
    db_path=None,
    role: str = "developer",
    goal: str = "build",
    selected_frameworks=None,
    custom_role_description: str = "",
) -> dict:
    """Create a new intake session. Returns session data dict.

    Classification is resolved dynamically (ADR D132):
    - If provided explicitly, use that value.
    - If None, resolve from project metadata (classification + impact_level).
    - Public / IL2 -> "PUBLIC" (no marking required).
    - IL4/IL5 -> "CUI", IL6 -> "SECRET" (backward compat per ADR D54).
    """
    session_id = _generate_id("sess")
    conn = _get_connection(db_path)

    # Validate project exists if provided
    if project_id:
        row = conn.execute(
            "SELECT id, classification, impact_level FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if not row:
            conn.close()
            raise ValueError(f"Project '{project_id}' not found in database.")

        # Resolve classification from project if not provided
        if classification is None:
            proj = dict(row) if hasattr(row, "keys") else {"classification": row[1], "impact_level": row[2]}
            cls_val = (proj.get("classification") or "").upper()
            il_val = (proj.get("impact_level") or "").upper()
            if cls_val == "PUBLIC" or il_val == "IL2":
                classification = "PUBLIC"
            elif cls_val in ("SECRET", "TOP SECRET", "TOP_SECRET") or il_val == "IL6":
                classification = "SECRET"
            else:
                classification = "CUI"
    else:
        if classification is None:
            classification = "CUI"

    conn.execute(
        """INSERT INTO intake_sessions
           (id, project_id, customer_name, customer_org, session_status,
            classification, impact_level, created_by)
           VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
        (session_id, project_id, customer_name, customer_org,
         classification, impact_level, created_by),
    )

    # Store session context (role, goal, frameworks, custom description)
    context = {
        "role": role,
        "goal": goal,
        "selected_frameworks": selected_frameworks or [],
        "custom_role_description": custom_role_description,
    }
    conn.execute("UPDATE intake_sessions SET context_summary = ? WHERE id = ?",
                 (json.dumps(context), session_id))

    # Insert initial system turn
    conn.execute(
        """INSERT INTO intake_conversation
           (session_id, turn_number, role, content, content_type)
           VALUES (?, 0, 'system', ?, 'text')""",
        (session_id, json.dumps({
            "event": "session_created",
            "project_id": project_id,
            "customer_name": customer_name,
            "impact_level": impact_level,
        })),
    )

    # Generate persona-appropriate welcome message
    default_welcome = (
        f"Session created. Welcome, {customer_name}. "
        f"I'm the ICDEV Requirements Analyst. I'll help capture and "
        f"structure your requirements for a {impact_level} system. "
        f"Let's start with the mission context — what problem does "
        f"this system need to solve?"
    )
    welcome_message = default_welcome

    persona = _load_persona(role, custom_role_description)
    if persona:
        # Try LLM-powered welcome
        llm_welcome = None
        if _HAS_LLM:
            try:
                fw_text = ""
                if selected_frameworks:
                    fw_text = f" Compliance frameworks: {', '.join(selected_frameworks)}."
                opening_system = (
                    f"{persona.get('system_prompt', '')}\n\n"
                    f"You are starting a requirements intake session with "
                    f"{customer_name} from {customer_org or 'their organization'}. "
                    f"Impact level: {impact_level}. Classification: {classification}. "
                    f"Goal: {goal}.{fw_text}\n\n"
                    f"Introduce yourself briefly in your role and ask your opening "
                    f"question. Keep it to 2-3 sentences."
                )
                router = get_router()
                request = _LLMRequest(
                    messages=[{"role": "user", "content": "Begin the intake session."}],
                    system_prompt=opening_system,
                    max_tokens=512,
                    temperature=0.7,
                    agent_id="icdev-requirements-analyst",
                    project_id=project_id,
                    classification=classification or "CUI",
                )
                resp = router.invoke("intake_persona_response", request)
                if resp and resp.content:
                    llm_welcome = resp.content.strip()
            except Exception:
                pass

        if llm_welcome:
            welcome_message = llm_welcome
        elif persona.get("opening_question"):
            welcome_message = persona["opening_question"].strip()

    # Store welcome as first analyst turn (turn_number=1)
    conn.execute(
        """INSERT INTO intake_conversation
           (session_id, turn_number, role, content, content_type, classification)
           VALUES (?, 1, 'analyst', ?, 'text', ?)""",
        (session_id, welcome_message, classification or "CUI"),
    )

    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="intake_session_created",
            actor=created_by,
            action=f"Created intake session {session_id} for {customer_name}",
            project_id=project_id,
            details={"session_id": session_id, "impact_level": impact_level},
        )

    return {
        "status": "ok",
        "session_id": session_id,
        "project_id": project_id,
        "customer_name": customer_name,
        "customer_org": customer_org,
        "impact_level": impact_level,
        "session_status": "active",
        "readiness_score": 0.0,
        "message": welcome_message,
    }


def get_session(session_id: str, db_path=None) -> dict:
    """Get session status and summary."""
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    session = dict(row)

    # Get counts
    req_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM intake_requirements WHERE session_id = ?",
        (session_id,),
    ).fetchone()["cnt"]

    turn_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM intake_conversation WHERE session_id = ?",
        (session_id,),
    ).fetchone()["cnt"]

    decomp_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM safe_decomposition WHERE session_id = ?",
        (session_id,),
    ).fetchone()["cnt"]

    doc_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM intake_documents WHERE session_id = ?",
        (session_id,),
    ).fetchone()["cnt"]

    conn.close()

    session["requirement_count"] = req_count
    session["turn_count"] = turn_count
    session["decomposition_count"] = decomp_count
    session["document_count"] = doc_count

    return session


def resume_session(session_id: str, db_path=None) -> dict:
    """Resume a paused session. Returns context summary and last state."""
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    session = dict(row)
    if session["session_status"] not in ("active", "paused"):
        conn.close()
        raise ValueError(
            f"Session '{session_id}' is {session['session_status']}, cannot resume."
        )

    # Get last 5 conversation turns for context
    recent_turns = conn.execute(
        """SELECT turn_number, role, content, content_type
           FROM intake_conversation
           WHERE session_id = ?
           ORDER BY turn_number DESC LIMIT 5""",
        (session_id,),
    ).fetchall()
    recent_turns = [dict(t) for t in reversed(recent_turns)]

    # Get requirement summary
    reqs = conn.execute(
        """SELECT id, raw_text, requirement_type, priority, status
           FROM intake_requirements
           WHERE session_id = ?
           ORDER BY created_at""",
        (session_id,),
    ).fetchall()
    req_summary = [dict(r) for r in reqs]

    # Update status to active
    conn.execute(
        "UPDATE intake_sessions SET session_status = 'active', updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), session_id),
    )
    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="intake_session_resumed",
            actor="icdev-requirements-analyst",
            action=f"Resumed intake session {session_id}",
            project_id=session.get("project_id"),
            details={"session_id": session_id},
        )

    return {
        "status": "ok",
        "session_id": session_id,
        "session_status": "active",
        "readiness_score": session.get("readiness_score", 0.0),
        "context_summary": session.get("context_summary", ""),
        "requirement_count": len(req_summary),
        "recent_turns": recent_turns,
        "requirements": req_summary,
        "message": f"Session resumed. You have {len(req_summary)} requirements captured "
                   f"with readiness score {session.get('readiness_score', 0.0):.1%}. "
                   f"Where would you like to continue?",
    }


def pause_session(session_id: str, db_path=None) -> dict:
    """Pause a session for later resumption."""
    conn = _get_connection(db_path)
    conn.execute(
        "UPDATE intake_sessions SET session_status = 'paused', updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), session_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "session_id": session_id, "session_status": "paused"}


# ---------------------------------------------------------------------------
# Conversation processing
# ---------------------------------------------------------------------------

def process_turn(
    session_id: str,
    customer_message: str,
    db_path=None,
) -> dict:
    """Process a customer message turn. Extracts requirements, detects gaps,
    and generates analyst response.

    This is the core function that the agent chat calls on each turn.
    It stores the customer message, analyzes it for requirements and issues,
    and returns a structured response.
    """
    conn = _get_connection(db_path)

    # Verify session exists and is active
    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")
    if dict(session)["session_status"] != "active":
        conn.close()
        raise ValueError(f"Session is {dict(session)['session_status']}, not active.")

    session_data = dict(session)

    # Get current turn number
    last_turn = conn.execute(
        "SELECT MAX(turn_number) as max_turn FROM intake_conversation WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    turn_number = (last_turn["max_turn"] or 0) + 1

    # Store customer turn
    conn.execute(
        """INSERT INTO intake_conversation
           (session_id, turn_number, role, content, content_type, classification)
           VALUES (?, ?, 'customer', ?, 'text', ?)""",
        (session_id, turn_number, customer_message, session_data.get("classification", "CUI")),
    )

    # --- Requirement extraction (deterministic keyword analysis) ---
    extracted_reqs = _extract_requirements_from_text(
        customer_message, session_id, turn_number, conn
    )

    # --- URL detection and content fetching ---
    url_contents = _extract_and_fetch_urls(customer_message)

    # --- Ambiguity detection (with dedup across turns) ---
    raw_ambiguities = _detect_ambiguities_in_text(customer_message)

    # Load previously flagged terms from context so we don't re-flag them
    context = {}
    try:
        context = json.loads(session_data.get("context_summary") or "{}")
    except (ValueError, TypeError):
        pass
    flagged_terms = set(context.get("flagged_ambiguities", []))
    ambiguities = [a for a in raw_ambiguities if a["phrase"].lower() not in flagged_terms]

    # Record newly flagged terms so future turns skip them
    if ambiguities:
        for a in ambiguities:
            flagged_terms.add(a["phrase"].lower())
        context["flagged_ambiguities"] = sorted(flagged_terms)
        conn.execute(
            "UPDATE intake_sessions SET context_summary = ? WHERE id = ?",
            (json.dumps(context), session_id),
        )

    # --- Gap signals ---
    gap_signals = _detect_gap_signals(customer_message, session_id, conn)

    # --- Boundary flags ---
    boundary_flags = _detect_boundary_signals(customer_message, session_data)

    # --- DevSecOps / ZTA signals (Phase 24) ---
    devsecops_signals = _detect_devsecops_signals(customer_message)
    zta_signals = _detect_zta_signals(customer_message)

    # --- MOSA signals (Phase 26, D125) ---
    mosa_signals = _detect_mosa_signals(customer_message, session_data)

    # --- Dev profile signals (Phase 34, D184-D188) ---
    dev_profile_signals = _detect_dev_profile_signals(customer_message, session_data)

    # --- AI governance signals (Phase 50, D322) ---
    ai_governance_signals = _detect_ai_governance_signals(customer_message, session_data)

    # --- Structured clarification (D159, spec-kit Pattern 4) ---
    clarification_signals = []
    try:
        from tools.requirements.clarification_engine import analyze_requirements_clarity
        db_path_resolved = db_path or DB_PATH
        clarity_result = analyze_requirements_clarity(
            session_id, max_questions=3, db_path=db_path_resolved,
        )
        clarification_signals = clarity_result.get("questions", [])
    except (ImportError, Exception):
        pass

    # --- Detect parallel opportunities (D161, spec-kit Pattern 7) ---
    parallel_opportunities = []
    try:
        from tools.requirements.decomposition_engine import detect_parallel_groups
        db_path_resolved = db_path or DB_PATH
        parallel_opportunities = detect_parallel_groups(session_id, db_path=db_path_resolved)
    except (ImportError, Exception):
        pass

    # --- Update session counters ---
    req_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM intake_requirements WHERE session_id = ?",
        (session_id,),
    ).fetchone()["cnt"]

    conn.execute(
        """UPDATE intake_sessions
           SET total_requirements = ?,
               ambiguity_count = ambiguity_count + ?,
               updated_at = ?
           WHERE id = ?""",
        (req_count, len(ambiguities), datetime.now(timezone.utc).isoformat(), session_id),
    )

    # --- BDD preview generation + store as acceptance criteria ---
    # Must run BEFORE readiness so testability score reflects BDD criteria
    bdd_previews = []
    try:
        from tools.requirements.decomposition_engine import generate_bdd_criteria
        for req in extracted_reqs:
            gherkin = generate_bdd_criteria(req["raw_text"], req["requirement_type"])
            bdd_previews.append({"requirement": req["raw_text"][:80], "gherkin": gherkin})
            # Store BDD as acceptance criteria so testability score reflects it
            if gherkin and req.get("id"):
                conn.execute(
                    "UPDATE intake_requirements SET acceptance_criteria = ? WHERE id = ?",
                    (gherkin, req["id"]),
                )
    except ImportError:
        pass

    # Flush BDD + requirement writes so the sidebar readiness poller
    # (which opens its own connection) can see them immediately.
    conn.commit()

    # --- Conversation coverage analysis (what topics are covered vs missing) ---
    coverage = _analyze_conversation_coverage(session_id, conn)

    # --- Readiness update (computed every turn, after BDD storage) ---
    readiness_update = _quick_readiness_estimate(session_id, conn)

    # --- Try LLM-powered persona response ---
    analyst_turn = turn_number + 1
    persona_signals = {
        "extracted_reqs": extracted_reqs,
        "ambiguities": ambiguities,
        "boundary_flags": boundary_flags,
        "gap_signals": gap_signals,
        "devsecops_signals": devsecops_signals,
        "zta_signals": zta_signals,
        "mosa_signals": mosa_signals,
        "ai_governance_signals": ai_governance_signals,
        "readiness_update": readiness_update,
        "total_requirements": req_count,
        "coverage": coverage,
        "url_contents": url_contents,
        "clarification_signals": clarification_signals,
        "parallel_opportunities": parallel_opportunities,
    }
    persona_response = _generate_persona_response(
        session_data, customer_message, persona_signals, conn
    )

    if persona_response is not None:
        analyst_response = persona_response
    else:
        # --- Fallback: deterministic analyst response ---
        response_parts = []

        if extracted_reqs:
            response_parts.append(
                f"I captured {len(extracted_reqs)} requirement(s) from what you described."
            )
            for req in extracted_reqs:
                response_parts.append(
                    f"  - [{req['requirement_type'].upper()}] {req['raw_text'][:100]}"
                )

        if url_contents:
            response_parts.append("\nI reviewed the link(s) you shared:")
            for uc in url_contents:
                title_part = f" ({uc['title']})" if uc.get("title") else ""
                response_parts.append(f"  - {uc['url']}{title_part}")
                if uc.get("summary") and not uc["summary"].startswith("("):
                    # Show a brief excerpt of the fetched content
                    summary_short = uc["summary"][:300]
                    response_parts.append(f"    Content: {summary_short}")

        if ambiguities:
            response_parts.append(
                f"\nI noticed {len(ambiguities)} term(s) that need clarification:"
            )
            for amb in ambiguities:
                response_parts.append(f"  - '{amb['phrase']}': {amb['clarification']}")

        if boundary_flags:
            response_parts.append("\nATO Boundary flags:")
            for flag in boundary_flags:
                response_parts.append(f"  - [{flag['tier']}] {flag['description']}")

        if gap_signals:
            response_parts.append("\nPotential gaps detected:")
            for gap in gap_signals:
                response_parts.append(f"  - {gap}")

        if devsecops_signals.get("detected_stages"):
            stages = devsecops_signals["detected_stages"]
            maturity = devsecops_signals.get("maturity_estimate", "unknown")
            response_parts.append(
                f"\nDevSecOps signals detected: {', '.join(stages)} "
                f"(estimated maturity: {maturity})"
            )
        elif devsecops_signals.get("greenfield"):
            response_parts.append(
                "\nNo existing DevSecOps tooling detected — "
                "ICDEV will configure pipeline security stages based on impact level."
            )

        if zta_signals.get("zta_detected"):
            pillars = zta_signals.get("detected_pillars", [])
            response_parts.append(
                "\nZero Trust Architecture requirement detected"
                + (f" (pillars: {', '.join(pillars)})" if pillars else "")
                + ". NIST SP 800-207 framework will be included in compliance assessment."
            )

        if mosa_signals.get("mosa_detected"):
            mosa_pillars = mosa_signals.get("detected_pillars", [])
            if mosa_signals.get("dod_ic_detected"):
                response_parts.append(
                    "\nDoD/IC customer detected — MOSA (Modular Open Systems Approach) "
                    "is required per 10 U.S.C. §4401. ICDEV will enforce modular architecture, "
                    "open standards, and interface control documentation."
                )
            else:
                response_parts.append(
                    "\nMOSA (Modular Open Systems Approach) signals detected. "
                    "MOSA framework will be included in compliance assessment."
                )
            if mosa_pillars:
                response_parts.append(
                    f"  MOSA pillars identified: {', '.join(p.replace('_', ' ') for p in mosa_pillars)}"
                )
            # Probe for missing MOSA pillars
            all_pillars = {"modular_architecture", "open_standards", "open_interfaces",
                           "data_rights", "competitive_sourcing", "continuous_assessment"}
            missing = all_pillars - set(mosa_pillars)
            if missing and len(mosa_pillars) < 4:
                probes = {
                    "open_interfaces": "Do you have existing Interface Control Documents (ICDs) or API specifications?",
                    "data_rights": "What are the government data rights requirements? (GPR, unlimited rights, etc.)",
                    "competitive_sourcing": "Is multi-vendor replaceability a requirement?",
                    "open_standards": "Which standard protocols/data formats will be used? (REST/OpenAPI, gRPC, JSON, Protobuf)",
                    "modular_architecture": "Is the system designed with modular, loosely-coupled components?",
                    "continuous_assessment": "Will there be ongoing architecture reviews and modularity metrics?",
                }
                probe_q = [probes[p] for p in sorted(missing) if p in probes][:2]
                if probe_q:
                    response_parts.append("\nTo complete MOSA assessment, please clarify:")
                    for q in probe_q:
                        response_parts.append(f"  - {q}")

        if ai_governance_signals.get("ai_governance_detected"):
            gov_pillars = ai_governance_signals.get("detected_pillars", [])
            if ai_governance_signals.get("federal_agency_detected"):
                response_parts.append(
                    "\nFederal agency detected — AI governance requirements apply per OMB M-25-21. "
                    "ICDEV will track AI inventory, model documentation, human oversight, "
                    "impact assessments, transparency, and accountability."
                )
            else:
                response_parts.append(
                    "\nAI/ML system usage detected. AI governance framework will be "
                    "included in compliance assessment."
                )
            if gov_pillars:
                response_parts.append(
                    f"  AI governance pillars identified: {', '.join(p.replace('_', ' ') for p in gov_pillars)}"
                )
            # Probe for missing governance pillars
            all_gov_pillars = {"ai_inventory", "model_documentation", "human_oversight",
                               "impact_assessment", "transparency", "accountability"}
            missing_gov = all_gov_pillars - set(gov_pillars)
            if missing_gov and len(gov_pillars) < 4:
                gov_probes = {
                    "ai_inventory": "Does this system use AI/ML models? If so, what types (classification, NLP, recommendation, generation)?",
                    "model_documentation": "Are there existing model cards or documentation for the AI models used?",
                    "human_oversight": "What human oversight is in place for AI decisions? Is there an appeal process?",
                    "impact_assessment": "Has an algorithmic impact assessment been conducted? Does the AI make rights-impacting decisions?",
                    "transparency": "Are users notified when AI is making or supporting decisions?",
                    "accountability": "Is there a designated Chief AI Officer (CAIO) or responsible official?",
                }
                probe_q = [gov_probes[p] for p in sorted(missing_gov) if p in gov_probes][:2]
                if probe_q:
                    response_parts.append("\nTo complete AI governance assessment, please clarify:")
                    for q in probe_q:
                        response_parts.append(f"  - {q}")

        # Add readiness update and targeted follow-up question
        if readiness_update:
            response_parts.append(
                f"\nReadiness: {readiness_update['overall']:.0%} "
                f"(completeness={readiness_update['completeness']:.0%}, "
                f"clarity={readiness_update['clarity']:.0%}, "
                f"feasibility={readiness_update['feasibility']:.0%}, "
                f"compliance={readiness_update['compliance']:.0%}, "
                f"testability={readiness_update['testability']:.0%})"
            )

            # Ask a targeted question to improve the weakest dimension
            dims = {
                "completeness": readiness_update.get("completeness", 0),
                "clarity": readiness_update.get("clarity", 0),
                "feasibility": readiness_update.get("feasibility", 0),
                "compliance": readiness_update.get("compliance", 0),
                "testability": readiness_update.get("testability", 0),
            }
            weakest = min(dims, key=dims.get)

            # Use coverage-based targeted questions for completeness
            # instead of generic "describe user roles, workflows..."
            cov = persona_signals.get("coverage", {})
            missing_qs = cov.get("missing_questions", [])

            followup_questions = {
                "completeness": (
                    missing_qs[0] if missing_qs else
                    "What other capabilities or workflows should this system support?"
                ),
                "clarity": (
                    "Could you quantify some of the requirements? "
                    "For example, expected number of users, response times, or data volumes."
                ),
                "feasibility": (
                    "What's the target timeline, team size, "
                    "and hosting environment? Any technology constraints?"
                ),
                "compliance": (
                    "What security requirements apply? "
                    "For example: authentication method (CAC/PIV, MFA), encryption "
                    "standards, audit logging, or access control policies."
                ),
                "testability": (
                    "How would you verify each requirement works? "
                    "What are the pass/fail conditions or acceptance criteria?"
                ),
            }
            if dims[weakest] < 0.7:
                response_parts.append(f"\n{followup_questions[weakest]}")

        # Structured clarification from Impact × Uncertainty matrix (D159)
        if clarification_signals:
            top_q = clarification_signals[0]
            question_text = top_q.get("question", "")
            if question_text:
                response_parts.append(f"\nTo help me clarify: {question_text}")

        # Parallel execution opportunities (D161)
        if parallel_opportunities:
            response_parts.append(
                f"\nNote: I've identified {len(parallel_opportunities)} group(s) of "
                "independent tasks that could run in parallel to speed up delivery."
            )

        analyst_response = "\n".join(response_parts) if response_parts else (
            "Thank you. Could you tell me more about the specific capabilities "
            "you need? For example, what are the key user workflows?"
        )

    # Store analyst turn
    conn.execute(
        """INSERT INTO intake_conversation
           (session_id, turn_number, role, content, content_type,
            extracted_requirements, metadata, classification)
           VALUES (?, ?, 'analyst', ?, 'text', ?, ?, ?)""",
        (
            session_id,
            analyst_turn,
            analyst_response,
            json.dumps([r["id"] for r in extracted_reqs]) if extracted_reqs else None,
            json.dumps({
                "ambiguities_found": len(ambiguities),
                "requirements_extracted": len(extracted_reqs),
                "boundary_flags": len(boundary_flags),
                "devsecops_stages_detected": devsecops_signals.get("detected_stages", []),
                "devsecops_maturity_estimate": devsecops_signals.get("maturity_estimate"),
                "zta_detected": zta_signals.get("zta_detected", False),
                "zta_pillars_detected": zta_signals.get("detected_pillars", []),
                "mosa_detected": mosa_signals.get("mosa_detected", False),
                "mosa_dod_ic_detected": mosa_signals.get("dod_ic_detected", False),
                "mosa_pillars_detected": mosa_signals.get("detected_pillars", []),
                "ai_governance_detected": ai_governance_signals.get("ai_governance_detected", False),
                "ai_governance_pillars_detected": ai_governance_signals.get("detected_pillars", []),
                "ai_governance_federal_agency": ai_governance_signals.get("federal_agency_detected", False),
                "clarification_questions": len(clarification_signals),
                "parallel_groups": len(parallel_opportunities),
            }),
            session_data.get("classification", "CUI"),
        ),
    )

    conn.commit()
    conn.close()

    # Audit log
    if _HAS_AUDIT and extracted_reqs:
        log_event(
            event_type="requirement_captured",
            actor="icdev-requirements-analyst",
            action=f"Extracted {len(extracted_reqs)} requirement(s) from turn {turn_number}",
            project_id=session_data.get("project_id"),
            details={
                "session_id": session_id,
                "turn": turn_number,
                "count": len(extracted_reqs),
            },
        )

    return {
        "status": "ok",
        "session_id": session_id,
        "turn_number": analyst_turn,
        "analyst_response": analyst_response,
        "extracted_requirements": extracted_reqs,
        "ambiguities": ambiguities,
        "boundary_flags": boundary_flags,
        "gap_signals": gap_signals,
        "readiness_update": readiness_update,
        "total_requirements": req_count + len(extracted_reqs),
        "bdd_previews": bdd_previews,
        "url_contents": url_contents,
    }


# ---------------------------------------------------------------------------
# Requirement extraction helpers
# ---------------------------------------------------------------------------

# Requirement type detection keywords
_REQ_TYPE_KEYWORDS = {
    "security": [
        "authenticate", "authorize", "encrypt", "CAC", "PIV", "MFA",
        "FIPS", "STIG", "access control", "audit log", "credential",
        "certificate", "PKI", "RBAC", "permission", "classification",
    ],
    "performance": [
        "response time", "latency", "throughput", "concurrent",
        "availability", "uptime", "SLA", "load", "capacity",
    ],
    "interface": [
        "integrate", "interface", "API", "REST", "SOAP", "feed",
        "import", "export", "connect", "external system", "third-party",
    ],
    "data": [
        "database", "data store", "retention", "backup", "archive",
        "migrate data", "data format", "schema", "CUI data",
    ],
    "compliance": [
        "NIST", "FedRAMP", "CMMC", "ATO", "SSP", "POAM",
        "STIG", "RMF", "accreditation", "authorization boundary",
    ],
    "non_functional": [
        "scalab", "reliab", "maintainab", "portab", "usab",
        "accessibility", "WCAG", "Section 508", "i18n", "l10n",
    ],
}

# Priority detection
_PRIORITY_KEYWORDS = {
    "critical": ["must", "shall", "critical", "mandatory", "required", "essential"],
    "high": ["should", "important", "needed", "key", "primary"],
    "medium": ["could", "nice to have", "desirable", "want"],
    "low": ["may", "optional", "future", "nice", "wish"],
}


def _extract_requirements_from_text(text, session_id, turn_number, conn):
    """Extract structured requirements from customer text using keyword analysis."""
    extracted = []
    # Split on sentence boundaries
    sentences = [s.strip() for s in text.replace("\n", ". ").split(".") if s.strip() and len(s.strip()) > 10]

    for sentence in sentences:
        lower = sentence.lower()

        # Check if this sentence contains requirement-like language
        has_req_signal = any(
            kw in lower
            for kw in ["need", "want", "must", "shall", "should", "require",
                       "able to", "capability", "feature", "support", "provide",
                       "enable", "allow", "system will", "system shall"]
        )
        if not has_req_signal:
            continue

        # Detect type
        req_type = "functional"  # default
        for rtype, keywords in _REQ_TYPE_KEYWORDS.items():
            if any(kw.lower() in lower for kw in keywords):
                req_type = rtype
                break

        # Detect priority
        priority = "medium"  # default
        for prio, keywords in _PRIORITY_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                priority = prio
                break

        # Create requirement record — classification inherited from session
        req_id = _generate_id("req")
        sess_row = conn.execute(
            "SELECT classification FROM intake_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        req_classification = sess_row[0] if sess_row else "CUI"
        conn.execute(
            """INSERT INTO intake_requirements
               (id, session_id, source_turn, raw_text, requirement_type,
                priority, status, classification)
               VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)""",
            (req_id, session_id, turn_number, sentence.strip(), req_type,
             priority, req_classification),
        )

        extracted.append({
            "id": req_id,
            "raw_text": sentence.strip(),
            "requirement_type": req_type,
            "priority": priority,
        })

    return extracted


def _analyze_conversation_coverage(session_id, conn):
    """Analyze conversation history to identify covered topics and specific gaps.

    Returns dict with 'covered' (set of topic keys), 'missing' (list of
    specific gap questions), and 'summary' (string for LLM context).
    """
    # Gather all customer messages
    rows = conn.execute(
        "SELECT content FROM intake_conversation "
        "WHERE session_id = ? AND role = 'customer' ORDER BY turn_number",
        (session_id,),
    ).fetchall()
    all_text = " ".join(dict(r)["content"] for r in rows).lower()

    # Also include requirements extracted from uploaded documents
    doc_reqs = conn.execute(
        "SELECT raw_text FROM intake_requirements "
        "WHERE session_id = ? AND source_document IS NOT NULL",
        (session_id,),
    ).fetchall()
    if doc_reqs:
        doc_text = " ".join(dict(r)["raw_text"] for r in doc_reqs).lower()
        all_text += " " + doc_text

    # Topic detection with specific follow-up questions
    topics = {
        "users_roles": {
            "keywords": ["user", "role", "agent", "admin", "operator", "analyst",
                         "viewer", "customer", "personnel", "staff"],
            "covered_question": None,
            "gap_question": "Who will use this system? What are the distinct user roles and their permissions?",
        },
        "workflow": {
            "keywords": ["workflow", "process", "step", "flow", "sequence",
                         "procedure", "pipeline", "task"],
            "covered_question": None,
            "gap_question": "What's the primary user workflow from start to finish?",
        },
        "data_model": {
            "keywords": ["data", "database", "record", "field", "store", "table",
                         "schema", "entity", "model", "input", "output"],
            "covered_question": None,
            "gap_question": "What data does the system manage? What are the key entities and their relationships?",
        },
        "integration": {
            "keywords": ["integrate", "api", "rest", "connect", "external",
                         "third-party", "system", "mcp", "soap", "feed"],
            "covered_question": None,
            "gap_question": "What external systems does this integrate with? What protocols (REST, MCP, file)?",
        },
        "performance": {
            "keywords": ["performance", "sla", "uptime", "latency", "response time",
                         "concurrent", "throughput", "availability", "99"],
            "covered_question": None,
            "gap_question": "What are the performance requirements? (SLA, response times, concurrent users)",
        },
        "security_auth": {
            "keywords": ["security", "auth", "login", "cac", "piv", "mfa",
                         "encrypt", "fips", "access control", "rbac", "permission"],
            "covered_question": None,
            "gap_question": "How do users authenticate? (CAC/PIV, MFA, username/password) What access controls are needed?",
        },
        "error_handling": {
            "keywords": ["error", "fail", "exception", "retry", "fallback",
                         "validation", "invalid", "reject", "deny"],
            "covered_question": None,
            "gap_question": "What happens when something goes wrong? (validation failures, system errors, invalid inputs)",
        },
        "reporting": {
            "keywords": ["report", "dashboard", "metric", "analytics", "audit",
                         "log", "history", "export", "csv", "pdf"],
            "covered_question": None,
            "gap_question": "Does the system need reporting, audit trails, or dashboards? What metrics matter?",
        },
        "deployment": {
            "keywords": ["deploy", "host", "cloud", "aws", "govcloud", "on-prem",
                         "environment", "staging", "production", "docker", "k8s"],
            "covered_question": None,
            "gap_question": "Where will this be deployed? (AWS GovCloud, on-prem, hybrid) What environments are needed?",
        },
        "ui_ux": {
            "keywords": ["ui", "ux", "interface", "screen", "page", "form",
                         "button", "design", "mobile", "responsive", "intuitive"],
            "covered_question": None,
            "gap_question": "What should the user interface look like? (web app, mobile, desktop) Any specific UX requirements?",
        },
        "ai_governance": {
            "keywords": ["ai system", "machine learning", "ml model", "deep learning",
                         "neural network", "nlp", "computer vision", "recommendation engine",
                         "predictive model", "automated decision", "algorithmic", "chatbot",
                         "generative ai", "llm", "foundation model", "model card",
                         "human oversight", "impact assessment", "ai governance",
                         "responsible ai", "caio", "chief ai officer"],
            "covered_question": None,
            "gap_question": "Does this system use AI/ML? If so, what governance is needed (model documentation, human oversight, impact assessments)?",
        },
    }

    covered = set()
    missing_questions = []
    for topic_key, topic in topics.items():
        if any(kw in all_text for kw in topic["keywords"]):
            covered.add(topic_key)
        else:
            missing_questions.append(topic["gap_question"])

    # Build summary for LLM context
    summary_parts = [f"Topics covered ({len(covered)}/{len(topics)}): {', '.join(sorted(covered)) or 'none'}"]
    if missing_questions:
        summary_parts.append(f"Topics NOT covered: {', '.join(sorted(set(topics.keys()) - covered))}")
        summary_parts.append("Ask about ONE of these specific gaps (pick the most important):")
        for q in missing_questions[:3]:
            summary_parts.append(f"  - {q}")

    return {
        "covered": covered,
        "total_topics": len(topics),
        "missing_questions": missing_questions,
        "summary": "\n".join(summary_parts),
    }


def _extract_and_fetch_urls(text):
    """Detect URLs in customer message and fetch page content/summaries.

    Returns a list of dicts: [{"url": "...", "title": "...", "summary": "..."}]
    Only fetches HTTP/HTTPS URLs.  Best-effort — failures return a note.
    """
    url_pattern = re.compile(
        r'https?://[^\s<>\"\')]+', re.IGNORECASE
    )
    urls = url_pattern.findall(text)
    if not urls:
        return []

    results = []
    try:
        import requests as _requests
    except ImportError:
        for u in urls:
            results.append({"url": u, "title": "", "summary": "(requests library not available)"})
        return results

    for url in urls[:3]:  # limit to 3 URLs per message
        try:
            resp = _requests.get(url, timeout=10, headers={
                "User-Agent": "ICDEV-Intake-Agent/1.0",
                "Accept": "text/html,application/json,text/plain",
            })
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")

            if "application/json" in content_type:
                # JSON API — summarize top-level keys
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        summary = f"JSON with keys: {', '.join(list(data.keys())[:15])}"
                        title = data.get("name", data.get("full_name", data.get("title", "")))
                        desc = data.get("description", "")
                        if desc:
                            summary += f". Description: {desc}"
                    elif isinstance(data, list):
                        summary = f"JSON array with {len(data)} items"
                        title = ""
                    else:
                        summary = str(data)[:300]
                        title = ""
                except (ValueError, TypeError):
                    summary = resp.text[:500]
                    title = ""
            else:
                # HTML — extract title and meta description / first text
                body = resp.text[:50000]
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', body, re.I)
                title = title_match.group(1).strip() if title_match else ""

                # Try meta description
                meta_match = re.search(
                    r'<meta\s+[^>]*name=["\']description["\']\s+content=["\']([^"\']+)',
                    body, re.I
                )
                if not meta_match:
                    meta_match = re.search(
                        r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']description',
                        body, re.I
                    )
                desc = meta_match.group(1).strip() if meta_match else ""

                # Try README or about-section text for GitHub-like pages
                readme_match = re.search(
                    r'<article[^>]*class="[^"]*markdown-body[^"]*"[^>]*>(.*?)</article>',
                    body, re.I | re.S
                )
                readme_text = ""
                if readme_match:
                    # Strip HTML tags for plain text
                    raw = re.sub(r'<[^>]+>', ' ', readme_match.group(1))
                    raw = re.sub(r'\s+', ' ', raw).strip()
                    readme_text = raw[:800]

                if readme_text:
                    summary = f"{desc}. README: {readme_text}" if desc else f"README: {readme_text}"
                elif desc:
                    summary = desc
                else:
                    # Fallback: strip tags from first visible text
                    stripped = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.S | re.I)
                    stripped = re.sub(r'<style[^>]*>.*?</style>', '', stripped, flags=re.S | re.I)
                    stripped = re.sub(r'<[^>]+>', ' ', stripped)
                    stripped = re.sub(r'\s+', ' ', stripped).strip()
                    summary = stripped[:500] if stripped else "(no readable content)"

            results.append({"url": url, "title": title, "summary": summary[:1000]})

        except Exception as exc:
            results.append({"url": url, "title": "", "summary": f"(could not fetch: {exc})"})

    return results


def _detect_ambiguities_in_text(text):
    """Detect ambiguous terms using pattern matching."""
    ambiguities = []
    lower = text.lower()

    # Load patterns from context file if available
    patterns_path = BASE_DIR / "context" / "requirements" / "ambiguity_patterns.json"
    if patterns_path.exists():
        with open(patterns_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            patterns = data.get("ambiguity_patterns", [])
    else:
        # Fallback minimal patterns
        patterns = [
            {"phrase": "as needed", "severity": "high",
             "clarification": "Define specific conditions that trigger this action."},
            {"phrase": "appropriate", "severity": "high",
             "clarification": "Define measurable criteria."},
            {"phrase": "timely", "severity": "high",
             "clarification": "Specify an exact time threshold."},
            {"phrase": "user-friendly", "severity": "medium",
             "clarification": "Define specific usability criteria."},
            {"phrase": "fast", "severity": "high",
             "clarification": "Specify a measurable target."},
            {"phrase": "secure", "severity": "critical",
             "clarification": "Specify security requirements: FIPS, STIG, controls."},
            {"phrase": "scalable", "severity": "medium",
             "clarification": "Define target scale: users, data volume."},
        ]

    for pattern in patterns:
        if pattern["phrase"].lower() in lower:
            ambiguities.append({
                "phrase": pattern["phrase"],
                "severity": pattern.get("severity", "medium"),
                "clarification": pattern.get("clarification", "Please clarify this term."),
            })

    return ambiguities


def _detect_gap_signals(text, session_id, conn):
    """Detect signals that may indicate requirement gaps."""
    signals = []
    lower = text.lower()

    # Check for external system mentions without interface detail
    interface_terms = ["integrate", "connect", "interface", "external", "feed", "third-party"]
    protocol_terms = ["rest", "api", "soap", "message queue", "file", "isa", "mou"]
    if any(t in lower for t in interface_terms) and not any(t in lower for t in protocol_terms):
        signals.append(
            "External system mentioned without interface protocol — "
            "ask about REST/SOAP/MQ and ISA/MOU requirements"
        )

    # Check for security without specifics
    if any(t in lower for t in ["secure", "security", "protect"]) and not any(
        t in lower for t in ["fips", "stig", "nist", "cac", "piv", "encrypt", "mfa"]
    ):
        signals.append(
            "Security mentioned without specifics — "
            "ask about FIPS encryption, CAC/PIV auth, STIG compliance"
        )

    # Check for data mentions without classification
    if any(t in lower for t in ["data", "information", "records"]) and not any(
        t in lower for t in ["cui", "classified", "unclassified", "fouo", "secret"]
    ):
        signals.append(
            "Data mentioned without classification — "
            "ask about CUI categories and data handling requirements"
        )

    return signals


def _detect_boundary_signals(text, session_data):
    """Detect potential ATO boundary impact signals."""
    flags = []
    lower = text.lower()
    impact_level = session_data.get("impact_level", "IL5")

    # Classification upgrade signals
    if impact_level in ("IL4", "IL5") and any(
        t in lower for t in ["secret", "ts/sci", "top secret", "classified"]
    ):
        flags.append({
            "tier": "RED",
            "description": f"Classification upgrade detected — current system is {impact_level} "
                          f"but SECRET/TS data mentioned. This would invalidate the current ATO.",
        })

    # New external interface
    if any(t in lower for t in ["new system", "new interface", "new connection", "new integration"]):
        flags.append({
            "tier": "ORANGE",
            "description": "New external interface — requires ISA/MOU and SSP Section 9 update.",
        })

    # BYOD/mobile
    if any(t in lower for t in ["mobile", "byod", "personal device", "phone", "tablet"]):
        flags.append({
            "tier": "ORANGE",
            "description": "Mobile/BYOD access — requires AC-19, MDM solution, SSP boundary update.",
        })

    # Cloud service change
    if any(t in lower for t in ["aws commercial", "azure", "gcp", "public cloud"]):
        flags.append({
            "tier": "ORANGE",
            "description": "Non-GovCloud service mentioned — current boundary is AWS GovCloud only.",
        })

    return flags


def _detect_devsecops_signals(text):
    """Detect DevSecOps maturity signals from customer text (Phase 24).

    Uses keyword matching from args/devsecops_config.yaml to identify existing
    security tooling and estimate maturity level.
    """
    try:
        from tools.devsecops.profile_manager import detect_maturity_from_text
        return detect_maturity_from_text(text)
    except (ImportError, Exception):
        # Fallback: inline minimal detection
        lower = text.lower()
        detected = []
        keyword_map = {
            "sast": ["static analysis", "code scanning", "bandit", "sonarqube", "fortify"],
            "sca": ["dependency scan", "pip-audit", "snyk", "npm audit"],
            "secret_detection": ["secret scanning", "gitleaks", "detect-secrets"],
            "container_scan": ["container scanning", "trivy", "grype", "image scanning"],
            "policy_as_code": ["policy as code", "opa", "gatekeeper", "kyverno"],
            "image_signing": ["image signing", "cosign", "sigstore"],
        }
        for stage, keywords in keyword_map.items():
            if any(kw in lower for kw in keywords):
                detected.append(stage)
        greenfield = any(s in lower for s in [
            "no security scanning", "greenfield", "starting from scratch",
        ])
        return {
            "detected_stages": sorted(set(detected)),
            "maturity_estimate": "level_1_initial" if greenfield else (
                "level_3_defined" if len(detected) >= 4 else
                "level_2_managed" if len(detected) >= 2 else
                "level_1_initial"
            ),
            "zta_detected": False,
            "greenfield": greenfield,
            "stage_count": len(detected),
        }


def _detect_zta_signals(text):
    """Detect Zero Trust Architecture signals from customer text (Phase 24-25).

    Identifies ZTA-relevant keywords and maps them to ZTA pillars.
    """
    lower = text.lower()
    zta_detected = False
    detected_pillars = []

    # General ZTA indicators
    general_keywords = [
        "zero trust", "nist 800-207", "never trust always verify",
        "zero trust architecture",
    ]
    if any(kw in lower for kw in general_keywords):
        zta_detected = True

    # Pillar-specific keywords
    pillar_keywords = {
        "user_identity": ["mfa", "multi-factor", "cac", "piv", "identity provider",
                          "sso", "single sign-on", "continuous auth", "icam"],
        "device": ["device posture", "mdm", "endpoint detection", "device trust",
                   "device compliance", "edr"],
        "network": ["micro-segmentation", "microsegmentation", "mtls", "mutual tls",
                    "service mesh", "istio", "linkerd", "network policy",
                    "software-defined perimeter", "ztna"],
        "application_workload": ["workload identity", "container hardening",
                                 "admission control", "signed images"],
        "data": ["data classification", "encryption at rest", "dlp",
                 "data loss prevention", "tokenization"],
        "visibility_analytics": ["siem", "continuous monitoring", "anomaly detection",
                                 "threat intelligence", "security analytics"],
        "automation_orchestration": ["soar", "auto-remediation", "security orchestration",
                                     "automated response", "self-healing"],
    }

    for pillar, keywords in pillar_keywords.items():
        if any(kw in lower for kw in keywords):
            detected_pillars.append(pillar)
            zta_detected = True

    return {
        "zta_detected": zta_detected,
        "detected_pillars": sorted(detected_pillars),
        "pillar_count": len(detected_pillars),
    }


def _detect_mosa_signals(text, session_data=None):
    """Detect MOSA (Modular Open Systems Approach) signals (Phase 26, D125).

    Auto-triggers for DoD/IC customers per 10 U.S.C. §4401. Also detects
    MOSA pillar keywords for targeted follow-up questions.
    """
    lower = text.lower()
    mosa_detected = False
    detected_pillars = []
    dod_ic_detected = False

    # DoD/IC customer keywords — auto-trigger MOSA (D125)
    dod_ic_keywords = [
        "department of defense", "dod", "air force", "army", "navy",
        "marine corps", "space force", "intelligence community",
        "combatant command", "acquisition program", "mdap", "acat",
        "program of record", "warfighter", "nsa", "dia", "nro", "nga",
        "military", "defense information systems",
    ]
    if any(kw in lower for kw in dod_ic_keywords):
        dod_ic_detected = True
        mosa_detected = True

    # Also check session customer_org for DoD/IC indicators
    if session_data:
        org = (session_data.get("customer_org") or "").lower()
        il = (session_data.get("impact_level") or "").upper()
        if any(kw in org for kw in ["dod", "defense", "military", "ic", "intelligence"]):
            dod_ic_detected = True
            mosa_detected = True
        if il in ("IL4", "IL5", "IL6"):
            mosa_detected = True

    # MOSA pillar keywords (from mosa_config.yaml intake_detection)
    pillar_keywords = {
        "modular_architecture": ["modular", "loosely coupled", "microservice",
                                 "component-based", "plugin", "module boundary",
                                 "encapsulation"],
        "open_standards": ["openapi", "rest api", "grpc", "protobuf",
                          "standard protocol", "open standard", "json schema"],
        "open_interfaces": ["interface control", "icd", "api versioning",
                           "backward compatible", "interface specification",
                           "integration spec"],
        "data_rights": ["data rights", "government purpose", "license tracking",
                       "source escrow", "intellectual property", "gpr",
                       "unlimited rights"],
        "competitive_sourcing": ["vendor lock-in", "vendor neutral", "competitive",
                                "replaceability", "build vs buy", "multi-vendor",
                                "plug-and-play"],
        "continuous_assessment": ["architecture review", "modularity metrics",
                                 "design review", "architecture evolution",
                                 "technology refresh"],
    }

    for pillar, keywords in pillar_keywords.items():
        if any(kw in lower for kw in keywords):
            detected_pillars.append(pillar)
            mosa_detected = True

    return {
        "mosa_detected": mosa_detected,
        "dod_ic_detected": dod_ic_detected,
        "detected_pillars": sorted(detected_pillars),
        "pillar_count": len(detected_pillars),
    }


def _detect_dev_profile_signals(text, session_data=None):
    """Detect development profile signals from customer text (Phase 34, D184-D188).

    Identifies coding standards, tooling preferences, and development methodology
    signals to recommend or auto-apply a development profile template.
    """
    try:
        from tools.builder.profile_detector import detect_from_text
        raw = detect_from_text(text)
        # Normalize to expected shape
        signals = raw.get("detected_signals", {})
        return {
            "profile_detected": raw.get("signal_count", 0) > 0,
            "detected_dimensions": sorted(signals.keys()),
            "dimension_count": raw.get("signal_count", 0),
            "suggested_templates": [],
            "raw_signals": signals,
        }
    except (ImportError, Exception):
        pass

    # Fallback: inline minimal keyword detection
    lower = text.lower()
    detected_dimensions = []

    dimension_keywords = {
        "language": ["python", "java", "go", "golang", "rust", "typescript", "c#",
                     "csharp", ".net", "flask", "fastapi", "spring boot", "express"],
        "style": ["snake_case", "camelcase", "camel case", "naming convention",
                  "code style", "indent", "line length", "prettier", "black",
                  "eslint", "ruff", "gofmt", "formatting", "linter"],
        "testing": ["tdd", "bdd", "test driven", "test coverage", "unit test",
                    "e2e test", "cucumber", "behave", "jest", "pytest"],
        "architecture": ["microservice", "monolith", "api gateway", "rest",
                         "graphql", "event driven", "hexagonal", "layered"],
        "security": ["fips", "encryption", "secret management", "sast",
                     "container hardening", "stig", "vulnerability"],
        "operations": ["kubernetes", "k8s", "docker", "docker compose",
                       "gitlab ci", "github actions", "jenkins", "air-gapped"],
        "git": ["trunk-based", "gitflow", "github flow", "squash merge",
                "conventional commits", "branch naming"],
        "ai": ["bedrock", "openai", "ollama", "byok", "token budget",
               "llm", "ai model", "code generation model"],
    }

    for dim, keywords in dimension_keywords.items():
        if any(kw in lower for kw in keywords):
            detected_dimensions.append(dim)

    # Check for template-matching signals
    template_signals = {
        "dod_baseline": ["dod", "department of defense", "il4", "il5", "il6",
                         "cmmc", "stig"],
        "fedramp_baseline": ["fedramp", "fed ramp", "jab", "3pao"],
        "healthcare_baseline": ["hipaa", "hitrust", "phi", "health"],
        "financial_baseline": ["pci dss", "pci", "sox", "financial"],
        "law_enforcement_baseline": ["cjis", "law enforcement", "fbi"],
        "startup": ["startup", "mvp", "lean", "fast iteration"],
    }

    suggested_templates = []
    for template, keywords in template_signals.items():
        if any(kw in lower for kw in keywords):
            suggested_templates.append(template)

    return {
        "profile_detected": len(detected_dimensions) > 0,
        "detected_dimensions": sorted(detected_dimensions),
        "dimension_count": len(detected_dimensions),
        "suggested_templates": suggested_templates,
    }


def _detect_ai_governance_signals(text, session_data=None):
    """Detect AI governance signals from customer text (D322).

    Auto-triggers for federal agencies per OMB M-25-21 and any AI/ML mention.
    Detects 6 governance pillar keywords for targeted follow-up questions.
    """
    lower = text.lower()
    ai_governance_detected = False
    detected_pillars = []
    federal_agency_detected = False

    # AI/ML mention keywords — auto-trigger governance (D322)
    ai_ml_keywords = [
        "ai system", "machine learning", "ml model", "deep learning",
        "neural network", "natural language processing", "nlp",
        "computer vision", "recommendation engine", "predictive model",
        "automated decision", "algorithmic", "chatbot", "virtual assistant",
        "generative ai", "large language model", "llm", "foundation model",
    ]
    if any(kw in lower for kw in ai_ml_keywords):
        ai_governance_detected = True

    # Federal agency keywords — auto-trigger per OMB M-25-21
    federal_keywords = [
        "federal agency", "omb", "executive order", "federal government",
        "government agency", "gsa", "irs", "fda", "epa", "usda",
        "hhs", "dhs", "dot", "hud", "ed.gov", "va ", "opm",
    ]
    if any(kw in lower for kw in federal_keywords):
        federal_agency_detected = True
        ai_governance_detected = True

    # Also check session customer_org for federal indicators
    if session_data:
        org = (session_data.get("customer_org") or "").lower()
        if any(kw in org for kw in ["federal", "agency", "government", "gsa",
                                      "omb", "dod", "defense", "military"]):
            federal_agency_detected = True
            ai_governance_detected = True

    # Governance pillar keywords (from ai_governance_config.yaml)
    pillar_keywords = {
        "ai_inventory": [
            "ai system", "machine learning", "ml model", "deep learning",
            "neural network", "nlp", "computer vision", "recommendation engine",
            "predictive model", "automated decision", "algorithmic", "chatbot",
            "generative ai", "llm", "foundation model",
        ],
        "model_documentation": [
            "model card", "model documentation", "training data",
            "model performance", "model accuracy", "model bias",
            "model validation", "model versioning",
        ],
        "human_oversight": [
            "human oversight", "human in the loop", "human on the loop",
            "manual review", "human approval", "override capability",
            "escalation", "appeal process",
        ],
        "impact_assessment": [
            "impact assessment", "rights impacting", "safety critical",
            "high risk ai", "algorithmic impact", "disparate impact",
            "bias assessment", "fairness",
        ],
        "transparency": [
            "transparency", "explainability", "interpretability",
            "notice", "disclosure", "ai disclosure",
        ],
        "accountability": [
            "accountability", "responsible ai", "caio",
            "chief ai officer", "ai governance", "ethics review",
            "incident response",
        ],
    }

    for pillar, keywords in pillar_keywords.items():
        if any(kw in lower for kw in keywords):
            detected_pillars.append(pillar)
            ai_governance_detected = True

    return {
        "ai_governance_detected": ai_governance_detected,
        "federal_agency_detected": federal_agency_detected,
        "detected_pillars": sorted(detected_pillars),
        "pillar_count": len(detected_pillars),
    }


def _quick_readiness_estimate(session_id, conn):
    """Quick readiness estimate based on requirement counts and quality."""
    reqs = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ?",
        (session_id,),
    ).fetchall()

    total = len(reqs)
    if total == 0:
        return {"overall": 0.0, "completeness": 0.0, "clarity": 1.0,
                "feasibility": 0.5, "compliance": 0.0, "testability": 0.0}

    # Completeness: check if we have multiple types
    types = set(dict(r)["requirement_type"] for r in reqs)
    type_coverage = len(types) / 6.0  # 6 major types
    completeness = min(1.0, type_coverage * (min(total, 20) / 20.0))

    # Clarity: based on unresolved ambiguities vs total requirements
    # Resolved = flagged but user has continued the conversation (addressed it)
    sess_row = conn.execute(
        "SELECT ambiguity_count, context_summary FROM intake_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    sess_dict = dict(sess_row) if sess_row else {}
    amb_count = sess_dict.get("ambiguity_count", 0)
    ctx = {}
    try:
        ctx = json.loads(sess_dict.get("context_summary") or "{}")
    except (ValueError, TypeError):
        pass
    flagged = ctx.get("flagged_ambiguities", [])
    # Count user turns after ambiguities were first flagged as clarification
    turn_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM intake_conversation "
        "WHERE session_id = ? AND role = 'customer'",
        (session_id,),
    ).fetchone()["cnt"]
    # Each user turn after the first resolves ambiguity somewhat
    resolved_credit = min(len(flagged), max(0, turn_count - 1)) if flagged else 0
    unresolved = max(0, len(flagged) - resolved_credit)
    # Clarity starts at 50% (baseline for having requirements), penalized by
    # unresolved ambiguities, boosted by conversation depth
    clarity_base = 0.50
    penalty = min(0.40, unresolved * 0.15)
    depth_bonus = min(0.50, turn_count * 0.05)  # each turn adds 5%, up to 50%
    clarity = min(1.0, max(0.0, clarity_base - penalty + depth_bonus))

    # Feasibility: assume 0.5 without architect review
    feasibility = 0.5

    # Compliance: check selected frameworks + security-type requirements
    context = {}
    try:
        ctx_row = conn.execute(
            "SELECT context_summary FROM intake_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if ctx_row:
            context = json.loads(dict(ctx_row).get("context_summary") or "{}")
    except (ValueError, TypeError):
        pass
    selected_fw = context.get("selected_frameworks", [])
    sec_reqs = sum(1 for r in reqs if dict(r)["requirement_type"] in ("security", "compliance"))

    if selected_fw:
        # Selecting frameworks IS the compliance declaration — full credit.
        compliance = 1.0
    else:
        compliance = min(1.0, sec_reqs / max(3, 1))

    # Testability: check for acceptance criteria (BDD/Gherkin stored during turn)
    with_criteria = 0
    for r in reqs:
        rd = dict(r)
        ac = rd.get("acceptance_criteria") or ""
        if ac.strip():
            with_criteria += 1
    testability = with_criteria / max(total, 1)

    config = _load_config()
    weights = config.get("ricoas", {}).get("readiness_weights", {
        "completeness": 0.25, "clarity": 0.25, "feasibility": 0.20,
        "compliance": 0.15, "testability": 0.15,
    })

    overall = (
        completeness * weights.get("completeness", 0.25) +
        clarity * weights.get("clarity", 0.25) +
        feasibility * weights.get("feasibility", 0.20) +
        compliance * weights.get("compliance", 0.15) +
        testability * weights.get("testability", 0.15)
    )

    return {
        "overall": round(overall, 3),
        "completeness": round(completeness, 3),
        "clarity": round(clarity, 3),
        "feasibility": round(feasibility, 3),
        "compliance": round(compliance, 3),
        "testability": round(testability, 3),
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_requirements(session_id: str, db_path=None) -> dict:
    """Export all requirements from a session as structured JSON."""
    conn = _get_connection(db_path)
    reqs = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ? ORDER BY created_at",
        (session_id,),
    ).fetchall()

    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()

    if not session:
        raise ValueError(f"Session '{session_id}' not found.")

    return {
        "status": "ok",
        "session_id": session_id,
        "project_id": dict(session).get("project_id"),
        "customer_name": dict(session).get("customer_name"),
        "impact_level": dict(session).get("impact_level"),
        "readiness_score": dict(session).get("readiness_score", 0.0),
        "total_requirements": len(reqs),
        "requirements": [dict(r) for r in reqs],
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Requirements Intake Engine"
    )
    parser.add_argument("--project-id", help="ICDEV project ID")
    parser.add_argument("--session-id", help="Existing session ID")
    parser.add_argument("--customer-name", help="Customer name (for new session)")
    parser.add_argument("--customer-org", help="Customer organization")
    parser.add_argument(
        "--impact-level",
        choices=["IL2", "IL4", "IL5", "IL6"],
        default="IL5",
        help="Classification impact level",
    )
    parser.add_argument(
        "--classification",
        choices=["CUI", "FOUO", "Public", "SECRET", "TOP_SECRET", "NONE"],
        default=None,
        help="Data classification override (default: resolved from project)",
    )
    parser.add_argument("--message", help="Customer message (single turn)")
    parser.add_argument("--resume", action="store_true", help="Resume paused session")
    parser.add_argument("--pause", action="store_true", help="Pause active session")
    parser.add_argument("--score-readiness", action="store_true", help="Score readiness")
    parser.add_argument("--export", action="store_true", help="Export requirements")
    parser.add_argument("--status", action="store_true", help="Get session status")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        result = None

        if args.session_id and args.resume:
            result = resume_session(args.session_id)

        elif args.session_id and args.pause:
            result = pause_session(args.session_id)

        elif args.session_id and args.message:
            result = process_turn(args.session_id, args.message)

        elif args.session_id and args.score_readiness:
            conn = _get_connection()
            readiness = _quick_readiness_estimate(args.session_id, conn)
            conn.close()
            result = {"status": "ok", "session_id": args.session_id, "readiness": readiness}

        elif args.session_id and args.export:
            result = export_requirements(args.session_id)

        elif args.session_id and args.status:
            result = get_session(args.session_id)

        elif args.customer_name and args.project_id:
            # Resolve classification: NONE -> "PUBLIC", None -> resolve from project
            cls_override = args.classification
            if cls_override == "NONE":
                cls_override = "PUBLIC"
            result = create_session(
                project_id=args.project_id,
                customer_name=args.customer_name,
                customer_org=args.customer_org,
                impact_level=args.impact_level,
                classification=cls_override,
            )

        else:
            parser.print_help()
            return

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if "message" in result:
                print(result["message"])
            elif "analyst_response" in result:
                print(result["analyst_response"])
            else:
                print(json.dumps(result, indent=2, default=str))

    except (ValueError, FileNotFoundError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
