# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""ACF — Autonomous Capability Foundry — Flask Blueprint.

:func:`create_foundry_blueprint` returns the canvas blueprint, or ``None`` when
the ``ICDEV_FOUNDRY_ENABLED`` feature flag is off, so ``app.py`` simply skips
registration (the canvas is fully dark when toggled off).

Pages (mounted under ``/foundry``):
  GET  /foundry                 cycle roll-up + proposed/scored/approved/rejected
                                concept list (RLS-filtered to the caller)
  GET  /foundry/<concept_id>    detail: scores + spec + emitted kanban tasks +
                                outcomes for one concept

JSON API:
  GET  /api/foundry/runs            recent foundry_runs
  GET  /api/foundry/concepts        concept list (status / run / limit filters)
  GET  /api/foundry/concept/<id>    one concept + spec + tasks + outcomes
  POST /api/foundry/run             run one cycle (delegates to ``foundry.engine``;
                                    503 when the engine module is not present)
  POST /foundry/api/iqe-query       plain-English → IQE → rows (shared widget)

SECURITY / RLS:
  Reads run through ``tools.db.storage.get_connection()``, which auto-attaches the
  request's ``g.security_context``. The ``foundry_*`` tables carry ``tenant_id`` /
  ``classification`` so the global RLS predicate filters every query to the
  caller's tenant + clearance. Writes (the optional ``/run`` endpoint) delegate to
  ``tools.foundry.engine`` which owns the novelty gate, CoD go/no-go approval, and
  the SIPA self-vet — this blueprint never mutates concept state directly.

Import-safe: the factory imports nothing from the (still-evolving) engine/synth
stack at module scope, and every handler wraps its backing call in ``try/except``
so a partially initialized schema (migration not yet run), a missing engine
module, or a missing page template degrades to an empty list / JSON fallback
instead of a 500.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from flask import Blueprint, jsonify, render_template, request

from tools.dashboard.auth import require_role
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.foundry.blueprint")

FEATURE_FLAG = "ICDEV_FOUNDRY_ENABLED"

# nav-sec-06: the ``/api/foundry/run`` compute trigger is a state-changing action
# (kicks off a harvest→synth→score→seed cycle). Before this fix it was gated only
# on "is authenticated". Restrict it to admin/pm; reads (runs/concepts/detail/IQE)
# stay open. Both roles appear in ``VALID_DASHBOARD_ROLES`` in tools/dashboard/auth.py.
_FOUNDRY_RUN_ROLES = ("admin", "pm")

# Concept lifecycle. Sourced from ``foundry.constants`` when that module exposes
# it; otherwise the documented default the synthesizer → scorer → deliberator
# pipeline emits (proposed → scored → approved | rejected).
try:  # pragma: no cover - trivial import guard
    from tools.foundry.constants import CONCEPT_STATUSES  # type: ignore
except Exception:  # noqa: BLE001
    CONCEPT_STATUSES = ("proposed", "scored", "approved", "rejected")

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #
def _is_enabled() -> bool:
    """True when ACF is toggled on via ``ICDEV_FOUNDRY_ENABLED``."""
    return str(os.environ.get(FEATURE_FLAG, "")).strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def _db():
    """An RLS-aware connection. ``get_connection`` auto-attaches the request's
    ``g.security_context`` so every read is filtered to the caller's tenant +
    classification (the foundry_* tables carry both columns)."""
    from tools.db.storage import get_connection

    return get_connection()


def _rows(cur) -> list[dict]:
    """Materialize a cursor into a list of plain dicts (backend-agnostic)."""
    desc = getattr(cur, "description", None)
    if not desc:
        return []
    cols = [d[0] for d in desc]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(cur) -> Optional[dict]:
    """Materialize a single cursor row into a dict, or ``None``."""
    desc = getattr(cur, "description", None)
    row = cur.fetchone()
    if row is None or not desc:
        return None
    return dict(zip([d[0] for d in desc], row))


def _json(value: Any) -> Any:
    """Decode a JSON(B) column, tolerating already-decoded objects (PG JSONB)
    and malformed / NULL text (SQLite)."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _q(conn, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for the PostgreSQL backend."""
    declared = getattr(conn, "_backend", None)
    if declared:
        is_pg = str(declared).lower().startswith(("postgre", "pg"))
    else:
        is_pg = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() in (
            "postgresql", "postgres", "pg",
        )
    return sql.replace("?", "%s") if is_pg else sql


