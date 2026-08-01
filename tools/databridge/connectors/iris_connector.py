# CUI // SP-CTI
"""IRIS DataBridge Connector — the vendor IRIS AI platform (ctx-expose-05).

IRIS is a third-party AI-enabled decision-support platform. Planned integration
surfaces (from the compass premium requirements):

    staffing_alignment   (read)        staff-to-LCAT alignment feed
    performance_reviews  (read+write)  review packets / workflow status
    dashboard_feed       (read)        customer-facing reporting series
    health               (read)        probe

IRIS has NO published API yet. The connector therefore defaults to
``stub_mode`` and serves deterministic fixtures
(``fixtures/iris_fixtures.py``, ``ConnectorResponse.metadata.stub=True``) so
downstream consumers can build against stable shapes today. Going live is a
config flip — ``{"stub_mode": False, "base_url": ..., "api_key": ...}`` —
plus real ``_endpoints`` paths; no consumer changes. When the vendor publishes
an OpenAPI spec, the Connector Forge (tools/databridge/forge/) can generate
a replacement validated against the same shapes.

Secrets arrive via the DataBridge connection manager's resolver chain
(vault:/aws:/file: refs with env fallback ``IRIS_API_KEY``) — never
hardcoded. Bearer auth is assumed until the real scheme is known.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector
from tools.databridge.registry import register_connector
from tools.logging.icdev_logger import get_logger

logger = get_logger("databridge.iris")

# Tables the write path accepts (performance review packets only).
_WRITABLE_TABLES = frozenset({"performance_reviews"})


@register_connector
class IRISConnector(SaaSBaseConnector):
    """IRIS connector — stub mode by default until a vendor API exists."""

    _connector_name = "iris"
    _default_base_url = ""  # unpublished; must be configured when live
    # Placeholder paths — replace when the vendor publishes the API.
    _endpoints = {
        "staffing_alignment": "/api/staffing/alignment",
        "performance_reviews": "/api/performance/reviews",
        "dashboard_feed": "/api/dashboards/feed",
        "health": "/api/health",
    }

    def __init__(self) -> None:
        super().__init__()
        self._stub_mode = True

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,  # performance_reviews only — see write()
            supports_schema_inference=True,
            max_batch_size=100,
            supported_formats=["json"],
        )

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        api_key = config.get("api_key", "")
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    # -- Connection lifecycle -------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        """Store config. Stub mode (the default) needs no reachable endpoint."""
        self._stub_mode = bool(config.get("stub_mode", True))
        if self._stub_mode:
            self._config = dict(config)
            self._auth_headers = {}
            self._connected = True
            logger.info("IRIS connector connected in stub mode (no API published yet)")
            return True
        if not config.get("base_url"):
            logger.error("IRIS live mode requires base_url (no default: API unpublished)")
            return False
        return super().connect(config)

    def health_check(self) -> Dict[str, Any]:
        if self._stub_mode:
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "mode": "stub",
                "note": "IRIS API unpublished — serving deterministic fixtures",
            }
        return super().health_check()

    # -- Data operations ------------------------------------------------------

    def _stub_response(self, table: str, *, limit: Any = None) -> ConnectorResponse:
        from tools.databridge.connectors.fixtures.iris_fixtures import FIXTURES

        rows = list(FIXTURES.get(table, []))
        if limit:
            rows = rows[: int(limit)]
        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows),
            duration_ms=0,
            metadata={"stub": True, "iris_api_version": "unpublished", "table": table},
        )

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        table = request.table_name or request.query
        if table not in self._endpoints:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown IRIS table '{table}'. Available: {list(self._endpoints)}"],
            )
        if self._stub_mode:
            return self._stub_response(table, limit=request.limit)
        return super().read(request)

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        table = request.table_name or request.query
        if table not in _WRITABLE_TABLES:
            return ConnectorResponse(
                status="error",
                errors=[f"IRIS table '{table}' is read-only (writable: {sorted(_WRITABLE_TABLES)})"],
            )
        if self._stub_mode:
            t0 = time.time()
            return ConnectorResponse(
                status="ok",
                data={"accepted": True, "echo": data},
                row_count=1,
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"stub": True, "iris_api_version": "unpublished", "table": table},
            )
        return super().write(request, data)
