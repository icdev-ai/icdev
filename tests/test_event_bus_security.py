# CUI // SP-CTI
"""Unit tests for event bus security context propagation (sec-eco-05)."""
from __future__ import annotations

import json

import pytest

from tools.canvas.event_bus import (
    EVENT_BLOCKED,
    EVENT_DOWNGRADED,
    EVENT_SECURITY_CONTEXT_PROPAGATED,
    _downgrade_payload,
    dispatch_pending,
    publish,
    subscribe,
)
from tools.db.storage import get_connection


@pytest.fixture(autouse=True)
def _clean_listeners():
    """Reset the in-process listener registry before each test."""
    from tools.canvas import event_bus
    event_bus._LISTENERS.clear()
    yield
    event_bus._LISTENERS.clear()


@pytest.fixture(autouse=True)
def _clean_db(icdev_db, monkeypatch):
    """Run the whole file against a per-test temp DB built from the real schema.

    This used to DELETE its own rows out of whatever ``get_connection()`` happened
    to resolve to — the developer's live ``data/icdev.db``. Two consequences, both
    of which bit: in a fresh worktree that database has no ``canvas_events`` table
    at all, so every test errored at setup; and where it did exist the suite was
    writing event and audit rows into a real database it then partially deleted
    from (``audit_trail`` is append-only, so that DELETE was itself a violation).
    ``icdev_db`` carries the canonical ``canvas_events`` and ``audit_trail`` DDL,
    and pointing ``ICDEV_DB_PATH`` at it covers the no-argument ``get_connection()``
    calls inside ``tools/canvas/event_bus.py``. No cleanup is needed: the DB is new.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    yield


class TestSecurityContextPropagation:
    def test_publish_injects_security_context(self):
        payload = {"msg": "hello"}
        event_id = publish("test_src", "test_evt", payload, target_canvas="test_tgt")
        assert event_id

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT payload_json FROM canvas_events WHERE id=?", (event_id,)
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        loaded = json.loads(row[0] if isinstance(row, (list, tuple)) else row["payload_json"])
        assert "_security_context" in loaded
        assert loaded["_security_context"]["clearance"] == "CUI"

    def test_publish_custom_security_context(self):
        sec_ctx = {"clearance": "SECRET", "compartment": "NOFORN", "tenant_id": "t1"}
        event_id = publish(
            "test_src", "test_evt", {"data": 1},
            target_canvas="test_tgt", security_context=sec_ctx
        )
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT payload_json FROM canvas_events WHERE id=?", (event_id,)
            ).fetchone()
        finally:
            conn.close()
        loaded = json.loads(row[0] if isinstance(row, (list, tuple)) else row["payload_json"])
        assert loaded["_security_context"]["clearance"] == "SECRET"
        assert loaded["_security_context"]["compartment"] == "NOFORN"
        assert loaded["_security_context"]["tenant_id"] == "t1"

    def test_subscriber_receives_event(self):
        received: list[dict] = []

        def handler(event_id, canvas_id, event_type, payload):
            received.append({"event_id": event_id, "payload": payload})

        subscribe("test_tgt", "test_evt", handler)
        event_id = publish("test_src", "test_evt", {"msg": "hi"}, target_canvas="test_tgt")

        assert len(received) == 1
        assert received[0]["event_id"] == event_id
        assert received[0]["payload"]["msg"] == "hi"
        assert received[0]["payload"]["_security_context"]["clearance"] == "CUI"

    def test_cross_tenant_event_blocked(self):
        received: list[dict] = []

        def handler(event_id, canvas_id, event_type, payload):
            received.append({"event_id": event_id, "payload": payload})

        subscribe(
            "test_tgt", "test_evt", handler,
            subscriber_context={"tenant_id": "tenant_a", "clearance": "CUI"}
        )
        publish(
            "test_src", "test_evt", {"msg": "hi"},
            target_canvas="test_tgt",
            security_context={"tenant_id": "tenant_b", "clearance": "CUI"}
        )

        assert len(received) == 0

    def test_cross_tenant_allowed_for_admin(self):
        received: list[dict] = []

        def handler(event_id, canvas_id, event_type, payload):
            received.append({"event_id": event_id, "payload": payload})

        subscribe(
            "test_tgt", "test_evt", handler,
            subscriber_context={"tenant_id": "tenant_a", "clearance": "CUI", "is_admin": True}
        )
        publish(
            "test_src", "test_evt", {"msg": "hi"},
            target_canvas="test_tgt",
            security_context={"tenant_id": "tenant_b", "clearance": "CUI"}
        )

        assert len(received) == 1

    def test_downgrade_ts_to_cui(self):
        received: list[dict] = []

        def handler(event_id, canvas_id, event_type, payload):
            received.append({"event_id": event_id, "event_type": event_type, "payload": payload})

        subscribe(
            "test_tgt", "test_evt", handler,
            subscriber_context={"clearance": "CUI"}
        )
        publish(
            "test_src", "test_evt", {"msg": "hi", "ts_plan": "secret"},
            target_canvas="test_tgt",
            security_context={"clearance": "TOP SECRET"}
        )

        assert len(received) == 1
        assert received[0]["event_type"] == EVENT_DOWNGRADED
        assert received[0]["payload"]["_downgraded"] is True
        assert received[0]["payload"]["_security_context"]["clearance"] == "CUI"
        assert "ts_plan" not in received[0]["payload"]

    def test_same_clearance_no_downgrade(self):
        received: list[dict] = []

        def handler(event_id, canvas_id, event_type, payload):
            received.append({"event_id": event_id, "event_type": event_type, "payload": payload})

        subscribe(
            "test_tgt", "test_evt", handler,
            subscriber_context={"clearance": "SECRET"}
        )
        publish(
            "test_src", "test_evt", {"msg": "hi"},
            target_canvas="test_tgt",
            security_context={"clearance": "SECRET"}
        )

        assert len(received) == 1
        assert received[0]["event_type"] == "test_evt"
        assert "_downgraded" not in received[0]["payload"]

    def test_dispatch_pending_with_security_checks(self):
        received: list[dict] = []

        def handler(event_id, canvas_id, event_type, payload):
            received.append({"event_id": event_id, "event_type": event_type, "payload": payload})

        subscribe(
            "test_tgt", "pending_evt", handler,
            subscriber_context={"clearance": "CUI", "tenant_id": "t1"}
        )

        # Publish without immediate target dispatch
        publish("test_src", "pending_evt", {"msg": "pending"}, target_canvas=None,
                security_context={"clearance": "SECRET", "tenant_id": "t1"})

        # Now dispatch pending for test_tgt
        fired = dispatch_pending("test_tgt")
        assert fired == 0  # target_canvas was None, so no pending row for test_tgt

    def test_dispatch_pending_targeted(self):
        received: list[dict] = []

        def handler(event_id, canvas_id, event_type, payload):
            received.append({"event_id": event_id, "event_type": event_type, "payload": payload})

        subscribe(
            "test_tgt", "pending_evt", handler,
            subscriber_context={"clearance": "CUI", "tenant_id": "t1"}
        )

        publish("test_src", "pending_evt", {"msg": "pending"}, target_canvas="test_tgt",
                security_context={"clearance": "SECRET", "tenant_id": "t1"})

        # publish() fires in-process listeners on the target canvas synchronously
        # and leaves consumed_at NULL, so an in-process subscriber sees the event
        # twice: once here and once on the dispatch_pending replay. Asserting a
        # single total delivery conflated the two hops; the replay is what this
        # test is about, so pin the count at each hop instead.
        assert len(received) == 1
        assert received[0]["event_type"] == EVENT_DOWNGRADED

        fired = dispatch_pending("test_tgt")
        assert fired == 1
        assert len(received) == 2
        # The replayed copy is downgraded too — the SECRET payload must not reach
        # a CUI subscriber intact on either path.
        assert received[1]["event_type"] == EVENT_DOWNGRADED
        assert received[1]["payload"]["_security_context"]["clearance"] == "CUI"
        assert received[1]["payload"]["_downgraded"] is True

        # Replay is once-only: the row is marked consumed.
        assert dispatch_pending("test_tgt") == 0
        assert len(received) == 2

    def test_audit_trail_entries_written(self):
        conn = get_connection()
        try:
            before = conn.execute(
                "SELECT COUNT(*) FROM audit_trail WHERE actor='icdev-event-bus'"
            ).fetchone()[0]
        finally:
            conn.close()

        subscribe("test_tgt", "audit_evt", lambda e, c, t, p: None)
        publish("test_src", "audit_evt", {"msg": "hi"}, target_canvas="test_tgt")

        conn = get_connection()
        try:
            after = conn.execute(
                "SELECT COUNT(*) FROM audit_trail WHERE actor='icdev-event-bus'"
            ).fetchone()[0]
        finally:
            conn.close()

        # Expect at least 2 audit entries: publish + propagated
        assert after >= before + 2

    def test_downgrade_payload_strips_ts_keys(self):
        payload = {
            "name": "project",
            "ts_plan": "top secret data",
            "sci_code": "SCI-001",
            "secret_key": "shh",
            "nested": {"ts_inner": "inner secret", "keep": "value"},
            "_security_context": {"clearance": "TOP SECRET", "compartment": "SCI"},
        }
        result = _downgrade_payload(payload)
        assert result["_security_context"]["clearance"] == "CUI"
        assert result["_downgraded"] is True
        assert "ts_plan" not in result
        assert "sci_code" not in result
        assert "secret_key" not in result
        assert "name" in result
        # nested dict should also be downgraded
        assert "ts_inner" not in result["nested"]
        assert result["nested"]["keep"] == "value"

    def test_downgrade_payload_terminates_and_scrubs_deep_nesting(self):
        """Regression: the downgrade used to recurse into its own marker keys.

        ``_downgrade_payload`` stamped ``_security_context`` onto the result and
        then descended into every dict value, so it re-entered the dict it had
        just written and recursed forever. Every downgrade raised RecursionError,
        i.e. the SECRET-to-CUI scrub never ran at all. This asserts the call
        returns, that the scrub reaches an arbitrarily deep level, and that the
        markers stay at the top level instead of being smeared through the payload.
        """
        payload = {"lvl": 0, "ts_top": "x", "child": {}}
        node = payload["child"]
        for depth in range(1, 25):
            node["lvl"] = depth
            node["ts_secret"] = "classified"
            node["child"] = {}
            node = node["child"]

        result = _downgrade_payload(payload)

        assert result["_downgraded"] is True
        assert result["_security_context"]["clearance"] == "CUI"
        assert result["_security_context"]["compartment"] is None
        assert "ts_top" not in result

        node = result["child"]
        for depth in range(1, 25):
            assert node["lvl"] == depth
            assert "ts_secret" not in node, f"TS key survived at depth {depth}"
            assert "_security_context" not in node
            assert "_downgraded" not in node
            node = node["child"]

    def test_new_event_type_constants(self):
        assert EVENT_SECURITY_CONTEXT_PROPAGATED == "security_context_propagated"
        assert EVENT_DOWNGRADED == "event_downgraded"
        assert EVENT_BLOCKED == "event_blocked"
