#!/usr/bin/env python3
# CUI // SP-CTI
"""R8: Polish Reflex — WriteGuard quality gate for proposal drafts.

Runs grammar, readability, tone, plagiarism, and AI detection checks
on draft responses. Stores scores in pg_proposal_quality_scores (D-PG-8).
Scanner-tier only (zero Claude tokens).
"""

import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Deterministic quality checks (scanner-tier, zero LLM tokens)
# ---------------------------------------------------------------------------

def _check_grammar(text: str) -> Dict[str, Any]:
    """Basic grammar checks (deterministic regex)."""
    issues = []

    # Double spaces
    doubles = len(re.findall(r"  +", text))
    if doubles:
        issues.append(f"{doubles} double-space occurrences")

    # Sentence not capitalized after period
    uncapped = len(re.findall(r"\.\s+[a-z]", text))
    if uncapped:
        issues.append(f"{uncapped} sentences not capitalized")

    # Repeated words
    repeated = len(re.findall(r"\b(\w+)\s+\1\b", text, re.IGNORECASE))
    if repeated:
        issues.append(f"{repeated} repeated word pairs")

    # Missing period at end
    stripped = text.strip()
    if stripped and stripped[-1] not in ".!?:;\"')}]":
        issues.append("text does not end with punctuation")

    score = max(0, 1.0 - (len(issues) * 0.15))
    return {"score": round(score, 2), "issues": issues}


def _check_readability(text: str) -> Dict[str, Any]:
    """Readability scoring (Flesch-Kincaid approximation, deterministic)."""
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = text.split()
    syllables = sum(_count_syllables(w) for w in words)

    if not sentences or not words:
        return {"score": 0.5, "grade_level": 0, "avg_sentence_length": 0}

    avg_sentence_len = len(words) / len(sentences)
    avg_syllables_per_word = syllables / len(words) if words else 0

    # Flesch-Kincaid Grade Level
    grade = 0.39 * avg_sentence_len + 11.8 * avg_syllables_per_word - 15.59
    grade = max(0, min(20, grade))

    # Score: ideal for proposals is grade 10-14
    if 10 <= grade <= 14:
        score = 1.0
    elif 8 <= grade < 10 or 14 < grade <= 16:
        score = 0.8
    else:
        score = max(0.3, 1.0 - abs(grade - 12) * 0.05)

    return {
        "score": round(score, 2),
        "grade_level": round(grade, 1),
        "avg_sentence_length": round(avg_sentence_len, 1),
    }


def _count_syllables(word: str) -> int:
    """Estimate syllable count for a word."""
    word = word.lower().strip(".,!?;:'\"")
    if not word:
        return 0
    count = 0
    vowels = "aeiouy"
    prev_vowel = False
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _check_tone(text: str) -> Dict[str, Any]:
    """Check for professional proposal tone (deterministic keyword matching)."""
    issues = []
    text_lower = text.lower()

    # Informal language
    informal = [
        "gonna", "wanna", "gotta", "kinda", "sorta", "ain't",
        "stuff", "things", "basically", "actually", "obviously",
        "pretty much", "a lot of", "tons of", "super",
    ]
    found_informal = [w for w in informal if w in text_lower]
    if found_informal:
        issues.append(f"informal language: {', '.join(found_informal[:5])}")

    # Weak/vague language
    weak = [
        "we think", "we believe", "we hope", "we feel",
        "maybe", "perhaps", "possibly", "might be able to",
        "try to", "attempt to",
    ]
    found_weak = [w for w in weak if w in text_lower]
    if found_weak:
        issues.append(f"weak language: {', '.join(found_weak[:5])}")

    # Positive proposal indicators
    strong = [
        "we will", "we shall", "our approach", "our team",
        "demonstrated", "proven", "experience", "expertise",
        "compliant", "certified", "delivered", "implemented",
    ]
    found_strong = sum(1 for w in strong if w in text_lower)

    score = max(0, 1.0 - (len(found_informal) + len(found_weak)) * 0.1)
    score = min(1.0, score + found_strong * 0.03)

    return {"score": round(score, 2), "issues": issues}


