
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""HealthBaseConnector — base class for HL7v2, FHIR REST, and CDA XML (D-CF-HEALTH-1).

Extends SaaSBaseConnector with healthcare-specific resource handling.
Concrete connectors override: connector_name, _protocol(), _base_url(), _auth_headers().
"""

import time
from abc import abstractmethod
from enum import Enum
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
    SchemaDefinition,
    SchemaField,
)

logger = get_logger("databridge.connectors.health_base")

try:
    import requests

    from tools.http.client import get_session

    HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore[assignment]
    get_session = None  # type: ignore[assignment]
    HAS_REQUESTS = False

try:
    import pyarrow as pa

    HAS_ARROW = True
except ImportError:
    pa = None  # type: ignore[assignment]
    HAS_ARROW = False


class HealthProtocol(Enum):
    """Supported healthcare data protocols."""

    FHIR_REST = "fhir_rest"
    HL7V2_MLLP = "hl7v2_mllp"
    CDA_XML = "cda_xml"


# Standard FHIR R4 resource types
FHIR_RESOURCE_TYPES = [
    "Patient",
    "Encounter",
    "Observation",
    "Condition",
    "Procedure",
    "MedicationRequest",
    "DiagnosticReport",
    "AllergyIntolerance",
    "Immunization",
    "CarePlan",
    "Practitioner",
    "Organization",
    "Location",
    "Device",
    "Medication",
]


class HealthBaseConnector(DataConnector):
    """Base class for healthcare data connectors (D-CF-HEALTH-1).

    Subclasses must implement:
      - connector_name property
      - _protocol() -> HealthProtocol
      - _base_url(config) -> str
      - _auth_headers(config) -> dict
    """

    _connector_type = ConnectorType.HEALTH
    _capabilities = ConnectorCapabilities(
        supports_read=True,
        supports_write=False,
        supports_schema_inference=True,
        supports_incremental=True,
        max_batch_size=1_000,
        supported_formats=["arrow", "json"],
    )

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._session: Any = None
        self._base: str = ""

    @property
    def connector_type(self) -> ConnectorType:
        return self._connector_type

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return self._capabilities

    @abstractmethod
    def _protocol(self) -> HealthProtocol:
        """Return the healthcare protocol this connector uses."""
        ...

    @abstractmethod
    def _base_url(self, config: Dict[str, Any]) -> str:
        """Return the FHIR server base URL or HL7 endpoint."""
        ...

    @abstractmethod
    def _auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Return authentication headers."""
        ...

    def connect(self, config: Dict[str, Any]) -> bool:
        self._config = config
        self._base = self._base_url(config)
        proto = self._protocol()

        if proto == HealthProtocol.FHIR_REST:
            if not HAS_REQUESTS:
                raise ImportError("requests required for FHIR REST connectors")
            self._session = get_session()
            self._session.headers.update(self._auth_headers(config))
            self._session.headers["Accept"] = "application/fhir+json"
            return True

        if proto == HealthProtocol.HL7V2_MLLP:
            # HL7v2 MLLP uses raw TCP — connect/send handled in read()
            return True

        if proto == HealthProtocol.CDA_XML:
            if HAS_REQUESTS:
                self._session = get_session()
                self._session.headers.update(self._auth_headers(config))
                self._session.headers["Accept"] = "application/xml"
            return True

        return False

    def disconnect(self) -> None:
        if self._session:
            self._session.close()
            self._session = None

    def health_check(self) -> Dict[str, Any]:
        start = time.time()
        proto = self._protocol()

        if proto == HealthProtocol.FHIR_REST and self._session:
            try:
                resp = self._session.get(f"{self._base}/metadata", timeout=10)
                return {
                    "status": "healthy" if resp.status_code < 400 else "unhealthy",
                    "latency_ms": int((time.time() - start) * 1000),
                    "fhir_version": resp.json().get("fhirVersion", "unknown") if resp.status_code < 400 else "unknown",
                }
            except Exception as exc:
                return {"status": "unhealthy", "message": str(exc)}

        if proto == HealthProtocol.HL7V2_MLLP:
            import socket

            try:
                host = self._config.get("host", "localhost")
                port = int(self._config.get("port", 2575))
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
                return {
                    "status": "healthy",
                    "latency_ms": int((time.time() - start) * 1000),
                }
            except Exception as exc:
                return {"status": "unhealthy", "message": str(exc)}

        return {"status": "unknown", "message": "Protocol not connected"}

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        proto = self._protocol()
        if proto == HealthProtocol.FHIR_REST:
            return self._read_fhir(request)
        if proto == HealthProtocol.HL7V2_MLLP:
            return self._read_hl7v2(request)
        if proto == HealthProtocol.CDA_XML:
            return self._read_cda(request)
        return ConnectorResponse(status="error", errors=[f"Unsupported protocol: {proto}"])

    def _read_fhir(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read FHIR resources. table_name = resource type (e.g. 'Patient')."""
        if not self._session:
            return ConnectorResponse(status="error", errors=["Not connected"])

        start = time.time()
        resource_type = request.table_name
        url = f"{self._base}/{resource_type}"
        params: Dict[str, Any] = {}
        if request.limit:
            params["_count"] = min(request.limit, 100)
        params.update(request.filters)

        all_rows: List[Dict[str, Any]] = []
        max_pages = self._config.get("max_pages", 10)

        try:
            for _ in range(max_pages):
                resp = self._session.get(url, params=params, timeout=30)
                if resp.status_code >= 400:
                    return ConnectorResponse(
                        status="error",
                        errors=[f"HTTP {resp.status_code}: {resp.text[:200]}"],
                        duration_ms=int((time.time() - start) * 1000),
                    )

                bundle = resp.json()
                entries = bundle.get("entry", [])
                for entry in entries:
                    resource = entry.get("resource", {})
                    all_rows.append(
                        {
                            "resource_type": resource.get("resourceType", ""),
                            "id": resource.get("id", ""),
                            "meta_version": resource.get("meta", {}).get("versionId", ""),
                            "meta_last_updated": resource.get("meta", {}).get("lastUpdated", ""),
                            "raw_json": str(resource),
                        }
                    )

                if request.limit and len(all_rows) >= request.limit:
                    all_rows = all_rows[: request.limit]
                    break

                # Follow FHIR pagination links
                next_link = None
                for link in bundle.get("link", []):
                    if link.get("relation") == "next":
                        next_link = link.get("url")
                        break
                if not next_link:
                    break
                url = next_link
                params = {}

            if HAS_ARROW and all_rows:
                data = pa.Table.from_pylist(all_rows)
            else:
                data = all_rows

            return ConnectorResponse(
                status="ok",
                data=data,
                row_count=len(all_rows),
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - start) * 1000),
            )

    def _read_hl7v2(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read HL7v2 messages via MLLP (Minimal Lower Layer Protocol)."""
        import socket

        start = time.time()
        host = self._config.get("host", "localhost")
        port = int(self._config.get("port", 2575))
        query_msg = request.query or request.table_name

        # MLLP framing: \x0b + message + \x1c\x0d
        mllp_msg = f"\x0b{query_msg}\x1c\x0d"

        try:
            sock = socket.create_connection((host, port), timeout=30)
            sock.sendall(mllp_msg.encode("utf-8"))

            response_data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b"\x1c\x0d" in response_data:
                    break
            sock.close()

            # Strip MLLP framing
            msg = response_data.decode("utf-8", errors="replace")
            msg = msg.strip("\x0b\x1c\x0d")

            rows = [{"raw_message": msg, "protocol": "hl7v2"}]
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=1,
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - start) * 1000),
            )

    def _read_cda(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read CDA XML documents."""
        if not self._session:
            return ConnectorResponse(status="error", errors=["Not connected"])

        start = time.time()
        url = f"{self._base}/{request.table_name}"

        try:
            resp = self._session.get(url, timeout=30)
            if resp.status_code >= 400:
                return ConnectorResponse(
                    status="error",
                    errors=[f"HTTP {resp.status_code}"],
                    duration_ms=int((time.time() - start) * 1000),
                )

            root = ET.fromstring(resp.text)  # nosec B314 -- parsing trusted internal MBSE/config XML
            rows = [
                {
                    "document_id": root.get("id", ""),
                    "title": (root.find(".//{urn:hl7-org:v3}title") or ET.Element("x")).text or "",
                    "raw_xml": resp.text[:10000],
                }
            ]
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=1,
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - start) * 1000),
            )

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        proto = self._protocol()
        if proto == HealthProtocol.FHIR_REST:
            return SchemaDefinition(
                fields=[
                    SchemaField(name="resource_type", data_type="utf8"),
                    SchemaField(name="id", data_type="utf8"),
                    SchemaField(name="meta_version", data_type="utf8"),
                    SchemaField(name="meta_last_updated", data_type="utf8"),
                    SchemaField(name="raw_json", data_type="utf8"),
                ],
                metadata={
                    "source": self.connector_name,
                    "resource_type": table_name,
                },
            )
        if proto == HealthProtocol.HL7V2_MLLP:
            return SchemaDefinition(
                fields=[
                    SchemaField(name="raw_message", data_type="utf8"),
                    SchemaField(name="protocol", data_type="utf8"),
                ],
                metadata={"source": self.connector_name, "protocol": "hl7v2"},
            )
        return SchemaDefinition(
            fields=[
                SchemaField(name="document_id", data_type="utf8"),
                SchemaField(name="title", data_type="utf8"),
                SchemaField(name="raw_xml", data_type="utf8"),
            ],
            metadata={"source": self.connector_name, "protocol": "cda_xml"},
        )

    def list_tables(self) -> List[str]:
        """For FHIR: return standard resource types. For HL7/CDA: empty."""
        proto = self._protocol()
        if proto == HealthProtocol.FHIR_REST:
            return FHIR_RESOURCE_TYPES
        return []
