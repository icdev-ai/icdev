# CUI // SP-CTI
"""Lesson-backed refinement evidence tests (exa-refine-04).

The bug these guard against is not a crash. `agent_improvement_artifacts` had an
`evidence_traces` column that held a bare list of opaque trace ids (or the
literal `'[]'` from NOVA SELA), while 14,765 `lesson_learned` rows sat in
`memory_entries` recording exactly WHY every one of those tasks ended the way it
did. A human reviewing a proposed refinement had nothing to review, and a
proposal motivated by nothing was indistinguishable from one motivated by a
recurring systemic failure.

So these tests assert the JOIN and the GATE against persisted values:
  * the lesson rows for a proposal's trajectory land in `evidence_traces`,
  * a proposal with no lesson rows is persisted with a non-'pending' status —
    which is what keeps it away from GEPA and from a human review queue,
  * the review surfaces render the evidence.

A test that only asserts `collect_evidence()` returns a dict passes against all
of that being broken.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
_MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    type TEXT DEFAULT 'event',
    importance INTEGER DEFAULT 5,
    created_at TEXT DEFAULT (datetime('now')),
    source TEXT DEFAULT 'manual',
    classification TEXT DEFAULT 'CUI'
)
"""


@pytest.fixture
def evidence_conn(tmp_path, monkeypatch):
    """Real StorageConnection over temp SQLite with production NOVA DDL.

    `get_connection` rather than bare sqlite3 so the ``%s -> ?`` translation the
    production SQL depends on stays in the loop.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)

    from tools.db.storage import get_connection
    from tools.nova.db.init_db import init_nova_tables

    conn = get_connection(db_path=str(tmp_path / "evidence.db"))
    assert init_nova_tables(conn)["status"] == "ok"
    conn.execute(_MEMORY_DDL)
    conn.commit()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _write_lesson(conn, task_id, pattern="verification_fail", **over):
    payload = {
        "task_id": task_id,
        "task_title": over.get("task_title", f"Title for {task_id}"),
        "outcome": over.get("outcome", "failure"),
        "pattern": pattern,
        "category": over.get("category", "Verification failure"),
        "failure_count": over.get("failure_count", 2),
        "last_failure_reason": over.get("last_failure_reason", "bandit found 5 new issues"),
        "transitions_count": 3,
        "recurrence_score": over.get("recurrence_score", 0.4),
        "is_systemic": over.get("is_systemic", True),
        "recommendation": over.get("recommendation", "Run the gate before reporting done"),
        "timestamp": "2026-08-12T00:00:00+00:00",
    }
    conn.execute(
        "INSERT INTO memory_entries (content, type, importance, created_at, source) "
        "VALUES (%s, %s, %s, %s, %s)",
        (json.dumps(payload, sort_keys=True), "lesson_learned", 8,
         "2026-08-12T00:00:00+00:00", "auto"),
    )
    conn.commit()


def _traces(*task_ids):
    return [
        {"trace_id": f"trace-{t}", "task_id": t, "task_type": "build",
         "skill_used": "icdev-build", "outcome": "failure", "lesson_pattern": ""}
        for t in task_ids
    ]


# ---------------------------------------------------------------------------
# The join: lesson rows reach the bundle
# ---------------------------------------------------------------------------
def test_lessons_for_task_ids_returns_the_matching_rows(evidence_conn):
    _write_lesson(evidence_conn, "exa-refine-04")
    _write_lesson(evidence_conn, "exa-audit-02", pattern="phantom_completion")
    _write_lesson(evidence_conn, "unrelated-99")

    from tools.workflow.refinement_evidence import lessons_for_task_ids

    rows = lessons_for_task_ids(
        ["exa-refine-04", "exa-audit-02"], days=3650, conn=evidence_conn
    )

    assert {r["task_id"] for r in rows} == {"exa-refine-04", "exa-audit-02"}
    assert {r["pattern"] for r in rows} == {"verification_fail", "phantom_completion"}
    # The row carries the reviewable content, not just an id.
    hit = next(r for r in rows if r["task_id"] == "exa-refine-04")
    assert hit["last_failure_reason"] == "bandit found 5 new issues"
    assert hit["recommendation"] == "Run the gate before reporting done"
    assert hit["memory_entry_id"]


def test_loose_like_does_not_admit_a_substring_collision(evidence_conn):
    """`exa-refine-04` must not match a lesson written for `exa-refine-041`."""
    _write_lesson(evidence_conn, "exa-refine-041")

    from tools.workflow.refinement_evidence import lessons_for_task_ids

    assert lessons_for_task_ids(["exa-refine-04"], days=3650, conn=evidence_conn) == []


def test_collect_evidence_carries_lessons_and_recurrence(evidence_conn, monkeypatch):
    _write_lesson(evidence_conn, "exa-refine-04")
    _write_lesson(evidence_conn, "exa-audit-02")

    import tools.workflow.refinement_evidence as re_mod

    # get_recurrence opens its own connection; point it at the fixture DB.
    monkeypatch.setattr(
        re_mod, "_recurrence_for",
        lambda pattern, ids, tt, days: {
            "pattern": pattern, "prefix": "exa", "total_similar": 2,
            "total_in_window": 5, "recurrence_score": 0.4,
        },
    )

    bundle = re_mod.collect_evidence(
        task_type="build",
        skill_used="icdev-build",
        traces=_traces("exa-refine-04", "exa-audit-02"),
        days=3650,
        conn=evidence_conn,
    )

    assert bundle["schema"] == re_mod.EVIDENCE_SCHEMA
    assert bundle["lesson_count"] == 2
    assert bundle["recurrence_score"] == 0.4
    assert bundle["dominant_pattern"] == "verification_fail"
    assert bundle["systemic_count"] == 2
    assert bundle["trace_ids"] == ["trace-exa-refine-04", "trace-exa-audit-02"]
    assert bundle["patterns"][0]["lesson_count"] == 2
    # Round-trips through the TEXT column unchanged.
    assert re_mod.parse_evidence(json.dumps(bundle))["lesson_count"] == 2


def test_collect_evidence_is_empty_not_raising_when_there_are_no_lessons(evidence_conn):
    from tools.workflow.refinement_evidence import collect_evidence

    bundle = collect_evidence(
        task_type="build", traces=_traces("never-seen-01"),
        days=3650, conn=evidence_conn,
    )
    assert bundle["lesson_count"] == 0
    assert bundle["patterns"] == []
    assert bundle["recurrence_score"] == 0.0


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def test_evidence_with_no_lessons_is_rejected(monkeypatch):
    import tools.workflow.refinement_evidence as re_mod

    monkeypatch.setattr(re_mod, "load_config", lambda: dict(re_mod._DEFAULT_CONFIG))
    verdict = re_mod.evaluate_evidence(re_mod._empty_bundle())

    assert verdict["supported"] is False
    assert verdict["gate_passed"] is False
    assert "no_supporting_evidence" in verdict["reason"]
    assert verdict["rejected_status"] != "pending"


def test_evidence_with_lessons_passes_the_gate(monkeypatch):
    import tools.workflow.refinement_evidence as re_mod

    monkeypatch.setattr(re_mod, "load_config", lambda: dict(re_mod._DEFAULT_CONFIG))
    bundle = re_mod._empty_bundle()
    bundle.update({"lesson_count": 3, "recurrence_score": 0.42,
                   "dominant_pattern": "verification_fail"})

    verdict = re_mod.evaluate_evidence(bundle)
    assert verdict["supported"] is True
    assert verdict["gate_passed"] is True
    assert verdict["lesson_count"] == 3


def test_recurrence_floor_rejects_below_threshold(monkeypatch):
    import tools.workflow.refinement_evidence as re_mod

    cfg = dict(re_mod._DEFAULT_CONFIG)
    cfg["min_recurrence_score"] = 0.75
    monkeypatch.setattr(re_mod, "load_config", lambda: cfg)

    bundle = re_mod._empty_bundle()
    bundle.update({"lesson_count": 3, "recurrence_score": 0.2})
    verdict = re_mod.evaluate_evidence(bundle)

    assert verdict["gate_passed"] is False
    assert "recurrence_below_floor" in verdict["reason"]


def test_gate_can_be_turned_off_but_still_records_the_verdict(monkeypatch):
    import tools.workflow.refinement_evidence as re_mod

    cfg = dict(re_mod._DEFAULT_CONFIG)
    cfg["require_evidence"] = False
    monkeypatch.setattr(re_mod, "load_config", lambda: cfg)

    verdict = re_mod.evaluate_evidence(re_mod._empty_bundle())
    assert verdict["supported"] is True     # not blocked
    assert verdict["gate_passed"] is False  # but the truth is still recorded
    assert verdict["enforced"] is False


# ---------------------------------------------------------------------------
# Tolerant reader — the three shapes already in the column
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expect_note",
    [
        (json.dumps(["trace-a", "trace-b"]), "legacy"),
        (json.dumps({"source_pattern": "x", "generator": "hermes"}), "provenance only"),
        ("[]", ""),
        ("not json at all", "unparseable"),
        (None, ""),
    ],
)
def test_parse_evidence_reads_every_legacy_shape(raw, expect_note):
    from tools.workflow.refinement_evidence import parse_evidence

    bundle = parse_evidence(raw)
    assert bundle["lesson_count"] == 0
    assert isinstance(bundle["trace_ids"], list)
    if expect_note:
        assert expect_note in bundle["note"]


def test_parse_evidence_keeps_legacy_trace_ids_visible():
    from tools.workflow.refinement_evidence import parse_evidence

    bundle = parse_evidence(json.dumps(["trace-a", "trace-b"]))
    assert bundle["trace_ids"] == ["trace-a", "trace-b"]


# ---------------------------------------------------------------------------
# Review surface
# ---------------------------------------------------------------------------
def test_summary_and_markdown_display_the_evidence():
    from tools.workflow.refinement_evidence import (
        _empty_bundle,
        evidence_summary,
        render_evidence_markdown,
    )

    bundle = _empty_bundle()
    bundle.update({
        "lesson_count": 2,
        "recurrence_score": 0.42,
        "dominant_pattern": "verification_fail",
        "systemic_count": 2,
        "patterns": [{"pattern": "verification_fail", "lesson_count": 2,
                      "recurrence_score": 0.42, "is_systemic": True}],
        "lessons": [{"task_id": "exa-refine-04", "pattern": "verification_fail",
                     "outcome": "failure", "last_failure_reason": "bandit found 5 new issues"}],
    })

    summary = evidence_summary(bundle)
    assert "2 lesson_learned row(s)" in summary
    assert "verification_fail" in summary
    assert "0.42" in summary

    md = render_evidence_markdown(bundle)
    assert "verification_fail" in md
    assert "exa-refine-04" in md
    assert "bandit found 5 new issues" in md


def test_summary_says_so_when_there_is_no_evidence():
    from tools.workflow.refinement_evidence import evidence_summary

    assert "no lesson evidence" in evidence_summary(json.dumps(["trace-a"])).lower()


# ---------------------------------------------------------------------------
# End-to-end through the writer: the persisted row is what matters
# ---------------------------------------------------------------------------
def _persisted(conn, artifact_id):
    row = conn.execute(
        "SELECT status, evidence_traces FROM agent_improvement_artifacts "
        "WHERE artifact_id = %s",
        (artifact_id,),
    ).fetchone()
    assert row is not None, f"{artifact_id} was not persisted"
    d = dict(row) if hasattr(row, "keys") else {"status": row[0], "evidence_traces": row[1]}
    return d["status"], json.loads(d["evidence_traces"])


def test_reflexion_artifact_persists_lesson_evidence(evidence_conn, monkeypatch):
    """The whole point: the persisted proposal carries its motivating lessons."""
    _write_lesson(evidence_conn, "exa-refine-04")
    _write_lesson(evidence_conn, "exa-audit-02")

    monkeypatch.setenv("ICDEV_HARNESS_COLEARN", "true")
    import importlib

    import tools.workflow.reflexion_agent as ra
    ra = importlib.reload(ra)

    traces = _traces("exa-refine-04", "exa-audit-02", "exa-bench-01")
    monkeypatch.setattr(ra, "get_traces_for_task_type", lambda tt, limit=20: traces)
    monkeypatch.setattr(ra, "_call_llm", lambda prompt, skill: "Add a retry and verify the schema.")
    monkeypatch.setattr(ra, "_conn", lambda: evidence_conn)
    monkeypatch.setattr(ra, "_ensure_tables", lambda conn: None)

    import tools.workflow.refinement_evidence as re_mod
    monkeypatch.setattr(
        re_mod, "_recurrence_for",
        lambda pattern, ids, tt, days: {
            "pattern": pattern, "prefix": "exa", "total_similar": 2,
            "total_in_window": 5, "recurrence_score": 0.4,
        },
    )
    monkeypatch.setattr(re_mod, "load_config", lambda: {**re_mod._DEFAULT_CONFIG, "window_days": 3650})
    # collect_evidence opens its own connection for the lesson lookup.
    real_lookup = re_mod.lessons_for_task_ids
    monkeypatch.setattr(
        re_mod, "lessons_for_task_ids",
        lambda ids, days=7, conn=None: real_lookup(ids, days=3650, conn=evidence_conn),
    )

    result = ra.generate_improvement_artifact("build", skill_used="icdev-build")

    assert result.get("evidence_rejected") is False, result
    assert result["status"] == "pending"
    status, evidence = _persisted(evidence_conn, result["artifact_id"])
    assert status == "pending"
    assert evidence["lesson_count"] == 2
    assert {lesson["task_id"] for lesson in evidence["lessons"]} == {
        "exa-refine-04", "exa-audit-02"
    }
    assert evidence["recurrence_score"] == 0.4
    assert evidence["trace_ids"][0] == "trace-exa-refine-04"


def test_reflexion_artifact_with_no_lessons_never_reaches_a_reviewer(
    evidence_conn, monkeypatch
):
    """No lesson rows → persisted, but NOT as 'pending'.

    'pending' is what GEPA's `_get_pending_artifacts` and the review queues
    select on, so a non-'pending' status IS the rejection.
    """
    monkeypatch.setenv("ICDEV_HARNESS_COLEARN", "true")
    import importlib

    import tools.workflow.reflexion_agent as ra
    ra = importlib.reload(ra)

    traces = _traces("no-lesson-01", "no-lesson-02", "no-lesson-03")
    monkeypatch.setattr(ra, "get_traces_for_task_type", lambda tt, limit=20: traces)
    monkeypatch.setattr(ra, "_call_llm", lambda prompt, skill: "Add a retry and verify.")
    monkeypatch.setattr(ra, "_conn", lambda: evidence_conn)
    monkeypatch.setattr(ra, "_ensure_tables", lambda conn: None)

    import tools.workflow.refinement_evidence as re_mod
    monkeypatch.setattr(
        re_mod, "lessons_for_task_ids", lambda ids, days=7, conn=None: []
    )

    result = ra.generate_improvement_artifact("build", skill_used="icdev-build")

    assert result["evidence_rejected"] is True
    assert result["status"] == "rejected_no_evidence"
    status, evidence = _persisted(evidence_conn, result["artifact_id"])
    assert status == "rejected_no_evidence"
    assert evidence["lesson_count"] == 0

    # And it is genuinely invisible to the promoter.
    from tools.skills.gepa_optimizer import _get_pending_artifacts
    assert all(
        a["artifact_id"] != result["artifact_id"]
        for a in _get_pending_artifacts(evidence_conn)
    )


def test_gepa_review_card_shows_the_evidence(monkeypatch):
    """GEPA's kanban card is the human review surface — it must carry the WHY."""
    seeded: list[dict] = []
    import tools.kanban.task_factory as tf
    monkeypatch.setattr(tf, "create_tasks", lambda tasks: seeded.extend(tasks))

    from tools.skills.gepa_optimizer import _seed_review_card
    from tools.workflow.refinement_evidence import _empty_bundle

    bundle = _empty_bundle()
    bundle.update({
        "lesson_count": 1, "recurrence_score": 0.42,
        "dominant_pattern": "verification_fail", "systemic_count": 1,
        "patterns": [{"pattern": "verification_fail", "lesson_count": 1,
                      "recurrence_score": 0.42, "is_systemic": True}],
        "lessons": [{"task_id": "exa-refine-04", "pattern": "verification_fail",
                     "outcome": "failure",
                     "last_failure_reason": "bandit found 5 new issues"}],
    })

    _seed_review_card("icdev-build", "/skills/icdev-build/SKILL.md", bundle)

    assert seeded, "no review card was created"
    body = seeded[0]["description"]
    assert "verification_fail" in body
    assert "exa-refine-04" in body
    assert "bandit found 5 new issues" in body
    assert "0.42" in body


