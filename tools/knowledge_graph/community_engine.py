# CUI // SP-CTI
"""GraphRAG community detection + summarization for the DIC knowledge graph.

Flat RAG answers "what does the corpus say about X" by retrieving the chunks
most similar to X. It cannot answer "what are the main themes across all these
documents" — no single chunk contains the answer; it is a property of the graph
as a whole. That global/thematic question is what GraphRAG's community summaries
exist to serve: cluster the knowledge graph into communities, summarise each, and
let a query reason over the summaries.

DIC already had the table (`dic_community_summaries`) but no engine — it was
empty. This is the engine. It became possible only once the KG actually had edges
(the LLM extractor in the RAG->KG bridge); you cannot cluster a graph of
disconnected nodes.

Pipeline:
  1. detect_communities — Louvain over each DIC graph's edges (networkx, seeded so
     re-runs are stable). Canvas architecture graphs are excluded: `kg_edges` is
     shared, and only rag_kg_bridge nodes are document-graph nodes.
  2. summarise_community — the LLM writes a theme summary for each community from
     its entities + internal relationships; deterministic fallback (top entities)
     when the LLM is unavailable, so an air-gapped run still produces something.
  3. store — upsert into dic_community_summaries, keyed by a content-stable
     community_id so re-running replaces rather than duplicates.
  4. search_communities — rank summaries against a query for global Q&A.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# A community smaller than this is noise, not a theme.
MIN_COMMUNITY_SIZE = 3
# Cap members shown to the LLM so a giant community can't blow the prompt.
_MAX_MEMBERS_IN_PROMPT = 40
_LOUVAIN_SEED = 1  # deterministic partitions across runs

_SUMMARY_SYSTEM_PROMPT = (
    "You are summarising one community of a knowledge graph built from a document "
    "corpus. You are given the community's entities (with types) and the "
    "relationships among them. Write 1-3 sentences naming the theme this "
    "community represents and what ties its entities together. Be concrete and "
    "specific; do not invent facts not implied by the entities and relations. "
    "Return only the summary text, no preamble."
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def detect_communities(
    node_ids: list[str],
    edges: list[tuple[str, str]],
    *,
    min_size: int = MIN_COMMUNITY_SIZE,
) -> list[list[str]]:
    """Partition a graph into communities via Louvain modularity.

    Pure function of (nodes, edges) — no DB, no LLM — so the clustering is
    testable in isolation. Returns communities with >= min_size members, largest
    first. Isolated nodes (no edges) form no community: a theme needs connection.
    """
    import networkx as nx
    from networkx.algorithms import community as nx_comm

    g = nx.Graph()
    g.add_nodes_from(node_ids)
    for a, b in edges:
        if a in g and b in g and a != b:
            g.add_edge(a, b)

    # Drop isolates before clustering — an unconnected node is not part of any
    # theme and would otherwise surface as a size-1 "community".
    g.remove_nodes_from(list(nx.isolates(g)))
    if g.number_of_nodes() == 0:
        return []

    try:
        parts = nx_comm.louvain_communities(g, seed=_LOUVAIN_SEED)
    except Exception as exc:  # noqa: BLE001 — fall back to a simpler algorithm
        logger.debug("louvain failed (%s), falling back to label propagation", exc)
        parts = nx_comm.label_propagation_communities(g)

    communities = [sorted(c) for c in parts if len(c) >= min_size]
    communities.sort(key=len, reverse=True)
    return communities


def _graph_tag(graph_id: str) -> str:
    """Short stable tag for a graph, embedded in every community_id so a graph's
    communities can be cleared by prefix before a rebuild."""
    return hashlib.sha256(str(graph_id).encode("utf-8")).hexdigest()[:8]


def _community_id(graph_id: str, member_ids: list[str]) -> str:
    """Content-stable id: same graph + same membership -> same id (idempotent).

    Shaped ``comm-<graph tag>-<membership hash>`` so DELETE by the graph prefix
    removes exactly this graph's communities, including ones whose membership
    shifted between runs (which get a new hash and would otherwise orphan)."""
    members = hashlib.sha256(",".join(sorted(member_ids)).encode("utf-8")).hexdigest()[:12]
    return f"comm-{_graph_tag(graph_id)}-{members}"


def _render_community(members: list[dict], edges: list[tuple[str, str, str]]) -> str:
    """Compact text description of a community for the summariser."""
    label_of = {m["id"]: m.get("label") or m["id"] for m in members}
    lines = ["Entities:"]
    for m in members[:_MAX_MEMBERS_IN_PROMPT]:
        lines.append(f"- {m.get('label')} ({m.get('entity_type') or 'concept'})")
    rels = [
        f"- {label_of.get(a, a)} --{rel}--> {label_of.get(b, b)}"
        for (a, b, rel) in edges
        if a in label_of and b in label_of
    ]
    if rels:
        lines.append("Relationships:")
        lines.extend(rels[:_MAX_MEMBERS_IN_PROMPT])
    return "\n".join(lines)


def _fallback_summary(members: list[dict]) -> str:
    """Deterministic summary when the LLM is unavailable (air-gap)."""
    labels = [m.get("label") for m in members if m.get("label")][:8]
    return "Community of related entities: " + ", ".join(labels) + "."


def summarise_community(
    members: list[dict],
    edges: list[tuple[str, str, str]],
    *,
    router: Optional[Any] = None,
) -> tuple[str, list[str]]:
    """Return (summary_text, citations) for one community.

    Citations are the member entity labels — the graph evidence the summary rests
    on. Never raises: a failed/absent LLM yields the deterministic fallback so the
    pipeline always stores something.
    """
    citations = [m.get("label") for m in members if m.get("label")]
    description = _render_community(members, edges)
    try:
        if router is None:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
        from tools.llm.provider import LLMRequest

        req = LLMRequest(
            messages=[{"role": "user", "content": description}],
            system_prompt=_SUMMARY_SYSTEM_PROMPT,
            max_tokens=250,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = router.invoke("kg_community_summary", req)
    except Exception as exc:  # noqa: BLE001 — graceful: deterministic fallback
        logger.debug("community summary LLM unavailable: %s", exc)
        return _fallback_summary(members), citations

    if resp and getattr(resp, "content", None):
        text = resp.content.strip()
        if text:
            return text[:2000], citations
    return _fallback_summary(members), citations


def _dic_graph_ids(conn) -> set[str]:
    """Graph ids belonging to the DIC document KG (rag_kg_bridge nodes).

    kg_edges/kg_nodes are shared with canvas architecture graphs; only bridge
    nodes are document-graph nodes. Read properties in Python rather than a
    JSON-in-SQL filter (PG-primary rule)."""
    dic: set[str] = set()
    for r in conn.execute("SELECT graph_id, properties FROM kg_nodes"):
        d = dict(r) if hasattr(r, "keys") else {"graph_id": r[0], "properties": r[1]}
        try:
            props = json.loads(d.get("properties") or "{}")
        except (json.JSONDecodeError, TypeError):
            props = {}
        if props.get("source") == "rag_kg_bridge":
            dic.add(d["graph_id"])
    return dic


def build_communities(
    conn,
    *,
    tenant_id: str = "default",
    classification: str = "CUI",
    router: Optional[Any] = None,
    min_size: int = MIN_COMMUNITY_SIZE,
    graph_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Detect + summarise + store communities for the DIC document graphs.

    Idempotent per graph: existing summaries for a processed graph are cleared
    before the fresh set is written, keyed by content-stable community ids.
    """
    targets = set(graph_ids) if graph_ids else _dic_graph_ids(conn)
    stats = {"graphs": 0, "communities": 0, "skipped_small": 0}
    now = _utcnow()

    for gid in sorted(targets):
        nodes = [
            (dict(r) if hasattr(r, "keys") else {"id": r[0], "label": r[1], "entity_type": r[2]})
            for r in conn.execute(
                "SELECT id, label, entity_type FROM kg_nodes WHERE graph_id = %s", (gid,)
            )
        ]
        if len(nodes) < min_size:
            continue
        node_by_id = {n["id"]: n for n in nodes}
        raw_edges = [
            (dict(r) if hasattr(r, "keys") else {"source_id": r[0], "target_id": r[1], "relationship": r[2]})
            for r in conn.execute(
                "SELECT source_id, target_id, relationship FROM kg_edges WHERE graph_id = %s", (gid,)
            )
        ]
        edge_pairs = [(e["source_id"], e["target_id"]) for e in raw_edges]
        communities = detect_communities(list(node_by_id), edge_pairs, min_size=min_size)
        if not communities:
            continue

        # Clear prior summaries for this graph before rewriting (idempotent).
        _clear_graph_communities(conn, gid)

        stats["graphs"] += 1
        for members_ids in communities:
            members = [node_by_id[i] for i in members_ids if i in node_by_id]
            member_set = set(members_ids)
            internal_edges = [
                (e["source_id"], e["target_id"], e["relationship"])
                for e in raw_edges
                if e["source_id"] in member_set and e["target_id"] in member_set
            ]
            summary, citations = summarise_community(members, internal_edges, router=router)
            cid = _community_id(gid, members_ids)
            _store_summary(
                conn, cid, gid, summary, citations, tenant_id, classification, now
            )
            stats["communities"] += 1

    try:
        conn.commit()
    except Exception:
        pass
    return stats


