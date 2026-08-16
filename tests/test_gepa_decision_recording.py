# CUI // SP-CTI
"""GEPA decision recording — rem-cap-01.

`skill_optimizer` was the last of the five tracked known-inert capabilities at
literal zero. The reason was NOT the one recorded in
`args/capability_consumption.yaml`, which blamed the >= 0.05 delta filter
against artifacts with `composite_score == baseline_score`. exa-refine-03 had
already fixed the WRITERS; what it could not fix was the 132 rows already
queued, because nothing rescores an artifact after insert.

MEASURED on the live board 2026-08-16 — 162 rows, and `genesis_reflex_state`
showing the gepa_optimizer reflex at 7 runs / 7 successes / last_metric_value
0.0 every time:

    status                 n    composite  baseline  skill_used
    pending              132        1.0       1.0    ''
    rejected_no_evidence  30       0.75       1.0    populated

So the caller existed, was registered, was enabled, and ran. The defect was that
GEPA had exactly ONE outcome it could write — `status='applied'`. An artifact it
evaluated and declined stayed `'pending'` forever, with two consequences:

  1. The queue could only grow, and those 132 rows were permanently
     unselectable, so `capability_consumption`'s own alarm condition (queue
     full, zero rows satisfying the selection predicate = "structurally cannot
     ever act") was stuck on regardless of whether the flywheel worked.
  2. "GEPA ran and correctly declined everything" was indistinguishable from
     "GEPA never ran" — the declared-but-unconsumed shape the awareness engine
     exists to catch, inside the module that catches it.

These tests therefore assert the RECORDED DECISION and what the probe counts,
never mere execution. A test that only asserts `run()` returns a dict passed
against the whole defect.
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

    `init_nova_tables` rather than a hand-written CREATE TABLE so the fixture
    schema cannot drift from production — which is also what proves the
    `gepa_decision`/`gepa_decided_at` columns really landed in the DDL and not
    only in the migration.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)

    from tools.db.storage import get_connection
    from tools.nova.db.init_db import init_nova_tables

    db_path = str(tmp_path / "nova.db")
    conn = get_connection(db_path=db_path)
    result = init_nova_tables(conn)
    assert result["status"] == "ok", result
    # `run()` closes the connection it was handed, so the fixture records the
    # path and the `gepa` fixture opens a fresh one per call — the production
    # shape, where get_connection() returns a new connection each time.
    conn.icdev_test_db_path = db_path
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _insert(conn, artifact_id, skill_used, composite, baseline,
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


def _decision(conn, artifact_id):
    row = conn.execute(
        "SELECT gepa_decision, gepa_decided_at FROM agent_improvement_artifacts "
        "WHERE artifact_id = %s",
        (artifact_id,),
    ).fetchone()
    return dict(row) if hasattr(row, "keys") else dict(
        zip(["gepa_decision", "gepa_decided_at"], row))


@pytest.fixture
def gepa(monkeypatch, nova_conn, tmp_path):
    """gepa_optimizer wired to the fixture DB and a temp skills root.

    `run()` resolves `from tools.db.storage import get_connection` against the
    sys.modules entry for the SHIM. `import tools.db.storage as st` binds the
    icdev copy instead, so patching that object leaves the call site untouched
    and the test silently hits the live board — patch via import_module.
    """
    mod = importlib.import_module("tools.skills.gepa_optimizer")
    root = tmp_path / "skills"
    (root / "icdev-build").mkdir(parents=True)
    (root / "icdev-build" / "SKILL.md").write_text(
        "---\nname: icdev-build\n---\n# Build\n" + "body line\n" * 40, encoding="utf-8")
    monkeypatch.setattr(mod, "_SKILLS_ROOT", root)
    monkeypatch.delenv("ICDEV_GEPA_FROZEN", raising=False)
    storage = importlib.import_module("tools.db.storage")
    real_get_connection = storage.get_connection
    db_path = nova_conn.icdev_test_db_path
    monkeypatch.setattr(
        storage, "get_connection",
        lambda *a, **k: real_get_connection(db_path=db_path),
    )
    return mod


# ---------------------------------------------------------------------------
# The defect: a decline left no record
# ---------------------------------------------------------------------------

def test_declined_artifact_gets_a_recorded_decision(gepa, nova_conn):
    """The headline. A zero-delta artifact is declined WITH a recorded reason.

    This is the live shape: composite == baseline == 1.0, which no rescoring
    path can ever change. Before rem-cap-01 this row was silently dropped by
    the selector and GEPA's cycle left no trace of having considered it.
    """
    _insert(nova_conn, "impr-flat", "icdev-build", composite=1.0, baseline=1.0)

    summary = gepa.run(dry_run=False)

    assert summary["applied"] == []
    assert [d["decision"] for d in summary["declined"]] == [gepa.DECISION_NO_DELTA]

    recorded = _decision(nova_conn, "impr-flat")
    assert recorded["gepa_decision"] == gepa.DECISION_NO_DELTA
    assert recorded["gepa_decided_at"], "a decision with no timestamp is not measurable"


def test_below_floor_composite_is_declined_as_low_score_not_no_delta(gepa, nova_conn):
    """The two score declines are different diagnoses and must not blur.

    `declined_low_score` says the candidate itself is weak; `declined_no_delta`
    says it is no better than what is already there. They send a reader to
    different fixes.
    """
    _insert(nova_conn, "impr-low", "icdev-build", composite=0.50, baseline=0.10)

    gepa.run(dry_run=False)

    assert _decision(nova_conn, "impr-low")["gepa_decision"] == gepa.DECISION_LOW_SCORE


def test_terminal_decline_is_not_re_evaluated_next_cycle(gepa, nova_conn):
    """The queue must actually DRAIN, or the backlog is immortal again.

    Nothing rescores an artifact after insert, so a score-based decline can
    never come out differently. Re-examining it every 24h would both waste the
    cycle and keep manufacturing consumption events for work already settled.
    """
    _insert(nova_conn, "impr-flat", "icdev-build", composite=1.0, baseline=1.0)

    first = gepa.run(dry_run=False)
    assert len(first["declined"]) == 1

    second = gepa.run(dry_run=False)
    assert second["declined"] == [], "a terminally declined artifact was re-examined"


def test_missing_skill_file_decline_is_retryable_but_blank_skill_is_terminal(gepa, nova_conn):
    """Blank `skill_used` can never resolve; a named-but-absent SKILL.md might.

    Collapsing the two would either strand an artifact whose skill file someone
    is about to add, or re-examine 68 unresolvable rows forever.
    """
    _insert(nova_conn, "impr-blank", "", composite=0.90, baseline=0.10)
    _insert(nova_conn, "impr-absent", "icdev-not-here", composite=0.90, baseline=0.10)

    gepa.run(dry_run=False)

    assert _decision(nova_conn, "impr-blank")["gepa_decision"] == \
        gepa.DECISION_UNMAPPABLE_SKILL
    assert _decision(nova_conn, "impr-absent")["gepa_decision"] == \
        gepa.DECISION_SKILL_FILE_MISSING

    assert gepa.DECISION_UNMAPPABLE_SKILL in gepa.TERMINAL_DECISIONS
    assert gepa.DECISION_SKILL_FILE_MISSING not in gepa.TERMINAL_DECISIONS

    # The retryable one comes back next cycle; the terminal one does not.
    second = gepa.run(dry_run=False)
    assert [d["artifact_id"] for d in second["declined"]] == ["impr-absent"]


def test_dry_run_records_nothing(gepa, nova_conn):
    """A preview that writes to the database is not a preview."""
    _insert(nova_conn, "impr-flat", "icdev-build", composite=1.0, baseline=1.0)

    gepa.run(dry_run=True)

    assert _decision(nova_conn, "impr-flat")["gepa_decision"] is None


def test_applied_artifact_records_an_applied_decision(gepa, nova_conn, monkeypatch):
    """An apply is a decision too, and lands in the same column the declines do.

    Otherwise the one outcome that always mattered would be the one outcome the
    consumption probe could not see.
    """
    monkeypatch.setattr(
        gepa, "_generate_patch",
        lambda *a, **k: "---\nname: icdev-build\n---\n# Build\n" + "body line\n" * 45,
    )
    monkeypatch.setattr(gepa, "_seed_review_card", lambda *a, **k: None)
    _insert(nova_conn, "impr-good", "icdev-build", composite=0.85, baseline=0.40)

    summary = gepa.run(dry_run=False)

    assert [a["artifact_id"] for a in summary["applied"]] == ["impr-good"]
    recorded = _decision(nova_conn, "impr-good")
    assert recorded["gepa_decision"] == gepa.DECISION_APPLIED
    assert recorded["gepa_decided_at"]


def test_selection_predicate_is_unchanged(gepa, nova_conn):
    """Recording declines must not have loosened what GEPA will PATCH.

    The regression that matters: a decision vocabulary is worthless if it was
    bought by letting weak candidates through to the write path.
    """
    _insert(nova_conn, "impr-big", "icdev-build", composite=0.90, baseline=0.20)
    _insert(nova_conn, "impr-flat", "icdev-build", composite=0.80, baseline=0.78)
    _insert(nova_conn, "impr-low", "icdev-build", composite=0.30, baseline=0.01)
    _insert(nova_conn, "impr-applied", "icdev-build", composite=0.90, baseline=0.10,
            status="applied")

    assert [a["artifact_id"] for a in gepa._get_pending_artifacts(nova_conn)] == \
        ["impr-big"]


# ---------------------------------------------------------------------------
# What capability_consumption counts
# ---------------------------------------------------------------------------

def test_a_recorded_decline_counts_as_consumption(gepa, nova_conn):
    """Consumption is a GEPA DECISION, applied or declined — not an apply alone.

    Counting applies only is what made a correct decline read as "never ran".
    The two neighbouring classes already count this way: mcp_tool_authorization
    counts a verdict rather than a denial, audit_chain a chained row rather
    than a tamper finding.
    """
    capcon = importlib.import_module("tools.awareness.capability_consumption")
    _insert(nova_conn, "impr-flat", "icdev-build", composite=1.0, baseline=1.0)
    gepa.run(dry_run=False)

    report = capcon.collect(
        conn=nova_conn,
        config={
            "window_days": 30, "inert_threshold": 0, "max_listed_units": 100,
            "classes": {"skill_optimizer": {"enabled": True}},
        },
        only=["skill_optimizer"],
    )
    result = report["classes"][0]

    assert result["telemetry_available"] is True
    assert result["declared"] == 1
    assert result["consumed"] == 1
    assert result["inert"] == 0
    assert result["events"] == 1
    assert result["extra"]["declined_lifetime"] == 1
    assert result["extra"]["applied_lifetime"] == 0
    # The alarm that used to be stuck on: nothing is left undecided.
    assert result["extra"]["pending_undecided_artifacts"] == 0


def test_an_upstream_evidence_rejection_is_not_a_gepa_declaration(gepa, nova_conn):
    """GEPA selects on status='pending' and is never shown a rejected artifact.

    30 of the live board's 162 rows are `rejected_no_evidence` — refused by the
    Reflexion agent's evidence gate before GEPA ever sees them. Counting their
    skills as capabilities GEPA failed to consume blames GEPA for an upstream
    decision and leaves the class permanently un-greenable however well GEPA
    works.
    """
    capcon = importlib.import_module("tools.awareness.capability_consumption")
    _insert(nova_conn, "impr-flat", "icdev-build", composite=1.0, baseline=1.0)
    _insert(nova_conn, "impr-rej", "icdev-test", composite=0.75, baseline=1.0,
            status="rejected_no_evidence")
    gepa.run(dry_run=False)

    report = capcon.collect(
        conn=nova_conn,
        config={
            "window_days": 30, "inert_threshold": 0, "max_listed_units": 100,
            "classes": {"skill_optimizer": {"enabled": True}},
        },
        only=["skill_optimizer"],
    )
    result = report["classes"][0]

    assert result["declared"] == 1, result["inert_units"]
    assert result["inert"] == 0
    assert result["extra"]["upstream_rejected_artifacts"] == 1


def test_a_database_without_the_decision_column_is_unmeasurable_not_zero(tmp_path,
                                                                        monkeypatch):
    """An install that has not run the migration must not report a clean zero.

    A misleading zero is the exact failure this module exists to prevent, and
    it would be a bitter one to ship here.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    capcon = importlib.import_module("tools.awareness.capability_consumption")
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(tmp_path / "old.db"))
    # The pre-migration shape: no gepa_decision / gepa_decided_at.
    conn.execute(
        "CREATE TABLE agent_improvement_artifacts ("
        " artifact_id TEXT PRIMARY KEY, skill_used TEXT, composite_score REAL,"
        " baseline_score REAL, status TEXT, applied_count INTEGER DEFAULT 0,"
        " applied_at TEXT)"
    )
    conn.commit()

    report = capcon.collect(
        conn=conn,
        config={
            "window_days": 30, "inert_threshold": 0, "max_listed_units": 100,
            "classes": {"skill_optimizer": {"enabled": True}},
        },
        only=["skill_optimizer"],
    )
    result = report["classes"][0]

    assert result["telemetry_available"] is False
    assert "gepa_decided_at" in (result["unmeasured_reason"] or "")
    conn.close()


