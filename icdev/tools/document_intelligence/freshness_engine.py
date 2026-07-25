# CUI // SP-CTI
"""DIC Freshness Engine — staleness scoring + autonomous reflex trigger.

Scoring dimensions (weights config-driven — args/dic_freshness_config.yaml):
  • Document age vs retention tier (default 90 days)
  • Time since last approved version
  • Number of drift events affecting the collection since last update
  • Section count pending review (more pending = less fresh)
  • KG blast radius — how many recently-changed entities the doc cites
    (5th dimension, DEFAULT WEIGHT 0 so existing scores are unchanged until
    an operator tunes it; extends consistency_checker concept-overlap)

Outputs:
  • per-doc dic_doc_freshness row (state, reason, source_event)
  • per-collection dic_freshness_scans row (stale_count, regen_priority)
  • ranked remediation list for the dashboard heatmap
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_FRESHNESS_STATES = ["fresh", "aging", "stale", "unknown"]
_DEFAULT_RETENTION_DAYS = 90

# Built-in scoring weights — the fallback when the config file is absent/malformed.
# These MATCH the historical hard-coded weights so scores are unchanged, and
# blast_radius defaults to 0.0 (5th dimension is inert until an operator tunes it).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "age": 0.25,
    "approval": 0.35,
    "drift": 0.25,
    "pending": 0.15,
    "blast_radius": 0.0,
}
_DEFAULT_BLAST_MIN_OVERLAP = 3
_DEFAULT_BLAST_SCORE_PER_ENTITY = 0.2

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "dic_freshness_config.yaml"
_config_cache: dict | None = None


def _load_config() -> dict:
    """Load freshness scoring config (weights + blast-radius params).

    Cached. Fail-soft: any read/parse error yields the built-in defaults so a
    bad config file can never change or crash scoring.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    cfg: dict = {}
    try:
        import yaml

        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("freshness: config load failed, using defaults: %s", exc)
        cfg = {}
    _config_cache = cfg
    return cfg


def get_weights() -> dict[str, float]:
    """Effective scoring weights: defaults overlaid with config `weights:`.

    Unknown keys in config are ignored; missing keys keep their default. The
    blast_radius weight stays 0.0 unless explicitly set, guaranteeing existing
    scores are unchanged out of the box.
    """
    weights = dict(_DEFAULT_WEIGHTS)
    raw = (_load_config().get("weights") or {})
    for key in weights:
        if key in raw:
            try:
                weights[key] = float(raw[key])
            except (TypeError, ValueError):
                pass
    return weights


def _blast_params() -> tuple[int, float, bool]:
    """(min_overlap, score_per_entity, emit_drift) from config, with defaults."""
    br = (_load_config().get("blast_radius") or {})
    try:
        min_overlap = int(br.get("min_overlap", _DEFAULT_BLAST_MIN_OVERLAP))
    except (TypeError, ValueError):
        min_overlap = _DEFAULT_BLAST_MIN_OVERLAP
    try:
        per_entity = float(br.get("score_per_entity", _DEFAULT_BLAST_SCORE_PER_ENTITY))
    except (TypeError, ValueError):
        per_entity = _DEFAULT_BLAST_SCORE_PER_ENTITY
    emit_drift = bool(br.get("emit_drift", True))
    return max(min_overlap, 1), per_entity, emit_drift


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
    *,
    blast_count: int = 0,
    weights: dict[str, float] | None = None,
    blast_score_per_entity: float = _DEFAULT_BLAST_SCORE_PER_ENTITY,
) -> FreshnessResult:
    """Compute freshness score (0=fresh → 1=stale) and state.

    ``blast_count`` is the number of recently-changed KG entities this document
    cites (the 5th, blast-radius dimension). Its weight defaults to 0.0, so when
    callers omit ``blast_count``/``weights`` the score is IDENTICAL to the
    historical 4-dimension formula.
    """
    w = weights or _DEFAULT_WEIGHTS
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

    # Blast radius: each recently-changed cited entity adds score_per_entity,
    # capped at 1.0. Inert when weights["blast_radius"] == 0 (default).
    blast_score = min(max(blast_count, 0) * blast_score_per_entity, 1.0)

    # Combined score (weighted).
    score = (
        age_score * w.get("age", 0.25)
        + approval_score * w.get("approval", 0.35)
        + drift_score * w.get("drift", 0.25)
        + pending_score * w.get("pending", 0.15)
        + blast_score * w.get("blast_radius", 0.0)
    )
    score = round(min(max(score, 0.0), 1.0), 3)

    if score < 0.35:
        state = "fresh"
    elif score < 0.7:
        state = "aging"
    else:
        state = "stale"

    reasons = []
    if age_score > 0.5:
        reasons.append(f"age {age_days:.0f}d")
    if approval_score > 0.5:
        reasons.append(f"unapproved {since_approved_days:.0f}d")
    if drift_score > 0:
        reasons.append(f"{drift_count_since_update} drift events")
    if pending_score > 0:
        reasons.append(f"{pending_section_count} pending sections")
    if blast_score > 0 and w.get("blast_radius", 0.0) > 0:
        reasons.append(f"blast radius {blast_count} changed entities")

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


