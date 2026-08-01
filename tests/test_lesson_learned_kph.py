# CUI // SP-CTI
"""Tests for the kph pipeline-hardening lesson patterns.

Covers _classify_outcome mapping, systemic membership, and category labels for
UNMERGED_STRANDED / MIGRATION_NUMBER_COLLISION / SIBLING_FILE_CONFLICT /
MISSING_ICDEV_MIRROR — pure functions, no DB.
"""
from __future__ import annotations

from tools.workflow.lesson_learned import (
    LessonPattern,
    _classify_outcome,
    _pattern_to_category,
    _SYSTEMIC_PATTERNS,
)


def _c(outcome="", reason="", fc=0, tc=1):
    return _classify_outcome(outcome, fc, reason, tc)


class TestClassifyKphOutcomes:
    def test_refused_done_unmerged_outcome(self):
        assert _c(outcome="refused_done_unmerged") == LessonPattern.UNMERGED_STRANDED

    def test_unmerged_stranded_outcome(self):
        assert _c(outcome="unmerged_stranded") == LessonPattern.UNMERGED_STRANDED

    def test_unmerged_reason_beats_phantom(self):
        # "has commits not on origin (unmerged)" must NOT be misfiled as phantom
        # ("no commits") — the stranded rule runs first.
        assert _c(reason="branch has commits not on origin/main (unmerged)") == \
            LessonPattern.UNMERGED_STRANDED

    def test_migration_collision(self):
        assert _c(outcome="migration_number_collision") == LessonPattern.MIGRATION_NUMBER_COLLISION
        assert _c(reason="migration number 259 collision") == LessonPattern.MIGRATION_NUMBER_COLLISION

    def test_sibling_file_conflict(self):
        assert _c(outcome="sibling_file_conflict") == LessonPattern.SIBLING_FILE_CONFLICT
        assert _c(reason="held: sibling file conflict with 2 open PRs") == \
            LessonPattern.SIBLING_FILE_CONFLICT

    def test_missing_icdev_mirror(self):
        assert _c(outcome="missing_icdev_mirror") == LessonPattern.MISSING_ICDEV_MIRROR
        assert _c(reason="tools/cortex/x.py icdev twin mirror missing") == \
            LessonPattern.MISSING_ICDEV_MIRROR

    def test_unrelated_outcome_unaffected(self):
        assert _c(outcome="success", fc=0, tc=1) == LessonPattern.SUCCESS_FIRST_TRY
        assert _c(reason="timeout", fc=3) == LessonPattern.TIMEOUT_QUARANTINE

    def test_real_coherence_gate_messages_auto_classify(self):
        # migration_numbering / icdev_mirror_parity failures flow into
        # last_failure_reason as "[check_id] message" (validated_commit
        # _parse_coherence_failures), so the EXISTING task-failure lesson hook
        # classifies them with no dedicated emitter needed.
        mig = "[migration_numbering] 1 changed migration(s) reuse an existing number; next free is 264."
        assert _c(outcome="failure", reason=mig) == LessonPattern.MIGRATION_NUMBER_COLLISION
        mir = "[icdev_mirror_parity] 1 changed tools/ module(s) not mirrored to icdev/ (roots: tools/cortex)"
        assert _c(outcome="failure", reason=mir) == LessonPattern.MISSING_ICDEV_MIRROR


class TestKphPatternsSystemic:
    def test_all_four_are_systemic(self):
        for p in (
            LessonPattern.UNMERGED_STRANDED,
            LessonPattern.MIGRATION_NUMBER_COLLISION,
            LessonPattern.SIBLING_FILE_CONFLICT,
            LessonPattern.MISSING_ICDEV_MIRROR,
        ):
            assert p in _SYSTEMIC_PATTERNS

    def test_categories_labeled(self):
        for p in (
            LessonPattern.UNMERGED_STRANDED,
            LessonPattern.MIGRATION_NUMBER_COLLISION,
            LessonPattern.SIBLING_FILE_CONFLICT,
            LessonPattern.MISSING_ICDEV_MIRROR,
        ):
            label = _pattern_to_category(p)
            assert label and label != p  # a human label, not the raw slug
