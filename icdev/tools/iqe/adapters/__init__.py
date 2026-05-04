# CUI // SP-CTI
"""IQE collection adapters — import a module to register its collections."""
from __future__ import annotations

from abc import ABC, abstractmethod


class IQEAdapter(ABC):
    """Base class for IQE collection adapters."""

    @abstractmethod
    def list_collections(self) -> list[str]: ...

    @abstractmethod
    def get_collection(self, collection: str, topology_id: str | None = None) -> list[dict]: ...
