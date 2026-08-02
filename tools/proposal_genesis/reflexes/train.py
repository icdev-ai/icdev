#!/usr/bin/env python3
# CUI // SP-CTI
"""R14: Train Reflex — Approved drafts + lessons → fine-tuning pairs → Ollama.

Generates fine-tuning training pairs from approved proposal drafts,
win/loss lessons, and knowledge base content.  Pairs are stored in the
fine-tuning dataset for later model training via the fine-tuning pipeline.

Pipeline: weekly Sun 22:00 (independent schedule, after R13 Analyze).
YELLOW tier (reversible writes — generates training data, does not train).
Scanner-tier LLM only (zero Claude tokens — qwen3.5 for pair generation).
"""

import json
import hashlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.train")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _content_hash(system: str, user: str, expected: str) -> str:
    """SHA-256 content hash for deduplication."""
    raw = f"{system}|{user}|{expected}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Module-level constants — training-pair generation limits, overridable from
# proposal_genesis_config.yaml under reflexes.train.  Change config, not code.
# ---------------------------------------------------------------------------
_DRAFTS_FETCH_LIMIT      = 50    # max approved drafts fetched per run
_LESSONS_FETCH_LIMIT     = 100   # max win/loss lessons fetched per run
_KB_FETCH_LIMIT          = 50    # max knowledge-base entries fetched per run
_DRAFT_MIN_CONTENT       = 100   # min draft content length to generate pairs
_DRAFT_PAIR2_MIN_CONTENT = 300   # min content length to generate quality-improvement pair
_DRAFT_PAIR_MAX_CONTENT  = 2000  # max content length for section-drafting pair
_DRAFT_PAIR2_USER_MAX    = 500   # max content length for quality-pair user input
_DRAFT_PAIR2_OUTPUT_MAX  = 1500  # max content length for quality-pair expected output
_LESSON_MIN_LENGTH       = 20    # min lesson text length to generate pairs
_KB_MIN_CONTENT          = 100   # min KB entry content length to generate pairs
_KB_PAIR_OUTPUT_MAX      = 2000  # max content length for KB pair expected output
_MAX_PAIRS_PER_RUN       = 100   # default max training pairs generated per run


# ---------------------------------------------------------------------------
# Source: Approved proposal drafts
# ---------------------------------------------------------------------------


def _get_approved_drafts(limit: int = _DRAFTS_FETCH_LIMIT) -> List[Dict]:
    """Get approved proposal drafts not yet used for training."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT d.id, d.opportunity_id, d.section_type,
                   d.draft_content, d.final_content,
                   o.title as opp_title, o.agency, o.naics_code
            FROM proposal_section_drafts d
            JOIN sam_gov_opportunities o ON o.id = d.opportunity_id
            WHERE d.status = 'approved'
            AND d.id NOT IN (
                SELECT source_id FROM pg_training_pair_sources
                WHERE source_type = 'approved_draft'
            )
            ORDER BY d.updated_at DESC
            LIMIT %s
        """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _generate_draft_pairs(draft: Dict) -> List[Dict]:
    """Generate Q&A training pairs from an approved draft.

    Uses deterministic template-based generation (air-gap safe).
    """
    pairs = []
    content = draft.get("final_content") or draft.get("draft_content") or ""
    if not content or len(content) < _DRAFT_MIN_CONTENT:
        return pairs

    section = draft.get("section_type", "general")
    agency = draft.get("agency", "the government")
    title = draft.get("opp_title", "this opportunity")

    # Pair 1: Section drafting
    pairs.append(
        {
            "system_prompt": (
                "You are a government proposal writer. Generate professional, "
                "compliant proposal sections that address RFP requirements."
            ),
            "user_input": (f"Write a {section} section for a proposal responding to {agency}'s requirement: {title}"),
            "expected_output": content[:_DRAFT_PAIR_MAX_CONTENT],
            "source_type": "approved_draft",
            "source_id": draft.get("id", ""),
        }
    )

    # Pair 2: Quality improvement
    if len(content) > _DRAFT_PAIR2_MIN_CONTENT:
        pairs.append(
            {
                "system_prompt": (
                    "You are a proposal quality reviewer. Provide concise, "
                    "professional content that meets government writing standards."
                ),
                "user_input": (
                    f"Improve the following {section} section for clarity and compliance:\n\n{content[:_DRAFT_PAIR2_USER_MAX]}"
                ),
                "expected_output": content[:_DRAFT_PAIR2_OUTPUT_MAX],
                "source_type": "approved_draft",
                "source_id": draft.get("id", ""),
            }
        )

    return pairs


# ---------------------------------------------------------------------------
# Source: Win/loss lessons
# ---------------------------------------------------------------------------


