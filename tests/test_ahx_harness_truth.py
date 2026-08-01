# CUI // SP-CTI
"""AHX — Agent Harness Truth & Measurement.

Locks in the fixes for the defects that left harness_eval unable to measure
anything: a silent zero-row outcome write, a three-way split in the outcome
vocabulary, documented tools that never existed, a per-machine hardcoded memory
path, and self-heal rate limits hardcoded in two modules.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.genesis.harness.eval_harness import (
    SUCCESS_OUTCOMES,
    VALID_OUTCOMES,
    record_decision,
    record_outcome,
)

# conftest forces the SQLite backend but does not provision a database for
# get_connection(), so point it at a scratch file carrying just this table.
_HARNESS_EVAL_DDL = """
CREATE TABLE IF NOT EXISTS harness_eval (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL DEFAULT '',
    reflex         TEXT NOT NULL,
    decision       TEXT NOT NULL,
    confidence     REAL,
    metadata_json  TEXT DEFAULT '{}',
    actual_outcome TEXT,
    resolved_at    TEXT,
    created_at     TEXT NOT NULL
);
"""


@pytest.fixture
def harness_db(tmp_path, monkeypatch):
    """Scratch SQLite DB with harness_eval, wired into get_connection()."""
    import sqlite3

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_HARNESS_EVAL_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    return db_path


# ---------------------------------------------------------------------------
# record_outcome must not silently no-op (ahx-eval-01)
# ---------------------------------------------------------------------------

def test_record_outcome_reports_when_no_decision_row_exists(harness_db):
    """A zero-row UPDATE used to be indistinguishable from success.

    That is the whole reason harness_eval accumulated 123-of-129 rows with a
    NULL outcome while every caller believed it had recorded one.
    """
    result = record_outcome("ahx-test-task-that-was-never-decided", "resolved")

    assert result["status"] == "no_decision_row"
    assert result["rows"] == 0
    assert result["task_id"] == "ahx-test-task-that-was-never-decided"


def test_record_outcome_reports_success_when_a_decision_row_exists(harness_db):
    task_id = "ahx-test-paired-task"
    record_decision(task_id=task_id, reflex="ahx_test", decision="promote", confidence=0.9)

    result = record_outcome(task_id, "resolved")

    assert result["status"] == "recorded"
    assert result["rows"] >= 1

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT actual_outcome FROM harness_eval WHERE task_id = %s", (task_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["actual_outcome"] == "resolved"


def test_record_outcome_never_raises_on_a_bad_outcome_value(harness_db):
    """Telemetry is fire-and-forget — callers wrap it and must keep working."""
    result = record_outcome("ahx-test-unknown-task", "not-a-real-outcome")
    assert result["status"] in {"no_decision_row", "recorded", "unknown", "error"}


def test_off_vocabulary_outcome_is_warned_about(harness_db):
    """An outcome the metrics cannot count must not pass unremarked.

    icdev_logger sets propagate=False, so caplog (which listens on root) never
    sees these records — attach to the module's own logger instead.
    """
    import logging

    from tools.genesis.harness import eval_harness

    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    eval_harness.LOG.addHandler(handler)
    try:
        record_outcome("ahx-test-vocab-task", "definitely_not_valid")
    finally:
        eval_harness.LOG.removeHandler(handler)

    assert any("off-vocabulary" in m for m in captured), captured


# ---------------------------------------------------------------------------
# The PR-flow terminal transition must close the loop (ahx-eval-02, ahx-vv-01)
# ---------------------------------------------------------------------------

def test_pr_watcher_done_records_the_harness_outcome(tmp_path, monkeypatch):
    """The primary build path completes through pr_watcher, not _move_task.

    Under the PR flow the kanban reflex deliberately does NOT mark a task done —
    the work is not finished until the PR merges, and pr_watcher owns that edge.
    It wrote the status and nothing else, so every task completing this way
    recorded a codegen decision at dispatch and then never an outcome. That is
    why 60 of 65 live codegen rows were unresolved, and why every row that DID
    have an outcome carried 'failed' (the only path still routed through
    _move_task) and not one carried 'resolved'.

    Without the fix this test leaves actual_outcome NULL.
    """
    import sqlite3

    db_path = tmp_path / "icdev.db"
    sqlite3.connect(str(db_path)).executescript(
        _HARNESS_EVAL_DDL
        + """
        CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, status TEXT, updated_at TEXT);
        CREATE TABLE kanban_status_transitions (
            id TEXT, task_id TEXT, from_status TEXT, to_status TEXT,
            actor TEXT, reason TEXT, recorded_at TEXT
        );
        INSERT INTO kanban_tasks (id, status) VALUES ('ahx-proof-01', 'pr_opened');
        """
    )
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.ci.pr_watcher import _set_task_status

    # The kanban reflex records the codegen decision when the build subprocess ends.
    record_decision(task_id="ahx-proof-01", reflex="codegen", decision="done", confidence=0.6)

    def _unresolved() -> int:
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM harness_eval WHERE actual_outcome IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()

    assert _unresolved() == 1, "decision should start life unresolved"

    # The PR merges.
    _set_task_status(get_connection, "ahx-proof-01", "done", reason="PR merged")

    assert _unresolved() == 0, (
        "pr_watcher completed a task without recording a harness outcome — the "
        "loop is open again"
    )

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT actual_outcome FROM harness_eval WHERE task_id = 'ahx-proof-01'"
        ).fetchone()
    finally:
        conn.close()
    assert row["actual_outcome"] == "resolved"


# ---------------------------------------------------------------------------
# One outcome vocabulary across all writers and readers (ahx-eval-02)
# ---------------------------------------------------------------------------

def test_sampler_cli_outcomes_map_into_the_canonical_vocabulary():
    """The sampler asks a human 'correct?' but must store the shared words.

    Writing the raw CLI word meant a correctly-labelled sample was counted by
    no consumer as a success — it scored as a miscalibration instead.
    """
    sampler = importlib.import_module("tools.genesis.reflexes.confidence_sampler")

    assert set(sampler._CLI_OUTCOME_TO_CANONICAL) == {"correct", "incorrect"}
    assert set(sampler._CLI_OUTCOME_TO_CANONICAL.values()) <= VALID_OUTCOMES
    # 'correct' must land on an outcome the metrics actually count as a success.
    assert sampler._CLI_OUTCOME_TO_CANONICAL["correct"] in SUCCESS_OUTCOMES
    assert sampler._CLI_OUTCOME_TO_CANONICAL["incorrect"] not in SUCCESS_OUTCOMES


def test_calibration_report_scores_sampled_rows_with_the_harness_vocabulary():
    """Sampled rows are read straight out of harness_eval.

    They were previously scored against kanban_verifications' {'passed'}, which
    no harness row ever contains.
    """
    cal = importlib.import_module("tools.genesis.harness.calibration_report")

    assert cal.HARNESS_SUCCESS == SUCCESS_OUTCOMES
    assert cal.HARNESS_SUCCESS != cal.VERIFICATION_SUCCESS


# ---------------------------------------------------------------------------
# Documented commands must resolve (ahx-doc-01)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_capability_catalogue_documents_no_phantom_tools():
    """context/capabilities/*.yaml is published surface.

    tools/pulse/engine/capability_scanner.py feeds `article_hooks` from these
    files into generated content, so an unbacked CLI claim here is publishable.
    """
    import re

    import yaml

    gate = yaml.safe_load(
        (REPO_ROOT / "args" / "doc_command_gate.yaml").read_text(encoding="utf-8")
    ) or {}
    grandfathered = set(gate.get("grandfathered") or {})

    unresolved: list[str] = []
    for path in sorted((REPO_ROOT / "context" / "capabilities").glob("*.yaml")):
        for match in re.finditer(r'"python ([^"]+)"', path.read_text(encoding="utf-8")):
            token = re.match(r"(?:-m\s+)?([\w./-]+)", match.group(1))
            if not token:
                continue
            raw = token.group(1)
            rel = raw if raw.endswith(".py") else raw.replace(".", "/") + ".py"
            if not rel.startswith("tools/"):
                continue
            if not (REPO_ROOT / rel).exists() and rel not in grandfathered:
                unresolved.append(f"{path.name} -> {rel}")

    assert not unresolved, (
        "capability YAML documents commands that do not exist and are not "
        f"grandfathered: {unresolved}"
    )


def test_doc_command_gate_covers_the_capability_catalogue():
    checker = importlib.import_module("tools.workflow.coherence_checker")
    docs, _ = checker._load_doc_command_config()
    expanded = checker._expand_doc_entries(docs)
    assert any("context/capabilities/" in d for d in expanded), (
        "capability YAMLs are outside the doc-command gate — that is how the "
        "phantom harness tools survived"
    )


def test_doc_entry_globs_expand_and_literals_pass_through():
    checker = importlib.import_module("tools.workflow.coherence_checker")
    out = checker._expand_doc_entries(["CLAUDE.md", "context/capabilities/*.yaml"])
    assert "CLAUDE.md" in out
    assert "context/capabilities/harness.yaml" in out
    assert len(out) == len(set(out)), "expansion must not duplicate entries"


# ---------------------------------------------------------------------------
# Memory path must be derived, not hardcoded (ahx-path-01)
# ---------------------------------------------------------------------------

def test_memory_path_slug_is_derived_from_the_checkout_path():
    from tools.memory.claude_memory_path import project_slug

    assert project_slug(Path(r"C:\ai\icdev")) == "C--ai-icdev"
    assert project_slug(Path("/opt/icdev")) == "opt-icdev"
    # The old literal was 'C--AI-ICDev' and only ever matched by virtue of
    # Windows comparing paths case-insensitively.
    assert project_slug(Path(r"C:\other\checkout")) != "C--ai-icdev"


def test_memory_path_honours_the_env_override(monkeypatch, tmp_path):
    from tools.memory import claude_memory_path

    monkeypatch.setenv(claude_memory_path.ENV_OVERRIDE, str(tmp_path))
    assert claude_memory_path.claude_memory_dir() == tmp_path


#: Files allowed to mention the literal — they document or seed it.
_SLUG_LITERAL_EXEMPT = {"claude_memory_path.py", "seed_ahx_arr_clx.py"}


def _grep_repo(needle: str, pathspecs: list[str]) -> list[str] | None:
    """Fast repo search via git grep. Returns None if git is unusable."""
    import subprocess

    try:
        proc = subprocess.run(
            # --untracked matters: without it git grep sees only tracked files,
            # so a newly-added module reintroducing the literal would slip past
            # this guard until the moment it was committed.
            ["git", "grep", "--fixed-strings", "--files-with-matches", "--untracked",
             needle, "--", *pathspecs],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # 0 = matches found, 1 = none found; anything else means git could not answer.
    if proc.returncode not in (0, 1):
        return None
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def test_no_module_hardcodes_the_project_slug():
    """Regression guard: the literal must not come back.

    Uses ``git grep`` rather than reading every file — the naive walk over
    ``tools/`` plus ``icdev/tools/`` is 7k files and ~104MB, which took about
    12s against a 30s pytest timeout and made this test a flake waiting to
    happen.
    """
    needle = "projects/C--AI-ICDev"
    hits = _grep_repo(needle, ["tools/**/*.py", "icdev/tools/**/*.py"])

    if hits is None:  # git unavailable — fall back to the direct walk
        hits = []
        for root in ("tools", "icdev/tools"):
            base = REPO_ROOT / root
            if not base.is_dir():
                continue
            for py in base.rglob("*.py"):
                try:
                    if needle in py.read_text(encoding="utf-8", errors="replace"):
                        hits.append(py.relative_to(REPO_ROOT).as_posix())
                except OSError:
                    continue

    offenders = [h for h in hits if Path(h).name not in _SLUG_LITERAL_EXEMPT]
    assert not offenders, f"hardcoded Claude memory slug found in: {offenders}"


