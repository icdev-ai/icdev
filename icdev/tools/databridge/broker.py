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
5. **Connection resolution** — the grant's ``connection_id`` is read from
   ``db_connections`` and handed to the connector as its config, and any
   ``auth_secret_ref`` on that row is resolved through the configured secret
   backend. An unreadable connection or an unresolvable credential is a
   refusal, not an empty config: running a connector on ``{}`` is how a
   per-connection ``egress_allowlist`` came to be declared and never enforced.
6. **Egress guard** — applied in the connector before the socket opens
   (``saas_base._guard_egress``; ``rss_connector`` carries its own copy).
7. **Audit** — one append-only row per call, whatever the outcome. An audit
   write that fails is raised, not logged: a decision that could not be
   recorded must not read as a clean call, and rows are withheld from the
   caller rather than delivered unaudited.

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


class AuditWriteFailed(RuntimeError):
    """Raised when an access decision could not be recorded.

    Distinct from BrokerDenied: the decision itself was reached, the TRAIL is
    what is missing. Kept as its own type because the two need opposite
    handling — a denial is a normal outcome to report, an unrecorded decision is
    a control failure to escalate.
    """


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
    #: Whether this call's decision reached databridge_agent_access_log.
    #: A separate field rather than a note inside ``error`` because "the audit
    #: row is missing" and "the fetch was refused" are different facts, and a
    #: caller that wants to alert on the first must not have to parse prose.
    audited: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "connector": self.connector,
            "table": self.table,
            "rows": self.rows,
            "row_count": self.row_count,
            "error": self.error,
            "redactions": self.redactions,
            "audited": self.audited,
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


#: Config key a resolved ``auth_secret_ref`` is injected under when a connection
#: does not name its own. SaaS connectors read their credential from their config
#: dict (see ``saas_base._build_auth_headers``), and ``api_key`` is the key that
#: docstring uses.
DEFAULT_SECRET_CONFIG_KEY = "api_key"


def _connection_config(connection_id: str) -> dict:
    """Load the db_connections row for *connection_id* and return its config.

    ``connection_id`` was decorative until now: the grant carried it, the broker
    passed it through to ``ConnectorRequest`` and nothing ever read the row, so
    the connector ran on an empty config — which meant, among other things, that
    a per-connection ``egress_allowlist`` was declared and never enforced.

    Fail-closed in both directions. A grant naming a connection whose row cannot
    be read is refused rather than run on ``{}``: an unreadable connection is
    the case where we cannot say what the connector would contact or as whom.
    A credential reference that will not resolve is refused for the same reason,
    and the resolved VALUE is never logged or returned — it goes into the config
    dict handed to ``connect()`` and nowhere else.

    Raises BrokerDenied.
    """
    try:
        from icdev.tools.databridge.connection_manager import (
            get_connection as _get_connection_row,
            resolve_secret,
        )
    except Exception as exc:  # noqa: BLE001
        raise BrokerDenied(f"connection manager unavailable: {exc}") from exc

    row = _get_connection_row(connection_id)
    if not row:
        # get_connection() swallows a store failure into None, so this covers
        # both "no such row" and "could not ask". Stated as the ambiguity it is
        # rather than asserting the row is missing.
        raise BrokerDenied(
            f"connection {connection_id!r} could not be read from db_connections "
            f"(no such row, or the store is unreachable)"
        )

    config: dict[str, Any] = {}
    raw = row.get("config_yaml") or ""
    if str(raw).strip():
        try:
            parsed = yaml.safe_load(raw)
        except Exception as exc:  # noqa: BLE001
            raise BrokerDenied(
                f"connection {connection_id!r} has unparseable config_yaml: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise BrokerDenied(
                f"connection {connection_id!r} config_yaml is not a mapping"
            )
        config = parsed

    secret_ref = str(row.get("auth_secret_ref") or "").strip()
    if secret_ref:
        try:
            secret = resolve_secret(secret_ref)
        except Exception as exc:  # noqa: BLE001
            # The reference, never the value — the ref is a location, which is
            # what an operator needs to fix this, and the value is what must not
            # reach a log line.
            raise BrokerDenied(
                f"credential for connection {connection_id!r} could not be "
                f"resolved from {secret_ref!r}: {exc}"
            ) from exc
        config[str(config.get("secret_config_key") or DEFAULT_SECRET_CONFIG_KEY)] = secret

    return config


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

    Raises AuditWriteFailed when the row does not land. It used to swallow the
    failure into a warning, and that is how the trail stayed empty for the whole
    life of this module: the table did not exist on the PostgreSQL backend at
    all -- its only DDL was authored in SQLite syntax in init_icdev_db.py and so
    never ran there -- and every insert raised UndefinedTable while every fetch
    reported success. A control whose failure mode is a log line nobody reads is
    indistinguishable from a control that works, which is the same defect as a
    security hook wrapped in `|| true`.
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
    except Exception as exc:  # noqa: BLE001 — re-raised as AuditWriteFailed below
        logger.error(
            "databridge broker: AUDIT WRITE FAILED for %s agent=%s connector=%s "
            "table=%s — the decision was NOT recorded: %s",
            decision, agent_id, connector, table, exc,
        )
        raise AuditWriteFailed(
            f"access decision {decision!r} could not be recorded: {exc}"
        ) from exc