def _recent_changed_entities(cur, tenant_id: str, limit: int = 200) -> list[str]:
    """Best-effort: distinct entities from recent drift events for the tenant.

    Drift events are the persisted trace of "something changed" (docmod evidence
    updates flow here via the docmod drift_bridge -> acoic). Used as the default
    changed-entity source when a caller does not pass ``changed_entities``.
    Fail-soft: returns [] on any error.
    """
    try:
        cur.execute(
            "SELECT DISTINCT entity FROM dic_drift_events "
            "WHERE tenant_id = %s AND entity IS NOT NULL "
            "ORDER BY detected_at DESC LIMIT %s",
            (tenant_id, limit),
        )
        return [
            (r[0] if hasattr(r, "__getitem__") else r["entity"])
            for r in cur.fetchall()
            if (r[0] if hasattr(r, "__getitem__") else r["entity"])
        ]
    except Exception:
        return []


def _route_blast_drift(flagged: list[dict], tenant_id: str, classification: str) -> None:
    """Route blast-radius findings through the SAME drift sink the docmod
    drift_bridge uses (acoic.handle_drift), so semantic blast-radius findings
    reach the existing HITL regen/compliance response instead of a new pipeline.

    Idempotent per (doc, entity-set) via a stable dedup_key. Fail-soft — a
    routing error must never fail the freshness scan.
    """
    try:
        from tools.document_intelligence import acoic
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("freshness: acoic unavailable for blast drift routing: %s", exc)
        return
    for f in flagged:
        doc_id = f.get("doc_id")
        if not doc_id:
            continue
        matched = f.get("matched_entities") or []
        dedup = f"kg_blast:{doc_id}:{','.join(sorted(matched))}"
        try:
            acoic.handle_drift({
                "source": "kg_blast_radius",
                "entity": ", ".join(matched[:5]),
                "severity": "medium",
                "document_id": doc_id,
                "dedup_key": dedup,
                "tenant_id": tenant_id,
                "classification": classification,
                "rationale": (
                    f"cites {f.get('overlap_count', 0)} recently-changed KG "
                    f"entities (blast radius)"
                ),
            })
        except Exception as exc:
            logger.warning("freshness: blast drift routing failed for %s: %s", doc_id, exc)


