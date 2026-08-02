# CUI // SP-CTI
"""IDP component facts — one fact row per registered ICDEV component.

This module answers a single question: *what do we already measure about every
component in ``args/component_registry.yaml``?*  The answer is a flat dict per
component with boolean and integer fields, which is exactly the shape an IQE
rule expression can filter on.

Why flat booleans rather than nested objects: an IQE rule is a ``where`` clause
over one row, so every fact a rule can test has to be a top-level field of that
row.  ``c.completeness_passed == true`` is a rule an operator can read; walking
into a nested report from the DSL is not.

Facts and where they come from:

    ownership          args/component_registry.yaml owner/owner_contact/on_call
                       (idp-cat-01) via ComponentRegistry
    IQE adapter        the registry's own ``iqe:`` block
    E2E spec           tests/e2e/<key>*.spec.ts (and the module-dir leaf, since
                       several canvases name their spec after the package rather
                       than the registry key)
    completeness       component_registry.validate_canvas_completeness — the
                       8-point dashboard-page gate. Canvas-only: the gate is
                       defined for dashboard pages, so a feature or core
                       extension is reported ``completeness_checked = false``
                       rather than failed for a gate that does not apply to it.
    coherence          coherence_checker.check_canvas_rls_bypass — the only
                       per-component coherence signal the platform computes.
    health probes      awareness_component_health, joined to a component by
                       longest matching ``url_prefix`` on the probe's route.

Every source is optional at runtime.  A missing DB, an un-migrated table or an
import failure degrades that one fact (probes become "not probed"), it never
raises — a scorecard that cannot be computed because one signal is unavailable
is worse than a scorecard that reports the signal as absent.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.logging.icdev_logger import get_logger

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_logger = get_logger("idp.component_facts")

#: Default evidence window, in days, for time-series facts (health probes).
DEFAULT_WINDOW_DAYS = 90

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([dhw])?\s*$", re.IGNORECASE)


def parse_window(window: Any, default_days: int = DEFAULT_WINDOW_DAYS) -> int:
    """Parse a scorecard ``evaluation.window`` into whole days.

    Accepts ``90``, ``"90"``, ``"90d"``, ``"12w"``, ``"48h"``.  An hour window
    rounds up to one day because every fact below has at most daily resolution.
    Anything unparseable falls back to *default_days* — a malformed window
    should not silently narrow the evidence to nothing.
    """
    if window is None:
        return default_days
    if isinstance(window, (int, float)) and not isinstance(window, bool):
        return max(1, int(window))
    m = _WINDOW_RE.match(str(window))
    if not m:
        _logger.warning("unparseable evaluation window %r — using %sd", window, default_days)
        return default_days
    value, unit = int(m.group(1)), (m.group(2) or "d").lower()
    if unit == "w":
        return max(1, value * 7)
    if unit == "h":
        return max(1, -(-value // 24))  # ceil
    return max(1, value)


# ---------------------------------------------------------------------------
# Individual fact sources
# ---------------------------------------------------------------------------


def _module_dir_leaf(module: str | None) -> str:
    """``tools.infra_canvas.blueprint`` → ``infra_canvas``."""
    if not module:
        return ""
    parts = module.split(".")
    # Drop the trailing module name (blueprint) to land on the package.
    return parts[-2] if len(parts) >= 2 else parts[-1]  # noqa: PLR2004


def _norm(name: str) -> str:
    """Fold separators out of a name so ``aiify`` matches ``ai_ify.spec.ts``."""
    return name.replace("_", "").replace("-", "").lower()


def _e2e_spec_counts(keys: Iterable[tuple[str, str]], repo_root: Path) -> dict[str, int]:
    """Return ``{component_key: spec_count}`` for ``tests/e2e/*.spec.ts``.

    *keys* is ``(component_key, module_dir_leaf)`` pairs.  A spec counts for a
    component when its filename starts with either name, compared with
    separators folded out.  canvas_health globs on the registry key alone, which
    both misses specs named after the package (``noc_canvas.spec.ts`` for the
    ``nocc`` canvas) and misses separator drift (``ai_ify.spec.ts`` for
    ``aiify``).  Matching the package leaf makes this a coarse signal — a spec
    named for a package credits every canvas served out of it — which is why the
    rule is worth points rather than being an exact-coverage claim.
    """
    e2e_dir = repo_root / "tests" / "e2e"
    if not e2e_dir.is_dir():
        return {}
    names = [_norm(p.name) for p in e2e_dir.glob("*.spec.ts")]
    counts: dict[str, int] = {}
    for key, leaf in keys:
        prefixes = {_norm(p) for p in (key, leaf) if p}
        counts[key] = sum(1 for n in names if any(n.startswith(p) for p in prefixes))
    return counts


def _coherence_violators() -> set[str]:
    """Component keys flagged by the canvas RLS-bypass coherence check.

    Mirrors ``tools/canvas_health/health_data.py::_rls_violations`` — a violation
    path such as ``tools/security_canvas/db/init_db.py`` names the package, which
    is the component's module directory.
    """
    try:
        from tools.workflow.coherence_checker import check_canvas_rls_bypass

        result = check_canvas_rls_bypass()
    except Exception as exc:  # noqa: BLE001 — a missing checker degrades one fact
        _logger.warning("coherence check unavailable: %s", exc)
        return set()

    violators: set[str] = set()
    for path in (getattr(result, "missing", None) or []):
        parts = Path(str(path).replace("\\", "/")).parts
        if len(parts) >= 3:  # noqa: PLR2004
            violators.add(parts[-3] if parts[-2] == "db" else parts[-2])
        elif len(parts) >= 2:  # noqa: PLR2004
            violators.add(parts[-2])
    return violators


def _health_probe_counts(
    prefixes: list[tuple[str, str]],
    window_days: int,
    conn: Any = None,
) -> dict[str, tuple[int, int]]:
    """Return ``{component_key: (probe_count, failure_count)}``.

    A probe row carries the route it hit inside its ``detail`` JSON blob.  We
    attribute it to the component whose ``url_prefix`` is the *longest* prefix of
    that route, so ``/admin/api/auth-log`` goes to ``admin_console`` and not to a
    component mounted at ``/``.

    ``detail`` is parsed in Python rather than with ``json_extract`` — the SQL
    here has to run on PostgreSQL as well, and CLAUDE.md forbids leaning on
    ``translate_sql`` for runtime JSON.
    """
    if not prefixes:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    owns_conn = conn is None
    try:
        if owns_conn:
            from tools.db.storage import get_connection

            conn = get_connection()
    except Exception as exc:  # noqa: BLE001 — no DB means no probe facts
        _logger.warning("health probes unavailable (no connection): %s", exc)
        return {}

    try:
        rows = conn.execute(
            "SELECT status, detail FROM awareness_component_health "
            "WHERE probed_at >= %s",
            (cutoff,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — un-migrated table is "not probed"
        _logger.warning("health probes unavailable: %s", exc)
        return {}
    finally:
        if owns_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110 — best-effort close
                pass

    # Longest-prefix first so the most specific mount wins.
    ordered = sorted(
        ((k, p.rstrip("/")) for k, p in prefixes if p and p != "/"),
        key=lambda kp: len(kp[1]),
        reverse=True,
    )

    counts: dict[str, list[int]] = {}
    for row in rows:
        status, detail = (row[0], row[1]) if not isinstance(row, dict) else (
            row.get("status"), row.get("detail")
        )
        try:
            payload = json.loads(detail) if isinstance(detail, str) else (detail or {})
        except (json.JSONDecodeError, ValueError):
            continue
        route = (payload or {}).get("route")
        if not route:
            continue
        for key, prefix in ordered:
            if route == prefix or route.startswith(prefix + "/"):
                slot = counts.setdefault(key, [0, 0])
                slot[0] += 1
                if str(status).lower() == "fail":
                    slot[1] += 1
                break
    return {k: (v[0], v[1]) for k, v in counts.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_component_facts(
    window_days: int = DEFAULT_WINDOW_DAYS,
    conn: Any = None,
    registry: Any = None,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return one fact row per registered component.

    Args:
        window_days: Evidence window for time-series facts (health probes).
        conn: Optional open DB connection. One is opened and closed per call
            when omitted.
        registry: Optional ``ComponentRegistry``. Defaults to the singleton.
        repo_root: Optional repo root for file-existence checks.

    Returns:
        A list of flat dicts — the ``idp.components`` IQE collection.
    """
    from tools.config.component_registry import get_registry, validate_canvas_completeness

    if registry is None:
        registry = get_registry()
    root = Path(repo_root or BASE_DIR)

    components = list(registry.list_all())
    leaves = [(c.key, _module_dir_leaf(c.module)) for c in components]
    e2e_counts = _e2e_spec_counts(leaves, root)
    violators = _coherence_violators()
    probe_counts = _health_probe_counts(
        [(c.key, c.url_prefix or "") for c in components], window_days, conn
    )

    rows: list[dict[str, Any]] = []
    for comp in components:
        leaf = _module_dir_leaf(comp.module)
        iqe = comp.iqe or {}
        collections = iqe.get("collections") or []

        completeness_checked = comp.kind == "canvas"
        completeness_passed = False
        present = required = 0
        if completeness_checked:
            try:
                report = validate_canvas_completeness(comp.key, registry=registry, repo_root=root)
                completeness_passed = bool(report.passed)
                required = sum(1 for i in report.items if i.required)
                present = sum(1 for i in report.items if i.required and i.present)
            except Exception as exc:  # noqa: BLE001 — report as failed, never raise
                _logger.warning("completeness gate failed for %s: %s", comp.key, exc)

        probes, probe_failures = probe_counts.get(comp.key, (0, 0))

        rows.append(
            {
                "key": comp.key,
                "kind": comp.kind,
                "display_name": comp.display_name,
                "enabled": bool(comp.is_enabled()),
                "url_prefix": comp.url_prefix or "",
                "module": comp.module or "",
                # Ownership (idp-cat-01)
                "owned": bool(comp.is_owned),
                "owner": comp.owner or "",
                "owner_contact": comp.owner_contact or "",
                "on_call": comp.on_call or "",
                "has_owner_contact": bool(comp.owner_contact),
                "has_on_call": bool(comp.on_call),
                # Query surface
                "has_iqe_adapter": bool(iqe.get("adapter_module")),
                "iqe_collections": len(collections),
                # Verification
                "has_e2e_spec": e2e_counts.get(comp.key, 0) > 0,
                "e2e_specs": e2e_counts.get(comp.key, 0),
                # 8-point dashboard-page completeness gate (canvases only)
                "completeness_checked": completeness_checked,
                "completeness_passed": completeness_passed,
                "completeness_present": present,
                "completeness_required": required,
                # Coherence
                "coherence_clean": comp.key not in violators and leaf not in violators,
                # Health probes, bounded by the scorecard's evaluation window
                "health_probed": probes > 0,
                "health_probes": probes,
                "health_probe_failures": probe_failures,
            }
        )
    return rows
