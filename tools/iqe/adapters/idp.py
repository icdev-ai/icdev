# CUI // SP-CTI
"""IQE adapter — Internal Developer Portal (IDP) component facts.

Collections:
    idp.components — one row per registered ICDEV component (every entry in
                     ``args/component_registry.yaml``, all kinds), carrying the
                     measurable facts a scorecard rule can assert on.

This is the query surface that scorecard-as-code rules are written against.
Rules in ``args/scorecards/*.yaml`` are ordinary IQE expressions over
``idp.components``::

    foreach c in idp.components where c.has_owner == true select c.key

so adding a scorecard rule is a YAML edit, never a Python edit. The Python in
this file exists only to *produce the facts*; it does not know about ladders,
weights, or levels — see ``tools/idp/scorecard.py`` for the evaluator.

Fact sourcing:
    * registry metadata (kind, route, owner, nav, IQE wiring) — from the
      component registry YAML, including ``raw`` so ownership fields added by
      a later migration are picked up with no change here.
    * file-existence facts (blueprint, E2E spec, constants, template, seed
      queries) — static checks against the repo tree, no subprocesses.
    * completeness — the shared 8-point validator in
      ``tools.config.component_registry``; only canvases are in scope for that
      gate, so non-canvas components report ``completeness_declared == false``.
    * rls_clean — the shared ``check_canvas_rls_bypass`` coherence check.
    * failing_probes — latest ``awareness_component_health`` snapshot per node,
      attributed to a component by ``route::<path>`` prefix. Probe rows are a
      live signal: when the table is empty or unreachable, ``health_probed`` is
      false and ``failing_probes`` is 0, so a rule can tell "healthy" apart
      from "never measured".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.iqe.executor import register_collection


# ---------------------------------------------------------------------------
# Repo-root resolution (never cwd — this module also runs from worktrees and
# from the icdev/ mirror, where cwd is not the repo root).
# ---------------------------------------------------------------------------

def _base_dir() -> Path:
    from tools.config.component_registry import BASE_DIR  # noqa: PLC0415

    return Path(BASE_DIR)


# ---------------------------------------------------------------------------
# Fact collectors
# ---------------------------------------------------------------------------

def _rls_violation_keys() -> set[str]:
    """Component keys with an open canvas RLS-bypass coherence violation."""
    try:
        from tools.canvas_health.health_data import rls_violation_keys  # noqa: PLC0415

        return rls_violation_keys()
    except Exception:
        return set()


def _module_leaf(module: str | None, root: Path) -> str:
    """Package directory leaf for a registry ``module`` entry ('' when absent)."""
    if not module:
        return ""
    try:
        from tools.config.component_registry import _module_dir_from_module  # noqa: PLC0415

        module_dir = _module_dir_from_module(module, root)
    except Exception:
        module_dir = module.replace(".", "/")
    return module_dir.split("/")[-1] if module_dir else ""


def _e2e_spec_for(key: str, leaf: str, e2e_dir: Path) -> str:
    """Return the first matching Playwright spec filename, or '' when none.

    Matches on the registry key and, when it differs, on the module directory
    leaf — a canvas whose package is named differently from its key still owns
    its spec (e.g. key ``logs`` living in ``tools/logging/``).
    """
    if not e2e_dir.is_dir():
        return ""
    for stem in [s for s in (key, leaf) if s]:
        matches = sorted(e2e_dir.glob(f"{stem}*.spec.ts"))
        if matches:
            return matches[0].name
    return ""


#: awareness_component_health is an append-only snapshot log — it held 465k rows
#: on 2026-08-02 and grows with every probe cycle. Only the newest cycle matters
#: here, and a cycle is ~1 row per route, so read a bounded newest-first window
#: instead of materializing the whole table. Sized well above the live route
#: count so a full cycle always fits.
_PROBE_WINDOW = 20000


def _query_probe_rows(conn: Any) -> list[tuple[Any, Any, Any]]:
    """Read the newest window of route probe snapshots. Raises on DB error."""
    cur = conn.execute(
        "SELECT node_id, status, probed_at FROM awareness_component_health "
        "WHERE probe_type = 'http_head' "
        f"ORDER BY probed_at DESC LIMIT {_PROBE_WINDOW}"  # noqa: S608
    )
    return [tuple(r)[:3] for r in cur.fetchall()]


def _latest_failing_routes(conn: Any) -> tuple[set[str], set[str]]:
    """Return (failing route paths, probed route paths) from awareness_component_health.

    Keeps only the newest snapshot per node so a route that has since recovered
    does not count against a component forever.

    Both halves are route sets, and the second one is why. It used to be a
    single global "did any probe row exist anywhere" boolean, which made
    ``health_probed`` true for all 67 components the moment *one* route was
    probed. Combined with ``failing_probes == 0`` — also 0 for a route nobody
    probed — every unprobed component passed the health rule. Measured
    2026-08-02: ``/document-intelligence`` had zero probe rows and still
    scored a pass. Returning the probed set lets each component ask whether
    *its own* routes were probed, so never-measured comes back as not
    applicable instead of as healthy.
    """
    rows: list[tuple[Any, Any, Any]] = []
    for candidate in (conn, None):
        try:
            if candidate is None:
                from tools.db.storage import get_connection  # noqa: PLC0415

                fallback = get_connection()
                try:
                    rows = _query_probe_rows(fallback)
                finally:
                    try:
                        fallback.close()
                    except Exception:  # noqa: BLE001, S110
                        pass
            else:
                rows = _query_probe_rows(candidate)
            if rows:
                break
        except Exception:
            # A failed statement leaves a PostgreSQL transaction aborted, and
            # every later query on that same connection then reports "relation
            # does not exist" whether or not it does. Roll back so the caller's
            # connection stays usable for the rest of the fact collection.
            if candidate is not None:
                try:
                    candidate.rollback()
                except Exception:  # noqa: BLE001, S110
                    pass
            continue

    if not rows:
        return set(), set()

    latest: dict[str, tuple[Any, str]] = {}
    for node_id, status, probed_at in rows:
        node = str(node_id or "")
        if not node.startswith("route::"):
            continue
        stamp = str(probed_at or "")
        prior = latest.get(node)
        if prior is None or stamp >= prior[0]:
            latest[node] = (stamp, str(status or ""))

    if not latest:
        return set(), set()

    probed = {node[len("route::"):] for node in latest}
    failing = {
        node[len("route::"):]
        for node, (_, status) in latest.items()
        if status in ("fail", "error")
    }
    return failing, probed


def probe_evidence(route: str, conn: Any = None, limit: int = 50) -> list[dict[str, Any]]:
    """Newest probe row per route under *route*'s prefix — the raw evidence.

    ``failing_probes`` is a count, and a count is an assertion until you can
    see the rows behind it. The IDP portal's evidence endpoint (idp-ui-01)
    serves these so a "probes healthy" verdict links to the probe rows that
    produced it rather than asking to be believed.

    Returns ``[]`` when the route is empty, the table is unreadable, or nothing
    under the prefix has been probed. An empty list is "no probe rows", which
    the caller must render as *not measured* — never as *healthy*.
    """
    if not route or route == "/":
        return []

    rows: list[tuple[Any, Any, Any]] = []
    for candidate in (conn, None):
        try:
            if candidate is None:
                from tools.db.storage import get_connection  # noqa: PLC0415

                fallback = get_connection()
                try:
                    rows = _query_probe_rows(fallback)
                finally:
                    try:
                        fallback.close()
                    except Exception:  # noqa: BLE001, S110
                        pass
            else:
                rows = _query_probe_rows(candidate)
            if rows:
                break
        except Exception:
            # Same aborted-transaction hazard as _latest_failing_routes.
            if candidate is not None:
                try:
                    candidate.rollback()
                except Exception:  # noqa: BLE001, S110
                    pass
            continue

    prefix = route.rstrip("/")
    latest: dict[str, tuple[str, str]] = {}
    for node_id, status, probed_at in rows:
        node = str(node_id or "")
        if not node.startswith("route::"):
            continue
        path = node[len("route::"):]
        if not (path == prefix or path.startswith(prefix + "/")):
            continue
        stamp = str(probed_at or "")
        prior = latest.get(path)
        if prior is None or stamp >= prior[0]:
            latest[path] = (stamp, str(status or ""))

    evidence = [
        {"route": path, "status": status, "probed_at": stamp, "node_id": f"route::{path}"}
        for path, (stamp, status) in latest.items()
    ]
    # Failing first — the rows that explain a bad verdict are the ones worth
    # showing when the list is truncated.
    evidence.sort(key=lambda e: (e["status"] not in ("fail", "error"), e["route"]))
    return evidence[:limit]


def _routes_under(route: str, routes: set[str]) -> int:
    """Count routes in *routes* that live under this component's url_prefix.

    A component with no url_prefix (or the bare "/") owns no route subtree and
    matches nothing: "/" as a prefix would otherwise claim every route in the
    platform.
    """
    if not route or route == "/":
        return 0
    prefix = route.rstrip("/")
    return sum(1 for r in routes if r == prefix or r.startswith(prefix + "/"))


def _failing_probe_count(route: str, failing_routes: set[str]) -> int:
    """Count failing probed routes that live under this component's url_prefix."""
    return _routes_under(route, failing_routes)


