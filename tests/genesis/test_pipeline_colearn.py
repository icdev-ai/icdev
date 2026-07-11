# CUI // SP-CTI
"""Phase 3a: harness delivery-pipeline co-learner (eval_harness pipeline health)."""
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection
from tools.genesis.harness import eval_harness as eh


def _seed(rows):
    """Insert kanban_verifications rows. Each row: dict of gate columns (1/0/None)."""
    from tools.kanban.init_db import init_kanban_tables
    init_kanban_tables()
    conn = get_connection()
    conn.execute("DELETE FROM kanban_verifications WHERE task_id LIKE 'plc-%'")
    now = datetime.now(timezone.utc).isoformat()
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO kanban_verifications "
            "(id, task_id, verified_at, result, codelens_passed, coherence_passed, "
            " review_passed, pytest_passed, e2e_ran, e2e_passed, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"v-{uuid.uuid4().hex[:8]}", f"plc-{i}", now, "pass",
             r.get("codelens_passed"), r.get("coherence_passed"), r.get("review_passed"),
             r.get("pytest_passed"), r.get("e2e_ran"), r.get("e2e_passed"), now),
        )
    conn.commit()
    conn.close()


def test_compute_pipeline_health_rates_and_samples():
    # 3 codelens pass, 1 fail -> 0.75 ; conformance 1 pass 1 fail -> 0.5 ;
    # e2e: only rows with e2e_ran=1 count -> 1 pass / 1 fail = 0.5 (a 3rd row e2e_ran=0 ignored)
    _seed([
        {"codelens_passed": 1, "coherence_passed": 1, "review_passed": 1, "pytest_passed": 1, "e2e_ran": 1, "e2e_passed": 1},
        {"codelens_passed": 1, "coherence_passed": 1, "review_passed": 0, "pytest_passed": None, "e2e_ran": 1, "e2e_passed": 0},
        {"codelens_passed": 1, "coherence_passed": None, "review_passed": None, "pytest_passed": 1, "e2e_ran": 0, "e2e_passed": None},
        {"codelens_passed": 0, "coherence_passed": 1, "review_passed": None, "pytest_passed": None, "e2e_ran": None, "e2e_passed": None},
    ])
    h = eh.compute_pipeline_health(window_days=30)
    r = h["gate_pass_rates"]
    s = h["sample_sizes"]
    assert s["codelens"] == 4 and abs(r["codelens"] - 0.75) < 1e-9
    assert s["conformance"] == 2 and abs(r["conformance"] - 0.5) < 1e-9   # review_passed NULL excluded
    assert s["e2e"] == 2 and abs(r["e2e"] - 0.5) < 1e-9                    # e2e_ran=0 row excluded
    assert s["pytest"] == 2 and abs(r["pytest"] - 1.0) < 1e-9             # 2 passes, NULLs excluded


def test_check_pipeline_gates_alerts_below_floor(monkeypatch):
    # conformance 1/4 pass = 0.25 < 0.60 floor; small min_sample so it triggers
    monkeypatch.setattr(eh, "_pipeline_thresholds", lambda: {
        **eh._DEFAULT_PIPELINE_GATES, "pipeline_min_sample": 3})
    _seed([
        {"review_passed": 1, "codelens_passed": 1, "coherence_passed": 1},
        {"review_passed": 0, "codelens_passed": 1, "coherence_passed": 1},
        {"review_passed": 0, "codelens_passed": 1, "coherence_passed": 1},
        {"review_passed": 0, "codelens_passed": 1, "coherence_passed": 1},
    ])
    alerts = eh.check_pipeline_gates()
    conf = [a for a in alerts if a["metric"] == "conformance_pass_rate"]
    assert conf, f"expected a conformance alert, got {[a['metric'] for a in alerts]}"
    a = conf[0]
    # shape must match _create_degradation_card's expectations
    for k in ("reflex", "metric", "value", "threshold", "severity", "recommendation"):
        assert k in a
    assert a["reflex"] == "delivery_pipeline"
    assert isinstance(a["value"], float) and a["value"] < a["threshold"]
    assert "%.3f" % a["value"]  # formattable as float
    # codelens all pass -> no codelens alert
    assert not [x for x in alerts if x["metric"] == "codelens_pass_rate"]


def test_check_pipeline_gates_no_alert_below_min_sample(monkeypatch):
    monkeypatch.setattr(eh, "_pipeline_thresholds", lambda: {
        **eh._DEFAULT_PIPELINE_GATES, "pipeline_min_sample": 10})
    _seed([{"review_passed": 0}, {"review_passed": 0}])  # only 2 < 10
    assert eh.check_pipeline_gates() == []


def test_check_pipeline_gates_never_raises(monkeypatch):
    monkeypatch.setattr(eh, "compute_pipeline_health", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert eh.check_pipeline_gates() == []  # swallowed


def test_harness_run_wires_pipeline_cards(monkeypatch):
    import tools.genesis.reflexes.harness as hr
    monkeypatch.setattr("tools.genesis.harness.eval_harness.check_gates", lambda: [])
    monkeypatch.setattr("tools.genesis.harness.eval_harness.check_pipeline_gates", lambda: [{
        "reflex": "delivery_pipeline", "metric": "conformance_pass_rate", "value": 0.4,
        "threshold": 0.6, "adaptive": False, "severity": "medium", "recommendation": "x"}])
    monkeypatch.setattr(hr, "_open_degradation_card_exists", lambda *a, **k: False)
    created = []
    monkeypatch.setattr(hr, "_create_degradation_card", lambda alert, **k: created.append(alert["metric"]) or "card-1")
    # neutralize the reflex-gate + meta paths so the test is focused
    monkeypatch.setattr("tools.genesis.harness.eval_harness.compute_metrics", lambda *a, **k: {"error": "skip"})
    try:
        hr.run({}, None)
    except Exception:
        pass  # meta/other paths may noop in the minimal test DB; we only assert the pipeline card
    assert "conformance_pass_rate" in created
