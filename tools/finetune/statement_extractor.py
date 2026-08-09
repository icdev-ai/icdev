#!/usr/bin/env python3
# CUI // SP-CTI
"""Statement extractor — grounded Q&A pair generation (D-FT-23).

Implements the "Know Your RAG" statement extraction method: decompose context
into atomic factual statements, then generate Q&A pairs from each statement.
Produces grounded pairs with near-zero hallucination risk.

Usage:
    python tools/finetune/statement_extractor.py --extract --chunk-text "Some text" --json
    python tools/finetune/statement_extractor.py --extract-from-rag --source-table innovation_signals --dataset-id ds-xxx --json
    python tools/finetune/statement_extractor.py --stats --dataset-id ds-xxx --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.finetune.statement_extractor")

DB_PATH = BASE_DIR / "data" / "icdev.db"
RAG_DB_PATH = BASE_DIR / "data" / "rag" / "rag_vectors.db"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATEMENT_TYPES = ("factual", "summary", "reasoning")

_EXTRACT_SYSTEM_PROMPT = """You are an expert at decomposing text into atomic factual statements.
Each statement must contain exactly ONE fact, be self-contained, and be verifiable from the source text.

Rules:
1. Extract only information explicitly present in the text
2. Each statement should be a complete sentence (subject + predicate)
3. Do not infer or hallucinate facts not in the source
4. Short phrases or titles alone are not valid statements

Return ONLY a valid JSON array of strings. No other text.
Example: ["NIST 800-53 defines 20 control families.", "AC-2 governs Account Management."]"""

_QUESTION_SYSTEM_PROMPT = """You are an expert at generating evaluation questions from factual statements.
For each statement, generate a natural question that the statement directly answers.

Rules:
1. The question must be answerable ONLY using the given statement
2. Do not ask about information outside the statement
3. Prefer specific questions over vague ones
4. Match question type to statement content (who/what/when/how/why)

Return ONLY valid JSON with keys "question" and "answer". No other text.
Example: {"question": "How many control families does NIST 800-53 define?", "answer": "NIST 800-53 defines 20 control families."}"""

# Causal/conditional pattern for reasoning classification
_CAUSAL_PATTERN = re.compile(
    r"\b(because|therefore|thus|hence|as a result|due to|in order to|"
    r"ensures|requires|must|shall|if .+ then|enables|prevents|causes|"
    r"results in|leads to|allows|mandates)\b",
    re.IGNORECASE,
)

# Summary indicators — multi-fact compound sentences
_SUMMARY_PATTERN = re.compile(
    r"\b(including|such as|as well as|in addition|additionally|furthermore|"
    r"along with|and also|covers|encompasses|consists of|comprises)\b",
    re.IGNORECASE,
)

# Minimum meaningful sentence length (characters)
_MIN_STATEMENT_LENGTH = 20


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path or DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _classify_statement_type(statement: str) -> str:
    """Classify a statement as factual, summary, or reasoning (deterministic).

    - reasoning: contains causal/conditional language
    - summary:   contains enumeration/aggregation language or is a compound sentence
    - factual:   short, direct declarative sentence
    """
    if _CAUSAL_PATTERN.search(statement):
        return "reasoning"
    if _SUMMARY_PATTERN.search(statement) or statement.count(",") >= 2:
        return "summary"
    return "factual"


def _parse_json_array(text: str) -> Optional[List[Any]]:
    """Extract and parse a JSON array from LLM response, stripping markdown fences."""
    text = text.strip()
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Extract and parse a JSON object from LLM response, stripping markdown fences."""
    text = text.strip()
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------
# Core: Statement Extraction
# ---------------------------------------------------------------------------


