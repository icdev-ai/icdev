# CUI // SP-CTI
"""Route and IQE-adapter tests for the Delta Review canvas (trust-hitl-02).

The store's invariants live in ``tests/test_hitl_delta.py``. These cover the
properties that exist only at the HTTP and IQE seams, and each is a way the
canvas would be actively harmful rather than merely incomplete:

* the settle route is the only write; if it accepted an empty rationale, or let
  a caller name someone else as the reviewer, the audit record it produces would
  be worse than none at all;
* a second settle that silently overwrote the first would give the panel two
  contradictory answers about the same diff;
* an IQE adapter that closed a caller-owned connection breaks a sibling fetch on
  another thread and returns half a union as though it were whole (ctx-trust-03);
* an IQE adapter that leaked draft text would put CUI into every downstream
  surface that renders a query result.
"""
from __future__ import annotations

import json

import pytest

flask = pytest.importorskip("flask")

from tools.delta_review import blueprint as dr_blueprint  # noqa: E402
from tools.quality import hitl_delta  # noqa: E402
from tools.quality.hitl_delta import compute_delta  # noqa: E402

BEFORE = (
    "ICDEV supports 47 compliance frameworks [source: ssp-1]. "
    "The platform runs on PostgreSQL [source: arch-2]."
)
AFTER = (
    "ICDEV supports several compliance frameworks [source: ssp-1]. "
    "The platform runs on PostgreSQL [source: arch-2]."
)
UNSUPPORTED = {
    "guard": "claim_guard", "issue": "unsupported_claim", "severity": "block",
    "item_number": 1, "detail": "the evidence does not state 47",
}

_DDL = """
CREATE TABLE trust_deltas (
    delta_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, artifact_type TEXT,
    stage TEXT NOT NULL, gate TEXT, before_hash TEXT NOT NULL,
    after_hash TEXT NOT NULL, before_text TEXT, after_text TEXT,
    findings_before TEXT, findings_after TEXT, findings_before_n INTEGER,
    findings_after_n INTEGER, spans TEXT, actor TEXT, rationale TEXT,
    disposition TEXT NOT NULL, approval_item_id TEXT, supersedes_delta_id TEXT,
    session_id TEXT, tenant_id TEXT, classification TEXT, created_at TEXT
)
"""


@pytest.fixture()
def store(monkeypatch):
    """In-memory delta store shared by the app under test.

    Patches ``hitl_delta._connect`` directly rather than ``tools.db.storage``:
    the storage module and its ``icdev.`` twin are distinct objects, so patching
    by string form installs the fake in only one of them and the test silently
    reaches the LIVE board.
    """
    import sqlite3

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(_DDL)
    conn.commit()

    class _Wrapper:
        def execute(self, sql, params=()):
            return conn.execute(sql.replace("%s", "?"), params)

        def commit(self):
            conn.commit()

        def close(self):
            pass

    monkeypatch.setattr(hitl_delta, "_connect", lambda: _Wrapper())
    monkeypatch.setattr(hitl_delta, "_enqueue_ask", lambda delta: "")
    yield conn
    conn.close()


@pytest.fixture()
def client(monkeypatch, store):
    monkeypatch.setenv("ICDEV_DELTA_REVIEW_ENABLED", "true")
    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    bp = dr_blueprint.create_delta_review_blueprint()
    assert bp is not None, "the canvas must mount when its flag is on"
    app.register_blueprint(bp)
    return app.test_client()


def _seed(artifact_id: str = "art-http") -> hitl_delta.Delta:
    return hitl_delta.record_delta(compute_delta(
        BEFORE, AFTER, artifact_id=artifact_id, gate="claim_guard",
        findings_before=[UNSUPPORTED], findings_after=[],
    ))


# ── the feature flag ─────────────────────────────────────────────────────────

def test_canvas_is_dark_when_the_flag_is_off(monkeypatch):
    monkeypatch.setenv("ICDEV_DELTA_REVIEW_ENABLED", "false")
    assert dr_blueprint.create_delta_review_blueprint() is None


# ── reads ────────────────────────────────────────────────────────────────────

def test_queue_lists_a_pending_delta(client, store):
    delta = _seed()
    body = client.get("/api/delta-review/deltas").get_json()
    assert [d["delta_id"] for d in body["deltas"]] == [delta.delta_id]
    assert body["telemetry_available"] is True