def test_proposal_queue_surfaces_the_evidence(evidence_conn):
    """`skills_lifecycle.list_proposals` is a human review surface too."""
    bundle = {
        "schema": "refinement_evidence/v1", "lesson_count": 2,
        "recurrence_score": 0.42, "dominant_pattern": "phantom_completion",
        "systemic_count": 2, "lessons": [], "patterns": [], "trace_ids": ["t1"],
    }
    evidence_conn.execute(
        "INSERT INTO agent_improvement_artifacts "
        "(artifact_id, task_type, skill_used, generation_n, improvement_text, "
        " composite_score, baseline_score, evidence_traces, status) "
        "VALUES (%s, 'skill_generation', %s, 1, %s, 0.0, 0.0, %s, 'pending')",
        ("prop-1", "icdev-auto-thing", "# Skill spec", json.dumps(bundle)),
    )
    evidence_conn.commit()

    from tools.agent_runtime.skills_lifecycle import list_proposals

    proposals = list_proposals(conn=evidence_conn)
    prop = next(p for p in proposals if p["artifact_id"] == "prop-1")

    assert prop["evidence"]["lesson_count"] == 2
    assert prop["evidence"]["dominant_pattern"] == "phantom_completion"
    assert "phantom_completion" in prop["evidence_summary"]
    # ...and the pre-existing provenance contract is untouched.
    assert prop["spec"] == "# Skill spec"
    assert isinstance(prop["provenance"], dict)


