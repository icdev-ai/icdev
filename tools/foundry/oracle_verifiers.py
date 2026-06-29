# CUI // SP-CTI
"""oracle_verifiers.py — Oracle-style deterministic concept-validation verifiers (acf-ada-03).

Adapts the lens-specific deterministic verifiers in
``tools/genesis/reflexes/oracle_triage.py`` from *kanban-task triage* to *ACF
concept validation*. Where oracle_triage decides promote/dismiss for a suggested
task, these verifiers answer a sharper question about a freshly-synthesized ACF
concept: *does this concept's claimed shape line up with what the codebase
actually contains?* — using nothing but file reads + regex (no LLM, no network,
air-gap safe, < 100 ms).

They **augment, not replace, ``novelty_gate.py``**. The novelty gate is a fuzzy
similarity score against the whole capability catalog; these are three sharp,
named, deterministic lenses, each returning a uniform verdict::

    {"lens": str, "passed": bool, "confidence": float, "detail": str}

Lenses
------
``concept_not_in_manifest``
    Does the concept's ``proposed_capability`` / ``name`` match an existing tool
    already registered in ``tools/manifest/*.md``?
      * match found  -> ``passed=False``  (concept duplicates a catalogued tool)
      * no match     -> ``passed=True``   (capability is genuinely net-new)

``route_not_listed``
    Are the page route(s) implied by the concept's ``canvas_contract`` already
    registered in the dashboard (``app.py`` ``@*.route`` decorators **or**
    ``args/component_registry.yaml`` canvas url_prefix entries)?
      * every page route registered  -> ``passed=True``
      * any page route missing       -> ``passed=False`` (route not yet listed)

``orphan_db_table``
    Does every table in the concept's ``canvas_contract.tables[]`` have a
    ``CREATE TABLE`` in ``tools/db/init_icdev_db.py``?
      * every table has a CREATE TABLE  -> ``passed=True``
      * any table missing               -> ``passed=False`` (orphan table)

``passed`` is a neutral, deterministic signal — the ACF deliberator decides what
to do with it. ``confidence`` is the strength of the evidence behind the verdict
(0..1). ``detail`` is a one-line human-readable explanation.

Public API
----------
    concept_not_in_manifest(concept, *, manifest_dir=None) -> dict
    route_not_listed(concept, *, app_path=None) -> dict
    orphan_db_table(concept, *, init_path=None) -> dict
    verify_concept(concept, **paths) -> list[dict]   # run all three

CLI
---
    python tools/foundry/oracle_verifiers.py --concept-json '{...}' --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# --- reuse the deterministic helpers already proven in novelty_gate -----------
# Same package; the module is mirrored into both tools/ and icdev/tools/ so try
# both import roots (mirrors novelty_gate's own dual-root pattern).
try:  # pragma: no cover - import plumbing
    from tools.foundry.novelty_gate import (
        _jaccard,
        _parse_markdown_table_rows,
        _tokenize,
    )
except Exception:  # pragma: no cover
    from icdev.tools.foundry.novelty_gate import (  # type: ignore
        _jaccard,
        _parse_markdown_table_rows,
        _tokenize,
    )


# =========================================================================
# PATH / REPO ROOT
# =========================================================================
def _find_repo_root() -> Path:
    """Walk up to the repo root, anchored on a marker present only there."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "goals" / "manifest.md").exists() or (parent / "CLAUDE.md").exists():
            return parent
    return here.parent.parent.parent


BASE_DIR = _find_repo_root()

_DEFAULT_MANIFEST_DIR = BASE_DIR / "tools" / "manifest"
_DEFAULT_APP_PATH = BASE_DIR / "tools" / "dashboard" / "app.py"
_DEFAULT_INIT_PATH = BASE_DIR / "tools" / "db" / "init_icdev_db.py"

# Lens names (uniform with the oracle_triage lens vocabulary).
LENS_CONCEPT_NOT_IN_MANIFEST = "concept_not_in_manifest"
LENS_ROUTE_NOT_LISTED = "route_not_listed"
LENS_ORPHAN_DB_TABLE = "orphan_db_table"

# A capability is treated as a manifest duplicate at/above this blended score.
_MANIFEST_MATCH_THRESHOLD = 0.45


