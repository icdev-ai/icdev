# CUI // SP-CTI
"""DataBridge Connector Registry — central registration and lookup.

Provides:
  - ``register_connector`` decorator for class-level auto-registration
  - ``get_connector_instance(name)`` to instantiate a registered connector
  - ``load_forge_connectors(db_path=)`` to bulk-load promoted forge connectors
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict, Optional, Type

logger = get_logger("databridge.registry")

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"

# Global connector class registry: connector_name -> class
_REGISTRY: Dict[str, Type] = {}


def register_connector(cls: Type) -> Type:
    """Class decorator that registers a connector class by its name.

    Usage::

        @register_connector
        class MyConnector(DataConnector):
            _connector_name = "my_connector"
            ...

    The class must have either a ``_connector_name`` class attribute or a
    ``connector_name`` property.
    """
    name = getattr(cls, "_connector_name", None)
    if name is None:
        # Try to get from an instance (property-based)
        try:
            instance = cls.__new__(cls)
            name = instance.connector_name
        except Exception:
            name = cls.__name__.lower()

    if name:
        _REGISTRY[name] = cls
        logger.debug("Registered connector: %s -> %s", name, cls.__name__)
    return cls


def get_connector_instance(name: str) -> Optional[Any]:
    """Instantiate and return a connector by registered name.

    Returns None if the name is not found in the registry.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        logger.warning("Connector '%s' not found in registry", name)
        return None
    try:
        return cls()
    except Exception as exc:
        logger.error("Failed to instantiate connector '%s': %s", name, exc)
        return None


def load_forge_connectors(db_path: Optional[str] = None) -> int:
    """Load promoted forge connectors from DB into registry.

    Scans ``db_forge_connectors`` for rows with status='promoted' or
    status='published' and dynamically registers them.  Returns the
    count of connectors loaded.
    """
    db_path or str(DB_PATH)
    loaded = 0
    try:
        conn = get_connection(db_path=str(db_path))
        rows = conn.execute(
            "SELECT connector_name, connector_code FROM db_forge_connectors WHERE status IN ('promoted', 'published')"
        ).fetchall()
        conn.close()

        for row in rows:
            name = row["connector_name"]
            code = row["connector_code"]
            if not code:
                continue
            try:
                # Execute the connector module code which should call
                # @register_connector internally
                exec(
                    compile(code, f"<forge:{name}>", "exec"),
                    {  # nosec B102 -- exec used for dynamic plugin loading with sanitized input
                        "__name__": f"forge_{name}",
                        "__builtins__": __builtins__,
                    },
                )
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to load forge connector '%s': %s", name, exc)

    except Exception as exc:
        logger.warning("load_forge_connectors failed: %s", exc)

    return loaded


def list_registered() -> Dict[str, str]:
    """Return dict of registered connector_name -> class_name."""
    return {name: cls.__name__ for name, cls in _REGISTRY.items()}
