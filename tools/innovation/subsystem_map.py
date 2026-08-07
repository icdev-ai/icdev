#!/usr/bin/env python3
# CUI // SP-CTI
"""Which ICDEV subsystem does an external project benchmark? (xbm-cmp-01-d2)

The scout watchlist tags every external target with a ``subsystem``
(``context/genesis/competitors.yaml``, tagged by xbm-list-01). The ICDEV half of
each of those tags — where its code lives, which tables hold its data — is
declared in ``args/xbm_subsystem_inventory.yaml`` (xbm-cmp-01-d1). Nothing joined
the two, so a finding about `langfuse/langfuse` still had to be matched to
ICDEV's observability subsystem by a human reading two files.

This module is that join: the lookup table from a project identifier to its
subsystem tag, and from a subsystem tag to ICDEV's counterpart. It is the data
structure the verdict engine (d3) scores and the report (d4) renders; it
deliberately computes no verdict of its own beyond re-reporting the one
``args/innovation_promoter.yaml`` already declares.

Three design points, each of which is a bug if reversed:

1. **An ambiguous alias is an error, not a coin flip.** The watchlist carries
   both ``cortexproject/cortex`` (observability) and ``TheHive-Project/Cortex``
   (security ops) — a collision the benchmark map calls out by name, and a third
   Cortex (ICDEV's own) sits nearby. Resolving the bare name ``cortex`` raises
   rather than picking whichever entry the file lists first; a comparison routed
   to the wrong subsystem is worse than one that refuses to route.

2. **Every project is mapped, not just the adaptation candidates.**
   ``seed_external_repos.load_repos`` filters to entries carrying an
   ``adaptation`` block (14 of 56) because only those become signals. A
   comparison needs all of them, including the three untrackable commercial
   products, so the watchlist is re-read here rather than reused through that
   filter.

3. **The inventory is the key space.** The promoter config only knows the ten
   subsystems the benchmark map covered; five more predate it. Keying on the
   promoter would silently drop `autonomous_research`, `design_quality`,
   `llm_infrastructure`, `mlops` and `embedded` — so a tag with no declared
   verdict reports ``declared_verdict: None``, which is the honest answer.

Usage:
    python tools/innovation/subsystem_map.py --project langgraph --json
    python tools/innovation/subsystem_map.py --subsystem observability --json
    python tools/innovation/subsystem_map.py --all --json
    python tools/innovation/subsystem_map.py --validate     # cross-file integrity
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.innovation.icdev_evidence import InventoryError, load_inventory

# Checkout layout first, packaged layout second — the same convention
# seed_external_repos.py and icdev_evidence.py use for their own config.
WATCHLIST_CANDIDATES = (
    BASE_DIR / "context" / "genesis" / "competitors.yaml",
    BASE_DIR / "data" / "context" / "genesis" / "competitors.yaml",
)

PROMOTER_CANDIDATES = (
    BASE_DIR / "args" / "innovation_promoter.yaml",
    BASE_DIR / "data" / "args" / "innovation_promoter.yaml",
)

_GITHUB_PREFIXES = ("https://github.com/", "http://github.com/", "github.com/")


class SubsystemMapError(RuntimeError):
    """A config the map is built from could not be read.

    Raised rather than returning an empty map: a lookup table that silently
    contains nothing resolves every project to "unknown", which reads as a
    finding about the watchlist rather than as a broken read.
    """


class AmbiguousProjectError(LookupError):
    """One identifier, more than one subsystem.

    Carries the candidates so a caller can disambiguate with a full ``owner/name``
    slug instead of guessing.
    """

    def __init__(self, identifier: str, subsystems: List[str]):
        self.identifier = identifier
        self.subsystems = subsystems
        super().__init__(
            f"{identifier!r} is ambiguous across subsystems {', '.join(subsystems)}; "
            f"use the full owner/name slug"
        )


# ── loading ──────────────────────────────────────────────────────────────


def watchlist_path(candidates=WATCHLIST_CANDIDATES) -> Path:
    for path in candidates:
        if path.exists():
            return path
    raise SubsystemMapError(
        "no watchlist found; looked for: " + ", ".join(str(p) for p in candidates)
    )


def promoter_path(candidates=PROMOTER_CANDIDATES) -> Optional[Path]:
    """The promoter config, or None.

    Optional on purpose: it supplies the declared verdict and the ``icdev_surface``
    pointer, both of which are commentary on a mapping that stands without them.
    """
    for path in candidates:
        if path.exists():
            return path
    return None


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a hard dependency
        raise SubsystemMapError(f"pyyaml is required to read {path}: {exc}") from exc
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SubsystemMapError(f"{path} is not valid YAML: {exc}") from exc


def load_targets(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every watchlist target, including the ones with no ``adaptation`` block."""
    data = _read_yaml(path or watchlist_path())
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise SubsystemMapError(f"{path or watchlist_path()} declares no targets")
    return [t for t in targets if isinstance(t, dict)]