# =========================================================================
# REGEX (mirrors oracle_triage's deterministic patterns)
# =========================================================================
_ROUTE_DECORATOR_RE = re.compile(r"""@\w+\.route\(\s*["']([^"']+)["']""", re.MULTILINE)
# First element of each (key, env, mod, attr) tuple inside the _CANVAS_DEFS list.
_CANVAS_DEF_KEY_RE = re.compile(r"""^\s*\(\s*["'](\w+)["']\s*,""", re.MULTILINE)
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+[\"'`]?(\w+)", re.IGNORECASE
)


# =========================================================================
# FILE PARSE CACHES (keyed by path + stat → invalidates when a fixture changes)
# =========================================================================
_MANIFEST_CACHE: dict[tuple, list[tuple[str, list[str]]]] = {}
_ROUTES_CACHE: dict[tuple, set[str]] = {}
_TABLES_CACHE: dict[tuple, set[str]] = {}


def _stat_key(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), 0, 0)


def clear_caches() -> None:
    """Reset all parse caches (used by tests that mutate fixtures in place)."""
    _MANIFEST_CACHE.clear()
    _ROUTES_CACHE.clear()
    _TABLES_CACHE.clear()


# =========================================================================
# CONCEPT HELPERS
# =========================================================================
def _concept_text(concept: dict[str, Any]) -> str:
    """Capability-bearing prose, capability/name weighted first."""
    parts = [
        str(concept.get("proposed_capability", "")),
        str(concept.get("name", "")),
        str(concept.get("problem_statement", "")),
    ]
    return " ".join(p for p in parts if p).strip()


def _concept_contract(concept: dict[str, Any]) -> dict[str, Any]:
    """Return the concept's canvas_contract, deriving one on the fly if absent.

    The deliberator usually has the contract already (spec_generator stamps it);
    when it doesn't, derive deterministically so the route / table lenses still
    have something concrete to check.
    """
    contract = concept.get("canvas_contract")
    if isinstance(contract, dict) and contract:
        return contract
    try:  # derive deterministically — same concept → same contract
        try:
            from tools.foundry.spec_generator import build_canvas_contract
        except Exception:
            from icdev.tools.foundry.spec_generator import build_canvas_contract  # type: ignore
        return build_canvas_contract(concept)
    except Exception:
        return {}


def _norm_route(path: str) -> str:
    """Normalize a route: strip trailing slash, collapse <param> tail, lowercase."""
    base = str(path or "").split("<")[0].rstrip("/")
    return (base or "/").lower()


