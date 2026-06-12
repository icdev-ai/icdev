#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 037: Create nc_simulation_sessions, nc_simulation_runs, nc_simulation_artifacts.

Network Canvas simulation history tables. canvas_type column enables cross-canvas
session history. All three tables are append-only (NIST AU).

  nc_simulation_sessions — one session per topology/mode invocation
  nc_simulation_runs     — one or more runs per session (step logs)
  nc_simulation_artifacts — output artifacts produced by a run
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS nc_simulation_sessions (
    id           TEXT        NOT NULL,
    canvas_type  TEXT        NOT NULL,
    topology_id  TEXT,
    mode         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata     JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (id)
)
"""

_DDL_SESSIONS_SQLITE = """
CREATE TABLE IF NOT EXISTS nc_simulation_sessions (
    id           TEXT NOT NULL,
    canvas_type  TEXT NOT NULL,
    topology_id  TEXT,
    mode         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    metadata     TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (id)
)
"""

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS nc_simulation_runs (
    id          TEXT        NOT NULL,
    session_id  TEXT        NOT NULL REFERENCES nc_simulation_sessions(id),
    run_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    steps       JSONB       NOT NULL DEFAULT '[]',
    summary     TEXT,
    PRIMARY KEY (id)
)
"""

_DDL_RUNS_SQLITE = """
CREATE TABLE IF NOT EXISTS nc_simulation_runs (
    id          TEXT NOT NULL,
    session_id  TEXT NOT NULL REFERENCES nc_simulation_sessions(id),
    run_at      TEXT NOT NULL DEFAULT (datetime('now')),
    steps       TEXT NOT NULL DEFAULT '[]',
    summary     TEXT,
    PRIMARY KEY (id)
)
"""

_DDL_ARTIFACTS = """
CREATE TABLE IF NOT EXISTS nc_simulation_artifacts (
    id            TEXT        NOT NULL,
    run_id        TEXT        NOT NULL REFERENCES nc_simulation_runs(id),
    artifact_type TEXT        NOT NULL,
    content       TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
)
"""

_DDL_ARTIFACTS_SQLITE = """
CREATE TABLE IF NOT EXISTS nc_simulation_artifacts (
    id            TEXT NOT NULL,
    run_id        TEXT NOT NULL REFERENCES nc_simulation_runs(id),
    artifact_type TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (id)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_nc_sim_sessions_canvas_type ON nc_simulation_sessions(canvas_type)",
    "CREATE INDEX IF NOT EXISTS idx_nc_sim_sessions_topology_id ON nc_simulation_sessions(topology_id)",
    "CREATE INDEX IF NOT EXISTS idx_nc_sim_runs_session_id ON nc_simulation_runs(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_nc_sim_artifacts_run_id ON nc_simulation_artifacts(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_nc_sim_artifacts_type ON nc_simulation_artifacts(artifact_type)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        if backend == "postgresql":
            conn.execute(_DDL_SESSIONS)
            conn.execute(_DDL_RUNS)
            conn.execute(_DDL_ARTIFACTS)
        else:
            conn.execute(_DDL_SESSIONS_SQLITE)
            conn.execute(_DDL_RUNS_SQLITE)
            conn.execute(_DDL_ARTIFACTS_SQLITE)
        for idx in _INDEXES:
            conn.execute(idx)
        conn.commit()
        print("[037_nc_simulation_tables] up: nc_simulation_sessions/runs/artifacts created (or already exist)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
