# CUI // SP-CTI
"""DataBridge Connector ABCs and data classes.

Defines the base connector interface (DataConnector), request/response
data classes, connector capabilities, and schema definitions used by all
DataBridge connector implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ConnectorType(Enum):
    """Supported connector protocol families."""

    DATABASE = "database"
    SAAS_API = "saas_api"
    FILE = "file"
    STREAMING = "streaming"
    SOAP = "soap"
    HEALTH = "health"


@dataclass
class ConnectorCapabilities:
    """Declares what a connector supports."""

    supports_read: bool = True
    supports_write: bool = False
    supports_schema_inference: bool = False
    supports_incremental: bool = False
    supports_transactions: bool = False
    max_batch_size: int = 1_000
    supported_formats: List[str] = field(default_factory=lambda: ["json"])


@dataclass
class ConnectorRequest:
    """Standardised request passed to connector read/write methods."""

    table_name: str = ""
    connection_id: str = ""
    query: str = ""
    limit: Optional[int] = None
    incremental_key: str = ""
    incremental_value: Any = None
    sync_direction: str = "read"
    filters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorResponse:
    """Standardised response returned by connector read/write methods."""

    status: str = "ok"
    data: Any = None
    row_count: int = 0
    has_more: bool = False
    next_offset: Any = None
    bytes_transferred: int = 0
    duration_ms: int = 0
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SchemaField:
    """Single field in a schema definition."""

    name: str = ""
    data_type: str = "utf8"
    nullable: bool = True
    description: str = ""


@dataclass
class SchemaDefinition:
    """Schema for a table / resource inferred by a connector."""

    fields: List[SchemaField] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class DataConnector(ABC):
    """Abstract base class for all DataBridge connectors.

    Every concrete connector must implement the abstract methods and
    properties defined here.
    """

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Unique name identifying this connector type."""
        ...

    @property
    @abstractmethod
    def connector_type(self) -> ConnectorType:
        """Protocol family of this connector."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        """Capability declaration for this connector."""
        ...

    @abstractmethod
    def connect(self, config: Dict[str, Any]) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection and release resources."""
        ...

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Return health status dict with at least a 'status' key."""
        ...

    @abstractmethod
    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read data from the source."""
        ...

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        """Write data to the destination. Optional — raise if unsupported."""
        raise NotImplementedError(f"{self.connector_name} does not support write")

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Infer schema for the given table/resource. Optional."""
        return SchemaDefinition(metadata={"source": self.connector_name, "table": table_name})

    def list_tables(self) -> List[str]:
        """List available tables/resources. Optional."""
        return []