def _extract_statements_llm(context_chunk: str) -> Optional[List[Dict[str, str]]]:
    """Use scanner-tier LLM (qwen3.5) to extract atomic statements.

    Returns list of {"statement": str, "type": str} or None on failure.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Decompose this text into atomic factual statements:\n\n"
                        f"{context_chunk[:3000]}\n\n"
                        "Return ONLY a JSON array of strings."
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=1500,
        )

        response = router.invoke("ft_pair_generation", request)
        if not response or not response.content:
            return None

        statements_raw = _parse_json_array(response.content)
        if not statements_raw:
            return None

        result = []
        for s in statements_raw:
            if not isinstance(s, str):
                continue
            stmt = s.strip()
            if len(stmt) < _MIN_STATEMENT_LENGTH:
                continue
            result.append(
                {
                    "statement": stmt,
                    "type": _classify_statement_type(stmt),
                }
            )
        return result if result else None

    except (ImportError, Exception):
        return None


def _extract_statements_template(context_chunk: str) -> List[Dict[str, str]]:
    """Template-based statement extraction (air-gap safe, deterministic).

    Splits text on sentence boundaries (. followed by space or end of string)
    and filters short fragments.
    """
    # Split on sentence-ending punctuation followed by whitespace or end
    raw_sentences = re.split(r"(?<=[.!?])\s+", context_chunk.strip())

    result = []
    for sentence in raw_sentences:
        stmt = sentence.strip().rstrip(".,;:!?")
        if not stmt:
            continue
        # Ensure it ends with a period for consistency
        if not stmt.endswith((".", "!", "?")):
            stmt = stmt + "."
        if len(stmt) < _MIN_STATEMENT_LENGTH:
            continue
        result.append(
            {
                "statement": stmt,
                "type": _classify_statement_type(stmt),
            }
        )

    return result


def extract_statements(context_chunk: str) -> List[Dict[str, str]]:
    """Decompose a text chunk into atomic factual statements.

    Tries LLM extraction first; falls back to sentence splitting for
    air-gap safe operation.

    Args:
        context_chunk: Raw text to decompose.

    Returns:
        List of dicts with keys:
            - statement (str): The atomic factual statement.
            - type (str): One of "factual", "summary", "reasoning".
    """
    if not context_chunk or not context_chunk.strip():
        return []

    # Strategy 1: LLM-based extraction (scanner-tier, qwen3.5)
    llm_result = _extract_statements_llm(context_chunk)
    if llm_result is not None:
        return llm_result

    # Strategy 2: Template-based (deterministic fallback)
    return _extract_statements_template(context_chunk)


# ---------------------------------------------------------------------------
# Core: Statement → Q&A Pair Conversion
# ---------------------------------------------------------------------------


def _statement_to_pair_llm(statement: str, context: str = "") -> Optional[Dict[str, str]]:
    """Use scanner-tier LLM to generate a Q&A pair from a statement.

    Returns {"question": str, "answer": str} or None on failure.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        ctx_section = f"\n\nSOURCE CONTEXT:\n{context[:800]}" if context else ""
        user_content = (
            f"STATEMENT: {statement}{ctx_section}\n\n"
            "Generate a question that this statement directly answers.\n"
            'Return JSON: {"question": "...", "answer": "..."}'
        )

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {"role": "system", "content": _QUESTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=300,
        )

        response = router.invoke("ft_pair_generation", request)
        if not response or not response.content:
            return None

        parsed = _parse_json_object(response.content)
        if not parsed:
            return None

        q = parsed.get("question", "").strip()
        a = parsed.get("answer", "").strip()
        if q and a and len(q) > 10 and len(a) > 10:
            return {"question": q, "answer": a}
        return None

    except (ImportError, Exception):
        return None


def _statement_to_pair_template(statement: str) -> Dict[str, str]:
    """Template-based Q&A pair generation (air-gap safe, deterministic).

    Generates a generic question from the first few words of the statement.
    """
    words = statement.split()
    # Take up to 5 words as subject phrase
    subject_phrase = " ".join(words[: min(5, len(words))]).rstrip(".,;:!?")

    # Try to detect question type from statement content
    stmt_lower = statement.lower()
    if any(w in stmt_lower for w in ["is defined as", "is a", "are defined", "refers to"]):
        question = f"What is {subject_phrase}?"
    elif any(w in stmt_lower for w in ["was", "were", "published in", "released in", "established in"]):
        question = f"What can you tell me about {subject_phrase}?"
    elif _CAUSAL_PATTERN.search(statement):
        question = f"Why or how does {subject_phrase} relate to the described outcome?"
    else:
        question = f"What can you tell me about {subject_phrase}?"

    return {"question": question, "answer": statement}


def _map_type_to_taxonomy(stmt_type: str) -> str:
    """Map statement type to RAG taxonomy label."""
    mapping = {
        "factual": "fact_single",
        "summary": "summary",
        "reasoning": "reasoning",
    }
    return mapping.get(stmt_type, "fact_single")


def statements_to_pairs(
    statements: List[Dict[str, str]],
    context: str = "",
) -> List[Dict[str, str]]:
    """Convert a list of atomic statements into Q&A training pairs.

    For each statement:
        1. Attempt LLM question generation (scanner-tier qwen3.5).
        2. Fall back to template-based question generation.
        3. Assign taxonomy_label from statement type.

    Args:
        statements: List of {"statement": str, "type": str} from extract_statements().
        context:    Original source context (optional, improves LLM question quality).

    Returns:
        List of dicts with keys:
            - user_input (str):         The generated question.
            - expected_output (str):    The statement (grounded answer).
            - system_prompt (str):      Standard RAG system prompt.
            - taxonomy_label (str):     One of the 4 taxonomy labels.
            - source_statement (str):   The original atomic statement.
    """
    if not statements:
        return []

    system_prompt = (
        "You are a knowledgeable assistant. Answer based on the provided context. "
        "If the answer is not in the context, say you don't know."
    )

    pairs = []
    for stmt_dict in statements:
        statement = stmt_dict.get("statement", "").strip()
        stmt_type = stmt_dict.get("type", "factual")

        if not statement:
            continue

        # Attempt LLM-based question generation
        qa = _statement_to_pair_llm(statement, context)
        if qa is None:
            qa = _statement_to_pair_template(statement)

        taxonomy_label = _map_type_to_taxonomy(stmt_type)

        pairs.append(
            {
                "user_input": qa["question"],
                "expected_output": qa["answer"],
                "system_prompt": system_prompt,
                "taxonomy_label": taxonomy_label,
                "source_statement": statement,
            }
        )

    return pairs


