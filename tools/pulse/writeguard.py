"""WriteGuard integration bridge for ICDEV™ Pulse.

FORGE-compliant: All checks are deterministic (no LLM). Rewriting is
handled by Claude Code (the orchestration layer), not by this module.

Connects to ICDEV™'s WriteGuard tools via direct Python imports for:
- Grammar checking (regex-based, deterministic)
- Readability scoring (Flesch-Kincaid, deterministic)
- Tone profiling (thought-leadership + educational)
- Plagiarism detection (RAG similarity)
- AI content detection (deterministic)
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to ICDEV™ WriteGuard tools
ICDEV_ROOT = Path(__file__).resolve().parent.parent.parent  # Up to ICDev/
WRITEGUARD_DIR = ICDEV_ROOT / "tools" / "writing"

# Ensure ICDEV™ root is on sys.path for imports
if str(ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(ICDEV_ROOT))


def _safe_import(func_path: str):
    """Safely import a function from tools.writing module.

    Args:
        func_path: Dotted path like 'grammar_checker.check_grammar'

    Returns:
        The function, or None if import fails.
    """
    module_name, func_name = func_path.rsplit(".", 1)
    try:
        mod = __import__(f"tools.writing.{module_name}", fromlist=[func_name])
        return getattr(mod, func_name)
    except (ImportError, AttributeError) as e:
        logger.warning("Could not import tools.writing.%s: %s", func_path, e)
        return None


def check_grammar(text: str) -> dict:
    """Run grammar check on text using regex patterns."""
    fn = _safe_import("grammar_checker.check_grammar")
    if not fn:
        return {"status": "unavailable", "findings": [], "score": 70}

    try:
        result = fn(text)
        findings = result.get("findings", [])
        total = result.get("summary", {}).get("total", len(findings))
        # Score: start at 100, deduct per finding weighted by severity.
        # Normalize by word count so longer articles aren't penalized
        # disproportionately — a 5k-word article naturally has more long
        # sentences than a 1k-word article.
        word_count = max(len(text.split()), 1)
        # Base penalty budget: 20 deduction points per 1000 words
        # (a perfect 1k-word article with 0 findings = 100)
        penalty_weights = {"info": 0.15, "low": 0.5, "medium": 1.5, "high": 3, "critical": 6}
        raw_penalty = 0.0
        for f in findings:
            severity = f.get("severity", "medium")
            raw_penalty += penalty_weights.get(severity, 1.5)
        # Scale: normalize penalty to errors-per-1000-words
        penalty_per_1k = (raw_penalty / word_count) * 1000
        # Cap deduction at 60 points (floor of 40)
        score = max(40, round(100.0 - penalty_per_1k, 1))
        return {"status": "ok", "findings": findings, "score": score, "issue_count": total}
    except Exception as e:
        logger.error("Grammar check error: %s", e)
        return {"status": "error", "findings": [], "score": 70, "error": str(e)}


def check_readability(text: str) -> dict:
    """Analyze readability metrics (Flesch-Kincaid)."""
    fn = _safe_import("readability_scorer.score_readability")
    if not fn:
        return {"status": "unavailable", "score": 70}

    try:
        result = fn(text)
        # score_readability returns dict with grade_level, flesch_ease, etc.
        grade = result.get("grade_level", 12)
        flesch = result.get("flesch_reading_ease", result.get("flesch_ease", 50))
        avg_sentence = result.get("avg_sentence_length", 20)

        # Convert Flesch ease (0-100, higher=easier) to a quality score.
        # Pulse articles are professional technical content (compliance,
        # DevSecOps, federal IT). Flesch 0-30 is normal for this domain
        # (grade level 15-25 is expected for NIST/FedRAMP content).
        # Scoring curve calibrated for GovTech audience:
        #   Flesch 60+ (easy)       → 95-100
        #   Flesch 40-60 (standard) → 85-95
        #   Flesch 20-40 (technical)→ 75-85  (sweet spot for compliance content)
        #   Flesch 0-20 (dense)     → 65-75  (acceptable for federal IT)
        if isinstance(flesch, (int, float)):
            if flesch >= 60:
                score = min(100, 95 + (flesch - 60) * 0.125)
            elif flesch >= 40:
                score = 85 + (flesch - 40) * 0.5   # 40→85, 60→95
            elif flesch >= 20:
                score = 75 + (flesch - 20) * 0.5   # 20→75, 40→85
            else:
                score = max(60, 65 + flesch * 0.5)  # 0→65, 20→75
        else:
            score = 70

        return {
            "status": "ok",
            "score": round(score, 1),
            "grade_level": grade,
            "flesch_ease": flesch,
            "avg_sentence_length": avg_sentence,
            **{k: v for k, v in result.items() if k not in ("grade_level", "flesch_ease", "avg_sentence_length")},
        }
    except Exception as e:
        logger.error("Readability check error: %s", e)
        return {"status": "error", "score": 70, "error": str(e)}


def check_tone(text: str, target_tone: str = "thought-leadership-educational") -> dict:
    """Analyze tone against target profile."""
    fn = _safe_import("tone_profiler.profile_tone")
    if not fn:
        return {"status": "unavailable", "score": 70, "findings": []}

    try:
        result = fn(text, use_llm=False)
        # profile_tone returns dict with tone dimensions, findings
        findings = result.get("findings", result.get("issues", []))
        score = result.get("score", result.get("overall_score", None))

        # If tone profiler returned a flat default (70) or None, compute
        # from profile dimensions. Pulse target: thought-leadership =
        # authoritative + technical + educational (plain_language).
        profile = result.get("profile", {})
        if (not isinstance(score, (int, float)) or score == 70) and profile:
            # Desired tone weights for Pulse articles
            desired = {"authoritative": 0.30, "technical": 0.30,
                       "plain_language": 0.20, "formal": 0.10, "academic": 0.10}
            alignment = 0.0
            for dim, weight in desired.items():
                val = profile.get(dim, 0.0)
                # Each dimension 0-1; scale contribution by weight
                alignment += min(val / 0.25, 1.0) * weight  # 0.25+ = full marks
            score = round(max(50, min(100, 60 + alignment * 40)), 1)

        if not isinstance(score, (int, float)):
            score = 70
        return {"status": "ok", "score": score, "findings": findings, **{
            k: v for k, v in result.items() if k not in ("findings", "issues", "score", "overall_score")
        }}
    except Exception as e:
        logger.error("Tone check error: %s", e)
        return {"status": "error", "score": 70, "findings": [], "error": str(e)}


def check_plagiarism(text: str) -> dict:
    """Check for plagiarism using RAG similarity."""
    fn = _safe_import("plagiarism_detector.check_plagiarism")
    if not fn:
        return {"status": "unavailable", "score": 0}

    try:
        result = fn(text)
        raw_score = result.get("score", result.get("similarity_score", 0))
        if not isinstance(raw_score, (int, float)):
            raw_score = 0
        # Invert: raw 0 (no plagiarism) = quality 100, raw 100 (full copy) = quality 0
        quality_score = round(100 - raw_score, 1)
        return {"status": "ok", "score": quality_score, "raw_similarity": raw_score, **{
            k: v for k, v in result.items() if k not in ("score", "similarity_score")
        }}
    except Exception as e:
        logger.error("Plagiarism check error: %s", e)
        return {"status": "error", "score": 100, "error": str(e)}


def check_ai_detection(text: str) -> dict:
    """Run AI content detection (deterministic advisory)."""
    fn = _safe_import("ai_content_detector.detect_ai_content")
    if not fn:
        return {"status": "unavailable", "score": 50}

    try:
        result = fn(text)
        # ai_score is 0-1 probability (0=human, 1=AI)
        raw_score = result.get("ai_score", result.get("score", result.get("ai_probability", 0.5)))
        if not isinstance(raw_score, (int, float)):
            raw_score = 0.5
        # Normalize: if raw is 0-1 scale, convert to 0-100
        if raw_score <= 1.0:
            raw_pct = raw_score * 100
        else:
            raw_pct = raw_score
        # Invert: low AI probability = high quality score
        quality_score = round(100 - raw_pct, 1)
        return {"status": "ok", "score": quality_score, "ai_score": raw_score, **{
            k: v for k, v in result.items() if k not in ("score", "ai_probability", "ai_score")
        }}
    except Exception as e:
        logger.error("AI detection error: %s", e)
        return {"status": "error", "score": 50, "error": str(e)}


def run_full_quality_check(text: str) -> dict:
    """Run all WriteGuard checks and return consolidated results.

    Returns:
        dict with keys: passed (bool), grammar, readability, tone,
        plagiarism, ai_detection, overall_score (0-100)
    """
    logger.info("Running full WriteGuard quality check...")

    results = {
        "grammar": check_grammar(text),
        "readability": check_readability(text),
        "tone": check_tone(text),
        "plagiarism": check_plagiarism(text),
        "ai_detection": check_ai_detection(text),
    }

    # Calculate overall score
    scores = []
    for key, result in results.items():
        if result.get("status") == "unavailable":
            logger.warning("WriteGuard %s unavailable, skipping", key)
            continue
        score = result.get("score", result.get("overall_score", 70))
        if isinstance(score, (int, float)):
            scores.append(score)

    overall_score = sum(scores) / len(scores) if scores else 50.0

    # Pass threshold: overall >= 60 and no critical failures
    # plagiarism score is now inverted (100=clean, 0=full copy), so check > 15 (=clean)
    plagiarism_quality = results["plagiarism"].get("score", 100)
    passed = overall_score >= 60 and (
        plagiarism_quality > 15 if isinstance(plagiarism_quality, (int, float)) else True
    )

    return {
        "passed": passed,
        "overall_score": round(overall_score, 1),
        "details": results,
        "recommendations": _generate_recommendations(results),
    }


def rewrite_content(text: str, quality_results: dict) -> dict:
    """Apply deterministic fixes and return findings for Claude Code to rewrite.

    FORGE-compliant: This function does NOT call any LLM. It applies regex-based
    deterministic fixes and returns the findings/instructions so Claude Code
    (the orchestration layer) can perform the LLM-quality rewrite directly.

    Args:
        text: Original markdown content.
        quality_results: Output from run_full_quality_check().

    Returns:
        dict with 'rewritten_text' (deterministic fixes applied),
        'changes_made', 'findings', 'rewrite_instructions', 'status'.
    """
    if not text or not text.strip():
        return {"rewritten_text": "", "changes_made": [], "findings": [], "status": "no_input"}

    # Collect findings from all check results
    findings = _extract_findings(quality_results)

    if not findings:
        logger.info("No actionable findings — skipping rewrite")
        return {
            "rewritten_text": text,
            "changes_made": [],
            "findings": [],
            "rewrite_instructions": "",
            "status": "no_findings",
        }

    # Apply deterministic fixes (regex-based, always runs)
    try:
        from tools.writing.rewriter import _apply_deterministic_fixes
        text, det_changes = _apply_deterministic_fixes(text)
    except ImportError:
        det_changes = []

    # Build instruction list for Claude Code to use
    instructions = "\n".join(
        f"- {f.get('category', 'general')}: {f.get('message', '')} → {f.get('suggestion', '')}"
        for f in findings if f.get("message")
    )

    changes = det_changes + [f.get("message", "") for f in findings if f.get("message")]

    return {
        "rewritten_text": text,
        "changes_made": changes,
        "findings": findings,
        "rewrite_instructions": instructions,
        "status": "deterministic_fixes_applied",
        "needs_claude_rewrite": True,
    }


def _extract_findings(quality_results: dict) -> list[dict]:
    """Extract actionable findings from WriteGuard quality check results.

    Maps the various check result formats into the unified finding format
    expected by rewriter.py: {category, message, suggestion}.
    """
    findings = []
    details = quality_results.get("details", {})

    # Grammar findings
    grammar = details.get("grammar", {})
    for issue in grammar.get("issues", grammar.get("findings", [])):
        findings.append({
            "category": "grammar",
            "message": issue.get("message", issue.get("description", "")),
            "suggestion": issue.get("suggestion", issue.get("replacement", "")),
        })

    # Readability findings
    readability = details.get("readability", {})
    grade = readability.get("grade_level", 12)
    if isinstance(grade, (int, float)) and grade > 14:
        findings.append({
            "category": "readability",
            "message": f"Reading grade level {grade:.0f} is too high for broad audience",
            "suggestion": "Simplify complex sentences, use shorter words, break up long paragraphs",
        })
    avg_sentence = readability.get("avg_sentence_length", 0)
    if isinstance(avg_sentence, (int, float)) and avg_sentence > 25:
        findings.append({
            "category": "readability",
            "message": f"Average sentence length ({avg_sentence:.0f} words) is too long",
            "suggestion": "Break long sentences into shorter ones (target 15-20 words)",
        })

    # Tone findings
    tone = details.get("tone", {})
    for issue in tone.get("issues", tone.get("findings", [])):
        findings.append({
            "category": "tone",
            "message": issue.get("message", issue.get("description", "")),
            "suggestion": issue.get("suggestion", ""),
        })

    # AI detection — high raw ai_score (0-1) means text sounds robotic
    ai_det = details.get("ai_detection", {})
    ai_score = ai_det.get("ai_score", 0)  # raw 0-1 scale
    if isinstance(ai_score, (int, float)) and ai_score > 0.7:
        findings.append({
            "category": "ai_detection",
            "message": "Content reads as AI-generated (high predictability score)",
            "suggestion": "Vary sentence structure, add concrete examples, use specific details instead of generalities",
        })

    # Recommendations as fallback findings
    for rec in quality_results.get("recommendations", []):
        findings.append({
            "category": "recommendation",
            "message": rec,
            "suggestion": "",
        })

    return findings


def _generate_recommendations(results: dict) -> list[str]:
    """Generate actionable recommendations from WriteGuard results."""
    recs = []

    grammar = results.get("grammar", {})
    if grammar.get("issue_count", 0) > 5:
        recs.append("Review grammar issues — multiple errors detected")

    readability = results.get("readability", {})
    grade_level = readability.get("grade_level", 12)
    if isinstance(grade_level, (int, float)) and grade_level > 14:
        recs.append("Simplify language — reading level is too high for broad audience")

    ai_detection = results.get("ai_detection", {})
    # Use raw ai_score (0-1, where >0.7 = likely AI)
    raw_ai = ai_detection.get("ai_score", 0)
    if isinstance(raw_ai, (int, float)) and raw_ai > 0.7:
        recs.append(
            "Content reads as AI-generated — add more personal anecdotes and varied sentence structure"
        )

    plagiarism = results.get("plagiarism", {})
    # Use raw_similarity (0-100, where >60 = high plagiarism)
    raw_plag = plagiarism.get("raw_similarity", 0)
    if isinstance(raw_plag, (int, float)) and raw_plag > 60:
        recs.append("High similarity detected — rephrase flagged sections")

    return recs
