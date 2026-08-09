# CUI // SP-CTI
"""spec_generator.py — ACF concept -> feature spec markdown + canvas contract (acf-design-01).

The fifth stage of the ACF loop (harvest -> synth -> novelty-gate -> score -> CoD ->
**spec_generator** -> task_graph -> seeder). It turns one approved capability *concept*
(a ``foundry_concepts`` row produced by the synthesizer, acf-synth-03) into two artifacts:

  * ``spec_md`` — a human-readable feature spec (problem, proposed capability, target
    users, architecture sketch, DB tables, routes, success criteria). Mirrors the
    template approach in ``tools/creative/spec_generator`` + ``tools/innovation/
    solution_generator``: a deterministic Python string template, air-gap safe, with an
    OPTIONAL LLM-assist that enriches prose and NEVER blocks (falls back to the template).

  * ``canvas_contract`` — the structured dict the task-graph templater (acf-graph) consumes
    to parameterize the canonical canvas-epic skeleton::

        {slug, title, env_flag, tables[], modules[], routes[],
         iqe_collections[], needs_mcp, needs_reflex}

    Every field is derived deterministically from the concept so two runs over the same
    concept yield the same contract. The contract always carries >=1 table and >=1 route
    (the 8-component canvas gate requires both), so the downstream templater can always
    emit a buildable epic.

Both artifacts are persisted append-only to ``foundry_specs`` (concept_id, spec_md,
canvas_contract JSON, task_count) via the RLS-aware ``get_connection()`` with the caller's
tenant_id / classification stamped.

Public API
----------
    build_canvas_contract(concept, *, config=None) -> dict
    generate_spec(concept, *, config=None, conn=None, persist=True) -> dict

CLI
---
    python tools/foundry/spec_generator.py --concept-json '{...}' --json
    python tools/foundry/spec_generator.py --concept-id 7 --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

try:  # optional — only needed to read args/foundry_config.yaml
    import yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover - yaml is a core dep but stay defensive
    yaml = None  # type: ignore
    _HAS_YAML = False




# =========================================================================
# PATH / CONFIG
# =========================================================================
def _find_repo_root() -> Path:
    """Anchor on a marker that exists only at the true repo root (handles the
    tools/ + icdev/tools/ mirror)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CLAUDE.md").exists() or (parent / "goals" / "manifest.md").exists():
            return parent
    return here.parent.parent.parent


BASE_DIR = _find_repo_root()
CONFIG_PATH = BASE_DIR / "args" / "foundry_config.yaml"

# Defaults for the ``spec`` section of args/foundry_config.yaml. Deterministic-first:
# llm_assist OFF so the cycle stays air-gap safe until explicitly enabled.
_DEFAULT_SPEC_CFG: dict[str, Any] = {
    "llm_assist": {"enabled": False, "function": "code_generation", "max_tokens": 600},
    # Heuristic keyword sets that flip the optional contract booleans / extra tables.
    "mcp_keywords": [
        "agent", "tool", "orchestrat", "integrat", "automat", "api",
        "pipeline", "workflow", "mcp",
    ],
    "reflex_keywords": [
        "continuous", "monitor", "schedule", "periodic", "autonomous",
        "detect", "watch", "poll", "recurring", "drift", "anomaly",
    ],
}


