# CUI // SP-CTI
"""ICDEV author-evidence DataBridge connector (read-only, local DB) — dwr-ev-01.

Exposes the currency statements authors made at DIC upload
(``dic_author_assertions``, tools/document_intelligence/author_evidence.py)
through the DataBridge broker, so an agent that wants to read what an author
asserted does it THROUGH ``broker.fetch`` — the connector+table allowlist, the
per-agent grant, the classification ceiling, the read-only rule and ONE
``databridge_agent_access_log`` row on every outcome — and never through a
private ``SELECT`` that skips all five.

Like ``icdev_demand`` this reads the LOCAL platform database: no endpoint, no
secret, no socket. The connection descriptor
(``args/databridge_connections.yaml``: ``dic-author-evidence-local``) declares a
loopback-only ``egress_allowlist`` because that is the vocabulary the shipped-
grant tests read "off-box" from; nothing here can leave the box whatever the
list says.

WHAT THIS IS NOT. It is not how the drafter learns what an author asserted.
The drafter asks ``cortex.resolve`` (tools/doc_modernization/evidence.py), whose
``currency`` rung reads the ``entity_currency`` store, where the author's row is
RANKED against every other source under the one declared policy and the
disagreement rides along under ``others``. This table holds the statements as
made; the store holds them ranked. A reader that took its verdict from here
would be reading one side of a conflict.

Table: ``author_assertions``. Filters: ``doc_id``, ``entity_key`` (normalized by
the store's own key), ``limit``.
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

logger = get_logger("databridge.icdev_author_evidence")

_TABLES = ("author_assertions",)
_DEFAULT_LIMIT = 100


@register_connector
class ICDEVAuthorEvidenceConnector(DataConnector):
    """Read-only feed of author-supplied currency statements."""

    _connector_name = "icdev_author_evidence"

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
            rows = self._read(limit=1)
            return {"status": "healthy", "connector": self._connector_name,
                    "sample_count": len(rows)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "connector": self._connector_name,
                    "error": str(exc)}

    @staticmethod
    def _read(limit: int, doc_id: str = "", entity_key: str = "") -> List[dict]:
        from tools.document_intelligence.author_evidence import list_assertions

        return list_assertions(doc_id=doc_id or None, entity_key=entity_key or None,
                               limit=limit)

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query
        if table not in _TABLES:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table '{table}'. Available: {list(_TABLES)}"],
            )
        filters = request.filters or {}
        try:
            rows = self._read(
                limit=int(request.limit or _DEFAULT_LIMIT),
                doc_id=str(filters.get("doc_id") or ""),
                entity_key=str(filters.get("entity_key") or ""),
            )
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={
                    "source": "dic_author_assertions",
                    "engine": "tools.document_intelligence.author_evidence.list_assertions",
                    # Said on every response: these rows are one source's
                    # statements, not the store's ranked answer.
                    "ranked": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:  # pragma: no cover - refusal
        return ConnectorResponse(
            status="unsupported",
            errors=["icdev_author_evidence is read-only; author statements are "
                    "written by the DIC ingest, never through the bridge"],
        )

    def list_tables(self) -> List[str]:
        return list(_TABLES)
