# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) — Pipeline Engine.

Public API:
    run_scan(input_type, input_ref, scan_context=None) -> dict

Orchestrates the full AAC pipeline:
  1. init_db()         — ensure schema is ready
  2. aac_scans INSERT  — status='running'
  3. detect_patterns() — Semgrep or AST fallback
  4. aac_opportunities INSERT + score_opportunity() per hit
  5. aac_scores INSERT
  6. generate_roadmap() -> aac_roadmaps INSERT
  7. promote top-5 opportunities -> kanban_tasks + aac_audit_log
  8. aac_scans UPDATE  — status='completed'
  9. aac_audit_log     — scan_started / scan_completed events
"""
from __future__ import annotations

import functools
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

import yaml

from tools.ai_augmentation.agent_readiness import run_readiness_check
from tools.ai_augmentation.db.init_db import get_connection, init_db
from tools.ai_augmentation.opportunity_scorer import score_opportunity
from tools.ai_augmentation.pattern_classifier import detect_patterns
from tools.ai_augmentation.roadmap_generator import generate_roadmap
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ai_augmentation.engine")

_CONFIG_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "args" / "aac_config.yaml"

_FALLBACK_ANOMALY_THRESHOLDS = {
    "innovation_signal_min_score": 0.60,
    "phase": {
        "p1_min_score": 0.70,
        "p2_min_score": 0.50,
        "p3_min_score": 0.30,
    },
    "priority_high_min_score": 0.70,
    # Score anomaly detection thresholds (mirrors args/aac_config.yaml)
    "value_feasibility_max_delta": 0.50,
    "component_outlier_floor": 0.05,
    "component_outlier_ceiling": 0.95,
}


def _load_anomaly_thresholds() -> dict:
    """Load anomaly_detection thresholds from args/aac_config.yaml with fallback."""
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

# Toggle for the optional NLP reference extractor.  Disabled by default so unit
# tests that mock the router can opt-in explicitly.
_NLP_REF_ENABLED = False


@functools.lru_cache(maxsize=128)
def _nlp_classify_ref(ref: str) -> tuple[bool, str]:
    """NLP extractor: classify a user-provided input reference string.

    Uses Claude (nlp_extractor model) to determine whether the ref is a git URL
    and to extract a short project name.  Falls back to regex when the Anthropic
    API is unavailable so callers always get a valid result.

    Returns:
        (is_git_url, project_name)
    """
    try:
        from tools.llm.router import LLMRouter, LLMRequest
        model = _PARADIGM_TO_MODEL.get("nlp_extractor", "claude-haiku-4-5-20251001")
        router = LLMRouter()
        req = LLMRequest(
            function="nlp_extractor",
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this input reference: {ref!r}\n"
                    "Return JSON only — no explanation:\n"
                    '{"is_git_url": <bool>, "project_name": "<short name>"}\n'
                    "is_git_url is true for https://, git@, or .git URLs. "
                    "project_name is the short repository or directory name."
                ),
            }],
            model=model,
            max_tokens=128,
        )
        resp = router.invoke("nlp_extractor", req)
        result = json.loads(resp.content.strip())
        return bool(result.get("is_git_url", False)), str(result.get("project_name", "") or ref.split("/")[-1])
    except Exception:
        # Regex fallback — identical logic to the original implementation
        is_git = bool(re.match(r"^(https?://|git@)", ref) or ref.endswith(".git"))
        if is_git:
            name = ref.rstrip("/").split("/")[-1].replace(".git", "")
        else:
            name = pathlib.Path(ref).name or ref
        return is_git, name


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(value: Any) -> Any:
    backend = os.environ.get(
        "AAC_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()
    if backend == "postgresql":
        try:
            from psycopg2.extras import Json
            return Json(value)
        except ImportError:
            pass
    return json.dumps(value)


def _exec(conn: Any, sql: str, params: tuple) -> Any:
    try:
        return conn.execute(sql, params)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return conn.execute(sql.replace("?", "%s"), params)


def _backend() -> str:
    return os.environ.get(
        "AAC_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()


def _insert(conn: Any, sql: str, params: tuple, id_col: str = "id") -> int:
    """Execute INSERT and return the generated PK for both SQLite and PostgreSQL."""
    if _backend() == "postgresql":
        pg_sql = sql.replace("?", "%s") + f" RETURNING {id_col}"
        cur = conn.execute(pg_sql, params)
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid or 0


def _build_summary(
    input_ref: str,
    opp_rows: list[dict],
    nlp_ref_info: dict | None = None,
) -> str:
    """Derive a one-sentence plain-English project summary."""
    ref = input_ref.strip().rstrip("/")
    if nlp_ref_info and nlp_ref_info.get("project_name"):
        name = nlp_ref_info["project_name"]
    else:
        _, name = _nlp_classify_ref(ref)

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


def _register_innovation_signals(opp_rows: list[dict], score_rows: list[dict], scan_id: int) -> int:
    """Option C: cross-register high-scoring AAC opportunities to innovation_signals."""
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
                title = f"AI Augmentation: {pattern} → {paradigm} in {module}"
                description = (
                    f"Detected by AAC scan #{scan_id}. Pattern: {pattern}, "
                    f"AI paradigm: {paradigm}, composite score: {composite:.2f}."
                )
                content_hash = hashlib.sha256(
                    f"aac-{scan_id}-{opp_id}".encode()
                ).hexdigest()[:32]
                signal_id = f"aac-{scan_id}-{opp_id}"
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
                        signal_id, "aac_opportunities", "internal_analysis",
                        title, description, content_hash,
                        _now(), "approved", "ai_augmentation_opportunity", composite,
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


def _promote_top_opportunities(
    opp_rows: list[dict],
    score_rows: list[dict],
    scan_id: int,
    roadmap_id: str,
) -> int:
    """Promote top 5 opportunities (by composite_score) to kanban_tasks.

    Uses the main ICDEV get_connection() for kanban_tasks and the AAC
    get_connection() for aac_audit_log. Skips tasks whose id already exists.
    Returns the count of tasks actually inserted.
    """
    from tools.db.storage import get_connection as _icdev_get_connection

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
    top5 = enriched[:5]
    if not top5:
        return 0

    thresholds = _load_anomaly_thresholds()
    priority_high_min = thresholds.get(
        "priority_high_min_score",
        _FALLBACK_ANOMALY_THRESHOLDS["priority_high_min_score"],
    )
    promoted_opps: list[dict] = []
    icdev_conn = _icdev_get_connection()
    try:
        for opp in top5:
            opp_id = opp["opportunity_id"]
            task_id = f"aac-opp-{str(opp_id)[:8]}"

            existing = _exec(
                icdev_conn,
                "SELECT id FROM kanban_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if existing:
                continue

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
            _exec(
                icdev_conn,
                "INSERT INTO kanban_tasks "
                "(id, title, description, task_type, priority, status, executor_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, title, description, "build", priority, "suggested", "claude_cli"),
            )
            icdev_conn.commit()
            promoted_opps.append({**opp, "task_id": task_id})
    finally:
        icdev_conn.close()

    if not promoted_opps:
        return 0

    # Write one audit entry per promoted task (separate AAC connection)
    aac_conn = get_connection()
    try:
        for opp in promoted_opps:
            _exec(
                aac_conn,
                "INSERT INTO aac_audit_log (event_type, scan_id, actor, detail) VALUES (?, ?, ?, ?)",
                (
                    "kanban_promoted",
                    scan_id,
                    "system",
                    _dump({
                        "task_id": opp["task_id"],
                        "opportunity_id": opp["opportunity_id"],
                        "composite_score": opp["composite_score"],
                        "roadmap_id": roadmap_id,
                    }),
                ),
            )
        aac_conn.commit()
    finally:
        aac_conn.close()

    return len(promoted_opps)


def _promote_phase_opportunities(
    roadmap: dict,
    opp_rows: list[dict],
    score_rows: list[dict],
    scan_id: int,
) -> int:
    """Create [Phase] kanban tasks for every opportunity bucketed into P1/P2/P3.

    One task per opportunity, labelled with its roadmap phase.  Tasks whose ID
    already exists are skipped (idempotent).  Returns the count inserted.
    """
    from tools.db.storage import get_connection as _icdev_get_connection

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
    inserted = 0
    icdev_conn = _icdev_get_connection()
    try:
        for phase in roadmap.get("phases", []):
            phase_label = phase.get("label", "")
            for opp_item in phase.get("opportunities", []):
                opp_id = int(opp_item.get("opportunity_id", 0))
                task_id = f"aac-rm-{short_id}-phase-{opp_id}"

                existing = _exec(
                    icdev_conn,
                    "SELECT id FROM kanban_tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if existing:
                    continue

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
                            "composite": float(score.get("composite_score", 0.0)),
                            "value": float(score.get("value_score", 0.0)),
                            "feasibility": float(score.get("feasibility_score", 0.0)),
                            "risk": float(score.get("risk_score", 0.0)),
                        },
                    },
                    indent=2,
                )
                priority = "high" if float(score.get("composite_score", 0.0)) >= priority_high_min else "medium"
                _exec(
                    icdev_conn,
                    "INSERT INTO kanban_tasks "
                    "(id, title, description, task_type, priority, status, executor_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (task_id, title, description, "build", priority, "suggested", "claude_cli"),
                )
                icdev_conn.commit()
                inserted += 1
    finally:
        icdev_conn.close()

    return inserted


def _is_git_url(ref: str) -> bool:
    """Return True if ref looks like a git or HTTPS repository URL."""
    return _nlp_classify_ref(ref.strip().rstrip("/"))[0]


def _strip_markdown_fences(text: str) -> str:
    """Remove leading/trailing ```json...``` fences from LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _coerce_language_list(value: Any) -> list[str]:
    """Normalize detected_languages to a list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _nlp_extract_ref_info(ref: str, ref_type: str) -> dict:
    """Use an LLM to extract project metadata from a git/URL/path reference.

    Returns:
        dict with keys project_name, hosting_platform, detected_languages,
        or {} if disabled, imports are missing, or the LLM call fails.
    """
    if not _NLP_REF_ENABLED:
        return {}

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except Exception:
        return {}

    model = _PARADIGM_TO_MODEL.get("nlp_extractor", "claude-haiku-4-5-20251001")
    router = LLMRouter()
    prompt = (
        f"Analyze this input reference: {ref!r} (type: {ref_type})\n"
        "Return JSON only — no explanation:\n"
        '{"project_name": "<short name>", "hosting_platform": "<platform>", '
        '"detected_languages": ["<lang>", ...]}\n'
        "hosting_platform should be one of: github, gitlab, bitbucket, azure_devops, local, unknown. "
        "detected_languages must be a list of programming-language strings."
    )
    try:
        req = LLMRequest(
            function="nlp_extractor",
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=256,
        )
        resp = router.invoke("nlp_extractor", req)
        content = _strip_markdown_fences(resp.content)
        result = json.loads(content)
    except Exception:
        return {}

    if not isinstance(result, dict):
        return {}

    project_name = str(result.get("project_name", "") or "").strip()
    hosting_platform = str(result.get("hosting_platform", "") or "").strip().lower()
    detected_languages = _coerce_language_list(result.get("detected_languages", []))

    return {
        "project_name": project_name,
        "hosting_platform": hosting_platform,
        "detected_languages": detected_languages,
    }


def _clone_git_url(url: str) -> str:
    """Shallow-clone a git URL into a temp directory and return the local path.

    Raises RuntimeError if git is unavailable or the clone fails.
    """
    if not shutil.which("git"):
        raise RuntimeError("git CLI is not installed; cannot clone remote repository")

    clone_dir = tempfile.mkdtemp(prefix="aac_git_")
    cmd = ["git", "clone", "--depth", "1", "--single-branch", url, clone_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        shutil.rmtree(clone_dir, ignore_errors=True)
        raise RuntimeError(
            f"git clone failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return clone_dir


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
    """Run the full AAC pipeline for a given source input.

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

    # ── Git URL normalisation ───────────────────────────────────────────────────
    cloned_path: str | None = None
    scan_path = input_ref
    if input_type == "git_url" or _is_git_url(input_ref):
        cloned_path = _clone_git_url(input_ref)
        scan_path = cloned_path

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

        language_profile: dict = {
            "python": total_files,
            "agent_readiness_summary": {
                "pillar_scores": readiness_result["pillar_scores"],
                "overall_readiness_score": readiness_result["overall_readiness_score"],
                "icdev_checks": readiness_result["icdev_checks"],
            },
        }

        nlp_ref_info = _nlp_extract_ref_info(input_ref, input_type)
        if nlp_ref_info:
            language_profile["nlp_ref_extraction"] = nlp_ref_info

        # 1b. Insert scan record
        conn = get_connection()
        try:
            scan_id: int = _insert(
                conn,
                "INSERT INTO aac_scans "
                "(input_type, input_ref, language_profile, total_files, total_loc, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (input_type, input_ref, _dump(language_profile), total_files, total_loc, "running"),
                "scan_id",
            )

            _exec(
                conn,
                "INSERT INTO aac_audit_log (event_type, scan_id, actor, detail) VALUES (?, ?, ?, ?)",
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
            for pat in patterns:
                paradigm = _PATTERN_TO_PARADIGM.get(pat["pattern_type"], "llm_generation")
                il_model = _PARADIGM_TO_MODEL.get(paradigm, "claude-sonnet-4-6")

                opp_id: int = _insert(
                    conn,
                    "INSERT INTO aac_opportunities "
                    "(scan_id, module_path, function_name, line_start, line_end, language, "
                    "pattern_type, pattern_detail, ai_paradigm, il_recommended_model, data_requirements) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                )

                score = score_opportunity(pat, scan_context)
                _exec(
                    conn,
                    "INSERT INTO aac_scores "
                    "(opportunity_id, value_score, feasibility_score, risk_score, composite_score, score_detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        opp_id,
                        score["value_score"],
                        score["feasibility_score"],
                        score["risk_score"],
                        score["composite_score"],
                        _dump(score["score_detail"]),
                    ),
                )
                conn.commit()

                opp_rows.append({
                    "opportunity_id": opp_id,
                    "ai_paradigm": paradigm,
                    "il_recommended_model": il_model,
                    **pat,
                })
                score_rows.append({"opportunity_id": opp_id, **score})
        finally:
            conn.close()

        # 4. Generate roadmap (persists to aac_roadmaps internally)
        roadmap = generate_roadmap(scan_id, opp_rows, score_rows)

        # 4a. Build and store deterministic project summary
        project_summary = _build_summary(input_ref, opp_rows, nlp_ref_info=nlp_ref_info)
        conn = get_connection()
        try:
            _exec(conn, "UPDATE aac_scans SET project_summary = ? WHERE scan_id = ?",
                  (project_summary, scan_id))
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
            _exec(
                conn,
                "UPDATE aac_scans SET status = ?, completed_at = ? WHERE scan_id = ?",
                ("completed", _now(), scan_id),
            )
            _exec(
                conn,
                "INSERT INTO aac_audit_log (event_type, scan_id, actor, detail) VALUES (?, ?, ?, ?)",
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

        return {
            "scan_id": scan_id,
            "opportunities_count": len(opp_rows),
            "scores_count": len(score_rows),
            "roadmap_id": roadmap["roadmap_id"],
            "kanban_promoted": kanban_promoted,
            "phase_promoted": phase_promoted,
            "status": "completed",
            "pillar_scores": readiness_result["pillar_scores"],
            "overall_readiness_score": readiness_result["overall_readiness_score"],
            "icdev_checks": readiness_result["icdev_checks"],
        }
    finally:
        if cloned_path:
            shutil.rmtree(cloned_path, ignore_errors=True)
