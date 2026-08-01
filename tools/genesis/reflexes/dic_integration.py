# CUI // SP-CTI
"""Genesis Reflex — DIC Integration (15-min cadence).

Polls canvas_events for unprocessed events targeted at DIC or matching
configured integration event-types, then queues AI-drafted (or stub) document
update suggestions in dic_suggestions for HITL review.

Flow per event:
  canvas_events row
    → canvas_adapter.resolve_affected_collections()  (YAML-driven)
    → per affected collection: pick representative section(s)
    → draft suggestion via LLMRouter (patch_mode) or stub if unavailable
    → suggestion_store.create_suggestion()
    → best-effort notification_log emit
    → mark_event_processed(event_id)

Idempotency: dic_processed_canvas_events guards against double-processing.
Each event exception is caught individually — one bad event never blocks others.
Must complete within 60s; LLM call is skipped when unavailable (air-gap safe).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

IMPLEMENTATION_STATUS = "full"
CADENCE_MINUTES = 15
_MAX_EVENTS_PER_RUN = 50   # safety cap — avoids runaway on first run with backlog
_MAX_SECTIONS_PER_COLLECTION = 3   # sections to suggest per collection per event


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Fetch unprocessed canvas events ──────────────────────────────────────────

def _fetch_unprocessed_events(conn) -> list[dict]:
    """Return canvas_events rows not yet processed by this reflex."""
    try:
        # Ensure processed table exists
        from tools.document_intelligence.canvas_adapter import _ensure_processed_table
        _ensure_processed_table(conn)
    except Exception:
        pass

    try:
        rows = conn.execute(
            """
            SELECT id, source_canvas, target_canvas, event_type, payload_json, created_at
            FROM   canvas_events
            WHERE  target_canvas = %s
              AND  id NOT IN (
                       SELECT event_id FROM dic_processed_canvas_events
                   )
            ORDER  BY created_at
            LIMIT  %s
            """,
            ("dic", _MAX_EVENTS_PER_RUN),
        ).fetchall()
    except Exception as exc:
        logger.warning("dic_integration: fetch events error: %s", exc)
        return []

    result = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            result.append({
                "id": row[0], "source_canvas": row[1],
                "target_canvas": row[2], "event_type": row[3],
                "payload_json": row[4], "created_at": row[5],
            })
        else:
            result.append(dict(row))
    return result


# ── Fetch representative sections for a collection ────────────────────────────

def _get_sections_for_collection(conn, collection_id: str) -> list[dict]:
    """Return up to N sections from the collection, most recently updated first."""
    try:
        rows = conn.execute(
            """
            SELECT s.section_id, s.doc_id, s.heading, s.content
            FROM   dic_sections s
            JOIN   dic_documents d ON d.doc_id = s.doc_id
            WHERE  d.collection_id = %s
              AND  (s.content IS NOT NULL AND s.content != '')
            ORDER  BY s.created_at DESC
            LIMIT  %s
            """,
            (collection_id, _MAX_SECTIONS_PER_COLLECTION),
        ).fetchall()
    except Exception:
        return []

    result = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            result.append({
                "section_id": row[0], "doc_id": row[1],
                "heading": row[2], "content": row[3],
            })
        else:
            result.append(dict(row))
    return result


# ── AI-assisted patch draft (best-effort, air-gap safe) ───────────────────────

def _draft_suggestion(
    section: dict,
    change_context: str,
    rationale: str,
    patch_mode: bool,
) -> str:
    """Return suggested content. Falls back to a stub when LLM is unavailable."""
    current = (section.get("content") or "").strip()
    heading = (section.get("heading") or "this section").strip()

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        mode_instruction = (
            "Produce ONLY the changed paragraph(s) with [KEEP] markers for unchanged text."
            if patch_mode
            else "Produce the complete updated section."
        )
        prompt = (
            f"You are updating a document section titled '{heading}'.\n\n"
            f"Change context:\n{change_context}\n\n"
            f"Current content:\n{current or '(empty)'}\n\n"
            f"Task: {mode_instruction}\n"
            "Keep the tone professional, concise, and accurate. "
            "Do not add information not supported by the change context."
        )
        req = LLMRequest(
            system_prompt=(
                "You are a technical documentation assistant. "
                "Update document sections based on system change events. "
                "Return only the updated document text — no commentary."
            ),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("doc_generation", req)
        if resp and resp.content and resp.content.strip():
            return resp.content.strip()
    except Exception as exc:
        logger.debug("dic_integration: LLM draft unavailable (%s), using stub", exc)

    # Stub suggestion — human can edit before accepting
    lines = ["[REVIEW REQUIRED] The following system change may affect this section:"]
    lines.append("")
    lines.append(f"Change context: {change_context}")
    lines.append("")
    lines.append(f"Rationale: {rationale}")
    if current:
        lines.append("")
        lines.append("Current content (preserved — edit above as needed):")
        lines.append(current)
    return "\n".join(lines)


# ── Notification emit (best-effort) ─────────────────────────────────────────

def _emit_notification(suggestion_id: str, collection_id: str, canvas_source: str,
                        rationale: str, conn) -> None:
    try:
        import zlib
        now = _now()
        log_id = f"nlog-{format(zlib.crc32(f'{now}{suggestion_id}'.encode()) & 0xFFFFFFFF, '08x')}"
        conn.execute(
            """
            INSERT INTO notification_log
                (id, event_type, adapter, severity, title, delivered, error, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                log_id,
                "dic_suggestion_created",
                "dic_integration_reflex",
                "info",
                f"DIC suggestion from {canvas_source}: {rationale[:80]}",
                True,
                None,
                now,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("dic_integration: notification emit failed: %s", exc)


# ── Per-event processor ───────────────────────────────────────────────────────

def _process_event(event: dict, conn, dry_run: bool) -> dict:
    """Process a single canvas_events row. Returns a per-event result dict."""
    event_id = event["id"]
    event_type = event.get("event_type", "")
    source_canvas = event.get("source_canvas", "")

    result = {
        "event_id": event_id,
        "event_type": event_type,
        "source_canvas": source_canvas,
        "collections_resolved": 0,
        "suggestions_created": 0,
        "error": None,
    }

    try:
        # Parse payload for change context
        payload_raw = event.get("payload_json") or "{}"
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = {}
        payload.pop("_security_context", None)  # strip internal field

        change_context = (
            payload.get("change_summary")
            or payload.get("description")
            or payload.get("summary")
            or f"{event_type} event from {source_canvas}"
        )

        # Tenant from payload security context (if present)
        tenant_id = (payload.get("_security_context") or {}).get("tenant_id", "")
        event["tenant_id"] = tenant_id  # pass through to adapter

        # Resolve affected collections
        from tools.document_intelligence.canvas_adapter import (
            resolve_affected_collections, mark_event_processed,
        )
        affected = resolve_affected_collections(event)
        result["collections_resolved"] = len(affected)

        if not affected:
            if not dry_run:
                mark_event_processed(event_id, conn)
            return result

        # For each affected collection, create suggestions for representative sections
        from tools.document_intelligence.suggestion_store import create_suggestion
        for col in affected:
            collection_id = col["collection_id"]
            rationale = col.get("rationale", "")
            patch_mode = col.get("patch_mode", False)

            sections = _get_sections_for_collection(conn, collection_id)

            if not sections:
                # No sections yet — create a collection-level stub suggestion
                if not dry_run:
                    sid = create_suggestion(
                        collection_id=collection_id,
                        trigger_event_id=event_id,
                        canvas_source=source_canvas,
                        suggested_content=_draft_suggestion(
                            {"heading": col.get("collection_name", ""), "content": ""},
                            change_context, rationale, patch_mode,
                        ),
                        current_content="",
                        rationale=rationale,
                        tenant_id=tenant_id,
                    )
                    _emit_notification(sid, collection_id, source_canvas, rationale, conn)
                    result["suggestions_created"] += 1
                continue

            for section in sections:
                if not dry_run:
                    suggested = _draft_suggestion(section, change_context, rationale, patch_mode)
                    sid = create_suggestion(
                        section_id=section["section_id"],
                        doc_id=section.get("doc_id", ""),
                        collection_id=collection_id,
                        trigger_event_id=event_id,
                        canvas_source=source_canvas,
                        suggested_content=suggested,
                        current_content=section.get("content", ""),
                        rationale=rationale,
                        tenant_id=tenant_id,
                    )
                    _emit_notification(sid, collection_id, source_canvas, rationale, conn)
                    result["suggestions_created"] += 1

        if not dry_run:
            mark_event_processed(event_id, conn)

    except Exception as exc:
        result["error"] = str(exc)
        logger.warning("dic_integration: event %s failed: %s", event_id, exc)

    return result


# ── Main entry point ──────────────────────────────────────────────────────────

def run(ctx: dict[str, Any], trust: Any = None, *, conn=None) -> dict[str, Any]:
    """Poll canvas_events and create DIC suggestions for HITL review.

    Args:
        ctx: reflex context dict; supports 'dry_run' (bool).
        trust: TrustKernel supplied by the daemon. The daemon dispatches every
            reflex as ``fn(config, trust)`` (see daemon.py ``_observe``), so this
            MUST be the second positional parameter. It previously was ``conn``,
            which meant the TrustKernel was used as a DB handle: every query
            raised, the error was swallowed below, and the reflex reported
            ``events_found: 0`` on every cycle — silently dead, not idle.
        conn: optional injected DB connection (keyword-only, used in tests).

    Returns structured result dict with events_processed, suggestions_created,
    errors list, and status.
    """
    dry_run = bool(ctx.get("dry_run", False))

    result: dict[str, Any] = {
        "cadence_minutes": CADENCE_MINUTES,
        "events_found": 0,
        "events_processed": 0,
        "suggestions_created": 0,
        "errors": [],
        "status": "ok",
        "dry_run": dry_run,
    }

    try:
        own_conn = conn is None
        if own_conn:
            conn = get_connection()

        try:
            events = _fetch_unprocessed_events(conn)
            result["events_found"] = len(events)

            for event in events:
                ev_result = _process_event(event, conn, dry_run)
                if ev_result.get("error"):
                    result["errors"].append(
                        f"event {ev_result['event_id']}: {ev_result['error']}"
                    )
                else:
                    result["events_processed"] += 1
                result["suggestions_created"] += ev_result.get("suggestions_created", 0)

        finally:
            if own_conn:
                conn.close()

    except Exception as exc:
        logger.error("dic_integration reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    if result["errors"] and result["status"] == "ok":
        result["status"] = "partial"

    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json as _json
    print(_json.dumps(run({}), indent=2))