def _check_plagiarism(text: str, opp_id: str) -> Dict[str, Any]:
    """Check for content similarity against existing drafts (D-PG-4, D-WG-5).

    Uses content hash comparison and n-gram overlap (deterministic).
    """
    conn = get_connection()
    try:
        # Get other drafts for different opportunities
        other_drafts = conn.execute(
            "SELECT section_text FROM proposal_section_drafts "
            "WHERE opportunity_id != ? AND status IN ('draft', 'approved') "
            "ORDER BY created_at DESC LIMIT 20",
            (opp_id,)
        ).fetchall()
    except Exception:
        return {"score": 1.0, "max_similarity": 0.0}
    finally:
        conn.close()

    if not other_drafts:
        return {"score": 1.0, "max_similarity": 0.0}

    text_ngrams = _get_ngrams(text, 4)
    max_similarity = 0.0

    for draft in other_drafts:
        other_ngrams = _get_ngrams(draft["section_text"] or "", 4)
        if text_ngrams and other_ngrams:
            overlap = len(text_ngrams & other_ngrams)
            total = len(text_ngrams | other_ngrams)
            similarity = overlap / total if total > 0 else 0
            max_similarity = max(max_similarity, similarity)

    # Score: 1.0 = no plagiarism, 0.0 = high similarity
    score = max(0, 1.0 - max_similarity)
    return {"score": round(score, 2), "max_similarity": round(max_similarity, 3)}


def _get_ngrams(text: str, n: int) -> set:
    """Extract character n-grams from text."""
    text = re.sub(r"\s+", " ", text.lower().strip())
    if len(text) < n:
        return set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _check_ai_detection(text: str) -> Dict[str, Any]:
    """Deterministic AI-generated text detection (D-WG-6).

    Uses burstiness and perplexity proxies (no LLM needed).
    """
    words = text.split()
    if len(words) < 20:
        return {"score": 1.0, "burstiness": 0, "advisory": "text too short"}

    # Sentence length variance (low variance = AI-like)
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) < 3:
        return {"score": 0.8, "burstiness": 0}

    lengths = [len(s.split()) for s in sentences]
    mean_len = sum(lengths) / len(lengths)
    variance = sum((sl - mean_len) ** 2 for sl in lengths) / len(lengths)
    burstiness = (variance ** 0.5) / mean_len if mean_len > 0 else 0

    # Low burstiness suggests AI generation
    if burstiness < 0.3:
        score = 0.5  # Possibly AI
    elif burstiness < 0.5:
        score = 0.7  # Unclear
    else:
        score = 0.9  # Likely human

    return {
        "score": round(score, 2),
        "burstiness": round(burstiness, 3),
        "sentence_count": len(sentences),
    }


def _run_writeguard(text: str, opportunity_id: str = "") -> Dict[str, Any]:
    """Optionally delegate to WriteGuard for deeper analysis.

    Returns WriteGuard results if available, else empty dict.
    WriteGuard integration is additive — Polish's own 5 checks always run.
    """
    try:
        from tools.writing.analysis_engine import analyze
        result = analyze(
            text,
            mode="inline",
            opportunity_id=opportunity_id,
            skip_llm=True,  # scanner-tier only, zero Claude tokens
        )
        return {
            "writeguard_score": result.get("quality_score", 0),
            "writeguard_findings": len(result.get("findings", [])),
            "writeguard_readability": result.get("readability", {}),
            "writeguard_grammar_errors": result.get("grammar_error_count", 0),
            "writeguard_ai_score": result.get("ai_content_score"),
            "writeguard_available": True,
        }
    except (ImportError, Exception):
        return {"writeguard_available": False}


def _compute_composite_score(checks: Dict[str, Dict]) -> float:
    """Compute weighted composite quality score."""
    weights = {
        "grammar": 0.20,
        "readability": 0.25,
        "tone": 0.25,
        "plagiarism": 0.15,
        "ai_detection": 0.15,
    }
    total = 0.0
    for check_name, weight in weights.items():
        check_result = checks.get(check_name, {})
        total += check_result.get("score", 0) * weight
    return round(total, 3)


