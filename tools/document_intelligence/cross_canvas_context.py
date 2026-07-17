# CUI // SP-CTI
"""Cross-canvas RAG+KG context for Document Intelligence Canvas (DIC).

When DIC generates or regenerates a document, it should not be limited to its own
collection — a *networking* document should be informed by the Network Design
Canvas (NDC) and the Migration Canvas, and a *security* document by the Security
Canvas (SDC) and the compliance crosswalk. This module is the bridge: given a
query and a set of target canvases, it pulls relevant context from the shared
knowledge graph (and, best-effort, the RAG vector store) and returns it in a shape
DIC's generator/verifier already understand.

Design notes:
- **Primary source = the shared KG** (``kg_graphs`` / ``kg_nodes`` / ``kg_edges``),
  which already holds NDC, SDC, compliance, IDC, migration entities. A deterministic
  lexical query over it is the reliable backstop that needs no embedding service.
- **Best-effort enrichment** via ``knowledge_graph.federation.federated_search`` and
  the RAG retriever, both wrapped in try/except so an unavailable service never
  breaks document generation.
- Everything degrades to "no extra context" silently — cross-canvas context is
  additive, never required.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Routing: which canvases inform which kind of document.
# Keyed by substrings matched against the collection_id and the query/heading.
# Each canvas key maps to a KG-graph selector (project_ids + name LIKE patterns)
# and RAG source_types in resolve below.
# --------------------------------------------------------------------------- #
CONTEXT_ROUTES: list[tuple[tuple[str, ...], list[str]]] = [
    # (trigger keywords, canvases to pull from)
    (("network", "net_", "ndc", "topolog", "bgp", "vlan", "subnet", "routing", "firewall"),
     ["ndc", "mdc", "idc"]),
    (("migration", "cutover", "7r", "rehost", "replatform"),
     ["mdc", "ndc", "idc"]),
    (("security", "sdc", "threat", "attack", "vuln", "zero trust", "ztna", "hardening"),
     ["sdc", "compliance"]),
    (("compliance", "nist", "rmf", "ato", "control", "ssp", "poam", "stig", "fedramp", "cmmc"),
     ["compliance", "sdc"]),
]

# Canvas key -> (KG project_ids, KG graph-name LIKE patterns, RAG source_types).
CANVAS_KG_SELECTORS: dict[str, tuple[list[str], list[str], list[str]]] = {
    "ndc": (["ndc", "ndc-network-intelligence"], ["%network%"],
            ["ndc_designs", "ndc_compliance", "ndc_configs", "network_devices",
             "network_intelligence_analyses"]),
    "mdc": (["mdc", "mdc-designs", "migration_canvas"], ["%migration%"],
            ["migration_designs", "mdc_designs"]),
    "idc": (["idc-designs"], ["%infrastructure design%"], ["idc_designs"]),
    "sdc": (["sdc", "sdc-designs"], ["%sdc%", "%security%"],
            ["sdc_designs", "sdc_assessments"]),
    "compliance": ([], ["%compliance%", "%crosswalk%"], ["compliance_artifacts"]),
}

DISPLAY = {
    "ndc": "Network Design Canvas", "mdc": "Migration Canvas",
    "idc": "Infrastructure Design Canvas", "sdc": "Security Design Canvas",
    "compliance": "Compliance Crosswalk",
}

_MAX_PER_CANVAS = 4
_MAX_TOTAL = 10


@dataclass
class CrossCanvasEvidence:
    """Cross-canvas context ready to merge into DIC generation."""
    block: str = ""                         # formatted text appended to the LLM prompt
    texts: list[str] = field(default_factory=list)       # raw chunk texts for the verifier
    citations: list[dict] = field(default_factory=list)  # citation dicts for persistence
    sources: list[dict] = field(default_factory=list)    # {canvas, label, kind} for reporting
    canvases: list[str] = field(default_factory=list)

    @property
    def found(self) -> int:
        return len(self.texts)


def resolve_context_canvases(collection_id: str | None, query: str = "") -> list[str]:
    """Decide which canvases should inform a document, from collection + query text.

    Returns a de-duplicated, order-preserving list of canvas keys. Empty when the
    document is not topically tied to another canvas.
    """
    hay = f"{collection_id or ''} {query or ''}".lower()
    out: list[str] = []
    for triggers, canvases in CONTEXT_ROUTES:
        if any(t in hay for t in triggers):
            for c in canvases:
                if c not in out:
                    out.append(c)
    return out


def _keywords(text: str) -> list[str]:
    toks = re.findall(r"[A-Za-z0-9]{4,}", (text or "").lower())
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:8]


def _kg_lexical(conn, canvas: str, keywords: list[str], limit: int) -> list[dict]:
    """Deterministic lexical search over the shared KG for one canvas.

    No embeddings required — matches keywords against node label/properties within
    the canvas's graphs (by project_id or graph-name pattern), ranked by centrality.
    """
    project_ids, name_patterns, _ = CANVAS_KG_SELECTORS.get(canvas, ([], [], []))
    graph_clauses, gparams = [], []
    for pid in project_ids:
        graph_clauses.append("g.project_id = ?")
        gparams.append(pid)
    for pat in name_patterns:
        graph_clauses.append("LOWER(g.name) LIKE ?")
        gparams.append(pat.lower())
    if not graph_clauses:
        return []
    graph_where = "(" + " OR ".join(graph_clauses) + ")"

    kw_clauses, kparams = [], []
    for kw in keywords:
        kw_clauses.append("(LOWER(n.label) LIKE ? OR LOWER(n.properties) LIKE ?)")
        kparams.extend([f"%{kw}%", f"%{kw}%"])
    kw_where = ("(" + " OR ".join(kw_clauses) + ")") if kw_clauses else "1=1"

    sql = (
        "SELECT n.id, n.label, n.entity_type, n.properties, g.name "
        "FROM kg_nodes n JOIN kg_graphs g ON g.id = n.graph_id "
        f"WHERE {graph_where} AND {kw_where} "
        "ORDER BY COALESCE(n.centrality, 0) DESC LIMIT ?"
    )
    try:
        rows = conn.execute(sql, tuple(gparams + kparams + [limit])).fetchall()
    except Exception as exc:
        logger.warning("cross_canvas: KG lexical query failed for %s: %s", canvas, exc)
        return []

    out = []
    for r in rows:
        node_id, label, etype, props, gname = r[0], r[1], r[2], r[3], r[4]
        detail = ""
        if props:
            try:
                pd = json.loads(props) if isinstance(props, str) else dict(props)
                bits = [f"{k}={v}" for k, v in list(pd.items())[:4]
                        if isinstance(v, (str, int, float)) and str(v).strip()]
                detail = "; ".join(bits)
            except Exception:
                detail = str(props)[:200]
        out.append({"node_id": node_id, "label": label, "entity_type": etype or "entity",
                    "detail": detail, "graph": gname})
    return out


def _neighbors(conn, node_ids: list[str], limit: int = 3) -> dict[str, list[str]]:
    """Best-effort 1-hop neighbor labels for matched nodes (relationship context)."""
    if not node_ids:
        return {}
    out: dict[str, list[str]] = {}
    try:
        placeholders = ",".join("?" for _ in node_ids)
        rows = conn.execute(
            "SELECT e.source_id, e.relationship, n.label "
            "FROM kg_edges e JOIN kg_nodes n ON n.id = e.target_id "
            f"WHERE e.source_id IN ({placeholders}) LIMIT %s",
            tuple(node_ids) + (limit * len(node_ids),),
        ).fetchall()
        for src, rel, tgt_label in rows:
            out.setdefault(src, [])
            if len(out[src]) < limit:
                out[src].append(f"{rel}: {tgt_label}" if rel else str(tgt_label))
    except Exception:
        pass
    return out


def gather(query: str, canvases: list[str], *, tenant_id: str = "default",
           top_k: int = _MAX_TOTAL, conn=None, use_rag: bool = True) -> CrossCanvasEvidence:
    """Gather cross-canvas context for ``query`` from the given canvases.

    KG (deterministic lexical + best-effort federation) plus best-effort RAG.
    Always returns a CrossCanvasEvidence; never raises. ``use_rag=False`` restricts
    to the deterministic KG path (used by hermetic tests).
    """
    ev = CrossCanvasEvidence(canvases=list(canvases))
    if not canvases:
        return ev

    keywords = _keywords(query)
    own_conn = conn is None
    if own_conn:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
        except Exception as exc:
            logger.warning("cross_canvas: no DB connection: %s", exc)
            return ev

    lines: list[str] = []
    try:
        per = max(1, min(_MAX_PER_CANVAS, top_k // max(1, len(canvases)) + 1))
        for canvas in canvases:
            nodes = _kg_lexical(conn, canvas, keywords, per)
            nbrs = _neighbors(conn, [n["node_id"] for n in nodes])
            for n in nodes:
                if len(ev.texts) >= top_k:
                    break
                rel = nbrs.get(n["node_id"], [])
                rel_txt = (" | related: " + "; ".join(rel)) if rel else ""
                text = (f"[{DISPLAY.get(canvas, canvas)}] {n['label']} "
                        f"({n['entity_type']})"
                        + (f" — {n['detail']}" if n['detail'] else "")
                        + rel_txt)
                ev.texts.append(text)
                lines.append(text)
                ev.sources.append({"canvas": canvas, "label": n["label"],
                                   "entity_type": n["entity_type"], "kind": "kg"})
                ev.citations.append({
                    "doc_title": f"{DISPLAY.get(canvas, canvas)} · {n['label']}",
                    "doc_id": f"kg:{canvas}:{n['node_id']}",
                    "chunk_id": str(n["node_id"]),
                    "source_uri": f"canvas://{canvas}",
                    "page": None,
                    "canvas": canvas,
                    "kind": "kg",
                })
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    # Best-effort RAG enrichment (skips silently if vector store unavailable/empty).
    try:
        rag_sources = []
        if use_rag:
            for c in canvases:
                rag_sources.extend(CANVAS_KG_SELECTORS.get(c, ([], [], []))[2])
        if rag_sources and len(ev.texts) < top_k:
            from tools.rag.retriever import RAGRetriever
            results = RAGRetriever(tenant_id=tenant_id).search(
                query, top_k=top_k - len(ev.texts), source_types=rag_sources)
            for r in results or []:
                content = getattr(r, "content", "") or ""
                if not content.strip():
                    continue
                st = getattr(r, "source_type", "rag")
                ev.texts.append(f"[RAG:{st}] {content[:400]}")
                lines.append(f"[RAG:{st}] {content[:300]}")
                ev.sources.append({"canvas": st, "label": getattr(r, "doc_title", st),
                                   "kind": "rag"})
                ev.citations.append({
                    "doc_title": getattr(r, "doc_title", st),
                    "doc_id": getattr(r, "doc_id", ""),
                    "chunk_id": getattr(r, "chunk_id", ""),
                    "source_uri": f"rag://{st}",
                    "page": getattr(r, "page", None),
                    "kind": "rag",
                })
    except Exception as exc:
        logger.debug("cross_canvas: RAG enrichment skipped: %s", exc)

    if lines:
        ev.block = ("\n\nCROSS-CANVAS CONTEXT (from other ICDEV canvases — cite as "
                    "[canvas: <name>]):\n" + "\n".join(f"- {ln}" for ln in lines))
    return ev


def gather_for_collection(query: str, collection_id: str | None, *,
                          tenant_id: str = "default", conn=None) -> CrossCanvasEvidence:
    """Convenience: resolve canvases from the collection+query, then gather."""
    canvases = resolve_context_canvases(collection_id, query)
    return gather(query, canvases, tenant_id=tenant_id, conn=conn)
