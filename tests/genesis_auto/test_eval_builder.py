#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for NOVA SELA — tools/evolution/eval_builder.py

Validates: import, EvalDataset helpers, golden load, kanban load,
synthetic fallback skip, build_dataset splits, is_sufficient gate.
All tests run on the SQLite test backend. LLM is mocked — no real calls.
"""

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _golden_dir():
    from tools.evolution.eval_builder import GOLDEN_DIR
    return GOLDEN_DIR


def _write_temp_golden(skill_name: str, tmp_path: Path, n: int = 8) -> Path:
    """Write a minimal JSONL golden file for a skill and return its path."""
    jsonl = tmp_path / f"{skill_name}.jsonl"
    lines = []
    for i in range(n):
        difficulty = ["easy", "medium", "hard"][i % 3]
        lines.append(json.dumps({
            "id": f"g-{i:03d}",
            "task_input": f"Task scenario {i} for {skill_name}",
            "expected_behavior": f"Expected rubric {i}",
            "difficulty": difficulty,
            "category": "test_category",
        }))
    jsonl.write_text("\n".join(lines), encoding="utf-8")
    return jsonl


# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------

def test_eval_builder_imports():
    """Module imports without error."""
    import tools.evolution.eval_builder  # noqa: F401


def test_eval_builder_classes_exist():
    """EvalExample, EvalDataset, and build_dataset are exposed."""
    from tools.evolution.eval_builder import EvalExample, EvalDataset, build_dataset
    for obj in (EvalExample, EvalDataset, build_dataset):
        assert obj is not None


# ---------------------------------------------------------------------------
# EvalExample
# ---------------------------------------------------------------------------

def test_eval_example_fields():
    """EvalExample holds all expected fields."""
    from tools.evolution.eval_builder import EvalExample
    ex = EvalExample(
        example_id="ex-001",
        task_input="run the tests",
        expected_behavior="all tests pass",
        difficulty="medium",
        category="testing",
        source="golden",
    )
    assert ex.example_id == "ex-001"
    assert ex.source == "golden"
    assert ex.score == 0.0
    assert ex.actual_output == ""


# ---------------------------------------------------------------------------
# EvalDataset helpers
# ---------------------------------------------------------------------------

def test_evaldataset_all_examples_combines_splits():
    """EvalDataset.all_examples returns train + val + holdout."""
    from tools.evolution.eval_builder import EvalDataset, EvalExample

    def _ex(eid):
        return EvalExample(eid, "input", "expected", "easy", "general", "golden")

    ds = EvalDataset(skill_name="test")
    ds.train = [_ex("t1"), _ex("t2")]
    ds.val = [_ex("v1")]
    ds.holdout = [_ex("h1"), _ex("h2"), _ex("h3")]

    assert len(ds.all_examples) == 6


def test_evaldataset_is_sufficient_true():
    """is_sufficient returns True when all_examples >= min_examples."""
    from tools.evolution.eval_builder import EvalDataset, EvalExample

    def _ex(i):
        return EvalExample(f"e{i}", "input", "expected", "easy", "general", "golden")

    ds = EvalDataset(skill_name="test")
    ds.train = [_ex(i) for i in range(6)]
    assert ds.is_sufficient(min_examples=6) is True


def test_evaldataset_is_sufficient_false():
    """is_sufficient returns False when examples < threshold."""
    from tools.evolution.eval_builder import EvalDataset

    ds = EvalDataset(skill_name="test")
    assert ds.is_sufficient(min_examples=6) is False
    assert ds.is_sufficient(min_examples=0) is True  # 0 threshold always passes


# ---------------------------------------------------------------------------
# _load_golden
# ---------------------------------------------------------------------------

def test_load_golden_returns_empty_when_file_missing():
    """_load_golden returns [] when the golden JSONL doesn't exist."""
    from tools.evolution.eval_builder import _load_golden
    result = _load_golden(f"nonexistent-skill-{uuid.uuid4().hex[:6]}")
    assert result == []