def _limit_arg() -> int:
    """Parse a bounded ``?limit=`` query argument."""
    try:
        n = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        n = _DEFAULT_LIMIT
    return max(1, min(n, _MAX_LIMIT))


# --------------------------------------------------------------------------- #
# Queries (RLS-aware reads)
# --------------------------------------------------------------------------- #
def _list_runs(conn, *, limit: int = 10) -> list[dict]:
    sql = (
        "SELECT id, cycle_at, harvested, concepts_proposed, concepts_approved, "
        "tasks_emitted, status FROM foundry_runs ORDER BY cycle_at DESC LIMIT ?"
    )
    return _rows(conn.execute(_q(conn, sql), (limit,)))


def _list_concepts(conn, *, status: Optional[str], run_id: Optional[str], limit: int) -> list[dict]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, run_id, name, slug, problem_statement, proposed_capability, "
        "novelty_score, market_score, fit_score, effort_estimate, compliance_risk, "
        "composite_score, status, reject_reason, created_at "
        f"FROM foundry_concepts {clause} ORDER BY composite_score DESC NULLS LAST, created_at DESC LIMIT ?"
    )
    params.append(limit)
    return _rows(conn.execute(_q(conn, sql), tuple(params)))


def _get_concept(conn, concept_id: str) -> Optional[dict]:
    sql = (
        "SELECT id, run_id, name, slug, problem_statement, proposed_capability, "
        "target_users, cluster_signal_ids, novelty_score, market_score, fit_score, "
        "effort_estimate, compliance_risk, composite_score, status, reject_reason, "
        "classification, created_by, created_at, updated_at "
        "FROM foundry_concepts WHERE id = ?"
    )
    concept = _one(conn.execute(_q(conn, sql), (concept_id,)))
    if concept is not None:
        concept["cluster_signal_ids"] = _json(concept.get("cluster_signal_ids"))
    return concept


def _get_spec(conn, concept_id: str) -> Optional[dict]:
    sql = (
        "SELECT id, spec_md, canvas_contract, task_count, created_at "
        "FROM foundry_specs WHERE concept_id = ? ORDER BY created_at DESC LIMIT 1"
    )
    spec = _one(conn.execute(_q(conn, sql), (concept_id,)))
    if spec is not None:
        spec["canvas_contract"] = _json(spec.get("canvas_contract"))
    return spec


def _get_tasks_emitted(conn, concept_id: str) -> list[dict]:
    sql = (
        "SELECT id, kanban_task_id, epic, seq, created_at FROM foundry_tasks_emitted "
        "WHERE concept_id = ? ORDER BY seq ASC, id ASC"
    )
    tasks = _rows(conn.execute(_q(conn, sql), (concept_id,)))
    return _attach_kanban_status(conn, tasks)


def _attach_kanban_status(conn, tasks: list[dict]) -> list[dict]:
    """Best-effort live status for each emitted kanban task.

    Adds a ``kanban_status`` key by reading ``kanban_tasks.status`` for the
    emitted ``kanban_task_id``. Wrapped so a missing kanban table, an RLS-shielded
    row, or a backend quirk leaves ``kanban_status=None`` rather than failing the
    whole detail view."""
    for t in tasks:
        t["kanban_status"] = None
    ids = [t.get("kanban_task_id") for t in tasks if t.get("kanban_task_id")]
    if not ids:
        return tasks
    try:
        placeholders = ", ".join("?" for _ in ids)
        sql = f"SELECT id, status FROM kanban_tasks WHERE id IN ({placeholders})"
        live = {r["id"]: r.get("status") for r in _rows(conn.execute(_q(conn, sql), tuple(ids)))}
        for t in tasks:
            t["kanban_status"] = live.get(t.get("kanban_task_id"))
    except Exception as exc:  # noqa: BLE001 — kanban table absent / RLS / backend quirk
        logger.debug("kanban live-status enrichment skipped: %s", exc)
    return tasks


def _get_outcomes(conn, concept_id: str) -> list[dict]:
    sql = (
        "SELECT id, outcome, metric, detail, created_at FROM foundry_outcomes "
        "WHERE concept_id = ? ORDER BY created_at DESC"
    )
    outcomes = _rows(conn.execute(_q(conn, sql), (concept_id,)))
    for o in outcomes:
        o["detail"] = _json(o.get("detail"))
    return outcomes


