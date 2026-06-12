"""WriteGuard Style Profile Extractor (WG 10a).

Deterministic extraction of 28 numeric style features from sample text into a
StyleProfile dict. No LLM, no NLTK/spaCy — regex + statistics only.

Usage:
    python tools/writing/style_profiler.py --file sample.txt --name "My Style"
    python tools/writing/style_profiler.py --text "..." --json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Reuse syllable counter from existing analysis engine
try:
    from tools.writing.analysis_engine import _count_syllables
except ImportError:
    # Allow direct script execution
    _HERE = Path(__file__).resolve().parent
    _ROOT = _HERE.parent.parent
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from tools.writing.analysis_engine import _count_syllables  # type: ignore


PROFILE_VERSION = 1

# ---------- Lexicons ----------

HEDGES = {
    "maybe", "perhaps", "somewhat", "arguably", "likely",
    "appears", "seems", "rather", "quite",
}
SUPERLATIVES = {
    "best", "worst", "most", "least",
    "extremely", "absolutely", "incredibly",
}
FORMAL_MARKERS = {
    "utilize", "therefore", "furthermore", "consequently",
    "moreover", "notwithstanding",
}
INFORMAL_MARKERS = {
    "got", "pretty", "stuff", "thing", "lots", "kinda", "sorta",
}
TRANSITIONS = {
    "however", "moreover", "nevertheless", "consequently",
    "meanwhile", "additionally", "similarly", "conversely",
    "accordingly", "therefore", "thus", "hence",
    "specifically", "namely", "instead",
}
FIRST_PERSON = {"i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves"}
THIRD_PERSON = {
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "it", "its", "itself",
    "they", "them", "their", "theirs", "themselves",
}

PASSIVE_RE = re.compile(r"\b(is|are|was|were|been|being|be)\s+\w+(ed|en)\b", re.IGNORECASE)
CONTRACTION_RE = re.compile(r"\b\w+(n't|'re|'ve|'ll|'d|'s|'m)\b", re.IGNORECASE)
CONTRACTION_SITE_RE = re.compile(
    r"\b(do not|does not|did not|is not|are not|was not|were not|have not|has not|had not|"
    r"will not|would not|should not|could not|cannot|can not|"
    r"i am|you are|we are|they are|he is|she is|it is|that is|there is|"
    r"i have|you have|we have|they have|i will|you will|we will|they will|"
    r"i would|you would|we would|they would|i had|you had|we had|they had)\b",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
SENT_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")


# ---------- Helpers ----------

def _words(text: str) -> List[str]:
    return WORD_RE.findall(text)


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _percentile(sorted_vals: List[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def _flesch_kincaid_grade(text: str) -> float:
    sents = _sentences(text)
    words = _words(text)
    if not sents or not words:
        return 0.0
    syllables = sum(_count_syllables(w) for w in words)
    # F-K Grade = 0.39 * (words/sents) + 11.8 * (syl/words) - 15.59
    grade = 0.39 * (len(words) / len(sents)) + 11.8 * (syllables / len(words)) - 15.59
    return round(grade, 2)


def _type_token_ratio(words: List[str], window: int = 100) -> float:
    """Rolling TTR over 100-word windows, averaged."""
    if not words:
        return 0.0
    lowered = [w.lower() for w in words]
    if len(lowered) < window:
        return round(len(set(lowered)) / len(lowered), 4)
    ratios: List[float] = []
    for i in range(0, len(lowered) - window + 1, max(1, window // 2)):
        seg = lowered[i:i + window]
        ratios.append(len(set(seg)) / window)
    return round(statistics.mean(ratios), 4) if ratios else 0.0


def _sentence_word_counts(text: str) -> List[int]:
    return [len(_words(s)) for s in _sentences(text) if _words(s)]


def _para_sentence_counts(text: str) -> List[int]:
    counts = []
    for para in _paragraphs(text):
        n = len(_sentences(para))
        if n > 0:
            counts.append(n)
    return counts


def _heading_info(text: str) -> Dict[str, int]:
    """Count headings and max depth. Supports markdown # and setext === / ---."""
    lines = text.splitlines()
    count = 0
    max_depth = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # ATX markdown: # ... ######
        m = re.match(r"^(#{1,6})\s+\S", stripped)
        if m:
            count += 1
            depth = len(m.group(1))
            max_depth = max(max_depth, depth)
            continue
        # Setext: next line === or ---
        if idx + 1 < len(lines):
            nxt = lines[idx + 1].strip()
            if nxt and re.match(r"^=+$", nxt) and len(stripped) > 0:
                count += 1
                max_depth = max(max_depth, 1)
                continue
            if nxt and re.match(r"^-+$", nxt) and len(stripped) > 0:
                count += 1
                max_depth = max(max_depth, 2)
                continue
        # UPPERCASE line heuristic (at least 2 words, all caps letters)
        words_in = _words(stripped)
        if (
            len(words_in) >= 2
            and all(w.isupper() for w in words_in)
            and len(stripped) <= 80
        ):
            count += 1
            max_depth = max(max_depth, 1)
    return {"count": count, "max_depth": max_depth}


