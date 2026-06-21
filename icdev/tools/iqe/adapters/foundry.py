# CUI // SP-CTI
"""IQE ACF — Autonomous Capability Foundry — collection adapters.

Importing this module registers four read-only collections on the module-level
Executor, each returning REAL rows from the ``foundry_*`` platform tables:

  foundry.concepts  — synthesized product concepts (foundry_concepts); filter by
                      status, novelty_score, composite_score, run_id, reject_reason.
  foundry.signals   — harvested input signals (foundry_signals); filter by
                      source_engine, theme, raw_score, run_id.
  foundry.runs      — foundry cycle roll-ups (foundry_runs); filter by status,
                      concepts_approved, tasks_emitted.
  foundry.outcomes  — recorded build/ship outcomes (foundry_outcomes); filter by
                      outcome, concept_id, metric.

These are platform findings tables (NOT canvas tables): they carry
``tenant_id`` / ``classification`` so reads go through the RLS-aware
``tools.db.storage.get_connection()``. Every adapter wraps its query in
try/except and returns ``[]`` if the schema is not yet migrated, so the IQE
widget degrades to an empty result set rather than a 500.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def concepts_adapter(conn: Any) -> list[dict]:
    """Return rows from foundry_concepts (highest composite score first)."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, run_id, name, slug, problem_statement, proposed_capability, "
            "target_users, novelty_score, market_score, fit_score, effort_estimate, "
            "compliance_risk, composite_score, status, reject_reason, "
            "classification, created_by, created_at, updated_at "
            "FROM foundry_concepts "
            "ORDER BY composite_score DESC NULLS LAST, created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def signals_adapter(conn: Any) -> list[dict]:
    """Return rows from foundry_signals (highest raw score first)."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, run_id, source_engine, source_ref, theme, raw_score, "
            "classification, created_at "
            "FROM foundry_signals "
            "ORDER BY raw_score DESC NULLS LAST, created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def runs_adapter(conn: Any) -> list[dict]:
    """Return rows from foundry_runs (most recent cycle first)."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, cycle_at, harvested, concepts_proposed, concepts_approved, "
            "tasks_emitted, status, classification, created_at "
            "FROM foundry_runs ORDER BY cycle_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


def outcomes_adapter(conn: Any) -> list[dict]:
    """Return rows from foundry_outcomes enriched with concept name."""
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT o.id, o.concept_id, k.name AS concept_name, o.outcome, "
            "o.metric, o.classification, o.created_at "
            "FROM foundry_outcomes o "
            "LEFT JOIN foundry_concepts k ON k.id = o.concept_id "
            "ORDER BY o.created_at DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        c.close()


register_collection("foundry.concepts", concepts_adapter)
register_collection("foundry.signals", signals_adapter)
register_collection("foundry.runs", runs_adapter)
register_collection("foundry.outcomes", outcomes_adapter)
