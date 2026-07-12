# CUI // SP-CTI
"""ICDEV Demand-Signal DataBridge connector (read-only, local DB).

Exposes the RFI/proposals demand pipeline's aggregated capability gaps
(``rfi_capability_gaps`` — tools/govcon/rfi_demand.py) through the DataBridge
feeds surface so external workforce tools (compass supply-vs-demand analysis,
prem-lcatq-04) can consume opportunity demand without direct DB access.

Unlike the SaaS connectors this one reads the LOCAL platform database via the
existing ``list_demand_signals()`` engine — no endpoints, no secrets. Access
control happens at the feeds surface: ``databridge:icdev_demand:read`` scope
on a cortex service key.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
)
from tools.databridge.registry import register_connector
from tools.logging.icdev_logger import get_logger

logger = get_logger("databridge.icdev_demand")

_TABLES = ("demand_signals",)
_DEFAULT_LIMIT = 100


@register_connector
class ICDEVDemandConnector(DataConnector):
    """Read-only feed of aggregated RFI/proposal demand signals."""

    _connector_name = "icdev_demand"

    def __init__(self) -> None:
        self._connected = False

    @property
    def connector_name(self) -> str:
        return self._connector_name

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.DATABASE

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            max_batch_size=_DEFAULT_LIMIT,
            supported_formats=["json"],
        )

    def connect(self, config: Dict[str, Any]) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def health_check(self) -> Dict[str, Any]:
        try:
            rows = self._read_signals(limit=1)
            return {"status": "healthy", "connector": self._connector_name,
                    "sample_count": len(rows)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "connector": self._connector_name,
                    "error": str(exc)}

    def _read_signals(self, limit: int) -> List[dict]:
        from tools.govcon.rfi_demand import list_demand_signals

        return list_demand_signals(limit=limit)

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query
        if table not in _TABLES:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table '{table}'. Available: {list(_TABLES)}"],
            )
        try:
            rows = self._read_signals(limit=int(request.limit or _DEFAULT_LIMIT))
            status_filter = (request.filters or {}).get("status")
            if status_filter:
                rows = [r for r in rows if r.get("status") == status_filter]
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"source": "rfi_capability_gaps",
                          "engine": "tools.govcon.rfi_demand.list_demand_signals"},
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def list_tables(self) -> List[str]:
        return list(_TABLES)
