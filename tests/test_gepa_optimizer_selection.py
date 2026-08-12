# CUI // SP-CTI
"""GEPA optimizer selection tests (exa-refine-03).

The bug these guard against was NOT "the function crashes" — `gepa_optimizer.run()`
ran happily as a Genesis reflex and reported success while being structurally
incapable of ever patching a skill:

  1. Every one of the 137 rows in `agent_improvement_artifacts` had
     ``composite_score == baseline_score`` because `reflexion_agent` wrote the
     baseline into both columns, so the delta was 0.0 against a
     ``_MIN_SCORE_DELTA`` of 0.05 and `_get_pending_artifacts` filtered all of
     them out, permanently.
  2. 68 of those rows had ``skill_used = ''``, so `_find_skill_file('')`
     returned None and the artifact was skipped even when it passed scoring.
  3. `_find_skill_file` prefixed unconditionally, so an ``icdev-<slug>`` value
     (what NOVA's skill_generator writes) was looked up as ``icdev-icdev-<slug>``.

So these tests assert SELECTION and PERSISTED VALUES, never mere execution. A
test that only asserts `run()` returns a dict passes against all three bugs.
"""
from __future__ import annotations

import importlib
import json

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def nova_conn(tmp_path, monkeypatch):
    """A real StorageConnection over a temp SQLite DB with production NOVA DDL.

    Deliberately `get_connection` rather than bare `sqlite3.connect` so the
    ``%s -> ?`` translation the production SQL relies on stays in the loop, and
    deliberately `init_nova_tables` rather than a hand-written CREATE TABLE so
    the fixture schema cannot drift from production.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)

    from tools.db.storage import get_connection
    from tools.nova.db.init_db import init_nova_tables

    conn = get_connection(db_path=str(tmp_path / "nova.db"))
    result = init_nova_tables(conn)
    assert result["status"] == "ok", result
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _stub_lesson_evidence(monkeypatch, task_ids):
    """Stand in for the lesson_learned rows the evidence bundle joins against.

    exa-refine-04 made a proposal carry the lessons that motivated it and
    rejects one that has none, so a fixture with no lesson rows now (correctly)
    produces a rejected artifact. `nova_conn` holds NOVA's DDL only — the
    lessons live in `memory_entries` — so the lookup is stubbed rather than
    dragging the whole memory schema into this file's fixture.
    """
    import tools.workflow.refinement_evidence as re_mod

    lessons = [
        {"task_id": t, "pattern": "timeout_quarantine", "outcome": "failure",
         "category": "Timeout quarantine", "is_systemic": True,
         "last_failure_reason": "timeout on dispatch",
         "recommendation": "add timeout handling", "memory_entry_id": f"m-{t}"}
        for t in task_ids
    ]
    monkeypatch.setattr(
        re_mod, "lessons_for_task_ids", lambda ids, days=7, conn=None: lessons
    )
    monkeypatch.setattr(
        re_mod, "_recurrence_for",
        lambda pattern, ids, tt, days: {
            "pattern": pattern, "prefix": "t", "total_similar": len(task_ids),
            "total_in_window": 10, "recurrence_score": 0.4,
        },
    )


def _insert_artifact(conn, artifact_id, skill_used, composite, baseline,
                     status="pending", task_type="build"):
    conn.execute(
        "INSERT INTO agent_improvement_artifacts "
        "(artifact_id, task_type, skill_used, generation_n, improvement_text, "
        " composite_score, baseline_score, evidence_traces, status) "
        "VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s)",
        (artifact_id, task_type, skill_used,
         "Add a retry around the flaky migration step and verify the schema.",
         composite, baseline, json.dumps(["trace-a", "trace-b", "trace-c"]), status),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Bug 1 — degenerate fitness: selection, not execution
# ---------------------------------------------------------------------------

def test_wellformed_artifact_is_selected(nova_conn):
    """A well-formed artifact with a real delta comes BACK from the selector."""
    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    _insert_artifact(nova_conn, "impr-good", "icdev-build", composite=0.82, baseline=0.50)

    pending = gepa._get_pending_artifacts(nova_conn)

    assert [a["artifact_id"] for a in pending] == ["impr-good"]
    selected = pending[0]
    assert selected["skill_used"] == "icdev-build"
    assert selected["composite_score"] - selected["baseline_score"] >= gepa._MIN_SCORE_DELTA
    # evidence_traces is parsed into a usable count for the patch prompt
    assert selected["n_traces"] == 3


def test_degenerate_composite_equals_baseline_is_rejected(nova_conn):
    """The historical shape — composite == baseline — must still be filtered out.

    This is the regression guard: if a writer ever starts copying the baseline
    into composite_score again, this test keeps passing while
    test_wellformed_artifact_is_selected keeps proving the selector works.
    """
    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    _insert_artifact(nova_conn, "impr-degenerate", "icdev-build",
                     composite=0.75, baseline=0.75)

    assert gepa._get_pending_artifacts(nova_conn) == []


def test_selection_ranks_and_filters_a_mixed_population(nova_conn):
    """Below-floor composite, sub-threshold delta, and non-pending are all excluded."""
    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    # Selected, larger delta first.
    _insert_artifact(nova_conn, "impr-big", "icdev-build", composite=0.90, baseline=0.20)
    _insert_artifact(nova_conn, "impr-small", "icdev-test", composite=0.70, baseline=0.60)
    # Rejected: composite below _MIN_COMPOSITE_SCORE even though the delta is big.
    _insert_artifact(nova_conn, "impr-lowscore", "icdev-build", composite=0.30, baseline=0.01)
    # Rejected: delta below _MIN_SCORE_DELTA.
    _insert_artifact(nova_conn, "impr-flat", "icdev-build", composite=0.80, baseline=0.78)
    # Rejected: already applied.
    _insert_artifact(nova_conn, "impr-applied", "icdev-build", composite=0.90,
                     baseline=0.10, status="applied")

    pending = gepa._get_pending_artifacts(nova_conn)

    assert [a["artifact_id"] for a in pending] == ["impr-big", "impr-small"]


def test_run_applies_a_selected_artifact(nova_conn, tmp_path, monkeypatch):
    """End-to-end: a selected artifact actually reaches the write path.

    Runs in dry_run so no SKILL.md is rewritten, but `applied` being non-empty
    is the proof the optimizer got past selection AND skill-file resolution —
    the two gates that used to make every cycle a no-op.
    """
    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    skill_dir = tmp_path / "skills" / "icdev-build"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: icdev-build\n---\n# Build\n", encoding="utf-8")
    monkeypatch.setattr(gepa, "_SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.delenv("ICDEV_GEPA_FROZEN", raising=False)
    # `run()` resolves `from tools.db.storage import get_connection` against the
    # sys.modules entry for the SHIM. `import tools.db.storage as st` binds the
    # icdev copy instead, so patching that object leaves the call site untouched
    # and the test silently hits the live DB — patch via import_module.
    monkeypatch.setattr(
        importlib.import_module("tools.db.storage"),
        "get_connection", lambda *a, **k: nova_conn,
    )

    _insert_artifact(nova_conn, "impr-e2e", "icdev-build", composite=0.85, baseline=0.40)

    summary = gepa.run(dry_run=True)

    assert [a["artifact_id"] for a in summary["applied"]] == ["impr-e2e"]
    assert summary["skipped"] == []


# ---------------------------------------------------------------------------
# Bug 2 — skill_used populated at write time
# ---------------------------------------------------------------------------

def test_reflexion_artifact_persists_skill_and_a_real_delta(nova_conn, monkeypatch):
    """`generate_improvement_artifact` must write a non-empty skill_used and
    a composite_score that is not just a copy of the baseline."""
    ra = importlib.import_module("tools.workflow.reflexion_agent")
    monkeypatch.setenv("ICDEV_HARNESS_COLEARN", "true")
    monkeypatch.setattr(ra, "_COLEARN_ENABLED", True)
    monkeypatch.setattr(ra, "_conn", lambda: nova_conn)
    monkeypatch.setattr(ra, "_ensure_tables", lambda conn: None)
    monkeypatch.setattr(
        ra, "_call_llm",
        lambda prompt, skill: (
            "## Root Cause\nThe migration step is flaky.\n\n"
            "## Proposed Improvements\n1. Add a retry and verify the schema for icdev-build.\n"
        ),
    )
    traces = [
        {"trace_id": f"trace-{i}", "task_id": f"t{i}", "task_type": "build",
         "skill_used": "icdev-build", "outcome": outcome,
         "lesson_pattern": "flaky_migration", "improvement_notes": "retry needed"}
        for i, outcome in enumerate(["success", "failure", "success", "partial", "failure"])
    ]
    monkeypatch.setattr(ra, "get_traces_for_task_type", lambda tt, limit=20: traces)
    _stub_lesson_evidence(monkeypatch, ["t1", "t4"])

    # NOTE: skill_used is deliberately NOT passed — run_batch_reflexion cannot
    # supply it, which is exactly how 68 rows landed with skill_used = ''.
    result = ra.generate_improvement_artifact("build", dry_run=False)

    assert result["skill_used"] == "icdev-build"

    row = nova_conn.execute(
        "SELECT skill_used, composite_score, baseline_score "
        "FROM agent_improvement_artifacts WHERE artifact_id = %s",
        (result["artifact_id"],),
    ).fetchone()
    row = dict(row) if hasattr(row, "keys") else dict(
        zip(["skill_used", "composite_score", "baseline_score"], row))

    assert row["skill_used"] == "icdev-build"
    assert row["composite_score"] != row["baseline_score"]

    # ...and the artifact it just wrote is one GEPA can select.
    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    assert result["artifact_id"] in {
        a["artifact_id"] for a in gepa._get_pending_artifacts(nova_conn)
    }


def test_explicit_skill_used_is_not_overwritten_by_trace_inference(nova_conn, monkeypatch):
    ra = importlib.import_module("tools.workflow.reflexion_agent")
    monkeypatch.setattr(ra, "_COLEARN_ENABLED", True)
    monkeypatch.setattr(ra, "_conn", lambda: nova_conn)
    monkeypatch.setattr(ra, "_ensure_tables", lambda conn: None)
    monkeypatch.setattr(ra, "_call_llm", lambda prompt, skill: "Add a retry step.")
    traces = [
        {"trace_id": f"trace-{i}", "task_id": f"t{i}", "task_type": "build",
         "skill_used": "icdev-test", "outcome": "success"}
        for i in range(4)
    ]
    monkeypatch.setattr(ra, "get_traces_for_task_type", lambda tt, limit=20: traces)

    result = ra.generate_improvement_artifact("build", skill_used="icdev-build", dry_run=True)

    assert result["skill_used"] == "icdev-build"


def test_baseline_credits_partial_outcomes(nova_conn, monkeypatch):
    """A bare successes/len tally scores `partial` as an outright failure."""
    ra = importlib.import_module("tools.workflow.reflexion_agent")
    all_partial = [{"trace_id": "t", "outcome": "partial"} for _ in range(4)]
    all_failed = [{"trace_id": "t", "outcome": "failure"} for _ in range(4)]

    assert ra._compute_score(all_partial) > ra._compute_score(all_failed)
    assert ra._compute_score([{"outcome": "success"}]) == 1.0
    assert ra._compute_score([]) == 0.0


# ---------------------------------------------------------------------------
# Shared fitness rubric — tools.workflow.improvement_fitness
# ---------------------------------------------------------------------------

def test_score_improvement_is_deterministic_and_bounded():
    """A promotion decision must be reproducible offline, so the rubric is
    deterministic and every component stays inside [0, 1]."""
    from tools.workflow.improvement_fitness import score_improvement

    text = "Add a retry around the flaky migration and verify the schema for icdev-build."
    first = score_improvement(text, "icdev-build")
    assert score_improvement(text, "icdev-build") == first
    for key in ("correctness", "procedure", "conciseness", "composite_score"):
        assert 0.0 <= first[key] <= 1.0, (key, first[key])


def test_score_improvement_prefers_specific_over_vague():
    """A vague candidate must not clear _MIN_COMPOSITE_SCORE ahead of a concrete one."""
    from tools.workflow.improvement_fitness import score_improvement

    specific = score_improvement(
        "Add a retry and verify the schema before the icdev-build migration step.",
        "icdev-build",
    )
    vague = score_improvement("improve better enhance fix update change modify", "icdev-build")
    assert specific["composite_score"] > vague["composite_score"]


def test_score_improvement_matches_sela_scorer():
    """SELA's _score_mutation must stay a thin delegate — GEPA compares the
    composite_score both writers produce against one shared floor."""
    from tools.genesis.reflexes.evolution import _score_mutation
    from tools.workflow.improvement_fitness import (
        DEFAULT_FITNESS_WEIGHTS, score_improvement,
    )

    text = "Validate the manifest and retry the failed check for icdev-test."
    assert _score_mutation(text, "icdev-test", DEFAULT_FITNESS_WEIGHTS) == \
        score_improvement(text, "icdev-test", DEFAULT_FITNESS_WEIGHTS)


def test_score_traces_is_outcome_weighted():
    from tools.workflow.improvement_fitness import score_traces

    assert score_traces([]) == 0.0
    assert score_traces([{"outcome": "success"}, {"outcome": "failure"}]) == 0.5
    assert score_traces([{"outcome": "partial"}, {"outcome": "partial"}]) == 0.5
    # Unknown outcomes score 0 rather than raising.
    assert score_traces([{"outcome": "who-knows"}]) == 0.0
    assert score_traces([{}]) == 0.0


def test_dominant_skill_picks_the_modal_non_empty_value():
    from tools.workflow.improvement_fitness import dominant_skill

    assert dominant_skill([]) == ""
    assert dominant_skill([{"skill_used": ""}, {"skill_used": "  "}]) == ""
    assert dominant_skill([{}, {"skill_used": "icdev-build"}]) == "icdev-build"
    assert dominant_skill([
        {"skill_used": "icdev-test"},
        {"skill_used": "icdev-build"},
        {"skill_used": "icdev-build"},
    ]) == "icdev-build"


# ---------------------------------------------------------------------------
# Bug 3 — double-prefix skill lookup
# ---------------------------------------------------------------------------

@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    root = tmp_path / "skills"
    for name in ("icdev-build", "icdev-code-review"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    monkeypatch.setattr(gepa, "_SKILLS_ROOT", root)
    return gepa


@pytest.mark.parametrize("skill_used,expected", [
    ("icdev-build", "icdev-build"),          # NOVA skill_generator's format
    ("build", "icdev-build"),                # bare slug
    ("icdev-icdev-build", "icdev-build"),    # already double-prefixed by an old run
    ("code_review", "icdev-code-review"),    # underscores normalised to hyphens
    ("icdev-code_review", "icdev-code-review"),
    ("  icdev-build  ", "icdev-build"),      # whitespace
])
def test_find_skill_file_resolves_every_writer_format(skills_root, skill_used, expected):
    found = skills_root._find_skill_file(skill_used)
    assert found is not None, f"{skill_used!r} did not resolve"
    assert found.parent.name == expected


@pytest.mark.parametrize("skill_used", ["", "   ", "no-such-skill", "icdev-no-such-skill"])
def test_find_skill_file_returns_none_for_unresolvable(skills_root, skill_used):
    assert skills_root._find_skill_file(skill_used) is None


def test_find_skill_file_never_double_prefixes():
    """The candidate list must not contain an `icdev-icdev-` name for a
    value that is already prefixed — that lookup can only ever miss."""
    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    candidates = gepa._skill_dir_candidates("icdev-build")
    assert "icdev-build" in candidates
    assert not any(c.startswith("icdev-icdev-") for c in candidates), candidates


# ---------------------------------------------------------------------------
# Model routing — no hardcoded model id
# ---------------------------------------------------------------------------

def test_patch_generation_has_no_pinned_model_id():
    """The patch call must route by llm_function, not a literal model id."""
    import ast
    import pathlib

    gepa = importlib.import_module("tools.skills.gepa_optimizer")
    for path in {pathlib.Path(gepa.__file__).resolve(),
                 pathlib.Path(gepa.__file__).resolve().parent.parent.parent
                 / "icdev" / "tools" / "skills" / "gepa_optimizer.py"}:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pins = [
            kw.value.value
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg in ("model", "model_id")
            and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
        ]
        assert pins == [], f"{path} pins model id(s): {pins}"


def test_gepa_skill_patch_llm_function_is_declared():
    """An undeclared llm_function silently falls back to routing.default."""
    import pathlib

    import yaml

    root = pathlib.Path(
        importlib.import_module("tools.skills.gepa_optimizer").__file__
    ).resolve().parent.parent.parent
    for rel in ("args/llm_config.yaml",
                "icdev/args/llm_config.yaml",
                "icdev/data/args/llm_config.yaml"):
        path = root / rel
        if not path.exists():
            continue
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entry = (cfg.get("routing") or {}).get("gepa_skill_patch")
        assert entry, f"{rel} does not declare routing.gepa_skill_patch"
        assert entry.get("chain"), f"{rel} gepa_skill_patch has no chain"
