from __future__ import annotations

"""WriteGuard integration bridge for ICDEV™ Pulse.

FORGE-compliant: All checks are deterministic (no LLM). Rewriting is
handled by Claude Code (the orchestration layer), not by this module.

Connects to ICDEV™'s WriteGuard tools via direct Python imports for:
- Grammar checking (regex-based, deterministic)
- Readability scoring (Flesch-Kincaid, deterministic)
- Tone profiling (thought-leadership + educational)
- Plagiarism detection (RAG similarity)
- AI content detection (deterministic)

v6b additions:
- compute_composites(): 5 named composite scores (Correctness, Clarity,
  Delivery, Originality, Engagement) mapped from base checks + inline metrics
- score_sections(): section-level scoring split by markdown headings,
  flags the weakest section with targeted recommendations
- Color badge on WriteGuard Score: Green ≥80, Yellow 60-79, Red <60
"""

import math
import re
import sys
from pathlib import Path


# Path to ICDEV™ WriteGuard tools
ICDEV_ROOT = Path(__file__).resolve().parent.parent.parent  # Up to ICDev/
WRITEGUARD_DIR = ICDEV_ROOT / "tools" / "writing"

# Ensure ICDEV™ root is on sys.path for imports
if str(ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(ICDEV_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger(__name__)


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
    """Analyze readability metrics (Flesch-Kincaid, Gunning Fog, SMOG, Coleman-Liau, composite)."""
    fn = _safe_import("readability_scorer.score_readability")
    if not fn:
        return {"status": "unavailable", "score": 70}

    try:
        result = fn(text)
        # score_readability returns dict with grade_level, flesch_ease, and multi-metric fields.
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
                score = 85 + (flesch - 40) * 0.5  # 40→85, 60→95
            elif flesch >= 20:
                score = 75 + (flesch - 20) * 0.5  # 20→75, 40→85
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
            "flesch_kincaid": result.get("flesch_kincaid"),
            "gunning_fog": result.get("gunning_fog"),
            "smog_index": result.get("smog_index"),
            "coleman_liau": result.get("coleman_liau"),
            "composite_grade": result.get("composite_grade"),
            **{k: v for k, v in result.items() if k not in (
                "grade_level", "flesch_ease", "avg_sentence_length",
                "flesch_kincaid", "gunning_fog", "smog_index", "coleman_liau", "composite_grade",
            )},
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
            desired = {
                "authoritative": 0.30,
                "technical": 0.30,
                "plain_language": 0.20,
                "formal": 0.10,
                "academic": 0.10,
            }
            alignment = 0.0
            for dim, weight in desired.items():
                val = profile.get(dim, 0.0)
                # Each dimension 0-1; scale contribution by weight
                alignment += min(val / 0.25, 1.0) * weight  # 0.25+ = full marks
            score = round(max(50, min(100, 60 + alignment * 40)), 1)

        if not isinstance(score, (int, float)):
            score = 70
        return {
            "status": "ok",
            "score": score,
            "findings": findings,
            **{k: v for k, v in result.items() if k not in ("findings", "issues", "score", "overall_score")},
        }
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
        return {
            "status": "ok",
            "score": quality_score,
            "raw_similarity": raw_score,
            **{k: v for k, v in result.items() if k not in ("score", "similarity_score")},
        }
    except Exception as e:
        logger.error("Plagiarism check error: %s", e)
        return {"status": "error", "score": 100, "error": str(e)}


def check_portion_marking(text: str) -> dict:
    """Run DoDM 5200.01 portion marking validation.

    Checks:
    - All-or-nothing rule: if any paragraph is marked, ALL must be marked
    - High-water mark banner derivation
    - Contradiction detection (e.g. (U) paragraph referencing SECRET content)
    - Returns designation indicator block for classified documents
    """
    fn = _safe_import("portion_marking_checker.check_portion_markings")
    if not fn:
        return {"status": "unavailable", "score": 100, "findings": [], "has_marks": False}

    try:
        return fn(text)
    except Exception as e:
        logger.error("Portion marking check error: %s", e)
        return {"status": "error", "score": 100, "findings": [], "has_marks": False, "error": str(e)}


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
        return {
            "status": "ok",
            "score": quality_score,
            "ai_score": raw_score,
            **{k: v for k, v in result.items() if k not in ("score", "ai_probability", "ai_score")},
        }
    except Exception as e:
        logger.error("AI detection error: %s", e)
        return {"status": "error", "score": 50, "error": str(e)}


# ────────────────────────────────────────────────────────────────────────────
# Inline text-metric helpers (pure Python, no external deps)
# ────────────────────────────────────────────────────────────────────────────

# Passive voice: "is/are/was/were/be/been/being + past participle"
_PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+\w+ed\b",
    re.IGNORECASE,
)

# Words that weaken delivery through vagueness or uncertainty
_HEDGING_WORDS = frozenset(
    {
        "maybe",
        "perhaps",
        "possibly",
        "probably",
        "apparently",
        "seemingly",
        "could",
        "might",
        "may",
        "seem",
        "seems",
        "appears",
        "generally",
        "usually",
        "often",
        "sometimes",
        "typically",
        "relatively",
        "fairly",
        "somewhat",
        "rather",
        "approximately",
        "around",
    }
)

# Common tech-content clichés to penalize
_CLICHES = (
    "at the end of the day",
    "think outside the box",
    "low-hanging fruit",
    "move the needle",
    "circle back",
    "deep dive",
    "game changer",
    "paradigm shift",
    "best practices",
    "cutting edge",
    "bleeding edge",
    "state of the art",
    "mission critical",
    "value add",
    "actionable insights",
    "going forward",
    "in this day and age",
    "it is what it is",
    "hit the ground running",
    "take it to the next level",
    "boil the ocean",
    "reinvent the wheel",
    "get on the same page",
    "delve into",
    "in the realm of",
    "in today's fast-paced",
    "in today's digital age",
    "it's worth noting",
    "it should be noted",
    "it is important to note",
    "as mentioned earlier",
    "as we can see",
)

# AI-generated text phrase patterns used in section-level originality scoring
_AI_PHRASES = (
    "in conclusion",
    "to summarize",
    "it is important to note",
    "it is worth noting",
    "it should be noted",
    "as mentioned",
    "as discussed",
    "in summary",
    "in today's",
    "in the realm of",
    "the world of",
    "at its core",
    "delve into",
    "dive deep",
    "navigating",
    "transformative",
    "harnessing the power",
)


def _tokenize_words(text: str) -> list:
    """Return lowercase alphabetic words from text."""
    return re.findall(r"\b[a-z]+\b", text.lower())


def _tokenize_sentences(text: str) -> list:
    """Return sentences with 3+ words from text."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s for s in parts if len(s.split()) >= 3]


def _passive_voice_ratio(text: str) -> float:
    """Fraction of sentences that contain passive voice (0–1)."""
    sentences = _tokenize_sentences(text)
    if not sentences:
        return 0.0
    passive = sum(1 for s in sentences if _PASSIVE_RE.search(s))
    return passive / len(sentences)


def _hedging_ratio(text: str) -> float:
    """Hedging-word density normalized to 0–1 (0.10 density = max 1.0)."""
    words = _tokenize_words(text)
    if not words:
        return 0.0
    count = sum(1 for w in words if w in _HEDGING_WORDS)
    return min(1.0, (count / len(words)) / 0.10)


def _sentence_variety_score(text: str) -> float:
    """Sentence-length std-dev normalized to 0–1 (std ≥ 8 words = 1.0)."""
    sentences = _tokenize_sentences(text)
    if len(sentences) < 3:
        return 0.5
    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    std = math.sqrt(sum((ln - mean) ** 2 for ln in lengths) / len(lengths))
    return min(1.0, std / 8.0)


def _vocabulary_richness(text: str) -> float:
    """Type-token ratio on first 200 words (unique / total, 0–1)."""
    words = _tokenize_words(text)
    if not words:
        return 0.5
    sample = words[:200]
    return len(set(sample)) / len(sample)


def _cliche_density(text: str) -> float:
    """Cliché count per 100 words, normalized 0–1 (1.0 = very clichéd)."""
    tl = text.lower()
    count = sum(1 for c in _CLICHES if c in tl)
    words = max(len(_tokenize_words(text)), 1)
    return min(1.0, count / max(words / 100.0, 1.0))


def _inline_flesch(text: str) -> float:
    """Compute Flesch reading ease inline (no external deps, 0–100)."""
    sentences = _tokenize_sentences(text)
    words = _tokenize_words(text)
    if not sentences or not words:
        return 50.0

    def _syllables(word: str) -> int:
        return max(1, len(re.findall(r"[aeiou]+", word.rstrip("e"))))

    total_syl = sum(_syllables(w) for w in words)
    avg_sent = len(words) / len(sentences)
    avg_syl = total_syl / len(words)
    return max(0.0, min(100.0, 206.835 - 1.015 * avg_sent - 84.6 * avg_syl))


def _flesch_to_score(flesch: float) -> float:
    """Map Flesch ease to quality score using govtech calibration curve."""
    if flesch >= 60:
        return min(100.0, 95.0 + (flesch - 60) * 0.125)
    elif flesch >= 40:
        return 85.0 + (flesch - 40) * 0.5
    elif flesch >= 20:
        return 75.0 + (flesch - 20) * 0.5
    else:
        return max(60.0, 65.0 + flesch * 0.5)


def _color_badge(score: float) -> str:
    """Return color badge string: 'green' ≥80, 'yellow' 60-79, 'red' <60."""
    if score >= 80:
        return "green"
    elif score >= 60:
        return "yellow"
    return "red"


# ────────────────────────────────────────────────────────────────────────────
# Named Composite Scoring (v6b)
# ────────────────────────────────────────────────────────────────────────────


def compute_composites(text: str, details: dict) -> dict:
    """Map WriteGuard base check details to 5 named composites.

    Composites
    ----------
    Correctness : grammar (grammar score maps 1-to-1)
    Clarity     : readability + paragraph-structure bonus (up to +5 pts)
    Delivery    : tone − passive penalty (max 20 pts) − hedging penalty (max 15 pts)
    Originality : average of plagiarism + AI-detection scores
    Engagement  : sentence variety (40%) + vocabulary TTR (40%) + cliché-free (20%)

    Color badge on overall WriteGuard Score: Green ≥80, Yellow 60-79, Red <60.

    Args:
        text: Raw markdown/plain text (used for inline metric computation).
        details: ``details`` sub-dict from :func:`run_full_quality_check`.

    Returns:
        dict with keys: correctness, clarity, delivery, originality, engagement,
        overall_score (0-100), color_badge, _metrics.
    """
    grammar_s = float(details.get("grammar", {}).get("score", 70.0))
    readability_s = float(details.get("readability", {}).get("score", 70.0))
    tone_s = float(details.get("tone", {}).get("score", 70.0))
    plagiarism_s = float(details.get("plagiarism", {}).get("score", 100.0))
    ai_s = float(details.get("ai_detection", {}).get("score", 70.0))

    # Inline metrics (pure Python)
    passive = _passive_voice_ratio(text)
    hedging = _hedging_ratio(text)
    variety = _sentence_variety_score(text)
    vocab = _vocabulary_richness(text)
    cliche = _cliche_density(text)

    # Structure bonus: reward well-sized paragraphs (50–150 words ideal)
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.split()) >= 5]
    if paragraphs:
        avg_para = sum(len(p.split()) for p in paragraphs) / len(paragraphs)
        structure_bonus = 5.0 if 50 <= avg_para <= 150 else (2.5 if 30 <= avg_para <= 200 else 0.0)
    else:
        structure_bonus = 0.0

    correctness = round(max(0.0, min(100.0, grammar_s)), 1)
    clarity = round(max(0.0, min(100.0, readability_s + structure_bonus)), 1)
    delivery = round(max(0.0, min(100.0, tone_s - passive * 20.0 - hedging * 15.0)), 1)
    originality = round((plagiarism_s + ai_s) / 2.0, 1)
    engagement = round(
        max(0.0, min(100.0, variety * 100.0 * 0.40 + vocab * 100.0 * 0.40 + (1.0 - cliche) * 100.0 * 0.20)),
        1,
    )

    overall = round((correctness + clarity + delivery + originality + engagement) / 5.0, 1)

    return {
        "correctness": correctness,
        "clarity": clarity,
        "delivery": delivery,
        "originality": originality,
        "engagement": engagement,
        "overall_score": overall,
        "color_badge": _color_badge(overall),
        "_metrics": {
            "passive_ratio": round(passive, 3),
            "hedging_ratio": round(hedging, 3),
            "variety": round(variety, 3),
            "vocab_richness": round(vocab, 3),
            "cliche_density": round(cliche, 3),
            "structure_bonus": structure_bonus,
        },
    }


def score_sections(text: str) -> dict:
    """Score each markdown heading-section independently.

    Splits the text at ``## …`` / ``### …`` (up to H4) boundaries. Sections
    with fewer than 30 words are skipped. Each section is scored on all 5
    composites using inline (no-external-dep) methods.  The weakest section
    is flagged with targeted recommendations.

    Args:
        text: Full markdown or plain-text article body.

    Returns:
        dict:
          sections – list of {heading, word_count, scores, avg, badge}
          weakest  – {heading, avg, badge, recommendations} or None
    """
    heading_re = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    headings = [(m.start(), m.group(2).strip()) for m in heading_re.finditer(text)]

    if not headings:
        segments = [("(Full Document)", text)]
    else:
        segments = []
        for i, (pos, title) in enumerate(headings):
            end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
            body = "\n".join(text[pos:end].split("\n")[1:]).strip()
            if body:
                segments.append((title, body))

    scored = []
    for heading, body in segments:
        words = _tokenize_words(body)
        if len(words) < 30:
            continue

        # Correctness: penalize double-spaces and likely comma splices
        dbl = len(re.findall(r"  +", body))
        splice = len(re.findall(r",\s+[a-z]{5,}\s+[a-z]{5,}", body))
        penalty = (dbl + splice * 2) / max(len(words) / 1000.0, 0.1)
        correctness = max(50.0, round(100.0 - penalty * 5.0, 1))

        # Clarity: inline Flesch → quality score
        clarity = round(_flesch_to_score(_inline_flesch(body)), 1)

        # Delivery: passive + hedging (baseline 75 — no tone profiler for sections)
        passive = _passive_voice_ratio(body)
        hedging = _hedging_ratio(body)
        delivery = round(max(40.0, min(100.0, 75.0 - passive * 20.0 - hedging * 15.0)), 1)

        # Originality: AI phrase detection (simplified, no RAG per section)
        body_lower = body.lower()
        ai_hits = sum(1 for p in _AI_PHRASES if p in body_lower)
        originality = round(max(40.0, min(100.0, 90.0 - ai_hits * 8.0)), 1)

        # Engagement: variety + vocab + cliché-free
        variety = _sentence_variety_score(body)
        vocab = _vocabulary_richness(body)
        cliche = _cliche_density(body)
        engagement = round(
            max(30.0, min(100.0, variety * 100.0 * 0.40 + vocab * 100.0 * 0.40 + (1.0 - cliche) * 100.0 * 0.20)),
            1,
        )

        avg = round((correctness + clarity + delivery + originality + engagement) / 5.0, 1)
        scored.append(
            {
                "heading": heading,
                "word_count": len(words),
                "scores": {
                    "correctness": correctness,
                    "clarity": clarity,
                    "delivery": delivery,
                    "originality": originality,
                    "engagement": engagement,
                },
                "avg": avg,
                "badge": _color_badge(avg),
            }
        )

    if not scored:
        return {"sections": [], "weakest": None}

    weakest = min(scored, key=lambda s: s["avg"])
    ws = weakest["scores"]
    recs = []
    if ws["correctness"] < 70:
        recs.append("Fix grammar and formatting issues (double spaces, run-on sentences).")
    if ws["clarity"] < 70:
        recs.append("Break up long sentences; aim for a Flesch ease score above 30.")
    if ws["delivery"] < 70:
        recs.append("Reduce passive voice and hedging words for stronger, clearer delivery.")
    if ws["originality"] < 70:
        recs.append("Rewrite AI-sounding phrases; add specific examples and data points.")
    if ws["engagement"] < 70:
        recs.append("Vary sentence lengths, use richer vocabulary, and avoid clichés.")

    return {
        "sections": scored,
        "weakest": {
            "heading": weakest["heading"],
            "avg": weakest["avg"],
            "badge": weakest["badge"],
            "recommendations": recs or ["This section meets quality standards."],
        },
    }


def run_full_quality_check(text: str) -> dict:
    """Run all WriteGuard checks and return consolidated results.

    Returns:
        dict with keys: passed (bool), grammar, readability, tone,
        plagiarism, ai_detection, overall_score (0-100)
    """
    logger.info("Running full WriteGuard quality check...")

    pm_result = check_portion_marking(text)
    results = {
        "grammar": check_grammar(text),
        "readability": check_readability(text),
        "tone": check_tone(text),
        "plagiarism": check_plagiarism(text),
        "ai_detection": check_ai_detection(text),
        "portion_marking": pm_result,
    }

    # Add new analysis_engine dimensions (passive voice, cliches, etc.)
    try:
        from tools.writing.analysis_engine import (
            _passive_voice_check,
            _overused_words_check,
            _cliche_check,
            _consistency_check,
        )
        pv = _passive_voice_check(text)
        results["passive_voice"] = {"status": "ok", "score": pv["score"] * 100, "findings": pv.get("issues", [])}
        ow = _overused_words_check(text)
        results["overused_words"] = {"status": "ok", "score": ow["score"] * 100, "findings": ow.get("issues", [])}
        cl = _cliche_check(text)
        results["cliches"] = {"status": "ok", "score": cl["score"] * 100, "findings": cl.get("issues", [])}
        co = _consistency_check(text)
        results["consistency"] = {"status": "ok", "score": co["score"] * 100, "findings": co.get("issues", [])}
    except Exception as exc:
        logger.warning("Extended analysis checks failed: %s", exc)

    # Style guide + content type compliance
    try:
        from tools.writing.style_guide import check_style_guide
        sg = check_style_guide(text)
        results["style_guide"] = {"status": "ok", "score": sg["score"] * 100, "findings": sg.get("issues", [])}
    except Exception as exc:
        logger.warning("Style guide check failed: %s", exc)

    # Transitions / pacing analysis
    try:
        from tools.writing.rewriter import analyze_transitions
        tr = analyze_transitions(text)
        results["pacing"] = {"status": "ok", "score": tr.get("pacing_score", 70), "findings": tr.get("findings", [])}
    except Exception as exc:
        logger.warning("Transition analysis failed: %s", exc)

    # ─── Extended dimensions (WG 14, 15, 16, 17, 22, 24, 26, 27) ───
    # These run deterministically and add findings; modes 19, 25, 28 run on-demand.
    def _safe(name, fn, score_key="score", multiplier=1):
        try:
            r = fn(text)
            s = r.get(score_key, r.get("overall_coverage_score", r.get("evidence_score", 70)))
            if isinstance(s, (int, float)) and s <= 1.0 and multiplier == 100:
                s = s * 100
            elif isinstance(s, (int, float)) and multiplier == 100 and s <= 100:
                pass  # already 0-100
            findings = r.get("issues", []) or r.get("findings", [])
            results[name] = {"status": "ok", "score": s, "findings": findings[:10], "raw": r}
        except Exception as exc:
            logger.debug("%s check failed: %s", name, exc)

    try:
        from tools.writing.structure_enforcer import enforce_structure
        r = enforce_structure(text)
        if r.get("is_decision_doc") or r.get("template"):
            results["structure"] = {"status": "ok", "score": r.get("score", 100), "findings": r.get("issues", [])[:10]}
    except Exception as exc:
        logger.debug("structure check failed: %s", exc)

    try:
        from tools.writing.claim_validator import score_evidence_density
        r = score_evidence_density(text)
        results["evidence"] = {"status": "ok", "score": r.get("evidence_score", 70), "findings": r.get("issues", [])[:10]}
    except Exception as exc:
        logger.debug("evidence check failed: %s", exc)

    try:
        from tools.writing.numeric_validator import validate_numerics
        r = validate_numerics(text)
        results["numeric_integrity"] = {"status": "ok", "score": r.get("score", 100), "findings": [i.get("message", str(i)) for i in r.get("issues", [])][:10]}
    except Exception as exc:
        logger.debug("numeric check failed: %s", exc)

    try:
        from tools.writing.bias_detector import detect_bias
        r = detect_bias(text, context="general")
        results["bias"] = {"status": "ok", "score": r.get("score", 100), "findings": [i.get("word", "") + ": " + i.get("suggestion", "") for i in r.get("issues", [])][:10]}
    except Exception as exc:
        logger.debug("bias check failed: %s", exc)

    try:
        from tools.writing.standards_validator import validate_citations
        r = validate_citations(text)
        results["standards"] = {"status": "ok", "score": r.get("score", 100), "findings": r.get("issues", [])[:10]}
    except Exception as exc:
        logger.debug("standards check failed: %s", exc)

    try:
        from tools.writing.alternatives_enforcer import enforce_alternatives
        r = enforce_alternatives(text)
        if r.get("is_decision_doc"):
            results["alternatives"] = {"status": "ok", "score": r.get("trade_off_quality_score", 100), "findings": r.get("issues", [])[:10]}
    except Exception as exc:
        logger.debug("alternatives check failed: %s", exc)

    try:
        from tools.writing.reproducibility_validator import validate_reproducibility
        r = validate_reproducibility(text)
        results["reproducibility"] = {"status": "ok", "score": r.get("reproducibility_score", 100), "findings": [str(i.get("message", i)) for i in r.get("issues", [])][:10]}
    except Exception as exc:
        logger.debug("reproducibility check failed: %s", exc)

    try:
        from tools.writing.neutrality_scorer import score_neutrality
        r = score_neutrality(text)
        results["neutrality"] = {"status": "ok", "score": r.get("score", 100), "findings": r.get("issues", [])[:10]}
    except Exception as exc:
        logger.debug("neutrality check failed: %s", exc)

    # Calculate overall score.
    # Portion marking is only included when marks are present — a plain article
    # with no portion marks should not be penalized for missing them.
    # Fail-closed guard (nav-intel-01): a checker that raised at runtime on THIS
    # content must never be allowed to *inflate* the composite toward "passed".
    # ``status == "error"`` means the checker crashed on this specific text (a
    # real moderation failure); ``status == "unavailable"`` means the checker
    # module could not be imported (a deployment condition) and is skipped
    # without forcing a fail. Excluding the errored checker's default 70/100
    # score prevents the fail-open, and ``checker_errored`` forces a fail so a
    # broken moderation path can never approve content.
    checker_errored = any(
        isinstance(r, dict) and r.get("status") == "error"
        for r in results.values()
    )

    scores = []
    for key, result in results.items():
        if result.get("status") in ("unavailable", "error"):
            logger.warning("WriteGuard %s %s, skipping score", key, result.get("status"))
            continue
        if key == "portion_marking" and not result.get("has_marks", False):
            continue  # Skip score contribution when document has no marks
        score = result.get("score", result.get("overall_score", 70))
        if isinstance(score, (int, float)):
            scores.append(score)

    overall_score = sum(scores) / len(scores) if scores else 50.0

    # Pass threshold: overall >= 60 and no critical failures
    # plagiarism score is now inverted (100=clean, 0=full copy), so check > 15 (=clean)
    plagiarism_quality = results["plagiarism"].get("score", 100)
    passed = (
        overall_score >= 60
        and (plagiarism_quality > 15 if isinstance(plagiarism_quality, (int, float)) else True)
        and not checker_errored
    )

    composites = compute_composites(text, results)
    section_data = score_sections(text)

    return {
        "passed": passed,
        "overall_score": round(overall_score, 1),
        "checker_errored": checker_errored,
        "details": results,
        "recommendations": _generate_recommendations(results),
        # v6b: named composites + section-level breakdown
        "composites": composites,
        "section_scores": section_data,
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
        for f in findings
        if f.get("message")
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
        findings.append(
            {
                "category": "grammar",
                "message": issue.get("message", issue.get("description", "")),
                "suggestion": issue.get("suggestion", issue.get("replacement", "")),
            }
        )

    # Readability findings
    readability = details.get("readability", {})
    grade = readability.get("grade_level", 12)
    if isinstance(grade, (int, float)) and grade > 14:
        findings.append(
            {
                "category": "readability",
                "message": f"Reading grade level {grade:.0f} is too high for broad audience",
                "suggestion": "Simplify complex sentences, use shorter words, break up long paragraphs",
            }
        )
    avg_sentence = readability.get("avg_sentence_length", 0)
    if isinstance(avg_sentence, (int, float)) and avg_sentence > 25:
        findings.append(
            {
                "category": "readability",
                "message": f"Average sentence length ({avg_sentence:.0f} words) is too long",
                "suggestion": "Break long sentences into shorter ones (target 15-20 words)",
            }
        )

    # Tone findings
    tone = details.get("tone", {})
    for issue in tone.get("issues", tone.get("findings", [])):
        findings.append(
            {
                "category": "tone",
                "message": issue.get("message", issue.get("description", "")),
                "suggestion": issue.get("suggestion", ""),
            }
        )

    # AI detection — high raw ai_score (0-1) means text sounds robotic
    ai_det = details.get("ai_detection", {})
    ai_score = ai_det.get("ai_score", 0)  # raw 0-1 scale
    if isinstance(ai_score, (int, float)) and ai_score > 0.7:
        findings.append(
            {
                "category": "ai_detection",
                "message": "Content reads as AI-generated (high predictability score)",
                "suggestion": "Vary sentence structure, add concrete examples, use specific details instead of generalities",  # noqa: E501
            }
        )

    # Recommendations as fallback findings
    for rec in quality_results.get("recommendations", []):
        findings.append(
            {
                "category": "recommendation",
                "message": rec,
                "suggestion": "",
            }
        )

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
        recs.append("Content reads as AI-generated — add more personal anecdotes and varied sentence structure")

    plagiarism = results.get("plagiarism", {})
    # Use raw_similarity (0-100, where >60 = high plagiarism)
    raw_plag = plagiarism.get("raw_similarity", 0)
    if isinstance(raw_plag, (int, float)) and raw_plag > 60:
        recs.append("High similarity detected — rephrase flagged sections")

    return recs


def handle_writeguard_analyze(args: dict) -> dict:
    """MCP-tool adapter for `run_full_quality_check` -- exposes WriteGuard's
    full deterministic quality check to cross-repo callers (e.g. idea_lab)
    over the `writeguard_analyze` MCP tool. No `opportunity_id` is passed,
    so the plagiarism check's DB-backed similarity lookup safely no-ops
    (`run_full_quality_check` -> `check_plagiarism` degrades to a neutral
    score rather than raising)."""
    try:
        text = args.get("text") or ""
        if not text.strip():
            return {"error": "text is required"}
        return run_full_quality_check(text)
    except Exception as exc:
        logger.warning("handle_writeguard_analyze: %s", exc)
        return {"error": str(exc)}