def load_benchmark_subsystems(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """``benchmark_subsystems`` from the promoter config; ``{}`` when absent."""
    path = path or promoter_path()
    if path is None:
        return {}
    declared = _read_yaml(path).get("benchmark_subsystems")
    return declared if isinstance(declared, dict) else {}


# ── project -> subsystem ─────────────────────────────────────────────────


def normalize_identifier(identifier: str) -> str:
    """Fold an identifier to its lookup form.

    A caller may hold a repo slug, a bare name, a display name or the URL off a
    signal row, so all four fold to the same key.

    >>> normalize_identifier("  Backstage/Backstage  ")
    'backstage/backstage'
    >>> normalize_identifier("https://github.com/langchain-ai/langgraph.git")
    'langchain-ai/langgraph'
    """
    text = (identifier or "").strip().lower()
    for prefix in _GITHUB_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    return text


def project_label(target: Dict[str, Any]) -> str:
    """How a target is named in output — its slug, else its display name."""
    return target.get("repo") or target.get("name") or ""


def project_aliases(target: Dict[str, Any]) -> List[str]:
    """Every identifier that should resolve this target, normalized.

    >>> project_aliases({"repo": "TheHive-Project/Cortex", "name": "TheHive Cortex"})
    ['thehive-project/cortex', 'cortex', 'thehive cortex']
    >>> project_aliases({"name": "Cortex.io", "url": "https://www.cortex.io/"})
    ['cortex.io']
    """
    slug = target.get("repo") or ""
    raw = [slug]
    if "/" in slug:
        raw.append(slug.split("/", 1)[1])
    raw.append(target.get("name") or "")
    raw.append(((target.get("adaptation") or {}).get("source")) or "")

    aliases: List[str] = []
    for candidate in raw:
        alias = normalize_identifier(candidate)
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def build_project_index(targets: Optional[List[Dict[str, Any]]] = None) -> Dict[str, List[str]]:
    """alias -> the subsystem tags claiming it, sorted.

    A single-element list is the normal case; a longer one is a genuine collision
    that :func:`subsystem_for_project` refuses to resolve.

    >>> index = build_project_index([
    ...     {"repo": "cortexproject/cortex", "subsystem": "observability"},
    ...     {"repo": "TheHive-Project/Cortex", "subsystem": "security_ops"},
    ... ])
    >>> index["cortexproject/cortex"]
    ['observability']
    >>> index["cortex"]
    ['observability', 'security_ops']
    """
    targets = load_targets() if targets is None else targets
    index: Dict[str, List[str]] = {}
    for target in targets:
        subsystem = (target.get("subsystem") or "").strip()
        if not subsystem:
            continue
        for alias in project_aliases(target):
            claimed = index.setdefault(alias, [])
            if subsystem not in claimed:
                claimed.append(subsystem)
    return {alias: sorted(tags) for alias, tags in index.items()}


def ambiguous_aliases(index: Optional[Dict[str, List[str]]] = None) -> Dict[str, List[str]]:
    """Aliases claimed by more than one subsystem."""
    index = build_project_index() if index is None else index
    return {alias: tags for alias, tags in index.items() if len(tags) > 1}


def subsystem_for_project(
    identifier: str,
    *,
    targets: Optional[List[Dict[str, Any]]] = None,
    index: Optional[Dict[str, List[str]]] = None,
) -> Optional[str]:
    """The subsystem tag an external project benchmarks, or None if unknown.

    Args:
        identifier: A repo slug, bare repo name, display name, GitHub URL or the
            ``adaptation.source`` key a signal was registered under.
        targets: Watchlist entries. Defaults to the shipped watchlist.
        index: A pre-built alias index, to avoid rebuilding it per lookup.

    Raises:
        AmbiguousProjectError: the identifier is claimed by more than one
            subsystem.

    >>> targets = [
    ...     {"repo": "langchain-ai/langgraph", "subsystem": "agent_runtime"},
    ...     {"repo": "langfuse/langfuse", "subsystem": "observability"},
    ...     {"name": "Cortex.io", "subsystem": "developer_portal"},
    ... ]
    >>> subsystem_for_project("langgraph", targets=targets)
    'agent_runtime'
    >>> subsystem_for_project("https://github.com/langfuse/langfuse", targets=targets)
    'observability'
    >>> subsystem_for_project("Cortex.io", targets=targets)
    'developer_portal'
    >>> subsystem_for_project("not-on-the-watchlist", targets=targets) is None
    True
    """
    if index is None:
        index = build_project_index(targets)
    tags = index.get(normalize_identifier(identifier))
    if not tags:
        return None
    if len(tags) > 1:
        raise AmbiguousProjectError(normalize_identifier(identifier), tags)
    return tags[0]


# ── subsystem -> ICDEV counterpart ───────────────────────────────────────


def icdev_counterpart(
    subsystem: str,
    *,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    benchmark: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """What ICDEV holds for a tag, merged from the inventory and the promoter.

    The inventory supplies the code and table patterns; the promoter supplies the
    verdict the benchmark map recorded and the surface it named. A tag the
    promoter never covered reports ``declared_verdict: None`` and
    ``benchmarked: False`` rather than being dropped.

    Raises:
        KeyError: the tag is not in the inventory — the same contract
            ``icdev_evidence.gather_subsystem_evidence`` uses, so a typo'd tag
            fails identically on both halves of the comparison.

    >>> inv = {"observability": {"title": "Observability / LLM telemetry",
    ...                          "section": 2, "modules": ["tools/observability/*.py"],
    ...                          "tables": ["otel_*"]}}
    >>> bench = {"observability": {"verdict": "ahead", "icdev_surface": "tools/observability/"}}
    >>> counterpart = icdev_counterpart("observability", inventory=inv, benchmark=bench)
    >>> counterpart["module_patterns"], counterpart["declared_verdict"]
    (['tools/observability/*.py'], 'ahead')
    >>> icdev_counterpart("mlops", inventory={"mlops": {}})["benchmarked"]
    False
    """
    inventory = load_inventory() if inventory is None else inventory
    if subsystem not in inventory:
        raise KeyError(
            f"subsystem {subsystem!r} is not in the inventory; "
            f"known: {', '.join(sorted(inventory))}"
        )
    spec = inventory[subsystem] or {}
    declared = (benchmark or {}).get(subsystem) or {}

    counterpart: Dict[str, Any] = {
        "subsystem": subsystem,
        "title": spec.get("title", subsystem),
        "benchmark_section": spec.get("section"),
        "module_patterns": list(spec.get("modules") or []),
        "table_patterns": list(spec.get("tables") or []),
        "benchmarked": bool(declared),
        "declared_verdict": declared.get("verdict"),
        "benchmarked_against": declared.get("benchmarked_against"),
        "icdev_surface": declared.get("icdev_surface"),
        "signal_categories": list(declared.get("categories") or []),
    }
    if spec.get("note"):
        counterpart["note"] = spec["note"]
    return counterpart


def _external_entry(target: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project": project_label(target),
        "repo": target.get("repo"),
        "name": target.get("name"),
        "url": target.get("url"),
        "category": target.get("category"),
        "trackable": target.get("trackable", True) is not False,
        "adaptation_candidate": target.get("adaptation") is not None,
        "aliases": project_aliases(target),
    }


def build_comparison_map(
    *,
    targets: Optional[List[Dict[str, Any]]] = None,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    benchmark: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The comparison data structure: external projects paired with ICDEV's side.

    Keyed on the inventory's tags, so a subsystem nobody benchmarks still appears
    with an empty ``external`` list — that emptiness is a finding about the
    watchlist and must not be hidden by keying on the watchlist instead.

    Projects whose tag is missing from the inventory, or which carry no tag at
    all, are collected under ``unmapped_projects`` rather than dropped.

    >>> result = build_comparison_map(
    ...     targets=[{"repo": "langfuse/langfuse", "subsystem": "observability"},
    ...              {"repo": "nobody/untagged"}],
    ...     inventory={"observability": {"title": "Observability / LLM telemetry"},
    ...                "embedded": {"title": "SparkPilot / RTOS"}},
    ...     benchmark={},
    ... )
    >>> result["subsystems"]["observability"]["external"][0]["project"]
    'langfuse/langfuse'
    >>> result["subsystems"]["embedded"]["external_count"]
    0
    >>> result["unmapped_projects"]
    [{'project': 'nobody/untagged', 'subsystem': None, 'reason': 'untagged'}]
    """
    targets = load_targets() if targets is None else targets
    inventory = load_inventory() if inventory is None else inventory
    benchmark = load_benchmark_subsystems() if benchmark is None else benchmark

    subsystems: Dict[str, Any] = {
        tag: {
            **icdev_counterpart(tag, inventory=inventory, benchmark=benchmark),
            "external": [],
            "external_count": 0,
        }
        for tag in sorted(inventory)
    }

    unmapped: List[Dict[str, Any]] = []
    for target in targets:
        tag = (target.get("subsystem") or "").strip()
        if not tag:
            unmapped.append(
                {"project": project_label(target), "subsystem": None, "reason": "untagged"}
            )
            continue
        if tag not in subsystems:
            unmapped.append(
                {
                    "project": project_label(target),
                    "subsystem": tag,
                    "reason": "tag not in inventory",
                }
            )
            continue
        subsystems[tag]["external"].append(_external_entry(target))

    for spec in subsystems.values():
        spec["external"].sort(key=lambda e: e["project"].lower())
        spec["external_count"] = len(spec["external"])

    return {
        "subsystems": subsystems,
        "total_subsystems": len(subsystems),
        "total_projects": sum(s["external_count"] for s in subsystems.values()),
        "unmapped_projects": unmapped,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── integrity ────────────────────────────────────────────────────────────


def validate(
    *,
    targets: Optional[List[Dict[str, Any]]] = None,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    benchmark: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Cross-file integrity across the three files the map is joined from.

    Blocking (``ok`` goes False): a watchlist tag with no inventory entry, a
    promoter tag with no inventory entry, or a target carrying no tag at all —
    each of those routes a real finding to nothing.

    Informational: an inventory tag no external project benchmarks, and aliases
    claimed by more than one subsystem. Both are true statements about the
    watchlist rather than defects in it — ``cortex`` is permanently ambiguous.
    """
    targets = load_targets() if targets is None else targets
    inventory = load_inventory() if inventory is None else inventory
    benchmark = load_benchmark_subsystems() if benchmark is None else benchmark

    tagged = {(t.get("subsystem") or "").strip() for t in targets}
    untagged = sorted(project_label(t) for t in targets if not (t.get("subsystem") or "").strip())
    tagged.discard("")

    missing_from_inventory = sorted(tagged - set(inventory))
    promoter_orphans = sorted(set(benchmark) - set(inventory))
    unbenchmarked = sorted(set(inventory) - tagged)
    collisions = ambiguous_aliases(build_project_index(targets))

    return {
        "ok": not (missing_from_inventory or promoter_orphans or untagged),
        "watchlist_tags_missing_from_inventory": missing_from_inventory,
        "promoter_tags_missing_from_inventory": promoter_orphans,
        "untagged_targets": untagged,
        "inventory_tags_with_no_external_project": unbenchmarked,
        "ambiguous_aliases": collisions,
        "counts": {
            "targets": len(targets),
            "watchlist_tags": len(tagged),
            "inventory_tags": len(inventory),
            "promoter_tags": len(benchmark),
        },
    }


# ── CLI ──────────────────────────────────────────────────────────────────


def _render(result: Dict[str, Any]) -> None:
    if "subsystems" in result:
        for spec in result["subsystems"].values():
            verdict = spec["declared_verdict"] or "not benchmarked"
            print(
                f"  {spec['subsystem']:<22} {spec['external_count']:>2} projects"
                f"  [{verdict}]  {spec['title']}"
            )
        print(
            f"\n{result['total_projects']} projects across "
            f"{result['total_subsystems']} subsystems"
        )
        for entry in result["unmapped_projects"]:
            print(f"  UNMAPPED {entry['project']}: {entry['reason']}", file=sys.stderr)
    elif "ok" in result:
        print("  OK" if result["ok"] else "  FAILED")
        for key in (
            "watchlist_tags_missing_from_inventory",
            "promoter_tags_missing_from_inventory",
            "untagged_targets",
            "inventory_tags_with_no_external_project",
        ):
            if result[key]:
                print(f"  {key}: {', '.join(result[key])}")
        for alias, tags in result["ambiguous_aliases"].items():
            print(f"  ambiguous alias {alias!r}: {', '.join(tags)}")
        print(
            "  "
            + ", ".join(f"{k}={v}" for k, v in sorted(result["counts"].items()))
        )
    elif "module_patterns" in result:
        print(f"  {result['subsystem']} — {result['title']}")
        print(f"    verdict: {result['declared_verdict'] or 'not benchmarked'}")
        print(f"    modules: {', '.join(result['module_patterns']) or '(none)'}")
        print(f"    tables:  {', '.join(result['table_patterns']) or '(none)'}")
    else:
        print(f"  {result['project']} -> {result['subsystem'] or '(unknown)'}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map an external project to the ICDEV subsystem it benchmarks"
    )
    parser.add_argument("--project", help="Repo slug, bare name, display name or GitHub URL")
    parser.add_argument("--subsystem", help="Show ICDEV's counterpart for a subsystem tag")
    parser.add_argument("--all", action="store_true", help="The whole comparison map")
    parser.add_argument("--validate", action="store_true", help="Cross-file integrity report")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    try:
        if args.validate:
            result = validate()
        elif args.all:
            result = build_comparison_map()
        elif args.subsystem:
            result = icdev_counterpart(
                args.subsystem, benchmark=load_benchmark_subsystems()
            )
        elif args.project:
            result = {
                "project": args.project,
                "subsystem": subsystem_for_project(args.project),
            }
        else:
            parser.error("use --project <id>, --subsystem <tag>, --all, or --validate")
    except (SubsystemMapError, InventoryError, AmbiguousProjectError, KeyError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(
            json.dumps({"error": message}) if args.json else f"ERROR: {message}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _render(result)
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