def record_denial(agent_id: str, connector: str, table: str, reason: str) -> bool:
    """Record a refusal reached by a caller sitting IN FRONT of the broker.

    ``fetch()`` audits its own decisions. A caller that applies its own policy
    BEFORE calling it would otherwise refuse silently — and a refusal nobody
    recorded is indistinguishable from a call nobody made, which is the exact
    blind spot ``databridge_agent_access_log`` exists to close. The Cortex
    ``external`` backend (cef-bck-02) is the first such caller: it requires a
    ``databridge:<connector>:read`` scope on the presenting service key, and a
    key without it never reaches ``fetch()``.

    Same table and same row shape as an ordinary denial, so an operator reads
    ONE trail rather than correlating two. This is not a second authorization
    path: the broker still applies every check independently when a fetch does
    happen, and nothing here can allow anything.

    Returns True when the row landed, False when it did not. Never raises: the
    refusal stands either way, and a caller that must distinguish "refused" from
    "refused and unrecorded" reads the return value — the same split
    ``FetchOutcome.audited`` makes for an allowed call.
    """
    try:
        _audit(agent_id, connector, table, "denied", reason)
        return True
    except AuditWriteFailed as exc:
        logger.error(
            "databridge broker: pre-broker denial for agent=%s connector=%s "
            "table=%s was NOT recorded: %s", agent_id, connector, table, exc,
        )
        return False


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
        audited = True
        try:
            _audit(agent_id, connector, table, "denied", reason)
        except AuditWriteFailed as exc:
            # The refusal stands — nothing left. But an agent that keeps being
            # refused a connector is the signal this table exists to carry, so a
            # denial that went unrecorded is reported as such rather than
            # returned as an ordinary denial.
            audited = False
            reason = f"{reason} (NOT AUDITED: {exc})"
        return FetchOutcome(ok=False, connector=connector, table=table,
                            error=reason, audited=audited)

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

    # 5. Dispatch. egress_guard fires in the connector before the socket opens.
    connection_id = str(grant.get("connection_id") or "")
    try:
        from icdev.tools.databridge.connector import ConnectorRequest
        from icdev.tools.databridge.registry import get_connector_instance

        instance = get_connector_instance(connector)
        if instance is None:
            return _deny(f"connector {connector!r} is not registered")

        # A grant with no connection_id keeps the old behaviour — an empty
        # config — because a connector needing neither endpoint nor credential
        # is a legitimate shape and denying it would be a new refusal with no
        # security story behind it.
        config = _connection_config(connection_id) if connection_id else {}
        if not instance.connect(config):
            return _deny(
                f"connector {connector!r} refused to connect using connection "
                f"{connection_id or '<none>'!r}"
            )

        response = instance.read(ConnectorRequest(
            table_name=table,
            connection_id=connection_id,
            query=query,
            limit=limit,
            filters=safe_filters,
        ))
    except BrokerDenied as exc:  # connection record / credential resolution
        return _deny(str(exc))
    except PermissionError as exc:  # egress guard, credential refusal
        return _deny(f"blocked: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _deny(f"connector error: {exc}")

    rows = list(getattr(response, "data", None) or [])[:limit]
    try:
        _audit(agent_id, connector, table, "allowed", "",
               rows=len(rows), redactions=redactions)
    except AuditWriteFailed as exc:
        # The read already happened — the rows are in this process. What is
        # still preventable is the agent RECEIVING them unaudited, so they are
        # withheld and the outcome is not ok. "Auto-fetch, and log it" is not a
        # fetch that logs when convenient: an unlogged fetch is not the
        # authorised behaviour, and returning the rows anyway with a warning in
        # the log is exactly the swallow this replaced.
        return FetchOutcome(
            ok=False, connector=connector, table=table,
            row_count=0, redactions=redactions, audited=False,
            error=(f"fetch succeeded but its audit row could not be written, so "
                   f"the rows are withheld: {exc}"),
        )

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


__all__ = ["fetch", "list_available", "record_denial", "FetchOutcome",
           "BrokerDenied", "AuditWriteFailed", "load_manifest",
           "DEFAULT_MAX_ROWS", "HARD_MAX_ROWS"]