def scan_collection(
    collection_id: str,
    *,
    tenant_id: str = "default",
    classification: str = "CUI",
    retention_days: int | None = None,
    changed_entities: list[str] | None = None,
) -> ScanResult:
    """Scan all documents in a collection and score freshness.

    Returns ScanResult with per-doc FreshnessResult list and aggregate counts.
    Persists results to dic_doc_freshness and dic_freshness_scans.

    The 5th (blast-radius) dimension only does work when its config weight is
    > 0 (default 0). ``changed_entities`` lets a caller pass the exact changed
    entity set (e.g. kg_temporal_diff output); when omitted the engine derives
    it from recent drift events. When the weight is 0 no blast query runs and
    scores are unchanged.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        # Load collection retention.
        if retention_days is None:
            try:
                cur.execute(
                    "SELECT retention_days FROM dic_collections WHERE collection_id = %s AND tenant_id = %s",
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
            "WHERE collection_id = %s AND tenant_id = %s ORDER BY created_at DESC",
            (collection_id, tenant_id),
        )
        docs = cur.fetchall()

        # Load latest version / approved timestamps per doc.
        version_info: dict[str, dict] = {}
        for d in docs:
            did = d[0] if hasattr(d, "__getitem__") else d["doc_id"]
            try:
                cur.execute(
                    "SELECT created_at, status FROM dic_versions WHERE doc_id = %s ORDER BY version_no DESC LIMIT 1",
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
                "SELECT COUNT(*) FROM dic_drift_events WHERE source = %s AND detected_at > "
                "(SELECT MAX(created_at) FROM dic_versions WHERE collection_id = %s)",
                (collection_id, collection_id),
            )
            drift_row = cur.fetchone()
            drift_count = drift_row[0] if drift_row else 0
        except Exception:
            drift_count = 0

        # ── Blast radius (5th dimension) — only when its weight is tuned up. ──
        weights = get_weights()
        blast_counts: dict[str, int] = {}
        blast_flagged: list[dict] = []
        blast_per_entity = _DEFAULT_BLAST_SCORE_PER_ENTITY
        if weights.get("blast_radius", 0.0) > 0:
            min_overlap, blast_per_entity, emit_drift = _blast_params()
            entities = changed_entities
            if entities is None:
                entities = _recent_changed_entities(cur, tenant_id)
            if entities:
                try:
                    from tools.document_intelligence.consistency_checker import (
                        find_docs_citing_changed_entities,
                    )

                    blast_flagged = find_docs_citing_changed_entities(
                        entities, min_overlap=min_overlap, tenant_id=tenant_id,
                    )
                    blast_counts = {
                        f["doc_id"]: int(f.get("overlap_count", 0))
                        for f in blast_flagged
                    }
                except Exception as exc:
                    logger.warning("freshness: blast-radius compute failed: %s", exc)
            if blast_flagged and emit_drift:
                _route_blast_drift(blast_flagged, tenant_id, classification)

        # Snapshot prior freshness states + last-notified times BEFORE the
        # upsert loop overwrites them, so the notifier can detect crossings.
        # Degrades to an empty snapshot on any error (e.g. a pre-migration DB
        # without last_notified_at) — no crossings, no crash.
        prior_states: dict[str, dict] = {}
        try:
            cur.execute(
                "SELECT doc_id, state, last_notified_at FROM dic_doc_freshness "
                "WHERE collection_id = %s AND tenant_id = %s",
                (collection_id, tenant_id),
            )
            for pr in cur.fetchall():
                pid = pr[0] if hasattr(pr, "__getitem__") else pr["doc_id"]
                prior_states[pid] = {
                    "state": pr[1] if hasattr(pr, "__getitem__") else pr["state"],
                    "last_notified_at": pr[2] if hasattr(pr, "__getitem__") else pr["last_notified_at"],
                }
        except Exception:
            prior_states = {}

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
                    "SELECT COUNT(*) FROM dic_sections WHERE doc_id = %s AND status IN ('pending_review','needs_revision','draft')",
                    (did,),
                )
                psc_row = cur.fetchone()
                pending_sections = psc_row[0] if psc_row else 0
            except Exception:
                pending_sections = 0

            fres = _score_doc(
                did, title, collection_id, created_at, latest_at, approved_at,
                retention_days, drift_count, pending_sections, tenant_id, doc_cls,
                blast_count=blast_counts.get(did, 0),
                weights=weights,
                blast_score_per_entity=blast_per_entity,
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
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
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
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (scan_id, collection_id, stale_count, regen_priority, _now_utc(), tenant_id),
            )
        except Exception as exc:
            logger.warning("freshness: insert scan failed: %s", exc)

        # Proactive owner notifications for state crossings (dmx-loop-01).
        # Config-gated (default OFF) and fully isolated: any failure here must
        # never cost us the scan/persistence. The notifier updates
        # last_notified_at on `conn`; the commit below persists it alongside the
        # freshness upserts. Notify-only — no document edits.
        try:
            from tools.document_intelligence.freshness_notifier import (
                notify_freshness_crossings,
            )

            notify_freshness_crossings(
                results, prior_states, conn=conn, tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.warning("freshness: notification hook failed: %s", exc)

        conn.commit()

        return ScanResult(
            scan_id=scan_id,
            collection_id=collection_id,
            stale_count=stale_count,
            aging_count=aging_count,
            fresh_count=fresh_count,
            regen_priority=regen_priority,
            docs=results,
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
            "WHERE f.tenant_id = %s ORDER BY f.score DESC LIMIT %s",
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