# ---------------------------------------------------------------------------
# Public pipeline
# ---------------------------------------------------------------------------


def extract_and_generate(
    context_chunk: str,
    purpose: str = "general",
) -> List[Dict[str, str]]:
    """Convenience pipeline: extract atomic statements then generate Q&A pairs.

    Args:
        context_chunk: Raw text passage to process.
        purpose:       Intended use ('general', 'compliance_export', etc.) for
                       system_prompt customization.

    Returns:
        List of Q&A training pairs (same structure as statements_to_pairs output).
    """
    if not context_chunk or not context_chunk.strip():
        return []

    statements = extract_statements(context_chunk)
    if not statements:
        return []

    purpose_hints = {
        "compliance_export": (
            "You are a compliance expert. Answer questions about security controls, "
            "compliance frameworks, and assessment procedures based on the provided context."
        ),
        "code_generation": (
            "You are a software architect. Answer questions about code patterns, "
            "architecture decisions, and implementation approaches based on the provided context."
        ),
        "proposal_drafting": (
            "You are a proposal expert. Answer questions about proposal writing, "
            "requirements, deliverables, and compliance based on the provided context."
        ),
    }

    system_prompt = purpose_hints.get(
        purpose,
        "You are a knowledgeable assistant. Answer based on the provided context. "
        "If the answer is not in the context, say you don't know.",
    )

    pairs = statements_to_pairs(statements, context=context_chunk)

    # Override system_prompt with purpose-specific one
    for pair in pairs:
        pair["system_prompt"] = system_prompt

    return pairs


