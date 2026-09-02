# CUI // SP-CTI
"""DataBridge feeds — external read/write access to allowlisted connectors (ctx-expose-05).

Endpoints:
    GET  /api/databridge/v1/<connector>/<table>    read rows (?limit=N + filters)
    POST /api/databridge/v1/<connector>/<table>    write a payload (JSON body)

Only connectors in ``_CONNECTOR_ALLOWLIST`` are reachable — the DataBridge
registry holds many internal connectors (Splunk, ServiceNow, trading APIs)
that must never be exposed to external service-key callers. v1 exposes the local-DB
only.

Auth: the same ``icdev_ctx_`` service keys as the Cortex REST surface, with
``databridge:<connector>:read`` / ``databridge:<connector>:write`` scopes.
The central dashboard auth hook (tools/dashboard/auth.py) resolves the key
into ``g.cortex_binding``; this blueprint enforces the connector allowlist
and scopes. Dashboard session users are NOT granted feed access — this is a
service-to-service surface only.

Neither exposed connector takes an endpoint or a secret; a ``db_connections`` row through the connection manager's
secret-resolver chain can replace this without changing the route contract.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from flask import Blueprint, g, jsonify, request

from tools.dashboard.config import DEFAULT_CLASSIFICATION
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dashboard.databridge_feeds")

databridge_feeds_bp = Blueprint("databridge_feeds_api", __name__)

_PREFIX = "/api/databridge/v1"

# Externally exposable connectors only. Everything else in the registry is
# internal-only regardless of the caller's scopes.
_CONNECTOR_ALLOWLIST = frozenset({"icdev_demand", "icdev_cpmp"})

# Cached connected instances, keyed by connector name.
_instances: Dict[str, Any] = {}


def _connector_config(name: str) -> Dict[str, Any]:
    """Env-based v1 config. Both exposed connectors read the local platform DB
    and take no endpoint or secret, so there is nothing to configure."""
    return {}


def _get_instance(name: str) -> Optional[Any]:
    if name in _instances:
        return _instances[name]
    # Import for registration side effect, then resolve through the registry.
    if name == "icdev_demand":
        import tools.databridge.connectors.icdev_demand_connector  # noqa: F401
    elif name == "icdev_cpmp":
        import tools.databridge.connectors.icdev_cpmp_connector  # noqa: F401

    from tools.databridge.registry import get_connector_instance

    connector = get_connector_instance(name)
    if connector is None:
        return None
    if not connector.connect(_connector_config(name)):
        logger.warning("databridge feed connector '%s' failed to connect", name)
        return None
    _instances[name] = connector
    return connector


def _check(connector: str, direction: str):
    """Validate service binding + allowlist + scope. Returns a response or None.

    ``g.cortex_binding`` is set by the central auth hook when the caller
    presented a valid icdev_ctx_ service key. Session users don't have one
    and are denied — feeds are service-to-service only.
    """
    binding = getattr(g, "cortex_binding", None)
    if binding is None:
        return jsonify({
            "error": "DataBridge feeds require a Cortex service key (icdev_ctx_)",
            "code": "unauthorized",
        }), 401
    if connector not in _CONNECTOR_ALLOWLIST:
        return jsonify({
            "error": f"connector '{connector}' is not exposed "
                     f"(available: {sorted(_CONNECTOR_ALLOWLIST)})",
            "code": "forbidden",
        }), 403
    scope = f"databridge:{connector}:{direction}"
    if scope not in (binding.get("scopes") or []):
        return jsonify({
            "error": f"API key lacks required scope '{scope}'",
            "code": "forbidden",
            "label": binding.get("label", ""),
        }), 403
    return None


def _respond(resp) -> tuple:
    body = {
        "ok": resp.status == "ok",
        "status": resp.status,
        "data": resp.data,
        "row_count": resp.row_count,
        "errors": resp.errors,
        "metadata": resp.metadata,
        "classification": DEFAULT_CLASSIFICATION,
    }
    return jsonify(body), (200 if resp.status == "ok" else 502)


@databridge_feeds_bp.route(f"{_PREFIX}/<connector>/<table>", methods=["GET"])
def feed_read(connector: str, table: str):
    denied = _check(connector, "read")
    if denied is not None:
        return denied
    instance = _get_instance(connector)
    if instance is None:
        return jsonify({"error": f"connector '{connector}' unavailable",
                        "code": "unavailable"}), 502

    from tools.databridge.connector import ConnectorRequest

    limit = request.args.get("limit", type=int)
    filters = {k: v for k, v in request.args.items() if k != "limit"}
    if connector == "icdev_cpmp":
        # Bell-LaPadula read-down inside the connector: rows above the service
        # key's ceiling never leave the platform.
        filters["_caller_classification"] = g.cortex_binding["ctx"].classification
    resp = instance.read(ConnectorRequest(table_name=table, limit=limit, filters=filters))
    return _respond(resp)


@databridge_feeds_bp.route(f"{_PREFIX}/<connector>/<table>", methods=["POST"])
def feed_write(connector: str, table: str):
    denied = _check(connector, "write")
    if denied is not None:
        return denied
    instance = _get_instance(connector)
    if instance is None:
        return jsonify({"error": f"connector '{connector}' unavailable",
                        "code": "unavailable"}), 502

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"error": "request body must be JSON", "code": "validation"}), 400

    from tools.databridge.connector import ConnectorRequest

    resp = instance.write(ConnectorRequest(table_name=table, sync_direction="write"), body)
    return _respond(resp)
