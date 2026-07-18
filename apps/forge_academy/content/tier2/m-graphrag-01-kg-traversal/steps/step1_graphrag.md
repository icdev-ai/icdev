---
ontology_id: icdev:mission:m-graphrag-01-kg-traversal:step:1
step_class: icdev:Lab
---

# GraphRAG & the Knowledge Graph

Plain RAG answers "what text is *similar* to my query?" That is powerful, but it misses
facts that are **connected** rather than similar. GraphRAG fixes that by combining the
two: a vector search finds seed nodes, then a walk over the **Knowledge Graph** pulls in
the facts linked to those seeds.

## kg_edges — the shared graph

The KG stores relationships in **`kg_edges`** — real columns are `graph_id`,
`source_id`, `target_id`, and `relationship` (the edge label), plus `weight` and
`properties`. `kg_search` retrieves nodes; the edges let you traverse from a node to
its neighbors.

> **Warning:** `kg_edges` (and `kg_nodes`) is **one physical table shared across every
> canvas graph** — DIC, the compliance crosswalk, the network graph, and more all write
> into it, partitioned only by the `graph_id` column. Two consequences:
> **(1) Never purge `kg_edges` by label** — you will delete another canvas's
> relationships. **(2) Always scope a traversal by `graph_id` / `project_id`** — a
> `retrieve()` call that forgets to pass one queries *all* graphs at once.

## How GraphRAG retrieves

`tools/knowledge_graph/graph_rag.py::retrieve()` does keyword + semantic candidate
selection, then **expands the 1-hop neighborhood** over `kg_edges`, then scores. The
semantic step has two paths:

- **PostgreSQL (primary)** — cosine similarity is computed **in the database** with
  pgvector's `<=>` distance operator against `kg_nodes.embedding_vec`, so only the top
  rows leave the DB.
- **SQLite (fallback)** — packed-float32 embeddings are streamed to Python and scored
  with a plain cosine helper.

Either way, the **graph expansion** — walking `kg_edges` from the seed nodes to pull in
connected facts — is the move you'll build today.

## What you'll build

The Tier-A graph hop, with the stdlib `collections`:

1. `build_adjacency()` — turn `kg_edges` into an **undirected** neighbor map (GraphRAG
   walks edges in both directions).
2. `k_hop_neighbors()` — breadth-first expansion out to `k` hops from a start node.
3. `graphrag_expand()` — take the seed nodes a vector search returned and return the
   full connected neighborhood (seeds + k-hop neighbors), sorted.

This is the exact move that lets "show me AC-2" also surface the controls AC-2 *depends
on* — the connected facts a similarity search alone would never rank. Open
`step1_starter.py` and implement the three `TODO`s.
