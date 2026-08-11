# CUI // SP-CTI

# Component Dependency Relationship — emit a real graph (sbx-cov-02)

**Element:** Component Dependency Relationship, SBOM 2026 Minimum Elements §1.2
**Standard:** *2026 Minimum Elements for a Software Bill of Materials (SBOM)*, CISA with
NSA, FBI and 16 international partners, 2026-07-29, v2.1
**Gap analysis:** [`docs/compliance/sbom-2026-minimum-elements-gap-analysis.md`](../compliance/sbom-2026-minimum-elements-gap-analysis.md) §3.2.5
**Status:** MET — the last of the 17 data-field elements

---

## What was wrong

ICDEV emitted a **flat component list**. There was no CycloneDX `dependencies` array in
the output at all, so no dependency graph could be reconstructed from an ICDEV SBOM. The
element was not partially met; it was entirely absent.

The information existed. sbx-cov-01 already walked lockfiles and resolved the transitive
tree, and every resolver component carries a `key` and a list of `dependencies`. The
generator then flattened that set into `components[]` and discarded the edges.

## What landed

`tools/compliance/dependency_graph.py` (mirrored to `icdev/`). The generator builds the
graph *before* the components array and emits it rooted at `metadata.component`:

```json
"dependencies": [
  {"ref": "icdev-proj",       "dependsOn": ["3f8e66f3f6d766ac"]},
  {"ref": "3f8e66f3f6d766ac", "dependsOn": ["7a4ca98c8cd0fdd5"]},
  {"ref": "7a4ca98c8cd0fdd5", "dependsOn": []}
]
```

### Design decisions

**The edge definition is the standard's narrow one.** An edge exists only where one
component is *necessary for the operation of* another. That is the resolver's edge set;
nothing is inferred from co-location in a manifest, alphabetical adjacency, or an
ecosystem defaulting rule.

Root edges come from **in-degree**, not from the resolver's `direct` flag. Several
resolvers default that flag to `True` because their source records no directness — trusting
it would fan the target out over the whole transitive set and assert direct requirements
that do not exist.

**Embed, do not link.** The standard permits either. A link satisfies Coverage only if the
recipient has access to *every* linked document, which ICDEV cannot guarantee for an
artifact leaving the enclave. One document carries the whole graph, and states that choice
in `icdev:sbom:dependency:embedding` rather than leaving it implicit.

**Unknown ≠ known-empty.** A declared-manifest component's own dependencies were never
read, so its edge set is unknown — CycloneDX says that with an *absent* entry. An entry
with an empty `dependsOn` asserts the component depends on nothing. Both shapes are
emitted, and `icdev:sbom:dependency:unknown` counts the first.

**Instance identity is refined to include relationships.** A node is
`(metadata identity, set of dependency identities)`. Two instances of the same name and
version that resolve *different* dependencies — the reason npm nests `node_modules` — are
two components, each with its own edges. This is a strict refinement of sbx-cov-01's
metadata-only rule: nothing that rule kept apart is merged.

**Cycles are detected, not followed.** An iterative back-edge search (recursion would blow
the stack on a deep npm tree) reports cycles in `icdev:sbom:dependency:cycles`. Members of
a cycle with no external entry stay reachable from the target via a single attached entry
point. The validator **recomputes** the count, so the claim is verifiable rather than
trusted.

## Two pre-existing defects this fixed

**Two components could share a bom-ref.** `component_id` hashes coordinates alone
(`group/name@version`), while a component is listed separately when any of six fields
differ — `scope` and `purl` among them. Two separately-listed components therefore minted
one ref. Invisible in a flat list; a real defect the moment a `dependsOn` has to name one
of them, and it was already collapsing the two onto a single `sbom_components` row via
`ON CONFLICT(id) DO UPDATE`. Collisions are now broken deterministically (`<ref>-2`), and
the validator rejects any document where two components share a ref.

**The gate accepted presence as proof.** `sbom_conformance_gate` scored this element with
`bool(sbom.get("dependencies"))`. An array whose `dependsOn` names a ref no component
carries builds no graph at all — and it is exactly the shape a partial implementation
produces. The gate now delegates to `validate_dependency_graph`, so the element is scored
by the same module that emits it.

## Interfaces

| Function | Purpose |
|---|---|
| `build_dependency_graph(components, root_ref, mint_ref=None, root=None)` | Resolver instances → nodes, edges, adjacency, cycles |
| `to_cyclonedx_dependencies(graph)` | The `dependencies` array |
| `to_spdx_relationships(graph, spdx_id_for=None)` | The same edges as SPDX `RELATIONSHIP` entries |
| `graph_properties(graph)` | `icdev:sbom:dependency:*` metadata properties |
| `dependency_rows(graph)` | Edges as `sbom_dependencies` row payloads |
| `validate_dependency_graph(sbom)` | Score the element: `met` / `not_met` + findings |
| `relationship_check_sql(column)` | The CHECK clause, derived from `RELATIONSHIP_TYPES` |

```bash
python tools/compliance/dependency_graph.py --validate /path/to/sbom.cdx.json --json
```

Exits non-zero on a flat list, a dangling `dependsOn`, a duplicated entry, an unrooted
graph, a component unreachable from the target, two components sharing a ref, or a declared
cycle count that disagrees with the graph.

## One graph, two serializations

`spdx_writer` (sbx-fmt-01) already derived its `RELATIONSHIP` entries from the CycloneDX
`dependencies` array and named sbx-cov-02 as the owner of the edge vocabulary. Emitting the
array therefore lights up the SPDX side with no change to that writer — the two
serializations express the same edge set rather than two opinions of it.

`RELATIONSHIP_TYPES` is also the vocabulary migration `20260809232803` derives the
`sbom_dependencies.relationship_type` CHECK from — the constraint sbx-fnd-02 deliberately
left off the table for this task to define, under the house rule that a CHECK vocabulary
is derived from a Python constant rather than hand-written in DDL.

## Verification

`tests/test_sbom_dependency_graph.py` — 59 cases, on the CI allowlist. Covers the rooted
graph and ref resolution, chain reconstruction (not flattening), duplicate instances with
divergent relationships, bom-ref uniqueness, cycles, unknown-vs-known-empty, embedding,
SPDX parity, the gate, the migration's CHECK on SQLite (including idempotency and row
preservation), and the CLI in both directions.

Sandbox posture: [`docs/security/sandbox-coverage.md`](../security/sandbox-coverage.md)
Gap 58 — **bypass-documented**; the module evaluates nothing and walks an integer-indexed
adjacency map.