def test_proposal_queue_is_honest_about_a_provenance_only_row(evidence_conn):
    """NOVA's generator writes a provenance dict — that is not lesson evidence."""
    evidence_conn.execute(
        "INSERT INTO agent_improvement_artifacts "
        "(artifact_id, task_type, skill_used, generation_n, improvement_text, "
        " composite_score, baseline_score, evidence_traces, status) "
        "VALUES (%s, 'skill_generation', %s, 1, %s, 0.0, 0.0, %s, 'pending')",
        ("prop-legacy", "icdev-auto-old", "# Old spec",
         json.dumps({"source_pattern": "run the thing", "generator": "hermes"})),
    )
    evidence_conn.commit()

    from tools.agent_runtime.skills_lifecycle import list_proposals

    prop = next(
        p for p in list_proposals(conn=evidence_conn) if p["artifact_id"] == "prop-legacy"
    )
    assert prop["evidence"]["lesson_count"] == 0
    assert "no lesson evidence" in prop["evidence_summary"].lower()
    assert prop["provenance"]["source_pattern"] == "run the thing"


def test_gepa_reads_lesson_counts_off_a_bundled_artifact(evidence_conn):
    evidence_conn.execute(
        "INSERT INTO agent_improvement_artifacts "
        "(artifact_id, task_type, skill_used, generation_n, improvement_text, "
        " composite_score, baseline_score, evidence_traces, status) "
        "VALUES (%s, %s, %s, 1, %s, %s, %s, %s, 'pending')",
        ("art-bundled", "build", "icdev-build",
         "Add a retry around the flaky migration step and verify the schema.",
         0.9, 0.4,
         json.dumps({"schema": "refinement_evidence/v1", "lesson_count": 3,
                     "trace_ids": ["t1", "t2"], "recurrence_score": 0.4,
                     "lessons": [], "patterns": [], "dominant_pattern": "x",
                     "systemic_count": 0})),
    )
    evidence_conn.commit()

    from tools.skills.gepa_optimizer import _get_pending_artifacts

    found = {a["artifact_id"]: a for a in _get_pending_artifacts(evidence_conn)}
    assert "art-bundled" in found
    assert found["art-bundled"]["n_lessons"] == 3
    assert found["art-bundled"]["n_traces"] == 2  # not len(dict-keys)