def generate_from_rag_source(
    dataset_id: str,
    source_table: str,
    limit: int = 100,
    purpose: str = "general",
    classification: str = "CUI",
    db_path: Optional[Path] = None,
    rag_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate statement-grounded Q&A pairs from RAG chunks and store in dataset.

    Reads chunks from the `rag_chunks` table, extracts atomic statements,
    generates Q&A pairs, and stores them via dataset_manager.add_example.

    Args:
        dataset_id:   Target fine-tuning dataset ID (must already exist).
        source_table: Source table name to filter rag_chunks by.
        limit:        Maximum number of chunks to process.
        purpose:      Pair generation purpose (affects system_prompt).
        classification: CUI marking for stored pairs.
        db_path:      Override for icdev.db path.
        rag_db_path:  Override for rag_vectors.db path.

    Returns:
        Summary dict with chunks_processed, statements_extracted, pairs_generated,
        pairs_stored, and any errors.
    """
    from tools.finetune.dataset_manager import add_example

    rag_conn: Optional[sqlite3.Connection] = None
    results: Dict[str, Any] = {
        "success": False,
        "dataset_id": dataset_id,
        "source_table": source_table,
        "chunks_processed": 0,
        "statements_extracted": 0,
        "pairs_generated": 0,
        "pairs_stored": 0,
        "errors": [],
    }

    try:
        rag_conn = _get_db(rag_db_path or RAG_DB_PATH)

        # Fetch chunks for the given source table
        try:
            rows = rag_conn.execute(
                "SELECT id, content FROM rag_chunks WHERE source_table = %s LIMIT %s",
                (source_table, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # rag_chunks may not exist in this DB — try icdev.db
            rag_conn.close()
            rag_conn = _get_db(db_path)
            rows = rag_conn.execute(
                "SELECT id, content FROM rag_chunks WHERE source_table = %s LIMIT %s",
                (source_table, limit),
            ).fetchall()

        for row in rows:
            chunk_id = row[0]
            content = row[1] or ""

            if not content.strip():
                continue

            results["chunks_processed"] += 1

            try:
                pairs = extract_and_generate(content, purpose=purpose)
                results["statements_extracted"] += len(pairs)
                results["pairs_generated"] += len(pairs)

                for pair in pairs:
                    taxonomy_label = pair.get("taxonomy_label", "fact_single")
                    source_statement = pair.get("source_statement", "")

                    # Store metadata as part of system_prompt annotation
                    metadata_note = (
                        f" [taxonomy:{taxonomy_label}] [chunk:{chunk_id}] [stmt:{_content_hash(source_statement)}]"
                    )

                    store_result = add_example(
                        dataset_id=dataset_id,
                        user_input=pair["user_input"],
                        expected_output=pair["expected_output"],
                        system_prompt=pair["system_prompt"] + metadata_note,
                        source="rag_auto_generated",
                        source_chunk_id=chunk_id,
                        classification=classification,
                        db_path=db_path,
                    )

                    if store_result.get("success"):
                        results["pairs_stored"] += 1
                    else:
                        # Dedup collision is acceptable — skip silently
                        err = store_result.get("error", "")
                        if "UNIQUE constraint" not in err and "duplicate" not in err.lower():
                            results["errors"].append(f"chunk {chunk_id}: {err}")

            except Exception as exc:
                results["errors"].append(f"chunk {chunk_id}: {exc!s}")
                logger.warning("Error processing chunk %s: %s", chunk_id, exc)

        results["success"] = True

    except Exception as exc:
        results["errors"].append(str(exc))
        logger.error("generate_from_rag_source failed: %s", exc)
    finally:
        if rag_conn:
            rag_conn.close()

    return results


def get_statement_stats(dataset_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Return statement-extraction stats for a dataset.

    Counts pairs grouped by taxonomy_label annotation in system_prompt.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT system_prompt FROM ft_dataset_examples WHERE dataset_id = %s",
            (dataset_id,),
        ).fetchall()

        label_counts: Dict[str, int] = {
            "fact_single": 0,
            "summary": 0,
            "reasoning": 0,
            "unanswerable": 0,
            "unknown": 0,
        }
        total = 0

        for row in rows:
            sp = row[0] or ""
            total += 1
            match = re.search(r"\[taxonomy:(\w+)\]", sp)
            if match:
                lbl = match.group(1)
                if lbl in label_counts:
                    label_counts[lbl] += 1
                else:
                    label_counts["unknown"] += 1
            else:
                label_counts["unknown"] += 1

        return {
            "success": True,
            "dataset_id": dataset_id,
            "total_pairs": total,
            "taxonomy_distribution": label_counts,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Statement extractor — grounded Q&A pair generation (D-FT-23).",
    )
    p.add_argument("--extract", action="store_true", help="Extract statements from a text chunk.")
    p.add_argument("--extract-from-rag", action="store_true", help="Generate pairs from RAG chunks in the DB.")
    p.add_argument("--stats", action="store_true", help="Show taxonomy stats for a dataset.")
    p.add_argument("--chunk-text", help="Text to extract statements from.")
    p.add_argument("--source-table", help="RAG source table name to read chunks from.")
    p.add_argument("--dataset-id", help="Fine-tuning dataset ID.")
    p.add_argument("--limit", type=int, default=100, help="Max chunks to process (default: 100).")
    p.add_argument("--purpose", default="general", help="Pair generation purpose (default: general).")
    p.add_argument("--classification", default="CUI", help="Classification marking (default: CUI).")
    p.add_argument("--json", action="store_true", help="Output as JSON.")
    return p


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = _build_parser()
    args = parser.parse_args()

    if args.extract:
        if not args.chunk_text:
            parser.error("--extract requires --chunk-text")
        pairs = extract_and_generate(args.chunk_text, purpose=args.purpose)
        output = {
            "success": True,
            "pairs_generated": len(pairs),
            "pairs": pairs,
        }
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"Generated {len(pairs)} Q&A pairs:")
            for i, pair in enumerate(pairs, 1):
                print(f"\n[{i}] [{pair['taxonomy_label']}]")
                print(f"  Q: {pair['user_input']}")
                print(f"  A: {pair['expected_output'][:120]}")
        return

    if args.extract_from_rag:
        if not args.source_table or not args.dataset_id:
            parser.error("--extract-from-rag requires --source-table and --dataset-id")
        result = generate_from_rag_source(
            dataset_id=args.dataset_id,
            source_table=args.source_table,
            limit=args.limit,
            purpose=args.purpose,
            classification=args.classification,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Processed {result['chunks_processed']} chunks")
            print(f"Statements extracted : {result['statements_extracted']}")
            print(f"Pairs generated      : {result['pairs_generated']}")
            print(f"Pairs stored         : {result['pairs_stored']}")
            if result["errors"]:
                print(f"Errors ({len(result['errors'])}):")
                for err in result["errors"][:5]:
                    print(f"  - {err}")
        return

    if args.stats:
        if not args.dataset_id:
            parser.error("--stats requires --dataset-id")
        result = get_statement_stats(args.dataset_id)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("success"):
                print(f"Dataset: {args.dataset_id}")
                print(f"Total pairs: {result['total_pairs']}")
                print("Taxonomy distribution:")
                for label, count in result["taxonomy_distribution"].items():
                    print(f"  {label}: {count}")
            else:
                print(f"Error: {result.get('error')}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
