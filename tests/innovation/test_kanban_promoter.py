"""Tests for tools/innovation/kanban_promoter.py

Pins the four properties the promoter exists to guarantee:

  1. a gap verdict produces exactly one suggested task
  2. re-running produces none (stable id + idempotency_key)
  3. the per-run and per-subsystem caps are enforced AND logged on truncation
  4. nothing reaches 'backlog' — cards are always 'suggested'

The write path is exercised against a recorded ``create_tasks`` rather than a
live board, so the assertions are about the specs the promoter hands to
task_factory. task_factory's own dedup is covered by its own tests.
"""
from __future__ import annotations

import inspect
import logging
import re
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation import kanban_promoter as kp  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG = {
    "source_types": ["external_repo_scouting"],
    "triage_results": ["approved"],
    "min_innovation_score": 0.5,
    "max_per_run": 3,
    "max_per_subsystem": 2,
    "default_task_type": "research",
    "gap_verdicts": ["gap", "parity_with_named_gaps"],
    "benchmark_subsystems": {
        "developer_portal": {
            "section": 1,
            "verdict": "gap",
            "benchmarked_against": "Backstage",
            "icdev_surface": "args/component_registry.yaml",
            "categories": ["developer_experience", "dx"],
        },
        "agent_runtime": {
            "section": 3,
            "verdict": "parity_with_named_gaps",
            "categories": ["ai_tooling", "workflow"],
        },
        "observability": {
            "section": 2,
            "verdict": "ahead",
            "categories": ["observability"],
        },
        "rag_knowledge_graph": {
            "section": 5,
            "verdict": "parity",
            "categories": ["knowledge"],
        },
    },
    "priority_thresholds": {"high": 0.7, "medium": 0.5},
}


def _sig(sid, category, score=0.8, **extra):
    base = {
        "id": sid,
        "title": f"Finding {sid}",
        "category": category,
        "innovation_score": score,
        "triage_result": "approved",
        "source_type": "external_repo_scouting",
    }
    base.update(extra)
    return base


@pytest.fixture
def promoter_warnings():
    """Capture WARNINGs off the promoter's own logger.

    ``get_logger`` returns a logger with ``propagate=False`` and its own file
    handlers, so pytest's ``caplog`` (which listens on root) sees nothing.
    Attach directly to the logger the module actually writes to.
    """
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    kp.logger.addHandler(handler)
    try:
        yield records
    finally:
        kp.logger.removeHandler(handler)


@pytest.fixture
def recorded_create_tasks(monkeypatch):
    """Capture the specs handed to task_factory.create_tasks.

    Patched via the module object so the shim/canonical namespace split
    (tools.x vs icdev.tools.x) can't route the promoter to a different
    object than the one under test.
    """
    import importlib

    tf = importlib.import_module("tools.kanban.task_factory")
    calls: list[list[dict]] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()

    def _fake_create_tasks(specs):
        calls.append(list(specs))
        created = []
        for s in specs:
            # Mirror the real dedup: skip on id OR idempotency_key collision.
            if s["id"] in seen_ids or s.get("idempotency_key") in seen_keys:
                continue
            seen_ids.add(s["id"])
            if s.get("idempotency_key"):
                seen_keys.add(s["idempotency_key"])
            created.append(s["id"])
        return created

    monkeypatch.setattr(tf, "create_tasks", _fake_create_tasks)
    return calls


# ---------------------------------------------------------------------------
# Priority mapping
# ---------------------------------------------------------------------------


def test_priority_high_score():
    assert kp._priority_for_score(0.85, {"high": 0.7, "medium": 0.5}) == "high"


def test_priority_medium_score():
    assert kp._priority_for_score(0.65, {"high": 0.7, "medium": 0.5}) == "medium"


def test_priority_low_score():
    assert kp._priority_for_score(0.45, {"high": 0.7, "medium": 0.5}) == "low"


def test_priority_none_score():
    assert kp._priority_for_score(None, {"high": 0.7, "medium": 0.5}) == "low"


def test_priority_boundary_exactly_high():
    assert kp._priority_for_score(0.7, {"high": 0.7, "medium": 0.5}) == "high"


# ---------------------------------------------------------------------------
# Criterion 1 — a gap verdict produces exactly one suggested task
# ---------------------------------------------------------------------------


