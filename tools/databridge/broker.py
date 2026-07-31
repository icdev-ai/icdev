# CUI // SP-CTI
"""The only way an agent reaches an external SaaS connector.

Before this, an agent could not reach DataBridge at all — no ACE role's
``icdev_tools`` referenced it, ``args/agent_toolsets.yaml`` had no connector
bundle, and ``TOOL_REGISTRY`` had no generic fetch. The only thing keeping a
co-worker away from a customer's Splunk or ServiceNow was that nobody had
wired it up. That is an accident, not a control.

Wiring it up without a chokepoint would have made the accident into a hole, so
this module is the chokepoint. Nothing agent-facing may call
``get_connector_instance()`` directly; everything goes through ``fetch()``,
which applies, in order:

1. **Air-gap interlock** — an external SaaS call is off-box by definition.
2. **Authorization** — the (agent, connector, table) triple against a
   declarative manifest. An unlisted connector is refused before anything else
   happens, so an unknown target costs no credential resolution and no DNS.
3. **Read-only enforcement** — writes are refused outright. An agent creating a
   Jira ticket or updating a CRM record is a far larger blast radius than
   reading one, it lands in a system ICDEV does not own and cannot roll back,
   and it needs its own approval design.
4. **Outbound redaction** — any free-text filter value is sanitized
   fail-closed, because a query string is the one part of a fetch that carries
   caller content.
5. **Egress guard** — applied in ``saas_base`` before the socket opens.
6. **Audit** — one append-only row per call, whatever the outcome.

Deliberately NOT reachable through ``ToolRunner``: it matches command strings
exactly, so a parameterised data fetch cannot be usefully allowlisted there.
The MCP tool ``databridge_fetch`` is the agent-facing surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_MANIFEST_NAME = "databridge_agent_access.yaml"

#: Row cap per call. An agent that needs more than this is doing analysis that
#: belongs in a pipeline, not in a reasoning loop, and an unbounded fetch is how
#: a tool call becomes an exfiltration.
DEFAULT_MAX_ROWS = 200
HARD_MAX_ROWS = 1000


class BrokerDenied(PermissionError):
    """Raised when a fetch is refused. Carries the reason for the audit row."""


@dataclass
class FetchOutcome:
    """Result of a brokered fetch."""

    ok: bool
    connector: str
    table: str
    rows: list = field(default_factory=list)
    row_count: int = 0
    error: str = ""
    redactions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "connector": self.connector,
            "table": self.table,
            "rows": self.rows,
            "row_count": self.row_count,
            "error": self.error,
            "redactions": self.redactions,
        }


def _manifest_path():
    from icdev._paths import get_data_path

    return get_data_path("args") / _MANIFEST_NAME


def load_manifest() -> dict:
    """Load the access manifest, degrading to deny-all.

    A missing or unreadable manifest means no agent may reach any connector.
    Defaulting to permissive here would make a config mistake indistinguishable
    from a grant.
    """
    try:
        raw = yaml.safe_load(_manifest_path().read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("databridge broker: no access manifest (%s) — denying all", exc)
        return {"enabled": False, "connectors": []}
    raw.setdefault("enabled", False)
    raw.setdefault("connectors", [])
    return raw


def _airgap_active() -> bool:
    try:
        from icdev.tools.airgap import is_airgap

        return bool(is_airgap())
    except Exception:  # noqa: BLE001
        return False


def _connector_grant(connector: str) -> dict | None:
    """Return the manifest entry for *connector*, or None."""
    manifest = load_manifest()
    if not manifest.get("enabled"):
        return None
    for entry in manifest.get("connectors") or []:
        if isinstance(entry, dict) and entry.get("name") == connector:
            return entry
    return None


def _redact_outbound(text: str) -> tuple[str, int]:
    """Sanitize a free-text query value. Raises when it cannot be verified.

    Fail-closed on purpose: a filter value is the one part of a fetch carrying
    caller content, and an unavailable sanitizer means we cannot say what would
    leave. Compare ICDEV's LLM egress, which takes the same position.
    """
    value = str(text or "")
    if not value.strip():
        return value, 0

    try:
        from icdev.tools.redaction.govcon_sanitizer import GovConSanitizer
    except Exception as exc:  # noqa: BLE001
        raise BrokerDenied(f"outbound redaction unavailable: {exc}") from exc

    try:
        sanitized, meta = GovConSanitizer().sanitize_for_llm(
            value, function_name="databridge_fetch", is_local_only=False
        )
    except Exception as exc:  # noqa: BLE001
        raise BrokerDenied(f"outbound redaction failed: {exc}") from exc

    count = 0
    if isinstance(meta, dict):
        count = int(meta.get("redactions_applied") or meta.get("count") or 0)
    return sanitized, count


def _audit(agent_id: str, connector: str, table: str, decision: str,
           reason: str = "", rows: int = 0, redactions: int = 0) -> None:
    """Append one row per call, allowed or denied.

    Writes to databridge_agent_access_log, NOT db_sync_log. The first draft
    used db_sync_log and every insert failed silently: that table records sync
    OPERATIONS, requires a connection_id FK and counts rows, while an
    authorization decision has none of those — a denied fetch has no connection
    and moved nothing. The audit trail was empty precisely when it mattered, and
    the tests did not catch it because they stubbed this function.

    Denials are the interesting half: a connector an agent keeps being refused
    is either a misconfiguration or someone probing.
    """
    try:
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO databridge_agent_access_log "
                "(agent_id, connector_name, table_name, decision, reason, "
                " rows_returned, redactions_applied, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (agent_id or "unknown", connector, table, decision,
                 str(reason)[:500], int(rows), int(redactions),
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — auditing must not block the decision
        logger.warning("databridge broker: audit write failed: %s", exc)


def fetch(
    agent_id: str,
    connector: str,
    table: str,
    *,
    filters: dict | None = None,
    query: str = "",
    limit: int = DEFAULT_MAX_ROWS,
    classification: str = "UNCLASSIFIED",
) -> FetchOutcome:
    """Read from an external connector on an agent's behalf.

    Read-only by construction: there is no write path here, and adding one is a
    separate design with its own approval flow.

    Never raises — a denial is a result, because an agent loop should be able to
    reason about "I was refused" rather than crash.
    """
    connector = str(connector or "").strip()
    table = str(table or "").strip()
    limit = max(1, min(int(limit or DEFAULT_MAX_ROWS), HARD_MAX_ROWS))

    def _deny(reason: str) -> FetchOutcome:
        logger.warning(
            "databridge broker: DENIED agent=%s connector=%s table=%s — %s",
            agent_id, connector, table, reason,
        )
        _audit(agent_id, connector, table, "denied", reason)
        return FetchOutcome(ok=False, connector=connector, table=table, error=reason)

    if not connector or not table:
        return _deny("connector and table are required")

    # 1. Air-gap.
    if _airgap_active():
        return _deny("air-gap mode is active; external connectors are unavailable")

    # 2. Authorization — before credentials, before DNS.
    grant = _connector_grant(connector)
    if grant is None:
        return _deny(f"connector {connector!r} is not granted to agents")

    allowed_tables = grant.get("tables") or []
    if table not in allowed_tables:
        return _deny(f"table {table!r} not in the grant for {connector!r}")

    allowed_agents = grant.get("agents") or []
    if allowed_agents and agent_id not in allowed_agents:
        return _deny(f"agent {agent_id!r} is not granted {connector!r}")

    ceiling = str(grant.get("classification_ceiling") or "UNCLASSIFIED").upper()
    order = ["UNCLASSIFIED", "CUI", "SECRET", "TOP SECRET"]
    try:
        if order.index(str(classification).upper()) > order.index(ceiling):
            return _deny(f"classification {classification} exceeds ceiling {ceiling}")
    except ValueError:
        return _deny(f"unknown classification {classification!r}")

    # 3. Read-only. Stated explicitly rather than implied by the absence of a
    # write path, so a future contributor has to argue with it.
    if (filters or {}).get("_write") or query.strip().lower().startswith(
        ("insert", "update", "delete", "drop")
    ):
        return _deny("the agent broker is read-only")

    # 4. Outbound redaction of free-text values.
    redactions = 0
    safe_filters = dict(filters or {})
    try:
        if query:
            query, n = _redact_outbound(query)
            redactions += n
        for key, value in list(safe_filters.items()):
            if isinstance(value, str) and value.strip():
                safe_filters[key], n = _redact_outbound(value)
                redactions += n
    except BrokerDenied as exc:
        return _deny(str(exc))

    # 5. Dispatch. egress_guard fires inside saas_base before the socket opens.
    try:
        from icdev.tools.databridge.connector import ConnectorRequest
        from icdev.tools.databridge.registry import get_connector_instance

        instance = get_connector_instance(connector)
        if instance is None:
            return _deny(f"connector {connector!r} is not registered")

        response = instance.read(ConnectorRequest(
            table_name=table,
            connection_id=str(grant.get("connection_id") or ""),
            query=query,
            limit=limit,
            filters=safe_filters,
        ))
    except PermissionError as exc:  # egress guard, credential refusal
        return _deny(f"blocked: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _deny(f"connector error: {exc}")

    rows = list(getattr(response, "data", None) or [])[:limit]
    _audit(agent_id, connector, table, "allowed", "",
           rows=len(rows), redactions=redactions)

    return FetchOutcome(
        ok=True, connector=connector, table=table,
        rows=rows, row_count=len(rows), redactions=redactions,
    )


def list_available(agent_id: str = "") -> list[dict]:
    """Connectors and tables this agent may read.

    Lets an agent discover its own reach instead of probing and collecting
    denials — probing is indistinguishable from an attack in the audit trail.
    """
    manifest = load_manifest()
    if not manifest.get("enabled") or _airgap_active():
        return []

    out: list[dict] = []
    for entry in manifest.get("connectors") or []:
        if not isinstance(entry, dict):
            continue
        agents = entry.get("agents") or []
        if agents and agent_id and agent_id not in agents:
            continue
        out.append({
            "connector": entry.get("name"),
            "tables": list(entry.get("tables") or []),
            "classification_ceiling": entry.get("classification_ceiling", "UNCLASSIFIED"),
            "description": entry.get("description", ""),
        })
    return out


__all__ = ["fetch", "list_available", "FetchOutcome", "BrokerDenied",
           "load_manifest", "DEFAULT_MAX_ROWS", "HARD_MAX_ROWS"]