def _signal_count(conn) -> int:
    try:
        cur = conn.execute(_q(conn, "SELECT COUNT(*) FROM foundry_signals"))
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _concept_detail(conn, concept_id: str) -> Optional[dict]:
    concept = _get_concept(conn, concept_id)
    if concept is None:
        return None
    return {
        "concept": concept,
        "spec": _get_spec(conn, concept_id),
        "tasks_emitted": _get_tasks_emitted(conn, concept_id),
        "outcomes": _get_outcomes(conn, concept_id),
    }


def _status_rollup(concepts: list[dict]) -> dict:
    by_status = {s: 0 for s in CONCEPT_STATUSES}
    for c in concepts:
        st = c.get("status")
        if st:
            by_status[st] = by_status.get(st, 0) + 1
    return {"by_status": by_status, "total": len(concepts)}


# --------------------------------------------------------------------------- #
# Blueprint factory
# --------------------------------------------------------------------------- #
def create_foundry_blueprint() -> Optional[Blueprint]:
    """Build the ACF canvas blueprint, or ``None`` when the canvas is toggled off.

    Returns ``None`` if ``ICDEV_FOUNDRY_ENABLED`` is not truthy so ``app.py`` can
    register-or-skip with a single guard. When enabled, returns a blueprint serving
    the two ``/foundry`` pages and the ``/api/foundry`` JSON endpoints.
    """
    if not _is_enabled():
        logger.info("foundry blueprint: %s is off — canvas not mounted", FEATURE_FLAG)
        return None

    bp = Blueprint("foundry", __name__)

    # Idempotent one-time schema guard (CREATE-IF-NOT-EXISTS), fired once on the
    # blueprint's first served request so we don't open/close a connection per req.
    _state = {"db_ready": False}

    @bp.before_request
    def _ensure_db() -> None:
        if _state["db_ready"]:
            return
        try:
            from tools.foundry.db.init_db import init_db as init_foundry_db

            init_foundry_db()
        except Exception as exc:  # noqa: BLE001 — never let init failure 500 a read
            logger.warning("foundry before_request init_db unavailable: %s", exc)
        finally:
            _state["db_ready"] = True

    # ── Pages ─────────────────────────────────────────────────────────────── #
    @bp.route("/foundry")
    @bp.route("/foundry/")
    def foundry_index():
        """Cycle roll-up + concept list (RLS-filtered to the caller)."""
        runs: list[dict] = []
        concepts: list[dict] = []
        signals = 0
        conn = None
        try:
            conn = _db()
            runs = _list_runs(conn, limit=10)
            concepts = _list_concepts(conn, status=None, run_id=None, limit=_DEFAULT_LIMIT)
            signals = _signal_count(conn)
        except Exception as exc:  # noqa: BLE001 — empty state before first cycle / migration
            logger.warning("foundry_index read failed: %s", exc)
        finally:
            if conn is not None:
                conn.close()

        summary = _status_rollup(concepts)
        latest_run = runs[0] if runs else None
        try:
            return render_template(
                "foundry/index.html",
                runs=runs,
                latest_run=latest_run,
                concepts=concepts,
                summary=summary,
                signal_count=signals,
                statuses=CONCEPT_STATUSES,
            )
        except Exception as exc:  # noqa: BLE001 — JSON fallback if template missing
            logger.info("foundry/index.html unavailable (%s); JSON fallback", exc)
            return jsonify(
                {"runs": runs, "concepts": concepts, "summary": summary, "signal_count": signals}
            )

    @bp.route("/foundry/<concept_id>")
    def foundry_detail(concept_id: str):
        """Detail page: scores + spec + emitted kanban tasks + outcomes."""
        detail = None
        conn = None
        try:
            conn = _db()
            detail = _concept_detail(conn, concept_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("foundry_detail read failed for %s: %s", concept_id, exc)
        finally:
            if conn is not None:
                conn.close()

        if detail is None:
            return jsonify({"error": f"concept not found: {concept_id}"}), 404

        try:
            return render_template(
                "foundry/detail.html",
                concept_id=concept_id,
                statuses=CONCEPT_STATUSES,
                **detail,
            )
        except Exception as exc:  # noqa: BLE001 — JSON fallback if template missing
            logger.info("foundry/detail.html unavailable (%s); JSON fallback", exc)
            return jsonify(detail)

    # ── JSON API — reads ──────────────────────────────────────────────────── #
    @bp.route("/api/foundry/runs", methods=["GET"])
    def api_runs():
        conn = None
        try:
            conn = _db()
            items = _list_runs(conn, limit=_limit_arg())
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_runs read failed: %s", exc)
            items = []
        finally:
            if conn is not None:
                conn.close()
        return jsonify({"runs": items, "count": len(items)})

    @bp.route("/api/foundry/concepts", methods=["GET"])
    def api_concepts():
        status = request.args.get("status")
        run_id = request.args.get("run_id")
        conn = None
        try:
            conn = _db()
            items = _list_concepts(conn, status=status, run_id=run_id, limit=_limit_arg())
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_concepts read failed: %s", exc)
            items = []
        finally:
            if conn is not None:
                conn.close()
        return jsonify({"concepts": items, "count": len(items)})

    @bp.route("/api/foundry/concept/<concept_id>", methods=["GET"])
    def api_concept(concept_id: str):
        conn = None
        try:
            conn = _db()
            detail = _concept_detail(conn, concept_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_concept read failed for %s: %s", concept_id, exc)
            detail = None
        finally:
            if conn is not None:
                conn.close()
        if detail is None:
            return jsonify({"error": f"concept not found: {concept_id}"}), 404
        return jsonify(detail)

    # ── JSON API — run one cycle (delegates to the engine) ────────────────── #
    @bp.route("/api/foundry/run", methods=["POST"])
    @require_role(*_FOUNDRY_RUN_ROLES)
    def api_run():
        """Trigger one foundry cycle. Delegates to ``tools.foundry.engine`` which
        owns harvest → synth → novelty-gate → score → CoD go/no-go → SIPA self-vet
        → seed. Returns 503 when the engine module is not yet present so the page
        degrades gracefully rather than 500-ing."""
        body = request.get_json(silent=True) or {}
        dry_run = bool(body.get("dry_run", False))
        try:
            from tools.foundry.engine import run_cycle  # type: ignore
        except Exception as exc:  # noqa: BLE001
            logger.info("foundry engine unavailable: %s", exc)
            return jsonify({"error": "foundry engine not available", "detail": str(exc)}), 503
        try:
            result = run_cycle(dry_run=dry_run)
        except TypeError:
            # Tolerate a run_cycle() that does not accept dry_run.
            result = run_cycle()
        except Exception as exc:  # noqa: BLE001
            logger.warning("foundry run_cycle failed: %s", exc)
            return jsonify({"error": "cycle failed", "detail": str(exc)}), 500
        if not isinstance(result, dict):
            result = {"result": str(result)}
        return jsonify(result)

    # ── JSON API — IQE natural-language query ─────────────────────────────── #
    @bp.route("/foundry/api/iqe-query", methods=["POST"])
    def foundry_iqe_query():
        """Plain-English → IQE → rows over the foundry_* collections.

        Mirrors the canvas-aware dispatcher in ``app.py`` so the shared
        ``includes/iqe_query_widget.html`` widget renders the generated IQE plus
        the matching rows. The IQE adapter (``tools/iqe/adapters/foundry.py``)
        registers the collections; this route degrades to a JSON error if the
        adapter is not yet present (shipped by the sibling IQE task)."""
        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        collections = body.get("collections") or [
            "foundry.concepts", "foundry.signals", "foundry.runs", "foundry.outcomes",
        ]
        iqe_str = ""
        try:
            from tools.iqe import adapters as _adapters  # noqa: F401
            from tools.iqe.adapters import foundry as _  # noqa: F401  registers collections
            from tools.iqe.executor import execute_query
            from tools.iqe.nl_to_iqe import nl_to_iqe
            from tools.iqe.parser import parse as iqe_parse

            translated = nl_to_iqe(question, collections)
            iqe_str = translated.get("iqe", "")
            ast = iqe_parse(iqe_str)
            rows = execute_query(ast, conn=None)
            return jsonify({"ok": True, "canvas": "foundry", "iqe": iqe_str, "rows": rows})
        except Exception as exc:  # noqa: BLE001
            logger.warning("foundry iqe-query error: %s", exc)
            return jsonify({"error": str(exc), "canvas": "foundry", "iqe": iqe_str}), 500

    logger.info("foundry blueprint mounted (%s on)", FEATURE_FLAG)
    return bp