def _clear_graph_communities(conn, graph_id) -> None:
    """Delete this graph's prior community summaries before a rebuild.

    community_id is ``comm-<graph tag>-<membership hash>``, so a prefix match on
    the graph tag removes exactly this graph's communities — including any whose
    membership changed since the last run and would otherwise linger as orphans.
    """
    conn.execute(
        "DELETE FROM dic_community_summaries WHERE community_id LIKE %s",
        (f"comm-{_graph_tag(graph_id)}-%",),
    )


def _store_summary(
    conn, community_id, graph_id, summary, citations, tenant_id, classification, now
) -> None:
    summary_id = community_id  # one summary per community; upsert in place
    conn.execute(
        "INSERT INTO dic_community_summaries "
        "(summary_id, community_id, summary_text, citations_list, model_version, "
        " created_at, updated_at, tenant_id, classification) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (summary_id) DO UPDATE SET "
        "  summary_text = excluded.summary_text, "
        "  citations_list = excluded.citations_list, "
        "  updated_at = excluded.updated_at",
        (
            summary_id, community_id, summary, json.dumps(citations),
            "graphrag-v1", now, now, tenant_id, classification,
        ),
    )


def _graph_tags_for_collection(conn, collection_id: str) -> set[str]:
    """The community_id graph tags belonging to a collection's document graph.

    A collection's chunks resolve to one KG graph; community_id embeds
    _graph_tag(graph_id). Resolve the graph(s) via the node->chunk join
    (source_chunk_id is populated on ~all bridge nodes) so search can scope to
    the collection the user is actually in, rather than every theme in the tenant.
    """
    if not collection_id:
        return set()
    try:
        rows = conn.execute(
            "SELECT DISTINCT n.graph_id FROM kg_nodes n "
            "JOIN rag_chunks ch ON ch.id = n.source_chunk_id "
            "WHERE ch.project_id = %s",
            (collection_id,),
        ).fetchall()
    except Exception:  # noqa: BLE001 — scoping is best-effort; fall back to tenant-wide
        return set()
    return {
        _graph_tag(dict(r)["graph_id"] if hasattr(r, "keys") else r[0])
        for r in rows
    }


