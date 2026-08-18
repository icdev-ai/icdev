# CUI // SP-CTI
"""De facto standard learner — deployment-frequency statistics.

Learns what is *actually fielded* from the inventory feeds declared in
``args/docmod/inventory_feeds.yaml``: the most frequently and most recently
seen vendor/model per category is the de facto current standard. Curated
catalog entries remain AUTHORITATIVE — learner output is corroboration,
tie-breaker, and the source of defacto_divergence / catalog_gap findings via
cross_check().

WHY FEEDS AND NOT ONE TABLE (cef-fnd-04)

This module used to read ni_devices and nothing else, and ni_devices holds 0
rows in deployments with no reachable NetBox or CSV export — so
docmod_defacto_standards was empty despite the writer running nightly for
months. That is the "empty substrate" failure: the writer worked and had
nothing to learn from. Feeds make the input a declaration rather than a
hardcoded table, so an alternative source can supply evidence where no
deployment inventory exists.

EVIDENCE CLASSES ARE NEVER BLENDED

Each feed declares an ``evidence_kind`` and a ``precedence``. share_pct is
computed WITHIN a feed, ``get_recommended`` answers from the best-precedence
feed that has data for the category, and every row records which feed it came
from. An observed estate beats a drawing of one; no quantity of drawings
becomes an observation.

Pure-Python aggregation (no dialect SQL — feed JSON is parsed in Python, never
by json_extract). Results persist to docmod_defacto_standards (mutable,
recomputed per sweep).
"""
from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

DOMAIN = "network_hardware"

_REPO_ROOT = Path(__file__).resolve().parents[2]
FEEDS_PATH = _REPO_ROOT / "args" / "docmod" / "inventory_feeds.yaml"

#: A table name interpolated into SQL must look like an identifier. The names
#: come from a repo-owned config file, but the check costs nothing.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Canonical fields a feed maps onto — the columns of docmod_defacto_standards
#: plus the timestamp recency weighting needs. Not domain vocabulary.
_FIELDS = ("category", "vendor", "product", "version", "observed_at")

#: Used when a feed declares no evidence_kind. Never guessed upward to
#: 'inventory': unlabelled evidence must not inherit the strongest label.
_DEFAULT_EVIDENCE_KIND = "unspecified"

_feeds_cache: dict = {"feeds": None, "mtime": None}


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


# ── feeds ─────────────────────────────────────────────────────────────────────

def load_feeds(force: bool = False) -> list[dict]:
    """Enabled inventory feeds, in precedence order, mtime hot-reloaded."""
    import yaml

    if not FEEDS_PATH.exists():
        logger.warning("docmod learner: %s missing — no feeds declared", FEEDS_PATH)
        return []
    mtime = FEEDS_PATH.stat().st_mtime
    if not force and _feeds_cache["feeds"] is not None and _feeds_cache["mtime"] == mtime:
        return _feeds_cache["feeds"]
    raw = yaml.safe_load(FEEDS_PATH.read_text(encoding="utf-8")) or {}
    feeds = [
        f for f in (raw.get("feeds") or [])
        if isinstance(f, dict) and f.get("id") and f.get("enabled", True)
    ]
    feeds.sort(key=lambda f: (int(f.get("precedence") or 100), str(f["id"])))
    _feeds_cache["feeds"], _feeds_cache["mtime"] = feeds, mtime
    return feeds


def _as_json(value):
    """Parse a JSON column that may arrive as text, as double-encoded text, or
    already decoded by the driver. Returns {} for anything unusable — a feed
    whose payload cannot be read contributes nothing rather than raising."""
    for _ in range(3):
        if isinstance(value, (dict, list)):
            return value
        if value in (None, ""):
            return {}
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, (dict, list)) else {}


def _pick(source: dict, candidates) -> str:
    """First non-empty candidate key, `a.b` descending one dict level."""
    for key in candidates or []:
        cur = source
        for part in str(key).split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        text = str(cur).strip() if cur is not None else ""
        if text:
            return text
    return ""


