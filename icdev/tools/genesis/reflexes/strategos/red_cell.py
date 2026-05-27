# CUI // SP-CTI
"""Red Cell Reflex — adversarial COA challenge for ICDEV™ Strategos.

Runs every 12h.  For each active COA in sg_coa_options:
  1. Parse friendly resource allocation across operational domains.
  2. Apply Nash equilibrium best-response (Colonel Blotto model) to derive
     the adversary counter-COA that maximises exploitation of gaps.
  3. Score vulnerability 0–10 and compute counter-probability.
  4. Persist to sg_red_cell_assessments.
  5. Surface Oracle recommendation via sg_sio_assessments when
     vulnerability_score >= 7.

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

_VULN_THRESHOLD = 7.0
_NATO_RELIABILITY = "B/2"   # usually reliable / probably true


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


def _vulnerability_score(friendly: Dict[str, float], adversary: Dict[str, float]) -> float:
    """Compute 0–10 vulnerability score from allocation entropy gap.

    Components:
    - Entropy gap:  low friendly entropy → concentrated → exploitable.
    - Max exploit:  maximum (adv_w - fri_w) across domains, scaled.
    - Critical gap: +1.5 per domain with friendly weight < 0.10 (max +3).
    """
    norm = _normalise(friendly)
    n = len(norm)
    max_entropy = math.log2(n) if n > 1 else 1.0
    entropy = -sum(w * math.log2(w) for w in norm.values() if w > 0)
    entropy_gap = 1.0 - (entropy / max_entropy)   # 0=balanced, 1=all-in-one

    max_gap = max(
        (adversary.get(d, 0) - norm.get(d, 0) for d in adversary), default=0.0
    )
    max_gap = max(max_gap, 0.0)

    critical_gaps = sum(1 for w in norm.values() if w < 0.10)
    critical_bonus = min(critical_gaps * 1.5, 3.0)

    raw = (entropy_gap * 5.0) + (max_gap * 4.0) + critical_bonus
    return round(min(raw, 10.0), 2)


def _counter_probability(score: float) -> float:
    """Map vulnerability score [0–10] to adversary counter-probability [0.05–0.95]."""
    return round(min(max(0.05 + (score / 10.0) * 0.90, 0.05), 0.95), 3)


def _build_counter_description(
    coa_title: str,
    friendly: Dict[str, float],
    adversary: Dict[str, float],
    score: float,
) -> str:
    norm = _normalise(friendly)
    sorted_adv = sorted(adversary.items(), key=lambda x: x[1], reverse=True)
    sorted_fri = sorted(norm.items(), key=lambda x: x[1])

    severity = (
        "CRITICAL" if score >= 8.5
        else "HIGH" if score >= 7.0
        else "MODERATE" if score >= 4.0
        else "LOW"
    )

    gaps = [f"{d} ({w:.0%})" for d, w in sorted_fri if w < 0.15]
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
        conn.execute(
            "INSERT INTO sg_coa_options (id, title, description, resource_allocation, status, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)" if is_pg() else
            "INSERT INTO sg_coa_options (id, title, description, resource_allocation, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
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


def _surface_oracle(assessments: List[Dict[str, Any]]) -> int:
    """Write findings with vulnerability_score >= 7 to sg_sio_assessments."""
    high = [a for a in assessments if a["vulnerability_score"] >= _VULN_THRESHOLD]
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

    _ensure_tables()

    coas = _fetch_active_coas()
    print(f"[red_cell] {len(coas)} active COA(s) found")

    if not coas:
        _seed_default_coa()
        coas = _fetch_active_coas()

    if not coas:
        print("[red_cell] no active COAs after seed — exiting cleanly")
        return {"success": True, "metric_value": 0.0, "details": {"reason": "no_active_coas"}}

    assessments: List[Dict[str, Any]] = []
    now = _utcnow_iso()

    for coa in coas:
        friendly = coa["allocation"]
        adversary = _blotto_best_response(friendly)
        score = _vulnerability_score(friendly, adversary)
        prob = _counter_probability(score)
        counter_desc = _build_counter_description(coa["title"], friendly, adversary, score)
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

        level = "CRITICAL" if score >= 8.5 else ("HIGH" if score >= 7.0 else "OK")
        print(
            f"[red_cell] '{coa['title']}' — score={score:.1f}/10 [{level}]"
            f"  P(counter)={prob:.0%}"
        )

    written = _write_assessments(assessments)
    oracle_rows = _surface_oracle(assessments)
    high_vuln = sum(1 for a in assessments if a["vulnerability_score"] >= _VULN_THRESHOLD)

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
        },
    }