def test_gap_verdict_signal_is_eligible():
    gated = kp.classify_signals([_sig("sig-a", "developer_experience")], CONFIG)
    assert len(gated["eligible"]) == 1
    assert gated["eligible"][0]["_subsystem"] == "developer_portal"
    assert gated["eligible"][0]["_verdict"] == "gap"


def test_parity_with_named_gaps_counts_as_a_gap():
    gated = kp.classify_signals([_sig("sig-b", "ai_tooling")], CONFIG)
    assert len(gated["eligible"]) == 1
    assert gated["eligible"][0]["_verdict"] == "parity_with_named_gaps"


def test_ahead_subsystem_produces_no_work():
    """ICDEV is ahead on observability — a finding there is intel, not work."""
    gated = kp.classify_signals([_sig("sig-c", "observability")], CONFIG)
    assert gated["eligible"] == []
    assert gated["skipped_not_a_gap"][0]["verdict"] == "ahead"


def test_parity_subsystem_produces_no_work():
    gated = kp.classify_signals([_sig("sig-d", "knowledge")], CONFIG)
    assert gated["eligible"] == []
    assert gated["skipped_not_a_gap"][0]["verdict"] == "parity"


def test_unmapped_category_produces_no_work():
    """An unmapped category is skipped, not guessed into a subsystem."""
    gated = kp.classify_signals([_sig("sig-e", "performance")], CONFIG)
    assert gated["eligible"] == []
    assert gated["skipped_unmapped"] == ["sig-e"]


def test_metadata_subsystem_tag_overrides_category():
    """The watchlist routing tag wins over the coarse category map."""
    sig = _sig("sig-f", "observability", metadata='{"subsystem": "developer_portal"}')
    gated = kp.classify_signals([sig], CONFIG)
    assert len(gated["eligible"]) == 1
    assert gated["eligible"][0]["_subsystem"] == "developer_portal"


def test_unknown_metadata_tag_falls_back_to_category():
    sig = _sig("sig-g", "developer_experience", metadata='{"subsystem": "no_such_thing"}')
    gated = kp.classify_signals([sig], CONFIG)
    assert len(gated["eligible"]) == 1
    assert gated["eligible"][0]["_subsystem"] == "developer_portal"


def test_malformed_metadata_json_does_not_crash():
    sig = _sig("sig-h", "developer_experience", metadata="{not json")
    gated = kp.classify_signals([sig], CONFIG)
    assert len(gated["eligible"]) == 1


def test_one_gap_verdict_creates_exactly_one_task(recorded_create_tasks):
    gated = kp.classify_signals([_sig("sig-one", "developer_experience")], CONFIG)
    result = kp.promote_signals(gated["eligible"], CONFIG, dry_run=False)
    assert result["created"] == 1
    assert len(recorded_create_tasks[0]) == 1


# ---------------------------------------------------------------------------
# Criterion 2 — re-running produces none
# ---------------------------------------------------------------------------


def test_task_id_is_stable_across_calls():
    """A clock-seeded id would defeat task_factory's dedup entirely."""
    assert kp.stable_task_id("sig-xyz") == kp.stable_task_id("sig-xyz")
    assert kp.stable_task_id("sig-xyz") != kp.stable_task_id("sig-abc")


def test_idempotency_key_is_stable_and_signal_scoped():
    assert kp.idempotency_key("sig-1") == kp.idempotency_key("sig-1")
    assert kp.idempotency_key("sig-1") != kp.idempotency_key("sig-2")


def test_rerun_creates_nothing(recorded_create_tasks):
    gated = kp.classify_signals([_sig("sig-rerun", "developer_experience")], CONFIG)

    first = kp.promote_signals(gated["eligible"], CONFIG, dry_run=False)
    second = kp.promote_signals(gated["eligible"], CONFIG, dry_run=False)

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped_existing"] == 1


def test_spec_carries_idempotency_key_and_source_link(recorded_create_tasks):
    gated = kp.classify_signals([_sig("sig-k", "developer_experience")], CONFIG)
    kp.promote_signals(gated["eligible"], CONFIG, dry_run=False)
    spec = recorded_create_tasks[0][0]
    assert spec["idempotency_key"] == "innovation-promoter:sig-k"
    assert spec["source_prediction_id"] == "sig-k"
    assert spec["id"] == kp.stable_task_id("sig-k")


