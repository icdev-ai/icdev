#!/usr/bin/env python3
# CUI // SP-CTI
"""Q&A pair generator — auto-generate training pairs from RAG chunks (D-FT-10).

Uses qwen3 (scanner_function) to generate question-answer pairs from
document chunks or RAG content. Pairs require human review before training.

Pipeline: chunks → qwen3 generates Q&A → store as unapproved examples → human labels

Usage:
    python tools/finetune/pair_generator.py --generate --dataset-id "ds-xxx" --document-id "ftdoc-xxx" --json
    python tools/finetune/pair_generator.py --generate-from-rag --dataset-id "ds-xxx" --source-table "research_signals" --json
    python tools/finetune/pair_generator.py --stats --dataset-id "ds-xxx" --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
RAG_DB_PATH = BASE_DIR / "data" / "rag" / "rag_vectors.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(system_prompt: str, user_input: str, expected_output: str) -> str:
    """SHA-256 content hash for dedup."""
    combined = f"{system_prompt}|{user_input}|{expected_output}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _load_config() -> Dict[str, Any]:
    """Load pair generation config from finetune_config.yaml."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("dataset", {})
        except (ImportError, Exception):
            pass
    return {"questions_per_chunk": 3, "auto_generate_pairs": True}


# -- LLM-based Pair Generation ------------------------------------------------


def _generate_pairs_from_chunk(
    chunk_content: str,
    purpose: str = "general",
    questions_per_chunk: int = 3,
    system_prompt: str = "",
) -> List[Dict[str, str]]:
    """Generate Q&A pairs from a chunk using qwen3 (scanner_function).

    Returns list of {"user_input": ..., "expected_output": ...}.
    """
    prompt = _build_generation_prompt(chunk_content, purpose, questions_per_chunk)

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {"role": "system", "content": _PAIR_GEN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )

        # Use scanner_function path (qwen3 only, no Claude review)
        response = router.invoke("ft_pair_generation", request)

        if response and response.content:
            return _parse_pairs_response(response.content, system_prompt)
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: template-based generation (air-gap safe, no LLM needed)
    return _generate_pairs_template(chunk_content, purpose, questions_per_chunk)


_PAIR_GEN_SYSTEM_PROMPT = """You are a training data generator for fine-tuning language models.
Given a text passage, generate high-quality question-answer pairs.

Rules:
1. Questions should be diverse (factual, analytical, procedural)
2. Answers should be grounded in the passage content only
3. Each answer should be 2-5 sentences
4. Output ONLY valid JSON array format

Output format:
[
  {"question": "...", "answer": "..."},
  {"question": "...", "answer": "..."}
]"""


def _build_generation_prompt(
    chunk_content: str,
    purpose: str,
    count: int,
) -> str:
    """Build the generation prompt for qwen3."""
    purpose_hints = {
        "general": "Generate diverse questions covering facts, processes, and analysis.",
        "proposal_drafting": "Generate questions about proposal writing, requirements, deliverables, and compliance.",
        "compliance_export": "Generate questions about compliance controls, security requirements, and assessment procedures.",
        "code_generation": "Generate questions about code patterns, architecture decisions, and implementation approaches.",
        "custom": "Generate diverse questions covering the main topics in the text.",
    }
    hint = purpose_hints.get(purpose, purpose_hints["general"])

    return f"""Generate exactly {count} question-answer pairs from this text passage.

{hint}

TEXT PASSAGE:
---
{chunk_content[:3000]}
---

Output ONLY the JSON array. No other text."""


def _parse_pairs_response(
    response_text: str,
    system_prompt: str = "",
) -> List[Dict[str, str]]:
    """Parse LLM response into Q&A pairs."""
    # Try to extract JSON from response
    text = response_text.strip()

    # Handle markdown-wrapped JSON
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()

    try:
        pairs = json.loads(text)
        if not isinstance(pairs, list):
            return []

        result = []
        for pair in pairs:
            q = pair.get("question", "").strip()
            a = pair.get("answer", "").strip()
            if q and a and len(q) > 10 and len(a) > 20:
                result.append(
                    {
                        "system_prompt": system_prompt,
                        "user_input": q,
                        "expected_output": a,
                    }
                )
        return result
    except (json.JSONDecodeError, ValueError):
        return []


