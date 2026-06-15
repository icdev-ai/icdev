#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for NOVA SELA — tools/evolution/artifact_evolver.py

Validates: constraint gates (size, growth, structure), dry_run behaviour,
oracle_predictions write on improvement, NEVER auto-merge invariant.
LLM is mocked — no real calls. DB interactions are mocked.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ---------------------------------------------------------------------------
# Import checks
# ---------------------------------------------------------------------------

def test_artifact_evolver_imports():
    """Module imports without error."""
    import tools.evolution.artifact_evolver  # noqa: F401


def test_artifact_evolver_functions_exist():
    """evolve_artifact and evolve_all_skills are exposed."""
    from tools.evolution.artifact_evolver import evolve_artifact, evolve_all_skills
    assert callable(evolve_artifact)
    assert callable(evolve_all_skills)


# ---------------------------------------------------------------------------
# _validate_candidate — constraint gates
# ---------------------------------------------------------------------------

def test_validate_candidate_passes_valid():
    """A well-formed candidate within size and growth limits passes all gates."""
    from tools.evolution.artifact_evolver import _validate_candidate

    # Use a large enough baseline so a small addition doesn't breach 20% growth
    baseline = "# My Skill\n\n" + ("Do step A correctly.\n" * 30)
    candidate = "# My Skill\n\n" + ("Do step A correctly.\n" * 31)  # ~3% growth
    passed, reason = _validate_candidate(candidate, baseline)
    assert passed is True, f"Expected pass but got: {reason}"
    assert reason == "ok"


def test_validate_candidate_rejects_empty():
    """Empty or too-short candidate is rejected."""
    from tools.evolution.artifact_evolver import _validate_candidate

    passed, reason = _validate_candidate("", "# Baseline\nSome text here.\n")
    assert passed is False
    assert "empty" in reason or "short" in reason