def _get_recent_lessons(limit: int = _LESSONS_FETCH_LIMIT) -> List[Dict]:
    """Get actionable lessons not yet used for training."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT l.id, l.category, l.lesson, l.actionable,
                   r.outcome, r.our_strengths, r.our_weaknesses,
                   o.title as opp_title, o.agency
            FROM pg_win_loss_lessons l
            JOIN pg_win_loss_records r ON r.id = l.win_loss_id
            JOIN sam_gov_opportunities o ON o.id = r.opportunity_id
            WHERE l.actionable = 1
            AND l.id NOT IN (
                SELECT source_id FROM pg_training_pair_sources
                WHERE source_type = 'win_loss_lesson'
            )
            ORDER BY l.created_at DESC
            LIMIT %s
        """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _generate_lesson_pairs(lesson: Dict) -> List[Dict]:
    """Generate training pairs from win/loss lessons."""
    pairs = []
    lesson_text = lesson.get("lesson", "")
    if not lesson_text or len(lesson_text) < _LESSON_MIN_LENGTH:
        return pairs

    category = lesson.get("category", "general")
    outcome = lesson.get("outcome", "unknown")
    agency = lesson.get("agency", "the government")

    # Pair: Lesson application
    pairs.append(
        {
            "system_prompt": (
                "You are a government contracting advisor. Provide actionable "
                "recommendations based on past proposal outcomes."
            ),
            "user_input": (
                f"Based on a {outcome} outcome with {agency}, what lessons should we apply in the {category} area?"
            ),
            "expected_output": (
                f"Key lesson: {lesson_text}\n\n"
                f"Strengths to leverage: {lesson.get('our_strengths', 'N/A')}\n"
                f"Weaknesses to address: {lesson.get('our_weaknesses', 'N/A')}"
            ),
            "source_type": "win_loss_lesson",
            "source_id": lesson.get("id", ""),
        }
    )

    return pairs


# ---------------------------------------------------------------------------
# Source: Knowledge base
# ---------------------------------------------------------------------------


def _get_kb_entries(limit: int = _KB_FETCH_LIMIT) -> List[Dict]:
    """Get knowledge base entries not yet used for training."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT kb.id, kb.title, kb.content, kb.domain,
                   kb.tags, kb.usage_count
            FROM proposal_knowledge_base kb
            WHERE kb.status = 'active'
            AND kb.content IS NOT NULL
            AND LENGTH(kb.content) > 100
            AND kb.id NOT IN (
                SELECT source_id FROM pg_training_pair_sources
                WHERE source_type = 'knowledge_base'
            )
            ORDER BY kb.usage_count DESC, kb.updated_at DESC
            LIMIT %s
        """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _generate_kb_pairs(entry: Dict) -> List[Dict]:
    """Generate training pairs from knowledge base entries."""
    pairs = []
    content = entry.get("content", "")
    if not content or len(content) < _KB_MIN_CONTENT:
        return pairs

    title = entry.get("title", "")
    domain = entry.get("domain", "general")

    pairs.append(
        {
            "system_prompt": (
                "You are a proposal knowledge expert. Provide detailed, "
                "accurate content based on the organization's capability library."
            ),
            "user_input": (f"What capabilities and experience do we have in {domain}: {title}?"),
            "expected_output": content[:_KB_PAIR_OUTPUT_MAX],
            "source_type": "knowledge_base",
            "source_id": entry.get("id", ""),
        }
    )

    return pairs


# ---------------------------------------------------------------------------
# Store training pairs
# ---------------------------------------------------------------------------


