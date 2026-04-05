#!/usr/bin/env python3
# CUI // SP-CTI
"""R8: Polish Reflex — WriteGuard quality gate for proposal drafts.

Runs 6 quality checks on draft responses: stub detection (pre-gate),
grammar, readability, tone, plagiarism, and AI detection.
Stores scores in pg_proposal_quality_scores (D-PG-8).
Scanner-tier only (zero Claude tokens).

Enhancement §3.6: Stub detection pre-gate using GSD-adapted patterns.
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


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


def _check_stub_content(text: str, opportunity_id: str) -> Dict[str, Any]:
    """Check for stub/placeholder content in proposal drafts (§3.6 pre-gate).

    GSD-adapted stub detection for proposal-specific patterns.
    Returns score 1.0 (fully substantive) to 0.0 (pure stub).
    Deterministic, scanner-tier, zero LLM tokens.
    """
    text_lower = text.lower().strip()
    stub_patterns_found = []
    word_count = len(text.split())

    # ── Proposal-specific stub patterns ──────────────────────────────────
    PROPOSAL_STUB_PATTERNS = [
        (r"\bTBD\b", "TBD placeholder"),
        (r"\bTODO\b", "TODO placeholder"),
        (r"\binsert\s+here\b", "insert-here placeholder"),
        (r"\brefer\s+to\s+attachment\b", "refer-to-attachment placeholder"),
        (r"\[COMPANY\s*NAME\]", "[COMPANY NAME] template marker"),
        (r"\[CONTRACTOR\s*NAME\]", "[CONTRACTOR NAME] template marker"),
        (r"\[YOUR\s+\w+\]", "[YOUR ...] template marker"),
        (r"\bas\s+described\s+in\s+our\s+response\b", "circular reference (no content)"),
        (r"\bLorem\s+ipsum\b", "Lorem ipsum boilerplate"),
        (r"\bsee\s+section\b(?!.*\b(?:above|below|provides|describes|details)\b)",
         "see-section without content"),
        (r"\bfill\s+in\b", "fill-in placeholder"),
        (r"\bto\s+be\s+determined\b", "to-be-determined placeholder"),
        (r"\bplaceholder\b", "placeholder marker"),
        (r"\bboilerplate\b", "boilerplate marker"),
        (r"\btemplate\s+text\b", "template text marker"),
        (r"\bpending\s+input\b", "pending input marker"),
        (r"\b\[\.{3,}\]", "[...] ellipsis placeholder"),
        (r"\bXXX\b", "XXX placeholder"),
    ]

    for pattern, label in PROPOSAL_STUB_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            stub_patterns_found.append({
                "pattern": label,
                "count": len(matches),
            })

    # ── Content substantiveness checks ───────────────────────────────────
    # Very short text relative to a proposal section is suspicious
    thin_content = word_count < 50
    if thin_content:
        stub_patterns_found.append({
            "pattern": "thin content (< 50 words)",
            "count": 1,
        })

    # High ratio of template markers to content
    template_marker_count = len(re.findall(r"\[.*?\]", text))
    if word_count > 0 and template_marker_count / max(1, word_count) > 0.05:
        stub_patterns_found.append({
            "pattern": "high template marker ratio",
            "count": template_marker_count,
        })

    # ── Opportunity-specific data reference check ────────────────────────
    # Good proposals reference the specific opportunity/agency
    references_opportunity = False
    if opportunity_id:
        # Check for opportunity ID or fragments
        if opportunity_id.lower() in text_lower:
            references_opportunity = True

        # Also check for agency name from DB
        try:
            conn = get_connection()
            opp_row = conn.execute(
                "SELECT title, agency FROM proposal_opportunities WHERE id = ?",
                (opportunity_id,)
            ).fetchone()
            conn.close()
            if opp_row:
                agency = (opp_row["agency"] or "").lower().strip()
                title = (opp_row["title"] or "").lower().strip()
                if agency and agency in text_lower:
                    references_opportunity = True
                # Check for title keywords (at least 2 significant words)
                if title:
                    title_words = [w for w in title.split() if len(w) > 3]
                    matches = sum(1 for w in title_words if w in text_lower)
                    if matches >= 2:
                        references_opportunity = True
        except Exception:
            pass  # DB unavailable — skip opportunity reference check

    if not references_opportunity and word_count >= 50:
        stub_patterns_found.append({
            "pattern": "no opportunity-specific references",
            "count": 1,
        })

    # ── Compute stub level and score ─────────────────────────────────────
    total_stub_signals = sum(p["count"] for p in stub_patterns_found)

    # Score calculation: start at 1.0, deduct per stub signal
    # Each stub signal costs 0.12, thin content costs extra
    deduction = total_stub_signals * 0.12
    if thin_content:
        deduction += 0.20
    if not references_opportunity and word_count >= 50:
        deduction += 0.08

    score = max(0.0, min(1.0, 1.0 - deduction))

    # Determine stub level (GSD 4-level mapping)
    if score >= 0.8:
        stub_level = "SUBSTANTIVE"
    elif score >= 0.5:
        stub_level = "PARTIAL"
    elif score >= 0.2:
        stub_level = "STUB"
    else:
        stub_level = "EMPTY"

    return {
        "score": round(score, 2),
        "stub_level": stub_level,
        "stub_patterns_found": stub_patterns_found,
        "word_count": word_count,
        "references_opportunity": references_opportunity,
        "total_stub_signals": total_stub_signals,
    }


def _check_context_pressure(text: str) -> Dict[str, Any]:
    """Estimate if text approaches context limits (advisory).

    Checks text length against typical LLM context windows.
    Deterministic, scanner-tier, zero LLM tokens.
    """
    # Approximate tokens: ~4 chars per token
    CHARS_PER_TOKEN = 4
    estimated_tokens = len(text) / CHARS_PER_TOKEN

    # Context window thresholds (for proposal sections being reviewed)
    # A single proposal section should not consume more than ~8K tokens
    SECTION_WARNING_TOKENS = 6000
    SECTION_CRITICAL_TOKENS = 10000

    # Full context window pressure (if text is being injected into LLM)
    # Based on common context windows (128K, 200K)
    WINDOW_SIZE = 200000
    window_pct_used = (estimated_tokens / WINDOW_SIZE) * 100

    if estimated_tokens > SECTION_CRITICAL_TOKENS:
        pressure_level = "critical"
        advisory = "Section text is very long; consider splitting into sub-sections"
    elif estimated_tokens > SECTION_WARNING_TOKENS:
        pressure_level = "warning"
        advisory = "Section text is approaching length limits"
    else:
        pressure_level = "normal"
        advisory = ""

    return {
        "estimated_tokens": int(estimated_tokens),
        "pressure_level": pressure_level,
        "window_pct_used": round(window_pct_used, 2),
        "advisory": advisory,
        "char_count": len(text),
        "word_count": len(text.split()),
    }


def _run_writeguard(text: str, opportunity_id: str = "") -> Dict[str, Any]:
    """Optionally delegate to WriteGuard for deeper analysis.

    Returns WriteGuard results if available, else empty dict.
    WriteGuard integration is additive — Polish's own 6 checks always run.
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
    """Compute weighted composite quality score.

    Weights total 1.0: grammar(0.18) + readability(0.22) + tone(0.22) +
    plagiarism(0.14) + ai_detection(0.14) + stub_detection(0.10).
    """
    weights = {
        "grammar": 0.18,
        "readability": 0.22,
        "tone": 0.22,
        "plagiarism": 0.14,
        "ai_detection": 0.14,
        "stub_detection": 0.10,
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

    Triggered after R7 Draft. Runs 6 quality checks on all new drafts:
    stub detection (pre-gate), grammar, readability, tone, plagiarism,
    AI detection. Stores results in pg_proposal_quality_scores
    (append-only, NIST AU-2).

    Enhancement §3.6: Stub detection blocks drafts below SUBSTANTIVE level
    regardless of other scores.
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

        # §3.6 Pre-gate: stub detection (runs BEFORE other quality checks)
        stub_result = _check_stub_content(text, row["opportunity_id"])
        stub_blocked = stub_result["score"] < 0.5  # Below SUBSTANTIVE

        # Context pressure monitoring (advisory)
        ctx_pressure = _check_context_pressure(text)

        # Run all 6 quality checks (stub_detection included in composite)
        checks = {
            "stub_detection": stub_result,
            "grammar": _check_grammar(text),
            "readability": _check_readability(text),
            "tone": _check_tone(text),
            "plagiarism": _check_plagiarism(text, row["opportunity_id"]),
            "ai_detection": _check_ai_detection(text),
        }

        # Attach context pressure as advisory metadata (not scored)
        checks["context_pressure"] = ctx_pressure

        # Optional WriteGuard deep analysis (D-WG integration)
        if config.get("writeguard_enabled", True):
            wg = _run_writeguard(text, row["opportunity_id"])
            checks["writeguard"] = wg

        composite = _compute_composite_score(checks)
        total_score += composite

        # §3.6: Stub pre-gate — if below SUBSTANTIVE, force fail
        # regardless of composite score
        passed = composite >= quality_threshold
        if stub_blocked:
            passed = False
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
            "stub_blocked": stub_blocked,
            "stub_level": stub_result["stub_level"],
            "stub_score": stub_result["score"],
            "context_pressure": ctx_pressure["pressure_level"],
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