def _list_item_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if re.match(r"^[-*]\s+\S", stripped):
            count += 1
        elif re.match(r"^\d+\.\s+\S", stripped):
            count += 1
    return count


def _passive_ratio(sentences: List[str]) -> float:
    if not sentences:
        return 0.0
    hits = sum(1 for s in sentences if PASSIVE_RE.search(s))
    return round(hits / len(sentences), 4)


def _pronoun_ratios(words: List[str]) -> Dict[str, float]:
    if not words:
        return {"first": 0.0, "third": 0.0}
    lowered = [w.lower() for w in words]
    first = sum(1 for w in lowered if w in FIRST_PERSON)
    third = sum(1 for w in lowered if w in THIRD_PERSON)
    return {
        "first": round(first / len(words), 4),
        "third": round(third / len(words), 4),
    }


def _lexicon_density(words: List[str], lexicon: set) -> int:
    lowered = [w.lower() for w in words]
    return sum(1 for w in lowered if w in lexicon)


def _formal_score(words: List[str]) -> float:
    """Formal/(formal+informal) ratio; 0.5 if none, else 0..1."""
    if not words:
        return 0.5
    formal = _lexicon_density(words, FORMAL_MARKERS)
    informal = _lexicon_density(words, INFORMAL_MARKERS)
    if formal + informal == 0:
        return 0.5
    return round(formal / (formal + informal), 4)


def _contraction_ratio(text: str) -> float:
    contractions = len(CONTRACTION_RE.findall(text))
    expansion_sites = len(CONTRACTION_SITE_RE.findall(text))
    total = contractions + expansion_sites
    if total == 0:
        return 0.0
    return round(contractions / total, 4)


def _adverb_count(words: List[str]) -> int:
    """Approximate: words ending in -ly, length > 3, excluding common non-adverbs."""
    stop = {"only", "ally", "early", "ugly", "holy", "silly", "jelly", "belly", "italy", "reply", "apply", "family", "july"}
    count = 0
    for w in words:
        wl = w.lower()
        if len(wl) > 3 and wl.endswith("ly") and wl not in stop:
            count += 1
    return count


def _punct_counts(text: str) -> Dict[str, int]:
    return {
        "semicolon": text.count(";"),
        "em_dash": text.count("\u2014") + len(re.findall(r"--", text)),
        "parenthetical": text.count("("),
        "exclamation": text.count("!"),
        "question": text.count("?"),
    }


def _per_1k(count: int, total_words: int) -> float:
    if total_words == 0:
        return 0.0
    return round(count * 1000.0 / total_words, 3)


# ---------- Public API ----------

