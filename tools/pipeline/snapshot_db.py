#!/usr/bin/env python3
# CUI // SP-CTI
"""Pipeline snapshot CRUD — append-only DAG snapshot store.

.. deprecated:: pdx-data-01
    DEPRECATED in favor of the ``pdc_snapshots`` canvas table (see
    ``tools/pipeline/db/init_db.py`` and ``tools.pipeline.twin.take_snapshot``),
    which is the store actually written on every pipeline save and read by the
    ``/devops/twin`` delta view (``tools.pipeline.delta``) and the IQE pipeline
    adapters. This module targets ``pipeline_snapshots`` in the shared icdev DB,
    a table nothing in ``tools/`` writes; it has no remaining runtime callers.
    Do not wire new code to it. It is retained (not deleted) because migration
    027 and the APPEND_ONLY registration for ``pipeline_snapshots`` are owned by
    sibling tasks. NOTE: ``get_connection()`` here carries no security context,
    so calling these functions inside a Flask request would attach the global
    RLS predicate and fail (``pipeline_snapshots`` lacks tenant_id/classification
    columns) — another reason to use ``pdc_snapshots`` via the canvas connection.

Thin DB layer over the pipeline_snapshots table (migration 027).
Snapshots are immutable once written; call create_snapshot to record a
new state rather than updating an existing row.

Usage::

    snap_id = create_snapshot(
        pipeline_id="pipe-abc",
        label="pre-merge baseline",
        nodes_json='[{"id":"n1","type":"build-runner"}]',
        edges_json='[{"source":"n1","target":"n2"}]',
    )
    row = get_snapshot(snap_id)
    rows = list_snapshots("pipe-abc", limit=10)
    latest = get_latest_snapshot("pipe-abc")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tools.db.storage import get_connection


def create_snapshot(
    pipeline_id: str,
    nodes_json: str | list,
    edges_json: str | list,
    *,
    label: str | None = None,
    snapshot_type: str = "baseline",
    meta_json: str | dict | None = None,
    created_by: str | None = None,
) -> str:
    """Insert a new pipeline snapshot and return its snapshot_id.

    Accepts raw JSON strings or Python objects for nodes/edges/meta.
    """
    if not isinstance(nodes_json, str):
        nodes_json = json.dumps(nodes_json)
    if not isinstance(edges_json, str):
        edges_json = json.dumps(edges_json)
    if meta_json is None:
        meta_json = "{}"
    elif not isinstance(meta_json, str):
        meta_json = json.dumps(meta_json)

    snap_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO pipeline_snapshots
                (id, pipeline_id, snapshot_type, label, nodes_json, edges_json, meta_json, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (snap_id, pipeline_id, snapshot_type, label, nodes_json, edges_json, meta_json, created_by, now),
        )
        conn.commit()
    finally:
        conn.close()

    return snap_id


def get_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    """Return a snapshot row as a dict, or None if not found."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM pipeline_snapshots WHERE id = %s",
            (snapshot_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return dict(row)


def list_snapshots(pipeline_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return snapshots for a pipeline ordered newest-first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM pipeline_snapshots
            WHERE pipeline_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (pipeline_id, limit),
        ).fetchall()
    finally:
        conn.close()

    return [dict(r) for r in rows]


def get_latest_snapshot(pipeline_id: str, snapshot_type: str = "baseline") -> dict[str, Any] | None:
    """Return the most recent snapshot of a given type for a pipeline."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM pipeline_snapshots
            WHERE pipeline_id = %s AND snapshot_type = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (pipeline_id, snapshot_type),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return dict(row)
