# CUI // SP-CTI
"""WriteGuard analysis engine — unified quality analysis for written content.

Provides the ``analyze()`` entry point consumed by:
    tools/proposal_genesis/reflexes/polish.py  (Proposal Genesis R8 Polish)
    tools/writing/batch_analyzer.py            (batch scanning)

Scanner-tier only when ``skip_llm=True`` (zero Claude tokens, D-WG-2).
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_syllables(word: str) -> int:
    word = word.lower().strip(".,!?;:'\"")
    if not word:
        return 0
    count = 0
    prev_vowel = False
    for ch in word:
        is_v = ch in "aeiouy"
        if is_v and not prev_vowel:
            count += 1
        prev_vowel = is_v
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _count_complex_words(words: list) -> int:
    """Count polysyllabic words (3 or more syllables) for Fog/SMOG."""
    return sum(1 for w in words if _count_syllables(w) >= 3)


def _grammar_check(text: str) -> Dict[str, Any]:
    """Deterministic grammar analysis."""
    issues: List[str] = []

    doubles = len(re.findall(r"  +", text))
    if doubles:
        issues.append(f"{doubles} double-space occurrences")

    uncapped = len(re.findall(r"\.\s+[a-z]", text))
    if uncapped:
        issues.append(f"{uncapped} sentences not capitalised")

    repeated = len(re.findall(r"\b(\w+)\s+\1\b", text, re.IGNORECASE))
    if repeated:
        issues.append(f"{repeated} repeated word pairs")

    error_count = doubles + uncapped + repeated
    score = max(0.0, 1.0 - error_count * 0.05)
    return {"score": round(score, 2), "error_count": error_count, "issues": issues}


def _readability_check(text: str) -> Dict[str, Any]:
    """Multi-metric readability: Flesch-Kincaid, Gunning Fog, SMOG, Coleman-Liau + composite."""
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    words = text.split()
    if not sentences or not words:
        return {
            "score": 0.5,
            "grade_level": 0,
            "avg_sentence_length": 0,
            "flesch_kincaid": 0.0,
            "gunning_fog": 0.0,
            "smog_index": 0.0,
            "coleman_liau": 0.0,
            "composite_grade": 0.0,
        }

    n_sentences = len(sentences)
    n_words = len(words)
    syl_per_word = [_count_syllables(w) for w in words]
    total_syllables = sum(syl_per_word)

    avg_sent = n_words / n_sentences
    avg_syl = total_syllables / n_words

    # Flesch-Kincaid Grade Level
    fk_grade = 0.39 * avg_sent + 11.8 * avg_syl - 15.59
    fk_grade = max(0.0, min(20.0, fk_grade))

    # Gunning Fog Index: 0.4 × (avg_words/sentence + 100 × complex_ratio)
    # Complex = 3+ syllables
    complex_count = sum(1 for s in syl_per_word if s >= 3)
    fog = 0.4 * (avg_sent + 100.0 * complex_count / n_words)
    fog = max(0.0, min(20.0, fog))

    # SMOG Index: 3 + √(polysyllables × 30 / n_sentences)
    smog = 3.0 + math.sqrt(complex_count * 30.0 / n_sentences)
    smog = max(0.0, min(20.0, smog))

    # Coleman-Liau Index: 0.0588·L − 0.296·S − 15.8
    # L = avg letters per 100 words; S = avg sentences per 100 words
    letter_count = sum(len(re.sub(r"[^a-zA-Z]", "", w)) for w in words)
    L = (letter_count / n_words) * 100.0
    S = (n_sentences / n_words) * 100.0
    cli = 0.0588 * L - 0.296 * S - 15.8
    cli = max(0.0, min(20.0, cli))

    # Composite: equal-weight average of all four indices
    composite_grade = (fk_grade + fog + smog + cli) / 4.0

    # Quality score from composite (proposal ideal: grade 10-14)
    if 10 <= composite_grade <= 14:
        score = 1.0
    elif 8 <= composite_grade < 10 or 14 < composite_grade <= 16:
        score = 0.8
    else:
        score = max(0.3, 1.0 - abs(composite_grade - 12) * 0.05)

    return {
        "score": round(score, 2),
        "grade_level": round(composite_grade, 1),
        "avg_sentence_length": round(avg_sent, 1),
        "flesch_kincaid": round(fk_grade, 1),
        "gunning_fog": round(fog, 1),
        "smog_index": round(smog, 1),
        "coleman_liau": round(cli, 1),
        "composite_grade": round(composite_grade, 1),
    }


def _tone_check(text: str) -> Dict[str, Any]:
    """Professional tone analysis via keyword matching."""
    issues: List[str] = []
    text_lower = text.lower()

    informal = [
        "gonna",
        "wanna",
        "gotta",
        "kinda",
        "sorta",
        "ain't",
        "stuff",
        "things",
        "basically",
        "obviously",
        "super",
        "pretty much",
        "a lot of",
        "tons of",
    ]
    found = [w for w in informal if w in text_lower]
    if found:
        issues.append(f"informal language: {', '.join(found[:5])}")

    weak = [
        "we think",
        "we believe",
        "we hope",
        "we feel",
        "maybe",
        "perhaps",
        "possibly",
        "try to",
    ]
    found_weak = [w for w in weak if w in text_lower]
    if found_weak:
        issues.append(f"weak language: {', '.join(found_weak[:5])}")

    score = max(0.0, 1.0 - (len(found) + len(found_weak)) * 0.10)
    return {"score": round(score, 2), "issues": issues}


def _ai_detection(text: str) -> Dict[str, Any]:
    """Deterministic AI-content detection — multi-signal (D-WG-6).

    Five signals combined into a weighted score (0 = human, 1 = AI-like):

    burstiness       : sentence-length CoV — AI produces uniform lengths
    ttr              : type-token ratio (unique_words / total_words) — AI reuses vocab
    hapax_ratio      : hapax legomena / unique_words — AI repeats words more
    trigram_entropy  : normalised character-trigram Shannon entropy — AI produces
                       repetitive character patterns → lower normalised entropy
    start_diversity  : unique sentence-start words / sentence count — AI repeats
                       opening words (e.g. "The", "This", "In")

    Calibration data: context/writeguard/calibration/ai_detection_calibration.yaml
    """
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) < 3:
        return {
            "score": 0.8,
            "burstiness": 0.0,
            "ttr": 0.0,
            "hapax_ratio": 0.0,
            "trigram_entropy": 0.0,
            "start_diversity": 0.0,
            "issues": [],
        }

    # ── Signal 1: Burstiness (sentence-length coefficient of variation) ──
    lengths = [len(s.split()) for s in sentences]
    mean_len = sum(lengths) / len(lengths)
    variance = sum((ln - mean_len) ** 2 for ln in lengths) / len(lengths)
    burstiness = (variance ** 0.5) / mean_len if mean_len > 0 else 0.0

    # ── Signal 2: Type-token ratio + hapax legomena ratio ───────────────
    words_raw = re.findall(r"[a-z0-9]+", text.lower())
    if words_raw:
        freq: Dict[str, int] = {}
        for w in words_raw:
            freq[w] = freq.get(w, 0) + 1
        ttr = len(freq) / len(words_raw)
        hapax_ratio = (
            sum(1 for v in freq.values() if v == 1) / len(freq) if freq else 0.0
        )
    else:
        ttr = 0.0
        hapax_ratio = 0.0

    # ── Signal 3: Character trigram entropy ─────────────────────────────
    clean = re.sub(r"\s+", " ", text.lower())
    tg_freq: Dict[str, int] = {}
    for i in range(len(clean) - 2):
        tg = clean[i : i + 3]
        tg_freq[tg] = tg_freq.get(tg, 0) + 1
    if len(tg_freq) > 1:
        total_tg = sum(tg_freq.values())
        raw_entropy = -sum(
            (v / total_tg) * math.log2(v / total_tg) for v in tg_freq.values()
        )
        max_entropy = math.log2(len(tg_freq))
        trigram_entropy = raw_entropy / max_entropy
    else:
        trigram_entropy = 0.0

    # ── Signal 4: Sentence-start diversity ──────────────────────────────
    starts = []
    for s in sentences:
        ws = s.split()
        starts.append(ws[0].lower().rstrip(".,!?;:") if ws else "")
    unique_starts = len(set(w for w in starts if w))
    start_diversity = unique_starts / len(sentences) if sentences else 0.0

    # ── Sub-scores (0.0 = human-like, 1.0 = AI-like) ────────────────────
    # Burstiness: low variance → uniform → AI
    if burstiness >= 0.50:
        burst_sub = 0.20
    elif burstiness >= 0.30:
        burst_sub = 0.50
    else:
        burst_sub = 0.85

    # TTR: low lexical diversity → AI
    if ttr >= 0.65:
        ttr_sub = 0.20
    elif ttr >= 0.45:
        ttr_sub = 0.50
    else:
        ttr_sub = 0.80

    # Hapax ratio: fewer once-only words → AI repeating vocabulary
    if hapax_ratio >= 0.50:
        hapax_sub = 0.20
    elif hapax_ratio >= 0.30:
        hapax_sub = 0.50
    else:
        hapax_sub = 0.80

    # Trigram entropy: low normalised entropy → repetitive char patterns → AI
    # Typical prose range: 0.78-0.92 (normalised)
    if trigram_entropy >= 0.88:
        entropy_sub = 0.20  # high diversity → human
    elif trigram_entropy >= 0.78:
        entropy_sub = 0.50  # mid range → uncertain
    else:
        entropy_sub = 0.80  # low entropy → AI-like repetition

    # Start-word diversity: low variety → AI repeating sentence openers
    if start_diversity >= 0.65:
        start_sub = 0.20
    elif start_diversity >= 0.45:
        start_sub = 0.50
    else:
        start_sub = 0.80

    # ── Weighted combination ─────────────────────────────────────────────
    score = (
        burst_sub * 0.30
        + ttr_sub * 0.20
        + hapax_sub * 0.15
        + entropy_sub * 0.20
        + start_sub * 0.15
    )

    issues: List[str] = []
    if score >= 0.65:
        issues.append("likely AI-generated content")
    elif score >= 0.50:
        issues.append("possible AI-generated content")

    return {
        "score": round(score, 3),
        "burstiness": round(burstiness, 3),
        "ttr": round(ttr, 3),
        "hapax_ratio": round(hapax_ratio, 3),
        "trigram_entropy": round(trigram_entropy, 3),
        "start_diversity": round(start_diversity, 3),
        "issues": issues,
    }


def _plagiarism_check(text: str, opportunity_id: str = "") -> Dict[str, Any]:
    """Content similarity check (n-gram overlap against existing drafts).

    Falls back gracefully if DB tables are missing.
    """
    if not opportunity_id:
        return {"score": 1.0, "max_similarity": 0.0}

    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT section_text FROM proposal_section_drafts "
                "WHERE opportunity_id != ? AND status IN ('draft', 'approved') "
                "ORDER BY created_at DESC LIMIT 20",
                (opportunity_id,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {"score": 1.0, "max_similarity": 0.0}

    if not rows:
        return {"score": 1.0, "max_similarity": 0.0}

    text_ngrams = _ngrams(text, 4)
    max_sim = 0.0
    for row in rows:
        other = row["section_text"] if hasattr(row, "keys") else row[0]
        other_ng = _ngrams(other or "", 4)
        if text_ngrams and other_ng:
            overlap = len(text_ngrams & other_ng)
            total = len(text_ngrams | other_ng)
            sim = overlap / total if total else 0
            max_sim = max(max_sim, sim)

    return {"score": round(max(0, 1.0 - max_sim), 2), "max_similarity": round(max_sim, 3)}


def _ngrams(text: str, n: int) -> set:
    text = re.sub(r"\s+", " ", text.lower().strip())
    if len(text) < n:
        return set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _sentence_structure(text: str) -> Dict[str, Any]:
    """Sentence structure variety analysis (D-WG-3b).

    Returns:
        sentence_lengths  : list[int] — word count per sentence (histogram source)
        length_distribution: dict with keys short/medium/long (count + ratio)
        start_word_runs   : list[dict] — consecutive same-start-word runs > 3
        start_diversity   : float — unique start words / total sentences (0-1)
        variety_score     : int — 0-100 composite variety score
        findings          : list[str] — human-readable flags
    """
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if not sentences:
        return {
            "sentence_lengths": [],
            "length_distribution": {"short": 0, "medium": 0, "long": 0},
            "short_ratio": 0.0,
            "medium_ratio": 0.0,
            "long_ratio": 0.0,
            "start_word_runs": [],
            "start_diversity": 0.0,
            "variety_score": 0,
            "findings": [],
        }

    # ── Sentence length distribution ──────────────────────────────
    lengths = [len(s.split()) for s in sentences]
    short = sum(1 for l in lengths if l <= 10)
    medium = sum(1 for l in lengths if 11 <= l <= 20)
    long_ = sum(1 for l in lengths if l > 20)
    total = len(sentences)

    short_ratio = round(short / total, 3)
    medium_ratio = round(medium / total, 3)
    long_ratio = round(long_ / total, 3)

    # ── Start-word diversity ───────────────────────────────────────
    start_words = []
    for s in sentences:
        words = s.split()
        start_words.append(words[0].lower().rstrip(".,!?;:") if words else "")

    unique_starts = len(set(w for w in start_words if w))
    start_diversity = round(unique_starts / total, 3) if total else 0.0

    # Detect runs of >3 consecutive same start word
    runs: List[Dict[str, Any]] = []
    i = 0
    while i < len(start_words):
        j = i + 1
        while j < len(start_words) and start_words[j] == start_words[i]:
            j += 1
        run_len = j - i
        if run_len > 3:
            runs.append({"word": start_words[i], "count": run_len, "start_index": i})
        i = j

    # ── Variety score (0-100) ──────────────────────────────────────
    # Component 1: length variety — penalise extreme ratios
    # Ideal: some mix of short/medium/long. Score 100 when no bucket > 70%.
    max_ratio = max(short_ratio, medium_ratio, long_ratio)
    if max_ratio <= 0.50:
        length_score = 100
    elif max_ratio <= 0.70:
        length_score = 75
    elif max_ratio <= 0.85:
        length_score = 50
    else:
        length_score = 25

    # Component 2: start-word diversity (0-1 → 0-100)
    diversity_score = round(start_diversity * 100)

    # Component 3: penalty for long same-start runs
    run_penalty = min(50, sum(r["count"] - 3 for r in runs) * 10)

    variety_score = max(0, round((length_score * 0.5 + diversity_score * 0.5) - run_penalty))

    # ── Findings ──────────────────────────────────────────────────
    findings: List[str] = []
    if max_ratio > 0.70:
        dominant = "short" if short_ratio == max_ratio else ("medium" if medium_ratio == max_ratio else "long")
        findings.append(f"{round(max_ratio * 100)}% of sentences are {dominant} — vary sentence length")
    if start_diversity < 0.40:
        findings.append(f"low start-word diversity ({round(start_diversity * 100)}%) — avoid repeating opening words")
    for r in runs:
        findings.append(f"{r['count']} consecutive sentences start with '{r['word']}'")

    return {
        "sentence_lengths": lengths,
        "length_distribution": {"short": short, "medium": medium, "long": long_},
        "short_ratio": short_ratio,
        "medium_ratio": medium_ratio,
        "long_ratio": long_ratio,
        "start_word_runs": runs,
        "start_diversity": start_diversity,
        "variety_score": variety_score,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze(
    text: str,
    mode: str = "inline",
    opportunity_id: str = "",
    skip_llm: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run full WriteGuard analysis on *text*.

    Parameters
    ----------
    text : str
        The content to analyse (plain text or markdown).
    mode : str
        ``"inline"`` for single-text analysis, ``"batch"`` for bulk.
    opportunity_id : str
        Optional proposal opportunity ID for plagiarism cross-check.
    skip_llm : bool
        When ``True`` (default), use only deterministic checks —
        scanner-tier, zero Claude tokens.

    Returns
    -------
    dict
        Keys:
        - ``quality_score`` (float, 0-1 composite)
        - ``findings`` (list[dict])
        - ``readability`` (dict)
        - ``grammar_error_count`` (int)
        - ``ai_content_score`` (float or None)
        - ``checks`` (dict of individual results)
    """
    if not text or not text.strip():
        return {
            "quality_score": 0,
            "findings": [{"error": "empty text"}],
            "readability": {},
            "grammar_error_count": 0,
            "ai_content_score": None,
            "checks": {},
        }

    grammar = _grammar_check(text)
    readability = _readability_check(text)
    tone = _tone_check(text)
    plagiarism = _plagiarism_check(text, opportunity_id)
    ai_det = _ai_detection(text)
    sentence_structure = _sentence_structure(text)

    checks = {
        "grammar": grammar,
        "readability": readability,
        "tone": tone,
        "plagiarism": plagiarism,
        "ai_detection": ai_det,
        "sentence_structure": sentence_structure,
    }

    # Weighted composite (same weights as polish.py)
    weights = {
        "grammar": 0.20,
        "readability": 0.25,
        "tone": 0.25,
        "plagiarism": 0.15,
        "ai_detection": 0.15,
    }
    quality_score = round(sum(checks[k]["score"] * w for k, w in weights.items()), 3)

    # Collect findings
    findings: List[Dict[str, Any]] = []
    for name, result in checks.items():
        for issue in result.get("issues", []):
            findings.append({"check": name, "issue": issue})

    return {
        "quality_score": quality_score,
        "findings": findings,
        "readability": readability,
        "grammar_error_count": grammar.get("error_count", 0),
        "ai_content_score": ai_det.get("score"),
        "sentence_structure": sentence_structure,
        "checks": checks,
    }
