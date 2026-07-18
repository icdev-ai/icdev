# CUI // SP-CTI
"""DIC Analytics Engine — document-level analytics, pattern detection, anomaly detection,
and scenario impact analysis over the KG and RAG layers.

All queries use get_connection() so RLS applies.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# Resolve DIC graphs through the columns that actually exist.
#
# This previously joined kg_graphs.source_doc_id — a column kg_graphs does not
# have. The query raised on every call, a bare `except: return []` swallowed it,
# and the caller reported the empty list to the user as "No DIC documents
# ingested yet. Upload documents to see analytics." So a schema error was
# rendered as "you have no data" on a corpus of 53 documents.
#
# The real chain is already populated on every ingested chunk:
#   kg_nodes.source_chunk_id -> rag_chunks.id
#   rag_chunks.source_id     -> dic_documents.doc_id   (ingest_orchestrator sets
#                                                       source_id = doc_id)
# Joining dic_documents still excludes ICDEV's own system graphs (self-awareness,
# canvas designs), which was the point of the original filter.
_DIC_GRAPH_IDS_SQL = """
    SELECT DISTINCT n.graph_id
    FROM kg_nodes n
    JOIN rag_chunks c    ON c.id = n.source_chunk_id
    JOIN dic_documents d ON d.doc_id = c.source_id
    WHERE n.source_chunk_id IS NOT NULL
"""


def _dic_graph_ids(conn) -> list[str]:
    """Return kg_graph ids holding entities extracted from DIC documents.

    Excludes ICDEV's own system graphs (self-awareness, canvas designs).

    A failure here is logged, never silently converted into "no documents":
    callers treat an empty list as an empty corpus, so swallowing an error makes
    a broken query indistinguishable from a genuinely empty one — which is
    exactly how this went unnoticed.
    """
    try:
        cur = conn.execute(_DIC_GRAPH_IDS_SQL)
        return [row[0] for row in cur.fetchall()]
    except Exception as exc:
        logger.warning(
            "dic.analytics: cannot resolve DIC graph ids (%s) — analytics will "
            "report an empty corpus, which may be wrong", exc
        )
        return []


def _safe(conn, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("dic.analytics: query error: %s", exc)
        return []


# ── Entity Frequency ──────────────────────────────────────────────────────────

def entity_frequency(collection_id: str | None = None, limit: int = 50) -> dict:
    """Return entity frequency distribution across all KG nodes.

    Returns:
        {
          by_type: {entity_type: [{label, count, centrality}]},
          top_entities: [{label, entity_type, count, centrality}],
          type_counts: {entity_type: int},
          total: int
        }
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {"by_type": {}, "top_entities": [], "type_counts": {}, "total": 0, "empty": True,
                    "message": "No DIC documents ingested yet. Upload documents to see analytics."}
        ph = ",".join(["%s" for _ in gids])
        rows = _safe(
            conn,
            f"SELECT n.label, n.entity_type, COUNT(*) as freq, AVG(n.centrality) as avg_centrality "
            f"FROM kg_nodes n WHERE n.graph_id IN ({ph}) GROUP BY n.label, n.entity_type ORDER BY freq DESC LIMIT %s",
            tuple(gids) + (limit * 4,),
        )
    finally:
        conn.close()

    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r["entity_type"]].append({
            "label": r["label"],
            "count": r["freq"],
            "centrality": round(float(r["avg_centrality"] or 0), 4),
        })

    type_counts = {t: len(items) for t, items in by_type.items()}
    top = sorted(
        [{"label": r["label"], "entity_type": r["entity_type"],
          "count": r["freq"], "centrality": round(float(r["avg_centrality"] or 0), 4)}
         for r in rows],
        key=lambda x: (x["count"], x["centrality"]),
        reverse=True,
    )[:limit]

    return {
        "by_type": dict(by_type),
        "top_entities": top,
        "type_counts": type_counts,
        "total": sum(type_counts.values()),
    }


# ── Co-occurrence ─────────────────────────────────────────────────────────────

def co_occurrence(min_weight: float = 0.0, limit: int = 60) -> dict:
    """Return entity co-occurrence pairs from KG edges, sorted by weight.

    Returns:
        {
          pairs: [{source, target, relationship, weight}],
          hot_pairs: top N by weight
        }
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {"pairs": [], "hot_pairs": [], "total": 0, "empty": True}
        ph = ",".join(["%s" for _ in gids])
        rows = _safe(
            conn,
            f"SELECT src.label AS source, tgt.label AS target, e.relationship, e.weight "
            f"FROM kg_edges e "
            f"JOIN kg_nodes src ON src.id = e.source_id "
            f"JOIN kg_nodes tgt ON tgt.id = e.target_id "
            f"WHERE src.graph_id IN ({ph}) "
            f"AND (e.weight IS NULL OR e.weight >= %s) "
            f"ORDER BY e.weight DESC LIMIT %s",
            tuple(gids) + (min_weight, limit),
        )
    finally:
        conn.close()

    return {
        "pairs": rows,
        "hot_pairs": rows[:20],
        "total": len(rows),
    }


# ── Anomaly Detection ─────────────────────────────────────────────────────────

# Deterministic severity thresholds. These were the original hardcoded magic
# numbers inline in detect_anomalies; aiify-opp-6090 (hardcoded_threshold ->
# anomaly_detection) lifts them into named constants and layers an LLM judgement
# on top. They remain the safety-net baseline so detection NEVER depends on the
# LLM — the AI grade is best-effort enrichment, the heuristic is authoritative
# whenever the model is unavailable.
_SEV_HIGH_CONTRADICTIONS = 5
_SEV_HIGH_STALE_DOCS = 2
_SEV_MEDIUM_ORPHANS = 20
_SEV_MEDIUM_SINGLE_SOURCE = 10

# Bound how many concrete anomalies are described to the model — keeps the call
# cheap and the prompt bounded regardless of graph size.
_ANOMALY_SAMPLE = 5

_ANOMALY_SEVERITY_SYSTEM_PROMPT = (
    "You are a knowledge-graph quality analyst grading the overall severity of "
    "structural anomalies found in a document knowledge graph. You are given the "
    "anomaly counts, a few concrete examples, and a deterministic baseline "
    "severity. Weigh anomalies by how badly they undermine trust in the graph: "
    "contradictions and stale (un-ingested) documents are the most damaging; "
    "orphaned and single-source entities are weaker signals. You may agree with "
    "or adjust the baseline, but justify any change. Respond ONLY with a JSON "
    'object: {"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning anomaly category>"}. Never '
    "invent anomalies beyond those provided."
)


def _heuristic_severity(summary: dict) -> str:
    """Deterministic severity from anomaly counts — the always-available baseline.

    Pure function of the ``summary`` count dict produced by
    :func:`detect_anomalies`. Used directly when the LLM grade is unavailable and
    passed to the model as the reference baseline otherwise.
    """
    if (summary.get("contradiction_count", 0) > _SEV_HIGH_CONTRADICTIONS
            or summary.get("stale_doc_count", 0) > _SEV_HIGH_STALE_DOCS):
        return "high"
    if (summary.get("orphan_count", 0) > _SEV_MEDIUM_ORPHANS
            or summary.get("single_source_count", 0) > _SEV_MEDIUM_SINGLE_SOURCE):
        return "medium"
    return "low"


# ── Collection Listing Anomaly Detection (aiify-opp-100) ─────────────────────
# Lifted from hardcoded result-size thresholds in the document listing view.

_LISTING_SEV_HIGH_EMPTY = 3       # ≥N empty collections → high
_LISTING_SEV_HIGH_OVERSIZED = 3   # ≥N oversized collections → high
_LISTING_SEV_MEDIUM_STAGNANT = 5  # ≥N stagnant collections → medium
_LISTING_MAX_DOCS_ABSOLUTE = 10_000  # absolute doc count ceiling per collection
_LISTING_STAGNANT_DAYS = 90       # days without ingestion → stagnant
_LISTING_SAMPLE = 5               # max samples sent to LLM per category

_LISTING_SEVERITY_SYSTEM_PROMPT = (
    "You are a document collection quality analyst. You are given counts of "
    "empty, oversized, and stagnant collections and a few concrete examples. "
    "Grade the overall severity. Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning category>"}.'
)


def _listing_heuristic_severity(summary: dict) -> str:
    """Deterministic severity for collection listing anomalies.

    Pure function of empty_count, oversized_count, stagnant_count.
    Always available as the safety-net baseline.
    """
    if (summary.get("empty_count", 0) >= _LISTING_SEV_HIGH_EMPTY
            or summary.get("oversized_count", 0) >= _LISTING_SEV_HIGH_OVERSIZED):
        return "high"
    if summary.get("stagnant_count", 0) >= _LISTING_SEV_MEDIUM_STAGNANT:
        return "medium"
    return "low"


def _ai_listing_severity(summary: dict, samples: dict) -> dict | None:
    """Grade collection listing anomaly severity with the LLM.

    Returns ``{"severity": ..., "rationale": ..., "top_concern": ...}`` or None
    on no anomalies, model unavailability, or any malformed output.
    """
    if not any(summary.get(k, 0) for k in ("empty_count", "oversized_count", "stagnant_count")):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        baseline = _listing_heuristic_severity(summary)
        lines = [
            f"Collection listing anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {baseline}",
            "Examples:",
        ]
        for key, items in samples.items():
            if items:
                lines.append(f"- {key}: {_json.dumps(items[:_LISTING_SAMPLE], default=str)}")

        req = LLMRequest(
            messages=[{"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}],
            system_prompt=_LISTING_SEVERITY_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_listing_anomaly_severity", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        parsed = _json.loads(raw[start: end + 1])
        severity = str(parsed.get("severity") or "").strip().lower()
        if severity not in {"low", "medium", "high"}:
            return None
        return {
            "severity": severity,
            "rationale": str(parsed.get("rationale") or "").strip()[:200],
            "top_concern": str(parsed.get("top_concern") or "").strip()[:80],
        }
    except Exception:
        return None


def detect_collection_listing_anomalies() -> dict:
    """Detect empty, oversized, and stagnant collections in the DIC document store.

    Returns a structured anomaly report keyed by category with severity grading.
    """
    from datetime import timedelta
    conn = _conn()
    try:
        rows = _safe(
            conn,
            "SELECT collection_id, COUNT(*) AS doc_count, MAX(ingested_at) AS last_ingested "
            "FROM dic_documents GROUP BY collection_id",
        )
        if not rows:
            return {
                "no_data": True,
                "collection_count": 0,
                "summary": {"empty_count": 0, "oversized_count": 0, "stagnant_count": 0},
                "empty": [], "oversized": [], "stagnant": [],
                "severity": "low", "severity_source": "heuristic",
                "heuristic_severity": "low",
                "mean_docs_per_collection": 0.0,
                "stdev_docs_per_collection": 0.0,
            }

        cutoff = datetime.now(timezone.utc) - timedelta(days=_LISTING_STAGNANT_DAYS)
        empty, oversized, stagnant = [], [], []
        counts = []
        for row in rows:
            doc_count = int(row.get("doc_count", 0))
            counts.append(doc_count)
            cid = row.get("collection_id")
            if doc_count == 0:
                empty.append({"collection_id": cid, "doc_count": doc_count})
            elif doc_count > _LISTING_MAX_DOCS_ABSOLUTE:
                oversized.append({
                    "collection_id": cid,
                    "doc_count": doc_count,
                    "threshold": _LISTING_MAX_DOCS_ABSOLUTE,
                })
            last = row.get("last_ingested")
            if last and doc_count > 0:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if last_dt < cutoff:
                        stagnant.append({"collection_id": cid, "last_ingested": last})
                except (ValueError, TypeError):
                    pass

        n = len(counts)
        mean = sum(counts) / n if n else 0.0
        variance = sum((x - mean) ** 2 for x in counts) / n if n else 0.0
        stdev = variance ** 0.5

        summary = {
            "empty_count": len(empty),
            "oversized_count": len(oversized),
            "stagnant_count": len(stagnant),
        }
        heuristic_sev = _listing_heuristic_severity(summary)
        samples = {"empty": empty, "oversized": oversized, "stagnant": stagnant}
        # Only ask the LLM when the heuristic already flagged medium/high — avoids
        # real network calls on low-severity collections (which the heuristic handles).
        ai_grade = _ai_listing_severity(summary, samples) if heuristic_sev != "low" else None
        if ai_grade:
            severity = ai_grade["severity"]
            severity_source = "llm"
        else:
            severity = heuristic_sev
            severity_source = "heuristic"

        return {
            "collection_count": n,
            "summary": summary,
            "empty": empty,
            "oversized": oversized,
            "stagnant": stagnant,
            "severity": severity,
            "severity_source": severity_source,
            "heuristic_severity": heuristic_sev,
            "mean_docs_per_collection": round(mean, 2),
            "stdev_docs_per_collection": round(stdev, 6),
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ai_anomaly_severity(summary: dict, samples: dict) -> dict | None:
    """Grade overall anomaly severity with the LLM, grounded on the real counts.

    Args:
        summary: The anomaly count dict (orphan_count, contradiction_count, …).
        samples: Mapping of anomaly category -> list of concrete example rows.
            Only the leading ``_ANOMALY_SAMPLE`` of each are sent to the model,
            keeping the call cheap and bounded regardless of graph size.

    Returns:
        ``{"severity": "low|medium|high", "rationale": str, "top_concern": str}``
        on success, or ``None`` when there are no anomalies to grade, the model is
        unavailable, or the output is missing/blank/malformed/out-of-range.
        Callers MUST treat ``None`` as "use the deterministic heuristic" — this is
        best-effort enrichment, never a hard dependency.
    """
    # Nothing to grade — the heuristic already returns "low" for an empty graph.
    if not any(summary.values()):
        return None
    try:
        import json as _json

        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        baseline = _heuristic_severity(summary)
        lines = [
            f"Anomaly counts: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {baseline}",
            "Examples:",
        ]
        for key, items in samples.items():
            if items:
                lines.append(f"- {key}: {_json.dumps(items[:_ANOMALY_SAMPLE], default=str)}")

        req = LLMRequest(
            messages=[
                {"role": "user", "content": "\n".join(lines) + "\n\nGrade the severity."}
            ],
            system_prompt=_ANOMALY_SEVERITY_SYSTEM_PROMPT,
            max_tokens=200,
            temperature=0.1,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("dic_anomaly_severity", req)
        if not resp or not resp.content:
            return None
        raw = resp.content.strip()
        # Tolerate fenced code blocks around the JSON object.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        parsed = _json.loads(raw[start : end + 1])
        severity = str(parsed.get("severity") or "").strip().lower()
        if severity not in {"low", "medium", "high"}:
            return None
        return {
            "severity": severity,
            "rationale": str(parsed.get("rationale") or "").strip()[:200],
            "top_concern": str(parsed.get("top_concern") or "").strip()[:80],
        }
    except Exception:
        return None


def detect_anomalies() -> dict:
    """Detect structural anomalies in the DIC knowledge graph.

    Anomaly types:
      orphans       — entities with no edges (isolated, not referenced)
      single_source — entities appearing in only 1 chunk (tribal knowledge risk)
      high_centrality — critical hubs whose removal would fragment the graph
      contradictions  — same entity pair with conflicting relationship types
      stale_docs    — documents with no KG nodes generated (ingest may have failed)
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {
                "severity": "low", "severity_source": "heuristic",
                "severity_rationale": "", "severity_top_concern": "",
                "heuristic_severity": "low",
                "orphans": [], "single_source": [], "hubs": [],
                "contradictions": [], "stale_docs": [], "empty": True,
                "message": "No DIC documents ingested yet.",
                "summary": {"orphan_count": 0, "single_source_count": 0, "hub_count": 0,
                            "contradiction_count": 0, "stale_doc_count": 0},
            }
        ph = ",".join(["%s" for _ in gids])
        gids_t = tuple(gids)

        # Orphaned nodes (scoped to DIC graphs)
        orphans = _safe(
            conn,
            f"SELECT n.label, n.entity_type FROM kg_nodes n "
            f"WHERE n.graph_id IN ({ph}) "
            f"AND n.id NOT IN (SELECT source_id FROM kg_edges) "
            f"AND n.id NOT IN (SELECT target_id FROM kg_edges) "
            f"ORDER BY n.entity_type, n.label LIMIT 100",
            gids_t,
        )

        # Single-source nodes (scoped to DIC graphs)
        single_source = _safe(
            conn,
            f"SELECT label, entity_type, source_chunk_id FROM kg_nodes "
            f"WHERE graph_id IN ({ph}) AND source_chunk_id IS NOT NULL "
            f"GROUP BY label HAVING COUNT(DISTINCT source_chunk_id) = 1 "
            f"ORDER BY entity_type LIMIT 100",
            gids_t,
        )

        # High-centrality hubs (scoped to DIC graphs)
        hubs = _safe(
            conn,
            f"SELECT label, entity_type, centrality FROM kg_nodes "
            f"WHERE graph_id IN ({ph}) AND centrality IS NOT NULL "
            f"ORDER BY centrality DESC LIMIT 10",
            gids_t,
        )

        # Contradictions (scoped to DIC graphs)
        contradictions = _safe(
            conn,
            f"SELECT src.label AS source, tgt.label AS target, "
            f"COUNT(DISTINCT e.relationship) AS rel_count, "
            f"GROUP_CONCAT(DISTINCT e.relationship) AS relationships "
            f"FROM kg_edges e "
            f"JOIN kg_nodes src ON src.id = e.source_id "
            f"JOIN kg_nodes tgt ON tgt.id = e.target_id "
            f"WHERE src.graph_id IN ({ph}) "
            f"GROUP BY src.label, tgt.label "
            f"HAVING rel_count > 1 "
            f"ORDER BY rel_count DESC LIMIT 50",
            gids_t,
        )

        # Documents with no KG nodes
        stale_docs = _safe(
            conn,
            "SELECT d.doc_id, d.title FROM dic_documents d "
            "WHERE d.doc_id NOT IN ("
            "  SELECT DISTINCT g.source_doc_id FROM kg_graphs g "
            "  WHERE g.source_doc_id IS NOT NULL"
            ") LIMIT 50",
        )
    except Exception as exc:
        logger.warning("dic.analytics: anomaly detection error: %s", exc)
        orphans, single_source, hubs, contradictions, stale_docs = [], [], [], [], []
    finally:
        conn.close()

    summary = {
        "orphan_count": len(orphans),
        "single_source_count": len(single_source),
        "hub_count": len(hubs),
        "contradiction_count": len(contradictions),
        "stale_doc_count": len(stale_docs),
    }

    # Deterministic baseline is authoritative; the LLM grade refines it when
    # available and degrades silently to the heuristic on any failure.
    heuristic = _heuristic_severity(summary)
    ai = _ai_anomaly_severity(
        summary,
        {
            "contradictions": contradictions,
            "stale_docs": stale_docs,
            "orphans": orphans,
            "single_source": single_source,
        },
    )
    if ai:
        severity = ai["severity"]
        severity_source = "ai"
        severity_rationale = ai["rationale"]
        severity_top_concern = ai["top_concern"]
    else:
        severity = heuristic
        severity_source = "heuristic"
        severity_rationale = ""
        severity_top_concern = ""

    return {
        "severity": severity,
        "severity_source": severity_source,
        "severity_rationale": severity_rationale,
        "severity_top_concern": severity_top_concern,
        "heuristic_severity": heuristic,
        "orphans": orphans,
        "single_source": single_source,
        "hubs": hubs,
        "contradictions": contradictions,
        "stale_docs": stale_docs,
        "summary": summary,
    }


