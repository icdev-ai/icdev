# CUI // SP-CTI
"""AI-ify Canvas — Pipeline Engine.

Public API:
    run_scan(input_type, input_ref, scan_context=None) -> dict

Orchestrates the full AI-ify pipeline:
  1. init_db()         — ensure schema is ready
  2. aiify_scans INSERT  — status='running'
  3. detect_patterns() — Semgrep or AST fallback
  4. aiify_opportunities INSERT + score_opportunity() per hit
  5. aiify_scores INSERT
  6. generate_roadmap() -> aiify_roadmaps INSERT
  7. promote top-5 opportunities -> kanban_tasks + aiify_audit_log
  8. aiify_scans UPDATE  — status='completed'
  9. aiify_audit_log     — scan_started / scan_completed events
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import yaml

from tools.aiify.agent_readiness import run_readiness_check
from tools.aiify.db.init_db import get_connection, init_db
from tools.aiify.opportunity_scorer import (
    score_and_assess,
    roll_up_scan_verdict,
)
from tools.aiify.pattern_classifier import detect_patterns
from tools.aiify.roadmap_generator import generate_roadmap
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.aiify.engine")

_CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "args" / "aiify_config.yaml"


def _load_aiify_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


_aiify_cfg = _load_aiify_config()
_nlp_ref_cfg: dict = _aiify_cfg.get("nlp_ref_extractor", {})
_NLP_REF_ENABLED: bool = (
    os.environ.get("ICDEV_AIIFY_NLP_REF", "").lower() in ("1", "true", "yes")
    or bool(_nlp_ref_cfg.get("enabled", False))
)
_NLP_REF_MODEL: str = str(_nlp_ref_cfg.get("model", "claude-haiku-4-5-20251001"))
_NLP_REF_MAX_TOKENS: int = int(_nlp_ref_cfg.get("max_tokens", 128))

_NLP_REF_PROMPT = """\
You are a code analysis assistant extracting metadata from a scan target reference.

Input type : {input_type}
Input ref  : {input_ref}

Extract:
- project_name       — short human-readable name (from the URL slug or directory name)
- hosting_platform   — one of: github, gitlab, bitbucket, azure_devops, local, unknown
- detected_languages — list of likely programming languages inferred from the ref string

Respond with JSON only (no markdown):
{{"project_name": "my-app", "hosting_platform": "github", "detected_languages": ["python"]}}"""

_FALLBACK_ANOMALY_THRESHOLDS = {
    "innovation_signal_min_score": 0.60,
    "phase": {
        "p1_min_score": 0.70,
        "p2_min_score": 0.50,
        "p3_min_score": 0.30,
    },
    "priority_high_min_score": 0.70,
    "value_feasibility_max_delta": 0.50,
    "component_outlier_floor": 0.05,
    "component_outlier_ceiling": 0.95,
}


def _load_anomaly_thresholds() -> dict:
    """Load anomaly_detection thresholds from args/aiify_config.yaml with fallback."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and "anomaly_detection" in cfg:
            return cfg["anomaly_detection"]
    except Exception:
        pass
    return dict(_FALLBACK_ANOMALY_THRESHOLDS)


# Maps each pattern type to a valid AI paradigm (constants.AI_PARADIGMS)
_PATTERN_TO_PARADIGM: dict[str, str] = {
    "nested_conditionals": "ml_classifier",
    "regex_user_input": "nlp_extractor",
    "string_template_rendering": "llm_generation",
    "scheduled_cron": "agentic_trigger",
    "hardcoded_threshold": "anomaly_detection",
    "db_render_notify_chain": "llm_generation",
    "keyword_list_search": "embedding_search",
    "large_rule_table": "decision_agent",
}