# ---------------------------------------------------------------------------
# Self-heal rate limits have one home (ahx-heal-01)
# ---------------------------------------------------------------------------

def test_self_heal_rate_limits_come_from_the_constitution():
    from tools.knowledge.self_heal_analyzer import _heal_rate_limits

    limits = _heal_rate_limits()
    assert set(limits) >= {
        "confidence_threshold",
        "escalation_threshold",
        "max_heal_attempts_per_pattern_per_hour",
        "max_heal_attempts_global_per_hour",
    }


def test_self_heal_posture_is_unchanged_by_the_consolidation():
    """Consolidating the limits must not loosen or tighten the safety posture.

    The per-pattern (3/hr) and global (5/hr) caps are different scopes, not a
    contradiction; both are preserved exactly as they were hardcoded.
    """
    from tools.knowledge import self_heal_analyzer as sha

    assert sha.CONFIDENCE_THRESHOLD == 0.7
    assert sha.ESCALATION_THRESHOLD == 0.3
    assert sha.MAX_HEAL_ATTEMPTS == 3
    assert sha._heal_rate_limits()["max_heal_attempts_global_per_hour"] == 5


def test_rate_limits_fall_back_rather_than_disabling_themselves(monkeypatch):
    """A malformed config must not silently remove the caps."""
    from tools.knowledge import self_heal_analyzer as sha

    monkeypatch.setattr(sha, "BASE_DIR", Path(os.devnull).parent / "nonexistent")
    limits = sha._heal_rate_limits()
    assert limits["confidence_threshold"] == 0.7
    assert limits["max_heal_attempts_global_per_hour"] == 5


# ---------------------------------------------------------------------------
# harness_eval schema is reachable from migrations (ahx-eval-03)
# ---------------------------------------------------------------------------

def test_harness_eval_has_a_numbered_migration():
    migrations = REPO_ROOT / "tools" / "db" / "migrations"
    hits = [
        p.name
        for p in migrations.glob("*.sql")
        if "harness_eval" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits, (
        "harness_eval existed only in pg_consolidated.sql and tests/conftest.py; "
        "a database built from migrations alone would never get the table"
    )


@pytest.mark.parametrize(
    "column",
    ["id", "task_id", "reflex", "decision", "confidence",
     "metadata_json", "actual_outcome", "resolved_at", "created_at"],
)
def test_migration_matches_the_consolidated_schema(column):
    migration = REPO_ROOT / "tools" / "db" / "migrations" / "302_harness_eval_table.sql"
    assert column in migration.read_text(encoding="utf-8")