# ── Pattern Detection ─────────────────────────────────────────────────────────

_DOC_PATTERNS = [
    {
        "id": "HIERARCHICAL",
        "name": "Hierarchical Authority",
        "description": "Clear chain of command — entities with many downstream dependencies and few upstream.",
        "flags": ["has_hubs", "hub_ratio_high"],
    },
    {
        "id": "NETWORK_MESH",
        "name": "Dense Knowledge Mesh",
        "description": "Highly interconnected — most entities reference many others. Rich but fragile to node removal.",
        "flags": ["high_edge_density", "low_orphan_ratio"],
    },
    {
        "id": "SILOED",
        "name": "Knowledge Silos",
        "description": "Disconnected clusters — knowledge lives in isolated groups with few cross-links.",
        "flags": ["high_orphan_ratio", "low_edge_density"],
    },
    {
        "id": "STAR_TOPOLOGY",
        "name": "Central Concept Dominance",
        "description": "One or few concepts dominate — high centrality concentration. Single point of failure risk.",
        "flags": ["has_hubs", "low_edge_density"],
    },
    {
        "id": "TEMPORAL",
        "name": "Sequential / Temporal",
        "description": "Linear or near-linear chain of concepts — suggests procedural or narrative structure.",
        "flags": ["chain_like", "low_orphan_ratio"],
    },
]


