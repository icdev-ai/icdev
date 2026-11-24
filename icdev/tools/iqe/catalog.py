# CUI // SP-CTI
"""IQE seed-query catalog: load every adapter, say which collections can run, run a seed.

The dashboard's ``GET /iqe`` / ``POST /iqe/run`` and ``POST /api/v1/iqe/query``
read the seed files under ``context/iqe/queries/``. They used to run them on
whatever collections some other page had happened to import, and /iqe/run
answered an unreadable collection with ``{"ok": true, "count": 0}``. This module
is the one place both routes go through:

* :func:`load_all_adapters` imports every ``tools.iqe.adapters.*`` module once,
  so a seed's collection is registered no matter which page loaded first; a
  module that fails to import is reported, never silently skipped.
* :data:`NO_EXECUTOR_SOURCE` names the collections seeds read that have NO data
  source anywhere -- reviewed, each with its reason. The listing withholds those
  seeds and the run route refuses them with that reason.
* :func:`run_seed` executes one seed with ``conn=None`` -- each adapter opens its
  own database, exactly as every canvas route calls ``execute_query(ast, None)``
  -- and turns an unreadable collection into ``ok: false`` with the reason, and
  any other failure into ``ok: false`` without the driver's raw text.
"""
from __future__ import annotations

import importlib
import pkgutil
import threading
from pathlib import Path
from typing import Any

from tools.iqe.ast_nodes import CollectionCall, ForeachNode
from tools.iqe.executor import (
    CollectionUnavailableError,
    execute_query_with_meta,
    list_collections,
)
from tools.iqe.parser import IQESyntaxError, parse
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.iqe.catalog")

#: Collections that seed queries read but that nothing can serve: no adapter
#: registers them and no table backs them in any schema (checked against every
#: CREATE TABLE under tools/, including tools/db/schema/pg_consolidated.sql).
#: tests/test_iqe_seed_collections_registered.py fails on an entry that becomes
#: registered or that no seed reads any more.
_NDC_V01 = (
    "NDC v0.1 seed (docs/features/phase-iqe-v0-1.md). No adapter or table holds {what}: "
    "tools/iqe/adapters/ndc.py serves devices, circuits, sites and the like, and the "
    "nqe.* collections args/component_registry.yaml declares have no adapter. "
    "tests/test_iqe_seed_queries.py runs it only over fixture tables through the "
    "executor's table fallback. It needs the planned NQE/Batfish import adapter (iqe-v0-3)."
)
NO_EXECUTOR_SOURCE: dict[str, str] = {
    "network.interfaces": _NDC_V01.format(what="interface state"),
    "network.bgp_peers": _NDC_V01.format(what="BGP peer state"),
    "network.bgp_sessions": _NDC_V01.format(what="BGP session state"),
    "network.acl_rules": _NDC_V01.format(what="ACL rules"),
    "network.routes": _NDC_V01.format(what="routing tables"),
    "network.vlans": _NDC_V01.format(what="VLAN port counts"),
    "odc_coverage": (
        "ODC seed over a per-technique coverage table (severity, signal_source, fp_rate, "
        "partial coverage) that no schema defines. tools/iqe/adapters/observability.py "
        "serves mitre.coverage, which derives full/none coverage from canvas designs and "
        "has none of those fields, so the seed cannot be pointed at it without changing "
        "what it asks."
    ),
}

_ADAPTERS_PACKAGE = "tools.iqe.adapters"
_lock = threading.Lock()
_loaded = False
_failures: dict[str, str] = {}


def load_all_adapters() -> dict[str, str]:
    """Import every adapter module once; return ``{module: why it failed}``.

    Idempotent and thread-safe. A module that raises on import registers none
    of its collections, so it is logged and returned rather than skipped
    silently -- its seeds then fail their registration check or report the
    collection as unavailable.
    """
    global _loaded
    with _lock:
        if _loaded:
            return dict(_failures)
        package = importlib.import_module(_ADAPTERS_PACKAGE)
        for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
            module = f"{_ADAPTERS_PACKAGE}.{info.name}"
            try:
                importlib.import_module(module)
            except Exception as exc:  # noqa: BLE001 -- one broken adapter must not hide the rest
                _failures[module] = type(exc).__name__
                logger.warning("iqe catalog: adapter %s failed to import: %s", module, exc)
        _loaded = True
        return dict(_failures)


