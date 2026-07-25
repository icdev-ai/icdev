# CUI // SP-CTI
"""IQE AI/ML Model Canvas collection adapters.

Importing this module registers four collections on the module-level Executor:
  aimc.designs     — AI/ML model canvas designs (aiml_designs); filter by
                     classification, il_level, adaptation_strategy.
  aimc.nodes       — Canvas node inventory (aiml_nodes); filter by node_type,
                     classification, design_id.
  aimc.assessments — Compliance assessments per design (aiml_assessments);
                     filter by framework_id, score, passed.
  aimc.artifacts   — Generated artifacts (aiml_artifacts); filter by
                     artifact_type, design_id.

Note: AIMC is early-stage (v1). If tables are absent the adapters return []
gracefully rather than raising an exception.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _aimc_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.aiml_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def _safe_fetch(conn: Any, sql: str) -> list[dict]:
    """Execute *sql* and return rows; return [] if the table does not exist yet."""
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def designs_adapter(conn: Any) -> list[dict]:
    """Return rows from aiml_designs."""
    c, owned = _aimc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, name, description, classification, il_level, "
            "primary_use_case, adaptation_strategy, created_at, updated_at "
            "FROM aiml_designs",
        )
    finally:
        if owned:
            c.close()


def nodes_adapter(conn: Any) -> list[dict]:
    """Return rows from aiml_nodes."""
    c, owned = _aimc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, design_id, node_type, label, classification, created_at "
            "FROM aiml_nodes",
        )
    finally:
        if owned:
            c.close()


def assessments_adapter(conn: Any) -> list[dict]:
    """Return rows from aiml_assessments enriched with design name."""
    c, owned = _aimc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT a.id, a.design_id, d.name AS design_name, "
            "a.framework_id, a.framework_name, a.score, a.passed, a.created_at "
            "FROM aiml_assessments a "
            "LEFT JOIN aiml_designs d ON d.id = a.design_id",
        )
    finally:
        if owned:
            c.close()


def artifacts_adapter(conn: Any) -> list[dict]:
    """Return rows from aiml_artifacts."""
    c, owned = _aimc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, design_id, artifact_type, title, format, created_at "
            "FROM aiml_artifacts",
        )
    finally:
        if owned:
            c.close()


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for AIMC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='aimc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def twin_snapshots_adapter(conn: Any) -> list[dict]:
    """Return AIML twin snapshots (aiml_twin_snapshots) — the digital-twin surface."""
    c, owned = _aimc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, design_id, label, node_count, edge_count, created_by, created_at "
            "FROM aiml_twin_snapshots ORDER BY created_at DESC",
        )
    finally:
        if owned:
            c.close()


register_collection("aimc.designs", designs_adapter)
register_collection("aimc.nodes", nodes_adapter)
register_collection("aimc.assessments", assessments_adapter)
register_collection("aimc.artifacts", artifacts_adapter)
register_collection("aimc.ai_decisions", ai_decisions_adapter)
register_collection("aimc.twin_snapshots", twin_snapshots_adapter)
