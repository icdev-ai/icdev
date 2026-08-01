# CUI // SP-CTI
"""DIC Canvas Adapter — maps canvas_events rows to affected DIC collections.

Primary entry point:
    resolve_affected_collections(event) -> list[dict]

Secondary:
    mark_event_processed(event_id, conn)  — idempotency guard for the reflex

Config:
    args/dic_canvas_integrations.yaml  — event_type → collection tags + metadata

Matching order (per event_type string):
    1. Exact match           — 'ndc.topology.drift_detected'
    2. Longest prefix match  — 'ndc.topology' then 'ndc'
    3. Fallback entry        — always present in the YAML

Tag intersection is Python-side; no SQL JSON functions used (PG portability rule).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.document_intelligence.canvas_adapter")

# ── Config cache ──────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "dic_canvas_integrations.yaml"
_cache: dict[str, Any] | None = None
_cache_mtime: float = 0.0


def _load_config() -> dict[str, Any]:
    """Load integrations config, reloading if the file changed on disk."""
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return {}
    if _cache is None or mtime > _cache_mtime:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        _cache = raw.get("integrations", {})
        _cache_mtime = mtime
    return _cache  # type: ignore[return-value]


# ── Event-type matcher ────────────────────────────────────────────────────────

def _match_config_entry(event_type: str, entries: dict[str, Any]) -> tuple[str, dict]:
    """Return (matched_key, entry_dict) using exact → longest-prefix → fallback."""
    if event_type in entries:
        return event_type, entries[event_type]

    # Longest prefix match: try progressively shorter dot-prefixes
    parts = event_type.split(".")
    for length in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:length])
        if prefix in entries:
            return prefix, entries[prefix]

    if "fallback" in entries:
        return "fallback", entries["fallback"]

    return "", {}


# ── Tags column lazy-init ─────────────────────────────────────────────────────

def _ensure_tags_column(conn) -> None:
    """Add tags TEXT column to dic_collections if it doesn't exist yet."""
    try:
        conn.execute("ALTER TABLE dic_collections ADD COLUMN tags TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # column already exists or table doesn't exist yet


# ── Processed-events table ────────────────────────────────────────────────────

def _ensure_processed_table(conn) -> None:
    """Create dic_processed_canvas_events if absent (lazy-init)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dic_processed_canvas_events (
            event_id        TEXT PRIMARY KEY,
            processed_at    TEXT NOT NULL,
            processor       TEXT NOT NULL DEFAULT 'dic_integration'
        )
        """
    )
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_affected_collections(event: dict) -> list[dict]:
    """Map a canvas_events row to the DIC collections that should be reviewed.

    Args:
        event: dict with at least 'event_type' key; optional 'source_canvas',
               'payload_json', 'created_at', 'tenant_id'.

    Returns:
        List of dicts, each containing:
            collection_id, collection_name, matched_tags, doc_type, priority,
            rationale, patch_mode, config_key
        Empty list if no matching collections are found (never raises).
    """
    try:
        return _resolve(event)
    except Exception:
        return []


def _resolve(event: dict) -> list[dict]:
    entries = _load_config()
    if not entries:
        return []

    event_type = (event.get("event_type") or "").strip()
    if not event_type:
        return []

    config_key, cfg = _match_config_entry(event_type, entries)
    if not cfg:
        return []

    target_tags: set[str] = set(cfg.get("target_collection_tags", []))
    doc_types: list[str] = cfg.get("doc_types", [])
    priority: str = cfg.get("priority", "low")
    rationale: str = cfg.get("rationale", "")
    patch_mode: bool = bool(cfg.get("patch_mode", False))
    target_all = "all" in target_tags

    tenant_id = event.get("tenant_id") or ""

    with get_connection() as conn:
        _ensure_tags_column(conn)

        if tenant_id:
            rows = conn.execute(
                "SELECT collection_id, name, tags FROM dic_collections WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT collection_id, name, tags FROM dic_collections",
            ).fetchall()

    results: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        if isinstance(row, (list, tuple)):
            cid, cname, ctags_raw = row[0], row[1], row[2]
        else:
            cid = row["collection_id"]
            cname = row["name"]
            ctags_raw = row["tags"]

        if cid in seen:
            continue

        col_tags: set[str] = {
            t.strip().lower()
            for t in (ctags_raw or "").split(",")
            if t.strip()
        }

        if target_all or col_tags & {t.lower() for t in target_tags}:
            matched = (
                list(col_tags) if target_all
                else sorted(col_tags & {t.lower() for t in target_tags})
            )
            results.append({
                "collection_id": cid,
                "collection_name": cname or "",
                "matched_tags": matched,
                "doc_type": doc_types[0] if doc_types else "general",
                "doc_types": doc_types,
                "priority": priority,
                "rationale": rationale,
                "patch_mode": patch_mode,
                "config_key": config_key,
            })
            seen.add(cid)

    return results


def is_event_processed(event_id: str) -> bool:
    """Return True if this event_id was already processed by the DIC integration reflex."""
    try:
        with get_connection() as conn:
            _ensure_processed_table(conn)
            row = conn.execute(
                "SELECT event_id FROM dic_processed_canvas_events WHERE event_id = %s",
                (event_id,),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def mark_event_processed(event_id: str, conn=None) -> None:
    """Record event_id as processed (idempotent — silently no-ops on duplicate)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    def _insert(c) -> None:
        _ensure_processed_table(c)
        try:
            c.execute(
                """
                INSERT INTO dic_processed_canvas_events (event_id, processed_at)
                VALUES (%s, %s)
                """,
                (event_id, now),
            )
            c.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # duplicate key — already processed
            logger.warning(
                "_insert: best-effort INSERT into dic_processed_canvas_events failed (non-blocking): %s",
                exc,
            )

    if conn is not None:
        _insert(conn)
    else:
        with get_connection() as c:
            _insert(c)
