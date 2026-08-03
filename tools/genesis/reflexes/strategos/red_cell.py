# CUI // SP-CTI
"""Red Cell Reflex — adversarial COA challenge for ICDEV™ Strategos.

Runs every 12h.  For each active COA in sg_coa_options:
  1. Parse friendly resource allocation across operational domains.
  2. Apply Nash equilibrium best-response (Colonel Blotto model) to derive
     the adversary counter-COA that maximises exploitation of gaps.
  3. Score vulnerability 0–10 and compute counter-probability.
  4. Persist to sg_red_cell_assessments.
  5. Surface Oracle recommendation via sg_sio_assessments when
     vulnerability_score >= vuln_threshold (adaptive or static from config).

Config: args/strategos_config.yaml (red_cell section, optional).
Tables:  sg_coa_options, sg_red_cell_assessments, sg_sio_assessments.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[4]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, is_pg  # noqa: E402

logger = get_logger(__name__)

_NATO_RELIABILITY = "B/2"   # usually reliable / probably true

_CONFIG_PATH = BASE_DIR / "args" / "strategos_config.yaml"

# Defaults — used when config file is absent or key is missing.
_RC_DEFAULTS: Dict[str, Any] = {
    "vuln_threshold": 7.0,
    "severity": {"critical": 8.5, "high": 7.0, "moderate": 4.0},
    "thin_domain_threshold": 0.10,
    "gap_display_threshold": 0.15,
    "entropy_gap_weight": 5.0,
    "max_gap_weight": 4.0,
    "critical_bonus_per_gap": 1.5,
    "critical_bonus_max": 3.0,
}


def _load_config() -> Dict[str, Any]:
    """Return the full strategos_config.yaml as a dict (empty dict on missing/error).

    Provides the unmerged top-level structure so callers can read any section.
    """
    try:
        import yaml  # type: ignore
        if not _CONFIG_PATH.exists():
            return {}
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.debug("[red_cell] _load_config failed: %s", exc)
        return {}


def _load_red_cell_config() -> Dict[str, Any]:
    """Return red_cell config block merged with defaults (never raises)."""
    try:
        import yaml  # type: ignore
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        rc = raw.get("red_cell") or {}
        merged: Dict[str, Any] = dict(_RC_DEFAULTS)
        merged.update({k: v for k, v in rc.items() if v is not None})
        # Merge nested severity dict
        if "severity" in rc and isinstance(rc["severity"], dict):
            merged["severity"] = {**_RC_DEFAULTS["severity"], **rc["severity"]}
        return merged
    except Exception as exc:
        logger.debug("[red_cell] config load failed (%s) — using defaults", exc)
        return dict(_RC_DEFAULTS)


# ── anomaly detection helpers ─────────────────────────────────────────────────

def _fetch_recent_vuln_scores(*, window: int = 20) -> List[float]:
    """Return the N most-recent vulnerability scores from sg_red_cell_assessments."""
    try:
        conn = get_connection()
        rows = conn.execute(
            (
                "SELECT vulnerability_score FROM sg_red_cell_assessments "
                "ORDER BY generated_at DESC LIMIT %s"
                if is_pg() else
                "SELECT vulnerability_score FROM sg_red_cell_assessments "
                "ORDER BY generated_at DESC LIMIT ?"
            ),
            (window,),
        ).fetchall()
        conn.close()
        return [float(r[0]) for r in rows if r[0] is not None]
    except Exception as exc:
        logger.debug("[red_cell] _fetch_recent_vuln_scores error: %s", exc)
        return []


def _compute_anomaly_threshold(config: Dict[str, Any]) -> float:
    """Compute an adaptive vulnerability threshold using historical score statistics.

    config: full YAML dict (from _load_config()) with optional nested
    ["red_cell"]["anomaly_detection"] block.

    Falls back to the static vuln_threshold when:
    - anomaly_detection.enabled is False
    - fewer than min_records historical scores exist
    - DB query fails

    Formula: threshold = mean + zscore_k × population_std,
    clamped to [min_floor, max_cap].
    """
    rc = config.get("red_cell", {})
    static_threshold: float = float(rc.get("vuln_threshold", _RC_DEFAULTS["vuln_threshold"]))
    ad = rc.get("anomaly_detection") or {}

    if not ad.get("enabled", True):
        return static_threshold

    min_records: int = int(ad.get("min_records", 5))
    baseline_window: int = int(ad.get("baseline_window", 20))
    zscore_k: float = float(ad.get("zscore_k", 1.0))
    min_floor: float = float(ad.get("min_floor", 5.0))
    max_cap: float = float(ad.get("max_cap", 9.5))

    scores = _fetch_recent_vuln_scores(window=baseline_window)
    if len(scores) < min_records:
        return static_threshold

    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    std_dev = math.sqrt(variance)
    adaptive = mean + zscore_k * std_dev
    calibrated = round(min(max(adaptive, min_floor), max_cap), 2)
    logger.info(
        "[red_cell] anomaly threshold: mean=%.2f std=%.2f k=%.1f → %.2f (n=%d)",
        mean, std_dev, zscore_k, calibrated, len(scores),
    )
    return calibrated


# ── adaptive threshold calibration (anomaly detection) ────────────────────────

def _calibrate_vuln_threshold(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Adaptively calibrate the vulnerability threshold from historical data.

    Fetches recent vulnerability scores from sg_red_cell_assessments, computes
    a rolling mean + std_dev, and sets the effective threshold to
    mean + (zscore × std_dev).  This surfaces COAs that are anomalously
    vulnerable relative to recent history rather than crossing a static line.

    An optional LLM call (claude-haiku) may recommend reverting to the base
    config threshold when the adaptive value is statistically unreliable.

    Returns a dict with keys: threshold (float), method (str), adapted (bool),
    plus diagnostic fields written to the run() details block.
    """
    base: float = float(cfg.get("vuln_threshold", 7.0))
    ad_cfg: Dict[str, Any] = cfg.get("anomaly_detection", {})

    if not ad_cfg.get("enabled", True):
        return {"threshold": base, "method": "config_default", "adapted": False}

    baseline_window = int(ad_cfg.get("baseline_window", 20))
    min_history = int(ad_cfg.get("min_history", 5))
    zscore_multiplier = float(ad_cfg.get("zscore_threshold", 1.5))
    adapt = bool(ad_cfg.get("adapt_vuln_threshold", True))
    min_floor: float = float(ad_cfg.get("min_floor", 4.0))
    max_cap: float = float(ad_cfg.get("max_cap", 9.5))

    try:
        conn = get_connection()
        ph = _ph()
        rows = conn.execute(
            f"SELECT vulnerability_score FROM sg_red_cell_assessments "
            f"ORDER BY generated_at DESC LIMIT {ph}",
            (baseline_window,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.debug("[red_cell] calibrate: DB fetch failed: %s", exc)
        return {"threshold": base, "method": "config_default", "adapted": False}

    scores = [float(r[0]) for r in rows if r[0] is not None]
    n = len(scores)

    if n < min_history or not adapt:
        return {
            "threshold": base,
            "method": "config_default",
            "adapted": False,
            "history_count": n,
        }

    mean = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / n
    std_dev = variance ** 0.5

    adaptive = (
        round(max(min_floor, min(max_cap, mean + zscore_multiplier * std_dev)), 2)
        if std_dev > 0
        else base
    )

    result: Dict[str, Any] = {
        "threshold": adaptive,
        "method": "zscore_adaptive",
        "adapted": True,
        "history_count": n,
        "historical_mean": round(mean, 2),
        "historical_std": round(std_dev, 2),
        "zscore_used": zscore_multiplier,
    }

    model: str = ad_cfg.get("model", "claude-haiku-4-5-20251001")
    try:
        from tools.llm.router import LLMRouter  # type: ignore
        from tools.llm.provider import LLMRequest  # type: ignore
        import re as _re

        prompt = (
            "You are a Red Cell anomaly detection calibrator. "
            f"Historical vulnerability scores (last {n} assessments): "
            f"mean={mean:.2f}, std_dev={std_dev:.2f}. "
            f"Adaptive threshold computed: {adaptive:.2f} (mean + {zscore_multiplier}σ). "
            f"Config base threshold: {base:.1f}. "
            f'Reply with JSON only: {{"verdict": "adaptive_ok" or "revert_to_base", '
            f'"reason": "one sentence", "recommended_threshold": <number {min_floor}-{max_cap}>}}'
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=120,
            temperature=0.1,
        )
        resp = LLMRouter().invoke("anomaly_detection", req)
        m = _re.search(r"\{.*\}", resp.content, _re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            verdict = parsed.get("verdict", "adaptive_ok")
            rec = parsed.get("recommended_threshold")
            result["llm_reason"] = parsed.get("reason", "")
            if verdict == "revert_to_base":
                result["threshold"] = base
                result["method"] = "llm_reverted_to_base"
            elif rec is not None:
                result["threshold"] = round(max(min_floor, min(max_cap, float(rec))), 2)
                result["method"] = "llm_recommended"
    except Exception as exc:
        logger.debug("[red_cell] LLM calibration skipped: %s", exc)

    return result


# ── helpers ───────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ph() -> str:
    return "%s" if is_pg() else "?"


def _ensure_tables() -> None:
    try:
        from tools.db.migrations.run_migration import run_migration  # type: ignore
        run_migration("056_sg_coa_red_cell")
        return
    except Exception:
        pass

    # Inline fallback DDL — runs if migration runner is unavailable.
    try:
        conn = get_connection()
        conn.execute("""
CREATE TABLE IF NOT EXISTS sg_coa_options (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
    resource_allocation TEXT,
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
)""")
        conn.execute("""
CREATE TABLE IF NOT EXISTS sg_red_cell_assessments (
    id TEXT PRIMARY KEY, coa_id TEXT NOT NULL,
    counter_coa_description TEXT,
    vulnerability_score REAL NOT NULL DEFAULT 0,
    counter_probability REAL NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL
)""")
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_sg_coa_status  ON sg_coa_options(status)",
            "CREATE INDEX IF NOT EXISTS idx_src_coa_id     ON sg_red_cell_assessments(coa_id)",
            "CREATE INDEX IF NOT EXISTS idx_src_vuln       ON sg_red_cell_assessments(vulnerability_score DESC)",
        ):
            try:
                conn.execute(stmt)
            except Exception:
                pass
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("[red_cell] inline DDL failed: %s", exc)


# ── Colonel Blotto Nash equilibrium model ────────────────────────────────────

def _normalise(allocation: Dict[str, float]) -> Dict[str, float]:
    """Normalise weights to sum=1.  Floors each domain at 0.05 to avoid /0."""
    domains = list(allocation.keys())
    weights = [max(float(allocation[d]), 0.05) for d in domains]
    total = sum(weights)
    return {d: w / total for d, w in zip(domains, weights)}


def _blotto_best_response(friendly: Dict[str, float]) -> Dict[str, float]:
    """Adversary Nash mixed strategy: weight domains by 1/w_i (inverse allocation).

    In a Colonel Blotto game, the adversary maximises expected wins by
    concentrating resources where the friendly allocation is thinnest.
    The maximin mixed strategy assigns probability ∝ 1/w_i to each domain.
    """
    norm = _normalise(friendly)
    inv = {d: 1.0 / w for d, w in norm.items()}
    total_inv = sum(inv.values())
    return {d: v / total_inv for d, v in inv.items()}


def _vulnerability_score(
    friendly: Dict[str, float],
    adversary: Dict[str, float],
    cfg: Dict[str, Any] | None = None,
) -> float:
    """Compute 0–10 vulnerability score from allocation entropy gap.

    Components:
    - Entropy gap:  low friendly entropy → concentrated → exploitable.
    - Max exploit:  maximum (adv_w - fri_w) across domains, scaled.
    - Critical gap: +bonus per domain with friendly weight < thin_domain_threshold.
    """
    if cfg is None:
        cfg = _load_red_cell_config()
    thin_threshold: float = cfg["thin_domain_threshold"]
    entropy_w: float = cfg["entropy_gap_weight"]
    max_gap_w: float = cfg["max_gap_weight"]
    bonus_per: float = cfg["critical_bonus_per_gap"]
    bonus_cap: float = cfg["critical_bonus_max"]

    norm = _normalise(friendly)
    n = len(norm)
    max_entropy = math.log2(n) if n > 1 else 1.0
    entropy = -sum(w * math.log2(w) for w in norm.values() if w > 0)
    entropy_gap = 1.0 - (entropy / max_entropy)   # 0=balanced, 1=all-in-one

    max_gap = max(
        (adversary.get(d, 0) - norm.get(d, 0) for d in adversary), default=0.0
    )
    max_gap = max(max_gap, 0.0)

    critical_gaps = sum(1 for w in norm.values() if w < thin_threshold)
    critical_bonus = min(critical_gaps * bonus_per, bonus_cap)

    raw = (entropy_gap * entropy_w) + (max_gap * max_gap_w) + critical_bonus
    return round(min(raw, 10.0), 2)


def _counter_probability(score: float) -> float:
    """Map vulnerability score [0–10] to adversary counter-probability [0.05–0.95]."""
    return round(min(max(0.05 + (score / 10.0) * 0.90, 0.05), 0.95), 3)


def _build_counter_description(
    coa_title: str,
    friendly: Dict[str, float],
    adversary: Dict[str, float],
    score: float,
    cfg: Dict[str, Any] | None = None,
    *,
    severity_config: Dict[str, float] | None = None,
) -> str:
    if cfg is None:
        cfg = _load_red_cell_config()
    sev = severity_config if severity_config is not None else cfg["severity"]
    gap_display: float = cfg["gap_display_threshold"]

    norm = _normalise(friendly)
    sorted_adv = sorted(adversary.items(), key=lambda x: x[1], reverse=True)
    sorted_fri = sorted(norm.items(), key=lambda x: x[1])

    severity = (
        "CRITICAL" if score >= sev["critical"]
        else "HIGH" if score >= sev["high"]
        else "MODERATE" if score >= sev["moderate"]
        else "LOW"
    )

    gaps = [f"{d} ({w:.0%})" for d, w in sorted_fri if w < gap_display]
    gap_str = ", ".join(gaps) if gaps else sorted_fri[0][0] if sorted_fri else "unknown"
    top_exploit = " and ".join(d for d, _ in sorted_adv[:2])

    lines = [
        f"RED CELL COUNTER-COA [{severity}] vs. '{coa_title}'",
        "",
        f"Exploitation vector: thin friendly allocation in {gap_str}.",
        f"Primary adversary strike domains: {top_exploit}.",
        "",
        "Adversary reallocation (Nash best-response):",
    ]
    for domain, adv_w in sorted_adv[:4]:
        fri_w = norm.get(domain, 0.0)
        delta = adv_w - fri_w
        lines.append(
            f"  • {domain}: {adv_w:.0%} adv vs {fri_w:.0%} friendly  (gap +{delta:.0%})"
        )
    lines += [
        "",
        (
            "Assessment: Recommend redistributing friendly allocation to reduce "
            f"entropy gap and close critical-domain thin spots.  "
            f"Vulnerability score: {score:.1f}/10."
        ),
    ]
    return "\n".join(lines)


# ── DB read/write ─────────────────────────────────────────────────────────────

def _fetch_active_coas() -> List[Dict[str, Any]]:
    try:
        conn = get_connection()
        query = (
            "SELECT id, title, description, resource_allocation "
            "FROM sg_coa_options WHERE status = %s"
            if is_pg() else
            "SELECT id, title, description, resource_allocation "
            "FROM sg_coa_options WHERE status = ?"
        )
        rows = conn.execute(query, ("active",)).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("[red_cell] fetch_coas error: %s", exc)
        return []

    result = []
    for row in rows:
        coa_id, title, desc, alloc_json = row
        try:
            allocation = json.loads(alloc_json) if alloc_json else {}
        except Exception:
            allocation = {}
        if isinstance(allocation, dict) and allocation:
            result.append({
                "id": coa_id,
                "title": title or coa_id,
                "description": desc or "",
                "allocation": allocation,
            })
    return result


def _seed_default_coa() -> None:
    """Insert a sample active COA on a fresh DB so the reflex has data to work with."""
    try:
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM sg_coa_options WHERE status = %s" if is_pg() else
            "SELECT COUNT(*) FROM sg_coa_options WHERE status = ?",
            ("active",)
        ).fetchone()[0]
        if count > 0:
            conn.close()
            return
        now = _utcnow_iso()
        # title/description are not columns on sg_coa_options (swp-scan-01);
        # the live names are coa_name/course_description, and updated_at is
        # NOT NULL. The seed therefore never landed, so the reflex kept finding
        # zero active COAs and re-running this same failing insert.
        conn.execute(
            "INSERT INTO sg_coa_options "
            "(id, coa_name, course_description, resource_allocation, status, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)" if is_pg() else
            "INSERT INTO sg_coa_options "
            "(id, coa_name, course_description, resource_allocation, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "coa-seed-01",
                "COA Alpha — Force Projection",
                "Default seed: heavy kinetic, thin cyber/space allocation",
                json.dumps({
                    "cyber": 0.10,
                    "kinetic": 0.50,
                    "information": 0.15,
                    "logistics": 0.20,
                    "space": 0.05,
                }),
                "active",
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        print("[red_cell] seeded default COA Alpha for demonstration")
    except Exception as exc:
        logger.warning("[red_cell] seed failed: %s", exc)


def _write_assessments(assessments: List[Dict[str, Any]]) -> int:
    if not assessments:
        return 0
    try:
        conn = get_connection()
        _pg = is_pg()
        written = 0
        for a in assessments:
            rid = a["id"]
            exists = conn.execute(
                "SELECT 1 FROM sg_red_cell_assessments WHERE id = %s" if _pg else
                "SELECT 1 FROM sg_red_cell_assessments WHERE id = ?",
                (rid,)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE sg_red_cell_assessments "
                    "SET counter_coa_description=%s, vulnerability_score=%s, "
                    "counter_probability=%s, generated_at=%s WHERE id=%s" if _pg else
                    "UPDATE sg_red_cell_assessments "
                    "SET counter_coa_description=?, vulnerability_score=?, "
                    "counter_probability=?, generated_at=? WHERE id=?",
                    (a["counter_coa_description"], a["vulnerability_score"],
                     a["counter_probability"], a["generated_at"], rid),
                )
            else:
                conn.execute(
                    "INSERT INTO sg_red_cell_assessments "
                    "(id, coa_id, counter_coa_description, vulnerability_score, "
                    "counter_probability, generated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)" if _pg else
                    "INSERT INTO sg_red_cell_assessments "
                    "(id, coa_id, counter_coa_description, vulnerability_score, "
                    "counter_probability, generated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (rid, a["coa_id"], a["counter_coa_description"],
                     a["vulnerability_score"], a["counter_probability"], a["generated_at"]),
                )
            written += 1
        conn.commit()
        conn.close()
        return written
    except Exception as exc:
        logger.error("[red_cell] write_assessments error: %s", exc)
        return 0


def _surface_oracle(assessments: List[Dict[str, Any]], cfg: Dict[str, Any] | None = None) -> int:
    """Write findings with vulnerability_score >= vuln_threshold to sg_sio_assessments."""
    if cfg is None:
        cfg = _load_red_cell_config()
    vuln_threshold: float = cfg["vuln_threshold"]
    high = [a for a in assessments if a["vulnerability_score"] >= vuln_threshold]
    if not high:
        return 0
    try:
        conn = get_connection()
        # Ensure sg_sio_assessments exists (created by migration 050).
        conn.execute("""
CREATE TABLE IF NOT EXISTS sg_sio_assessments (
    id TEXT PRIMARY KEY, confidence REAL NOT NULL,
    nato_reliability TEXT NOT NULL, recommendation TEXT,
    lens_source TEXT NOT NULL, timestamp TEXT NOT NULL,
    score REAL, narrative TEXT, evidence_json TEXT,
    created_at TEXT NOT NULL
)""")
        _pg = is_pg()
        now = _utcnow_iso()
        written = 0
        for a in high:
            sid = "rc-" + _sha256_short(a["coa_id"] + now)
            exists = conn.execute(
                "SELECT 1 FROM sg_sio_assessments WHERE id = %s" if _pg else
                "SELECT 1 FROM sg_sio_assessments WHERE id = ?",
                (sid,)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO sg_sio_assessments "
                "(id, confidence, nato_reliability, recommendation, lens_source, "
                "timestamp, score, narrative, evidence_json, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)" if _pg else
                "INSERT INTO sg_sio_assessments "
                "(id, confidence, nato_reliability, recommendation, lens_source, "
                "timestamp, score, narrative, evidence_json, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    round(a["vulnerability_score"] / 10.0, 3),
                    _NATO_RELIABILITY,
                    (
                        f"Red Cell: COA '{a.get('coa_title', a['coa_id'])}' "
                        f"scores {a['vulnerability_score']:.1f}/10 vulnerability.  "
                        f"Adversary counter-probability: {a['counter_probability']:.0%}.  "
                        f"Immediate force rebalancing recommended."
                    ),
                    "red_cell",
                    now,
                    a["vulnerability_score"],
                    a["counter_coa_description"][:500],
                    json.dumps({
                        "coa_id": a["coa_id"],
                        "vulnerability_score": a["vulnerability_score"],
                        "counter_probability": a["counter_probability"],
                        "assessment_id": a["id"],
                    }),
                    now,
                ),
            )
            written += 1
        conn.commit()
        conn.close()
        return written
    except Exception as exc:
        logger.error("[red_cell] surface_oracle error: %s", exc)
        return 0


