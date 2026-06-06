# CUI // SP-CTI
"""DIC Freshness Engine — staleness scoring + autonomous reflex trigger.

Scoring dimensions:
  • Document age vs retention tier (default 90 days)
  • Time since last approved version
  • Number of drift events affecting the collection since last update
  • Section count pending review (more pending = less fresh)

Outputs:
  • per-doc dic_doc_freshness row (state, reason, source_event)
  • per-collection dic_freshness_scans row (stale_count, regen_priority)
  • ranked remediation list for the dashboard heatmap
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_FRESHNESS_STATES = ["fresh", "aging", "stale", "unknown"]
_DEFAULT_RETENTION_DAYS = 90

# State classification cutoffs. These were the original hardcoded magic numbers
# inline in _score_doc; aiify-opp-6042 (hardcoded_threshold -> anomaly_detection,
# the document-freshness sibling of opp-6090 in analytics_engine) lifts them into
# named constants. score < _FRESH_THRESHOLD → fresh; < _STALE_THRESHOLD → aging;
# otherwise stale. Changing the band boundaries now happens in one place.
_FRESH_THRESHOLD = 0.35
_STALE_THRESHOLD = 0.70

# ── Anomaly detection (collection-relative outliers) ──────────────────────────
# The fixed thresholds above grade each document against an absolute retention
# yardstick. They are blind to a document that is anomalous *for its collection*:
# the one stale outlier in an otherwise-fresh corpus, or the worst-rotted doc in
# a uniformly aging one. The anomaly layer flags docs whose freshness score sits
# more than _ANOMALY_STDEV_K standard deviations above the collection mean. It is
# a pure statistical heuristic and is ALWAYS authoritative; the optional LLM grade
# is best-effort severity enrichment that degrades silently to None.
_ANOMALY_STDEV_K = 2.0          # outlier if score > mean + k * stdev
_ANOMALY_MIN_DOCS = 4           # need a real distribution before flagging outliers
_ANOMALY_ABS_FLOOR = _FRESH_THRESHOLD  # never flag a doc that is itself still fresh
_ANOMALY_SAMPLE = 5             # bound how many concrete outliers the LLM sees

# Deterministic severity cutoffs on the anomalous fraction of the collection.
_ANOMALY_SEV_HIGH_FRACTION = 0.25
_ANOMALY_SEV_MEDIUM_FRACTION = 0.10

_ANOMALY_SEVERITY_SYSTEM_PROMPT = (
    "You are a records-management analyst grading how urgently a document "
    "collection needs a freshness review. You are given the collection size, the "
    "documents whose staleness is a statistical outlier relative to their peers, "
    "and a deterministic baseline severity. Outliers that are far above the mean "
    "or that dominate a small collection are the most urgent. You may agree with "
    "or adjust the baseline, but justify any change. Respond ONLY with a JSON "
    'object: {"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<doc_id or title of the single most urgent outlier>"}. Never '
    "invent documents beyond those provided."
)


@dataclass
class FreshnessResult:
    doc_id: str
    title: str = ""
    collection_id: str = "default"
    state: str = "unknown"
    score: float = 0.0  # 0.0 = fresh, 1.0 = stale
    reason: str = ""
    source_event: str = ""
    tenant_id: str = "default"
    classification: str = "CUI"


@dataclass
class ScanResult:
    scan_id: str = ""
    collection_id: str = "default"
    stale_count: int = 0
    aging_count: int = 0
    fresh_count: int = 0
    regen_priority: float = 0.0
    docs: list[FreshnessResult] = field(default_factory=list)
    anomalies: dict = field(default_factory=dict)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso: str | None) -> float:
    if not iso:
        return 9999.0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return 9999.0


def _classify_state(score: float) -> str:
    """Map a freshness score to a state using the named band thresholds.

    Pure function: ``score < _FRESH_THRESHOLD`` → ``"fresh"``,
    ``< _STALE_THRESHOLD`` → ``"aging"``, otherwise ``"stale"``.
    """
    if score < _FRESH_THRESHOLD:
        return "fresh"
    if score < _STALE_THRESHOLD:
        return "aging"
    return "stale"


def _heuristic_anomaly_severity(anomaly_count: int, total: int) -> str:
    """Deterministic anomaly severity — the always-available baseline.

    Pure function of how large a fraction of the collection is anomalous. Used
    directly when the LLM grade is unavailable and handed to the model as the
    reference baseline otherwise.
    """
    if total <= 0 or anomaly_count <= 0:
        return "low"
    fraction = anomaly_count / total
    if fraction >= _ANOMALY_SEV_HIGH_FRACTION:
        return "high"
    if fraction >= _ANOMALY_SEV_MEDIUM_FRACTION:
        return "medium"
    return "low"


def _compute_freshness_anomalies(results: list["FreshnessResult"]) -> dict:
    """Pure statistical outlier pass — the authoritative, LLM-free heuristic.

    An outlier is a doc whose score exceeds ``mean + _ANOMALY_STDEV_K * stdev``
    AND clears ``_ANOMALY_ABS_FLOOR`` (so a still-fresh doc is never flagged).
    Below ``_ANOMALY_MIN_DOCS`` the distribution is too small to be meaningful and
    no anomalies are reported. No I/O, no LLM — safe to call anywhere.

    Returns the count summary plus the ranked outlier list and the deterministic
    ``severity`` baseline. :func:`detect_freshness_anomalies` layers the optional
    LLM grade on top of this.
    """
    scores = [r.score for r in results]
    total = len(scores)
    if total < _ANOMALY_MIN_DOCS:
        return {
            "anomaly_count": 0, "mean": 0.0, "stdev": 0.0, "threshold": 0.0,
            "anomalies": [], "severity": "low", "total": total,
        }

    mean = sum(scores) / total
    variance = sum((s - mean) ** 2 for s in scores) / total
    stdev = variance ** 0.5
    threshold = mean + _ANOMALY_STDEV_K * stdev

    anomalies: list[dict] = []
    for r in results:
        if r.score > threshold and r.score >= _ANOMALY_ABS_FLOOR:
            z = round((r.score - mean) / stdev, 3) if stdev > 0 else 0.0
            anomalies.append({
                "doc_id": r.doc_id,
                "title": r.title,
                "score": r.score,
                "z_score": z,
                "state": r.state,
            })
    anomalies.sort(key=lambda a: a["score"], reverse=True)

    return {
        "anomaly_count": len(anomalies),
        "mean": round(mean, 3),
        "stdev": round(stdev, 3),
        "threshold": round(threshold, 3),
        "anomalies": anomalies,
        "severity": _heuristic_anomaly_severity(len(anomalies), total),
        "total": total,
    }


def detect_freshness_anomalies(results: list["FreshnessResult"]) -> dict:
    """Flag documents whose freshness score is a statistical outlier for the corpus.

    Complements the absolute fresh/aging/stale bands: a doc can sit inside an
    acceptable band yet be markedly staler than its peers (or be the worst case in
    an already-degraded collection).

    The statistical detection (:func:`_compute_freshness_anomalies`) and the
    deterministic ``severity`` baseline are ALWAYS authoritative and never depend
    on the LLM. The returned ``ai_grade`` is best-effort severity enrichment and is
    ``None`` when the model is unavailable or there is nothing to grade.

    Returns:
        {
          "anomaly_count": int,
          "mean": float, "stdev": float, "threshold": float,
          "anomalies": [{"doc_id", "title", "score", "z_score", "state"}],
          "severity": "low|medium|high",   # authoritative deterministic baseline
          "ai_grade": {...} | None,        # optional LLM refinement
        }
    """
    out = _compute_freshness_anomalies(results)
    ai_grade = _ai_freshness_anomaly_severity(
        {"anomaly_count": out["anomaly_count"], "total": out["total"],
         "mean": out["mean"], "stdev": out["stdev"],
         "baseline_severity": out["severity"]},
        out["anomalies"],
    )
    out.pop("total", None)
    out["ai_grade"] = ai_grade
    return out


def _ai_freshness_anomaly_severity(summary: dict, anomalies: list[dict]) -> dict | None:
    """Grade collection freshness-anomaly severity with the LLM, grounded on real data.

    Args:
        summary: Distribution facts — anomaly_count, total, mean, stdev, and the
            deterministic ``baseline_severity``.
        anomalies: The concrete outlier docs. Only the leading ``_ANOMALY_SAMPLE``
            are sent to the model, keeping the call cheap and bounded regardless of
            collection size.

    Returns:
        ``{"severity": "low|medium|high", "rationale": str, "top_concern": str}``
        on success, or ``None`` when there are no anomalies to grade, the model is
        unavailable, or the output is missing/blank/malformed/out-of-range. Callers
        MUST treat ``None`` as "use the deterministic heuristic" — this is
        best-effort enrichment, never a hard dependency.
    """
    if not anomalies or summary.get("anomaly_count", 0) <= 0:
        return None
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        lines = [
            f"Collection facts: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {summary.get('baseline_severity')}",
            "Outlier documents:",
        ]
        for a in anomalies[:_ANOMALY_SAMPLE]:
            lines.append(f"- {_json.dumps(a, default=str)}")

        req = LLMRequest(
            messages=[
                {"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}
            ],
            system_prompt=_ANOMALY_SEVERITY_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_freshness_anomaly_severity", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
        # Tolerate fenced code blocks around the JSON object.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        parsed = _json.loads(raw[start : end + 1])
        severity = str(parsed.get("severity") or "").strip().lower()
        if severity not in {"low", "medium", "high"}:
            return None
        return {
            "severity": severity,
            "rationale": str(parsed.get("rationale") or "").strip()[:200],
            "top_concern": str(parsed.get("top_concern") or "").strip()[:80],
        }
    except Exception:
        return None


def _score_doc(
    doc_id: str,
    title: str,
    collection_id: str,
    created_at: str,
    latest_version_at: str | None,
    latest_approved_at: str | None,
    retention_days: int,
    drift_count_since_update: int,
    pending_section_count: int,
    tenant_id: str,
    classification: str,
) -> FreshnessResult:
    """Compute freshness score (0=fresh → 1=stale) and state."""
    age_days = _days_since(created_at)
    since_approved_days = _days_since(latest_approved_at) if latest_approved_at else age_days

    # Base age score: 0 at creation, 1 at retention_days.
    age_score = min(age_days / max(retention_days, 1), 1.0)

    # Approval lag score: 0 if approved recently, 1 if never approved or very old.
    approval_score = min(since_approved_days / max(retention_days, 1), 1.0)

    # Drift impact: each drift event since last update adds 0.15, capped at 0.6.
    drift_score = min(drift_count_since_update * 0.15, 0.6)

    # Pending sections: each pending adds 0.05, capped at 0.3.
    pending_score = min(pending_section_count * 0.05, 0.3)

    # Combined score (weighted).
    score = (
        age_score * 0.25
        + approval_score * 0.35
        + drift_score * 0.25
        + pending_score * 0.15
    )
    score = round(min(max(score, 0.0), 1.0), 3)

    state = _classify_state(score)

    reasons = []
    if age_score > 0.5:
        reasons.append(f"age {age_days:.0f}d")
    if approval_score > 0.5:
        reasons.append(f"unapproved {since_approved_days:.0f}d")
    if drift_score > 0:
        reasons.append(f"{drift_count_since_update} drift events")
    if pending_score > 0:
        reasons.append(f"{pending_section_count} pending sections")

    return FreshnessResult(
        doc_id=doc_id,
        title=title,
        collection_id=collection_id,
        state=state,
        score=score,
        reason="; ".join(reasons) if reasons else "within retention",
        source_event="freshness_scan",
        tenant_id=tenant_id,
        classification=classification,
    )


def scan_collection(
    collection_id: str,
    *,
    tenant_id: str = "default",
    classification: str = "CUI",
    retention_days: int | None = None,
) -> ScanResult:
    """Scan all documents in a collection and score freshness.

    Returns ScanResult with per-doc FreshnessResult list and aggregate counts.
    Persists results to dic_doc_freshness and dic_freshness_scans.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        # Load collection retention.
        if retention_days is None:
            try:
                cur.execute(
                    "SELECT retention_days FROM dic_collections WHERE collection_id = ? AND tenant_id = ?",
                    (collection_id, tenant_id),
                )
                row = cur.fetchone()
                if row:
                    retention_days = row[0] if hasattr(row, "__getitem__") else row["retention_days"]
            except Exception:
                pass
        retention_days = retention_days or _DEFAULT_RETENTION_DAYS

        # Load documents in collection.
        cur.execute(
            "SELECT doc_id, title, created_at, classification FROM dic_documents "
            "WHERE collection_id = ? AND tenant_id = ? ORDER BY created_at DESC",
            (collection_id, tenant_id),
        )
        docs = cur.fetchall()

        # Load latest version / approved timestamps per doc.
        version_info: dict[str, dict] = {}
        for d in docs:
            did = d[0] if hasattr(d, "__getitem__") else d["doc_id"]
            try:
                cur.execute(
                    "SELECT created_at, status FROM dic_versions WHERE doc_id = ? ORDER BY version_no DESC LIMIT 1",
                    (did,),
                )
                vrow = cur.fetchone()
                if vrow:
                    version_info[did] = {
                        "latest_at": vrow[0] if hasattr(vrow, "__getitem__") else vrow["created_at"],
                        "status": vrow[1] if hasattr(vrow, "__getitem__") else vrow["status"],
                    }
                else:
                    version_info[did] = {"latest_at": None, "status": "approved"}
            except Exception:
                version_info[did] = {"latest_at": None, "status": "approved"}

        # Load drift events since the latest version per doc (approximate: collection-wide).
        try:
            cur.execute(
                "SELECT COUNT(*) FROM dic_drift_events WHERE source = ? AND detected_at > "
                "(SELECT MAX(created_at) FROM dic_versions WHERE collection_id = ?)",
                (collection_id, collection_id),
            )
            drift_row = cur.fetchone()
            drift_count = drift_row[0] if drift_row else 0
        except Exception:
            drift_count = 0

        results: list[FreshnessResult] = []
        stale_count = 0
        aging_count = 0
        fresh_count = 0

        for d in docs:
            did = d[0] if hasattr(d, "__getitem__") else d["doc_id"]
            title = (d[1] if hasattr(d, "__getitem__") else d.get("title", "")) or ""
            created_at = d[2] if hasattr(d, "__getitem__") else d.get("created_at", "")
            doc_cls = d[3] if hasattr(d, "__getitem__") else d.get("classification", classification)
            vinfo = version_info.get(did, {})
            latest_at = vinfo.get("latest_at")
            approved_at = latest_at if vinfo.get("status") == "approved" else None

            # Pending sections count.
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM dic_sections WHERE doc_id = ? AND status IN ('pending_review','needs_revision','draft')",
                    (did,),
                )
                psc_row = cur.fetchone()
                pending_sections = psc_row[0] if psc_row else 0
            except Exception:
                pending_sections = 0

            fres = _score_doc(
                did, title, collection_id, created_at, latest_at, approved_at,
                retention_days, drift_count, pending_sections, tenant_id, doc_cls,
            )
            results.append(fres)
            if fres.state == "stale":
                stale_count += 1
            elif fres.state == "aging":
                aging_count += 1
            else:
                fresh_count += 1

            # Persist per-doc freshness.
            try:
                cur.execute(
                    "INSERT INTO dic_doc_freshness (doc_id, collection_id, state, reason, source_event, score, updated_at, tenant_id, classification) "
                    "VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (doc_id) DO UPDATE SET state=EXCLUDED.state, reason=EXCLUDED.reason, source_event=EXCLUDED.source_event, score=EXCLUDED.score, updated_at=EXCLUDED.updated_at",
                    (did, collection_id, fres.state, fres.reason, fres.source_event, fres.score, _now_utc(), tenant_id, doc_cls),
                )
            except Exception as exc:
                logger.warning("freshness: upsert dic_doc_freshness failed for %s: %s", did, exc)

        # Collection-level aggregate.
        total = len(results)
        regen_priority = round((stale_count * 1.0 + aging_count * 0.5) / max(total, 1), 3)
        scan_id = f"scan_{collection_id}_{_now_utc().replace(':', '-')}"
        try:
            cur.execute(
                "INSERT INTO dic_freshness_scans (scan_id, collection_id, stale_count, regen_priority, scanned_at, tenant_id) "
                "VALUES (?,?,?,?,?,?)",
                (scan_id, collection_id, stale_count, regen_priority, _now_utc(), tenant_id),
            )
        except Exception as exc:
            logger.warning("freshness: insert scan failed: %s", exc)

        conn.commit()

        # Collection-relative anomaly pass — flags outliers the absolute bands
        # miss. Deterministic; LLM severity grade is best-effort enrichment.
        anomalies = detect_freshness_anomalies(results)

        return ScanResult(
            scan_id=scan_id,
            collection_id=collection_id,
            stale_count=stale_count,
            aging_count=aging_count,
            fresh_count=fresh_count,
            regen_priority=regen_priority,
            docs=results,
            anomalies=anomalies,
        )
    finally:
        conn.close()


def corpus_heatmap(
    *,
    tenant_id: str = "default",
    limit: int = 200,
) -> list[dict]:
    """Return the latest freshness state for all documents across collections.

    Ordered by score descending (stale first). Used by the dashboard heatmap.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT f.doc_id, f.collection_id, f.state, f.reason, f.score, d.title "
            "FROM dic_doc_freshness f LEFT JOIN dic_documents d ON d.doc_id = f.doc_id "
            "WHERE f.tenant_id = ? ORDER BY f.score DESC LIMIT ?",
            (tenant_id, limit),
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        logger.warning("freshness: heatmap query failed: %s", exc)
        return []
    finally:
        conn.close()