def _read_feed(feed: dict, conn) -> list[dict]:
    """Normalize one feed into records carrying the canonical fields."""
    table = feed.get("table")
    if not table or not _IDENT_RE.match(str(table)):
        logger.warning("docmod learner: feed %s has no usable table", feed.get("id"))
        return []
    fields = feed.get("fields") or {}
    kind = str(feed.get("kind") or "table")
    try:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table}"  # nosec B608 - identifier validated above
        ).fetchall()]
    except Exception as exc:
        logger.warning("docmod learner: feed %s unreadable (%s)", feed.get("id"), exc)
        try:
            conn.rollback()  # PG: a failed statement poisons the transaction
        except Exception:
            pass
        return []

    records: list[dict] = []
    if kind == "json_nodes":
        json_column = feed.get("json_column")
        node_path = str(feed.get("node_path") or "nodes")
        for row in rows:
            payload = _as_json(row.get(json_column))
            nodes = payload.get(node_path) if isinstance(payload, dict) else payload
            row_ts = _pick(row, feed.get("row_observed_at") or [])
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                rec = {f: _pick(node, fields.get(f)) for f in _FIELDS}
                rec["observed_at"] = rec["observed_at"] or row_ts
                records.append(rec)
    else:
        for row in rows:
            records.append({f: _pick(row, fields.get(f)) for f in _FIELDS})

    # A record naming no product identifies nothing and cannot be a standard.
    return [r for r in records if r["product"]]


def recompute(conn=None) -> dict:
    """Recompute docmod_defacto_standards from every declared inventory feed.

    Recency weight = 0.5 ** (age_days / half_life); records without a usable
    timestamp weigh 1.0 (unweighted fallback). share_pct is a share of the
    category WITHIN one feed — never pooled across feeds of different evidence
    classes."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        feeds = load_feeds()
        half_life = _half_life_days()
        now = _now_dt()

        buckets: dict[tuple, dict] = {}
        feed_report: dict[str, dict] = {}
        total_records = 0
        for feed in feeds:
            feed_id = str(feed["id"])
            domain = str(feed.get("domain") or DOMAIN)
            evidence_kind = str(feed.get("evidence_kind") or _DEFAULT_EVIDENCE_KIND)
            records = _read_feed(feed, conn)
            total_records += len(records)
            feed_report[feed_id] = {
                "records": len(records), "domain": domain,
                "evidence_kind": evidence_kind, "table": feed.get("table"),
            }
            for r in records:
                key = (
                    domain, feed_id, evidence_kind,
                    (r["category"] or "unknown").lower(),
                    r["vendor"], r["product"], r["version"],
                )
                b = buckets.setdefault(key, {"count": 0, "weight": 0.0})
                b["count"] += 1
                ts = _parse_dt(r["observed_at"])
                if ts is not None:
                    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
                    b["weight"] += math.pow(0.5, age_days / half_life)
                else:
                    b["weight"] += 1.0

        # Share within (domain, feed, category) — like evidence only.
        totals: dict[tuple, float] = {}
        for (domain, feed_id, _kind, category, *_), b in buckets.items():
            k = (domain, feed_id, category)
            totals[k] = totals.get(k, 0.0) + b["weight"]

        computed_at = now.isoformat()
        # Recomputed in full per sweep. Delete every domain a feed declares, so
        # a feed turned off stops contributing instead of leaving stale rows.
        for domain in sorted({str(f.get("domain") or DOMAIN) for f in feeds} | {DOMAIN}):
            conn.execute(
                "DELETE FROM docmod_defacto_standards WHERE domain = %s", (domain,)
            )
        written = 0
        for (domain, feed_id, evidence_kind, category, vendor, product, version), b in buckets.items():
            total = totals.get((domain, feed_id, category)) or 1.0
            conn.execute(
                """INSERT INTO docmod_defacto_standards
                   (id, domain, category, vendor, product, version,
                    deploy_count, weighted_score, share_pct, computed_at,
                    source_feed, evidence_kind)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f"dfs-{uuid.uuid4().hex[:10]}", domain, category, vendor, product,
                    version, b["count"], round(b["weight"], 6),
                    round(100.0 * b["weight"] / total, 2), computed_at,
                    feed_id, evidence_kind,
                ),
            )
            written += 1
        conn.commit()
        return {
            "domain": DOMAIN,
            "entries": written,
            # Total records read across every feed. Kept under the original key
            # so existing callers and the sweep result keep working.
            "devices": total_records,
            "feeds": feed_report,
        }
    finally:
        if own:
            conn.close()


