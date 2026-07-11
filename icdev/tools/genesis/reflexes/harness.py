# CUI // SP-CTI
"""Harness Reflex — runs every 6h, checks evaluation gates, promotes degradation cards.

Calls eval_harness.check_gates() and creates kanban tasks for any metrics that
have fallen below threshold. Idempotent: skips if an open degradation card
already exists for the same reflex+metric pair.
"""
from __future__ import annotations


from tools.logging.icdev_logger import get_logger
from datetime import datetime, timezone
from typing import Any

LOG = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _open_degradation_card_exists(reflex: str, metric: str) -> bool:
    """Return True if an unresolved harness degradation card already exists."""
    try:
        conn = _conn()
        row = conn.execute(
            """
            SELECT id FROM kanban_tasks
             WHERE title LIKE %s
               AND status NOT IN ('done', 'dismissed')
             LIMIT 1
            """,
            (f"%[harness] {reflex}.{metric}%",),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _get_echo_context(task_type: str) -> str:
    """Return ECHO improvement artifact text for injection into card descriptions."""
    try:
        from tools.workflow.reflexion_agent import get_latest_improvement
        return get_latest_improvement(task_type)
    except Exception:
        return ""


def _build_skill_prompt(task_type: str, colearn_enabled: bool) -> str:
    """Build a skill prompt, prepending the ECHO improvement artifact when co-learning is active.

    When ICDEV_HARNESS_COLEARN is true, calls get_latest_improvement(task_type) and
    prepends the returned artifact to the base remediation prompt so the kanban
    executor receives Reflexion-generated context before the task description.
    """
    base_prompt = (
        f"Investigate the degraded `{task_type}` harness metric and propose a "
        "remediation plan. Apply any relevant heuristic amendments or self-healing "
        "steps as described in the card details above."
    )
    if colearn_enabled:
        try:
            from tools.workflow.reflexion_agent import get_latest_improvement
            artifact = get_latest_improvement(task_type)
            if artifact:
                return artifact + "\n\n" + base_prompt
        except Exception:
            pass
    return base_prompt


def _try_auto_release_prompt(reflex: str, dry_run: bool = False) -> bool:
    """Check for a draft prompt version for this reflex and activate it if found.

    Returns True when a draft was found and activated (or would be, in dry_run).
    Logs the release to the audit log.  Never raises — failures are logged + return False.
    """
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT prompt_name, version FROM prompt_versions "
            "WHERE prompt_name = %s AND status = 'draft' "
            "ORDER BY version DESC LIMIT 1",
            (reflex,),
        ).fetchone()
        if row is None:
            return False
        prompt_name = row["prompt_name"] if isinstance(row, dict) else row[0]
        version = row["version"] if isinstance(row, dict) else row[1]
        if dry_run:
            LOG.info(
                "[harness] [dry-run] auto-release: would activate %s v%s", prompt_name, version
            )
            return True
        from tools.llm.prompt_registry import activate_prompt
        activate_prompt(prompt_name, version, actor="harness-auto-release")
        LOG.info("[harness] auto-released draft prompt %s v%s", prompt_name, version)
        return True
    except Exception as exc:
        LOG.debug("[harness] auto-release check failed for %s: %s", reflex, exc)
        return False