# =========================================================================
# PARSERS
# =========================================================================
def _load_manifest_entries(manifest_dir: Path) -> list[tuple[str, list[str]]]:
    """Return [(tool_name, tokens), ...] parsed from every manifest shard.

    ``tokens`` covers the tool name + its description so a capability phrase that
    paraphrases the description still matches.
    """
    key = (str(manifest_dir),) + tuple(
        sorted(_stat_key(p) for p in manifest_dir.glob("*.md"))
    ) if manifest_dir.is_dir() else (str(manifest_dir),)
    cached = _MANIFEST_CACHE.get(key)
    if cached is not None:
        return cached

    entries: list[tuple[str, list[str]]] = []
    if manifest_dir.is_dir():
        for md in sorted(manifest_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            for cells in _parse_markdown_table_rows(text):
                if len(cells) < 2:
                    continue
                name = cells[0].strip().strip("`*")
                if not name or name.lower() in ("tool", "name", "module"):
                    continue
                desc = cells[2] if len(cells) > 2 and cells[2] else max(cells[1:], key=len, default="")
                entries.append((name, _tokenize(f"{name} {desc}")))
    _MANIFEST_CACHE[key] = entries
    return entries


def _load_registered_routes(app_path: Path) -> set[str]:
    """Return the set of normalized routes the dashboard registers.

    Sourced from explicit ``@*.route(...)`` decorators in app.py **and** from the
    component registry's canvas url_prefix entries. The registry replaces the
    legacy hardcoded ``_CANVAS_DEFS`` list.
    """
    key = _stat_key(app_path)
    cached = _ROUTES_CACHE.get(key)
    if cached is not None:
        return cached

    routes: set[str] = set()
    try:
        src = app_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _ROUTES_CACHE[key] = routes
        return routes

    for m in _ROUTE_DECORATOR_RE.finditer(src):
        routes.add(_norm_route(m.group(1)))

    # Registry-driven canvas url_prefixes (replaces _CANVAS_DEFS parsing).
    try:
        repo_root = app_path.resolve().parent.parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from tools.config.component_registry import get_registry

        registry = get_registry()
        for canvas_key, prefix in registry.get_url_prefixes().items():
            if prefix:
                routes.add(_norm_route(prefix))
            else:
                # Empty url_prefix means the blueprint defines its own routes;
                # fall back to the legacy convention of /<canvas_key>.
                routes.add(_norm_route(f"/{canvas_key}"))
    except Exception:
        # If the registry is unavailable, decorator-derived routes still give
        # a partial answer; fail soft rather than blocking the verifier.
        pass

    _ROUTES_CACHE[key] = routes
    return routes


def _load_created_tables(init_path: Path) -> set[str]:
    """Return the set of table names with a ``CREATE TABLE`` in init_icdev_db.py."""
    key = _stat_key(init_path)
    cached = _TABLES_CACHE.get(key)
    if cached is not None:
        return cached

    tables: set[str] = set()
    try:
        src = init_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _TABLES_CACHE[key] = tables
        return tables
    for m in _CREATE_TABLE_RE.finditer(src):
        tables.add(m.group(1).lower())
    _TABLES_CACHE[key] = tables
    return tables


# =========================================================================
# VERIFIER 1 — concept_not_in_manifest
# =========================================================================
def concept_not_in_manifest(
    concept: dict[str, Any], *, manifest_dir: Optional[Path] = None
) -> dict[str, Any]:
    """Fail when the concept's capability matches an existing manifest tool.

    ``passed=True``  → no manifest entry resembles the capability (net-new).
    ``passed=False`` → a catalogued tool already covers this capability.
    """
    mdir = Path(manifest_dir) if manifest_dir is not None else _DEFAULT_MANIFEST_DIR
    entries = _load_manifest_entries(mdir)
    concept_tokens = _tokenize(_concept_text(concept))
    concept_set = set(concept_tokens)

    if not concept_tokens or not entries:
        return {
            "lens": LENS_CONCEPT_NOT_IN_MANIFEST,
            "passed": True,
            "confidence": 0.0,
            "detail": (
                "Empty concept capability — nothing to dedupe"
                if not concept_tokens
                else f"No manifest entries found under {mdir} — cannot match"
            ),
        }

    best_name = ""
    best_score = 0.0
    for name, entry_tokens in entries:
        if not entry_tokens:
            continue
        score = _jaccard(concept_set, set(entry_tokens))
        # Name-containment boost: the whole tool name appears in the concept's
        # capability tokens → treat as a near-certain duplicate.
        name_tokens = _tokenize(name)
        if name_tokens and set(name_tokens) <= concept_set:
            score = max(score, 0.9)
        if score > best_score:
            best_score, best_name = score, name

    matched = best_score >= _MANIFEST_MATCH_THRESHOLD
    if matched:
        return {
            "lens": LENS_CONCEPT_NOT_IN_MANIFEST,
            "passed": False,
            "confidence": round(best_score, 4),
            "detail": (
                f"Capability matches existing manifest tool {best_name!r} "
                f"(similarity {best_score:.2f} >= {_MANIFEST_MATCH_THRESHOLD}) — duplicate"
            ),
        }
    return {
        "lens": LENS_CONCEPT_NOT_IN_MANIFEST,
        "passed": True,
        "confidence": round(1.0 - best_score, 4),
        "detail": (
            f"No manifest tool matches (nearest {best_name!r} at {best_score:.2f} "
            f"< {_MANIFEST_MATCH_THRESHOLD}) — capability is net-new"
        ),
    }


# =========================================================================
# VERIFIER 2 — route_not_listed
# =========================================================================
def route_not_listed(
    concept: dict[str, Any], *, app_path: Optional[Path] = None
) -> dict[str, Any]:
    """Fail when a page route implied by the contract is missing from the dashboard.

    ``passed=True``  → every page route is registered in app.py / _CANVAS_DEFS.
    ``passed=False`` → at least one page route is not yet listed.
    """
    apath = Path(app_path) if app_path is not None else _DEFAULT_APP_PATH
    registered = _load_registered_routes(apath)
    contract = _concept_contract(concept)

    # Page routes from the contract; fall back to /<slug> if none are tagged page.
    page_routes = [
        r.get("path", "")
        for r in contract.get("routes", [])
        if isinstance(r, dict) and r.get("kind") == "page" and r.get("path")
    ]
    if not page_routes and contract.get("slug"):
        page_routes = [f"/{contract['slug']}"]

    if not page_routes:
        return {
            "lens": LENS_ROUTE_NOT_LISTED,
            "passed": True,
            "confidence": 0.0,
            "detail": "Concept implies no dashboard page route — nothing to check",
        }

    def _is_listed(path: str) -> bool:
        nr = _norm_route(path)
        if nr in registered:
            return True
        # prefix match: registered "/foo" should satisfy "/foo/bar"
        return any(nr == r or nr.startswith(r + "/") for r in registered if r != "/")

    missing = [p for p in page_routes if not _is_listed(p)]
    total = len(page_routes)
    if missing:
        return {
            "lens": LENS_ROUTE_NOT_LISTED,
            "passed": False,
            "confidence": round(len(missing) / total, 4),
            "detail": (
                f"{len(missing)}/{total} page route(s) not registered in app.py/component_registry: "
                f"{', '.join(missing)}"
            ),
        }
    return {
        "lens": LENS_ROUTE_NOT_LISTED,
        "passed": True,
        "confidence": 1.0,
        "detail": f"All {total} page route(s) already registered: {', '.join(page_routes)}",
    }


# =========================================================================
# VERIFIER 3 — orphan_db_table
# =========================================================================
def orphan_db_table(
    concept: dict[str, Any], *, init_path: Optional[Path] = None
) -> dict[str, Any]:
    """Fail when a contract table has no CREATE TABLE in init_icdev_db.py.

    ``passed=True``  → every declared table has a CREATE TABLE.
    ``passed=False`` → at least one declared table is an orphan.
    """
    ipath = Path(init_path) if init_path is not None else _DEFAULT_INIT_PATH
    created = _load_created_tables(ipath)
    contract = _concept_contract(concept)

    tables = [
        str(t.get("name", "")).strip()
        for t in contract.get("tables", [])
        if isinstance(t, dict) and t.get("name")
    ]
    if not tables:
        return {
            "lens": LENS_ORPHAN_DB_TABLE,
            "passed": True,
            "confidence": 0.0,
            "detail": "Concept declares no tables — nothing to check",
        }

    orphans = [t for t in tables if t.lower() not in created]
    total = len(tables)
    if orphans:
        return {
            "lens": LENS_ORPHAN_DB_TABLE,
            "passed": False,
            "confidence": round(len(orphans) / total, 4),
            "detail": (
                f"{len(orphans)}/{total} table(s) have no CREATE TABLE in "
                f"{ipath.name}: {', '.join(orphans)}"
            ),
        }
    return {
        "lens": LENS_ORPHAN_DB_TABLE,
        "passed": True,
        "confidence": 1.0,
        "detail": f"All {total} declared table(s) have a CREATE TABLE: {', '.join(tables)}",
    }


# =========================================================================
# RUN-ALL
# =========================================================================
def verify_concept(
    concept: dict[str, Any],
    *,
    manifest_dir: Optional[Path] = None,
    app_path: Optional[Path] = None,
    init_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Run all three lenses over *concept*; return their verdicts in order."""
    return [
        concept_not_in_manifest(concept, manifest_dir=manifest_dir),
        route_not_listed(concept, app_path=app_path),
        orphan_db_table(concept, init_path=init_path),
    ]


# =========================================================================
# CLI
# =========================================================================
def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACF Oracle-style deterministic concept verifiers"
    )
    parser.add_argument("--concept-json", required=True, help="Concept draft as a JSON object")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    try:
        concept = json.loads(args.concept_json)
    except json.JSONDecodeError as exc:
        parser.error(f"invalid --concept-json: {exc}")
        return 2

    results = verify_concept(concept)
    if args.json:
        print(json.dumps({"results": results}, default=str))
    else:
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"[{mark}] {r['lens']:24} conf={r['confidence']:.2f}  {r['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
