# CUI // SP-CTI
"""Tests for constant extraction in review/adapt/analyze/train proposal_genesis reflexes."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.review import (
    _SHALL_RESPONSE_MIN_WORDS, _SPECIFICITY_MIN_WORDS, _ISSUE_PENALTY,
    _PAST_PERF_BONUS, _MIN_SECTION_CHARS, _CRITICAL_SCORE_THRESHOLD,
    _DRAFTS_FETCH_LIMIT, _FINDING_DETAILS_LIMIT,
    _score_section_compliance, REVIEW_TYPES,
)
from tools.proposal_genesis.reflexes.adapt import (
    _LEVEL1_MAX_TEAM, _LEVEL2_MAX_TEAM, _LEVEL3_MAX_TEAM,
    _LEVEL1_MAX_VALUE, _LEVEL3_MIN_VALUE, _OPP_FETCH_LIMIT,
    PROCESS_LEVELS, _determine_process_level,
)
from tools.proposal_genesis.reflexes.analyze import (
    _STRENGTH_THRESHOLD, _WEAKNESS_THRESHOLD, _CAP_FIT_WEAKNESS,
    _COMPLIANCE_WEAKNESS, _RESOURCE_WEAKNESS, _QUALITY_FLOOR,
    _COMPETITOR_FETCH_LIMIT, _MAX_ANALYSES_PER_RUN,
)
from tools.proposal_genesis.reflexes.train import (
    _DRAFTS_FETCH_LIMIT as TRAIN_DRAFTS_LIMIT,
    _LESSONS_FETCH_LIMIT, _KB_FETCH_LIMIT,
    _DRAFT_MIN_CONTENT, _DRAFT_PAIR2_MIN_CONTENT, _DRAFT_PAIR_MAX_CONTENT,
    _DRAFT_PAIR2_USER_MAX, _DRAFT_PAIR2_OUTPUT_MAX,
    _LESSON_MIN_LENGTH, _KB_MIN_CONTENT, _MAX_PAIRS_PER_RUN,
    _generate_draft_pairs,
)


# ─────────────────────────────────────────────────────────────────
# review.py
# ─────────────────────────────────────────────────────────────────

class TestReviewConstants:
    def test_word_thresholds_ordered(self):
        assert _SPECIFICITY_MIN_WORDS > _SHALL_RESPONSE_MIN_WORDS > 0

    def test_penalties_and_bonuses_valid(self):
        assert 0.0 < _ISSUE_PENALTY < 1.0
        assert 0.0 < _PAST_PERF_BONUS < 1.0

    def test_section_and_score_thresholds(self):
        assert _MIN_SECTION_CHARS > 0
        assert 0.0 < _CRITICAL_SCORE_THRESHOLD < 1.0

    def test_limits_positive(self):
        assert _DRAFTS_FETCH_LIMIT > 0
        assert _FINDING_DETAILS_LIMIT > 0

    def test_review_types_thresholds_ordered(self):
        # Pink < Red < Gold < White
        assert (REVIEW_TYPES["pink"]["threshold"] < REVIEW_TYPES["red"]["threshold"]
                < REVIEW_TYPES["gold"]["threshold"] < REVIEW_TYPES["white"]["threshold"])

    def test_score_compliance_clean_text(self):
        # Short text with no issues scores high
        r = _score_section_compliance("We will deliver a compliant solution.")
        assert 0.0 <= r["score"] <= 1.0

    def test_score_compliance_long_text_no_specifics(self):
        # Long text with no quantitative evidence flags an issue
        long_text = "We will deliver excellent work. " * 30  # >100 words, no numbers
        r = _score_section_compliance(long_text)
        assert any("quantitative" in issue for issue in r["issues"])


# ─────────────────────────────────────────────────────────────────
# adapt.py
# ─────────────────────────────────────────────────────────────────

class TestAdaptConstants:
    def test_team_sizes_ordered(self):
        assert _LEVEL1_MAX_TEAM < _LEVEL2_MAX_TEAM < _LEVEL3_MAX_TEAM

    def test_value_thresholds_ordered(self):
        assert _LEVEL1_MAX_VALUE < _LEVEL3_MIN_VALUE

    def test_process_levels_use_constants(self):
        assert PROCESS_LEVELS[1]["max_team"] == _LEVEL1_MAX_TEAM
        assert PROCESS_LEVELS[2]["max_team"] == _LEVEL2_MAX_TEAM
        assert PROCESS_LEVELS[3]["max_team"] == _LEVEL3_MAX_TEAM

    def test_level3_high_value(self):
        r = _determine_process_level({"estimated_value": 10_000_000}, team_size=2)
        assert r["level"] == 3

    def test_level3_large_team(self):
        r = _determine_process_level({"estimated_value": 100_000}, team_size=12)
        assert r["level"] == 3

    def test_level1_small(self):
        r = _determine_process_level({"estimated_value": 100_000}, team_size=2)
        assert r["level"] == 1

    def test_level2_default(self):
        r = _determine_process_level({"estimated_value": 2_000_000}, team_size=5)
        assert r["level"] == 2

    def test_opp_fetch_limit_positive(self):
        assert _OPP_FETCH_LIMIT > 0


# ─────────────────────────────────────────────────────────────────
# analyze.py
# ─────────────────────────────────────────────────────────────────

class TestAnalyzeConstants:
    def test_strength_weakness_ordered(self):
        assert _STRENGTH_THRESHOLD > _WEAKNESS_THRESHOLD > 0

    def test_weakness_thresholds_valid(self):
        for v in (_CAP_FIT_WEAKNESS, _COMPLIANCE_WEAKNESS, _RESOURCE_WEAKNESS):
            assert 0.0 < v < 1.0

    def test_quality_floor_valid(self):
        assert 0 < _QUALITY_FLOOR < 100

    def test_limits_positive(self):
        assert _COMPETITOR_FETCH_LIMIT > 0
        assert _MAX_ANALYSES_PER_RUN > 0


# ─────────────────────────────────────────────────────────────────
# train.py
# ─────────────────────────────────────────────────────────────────

class TestTrainConstants:
    def test_fetch_limits_positive(self):
        assert TRAIN_DRAFTS_LIMIT > 0
        assert _LESSONS_FETCH_LIMIT > 0
        assert _KB_FETCH_LIMIT > 0

    def test_content_min_thresholds_ordered(self):
        # Pair-2 requires more content than pair-1
        assert _DRAFT_PAIR2_MIN_CONTENT > _DRAFT_MIN_CONTENT > 0

    def test_max_content_caps_positive(self):
        assert _DRAFT_PAIR_MAX_CONTENT > 0
        assert _DRAFT_PAIR2_USER_MAX > 0
        assert _DRAFT_PAIR2_OUTPUT_MAX > 0
        assert _KB_MIN_CONTENT > 0

    def test_misc_limits(self):
        assert _LESSON_MIN_LENGTH > 0
        assert _MAX_PAIRS_PER_RUN > 0

    def test_draft_pairs_skips_short_content(self):
        # Content below _DRAFT_MIN_CONTENT yields no pairs
        short = {"final_content": "too short", "section_type": "intro"}
        assert _generate_draft_pairs(short) == []

    def test_draft_pairs_generates_for_long_content(self):
        long_content = "This is substantive proposal content. " * 20  # > 100 chars
        draft = {"final_content": long_content, "section_type": "technical",
                 "agency": "DOD", "opp_title": "Cloud Migration", "id": "d1"}
        pairs = _generate_draft_pairs(draft)
        assert len(pairs) >= 1
        # Output respects max content cap
        for p in pairs:
            assert len(p["expected_output"]) <= _DRAFT_PAIR_MAX_CONTENT
