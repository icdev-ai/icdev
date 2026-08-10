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
*necessary for the operation of* the other. That is ``dependency_resolver``'s
edge set (sbx-cov-01) and nothing else — this module never infers an edge from
co-location in a manifest, from alphabetical adjacency, or from an ecosystem
defaulting rule.

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
emitted metadata was identical. That is not sufficient once relationships
exist: two instances of the same name and version can resolve *different*
dependencies, and collapsing them would either invent an edge or lose one.

So a node is ``(metadata identity, set of dependency identities)``. This is a
strict refinement of sbx-cov-01's rule — it never merges two instances that
rule kept apart, and it splits the ones whose relationships differ. Instances
agreeing on both are genuinely indistinguishable in the emitted document and
are one node. The refinement is one level deep, not a full bisimulation: two
nodes depending on the same package *identity* are merged even if that identity
has itself split into several nodes. Going deeper would need a fixpoint over a
cyclic graph for a distinction no consumer can act on.

BOM-REFS ARE MINTED BY THE CALLER, THEN MADE UNIQUE HERE

``sbom_identifiers.component_id`` is the ICDEV bom-ref formula and also the
``sbom_components`` primary key, so this module does not invent a second one —
it takes ``mint_ref`` and uses it. But that formula hashes *coordinates alone*
(``group/name@version``), while a component is listed separately when any of six
fields differ, ``scope`` and ``purl`` among them. Two separately-listed
components therefore collide on one ref, which predates this task and is
invisible in a flat list: it only becomes a defect once a ``dependsOn`` has to
name one of them, and it also made the two collapse onto a single
``sbom_components`` row via ``ON CONFLICT(id) DO UPDATE``. Colliding refs are
disambiguated deterministically here (``<ref>-2``, ``-3``, …) so that every
``dependsOn`` resolves to exactly one component.

SPDX

