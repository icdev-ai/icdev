# CUI // SP-CTI

# Internal Awareness Engine — Relationship Derivation (idp-cat-02)

**Status:** shipped
**Scope:** `tools/awareness/edge_deriver.py`, `tools/awareness/component_indexer.py`,
`args/awareness_config.yaml`, `/components-map` API + drawer

---

## The problem, as measured

The self-awareness graph held **2,432 nodes and 0 edges**:

| entity_type | count |
|---|---|
| tool | 2,183 |
| reflex | 119 |
| goal | 71 |
| mcp_server | 28 |
| skill | 23 |
| canvas_module | 8 |
| **edges** | **0** |

`component_indexer.derive_edges()` implemented exactly one heuristic — canvas
name vs goal title keyword match — and explicitly deferred everything else.
That heuristic produced **zero** edges against the real tree, which is why the
graph was empty rather than merely thin: no goal title in `goals/` contains the
string `boundary_canvas`, `data_canvas`, `network_canvas`, and so on.

A node bag with no relationships cannot answer the one question an internal
developer platform exists to answer: **what breaks if this changes.** No
dependency graph, no blast radius, no impact analysis.

---

## Design rule: mechanical over similar

An `import` statement is a fact. A `CREATE TABLE` is a fact. A `@bp.route(...)`
decorator is a fact. Two titles sharing a word is a guess.

Every edge therefore carries its own provenance:

```json
{
  "derivation": "python_import_ast",
  "mechanical": true,
  "evidence": "tools/awareness/health_prober.py:31 imports tools.db.storage.get_connection"
}
```

`weight` doubles as confidence (1.0 = the evidence *is* the relationship).
Consumers that want only hard facts pass `mechanical_only=True` and the single
similarity-based derivation drops out without taking the dependency graph
with it.

## Derivations

| derivation | relationship | source of truth | mechanical |
|---|---|---|---|
| `python_import_ast` | `imports` | AST `Import` / `ImportFrom` in every file-backed Python node | yes |
| `documented_command` | `invokes` | `python tools/x.py` / `python -m tools.x` in goal + skill markdown | yes |
| `ddl_create_table` | `creates_table` | `CREATE TABLE` in migrations, `init_icdev_db.py`, canvas `db/init_db.py` | yes |
| `sql_table_reference` | `uses_table` | SQL in a module naming a table that has real DDL | yes |
| `flask_route_decorator` | `serves_route` | `@bp.route("/…")` | yes |
| `component_registry_iqe` | `provides_collection` | `args/component_registry.yaml` → `iqe.collections` | yes |
| `component_registry_module` | `implemented_by` | registry `module` / `iqe.adapter_module` | yes |
| `component_registry_depends_on` | `depends_on` | registry `depends_on` | yes |
| `title_keyword_match` | `referenced_by_goal` | canvas name appearing in a goal title | **no** (weight 0.4) |

### Endpoint nodes

Mechanical edges need endpoints the indexer never produced, so the deriver
contributes five node types alongside them. All are file-backed
(`properties.file_path` points at the migration / blueprint / adapter that
defines them), so `prune_stale_nodes` reconciles them against disk like any
other node.

`migration`, `db_table`, `route`, `component`, `iqe_collection`

`component` exists because 35 of the 66 registered components declare a module
(`tools.network.blueprint`, `tools.iqe.adapters.ndc`) that is not itself a
manifest tool — without a node of its own, a registered component with no
manifest entry would have been invisible and its declared prerequisites
unrepresentable.

## Result on the live tree

```
nodes  2,412 indexed  +  3,210 derived  =  5,622
edges  11,403

imports              4,752
uses_table           3,293
creates_table        1,445
serves_route         1,398
invokes                266
provides_collection    207
implemented_by          41
depends_on               1
```

`tools/db/storage.py` reports **1,036 direct dependents** and 1,375 at depth 2.

## Querying

```bash
python tools/awareness/edge_deriver.py --derive --json
python tools/awareness/edge_deriver.py --dependents tools/db/storage.py --json
python tools/awareness/edge_deriver.py --dependents kanban_tasks --depth 2
python tools/awareness/edge_deriver.py --dependencies tools/awareness/health_prober.py
python tools/awareness/edge_deriver.py --stats --json
```

```python
from tools.awareness.edge_deriver import get_dependents
get_dependents("tools/db/storage.py", depth=1, mechanical_only=True)
```

```
GET /api/components-map/dependents/<ref>?depth=2&mechanical_only=1
GET /api/components-map/dependents/<ref>?direction=deps
```

`<ref>` accepts a node id, a repo-relative file path, or a node label. The
`/components-map` detail drawer calls this endpoint and renders a **Blast
radius** panel showing direct dependents with the derivation that produced
each one; non-mechanical rows are marked `~guess`.

## Guardrails

* **Config** — `args/awareness_config.yaml` → `edges`. Each derivation is
  individually switchable and `edges.enabled: false` disables the layer
  entirely.
* **Fan-out caps** — `max_imports_per_module` (60), `max_tables_per_module`
  (15), `max_routes_per_module` (80), `max_commands_per_document` (40). A
  6,000-line `app.py` cannot flood the graph. Truncation is logged, never
  silent.
* **Table matching is closed-vocabulary** — `SELECT … FROM whatever` only
  produces an edge when `whatever` has actual `CREATE TABLE` DDL somewhere in
  the tree, so the SQL scanner cannot invent tables.
* **Persistence is chunked** — 200 rows per transaction with row-by-row replay
  on chunk failure. The Phase 1a per-row commit was invisible at 2,432 nodes /
  0 edges and is not at 5,622 nodes / 11,403 edges.
* **Never wedges a scan** — `component_indexer.derive_graph` catches any
  derivation failure and falls back to an edgeless graph rather than aborting
  the index.

## UI payload guards

Real edges made two `/components-map` endpoints reachable at a scale they were
never sized for:

* `/api/components-map/graph` now caps the edge payload (`max_edges`, default
  2,000, highest weight first) and reports `edge_total` / `edges_truncated`.
* `/api/components-map/neighbors/<id>` caps at 300 edges and reports
  `truncated`. A 1,036-edge hub would otherwise have both wedged the JointJS
  canvas and exceeded SQLite's 999-parameter limit on its `IN (…)` clause.

Use `/api/components-map/dependents` for the full, unrendered list.

## Adjacent SQLite-path bugs fixed in passing

Every components-map API returned `unable to open database file` on a SQLite
install because `_cmap_conn` called `get_connection("icdev")` — whose only
positional parameter is `db_path`, ignored on PostgreSQL but taken literally by
SQLite. With that fixed, two `sqlite3.Row.get()` calls (`api_cmap_node`, the
page stat block) surfaced and were normalised through `dict()`; the page block
had been swallowing its own exception and rendering "0 components".

## Tests

`tests/test_awareness_edge_deriver.py` (35 tests, in the CI unit tier):

* every edge records a known `derivation`, a `mechanical` flag and `evidence`
* `title_keyword_match` is the only non-mechanical derivation, and every
  mechanical derivation outweighs it
* one test per derivation against a miniature fixture repo
* `FROM not_a_real_table` produces neither a node nor an edge
* config toggles and fan-out caps are honoured
* query API: direct dependents, transitive depth, inverse direction, lookup by
  id/path/label, unknown ref errors instead of returning empty success,
  `mechanical_only` filtering
* live-repo floor: **> 1,000 edges** — the direct regression guard for "the
  graph is a node bag again"