_PARADIGM_TO_MODEL: dict[str, str] = {
    "ml_classifier": "claude-haiku-4-5-20251001",
    "nlp_extractor": "claude-haiku-4-5-20251001",
    "llm_generation": "claude-sonnet-4-6",
    "agentic_trigger": "claude-sonnet-4-6",
    "anomaly_detection": "claude-haiku-4-5-20251001",
    "embedding_search": "claude-haiku-4-5-20251001",
    "decision_agent": "claude-opus-4-7",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(value: Any) -> Any:
    backend = os.environ.get(
        "AIIFY_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()
    if backend == "postgresql":
        try:
            from psycopg2.extras import Json
            return Json(value)
        except ImportError:
            pass
    return json.dumps(value)


def _backend() -> str:
    return os.environ.get(
        "AIIFY_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()


def _insert(conn: Any, sql: str, params: tuple, id_col: str = "id",
            commit: bool = True) -> int:
    """Execute a ``%s``-placeholder INSERT and return the generated PK.

    Connections are translating StorageConnections (see init_db.get_connection),
    so the single PG-native ``%s`` style flows through unchanged on PostgreSQL and
    is rewritten to ``?`` on SQLite. PostgreSQL uses RETURNING; SQLite uses
    ``lastrowid``. The former ``_exec`` blind retry-with-%s helper is gone — every
    call site now authors ``%s`` directly (penta-aiify-06).

    ``commit=False`` returns the new PK without committing so a caller can batch
    many inserts into a single transaction (the returned id is visible within the
    open transaction for a follow-on FK insert). RETURNING/lastrowid both work
    pre-commit, so batching is safe.
    """
    from tools.db.storage import is_pg
    if is_pg(conn):
        cur = conn.execute(sql + f" RETURNING {id_col}", params)
        row = cur.fetchone()
        if commit:
            conn.commit()
        return int(row[0]) if row else 0
    cur = conn.execute(sql, params)
    if commit:
        conn.commit()
    return cur.lastrowid or 0


def _build_summary(input_ref: str, opp_rows: list[dict]) -> str:
    """Derive a one-sentence plain-English project summary without LLM calls."""
    ref = input_ref.strip().rstrip("/")
    if re.match(r"^(https?://|git@)", ref):
        name = ref.rstrip("/").split("/")[-1].replace(".git", "")
    else:
        name = pathlib.Path(ref).name or ref

    if not opp_rows:
        return f"'{name}' — no AI-augmentable patterns detected."

    pattern_counts: dict[str, int] = {}
    paradigm_counts: dict[str, int] = {}
    for opp in opp_rows:
        pt = opp.get("pattern_type", "unknown")
        pa = opp.get("ai_paradigm", "")
        pattern_counts[pt] = pattern_counts.get(pt, 0) + 1
        if pa:
            paradigm_counts[pa] = paradigm_counts.get(pa, 0) + 1

    top_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    top_paradigms = sorted(paradigm_counts.items(), key=lambda x: x[1], reverse=True)[:2]
    pattern_str = ", ".join(f"{count}× {pt}" for pt, count in top_patterns)
    paradigm_str = " + ".join(p for p, _ in top_paradigms) if top_paradigms else "llm_generation"
    return (
        f"'{name}' — {len(opp_rows)} augmentation opportunities: "
        f"{pattern_str}. Recommended AI: {paradigm_str}."
    )


def _nlp_extract_ref_info(input_ref: str, input_type: str) -> dict:
    """Use Claude Haiku NLP extraction to classify the scan target and extract project metadata.

    This is an opt-in enhancement that replaces regex URL/path parsing with
    LLM-powered extraction, yielding richer project identification metadata
    (hosting platform, language hints, canonical project name).

    Enabled by: ICDEV_AIIFY_NLP_REF=true env var or nlp_ref_extractor.enabled in aiify_config.yaml.

    Returns:
        Dict with keys ``project_name``, ``hosting_platform``, ``detected_languages``.
        Empty dict when disabled or on any failure — callers must handle gracefully.
    """
    if not _NLP_REF_ENABLED:
        return {}

    try:
        from tools.llm.provider import LLMRequest  # lazy import — optional dep
        from tools.llm.router import LLMRouter
    except ImportError:
        return {}

    try:
        router = LLMRouter()
        request = LLMRequest(
            messages=[{
                "role": "user",
                "content": _NLP_REF_PROMPT.format(
                    input_type=input_type or "unknown",
                    input_ref=input_ref or "",
                ),
            }],
            system_prompt=(
                "You are a code analysis assistant. "
                "Reply only with the JSON object specified — no prose."
            ),
            agent_id="scan-ref-nlp-extractor",
            classification="CUI",
            max_tokens=_NLP_REF_MAX_TOKENS,
            effort="low",
            preferred_model=_NLP_REF_MODEL,
        )
        response = router.invoke("code_generation", request)
        raw = re.sub(r"```(?:json)?|```", "", response.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {}
        data: dict = json.loads(m.group(0))
        if not data:
            return {}
    except Exception:  # network error, parse error — degrade gracefully
        return {}

    detected_languages = data.get("detected_languages", [])
    if not isinstance(detected_languages, list):
        detected_languages = []

    return {
        "project_name": str(data.get("project_name", "")),
        "hosting_platform": str(data.get("hosting_platform", "")),
        "detected_languages": [str(lang) for lang in detected_languages],
    }


def _register_innovation_signals(opp_rows: list[dict], score_rows: list[dict], scan_id: int) -> int:
    """Option C: cross-register high-scoring AI-ify opportunities to innovation_signals."""
    thresholds = _load_anomaly_thresholds()
    innovation_min = thresholds.get(
        "innovation_signal_min_score",
        _FALLBACK_ANOMALY_THRESHOLDS["innovation_signal_min_score"],
    )
    score_index = {int(s["opportunity_id"]): s for s in score_rows}
    registered = 0
    try:
        from tools.db.storage import get_connection as _icdev_conn
        import hashlib
        conn = _icdev_conn()
        try:
            for opp in opp_rows:
                opp_id = int(opp.get("opportunity_id", 0))
                score = score_index.get(opp_id, {})
                composite = float(score.get("composite_score", 0.0))
                if composite < innovation_min:
                    continue
                pattern = opp.get("pattern_type", "")
                paradigm = opp.get("ai_paradigm", "")
                module = opp.get("module_path", "")
                title = f"AI-ify: {pattern} → {paradigm} in {module}"
                description = (
                    f"Detected by AI-ify scan #{scan_id}. Pattern: {pattern}, "
                    f"AI paradigm: {paradigm}, composite score: {composite:.2f}."
                )
                content_hash = hashlib.sha256(
                    f"aiify-{scan_id}-{opp_id}".encode()
                ).hexdigest()[:32]
                signal_id = f"aiify-{scan_id}-{opp_id}"
                # Skip if already registered
                existing = conn.execute(
                    "SELECT id FROM innovation_signals WHERE id = %s", (signal_id,)
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    "INSERT INTO innovation_signals "
                    "(id, source, source_type, title, description, content_hash, "
                    "discovered_at, status, category, innovation_score) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        signal_id, "aiify_opportunities", "internal_analysis",
                        title, description, content_hash,
                        _now(), "approved", "aiify_opportunity", composite,
                    ),
                )
                conn.commit()
                registered += 1
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # innovation_signals table may not exist in all environments
        logger.warning(
            "_register_innovation_signals: best-effort INSERT into innovation_signals failed (non-blocking): %s",
            exc,
        )
    return registered


def _phase_label(score: float, thresholds: dict | None = None) -> str:
    if thresholds is None:
        thresholds = _load_anomaly_thresholds()
    phase_cfg = thresholds.get("phase", _FALLBACK_ANOMALY_THRESHOLDS["phase"])
    if score >= phase_cfg.get("p1_min_score", 0.70):
        return "P1 — Quick Wins"
    if score >= phase_cfg.get("p2_min_score", 0.50):
        return "P2 — Core Modernization"
    if score >= phase_cfg.get("p3_min_score", 0.30):
        return "P3 — Long-Horizon Investments"
    return "Unclassified"


# Number of highest-scoring opportunities the "top" promoter takes, before the
# phase promoter fills the remaining per-scan budget.
_TOP_PROMOTE_N = 5

_FALLBACK_PROMOTION_CONFIG = {
    "auto_promote_cap": 10,
    "auto_promote_status": "suggested",
}


def _load_promotion_config() -> dict:
    """Load kanban_promotion settings from args/aiify_config.yaml with fallback.

    Controls auto-promotion of scan opportunities to kanban_tasks:
      * ``auto_promote_cap``    — hard cap on tasks auto-created per scan.
      * ``auto_promote_status`` — kanban status for auto-created tasks; always a
        non-dispatchable/quarantine status (defaults to 'suggested').
    """
    merged = dict(_FALLBACK_PROMOTION_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and isinstance(cfg.get("kanban_promotion"), dict):
            merged.update(cfg["kanban_promotion"])
    except Exception:
        pass
    return merged


def _rank_opportunities(opp_rows: list[dict], score_rows: list[dict]) -> list[dict]:
    """Return opportunities enriched with scores, sorted by composite desc."""
    score_index: dict[int, dict] = {int(s["opportunity_id"]): s for s in score_rows}
    enriched: list[dict] = []
    for opp in opp_rows:
        opp_id = int(opp["opportunity_id"])
        score = score_index.get(opp_id, {})
        enriched.append({
            "opportunity_id": opp_id,
            "pattern_type": opp.get("pattern_type", ""),
            "module_path": opp.get("module_path", ""),
            "function_name": opp.get("function_name", "<unknown>"),
            "ai_paradigm": opp.get("ai_paradigm", "llm_generation"),
            "il_recommended_model": opp.get("il_recommended_model", ""),
            "composite_score": float(score.get("composite_score", 0.0)),
            "value_score": float(score.get("value_score", 0.0)),
            "feasibility_score": float(score.get("feasibility_score", 0.0)),
            "risk_score": float(score.get("risk_score", 0.0)),
        })
    enriched.sort(key=lambda x: x["composite_score"], reverse=True)
    return enriched


def _write_promotion_audit(scan_id: int, roadmap_id: str, entries: list[dict]) -> None:
    """Best-effort: one aiify_audit_log 'kanban_promoted' row per created task.

    Batched into a single commit. Audit failure must never break promotion or the
    scan pipeline, so the whole block is defensive.
    """
    if not entries:
        return
    try:
        aiify_conn = get_connection()
    except Exception:
        return
    try:
        for e in entries:
            aiify_conn.execute(
                "INSERT INTO aiify_audit_log (event_type, scan_id, actor, detail) VALUES (%s, %s, %s, %s)",
                (
                    "kanban_promoted",
                    scan_id,
                    "system",
                    _dump({
                        "task_id": e.get("task_id"),
                        "opportunity_id": e.get("opportunity_id"),
                        "composite_score": e.get("composite_score"),
                        "roadmap_id": roadmap_id,
                    }),
                ),
            )
        aiify_conn.commit()
    except Exception:
        try:
            aiify_conn.rollback()
        except Exception:
            pass
    finally:
        try:
            aiify_conn.close()
        except Exception:
            pass


def _promote_top_opportunities(
    opp_rows: list[dict],
    score_rows: list[dict],
    scan_id: int,
    roadmap_id: str,
) -> int:
    """Promote the highest-scoring opportunities to kanban_tasks (capped).

    Routes ALL task creation through tools.kanban.task_factory.create_tasks with a
    per-opportunity idempotency_key (the single dedup choke point), never exceeds
    ``auto_promote_cap`` (args/aiify_config.yaml), and lands tasks in the
    configured non-dispatchable status ('suggested') pending HITL review.
    Returns the count of tasks actually created.
    """
    from tools.kanban.task_factory import create_tasks

    cfg = _load_promotion_config()
    cap = int(cfg.get("auto_promote_cap", 10) or 0)
    status = str(cfg.get("auto_promote_status", "suggested")) or "suggested"
    if cap <= 0:
        return 0

    ranked = _rank_opportunities(opp_rows, score_rows)
    top = ranked[: min(_TOP_PROMOTE_N, cap)]
    if not top:
        return 0

    thresholds = _load_anomaly_thresholds()
    priority_high_min = thresholds.get(
        "priority_high_min_score",
        _FALLBACK_ANOMALY_THRESHOLDS["priority_high_min_score"],
    )

    specs: list[dict] = []
    audit: list[dict] = []
    for opp in top:
        opp_id = opp["opportunity_id"]
        task_id = f"aiify-opp-{str(opp_id)[:8]}"
        priority = "high" if opp["composite_score"] >= priority_high_min else "medium"
        title = (
            f"[AI Opp] {opp['pattern_type']} in "
            f"{opp['module_path']}:{opp['function_name']} "
            f"-> {opp['ai_paradigm']}"
        )
        description = json.dumps(
            {
                "opportunity_id": opp_id,
                "scan_id": scan_id,
                "roadmap_id": roadmap_id,
                "pattern_type": opp["pattern_type"],
                "module_path": opp["module_path"],
                "function_name": opp["function_name"],
                "ai_paradigm": opp["ai_paradigm"],
                "scores": {
                    "composite": opp["composite_score"],
                    "value": opp["value_score"],
                    "feasibility": opp["feasibility_score"],
                    "risk": opp["risk_score"],
                },
                "roadmap_phase": _phase_label(opp["composite_score"]),
                "model_recommendation": opp["il_recommended_model"],
            },
            indent=2,
        )
        specs.append({
            "id": task_id,
            "idempotency_key": f"aiify-opp-{opp_id}",
            "title": title,
            "description": description,
            "task_type": "build",
            "priority": priority,
            "status": status,
            "dispatch_source": "aiify_auto",
        })
        audit.append({
            "task_id": task_id,
            "opportunity_id": opp_id,
            "composite_score": opp["composite_score"],
        })

    created = create_tasks(specs)
    created_set = set(created)
    _write_promotion_audit(
        scan_id, roadmap_id,
        [a for a in audit if a["task_id"] in created_set],
    )
    return len(created)


def _promote_phase_opportunities(
    roadmap: dict,
    opp_rows: list[dict],
    score_rows: list[dict],
    scan_id: int,
) -> int:
    """Create capped [Phase] kanban tasks for opportunities not already promoted.

    Historically this created one task per opportunity across every phase with no
    cap — the root cause of runaway kanban seeding. It now shares a single
    per-scan budget with _promote_top_opportunities (total <= auto_promote_cap),
    excludes the opportunities the top promoter already took, routes through
    task_factory with a per-opportunity idempotency_key, and lands tasks in the
    configured non-dispatchable status. Returns the count of tasks created.
    """
    from tools.kanban.task_factory import create_tasks

    cfg = _load_promotion_config()
    cap = int(cfg.get("auto_promote_cap", 10) or 0)
    status = str(cfg.get("auto_promote_status", "suggested")) or "suggested"
    budget = cap - min(_TOP_PROMOTE_N, cap)
    if budget <= 0:
        return 0

    ranked = _rank_opportunities(opp_rows, score_rows)
    excluded = {o["opportunity_id"] for o in ranked[: min(_TOP_PROMOTE_N, cap)]}

    roadmap_id: str = roadmap.get("roadmap_id", "")
    # 'rm-8a699d41b6' → '8a699'
    short_id = roadmap_id[3:8] if len(roadmap_id) > 8 else roadmap_id.replace("-", "")

    score_index: dict[int, dict] = {int(s["opportunity_id"]): s for s in score_rows}
    opp_index: dict[int, dict] = {int(o["opportunity_id"]): o for o in opp_rows}

    thresholds = _load_anomaly_thresholds()
    priority_high_min = thresholds.get(
        "priority_high_min_score",
        _FALLBACK_ANOMALY_THRESHOLDS["priority_high_min_score"],
    )

    specs: list[dict] = []
    audit: list[dict] = []
    seen: set[int] = set()
    for phase in roadmap.get("phases", []):
        if len(seen) >= budget:
            break
        phase_label = phase.get("label", "")
        for opp_item in phase.get("opportunities", []):
            if len(seen) >= budget:
                break
            opp_id = int(opp_item.get("opportunity_id", 0))
            if opp_id in excluded or opp_id in seen:
                continue
            seen.add(opp_id)

            opp = opp_index.get(opp_id, opp_item)
            score = score_index.get(opp_id, {})

            # Prefer opp_index (has computed paradigm/model); fall back to roadmap item
            paradigm = opp.get("ai_paradigm") or opp_item.get("ai_paradigm", "")
            model = opp.get("il_recommended_model") or opp_item.get("il_recommended_model", "")
            pattern_type = opp.get("pattern_type") or opp_item.get("pattern_type", "")
            module_path = opp.get("module_path") or opp_item.get("module_path", "")
            function_name = (
                opp.get("function_name") or opp_item.get("function_name", "<unknown>")
            )
            composite = float(score.get("composite_score", 0.0))
            task_id = f"aiify-rm-{short_id}-phase-{opp_id}"
            title = f"[Phase] {pattern_type} in {module_path} -> {paradigm}"
            description = json.dumps(
                {
                    "opportunity_id": opp_id,
                    "scan_id": scan_id,
                    "roadmap_id": roadmap_id,
                    "phase": phase_label,
                    "pattern_type": pattern_type,
                    "module_path": module_path,
                    "function_name": function_name,
                    "ai_paradigm": paradigm,
                    "model_recommendation": model,
                    "scores": {
                        "composite": composite,
                        "value": float(score.get("value_score", 0.0)),
                        "feasibility": float(score.get("feasibility_score", 0.0)),
                        "risk": float(score.get("risk_score", 0.0)),
                    },
                },
                indent=2,
            )
            priority = "high" if composite >= priority_high_min else "medium"
            specs.append({
                "id": task_id,
                "idempotency_key": f"aiify-opp-{opp_id}",
                "title": title,
                "description": description,
                "task_type": "build",
                "priority": priority,
                "status": status,
                "dispatch_source": "aiify_auto",
            })
            audit.append({
                "task_id": task_id,
                "opportunity_id": opp_id,
                "composite_score": composite,
            })

    created = create_tasks(specs)
    created_set = set(created)
    _write_promotion_audit(
        scan_id, roadmap_id,
        [a for a in audit if a["task_id"] in created_set],
    )
    return len(created)


def detect_score_anomalies(rows: list, thresholds: dict | None = None) -> list:
    """Detect statistical anomalies in a batch of scored opportunities.

    Uses thresholds from the ``anomaly_detection`` section of aiify_config.yaml
    so that sensitivity can be tuned without touching source code.

    Args:
        rows:       List of score row dicts with ``opportunity_id``,
                    ``value_score``, ``feasibility_score``, ``risk_score``,
                    ``composite_score``.
        thresholds: Optional threshold dict; defaults to the
                    ``anomaly_detection`` section from aiify_config.yaml.

    Returns:
        List of anomaly dicts; empty means no anomalies.  Each dict has:
            ``opportunity_id``, ``anomaly_type``, ``detail``.
    """
    if thresholds is None:
        thresholds = _load_anomaly_thresholds()

    max_delta = float(thresholds.get("value_feasibility_max_delta", 0.50))
    floor = float(thresholds.get("component_outlier_floor", 0.05))
    ceiling = float(thresholds.get("component_outlier_ceiling", 0.95))

    anomalies: list = []
    for row in rows:
        opp_id = row.get("opportunity_id")
        value = float(row.get("value_score", 0.0))
        feasibility = float(row.get("feasibility_score", 0.0))
        risk = float(row.get("risk_score", 0.0))
        composite = float(row.get("composite_score", 0.0))

        delta = abs(value - feasibility)
        if delta > max_delta:
            anomalies.append({
                "opportunity_id": opp_id,
                "anomaly_type": "value_feasibility_imbalance",
                "detail": {
                    "value_score": value,
                    "feasibility_score": feasibility,
                    "delta": round(delta, 4),
                    "threshold": max_delta,
                },
            })

        for comp_name, comp_val in [
            ("value_score", value),
            ("feasibility_score", feasibility),
            ("risk_score", risk),
            ("composite_score", composite),
        ]:
            if comp_val < floor:
                anomalies.append({
                    "opportunity_id": opp_id,
                    "anomaly_type": "component_outlier_low",
                    "detail": {"component": comp_name, "value": comp_val, "floor": floor},
                })
            elif comp_val > ceiling:
                anomalies.append({
                    "opportunity_id": opp_id,
                    "anomaly_type": "component_outlier_high",
                    "detail": {"component": comp_name, "value": comp_val, "ceiling": ceiling},
                })

    # DIC Canvas Synergy — emit gap_identified for high-value anomalous opportunities (dsyn-emit-08)
    if anomalies:
        try:
            from tools.aiify.event_emitter import emit_gap_identified
            for anomaly in anomalies[:5]:  # cap at 5 to avoid event flood
                opp_id = anomaly.get("opportunity_id", "")
                detail = anomaly.get("detail") or {}
                value = float(detail.get("value_score", detail.get("value", 0.0)))
                emit_gap_identified(
                    canvas_name="aiify",
                    opportunity_id=str(opp_id),
                    gap_type=anomaly.get("anomaly_type", "anomaly"),
                    value_score=value,
                )
        except Exception:
            pass  # event emission never blocks anomaly detection

    return anomalies


def _is_git_url(ref: str) -> bool:
    """Return True if ref looks like a git or HTTPS repository URL."""
    return bool(
        re.match(r"^(https?://|git@)", ref)
        or ref.endswith(".git")
    )


def _clone_git_url(url: str) -> str:
    """Shallow-clone a git URL into a temp directory and return the local path.

    Raises RuntimeError if git is unavailable or the clone fails.
    """
    if not shutil.which("git"):
        raise RuntimeError("git CLI is not installed; cannot clone remote repository")

    clone_dir = tempfile.mkdtemp(prefix="aiify_git_")
    cmd = ["git", "clone", "--depth", "1", "--single-branch", url, clone_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        shutil.rmtree(clone_dir, ignore_errors=True)
        raise RuntimeError(
            f"git clone failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return clone_dir


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_unc_path(ref: str) -> bool:
    r"""Windows UNC share (\\server\share) or //server/share."""
    return ref.startswith("\\\\") or ref.startswith("//")


def _fetch_s3(ref: str) -> str:
    """Download an s3://bucket/prefix tree to a temp dir and return its path."""
    try:
        import boto3  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "s3:// input requires the 'boto3' package, which is not installed "
            "in this environment. Install boto3 or sync the source locally first."
        ) from exc
    parsed = urllib.parse.urlparse(ref)
    bucket, prefix = parsed.netloc, parsed.path.lstrip("/")
    dest = tempfile.mkdtemp(prefix="aiify_s3_")
    s3 = boto3.client("s3")
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(prefix):].lstrip("/") if prefix else key
            fp = pathlib.Path(dest) / (rel or pathlib.Path(key).name)
            fp.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(fp))
            count += 1
    if count == 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"No objects found at {ref}")
    return dest


def _resolve_input(input_type: str, input_ref: str) -> tuple[str, str | None]:
    r"""Resolve an input reference to a local filesystem path to scan.

    Returns ``(scan_path, cleanup_path)``; ``cleanup_path`` is a temp directory
    to remove afterwards (git clone / s3 download) or ``None`` for in-place
    paths. Supported sources: local path (POSIX/Windows drive), UNC share
    (\\server\share), git URL (http/https/ssh/.git/git+ssh://), file:// URI,
    and s3:// (requires boto3). Unsupported schemes (smb://, ftp://, bare
    http(s) non-repo) raise a clear RuntimeError telling the user to mount/sync
    locally — we don't pretend to support transports the environment can't reach.
    """
    ref = (input_ref or "").strip()
    if not ref:
        raise RuntimeError("Empty input reference")

    if (input_type == "git_url" or _is_git_url(ref)
            or ref.startswith(("git+ssh://", "git+https://", "git+http://"))):
        url = ref
        for pre in ("git+ssh://", "git+https://", "git+http://"):
            if url.startswith(pre):
                url = url[len("git+"):]
                break
        cloned = _clone_git_url(url)
        return cloned, cloned

    if _is_unc_path(ref) or _WINDOWS_DRIVE_RE.match(ref):
        return ref, None

    parsed = urllib.parse.urlparse(ref)
    scheme = parsed.scheme.lower()
    if scheme == "":
        return ref, None
    if scheme == "file":
        if parsed.netloc and parsed.netloc.lower() not in ("", "localhost"):
            return urllib.request.url2pathname(f"//{parsed.netloc}{parsed.path}"), None
        return urllib.request.url2pathname(parsed.path), None
    if scheme == "s3":
        path = _fetch_s3(ref)
        return path, path
    if scheme in ("smb", "ftp", "http", "https"):
        raise RuntimeError(
            f"Input scheme '{scheme}://' is not supported for code scanning here. "
            f"Use a local path, UNC share (\\\\server\\share), a git URL "
            f"(https://…/repo.git or git+ssh://…), file://, or s3://. "
            f"For {scheme}://, mount or sync the source locally and pass its path."
        )
    raise RuntimeError(f"Unrecognized input reference scheme: {scheme!r} ({input_ref!r})")


def _count_source(path: str) -> tuple[int, int]:
    p = pathlib.Path(path)
    total_files = total_loc = 0
    items = [p] if p.is_file() else list(p.rglob("*"))
    for f in items:
        if f.is_file():
            total_files += 1
            try:
                total_loc += len(f.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass
    return total_files, total_loc


def run_scan(
    input_type: str,
    input_ref: str,
    scan_context: dict | None = None,
) -> dict:
    """Run the full AI-ify pipeline for a given source input.

    Args:
        input_type:   e.g. 'local_path', 'git_url', 'upload'
        input_ref:    Path or URL to the source to analyze.
        scan_context: Optional; il_level defaults to 'il4'.

    Returns:
        {"scan_id", "opportunities_count", "scores_count", "roadmap_id",
         "kanban_promoted", "status"}
    """
    if scan_context is None:
        scan_context = {"il_level": "il4"}

    init_db()

    # ── Input resolution (local / UNC / git / file:// / s3://) ──────────────────
    scan_path, cloned_path = _resolve_input(input_type, input_ref)

    try:
        total_files, total_loc = _count_source(scan_path)

        # 1a. Agent Readiness check (runs before Semgrep scan)
        try:
            readiness_result = run_readiness_check(scan_path)
        except Exception as exc:  # noqa: BLE001
            readiness_result = {
                "pillar_scores": {},
                "overall_readiness_score": 0.0,
                "icdev_checks": {},
                "error": str(exc),
            }

        # Optional NLP extraction: classify the scan target ref for richer metadata.
        # Falls back silently to empty dict when disabled or LLM unavailable.
        nlp_ref_info = _nlp_extract_ref_info(input_ref, input_type)

        language_profile: dict = {
            "python": total_files,
            "agent_readiness_summary": {
                "pillar_scores": readiness_result["pillar_scores"],
                "overall_readiness_score": readiness_result["overall_readiness_score"],
                "icdev_checks": readiness_result["icdev_checks"],
            },
        }
        if nlp_ref_info:
            language_profile["nlp_ref_extraction"] = nlp_ref_info

        # 1b. Insert scan record
        conn = get_connection()
        try:
            scan_id: int = _insert(
                conn,
                "INSERT INTO aiify_scans "
                "(input_type, input_ref, language_profile, total_files, total_loc, status) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (input_type, input_ref, _dump(language_profile), total_files, total_loc, "running"),
                "scan_id",
            )

            conn.execute(
                "INSERT INTO aiify_audit_log (event_type, scan_id, actor, detail) VALUES (%s, %s, %s, %s)",
                ("scan_started", scan_id, "system", _dump({"input_type": input_type, "input_ref": input_ref})),
            )
            conn.commit()
        finally:
            conn.close()

        # 2. Detect patterns (Semgrep or AST fallback)
        patterns = detect_patterns(scan_path)

        # 3. Insert opportunities + scores
        opp_rows: list[dict] = []
        score_rows: list[dict] = []

        conn = get_connection()
        try:
            # Batch all opportunity+score inserts into ONE transaction (was a
            # per-row commit each iteration). The opportunity id is visible to its
            # own score insert pre-commit; a single commit lands after the loop
            # (penta-aiify-06 — atomic per scan, far fewer fsyncs).
            for pat in patterns:
                paradigm = _PATTERN_TO_PARADIGM.get(pat["pattern_type"], "llm_generation")
                il_model = _PARADIGM_TO_MODEL.get(paradigm, "claude-sonnet-4-6")

                opp_id: int = _insert(
                    conn,
                    "INSERT INTO aiify_opportunities "
                    "(scan_id, module_path, function_name, line_start, line_end, language, "
                    "pattern_type, pattern_detail, ai_paradigm, il_recommended_model, data_requirements) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        scan_id,
                        pat["module_path"],
                        pat.get("function_name", "<unknown>"),
                        pat.get("line_start", 0),
                        pat.get("line_end", 0),
                        pat.get("language", "python"),
                        pat["pattern_type"],
                        _dump(pat.get("pattern_detail", {})),
                        paradigm,
                        il_model,
                        _dump({}),
                    ),
                    "opportunity_id",
                    commit=False,
                )

                pat["ai_paradigm"] = paradigm
                score = score_and_assess(pat, scan_context)
                conn.execute(
                    "INSERT INTO aiify_scores "
                    "(opportunity_id, value_score, feasibility_score, risk_score, composite_score, "
                    "score_detail, verdict, ai_readiness, rationale, pros, cons, category) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        opp_id,
                        score["value_score"],
                        score["feasibility_score"],
                        score["risk_score"],
                        score["composite_score"],
                        _dump(score["score_detail"]),
                        score["verdict"],
                        score["ai_readiness"],
                        score["rationale"],
                        _dump(score["pros"]),
                        _dump(score["cons"]),
                        score["category"],
                    ),
                )

                opp_rows.append({
                    "opportunity_id": opp_id,
                    "ai_paradigm": paradigm,
                    "il_recommended_model": il_model,
                    **pat,
                })
                score_rows.append({"opportunity_id": opp_id, **score})
            conn.commit()  # single commit for the whole opportunity+score batch
        finally:
            conn.close()

        # 4. Generate roadmap (persists to aiify_roadmaps internally)
        roadmap = generate_roadmap(scan_id, opp_rows, score_rows)

        # 4a. Build and store deterministic project summary
        project_summary = _build_summary(input_ref, opp_rows)
        overall = roll_up_scan_verdict(score_rows)
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE aiify_scans SET project_summary = %s, overall_verdict = %s, "
                "overall_ai_readiness = %s, overall_rationale = %s WHERE scan_id = %s",
                (project_summary, overall["overall_verdict"],
                 overall["overall_ai_readiness"], overall["overall_rationale"], scan_id),
            )
            conn.commit()
        finally:
            conn.close()

        # 5. Promote top opportunities to kanban (top-5 [AI Opp] + all-phase [Phase])
        kanban_promoted = _promote_top_opportunities(opp_rows, score_rows, scan_id, roadmap["roadmap_id"])
        phase_promoted = _promote_phase_opportunities(roadmap, opp_rows, score_rows, scan_id)

        # 5a. Option C: register high-scoring opps as innovation signals
        _register_innovation_signals(opp_rows, score_rows, scan_id)

        # 6. Mark scan completed + final audit entry
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE aiify_scans SET status = %s, completed_at = %s WHERE scan_id = %s",
                ("completed", _now(), scan_id),
            )
            conn.execute(
                "INSERT INTO aiify_audit_log (event_type, scan_id, actor, detail) VALUES (%s, %s, %s, %s)",
                (
                    "scan_completed",
                    scan_id,
                    "system",
                    _dump({"opportunities_count": len(opp_rows), "roadmap_id": roadmap["roadmap_id"]}),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        scan_result = {
            "scan_id": scan_id,
            "opportunities_count": len(opp_rows),
            "scores_count": len(score_rows),
            "roadmap_id": roadmap["roadmap_id"],
            "kanban_promoted": kanban_promoted,
            "phase_promoted": phase_promoted,
            "status": "completed",
            "overall_verdict": overall["overall_verdict"],
            "overall_ai_readiness": overall["overall_ai_readiness"],
            "overall_rationale": overall["overall_rationale"],
            "pillar_scores": readiness_result["pillar_scores"],
            "overall_readiness_score": readiness_result["overall_readiness_score"],
            "icdev_checks": readiness_result["icdev_checks"],
        }

        # DIC Canvas Synergy — emit canvas_scored event (dsyn-emit-08)
        try:
            from tools.aiify.event_emitter import emit_canvas_scored
            # Use overall_ai_readiness as score proxy (A/B/C/D grade)
            readiness = overall.get("overall_ai_readiness", "C")
            emit_canvas_scored(
                canvas_name=input_ref if isinstance(input_ref, str) else "aiify",
                previous_grade="",  # no previous grade in this scan context
                current_grade=readiness,
                current_score=readiness_result.get("overall_readiness_score", 0.0) * 100,
                scan_id=str(scan_id),
            )
        except Exception:
            pass  # event emission never blocks scan result

        return scan_result
    finally:
        if cloned_path:
            shutil.rmtree(cloned_path, ignore_errors=True)