def search_communities(
    conn,
    query: str,
    *,
    tenant_id: str = "default",
    limit: int = 5,
    collection_id: str | None = None,
) -> list[dict]:
    """Return community summaries most relevant to *query* for global Q&A.

    Token-overlap ranking over summary_text + citations. Deliberately simple and
    deterministic: community summaries are few (one per theme), so exhaustive
    scoring is cheap and needs no embedding index.

    When ``collection_id`` is given, results are scoped to that collection's
    graph — a user asking "the main themes" inside a collection means THAT
    collection, not every document in the tenant. If the collection resolves to
    no graph (nothing ingested there yet), it degrades to tenant-wide rather than
    returning nothing.
    """
    q_tokens = _tokens(query)
    rows = [
        (dict(r) if hasattr(r, "keys") else {
            "summary_id": r[0], "community_id": r[1], "summary_text": r[2], "citations_list": r[3]})
        for r in conn.execute(
            "SELECT summary_id, community_id, summary_text, citations_list "
            "FROM dic_community_summaries WHERE tenant_id = %s",
            (tenant_id,),
        )
    ]
    tags = _graph_tags_for_collection(conn, collection_id) if collection_id else set()
    if tags:
        scoped = [r for r in rows if any(r["community_id"].startswith(f"comm-{t}-") for t in tags)]
        if scoped:  # keep tenant-wide only when the collection has no communities yet
            rows = scoped
    scored = []
    for row in rows:
        blob = (row.get("summary_text") or "") + " " + (row.get("citations_list") or "")
        overlap = len(q_tokens & _tokens(blob))
        scored.append((overlap, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    # If nothing overlaps, still return the top summaries — a thematic question
    # ("what are the main topics?") may share no tokens with any single theme.
    return [row for _score, row in scored[:limit]]


def themes_by_collection(conn, tenant_id: str = "default") -> list[dict]:
    """Community summaries grouped by the collection they came from — for browsing.

    community_id embeds a graph tag, not a collection name, so this resolves each
    collection to its graph tag(s) forward (via the node->chunk join) and buckets
    the summaries under it. The live project_id is inconsistent — sometimes the
    collection_id, sometimes the collection name — so both are tried and unioned.
    Any summary whose graph does not resolve to a collection is surfaced under
    '(unmatched)' rather than dropped, so the view never silently hides a theme.
    """
    rows = [
        (dict(r) if hasattr(r, "keys") else {"community_id": r[0], "summary_text": r[1], "citations_list": r[2]})
        for r in conn.execute(
            "SELECT community_id, summary_text, citations_list "
            "FROM dic_community_summaries WHERE tenant_id = %s ORDER BY community_id",
            (tenant_id,),
        )
    ]
    from collections import defaultdict

    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        parts = (r.get("community_id") or "").split("-")
        if len(parts) >= 2:
            try:
                cites = json.loads(r.get("citations_list") or "[]")
            except (json.JSONDecodeError, TypeError):
                cites = []
            by_tag[parts[1]].append({"summary": r.get("summary_text") or "", "entities": cites[:8]})

    colls = [
        (dict(r) if hasattr(r, "keys") else {"collection_id": r[0], "name": r[1]})
        for r in conn.execute(
            "SELECT collection_id, name FROM dic_collections WHERE tenant_id = %s ORDER BY name",
            (tenant_id,),
        )
    ]

    out: list[dict] = []
    used: set[str] = set()
    for coll in colls:
        tags = _graph_tags_for_collection(conn, coll["collection_id"]) | _graph_tags_for_collection(conn, coll.get("name") or "")
        themes: list[dict] = []
        for t in tags:
            themes.extend(by_tag.get(t, []))
            used.add(t)
        if themes:
            out.append({"collection": coll.get("name") or coll["collection_id"], "themes": themes})

    orphan = [th for tag, ths in by_tag.items() if tag not in used for th in ths]
    if orphan:
        out.append({"collection": "(unmatched)", "themes": orphan})
    out.sort(key=lambda c: (c["collection"] == "(unmatched)", -len(c["themes"])))
    return out


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="GraphRAG community engine for DIC")
    p.add_argument("--build", action="store_true", help="Detect + summarise + store communities")
    p.add_argument("--search", metavar="QUERY", help="Search community summaries")
    p.add_argument("--tenant", default="default")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    from tools.db.storage import get_connection

    conn = get_connection()
    if args.build:
        stats = build_communities(conn, tenant_id=args.tenant)
        print(json.dumps(stats) if args.as_json else f"communities built: {stats}")
        return 0
    if args.search:
        results = search_communities(conn, args.search, tenant_id=args.tenant)
        if args.as_json:
            print(json.dumps(results, default=str, indent=2))
        else:
            for r in results:
                print(f"- {r['summary_text']}")
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