def _generate_pairs_template(
    chunk_content: str,
    purpose: str,
    count: int,
) -> List[Dict[str, str]]:
    """Template-based pair generation (air-gap fallback, no LLM needed).

    Generates simple extractive Q&A pairs from sentences.
    """
    sentences = [s.strip() for s in chunk_content.split(".") if len(s.strip()) > 30]
    pairs = []

    for i, sentence in enumerate(sentences[:count]):
        # Simple extractive pattern: turn statement into question
        words = sentence.split()
        if len(words) < 5:
            continue

        question = f"What can you tell me about: {' '.join(words[:8])}...?"
        answer = sentence.strip()
        if not answer.endswith("."):
            answer += "."

        pairs.append(
            {
                "system_prompt": "",
                "user_input": question,
                "expected_output": answer,
            }
        )

    return pairs


# -- Pipeline: Generate from Document Chunks -----------------------------------


def generate_from_document(
    dataset_id: str,
    chunks: List[Dict[str, Any]],
    purpose: str = "general",
    questions_per_chunk: int = 3,
    system_prompt: str = "",
    source_document_id: str = "",
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate Q&A pairs from document chunks and add to dataset.

    Args:
        dataset_id: Target dataset
        chunks: List of chunk dicts with 'content' and optional 'chunk_id'
        purpose: Dataset purpose for guiding generation
        questions_per_chunk: How many Q&A pairs per chunk
        system_prompt: Optional system prompt for all pairs
        source_document_id: Document ID for provenance
    """
    config = _load_config()
    qpc = questions_per_chunk or config.get("questions_per_chunk", 3)

    from tools.finetune.dataset_manager import add_example

    total_generated = 0
    total_added = 0
    total_dedup = 0
    errors = []

    for chunk in chunks:
        content = chunk.get("content", "")
        chunk_id = chunk.get("chunk_id", "")

        if not content or len(content) < 50:
            continue

        pairs = _generate_pairs_from_chunk(
            chunk_content=content,
            purpose=purpose,
            questions_per_chunk=qpc,
            system_prompt=system_prompt,
        )
        total_generated += len(pairs)

        for pair in pairs:
            result = add_example(
                dataset_id=dataset_id,
                user_input=pair["user_input"],
                expected_output=pair["expected_output"],
                system_prompt=pair.get("system_prompt", system_prompt),
                source="rag_auto_generated",
                source_chunk_id=chunk_id,
                source_document_id=source_document_id,
                classification=classification,
                tenant_id=tenant_id,
                project_id=project_id,
                db_path=db_path,
            )
            if result.get("success"):
                if result.get("deduplicated"):
                    total_dedup += 1
                else:
                    total_added += 1
            else:
                errors.append(result.get("error", "Unknown error"))

    return {
        "success": True,
        "dataset_id": dataset_id,
        "chunks_processed": len(chunks),
        "pairs_generated": total_generated,
        "pairs_added": total_added,
        "pairs_deduplicated": total_dedup,
        "errors": errors[:10],  # Limit error output
    }


def generate_from_rag_source(
    dataset_id: str,
    source_table: str,
    purpose: str = "general",
    questions_per_chunk: int = 3,
    system_prompt: str = "",
    limit: int = 100,
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate Q&A pairs from existing RAG chunks.

    Reads chunks from rag_chunks table in the RAG vector store DB.
    """
    rag_db = RAG_DB_PATH
    if not rag_db.exists():
        return {"success": False, "error": f"RAG vector store not found: {rag_db}"}
    conn = _get_db(rag_db)
    try:
        rows = conn.execute(
            """SELECT id, content, source_id
               FROM rag_chunks
               WHERE source_table = %s AND tier = 'hot'
               ORDER BY created_at DESC LIMIT %s""",
            (source_table, limit),
        ).fetchall()

        if not rows:
            return {"success": False, "error": f"No RAG chunks found for source: {source_table}"}

        chunks = [
            {
                "content": r["content"] if isinstance(r, dict) else r[1],
                "chunk_id": r["id"] if isinstance(r, dict) else r[0],
            }
            for r in rows
        ]
    except Exception as e:
        return {"success": False, "error": f"RAG query error: {e}"}
    finally:
        conn.close()

    return generate_from_document(
        dataset_id=dataset_id,
        chunks=chunks,
        purpose=purpose,
        questions_per_chunk=questions_per_chunk,
        system_prompt=system_prompt,
        classification=classification,
        tenant_id=tenant_id,
        project_id=project_id,
        db_path=db_path,
    )


def get_generation_stats(
    dataset_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get statistics about auto-generated pairs in a dataset."""
    conn = _get_db(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = %s AND source = 'rag_auto_generated'",
            (dataset_id,),
        ).fetchone()[0]

        approved = conn.execute(
            "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = %s AND source = 'rag_auto_generated' AND approved = 1",
            (dataset_id,),
        ).fetchone()[0]

        labeled = conn.execute(
            """SELECT COUNT(*) FROM ft_dataset_examples
               WHERE dataset_id = %s AND source = 'rag_auto_generated'
                 AND (quality_score > 0 OR compliance_score > 0 OR relevance_score > 0)""",
            (dataset_id,),
        ).fetchone()[0]

        return {
            "success": True,
            "dataset_id": dataset_id,
            "auto_generated_total": total,
            "auto_generated_approved": approved,
            "auto_generated_labeled": labeled,
            "auto_generated_pending_review": total - labeled,
            "approval_rate_pct": round(approved / labeled * 100, 1) if labeled > 0 else 0.0,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# -- CLI -----------------------------------------------------------------------


def bayesian_rank_pairs(
    dataset_id: str,
    top_k: int = 50,
    seed: int = 42,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Rank unapproved pairs by Bayesian information gain (D-BT-2, D-KARL-4).

    Uses Bayesian Teaching to surface the most informative training pairs
    first for human review, following Goldilocks difficulty calibration.
    """
    try:
        from tools.intelligence.bayesian_teacher import score_training_pairs

        return score_training_pairs(dataset_id, top_k=top_k, seed=seed, db_path=db_path)
    except ImportError:
        return {"success": False, "error": "Bayesian Teaching engine not available"}
    except Exception as e:
        return {"success": False, "error": f"Bayesian ranking failed: {e}"}


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning Q&A pair generator")
    parser.add_argument("--generate", action="store_true", help="Generate from document chunks")
    parser.add_argument("--generate-from-rag", action="store_true", help="Generate from RAG chunks")
    parser.add_argument(
        "--bayesian-rank", action="store_true", help="Rank pending pairs by Bayesian information gain (D-BT-2)"
    )
    parser.add_argument("--stats", action="store_true", help="Generation statistics")

    parser.add_argument("--dataset-id", type=str, default="")
    parser.add_argument("--document-id", type=str, default="")
    parser.add_argument("--source-table", type=str, default="")
    parser.add_argument("--purpose", type=str, default="general")
    parser.add_argument("--questions-per-chunk", type=int, default=3)
    parser.add_argument("--system-prompt", type=str, default="")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--classification", type=str, default="CUI")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.generate and args.dataset_id:
        # CLI --generate requires programmatic API with pre-extracted chunks
        if args.document_id:
            # Try to load chunks from a previous extraction
            result = {"success": False, "error": "Provide --file with --generate to extract and generate pairs"}
        else:
            result = {"success": False, "error": "Provide --dataset-id and --document-id or use programmatic API"}
    elif args.generate_from_rag and args.dataset_id:
        result = generate_from_rag_source(
            dataset_id=args.dataset_id,
            source_table=args.source_table,
            purpose=args.purpose,
            questions_per_chunk=args.questions_per_chunk,
            system_prompt=args.system_prompt,
            limit=args.limit,
            classification=args.classification,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.bayesian_rank and args.dataset_id:
        result = bayesian_rank_pairs(
            dataset_id=args.dataset_id,
            top_k=args.limit,
        )
    elif args.stats and args.dataset_id:
        result = get_generation_stats(dataset_id=args.dataset_id)
    else:
        result = {
            "success": False,
            "error": "Specify --generate, --generate-from-rag, --bayesian-rank, or --stats with --dataset-id",
        }

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
