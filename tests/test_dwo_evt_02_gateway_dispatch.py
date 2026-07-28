# CUI // SP-CTI
"""DWO-EVT-02 — gateway events start workflow runs.

The gateway ingests events through an HMAC envelope and an 8-gate security
chain; workflow_runner runs DAGs. Nothing joined the two. These cover the hop.

Acceptance criteria from the card:
  * a cleared event on a bound channel starts the trigger's run
  * the same delivery id replayed starts EXACTLY ONE run
  * an event whose classification exceeds the workflow IL is refused + audited
  * every evaluated event lands in studio_trigger_events, matched or not
"""
from __future__ import annotations

import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import event_dispatch, event_sources
from tools.studio.init_db import init_studio_tables


class _Envelope:
    """Minimal stand-in for gateway CommandEnvelope."""

    def __init__(self, channel="github", eid="env-1", args=None, command="deploy"):
        self.channel = channel
        self.id = eid
        self.args = args or {}
        self.command = command


@pytest.fixture(autouse=True)
def _schema(tmp_path, monkeypatch):
    """Fresh SQLite file per test.

    Pointed at a temp path rather than the repo DB: ``init_studio_tables`` uses
    CREATE TABLE IF NOT EXISTS, so against an existing database it silently
    leaves the pre-migration-308 table in place and every audit assertion here
    fails for the wrong reason.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    init_studio_tables()


@pytest.fixture
def bound(monkeypatch):
    """A gateway_channel source + one trigger pointing at a workflow."""
    wf_id = f"wf-{uuid.uuid4().hex[:10]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) VALUES (%s,%s,%s)",
            (wf_id, "Deploy", "canvas: ndc\nsteps: []\n"),
        )
        conn.commit()
    finally:
        conn.close()

    src = event_sources.create_event_source(
        name="github", kind="gateway_channel", config={"channel": "github"},
    )
    source_id = src["source_id"]
    trg = event_sources.create_workflow_trigger(
        workflow_id=wf_id, source_id=source_id, event_type="push", event_filter=[],
    )
    started: list[tuple] = []

    def _fake_start_run(workflow_id, project_id="default", inputs=None):
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        started.append((workflow_id, project_id, inputs, run_id))
        return run_id

    import tools.studio.workflow_runner as wr
    monkeypatch.setattr(wr, "start_run", _fake_start_run)
    return {"source_id": source_id, "trigger_id": trg["trigger_id"],
            "workflow_id": wf_id, "started": started}


def _events(source_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM studio_trigger_events WHERE source_id = %s ORDER BY received_at",
            (source_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Happy path ─────────────────────────────────────────────────────────────

def test_cleared_event_starts_the_bound_run(bound):
    env = _Envelope(args={"type": "push", "branch": "main"})
    out = event_dispatch.dispatch_envelope(env, {}, {"type": "push", "id": "d1"}, {})

    assert out["matched"] == 1
    assert len(out["started"]) == 1
    assert bound["started"], "start_run was never called"
    assert bound["started"][0][0] == bound["workflow_id"]


def test_run_started_is_audited_as_a_second_row(bound):
    """studio_trigger_events is append-only: the run_id is a new row, not an UPDATE."""
    env = _Envelope(args={"type": "push"})
    event_dispatch.dispatch_envelope(env, {}, {"type": "push", "id": "d2"}, {})

    rows = _events(bound["source_id"])
    outcomes = [r["outcome"] for r in rows]
    assert "matched" in outcomes, outcomes
    assert "run_started" in outcomes, outcomes
    claim = next(r for r in rows if r["outcome"] == "matched")
    assert claim["run_id"] is None, "the claim row must not carry a run_id"


# ── Idempotency ────────────────────────────────────────────────────────────

def test_replayed_delivery_starts_exactly_one_run(bound):
    payload = {"type": "push", "id": "delivery-abc"}
    env = _Envelope(args=payload)

    event_dispatch.dispatch_envelope(env, {}, payload, {"X-GitHub-Delivery": "delivery-abc"})
    event_dispatch.dispatch_envelope(env, {}, payload, {"X-GitHub-Delivery": "delivery-abc"})

    assert len(bound["started"]) == 1, (
        f"replay started {len(bound['started'])} runs; the UNIQUE idempotency_key "
        "did not hold"
    )


def test_event_without_a_delivery_id_is_not_deduplicated(bound):
    """No stable id → dispatched but not de-duplicated, and it says so."""
    env = _Envelope(args={"type": "push"})
    out = event_dispatch.dispatch_envelope(env, {}, {"type": "push"}, {})
    assert out["deduplicated"] is False


def test_delivery_id_is_read_from_headers_first():
    got = event_dispatch.extract_delivery_id(
        {"id": "from-payload"}, {"X-GitHub-Delivery": "from-header"})
    assert got == "from-header"


def test_delivery_id_falls_back_to_payload():
    assert event_dispatch.extract_delivery_id({"delivery_id": "p1"}, {}) == "p1"


def test_delivery_id_absent_returns_none():
    assert event_dispatch.extract_delivery_id({}, {}) is None


# ── Classification ─────────────────────────────────────────────────────────

def test_event_above_the_workflow_il_is_refused_and_audited(bound):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE studio_workflow_triggers SET workflow_il='IL4' WHERE trigger_id=%s",
            (bound["trigger_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    env = _Envelope(args={"type": "push"})
    out = event_dispatch.dispatch_envelope(
        env, {"max_il": "IL6"}, {"type": "push", "id": "d3"}, {})

    assert not bound["started"], "a refused event must not start a run"
    assert len(out["refused"]) == 1
    outcomes = [r["outcome"] for r in _events(bound["source_id"])]
    assert "refused_classification" in outcomes, outcomes


def test_refusal_claims_no_idempotency_key(bound):
    """A refusal must not burn the delivery id — re-registering at the right IL
    has to be able to run that same delivery."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE studio_workflow_triggers SET workflow_il='IL4' WHERE trigger_id=%s",
            (bound["trigger_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    env = _Envelope(args={"type": "push"})
    event_dispatch.dispatch_envelope(
        env, {"max_il": "IL6"}, {"type": "push"}, {"X-GitHub-Delivery": "d4"})

    refusal = next(r for r in _events(bound["source_id"])
                   if r["outcome"] == "refused_classification")
    assert refusal["idempotency_key"] is None


@pytest.mark.parametrize("event_il,workflow_il,allowed", [
    ("IL2", "IL6", True),
    ("IL4", "IL4", True),
    ("IL6", "IL4", False),
    ("IL5", "IL4", False),
    ("IL4", "IL5", True),
])
def test_classification_allows(event_il, workflow_il, allowed):
    assert event_sources.classification_allows(event_il, workflow_il) is allowed


def test_more_sensitive_ceiling_wins():
    """A source at IL5 on a channel capped at IL4 is treated as IL5."""
    assert event_dispatch.event_classification({"max_il": "IL4"}, {"max_il": "IL5"}) == "IL5"
    assert event_dispatch.event_classification({"max_il": "IL6"}, {"max_il": "IL2"}) == "IL6"


# ── Audit completeness ─────────────────────────────────────────────────────

def test_non_matching_event_is_still_audited(bound):
    """'my trigger never fires' must be answerable."""
    env = _Envelope(args={"type": "unrelated"})
    out = event_dispatch.dispatch_envelope(env, {}, {"type": "unrelated"}, {})

    assert out["matched"] == 0
    assert not bound["started"]
    rows = _events(bound["source_id"])
    assert rows and rows[-1]["outcome"] == "no_match"
    assert rows[-1]["reason"]


def test_unbound_channel_starts_nothing(bound):
    env = _Envelope(channel="slack", args={"type": "push"})
    out = event_dispatch.dispatch_envelope(env, {}, {"type": "push"}, {})
    assert out["matched"] == 0
    assert not bound["started"]


def test_start_run_failure_is_audited_not_raised(bound, monkeypatch):
    import tools.studio.workflow_runner as wr

    def _boom(*_a, **_kw):
        raise RuntimeError("workflow is broken")

    monkeypatch.setattr(wr, "start_run", _boom)
    env = _Envelope(args={"type": "push"})
    out = event_dispatch.dispatch_envelope(env, {}, {"type": "push", "id": "d5"}, {})

    assert out["started"] == []
    outcomes = [r["outcome"] for r in _events(bound["source_id"])]
    assert "error" in outcomes, outcomes


def test_dispatch_never_raises_into_the_caller(monkeypatch):
    """A registry problem must not turn a delivered webhook into a 500."""
    monkeypatch.setattr(
        event_dispatch, "source_for_channel",
        lambda _c: (_ for _ in ()).throw(RuntimeError("registry down")))
    out = event_dispatch.dispatch_envelope(_Envelope(), {}, {}, {})
    assert "error" in out


# ── The gateway wiring ─────────────────────────────────────────────────────

def test_gateway_dispatches_only_after_the_security_chain():
    """The hop must sit after run_security_chain, never before it.

    Guards the one property that cannot be re-derived from behaviour: a future
    edit moving this call above the chain would let an ungated event start a
    run, and every functional test here would still pass.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "tools/gateway/gateway_agent.py"
    text = src.read_text(encoding="utf-8")

    chain_at = text.index("passed, gate_results = run_security_chain(")
    reject_at = text.index("return jsonify({\"status\": \"rejected\"")
    dispatch_at = text.index("dispatch_envelope_async(")

    assert chain_at < dispatch_at, "dispatch must come after the security chain"
    assert reject_at < dispatch_at, "dispatch must come after the rejection return"


def test_no_new_route_is_registered():
    """dwo-evt-02 opens no ingress of its own."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "tools/studio/event_dispatch.py"
    text = src.read_text(encoding="utf-8")
    for marker in ("@app.route", "@bp.route", "add_url_rule", "Blueprint("):
        assert marker not in text, f"event_dispatch.py must not register routes ({marker})"