def test_dry_run_writes_nothing(recorded_create_tasks):
    gated = kp.classify_signals([_sig("sig-dry", "developer_experience")], CONFIG)
    result = kp.promote_signals(gated["eligible"], CONFIG, dry_run=True)
    assert result["created"] == 0
    assert result["would_create"] == 1
    assert result["dry_run"] is True
    assert recorded_create_tasks == []


# ---------------------------------------------------------------------------
# Criterion 3 — caps enforced and logged when they truncate
# ---------------------------------------------------------------------------


def test_per_subsystem_cap_enforced():
    sigs = [_sig(f"sig-{i}", "developer_experience", score=0.9 - i / 100)
            for i in range(5)]
    gated = kp.classify_signals(sigs, CONFIG)
    capped = kp.apply_caps(gated["eligible"], CONFIG)
    assert len(capped["kept"]) == 2                     # max_per_subsystem
    assert len(capped["dropped_by_subsystem_cap"]) == 3
    assert capped["truncated"] is True


def test_per_run_cap_enforced_across_subsystems():
    # 2 developer_portal + 2 agent_runtime = 4 eligible, run cap is 3
    sigs = [
        _sig("sig-a", "developer_experience", score=0.95),
        _sig("sig-b", "dx", score=0.90),
        _sig("sig-c", "ai_tooling", score=0.85),
        _sig("sig-d", "workflow", score=0.80),
    ]
    gated = kp.classify_signals(sigs, CONFIG)
    capped = kp.apply_caps(gated["eligible"], CONFIG)
    assert len(capped["kept"]) == 3                     # max_per_run
    assert len(capped["dropped_by_run_cap"]) == 1
    assert capped["dropped_by_run_cap"][0]["id"] == "sig-d"   # lowest score drops
    assert capped["truncated"] is True


def test_cap_keeps_highest_scoring():
    sigs = [
        _sig("sig-low", "developer_experience", score=0.55),
        _sig("sig-high", "developer_experience", score=0.95),
        _sig("sig-mid", "developer_experience", score=0.75),
    ]
    gated = kp.classify_signals(sigs, CONFIG)
    capped = kp.apply_caps(gated["eligible"], CONFIG)
    assert [s["id"] for s in capped["kept"]] == ["sig-high", "sig-mid"]


def test_no_truncation_flag_when_under_cap():
    gated = kp.classify_signals([_sig("sig-solo", "developer_experience")], CONFIG)
    capped = kp.apply_caps(gated["eligible"], CONFIG)
    assert capped["truncated"] is False
    assert capped["dropped_by_run_cap"] == []
    assert capped["dropped_by_subsystem_cap"] == []


def test_truncation_is_logged_not_silent(promoter_warnings):
    """A cap that drops findings silently is indistinguishable from a
    promoter that found nothing. Pin the WARNING."""
    sigs = [_sig(f"sig-{i}", "developer_experience", score=0.9) for i in range(5)]
    gated = kp.classify_signals(sigs, CONFIG)

    kp.apply_caps(gated["eligible"], CONFIG)

    assert any("CAP TRUNCATED" in m for m in promoter_warnings)
    assert any("per-subsystem cap" in m for m in promoter_warnings)


def test_run_cap_truncation_is_logged(promoter_warnings):
    sigs = [
        _sig("sig-a", "developer_experience", score=0.95),
        _sig("sig-b", "dx", score=0.90),
        _sig("sig-c", "ai_tooling", score=0.85),
        _sig("sig-d", "workflow", score=0.80),
    ]
    gated = kp.classify_signals(sigs, CONFIG)

    kp.apply_caps(gated["eligible"], CONFIG)

    assert any("per-run cap" in m for m in promoter_warnings)


def test_no_warning_when_caps_do_not_bite(promoter_warnings):
    """The WARNING must mean something — no truncation, no warning."""
    gated = kp.classify_signals([_sig("sig-quiet", "developer_experience")], CONFIG)
    kp.apply_caps(gated["eligible"], CONFIG)
    assert not any("CAP TRUNCATED" in m for m in promoter_warnings)