def _create_degradation_card(
    alert: dict,
    echo_context: str = "",
    skill_prompt: str = "",
    status: str = "backlog",
) -> str | None:
    """Insert a kanban_tasks row for a degradation alert.

    ``status`` defaults to 'backlog' (auto-dispatched remediation) for reflex
    gate failures. Delivery-pipeline health alerts pass status='suggested' so a
    human triages them in the Suggested lane before any dispatch — pipeline
    pass-rate degradation is an observation to review, not a concrete code task
    to auto-run."""
    import uuid
    reflex = alert["reflex"]
    metric = alert["metric"]
    value = alert["value"]
    severity = alert["severity"]
    recommendation = alert["recommendation"]

    task_id = f"harness-{reflex[:6]}-{metric[:8]}-{uuid.uuid4().hex[:6]}"
    title = f"[harness] {reflex}.{metric} degraded ({value:.3f})"
    body = (
        f"**Harness Gate Failure**\n\n"
        f"- Reflex: `{reflex}`\n"
        f"- Metric: `{metric}` = `{value:.4f}`\n"
        f"- Threshold: `{alert['threshold']}`\n"
        f"- Severity: `{severity}`\n\n"
        f"**Recommendation:** {recommendation}"
    )
    if echo_context:
        body += f"\n\n{echo_context}"
    if skill_prompt:
        body += f"\n\n**Skill Prompt:**\n{skill_prompt}"

    try:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, status, priority, dispatch_source, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'harness_reflex', %s, %s)
            """,
            (
                task_id,
                title,
                body,
                status,
                "high" if severity == "high" else "medium",
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
        LOG.info("[harness] Created degradation card: %s", task_id)
        return task_id
    except Exception as exc:
        LOG.warning("[harness] Failed to create degradation card: %s", exc)
        return None


def _create_review_card() -> None:
    """Create a kanban card asking a human to review proposed heuristic amendments."""
    import uuid
    title = "[harness] Review oracle heuristic proposals"
    # Skip if already open
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT id FROM kanban_tasks WHERE title = %s AND status NOT IN ('done','dismissed') LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return
        task_id = f"harness-heur-review-{uuid.uuid4().hex[:6]}"
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, status, priority, dispatch_source, created_at, updated_at)
            VALUES (%s, %s, %s, 'backlog', 'high', 'harness_reflex', %s, %s)
            """,
            (
                task_id,
                title,
                (
                    "**Oracle Heuristic Proposals Ready for Review**\n\n"
                    "The harness detected degraded precision and proposed new triage heuristics.\n\n"
                    "Review `args/oracle_heuristics_proposed.yaml`, then run:\n"
                    "```\n"
                    "python -c \"from tools.genesis.harness.heuristic_writer import merge_approved_proposals; "
                    "print(merge_approved_proposals(), 'heuristics merged')\"\n"
                    "```"
                ),
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
        LOG.info("[harness] Created heuristic review card: %s", task_id)
    except Exception as exc:
        LOG.warning("[harness] Failed to create review card: %s", exc)


def _create_meta_review_card(meta_result: dict) -> None:
    """Create a kanban card asking a human to review meta-harness proposals."""
    import uuid
    title = "[harness] Review meta-harness structural proposals"
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT id FROM kanban_tasks WHERE title = %s AND status NOT IN ('done','dismissed') LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return
        n_oracle = len(meta_result.get("oracle_proposals", []))
        n_heal = len(meta_result.get("heal_proposals", []))
        task_id = f"harness-meta-review-{uuid.uuid4().hex[:6]}"
        body = (
            "**Meta-Harness Structural Proposals Ready for Review**\n\n"
            f"- Oracle heuristic retirements proposed: {n_oracle}\n"
            f"- Heal constitution tightenings proposed: {n_heal}\n\n"
            f"Review `{meta_result.get('proposals_path', 'args/meta_harness_proposals.yaml')}`, "
            "then apply changes manually to `args/oracle_heuristics.yaml` and/or "
            "`args/heal_constitution.yaml`. Delete the proposals file after review."
        )
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, status, priority, dispatch_source, created_at, updated_at)
            VALUES (%s, %s, %s, 'backlog', 'high', 'harness_reflex', %s, %s)
            """,
            (task_id, title, body, _utcnow(), _utcnow()),
        )
        conn.commit()
        LOG.info("[harness] Created meta review card: %s", task_id)
    except Exception as exc:
        LOG.warning("[harness] Failed to create meta review card: %s", exc)


def run(config: dict[str, Any], trust: Any) -> dict[str, Any]:
    """Execute the Harness Reflex."""
    import os
    from tools.genesis.harness.eval_harness import check_gates, compute_metrics

    dry_run: bool = config.get("dry_run", False)
    colearn_enabled = os.getenv("ICDEV_HARNESS_COLEARN", "").lower() in ("true", "1")

    # Compute metrics for summary
    metrics_summary = {}
    for reflex in ("oracle_triage", "heal"):
        metrics_summary[reflex] = compute_metrics(reflex, window_days=30)

    # Check gates
    alerts = check_gates()
    new_cards: list[str] = []
    colearn_results: dict = {}

    # NOVA ECHO: when co-learning is enabled, run batch reflexion to generate
    # improvement artifacts for common task_types. These artifacts are prepended
    # to degradation card descriptions so kanban executors get ECHO context.
    echo_artifacts: dict = {}
    if colearn_enabled:
        try:
            from tools.workflow.reflexion_agent import run_batch_reflexion
            reflex_result = run_batch_reflexion(dry_run=dry_run)
            for tt, art in reflex_result.get("results", {}).items():
                if not art.get("skipped"):
                    echo_artifacts[tt] = art
            LOG.info(
                "[harness] ECHO batch reflexion: %d task_types processed, %d artifacts",
                reflex_result.get("task_types_processed", 0),
                len(echo_artifacts),
            )
        except Exception as exc:
            LOG.warning("[harness] ECHO batch reflexion failed: %s", exc)

    for alert in alerts:
        reflex = alert["reflex"]
        metric = alert["metric"]

        if _open_degradation_card_exists(reflex, metric):
            LOG.debug("[harness] skipping %s.%s — card already open", reflex, metric)
            continue

        # NOVA ECHO: inject latest improvement artifact for this reflex/task_type
        echo_context = _get_echo_context(reflex) if colearn_enabled else ""
        skill_prompt = _build_skill_prompt(reflex, colearn_enabled)

        # C-1 Auto-release: if a draft prompt version exists for this reflex,
        # activate it instead of (or before) creating a degradation card.
        _auto_released = _try_auto_release_prompt(reflex, dry_run=dry_run)

        if not dry_run:
            if not _auto_released:
                # No draft to auto-release — create the degradation card as usual.
                card_id = _create_degradation_card(
                    alert, echo_context=echo_context, skill_prompt=skill_prompt
                )
                if card_id:
                    new_cards.append(card_id)
        else:
            if _auto_released:
                LOG.info("[harness] [dry-run] would auto-release draft prompt for %s", reflex)
            else:
                LOG.info("[harness] [dry-run] would create card for %s.%s", reflex, metric)
            new_cards.append(f"dry-run:{reflex}.{metric}")

        # Co-learning pass: when precision or ECE gate fires for oracle_triage,
        # extract error cases and propose heuristic amendments via LLM.
        if colearn_enabled and reflex == "oracle_triage" and metric in ("precision", "ece"):
            try:
                from tools.genesis.harness.heuristic_writer import run_colearn_pass
                result = run_colearn_pass(reflex="oracle_triage", dry_run=dry_run)
                colearn_results[metric] = result
                if result.get("proposals_written"):
                    LOG.info("[harness] co-learning wrote proposals for %s.%s", reflex, metric)
                    _create_review_card()
            except Exception as exc:
                LOG.warning("[harness] co-learning pass failed: %s", exc)

    # Delivery-pipeline co-learner (Phase 3a): watch the task pipeline's gate
    # pass-rates (from kanban_verifications) and surface a degradation card when
    # one degrades. Best-effort — never break the harness cycle.
    pipeline_alerts: list[dict] = []
    try:
        from tools.genesis.harness.eval_harness import check_pipeline_gates
        pipeline_alerts = check_pipeline_gates()
        for palert in pipeline_alerts:
            preflex, pmetric = palert["reflex"], palert["metric"]
            if _open_degradation_card_exists(preflex, pmetric):
                LOG.debug("[harness] skipping %s.%s — card already open", preflex, pmetric)
                continue
            if not dry_run:
                # Pipeline health degradation → Suggested lane (human triages
                # before dispatch); reflex gate failures above stay auto-dispatch.
                _pcard = _create_degradation_card(palert, status="suggested")
                if _pcard:
                    new_cards.append(_pcard)
            else:
                LOG.info("[harness] [dry-run] would create pipeline card for %s.%s", preflex, pmetric)
                new_cards.append(f"dry-run:{preflex}.{pmetric}")
    except Exception as _pexc:
        LOG.warning("[harness] delivery-pipeline co-learner skipped: %s", _pexc)

    status = "ok" if not (alerts or pipeline_alerts) else ("degraded" if new_cards else "cards_exist")

    # Meta-harness: run once per day to propose structural amendments
    meta_result: dict = {}
    try:
        from tools.genesis.harness.meta_harness import should_run_today, run_meta_review
        if should_run_today():
            meta_result = run_meta_review(dry_run=dry_run)
            if meta_result.get("proposals_written"):
                LOG.info(
                    "[harness] meta-harness wrote proposals to %s",
                    meta_result.get("proposals_path"),
                )
                _create_meta_review_card(meta_result)
    except Exception as exc:
        LOG.warning("[harness] meta-harness pass failed: %s", exc)

    return {
        "success": True,
        "metric_value": float(len(new_cards)),
        "details": {
            "status": status,
            "alerts_found": len(alerts),
            "new_cards_created": len(new_cards),
            "new_card_ids": new_cards,
            "metrics": metrics_summary,
            "dry_run": dry_run,
            "colearn": colearn_results,
            "echo_artifacts_generated": len(echo_artifacts),
            "meta": meta_result,
        },
    }
