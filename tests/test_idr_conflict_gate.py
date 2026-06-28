# CUI // SP-CTI
"""Unit tests for IDRConflictGate — HITL conflict gate for merge/CI conflicts."""
import json
import sqlite3
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers: in-memory SQLite stand-in so no real DB is needed
# ---------------------------------------------------------------------------

def _make_fake_conn(hitl_stage: str | None = None):
    """Return a fake connection whose fetchone() returns the given hitl_stage."""
    mem = sqlite3.connect(":memory:")
    mem.execute(
        "CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, hitl_stage TEXT, updated_at TEXT)"
    )
    if hitl_stage is not None:
        mem.execute(
            "INSERT INTO kanban_tasks VALUES (?, ?, ?)",
            ("task-001", hitl_stage, "2026-01-01T00:00:00+00:00"),
        )
    else:
        mem.execute(
            "INSERT INTO kanban_tasks VALUES (?, ?, ?)",
            ("task-001", None, "2026-01-01T00:00:00+00:00"),
        )
    mem.commit()

    # Wrap so UPDATE writes go through without PG %s → ? substitution
    class _FakeConn:
        def execute(self, sql, params=()):
            sql = sql.replace("%s", "?")
            return mem.execute(sql, params)

        def commit(self):
            mem.commit()

        def close(self):
            pass  # keep in-memory DB alive across multiple calls in same test

        def raw(self):
            return mem

    return _FakeConn()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIDRConflictGateNoConflicts(unittest.TestCase):
    def setUp(self):
        self.conn = _make_fake_conn(hitl_stage=None)
        self.patcher = patch("tools.idr.conflict_gate.get_connection", return_value=self.conn)
        self.patcher.start()
        from tools.idr.conflict_gate import IDRConflictGate
        self.gate = IDRConflictGate()

    def tearDown(self):
        self.patcher.stop()

    def test_no_conflicts_returns_empty(self):
        self.assertEqual(self.gate.get_conflicts("task-001"), [])

    def test_has_unresolved_false_when_empty(self):
        self.assertFalse(self.gate.has_unresolved_conflicts("task-001"))

    def test_can_resume_true_when_no_conflicts(self):
        self.assertTrue(self.gate.can_resume("task-001"))

    def test_assert_can_resume_passes_when_no_conflicts(self):
        # Should not raise
        self.gate.assert_can_resume("task-001")


class TestIDRConflictGateRecordAndResolve(unittest.TestCase):
    def setUp(self):
        self.conn = _make_fake_conn(hitl_stage=None)
        self.patcher = patch("tools.idr.conflict_gate.get_connection", return_value=self.conn)
        self.patcher.start()
        from tools.idr.conflict_gate import IDRConflictGate
        self.gate = IDRConflictGate()

    def tearDown(self):
        self.patcher.stop()

    def test_record_returns_conflict_id(self):
        cid = self.gate.record_conflict("task-001", "merge_conflict", "3 files conflict")
        self.assertIsInstance(cid, str)
        self.assertEqual(len(cid), 36)  # UUID format

    def test_after_record_has_unresolved_true(self):
        self.gate.record_conflict("task-001", "merge_conflict", "details")
        self.assertTrue(self.gate.has_unresolved_conflicts("task-001"))

    def test_after_record_can_resume_false(self):
        self.gate.record_conflict("task-001", "merge_conflict", "details")
        self.assertFalse(self.gate.can_resume("task-001"))

    def test_resolve_clears_unresolved(self):
        cid = self.gate.record_conflict("task-001", "merge_conflict", "details")
        result = self.gate.resolve_conflict("task-001", cid, resolver="alice", note="Fixed")
        self.assertTrue(result)
        self.assertFalse(self.gate.has_unresolved_conflicts("task-001"))
        self.assertTrue(self.gate.can_resume("task-001"))

    def test_resolve_nonexistent_returns_false(self):
        result = self.gate.resolve_conflict("task-001", "bad-uuid", resolver="alice")
        self.assertFalse(result)

    def test_assert_can_resume_raises_with_open_conflicts(self):
        from tools.idr.conflict_gate import ConflictNotResolved
        self.gate.record_conflict("task-001", "merge_conflict", "details")
        with self.assertRaises(ConflictNotResolved):
            self.gate.assert_can_resume("task-001")

    def test_conflict_fields_populated(self):
        cid = self.gate.record_conflict("task-001", "ci_failure", "tests failed")
        conflicts = self.gate.get_conflicts("task-001")
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c["id"], cid)
        self.assertEqual(c["type"], "ci_failure")
        self.assertEqual(c["details"], "tests failed")
        self.assertFalse(c["resolved"])
        self.assertIsNone(c["resolved_by"])

    def test_resolved_fields_populated(self):
        cid = self.gate.record_conflict("task-001", "merge_conflict", "conflict")
        self.gate.resolve_conflict("task-001", cid, resolver="bob", note="done")
        conflicts = self.gate.get_conflicts("task-001")
        c = conflicts[0]
        self.assertTrue(c["resolved"])
        self.assertEqual(c["resolved_by"], "bob")
        self.assertEqual(c["resolution_note"], "done")
        self.assertIsNotNone(c["resolved_at"])


