# CUI // SP-CTI — Governance twins (icx-cnf-03)
"""Cortex, ACE, Co-Workers and conformance-audit governance twins.

Each runs against its own throwaway SQLite database (``ICDEV_DB_PATH`` under
``tmp_path``), seeded through the subsystems' own DDL. What is pinned:

* the four adapters register under real component-registry canvas keys;
* nothing observed is never a pass: an unreadable source is unverified, lowers
  coverage and gives ``unknown``;
* every violation carries a ``thread_id`` that resolves to its record (and on
  to provenance where the record names one);
* ``judge`` is pure and ``replay`` recomputes a recorded verdict identically, and
  says so when it cannot;
* the observation table refuses UPDATE and DELETE;
* the console page, its JSON routes and the MCP tool serve all of it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tools.twin_core import governance as gov
from tools.twin_core.registry import TwinRegistry, known_canvas_keys

GOVERNANCE_KEYS = {"cortex", "ace", "cwk", "twin_observatory"}


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """An empty per-test SQLite database for every connection kind."""
    path = tmp_path / "gov.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    monkeypatch.delenv("ICDEV_ACE_DB_URL", raising=False)
    TwinRegistry.discover(force=True)
    return path


@pytest.fixture
def full_db(icdev_db, monkeypatch):
    """The conftest minimal schema (Cortex finding tables included), per test."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.delenv("ICDEV_ACE_DB_URL", raising=False)
    TwinRegistry.discover(force=True)
    return icdev_db


def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _exec(sql: str, params=()) -> None:
    conn = _conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _adapter(key):
    return gov.adapter_for(key)


# ── registration ─────────────────────────────────────────────────────────────


def test_four_governance_twins_register_under_registry_canvas_keys(db):
    adapters = gov.governance_adapters()
    assert set(adapters) == GOVERNANCE_KEYS
    # validated against args/component_registry.yaml, not just any string
    assert GOVERNANCE_KEYS <= known_canvas_keys()
    assert adapters["twin_observatory"].observes == ("conformance.audit",)


def test_the_conformance_audit_sees_thread_ids_and_replay_on_each(db):
    from tools.conformance.feature_audit import twin_adapters

    found = {a["canvas_key"]: a for a in twin_adapters(gov.BASE_DIR, "tools/twin_core/adapters")}
    for key in GOVERNANCE_KEYS:
        assert found[key]["thread_ids"] and found[key]["replay"], key


# ── nothing observed is never a pass ─────────────────────────────────────────


@pytest.mark.parametrize("key", ["cortex", "ace"])
def test_an_empty_database_is_unknown_never_pass(db, key):
    env = gov.observe(_adapter(key), record=False)
    assert env["verdict"] == "unknown"
    assert env["coverage_score"] == 0.0
    assert env["violations"], "each unverified check is stated"
    assert all(v["severity"] == "low" and v["rule_id"].endswith(".unverified")
               for v in env["violations"])
    assert all(v["thread_id"].startswith("twin_obs:") for v in env["violations"])


def test_conclude_never_passes_with_an_unverified_check():
    checks = [gov.check("a", "A", True), gov.check("b", "B", False, "gone")]
    assert gov.conclude(checks, []) == ("unknown", 0.5)
    assert gov.conclude([gov.check("a", "A", True)], []) == ("pass", 1.0)
    assert gov.conclude([], []) == ("unknown", 0.0)
    low = gov.finding("x", "low", "advisory", thread_id="t:1")
    assert gov.conclude([gov.check("a", "A", True)], [low])[0] == "pass"
    crit = gov.finding("x", "critical", "bad", thread_id="t:1")
    assert gov.conclude(checks, [crit])[0] == "fail"


def test_a_finding_without_a_thread_is_refused():
    with pytest.raises(ValueError):
        gov.finding("x", "high", "no thread", thread_id="")


# ── Cortex ───────────────────────────────────────────────────────────────────