def extract_profile(text: str, name: str = "") -> Dict[str, Any]:
    """Extract a 28-feature StyleProfile from sample text."""
    text = text or ""
    words = _words(text)
    sentences = _sentences(text)
    sent_wc = _sentence_word_counts(text)
    para_sc = _para_sentence_counts(text)
    total_words = len(words)

    # Sentence features
    if sent_wc:
        sorted_sw = sorted(sent_wc)
        sent_len_mean = round(statistics.mean(sent_wc), 3)
        sent_len_median = round(statistics.median(sent_wc), 3)
        sent_len_std = round(statistics.pstdev(sent_wc), 3) if len(sent_wc) > 1 else 0.0
        sent_len_p10 = round(_percentile(sorted_sw, 10), 3)
        sent_len_p90 = round(_percentile(sorted_sw, 90), 3)
    else:
        sent_len_mean = sent_len_median = sent_len_std = sent_len_p10 = sent_len_p90 = 0.0

    # Vocabulary
    fk_grade = _flesch_kincaid_grade(text)
    ttr = _type_token_ratio(words, 100)
    avg_word_len = round(
        statistics.mean(len(w) for w in words) if words else 0.0, 3
    )

    # Paragraph
    if para_sc:
        para_len_mean = round(statistics.mean(para_sc), 3)
        para_len_std = round(statistics.pstdev(para_sc), 3) if len(para_sc) > 1 else 0.0
    else:
        para_len_mean = para_len_std = 0.0

    # Structure
    hinfo = _heading_info(text)
    heading_density = _per_1k(hinfo["count"], total_words)
    max_heading_depth = hinfo["max_depth"]
    list_density = _per_1k(_list_item_count(text), total_words)

    # Voice
    passive_ratio = _passive_ratio(sentences)
    pronouns = _pronoun_ratios(words)

    # Tone
    hedge_density = _per_1k(_lexicon_density(words, HEDGES), total_words)
    superlative_density = _per_1k(_lexicon_density(words, SUPERLATIVES), total_words)
    formal_score = _formal_score(words)
    contraction_ratio = _contraction_ratio(text)

    # Connective
    transition_density = _per_1k(_lexicon_density(words, TRANSITIONS), total_words)
    adverb_density = _per_1k(_adverb_count(words), total_words)

    # Punctuation
    punct = _punct_counts(text)
    semicolon_per_1k = _per_1k(punct["semicolon"], total_words)
    em_dash_per_1k = _per_1k(punct["em_dash"], total_words)
    parenthetical_per_1k = _per_1k(punct["parenthetical"], total_words)
    exclamation_per_1k = _per_1k(punct["exclamation"], total_words)
    question_per_1k = _per_1k(punct["question"], total_words)

    features: Dict[str, float] = {
        # Sentence (5)
        "sent_len_mean": sent_len_mean,
        "sent_len_median": sent_len_median,
        "sent_len_std": sent_len_std,
        "sent_len_p10": sent_len_p10,
        "sent_len_p90": sent_len_p90,
        # Vocabulary (3)
        "flesch_kincaid_grade": fk_grade,
        "type_token_ratio": ttr,
        "avg_word_len": avg_word_len,
        # Paragraph (2)
        "para_len_mean": para_len_mean,
        "para_len_std": para_len_std,
        # Structure (3)
        "heading_density": heading_density,
        "max_heading_depth": float(max_heading_depth),
        "list_density": list_density,
        # Voice (3)
        "passive_ratio": passive_ratio,
        "first_person_ratio": pronouns["first"],
        "third_person_ratio": pronouns["third"],
        # Tone (4)
        "hedge_density": hedge_density,
        "superlative_density": superlative_density,
        "formal_score": formal_score,
        "contraction_ratio": contraction_ratio,
        # Connective (2)
        "transition_density": transition_density,
        "adverb_density": adverb_density,
        # Punctuation (5)
        "semicolon_per_1k": semicolon_per_1k,
        "em_dash_per_1k": em_dash_per_1k,
        "parenthetical_per_1k": parenthetical_per_1k,
        "exclamation_per_1k": exclamation_per_1k,
        "question_per_1k": question_per_1k,
    }

    if total_words >= 1500:
        confidence = "high"
    elif total_words >= 800:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "name": name,
        "source_word_count": total_words,
        "confidence": confidence,
        "features": features,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": PROFILE_VERSION,
    }


def compare_profiles(profile_a: Dict[str, Any], profile_b: Dict[str, Any]) -> Dict[str, Any]:
    """Compute per-feature deltas and an overall similarity score (0-100)."""
    fa = profile_a.get("features", {})
    fb = profile_b.get("features", {})
    keys = sorted(set(fa.keys()) | set(fb.keys()))
    deltas: Dict[str, float] = {}
    # Rough per-feature scales for normalization
    scales = {
        "sent_len_mean": 20.0, "sent_len_median": 20.0, "sent_len_std": 10.0,
        "sent_len_p10": 15.0, "sent_len_p90": 40.0,
        "flesch_kincaid_grade": 12.0, "type_token_ratio": 1.0, "avg_word_len": 6.0,
        "para_len_mean": 10.0, "para_len_std": 5.0,
        "heading_density": 20.0, "max_heading_depth": 6.0, "list_density": 50.0,
        "passive_ratio": 1.0, "first_person_ratio": 0.3, "third_person_ratio": 0.3,
        "hedge_density": 20.0, "superlative_density": 20.0,
        "formal_score": 1.0, "contraction_ratio": 1.0,
        "transition_density": 20.0, "adverb_density": 50.0,
        "semicolon_per_1k": 10.0, "em_dash_per_1k": 10.0,
        "parenthetical_per_1k": 20.0, "exclamation_per_1k": 10.0,
        "question_per_1k": 10.0,
    }
    normalized: List[float] = []
    for k in keys:
        va = float(fa.get(k, 0.0))
        vb = float(fb.get(k, 0.0))
        diff = abs(va - vb)
        deltas[k] = round(diff, 4)
        scale = scales.get(k, max(1.0, abs(va) + abs(vb) + 1e-9))
        normalized.append(min(1.0, diff / scale) if scale > 0 else 0.0)
    if normalized:
        avg_norm_diff = sum(normalized) / len(normalized)
        similarity_score = round(max(0.0, min(100.0, (1.0 - avg_norm_diff) * 100.0)), 2)
    else:
        similarity_score = 0.0
    return {"deltas": deltas, "similarity_score": similarity_score}


# ---------- CLI ----------

def _read_text(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if args.text:
        return args.text
    return sys.stdin.read()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WriteGuard Style Profile Extractor")
    parser.add_argument("--file", help="Path to sample text file (UTF-8)")
    parser.add_argument("--text", help="Inline text sample")
    parser.add_argument("--name", default="", help="Profile name label")
    parser.add_argument("--json", action="store_true", help="Emit JSON (default)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    text = _read_text(args)
    profile = extract_profile(text, name=args.name)
    indent = 2 if args.pretty or not args.json else None
    print(json.dumps(profile, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
