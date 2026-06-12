# CUI // SP-CTI
"""WriteGuard Style Matcher (WG 10c) — score text against a style profile.

Computes distance between a text's style features and a saved StyleProfile,
generates deterministic rewrite instructions to close the gap.

Usage:
    from tools.writing.style_matcher import score_against_profile, generate_style_instructions

    result = score_against_profile(text, profile)
    # {style_match_score: 78.5, per_feature_deltas: {...}, closest_features: [...],
    #  furthest_features: [...]}

    instructions = generate_style_instructions(text, profile)
    # ["Shorten sentences from avg 22 to ~16 words.", ...]

CUI // SP-CTI
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.writing.style_profiler import extract_profile

# Feature weights — most perceptible features weighted highest
FEATURE_WEIGHTS: Dict[str, float] = {
    "sent_len_mean": 3.0,
    "passive_ratio": 2.5,
    "flesch_kincaid_grade": 2.5,
    "first_person_ratio": 2.0,
    "contraction_ratio": 2.0,
    "formal_score": 2.0,
    "hedge_density": 1.5,
    "para_len_mean": 1.0,
    "sent_len_std": 1.0,
    "type_token_ratio": 1.0,
    "superlative_density": 1.0,
    "transition_density": 1.0,
    "adverb_density": 1.0,
    "third_person_ratio": 1.0,
    "heading_density": 1.0,
    "list_density": 1.0,
    "avg_word_len": 1.0,
    "semicolon_per_1k": 0.5,
    "em_dash_per_1k": 0.5,
    "parenthetical_per_1k": 0.5,
    "exclamation_per_1k": 0.5,
    "question_per_1k": 0.5,
}

# Population norms for scaling feature differences
# (typical ranges from diverse writing samples — used to normalize absolute deltas)
FEATURE_NORMS: Dict[str, float] = {
    "sent_len_mean": 10.0,
    "sent_len_median": 10.0,
    "sent_len_std": 5.0,
    "sent_len_p10": 8.0,
    "sent_len_p90": 15.0,
    "flesch_kincaid_grade": 5.0,
    "type_token_ratio": 0.3,
    "avg_word_len": 2.0,
    "para_len_mean": 3.0,
    "para_len_std": 2.0,
    "heading_density": 10.0,
    "max_heading_depth": 3.0,
    "list_density": 20.0,
    "passive_ratio": 0.3,
    "first_person_ratio": 0.3,
    "third_person_ratio": 0.3,
    "hedge_density": 10.0,
    "superlative_density": 10.0,
    "formal_score": 1.0,
    "contraction_ratio": 0.3,
    "transition_density": 15.0,
    "adverb_density": 30.0,
    "semicolon_per_1k": 5.0,
    "em_dash_per_1k": 5.0,
    "parenthetical_per_1k": 10.0,
    "exclamation_per_1k": 5.0,
    "question_per_1k": 5.0,
}

# Noticeable-difference thresholds — skip instructions if delta is below this
NOTICEABLE_THRESHOLDS: Dict[str, float] = {
    "sent_len_mean": 3.0,          # +/- 3 words
    "passive_ratio": 0.10,          # +/- 10 percentage points
    "flesch_kincaid_grade": 1.5,    # +/- 1.5 grade levels
    "contraction_ratio": 0.15,
    "first_person_ratio": 0.15,
    "third_person_ratio": 0.15,
    "formal_score": 0.3,
    "hedge_density": 3.0,
    "superlative_density": 2.0,
    "adverb_density": 10.0,
    "transition_density": 5.0,
    "heading_density": 3.0,
    "list_density": 5.0,
    "para_len_mean": 1.0,
    "type_token_ratio": 0.10,
    "avg_word_len": 1.0,
}


def score_against_profile(text: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Score `text` against a saved style `profile`.

    Returns
    -------
    dict
        style_match_score (0-100, higher = closer match)
        per_feature_deltas (feature -> abs delta)
        closest_features (top 5 features with smallest normalized delta)
        furthest_features (top 5 features with largest normalized delta)
        candidate_profile (full extracted profile of the candidate text)
    """
    if not profile or not profile.get("features"):
        return {
            "style_match_score": 0.0,
            "per_feature_deltas": {},
            "closest_features": [],
            "furthest_features": [],
            "error": "Invalid profile (missing features)",
        }

    candidate = extract_profile(text, name="candidate")
    cand_features = candidate.get("features", {})
    ref_features = profile.get("features", {})

    total_weight = 0.0
    weighted_sum = 0.0
    deltas: Dict[str, Dict[str, float]] = {}

    for feature, ref_val in ref_features.items():
        cand_val = cand_features.get(feature, 0.0)
        if not isinstance(ref_val, (int, float)) or not isinstance(cand_val, (int, float)):
            continue
        abs_delta = abs(ref_val - cand_val)
        norm = FEATURE_NORMS.get(feature, 1.0) or 1.0
        normalized = abs_delta / norm
        weight = FEATURE_WEIGHTS.get(feature, 1.0)
        weighted_sum += min(normalized, 1.0) * weight
        total_weight += weight
        deltas[feature] = {
            "reference": round(ref_val, 3),
            "candidate": round(cand_val, 3),
            "abs_delta": round(abs_delta, 3),
            "normalized": round(normalized, 3),
            "weight": weight,
        }

    score_0_1 = 1.0 - (weighted_sum / total_weight) if total_weight > 0 else 0.0
    style_match_score = round(max(0.0, min(1.0, score_0_1)) * 100, 1)

    # Rank features
    sorted_features = sorted(deltas.items(), key=lambda x: x[1]["normalized"])
    closest = [{"feature": k, **v} for k, v in sorted_features[:5]]
    furthest = [{"feature": k, **v} for k, v in sorted_features[-5:][::-1]]

    return {
        "style_match_score": style_match_score,
        "per_feature_deltas": deltas,
        "closest_features": closest,
        "furthest_features": furthest,
        "candidate_word_count": candidate.get("source_word_count", 0),
    }


