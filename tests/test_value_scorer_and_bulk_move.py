# CUI // SP-CTI
"""Tests for tools.awareness.value_scorer + /api/kanban/tasks/bulk-move.

Covers:
  * compute_value() — formula, rule weights, dedup multiplier, clamp
  * compute_dup_counts() + extract_subject() — title parsing
  * annotate_tasks_with_value() — mutates in place, always safe
  * bulk_move_tasks endpoint — promote + dismiss paths, prediction
    outcome bookkeeping, partial failure handling, caps
  * list_tasks sort=value / sort=confidence
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests import _sql_compat as sql_compat  # noqa: E402
from tools.awareness.value_scorer import (  # noqa: E402
    annotate_tasks_with_value,
    compute_dup_counts,
    compute_value,
    extract_subject,
)


# ---------------------------------------------------------------------------
# Pure function: compute_value
# ---------------------------------------------------------------------------


class TestComputeValue:
    def test_baseline_confidence_times_weight(self):
        # broken_test_reference has weight 1.5 in the shipped yaml
        v = compute_value(0.9, "broken_test_reference", dup_count=1)
        # 0.9 × 1.5 × 1.0 = 1.35
        assert abs(v - 1.35) < 0.001

    def test_dedup_boost_applied(self):
        # 4 duplicates → 1 + 0.1 × 3 = 1.3 multiplier
        v = compute_value(0.9, "broken_test_reference", dup_count=4)
        expected = 0.9 * 1.5 * 1.3
        assert abs(v - expected) < 0.001

    def test_dedup_boost_capped(self):
        # dup_count >> cap (default 10) should saturate at cap
        v_huge = compute_value(0.9, "broken_test_reference", dup_count=1000)
        v_cap = compute_value(0.9, "broken_test_reference", dup_count=11)
        # Both should hit the cap
        assert abs(v_huge - v_cap) < 0.001

    def test_unknown_rule_uses_default(self):
        v = compute_value(1.0, "this_rule_does_not_exist", dup_count=1)
        assert abs(v - 1.0) < 0.001  # 1.0 × _default(1.0) × 1.0

    def test_none_confidence_is_zero(self):
        v = compute_value(None, "broken_test_reference", dup_count=1)
        assert v == 0.0

    def test_negative_confidence_clamped(self):
        v = compute_value(-0.5, "broken_test_reference", dup_count=1)
        assert v == 0.0

    def test_dup_count_clamped_to_minimum_one(self):
        # dup_count=0 should behave like dup_count=1 (no boost)
        v0 = compute_value(0.9, "broken_test_reference", dup_count=0)
        v1 = compute_value(0.9, "broken_test_reference", dup_count=1)
        assert abs(v0 - v1) < 0.001

    def test_route_not_listed_weight_lower_than_default(self):
        # route_not_listed is the noisy rule, weight 0.6
        v_low = compute_value(0.9, "route_not_listed", dup_count=1)
        v_default = compute_value(0.9, "some_unknown_rule", dup_count=1)
        assert v_low < v_default

    def test_value_ranking_matches_rule_tier(self):
        # At equal confidence, runtime-breakage rules outrank noisy ones
        conf = 0.9
        v_break = compute_value(conf, "broken_test_reference", 1)
        v_orphan = compute_value(conf, "orphan_db_table", 1)
        v_discover = compute_value(conf, "tool_not_in_manifest", 1)
        v_noise = compute_value(conf, "route_not_listed", 1)
        assert v_break > v_orphan > v_discover > v_noise

    def test_malformed_confidence_does_not_raise(self):
        # Strings, lists, None should all return 0.0 (or fall through gracefully)
        assert compute_value("abc", "broken_test_reference", 1) == 0.0
        assert compute_value([0.9], "broken_test_reference", 1) == 0.0


# ---------------------------------------------------------------------------
# compute_dup_counts / extract_subject
# ---------------------------------------------------------------------------


class TestDupCountsAndSubject:
    def test_extract_subject_from_gap_title(self):
        title = "[Gap] Missing symbol: tools.rag.source_registry.SOURCE_REGISTRY"
        assert (
            extract_subject(title)
            == "tools.rag.source_registry.SOURCE_REGISTRY"
        )

    def test_extract_subject_no_colon(self):
        assert extract_subject("Plain title") == "Plain title"

    def test_extract_subject_empty_and_none(self):
        assert extract_subject("") == ""
        assert extract_subject(None) == ""  # type: ignore[arg-type]

    def test_compute_dup_counts(self):
        subjects = ["a", "b", "a", "a", "c"]
        counts = compute_dup_counts(subjects)
        assert counts == {"a": 3, "b": 1, "c": 1}

    def test_compute_dup_counts_empty(self):
        assert compute_dup_counts([]) == {}


# ---------------------------------------------------------------------------
# annotate_tasks_with_value — in-place mutation
# ---------------------------------------------------------------------------


class TestAnnotateTasks:
    def test_annotates_single_task(self):
        tasks = [
            {
                "id": "t1",
                "title": "[Gap] Missing symbol: foo.bar",
                "oracle_confidence": 0.9,
                "oracle_lens": "broken_test_reference",
            }
        ]
        annotate_tasks_with_value(tasks)
        assert tasks[0]["oracle_dup_count"] == 1
        assert tasks[0]["oracle_value"] > 0

    def test_annotates_dup_group(self):
        tasks = [
            {
                "title": "[Gap] Missing symbol: foo.FIX_REGISTRY",
                "oracle_confidence": 0.9,
                "oracle_lens": "broken_test_reference",
            },
            {
                "title": "[Gap] Missing symbol: foo.FIX_REGISTRY",
                "oracle_confidence": 0.9,
                "oracle_lens": "broken_test_reference",
            },
            {
                "title": "[Gap] Missing symbol: foo.FIX_REGISTRY",
                "oracle_confidence": 0.9,
                "oracle_lens": "broken_test_reference",
            },
        ]
        annotate_tasks_with_value(tasks)
        assert all(t["oracle_dup_count"] == 3 for t in tasks)
        # 3 dups → boost 1.2 → 0.9 × 1.5 × 1.2 = 1.62
        for t in tasks:
            assert abs(t["oracle_value"] - 1.62) < 0.001

    def test_annotates_non_oracle_task(self):
        """Manual kanban cards (no oracle_confidence) should get value 0."""
        tasks = [{"id": "t1", "title": "manual task"}]
        annotate_tasks_with_value(tasks)
        assert tasks[0]["oracle_value"] == 0.0
        assert tasks[0]["oracle_dup_count"] == 1

    def test_mixed_list_safe(self):
        tasks = [
            {
                "title": "[Gap] Missing: a",
                "oracle_confidence": 0.95,
                "oracle_lens": "tool_not_in_manifest",
            },
            {"title": "manual"},
            {
                "title": "[Gap] Missing: a",
                "oracle_confidence": 0.95,
                "oracle_lens": "tool_not_in_manifest",
            },
        ]
        annotate_tasks_with_value(tasks)
        # Oracle-backed cards dedup (2×), manual card is its own group
        assert tasks[0]["oracle_dup_count"] == 2
        assert tasks[2]["oracle_dup_count"] == 2
        assert tasks[1]["oracle_dup_count"] == 1


# ---------------------------------------------------------------------------
# Flask endpoint: bulk_move_tasks
# ---------------------------------------------------------------------------


@pytest.fixture
def kanban_bulk_client(tmp_path, monkeypatch):
    """Flask app + fresh sqlite DB with oracle_predictions + kanban_tasks."""
    from flask import Flask

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE kanban_tasks (
            id                   TEXT PRIMARY KEY,
            title                TEXT NOT NULL,
            description          TEXT,
            task_type            TEXT DEFAULT 'build',
            priority             TEXT DEFAULT 'medium',
            status               TEXT DEFAULT 'suggested',
            scheduled_at         TEXT,
            created_at           TEXT,
            updated_at           TEXT,
            completed_at         TEXT,
            executor_type        TEXT,
            execution_id         TEXT,
            executor_url         TEXT,
            source_prediction_id TEXT,
            depends_on_task_id   TEXT
        );
        CREATE TABLE oracle_predictions (
            id              TEXT PRIMARY KEY,
            confidence      REAL,
            prediction_text TEXT,
            lens_name       TEXT,
            prediction_type TEXT,
            outcome         TEXT DEFAULT 'pending'
        );
        -- list_tasks() LEFT JOINs the newest verification per task for
        -- phantom_ratio (migration 019). Seeded empty: the join is what the
        -- route needs, not any row. Absent, every /api/kanban/tasks call
        -- 500s on `no such table` — which the %s syntax error used to mask.
        CREATE TABLE kanban_verifications (
            id              TEXT PRIMARY KEY,
            task_id         TEXT NOT NULL,
            verified_at     TEXT NOT NULL,
            result          TEXT NOT NULL,
            phantom_ratio   REAL DEFAULT 0
        );
        """
    )

    # Seed 3 suggested tasks backed by 3 predictions.
    for i, (tid, pid, conf) in enumerate(
        [
            ("task-aaa", "pred-a", 0.95),
            ("task-bbb", "pred-b", 0.90),
            ("task-ccc", "pred-c", 0.85),
        ]
    ):
        conn.execute(
            "INSERT INTO oracle_predictions (id, confidence, lens_name, outcome) "
            "VALUES (?, ?, 'broken_test_reference', 'pending')",
            (pid, conf),
        )
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, status, source_prediction_id, created_at, updated_at) "
            "VALUES (?, ?, 'suggested', ?, datetime('now'), datetime('now'))",
            (tid, f"[Gap] Missing: subj-{i}", pid),
        )
    conn.commit()
    conn.close()

    def _fake_conn():
        # Must translate %s -> ?, the way the StorageConnection it stands in
        # for does. tools/dashboard/api/kanban.py authors PostgreSQL
        # placeholders throughout; on a bare sqlite3 connection list_tasks()
        # 500s on `WHERE kt.status = %s`, and the oracle_predictions dismiss
        # in bulk_move_tasks() raises into its own `except Exception` fallback
        # so the prediction silently stays 'pending'.
        return sql_compat.connect(db_path)

    from tools.dashboard.api import kanban as kanban_mod

    monkeypatch.setattr(kanban_mod, "get_connection", _fake_conn)

    class _StubSSE:
        def broadcast(self, *args, **kwargs):
            pass

    monkeypatch.setattr(kanban_mod, "sse_manager", _StubSSE())

    app = Flask(__name__)
    app.register_blueprint(kanban_mod.kanban_api)
    return app.test_client(), db_path


