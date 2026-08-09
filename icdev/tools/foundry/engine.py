# CUI // SP-CTI
"""engine.py — ACF (Autonomous Capability Foundry) orchestration engine + CLI.

The brain of the foundry loop. ``run_cycle()`` opens a ``foundry_runs`` row and
orchestrates one full cycle::

    harvest -> synthesize -> novelty-gate -> score -> CoD go/no-go ->
    spec_generator -> task_graph -> seeder.emit

persisting at each step and finalizing the run row with the roll-up counts. Each
stage is invoked through a small signature-aware wrapper so the engine stays
forward-compatible while the sibling stage modules land: a stage whose module is
absent (``synthesizer`` / ``scorer`` / ``deliberator`` / ``seeder``) degrades to a
clean no-op (logs, contributes nothing) instead of crashing the cycle — the exact
graceful-degradation pattern used across the rest of the foundry canvas
(``blueprint.py``, ``reflexes/foundry_cycle.py``, ``novelty_gate.py``). The
already-shipped stages (``harvester`` / ``novelty_gate`` / ``spec_generator`` /
``task_graph``) run for real.

Rate limits (acf-engine-02) are enforced BEFORE emit, from
``args/foundry_config.yaml -> rate_limits``:

  * ``max_concepts_per_cycle`` — approved concepts are capped to this count so one
    noisy harvest can't flood the board.
  * ``max_active_projects`` — if the number of distinct ACF-owned projects with
    un-done kanban tasks is already at/above this cap, the cycle skips emit and the
    run is recorded ``status='rate_limited'``.

``dry_run=True`` runs the full pipeline (harvest/synth/score/CoD/spec/task-graph)
but the seeder does NOT write to ``kanban_tasks`` — useful for smoke tests and the
reflex's dry-run mirror.

All DB access goes through the RLS-aware ``tools.db.storage.get_connection()``
(never ``get_canvas_connection()``) with the caller's tenant_id / classification
stamped — ``foundry_*`` are platform tables, not canvas tables.

Public API
----------
    run_cycle(*, dry_run=False, max_concepts=None, config=None, conn=None) -> dict
    status(*, conn=None, limit=10) -> dict

CLI
---
    python tools/foundry/engine.py --run [--dry-run] [--json]
    python tools/foundry/engine.py --status [--json]
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Optional

try:  # optional — only needed to read args/foundry_config.yaml
    import yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover - yaml is a core dep but stay defensive
    yaml = None  # type: ignore
    _HAS_YAML = False




# =========================================================================
# PATH / CONFIG
# =========================================================================
def _find_repo_root() -> Path:
    """Anchor on a marker that exists only at the true repo root (handles the
    tools/ + icdev/tools/ mirror)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CLAUDE.md").exists() or (parent / "goals" / "manifest.md").exists():
            return parent
    return here.parent.parent.parent


BASE_DIR = _find_repo_root()
CONFIG_PATH = BASE_DIR / "args" / "foundry_config.yaml"

# Defaults mirror args/foundry_config.yaml so the engine behaves identically when
# the config file is missing/unreadable (air-gap safe).
_DEFAULT_RATE_LIMITS = {"max_concepts_per_cycle": 5, "max_active_projects": 3}
_DEFAULT_SCORING = {"min_composite": 0.6}
_DEFAULT_DELIBERATION = {"enabled": True, "defer_to_score_on_fallback": True}
_DEFAULT_CIRCUIT = {"vv_fail_rate": 0.5, "window": 10}
_DEFAULT_SELF_VET = {"require_integrity_gate": True, "require_security_gate": True}


def _load_config(config: Optional[dict]) -> dict:
    """Full foundry_config dict (or the passed override)."""
    if config is not None:
        return config
    if _HAS_YAML and CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("foundry_config.yaml unreadable (%s); using defaults", exc)
    return {}


def _rate_limits(cfg: dict) -> dict:
    rl = dict(_DEFAULT_RATE_LIMITS)
    src = cfg.get("rate_limits") or {}
    if isinstance(src, dict):
        for key in _DEFAULT_RATE_LIMITS:
            if src.get(key) is not None:
                rl[key] = int(src[key])
    return rl