# ── entry point ───────────────────────────────────────────────────────────────

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Genesis reflex entry point — called by daemon every 12h."""
    print("[red_cell] starting adversarial COA challenge")

    rc_cfg = _load_red_cell_config()
    _ensure_tables()

    coas = _fetch_active_coas()
    print(f"[red_cell] {len(coas)} active COA(s) found")

    if not coas:
        _seed_default_coa()
        coas = _fetch_active_coas()

    if not coas:
        print("[red_cell] no active COAs after seed — exiting cleanly")
        return {"success": True, "metric_value": 0.0, "details": {"reason": "no_active_coas"}}

    sev = rc_cfg["severity"]
    cal = _calibrate_vuln_threshold(rc_cfg)
    vuln_threshold: float = float(cal["threshold"])
    cal_method: str = cal.get("method", "config_default")
    if cal.get("adapted"):
        print(
            f"[red_cell] adaptive vuln_threshold={vuln_threshold:.2f} "
            f"(method={cal_method}, n={cal.get('history_count', 0)}, "
            f"mean={cal.get('historical_mean', 'n/a')}, "
            f"std={cal.get('historical_std', 'n/a')})"
        )
    assessments: List[Dict[str, Any]] = []
    now = _utcnow_iso()

    for coa in coas:
        friendly = coa["allocation"]
        adversary = _blotto_best_response(friendly)
        score = _vulnerability_score(friendly, adversary, rc_cfg)
        prob = _counter_probability(score)
        counter_desc = _build_counter_description(coa["title"], friendly, adversary, score, rc_cfg)
        aid = "rc-" + _sha256_short(coa["id"] + now)

        assessments.append({
            "id": aid,
            "coa_id": coa["id"],
            "coa_title": coa["title"],
            "counter_coa_description": counter_desc,
            "vulnerability_score": score,
            "counter_probability": prob,
            "generated_at": now,
        })

        level = (
            "CRITICAL" if score >= sev["critical"]
            else "HIGH" if score >= sev["high"]
            else "OK"
        )
        print(
            f"[red_cell] '{coa['title']}' — score={score:.1f}/10 [{level}]"
            f"  P(counter)={prob:.0%}"
        )

    # Inject calibrated threshold into config copy for oracle surface call
    rc_cfg_run = {**rc_cfg, "vuln_threshold": vuln_threshold}
    written = _write_assessments(assessments)
    oracle_rows = _surface_oracle(assessments, rc_cfg_run)
    high_vuln = sum(1 for a in assessments if a["vulnerability_score"] >= vuln_threshold)

    print(
        f"[red_cell] done — {written} assessments written, "
        f"{oracle_rows} Oracle rows surfaced, "
        f"{high_vuln} high-vulnerability COA(s)"
    )

    return {
        "success": True,
        "metric_value": float(written),
        "details": {
            "coas_assessed": len(coas),
            "assessments_written": written,
            "oracle_rows": oracle_rows,
            "high_vulnerability": high_vuln,
            "vuln_threshold_used": vuln_threshold,
            "threshold_method": cal_method,
            "threshold_adapted": cal.get("adapted", False),
        },
    }
