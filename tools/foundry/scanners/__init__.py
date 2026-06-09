# CUI // SP-CTI
"""ACF vertical source scanner registry.

The Harvester used to call each per-source reader (``harvest_innovation``,
``harvest_creative``, ``harvest_research``, ``harvest_genesis``,
``harvest_telemetry``) via hardcoded ``if name in sources`` branches. Adding a
new signal source meant editing the harvester itself.

This subpackage introduces a function-registry pattern (D-RES-3 / D352) borrowed
from ``tools/research/source_scanner.py``::

    from tools.foundry.scanners import register_source, scan, list_sources

    @register_source("my_new_source")
    def scan_my_new_source(config, *, conn=None, db_path=None, **kwargs):
        return [ {...normalized signal...}, ... ]

    signals = scan("my_new_source", config={...}, conn=conn)

Source contract
---------------
Every registered scanner MUST have the signature::

    scanner(config: dict, *, conn=None, db_path=None, **kwargs) -> list[dict]

where each list element is the *normalized in-memory* signal dict produced by
``tools.foundry.harvester._make_signal`` (with ``source_engine`` set to the
registered name). Returning ``[]`` is a valid no-op (e.g. source disabled in
config). Raising an exception is also fine — the registry caller logs + skips
the source so one bad adapter never aborts the cycle.

Auto-registration
-----------------
Importing this subpackage imports every sibling module (arxiv, …) so the
registry is populated without explicit wiring. Add a new scanner by creating
``tools/foundry/scanners/<name>.py`` and decorating the function with
``@register_source("<name>")``; nothing else needs to change.

Cap / config keys
-----------------
The arxiv_acf scanner reads ``args/foundry_config.yaml -> sources.arxiv_acf``
(``enabled``, ``max_results``, ``keywords``, ``categories``, ``rate_limit``).
Other scanners that read from local DB tables ignore the YAML block and use
``conn`` / ``db_path`` directly.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# The registry. Insertion order is the scan order when ``scan()`` is called
# with ``name=None`` (run all). Built-in DB scanners (innovation, creative,
# research, genesis, telemetry) are NOT registered here — they keep their
# original home in ``tools.foundry.harvester`` to preserve the engine's
# existing call surface; vertical *external* scanners (arxiv, etc.) live
# here and may also be invoked through the registry for one-off scans.
SOURCE_SCANNERS: dict[str, Callable[..., list[dict]]] = {}


def register_source(name: str) -> Callable[[Callable[..., list[dict]]], Callable[..., list[dict]]]:
    """Decorator that registers a scanner function under ``name``.

    Example::

        @register_source("arxiv_acf")
        def scan_arxiv_acf(config, *, conn=None, db_path=None, **kwargs):
            ...

    Re-registration with the same name is a no-op (the first registration
    wins) — protects against duplicate imports.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("source name must be a non-empty string")

    def decorator(fn: Callable[..., list[dict]]) -> Callable[..., list[dict]]:
        if name in SOURCE_SCANNERS and SOURCE_SCANNERS[name] is not fn:
            # First registration wins; warn so duplicate decorators are obvious
            # in logs without crashing the importer.
            import logging
            logging.getLogger("icdev.foundry.scanners").warning(
                "source %r already registered; keeping the first registration", name
            )
            return fn
        SOURCE_SCANNERS[name] = fn
        # Tag the function for introspection (list_sources surfaces this).
        setattr(fn, "_acf_source_name", name)
        return fn

    return decorator


def scan(
    name: str,
    config: Optional[dict] = None,
    *,
    conn: Any = None,
    db_path: Optional[str] = None,
    **kwargs: Any,
) -> list[dict]:
    """Run a single registered scanner by name.

    Returns ``[]`` for unknown sources, disabled sources, or when the scanner
    raises (errors are logged, never raised, so one bad source doesn't abort
    the cycle). Each element is a normalized signal dict matching the
    ``foundry_signals`` row shape (see ``tools.foundry.harvester._make_signal``).
    """
    import logging
    log = logging.getLogger("icdev.foundry.scanners")

    fn = SOURCE_SCANNERS.get(name)
    if fn is None:
        log.warning("unknown source %r (registered: %s)", name, sorted(SOURCE_SCANNERS))
        return []
    # Honor the per-source ``enabled`` flag from foundry_config.yaml.
    if config is not None:
        src_cfg = (config.get("sources") or {}).get(name) or {}
        if isinstance(src_cfg, dict) and src_cfg.get("enabled") is False:
            return []
    try:
        return list(fn(config or {}, conn=conn, db_path=db_path, **kwargs) or [])
    except Exception as exc:  # noqa: BLE001 - one bad source must not abort a cycle
        log.warning("scanner %r failed: %s", name, exc)
        return []


def list_sources() -> list[dict]:
    """Return a list of registered sources with their callable's ``__name__``."""
    out = []
    for name, fn in SOURCE_SCANNERS.items():
        out.append(
            {
                "name": name,
                "scanner_function": getattr(fn, "__name__", repr(fn)),
                "module": getattr(fn, "__module__", "?"),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Auto-register built-in scanners by importing the sibling modules.
# Each module decorates its function with @register_source(...) so importing
# is enough to populate the registry.
# --------------------------------------------------------------------------- #
def _autoregister() -> None:
    import importlib
    import logging
    log = logging.getLogger("icdev.foundry.scanners")

    for mod_name in ("arxiv",):
        try:
            importlib.import_module(f"tools.foundry.scanners.{mod_name}")
        except Exception as exc:  # noqa: BLE001
            log.debug("scanner module %s not registered: %s", mod_name, exc)


_autoregister()


__all__ = [
    "SOURCE_SCANNERS",
    "register_source",
    "scan",
    "list_sources",
]
