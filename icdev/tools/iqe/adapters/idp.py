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


def _latest_failing_routes(conn: Any) -> tuple[set[str], bool]:
    """Return (failing route paths, probed) from awareness_component_health.

    Keeps only the newest snapshot per node so a route that has since recovered
    does not count against a component forever. ``probed`` is False when the
    table is empty or unreadable, which lets a rule distinguish a genuinely
    healthy component from one that has never been probed.
    """
    rows: list[tuple[Any, Any, Any]] = []
    for candidate in (conn, None):
        try:
            if candidate is None:
                from tools.db.storage import get_connection  # noqa: PLC0415

                with get_connection() as fallback:
                    cur = fallback.execute(
                        "SELECT node_id, status, probed_at FROM awareness_component_health"
                    )
                    rows = [tuple(r)[:3] for r in cur.fetchall()]
            else:
                cur = candidate.execute(
                    "SELECT node_id, status, probed_at FROM awareness_component_health"
                )
                rows = [tuple(r)[:3] for r in cur.fetchall()]
            if rows:
                break
        except Exception:
            continue

    if not rows:
        return set(), False

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
        return set(), False

    return (
        {node[len("route::"):] for node, (_, status) in latest.items() if status in ("fail", "error")},
        True,
    )


def _failing_probe_count(route: str, failing_routes: set[str]) -> int:
    """Count failing probed routes that live under this component's url_prefix."""
    if not route or route == "/":
        return 0
    prefix = route.rstrip("/")
    return sum(
        1 for r in failing_routes if r == prefix or r.startswith(prefix + "/")
    )


# ---------------------------------------------------------------------------
# Collection adapter
# ---------------------------------------------------------------------------

# Collecting the facts walks the repo tree and AST-parses every canvas
# blueprint (~18s for 66 components). A scorecard runs one IQE query per rule
# against this same collection, so without a cache an 8-rule scorecard would
# pay that cost 8 times. The facts are derived from files and registry YAML,
# neither of which changes inside a single evaluation run.
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
    failing_routes, health_probed = _latest_failing_routes(conn)

    rows: list[dict] = []
    for comp in registry.list_all():
        raw = comp.raw or {}
        leaf = _module_leaf(comp.module, root)
        route = comp.url_prefix or ""

        # Ownership — read from raw so fields added by a later registry
        # migration (idp-cat-01) are picked up with no change to this module.
        owner = str(raw.get("owner") or "")
        owner_team = str(raw.get("owner_team") or "")
        on_call = str(raw.get("on_call") or "")

        iqe_block = comp.iqe or {}
        adapter_module = str(iqe_block.get("adapter_module") or "")
        adapter_leaf = adapter_module.split(".")[-1] if adapter_module else ""
        has_iqe_adapter = bool(
            adapter_leaf and (adapters_dir / f"{adapter_leaf}.py").is_file()
        )

        seed_dirs = [d for d in (comp.key, leaf) if d]
        has_seed_queries = any(
            (queries_dir / d).is_dir() and any((queries_dir / d).glob("*.iqe"))
            for d in seed_dirs
        )

        completeness_declared = comp.kind == "canvas"
        completeness_passed = False
        completeness_points = 0
        if completeness_declared:
            try:
                report = validate_canvas_completeness(comp.key, registry=registry, repo_root=root)
                completeness_passed = bool(report.passed)
                completeness_points = sum(
                    1 for item in report.items if item.present or not item.required
                )
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
            "owner_team": owner_team,
            "on_call": on_call,
            "has_owner": bool(owner or owner_team),
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
            "health_probed": health_probed,
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