def generate_style_instructions(text: str, profile: Dict[str, Any]) -> List[str]:
    """Generate deterministic rewrite instructions to match the profile.

    Only emits an instruction when the delta exceeds the noticeable-difference
    threshold for that feature — filters out trivial differences.
    """
    result = score_against_profile(text, profile)
    deltas = result.get("per_feature_deltas", {})
    instructions: List[str] = []

    # Sentence length
    sl = deltas.get("sent_len_mean")
    if sl and sl["abs_delta"] >= NOTICEABLE_THRESHOLDS["sent_len_mean"]:
        diff = sl["candidate"] - sl["reference"]
        if diff > 0:
            instructions.append(
                f"Shorten sentences from avg {sl['candidate']:.0f} to ~{sl['reference']:.0f} words."
            )
        else:
            instructions.append(
                f"Lengthen sentences from avg {sl['candidate']:.0f} to ~{sl['reference']:.0f} words."
            )

    # Passive voice
    pv = deltas.get("passive_ratio")
    if pv and pv["abs_delta"] >= NOTICEABLE_THRESHOLDS["passive_ratio"]:
        diff = pv["candidate"] - pv["reference"]
        if diff > 0:
            instructions.append(
                f"Reduce passive voice from {pv['candidate']:.0%} to ~{pv['reference']:.0%}."
            )
        else:
            instructions.append(
                f"Increase passive voice from {pv['candidate']:.0%} to ~{pv['reference']:.0%}."
            )

    # Readability
    fk = deltas.get("flesch_kincaid_grade")
    if fk and fk["abs_delta"] >= NOTICEABLE_THRESHOLDS["flesch_kincaid_grade"]:
        diff = fk["candidate"] - fk["reference"]
        if diff > 0:
            instructions.append(
                f"Simplify language — target Flesch-Kincaid grade {fk['reference']:.0f} "
                f"(currently {fk['candidate']:.0f})."
            )
        else:
            instructions.append(
                f"Increase technical depth — target grade {fk['reference']:.0f} "
                f"(currently {fk['candidate']:.0f})."
            )

    # Contractions
    cr = deltas.get("contraction_ratio")
    if cr and cr["abs_delta"] >= NOTICEABLE_THRESHOLDS["contraction_ratio"]:
        diff = cr["candidate"] - cr["reference"]
        if diff < 0:
            instructions.append("Use more contractions (don't, we'll, it's) for a conversational tone.")
        else:
            instructions.append("Reduce contractions — expand to full forms (do not, we will) for a formal tone.")

    # First-person
    fp = deltas.get("first_person_ratio")
    if fp and fp["abs_delta"] >= NOTICEABLE_THRESHOLDS["first_person_ratio"]:
        diff = fp["candidate"] - fp["reference"]
        if diff > 0:
            instructions.append(
                f"Reduce first-person voice (I/we) from {fp['candidate']:.0%} to ~{fp['reference']:.0%}."
            )
        else:
            instructions.append(
                f"Increase first-person voice (I/we) from {fp['candidate']:.0%} to ~{fp['reference']:.0%}."
            )

    # Formal score
    fs = deltas.get("formal_score")
    if fs and fs["abs_delta"] >= NOTICEABLE_THRESHOLDS["formal_score"]:
        diff = fs["candidate"] - fs["reference"]
        if diff < 0:
            instructions.append("Use more formal markers (therefore, furthermore, consequently).")
        else:
            instructions.append("Reduce formal markers — simplify phrasing.")

    # Hedge density
    hd = deltas.get("hedge_density")
    if hd and hd["abs_delta"] >= NOTICEABLE_THRESHOLDS["hedge_density"]:
        diff = hd["candidate"] - hd["reference"]
        if diff > 0:
            instructions.append(
                f"Reduce hedging words (maybe, perhaps, somewhat) — currently "
                f"{hd['candidate']:.1f}/1k words, target ~{hd['reference']:.1f}."
            )

    # Superlative density
    sd = deltas.get("superlative_density")
    if sd and sd["abs_delta"] >= NOTICEABLE_THRESHOLDS["superlative_density"]:
        diff = sd["candidate"] - sd["reference"]
        if diff > 0:
            instructions.append("Reduce superlatives (best, most, extremely) — tone down claims.")

    # Adverb density
    ad = deltas.get("adverb_density")
    if ad and ad["abs_delta"] >= NOTICEABLE_THRESHOLDS["adverb_density"]:
        diff = ad["candidate"] - ad["reference"]
        if diff > 0:
            instructions.append(
                f"Reduce -ly adverbs from {ad['candidate']:.1f}/1k to ~{ad['reference']:.1f}/1k. "
                "Use stronger verbs instead."
            )

    # Transition density
    td = deltas.get("transition_density")
    if td and td["abs_delta"] >= NOTICEABLE_THRESHOLDS["transition_density"]:
        diff = td["candidate"] - td["reference"]
        if diff < 0:
            instructions.append("Add more transition words (however, therefore, moreover) for flow.")
        else:
            instructions.append("Reduce transition words — current pacing is too measured.")

    # Heading density
    hdd = deltas.get("heading_density")
    if hdd and hdd["abs_delta"] >= NOTICEABLE_THRESHOLDS["heading_density"]:
        diff = hdd["candidate"] - hdd["reference"]
        if diff < 0:
            instructions.append("Add more section headings for structure.")

    # List density
    ld = deltas.get("list_density")
    if ld and ld["abs_delta"] >= NOTICEABLE_THRESHOLDS["list_density"]:
        diff = ld["candidate"] - ld["reference"]
        if diff < 0:
            instructions.append("Use more bullet/numbered lists — break up dense prose.")

    # Paragraph length
    pl = deltas.get("para_len_mean")
    if pl and pl["abs_delta"] >= NOTICEABLE_THRESHOLDS["para_len_mean"]:
        diff = pl["candidate"] - pl["reference"]
        if diff > 0:
            instructions.append(
                f"Shorten paragraphs from avg {pl['candidate']:.0f} to ~{pl['reference']:.0f} sentences."
            )
        else:
            instructions.append(
                f"Combine short paragraphs — target ~{pl['reference']:.0f} sentences each."
            )

    return instructions


def main():
    """CLI for testing."""
    import argparse
    import json
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="WriteGuard Style Matcher")
    parser.add_argument("--text", help="Text to score")
    parser.add_argument("--file", help="File with text to score")
    parser.add_argument("--profile", help="Path to saved profile JSON")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    text = args.text or ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        text = sys.stdin.read()

    if not args.profile:
        print("ERROR: --profile required", file=sys.stderr)
        sys.exit(1)

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    result = score_against_profile(text, profile)
    instructions = generate_style_instructions(text, profile)

    if args.json:
        print(json.dumps({**result, "instructions": instructions}, indent=2))
    else:
        print(f"Style match: {result['style_match_score']}/100")
        print(f"\nInstructions ({len(instructions)}):")
        for i, ins in enumerate(instructions, 1):
            print(f"  {i}. {ins}")


if __name__ == "__main__":
    main()