def test_dropped_findings_are_reported_by_id():
    """Truncation must be auditable — every drop accounted for."""
    sigs = [_sig(f"sig-{i}", "developer_experience", score=0.9) for i in range(5)]
    gated = kp.classify_signals(sigs, CONFIG)
    capped = kp.apply_caps(gated["eligible"], CONFIG)
    dropped = {d["id"] for d in capped["dropped_by_subsystem_cap"]}
    kept = {s["id"] for s in capped["kept"]}
    assert len(dropped | kept) == 5          # nothing vanished
    assert not (dropped & kept)              # and nothing double-counted


# ---------------------------------------------------------------------------
# Criterion 4 — nothing reaches backlog without confirmation
# ---------------------------------------------------------------------------


def test_built_task_is_always_suggested():
    task = kp.build_kanban_task(
        {"id": "sig-s", "title": "T", "innovation_score": 0.9,
         "_subsystem": "developer_portal", "_verdict": "gap"},
        CONFIG,
    )
    assert task["status"] == "suggested"


def test_promoter_refuses_to_write_backlog(monkeypatch, recorded_create_tasks):
    """Even if build_kanban_task is subverted, the write path refuses."""
    def _backlog_task(signal, config):
        return {
            "id": "task-x", "title": "T", "description": "",
            "task_type": "research", "priority": "high",
            "status": "backlog", "source_prediction_id": signal["id"],
            "idempotency_key": "k", "dispatch_source": "innovation_promoter",
            "acceptance_criteria": "",
        }

    monkeypatch.setattr(kp, "build_kanban_task", _backlog_task)
    with pytest.raises(ValueError, match="only create 'suggested'"):
        kp.promote_signals([_sig("sig-z", "developer_experience")], CONFIG, dry_run=False)
    assert recorded_create_tasks == []


def test_suggested_status_constant_is_suggested():
    assert kp.SUGGESTED_STATUS == "suggested"


# ---------------------------------------------------------------------------
# Scope — benchmark findings only (the 259-approved-CVE flood)
# ---------------------------------------------------------------------------


def test_shipped_config_scopes_to_benchmark_source_types():
    cfg = kp.load_config()
    assert "cve" not in cfg["source_types"]
    assert "cli_harmonization" not in cfg["source_types"]
    assert "external_repo_scouting" in cfg["source_types"]


def test_shipped_config_caps_are_bounded():
    cfg = kp.load_config()
    assert 0 < int(cfg["max_per_run"]) <= 10
    assert 0 < int(cfg["max_per_subsystem"]) <= int(cfg["max_per_run"])


def test_shipped_config_excludes_ahead_verdicts_from_gap_list():
    cfg = kp.load_config()
    assert "ahead" not in cfg["gap_verdicts"]
    assert "parity" not in cfg["gap_verdicts"]
    assert "gap" in cfg["gap_verdicts"]


def test_shipped_config_every_subsystem_has_a_verdict():
    cfg = kp.load_config()
    for key, spec in cfg["benchmark_subsystems"].items():
        assert spec.get("verdict"), f"{key} has no verdict"


# ---------------------------------------------------------------------------
# PROMOTION_CONTRACT_SQL — pinned to the YAML it transcribes
# ---------------------------------------------------------------------------
# The constant restates the verdict map in SQL for reviewability. That is a
# third copy (map doc -> YAML -> SQL), so these tests are what stop it drifting.


def _contract_verdict_triples() -> set[tuple[str, str, str]]:
    """Parse the benchmark_verdict VALUES rows out of the SQL constant."""
    block = kp.PROMOTION_CONTRACT_SQL.split("benchmark_verdict", 1)[1].split(")\nSELECT", 1)[0]
    rows = re.findall(
        r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(\d+)\s*,\s*'([^']+)'\s*\)", block
    )
    return {(cat, sub, verdict) for cat, sub, _section, verdict in rows}


def _yaml_verdict_triples(cfg: dict) -> set[tuple[str, str, str]]:
    return {
        (str(cat).strip().lower(), key, str(spec["verdict"]).strip().lower())
        for key, spec in cfg["benchmark_subsystems"].items()
        for cat in (spec.get("categories") or [])
    }