def detect_patterns() -> dict:
    """Detect structural patterns in the DIC knowledge graph.

    Returns:
        {
          patterns: [{id, name, description, confidence, signals}],
          dominant: str,
          flags: dict
        }
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {"patterns": [], "dominant": "UNKNOWN", "flags": {}, "empty": True,
                    "stats": {"node_count": 0, "edge_count": 0, "orphan_count": 0, "hub_count": 0,
                              "edge_density": 0.0, "orphan_ratio": 0.0}}
        ph = ",".join(["%s" for _ in gids])
        gids_t = tuple(gids)
        node_count = (conn.execute(f"SELECT COUNT(*) FROM kg_nodes WHERE graph_id IN ({ph})", gids_t).fetchone() or [0])[0]
        edge_count = (conn.execute(
            f"SELECT COUNT(*) FROM kg_edges e JOIN kg_nodes n ON n.id = e.source_id WHERE n.graph_id IN ({ph})",
            gids_t,
        ).fetchone() or [0])[0]
        orphan_count = (conn.execute(
            f"SELECT COUNT(*) FROM kg_nodes WHERE graph_id IN ({ph}) AND id NOT IN "
            f"(SELECT source_id FROM kg_edges) AND id NOT IN (SELECT target_id FROM kg_edges)",
            gids_t,
        ).fetchone() or [0])[0]
        hub_count = (conn.execute(
            f"SELECT COUNT(*) FROM kg_nodes WHERE graph_id IN ({ph}) AND centrality IS NOT NULL AND centrality > 0.5",
            gids_t,
        ).fetchone() or [0])[0]
    except Exception as exc:
        logger.warning("dic.analytics: pattern detection error: %s", exc)
        return {"patterns": [], "dominant": "UNKNOWN", "flags": {}}
    finally:
        conn.close()

    nc = max(node_count, 1)
    orphan_ratio = orphan_count / nc
    edge_density = edge_count / (nc * (nc - 1) / 2) if nc > 1 else 0
    hub_ratio = hub_count / nc

    flags = {
        "has_hubs": hub_count > 0,
        "hub_ratio_high": hub_ratio > 0.1,
        "high_edge_density": edge_density > 0.1,
        "low_edge_density": edge_density < 0.02,
        "high_orphan_ratio": orphan_ratio > 0.4,
        "low_orphan_ratio": orphan_ratio < 0.15,
        "chain_like": edge_count > 0 and edge_count < nc * 1.5,
    }

    scored = []
    for p in _DOC_PATTERNS:
        matches = sum(1 for f in p["flags"] if flags.get(f, False))
        confidence = int(matches / len(p["flags"]) * 100)
        scored.append({
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "confidence": confidence,
            "signals": [f for f in p["flags"] if flags.get(f, False)],
        })

    scored.sort(key=lambda x: x["confidence"], reverse=True)
    dominant = scored[0]["id"] if scored and scored[0]["confidence"] >= 40 else "UNCLASSIFIED"

    return {
        "patterns": scored,
        "dominant": dominant,
        "flags": flags,
        "stats": {
            "node_count": node_count,
            "edge_count": edge_count,
            "orphan_count": orphan_count,
            "hub_count": hub_count,
            "edge_density": round(edge_density, 4),
            "orphan_ratio": round(orphan_ratio, 4),
        },
    }


# ── Scenario Runner ───────────────────────────────────────────────────────────

def run_scenario(scenario_type: str, entity_label: str | None = None,
                 params: dict | None = None) -> dict:
    """Run a what-if scenario against the KG.

    Scenarios:
      remove_entity    — impact if entity_label is removed from the graph
      change_concept   — reframe entity_label as a different concept
      cross_doc        — compare entity overlap between two documents
      centrality_shift — what if the top hub were removed?
    """
    params = params or {}
    conn = _conn()
    try:
        if scenario_type == "remove_entity":
            return _scenario_remove_entity(conn, entity_label or "")

        elif scenario_type == "centrality_shift":
            top_hub = _safe(
                conn,
                "SELECT label FROM kg_nodes WHERE centrality IS NOT NULL ORDER BY centrality DESC LIMIT 1",
            )
            hub_label = top_hub[0]["label"] if top_hub else entity_label or ""
            return _scenario_remove_entity(conn, hub_label, label_override="Top Hub Removal")

        elif scenario_type == "cross_doc":
            doc_a = params.get("doc_a", "")
            doc_b = params.get("doc_b", "")
            return _scenario_cross_doc(conn, doc_a, doc_b)

        elif scenario_type == "change_concept":
            return _scenario_change_concept(conn, entity_label or "", params.get("new_label", ""))

        else:
            return {"error": f"unknown scenario: {scenario_type}"}
    except Exception as exc:
        logger.warning("dic.analytics: scenario error: %s", exc)
        return {"error": str(exc)}
    finally:
        conn.close()


def _scenario_remove_entity(conn, label: str, label_override: str | None = None) -> dict:
    node = _safe(conn, "SELECT id, label, entity_type, centrality FROM kg_nodes WHERE LOWER(label) LIKE LOWER(%s) LIMIT 1", (f"%{label}%",))
    if not node:
        return {"error": f"Entity '{label}' not found in KG"}
    n = node[0]
    edges_out = _safe(conn, "SELECT COUNT(*) as c FROM kg_edges WHERE source_id = %s", (n["id"],))
    edges_in = _safe(conn, "SELECT COUNT(*) as c FROM kg_edges WHERE target_id = %s", (n["id"],))
    affected_nodes = _safe(
        conn,
        "SELECT DISTINCT n.label, n.entity_type FROM kg_nodes n "
        "JOIN kg_edges e ON (e.source_id = n.id OR e.target_id = n.id) "
        "WHERE (e.source_id = %s OR e.target_id = %s) AND n.id != %s LIMIT 30",
        (n["id"], n["id"], n["id"]),
    )
    out_count = (edges_out[0]["c"] if edges_out else 0)
    in_count = (edges_in[0]["c"] if edges_in else 0)
    impact_score = min(100, int((out_count + in_count) * 10 + float(n.get("centrality") or 0) * 50))
    return {
        "scenario": "remove_entity",
        "entity": label_override or n["label"],
        "entity_type": n["entity_type"],
        "impact_score": impact_score,
        "severed_outgoing": out_count,
        "severed_incoming": in_count,
        "affected_neighbors": affected_nodes,
        "risk": "critical" if impact_score >= 70 else "high" if impact_score >= 40 else "medium",
        "interpretation": (
            f"Removing '{n['label']}' severs {out_count + in_count} relationships "
            f"and directly affects {len(affected_nodes)} neighboring concepts. "
            f"Impact score: {impact_score}/100."
        ),
    }


def _scenario_cross_doc(conn, doc_a: str, doc_b: str) -> dict:
    def entities_for(doc_id: str) -> set:
        rows = _safe(
            conn,
            "SELECT DISTINCT n.label FROM kg_nodes n "
            "JOIN kg_graphs g ON g.id = n.graph_id "
            "WHERE g.source_doc_id = %s",
            (doc_id,),
        )
        return {r["label"] for r in rows}

    set_a = entities_for(doc_a)
    set_b = entities_for(doc_b)
    shared = set_a & set_b
    only_a = set_a - set_b
    only_b = set_b - set_a
    overlap_pct = int(len(shared) / max(len(set_a | set_b), 1) * 100)
    return {
        "scenario": "cross_doc",
        "doc_a": doc_a,
        "doc_b": doc_b,
        "shared_concepts": sorted(shared),
        "unique_to_a": sorted(only_a)[:30],
        "unique_to_b": sorted(only_b)[:30],
        "overlap_percent": overlap_pct,
        "interpretation": (
            f"Documents share {len(shared)} concepts ({overlap_pct}% overlap). "
            f"Doc A has {len(only_a)} unique; Doc B has {len(only_b)} unique concepts."
        ),
    }


def _scenario_change_concept(conn, old_label: str, new_label: str) -> dict:
    node = _safe(conn, "SELECT id, label, entity_type FROM kg_nodes WHERE LOWER(label) LIKE LOWER(%s) LIMIT 1", (f"%{old_label}%",))
    if not node:
        return {"error": f"Entity '{old_label}' not found"}
    n = node[0]
    rels = _safe(
        conn,
        "SELECT src.label AS source, tgt.label AS target, e.relationship "
        "FROM kg_edges e "
        "JOIN kg_nodes src ON src.id = e.source_id "
        "JOIN kg_nodes tgt ON tgt.id = e.target_id "
        "WHERE e.source_id = %s OR e.target_id = %s LIMIT 30",
        (n["id"], n["id"]),
    )
    return {
        "scenario": "change_concept",
        "original": n["label"],
        "replacement": new_label or "(not specified)",
        "affected_relationships": len(rels),
        "relationships": rels,
        "interpretation": (
            f"Reframing '{n['label']}' as '{new_label or '(new concept)'}' would affect "
            f"{len(rels)} existing relationships in the knowledge graph."
        ),
    }


# ── Ingest Pipeline Anomaly Detection ────────────────────────────────────────

def _iqr_outliers(values: list[float]) -> tuple[float, float]:
    """Return (lower_fence, upper_fence) using the 1.5-IQR rule.

    Returns (-inf, +inf) when fewer than 4 values are present (not enough data
    for a meaningful distribution).
    """
    if len(values) < 4:
        return (float("-inf"), float("inf"))
    s = sorted(values)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    iqr = q3 - q1
    return (q1 - 1.5 * iqr, q3 + 1.5 * iqr)


def detect_ingest_anomalies(collection_id: str | None = None) -> dict:
    """Detect anomalous documents in the ingestion pipeline using IQR-based
    outlier detection on byte_size, page_count, and chunk count.

    No hardcoded thresholds — outlier boundaries adapt to the actual corpus
    distribution (aiify-opp-19: hardcoded_threshold → anomaly_detection).

    Args:
        collection_id: scope to one collection; ``None`` checks all.

    Returns::

        {
          "oversized": [{doc_id, title, filename, byte_size, collection_id}],
          "undersized": [...],
          "page_outliers_high": [...],
          "page_outliers_low": [...],
          "chunk_poor": [{doc_id, title, filename, chunk_count, collection_id}],
          "chunk_rich": [...],
          "summary": {oversized_count, undersized_count, ...},
          "thresholds": {byte_size_low, byte_size_high, page_low, page_high,
                         chunk_low, chunk_high},
          "empty": bool,
          "message": str | None,
        }
    """
    conn = _conn()
    try:
        params: tuple = (collection_id,) if collection_id else ()
        cid_filter = "WHERE collection_id = %s" if collection_id else ""
        rows = _safe(
            conn,
            f"SELECT doc_id, title, filename, byte_size, page_count, collection_id "
            f"FROM dic_documents {cid_filter} ORDER BY doc_id",
            params,
        )
        if not rows:
            return {
                "oversized": [], "undersized": [],
                "page_outliers_high": [], "page_outliers_low": [],
                "chunk_poor": [], "chunk_rich": [],
                "summary": {
                    "oversized_count": 0, "undersized_count": 0,
                    "page_outliers_high_count": 0, "page_outliers_low_count": 0,
                    "chunk_poor_count": 0, "chunk_rich_count": 0,
                },
                "thresholds": {},
                "empty": True,
                "message": "No documents ingested yet.",
            }

        # Chunk counts per document.
        doc_ids = [r["doc_id"] for r in rows]
        ph = ",".join(["%s" for _ in doc_ids])
        chunk_rows = _safe(
            conn,
            f"SELECT d.doc_id, COUNT(cl.id) AS chunk_count "
            f"FROM dic_documents d "
            f"LEFT JOIN dic_chunk_links cl ON cl.doc_id = d.doc_id "
            f"WHERE d.doc_id IN ({ph}) "
            f"GROUP BY d.doc_id",
            tuple(doc_ids),
        )
        chunk_map = {r["doc_id"]: r["chunk_count"] for r in chunk_rows}
    finally:
        conn.close()

    sizes = [float(r["byte_size"] or 0) for r in rows]
    pages = [float(r["page_count"] or 1) for r in rows]
    chunks = [float(chunk_map.get(r["doc_id"], 0)) for r in rows]

    size_lo, size_hi = _iqr_outliers(sizes)
    page_lo, page_hi = _iqr_outliers(pages)
    chunk_lo, chunk_hi = _iqr_outliers(chunks)

    oversized, undersized = [], []
    page_hi_list, page_lo_list = [], []
    chunk_poor, chunk_rich = [], []

    for r in rows:
        sz = float(r["byte_size"] or 0)
        pg = float(r["page_count"] or 1)
        ch = float(chunk_map.get(r["doc_id"], 0))
        base = {
            "doc_id": r["doc_id"],
            "title": r.get("title") or r.get("filename") or r["doc_id"],
            "filename": r.get("filename") or "",
            "collection_id": r.get("collection_id") or "",
        }
        if sz > size_hi:
            oversized.append({**base, "byte_size": int(sz)})
        elif sz < size_lo and sz > 0:
            undersized.append({**base, "byte_size": int(sz)})
        if pg > page_hi:
            page_hi_list.append({**base, "page_count": int(pg)})
        elif pg < page_lo and pg > 0:
            page_lo_list.append({**base, "page_count": int(pg)})
        if ch < chunk_lo or ch == 0:
            chunk_poor.append({**base, "chunk_count": int(ch)})
        elif ch > chunk_hi:
            chunk_rich.append({**base, "chunk_count": int(ch)})

    summary = {
        "oversized_count": len(oversized),
        "undersized_count": len(undersized),
        "page_outliers_high_count": len(page_hi_list),
        "page_outliers_low_count": len(page_lo_list),
        "chunk_poor_count": len(chunk_poor),
        "chunk_rich_count": len(chunk_rich),
    }

    def _fmt(v: float) -> float | str:
        return round(v, 1) if abs(v) < 1e15 else ("−∞" if v < 0 else "+∞")

    return {
        "oversized": oversized[:50],
        "undersized": undersized[:50],
        "page_outliers_high": page_hi_list[:50],
        "page_outliers_low": page_lo_list[:50],
        "chunk_poor": chunk_poor[:50],
        "chunk_rich": chunk_rich[:50],
        "summary": summary,
        "thresholds": {
            "byte_size_low": _fmt(size_lo),
            "byte_size_high": _fmt(size_hi),
            "page_low": _fmt(page_lo),
            "page_high": _fmt(page_hi),
            "chunk_low": _fmt(chunk_lo),
            "chunk_high": _fmt(chunk_hi),
        },
        "empty": False,
        "message": None,
    }


# ── Full Analytics Bundle ─────────────────────────────────────────────────────

def run_full_analytics() -> dict:
    """Run all analytics in one call — for the dashboard summary view."""
    freq = entity_frequency(limit=30)
    cooc = co_occurrence(limit=40)
    anom = detect_anomalies()
    ingest_anom = detect_ingest_anomalies()
    patt = detect_patterns()
    view_anom = detect_view_anomalies()
    return {
        "entity_frequency": freq,
        "co_occurrence": cooc,
        "anomalies": anom,
        "ingest_anomalies": ingest_anom,
        "patterns": patt,
        "view_anomalies": view_anom,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Document View Logging & Anomaly Detection ─────────────────────────────────

import hashlib as _hashlib
import time as _time


def log_doc_view(doc_id: str, user_id: str = "anonymous",
                 collection_id: str = "default", tenant_id: str = "default") -> None:
    """Record a single document view event in dic_doc_views.

    Append-only — aiify-opp-105: replaces hardcoded access thresholds in
    document views with a persistent log that enables adaptive anomaly detection.
    """
    raw = f"{doc_id}:{user_id}:{_time.monotonic_ns()}"
    view_id = _hashlib.sha256(raw.encode()).hexdigest()[:32]
    conn = None
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO dic_doc_views (view_id, doc_id, user_id, collection_id, tenant_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (view_id, doc_id, user_id, collection_id, tenant_id),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("dic.analytics: log_doc_view failed: %s", exc)
    finally:
        if conn is not None:
            conn.close()


def detect_view_anomalies(collection_id: str | None = None,
                          window_days: int = 30) -> dict:
    """Detect anomalous document view patterns using IQR-based outlier detection.

    No hardcoded thresholds — view-count boundaries adapt to the actual corpus
    distribution (aiify-opp-105: hardcoded_threshold → anomaly_detection).

    Args:
        collection_id: scope to one collection; ``None`` checks all.
        window_days: look-back window for counting views (default 30 days).

    Returns::

        {
          "hot_docs": [{doc_id, view_count, collection_id}],   # view-count outliers high
          "cold_docs": [{doc_id, view_count, collection_id}],  # view-count outliers low (> 0)
          "unviewed_docs": [{doc_id, collection_id}],          # zero views in window
          "summary": {hot_count, cold_count, unviewed_count, total_docs, window_days},
          "thresholds": {view_low, view_high},
          "empty": bool,
          "message": str | None,
        }
    """
    conn = _conn()
    try:
        # All docs in scope
        cid_filter = "WHERE collection_id = %s" if collection_id else ""
        params: tuple = (collection_id,) if collection_id else ()
        doc_rows = _safe(
            conn,
            f"SELECT doc_id, collection_id FROM dic_documents {cid_filter}",
            params,
        )
        if not doc_rows:
            return {
                "hot_docs": [], "cold_docs": [], "unviewed_docs": [],
                "summary": {"hot_count": 0, "cold_count": 0, "unviewed_count": 0,
                            "total_docs": 0, "window_days": window_days},
                "thresholds": {},
                "empty": True,
                "message": "No documents ingested yet.",
            }

        doc_map = {r["doc_id"]: r.get("collection_id", "default") for r in doc_rows}

        # View counts per doc within the window — use Python-computed cutoff so
        # the ISO string comparison works identically on SQLite and PostgreSQL.
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        cid_view_filter = "AND collection_id = %s" if collection_id else ""
        view_params: tuple = (cutoff, collection_id) if collection_id else (cutoff,)
        view_rows = _safe(
            conn,
            f"SELECT doc_id, COUNT(*) AS view_count FROM dic_doc_views "
            f"WHERE viewed_at >= %s {cid_view_filter} GROUP BY doc_id",
            view_params,
        )
        view_counts = {r["doc_id"]: r["view_count"] for r in view_rows}

        # Separate docs with views from those with none
        viewed = {did: cnt for did, cnt in view_counts.items() if cnt > 0}
        unviewed = [
            {"doc_id": did, "collection_id": doc_map[did]}
            for did in doc_map
            if did not in view_counts
        ]

        counts = list(float(v) for v in viewed.values())
        lo, hi = _iqr_outliers(counts)

        hot_docs, cold_docs = [], []
        for did, cnt in viewed.items():
            entry = {"doc_id": did, "view_count": cnt,
                     "collection_id": doc_map.get(did, "default")}
            if cnt > hi:
                hot_docs.append(entry)
            elif cnt < lo and lo > 0:
                cold_docs.append(entry)

        def _fmt(v: float) -> float | str:
            return round(v, 1) if abs(v) < 1e15 else ("−∞" if v < 0 else "+∞")

        return {
            "hot_docs": sorted(hot_docs, key=lambda x: x["view_count"], reverse=True)[:50],
            "cold_docs": sorted(cold_docs, key=lambda x: x["view_count"])[:50],
            "unviewed_docs": unviewed[:50],
            "summary": {
                "hot_count": len(hot_docs),
                "cold_count": len(cold_docs),
                "unviewed_count": len(unviewed),
                "total_docs": len(doc_map),
                "window_days": window_days,
            },
            "thresholds": {
                "view_low": _fmt(lo),
                "view_high": _fmt(hi),
            },
            "empty": False,
            "message": None,
        }
    finally:
        conn.close()


# ── Ingest Job Task Anomaly Detection (aiify-opp-91, aiify-opp-92) ──────────
# Analogous to paperless-ngx tasks.py hardcoded_threshold → anomaly_detection.
# opp-91 and opp-92 both flag separate hardcoded values in the same source file;
# both are resolved here: IQR-based detection adapts to actual job distributions
# with no hardcoded pass/fail thresholds.

_JOB_TASK_SYSTEM_PROMPT = (
    "You are a document ingestion pipeline quality analyst. You are given metrics "
    "about ingestion job processing: failure rates per collection, stale job counts, "
    "and processing latency outliers. Assess the overall health of the pipeline. "
    "Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning anomaly category>"}. '
    "Never invent anomalies beyond those provided."
)


def _ai_job_task_severity(summary: dict, samples: dict) -> dict | None:
    """Grade ingest job task anomaly severity with the LLM.

    Returns ``{"severity": ..., "rationale": ..., "top_concern": ...}`` or None
    when there are no anomalies, the model is unavailable, or output is malformed.
    """
    if not any(summary.get(k, 0) for k in ("failed_count", "stale_queued_count",
                                             "stale_processing_count", "latency_outlier_count")):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        heuristic = _job_task_heuristic_severity(summary)
        lines = [
            f"Ingest job task anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {heuristic}",
            "Sample failed jobs: " + _json.dumps(samples.get("failed", [])[:5]),
            "Sample stale queued: " + _json.dumps(samples.get("stale_queued", [])[:5]),
            "Sample latency outliers: " + _json.dumps(samples.get("latency_outliers", [])[:5]),
        ]
        prompt = "\n".join(lines)
        router = LLMRouter()
        req = LLMRequest(
            function_name="anomaly_detection",
            prompt=prompt,
            system=_JOB_TASK_SYSTEM_PROMPT,
            max_tokens=256,
        )
        result = router.invoke("anomaly_detection", req)
        raw = (result.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = _json.loads(raw[start:end + 1])
        if parsed.get("severity") not in ("low", "medium", "high"):
            return None
        return parsed
    except Exception as exc:
        logger.debug("dic.analytics: job task LLM grade failed: %s", exc)
        return None


def _job_task_heuristic_severity(summary: dict) -> str:
    """Deterministic severity for ingest job task anomalies — always-available baseline.

    Treats failed jobs and stuck processing jobs as high-severity;
    stale queued and latency outliers as medium.
    """
    if summary.get("failed_count", 0) > 0 or summary.get("stale_processing_count", 0) > 0:
        return "high"
    if summary.get("stale_queued_count", 0) > 0 or summary.get("latency_outlier_count", 0) > 0:
        return "medium"
    return "low"


def detect_ingest_job_anomalies(collection_id: str | None = None,
                                stale_minutes: int = 60) -> dict:
    """Detect anomalous patterns in document ingestion task processing.

    No hardcoded pass/fail thresholds — IQR-based latency outlier detection
    adapts to the actual job duration distribution (aiify-opp-91, aiify-opp-92:
    hardcoded_threshold → anomaly_detection).

    Args:
        collection_id: Scope to one collection; ``None`` checks all.
        stale_minutes: Jobs unchanged for longer than this are flagged stale.
            Defaults to 60 — callers can tune without touching source code.

    Returns::

        {
          "failed": [{job_id, filename, collection_id, stage_detail, age_minutes}],
          "stale_queued": [{job_id, filename, collection_id, age_minutes}],
          "stale_processing": [{job_id, filename, collection_id, age_minutes}],
          "latency_outliers": [{job_id, filename, collection_id, duration_seconds}],
          "summary": {failed_count, stale_queued_count, stale_processing_count,
                      latency_outlier_count, total_jobs, thresholds},
          "severity": "low|medium|high",
          "severity_source": "llm|heuristic",
          "heuristic_severity": "low|medium|high",
          "empty": bool,
          "message": str | None,
        }
    """
    from datetime import timedelta

    conn = _conn()
    try:
        cid_filter = "WHERE collection_id = %s" if collection_id else ""
        params: tuple = (collection_id,) if collection_id else ()
        rows = _safe(
            conn,
            f"SELECT job_id, filename, collection_id, status, stage_detail, "
            f"created_at, updated_at FROM dic_ingest_jobs {cid_filter} ORDER BY created_at DESC",
            params,
        )
    finally:
        conn.close()

    if not rows:
        return {
            "failed": [], "stale_queued": [], "stale_processing": [],
            "latency_outliers": [],
            "summary": {
                "failed_count": 0, "stale_queued_count": 0,
                "stale_processing_count": 0, "latency_outlier_count": 0,
                "total_jobs": 0,
                "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No ingestion jobs found.",
        }

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=stale_minutes)

    failed, stale_queued, stale_processing = [], [], []
    completed_durations: list[float] = []
    completed_jobs: list[dict] = []

    def _parse_ts(v) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            dt = v
        else:
            try:
                dt = datetime.fromisoformat(str(v))
            except (ValueError, TypeError):
                return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    for r in rows:
        status = (r.get("status") or "").lower()
        cid = r.get("collection_id", "default")
        fname = r.get("filename", "")
        jid = r.get("job_id", "")
        stage = r.get("stage_detail", "")

        created = _parse_ts(r.get("created_at"))
        updated = _parse_ts(r.get("updated_at"))

        age_minutes: float = 0.0
        if updated:
            age_minutes = (now - updated).total_seconds() / 60.0

        if status in ("error", "failed"):
            failed.append({
                "job_id": jid, "filename": fname,
                "collection_id": cid, "stage_detail": stage,
                "age_minutes": round(age_minutes, 1),
            })
        elif status == "queued" and updated and updated < stale_cutoff:
            stale_queued.append({
                "job_id": jid, "filename": fname,
                "collection_id": cid,
                "age_minutes": round(age_minutes, 1),
            })
        elif status == "processing" and updated and updated < stale_cutoff:
            stale_processing.append({
                "job_id": jid, "filename": fname,
                "collection_id": cid, "stage_detail": stage,
                "age_minutes": round(age_minutes, 1),
            })
        elif status == "done" and created and updated:
            duration_secs = (updated - created).total_seconds()
            if duration_secs >= 0:
                completed_durations.append(duration_secs)
                completed_jobs.append({
                    "job_id": jid, "filename": fname,
                    "collection_id": cid,
                    "duration_seconds": round(duration_secs, 1),
                })

    # IQR-based latency outlier detection — no hardcoded duration threshold.
    latency_outliers: list[dict] = []
    dur_lo, dur_hi = _iqr_outliers(completed_durations)
    if dur_hi < float("inf"):
        for job in completed_jobs:
            if job["duration_seconds"] > dur_hi:
                latency_outliers.append(job)

    summary = {
        "failed_count": len(failed),
        "stale_queued_count": len(stale_queued),
        "stale_processing_count": len(stale_processing),
        "latency_outlier_count": len(latency_outliers),
        "total_jobs": len(rows),
        "thresholds": {
            "stale_minutes": stale_minutes,
            "latency_high_seconds": round(dur_hi, 1) if dur_hi < float("inf") else None,
        },
    }
    heuristic_sev = _job_task_heuristic_severity(summary)
    samples = {
        "failed": failed[:5],
        "stale_queued": stale_queued[:5],
        "stale_processing": stale_processing[:5],
        "latency_outliers": latency_outliers[:5],
    }
    ai_grade = _ai_job_task_severity(summary, samples) if heuristic_sev != "low" else None

    if ai_grade:
        severity = ai_grade["severity"]
        severity_source = "llm"
    else:
        severity = heuristic_sev
        severity_source = "heuristic"

    return {
        "failed": failed[:50],
        "stale_queued": stale_queued[:50],
        "stale_processing": stale_processing[:50],
        "latency_outliers": sorted(
            latency_outliers, key=lambda x: x["duration_seconds"], reverse=True
        )[:50],
        "summary": summary,
        "severity": severity,
        "severity_source": severity_source,
        "heuristic_severity": heuristic_sev,
        "empty": False,
        "message": None,
    }


# ── Document Field Validation Anomaly Detection (aiify-rm-a3344-phase-137) ────
# Analogous to paperless-ngx validators.py hardcoded_threshold → anomaly_detection.
# paperless hardcodes CharField max_length, color hex patterns, and ASN format
# bounds; DIC replaces those with IQR-based corpus-adaptive thresholds and an
# LLM severity grader so limits evolve with the document corpus.
#
# Detects: title length outliers (too long / too short / empty), filename length
# outliers (excessively long), missing/empty filenames, and unsupported
# content-type values — none of these use hardcoded cut-offs.

_FIELD_VALIDATOR_SYSTEM_PROMPT = (
    "You are a document metadata quality analyst. You are given anomaly statistics "
    "about document field values ingested into a DIC corpus: title length outliers, "
    "filename length outliers, empty field counts, and unsupported content types. "
    "Assess the overall metadata quality health. "
    "Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning anomaly category>"}. '
    "Never invent anomalies beyond those provided."
)


def _ai_field_validator_severity(summary: dict, samples: dict) -> dict | None:
    """Grade field validation anomaly severity with the LLM."""
    if not any(summary.get(k, 0) for k in (
        "empty_title_count", "empty_filename_count", "title_too_long_count",
        "title_too_short_count", "filename_too_long_count", "unsupported_type_count",
    )):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        heuristic = _field_validator_heuristic_severity(summary)
        lines = [
            f"Field validation anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {heuristic}",
            "Sample empty-title docs: " + _json.dumps(samples.get("empty_title", [])[:5]),
            "Sample title-too-long docs: " + _json.dumps(samples.get("title_too_long", [])[:5]),
            "Sample filename-too-long docs: " + _json.dumps(samples.get("filename_too_long", [])[:5]),
            "Sample unsupported-type docs: " + _json.dumps(samples.get("unsupported_type", [])[:5]),
        ]
        req = LLMRequest(
            function_name="anomaly_detection",
            prompt="\n".join(lines),
            system=_FIELD_VALIDATOR_SYSTEM_PROMPT,
            max_tokens=256,
        )
        result = LLMRouter().invoke("anomaly_detection", req)
        raw = (result.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = _json.loads(raw[start:end + 1])
        if parsed.get("severity") not in ("low", "medium", "high"):
            return None
        return parsed
    except Exception as exc:
        logger.debug("dic.analytics: field validator LLM grade failed: %s", exc)
        return None


def _field_validator_heuristic_severity(summary: dict) -> str:
    """Deterministic severity for field validation anomalies — always-available baseline."""
    if summary.get("empty_filename_count", 0) > 0 or summary.get("unsupported_type_count", 0) > 0:
        return "high"
    if (
        summary.get("empty_title_count", 0) > 0
        or summary.get("title_too_long_count", 0) > 0
        or summary.get("filename_too_long_count", 0) > 0
    ):
        return "medium"
    if summary.get("title_too_short_count", 0) > 0:
        return "low"
    return "low"


# Content types supported by the DIC ingest pipeline (from constants.SUPPORTED_EXTENSIONS
# translated to MIME equivalents). Values outside this set are flagged as anomalous.
_SUPPORTED_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/markdown",
    "text/html",
    "image/png",
    "image/jpeg",
    "image/tiff",
})


def detect_field_validation_anomalies(collection_id: str | None = None) -> dict:
    """Detect anomalous document field values in the DIC corpus.

    Analogous to paperless-ngx validators.py hardcoded_threshold →
    anomaly_detection (aiify-rm-a3344-phase-137). paperless validators.py
    hard-codes CharField max_length, color pattern lengths, and ASN format
    bounds. This function replaces those fixed limits with IQR-based outlier
    detection that adapts to the actual corpus distribution, plus an LLM
    severity grader.

    Detects:
      - Empty/missing titles (always anomalous — no threshold needed)
      - Title length outliers: IQR upper fence (too long) and lower fence (too
        short, excluding empty docs already caught above)
      - Empty/missing filenames (always anomalous)
      - Filename length outliers: IQR upper fence (excessively long paths)
      - Unsupported content_type values (outside the DIC ingest MIME set)

    Args:
        collection_id: Scope to one collection; ``None`` checks all.

    Returns::

        {
          "empty_title": [{doc_id, filename, collection_id}],
          "title_too_long": [{doc_id, title, title_len, collection_id}],
          "title_too_short": [{doc_id, title, title_len, collection_id}],
          "empty_filename": [{doc_id, title, collection_id}],
          "filename_too_long": [{doc_id, filename, filename_len, collection_id}],
          "unsupported_type": [{doc_id, title, content_type, collection_id}],
          "summary": {empty_title_count, title_too_long_count, title_too_short_count,
                      empty_filename_count, filename_too_long_count,
                      unsupported_type_count, total_docs, thresholds},
          "severity": "low|medium|high",
          "severity_source": "llm|heuristic",
          "heuristic_severity": "low|medium|high",
          "empty": bool,
          "message": str | None,
        }
    """
    conn = _conn()
    try:
        params: tuple = (collection_id,) if collection_id else ()
        cid_filter = "WHERE collection_id = %s" if collection_id else ""
        rows = _safe(
            conn,
            f"SELECT doc_id, title, filename, content_type, collection_id "
            f"FROM dic_documents {cid_filter} ORDER BY doc_id",
            params,
        )
    finally:
        conn.close()

    if not rows:
        return {
            "empty_title": [], "title_too_long": [], "title_too_short": [],
            "empty_filename": [], "filename_too_long": [], "unsupported_type": [],
            "summary": {
                "empty_title_count": 0, "title_too_long_count": 0,
                "title_too_short_count": 0, "empty_filename_count": 0,
                "filename_too_long_count": 0, "unsupported_type_count": 0,
                "total_docs": 0,
                "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No documents ingested yet.",
        }

    # Split into docs with and without populated titles/filenames for IQR.
    titled = [r for r in rows if (r.get("title") or "").strip()]
    filed = [r for r in rows if (r.get("filename") or "").strip()]

    title_lens = [float(len((r.get("title") or "").strip())) for r in titled]
    fname_lens = [float(len((r.get("filename") or "").strip())) for r in filed]

    title_lo, title_hi = _iqr_outliers(title_lens) if len(title_lens) >= 4 else (0.0, float("inf"))
    fname_lo, fname_hi = _iqr_outliers(fname_lens) if len(fname_lens) >= 4 else (0.0, float("inf"))

    empty_title: list[dict] = []
    title_too_long: list[dict] = []
    title_too_short: list[dict] = []
    empty_filename: list[dict] = []
    filename_too_long: list[dict] = []
    unsupported_type: list[dict] = []

    for r in rows:
        doc_id = r["doc_id"]
        title = (r.get("title") or "").strip()
        filename = (r.get("filename") or "").strip()
        ctype = (r.get("content_type") or "").strip().lower()
        cid = r.get("collection_id") or ""

        if not title:
            empty_title.append({"doc_id": doc_id, "filename": filename, "collection_id": cid})
        else:
            tlen = len(title)
            if title_hi < float("inf") and tlen > title_hi:
                title_too_long.append({
                    "doc_id": doc_id, "title": title[:80], "title_len": tlen,
                    "collection_id": cid,
                })
            elif title_lo > 0 and tlen < title_lo:
                title_too_short.append({
                    "doc_id": doc_id, "title": title, "title_len": tlen,
                    "collection_id": cid,
                })

        if not filename:
            empty_filename.append({"doc_id": doc_id, "title": title[:80], "collection_id": cid})
        else:
            flen = len(filename)
            if fname_hi < float("inf") and flen > fname_hi:
                filename_too_long.append({
                    "doc_id": doc_id, "filename": filename[:120], "filename_len": flen,
                    "collection_id": cid,
                })

        if ctype and ctype not in _SUPPORTED_CONTENT_TYPES:
            unsupported_type.append({
                "doc_id": doc_id, "title": title[:80], "content_type": ctype,
                "collection_id": cid,
            })

    summary = {
        "empty_title_count": len(empty_title),
        "title_too_long_count": len(title_too_long),
        "title_too_short_count": len(title_too_short),
        "empty_filename_count": len(empty_filename),
        "filename_too_long_count": len(filename_too_long),
        "unsupported_type_count": len(unsupported_type),
        "total_docs": len(rows),
        "thresholds": {
            "title_len_low": round(title_lo, 1) if title_lo > 0 else None,
            "title_len_high": round(title_hi, 1) if title_hi < float("inf") else None,
            "filename_len_high": round(fname_hi, 1) if fname_hi < float("inf") else None,
        },
    }

    heuristic_sev = _field_validator_heuristic_severity(summary)
    samples = {
        "empty_title": empty_title[:5],
        "title_too_long": title_too_long[:5],
        "filename_too_long": filename_too_long[:5],
        "unsupported_type": unsupported_type[:5],
    }
    ai_grade = _ai_field_validator_severity(summary, samples) if heuristic_sev != "low" else None

    if ai_grade:
        severity = ai_grade["severity"]
        severity_source = "llm"
    else:
        severity = heuristic_sev
        severity_source = "heuristic"

    return {
        "empty_title": empty_title[:50],
        "title_too_long": title_too_long[:50],
        "title_too_short": title_too_short[:50],
        "empty_filename": empty_filename[:50],
        "filename_too_long": filename_too_long[:50],
        "unsupported_type": unsupported_type[:50],
        "summary": summary,
        "severity": severity,
        "severity_source": severity_source,
        "heuristic_severity": heuristic_sev,
        "empty": False,
        "message": None,
    }


# Analogous to paperless-ngx document_exporter.py hardcoded_threshold →
# anomaly_detection (aiify-rm-a3344-phase-41).  Detects oversized outputs,
# error-status records, stale in-progress exports, and provider overconcentration
# across dic_generated_outputs — no hardcoded thresholds (IQR-based sizing).

_OUTPUT_EXPORT_SYSTEM_PROMPT = (
    "You are a document output pipeline quality analyst. You are given metrics "
    "about generated outputs: oversized content counts, failed export counts, "
    "stale in-progress counts, and provider overconcentration. "
    "Assess the overall health of the export pipeline. "
    "Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning anomaly category>"}. '
    "Never invent anomalies beyond those provided."
)


def _ai_output_export_severity(summary: dict, samples: dict) -> dict | None:
    """Grade output export anomaly severity with the LLM."""
    if not any(summary.get(k, 0) for k in (
        "oversized_count", "error_count", "stale_count", "overconcentrated_providers"
    )):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        heuristic = _output_export_heuristic_severity(summary)
        lines = [
            f"Output export anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {heuristic}",
            "Sample oversized outputs: " + _json.dumps(samples.get("oversized", [])[:5]),
            "Sample error outputs: " + _json.dumps(samples.get("errors", [])[:5]),
            "Sample stale outputs: " + _json.dumps(samples.get("stale", [])[:5]),
        ]
        req = LLMRequest(
            function_name="anomaly_detection",
            prompt="\n".join(lines),
            system=_OUTPUT_EXPORT_SYSTEM_PROMPT,
            max_tokens=256,
        )
        result = LLMRouter().invoke("anomaly_detection", req)
        raw = (result.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = _json.loads(raw[start:end + 1])
        if parsed.get("severity") not in ("low", "medium", "high"):
            return None
        return parsed
    except Exception as exc:
        logger.debug("dic.analytics: output export LLM grade failed: %s", exc)
        return None


def _output_export_heuristic_severity(summary: dict) -> str:
    """Deterministic severity for output export anomalies — always-available baseline."""
    if summary.get("error_count", 0) > 0:
        return "high"
    if summary.get("stale_count", 0) > 0 or summary.get("oversized_count", 0) > 0:
        return "medium"
    if summary.get("overconcentrated_providers", 0) > 0:
        return "medium"
    return "low"


def detect_output_export_anomalies(
    collection_id: str | None = None,
    stale_minutes: int = 30,
) -> dict:
    """Detect anomalous patterns in document output/export generation.

    Analogous to paperless-ngx document_exporter.py hardcoded_threshold →
    anomaly_detection (aiify-rm-a3344-phase-41).  No hardcoded size thresholds —
    IQR-based detection adapts to the actual corpus distribution.

    Detects:
      - Oversized content_json payloads (IQR upper fence on byte length)
      - Error-status output records
      - Stale in-progress outputs (stuck longer than stale_minutes)
      - Provider overconcentration (single provider > 80% of outputs)

    Args:
        collection_id: Scope to one collection; ``None`` checks all.
        stale_minutes: Outputs with non-done status unchanged this long are flagged.

    Returns::

        {
          "oversized": [{id, output_type, collection_id, size_bytes}],
          "errors": [{id, output_type, collection_id, status, age_minutes}],
          "stale": [{id, output_type, collection_id, status, age_minutes}],
          "provider_concentration": {provider: count},
          "summary": {oversized_count, error_count, stale_count,
                      overconcentrated_providers, total_outputs, thresholds},
          "severity": "low|medium|high",
          "severity_source": "llm|heuristic",
          "heuristic_severity": "low|medium|high",
          "empty": bool,
          "message": str | None,
        }
    """
    from datetime import timedelta

    conn = _conn()
    try:
        cid_filter = "WHERE collection_id = %s" if collection_id else ""
        params: tuple = (collection_id,) if collection_id else ()
        rows = _safe(
            conn,
            f"SELECT id, output_type, collection_id, provider, status, "
            f"content_json, created_at, updated_at "
            f"FROM dic_generated_outputs {cid_filter} ORDER BY created_at DESC",
            params,
        )
    finally:
        conn.close()

    if not rows:
        return {
            "oversized": [], "errors": [], "stale": [],
            "provider_concentration": {},
            "summary": {
                "oversized_count": 0, "error_count": 0, "stale_count": 0,
                "overconcentrated_providers": 0, "total_outputs": 0,
                "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No generated outputs found.",
        }

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=stale_minutes)

    errors: list[dict] = []
    stale: list[dict] = []
    content_sizes: list[float] = []
    size_entries: list[dict] = []
    provider_counts: dict[str, int] = {}

    def _parse_ts(v) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            dt = v
        else:
            try:
                dt = datetime.fromisoformat(str(v))
            except (ValueError, TypeError):
                return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    for r in rows:
        oid = r.get("id", "")
        otype = r.get("output_type", "")
        cid = r.get("collection_id", "default")
        provider = r.get("provider") or "unknown"
        status = (r.get("status") or "done").lower()
        raw_content = r.get("content_json") or "{}"
        updated = _parse_ts(r.get("updated_at") or r.get("created_at"))

        provider_counts[provider] = provider_counts.get(provider, 0) + 1

        age_minutes: float = 0.0
        if updated:
            age_minutes = (now - updated).total_seconds() / 60.0

        if status in ("error", "failed"):
            errors.append({
                "id": oid, "output_type": otype,
                "collection_id": cid, "status": status,
                "age_minutes": round(age_minutes, 1),
            })
        elif status not in ("done",) and updated and updated < stale_cutoff:
            stale.append({
                "id": oid, "output_type": otype,
                "collection_id": cid, "status": status,
                "age_minutes": round(age_minutes, 1),
            })

        size_bytes = len(
            raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
        )
        content_sizes.append(float(size_bytes))
        size_entries.append({
            "id": oid, "output_type": otype,
            "collection_id": cid, "size_bytes": size_bytes,
        })

    # IQR-based size outlier detection — no hardcoded byte threshold.
    oversized: list[dict] = []
    _, size_hi = _iqr_outliers(content_sizes)
    if size_hi < float("inf"):
        for entry in size_entries:
            if entry["size_bytes"] > size_hi:
                oversized.append(entry)

    # Provider overconcentration: single provider > 80% of all outputs (min 5 samples).
    total = len(rows)
    overconcentrated = sum(
        1 for cnt in provider_counts.values()
        if total >= 5 and cnt / total > 0.80
    )

    summary = {
        "oversized_count": len(oversized),
        "error_count": len(errors),
        "stale_count": len(stale),
        "overconcentrated_providers": overconcentrated,
        "total_outputs": total,
        "thresholds": {
            "stale_minutes": stale_minutes,
            "size_high_bytes": round(size_hi) if size_hi < float("inf") else None,
            "provider_concentration_pct": 80,
        },
    }
    heuristic_sev = _output_export_heuristic_severity(summary)
    samples = {
        "oversized": oversized[:5],
        "errors": errors[:5],
        "stale": stale[:5],
    }
    ai_grade = _ai_output_export_severity(summary, samples) if heuristic_sev != "low" else None

    if ai_grade:
        severity = ai_grade["severity"]
        severity_source = "llm"
    else:
        severity = heuristic_sev
        severity_source = "heuristic"

    return {
        "oversized": oversized[:50],
        "errors": errors[:50],
        "stale": stale[:50],
        "provider_concentration": provider_counts,
        "summary": summary,
        "severity": severity,
        "severity_source": severity_source,
        "heuristic_severity": heuristic_sev,
        "empty": False,
        "message": None,
    }


# ── Document Model Attribute Anomaly Detection (aiify-rm-a3344-phase-58) ──────
# Analogous to paperless-ngx models.py hardcoded_threshold → anomaly_detection.
# paperless models.py hard-codes Django CharField max_length constraints, ASN
# sequence bounds, and Correspondent/Tag/DocumentType match-score field choices.
# DIC replaces those fixed model-level constraints with IQR-based corpus-adaptive
# detection and an LLM severity grader.
#
# Detects: NULL provider (model attribute absent at DB level), NULL content_type
# at DB level (distinct from format validation in phase-137 which checks
# non-null values), incomplete chunk-embedding ratios (IQR on
# chunks_done/chunks_total for completed jobs), and orphaned ingest jobs (status
# "done" but doc_id NULL — model record never created).

_MODEL_ATTR_SYSTEM_PROMPT = (
    "You are a document intelligence model quality analyst. You are given statistics "
    "about model-level attribute anomalies in a DIC corpus: null provider counts, "
    "null content_type counts at the DB level, incomplete chunk-embedding job counts, "
    "and orphaned ingest jobs (done but no document record created). "
    "Assess the overall model data quality health. "
    "Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning anomaly category>"}. '
    "Never invent anomalies beyond those provided."
)


def _ai_model_attr_severity(summary: dict, samples: dict) -> dict | None:
    """Grade model attribute anomaly severity with the LLM."""
    if not any(summary.get(k, 0) for k in (
        "null_provider_count", "null_content_type_count",
        "incomplete_embedding_count", "orphaned_job_count",
    )):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        heuristic = _model_attr_heuristic_severity(summary)
        lines = [
            f"Model attribute anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {heuristic}",
            "Sample null-provider docs: " + _json.dumps(samples.get("null_provider", [])[:5]),
            "Sample incomplete-embedding jobs: " + _json.dumps(
                samples.get("incomplete_embedding", [])[:5]
            ),
            "Sample orphaned jobs: " + _json.dumps(samples.get("orphaned_jobs", [])[:5]),
        ]
        req = LLMRequest(
            function_name="anomaly_detection",
            prompt="\n".join(lines),
            system=_MODEL_ATTR_SYSTEM_PROMPT,
            max_tokens=256,
        )
        result = LLMRouter().invoke("anomaly_detection", req)
        raw = (result.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = _json.loads(raw[start:end + 1])
        if parsed.get("severity") not in ("low", "medium", "high"):
            return None
        return parsed
    except Exception as exc:
        logger.debug("dic.analytics: model attr LLM grade failed: %s", exc)
        return None


def _model_attr_heuristic_severity(summary: dict) -> str:
    """Deterministic severity for model attribute anomalies — always-available baseline."""
    if summary.get("orphaned_job_count", 0) > 0:
        return "high"
    if summary.get("null_provider_count", 0) > 0 or summary.get("null_content_type_count", 0) > 0:
        return "medium"
    if summary.get("incomplete_embedding_count", 0) > 0:
        return "medium"
    return "low"


def detect_document_model_anomalies(collection_id: str | None = None) -> dict:
    """Detect anomalous model-level attribute gaps in the DIC corpus.

    Analogous to paperless-ngx models.py hardcoded_threshold →
    anomaly_detection (aiify-rm-a3344-phase-58). paperless models.py defines
    Django model fields with hardcoded constraints (CharField max_length, ASN
    bounds, match-score field choices). This function replaces those fixed
    model-level constraints with IQR-based corpus-adaptive detection.

    Detects:
      - NULL provider at DB level (model attribute not populated at ingest time)
      - NULL content_type at DB level (distinct from empty-string format checks
        in phase-137 which validate the format of non-null values)
      - Incomplete chunk-embedding ratio: completed ingest jobs where
        chunks_done/chunks_total is below the IQR lower fence (indicating a
        partial embedding failure, not captured by job status alone)
      - Orphaned ingest jobs: status="done" but doc_id IS NULL (job marked
        complete but the dic_documents model record was never created)

    Args:
        collection_id: Scope to one collection; ``None`` checks all.

    Returns::

        {
          "null_provider": [{doc_id, filename, collection_id}],
          "null_content_type": [{doc_id, title, collection_id}],
          "incomplete_embedding": [{job_id, filename, chunks_done, chunks_total,
                                    ratio, collection_id}],
          "orphaned_jobs": [{job_id, filename, collection_id}],
          "summary": {null_provider_count, null_content_type_count,
                      incomplete_embedding_count, orphaned_job_count,
                      total_docs, total_jobs, thresholds},
          "severity": "low|medium|high",
          "severity_source": "llm|heuristic",
          "heuristic_severity": "low|medium|high",
          "empty": bool,
          "message": str | None,
        }
    """
    conn = _conn()
    try:
        doc_cid_filter = "WHERE collection_id = %s" if collection_id else ""
        job_cid_filter = "WHERE collection_id = %s" if collection_id else ""
        cid_params: tuple = (collection_id,) if collection_id else ()

        doc_rows = _safe(
            conn,
            f"SELECT doc_id, title, filename, content_type, provider, collection_id "
            f"FROM dic_documents {doc_cid_filter} ORDER BY doc_id",
            cid_params,
        )
        job_rows = _safe(
            conn,
            f"SELECT job_id, filename, collection_id, status, doc_id, "
            f"chunks_done, chunks_total "
            f"FROM dic_ingest_jobs {job_cid_filter} ORDER BY job_id",
            cid_params,
        )
    finally:
        conn.close()

    if not doc_rows and not job_rows:
        return {
            "null_provider": [],
            "null_content_type": [],
            "incomplete_embedding": [],
            "orphaned_jobs": [],
            "summary": {
                "null_provider_count": 0, "null_content_type_count": 0,
                "incomplete_embedding_count": 0, "orphaned_job_count": 0,
                "total_docs": 0, "total_jobs": 0, "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No documents or ingest jobs found.",
        }

    # ── NULL model attributes ─────────────────────────────────────────────────
    null_provider: list[dict] = []
    null_content_type: list[dict] = []

    for row in doc_rows:
        prov = row.get("provider")
        ctype = row.get("content_type")
        if prov is None:
            null_provider.append({
                "doc_id": row["doc_id"],
                "filename": row.get("filename", ""),
                "collection_id": row.get("collection_id", ""),
            })
        if ctype is None:
            null_content_type.append({
                "doc_id": row["doc_id"],
                "title": row.get("title", ""),
                "collection_id": row.get("collection_id", ""),
            })

    # ── Chunk embedding completion ratio (IQR-based) ──────────────────────────
    # Only consider completed (done) jobs with chunks_total > 0.
    done_jobs = [
        r for r in job_rows
        if r.get("status") == "done" and (r.get("chunks_total") or 0) > 0
    ]
    ratios = [
        row["chunks_done"] / row["chunks_total"]
        for row in done_jobs
        if row.get("chunks_done") is not None and row.get("chunks_total")
    ]
    ratio_lo, _ = _iqr_outliers(ratios)

    incomplete_embedding: list[dict] = []
    for row, ratio in zip(done_jobs, ratios):
        if ratio < ratio_lo:
            incomplete_embedding.append({
                "job_id": row["job_id"],
                "filename": row.get("filename", ""),
                "chunks_done": row.get("chunks_done", 0),
                "chunks_total": row.get("chunks_total", 0),
                "ratio": round(ratio, 4),
                "collection_id": row.get("collection_id", ""),
            })

    # ── Orphaned jobs: done but no doc_id ─────────────────────────────────────
    orphaned_jobs: list[dict] = [
        {
            "job_id": r["job_id"],
            "filename": r.get("filename", ""),
            "collection_id": r.get("collection_id", ""),
        }
        for r in job_rows
        if r.get("status") == "done" and not r.get("doc_id")
    ]

    # ── Thresholds ────────────────────────────────────────────────────────────
    thresholds: dict = {}
    if ratio_lo < float("inf") and ratios:
        thresholds["embedding_ratio_low"] = round(ratio_lo, 4)

    summary = {
        "null_provider_count": len(null_provider),
        "null_content_type_count": len(null_content_type),
        "incomplete_embedding_count": len(incomplete_embedding),
        "orphaned_job_count": len(orphaned_jobs),
        "total_docs": len(doc_rows),
        "total_jobs": len(job_rows),
        "thresholds": thresholds,
    }

    heuristic_sev = _model_attr_heuristic_severity(summary)
    samples = {
        "null_provider": null_provider[:5],
        "null_content_type": null_content_type[:5],
        "incomplete_embedding": incomplete_embedding[:5],
        "orphaned_jobs": orphaned_jobs[:5],
    }
    ai_grade = _ai_model_attr_severity(summary, samples) if heuristic_sev != "low" else None

    if ai_grade:
        severity = ai_grade["severity"]
        severity_source = "llm"
    else:
        severity = heuristic_sev
        severity_source = "heuristic"

    return {
        "null_provider": null_provider[:50],
        "null_content_type": null_content_type[:50],
        "incomplete_embedding": incomplete_embedding[:50],
        "orphaned_jobs": orphaned_jobs[:50],
        "summary": summary,
        "severity": severity,
        "severity_source": severity_source,
        "heuristic_severity": heuristic_sev,
        "empty": False,
        "message": None,
    }


# ── Document Routing Anomaly Detection (aiify-rm-a3344-phase-53) ──────────────
# Analogous to paperless-ngx matching.py hardcoded_threshold → anomaly_detection.
# paperless matching.py hard-codes fuzzy-match score thresholds (e.g. >= 86 for
# fuzz.partial_ratio), algorithm-specific cutoffs for MATCH_LITERAL/MATCH_FUZZY/
# MATCH_AUTO, and per-algorithm routing constants.  DIC replaces those fixed
# routing thresholds with IQR-based corpus-adaptive anomaly detection over three
# signals:
#
#   1. Collection concentration — documents over-concentrated in a single
#      collection (analogous to all docs matching the same correspondent/tag).
#   2. Default-collection backlog — documents that completed ingest but were
#      never routed out of the 'default' collection (analogous to unmatched docs).
#   3. Content-type imbalance — IQR on per-type document counts to flag when one
#      content_type dominates the corpus (analogous to over-permissive match rules).

_ROUTING_SYSTEM_PROMPT = (
    "You are a document routing quality analyst. You are given statistics about "
    "collection-assignment and content-type distribution anomalies in a DIC corpus: "
    "collection concentration ratios, unrouted document counts, content-type "
    "imbalance data, and a deterministic baseline severity. "
    "Assess whether the routing health poses a real risk to retrieval quality. "
    "Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning routing anomaly>"}. '
    "Never invent anomalies beyond those provided."
)


def _ai_routing_severity(summary: dict, samples: dict) -> dict | None:
    """Grade routing anomaly severity with the LLM."""
    if not any(summary.get(k, 0) for k in (
        "unrouted_count", "dominant_collection_docs",
        "dominant_type_docs", "over_concentrated_collection",
    )):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        heuristic = _routing_heuristic_severity(summary)
        lines = [
            f"Routing anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {heuristic}",
            "Sample unrouted docs: " + _json.dumps(samples.get("unrouted", [])[:5]),
            "Sample dominant-collection docs: " + _json.dumps(
                samples.get("dominant_collection", [])[:5]
            ),
        ]
        req = LLMRequest(
            function_name="anomaly_detection",
            prompt="\n".join(lines),
            system=_ROUTING_SYSTEM_PROMPT,
            max_tokens=256,
        )
        result = LLMRouter().invoke("anomaly_detection", req)
        raw = (result.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = _json.loads(raw[start:end + 1])
        if parsed.get("severity") not in ("low", "medium", "high"):
            return None
        return parsed
    except Exception as exc:
        logger.debug("dic.analytics: routing LLM grade failed: %s", exc)
        return None


def _routing_heuristic_severity(summary: dict) -> str:
    """Deterministic severity for routing anomalies — always-available baseline."""
    if summary.get("over_concentrated_collection"):
        return "high"
    if summary.get("unrouted_count", 0) > 0:
        return "medium"
    if summary.get("dominant_type_docs", 0) > 0:
        return "medium"
    return "low"


def detect_document_routing_anomalies(collection_id: str | None = None) -> dict:
    """Detect anomalous routing patterns in the DIC corpus.

    Analogous to paperless-ngx matching.py hardcoded_threshold →
    anomaly_detection (aiify-rm-a3344-phase-53).  Instead of fixed
    algorithm-specific match-score cutoffs (e.g. fuzzy >= 86), this function
    characterises the actual document distribution across collections and
    content types using IQR-based statistics and flags statistical outliers.

    Detects:
      - Unrouted documents: completed-ingest docs still in the 'default'
        collection (analogous to docs that matched no routing rule).
      - Collection concentration: a single collection holds more than
        Q3+1.5×IQR of the per-collection document count (over-permissive
        routing — one rule is matching everything).
      - Content-type imbalance: one content_type dominates beyond the IQR
        upper fence (may indicate a misconfigured classifier or missing
        type-specific routing rules).

    Args:
        collection_id: When provided, scope the analysis to that collection
            only; ``None`` analyses the full corpus.

    Returns::

        {
          "unrouted": [{doc_id, filename, collection_id, content_type}],
          "dominant_collection": [{collection_id, doc_count}],
          "dominant_type": [{content_type, doc_count}],
          "collection_distribution": {collection_id: count},
          "type_distribution": {content_type: count},
          "summary": {unrouted_count, total_docs, collection_count,
                      dominant_collection_docs, dominant_type_docs,
                      over_concentrated_collection, thresholds},
          "severity": "low|medium|high",
          "severity_source": "llm|heuristic",
          "heuristic_severity": "low|medium|high",
          "empty": bool,
          "message": str | None,
        }
    """
    conn = _conn()
    try:
        cid_filter = "WHERE collection_id = %s" if collection_id else ""
        cid_params: tuple = (collection_id,) if collection_id else ()
        doc_rows = _safe(
            conn,
            f"SELECT doc_id, filename, collection_id, content_type "
            f"FROM dic_documents {cid_filter} ORDER BY doc_id",
            cid_params,
        )
    finally:
        conn.close()

    if not doc_rows:
        return {
            "unrouted": [],
            "dominant_collection": [],
            "dominant_type": [],
            "collection_distribution": {},
            "type_distribution": {},
            "summary": {
                "unrouted_count": 0, "total_docs": 0, "collection_count": 0,
                "dominant_collection_docs": 0, "dominant_type_docs": 0,
                "over_concentrated_collection": False, "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No documents found.",
        }

    total = len(doc_rows)

    # ── Unrouted documents ────────────────────────────────────────────────────
    unrouted: list[dict] = [
        {
            "doc_id": r["doc_id"],
            "filename": r.get("filename", ""),
            "collection_id": r.get("collection_id", "default"),
            "content_type": r.get("content_type", ""),
        }
        for r in doc_rows
        if (r.get("collection_id") or "default") == "default"
    ]

    # ── Per-collection document counts ────────────────────────────────────────
    col_counts: dict[str, int] = defaultdict(int)
    for r in doc_rows:
        cid = r.get("collection_id") or "default"
        col_counts[cid] += 1

    col_values = sorted(col_counts.values())
    n_cols = len(col_values)

    dominant_collection: list[dict] = []
    over_concentrated = False
    col_q1 = col_q3 = col_iqr = 0.0
    col_upper_fence = float("inf")

    if n_cols >= 4:
        col_q1 = col_values[n_cols // 4]
        col_q3 = col_values[(3 * n_cols) // 4]
        col_iqr = col_q3 - col_q1
        col_upper_fence = col_q3 + 1.5 * col_iqr
        for cid, cnt in col_counts.items():
            if cnt > col_upper_fence:
                dominant_collection.append({"collection_id": cid, "doc_count": cnt})
                over_concentrated = True
    elif n_cols == 1:
        # Single collection — always flag if corpus is non-trivial
        only_cid, only_cnt = next(iter(col_counts.items()))
        if only_cnt >= 5:
            dominant_collection.append({"collection_id": only_cid, "doc_count": only_cnt})
            over_concentrated = True

    # ── Per-content-type document counts ─────────────────────────────────────
    type_counts: dict[str, int] = defaultdict(int)
    for r in doc_rows:
        ctype = r.get("content_type") or "unknown"
        type_counts[ctype] += 1

    type_values = sorted(type_counts.values())
    n_types = len(type_values)

    dominant_type: list[dict] = []
    type_upper_fence = float("inf")

    if n_types >= 4:
        tq3 = type_values[(3 * n_types) // 4]
        tq1 = type_values[n_types // 4]
        tiqr = tq3 - tq1
        type_upper_fence = tq3 + 1.5 * tiqr
        for ctype, cnt in type_counts.items():
            if cnt > type_upper_fence:
                dominant_type.append({"content_type": ctype, "doc_count": cnt})

    dominant_col_docs = max((d["doc_count"] for d in dominant_collection), default=0)
    dominant_type_docs = max((d["doc_count"] for d in dominant_type), default=0)

    thresholds: dict = {}
    if col_upper_fence < float("inf"):
        thresholds["collection_upper_fence"] = round(col_upper_fence, 2)
    if type_upper_fence < float("inf"):
        thresholds["type_upper_fence"] = round(type_upper_fence, 2)

    summary = {
        "unrouted_count": len(unrouted),
        "total_docs": total,
        "collection_count": n_cols,
        "dominant_collection_docs": dominant_col_docs,
        "dominant_type_docs": dominant_type_docs,
        "over_concentrated_collection": over_concentrated,
        "thresholds": thresholds,
    }

    heuristic_sev = _routing_heuristic_severity(summary)
    samples = {
        "unrouted": unrouted[:5],
        "dominant_collection": dominant_collection[:5],
        "dominant_type": dominant_type[:5],
    }
    ai_grade = _ai_routing_severity(summary, samples) if heuristic_sev != "low" else None

    if ai_grade:
        severity = ai_grade["severity"]
        severity_source = "llm"
    else:
        severity = heuristic_sev
        severity_source = "heuristic"

    return {
        "unrouted": unrouted[:50],
        "dominant_collection": dominant_collection,
        "dominant_type": dominant_type,
        "collection_distribution": dict(col_counts),
        "type_distribution": dict(type_counts),
        "summary": summary,
        "severity": severity,
        "severity_source": severity_source,
        "heuristic_severity": heuristic_sev,
        "empty": False,
        "message": None,
    }


# ── Bulk Edit Anomaly Detection (aiify-rm-a3344-phase-12) ─────────────────────
# Analogous to paperless-ngx bulk_edit.py hardcoded_threshold → anomaly_detection.
# paperless bulk_edit.py uses a hardcoded CHUNK_SIZE constant and fixed per-operation
# document-count guards to gate bulk tag/correspondent/type/storage-path mutations.
# DIC replaces those with IQR-based detection over dic_ingest_jobs batch sizes and
# error rates, adapting to the actual throughput of the corpus rather than a static cap.
#
# Detects:
#   - Oversized batch jobs (chunks_total outlier, IQR upper fence)
#   - High error-rate jobs (errors_json non-empty at an abnormal rate)
#   - Failed jobs at collection level (status='error' cluster within one collection)
#   - Chunk-completion ratio anomalies (jobs abandoned mid-batch)

_BULK_EDIT_SYSTEM_PROMPT = (
    "You are a document batch-processing quality analyst. You are given metrics "
    "about bulk ingest/edit jobs: oversized batch counts, error rate, failed job "
    "counts, and abandoned (partial-completion) job counts. "
    "Assess the overall health of the bulk editing pipeline. "
    "Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<the single most concerning anomaly category>"}. '
    "Never invent anomalies beyond those provided."
)


def _ai_bulk_edit_severity(summary: dict, samples: dict) -> dict | None:
    """Grade bulk-edit anomaly severity with the LLM."""
    if not any(summary.get(k, 0) for k in (
        "oversized_batch_count", "error_job_count", "abandoned_job_count",
        "collection_failure_clusters",
    )):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        heuristic = _bulk_edit_heuristic_severity(summary)
        lines = [
            f"Bulk edit anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {heuristic}",
            "Sample oversized batch jobs: " + _json.dumps(samples.get("oversized", [])[:5]),
            "Sample error jobs: " + _json.dumps(samples.get("errors", [])[:5]),
            "Sample abandoned jobs: " + _json.dumps(samples.get("abandoned", [])[:5]),
        ]
        req = LLMRequest(
            function_name="anomaly_detection",
            prompt="\n".join(lines),
            system=_BULK_EDIT_SYSTEM_PROMPT,
            max_tokens=256,
        )
        result = LLMRouter().invoke("anomaly_detection", req)
        raw = (result.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = _json.loads(raw[start:end + 1])
        if parsed.get("severity") not in ("low", "medium", "high"):
            return None
        return parsed
    except Exception as exc:
        logger.debug("dic.analytics: bulk edit LLM grade failed: %s", exc)
        return None


def _bulk_edit_heuristic_severity(summary: dict) -> str:
    """Deterministic severity for bulk-edit anomalies — always-available baseline."""
    if summary.get("error_job_count", 0) > 0 or summary.get("collection_failure_clusters", 0) > 0:
        return "high"
    if summary.get("abandoned_job_count", 0) > 0 or summary.get("oversized_batch_count", 0) > 0:
        return "medium"
    return "low"


def detect_bulk_edit_anomalies(
    collection_id: str | None = None,
) -> dict:
    """Detect anomalous patterns in bulk document editing/ingest operations.

    Analogous to paperless-ngx bulk_edit.py hardcoded_threshold →
    anomaly_detection (aiify-rm-a3344-phase-12). paperless bulk_edit.py uses a
    static CHUNK_SIZE constant and per-operation document-count caps to guard bulk
    mutations. This function replaces those fixed limits with IQR-based outlier
    detection over dic_ingest_jobs, adapting to the actual batch distribution of
    the corpus.

    Detects:
      - Oversized batch jobs: chunks_total above IQR upper fence
      - Error jobs: status='error' or non-empty errors_json
      - Abandoned jobs: done=False and chunks_done < chunks_total (partial completion)
      - Collection failure clusters: ≥2 failed jobs in the same collection

    Args:
        collection_id: Scope to one collection; ``None`` checks all.

    Returns::

        {
          "oversized_batches": [{job_id, filename, collection_id, chunks_total}],
          "error_jobs": [{job_id, filename, collection_id, status, error_count}],
          "abandoned_jobs": [{job_id, filename, collection_id,
                              chunks_done, chunks_total, completion_ratio}],
          "collection_failure_clusters": [{collection_id, failure_count}],
          "summary": {oversized_batch_count, error_job_count, abandoned_job_count,
                      collection_failure_clusters, total_jobs, thresholds},
          "severity": "low|medium|high",
          "severity_source": "llm|heuristic",
          "heuristic_severity": "low|medium|high",
          "empty": bool,
          "message": str | None,
        }
    """
    import json as _json

    conn = _conn()
    try:
        params: tuple = (collection_id,) if collection_id else ()
        cid_filter = "WHERE collection_id = %s" if collection_id else ""
        rows = _safe(
            conn,
            f"SELECT job_id, filename, collection_id, status, "
            f"chunks_total, chunks_done, errors_json "
            f"FROM dic_ingest_jobs {cid_filter} ORDER BY job_id",
            params,
        )
    finally:
        conn.close()

    if not rows:
        return {
            "oversized_batches": [],
            "error_jobs": [],
            "abandoned_jobs": [],
            "collection_failure_clusters": [],
            "summary": {
                "oversized_batch_count": 0,
                "error_job_count": 0,
                "abandoned_job_count": 0,
                "collection_failure_clusters": 0,
                "total_jobs": 0,
                "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No ingest jobs found.",
        }

    # IQR over batch sizes to replace the paperless hardcoded CHUNK_SIZE cap.
    chunk_totals = [float(r.get("chunks_total") or 0) for r in rows if (r.get("chunks_total") or 0) > 0]
    _, size_hi = _iqr_outliers(chunk_totals) if len(chunk_totals) >= 4 else (0.0, float("inf"))

    oversized_batches: list[dict] = []
    error_jobs: list[dict] = []
    abandoned_jobs: list[dict] = []
    failure_by_collection: dict[str, int] = defaultdict(int)

    for r in rows:
        job_id = r.get("job_id") or ""
        filename = r.get("filename") or ""
        cid = r.get("collection_id") or ""
        status = (r.get("status") or "").lower()
        chunks_total = int(r.get("chunks_total") or 0)
        chunks_done = int(r.get("chunks_done") or 0)

        try:
            errors = _json.loads(r.get("errors_json") or "[]")
            error_count = len(errors) if isinstance(errors, list) else 0
        except Exception:
            error_count = 0

        # Oversized batch — IQR upper fence instead of a hardcoded max.
        if size_hi < float("inf") and chunks_total > size_hi:
            oversized_batches.append({
                "job_id": job_id,
                "filename": filename,
                "collection_id": cid,
                "chunks_total": chunks_total,
            })

        # Error jobs — status 'error' or any entries in errors_json.
        if status == "error" or error_count > 0:
            error_jobs.append({
                "job_id": job_id,
                "filename": filename,
                "collection_id": cid,
                "status": status,
                "error_count": error_count,
            })
            failure_by_collection[cid] += 1

        # Abandoned jobs — non-terminal status but chunks_done < chunks_total.
        elif status not in ("done", "error") and chunks_total > 0 and chunks_done < chunks_total:
            ratio = round(chunks_done / chunks_total, 3) if chunks_total else 0.0
            abandoned_jobs.append({
                "job_id": job_id,
                "filename": filename,
                "collection_id": cid,
                "chunks_done": chunks_done,
                "chunks_total": chunks_total,
                "completion_ratio": ratio,
            })

    collection_failure_clusters = [
        {"collection_id": cid, "failure_count": cnt}
        for cid, cnt in failure_by_collection.items()
        if cnt >= 2
    ]

    thresholds: dict = {}
    if size_hi < float("inf"):
        thresholds["chunks_total_upper_fence"] = round(size_hi, 1)

    summary = {
        "oversized_batch_count": len(oversized_batches),
        "error_job_count": len(error_jobs),
        "abandoned_job_count": len(abandoned_jobs),
        "collection_failure_clusters": len(collection_failure_clusters),
        "total_jobs": len(rows),
        "thresholds": thresholds,
    }

    heuristic_sev = _bulk_edit_heuristic_severity(summary)
    samples = {
        "oversized": oversized_batches[:5],
        "errors": error_jobs[:5],
        "abandoned": abandoned_jobs[:5],
    }
    ai_grade = _ai_bulk_edit_severity(summary, samples) if heuristic_sev != "low" else None

    if ai_grade:
        severity = ai_grade["severity"]
        severity_source = "llm"
    else:
        severity = heuristic_sev
        severity_source = "heuristic"

    return {
        "oversized_batches": oversized_batches[:50],
        "error_jobs": error_jobs[:50],
        "abandoned_jobs": abandoned_jobs[:50],
        "collection_failure_clusters": collection_failure_clusters,
        "summary": summary,
        "severity": severity,
        "severity_source": severity_source,
        "heuristic_severity": heuristic_sev,
        "empty": False,
        "message": None,
    }


# ── Ingest Throughput Anomaly Detection (aiify-rm-a3344-phase-93) ─────────────
# Analogous to paperless-ngx src/documents/tasks.py hardcoded_threshold →
# anomaly_detection (opp-93). paperless hardcodes queue-depth and task-expiry
# constants that silently break at scale; DIC replaces them with IQR-based
# per-hour throughput outlier detection that adapts to actual pipeline cadence.
#
# Detects: hours with abnormally low completed-job counts (below the IQR lower
# fence computed from the rolling 30-day hourly completion distribution).
# No hardcoded minimum-throughput threshold — the fence adapts to the corpus.

_THROUGHPUT_SYSTEM_PROMPT = (
    "You are a document ingestion pipeline throughput analyst. You are given "
    "statistics about completed ingestion jobs bucketed by hour over the past "
    "30 days: the hourly distribution summary and the hours that fell below the "
    "IQR-derived lower fence (abnormally low throughput). Assess the severity. "
    "Respond ONLY with a JSON object: "
    '{"severity": "low|medium|high", "rationale": "<=160 chars", '
    '"top_concern": "<brief label for the most pressing throughput issue>"}. '
    "Never invent anomalies beyond those provided."
)


def _throughput_heuristic_severity(summary: dict) -> str:
    """Deterministic throughput anomaly severity — always-available baseline.

    Low-throughput hours above 20% of the sample window or any zero-throughput
    hour in an otherwise active pipeline are elevated to medium or high.
    """
    n_low = summary.get("low_throughput_hours", 0)
    n_zero = summary.get("zero_throughput_hours_in_active_window", 0)
    n_total = summary.get("sample_hours", 0)
    if n_total == 0:
        return "low"
    if n_zero >= 3 or (n_total > 0 and n_low / n_total > 0.4):
        return "high"
    if n_zero >= 1 or (n_total > 0 and n_low / n_total > 0.2):
        return "medium"
    return "low"


def _ai_throughput_severity(summary: dict, samples: dict) -> dict | None:
    """Grade throughput anomaly severity with the LLM."""
    if not any(summary.get(k, 0) for k in (
        "low_throughput_hours", "zero_throughput_hours_in_active_window",
    )):
        return None
    try:
        import json as _json
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter

        heuristic = _throughput_heuristic_severity(summary)
        lines = [
            f"Throughput anomaly summary: {_json.dumps(summary, sort_keys=True)}",
            f"Deterministic baseline severity: {heuristic}",
            "Sample low-throughput hours: " + _json.dumps(samples.get("low_hours", [])[:5]),
            "Sample zero-throughput hours: " + _json.dumps(samples.get("zero_hours", [])[:5]),
        ]
        req = LLMRequest(
            function_name="anomaly_detection",
            prompt="\n".join(lines),
            system=_THROUGHPUT_SYSTEM_PROMPT,
            max_tokens=256,
        )
        result = LLMRouter().invoke("anomaly_detection", req)
        raw = (result.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = _json.loads(raw[start:end + 1])
        if parsed.get("severity") not in ("low", "medium", "high"):
            return None
        return parsed
    except Exception as exc:
        logger.debug("dic.analytics: throughput LLM grade failed: %s", exc)
        return None


def detect_ingest_throughput_anomaly(
    collection_id: str | None = None,
    lookback_days: int = 30,
) -> dict:
    """Detect hours with abnormally low document ingestion throughput.

    Buckets completed ingest jobs by UTC hour over the past ``lookback_days``
    days, then applies IQR-based outlier detection to the per-hour counts.
    Hours whose count falls below the lower fence are flagged as anomalous.
    No hardcoded minimum-throughput threshold — the fence adapts to the actual
    pipeline cadence (aiify-rm-a3344-phase-93: hardcoded_threshold →
    anomaly_detection, analogous to paperless tasks.py queue constants).

    Args:
        collection_id: Scope to one collection; ``None`` checks all.
        lookback_days: Rolling window for the hourly distribution baseline.
            Defaults to 30 days; callers can widen without touching source code.

    Returns::

        {
          "low_throughput_hours": [{"hour_utc": "YYYY-MM-DDTHH", "count": int,
                                    "lower_fence": float}],
          "zero_throughput_hours_in_active_window": [{"hour_utc": "YYYY-MM-DDTHH"}],
          "hourly_distribution": {"p25": float, "p50": float, "p75": float,
                                  "lower_fence": float, "upper_fence": float},
          "summary": {low_throughput_hours, zero_throughput_hours_in_active_window,
                      sample_hours, total_completed_jobs, thresholds},
          "severity": "low|medium|high",
          "severity_source": "llm|heuristic",
          "heuristic_severity": "low|medium|high",
          "empty": bool,
          "message": str | None,
        }
    """
    from datetime import timedelta

    conn = _conn()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        cid_clause = "AND collection_id = %s" if collection_id else ""
        params: tuple = (cutoff.isoformat(), collection_id) if collection_id else (cutoff.isoformat(),)
        rows = _safe(
            conn,
            f"SELECT job_id, collection_id, created_at, updated_at, status "
            f"FROM dic_ingest_jobs "
            f"WHERE status = 'done' AND updated_at >= %s {cid_clause} "
            f"ORDER BY updated_at",
            params,
        )
    finally:
        conn.close()

    if not rows:
        return {
            "low_throughput_hours": [],
            "zero_throughput_hours_in_active_window": [],
            "hourly_distribution": {},
            "summary": {
                "low_throughput_hours": 0,
                "zero_throughput_hours_in_active_window": 0,
                "sample_hours": 0,
                "total_completed_jobs": 0,
                "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No completed ingestion jobs in the lookback window.",
        }

    def _parse_ts(v) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            dt = v
        else:
            try:
                dt = datetime.fromisoformat(str(v))
            except (ValueError, TypeError):
                return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    # Bucket by UTC hour string "YYYY-MM-DDTHH"
    from collections import defaultdict as _dd
    hour_counts: dict[str, int] = _dd(int)
    for r in rows:
        ts = _parse_ts(r.get("updated_at"))
        if ts is None:
            continue
        bucket = ts.strftime("%Y-%m-%dT%H")
        hour_counts[bucket] += 1

    if not hour_counts:
        return {
            "low_throughput_hours": [],
            "zero_throughput_hours_in_active_window": [],
            "hourly_distribution": {},
            "summary": {
                "low_throughput_hours": 0,
                "zero_throughput_hours_in_active_window": 0,
                "sample_hours": 0,
                "total_completed_jobs": len(rows),
                "thresholds": {},
            },
            "severity": "low",
            "severity_source": "heuristic",
            "heuristic_severity": "low",
            "empty": True,
            "message": "No parseable timestamps in completed jobs.",
        }

    counts_list = list(hour_counts.values())
    lo_fence, hi_fence = _iqr_outliers([float(c) for c in counts_list])
    sorted_hours = sorted(hour_counts.items())

    # Hours below the lower fence are flagged as low-throughput outliers.
    low_hours: list[dict] = []
    if lo_fence > float("-inf"):
        for bucket, cnt in sorted_hours:
            if cnt < lo_fence:
                low_hours.append({
                    "hour_utc": bucket,
                    "count": cnt,
                    "lower_fence": round(lo_fence, 2),
                })

    # Zero-throughput hours only flagged when there are active hours nearby,
    # meaning the pipeline was running but produced nothing for that hour.
    # We define "active window" as any hour that has ≥1 job in the dataset,
    # so isolated zero-hour gaps between runs are not surfaced as anomalies.
    # (gap detection is left to stale-job detection in detect_ingest_job_anomalies)
    zero_hours: list[dict] = []
    if len(sorted_hours) >= 4:
        _active_set = {bucket for bucket, cnt in sorted_hours if cnt > 0}
        # For each consecutive pair of active hours more than 1 hour apart,
        # emit intermediate hours as zero-throughput gaps (max 5 per pair).
        from datetime import timedelta as _td
        for i in range(len(sorted_hours) - 1):
            b0, _ = sorted_hours[i]
            b1, _ = sorted_hours[i + 1]
            try:
                t0 = datetime.strptime(b0, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
                t1 = datetime.strptime(b1, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            gap_hours = int((t1 - t0).total_seconds() // 3600)
            if 1 < gap_hours <= 6:
                for g in range(1, gap_hours):
                    gh = (t0 + _td(hours=g)).strftime("%Y-%m-%dT%H")
                    zero_hours.append({"hour_utc": gh})

    n = len(counts_list)
    s = sorted(counts_list)
    dist: dict = {}
    if n >= 2:
        dist = {
            "p25": round(s[n // 4], 2),
            "p50": round(s[n // 2], 2),
            "p75": round(s[(3 * n) // 4], 2),
            "lower_fence": round(lo_fence, 2) if lo_fence > float("-inf") else None,
            "upper_fence": round(hi_fence, 2) if hi_fence < float("inf") else None,
        }

    summary = {
        "low_throughput_hours": len(low_hours),
        "zero_throughput_hours_in_active_window": len(zero_hours),
        "sample_hours": len(hour_counts),
        "total_completed_jobs": len(rows),
        "thresholds": {
            "lookback_days": lookback_days,
            "lower_fence": round(lo_fence, 2) if lo_fence > float("-inf") else None,
        },
    }
    heuristic_sev = _throughput_heuristic_severity(summary)
    samples = {
        "low_hours": low_hours[:5],
        "zero_hours": zero_hours[:5],
    }
    ai_grade = _ai_throughput_severity(summary, samples) if heuristic_sev != "low" else None

    if ai_grade:
        severity = ai_grade["severity"]
        severity_source = "llm"
    else:
        severity = heuristic_sev
        severity_source = "heuristic"

    return {
        "low_throughput_hours": low_hours[:50],
        "zero_throughput_hours_in_active_window": zero_hours[:50],
        "hourly_distribution": dist,
        "summary": summary,
        "severity": severity,
        "severity_source": severity_source,
        "heuristic_severity": heuristic_sev,
        "empty": False,
        "message": None,
    }