# ---------------------------------------------------------------------------
# Collection adapter
# ---------------------------------------------------------------------------

# Collecting the facts walks the repo tree, AST-parses every canvas blueprint
# and reads a probe window from the DB (~0.6s for 66 components, measured
# 2026-08-02). A scorecard runs one IQE query per rule against this same
# collection, so an 11-rule scorecard would otherwise pay that cost 11 times.
# The facts derive from files and registry YAML, neither of which changes
# inside a single evaluation run.
_CACHE: list[dict] | None = None


def reset_cache() -> None:
    """Drop the memoized fact rows — call after changing registry or tree state."""
    global _CACHE
    _CACHE = None


def components_adapter(conn: Any = None) -> list[dict]:
    """Return one fact row per registered component (memoized per process)."""
    global _CACHE
    if _CACHE is not None:
        return [dict(r) for r in _CACHE]
    rows = _collect_components(conn)
    _CACHE = [dict(r) for r in rows]
    return rows


def _collect_components(conn: Any = None) -> list[dict]:
    try:
        from tools.config.component_registry import (  # noqa: PLC0415
            get_registry,
            validate_canvas_completeness,
        )

        registry = get_registry()
    except Exception:
        return []

    root = _base_dir()
    e2e_dir = root / "tests" / "e2e"
    adapters_dir = root / "tools" / "iqe" / "adapters"
    queries_dir = root / "context" / "iqe" / "queries"
    rls_violators = _rls_violation_keys()
    failing_routes, probed_routes = _latest_failing_routes(conn)

    rows: list[dict] = []
    for comp in registry.list_all():
        raw = comp.raw or {}
        leaf = _module_leaf(comp.module, root)
        route = comp.url_prefix or ""

        # Ownership (idp-cat-01). Read the *scrubbed* dataclass fields, never
        # raw YAML: Component normalizes the UNOWNED_SENTINELS ('tbd', 'todo',
        # 'unassigned', …) to None, so `owner: TBD` scores as unowned. Reading
        # raw would grade a placeholder as a real owner — the precise failure
        # idp-cat-01 set out to prevent. raw is only the fallback for a
        # registry loaded by an older Component without these fields.
        owner = str(getattr(comp, "owner", None) or "")
        owner_contact = str(getattr(comp, "owner_contact", None) or "")
        on_call = str(getattr(comp, "on_call", None) or "")
        if not hasattr(comp, "is_owned"):
            owner = str(raw.get("owner") or "")
        has_owner = bool(getattr(comp, "is_owned", None) or (not hasattr(comp, "is_owned") and owner))

        iqe_block = comp.iqe or {}
        adapter_module = str(iqe_block.get("adapter_module") or "")
        adapter_leaf = adapter_module.split(".")[-1] if adapter_module else ""
        has_iqe_adapter = bool(
            adapter_leaf and (adapters_dir / f"{adapter_leaf}.py").is_file()
        )

        # Seed queries ship as .iqe (295 files) with a handful of .yaml
        # descriptors alongside; accept either rather than grading a canvas
        # down for the extension its authors happened to use.
        seed_dirs = [d for d in (comp.key, leaf) if d]
        has_seed_queries = any(
            (queries_dir / d).is_dir()
            and any(
                p
                for ext in ("*.iqe", "*.yaml", "*.yml")
                for p in (queries_dir / d).glob(ext)
            )
            for d in seed_dirs
        )

        completeness_declared = comp.kind == "canvas"
        completeness_passed = False
        completeness_points = 0
        if completeness_declared:
            try:
                report = validate_canvas_completeness(comp.key, registry=registry, repo_root=root)
                completeness_passed = bool(report.passed)
                completeness_points = sum(1 for item in report.items if item.present)
            except Exception:
                completeness_declared = False

        e2e_spec = _e2e_spec_for(comp.key, leaf, e2e_dir)

        rows.append({
            # identity
            "key": comp.key,
            "display_name": comp.display_name,
            "kind": comp.kind,
            "cli_name": comp.cli_name,
            "route": route,
            "module": comp.module or "",
            "enabled": bool(comp.default_enabled),
            "min_il": comp.min_il or "",
            "min_tier": comp.min_tier or "",
            # ownership (idp-cat-01)
            "owner": owner,
            "owner_contact": owner_contact,
            "on_call": on_call,
            "has_owner": has_owner,
            "has_owner_contact": bool(owner_contact),
            # wiring
            "has_blueprint": bool(comp.module) and _blueprint_present(comp.module, root),
            "has_e2e_spec": bool(e2e_spec),
            "e2e_spec": e2e_spec,
            "has_iqe_adapter": has_iqe_adapter,
            "has_seed_queries": has_seed_queries,
            "iqe_collections": len(iqe_block.get("collections") or []),
            "has_nav": bool(comp.nav),
            # 8-point completeness gate (canvases only)
            "completeness_declared": completeness_declared,
            "completeness_passed": completeness_passed,
            "completeness_points": completeness_points,
            # coherence + live health
            "rls_clean": comp.key not in rls_violators,
            "failing_probes": _failing_probe_count(route, failing_routes),
            # Per component, not platform-wide: "were THIS component's routes
            # probed?". A component whose routes have no probe row is not
            # healthy, it is unmeasured, and the health rule's filter uses this
            # to say so.
            "probed_routes": _routes_under(route, probed_routes),
            "health_probed": _routes_under(route, probed_routes) > 0,
        })

    rows.sort(key=lambda r: r["key"])
    return rows


def _blueprint_present(module: str, root: Path) -> bool:
    try:
        from tools.config.component_registry import _blueprint_path_from_module  # noqa: PLC0415

        return _blueprint_path_from_module(module, root).is_file()
    except Exception:
        return False


register_collection("idp.components", components_adapter)