def _load_config(config: Optional[dict]) -> dict:
    """Return the ``spec`` config section merged over ``_DEFAULT_SPEC_CFG``.

    ``config`` may be either the full foundry_config dict or just its ``spec`` block;
    both are accepted so callers can pass whatever they already hold.
    """
    if config is not None:
        section = config.get("spec", config) if isinstance(config, dict) else {}
    elif _HAS_YAML and CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open(encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
            section = doc.get("spec", {}) if isinstance(doc, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("foundry_config.yaml unreadable (%s); using spec defaults", exc)
            section = {}
    else:
        section = {}

    merged = dict(_DEFAULT_SPEC_CFG)
    if isinstance(section, dict):
        for key, val in section.items():
            if key == "llm_assist" and isinstance(val, dict):
                la = dict(_DEFAULT_SPEC_CFG["llm_assist"])
                la.update(val)
                merged["llm_assist"] = la
            else:
                merged[key] = val
    return merged


def _caller_context() -> tuple[str, str]:
    """(tenant_id, classification) from the active security context, defaults otherwise."""
    try:
        from tools.security.security_context import get_security_context

        ctx = get_security_context()
    except Exception:
        ctx = None
    tenant_id = (getattr(ctx, "tenant_id", None) or "default") if ctx else "default"
    classification = (getattr(ctx, "classification", None) or "CUI") if ctx else "CUI"
    return tenant_id, classification


# =========================================================================
# DETERMINISTIC DERIVATION
# =========================================================================
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WORD_RE = re.compile(r"[a-z0-9]+")
# Words that make poor table-prefix initials / domain nouns.
_PREFIX_STOP = frozenset(
    """a an the and or of to for in on with from by as at is are be that this new
    auto autonomous ai ml engine canvas tool system platform capability capabilities
    feature features service services manager management module modules""".split()
)


def _slugify(text: str, fallback: str = "capability") -> str:
    """Lowercase, collapse non-alphanumerics to single hyphens. Canvas-slug shape."""
    s = _SLUG_RE.sub("-", str(text or "").strip().lower()).strip("-")
    return s or fallback


def _table_prefix(slug: str, name: str) -> str:
    """Short snake-case table prefix (e.g. 'smart-contract-auditor' -> 'sca').

    Built from the initials of the slug's significant words; falls back to the
    first alphanumerics of the slug when initials are too short. Canvas tables use
    a compact prefix (aac_/dsoc_/ccc_) — this mirrors that convention.
    """
    words = [w for w in _WORD_RE.findall(slug.replace("-", " ")) if w not in _PREFIX_STOP]
    if not words:
        words = _WORD_RE.findall(slug.replace("-", " "))
    initials = "".join(w[0] for w in words if w)
    if len(initials) >= 2:
        prefix = initials[:5]
    else:
        flat = re.sub(r"[^a-z0-9]", "", slug)
        prefix = (flat[:4] or "cap")
    # Snake-safe, never start with a digit (valid SQL identifier).
    if prefix and prefix[0].isdigit():
        prefix = f"c{prefix}"
    return prefix


def _env_flag(slug: str) -> str:
    """Canvas feature flag, e.g. 'smart-contract-auditor' -> ICDEV_SMART_CONTRACT_AUDITOR_ENABLED."""
    return "ICDEV_" + re.sub(r"[^A-Z0-9]+", "_", slug.upper()).strip("_") + "_ENABLED"


def _concept_text(concept: dict[str, Any]) -> str:
    """All capability-bearing prose for keyword heuristics."""
    parts = [
        concept.get("name"),
        concept.get("proposed_capability"),
        concept.get("problem_statement"),
        concept.get("target_users"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _matches_any(text: str, keywords: list[str]) -> bool:
    return any(kw.lower() in text for kw in keywords if kw)


def _derive_tables(prefix: str, *, needs_reflex: bool) -> list[dict[str, Any]]:
    """Always at least the primary entity + an append-only event log.

    A reflex-driven capability also gets a ``_runs`` table to record each cycle.
    """
    tables: list[dict[str, Any]] = [
        {"name": f"{prefix}_items", "append_only": False, "purpose": "Primary domain records"},
        {"name": f"{prefix}_events", "append_only": True, "purpose": "Append-only event / audit log"},
    ]
    if needs_reflex:
        tables.append(
            {"name": f"{prefix}_runs", "append_only": True, "purpose": "Per-cycle run history (reflex)"}
        )
    return tables


def _derive_modules(slug: str, prefix: str, *, needs_mcp: bool, needs_reflex: bool) -> list[dict[str, str]]:
    """The Python modules the canvas-epic skeleton must produce."""
    modules: list[dict[str, str]] = [
        {"path": f"tools/{prefix}/constants.py", "purpose": "Enumerated constants driving SQL CHECKs"},
        {"path": f"tools/{prefix}/db/init_db.py", "purpose": "PG-first dual schema, idempotent init_db()"},
        {"path": f"tools/{prefix}/{prefix}_engine.py", "purpose": "Core capability logic"},
        {"path": f"tools/{prefix}/blueprint.py", "purpose": "Flask blueprint — page + API routes"},
    ]
    if needs_mcp:
        modules.append(
            {"path": f"tools/{prefix}/mcp_adapter.py", "purpose": "MCP tool registration for the gateway"}
        )
    if needs_reflex:
        modules.append(
            {"path": f"tools/genesis/reflexes/{prefix}_cycle.py", "purpose": "Genesis reflex — periodic cycle"}
        )
    return modules


def _derive_routes(slug: str, prefix: str) -> list[dict[str, str]]:
    """Always the canvas page, a primary API endpoint, and the IQE query route.

    The 8-component canvas gate requires a page route + IQE integration, so these
    three are the minimum a buildable contract must carry.
    """
    return [
        {"method": "GET", "path": f"/{slug}", "kind": "page", "purpose": "Canvas dashboard page"},
        {"method": "POST", "path": f"/api/{prefix}/items", "kind": "api", "purpose": "Create / query domain records"},
        {"method": "POST", "path": "/api/iqe-query", "kind": "iqe", "purpose": "IQE natural-language query widget"},
    ]


def _derive_iqe_collections(prefix: str) -> list[str]:
    """IQE collection names the adapter registers (one per queryable table)."""
    return [f"{prefix}_items", f"{prefix}_events"]


def _estimate_task_count(contract: dict[str, Any]) -> int:
    """Rough count of atomic kanban tasks the task-graph templater will emit.

    The real count is computed by acf-graph; this is a persisted estimate so the
    spec row and Home roadmap card have a number before the graph runs. Base = the
    8 canvas-completeness components; plus one task per extra table / module / route
    / optional subsystem beyond the baseline already covered by those components.
    """
    base = 8  # the 8-component new-canvas completeness gate
    extra_tables = max(0, len(contract.get("tables", [])) - 2)
    extra_routes = max(0, len(contract.get("routes", [])) - 3)
    extra_modules = max(0, len(contract.get("modules", [])) - 4)
    optional = int(bool(contract.get("needs_mcp"))) + int(bool(contract.get("needs_reflex")))
    return base + extra_tables + extra_routes + extra_modules + optional


def build_canvas_contract(concept: dict[str, Any], *, config: Optional[dict] = None) -> dict[str, Any]:
    """Derive the structured canvas contract the task-graph templater consumes.

    Pure / deterministic: same concept -> same contract. Guarantees >=1 table and
    >=1 route so the downstream skeleton is always buildable.
    """
    cfg = _load_config(config)
    name = str(concept.get("name") or "Untitled Capability").strip()
    slug = _slugify(concept.get("slug") or name)
    prefix = _table_prefix(slug, name)

    text = _concept_text(concept)
    needs_mcp = _matches_any(text, list(cfg.get("mcp_keywords", [])))
    needs_reflex = _matches_any(text, list(cfg.get("reflex_keywords", [])))

    contract = {
        "slug": slug,
        "title": name,
        "env_flag": _env_flag(slug),
        "tables": _derive_tables(prefix, needs_reflex=needs_reflex),
        "modules": _derive_modules(slug, prefix, needs_mcp=needs_mcp, needs_reflex=needs_reflex),
        "routes": _derive_routes(slug, prefix),
        "iqe_collections": _derive_iqe_collections(prefix),
        "needs_mcp": needs_mcp,
        "needs_reflex": needs_reflex,
    }
    return contract


# =========================================================================
# SPEC MARKDOWN TEMPLATE
# =========================================================================
_SPEC_TEMPLATE = """# Feature Spec: {title}

CUI // SP-CTI

> Autonomously drafted by the ICDEV Capability Foundry (ACF). Concept slug `{slug}`.

## Problem Statement
{problem_statement}

## Proposed Capability
{proposed_capability}

## Target Users
{target_users}

## Concept Scorecard
| Dimension | Score |
|-----------|-------|
| Novelty | {novelty_score} |
| Market | {market_score} |
| Fit | {fit_score} |
| Effort (cost) | {effort_estimate} |
| Compliance risk (cost) | {compliance_risk} |
| **Composite** | **{composite_score}** |

## Architecture Sketch
{architecture_sketch}

- **Feature flag:** `{env_flag}`
- **MCP gateway integration:** {needs_mcp}
- **Genesis reflex (periodic cycle):** {needs_reflex}

## DB Tables
{tables_section}

## Routes
{routes_section}

## Modules
{modules_section}

## IQE Collections
{iqe_section}

## Success Criteria
{success_section}
"""


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _default_architecture_sketch(concept: dict[str, Any], contract: dict[str, Any]) -> str:
    """Deterministic architecture narrative assembled from the contract."""
    n_tables = len(contract["tables"])
    n_routes = len(contract["routes"])
    pieces = [
        f"A new ICDEV canvas at `/{contract['slug']}`, gated by `{contract['env_flag']}`, "
        f"backed by {n_tables} RLS-aware table(s) and {n_routes} route(s).",
        "Core logic lives in a deterministic engine module; the Flask blueprint renders the "
        "dashboard page and exposes the JSON API. All persistence goes through the RLS-aware "
        "`get_connection()` with tenant_id / classification stamped per row.",
    ]
    if contract["needs_mcp"]:
        pieces.append("Exposed to agents via an MCP adapter registered in the gateway tool registry.")
    if contract["needs_reflex"]:
        pieces.append("Driven on a cadence by a Genesis reflex that runs one cycle per interval.")
    return " ".join(pieces)


def _render_list(items: list[Any], render) -> str:
    rendered = [render(it) for it in items]
    return "\n".join(rendered) if rendered else "_None._"


def _render_tables(tables: list[dict]) -> str:
    def _row(t: dict) -> str:
        flag = " (append-only)" if t.get("append_only") else ""
        return f"- `{t['name']}`{flag} — {t.get('purpose', '')}"

    return _render_list(tables, _row)


def _render_routes(routes: list[dict]) -> str:
    def _row(r: dict) -> str:
        return f"- `{r.get('method', 'GET')} {r['path']}` ({r.get('kind', 'api')}) — {r.get('purpose', '')}"

    return _render_list(routes, _row)


def _render_modules(modules: list[dict]) -> str:
    return _render_list(modules, lambda m: f"- `{m['path']}` — {m.get('purpose', '')}")


def _render_success_criteria(concept: dict[str, Any], contract: dict[str, Any]) -> str:
    """Deterministic, checkable acceptance criteria tied to the contract + house rules."""
    first_table = contract["tables"][0]["name"]
    page_route = next((r["path"] for r in contract["routes"] if r.get("kind") == "page"), f"/{contract['slug']}")
    criteria = [
        f"1. The canvas page `{page_route}` renders without error behind the `{contract['env_flag']}` flag.",
        f"2. Each of the {len(contract['tables'])} declared table(s) is created idempotently "
        f"(e.g. `{first_table}`) and all reads/writes go through `get_connection()` (RLS-aware).",
        "3. All append-only tables are registered in `.claude/hooks/pre_tool_use.py` "
        "(APPEND_ONLY_TABLES) and never UPDATE/DELETE-d.",
        "4. The 8-component new-canvas completeness gate passes (template + icdev/ mirror + route + "
        "module + constants + migration + nav link + IQE integration).",
        "5. Generated artifacts carry CUI // SP-CTI markings and every capability event is audit-logged.",
        "6. `coherence_checker.py --all --gate` and the security gates pass before the canvas is merged.",
    ]
    return "\n".join(criteria)


def _maybe_llm_enrich(
    concept: dict[str, Any], contract: dict[str, Any], cfg: dict[str, Any]
) -> dict[str, str]:
    """Optionally enrich narrative prose via the LLM Router. Never blocks.

    Returns a dict of section overrides ({} on any failure / when disabled). The
    deterministic template is always usable on its own — this only sharpens prose
    when a provider is explicitly enabled AND available (air-gap safe).
    """
    la = cfg.get("llm_assist", {}) or {}
    if not la.get("enabled") or os.environ.get("ICDEV_NO_LLM"):
        return {}
    try:
        try:
            from icdev.tools.llm.router import LLMRouter  # type: ignore
        except Exception:
            from tools.llm.router import LLMRouter  # type: ignore

        router = LLMRouter()
        provider, model_id, _ = router.get_provider_for_function(
            la.get("function", "code_generation")
        )
        if provider is None:
            return {}
        prompt = (
            "You are drafting a concise architecture sketch for a new internal software "
            "capability. Given the concept below, write 2-3 sentences describing how it should "
            "be built as an ICDEV canvas (engine module + Flask blueprint + RLS-aware tables). "
            "Output ONLY the prose, no headings.\n\n"
            f"Name: {concept.get('name')}\n"
            f"Capability: {concept.get('proposed_capability')}\n"
            f"Problem: {concept.get('problem_statement')}\n"
            f"Tables: {[t['name'] for t in contract['tables']]}\n"
            f"Routes: {[r['path'] for r in contract['routes']]}\n"
        )
        out = provider.complete(
            model_id=model_id, prompt=prompt, max_tokens=int(la.get("max_tokens", 600))
        )
        text = (out or "").strip()
        if text:
            return {"architecture_sketch": text}
    except Exception as exc:  # noqa: BLE001 - LLM-assist must never break the spec
        logger.debug("spec LLM-assist skipped: %s", exc)
    return {}


def render_spec_md(concept: dict[str, Any], contract: dict[str, Any], *, config: Optional[dict] = None) -> str:
    """Render the feature-spec markdown from a concept + its canvas contract."""
    cfg = _load_config(config)
    overrides = _maybe_llm_enrich(concept, contract, cfg)
    architecture = overrides.get("architecture_sketch") or _default_architecture_sketch(concept, contract)

    return _SPEC_TEMPLATE.format(
        title=contract["title"],
        slug=contract["slug"],
        problem_statement=str(concept.get("problem_statement") or "_Not specified._"),
        proposed_capability=str(concept.get("proposed_capability") or "_Not specified._"),
        target_users=str(concept.get("target_users") or "_Not specified._"),
        novelty_score=_fmt_score(concept.get("novelty_score")),
        market_score=_fmt_score(concept.get("market_score")),
        fit_score=_fmt_score(concept.get("fit_score")),
        effort_estimate=_fmt_score(concept.get("effort_estimate")),
        compliance_risk=_fmt_score(concept.get("compliance_risk")),
        composite_score=_fmt_score(concept.get("composite_score")),
        architecture_sketch=architecture,
        env_flag=contract["env_flag"],
        needs_mcp="yes" if contract["needs_mcp"] else "no",
        needs_reflex="yes" if contract["needs_reflex"] else "no",
        tables_section=_render_tables(contract["tables"]),
        routes_section=_render_routes(contract["routes"]),
        modules_section=_render_modules(contract["modules"]),
        iqe_section=_render_list(contract["iqe_collections"], lambda c: f"- `{c}`"),
        success_section=_render_success_criteria(concept, contract),
    )


# =========================================================================
# PERSISTENCE
# =========================================================================
_INSERT_SQL = (
    "INSERT INTO foundry_specs "
    "(concept_id, spec_md, canvas_contract, task_count, tenant_id, classification) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def _persist(concept_id: Any, spec_md: str, contract: dict, task_count: int, conn: Any) -> Optional[int]:
    """Append the spec to foundry_specs. Returns the new row id (best-effort)."""
    tenant_id, classification = _caller_context()
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        cur = conn.execute(
            _INSERT_SQL,
            (
                concept_id,
                spec_md,
                json.dumps(contract),
                task_count,
                tenant_id,
                classification,
            ),
        )
        conn.commit()
        return getattr(cur, "lastrowid", None)
    finally:
        if own_conn:
            conn.close()


# =========================================================================
# PUBLIC: generate_spec
# =========================================================================
def generate_spec(
    concept: dict[str, Any],
    *,
    config: Optional[dict] = None,
    conn: Any = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Turn one capability *concept* into ``{spec_md, canvas_contract}`` and persist it.

    Args:
        concept: a foundry_concepts-shaped dict (name, slug, problem_statement,
            proposed_capability, target_users, score columns, optional ``id``).
        config: optional foundry_config dict (or its ``spec`` block) override.
        conn: optional open DB connection (caller manages its lifecycle).
        persist: when True (default) and the concept has an ``id``, append the spec
            to ``foundry_specs``. A concept without an id is rendered but not persisted.

    Returns:
        ``{spec_md, canvas_contract, task_count, concept_id, spec_id, persisted}``.
        ``canvas_contract`` always has >=1 table and >=1 route.
    """
    contract = build_canvas_contract(concept, config=config)
    spec_md = render_spec_md(concept, contract, config=config)
    task_count = _estimate_task_count(contract)

    concept_id = concept.get("id")
    spec_id: Optional[int] = None
    persisted = False
    if persist and concept_id is not None:
        try:
            init_db()  # idempotent — guarantees foundry_specs exists
            spec_id = _persist(concept_id, spec_md, contract, task_count, conn)
            persisted = True
        except Exception as exc:  # noqa: BLE001 - persistence failure must not lose the artifact
            logger.warning("foundry_specs persist failed for concept %s: %s", concept_id, exc)

    logger.info(
        "spec generated concept=%s slug=%s tables=%d routes=%d tasks~%d%s",
        concept_id,
        contract["slug"],
        len(contract["tables"]),
        len(contract["routes"]),
        task_count,
        " (persisted)" if persisted else "",
    )
    return {
        "spec_md": spec_md,
        "canvas_contract": contract,
        "task_count": task_count,
        "concept_id": concept_id,
        "spec_id": spec_id,
        "persisted": persisted,
    }


# =========================================================================
# CLI
# =========================================================================
def _load_concept_by_id(concept_id: str) -> Optional[dict]:
    """Read one concept row for the CLI (best-effort)."""
    try:
        from tools.db.storage import get_connection

        init_db()
        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT id, run_id, name, slug, problem_statement, proposed_capability, "
                "target_users, cluster_signal_ids, novelty_score, market_score, fit_score, "
                "effort_estimate, compliance_risk, composite_score, status "
                "FROM foundry_concepts WHERE id = %s",
                (concept_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            return dict(row)
        except (TypeError, ValueError):
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not load concept %s: %s", concept_id, exc)
        return None


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACF spec generator — concept -> spec markdown + canvas contract (CUI // SP-CTI)"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--concept-json", help="Concept draft as a JSON object")
    src.add_argument("--concept-id", help="Load the concept from foundry_concepts by id")
    parser.add_argument("--no-persist", action="store_true", help="Do not write to foundry_specs")
    parser.add_argument("--json", action="store_true", help="Emit JSON (default prints spec_md)")
    args = parser.parse_args(argv)

    if args.concept_json:
        try:
            concept = json.loads(args.concept_json)
        except json.JSONDecodeError as exc:
            parser.error(f"invalid --concept-json: {exc}")
            return 2
    else:
        concept = _load_concept_by_id(args.concept_id)
        if concept is None:
            print(json.dumps({"error": f"concept not found: {args.concept_id}"}))
            return 1

    result = generate_spec(concept, persist=not args.no_persist)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result["spec_md"])
        print("\n--- canvas_contract ---")
        print(json.dumps(result["canvas_contract"], indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    raise SystemExit(_main())

from tools.foundry.db.init_db import init_db  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.foundry.spec_generator")
