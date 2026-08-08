#!/usr/bin/env python3
# CUI // SP-CTI
"""Component Dependency Relationship — the SBOM 2026 element, as a real graph.

Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)",
CISA with NSA, FBI and 16 international partners, 2026-07-29, v2.1.
Gap analysis: ``docs/compliance/sbom-2026-minimum-elements-gap-analysis.md`` §3.2.

Before sbx-cov-02 the ICDEV SBOM was a flat component list: no CycloneDX
``dependencies`` array at all, so no dependency graph could be reconstructed
from ICDEV output. The element was absent, not partial.

WHAT A RELATIONSHIP IS

The standard's definition is narrow: an edge exists where one component is
*necessary for the operation of* the other. That is the resolver's edge set and
nothing else — this module never infers an edge from co-location in a manifest,
from alphabetical adjacency, or from an ecosystem defaulting rule.

EMBED, DO NOT LINK

The standard permits either embedding subcomponents in one document or linking
to a separate SBOM document per dependency. Linking only satisfies Coverage if
the recipient has access to *every* linked document, and ICDEV cannot guarantee
that for an artifact that leaves the enclave. So ICDEV embeds: one document
carries the whole transitive graph. That choice is stated in the artifact as
``icdev:sbom:dependency:embedding``.

INSTANCE IDENTITY

``dependency_resolver`` emits one component per *installed instance* — the
nested ``node_modules`` duplicates npm creates are separate instances, each with
its own key and its own outgoing edges. sbx-cov-01 collapsed instances whose
emitted metadata was identical. That is not sufficient here: two instances of
the same name and version can resolve *different* dependencies, and collapsing
them would either invent an edge or lose one.

So a node is ``(metadata identity, set of dependency identities)``. This is a
strict refinement of sbx-cov-01's rule — it never merges two instances that rule
kept apart, and it splits the ones whose relationships differ. Instances that
agree on both are genuinely indistinguishable in the emitted document and are
one node. The refinement is one level deep, not a full bisimulation: two nodes
that depend on the same package *identity* are merged even if that identity has
itself split into several nodes. Going deeper would need a fixpoint over a
cyclic graph for a distinction no consumer can act on.

SPDX

The edge vocabulary is format-neutral so sbx-fmt-01 can express the same edges
as SPDX ``RELATIONSHIP`` entries: see ``RELATIONSHIP_TYPES``, ``SPDX_RELATIONSHIP``
and ``to_spdx_relationships``. ``RELATIONSHIP_TYPES`` is also the vocabulary the
``sbom_dependencies.relationship_type`` CHECK constraint is derived from — the
constraint sbx-fnd-02 deliberately left off the table for this task to define.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

# --- relationship vocabulary ------------------------------------------------
#
# These are component -> component edges, read as "<from> requires <to>". The
# SQL CHECK on sbom_dependencies.relationship_type is generated from
# RELATIONSHIP_TYPES (house rule: derive CHECK vocabularies from the Python
# constant, never hand-write the list in DDL).

#: The target is required for the operation of the source.
RELATIONSHIP_DEPENDS_ON = "depends_on"
#: The target is needed only in an optional configuration — a dev, test, peer or
#: extras install. The edge is real; the qualifier is what makes it optional.
RELATIONSHIP_OPTIONAL_DEPENDS_ON = "optional_depends_on"

#: Every legal value of ``sbom_dependencies.relationship_type``.
RELATIONSHIP_TYPES = (
    RELATIONSHIP_DEPENDS_ON,
    RELATIONSHIP_OPTIONAL_DEPENDS_ON,
)

#: Document -> target component. Not an edge in ``sbom_dependencies`` (both of
#: that table's operands are component rows); CycloneDX carries it as
#: ``metadata.component`` and SPDX as ``SPDXRef-DOCUMENT DESCRIBES ...``.
RELATIONSHIP_DESCRIBES = "describes"

SPDX_DOCUMENT_REF = "SPDXRef-DOCUMENT"

#: relationship type -> (SPDX RELATIONSHIP kind, operands are swapped).
#: SPDX's ``DEPENDS_ON`` reads A-depends-on-B, same direction as ours, while
#: ``OPTIONAL_DEPENDENCY_OF`` reads A-is-an-optional-dependency-of-B, which is
#: the inverse — hence the swap flag rather than a second edge direction.
SPDX_RELATIONSHIP = {
    RELATIONSHIP_DEPENDS_ON: ("DEPENDS_ON", False),
    RELATIONSHIP_OPTIONAL_DEPENDS_ON: ("OPTIONAL_DEPENDENCY_OF", True),
}

#: CycloneDX has one edge kind. Optionality rides on ``components[].scope``,
#: which the generator already emits, so both types render into ``dependsOn``.
CYCLONEDX_EDGE_FIELD = "dependsOn"

ELEMENT_NAME = "Component Dependency Relationship"


def relationship_check_sql(column="relationship_type"):
    """Render the CHECK clause for ``sbom_dependencies.relationship_type``.

    Used by the migration test to prove the constraint in the database and the
    constant here have not drifted apart.
    """
    values = ", ".join(f"'{value}'" for value in RELATIONSHIP_TYPES)
    return f"CHECK ({column} IN ({values}))"


# --- graph construction -----------------------------------------------------


def _identity(component):
    """The metadata tuple that decides whether two instances look the same.

    sbx-cov-01 defined this rule inside ``sbom_generator``; it lives here now
    because the node identity it feeds is what bom-refs are minted from, and
    two places minting refs is how a ``dependsOn`` ends up pointing at nothing.
    """
    return (
        str(component.get("type", "library")),
        str(component.get("group", "")),
        str(component.get("name", "")),
        str(component.get("version", "")),
        str(component.get("purl", "")),
        str(component.get("scope", "")),
    )


def _identity_text(identity):
    return "|".join(identity)


def _edge_type(target_component):
    """Classify an edge from the scope the resolver assigned to its target."""
    if str(target_component.get("scope", "")) == "optional":
        return RELATIONSHIP_OPTIONAL_DEPENDS_ON
    return RELATIONSHIP_DEPENDS_ON


def _fingerprint(identity, dependency_identities):
    payload = _identity_text(identity) + "\x1e" + "\x1f".join(
        sorted(_identity_text(dep) for dep in dependency_identities)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def detect_cycles(adjacency):
    """Every cycle reachable by depth-first search, as lists of refs.

    Returns one representative path per distinct cycle (rotations deduplicated),
    which is what a report needs. It is a back-edge search, not an enumeration
    of all simple cycles: a graph is acyclic exactly when this returns ``[]``,
    but a graph with several overlapping cycles may report fewer paths than it
    strictly contains. Iterative, so a deep npm tree cannot blow the stack.
    """
    white, grey, black = 0, 1, 2
    color = {node: white for node in adjacency}
    cycles = []
    seen = set()

    for start in adjacency:
        if color[start] != white:
            continue
        color[start] = grey
        path = [start]
        stack = [(start, iter(adjacency[start]))]
        while stack:
            node, edges = stack[-1]
            descended = False
            for nxt in edges:
                if nxt not in color:
                    continue
                if color[nxt] == grey:
                    cycle = path[path.index(nxt):] + [nxt]
                    signature = _cycle_signature(cycle)
                    if signature not in seen:
                        seen.add(signature)
                        cycles.append(cycle)
                elif color[nxt] == white:
                    color[nxt] = grey
                    path.append(nxt)
                    stack.append((nxt, iter(adjacency[nxt])))
                    descended = True
                    break
            if not descended:
                color[node] = black
                stack.pop()
                path.pop()
    return cycles


def _cycle_signature(cycle):
    """Rotation-independent key, so a>b>a and b>a>b are reported once."""
    ring = cycle[:-1]
    if not ring:
        return tuple(cycle)
    pivot = ring.index(min(ring))
    return tuple(ring[pivot:] + ring[:pivot])


def _reachable(adjacency, root):
    seen = {root}
    queue = [root]
    while queue:
        node = queue.pop()
        for nxt in adjacency.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def build_dependency_graph(components, root_ref, root=None):
    """Build the dependency graph of one SBOM document.

    Args:
        components: resolver-shape component instances — dicts carrying ``key``,
            ``dependencies`` (a list of target keys), ``resolution`` and the
            metadata fields ``_identity`` reads.
        root_ref: bom-ref of the target software, i.e. ``metadata.component``.
        root: optional dict of extra root detail carried through to the caller.

    Returns:
        ``{"root", "nodes", "edges", "adjacency", "cycles", "unrooted",
        "unknown_refs"}``. ``nodes`` are in first-seen order and each carries
        ``ref``, ``instance`` (the representative resolver component),
        ``instance_keys``, ``declared`` and ``known_dependencies``. The caller
        renders ``instance`` into whatever component shape its format needs;
        this module owns only the refs and the edges between them.
    """
    instances = []
    by_key = {}
    for index, component in enumerate(components or []):
        instance = dict(component)
        key = instance.get("key") or f"anon|{index}"
        if key in by_key:
            # The resolver guarantees unique keys per ecosystem; a collision
            # means two ecosystems chose the same key. Keep the first and give
            # the second a distinct one rather than silently dropping it.
            key = f"{key}#{index}"
        instance["key"] = key
        by_key[key] = instance
        instances.append(instance)

    identity_of = {key: _identity(instance) for key, instance in by_key.items()}

    # Edge targets, resolved to instance keys. A key the resolver never emitted
    # is a dangling edge: dropped, because a dependsOn that does not resolve is
    # exactly the defect this task exists to prevent.
    targets_of = {}
    dangling = 0
    for key, instance in by_key.items():
        targets = []
        for target in instance.get("dependencies") or []:
            if target in by_key:
                targets.append(target)
            else:
                dangling += 1
        targets_of[key] = targets

    # A node is (metadata identity, set of dependency identities).
    node_of_key = {}
    nodes = []
    nodes_by_signature = {}
    used_refs = {}
    for instance in instances:
        key = instance["key"]
        dep_identities = frozenset(identity_of[target] for target in targets_of[key])
        signature = (identity_of[key], dep_identities)
        node = nodes_by_signature.get(signature)
        if node is None:
            base = _fingerprint(identity_of[key], dep_identities)
            ref = base
            attempt = 0
            while used_refs.get(ref, signature) != signature:
                # 64-bit truncation collision between two distinct nodes. Every
                # node needs its own ref or an edge would point at both.
                attempt += 1
                ref = f"{base}-{attempt}"
            used_refs[ref] = signature
            node = {
                "ref": ref,
                "instance": instance,
                "instance_keys": [],
                "declared": True,
                "ecosystem": instance.get("ecosystem", ""),
            }
            nodes_by_signature[signature] = node
            nodes.append(node)
        node["instance_keys"].append(key)
        if str(instance.get("resolution", "")) != "declared":
            node["declared"] = False
        node_of_key[key] = node

    for node in nodes:
        # A declared-manifest component's own dependencies were never read, so
        # its outgoing edge set is UNKNOWN, which is not the same fact as "it
        # has none". CycloneDX says that with an absent entry; an entry with an
        # empty dependsOn asserts the component is known to depend on nothing.
        node["known_dependencies"] = not node["declared"]

    edges = []
    seen_edges = set()
    for node in nodes:
        for key in node["instance_keys"]:
            for target in targets_of[key]:
                target_node = node_of_key[target]
                edge = (node["ref"], target_node["ref"], _edge_type(by_key[target]))
                if edge in seen_edges:
                    continue
                seen_edges.add(edge)
                edges.append({"from": edge[0], "to": edge[1], "type": edge[2]})

    # Root edges. A node nothing else depends on is a direct dependency of the
    # target software; a node with an in-edge is reached through its parent.
    # Deriving this from in-degree rather than from the resolver's `direct`
    # flag is deliberate: several resolvers default that flag to True because
    # their source records no directness, which would fan the root out over the
    # entire transitive set and assert a direct requirement that is not there.
    in_degree = {node["ref"]: 0 for node in nodes}
    for edge in edges:
        if edge["to"] != edge["from"]:
            in_degree[edge["to"]] += 1

    adjacency = {node["ref"]: [] for node in nodes}
    adjacency[root_ref] = []
    for edge in edges:
        adjacency[edge["from"]].append(edge["to"])

    root_children = [node for node in nodes if in_degree[node["ref"]] == 0]
    for node in root_children:
        adjacency[root_ref].append(node["ref"])

    # Anything still unreachable sits inside a cycle with no external entry, or
    # is an orphan. Attaching it to the root keeps every component reachable
    # from the target — an unreachable component is not in the tree at all —
    # and the count is reported rather than swallowed. One node is attached at
    # a time and reachability recomputed, so a cycle gets a single entry point
    # rather than a root edge to every member of it.
    node_by_ref = {node["ref"]: node for node in nodes}
    reachable = _reachable(adjacency, root_ref)
    unrooted = []
    for node in nodes:
        if node["ref"] in reachable:
            continue
        unrooted.append(node["ref"])
        adjacency[root_ref].append(node["ref"])
        root_children.append(node_by_ref[node["ref"]])
        reachable = _reachable(adjacency, root_ref)

    for node in root_children:
        edge = (root_ref, node["ref"], _edge_type(node["instance"]))
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        edges.append({"from": edge[0], "to": edge[1], "type": edge[2]})

    cycles = detect_cycles(adjacency)

    return {
        "root": dict(root or {}, ref=root_ref),
        "nodes": nodes,
        "edges": edges,
        "adjacency": adjacency,
        "cycles": cycles,
        "unrooted": unrooted,
        "unknown_refs": [node["ref"] for node in nodes if not node["known_dependencies"]],
        "dangling_edges": dangling,
    }


# --- renderers --------------------------------------------------------------


def to_cyclonedx_dependencies(graph):
    """Render the graph as a CycloneDX ``dependencies`` array.

    The root entry is always emitted, including when it depends on nothing —
    "this software has no components" is a statement worth making. Nodes whose
    own dependencies are unknown get no entry at all, which is how CycloneDX
    distinguishes unknown from known-empty.
    """
    outgoing = {}
    for edge in graph["edges"]:
        outgoing.setdefault(edge["from"], set()).add(edge["to"])

    root_ref = graph["root"]["ref"]
    entries = [{"ref": root_ref, CYCLONEDX_EDGE_FIELD: sorted(outgoing.get(root_ref, ()))}]
    for node in graph["nodes"]:
        if not node["known_dependencies"]:
            continue
        entries.append(
            {"ref": node["ref"], CYCLONEDX_EDGE_FIELD: sorted(outgoing.get(node["ref"], ()))}
        )
    return entries


def to_spdx_relationships(graph, spdx_id_for=None):
    """Render the same edges as SPDX ``RELATIONSHIP`` entries.

    sbx-fmt-01 owns the SPDX document writer; this is the edge half of it, kept
    beside the vocabulary so the two formats cannot drift into describing
    different graphs. ``spdx_id_for`` maps a bom-ref to an SPDX element id.
    """
    if spdx_id_for is None:
        def spdx_id_for(ref):
            return f"SPDXRef-{ref}"

    relationships = [
        {
            "spdxElementId": SPDX_DOCUMENT_REF,
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": spdx_id_for(graph["root"]["ref"]),
        }
    ]
    for edge in graph["edges"]:
        spdx_type, swapped = SPDX_RELATIONSHIP[edge["type"]]
        left, right = edge["from"], edge["to"]
        if swapped:
            left, right = right, left
        relationships.append(
            {
                "spdxElementId": spdx_id_for(left),
                "relationshipType": spdx_type,
                "relatedSpdxElement": spdx_id_for(right),
            }
        )
    return relationships


def graph_properties(graph):
    """ICDEV metadata properties describing the graph, for gates that do not
    carry a CycloneDX parser."""
    properties = [
        {"name": "icdev:sbom:dependency:graph", "value": "rooted"},
        {
            "name": "icdev:sbom:dependency:embedding",
            "value": (
                "embedded: every dependency is a component of this document. No "
                "dependency is expressed as a link to a separate SBOM, because a "
                "link only satisfies Coverage when the recipient is guaranteed "
                "access to every linked document."
            ),
        },
        {"name": "icdev:sbom:dependency:edges", "value": str(len(graph["edges"]))},
        {"name": "icdev:sbom:dependency:cycles", "value": str(len(graph["cycles"]))},
        {
            "name": "icdev:sbom:dependency:unknown",
            "value": str(len(graph["unknown_refs"])),
        },
    ]
    if graph["cycles"]:
        properties.append(
            {
                "name": "icdev:sbom:dependency:cycles:detail",
                "value": "; ".join(" > ".join(cycle) for cycle in graph["cycles"]),
            }
        )
    if graph["unrooted"]:
        properties.append(
            {
                "name": "icdev:sbom:dependency:unrooted",
                "value": (
                    f"{len(graph['unrooted'])} component(s) had no dependent inside the "
                    "graph and were attached to the target directly so that every "
                    "component stays reachable"
                ),
            }
        )
    return properties


# --- conformance ------------------------------------------------------------


def validate_dependency_graph(sbom):
    """Score the Component Dependency Relationship element of a CycloneDX doc.

    sbx-sig-02 builds the full minimum-elements validator; this is the per-element
    contract it consumes, so the element is scored by the same code that emits it
    rather than by a second, drifting opinion.

    Returns ``{"element", "status", "findings", "stats"}`` where ``status`` is
    ``met`` or ``not_met`` and each finding is ``{"code", "message"}``.
    """
    findings = []
    components = sbom.get("components") or []
    dependencies = sbom.get("dependencies")
    metadata = sbom.get("metadata") or {}
    target = (metadata.get("component") or {}).get("bom-ref")
    properties = {p.get("name"): p.get("value") for p in (metadata.get("properties") or [])}

    known_refs = {component.get("bom-ref") for component in components}
    known_refs.discard(None)
    if target:
        known_refs.add(target)

    if not isinstance(dependencies, list) or not dependencies:
        findings.append(
            {
                "code": "dependencies_absent",
                "message": (
                    "no `dependencies` array — the SBOM is a flat component list and no "
                    "dependency graph can be built from it"
                ),
            }
        )
        return _verdict(findings, {"components": len(components), "entries": 0, "edges": 0})

    seen_refs = set()
    edge_count = 0
    for entry in dependencies:
        ref = entry.get("ref")
        if ref in seen_refs:
            findings.append(
                {"code": "duplicate_ref", "message": f"`dependencies` lists ref {ref!r} twice"}
            )
        seen_refs.add(ref)
        if ref not in known_refs:
            findings.append(
                {
                    "code": "unresolved_ref",
                    "message": f"`dependencies[].ref` {ref!r} matches no component bom-ref",
                }
            )
        for target_ref in entry.get(CYCLONEDX_EDGE_FIELD) or []:
            edge_count += 1
            if target_ref not in known_refs:
                findings.append(
                    {
                        "code": "unresolved_depends_on",
                        "message": (
                            f"`dependsOn` {target_ref!r} (from {ref!r}) matches no component "
                            "bom-ref"
                        ),
                    }
                )

    if not target:
        findings.append(
            {
                "code": "no_target",
                "message": "metadata.component has no bom-ref, so the graph has no root",
            }
        )
    elif target not in seen_refs:
        findings.append(
            {
                "code": "unrooted_graph",
                "message": (
                    f"no `dependencies` entry for the target component {target!r} — the graph "
                    "is not rooted at the software the SBOM describes"
                ),
            }
        )

    adjacency = {
        entry.get("ref"): list(entry.get(CYCLONEDX_EDGE_FIELD) or []) for entry in dependencies
    }
    for values in list(adjacency.values()):
        for value in values:
            adjacency.setdefault(value, [])
    if target:
        reachable = _reachable(adjacency, target)
        orphans = sorted(ref for ref in known_refs if ref not in reachable)
        if orphans:
            findings.append(
                {
                    "code": "unreachable_components",
                    "message": (
                        f"{len(orphans)} component(s) are not reachable from the target: "
                        + ", ".join(orphans[:5])
                        + ("…" if len(orphans) > 5 else "")
                    ),
                }
            )

    cycles = detect_cycles(adjacency)
    declared_cycles = properties.get("icdev:sbom:dependency:cycles")
    if declared_cycles is None:
        findings.append(
            {
                "code": "cycles_not_checked",
                "message": (
                    "no `icdev:sbom:dependency:cycles` property — the graph was never "
                    "cycle-checked, so a consumer walking it cannot know whether it terminates"
                ),
            }
        )
    elif str(declared_cycles) != str(len(cycles)):
        findings.append(
            {
                "code": "cycle_count_mismatch",
                "message": (
                    f"the document declares {declared_cycles} cycle(s) but the graph contains "
                    f"{len(cycles)}"
                ),
            }
        )

    return _verdict(
        findings,
        {
            "components": len(components),
            "entries": len(dependencies),
            "edges": edge_count,
            "cycles": len(cycles),
        },
    )


def _verdict(findings, stats):
    return {
        "element": ELEMENT_NAME,
        "status": "not_met" if findings else "met",
        "findings": findings,
        "stats": stats,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate the Component Dependency Relationship element of a CycloneDX SBOM"
    )
    parser.add_argument("--validate", required=True, help="Path to a CycloneDX JSON SBOM")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        sbom = json.loads(Path(args.validate).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read {args.validate}: {exc}", file=sys.stderr)
        sys.exit(1)

    report = validate_dependency_graph(sbom)
    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['element']}: {report['status'].upper()}")
        for key, value in report["stats"].items():
            print(f"  {key}: {value}")
        for finding in report["findings"]:
            print(f"  [{finding['code']}] {finding['message']}")
    sys.exit(0 if report["status"] == "met" else 1)


if __name__ == "__main__":
    main()