def test_load_golden_loads_examples(tmp_path):
    """_load_golden parses JSONL correctly when file exists."""
    import tools.evolution.eval_builder as eb
    skill = f"test-skill-{uuid.uuid4().hex[:6]}"

    # Write temp file and redirect GOLDEN_DIR
    jsonl = tmp_path / f"{skill}.jsonl"
    jsonl.write_text(
        '{"id": "g001", "task_input": "build a feature", "expected_behavior": "all tests pass", "difficulty": "easy", "category": "build"}\n'
        '{"id": "g002", "task_input": "fix a bug", "expected_behavior": "regression tests pass", "difficulty": "hard"}\n',
        encoding="utf-8",
    )

    orig_dir = eb.GOLDEN_DIR
    eb.GOLDEN_DIR = tmp_path
    try:
        examples = eb._load_golden(skill)
    finally:
        eb.GOLDEN_DIR = orig_dir

    assert len(examples) == 2
    assert examples[0].source == "golden"
    assert examples[0].example_id == "g001"
    assert examples[0].difficulty == "easy"
    assert examples[1].difficulty == "hard"
    assert examples[1].category == "general"  # missing → default


def test_load_golden_skips_blank_lines(tmp_path):
    """_load_golden skips blank lines in the JSONL file."""
    import tools.evolution.eval_builder as eb
    skill = f"test-skill-{uuid.uuid4().hex[:6]}"

    jsonl = tmp_path / f"{skill}.jsonl"
    jsonl.write_text(
        '\n'
        '{"id": "g001", "task_input": "do X", "expected_behavior": "Y done"}\n'
        '\n',
        encoding="utf-8",
    )

    orig_dir = eb.GOLDEN_DIR
    eb.GOLDEN_DIR = tmp_path
    try:
        examples = eb._load_golden(skill)
    finally:
        eb.GOLDEN_DIR = orig_dir

    assert len(examples) == 1


# ---------------------------------------------------------------------------
# _load_kanban
# ---------------------------------------------------------------------------

def test_load_kanban_returns_list():
    """_load_kanban returns a list (possibly empty) without raising."""
    from tools.evolution.eval_builder import _load_kanban
    result = _load_kanban(f"nonexistent-skill-{uuid.uuid4().hex[:6]}")
    assert isinstance(result, list)


def test_load_kanban_difficulty_from_failure_count():
    """_load_kanban correctly derives difficulty from failure_count via mocked DB rows."""
    import tools.evolution.eval_builder as eb

    skill = f"nova-sela-kb-{uuid.uuid4().hex[:6]}"
    tid_easy = f"sela-easy-{uuid.uuid4().hex[:6]}"
    tid_hard = f"sela-hard-{uuid.uuid4().hex[:6]}"

    # Simulate fetchall() returning (id, title, desc, status, failure_count) rows
    fake_rows = [
        (tid_easy, "Easy task", "desc easy", "done", 0),
        (tid_hard, "Hard task", "desc hard", "done", 2),
    ]

    class FakeCursor:
        def execute(self, sql, params=None):
            return self
        def fetchall(self):
            return fake_rows
        def close(self):
            pass

    class FakeConn:
        def execute(self, sql, params=None):
            return FakeCursor()
        def close(self):
            pass

    # Patch get_connection wherever the module imports it from
    import importlib
    storage_mod = importlib.import_module("tools.db.storage")
    orig_fn = storage_mod.get_connection
    storage_mod.get_connection = lambda: FakeConn()
    try:
        examples = eb._load_kanban(skill)
    finally:
        storage_mod.get_connection = orig_fn

    assert len(examples) == 2
    difficulties = {e.example_id: e.difficulty for e in examples}
    easy_id = tid_easy[:12]
    hard_id = tid_hard[:12]
    assert difficulties.get(easy_id) == "easy", f"Got: {difficulties}"
    assert difficulties.get(hard_id) == "hard", f"Got: {difficulties}"


# ---------------------------------------------------------------------------
# build_dataset — empty
# ---------------------------------------------------------------------------

def test_build_dataset_empty_skill_no_golden_no_kanban():
    """build_dataset returns empty EvalDataset when no examples found."""
    from tools.evolution.eval_builder import build_dataset
    skill = f"nova-sela-empty-{uuid.uuid4().hex[:6]}"
    ds = build_dataset(skill, skill_text="")
    assert ds.skill_name == skill
    assert len(ds.all_examples) == 0
    assert ds.is_sufficient() is False


# ---------------------------------------------------------------------------
# build_dataset — golden source
# ---------------------------------------------------------------------------

def test_build_dataset_golden_creates_splits(tmp_path):
    """build_dataset with 10 golden examples creates non-empty train/val/holdout."""
    import tools.evolution.eval_builder as eb

    skill = f"nova-sela-golden-{uuid.uuid4().hex[:6]}"
    _write_temp_golden(skill, tmp_path, n=10)

    orig_dir = eb.GOLDEN_DIR
    eb.GOLDEN_DIR = tmp_path
    try:
        ds = eb.build_dataset(skill, skill_text="")
    finally:
        eb.GOLDEN_DIR = orig_dir

    assert ds.skill_name == skill
    assert len(ds.train) >= 1
    assert len(ds.val) >= 1
    assert len(ds.holdout) >= 1
    assert len(ds.train) + len(ds.val) + len(ds.holdout) == 10
    assert ds.is_sufficient()