def _feed_precedence() -> dict[str, int]:
    return {str(f["id"]): int(f.get("precedence") or 100) for f in load_feeds()}


def get_recommended(category: str, conn=None) -> dict | None:
    """Top weighted-share entry for a category — the learned de facto standard.

    Answered from the BEST-precedence feed that has rows for this category, and
    from that feed alone. Two feeds are never blended: an observed deployed
    estate and a modelled design are different claims, and a top entry pooled
    across them would belong to neither.
    """
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
    precedence = _feed_precedence()
    # 100 is the same default load_feeds() applies to an undeclared precedence,
    # so a row written by a feed since removed from the YAML ranks last rather
    # than crashing the lookup.
    best = min(precedence.get(str(r.get("source_feed") or ""), 100) for r in rows)
    scoped = [r for r in rows if precedence.get(str(r.get("source_feed") or ""), 100) == best]
    return max(scoped, key=lambda r: (r.get("weighted_score") or 0, r.get("deploy_count") or 0))


#: Rows scanned per free-text search before the read stops. See the same
#: constant's note in tools/currency/entity_currency.py: a bounded read that
#: does not say it was bounded reads as full coverage.
SEARCH_ROW_CAP = 200


def search(text: str, limit: int = 10, conn=None) -> list[dict]:
    """Free-text lookup over the learned de-facto rows — corroboration evidence.

    Returns the matching ``docmod_defacto_standards`` rows, each carrying its
    feed's ``precedence`` (best = lowest) and ``match`` (the fraction of query
    terms the row's own text contains), ordered best-evidence first. The rows
    are NOT blended across feeds and NOT merged with catalog evidence: the
    caller ranks them below curated output, which is the whole authority rule
    this module's docstring states.

    UNLIKE ``get_recommended``, a missing/dead table RAISES here rather than
    returning nothing — the Cortex ``currency`` backend distinguishes "the
    learner table is gone" from "the learner has learned nothing", and it can
    only do that if the failure reaches it.
    """
    # One copy of the tokenizing/matching rule, in the store that owns it.
    from tools.currency.entity_currency import match_score, search_terms

    terms = search_terms(text)
    if not terms:
        return []
    own = conn is None
    if own:
        conn = _connect()
    try:
        clauses, params = [], []
        for term in terms:
            like = f"%{term}%"
            clauses.append(
                "(LOWER(product) LIKE %s OR LOWER(vendor) LIKE %s "
                "OR LOWER(category) LIKE %s)"
            )
            params.extend([like, like, like])
        sql = (
            "SELECT * FROM docmod_defacto_standards "  # nosec B608 - constant identifier; every value bound
            f"WHERE ({' OR '.join(clauses)}) "
            "ORDER BY domain, category, vendor, product, version LIMIT %s"
        )
        params.append(SEARCH_ROW_CAP)
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        if own:
            conn.close()

    precedence = _feed_precedence()
    out = []
    for row in rows:
        haystack = " ".join(str(row.get(c) or "").lower() for c in
                            ("domain", "category", "vendor", "product", "version"))
        row["match"] = match_score(terms, haystack)
        row["precedence"] = precedence.get(str(row.get("source_feed") or ""), 100)
        out.append(row)
    out.sort(key=lambda r: (
        int(r.get("precedence") or 100),
        -float(r.get("match") or 0.0),
        -float(r.get("weighted_score") or 0.0),
    ))
    return out[:max(1, int(limit or 1))]


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
    precedence = _feed_precedence()
    by_category: dict[str, list[dict]] = {}
    for r in rows:
        by_category.setdefault(r["category"], []).append(r)

    for category, entries in by_category.items():
        # Same rule as get_recommended: assert against the best evidence class
        # available for this category, never against a blend of two.
        best = min(precedence.get(str(e.get("source_feed") or ""), 100) for e in entries)
        entries = [e for e in entries
                   if precedence.get(str(e.get("source_feed") or ""), 100) == best]
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