class TestBulkMoveEndpoint:
    def test_bulk_promote_moves_to_backlog(self, kanban_bulk_client):
        client, db_path = kanban_bulk_client
        r = client.post(
            "/api/kanban/tasks/bulk-move",
            json={"task_ids": ["task-aaa", "task-bbb"], "status": "backlog"},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["moved"] == 2
        assert body["failed"] == []

        # DB confirms
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, status FROM kanban_tasks ORDER BY id"
        ).fetchall()
        by_id = {r[0]: r[1] for r in rows}
        assert by_id["task-aaa"] == "backlog"
        assert by_id["task-bbb"] == "backlog"
        assert by_id["task-ccc"] == "suggested"  # untouched
        conn.close()

    def test_bulk_dismiss_marks_prediction_dismissed(self, kanban_bulk_client):
        client, db_path = kanban_bulk_client
        r = client.post(
            "/api/kanban/tasks/bulk-move",
            json={"task_ids": ["task-aaa"], "status": "done"},
        )
        assert r.status_code == 200
        assert r.get_json()["moved"] == 1

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        task_row = conn.execute(
            "SELECT status, completed_at FROM kanban_tasks WHERE id = 'task-aaa'"
        ).fetchone()
        assert task_row["status"] == "done"
        assert task_row["completed_at"] is not None
        pred_row = conn.execute(
            "SELECT outcome FROM oracle_predictions WHERE id = 'pred-a'"
        ).fetchone()
        assert pred_row["outcome"] == "dismissed"
        # Untouched prediction remains pending
        pred_b = conn.execute(
            "SELECT outcome FROM oracle_predictions WHERE id = 'pred-b'"
        ).fetchone()
        assert pred_b["outcome"] == "pending"
        conn.close()

    def test_bulk_move_rejects_empty_list(self, kanban_bulk_client):
        client, _ = kanban_bulk_client
        r = client.post(
            "/api/kanban/tasks/bulk-move",
            json={"task_ids": [], "status": "backlog"},
        )
        assert r.status_code == 400

    def test_bulk_move_rejects_invalid_status(self, kanban_bulk_client):
        client, _ = kanban_bulk_client
        r = client.post(
            "/api/kanban/tasks/bulk-move",
            json={"task_ids": ["task-aaa"], "status": "bogus"},
        )
        assert r.status_code == 400

    def test_bulk_move_rejects_cap_exceeded(self, kanban_bulk_client):
        client, _ = kanban_bulk_client
        r = client.post(
            "/api/kanban/tasks/bulk-move",
            json={
                "task_ids": [f"task-{i}" for i in range(1001)],
                "status": "backlog",
            },
        )
        assert r.status_code == 400
        assert "cap" in r.get_json()["error"].lower()

    def test_bulk_move_unknown_ids_go_to_failed(self, kanban_bulk_client):
        client, _ = kanban_bulk_client
        r = client.post(
            "/api/kanban/tasks/bulk-move",
            json={
                "task_ids": ["task-aaa", "task-nonexistent"],
                "status": "backlog",
            },
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["moved"] == 1
        assert any(
            f.get("id") == "task-nonexistent" and f.get("error") == "not found"
            for f in body["failed"]
        )


# ---------------------------------------------------------------------------
# Flask endpoint: list_tasks sort=value / confidence
# ---------------------------------------------------------------------------


class TestListTasksSort:
    def test_sort_by_value(self, kanban_bulk_client):
        client, _ = kanban_bulk_client
        r = client.get("/api/kanban/tasks?status=suggested&sort=value")
        assert r.status_code == 200
        tasks = r.get_json()["tasks"]
        # All three seeded tasks should have oracle_value computed
        for t in tasks:
            assert "oracle_value" in t
            assert "oracle_dup_count" in t
        values = [t["oracle_value"] for t in tasks]
        assert values == sorted(values, reverse=True)

    def test_sort_by_confidence(self, kanban_bulk_client):
        client, _ = kanban_bulk_client
        r = client.get("/api/kanban/tasks?status=suggested&sort=confidence")
        assert r.status_code == 200
        tasks = r.get_json()["tasks"]
        confs = [t.get("oracle_confidence") or 0 for t in tasks]
        assert confs == sorted(confs, reverse=True)

    def test_sort_default_preserves_priority_order(self, kanban_bulk_client):
        """Without ?sort= the existing priority-based order is preserved."""
        client, _ = kanban_bulk_client
        r = client.get("/api/kanban/tasks?status=suggested")
        assert r.status_code == 200
        tasks = r.get_json()["tasks"]
        # Still annotated with oracle_value even when sort is not requested
        for t in tasks:
            assert "oracle_value" in t

    def test_sort_by_priority(self, kanban_bulk_client):
        """Priority sort: critical → high → medium → low. Secondary key =
        value DESC so the most valuable item within a priority class
        floats to the top."""
        client, db_path = kanban_bulk_client

        # Tighten the fixture: reseed with mixed priorities so we can
        # verify ordering. The default fixture seeds all as 'high', which
        # wouldn't distinguish a priority sort from a no-op.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE kanban_tasks SET priority = 'critical' WHERE id = 'task-aaa'"
        )
        conn.execute(
            "UPDATE kanban_tasks SET priority = 'low' WHERE id = 'task-bbb'"
        )
        conn.execute(
            "UPDATE kanban_tasks SET priority = 'medium' WHERE id = 'task-ccc'"
        )
        conn.commit()
        conn.close()

        r = client.get("/api/kanban/tasks?status=suggested&sort=priority")
        assert r.status_code == 200
        tasks = r.get_json()["tasks"]
        ordered_priorities = [t.get("priority") for t in tasks]
        # critical first, then medium, then low
        assert ordered_priorities == ["critical", "medium", "low"]

    def test_sort_priority_secondary_value_tiebreak(
        self, kanban_bulk_client
    ):
        """When two tasks share a priority, the one with higher
        oracle_value should rank first."""
        client, db_path = kanban_bulk_client

        # All three seeded as high priority + different confidence
        # (which drives value because rule weight is constant).
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE kanban_tasks SET priority = 'high' WHERE id IN "
            "('task-aaa','task-bbb','task-ccc')"
        )
        conn.commit()
        conn.close()

        r = client.get("/api/kanban/tasks?status=suggested&sort=priority")
        tasks = r.get_json()["tasks"]
        # All three are high, so ordering falls through to value DESC.
        values = [t.get("oracle_value") or 0 for t in tasks]
        assert values == sorted(values, reverse=True)

    def test_sort_priority_unknown_rank_bottom(self, kanban_bulk_client):
        """A row with a bogus priority ('wat') should rank at the bottom
        without raising."""
        client, db_path = kanban_bulk_client

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE kanban_tasks SET priority = 'wat' WHERE id = 'task-aaa'"
        )
        conn.execute(
            "UPDATE kanban_tasks SET priority = 'critical' WHERE id = 'task-bbb'"
        )
        conn.commit()
        conn.close()

        r = client.get("/api/kanban/tasks?status=suggested&sort=priority")
        tasks = r.get_json()["tasks"]
        # task-bbb (critical) first, task-aaa (bogus) last
        assert tasks[0]["id"] == "task-bbb"
        assert tasks[-1]["id"] == "task-aaa"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