def test_delta_detail_carries_the_span_verdicts(client, store):
    delta = _seed()
    body = client.get(f"/api/delta-review/delta/{delta.delta_id}").get_json()
    assert body["resolved_count"] == 1
    assert body["can_settle"] is True
    assert [s["finding_verdict"] for s in body["spans"]] == ["resolved", "clean"]


def test_missing_delta_is_404(client, store):
    assert client.get("/api/delta-review/delta/td-nope").status_code == 404


# ── the settle write — the point of the canvas ───────────────────────────────

def test_settle_records_the_disposition(client, store):
    delta = _seed()
    resp = client.post(
        f"/api/delta-review/delta/{delta.delta_id}/settle",
        json={"approved": True,
              "rationale": "checked the revised figure against the cited SSP section"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["disposition"] == "approved"
    assert body["settlement_id"] != delta.delta_id

    settlement = hitl_delta.get_settlement(delta.delta_id)
    assert settlement is not None
    assert settlement.supersedes_delta_id == delta.delta_id
    # ...and the evidence row is untouched.
    assert hitl_delta.get_delta(delta.delta_id).disposition == "pending"


@pytest.mark.parametrize("rationale", ["", "   ", "ok"])
def test_settle_refuses_an_empty_or_token_rationale(client, store, rationale):
    """An approval reading 'ok' is the same unauditable artifact as an empty
    one. A 400 here — not a 500, and never a silent accept."""
    delta = _seed()
    resp = client.post(
        f"/api/delta-review/delta/{delta.delta_id}/settle",
        json={"approved": True, "rationale": rationale},
    )
    assert resp.status_code == 400
    assert "rationale" in resp.get_json()["error"]
    assert hitl_delta.get_settlement(delta.delta_id) is None


def test_settle_requires_an_explicit_approved_flag(client, store):
    """Omitting it must not default to either outcome. A disposition is an
    explicit act — the same call approval_inbox's CLI makes when it refuses
    --resolve without exactly one of --approve/--deny."""
    delta = _seed()
    resp = client.post(
        f"/api/delta-review/delta/{delta.delta_id}/settle",
        json={"rationale": "a perfectly adequate stated reason"},
    )
    assert resp.status_code == 400
    assert hitl_delta.get_settlement(delta.delta_id) is None


def test_a_second_settle_is_409_not_an_overwrite(client, store):
    delta = _seed()
    first = client.post(
        f"/api/delta-review/delta/{delta.delta_id}/settle",
        json={"approved": True, "rationale": "verified against the source document"},
    )
    second = client.post(
        f"/api/delta-review/delta/{delta.delta_id}/settle",
        json={"approved": False, "rationale": "changed my mind about this diff"},
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert hitl_delta.get_settlement(delta.delta_id).disposition == "approved"


def test_the_actor_cannot_be_supplied_by_the_caller(client, store):
    """nav-comp-06. A caller must not be able to attribute a disposition to
    someone else — the recorded actor comes from the authenticated user."""
    delta = _seed()
    client.post(
        f"/api/delta-review/delta/{delta.delta_id}/settle",
        json={"approved": True, "actor": "someone-else",
              "rationale": "verified against the source document"},
    )
    assert hitl_delta.get_settlement(delta.delta_id).actor != "someone-else"


# ── IQE adapters ─────────────────────────────────────────────────────────────

def test_adapters_do_not_close_a_caller_owned_connection(store):
    """ctx-trust-03. ``_fetch_union`` fetches collections in PARALLEL over ONE
    caller-supplied connection; closing it in ``finally`` kills a sibling fetch
    mid-flight and the union quietly returns half its rows."""
    from tools.iqe.adapters import delta_review as adapter

    _seed("art-iqe")
    closed = {"n": 0}

    class _Conn:
        def execute(self, sql, params=()):
            return store.execute(sql.replace("%s", "?"), params)

        def close(self):
            closed["n"] += 1

    rows = adapter.deltas_adapter(_Conn())
    assert len(rows) == 1
    assert closed["n"] == 0, "the adapter closed a connection it does not own"


def test_adapters_never_emit_draft_text(store):
    """The artifact is CUI and IQE results travel into analyst answers, AI
    briefs and chat replies. Indices and verdicts, never claim strings."""
    from tools.iqe.adapters import delta_review as adapter

    _seed("art-cui")

    class _Conn:
        def execute(self, sql, params=()):
            return store.execute(sql.replace("%s", "?"), params)

        def close(self):
            pass

    for rows in (adapter.deltas_adapter(_Conn()), adapter.spans_adapter(_Conn())):
        blob = json.dumps(list(rows), default=str)
        assert "PostgreSQL" not in blob
        assert "compliance frameworks" not in blob
        assert "before_text" not in blob


def test_spans_collection_flattens_and_labels_each_span(store):
    from tools.iqe.adapters import delta_review as adapter

    _seed("art-spans")

    class _Conn:
        def execute(self, sql, params=()):
            return store.execute(sql.replace("%s", "?"), params)

        def close(self):
            pass

    spans = list(adapter.spans_adapter(_Conn()))
    assert len(spans) == 2
    assert {s["finding_verdict"] for s in spans} == {"resolved", "clean"}
    assert all(s["artifact_id"] == "art-spans" for s in spans)


def test_every_seed_query_parses_and_names_a_registered_collection():
    """Seed queries ship as documentation of what this canvas can answer, and
    nothing was validating them.

    Caught a real defect: three of the four were written with ``//`` comments,
    which the IQE lexer does not accept — the supported form is ``#``. All four
    looked fine in review and three would have failed the moment anyone ran
    them. This is the shape of check that has to exist for the artifact to be
    worth shipping at all.
    """
    from pathlib import Path

    from tools.delta_review.constants import IQE_COLLECTIONS
    from tools.iqe.parser import IQESyntaxError, parse

    seeds = sorted(
        (Path(__file__).resolve().parents[1]
         / "context" / "iqe" / "queries" / "delta_review").glob("*.iqe")
    )
    assert len(seeds) >= 3, "the completeness gate requires at least 3 seed queries"

    for seed in seeds:
        text = seed.read_text(encoding="utf-8")
        try:
            parse(text)
        except IQESyntaxError as exc:  # pragma: no cover - the assertion is the report
            raise AssertionError(f"{seed.name} does not parse: {exc}") from exc
        body = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
        assert any(c in body for c in IQE_COLLECTIONS), (
            f"{seed.name} names no registered delta_review collection"
        )


def test_a_seed_query_actually_executes_against_the_collections(store):
    """Parsing is not running. A query that parses but names a column no adapter
    emits returns nothing and is indistinguishable from an empty board."""
    from pathlib import Path

    from tools.iqe.adapters import delta_review as _adapter  # noqa: F401  registers
    from tools.iqe.executor import execute_query
    from tools.iqe.parser import parse

    _seed("art-seed")

    class _Conn:
        """Supplied explicitly: with ``conn=None`` the adapter opens its own via
        ``get_connection()`` and reaches the real database, not this fixture."""

        def execute(self, sql, params=()):
            return store.execute(sql.replace("%s", "?"), params)

        def close(self):
            pass

    seed = (
        Path(__file__).resolve().parents[1]
        / "context" / "iqe" / "queries" / "delta_review" / "01_pending_deltas.iqe"
    )
    rows = execute_query(parse(seed.read_text(encoding="utf-8")), conn=_Conn())
    assert len(rows) == 1
    assert rows[0]["artifact_id"] == "art-seed"
    assert rows[0]["gate"] == "claim_guard"


def test_settlements_collection_exposes_the_rationale(store):
    """So that "were any approved without a stated reason" is ASKABLE. A
    guardrail nobody can query is one nobody can falsify."""
    from tools.iqe.adapters import delta_review as adapter

    delta = _seed("art-settled")
    hitl_delta.settle_delta(
        delta.delta_id, approved=True, actor="reviewer",
        rationale="evidence checked, wording now matches the source",
    )

    class _Conn:
        def execute(self, sql, params=()):
            return store.execute(sql.replace("%s", "?"), params)

        def close(self):
            pass

    rows = list(adapter.settlements_adapter(_Conn()))
    assert len(rows) == 1
    assert rows[0]["supersedes_delta_id"] == delta.delta_id
    assert rows[0]["rationale"].startswith("evidence checked")
    # The settlement must NOT also appear as a reviewable delta.
    assert [r["delta_id"] for r in adapter.deltas_adapter(_Conn())] == [delta.delta_id]