def _ensure_tracking_table() -> None:
    """Create tracking table if it doesn't exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pg_training_pair_sources (
                id          TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id   TEXT NOT NULL,
                pair_count  INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pg_tps_source
            ON pg_training_pair_sources(source_type, source_id)
        """)
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _store_pair(pair: Dict, dataset_id: Optional[str] = None) -> bool:
    """Store a training pair in ft_dataset_examples if table exists.

    Falls back to pg_training_pair_sources tracking only.
    """
    conn = get_connection()
    now = _utcnow_iso()
    ch = _content_hash(
        pair.get("system_prompt", ""),
        pair.get("user_input", ""),
        pair.get("expected_output", ""),
    )
    try:
        # Check for duplicate
        existing = conn.execute("SELECT id FROM pg_training_pair_sources WHERE content_hash = %s", (ch,)).fetchone()
        if existing:
            return False

        # Record the source tracking
        conn.execute(
            "INSERT INTO pg_training_pair_sources "
            "(id, source_type, source_id, pair_count, content_hash, "
            "created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgtps"),
                pair.get("source_type", "unknown"),
                pair.get("source_id", ""),
                1,
                ch,
                now,
            ),
        )

        # Try to store in fine-tuning dataset if available
        if dataset_id:
            try:
                # ft_dataset_examples has no status column — review state is
                # the boolean `approved`, so "pending_review" is 0. id is a
                # SERIAL integer, so the generated "ftex-…" string was the
                # wrong type for it, and content_hash is NOT NULL (swp-scan-01).
                conn.execute(
                    "INSERT INTO ft_dataset_examples "
                    "(dataset_id, system_prompt, user_input, "
                    "expected_output, source, approved, quality_score, "
                    "content_hash, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        dataset_id,
                        pair.get("system_prompt", ""),
                        pair.get("user_input", ""),
                        pair.get("expected_output", ""),
                        f"pg_train_{pair.get('source_type', 'unknown')}",
                        0,
                        None,
                        ch,
                        now,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # ft_dataset_examples may not exist
                logger.warning(
                    "_store_pair: best-effort INSERT into ft_dataset_examples failed (non-blocking): %s",
                    exc,
                )

        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def _audit_train(event_type: str, details: Dict, success: bool) -> None:
    """Log train event to audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                event_type,
                "train",
                "yellow",
                None,
                json.dumps(details),
                1 if success else 0,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit_train: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s", exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _get_or_create_dataset() -> Optional[str]:
    """Find existing proposal_drafting dataset or create one.

    Bridges R14 Train to the fine-tuning pipeline (D-FT-9).
    """
    conn = get_connection()
    try:
        # Look for existing proposal drafting dataset
        row = conn.execute(
            "SELECT id FROM ft_datasets "
            "WHERE purpose = 'proposal_drafting' "
            "AND status IN ('draft', 'labeling', 'ready') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return row["id"]

        # Create one
        ds_id = f"ds-{uuid.uuid4().hex[:12]}"
        now = _utcnow_iso()
        conn.execute(
            "INSERT INTO ft_datasets "
            "(id, name, description, purpose, base_model, version, "
            "example_count, classification, tenant_id, project_id, "
            "created_by, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, 1, 0, %s, %s, %s, %s, 'draft', %s, %s)",
            (
                ds_id,
                "Proposal Genesis Training Data",
                "Auto-generated from approved drafts, win/loss lessons, and knowledge base by R14 Train reflex.",
                "proposal_drafting",
                "qwen3.5:latest",
                "CUI",
                "",
                "",
                "pg_train",
                now,
                now,
            ),
        )
        conn.commit()
        return ds_id
    except Exception:
        return None
    finally:
        conn.close()


def _update_dataset_count(dataset_id: str, added: int) -> None:
    """Increment the example_count on the dataset after storing pairs."""
    if not dataset_id or added <= 0:
        return
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE ft_datasets SET example_count = example_count + %s, updated_at = %s WHERE id = %s",
            (added, _utcnow_iso(), dataset_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Train Reflex (R14).

    Steps:
      1. Ensure tracking table exists
      2. Get or create fine-tuning dataset (D-FT-9 bridge)
      3. Collect approved drafts → generate training pairs
      4. Collect win/loss lessons → generate training pairs
      5. Collect knowledge base entries → generate training pairs
      6. Deduplicate and store pairs into ft_dataset_examples
      7. Update dataset example count
      8. Audit all generation decisions

    Returns standard reflex result dict.
    """
    max_pairs = config.get("max_pairs_per_run", _MAX_PAIRS_PER_RUN)
    dataset_id = config.get("dataset_id") or _get_or_create_dataset()

    _ensure_tracking_table()

    all_pairs = []
    source_counts = {"approved_draft": 0, "win_loss_lesson": 0, "knowledge_base": 0}

    # Source 1: Approved drafts
    drafts = _get_approved_drafts(limit=50)
    for draft in drafts:
        pairs = _generate_draft_pairs(draft)
        for p in pairs:
            all_pairs.append(p)
            source_counts["approved_draft"] += 1

    # Source 2: Win/loss lessons
    lessons = _get_recent_lessons(limit=100)
    for lesson in lessons:
        pairs = _generate_lesson_pairs(lesson)
        for p in pairs:
            all_pairs.append(p)
            source_counts["win_loss_lesson"] += 1

    # Source 3: Knowledge base
    kb_entries = _get_kb_entries(limit=50)
    for entry in kb_entries:
        pairs = _generate_kb_pairs(entry)
        for p in pairs:
            all_pairs.append(p)
            source_counts["knowledge_base"] += 1

    # Store (with dedup)
    stored = 0
    duplicates = 0
    for pair in all_pairs[:max_pairs]:
        if _store_pair(pair, dataset_id):
            stored += 1
        else:
            duplicates += 1

    # Update dataset example count
    _update_dataset_count(dataset_id, stored)

    _audit_train(
        "training_pairs_generated",
        {
            "total_generated": len(all_pairs),
            "stored": stored,
            "duplicates": duplicates,
            "sources": source_counts,
            "drafts_scanned": len(drafts),
            "lessons_scanned": len(lessons),
            "kb_entries_scanned": len(kb_entries),
            "dataset_id": dataset_id,
        },
        success=True,
    )

    return {
        "success": True,
        "metric_value": float(stored),
        "details": {
            "pairs_generated": len(all_pairs),
            "pairs_stored": stored,
            "pairs_duplicated": duplicates,
            "sources": source_counts,
            "drafts_scanned": len(drafts),
            "lessons_scanned": len(lessons),
            "kb_entries_scanned": len(kb_entries),
            "dataset_id": dataset_id,
        },
    }