class TestIDRConflictGateResolveAll(unittest.TestCase):
    def setUp(self):
        self.conn = _make_fake_conn(hitl_stage=None)
        self.patcher = patch("tools.idr.conflict_gate.get_connection", return_value=self.conn)
        self.patcher.start()
        from tools.idr.conflict_gate import IDRConflictGate
        self.gate = IDRConflictGate()

    def tearDown(self):
        self.patcher.stop()

    def test_resolve_all_clears_multiple(self):
        self.gate.record_conflict("task-001", "merge_conflict", "a")
        self.gate.record_conflict("task-001", "ci_failure", "b")
        count = self.gate.resolve_all("task-001", resolver="charlie", note="batch clear")
        self.assertEqual(count, 2)
        self.assertTrue(self.gate.can_resume("task-001"))

    def test_resolve_all_returns_zero_when_none_pending(self):
        count = self.gate.resolve_all("task-001", resolver="charlie")
        self.assertEqual(count, 0)


class TestIDRConflictGateClear(unittest.TestCase):
    def setUp(self):
        self.conn = _make_fake_conn(hitl_stage=None)
        self.patcher = patch("tools.idr.conflict_gate.get_connection", return_value=self.conn)
        self.patcher.start()
        from tools.idr.conflict_gate import IDRConflictGate
        self.gate = IDRConflictGate()

    def tearDown(self):
        self.patcher.stop()

    def test_clear_removes_all_conflicts(self):
        self.gate.record_conflict("task-001", "merge_conflict", "c")
        self.gate.clear_conflicts("task-001")
        self.assertEqual(self.gate.get_conflicts("task-001"), [])

    def test_clear_enables_resume(self):
        self.gate.record_conflict("task-001", "merge_conflict", "c")
        self.gate.clear_conflicts("task-001")
        self.assertTrue(self.gate.can_resume("task-001"))


class TestIDRConflictGateEnvelopeMerge(unittest.TestCase):
    """Ensure saving IDR conflicts doesn't clobber existing hitl_stage keys."""

    def setUp(self):
        existing = json.dumps({"other_key": "other_value"})
        self.conn = _make_fake_conn(hitl_stage=existing)
        self.patcher = patch("tools.idr.conflict_gate.get_connection", return_value=self.conn)
        self.patcher.start()
        from tools.idr.conflict_gate import IDRConflictGate
        self.gate = IDRConflictGate()

    def tearDown(self):
        self.patcher.stop()

    def test_existing_envelope_keys_preserved(self):
        self.gate.record_conflict("task-001", "merge_conflict", "x")
        # Read raw hitl_stage from the fake DB
        raw = self.conn.execute(
            "SELECT hitl_stage FROM kanban_tasks WHERE id = ?", ("task-001",)
        ).fetchone()
        envelope = json.loads(raw[0])
        self.assertIn("other_key", envelope)
        self.assertEqual(envelope["other_key"], "other_value")
        self.assertIn("idr_conflicts", envelope)


class TestGetGateSingleton(unittest.TestCase):
    def test_get_gate_returns_same_instance(self):
        from tools.idr.conflict_gate import get_gate, IDRConflictGate
        import tools.idr.conflict_gate as mod
        mod._default_gate = None  # reset singleton
        g1 = get_gate()
        g2 = get_gate()
        self.assertIs(g1, g2)
        self.assertIsInstance(g1, IDRConflictGate)


if __name__ == "__main__":
    unittest.main()
