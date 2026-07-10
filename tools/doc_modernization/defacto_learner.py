# CUI // SP-CTI
"""De facto standard learner — deployment-frequency statistics.

Learns what is *actually deployed* from the ni_devices inventory (populated by
tools/network/network_ingester.py, NetBox/CSV/config imports): the most
frequently and most recently deployed vendor/model per device_type is the de
facto current standard. Curated catalog entries remain AUTHORITATIVE — learner
output is corroboration, tie-breaker, and the source of defacto_divergence /
catalog_gap findings via cross_check().

Pure-Python aggregation (no dialect SQL). Results persist to
docmod_defacto_standards (mutable, recomputed per sweep).
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

DOMAIN = "network_hardware"


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s[:26] if "+" not in s else s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _half_life_days() -> float:
    from .pack_loader import load_config
    return float(load_config().get("recency_half_life_days", 180) or 180)


def recompute(conn=None) -> dict:
    """Recompute docmod_defacto_standards for the network_hardware domain.

    Recency weight = 0.5 ** (age_days / half_life); rows without a usable
    timestamp weigh 1.0 (unweighted fallback)."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT device_type, vendor, model, firmware_version, updated_at, created_at "
                "FROM ni_devices WHERE model IS NOT NULL AND model != ''"
            ).fetchall()]
        except Exception as exc:
            logger.warning("docmod learner: ni_devices unavailable: %s", exc)
            rows = []

        half_life = _half_life_days()
        now = _now_dt()
        buckets: dict[tuple, dict] = {}
        for r in rows:
            key = (
                (r.get("device_type") or "unknown").lower(),
                (r.get("vendor") or "").strip(),
                (r.get("model") or "").strip(),
                (r.get("firmware_version") or "").strip(),
            )
            b = buckets.setdefault(key, {"count": 0, "weight": 0.0})
            b["count"] += 1
            ts = _parse_dt(r.get("updated_at")) or _parse_dt(r.get("created_at"))
            if ts is not None:
                age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
                b["weight"] += math.pow(0.5, age_days / half_life)
            else:
                b["weight"] += 1.0

        # share within each category (device_type) by weighted score
        category_totals: dict[str, float] = {}
        for (category, *_), b in buckets.items():
            category_totals[category] = category_totals.get(category, 0.0) + b["weight"]

        computed_at = now.isoformat()
        conn.execute(
            "DELETE FROM docmod_defacto_standards WHERE domain = %s", (DOMAIN,)
        )
        written = 0
        for (category, vendor, product, version), b in buckets.items():
            total = category_totals.get(category) or 1.0
            conn.execute(
                """INSERT INTO docmod_defacto_standards
                   (id, domain, category, vendor, product, version,
                    deploy_count, weighted_score, share_pct, computed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"dfs-{uuid.uuid4().hex[:10]}", DOMAIN, category, vendor, product,
                    version, b["count"], round(b["weight"], 6),
                    round(100.0 * b["weight"] / total, 2), computed_at,
                ),
            )
            written += 1
        conn.commit()
        return {"domain": DOMAIN, "entries": written, "devices": len(rows)}
    finally:
        if own:
            conn.close()


def get_recommended(category: str, conn=None) -> dict | None:
    """Top weighted-share entry for a device_type — the learned de facto standard."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM docmod_defacto_standards WHERE domain = %s AND category = %s",
            (DOMAIN, (category or "unknown").lower()),
        ).fetchall()]
    finally:
        if own:
            conn.close()
    if not rows:
        return None
    return max(rows, key=lambda r: (r.get("weighted_score") or 0, r.get("deploy_count") or 0))


def cross_check(catalog_provider, conn=None, min_share_pct: float = 25.0) -> dict:
    """Compare learned deployment reality against the curated catalog.

    Returns {'divergence': [...], 'gaps': [...]} — records for packs to turn
    into defacto_divergence / catalog_gap findings, and for the
    propose-from-defacto card flow."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM docmod_defacto_standards WHERE domain = %s", (DOMAIN,)
        ).fetchall()]
    finally:
        if own:
            conn.close()

    divergence, gaps = [], []
    by_category: dict[str, list[dict]] = {}
    for r in rows:
        by_category.setdefault(r["category"], []).append(r)

    for category, entries in by_category.items():
        top = max(entries, key=lambda r: r.get("weighted_score") or 0)
        if (top.get("share_pct") or 0) < min_share_pct:
            continue  # no dominant standard learned — nothing to assert
        approved = catalog_provider.get_approved(DOMAIN, category)
        if not approved:
            gaps.append({**top, "reason": "no approved catalog entry for category"})
            continue
        match = catalog_provider.lookup(DOMAIN, top["product"], vendor=top.get("vendor"))
        if match is None:
            gaps.append({**top, "reason": "top-deployed model missing from catalog"})
        elif match.status != "approved":
            divergence.append({
                **top,
                "catalog_entry_id": match.entry_id,
                "catalog_status": match.status,
                "reason": f"top-deployed model is '{match.status}' in the catalog",
            })
    return {"divergence": divergence, "gaps": gaps}