def _seed_cortex():
    from tests._schema_compat import ensure_table

    from tools.cortex.db.init_db import _ensure_schema

    conn = _conn()
    try:
        _ensure_schema(conn)
        ensure_table(conn, """CREATE TABLE IF NOT EXISTS source_citation_registry (
            id TEXT PRIMARY KEY, citation_type TEXT NOT NULL, source_table TEXT NOT NULL,
            source_record_id TEXT NOT NULL, source_doc TEXT, source_hash TEXT NOT NULL,
            classification TEXT DEFAULT 'CUI', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("INSERT INTO source_citation_registry (id, citation_type, source_table, "
                     "source_record_id, source_hash) VALUES (%s, %s, %s, %s, %s)",
                     ("scr-ok", "cortex", "cortex_governance", "cgov-ok", "abc"))
        full = {"pre_check": "pass", "input_redaction": "pass", "operation": "pass",
                "output_redaction": "pass", "provenance": "pass"}
        rows = [
            ("cgov-ok", "pass", 0, "scr-ok", {"outcomes": full}, _ago(hours=1)),
            # operation ran, provenance never did, and no provenance record
            ("cgov-hole", "warn", 0, None,
             {"outcomes": {**full, "provenance": "fail"}}, _ago(hours=2)),
            # a gate failed and the call went on
            ("cgov-open", "fail", 0, "scr-ok",
             {"outcomes": {**full, "citation_grounding": "fail"},
              "backends": {"failed": ["rag"]}}, _ago(hours=3)),
            # blocked at pre_check: later gates rightly absent
            ("cgov-block", "blocked", 1, None,
             {"outcomes": {"pre_check": "fail"}, "blocked_gate": "pre_check"}, _ago(hours=4)),
            # the call itself failed: nothing to redact
            ("cgov-err", "fail", 0, None,
             {"outcomes": {"pre_check": "pass", "operation": "fail"}}, _ago(hours=5)),
        ]
        for rid, outcome, blocked, prov, gates, at in rows:
            conn.execute(
                "INSERT INTO cortex_audit (id, outcome, blocked, provenance_id, gates_json, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (rid, outcome, blocked, prov, json.dumps(gates), at))
        conn.execute("INSERT INTO cortex_finding_runs (tenant_id, resolutions, last_run_at) "
                     "VALUES (%s, %s, %s)", ("default", 3, _ago(hours=1)))
        for fid, days in (("cef-old", 40), ("cef-mid", 10), ("cef-new", 1)):
            conn.execute(
                "INSERT INTO cortex_entity_findings (finding_id, tenant_id, finding_type, "
                "entity_key, entity_label, conflict_kind, provenance_id, first_seen_at, "
                "last_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (fid, "default", "conflict", fid, f"Entity {fid}", "status", "scr-ok",
                 _ago(days=days), _ago(hours=1)))
        conn.commit()
    finally:
        conn.close()


def test_cortex_observes_gates_chain_findings_with_threads(full_db):
    _seed_cortex()
    env = gov.observe(_adapter("cortex"), record=True)
    by_rule = {}
    for v in env["violations"]:
        by_rule.setdefault(v["rule_id"], []).append(v)
    assert [v["thread_id"] for v in by_rule["cortex.gate.mandatory"]] == ["cortex_audit:cgov-hole"]
    assert by_rule["cortex.gate.mandatory"][0]["severity"] == "critical"
    assert [v["thread_id"] for v in by_rule["cortex.gate.fail_open"]] == ["cortex_audit:cgov-open"]
    assert by_rule["cortex.audit.no_provenance"][0]["thread_id"] == "cortex_audit:cgov-hole"
    assert "cortex.audit.db_mutable" in by_rule and "cortex.audit.unchained" in by_rule
    aged = {v["thread_id"]: v["severity"] for v in by_rule["cortex.finding.unadjudicated"]}
    assert aged == {"cortex_finding:cef-old": "critical", "cortex_finding:cef-mid": "high"}
    assert env["verdict"] == "fail"
    summary = env["extra"]["summary"]
    assert summary["governed_calls"] == 5 and summary["blocked"] == 1
    assert summary["blocked_by_gate"] == {"pre_check": 1}
    assert env["extra"]["recorded"] is True


def test_cortex_threads_resolve_to_the_record_and_its_provenance(full_db):
    _seed_cortex()
    t = gov.resolve_thread("cortex_audit:cgov-open")
    assert t["resolved"] and t["record"]["id"] == "cgov-open"
    assert t["provenance"]["id"] == "scr-ok"
    f = gov.resolve_thread("cortex_finding:cef-old")
    assert f["resolved"] and f["provenance"]["source_table"] == "cortex_governance"
    gone = gov.resolve_thread("cortex_audit:cgov-missing")
    assert not gone["resolved"] and "no cortex_audit row" in gone["reason"]
    assert not gov.resolve_thread("nonsense")["resolved"]


def test_cortex_audit_triggers_clear_the_db_mutable_finding(full_db):
    _seed_cortex()
    _exec("CREATE TRIGGER cortex_audit_no_update BEFORE UPDATE ON cortex_audit "
          "BEGIN SELECT RAISE(ABORT, 'x'); END")
    _exec("CREATE TRIGGER cortex_audit_no_delete BEFORE DELETE ON cortex_audit "
          "BEGIN SELECT RAISE(ABORT, 'x'); END")
    env = gov.observe(_adapter("cortex"), record=False)
    assert "cortex.audit.db_mutable" not in {v["rule_id"] for v in env["violations"]}


# ── record and replay ────────────────────────────────────────────────────────


def test_replay_recomputes_the_recorded_verdict_identically(full_db):
    _seed_cortex()
    adapter = _adapter("cortex")
    env = gov.observe(adapter, record=True)
    obs = env["extra"]["observation_id"]
    result = adapter.replay(obs)
    assert result["match"] and result["evidence_intact"]
    assert result["recomputed"]["verdict"] == env["verdict"]
    assert result["recomputed"]["verdict_digest"] == env["extra"]["verdict_digest"]
    assert gov.replay_any(obs)["match"]
    # judge is pure: the same evidence twice gives the same digest
    evidence = json.loads(gov.load_observation(obs)["evidence_json"])
    assert (adapter.envelope(evidence)["extra"]["verdict_digest"]
            == adapter.envelope(evidence)["extra"]["verdict_digest"])


def test_ages_come_from_the_evidence_clock_not_the_wall_clock(full_db):
    _seed_cortex()
    adapter = _adapter("cortex")
    evidence = adapter.collect()
    later = dict(evidence)
    later["observed_at"] = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat(
        timespec="seconds")
    sev = {v["thread_id"]: v["severity"] for v in adapter.envelope(later)["violations"]
           if v["rule_id"] == "cortex.finding.unadjudicated"}
    assert sev["cortex_finding:cef-new"] == "critical"


def test_replay_says_when_the_record_does_not_hold(db):
    adapter = _adapter("cwk")
    env = gov.observe(adapter, record=True)
    row = gov.load_observation(env["extra"]["observation_id"])
    conn = _conn()
    try:  # a forged row: same evidence, a verdict digest it never produced
        conn.execute(
            "INSERT INTO twin_governance_observations (id, canvas, target_id, verdict, "
            "coverage_score, violation_count, counts_json, evidence_json, evidence_digest, "
            "verdict_digest, judge_version, observed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("tgo-forged", "cwk", "fleet", "pass", 1.0, 0, "{}", row["evidence_json"],
             row["evidence_digest"], "0" * 64, gov.JUDGE_VERSION, row["observed_at"]))
        conn.execute(
            "INSERT INTO twin_governance_observations (id, canvas, target_id, verdict, "
            "coverage_score, violation_count, counts_json, evidence_json, evidence_digest, "
            "verdict_digest, judge_version, observed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("tgo-edited", "cwk", "fleet", "pass", 1.0, 0, "{}", '{"edited": true}',
             row["evidence_digest"], row["verdict_digest"], gov.JUDGE_VERSION, row["observed_at"]))
        conn.commit()
    finally:
        conn.close()
    forged = adapter.replay("tgo-forged")
    assert not forged["match"] and forged["evidence_intact"]
    assert forged["reason"] == "recomputed verdict differs from the recorded one"
    edited = adapter.replay("tgo-edited")
    assert not edited["match"] and not edited["evidence_intact"]
    assert not gov.replay_any("tgo-none")["found"]
    assert "belongs to" in _adapter("ace").replay("tgo-forged")["reason"]


def test_observations_refuse_update_and_delete(db):
    env = gov.observe(_adapter("cwk"), record=True)
    obs = env["extra"]["observation_id"]
    for sql in ("UPDATE twin_governance_observations SET verdict = 'pass' WHERE id = %s",
                "DELETE FROM twin_governance_observations WHERE id = %s"):
        conn = _conn()
        try:
            with pytest.raises(Exception, match="immutable|cannot be deleted"):
                conn.execute(sql, (obs,))
        finally:
            conn.rollback()
            conn.close()
    # recording the same evidence again is idempotent, not a second row
    assert gov.record_observation(env, json.loads(gov.load_observation(obs)["evidence_json"]),
                                  "cwk") == obs
    assert len(gov.list_observations("cwk")) == 1


def test_fleet_health_reads_this_twins_observations(db):
    adapter = _adapter("twin_observatory")
    assert adapter.fleet_health()["snapshot_count"] in (None, 0)
    env = gov.observe(adapter, record=True)
    health = adapter.fleet_health()
    assert health["snapshot_count"] == 1
    assert health["verdicts"] == {env["verdict"]: 1}
    assert health["violation_counts"]["total"] == len(env["violations"])
    assert adapter.latest_status()["verdict"] == env["verdict"]


# ── ACE ──────────────────────────────────────────────────────────────────────


def _seed_ace():
    from tools.ace.db.init_db import init
    from tools.nova.db.init_db import init_nova_tables

    init()
    init_nova_tables()
    rows = [
        ("ai-stuck", "active", _ago(hours=10)),
        ("ai-wait", "paused", _ago(hours=30)),   # waiting on a person, not stuck
        ("ai-fresh", "active", _ago(minutes=5)),
        ("ai-failed", "failed", _ago(hours=6)),
        ("ai-aborted", "cancelled", _ago(hours=7)),
        ("ai-old-fail", "failed", _ago(days=30)),  # outside the window
    ]
    for rid, state, at in rows:
        _exec("INSERT INTO ace_instances (id, name, role_id, state, created_at, updated_at) "
              "VALUES (%s, %s, %s, %s, %s, %s)", (rid, rid, "ai_dev", state, at, at))
    for inst, cw, action, detail, at in (
        ("ai-wait", "cw1", "hitl_pending", "approve deploy", _ago(hours=30)),
        ("ai-fresh", "cw2", "hitl_pending", "approve tests", _ago(hours=1)),
        ("ai-fresh", "cw3", "hitl_pending", "done one", _ago(hours=9)),
        ("ai-fresh", "cw3", "hitl_resolved", "done one", _ago(hours=8)),
    ):
        _exec("INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, created_at) "
              "VALUES (%s, %s, %s, %s, %s)", (inst, cw, action, detail, at))
    for lid, role, score, at in (
        ("trust-1", "qa", 0.65, _ago(days=20)),   # trusted before the window
        ("trust-2", "qa", 0.25, _ago(days=1)),    # probationary now
        ("trust-3", "dev", 0.55, _ago(days=2)),
    ):
        _exec("INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score, recorded_at) "
              "VALUES (%s, %s, %s, %s, %s, %s)", (lid, role, -0.1, "failure", score, at))


def test_ace_observes_runs_checkpoints_aborts_and_trust(db):
    _seed_ace()
    env = gov.observe(_adapter("ace"), record=True)
    got = {(v["rule_id"], v["thread_id"]): v["severity"] for v in env["violations"]}
    assert got[("ace.run.stuck", "ace_instance:ai-stuck")] == "high"
    assert ("ace.run.stuck", "ace_instance:ai-wait") not in got
    assert got[("ace.run.failed", "ace_instance:ai-failed")] == "medium"
    assert ("ace.run.failed", "ace_instance:ai-old-fail") not in got
    assert got[("ace.run.aborted", "ace_instance:ai-aborted")] == "low"
    waiting = {k[1]: s for k, s in got.items() if k[0] == "ace.checkpoint.waiting"}
    assert sorted(waiting.values()) == ["critical", "low"]  # 30h and 1h; the resolved one is gone
    assert got[("ace.trust.band_drop", "ace_trust:trust-2")] == "medium"
    assert got[("ace.trust.probationary", "ace_trust:trust-2")] == "medium"
    assert env["verdict"] == "fail" and env["coverage_score"] == 1.0
    assert env["extra"]["summary"]["checkpoints_waiting"] == 2
    # every thread resolves
    for v in env["violations"]:
        assert gov.resolve_thread(v["thread_id"])["resolved"], v["thread_id"]
    assert _adapter("ace").replay(env["extra"]["observation_id"])["match"]


# ── Co-Workers and the conformance audit ─────────────────────────────────────


def test_cwk_reads_the_real_roster_sources_and_verifies_both(db):
    env = gov.observe(_adapter("cwk"), record=False)
    assert {c["status"] for c in env["extra"]["checks"]} == {"verified"}
    assert env["extra"]["summary"]["role_files"] > 0


def test_cwk_judge_states_silently_dropped_roles_and_id_collisions():
    adapter = gov.adapter_for("cwk")
    evidence = {
        "canvas": "cwk", "observed_at": "2026-11-14T00:00:00+00:00", "errors": {},
        "personas": {"path": "args/chat_personas.yaml", "sha256": "a" * 64, "error": "",
                     "keys": ["atlas"], "shape_ok": True},
        "roles": [
            {"path": "args/ace/roles/a.yaml", "sha256": "b" * 64, "error": "", "role_id": "dev"},
            {"path": "args/ace/roles/b.yaml", "sha256": "c" * 64, "error": "", "role_id": "dev"},
            {"path": "args/ace/roles/c.yaml", "sha256": "d" * 64,
             "error": "unparseable: x", "role_id": ""},
        ],
    }
    env = adapter.envelope(evidence)
    got = {v["rule_id"]: v["thread_id"] for v in env["violations"]}
    assert got["cwk.role.dropped"] == "config:args/ace/roles/c.yaml@dddddddddddd"
    assert got["cwk.id.collision"] == "config:args/ace/roles/b.yaml@cccccccccccc"
    assert env["verdict"] == "warn"


def test_conformance_twin_agrees_with_the_committed_census(db):
    env = gov.observe(_adapter("twin_observatory"), record=False)
    rules = {v["rule_id"] for v in env["violations"]}
    assert "conformance.census.stale" not in rules
    assert "conformance.census.over" not in rules
    assert {c["status"] for c in env["extra"]["checks"]} == {"verified"}
    assert gov.resolve_thread("conformance:cortex.pmo")["resolved"]


def test_conformance_twin_fails_when_census_and_snapshot_disagree():
    adapter = gov.adapter_for("twin_observatory")
    evidence = {"canvas": "twin_observatory", "observed_at": "2026-11-14T00:00:00+00:00",
                "errors": {},
                "snapshot": {"path": "x.json", "sha256": "0", "enforcement": "warn",
                             "features": [{"key": "f", "label": "F", "gaps": ["twin:no-adapter"]}],
                             "gap_keys": ["f|twin:no-adapter"]},
                "census": {"path": "c.txt", "lines": [], "census_max": 0, "readable": True}}
    env = adapter.envelope(evidence)
    rules = {v["rule_id"]: v["thread_id"] for v in env["violations"]}
    assert rules["conformance.census.stale"] == "conformance:census"
    assert env["verdict"] == "fail"


# ── console and MCP ──────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    from flask import Flask

    from tools.twin_observatory.blueprint import twin_observatory_bp

    # A stub base.html first in the search path: the real one needs the whole
    # dashboard's context. The page's own template is the real one.
    (tmp_path / "tpl").mkdir()
    (tmp_path / "tpl" / "base.html").write_text("{% block content %}{% endblock %}",
                                                encoding="utf-8")
    app = Flask(__name__, template_folder=str(tmp_path / "tpl"))
    app.config["TESTING"] = True
    app.register_blueprint(twin_observatory_bp)
    return app.test_client()


def test_console_page_observe_replay_and_thread(db, client):
    page = client.get("/twin-observatory/governance")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Governance Twins" in body and "No observation recorded yet" in body
    obs = client.post("/twin-observatory/api/governance/observe", json={"canvas": "cwk"})
    assert obs.status_code == 200
    obs_id = obs.get_json()["observations"][0]["observation_id"]
    body = client.get("/twin-observatory/governance").get_data(as_text=True)
    assert obs_id in body and "matches" in body
    replay = client.get(f"/twin-observatory/api/governance/replay/{obs_id}")
    assert replay.status_code == 200 and replay.get_json()["match"] is True
    assert client.get("/twin-observatory/api/governance/replay/tgo-none").status_code == 404
    assert client.get("/twin-observatory/api/governance/thread").status_code == 400
    assert client.get("/twin-observatory/api/governance/thread?id=x:y").status_code == 404
    api = client.get("/twin-observatory/api/governance").get_json()
    assert {t["canvas"] for t in api["twins"]} == GOVERNANCE_KEYS
    assert client.post("/twin-observatory/api/governance/observe",
                       json={"canvas": "ndc"}).status_code == 404


def test_page_shows_why_buttons_for_each_finding(full_db, client):
    _seed_cortex()
    client.post("/twin-observatory/api/governance/observe", json={"canvas": "cortex"})
    body = client.get("/twin-observatory/governance").get_data(as_text=True)
    assert 'data-thread="cortex_audit:cgov-hole"' in body
    thread = client.get("/twin-observatory/api/governance/thread?id=cortex_audit:cgov-hole")
    assert thread.status_code == 200 and thread.get_json()["record"]["id"] == "cgov-hole"


def test_mcp_tool_is_read_only_and_serves_twins_replay_and_thread(db):
    from tools.mcp.gap_handlers import handle_twin_governance
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, tool_authorization

    assert READ_ONLY_DECLARATIONS["twin_governance"] is True
    assert tool_authorization("twin_governance")["tier"] == "read"
    env = gov.observe(_adapter("cwk"), record=True)
    out = handle_twin_governance({"canvas": "cwk"})
    assert out["twins"][0]["observation"]["id"] == env["extra"]["observation_id"]
    assert handle_twin_governance({"observation_id": env["extra"]["observation_id"]})[
        "replay"]["match"]
    assert handle_twin_governance({"thread_id": "conformance:census"})["thread"]["resolved"]
    assert "error" in handle_twin_governance({"canvas": "nope"})
    # reading never records
    assert len(gov.list_observations("cwk")) == 1
