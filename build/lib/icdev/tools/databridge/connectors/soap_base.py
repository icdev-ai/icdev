
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""SoapBaseConnector — base class for SOAP/WSDL web services (D-CF-SOAP-1).

Supports: WSDL auto-parse, WS-Security, XSD schema inference.
Concrete connectors override: _wsdl_url(), _operation_name(), connector_name.
"""

import time
from abc import abstractmethod
from typing import Any, Dict, List

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
    SchemaDefinition,
    SchemaField,
)

logger = get_logger("databridge.connectors.soap_base")

try:
    import zeep
    from zeep.wsse.username import UsernameToken

    HAS_ZEEP = True
except ImportError:
    zeep = None  # type: ignore[assignment]
    HAS_ZEEP = False

try:
    import requests

    from tools.http.client import request as _http_request

    HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore[assignment]
    _http_request = None  # type: ignore[assignment]
    HAS_REQUESTS = False

try:
    import pyarrow as pa

    HAS_ARROW = True
except ImportError:
    pa = None  # type: ignore[assignment]
    HAS_ARROW = False


class SoapBaseConnector(DataConnector):
    """Base class for SOAP/WSDL connectors (D-CF-SOAP-1).

    Subclasses must implement:
      - connector_name property
      - _wsdl_url(config) -> str
      - _operation_name(table_name) -> str

    Optional overrides:
      - _build_params(request) -> dict
      - _parse_response(raw) -> List[dict]
      - _ws_security(config) -> zeep UsernameToken or None
    """

    _connector_type = ConnectorType.SOAP
    _capabilities = ConnectorCapabilities(
        supports_read=True,
        supports_write=True,
        supports_schema_inference=True,
        supports_incremental=False,
        max_batch_size=1_000,
        supported_formats=["arrow", "json"],
    )

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._client: Any = None

    @property
    def connector_type(self) -> ConnectorType:
        return self._connector_type

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return self._capabilities

    @abstractmethod
    def _wsdl_url(self, config: Dict[str, Any]) -> str:
        """Return WSDL URL for this service."""
        ...

    @abstractmethod
    def _operation_name(self, table_name: str) -> str:
        """Map a table_name to a SOAP operation name."""
        ...

    def _build_params(self, request: ConnectorRequest) -> Dict[str, Any]:
        """Build SOAP call parameters from a ConnectorRequest."""
        return request.filters.copy() if request.filters else {}

    def _parse_response(self, raw: Any) -> List[Dict[str, Any]]:
        """Convert SOAP response to list of dicts."""
        if isinstance(raw, list):
            return [dict(r) if hasattr(r, "__dict__") else {"value": r} for r in raw]
        if hasattr(raw, "__dict__"):
            return [{k: v for k, v in raw.__dict__.items() if not k.startswith("_")}]
        if isinstance(raw, dict):
            return [raw]
        return [{"value": str(raw)}] if raw is not None else []

    def _ws_security(self, config: Dict[str, Any]) -> Any:
        """Return WS-Security token if credentials provided."""
        if HAS_ZEEP and config.get("ws_username"):
            return UsernameToken(
                config["ws_username"],
                config.get("_resolved_secret") or config.get("ws_password", ""),
            )
        return None

    def connect(self, config: Dict[str, Any]) -> bool:
        self._config = config
        wsdl = self._wsdl_url(config)
        if HAS_ZEEP:
            try:
                wsse = self._ws_security(config)
                self._client = zeep.Client(wsdl, wsse=wsse)
                return True
            except Exception as exc:
                logger.error("SOAP connect via zeep failed: %s", exc)
                return False
        # Stdlib fallback: verify WSDL URL is reachable
        if HAS_REQUESTS:
            try:
                resp = _http_request("GET", wsdl, timeout=10)
                return resp.status_code < 400
            except Exception:
                return False
        logger.warning("Neither zeep nor requests available — assuming connected")
        return True

    def disconnect(self) -> None:
        self._client = None

    def health_check(self) -> Dict[str, Any]:
        start = time.time()
        wsdl = self._wsdl_url(self._config)
        if HAS_REQUESTS:
            try:
                resp = _http_request("GET", wsdl, timeout=10)
                return {
                    "status": "healthy" if resp.status_code < 400 else "unhealthy",
                    "latency_ms": int((time.time() - start) * 1000),
                    "http_status": resp.status_code,
                }
            except Exception as exc:
                return {"status": "unhealthy", "message": str(exc)}
        return {"status": "unknown", "message": "requests not available"}

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        start = time.time()
        try:
            op_name = self._operation_name(request.table_name)
            params = self._build_params(request)

            if self._client:
                op = getattr(self._client.service, op_name)
                raw = op(**params)
            else:
                raise NotImplementedError("zeep required for SOAP calls — install with: pip install zeep")

            rows = self._parse_response(raw)
            if request.limit:
                rows = rows[: request.limit]

            if HAS_ARROW and rows:
                data = pa.Table.from_pylist(rows)
            else:
                data = rows

            duration_ms = int((time.time() - start) * 1000)
            return ConnectorResponse(
                status="ok",
                data=data,
                row_count=len(rows),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - start) * 1000),
            )

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        """SOAP write = call an operation with data as params."""
        start = time.time()
        if not self._client:
            return ConnectorResponse(status="error", errors=["Not connected or zeep unavailable"])
        try:
            op_name = self._operation_name(request.table_name)
            op = getattr(self._client.service, op_name)
            if isinstance(data, dict):
                raw = op(**data)
            elif isinstance(data, list):
                raw = op(data)
            else:
                raw = op(data)
            return ConnectorResponse(
                status="ok",
                row_count=1,
                metadata={"response": str(raw)[:500]},
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - start) * 1000),
            )

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Infer schema from a sample read or WSDL type inspection."""
        resp = self.read(ConnectorRequest(table_name=table_name, limit=10))
        if resp.data:
            rows = resp.data
            if HAS_ARROW and hasattr(rows, "to_pylist"):
                rows = rows.to_pylist()
            if isinstance(rows, list) and rows:
                sample = rows[0]
                fields = [SchemaField(name=k, data_type="utf8") for k in sample.keys()]
                return SchemaDefinition(fields=fields)
        return SchemaDefinition(metadata={"source": self.connector_name, "protocol": "soap"})

    def list_tables(self) -> List[str]:
        """List available SOAP operations."""
        if self._client:
            try:
                return [op for op in dir(self._client.service) if not op.startswith("_")]
            except Exception:
                return []
        return []