def _store_quality_score(opp_id: str, draft_id: str,
                         composite: float, checks: Dict) -> str:
    """Store quality score in pg_proposal_quality_scores (append-only)."""
    score_id = f"pgqs-{uuid.uuid4().hex[:10]}"
    conn = get_connection()
    try:
        import json
        conn.execute("""
            INSERT INTO pg_proposal_quality_scores
                (id, opportunity_id, draft_id, composite_score,
                 grammar_score, readability_score, tone_score,
                 plagiarism_score, ai_detection_score,
                 check_details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            score_id, opp_id, draft_id, composite,
            checks.get("grammar", {}).get("score", 0),
            checks.get("readability", {}).get("score", 0),
            checks.get("tone", {}).get("score", 0),
            checks.get("plagiarism", {}).get("score", 0),
            checks.get("ai_detection", {}).get("score", 0),
            json.dumps(checks),
            _utcnow_iso(),
        ))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
    return score_id


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Polish Reflex (R8).

    Triggered after R7 Draft. Runs 5 quality checks on all new drafts:
    grammar, readability, tone, plagiarism, AI detection.
    Stores results in pg_proposal_quality_scores (append-only, NIST AU-2).
    """
    conn = get_connection()
    try:
        # Find drafts that haven't been quality-checked yet
        rows = conn.execute("""
            SELECT psd.id AS draft_id, psd.opportunity_id, psd.section_text,
                   po.title
            FROM proposal_section_drafts psd
            INNER JOIN proposal_opportunities po ON po.id = psd.opportunity_id
            LEFT JOIN pg_proposal_quality_scores pqs ON pqs.draft_id = psd.id
            WHERE psd.status = 'draft'
            AND pqs.id IS NULL
            ORDER BY psd.created_at DESC
            LIMIT 20
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    total_score = 0.0
    polish_results = []
    passing = 0
    quality_threshold = config.get("quality_threshold", 0.65)

    for row in rows:
        text = row["section_text"] or ""
        if len(text.strip()) < 10:
            continue

        # Run all 5 quality checks
        checks = {
            "grammar": _check_grammar(text),
            "readability": _check_readability(text),
            "tone": _check_tone(text),
            "plagiarism": _check_plagiarism(text, row["opportunity_id"]),
            "ai_detection": _check_ai_detection(text),
        }

        # Optional WriteGuard deep analysis (D-WG integration)
        if config.get("writeguard_enabled", True):
            wg = _run_writeguard(text, row["opportunity_id"])
            checks["writeguard"] = wg

        composite = _compute_composite_score(checks)
        total_score += composite

        passed = composite >= quality_threshold
        if passed:
            passing += 1

        # Store quality score
        score_id = _store_quality_score(
            row["opportunity_id"], row["draft_id"], composite, checks
        )

        # LLM Judge (Prometheus-2) — semantic evaluation
        judge_color = ""
        judge_composite = 0.0
        try:
            from tools.writing.llm_judge import evaluate_and_store, init_judge_db
            min_wg = config.get("judge_min_writeguard", 0.50)
            if composite >= min_wg:
                init_judge_db()
                # Fetch shall-statement as reference for the judge
                shall_ref = ""
                try:
                    c2 = get_connection()
                    shall_row = c2.execute(
                        "SELECT requirement_text FROM rfp_shall_statements WHERE id = ?",
                        (row.get("shall_statement_id", ""),),
                    ).fetchone()
                    c2.close()
                    if shall_row:
                        shall_ref = shall_row["requirement_text"]
                except Exception:
                    pass

                judge_result = evaluate_and_store(
                    text=text,
                    content_type="proposal_technical",
                    reference=shall_ref,
                    writeguard_score=composite * 100,
                    opportunity_id=row["opportunity_id"],
                    section_id=row["draft_id"],
                )
                if judge_result.get("status") == "evaluated":
                    judge_color = judge_result.get("color_rating", {}).get("color", "")
                    judge_composite = judge_result.get("composite_score", 0)
        except Exception:
            pass  # Judge is non-fatal

        polish_results.append({
            "draft_id": row["draft_id"],
            "opportunity_id": row["opportunity_id"],
            "composite_score": composite,
            "passed": passed,
            "score_id": score_id,
            "judge_color": judge_color,
            "judge_composite": judge_composite,
        })

    avg_score = total_score / len(polish_results) if polish_results else 0
    pass_rate = passing / len(polish_results) if polish_results else 0

    return {
        "success": True,
        "metric_value": round(avg_score, 3),
        "details": {
            "drafts_checked": len(polish_results),
            "avg_quality_score": round(avg_score, 3),
            "pass_rate": round(pass_rate, 3),
            "quality_threshold": quality_threshold,
            "passing": passing,
            "polish_results": polish_results,
        },
    }