The edge vocabulary is format-neutral so sbx-fmt-01 can express the same edges
as SPDX ``RELATIONSHIP`` entries: see ``RELATIONSHIP_TYPES``,
``SPDX_RELATIONSHIP`` and ``to_spdx_relationships``. ``spdx_writer`` already
reads the CycloneDX ``dependencies`` array this module renders and names
sbx-cov-02 as the owner of that vocabulary, so the two serializations describe
one graph rather than two. ``RELATIONSHIP_TYPES`` is also the vocabulary the
``sbom_dependencies.relationship_type`` CHECK constraint is derived from — the
constraint sbx-fnd-02 deliberately left off the table for this task to define.
"""

import argparse
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

#: CycloneDX 1.4-1.7 express one edge kind. Optionality rides on
#: ``components[].scope``, which the generator already emits, so both
#: relationship types render into ``dependsOn`` rather than splitting a ref
#: across two entries — two entries for one ref is a malformed document.
CYCLONEDX_EDGE_FIELD = "dependsOn"

ELEMENT_NAME = "Component Dependency Relationship"

#: Metadata property names this module writes onto the document.
PROPERTY_GRAPH = "icdev:sbom:dependency:graph"
PROPERTY_EMBEDDING = "icdev:sbom:dependency:embedding"
PROPERTY_EDGES = "icdev:sbom:dependency:edges"
PROPERTY_CYCLES = "icdev:sbom:dependency:cycles"
PROPERTY_CYCLES_DETAIL = "icdev:sbom:dependency:cycles:detail"
PROPERTY_UNKNOWN = "icdev:sbom:dependency:unknown"
PROPERTY_UNROOTED = "icdev:sbom:dependency:unrooted"

EMBEDDING_STATEMENT = (
    "embedded: every dependency is a component of this document. No dependency "
    "is expressed as a link to a separate SBOM, because a link only satisfies "
    "Coverage when the recipient is guaranteed access to every linked document."
)


def relationship_check_sql(column="relationship_type"):
    """Render the CHECK clause for ``sbom_dependencies.relationship_type``.

    Used by the migration and by the test that proves the constraint in the
    database and the constant here have not drifted apart.
    """
    values = ", ".join(f"'{value}'" for value in RELATIONSHIP_TYPES)
    return f"CHECK ({column} IN ({values}))"


# --- graph construction -----------------------------------------------------


def component_identity(component):
    """The metadata tuple that decides whether two instances are one component.

    The 2026 Coverage element requires that "multiple instances of a component
    with differing metadata are listed separately with their dependency
    relationship", so instances collapse only when every emitted field matches.
    ``sbom_generator._component_identity`` delegates here: the rule that decides
    what is one component and the rule that mints the refs edges point at must
    be the same rule, or a ``dependsOn`` ends up naming a component that the
    document does not list.
    """
    return (
        str(component.get("type", "library") or "library"),
        str(component.get("group", "") or ""),
        str(component.get("name", "") or ""),
        str(component.get("version", "") or ""),
        str(component.get("purl", "") or ""),
        str(component.get("scope", "") or ""),
    )


def _default_mint_ref(component):
    """Fallback bom-ref formula for standalone use.

    The generator passes ``sbom_identifiers.component_id`` instead, so an ICDEV
    document's refs stay the ids the rest of the pipeline already keys on.
    """
    from tools.compliance.sbom_identifiers import component_id

    return component_id(component)


def _edge_type(target_component):
    """Classify an edge from the scope the resolver assigned to its target."""
    if str(target_component.get("scope", "")) == "optional":
        return RELATIONSHIP_OPTIONAL_DEPENDS_ON
    return RELATIONSHIP_DEPENDS_ON


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


def build_dependency_graph(components, root_ref, mint_ref=None, root=None):
    """Build the dependency graph of one SBOM document.

    Args:
        components: resolver-shape component instances — dicts carrying ``key``,
            ``dependencies`` (a list of target keys), ``resolution`` and the
            metadata fields :func:`component_identity` reads.
        root_ref: bom-ref of the target software, i.e. ``metadata.component``.
        mint_ref: ``component -> str`` bom-ref formula. Defaults to
            ``sbom_identifiers.component_id``. Collisions between distinct nodes
            are broken here, so the formula does not have to be injective.
        root: optional dict of extra root detail carried through to the caller.

    Returns:
        ``{"root", "nodes", "edges", "adjacency", "cycles", "unrooted",
        "unknown_refs", "dangling_edges"}``. ``nodes`` are in first-seen order
        and each carries ``ref``, ``instance`` (the representative resolver
        component), ``instance_keys``, ``declared`` and ``known_dependencies``.
        The caller renders ``instance`` into whatever component shape its format
        needs; this module owns only the refs and the edges between them.
    """
    if mint_ref is None:
        mint_ref = _default_mint_ref

    # Instances are the caller's own dicts, not copies. The generator enriches
    # each one as it renders it (`identifiers`, for the sbom_components
    # round-trip) and its caller reads that back off the list it passed in, so
    # copying here would quietly break that link. The resolved key is kept in a
    # parallel map instead of written onto the component, so nothing this
    # module does is visible to the caller.
    # ``instances`` pairs each key with the caller's own dict, not a copy. The
    # generator enriches each component as it renders it (``identifiers``, for
    # the sbom_components round-trip) and its caller reads that back off the
    # list it passed in, so copying here would quietly break that link. The
    # resolved key is paired alongside rather than written onto the component,
    # so nothing this module does is visible to the caller — and pairing by
    # position rather than by ``id()`` keeps it correct when the same dict
    # object is passed twice.
    instances = []
    by_key = {}
    for index, component in enumerate(components or []):
        key = component.get("key") or f"anon|{index}"
        if key in by_key:
            # The resolver guarantees unique keys per ecosystem; a collision
            # means two ecosystems chose the same key. Keep the first and give
            # the second a distinct one rather than silently dropping it.
            key = f"{key}#{index}"
        by_key[key] = component
        instances.append((key, component))

    identity_of = {key: component_identity(instance) for key, instance in by_key.items()}

    # Edge targets, resolved to instance keys. A key the resolver never emitted
    # is a dangling edge: dropped and counted, because a dependsOn that does not
    # resolve is exactly the defect this task exists to prevent.
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
    used_refs = {root_ref: None}
    for key, instance in instances:
        dep_identities = frozenset(identity_of[target] for target in targets_of[key])
        signature = (identity_of[key], dep_identities)
        node = nodes_by_signature.get(signature)
        if node is None:
            base = str(mint_ref(instance))
            ref = base
            attempt = 1
            # `component_id` hashes coordinates alone, so two components the
            # document lists separately can mint the same ref. Every node needs
            # its own, or an edge would name both.
            while ref in used_refs:
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

    node_by_ref = {node["ref"]: node for node in nodes}
    root_children = [node for node in nodes if in_degree[node["ref"]] == 0]
    for node in root_children:
        adjacency[root_ref].append(node["ref"])

    # Anything still unreachable sits inside a cycle with no external entry, or
    # is an orphan. Attaching it to the root keeps every component reachable
    # from the target — an unreachable component is not in the tree at all —
    # and the count is reported rather than swallowed. One node is attached at
    # a time and reachability recomputed, so a cycle gets a single entry point
    # rather than a root edge to every member of it.
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

    sbx-fmt-01 owns the SPDX document writer and derives its relationships from
    the CycloneDX ``dependencies`` array, so the two formats cannot describe
    different graphs. This is the direct rendering of the same edge set, kept
    beside the vocabulary, and it is what expresses the optional-dependency
    distinction that CycloneDX carries on ``components[].scope`` rather than on
    the edge. ``spdx_id_for`` maps a bom-ref to an SPDX element id.
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
    """ICDEV metadata properties describing the graph.

    The cycle count is not decoration: ``validate_dependency_graph`` recomputes
    it and refuses a document whose declared count disagrees, which is what
    makes "the graph was cycle-checked" a claim a recipient can verify rather
    than one they have to take on trust.
    """
    properties = [
        {"name": PROPERTY_GRAPH, "value": "rooted"},
        {"name": PROPERTY_EMBEDDING, "value": EMBEDDING_STATEMENT},
        {"name": PROPERTY_EDGES, "value": str(len(graph["edges"]))},
        {"name": PROPERTY_CYCLES, "value": str(len(graph["cycles"]))},
        {"name": PROPERTY_UNKNOWN, "value": str(len(graph["unknown_refs"]))},
    ]
    if graph["cycles"]:
        properties.append(
            {
                "name": PROPERTY_CYCLES_DETAIL,
                "value": "; ".join(" > ".join(cycle) for cycle in graph["cycles"]),
            }
        )
    if graph["unrooted"]:
        properties.append(
            {
                "name": PROPERTY_UNROOTED,
                "value": (
                    f"{len(graph['unrooted'])} component(s) had no dependent inside the "
                    "graph and were attached to the target directly so that every "
                    "component stays reachable"
                ),
            }
        )
    return properties


def dependency_rows(graph):
    """Edges as ``sbom_dependencies`` row payloads, minus the record id.

    Root edges are excluded: both operands of that table are component rows and
    the target software is ``metadata.component``, not a ``components`` entry.
    """
    root_ref = graph["root"]["ref"]
    return [
        {
            "parent_component_id": edge["from"],
            "child_component_id": edge["to"],
            "relationship_type": edge["type"],
            "scope": (
                "optional" if edge["type"] == RELATIONSHIP_OPTIONAL_DEPENDS_ON else "required"
            ),
        }
        for edge in graph["edges"]
        if edge["from"] != root_ref
    ]


# --- conformance ------------------------------------------------------------


def validate_dependency_graph(sbom):
    """Score the Component Dependency Relationship element of a CycloneDX doc.

    The element is scored by the same module that emits it rather than by a
    second, drifting opinion — the same division `component_producer` uses.

    Returns ``{"element", "status", "findings", "stats"}`` where ``status`` is
    ``met`` or ``not_met`` and each finding is ``{"code", "message"}``.
    """
    findings = []
    components = [c for c in (sbom.get("components") or []) if isinstance(c, dict)]
    dependencies = sbom.get("dependencies")
    metadata = sbom.get("metadata") or {}
    target = (metadata.get("component") or {}).get("bom-ref")
    properties = {
        p.get("name"): p.get("value")
        for p in (metadata.get("properties") or [])
        if isinstance(p, dict)
    }

    known_refs = set()
    duplicate_component_refs = set()
    for component in components:
        ref = component.get("bom-ref")
        if ref is None:
            continue
        if ref in known_refs:
            duplicate_component_refs.add(ref)
        known_refs.add(ref)
    if target:
        known_refs.add(target)

    # Two components sharing a bom-ref makes every edge naming it ambiguous, so
    # it is a defect of this element even though it is written elsewhere.
    for ref in sorted(duplicate_component_refs):
        findings.append(
            {
                "code": "duplicate_component_ref",
                "message": (
                    f"two components share bom-ref {ref!r} — an edge naming it cannot "
                    "identify which one"
                ),
            }
        )

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
        if not isinstance(entry, dict):
            findings.append(
                {"code": "malformed_entry", "message": "`dependencies` contains a non-object entry"}
            )
            continue
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
        entry.get("ref"): list(entry.get(CYCLONEDX_EDGE_FIELD) or [])
        for entry in dependencies
        if isinstance(entry, dict)
    }
    for values in list(adjacency.values()):
        for value in values:
            adjacency.setdefault(value, [])
    if target:
        reachable = _reachable(adjacency, target)
        orphans = sorted(str(ref) for ref in known_refs if ref not in reachable)
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

    # The graph is cycle-checked on every call and the count is reported in
    # `stats`, so a consumer always learns whether the tree terminates. An
    # absent `icdev:` property is therefore not a defect — requiring it would
    # score every valid third-party CycloneDX document a gap for the sole reason
    # that ICDEV did not write it, which is a wrong answer rather than a strict
    # one. A count that is present and disagrees is a different matter: the
    # document contradicts itself, and that is drift worth blocking on.
    cycles = detect_cycles(adjacency)
    declared_cycles = properties.get(PROPERTY_CYCLES)
    if declared_cycles is not None and str(declared_cycles) != str(len(cycles)):
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


def _bootstrap_import_path():
    """Make ``tools.*`` importable when this file is run by path."""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def main():
    _bootstrap_import_path()
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