def _caller_context() -> tuple[str, str]:
    """(tenant_id, classification) from the active security context, with platform
    defaults when no request context is bound (mirrors harvester._caller_context)."""
    try:
        from tools.security.security_context import get_security_context

        ctx = get_security_context()
    except Exception:
        ctx = None
    tenant_id = (getattr(ctx, "tenant_id", None) or "default") if ctx else "default"
    classification = (getattr(ctx, "classification", None) or "CUI") if ctx else "CUI"
    return tenant_id, classification


# =========================================================================
# STAGE WIRING (signature-aware; degrades when a stage module is absent)
# =========================================================================
def _opt_module(name: str) -> Any:
    """Import ``tools.foundry.<name>`` (or its icdev mirror); None if absent."""
    for mod in (f"tools.foundry.{name}", f"icdev.tools.foundry.{name}"):
        try:
            return importlib.import_module(mod)
        except Exception:  # noqa: BLE001 - stage not shipped yet / import error -> degrade
            continue
    return None


def _flex_call(fn: Any, **kwargs: Any) -> Any:
    """Call *fn* forwarding only the keyword args it actually declares.

    Keeps the engine forward-compatible with sibling stage modules whose exact
    signatures may still be evolving (same approach as the foundry_cycle reflex's
    ``_call_run_cycle``). A ``**kwargs``-accepting callable receives everything.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return fn(**kwargs)
    accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return fn(**accepted)


def _stage_harvest(run_id: str, cfg: dict, conn: Any) -> list[dict]:
    """Stage 1 — harvest signals (shipped: tools.foundry.harvester)."""
    try:
        from tools.foundry import harvester

        return list(_flex_call(harvester.harvest, run_id=run_id, config=cfg, conn=conn) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("harvest stage degraded: %s", exc)
        return []


def _stage_synthesize(run_id: str, signals: list[dict], cfg: dict, conn: Any) -> list[dict]:
    """Stage 2 — cluster signals into concepts (tools.foundry.synthesizer).

    Absent until acf-synth-01 lands -> contributes zero concepts.
    """
    mod = _opt_module("synthesizer")
    fn = getattr(mod, "synthesize", None) if mod else None
    if fn is None:
        logger.info("synthesize stage skipped (synthesizer not available)")
        return []
    try:
        return list(_flex_call(fn, run_id=run_id, signals=signals, config=cfg, conn=conn) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("synthesize stage degraded: %s", exc)
        return []


def _stage_evaluate(concepts: list[dict], cfg: dict, conn: Any) -> list[dict]:
    """Stages 3-5 — novelty gate (shipped) -> score (scorer) -> CoD go/no-go
    (deliberator). Returns the list of APPROVED concepts.

    novelty_gate is applied for real (it is shipped). scorer / deliberator are
    optional: when absent, approval falls back to the deterministic score gate
    (``scoring.min_composite``) iff ``deliberation.defer_to_score_on_fallback``.
    """
    if not concepts:
        return []

    # Stage 3 — novelty gate (drops duplicates / low-novelty in place).
    survivors: list[dict] = []
    try:
        from tools.foundry import novelty_gate

        for c in concepts:
            novelty_gate.apply_novelty_gate(c, config=cfg.get("novelty"))
            if c.get("status") != "rejected":
                survivors.append(c)
    except Exception as exc:  # noqa: BLE001 - degrade to all-pass rather than abort
        logger.warning("novelty stage degraded: %s", exc)
        survivors = list(concepts)

    # Stage 4 — composite score (optional scorer module).
    scorer = _opt_module("scorer")
    score_fn = getattr(scorer, "score", None) if scorer else None
    if score_fn is not None:
        for c in survivors:
            try:
                _flex_call(score_fn, concept=c, config=cfg, conn=conn)
            except Exception as exc:  # noqa: BLE001
                logger.debug("score skip for %s: %s", c.get("slug"), exc)

    # Stage 5 — CoD go/no-go (optional deliberator module), else score-gate fallback.
    delib = _opt_module("deliberator")
    delib_fn = getattr(delib, "deliberate_concept", None) if delib else None
    delib_cfg = cfg.get("deliberation") or _DEFAULT_DELIBERATION
    min_composite = float((cfg.get("scoring") or _DEFAULT_SCORING).get("min_composite", 0.6))
    defer_to_score = bool(delib_cfg.get("defer_to_score_on_fallback", True))

    approved: list[dict] = []
    for c in survivors:
        decision: Optional[str] = None
        if delib_fn is not None:
            try:
                verdict = _flex_call(delib_fn, concept=c, config=cfg, conn=conn)
                if isinstance(verdict, dict):
                    decision = str(verdict.get("decision") or verdict.get("verdict") or "")
                else:
                    decision = str(verdict or "")
            except Exception as exc:  # noqa: BLE001 - fall through to score gate
                logger.debug("deliberate skip for %s: %s", c.get("slug"), exc)
        if decision is None or decision == "":
            # No deliberator (or it errored): defer to the deterministic score gate.
            if not defer_to_score:
                continue
            decision = "build" if float(c.get("composite_score", 0.0) or 0.0) >= min_composite else "no_build"
        if decision in ("build", "go", "approve", "approved"):
            c["status"] = "approved"
            approved.append(c)
        else:
            c["status"] = "rejected"
            c.setdefault("reject_reason", "cod_reject")
    return approved


def _stage_emit(approved: list[dict], cfg: dict, conn: Any, *, dry_run: bool) -> int:
    """Stages 6-8 — spec_generator -> task_graph -> seeder.emit. Returns the number
    of kanban tasks emitted (0 in dry_run, or when the seeder module is absent).

    spec_generator + task_graph are shipped and run for real; the seeder
    (acf-design-03) is optional until it lands — without it nothing is written.
    """
    if not approved:
        return 0
    try:
        from tools.foundry import spec_generator, task_graph
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit stage degraded (spec/task_graph unavailable): %s", exc)
        return 0

    seeder = _opt_module("seeder")
    emit_fn = getattr(seeder, "emit", None) if seeder else None
    if emit_fn is None and not dry_run:
        logger.info("emit stage skipped (seeder not available) — no tasks written")

    total = 0
    for concept in approved:
        try:
            spec = spec_generator.generate_spec(concept, config=cfg, conn=conn, persist=not dry_run)
            contract = spec.get("canvas_contract") or {}
            tasks = task_graph.build_task_graph(concept, contract)
        except Exception as exc:  # noqa: BLE001 - one concept failing must not abort the rest
            logger.warning("spec/task-graph failed for %s: %s", concept.get("slug"), exc)
            continue
        if dry_run or emit_fn is None:
            # Full pipeline ran; just don't write. Count the would-be tasks only in
            # the returned detail (not as emitted) to keep tasks_emitted honest.
            total += 0
            continue
        try:
            res = _flex_call(emit_fn, concept=concept, tasks=tasks, dry_run=dry_run, conn=conn)
            if isinstance(res, dict):
                total += int(res.get("emitted", 0) or 0)
            elif isinstance(res, int):
                total += res
        except Exception as exc:  # noqa: BLE001
            logger.warning("seeder.emit failed for %s: %s", concept.get("slug"), exc)
    return total


# =========================================================================
# HARNESS BRIDGE (acf-ada-01)
# =========================================================================
def _record_harness_decisions(run_id: str, approved: list[dict], all_concepts: list[dict]) -> None:
    """Forward every concept decision to the Genesis Harness (acf-ada-01).

    Writes one ``harness_eval`` row per concept:
      * ``status='approved'``  -> decision ``'acf_approve'`` (positive class)
      * ``status='rejected'``  -> decision ``'acf_reject'``  (negative class)
      * status unchanged       -> decision ``'acf_skip'``    (proposed-only)

    Confidence is the composite score (or 0.0 if missing). The bridge is
    best-effort and degrades silently when ``tools.genesis.harness.eval_harness``
    is unavailable — the cycle never crashes on a missing harness. Same
    graceful-degradation pattern as the rest of the engine.
    """
    try:
        from tools.foundry import harness_bridge
    except Exception as exc:  # noqa: BLE001 - air-gap / pre-shipped
        logger.debug("harness_bridge not importable (%s); skipping ACF decision recording", exc)
        return

    approved_slugs = {c.get("slug") for c in approved if c.get("slug")}
    for c in all_concepts:
        slug = c.get("slug")
        if not slug:
            continue
        if c.get("status") == "approved" or slug in approved_slugs:
            decision_type = harness_bridge.DECISION_APPROVE
        elif c.get("status") == "rejected":
            decision_type = harness_bridge.DECISION_REJECT
        else:
            decision_type = harness_bridge.DECISION_SKIP
        try:
            harness_bridge.record_acf_decision(
                slug=slug,
                decision_type=decision_type,
                confidence=c.get("composite_score"),
                metadata={
                    "run_id": run_id,
                    "novelty_score": c.get("novelty_score"),
                    "reject_reason": c.get("reject_reason"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - per-concept failure never aborts the cycle
            logger.debug("harness_bridge record_acf_decision failed for %s: %s", slug, exc)


# =========================================================================
# RATE LIMITS
# =========================================================================
def _active_project_count(conn: Any) -> int:
    """Number of distinct ACF-owned projects with at least one un-done kanban task.

    Counts via the foundry's own provenance trail (foundry_tasks_emitted) so only
    genuinely ACF-emitted work is counted, joined to live kanban_tasks status.
    Best-effort: returns 0 if any table is missing (e.g. a hermetic test that did
    not seed kanban_tasks).
    """
    sql = (
        "SELECT COUNT(DISTINCT c.slug) "
        "FROM foundry_concepts c "
        "JOIN foundry_tasks_emitted e ON e.concept_id = c.id "
        "JOIN kanban_tasks k ON k.id = e.kanban_task_id "
        "WHERE k.status != 'done'"
    )
    try:
        cur = conn.execute(sql)
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.debug("active project count unavailable: %s", exc)
        return 0
    if not row:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError, KeyError, IndexError):
        try:
            return int(list(dict(row).values())[0] or 0)
        except Exception:  # noqa: BLE001
            return 0


# =========================================================================
# CIRCUIT BREAKER
# =========================================================================

def _recent_vv_fail_rate(conn: Any, window: int = 10) -> Optional[dict]:
    """Return V&V outcome stats for the most recent *window* foundry_outcomes rows.

    Only ``vv_fail`` and ``vv_pass`` outcomes are counted; ``abandoned`` rows are
    inconclusive and excluded from both numerator and denominator.

    Returns ``None`` when no vv_fail/vv_pass rows exist in the window (an empty or
    all-abandoned window must not trip the circuit breaker).
    """
    try:
        rows = conn.execute(
            f"SELECT outcome FROM foundry_outcomes ORDER BY id DESC LIMIT {sql_placeholder(conn)}",
            (window,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("_recent_vv_fail_rate: DB read failed: %s", exc)
        return None

    fail_count = 0
    pass_count = 0
    for r in rows:
        try:
            outcome = r["outcome"]
        except (KeyError, TypeError):
            outcome = r[0]
        if outcome == "vv_fail":
            fail_count += 1
        elif outcome == "vv_pass":
            pass_count += 1

    total = fail_count + pass_count
    if total == 0:
        return None
    return {
        "total": total,
        "fail_count": fail_count,
        "window": window,
        "fail_rate": fail_count / total,
    }


def _circuit_breaker_open(conn: Any, config: dict) -> tuple:
    """Evaluate the V&V circuit breaker.

    Returns ``(opened, stats, cb_cfg)`` where:
    - ``opened`` — True when fail_rate >= threshold
    - ``stats``  — dict from _recent_vv_fail_rate, or None if window is empty
    - ``cb_cfg`` — resolved circuit config (threshold + window)
    """
    cb_cfg = {**_DEFAULT_CIRCUIT, **(config.get("circuit", {}) or {})}
    stats = _recent_vv_fail_rate(conn, window=int(cb_cfg["window"]))
    if stats is None:
        return False, None, cb_cfg
    opened = stats["fail_rate"] >= float(cb_cfg["vv_fail_rate"])
    return opened, stats, cb_cfg


def _ensure_hitl_circuit_card(
    *,
    conn: Any,
    run_id: Any,
    tenant_id: str,
    classification: str,
    stats: dict,
    cb_cfg: dict,
) -> str:
    """INSERT OR IGNORE a kanban HITL card for this circuit-open run.

    Idempotent: the card id is deterministic (acf-hitl-circuit-<run_id>) so
    re-calling for the same run_id silently no-ops.
    Returns the card id.
    """
    from datetime import datetime, timezone

    card_id = f"acf-hitl-circuit-{run_id}"
    now = datetime.now(timezone.utc).isoformat()
    desc = (
        f"ACF circuit breaker triggered.\n\n"
        f"V&V fail rate: {stats['fail_rate']:.0%} "
        f"({stats['fail_count']}/{stats['total']}) in last {stats['window']} outcomes.\n"
        f"Threshold: {cb_cfg['vv_fail_rate']:.0%}.\n\n"
        f"Review the recent V&V failures in foundry_outcomes before re-enabling autonomous build."
    )
    try:
        ph = sql_placeholder(conn)
        # kanban_tasks has no tenant_id column (swp-scan-01); the board is not
        # tenant-scoped. Writing it raised UndefinedColumn, so the circuit
        # breaker never actually raised a HITL card.
        conn.execute(
            f"""
            INSERT OR IGNORE INTO kanban_tasks
                (id, title, description, task_type, priority, status,
                 hitl_stage, dispatch_source, created_at, updated_at,
                 classification)
            VALUES ({ph}, {ph}, {ph}, 'hitl', 'critical', 'backlog',
                    'circuit_breaker', 'foundry_circuit_breaker', {ph}, {ph}, {ph})
            """,
            (
                card_id,
                f"[ACF] Circuit breaker open — {stats['fail_rate']:.0%} V&V fail rate",
                desc,
                now, now, classification,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_ensure_hitl_circuit_card: insert failed: %s", exc)
    return card_id


# =========================================================================
# foundry_runs LIFECYCLE
# =========================================================================
def _row_id(row: Any) -> Any:
    if row is None:
        return None
    try:
        return row["id"]
    except (KeyError, IndexError, TypeError):
        try:
            return row[0]
        except Exception:  # noqa: BLE001
            return None


def _open_run(conn: Any, tenant_id: str, classification: str) -> Any:
    """INSERT a 'running' foundry_runs row; return its primary key (used as run_id)."""
    ph = sql_placeholder(conn)
    sql = (
        f"INSERT INTO foundry_runs (status, tenant_id, classification) "
        f"VALUES ({ph}, {ph}, {ph})"
    )
    params = ("running", tenant_id, classification)
    if _is_pg():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        conn.commit()
        return _row_id(row)
    cur = conn.execute(sql, params)
    conn.commit()
    return getattr(cur, "lastrowid", None)


def _finalize_run(conn: Any, run_pk: Any, result: dict) -> None:
    """UPDATE the run row with final counts + status + detail JSON.

    Defensive: if a legacy CHECK constraint rejects a newer status value
    (e.g. 'rate_limited' on a DB created before the constant was extended), retry
    with the always-valid 'deferred' so finalization never crashes the cycle.
    """
    if run_pk is None:
        return
    sql = (
        "UPDATE foundry_runs SET harvested=?, concepts_proposed=?, "
        "concepts_approved=?, tasks_emitted=?, status=?, detail=? WHERE id=?"
    )
    detail_json = json.dumps(result.get("detail") or {})
    base = (
        int(result.get("harvested", 0)),
        int(result.get("concepts_proposed", 0)),
        int(result.get("concepts_approved", 0)),
        int(result.get("tasks_emitted", 0)),
    )
    status = result.get("status", "completed")
    try:
        conn.execute(sql, (*base, status, detail_json, run_pk))
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - likely a CHECK on a pre-extension DB
        logger.warning("finalize run %s status=%s rejected (%s); degrading to 'deferred'", run_pk, status, exc)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.execute(sql, (*base, "deferred", detail_json, run_pk))
            conn.commit()
        except Exception as exc2:  # noqa: BLE001
            logger.warning("finalize run %s failed: %s", run_pk, exc2)


# =========================================================================
# PUBLIC: run_cycle
# =========================================================================
def run_cycle(
    *,
    dry_run: bool = False,
    max_concepts: Optional[int] = None,
    config: Optional[dict] = None,
    conn: Any = None,
) -> dict:
    """Run one ACF cycle: harvest -> synth -> novelty -> score -> CoD -> emit.

    Args:
        dry_run: run the full pipeline but the seeder does not write to kanban.
        max_concepts: per-cycle approved-concept cap; defaults to
            ``rate_limits.max_concepts_per_cycle``.
        config: optional foundry_config override (defaults to args/foundry_config.yaml).
        conn: optional open DB connection (caller manages its lifecycle).

    Returns a roll-up dict::

        {run_id, id, harvested, concepts_proposed, concepts_approved,
         tasks_emitted, active_projects, status, dry_run, rate_limited?, detail}

    ``status`` is one of ``completed`` | ``rate_limited`` | ``failed``.
    """
    cfg = _load_config(config)
    rl = _rate_limits(cfg)
    cap_concepts = int(max_concepts) if max_concepts is not None else rl["max_concepts_per_cycle"]
    max_active = rl["max_active_projects"]

    init_db()  # idempotent — guarantees foundry_* exist
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()

    tenant_id, classification = _caller_context()
    run_pk = _open_run(conn, tenant_id, classification)
    run_id = str(run_pk) if run_pk is not None else "0"

    # Resolve self_vet and circuit configs early so they appear in detail
    # even on short-circuit paths.
    self_vet_cfg = {**_DEFAULT_SELF_VET, **(cfg.get("self_vet", {}) or {})}
    _cb_cfg_early = {**_DEFAULT_CIRCUIT, **(cfg.get("circuit", {}) or {})}

    result: dict = {
        "run_id": run_id,
        "id": run_pk,
        "harvested": 0,
        "concepts_proposed": 0,
        "concepts_approved": 0,
        "tasks_emitted": 0,
        "active_projects": 0,
        "status": "completed",
        "dry_run": dry_run,
        "detail": {
            "max_concepts_per_cycle": cap_concepts,
            "max_active_projects": max_active,
            "circuit": _cb_cfg_early,
            "self_vet": self_vet_cfg,
        },
    }
    detail = result["detail"]

    try:
        # 0. Circuit breaker — abort cycle if V&V fail rate is too high.
        cb_open, cb_stats, cb_cfg = _circuit_breaker_open(conn, cfg)
        detail["circuit"] = cb_cfg  # include resolved config (may differ from early default)
        if cb_open:
            card_id = _ensure_hitl_circuit_card(
                conn=conn, run_id=run_pk, tenant_id=tenant_id,
                classification=classification, stats=cb_stats, cb_cfg=cb_cfg,
            )
            result["status"] = "circuit_open"
            result["circuit_open"] = True
            detail["reason"] = "circuit breaker open: V&V fail rate exceeded threshold"
            detail["hitl_card_id"] = card_id
            detail["circuit_stats"] = cb_stats
            logger.warning(
                "foundry circuit breaker OPEN run=%s fail_rate=%.0f%% card=%s",
                run_id, cb_stats["fail_rate"] * 100, card_id,
            )
            _finalize_run(conn, run_pk, result)
            return result

        # 1. Harvest.
        signals = _stage_harvest(run_id, cfg, conn)
        result["harvested"] = len(signals)

        # 2. Synthesize concepts.
        concepts = _stage_synthesize(run_id, signals, cfg, conn)
        result["concepts_proposed"] = len(concepts)

        # 3-5. Novelty gate -> score -> CoD go/no-go.
        approved = _stage_evaluate(concepts, cfg, conn)

        # 2b. Record every evaluation decision (approved + rejected) with the
        # Genesis Harness so compute_metrics(reflex='acf') can compute
        # precision/recall on the foundry's own approval choices (acf-ada-01).
        # Rejected concepts are the ones _stage_evaluate mutated in place to
        # status='rejected'; proposed-but-undecided (e.g. rate-limited upstream)
        # would carry the default 'proposed' status.
        _record_harness_decisions(run_id, approved, concepts)

        # Rate limit A — cap approved concepts per cycle.
        if len(approved) > cap_concepts:
            detail["capped_concepts"] = {"approved": len(approved), "cap": cap_concepts}
            logger.info("rate limit: capping %d approved concepts to %d", len(approved), cap_concepts)
            approved = approved[:cap_concepts]
        result["concepts_approved"] = len(approved)

        # Rate limit B — skip emit when too many ACF projects are already in flight.
        active = _active_project_count(conn)
        result["active_projects"] = active
        detail["active_projects"] = active
        if active >= max_active:
            result["status"] = "rate_limited"
            result["rate_limited"] = True
            detail["reason"] = (
                f"active foundry projects {active} >= max_active_projects {max_active}; skipping emit"
            )
            logger.info("rate limit: %s — no tasks emitted this cycle", detail["reason"])
            _finalize_run(conn, run_pk, result)
            return result

        # 6-8. Spec -> task graph -> seed (seeder no-ops in dry_run).
        result["tasks_emitted"] = _stage_emit(approved, cfg, conn, dry_run=dry_run)
        _finalize_run(conn, run_pk, result)

        logger.info(
            "foundry run=%s harvested=%d proposed=%d approved=%d emitted=%d status=%s (dry_run=%s)",
            run_id, result["harvested"], result["concepts_proposed"],
            result["concepts_approved"], result["tasks_emitted"], result["status"], dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - surface as a failed run, never crash the caller
        logger.exception("foundry run %s failed: %s", run_id, exc)
        result["status"] = "failed"
        detail["error"] = str(exc)
        try:
            _finalize_run(conn, run_pk, result)
        except Exception:  # noqa: BLE001
            pass
    finally:
        if own_conn:
            conn.close()

    return result


# =========================================================================
# PUBLIC: status
# =========================================================================
def status(*, conn: Any = None, limit: int = 10) -> dict:
    """Engine status snapshot: recent runs + active projects + pipeline counts.

    Returns::

        {recent_runs: [...], active_projects: int,
         pipeline: {<concept_status>: count, ...},
         rate_limits: {max_concepts_per_cycle, max_active_projects}}
    """
    cfg = _load_config(None)
    rl = _rate_limits(cfg)

    init_db()
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()

    out: dict = {
        "recent_runs": [],
        "active_projects": 0,
        "pipeline": {},
        "rate_limits": rl,
    }
    try:
        # Recent runs.
        try:
            cur = conn.execute(
                "SELECT id, cycle_at, harvested, concepts_proposed, concepts_approved, "
                f"tasks_emitted, status FROM foundry_runs ORDER BY id DESC LIMIT {sql_placeholder(conn)}",
                (int(limit),),
            )
            cols = ["id", "cycle_at", "harvested", "concepts_proposed",
                    "concepts_approved", "tasks_emitted", "status"]
            for row in cur.fetchall():
                try:
                    out["recent_runs"].append(dict(row))
                except (TypeError, ValueError):
                    out["recent_runs"].append(dict(zip(cols, row)))
        except Exception as exc:  # noqa: BLE001
            logger.debug("recent runs unavailable: %s", exc)

        # Active ACF projects.
        out["active_projects"] = _active_project_count(conn)

        # Concept pipeline counts by status.
        try:
            cur = conn.execute(
                "SELECT status, COUNT(*) FROM foundry_concepts GROUP BY status"
            )
            for row in cur.fetchall():
                try:
                    st, n = row[0], row[1]
                except (KeyError, TypeError):
                    d = dict(row)
                    st, n = d.get("status"), list(d.values())[-1]
                if st is not None:
                    out["pipeline"][str(st)] = int(n or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pipeline counts unavailable: %s", exc)
    finally:
        if own_conn:
            conn.close()
    return out


# =========================================================================
# CLI
# =========================================================================
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="foundry-engine",
        description="ACF engine — run one capability foundry cycle or show status (CUI // SP-CTI)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="Run one foundry cycle")
    mode.add_argument("--status", action="store_true", help="Show recent runs + active projects + pipeline counts")
    parser.add_argument("--dry-run", action="store_true", help="Run the full pipeline but do not seed kanban tasks")
    parser.add_argument("--max-concepts", type=int, default=None, help="Per-cycle approved-concept cap override")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args(argv)

    if args.status:
        payload = status()
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        else:
            rl = payload["rate_limits"]
            print(f"ACF status — active projects: {payload['active_projects']}/{rl['max_active_projects']}")
            print(f"  pipeline: {payload['pipeline'] or '(no concepts)'}")
            print(f"  recent runs ({len(payload['recent_runs'])}):")
            for r in payload["recent_runs"]:
                print(
                    f"    #{r.get('id')} {r.get('cycle_at','')} status={r.get('status')} "
                    f"harvested={r.get('harvested')} proposed={r.get('concepts_proposed')} "
                    f"approved={r.get('concepts_approved')} emitted={r.get('tasks_emitted')}"
                )
        return 0

    # args.run
    payload = run_cycle(dry_run=args.dry_run, max_concepts=args.max_concepts)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(
            f"foundry run {payload['run_id']} status={payload['status']} "
            f"(dry_run={payload['dry_run']})"
        )
        print(
            f"  harvested={payload['harvested']} proposed={payload['concepts_proposed']} "
            f"approved={payload['concepts_approved']} emitted={payload['tasks_emitted']} "
            f"active_projects={payload['active_projects']}"
        )
        if payload.get("rate_limited"):
            print(f"  RATE LIMITED: {payload['detail'].get('reason')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    raise SystemExit(main())

from tools.foundry.db.init_db import _is_pg, init_db  # noqa: E402
from tools.db.storage import sql_placeholder  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.foundry.engine")
