# CUI // SP-CTI — ICDEV Data Design Canvas — Anomaly Detector
"""Classification-aware anomaly detector for DDC column profiles.

Pure function — no Flask, no DB writes.
Calls LLMRouter (code_generation) to surface anomalies from column stats.
Graceful fallback to rule-based heuristics when LLM is unavailable.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone

logger = get_logger("icdev.data_canvas.anomaly_detector")

try:
    from tools.canvas.ai_trace_mixin import record_canvas_decision as _record_decision
except Exception:
    def _record_decision(**_kw): pass  # type: ignore[assignment]

_PII_NAMES = {"email", "ssn", "phone", "dob", "zip", "phone_number", "social_security",
              "date_of_birth", "zipcode", "postal_code", "mobile", "cell", "fax"}

_FINDING_TYPES = {
    "high_null_rate",
    "zero_cardinality",
    "suspicious_distribution",
    "potential_pii",
    "outlier_range",
}

_RISK_ORDER = {"high": 3, "medium": 2, "low": 1, "none": 0}


# ── Rule-based fallback ───────────────────────────────────────────────────────

def _finding(col: str, severity: str, ftype: str, desc: str) -> dict:
    return {"column": col, "severity": severity, "finding_type": ftype, "description": desc}


def _check_null_rate(col_name: str, stats: dict) -> list[dict]:
    null_pct = float(stats.get("null_pct", 0) or 0)
    if null_pct > 40:
        return [_finding(col_name, "high", "high_null_rate", f"Column has {null_pct:.1f}% null values (threshold: >40%).")]
    if null_pct > 20:
        return [_finding(col_name, "medium", "high_null_rate", f"Column has {null_pct:.1f}% null values (threshold: >20%).")]
    return []


def _check_cardinality(col_name: str, stats: dict) -> list[dict]:
    dc = stats.get("distinct_count")
    if dc is not None and int(dc) == 0:
        return [_finding(col_name, "high", "zero_cardinality", "Column has zero distinct values — all rows are null or identical.")]
    return []


def _check_pii(col_name: str) -> list[dict]:
    col_lower = col_name.lower().replace("-", "_")
    if any(pii in col_lower for pii in _PII_NAMES):
        return [_finding(col_name, "medium", "potential_pii", f"Column name '{col_name}' suggests potential PII data.")]
    return []


def _check_outlier_range(col_name: str, stats: dict) -> list[dict]:
    col_min, col_max = stats.get("min"), stats.get("max")
    top_values = stats.get("top_values") or []
    if col_min is None or col_max is None or not top_values:
        return []
    try:
        mn, mx = float(col_min), float(col_max)
        tv_nums = [float(v) for v in top_values if v is not None]
        if tv_nums:
            tv_min, tv_max = min(tv_nums), max(tv_nums)
            spread = mx - mn
            if spread > 0 and ((tv_min - mn) / spread > 0.5 or (mx - tv_max) / spread > 0.5):
                return [_finding(col_name, "medium", "outlier_range",
                    f"Column range [{mn}, {mx}] extends far beyond most common values [{tv_min}, {tv_max}] — possible outliers.")]
    except (TypeError, ValueError):
        pass
    return []


def _rule_based_findings(profile_dict: dict) -> list[dict]:
    findings = []
    for col_name, stats in profile_dict.items():
        if not isinstance(stats, dict):
            continue
        findings.extend(_check_null_rate(col_name, stats))
        findings.extend(_check_cardinality(col_name, stats))
        findings.extend(_check_pii(col_name))
        findings.extend(_check_outlier_range(col_name, stats))
    return findings


def _compute_overall_risk(findings: list[dict]) -> str:
    if not findings:
        return "none"
    max_level = max((_RISK_ORDER.get(f.get("severity", "none"), 0) for f in findings), default=0)
    return {3: "high", 2: "medium", 1: "low", 0: "none"}[max_level]


# ── LLM prompt helpers ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a data quality analyst. Given column-level statistics, identify anomalies. "
    "Return ONLY valid JSON: {\"findings\": [{\"column\": str, \"severity\": \"high|medium|low\", "
    "\"finding_type\": str, \"description\": str}]}. "
    "finding_type must be one of: high_null_rate, zero_cardinality, suspicious_distribution, "
    "potential_pii, outlier_range."
)


def _build_prompt(profile_dict: dict) -> str:
    col_summaries = []
    for col, stats in profile_dict.items():
        if not isinstance(stats, dict):
            continue
        col_summaries.append(
            f"- {col}: null_pct={stats.get('null_pct')}, "
            f"distinct_count={stats.get('distinct_count')}, "
            f"min={stats.get('min')}, max={stats.get('max')}, "
            f"inferred_type={stats.get('inferred_type')}, "
            f"top_values={stats.get('top_values')}"
        )
    cols_text = "\n".join(col_summaries) if col_summaries else "(no columns)"
    return (
        "Analyze these column statistics and return a JSON findings array.\n\n"
        f"Columns:\n{cols_text}\n\n"
        "Flag: high null rate (>40% = high severity, >20% = medium), zero-cardinality columns, "
        "suspicious distributions, potential PII column names (email, ssn, phone, dob, zip), "
        "outlier min/max vs typical distribution.\n"
        "Return only the JSON object."
    )


def _parse_llm_response(raw: str) -> list[dict]:
    """Extract findings list from raw LLM text, tolerating markdown fences."""
    if not raw:
        return []
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(text)
        findings = data.get("findings", []) if isinstance(data, dict) else []
        validated = []
        for f in findings:
            if isinstance(f, dict) and "column" in f and "severity" in f:
                validated.append({
                    "column": str(f.get("column", "")),
                    "severity": str(f.get("severity", "low")),
                    "finding_type": str(f.get("finding_type", "suspicious_distribution")),
                    "description": str(f.get("description", "")),
                })
        return validated
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def detect_anomalies(
    profile_dict: dict,
    conn_params: dict | None = None,
    classification: str = "CUI",
    chain_mode: str = "",
) -> dict:
    """Detect anomalies in column-level profile statistics.

    Args:
        profile_dict: Mapping of column_name -> stats dict with keys:
            null_pct, distinct_count, min, max, top_values, inferred_type.
        conn_params: Unused (reserved for future DB-backed enrichment).
        classification: ATO classification marking applied to the result.
        chain_mode: Optional chain mode — "cot" for step-by-step reasoning,
            "cod" for debate-based evaluation.

    Returns:
        {
            "findings": [{"column", "severity", "finding_type", "description"}, ...],
            "overall_risk": "none|low|medium|high",
            "classification": str,
            "analyzed_at": ISO-8601 timestamp,
        }
    """
    analyzed_at = datetime.now(timezone.utc).isoformat()
    findings: list[dict] = []
    llm_used = False

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        provider, model_id, _cfg = router.get_provider_for_function("code_generation")
        if provider and model_id:
            prompt = _build_prompt(profile_dict)
            llm_request = LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_SYSTEM_PROMPT,
                max_tokens=1024,
                classification=classification,
                skip_injection_scan=True,
                chain_mode=chain_mode,
            )

            def _invoke():
                return router.invoke("code_generation", llm_request)

            ex = ThreadPoolExecutor(max_workers=1)
            future = ex.submit(_invoke)
            try:
                response = future.result(timeout=30)
                parsed = _parse_llm_response(response.content or "")
                if parsed:
                    findings = parsed
                    llm_used = True
            except FuturesTimeoutError:
                logger.info("LLM timeout (30s) for anomaly detection — using rule-based fallback")
            finally:
                ex.shutdown(wait=False)
    except Exception as exc:
        logger.info("LLM unavailable for anomaly detection (%s) — using rule-based fallback", exc)

    if not llm_used:
        findings = _rule_based_findings(profile_dict)

    result = {
        "findings": findings,
        "overall_risk": _compute_overall_risk(findings),
        "classification": classification,
        "analyzed_at": analyzed_at,
    }
    _record_decision(
        canvas_type="ddc",
        record_id=profile_dict.get("dataset_id") or profile_dict.get("design_id"),
        decision_type="anomaly",
        decision=f"{len(findings)} anomaly finding(s), overall_risk={result['overall_risk']}",
        rationale="LLM-assisted" if llm_used else "Rule-based",
        model_used=None,
        classification=classification,
    )
    return result


# ── Persistence helpers ───────────────────────────────────────────────────────

def _get_conn():
    # Canvas tables (dd_*) have no classification/tenant_id columns, so the
    # global RLS predicate attached by get_connection() would raise
    # UndefinedColumn. Use the canvas connection instead.
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection()


def save_anomaly_run(
    result: dict,
    profile_id: str | None = None,
    classification: str = "CUI",
) -> str:
    """Persist a detect_anomalies result to dd_anomaly_runs.

    Returns the inserted run_id (UUID hex string).
    Also stamps anomaly_json on the parent dd_explore_profiles row if profile_id given.
    """
    import uuid
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(result)

    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO dd_anomaly_runs "
            "(run_id, profile_id, overall_risk, findings_json, classification, run_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, profile_id, result.get("overall_risk", "none"), payload, classification, now),
        )

        if profile_id:
            conn.execute(
                "UPDATE dd_explore_profiles SET anomaly_json = ? WHERE profile_id = ?",
                (payload, profile_id),
            )

        conn.commit()
    finally:
        conn.close()

    return run_id


def get_latest_run(profile_id: str) -> dict | None:
    """Return the most recent anomaly run dict for a given profile_id, or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT findings_json, overall_risk, classification, run_at FROM dd_anomaly_runs "
            "WHERE profile_id = ? ORDER BY run_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    try:
        findings_data = json.loads(row[0] or "{}")
        findings = findings_data.get("findings", []) if isinstance(findings_data, dict) else []
    except (json.JSONDecodeError, TypeError):
        findings = []
    return {
        "findings": findings,
        "overall_risk": row[1],
        "classification": row[2],
        "analyzed_at": row[3],
    }