# ---------------------------------------------------------------------------
# The declaration itself
# ---------------------------------------------------------------------------

def test_gepa_has_a_scheduled_caller():
    """(a), not (b): the capability is declared AND wired to a real consumer.

    rem-cap-01 could have been answered by retiring GEPA. It was not, because a
    caller already exists on a 24h cadence — so the honest fix was to make its
    work recordable, not to invent a consumer or remove the declaration.
    """
    import pathlib

    import yaml

    from tools.genesis.daemon import REFLEX_NAMES
    from tools.genesis.reflex_registry import REGISTRY

    assert "gepa_optimizer" in REFLEX_NAMES
    assert any(entry.name == "gepa_optimizer" for entry in REGISTRY)

    root = pathlib.Path(
        importlib.import_module("tools.skills.gepa_optimizer").__file__
    ).resolve().parent.parent.parent
    cfg = yaml.safe_load(
        (root / "args" / "genesis_config.yaml").read_text(encoding="utf-8")) or {}
    assert (cfg.get("reflexes") or {}).get("gepa_optimizer"), \
        "gepa_optimizer is not scheduled in args/genesis_config.yaml"


def test_decision_vocabulary_is_mirrored_in_the_icdev_package():
    """`icdev.tools.*` is the canonical namespace; a drifted mirror is a silent
    behaviour split between the shim and the package."""
    shim = importlib.import_module("tools.skills.gepa_optimizer")
    # Deliberately NOT guarded by a skip: a gated test that skips is an
    # unmeasured test, and "the mirror is missing" is the finding, not a reason
    # to report nothing.
    pkg = importlib.import_module("icdev.tools.skills.gepa_optimizer")

    assert pkg.TERMINAL_DECISIONS == shim.TERMINAL_DECISIONS
    for name in ("DECISION_APPLIED", "DECISION_NO_DELTA", "DECISION_LOW_SCORE",
                 "DECISION_UNMAPPABLE_SKILL", "DECISION_SKILL_FILE_MISSING"):
        assert getattr(pkg, name) == getattr(shim, name), name