def test_validate_candidate_rejects_over_15kb():
    """Candidate exceeding 15 KB is rejected."""
    from tools.evolution.artifact_evolver import _validate_candidate, MAX_SKILL_BYTES

    # Build a candidate just over the limit
    big = "# Big Skill\n\n" + ("x " * (MAX_SKILL_BYTES // 2 + 100))
    passed, reason = _validate_candidate(big, "# Baseline\nSmall baseline.\n")
    assert passed is False
    assert "size" in reason or "limit" in reason or "exceed" in reason.lower()


def test_validate_candidate_rejects_growth_over_20_percent():
    """Candidate growing more than 20% beyond baseline bytes is rejected."""
    from tools.evolution.artifact_evolver import _validate_candidate

    baseline = "# Baseline\n\n" + ("a " * 500)  # ~1001 bytes
    # 25% larger
    candidate = "# Baseline\n\n" + ("a " * 625)
    passed, reason = _validate_candidate(candidate, baseline)
    assert passed is False
    assert "growth" in reason


def test_validate_candidate_rejects_missing_heading():
    """Candidate without a top-level '# ' heading is rejected."""
    from tools.evolution.artifact_evolver import _validate_candidate

    # Baseline and candidate must be similar size so growth gate doesn't fire first
    filler = "Do things correctly and efficiently per process.\n" * 10
    baseline = "# Skill\n\n" + filler
    candidate = "No heading here, just prose about how to do things correctly.\n" + filler
    passed, reason = _validate_candidate(candidate, baseline)
    assert passed is False
    assert "heading" in reason


# ---------------------------------------------------------------------------
# evolve_artifact — dry_run does NOT write oracle_predictions
# ---------------------------------------------------------------------------

def test_evolve_artifact_dry_run_no_db_write(tmp_path):
    """dry_run=True returns a result dict without inserting into oracle_predictions."""
    import tools.evolution.artifact_evolver as ev

    skill_name = f"test-dry-{uuid.uuid4().hex[:6]}"
    skill_text = (
        "# Test Skill\n\n"
        "Run all unit and integration tests. Verify output matches expected behaviour.\n"
        "Report any failures with full tracebacks and propose fixes.\n"
    )

    skill_file = tmp_path / f"{skill_name}.md"
    skill_file.write_text(skill_text, encoding="utf-8")

    from tools.evolution.eval_builder import EvalDataset, EvalExample

    def _ex(i):
        return EvalExample(f"e{i}", f"task {i}", f"expected {i}", "medium", "test", "golden")

    fake_dataset = EvalDataset(skill_name=skill_name)
    fake_dataset.train = [_ex(i) for i in range(4)]
    fake_dataset.val = [_ex(i + 4) for i in range(2)]
    fake_dataset.holdout = [_ex(i + 6) for i in range(2)]

    orig_skills_dir = ev.SKILLS_DIR
    ev.SKILLS_DIR = tmp_path
    try:
        # Lazy imports inside evolve_artifact — patch in their home modules
        with patch("tools.evolution.eval_builder.build_dataset", return_value=fake_dataset), \
             patch("tools.evolution.fitness.score_examples", return_value=(0.5, [])), \
             patch("tools.workflow.trace_logger.get_traces_for_task_type", return_value=[]), \
             patch("tools.evolution.artifact_evolver._generate_mutations",
                   return_value=[skill_text]), \
             patch("tools.evolution.artifact_evolver._create_evolution_suggestion") as mock_db:

            result = ev.evolve_artifact(skill_name, dry_run=True)

        mock_db.assert_not_called()
    finally:
        ev.SKILLS_DIR = orig_skills_dir

    assert result.get("dry_run") is True
    assert result.get("promoted") is False


# ---------------------------------------------------------------------------
# evolve_artifact — constraint gate rejects oversized candidate
# ---------------------------------------------------------------------------

def test_evolve_artifact_oversized_candidate_skips(tmp_path):
    """A candidate > 15 KB is rejected by the constraint gate and no promotion occurs."""
    import tools.evolution.artifact_evolver as ev

    skill_name = f"test-big-{uuid.uuid4().hex[:6]}"
    skill_text = "# Big Skill\n\nDo things.\n"
    skill_file = tmp_path / f"{skill_name}.md"
    skill_file.write_text(skill_text, encoding="utf-8")

    from tools.evolution.eval_builder import EvalDataset, EvalExample

    def _ex(i):
        return EvalExample(f"e{i}", f"task {i}", f"expected {i}", "medium", "test", "golden")

    fake_dataset = EvalDataset(skill_name=skill_name)
    fake_dataset.train = [_ex(i) for i in range(4)]
    fake_dataset.val = [_ex(i + 4) for i in range(2)]
    fake_dataset.holdout = [_ex(i + 6) for i in range(2)]

    # Candidate is > 15 KB
    oversized_candidate = "# Big Skill\n\n" + ("z " * 8000)

    orig_skills_dir = ev.SKILLS_DIR
    ev.SKILLS_DIR = tmp_path
    try:
        with patch("tools.evolution.eval_builder.build_dataset", return_value=fake_dataset), \
             patch("tools.evolution.fitness.score_examples", return_value=(0.5, [])), \
             patch("tools.workflow.trace_logger.get_traces_for_task_type", return_value=[]), \
             patch("tools.evolution.artifact_evolver._generate_mutations",
                   return_value=[oversized_candidate]), \
             patch("tools.evolution.artifact_evolver._create_evolution_suggestion") as mock_db:

            result = ev.evolve_artifact(skill_name, dry_run=False)

        mock_db.assert_not_called()
    finally:
        ev.SKILLS_DIR = orig_skills_dir

    assert result.get("skipped") is True or result.get("promoted") is False


# ---------------------------------------------------------------------------
# evolve_artifact — growth > 20% rejected
# ---------------------------------------------------------------------------

def test_evolve_artifact_growth_over_limit_skipped(tmp_path):
    """Candidate growing > 20% from baseline fails the growth gate and is not promoted."""
    import tools.evolution.artifact_evolver as ev

    skill_name = f"test-grow-{uuid.uuid4().hex[:6]}"
    # 500-word baseline ~ 3 KB
    skill_text = "# Grow Skill\n\n" + ("word " * 500)
    skill_file = tmp_path / f"{skill_name}.md"
    skill_file.write_text(skill_text, encoding="utf-8")

    from tools.evolution.eval_builder import EvalDataset, EvalExample

    def _ex(i):
        return EvalExample(f"e{i}", f"task {i}", f"expected {i}", "medium", "test", "golden")

    fake_dataset = EvalDataset(skill_name=skill_name)
    fake_dataset.train = [_ex(i) for i in range(4)]
    fake_dataset.val = [_ex(i + 4) for i in range(2)]
    fake_dataset.holdout = [_ex(i + 6) for i in range(2)]

    # Candidate is 25% larger (> +20%)
    bloated_candidate = "# Grow Skill\n\n" + ("word " * 625)

    orig_skills_dir = ev.SKILLS_DIR
    ev.SKILLS_DIR = tmp_path
    try:
        with patch("tools.evolution.eval_builder.build_dataset", return_value=fake_dataset), \
             patch("tools.evolution.fitness.score_examples", return_value=(0.5, [])), \
             patch("tools.workflow.trace_logger.get_traces_for_task_type", return_value=[]), \
             patch("tools.evolution.artifact_evolver._generate_mutations",
                   return_value=[bloated_candidate]), \
             patch("tools.evolution.artifact_evolver._create_evolution_suggestion") as mock_db:

            result = ev.evolve_artifact(skill_name, dry_run=False)

        mock_db.assert_not_called()
    finally:
        ev.SKILLS_DIR = orig_skills_dir

    assert result.get("skipped") is True or result.get("promoted") is False


# ---------------------------------------------------------------------------
# evolve_artifact — valid improvement writes oracle_predictions (status=suggested)
# ---------------------------------------------------------------------------

def test_evolve_artifact_improvement_writes_suggestion(tmp_path):
    """When winner beats baseline by >= MIN_IMPROVEMENT_DELTA, oracle_predictions is created."""
    import tools.evolution.artifact_evolver as ev

    skill_name = f"test-win-{uuid.uuid4().hex[:6]}"
    # Baseline must be large enough that a small addition stays under 20% growth
    _body = "Run all tests to validate correctness. Deploy with rollback on failure.\n" * 15
    skill_text = f"# Win Skill\n\n{_body}"
    skill_file = tmp_path / f"{skill_name}.md"
    skill_file.write_text(skill_text, encoding="utf-8")

    from tools.evolution.eval_builder import EvalDataset, EvalExample

    def _ex(i):
        return EvalExample(f"e{i}", f"task {i}", f"expected {i}", "easy", "test", "golden")

    fake_dataset = EvalDataset(skill_name=skill_name)
    fake_dataset.train = [_ex(i) for i in range(4)]
    fake_dataset.val = [_ex(i + 4) for i in range(2)]
    fake_dataset.holdout = [_ex(i + 6) for i in range(2)]

    # Candidate adds one line (~37 bytes) to a 1100-byte baseline: ~3% growth
    valid_candidate = f"# Win Skill\n\n{_body}Verify health before marking done.\n"

    # baseline=0.5, winner=0.65 → improvement=0.15 >= MIN_IMPROVEMENT_DELTA(0.05)
    call_count = [0]

    def fake_score_examples(examples, candidate_skill_text, mode="fast"):
        call_count[0] += 1
        # First call = baseline, next calls = winner scores
        if call_count[0] == 1:
            return 0.5, []
        return 0.65, []

    suggestion_id = f"nova-sela-{uuid.uuid4().hex[:10]}"

    orig_skills_dir = ev.SKILLS_DIR
    ev.SKILLS_DIR = tmp_path
    try:
        with patch("tools.evolution.eval_builder.build_dataset", return_value=fake_dataset), \
             patch("tools.evolution.fitness.score_examples", side_effect=fake_score_examples), \
             patch("tools.workflow.trace_logger.get_traces_for_task_type", return_value=[]), \
             patch("tools.evolution.artifact_evolver._generate_mutations",
                   return_value=[valid_candidate]), \
             patch("tools.evolution.artifact_evolver._create_evolution_suggestion",
                   return_value=suggestion_id) as mock_suggest:

            result = ev.evolve_artifact(skill_name, dry_run=False)

        mock_suggest.assert_called_once()
        call_args = mock_suggest.call_args
        assert call_args[0][0] == skill_name or call_args[1].get("skill_name") == skill_name
    finally:
        ev.SKILLS_DIR = orig_skills_dir

    assert result.get("promoted") is True
    assert result.get("suggestion_id") == suggestion_id


# ---------------------------------------------------------------------------
# NEVER auto-merge: skill file must NOT be modified by evolve_artifact
# ---------------------------------------------------------------------------

def test_evolve_artifact_never_modifies_skill_file(tmp_path):
    """evolve_artifact NEVER writes back to the skill file — only oracle_predictions."""
    import tools.evolution.artifact_evolver as ev

    skill_name = f"test-nowrite-{uuid.uuid4().hex[:6]}"
    original_text = "# No Write Skill\n\nOriginal content — must not change.\n"
    skill_file = tmp_path / f"{skill_name}.md"
    skill_file.write_text(original_text, encoding="utf-8")

    from tools.evolution.eval_builder import EvalDataset, EvalExample

    def _ex(i):
        return EvalExample(f"e{i}", f"task {i}", f"expected {i}", "easy", "test", "golden")

    fake_dataset = EvalDataset(skill_name=skill_name)
    fake_dataset.train = [_ex(i) for i in range(4)]
    fake_dataset.val = [_ex(i + 4) for i in range(2)]
    fake_dataset.holdout = [_ex(i + 6) for i in range(2)]

    improved_candidate = "# No Write Skill\n\nImproved content should NOT appear in file.\n"

    orig_skills_dir = ev.SKILLS_DIR
    ev.SKILLS_DIR = tmp_path
    try:
        with patch("tools.evolution.eval_builder.build_dataset", return_value=fake_dataset), \
             patch("tools.evolution.fitness.score_examples", side_effect=[(0.4, []), (0.9, []), (0.9, [])]), \
             patch("tools.workflow.trace_logger.get_traces_for_task_type", return_value=[]), \
             patch("tools.evolution.artifact_evolver._generate_mutations",
                   return_value=[improved_candidate]), \
             patch("tools.evolution.artifact_evolver._create_evolution_suggestion",
                   return_value=f"nova-sela-{uuid.uuid4().hex[:10]}"):

            ev.evolve_artifact(skill_name, dry_run=False)
    finally:
        ev.SKILLS_DIR = orig_skills_dir

    # Skill file must be byte-for-byte identical to original
    assert skill_file.read_text(encoding="utf-8") == original_text, \
        "evolve_artifact MUST NOT write back to the skill file"


# ---------------------------------------------------------------------------
# evolve_artifact — artifact not found
# ---------------------------------------------------------------------------

def test_evolve_artifact_missing_skill_returns_error(tmp_path):
    """evolve_artifact returns error dict when skill file doesn't exist."""
    import tools.evolution.artifact_evolver as ev

    orig_skills_dir = ev.SKILLS_DIR
    ev.SKILLS_DIR = tmp_path
    try:
        result = ev.evolve_artifact(f"nonexistent-{uuid.uuid4().hex[:8]}")
    finally:
        ev.SKILLS_DIR = orig_skills_dir

    assert "error" in result


# ---------------------------------------------------------------------------
# evolve_all_skills — returns dict
# ---------------------------------------------------------------------------

def test_evolve_all_skills_returns_dict(tmp_path):
    """evolve_all_skills returns a dict with skills_processed and results."""
    import tools.evolution.artifact_evolver as ev

    # Write two fake icdev-*.md files
    for name in ("icdev-alpha", "icdev-beta"):
        p = tmp_path / f"{name}.md"
        p.write_text(f"# {name} Skill\n\nDo something.\n", encoding="utf-8")

    orig_skills_dir = ev.SKILLS_DIR
    ev.SKILLS_DIR = tmp_path
    try:
        with patch("tools.evolution.artifact_evolver.evolve_artifact",
                   return_value={"skipped": True, "reason": "mocked"}):
            result = ev.evolve_all_skills(dry_run=True, limit=5)
    finally:
        ev.SKILLS_DIR = orig_skills_dir

    assert isinstance(result, dict)
    assert "skills_processed" in result
    assert "results" in result
    assert result["skills_processed"] <= 5
