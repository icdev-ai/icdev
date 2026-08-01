# CUI // SP-CTI
"""DIC Freshness Notifier — owner alerts on freshness state crossings (dmx-loop-01).

Fires an owner/steward notification (routed through the shared
``tools/notifications/gateway.py``) the first time a document CROSSES into an
``aging`` or ``stale`` freshness state. This is the side-effecting notification
boundary; the freshness SCORING (``freshness_engine._score_doc``) stays pure.

Design invariants:
  * Crossing-only — a document that was already ``stale`` does NOT re-alert on
    every scan. An alert fires only when the new state is a *worse* state than
    the previously persisted one (fresh/unknown -> aging/stale, or aging ->
    stale).
  * Cooldown / de-dup — ``dic_doc_freshness.last_notified_at`` records the last
    alert time per document; a repeat crossing inside ``cooldown_hours`` is
    suppressed. This column is MUTABLE (updated in place), so it is NOT an
    append-only audit surface.
  * Notify-only — this module never edits a document or a version; HITL
    ``pending_review`` gating is untouched.
  * Air-gap safe — if the gateway/channel is unreachable the crossing is logged
    and skipped; ``last_notified_at`` is left unchanged so a later scan can
    retry once the channel is reachable. A failure here NEVER crashes the
    scan/reflex.
  * Config-gated — behavior lives in ``args/docmod/docmod_config.yaml`` under
    ``freshness_notifications`` and is DISABLED by default.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Severity ordering for crossing detection. "unknown" is treated as the least
# severe so a first-ever computed aging/stale state counts as a crossing.
_STATE_SEVERITY = {"fresh": 0, "unknown": 0, "aging": 1, "stale": 2}

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "cooldown_hours": 168.0,
    "event_type": "dic_freshness_alert",
    "default_channel": "",
    "base_url": "",
    "max_findings": 3,
    "severity_by_state": {"aging": "warning", "stale": "error"},
}


def _load_notif_config() -> Dict[str, Any]:
    """Load the ``freshness_notifications`` block from docmod_config.yaml."""
    cfg: Dict[str, Any] = {}
    try:
        from tools.doc_modernization.pack_loader import load_config

        cfg = (load_config() or {}).get("freshness_notifications", {}) or {}
    except Exception as exc:  # pragma: no cover - config best-effort
        logger.debug("freshness_notifier: config load failed: %s", exc)
        cfg = {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if v is not None})
    return merged


def _sev(state: Optional[str]) -> int:
    return _STATE_SEVERITY.get((state or "unknown"), 0)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _within_cooldown(last_iso: Optional[str], now: datetime, cooldown_hours: float) -> bool:
    """True if a prior alert at ``last_iso`` is still inside the cooldown window."""
    last = _parse_iso(last_iso)
    if last is None:
        return False
    return (now - last).total_seconds() < float(cooldown_hours) * 3600.0


def _resolve_owner(conn, collection_id: str, tenant_id: str, cfg: Dict[str, Any]) -> str:
    """Owner/steward for the document's collection, else configured default channel."""
    owner = ""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT owner_id FROM dic_collections WHERE collection_id = %s AND tenant_id = %s",
            (collection_id, tenant_id),
        )
        row = cur.fetchone()
        if row is not None:
            owner = (row[0] if hasattr(row, "__getitem__") else row["owner_id"]) or ""
    except Exception as exc:
        logger.debug("freshness_notifier: owner lookup failed for %s: %s", collection_id, exc)
        owner = ""
    return owner or (cfg.get("default_channel") or "")


def _top_findings(conn, doc_id: str, limit: int, fallback_reason: str) -> List[str]:
    """Top-N modernization findings for the document (best-effort).

    Falls back to the freshness reason string when the modernization findings
    API is unavailable (e.g. air-gap or module not loaded).
    """
    _rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    try:
        from tools.doc_modernization import get_findings

        findings = get_findings(doc_id=doc_id) or []
        findings = sorted(findings, key=lambda f: _rank.get((f.get("severity") or "medium"), 2))
        out: List[str] = []
        for f in findings[: max(int(limit), 0)]:
            label = f.get("entity_label") or f.get("finding_type") or "finding"
            sev = f.get("severity") or "medium"
            ftype = f.get("finding_type") or ""
            out.append(f"{label} [{sev}]{(' — ' + ftype) if ftype else ''}")
        if out:
            return out
    except Exception as exc:
        logger.debug("freshness_notifier: findings lookup failed for %s: %s", doc_id, exc)
    return [fallback_reason] if fallback_reason else []