def test_contract_sql_verdict_map_matches_yaml():
    cfg = kp.load_config()
    sql_map, yaml_map = _contract_verdict_triples(), _yaml_verdict_triples(cfg)
    assert sql_map, "no VALUES rows parsed out of PROMOTION_CONTRACT_SQL"
    assert sql_map == yaml_map, (
        "PROMOTION_CONTRACT_SQL has drifted from args/innovation_promoter.yaml; "
        f"only in SQL: {sorted(sql_map - yaml_map)}; only in YAML: {sorted(yaml_map - sql_map)}"
    )


def test_contract_sql_sections_match_yaml():
    cfg = kp.load_config()
    block = kp.PROMOTION_CONTRACT_SQL.split("benchmark_verdict", 1)[1].split(")\nSELECT", 1)[0]
    for _cat, subsystem, section, _verdict in re.findall(
        r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(\d+)\s*,\s*'([^']+)'\s*\)", block
    ):
        assert int(section) == int(cfg["benchmark_subsystems"][subsystem]["section"])


def test_contract_sql_gap_filter_matches_yaml_gap_verdicts():
    cfg = kp.load_config()
    clause = re.search(r"v\.verdict IN \(([^)]*)\)", kp.PROMOTION_CONTRACT_SQL)
    assert clause, "PROMOTION_CONTRACT_SQL has no gap-verdict filter"
    assert set(re.findall(r"'([^']+)'", clause.group(1))) == {
        str(v).strip().lower() for v in cfg["gap_verdicts"]
    }


def test_contract_sql_scope_matches_yaml():
    cfg = kp.load_config()
    for source_type in cfg["source_types"]:
        assert f"'{source_type}'" in kp.PROMOTION_CONTRACT_SQL
    for triage in cfg["triage_results"]:
        assert f"'{triage}'" in kp.PROMOTION_CONTRACT_SQL
    assert f">= {cfg['min_innovation_score']}" in kp.PROMOTION_CONTRACT_SQL
    assert f">= {cfg['priority_thresholds']['high']} THEN 'high'" in kp.PROMOTION_CONTRACT_SQL
    assert f">= {cfg['priority_thresholds']['medium']} THEN 'medium'" in kp.PROMOTION_CONTRACT_SQL
    assert f"'{cfg['default_task_type']}'" in kp.PROMOTION_CONTRACT_SQL


def test_contract_sql_derivations_match_python():
    assert kp.stable_task_id("sig-x").startswith("task-innov-")
    assert "'task-innov-' || substr(encode(sha256(s.id::bytea), 'hex'), 1, 10)" \
        in kp.PROMOTION_CONTRACT_SQL
    assert kp.idempotency_key("sig-x") == "innovation-promoter:sig-x"
    assert "'innovation-promoter:' || s.id" in kp.PROMOTION_CONTRACT_SQL


def test_contract_sql_only_writes_suggested_status():
    assert f"'{kp.SUGGESTED_STATUS}'" in kp.PROMOTION_CONTRACT_SQL
    assert "'backlog'" not in kp.PROMOTION_CONTRACT_SQL


def test_contract_sql_is_read_only():
    upper = kp.PROMOTION_CONTRACT_SQL.upper()
    # \b, because s.created_at contains the substring CREATE.
    for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "CREATE", "ALTER", "MERGE"):
        assert not re.search(rf"\b{verb}\b", upper), \
            f"PROMOTION_CONTRACT_SQL is a diagnostic; found {verb!r}"
    # Strip quoted literals first — acceptance_criteria legitimately contains a ';'.
    assert ";" not in re.sub(r"'[^']*'", "''", upper), "must remain a single statement"


def test_contract_sql_reports_rather_than_filters_already_promoted():
    # The runtime anti-join lives in find_promotable_signals. Here it is a
    # column, so "0 candidates" is distinguishable from "0 findings".
    assert "AS already_promoted" in kp.PROMOTION_CONTRACT_SQL
    assert "NOT IN" not in kp.PROMOTION_CONTRACT_SQL.upper()
    assert "NOT IN (\n              SELECT source_prediction_id" in \
        inspect.getsource(kp.find_promotable_signals)


