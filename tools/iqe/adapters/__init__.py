"""IQE adapter base class."""
from __future__ import annotations
from abc import ABC, abstractmethod


class IQEAdapter(ABC):
    """Base adapter: maps collection names to lists of row dicts."""

    @abstractmethod
    def get_collection(self, collection: str, topology_id: str | None = None) -> list[dict]:
        """Return all rows for the named collection as a list of dicts."""

    @abstractmethod
    def list_collections(self) -> list[str]:
        """Return the collection names this adapter exposes."""
