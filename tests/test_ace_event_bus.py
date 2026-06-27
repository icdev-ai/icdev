# CUI // SP-CTI
"""Tests for icdev.tools.ace.event_bus (in-process pub/sub)."""
from __future__ import annotations

import threading
import time

import pytest


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def test_import():
    from icdev.tools.ace import event_bus  # noqa: F401


# ---------------------------------------------------------------------------
# Basic pub/sub
# ---------------------------------------------------------------------------

class TestPublishSubscribe:
    def test_subscriber_receives_event(self):
        from icdev.tools.ace import event_bus as eb
        q = eb.subscribe("inst-1")
        try:
            eb.publish("inst-1", {"type": "test", "x": 1})
            ev = eb.drain("inst-1", q, timeout=1.0)
            assert ev == {"type": "test", "x": 1}
        finally:
            eb.unsubscribe("inst-1", q)

    def test_unsubscribed_queue_gets_nothing(self):
        from icdev.tools.ace import event_bus as eb
        q = eb.subscribe("inst-unsub")
        eb.unsubscribe("inst-unsub", q)
        eb.publish("inst-unsub", {"type": "after_unsub"})
        ev = eb.drain("inst-unsub", q, timeout=0.1)
        assert ev is None

    def test_multiple_subscribers_all_receive(self):
        from icdev.tools.ace import event_bus as eb
        q1 = eb.subscribe("inst-multi")
        q2 = eb.subscribe("inst-multi")
        try:
            eb.publish("inst-multi", {"type": "broadcast"})
            ev1 = eb.drain("inst-multi", q1, timeout=1.0)
            ev2 = eb.drain("inst-multi", q2, timeout=1.0)
            assert ev1["type"] == "broadcast"
            assert ev2["type"] == "broadcast"
        finally:
            eb.unsubscribe("inst-multi", q1)
            eb.unsubscribe("inst-multi", q2)

    def test_different_instances_isolated(self):
        from icdev.tools.ace import event_bus as eb
        qa = eb.subscribe("inst-a")
        qb = eb.subscribe("inst-b")
        try:
            eb.publish("inst-a", {"type": "for-a"})
            ev_a = eb.drain("inst-a", qa, timeout=1.0)
            ev_b = eb.drain("inst-b", qb, timeout=0.1)
            assert ev_a["type"] == "for-a"
            assert ev_b is None
        finally:
            eb.unsubscribe("inst-a", qa)
            eb.unsubscribe("inst-b", qb)

    def test_drain_returns_none_on_timeout(self):
        from icdev.tools.ace import event_bus as eb
        q = eb.subscribe("inst-timeout")
        try:
            ev = eb.drain("inst-timeout", q, timeout=0.05)
            assert ev is None
        finally:
            eb.unsubscribe("inst-timeout", q)

    def test_full_queue_drops_event(self):
        from icdev.tools.ace import event_bus as eb
        q = eb.subscribe("inst-full", maxsize=2)
        try:
            eb.publish("inst-full", {"type": "e1"})
            eb.publish("inst-full", {"type": "e2"})
            eb.publish("inst-full", {"type": "e3"})  # dropped
            ev1 = eb.drain("inst-full", q, timeout=0.1)
            ev2 = eb.drain("inst-full", q, timeout=0.1)
            ev3 = eb.drain("inst-full", q, timeout=0.05)
            assert ev1["type"] == "e1"
            assert ev2["type"] == "e2"
            assert ev3 is None
        finally:
            eb.unsubscribe("inst-full", q)


# ---------------------------------------------------------------------------
# subscriber_count helper
# ---------------------------------------------------------------------------

class TestSubscriberCount:
    def test_count_zero_before_subscribe(self):
        from icdev.tools.ace import event_bus as eb
        assert eb.subscriber_count("inst-sc-fresh") == 0

    def test_count_increments(self):
        from icdev.tools.ace import event_bus as eb
        q = eb.subscribe("inst-sc-inc")
        try:
            assert eb.subscriber_count("inst-sc-inc") == 1
        finally:
            eb.unsubscribe("inst-sc-inc", q)

    def test_count_decrements_on_unsubscribe(self):
        from icdev.tools.ace import event_bus as eb
        q = eb.subscribe("inst-sc-dec")
        eb.unsubscribe("inst-sc-dec", q)
        assert eb.subscriber_count("inst-sc-dec") == 0


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_publish_and_subscribe(self):
        from icdev.tools.ace import event_bus as eb
        received = []
        errors = []

        def _consumer():
            q = eb.subscribe("inst-ts")
            try:
                for _ in range(5):
                    ev = eb.drain("inst-ts", q, timeout=2.0)
                    if ev is not None:
                        received.append(ev)
            except Exception as exc:
                errors.append(exc)
            finally:
                eb.unsubscribe("inst-ts", q)

        def _producer():
            for i in range(5):
                eb.publish("inst-ts", {"type": "t", "i": i})
                time.sleep(0.01)

        t1 = threading.Thread(target=_consumer, daemon=True)
        t2 = threading.Thread(target=_producer, daemon=True)
        t1.start(); time.sleep(0.05); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)
        assert not errors
        assert len(received) == 5