def collections_of(ast: ForeachNode) -> list[str]:
    """Every collection *ast* reads: the collection, or each join()/union() argument."""
    coll = ast.collection
    if isinstance(coll, CollectionCall):
        name = str(coll.name)
        args = [str(a.value) for a in coll.args]
        if name == "join":
            return args[:2]
        if name == "union":
            return args
        return [name]
    return [str(coll)]


def unavailable_reason(ast: ForeachNode) -> str | None:
    """Why *ast* cannot run on the executor, or None. Call :func:`load_all_adapters` first."""
    registered = set(list_collections())
    reasons = []
    for name in collections_of(ast):
        if name in registered:
            continue
        why = NO_EXECUTOR_SOURCE.get(name, "no adapter is registered for it")
        reasons.append(f"collection {name!r} is unavailable: {why}")
    return "; ".join(reasons) or None


def list_seeds(queries_dir: Path) -> dict[str, Any]:
    """The listing: seeds, and those withheld because a collection has no data source at all.

    Only :data:`NO_EXECUTOR_SOURCE` seeds are withheld. Any other seed whose
    collection is not registered stays listed and its run is refused with 422
    and the reason; tests/test_iqe_seed_collections_registered.py keeps every
    shipped seed out of that gap.
    """
    load_all_adapters()
    queries: list[dict] = []
    withheld: list[dict] = []
    if queries_dir.is_dir():
        for path in sorted(queries_dir.rglob("*.iqe")):
            rel = path.relative_to(queries_dir)
            entry = {
                "name": path.stem,
                "path": rel.as_posix(),
                "canvas": rel.parts[0] if len(rel.parts) > 1 else "general",
            }
            try:
                names = collections_of(parse(path.read_text(encoding="utf-8")))
            except IQESyntaxError:
                names = []  # listed; running it reports the parse error
            no_source = [n for n in names if n in NO_EXECUTOR_SOURCE]
            if no_source:
                withheld.append({**entry, "reason": "; ".join(
                    f"collection {n!r} has no data source: {NO_EXECUTOR_SOURCE[n]}" for n in no_source)})
            else:
                queries.append(entry)
    return {"ok": True, "queries": queries, "count": len(queries), "not_executable": withheld}


def run_seed(target: Path, query_path: str) -> tuple[dict[str, Any], int]:
    """Parse and run one seed file; return ``(body, status)``. Never an empty success on failure.

    ``complete`` / ``incomplete`` carry the executor's report: a row cap that bit,
    or a union member that failed, makes the rows a lower bound, and says so.
    """
    try:
        ast = parse(target.read_text(encoding="utf-8"))
    except IQESyntaxError as exc:
        return {"ok": False, "error": f"parse error: {exc}"}, 400
    load_all_adapters()
    reason = unavailable_reason(ast)
    if reason:
        return {"ok": False, "query": query_path, "error": reason}, 422
    try:
        result = execute_query_with_meta(ast, None)
    except CollectionUnavailableError as exc:
        return {"ok": False, "query": query_path, "error": str(exc)}, 422
    except Exception as exc:  # noqa: BLE001 -- reported as a failure, never as zero rows
        logger.warning("IQE execute failed for %s: %s", query_path, exc)
        return {"ok": False, "query": query_path,
                "error": f"execution failed ({type(exc).__name__}); see the server log"}, 500
    return {
        "ok": True,
        "query": query_path,
        "rows": result.rows,
        "count": len(result.rows),
        "complete": result.complete,
        "incomplete": result.incomplete,
    }, 200
