# CUI // SP-CTI
"""ICDEV™ Data Mesh — OpenLineage Emission Wrapper.

Emits lineage events to an OpenLineage/Marquez server when the
``openlineage-python`` package is installed, or falls back to writing
internal records into the ``dd_lineage`` table.

No Flask dependency.  No LLM dependency.  Deterministic execution only.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

_HAS_OL = bool(importlib.util.find_spec("openlineage"))

_MARQUEZ_URL = os.environ.get("ICDEV_MARQUEZ_URL", "http://localhost:5000")
_DEFAULT_NAMESPACE = os.environ.get("ICDEV_OL_NAMESPACE", "icdev")
_DEFAULT_CLASSIFICATION = "CUI"

logger = get_logger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_db():
    """Return data-canvas DB connection (canvas-scoped, no RLS)."""
    from tools.data_canvas.db.init_db import get_connection
    return get_connection()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_internal_event(
    run_id: str,
    job_name: str,
    inputs: list[str],
    outputs: list[str],
    state: str,
    design_id: str = "",
) -> None:
    """Write a synthetic lineage event into dd_lineage as internal fallback."""
    conn = _get_db()
    try:
        for src in inputs or [""]:
            for tgt in outputs or [""]:
                edge_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO dd_lineage
                        (id, design_id, source_node_id, target_node_id,
                         lineage_type, column_name, transform_desc, classification, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        design_id or run_id,
                        src or run_id,
                        tgt or run_id,
                        "flow",
                        "",
                        f"ol_event job={job_name} state={state}",
                        _DEFAULT_CLASSIFICATION,
                        _now_iso(),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


# ── public API ────────────────────────────────────────────────────────────────

def emit_lineage_event(
    run_id: str,
    job_name: str,
    inputs: list[str],
    outputs: list[str],
    state: str = "COMPLETE",
) -> dict[str, Any]:
    """Emit an OpenLineage RunEvent for a data pipeline step.

    Args:
        run_id:   Unique identifier for this pipeline run (UUID string).
        job_name: Human-readable job/task name.
        inputs:   List of input dataset names or URIs.
        outputs:  List of output dataset names or URIs.
        state:    OpenLineage run state — ``START``, ``COMPLETE``, or ``FAIL``.

    Returns:
        ``{emitted: bool, method: 'openlineage'|'internal', run_id: str}``
    """
    if _HAS_OL:
        try:
            from openlineage.client import OpenLineageClient
            from openlineage.client.run import (
                Dataset,
                Job,
                Run,
                RunEvent,
                RunState,
            )

            ol_state = getattr(RunState, state, RunState.COMPLETE)
            client = OpenLineageClient.from_environment()
            if not client.transport or not client.transport.url:
                # Override URL from env when transport is unconfigured
                from openlineage.client.transport.http import HttpConfig, HttpTransport
                client = OpenLineageClient(
                    transport=HttpTransport(HttpConfig(url=_MARQUEZ_URL))
                )

            event = RunEvent(
                eventType=ol_state,
                eventTime=_now_iso(),
                run=Run(runId=run_id),
                job=Job(namespace=_DEFAULT_NAMESPACE, name=job_name),
                inputs=[Dataset(namespace=_DEFAULT_NAMESPACE, name=n) for n in (inputs or [])],
                outputs=[Dataset(namespace=_DEFAULT_NAMESPACE, name=n) for n in (outputs or [])],
                producer="https://icdev.io/lineage",
            )
            client.emit(event)
            logger.info("OpenLineage event emitted: run_id=%s job=%s state=%s", run_id, job_name, state)
            return {"emitted": True, "method": "openlineage", "run_id": run_id}

        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenLineage emit failed (%s); falling back to internal", exc)

    # Fallback: internal dd_lineage table
    _insert_internal_event(run_id, job_name, inputs, outputs, state)
    logger.info("Internal lineage event stored: run_id=%s job=%s", run_id, job_name)
    return {"emitted": True, "method": "internal", "run_id": run_id}


def emit_data_product_lineage(product_id: str, design_id: str) -> dict[str, Any]:
    """Emit lineage for a data product by walking its dd_lineage edges.

    Reads all lineage edges for ``design_id`` from the ``dd_lineage`` table
    and emits them as OpenLineage DatasetFacets linking the data product to
    its provenance chain.

    Args:
        product_id: Identifier of the data product (dm_data_products.id).
        design_id:  The data design whose lineage edges describe this product.

    Returns:
        ``{emitted: bool, method: str, edges_emitted: int, run_id: str}``
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM dd_lineage WHERE design_id = ?", (design_id,)
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"emitted": False, "method": "none", "edges_emitted": 0, "run_id": ""}

    run_id = str(uuid.uuid4())
    edges_emitted = 0

    if _HAS_OL:
        try:
            from openlineage.client import OpenLineageClient
            from openlineage.client.facet import (
                DatasetFacet,
                SchemaDatasetFacet,
                SchemaField,
            )
            from openlineage.client.run import (
                Dataset,
                Job,
                Run,
                RunEvent,
                RunState,
            )
            from openlineage.client.transport.http import HttpConfig, HttpTransport

            client = OpenLineageClient(
                transport=HttpTransport(HttpConfig(url=_MARQUEZ_URL))
            )

            for row in rows:
                src = row["source_node_id"] if isinstance(row, dict) else row[2]
                tgt = row["target_node_id"] if isinstance(row, dict) else row[3]
                col = (row["column_name"] if isinstance(row, dict) else row[5]) or ""

                facets: dict[str, Any] = {
                    "dataProduct": DatasetFacet(
                        _producer="https://icdev.io/lineage",
                        _schemaURL="https://icdev.io/schemas/data-product",
                    )
                }
                if col:
                    facets["schema"] = SchemaDatasetFacet(
                        fields=[SchemaField(name=col, type="unknown")]
                    )

                event = RunEvent(
                    eventType=RunState.COMPLETE,
                    eventTime=_now_iso(),
                    run=Run(runId=run_id),
                    job=Job(
                        namespace=_DEFAULT_NAMESPACE,
                        name=f"data_product.{product_id}",
                    ),
                    inputs=[Dataset(namespace=_DEFAULT_NAMESPACE, name=src)],
                    outputs=[Dataset(namespace=_DEFAULT_NAMESPACE, name=tgt, facets=facets)],
                    producer="https://icdev.io/lineage",
                )
                client.emit(event)
                edges_emitted += 1

            logger.info(
                "Data product lineage emitted via OpenLineage: product=%s edges=%d",
                product_id,
                edges_emitted,
            )
            return {
                "emitted": True,
                "method": "openlineage",
                "edges_emitted": edges_emitted,
                "run_id": run_id,
            }

        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenLineage data-product emit failed (%s); falling back to internal", exc)

    # Fallback: re-insert as tagged internal events so the provenance chain is preserved
    conn = _get_db()
    try:
        for row in rows:
            src = row["source_node_id"] if isinstance(row, dict) else row[2]
            tgt = row["target_node_id"] if isinstance(row, dict) else row[3]
            conn.execute(
                """
                INSERT OR IGNORE INTO dd_lineage
                    (id, design_id, source_node_id, target_node_id,
                     lineage_type, column_name, transform_desc, classification, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    design_id,
                    src,
                    tgt,
                    "flow",
                    "",
                    f"product_lineage product_id={product_id}",
                    _DEFAULT_CLASSIFICATION,
                    _now_iso(),
                ),
            )
            edges_emitted += 1
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "Data product lineage stored internally: product=%s edges=%d",
        product_id,
        edges_emitted,
    )
    return {
        "emitted": True,
        "method": "internal",
        "edges_emitted": edges_emitted,
        "run_id": run_id,
    }
