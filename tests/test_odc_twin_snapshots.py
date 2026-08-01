# CUI // SP-CTI
"""list_snapshots ordering + payload coverage for tools/observability_canvas/twin.py.

test_odc_twin.py already covers the single-snapshot round trip, the no-assessment
marker, persist-failure, and the simulate/SLO projections. This file closes the
remaining gaps (obx-test-02):

  * list_snapshots orders newest-first across MANY snapshots and honours `limit`.
  * take_snapshot payload carries the basis fields (coverage_basis /
    coverage_score / service_count / label) and the persisted payload_json
    mirrors the returned dict.

Isolation mirrors test_odc_twin: a temp icdev.db seeded from MINIMAL_ICDEV_SCHEMA,
with the canvas getter monkeypatched (shim-aware) to a translating
StorageConnection. twin._now is stubbed to a monotonic clock so created_at
ordering is deterministic (real wall-clock snapshots can collide on identical
ISO timestamps).

NIST 800-53: AU-2, SA-11
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.conftest import MINIMAL_ICDEV_SCHEMA  # noqa: E402


@pytest.fixture
def twin_env(tmp_path, monkeypatch):
    db_path = tmp_path / "icdev.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(MINIMAL_ICDEV_SCHEMA)
    raw.commit()
    raw.close()

    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    from tools.db.storage import get_connection as storage_get_connection

    def _fake_conn():
        return storage_get_connection(str(db_path))

    init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")
    monkeypatch.setattr(init_db_mod, "get_connection", _fake_conn, raising=True)
    return db_path, _fake_conn


def _seed_design(conn, design_id, n_nodes=3):
    graph = {"nodes": [{"id": f"n{i}", "type": "src-app-log"} for i in range(n_nodes)], "edges": []}
    conn.execute(
        "INSERT INTO observability_designs (id, name, description, graph_json) VALUES (%s,%s,%s,%s)",
        (design_id, "twin-order", "seed", json.dumps(graph)),
    )
    conn.commit()


def _seed_assessment(conn, design_id, score):
    conn.execute(
        "INSERT INTO od_assessments (id, design_id, assessment_type, score, grade, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (str(uuid.uuid4()), design_id, "coverage", score, "B", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()


class _Clock:
    """Monotonic ISO timestamp stub so snapshot ordering is deterministic."""

    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"2026-01-01T00:00:{self.n:02d}+00:00"


def test_list_snapshots_newest_first(twin_env, monkeypatch):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")
    monkeypatch.setattr(twin, "_now", _Clock(), raising=True)

    design_id = "d-order"
    _seed_design(fake_conn(), design_id, n_nodes=4)

    labels = ["first", "second", "third", "fourth"]
    for lbl in labels:
        twin.take_snapshot(design_id, label=lbl)

    listed = twin.list_snapshots(design_id)
    assert [s["label"] for s in listed] == list(reversed(labels))
    # created_at strictly descending.
    times = [s["created_at"] for s in listed]
    assert times == sorted(times, reverse=True)
    # every row carries the design's node count.
    assert all(s["service_count"] == 4 for s in listed)


def test_list_snapshots_honours_limit(twin_env, monkeypatch):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")
    monkeypatch.setattr(twin, "_now", _Clock(), raising=True)

    design_id = "d-limit"
    _seed_design(fake_conn(), design_id, n_nodes=2)
    for i in range(5):
        twin.take_snapshot(design_id, label=f"snap-{i}")

    limited = twin.list_snapshots(design_id, limit=2)
    assert len(limited) == 2
    # The two newest (snap-4, snap-3) come back.
    assert [s["label"] for s in limited] == ["snap-4", "snap-3"]


def test_snapshot_payload_basis_fields_with_assessment(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-basis"
    conn = fake_conn()
    _seed_design(conn, design_id, n_nodes=6)
    _seed_assessment(conn, design_id, 81.0)

    snap = twin.take_snapshot(design_id, label="withassess")
    assert snap["coverage_basis"] == "od_assessments.score"
    assert snap["coverage_score"] == 81.0
    assert snap["service_count"] == 6
    assert snap["label"] == "withassess"

    # Persisted payload_json mirrors the returned dict.
    conn2 = fake_conn()
    row = conn2.execute(
        "SELECT payload_json, coverage_basis, service_count FROM odc_twin_snapshots WHERE id=%s",
        (snap["id"],),
    ).fetchone()
    conn2.close()
    payload = json.loads(row["payload_json"])
    assert payload["coverage_basis"] == "od_assessments.score"
    assert payload["coverage_score"] == 81.0
    assert payload["service_count"] == 6
    assert row["coverage_basis"] == "od_assessments.score"
    assert row["service_count"] == 6


def test_snapshot_default_label_when_unlabelled(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-defaultlabel"
    _seed_design(fake_conn(), design_id, n_nodes=1)
    snap = twin.take_snapshot(design_id)
    # Auto label derives from the snapshot date; basis is the no-assessment marker.
    assert snap["label"].startswith("snap-")
    assert snap["coverage_basis"] == "no_assessment"
    assert snap["coverage_score"] == 0.0