def test_default_config_shape():
    cfg = kp.DEFAULT_CONFIG
    assert "triage_results" in cfg
    assert "min_innovation_score" in cfg
    assert "max_per_run" in cfg
    assert "max_per_subsystem" in cfg
    assert cfg["priority_thresholds"]["high"] > cfg["priority_thresholds"]["medium"]


def test_load_config_returns_dict():
    cfg = kp.load_config()
    assert isinstance(cfg, dict)
    assert "priority_thresholds" in cfg


# ---------------------------------------------------------------------------
# Task content
# ---------------------------------------------------------------------------


def test_build_task_records_the_verdict_that_justified_it():
    task = kp.build_kanban_task(
        {"id": "sig-v", "title": "T", "innovation_score": 0.9,
         "_subsystem": "developer_portal", "_verdict": "gap"},
        CONFIG,
    )
    assert "developer_portal" in task["description"]
    assert "gap" in task["description"]
    assert "Backstage" in task["description"]


def test_build_task_title_truncated():
    task = kp.build_kanban_task(
        {"id": "sig-longone", "title": "x" * 200, "innovation_score": 0.5}, CONFIG
    )
    assert len(task["title"]) <= 120


def test_build_task_includes_spec_when_present():
    task = kp.build_kanban_task(
        {"id": "sig-1", "title": "T", "innovation_score": 0.7,
         "spec_content": "This is a detailed solution spec.",
         "estimated_effort": "2 days"},
        CONFIG,
    )
    assert "Solution spec" in task["description"]
    assert "2 days" in task["description"]


def test_build_task_missing_optional_fields():
    """Signals with only id/title shouldn't crash."""
    task = kp.build_kanban_task(
        {"id": "sig-minimal", "title": None, "innovation_score": None}, CONFIG
    )
    assert task["priority"] == "low"
    assert "sig-mini" in task["title"]


def test_promote_empty_list_creates_nothing(recorded_create_tasks):
    result = kp.promote_signals([], CONFIG, dry_run=False)
    assert result["created"] == 0
    assert recorded_create_tasks == []


# ---------------------------------------------------------------------------
# Real write path — no mock between the promoter and the database
# ---------------------------------------------------------------------------
#
# Everything above asserts the specs handed to a recorded create_tasks. That
# cannot catch a spec whose columns the real kanban_tasks does not have: a
# stubbed write reports success against a schema that would reject it. These
# two tests run the actual task_factory against a real SQLite file whose
# schema is built by the real init_kanban_tables(), so a column the promoter
# invents — or one a migration removes — fails here instead of in production.


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    """Point storage at a throwaway SQLite file for one test.

    monkeypatch.setenv so the pointer cannot leak into later tests or the
    session: a stray ICDEV_DB_PATH silently redirects every subsequent
    get_connection() at a dead tmpdir.
    """
    db = tmp_path / "kanban_proof.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    return db


def _rows(db):
    import sqlite3

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT id, status, dispatch_source, idempotency_key, "
            "source_prediction_id FROM kanban_tasks"
        )]
    finally:
        con.close()


def test_real_write_creates_exactly_one_suggested_task(real_db):
    """Criteria 1 and 4, through the real factory and the real schema."""
    sig = _sig("sig-real-0001", "developer_experience", score=0.82)
    eligible = kp.classify_signals([sig], CONFIG)["eligible"]

    result = kp.promote_signals(eligible, CONFIG, dry_run=False)
    assert result["created"] == 1

    rows = _rows(real_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "suggested"
    assert rows[0]["dispatch_source"] == "innovation_promoter"
    assert rows[0]["source_prediction_id"] == "sig-real-0001"
    assert rows[0]["idempotency_key"] == "innovation-promoter:sig-real-0001"
    # Nothing may reach the dispatchable column without a human.
    assert not [r for r in rows if r["status"] == "backlog"]


def test_real_write_rerun_creates_nothing(real_db):
    """Criterion 2: the second run is a genuine no-op in the database."""
    sig = _sig("sig-real-0002", "developer_experience", score=0.82)
    eligible = kp.classify_signals([sig], CONFIG)["eligible"]

    first = kp.promote_signals(eligible, CONFIG, dry_run=False)
    second = kp.promote_signals(eligible, CONFIG, dry_run=False)

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped_existing"] == 1
    assert len(_rows(real_db)) == 1