def _build_message(fres, owner: str, findings: List[str], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the notification title, body, and metadata."""
    base_url = (cfg.get("base_url") or "").rstrip("/")
    # The DIC freshness heatmap is the document's modernization landing surface.
    link = f"{base_url}/document-intelligence/freshness"
    findings_link = f"{base_url}/document-intelligence/api/modernization/doc/{fres.doc_id}/findings"
    label = fres.title or fres.doc_id

    title = f"[{fres.state.upper()}] Document '{label}' needs review"
    body_lines = [
        f"Document '{label}' ({fres.doc_id}) has crossed into '{fres.state}' freshness.",
        f"Freshness score: {fres.score}",
        f"Reason: {fres.reason or 'n/a'}",
        f"Owner/steward: {owner or 'unassigned'}",
        "",
        "Top findings:",
    ]
    if findings:
        body_lines.extend(f"  - {item}" for item in findings)
    else:
        body_lines.append("  - (no open modernization findings)")
    body_lines.extend(["", f"Review & modernize: {link}", f"Findings API: {findings_link}"])

    metadata = {
        "doc_id": fres.doc_id,
        "collection_id": fres.collection_id,
        "state": fres.state,
        "score": fres.score,
        "owner": owner,
        "tenant_id": fres.tenant_id,
        "classification": fres.classification,
        "link": link,
        "findings": findings,
    }
    return {"title": title, "body": "\n".join(body_lines), "metadata": metadata}


def _mark_notified(conn, doc_id: str, now_iso: str) -> None:
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dic_doc_freshness SET last_notified_at = %s WHERE doc_id = %s",
            (now_iso, doc_id),
        )
    except Exception as exc:
        logger.warning("freshness_notifier: could not persist last_notified_at for %s: %s", doc_id, exc)


def notify_freshness_crossings(
    results: List[Any],
    prior_states: Dict[str, Dict[str, Any]],
    *,
    conn,
    tenant_id: str = "default",
    config: Optional[Dict[str, Any]] = None,
    gateway=None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fire owner alerts for documents that crossed into aging/stale.

    Args:
        results: freshly-scored ``FreshnessResult`` objects for the collection.
        prior_states: ``{doc_id: {"state": str, "last_notified_at": str|None}}``
            captured BEFORE the current scan overwrote them.
        conn: open DB connection (shared with the caller's transaction); the
            caller is responsible for committing.
        tenant_id: tenant scope for owner resolution.
        config: optional pre-resolved ``freshness_notifications`` config
            (defaults are loaded from docmod_config.yaml).
        gateway: optional object exposing ``send(event_type, severity, title,
            body, metadata)`` — a ``NotificationGateway`` is built lazily when
            omitted. Injected in tests to assert the payload.
        now: injectable current time (defaults to ``datetime.now(utc)``).

    Returns:
        ``{"enabled", "notified", "suppressed", "skipped"}`` summary lists of
        doc_ids. Never raises — all failures degrade to a logged skip.
    """
    cfg = config if config is not None else _load_notif_config()
    summary = {"enabled": bool(cfg.get("enabled", False)), "notified": [], "suppressed": [], "skipped": []}

    if not cfg.get("enabled", False):
        return summary

    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cooldown_hours = float(cfg.get("cooldown_hours", 168) or 168)
    event_type = cfg.get("event_type") or "dic_freshness_alert"
    sev_map = cfg.get("severity_by_state") or {}
    max_findings = int(cfg.get("max_findings", 3) or 3)

    gw = gateway
    if gw is None:
        try:
            from tools.notifications.gateway import NotificationGateway

            gw = NotificationGateway()
        except Exception as exc:  # air-gap / import failure — skip cleanly
            logger.warning("freshness_notifier: gateway unavailable, skipping alerts: %s", exc)
            return summary

    for fres in results:
        new_state = getattr(fres, "state", "unknown")
        if new_state not in ("aging", "stale"):
            continue  # only aging/stale are alertable target states

        prior = prior_states.get(getattr(fres, "doc_id", ""), {}) or {}
        prior_state = prior.get("state", "unknown")

        # Crossing-only: the new state must be strictly worse than the prior one.
        if _sev(new_state) <= _sev(prior_state):
            summary["skipped"].append(fres.doc_id)
            continue

        # Cooldown / de-dup: suppress a repeat crossing inside the window.
        if _within_cooldown(prior.get("last_notified_at"), now, cooldown_hours):
            summary["suppressed"].append(fres.doc_id)
            continue

        owner = _resolve_owner(conn, fres.collection_id, tenant_id, cfg)
        findings = _top_findings(conn, fres.doc_id, max_findings, fres.reason)
        msg = _build_message(fres, owner, findings, cfg)
        severity = sev_map.get(new_state, "warning")

        try:
            gw.send(
                event_type=event_type,
                severity=severity,
                title=msg["title"],
                body=msg["body"],
                metadata=msg["metadata"],
            )
        except Exception as exc:
            # Gateway/channel unreachable — log + skip, leave last_notified_at
            # untouched so a later scan can retry. Never crash the sweep.
            logger.warning("freshness_notifier: delivery failed for %s: %s", fres.doc_id, exc)
            summary["skipped"].append(fres.doc_id)
            continue

        _mark_notified(conn, fres.doc_id, now_iso)
        summary["notified"].append(fres.doc_id)

    return summary