def test_build_dataset_golden_all_source_tagged(tmp_path):
    """All examples from golden JSONL are tagged source='golden'."""
    import tools.evolution.eval_builder as eb

    skill = f"nova-sela-src-{uuid.uuid4().hex[:6]}"
    _write_temp_golden(skill, tmp_path, n=8)

    orig_dir = eb.GOLDEN_DIR
    eb.GOLDEN_DIR = tmp_path
    try:
        ds = eb.build_dataset(skill, skill_text="")
    finally:
        eb.GOLDEN_DIR = orig_dir

    for ex in ds.all_examples:
        assert ex.source == "golden", f"Expected source=golden, got {ex.source!r}"


def test_build_dataset_split_ratios_approximate(tmp_path):
    """Train/val split is approximately 60/20/20 for 10 examples."""
    import tools.evolution.eval_builder as eb

    skill = f"nova-sela-ratio-{uuid.uuid4().hex[:6]}"
    _write_temp_golden(skill, tmp_path, n=10)

    orig_dir = eb.GOLDEN_DIR
    eb.GOLDEN_DIR = tmp_path
    try:
        ds = eb.build_dataset(skill, skill_text="", train_ratio=0.6, val_ratio=0.2)
    finally:
        eb.GOLDEN_DIR = orig_dir

    # With 10 examples: train ≈ 6, val ≈ 2, holdout ≈ 2
    assert len(ds.train) >= 4  # at least 40% (allow rounding)
    assert len(ds.val) >= 1
    assert len(ds.holdout) >= 1


# ---------------------------------------------------------------------------
# build_dataset — synthetic fallback skipped when golden sufficient
# ---------------------------------------------------------------------------

def test_build_dataset_skips_synthetic_when_golden_sufficient(tmp_path):
    """_generate_synthetic is NOT called when golden+kanban ≥ min_examples."""
    import tools.evolution.eval_builder as eb

    skill = f"nova-sela-skip-{uuid.uuid4().hex[:6]}"
    _write_temp_golden(skill, tmp_path, n=10)

    orig_dir = eb.GOLDEN_DIR
    eb.GOLDEN_DIR = tmp_path
    with patch.object(eb, "_generate_synthetic") as mock_syn:
        try:
            eb.build_dataset(skill, skill_text="some text", min_examples=6)
        finally:
            eb.GOLDEN_DIR = orig_dir
    mock_syn.assert_not_called()


def test_build_dataset_calls_synthetic_when_insufficient():
    """_generate_synthetic IS called when examples < min_examples and skill_text provided."""
    import tools.evolution.eval_builder as eb

    skill = f"nova-sela-syn-{uuid.uuid4().hex[:6]}"
    fake_examples = [
        eb.EvalExample(f"syn-{i}", "input", "expected", "medium", "test", "synthetic")
        for i in range(8)
    ]
    with patch.object(eb, "_load_golden", return_value=[]):
        with patch.object(eb, "_load_kanban", return_value=[]):
            with patch.object(eb, "_generate_synthetic", return_value=fake_examples) as mock_syn:
                ds = eb.build_dataset(skill, skill_text="some skill text", min_examples=6)
    mock_syn.assert_called_once_with(skill, "some skill text", count=10)
    assert len(ds.all_examples) == 8
    assert all(ex.source == "synthetic" for ex in ds.all_examples)


# ---------------------------------------------------------------------------
# build_dataset — sorted deterministically
# ---------------------------------------------------------------------------

def test_build_dataset_is_deterministic(tmp_path):
    """Two calls with same input produce same split (sorted by example_id)."""
    import tools.evolution.eval_builder as eb

    skill = f"nova-sela-det-{uuid.uuid4().hex[:6]}"
    _write_temp_golden(skill, tmp_path, n=10)

    orig_dir = eb.GOLDEN_DIR
    eb.GOLDEN_DIR = tmp_path
    try:
        ds1 = eb.build_dataset(skill, skill_text="")
        ds2 = eb.build_dataset(skill, skill_text="")
    finally:
        eb.GOLDEN_DIR = orig_dir

    ids1 = [e.example_id for e in ds1.train]
    ids2 = [e.example_id for e in ds2.train]
    assert ids1 == ids2, "build_dataset is not deterministic"
