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
import importlib
import re
from pathlib import Path
from typing import Any, Dict, Optional, Type

logger = get_logger("databridge.registry")

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"

# Global connector class registry: connector_name -> class
_REGISTRY: Dict[str, Type] = {}

# A connector registers itself as an IMPORT SIDE EFFECT of the @register_connector
# decorator, so a connector module nobody imported does not exist as far as this
# registry is concerned. Nothing imported the connector modules: the package has
# no __init__.py that pulls them in, and the agent broker's only lookup is
# get_connector_instance(). Every brokered fetch therefore died at
# "connector 'rss' is not registered" — a name that reads like a missing
# implementation when in fact 33 connectors were sitting on disk unimported.
#
# The import below is RELATIVE to this module's own package, not spelled
# "tools.databridge.connectors...". That distinction is load-bearing: this file
# is mirrored at tools/ and icdev/tools/, `tools.databridge.registry` and
# `icdev.tools.databridge.registry` resolve to two DISTINCT module objects with
# two DISTINCT _REGISTRY dicts (the tools/__init__.py shim redirects attribute
# access, not `import tools.x.y`), and the broker imports the icdev one. An
# absolute "tools." import here would register the class into the other copy's
# dict and this one would still answer "not registered".
_CONNECTOR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Names already attempted, so a miss costs one import attempt rather than one
#: per call. Holds failures too — a connector that does not exist must not be
#: re-probed on every fetch.
_AUTOLOAD_ATTEMPTED: set[str] = set()


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


def autoload_connector(name: str) -> bool:
    """Import ``<this package>.connectors.<name>_connector`` for its side effect.

    Returns True when *name* is registered afterwards.

    Only ``[a-z][a-z0-9_]*`` is accepted. The caller in practice is the agent
    broker, which has already matched *name* against its manifest allowlist
    before reaching here — but this function is public and the value it appends
    ``_connector`` to becomes a module path, so it is validated here rather than
    trusted from a caller that might change.
    """
    if name in _REGISTRY:
        return True
    if not _CONNECTOR_NAME_RE.match(name or "") or name in _AUTOLOAD_ATTEMPTED:
        return name in _REGISTRY
    _AUTOLOAD_ATTEMPTED.add(name)

    module = f"{__package__}.connectors.{name}_connector"
    try:
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 — a connector's optional dep may be absent
        # Not an error: only a granted connector is ever looked up, and an
        # optional dependency (feedparser, boto3, hvac) being uninstalled is a
        # deployment fact, not a fault. The caller reports "not registered".
        logger.debug("Connector autoload failed for %r (%s): %s", name, module, exc)
        return False
    return name in _REGISTRY


def get_connector_instance(name: str) -> Optional[Any]:
    """Instantiate and return a connector by registered name.

    Falls back to importing the connector module on a miss — see the note at
    ``_CONNECTOR_NAME_RE``. Returns None if the name cannot be resolved.
    """
    cls = _REGISTRY.get(name)
    if cls is None and autoload_connector(name):
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
    """Return dict of registered connector_name -> class_name.

    Reports what has been IMPORTED, not what exists on disk — see
    ``autoload_connector``. Call that first if you need a specific name.
    """
    return {name: cls.__name__ for name, cls in _REGISTRY.items()}
