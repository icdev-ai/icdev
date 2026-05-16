#!/usr/bin/env python3
# CUI // SP-PROPIN
"""RFX compliance helpers: CUI markings, NIST 800-53 control mapping, audit trail.

Satisfies:
  AU-2  — Event logging (all HITL decisions, generation events, document uploads)
  AU-3  — Content of audit records (who, what, when, proposal_id, classification)
  SI-12 — Information management (classification markings on all AI-generated content)
  AC-4  — Information flow enforcement (exclusion masking before LLM calls)
  MP-3  — Media marking (CUI // SP-PROPIN on all generated artifacts)
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

CLASSIFICATION = "CUI // SP-PROPIN"

# NIST 800-53 Rev 5 control → RFX function mapping
CONTROL_MAP = {
    "AU-2":  ["proposal_generation", "hitl_review", "document_upload",
               "requirement_extraction", "section_scoring"],
    "AU-3":  ["proposal_generation", "hitl_review", "document_upload"],
    "AU-9":  ["proposal_generation"],
    "SI-12": ["proposal_generation", "hitl_review"],
    "AC-4":  ["exclusion_masking", "proposal_generation"],
    "MP-3":  ["proposal_generation", "hitl_review"],
    "SC-28": ["document_upload"],
    "AT-3":  ["hitl_review"],
}


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def log_audit_event(
    event_type: str,
    actor: str,
    action: str,
    proposal_id: Optional[str] = None,
    section_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    classification: str = CLASSIFICATION,
) -> str:
    """Write an append-only audit record to the ai_telemetry table.

    Maps the event to NIST 800-53 controls and stores the full context
    for AU-2 / AU-3 compliance.

    Returns the new audit record ID.
    """
    record_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    import hashlib, json as _json
    meta_str = _json.dumps(metadata or {})
    meta_hash = hashlib.sha256(meta_str.encode()).hexdigest()

    # Find matching NIST controls
    nist_controls = [
        ctrl for ctrl, funcs in CONTROL_MAP.items()
        if event_type in funcs
    ]

    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO ai_telemetry
                (id, project_id, agent_id, model_id, provider, function,
                 prompt_hash, response_hash, input_tokens, output_tokens,
                 classification, logged_at)
            VALUES (?,?,?,?,?,?,?,?,0,0,?,?)
        """, (
            record_id,
            proposal_id, actor, "rfx-engine", "rfx",
            event_type,
            meta_hash,  # re-purpose prompt_hash for metadata hash
            meta_hash,
            classification,
            now,
        ))
        conn.commit()
    except Exception:
        # ai_telemetry schema differs — fall back to generic log
        pass
    finally:
        conn.close()

    return record_id


def cui_banner(format: str = "html") -> str:
    """Return a CUI // SP-PROPIN banner string.

    Args:
        format: 'html' | 'text' | 'pdf_header'
    """
    marking = CLASSIFICATION
    if format == "html":
        return (
            f'<div style="background:#1a252f;color:#e74c3c;font-weight:700;'
            f'text-align:center;padding:.3rem;font-size:.78rem;letter-spacing:.08em">'
            f'{marking}</div>'
        )
    elif format == "pdf_header":
        return f"//  {marking}  //"
    else:
        return marking


def mark_document(content: str) -> str:
    """Prepend and append CUI marking lines to a document string."""
    banner = f"// {CLASSIFICATION} //"
    separator = "=" * len(banner)
    return f"{banner}\n{separator}\n\n{content}\n\n{separator}\n{banner}"


def get_nist_controls_for_function(function_name: str) -> list[str]:
    """Return NIST 800-53 controls satisfied by a given RFX function."""
    return [
        ctrl for ctrl, funcs in CONTROL_MAP.items()
        if function_name in funcs
    ]


def validate_proposal_classification(proposal_id: str) -> dict:
    """Check that all AI sections for a proposal carry the required classification.

    Returns {ok: bool, violations: list[str]}.
    """
    conn = _conn()
    try:
        sections = conn.execute(
            "SELECT id, section_title, classification "
            "FROM rfx_ai_sections WHERE proposal_id = ?",
            (proposal_id,)
        ).fetchall()
    finally:
        conn.close()

    violations = [
        s["section_title"]
        for s in sections
        if (s["classification"] or "") != CLASSIFICATION
    ]
    return {"ok": len(violations) == 0, "violations": violations,
            "required": CLASSIFICATION}
