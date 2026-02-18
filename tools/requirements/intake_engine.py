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
import sqlite3
import uuid
from datetime import datetime
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


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(
    project_id: str,
    customer_name: str,
    customer_org: str = None,
    impact_level: str = "IL5",
    classification: str = "CUI",
    created_by: str = "icdev-requirements-analyst",
    db_path=None,
) -> dict:
    """Create a new intake session. Returns session data dict."""
    session_id = _generate_id("sess")
    conn = _get_connection(db_path)

    # Validate project exists if provided
    if project_id:
        row = conn.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            conn.close()
            raise ValueError(f"Project '{project_id}' not found in database.")

    conn.execute(
        """INSERT INTO intake_sessions
           (id, project_id, customer_name, customer_org, session_status,
            classification, impact_level, created_by)
           VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
        (session_id, project_id, customer_name, customer_org,
         classification, impact_level, created_by),
    )

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
        "message": f"Session created. Welcome, {customer_name}. "
                   f"I'm the ICDEV Requirements Analyst. I'll help capture and "
                   f"structure your requirements for a {impact_level} system. "
                   f"Let's start with the mission context — what problem does "
                   f"this system need to solve?",
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
        (datetime.utcnow().isoformat(), session_id),
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
        (datetime.utcnow().isoformat(), session_id),
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

    # --- Ambiguity detection ---
    ambiguities = _detect_ambiguities_in_text(customer_message)

    # --- Gap signals ---
    gap_signals = _detect_gap_signals(customer_message, session_id, conn)

    # --- Boundary flags ---
    boundary_flags = _detect_boundary_signals(customer_message, session_data)

    # --- DevSecOps / ZTA signals (Phase 24) ---
    devsecops_signals = _detect_devsecops_signals(customer_message)
    zta_signals = _detect_zta_signals(customer_message)

    # --- MOSA signals (Phase 26, D125) ---
    mosa_signals = _detect_mosa_signals(customer_message, session_data)

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
        (req_count, len(ambiguities), datetime.utcnow().isoformat(), session_id),
    )

    # --- Build analyst response turn ---
    analyst_turn = turn_number + 1
    response_parts = []

    if extracted_reqs:
        response_parts.append(
            f"I captured {len(extracted_reqs)} requirement(s) from what you described."
        )
        for req in extracted_reqs:
            response_parts.append(
                f"  - [{req['requirement_type'].upper()}] {req['raw_text'][:100]}"
            )

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
            f"\nZero Trust Architecture requirement detected"
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

    # Add contextual follow-up question
    config = _load_config()
    auto_score_interval = config.get("ricoas", {}).get(
        "intake_agent", {}
    ).get("auto_readiness_score_interval", 3)

    readiness_update = None
    if turn_number > 0 and turn_number % (auto_score_interval * 2) == 0:
        readiness_update = _quick_readiness_estimate(session_id, conn)
        response_parts.append(
            f"\nReadiness: {readiness_update['overall']:.0%} "
            f"(completeness={readiness_update['completeness']:.0%}, "
            f"clarity={readiness_update['clarity']:.0%})"
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

        # Create requirement record
        req_id = _generate_id("req")
        conn.execute(
            """INSERT INTO intake_requirements
               (id, session_id, source_turn, raw_text, requirement_type,
                priority, status, classification)
               VALUES (?, ?, ?, ?, ?, ?, 'draft', 'CUI')""",
            (req_id, session_id, turn_number, sentence.strip(), req_type, priority),
        )

        extracted.append({
            "id": req_id,
            "raw_text": sentence.strip(),
            "requirement_type": req_type,
            "priority": priority,
        })

    return extracted


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

    # Clarity: inverse of ambiguity ratio
    session = conn.execute(
        "SELECT ambiguity_count FROM intake_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    amb_count = dict(session).get("ambiguity_count", 0) if session else 0
    clarity = max(0.0, 1.0 - (amb_count / max(total, 1)) * 0.5)

    # Feasibility: assume 0.5 without architect review
    feasibility = 0.5

    # Compliance: check for security-type requirements
    sec_reqs = sum(1 for r in reqs if dict(r)["requirement_type"] in ("security", "compliance"))
    compliance = min(1.0, sec_reqs / max(3, 1))

    # Testability: check for acceptance criteria
    with_criteria = sum(1 for r in reqs if dict(r).get("acceptance_criteria"))
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
        "exported_at": datetime.utcnow().isoformat(),
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
            result = create_session(
                project_id=args.project_id,
                customer_name=args.customer_name,
                customer_org=args.customer_org,
                impact_level=args.impact_level,
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
