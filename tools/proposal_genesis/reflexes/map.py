#!/usr/bin/env python3

# CUI // SP-CTI
"""R6: Map Reflex — expanded capability matching (ICDEV™ + consulting + partners).

Wraps tools/govcon/capability_mapper.py with expanded catalog (D-PG-6).
GraphRAG/KARL enrichment for compliance-neighborhood discovery (§3.4, D-KARL-1/3).
Scanner-tier only (zero Claude tokens).
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from proposal_genesis_config.yaml
# under reflexes.map.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_GRAPHRAG_TOP_K         = 5     # Default graph retrieval depth per requirement
_RAG_TOP_K              = 3     # Default RAG retrieval depth per pattern
_PARALLEL_PATTERNS_CAP  = 20    # Max patterns per parallel enrichment run
_PARALLEL_MAX_WORKERS   = 3     # Thread pool size for parallel enrichment
_PARALLEL_FUTURE_TIMEOUT = 30   # Seconds to wait per RAG/GraphRAG future
_REQ_FETCH_LIMIT        = 50    # Max shall-statements to fetch per opportunity
_OPP_PROCESS_LIMIT      = 10    # Max opportunities per run cycle


def _compute_rag_top_k(anomaly_cfg: "dict | None" = None) -> int:
    """Compute adaptive top_k for RAG/GraphRAG from historical enrichment hit rates.

    If historical data shows low hit rates (< 30%), increasing top_k improves recall.
    Clamps to [2, 10] to avoid over-fetching. Falls back to _GRAPHRAG_TOP_K when
    fewer than min_samples rows or anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    if not cfg.get("enabled", True):
        return cfg.get("fallback_top_k", _GRAPHRAG_TOP_K)

    min_samples = cfg.get("min_samples", 20)
    fallback    = cfg.get("fallback_top_k", _GRAPHRAG_TOP_K)
    low_recall_threshold  = cfg.get("low_recall_threshold", 0.30)
    high_recall_threshold = cfg.get("high_recall_threshold", 0.70)
    top_k_floor = cfg.get("adaptive_bounds", {}).get("top_k_floor", 2)
    top_k_ceil  = cfg.get("adaptive_bounds", {}).get("top_k_ceil", 10)

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT AVG(CASE WHEN graph_discovered_capabilities > 0 THEN 1.0 ELSE 0.0 END) AS hit_rate, "
            "COUNT(*) AS n "
            "FROM (SELECT graph_discovered_capabilities FROM icdev_capability_map "
            "WHERE graph_discovered_capabilities IS NOT NULL LIMIT 200) sub"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[1]
            if n and n >= min_samples:
                hit_rate = float(row["hit_rate"] if isinstance(row, dict) else row[0]) or 0.0
                if hit_rate < low_recall_threshold:
                    top_k = min(top_k_ceil, fallback + 2)   # boost recall
                elif hit_rate > high_recall_threshold:
                    top_k = max(top_k_floor, fallback - 1)  # reduce over-fetch
                else:
                    top_k = fallback
                return int(top_k)
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return fallback


# Keywords that indicate compliance-related requirements (GraphRAG compliance profile)
_COMPLIANCE_KEYWORDS = frozenset(
    {
        "fedramp",
        "nist",
        "cmmc",
        "stig",
        "ato",
        "compliance",
        "rmf",
        "fisma",
        "fips",
        "cato",
        "poam",
        "ssp",
        "oscal",
        "il4",
        "il5",
        "il6",
        "800-53",
        "800-171",
    }
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_requirement_texts(opp_id: str) -> List[str]:
    """Fetch shall-statement texts from rfp_shall_statements for an opportunity."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT statement_text FROM rfp_shall_statements "
            "WHERE proposal_opportunity_id = %s OR sam_opportunity_id = %s "
            f"ORDER BY domain_category LIMIT {_REQ_FETCH_LIMIT}",
            (opp_id, opp_id),
        ).fetchall()
        return [r["statement_text"] for r in rows if r.get("statement_text")]
    except Exception:
        return []
    finally:
        conn.close()


def _is_compliance_requirement(text: str) -> bool:
    """Check if requirement text is compliance-related (keyword match)."""
    lower = text.lower()
    return any(kw in lower for kw in _COMPLIANCE_KEYWORDS)


def _enrich_with_graphrag(opp_id: str, requirements: List[str]) -> Dict[str, Any]:
    """Enrich capability mapping with GraphRAG compliance-neighborhood discovery.

    Uses graph_rag.retrieve() with compliance profile (D-KARL-1) to expand
    the control neighborhood beyond what keyword matching finds.
    Scanner-tier only (zero Claude tokens).

    Args:
        opp_id: Opportunity ID for logging.
        requirements: List of requirement texts to query.

    Returns:
        Dict with graph_matches count, expanded_controls, and
        compliance_neighborhoods discovered.  Graceful skip on import failure.
    """
    try:
        from tools.knowledge_graph.graph_rag import retrieve as graph_retrieve
    except ImportError:
        logger.debug("graph_rag not available — skipping GraphRAG enrichment")
        return {"status": "skipped", "reason": "graph_rag_not_available"}

    graph_matches = 0
    expanded_controls: List[str] = []
    compliance_neighborhoods = 0
    seen_controls: set = set()

    for req_text in requirements:
        profile = "compliance" if _is_compliance_requirement(req_text) else "exploratory"
        try:
            result = graph_retrieve(
                query=req_text,
                profile=profile,
                top_k=_GRAPHRAG_TOP_K,
                compress=False,
            )
            nodes_returned = result.get("nodes_returned", 0)
            if nodes_returned > 0:
                graph_matches += 1

            # Extract control identifiers from context (e.g., "AC-2", "SC-7")
            context = result.get("context", "")
            if context:
                controls = re.findall(r"\b([A-Z]{2}-\d{1,2}(?:\(\d+\))?)\b", context)
                for ctrl in controls:
                    if ctrl not in seen_controls:
                        seen_controls.add(ctrl)
                        expanded_controls.append(ctrl)

            if profile == "compliance" and nodes_returned > 0:
                compliance_neighborhoods += 1
        except Exception as exc:
            logger.debug("GraphRAG query failed for opp %s: %s", opp_id, exc)

    return {
        "status": "ok",
        "graph_matches": graph_matches,
        "expanded_controls": expanded_controls,
        "compliance_neighborhoods": compliance_neighborhoods,
        "requirements_queried": len(requirements),
    }


def _enrich_with_parallel_rag(opp_id: str, patterns: List[str]) -> Dict[str, Any]:
    """Enrich mapping with parallel multi-strategy retrieval (D-KARL-3).

    Fires RAG retriever + GraphRAG concurrently via ThreadPoolExecutor
    for each pattern text, then synthesises results.
    Scanner-tier only (zero Claude tokens).

    Args:
        opp_id: Opportunity ID for logging/context.
        patterns: List of pattern texts to query.

    Returns:
        Dict with enrichment counts.  Graceful skip on import failure.
    """
    # Attempt imports — either or both may be unavailable
    rag_retriever = None
    graph_retrieve = None

    try:
        from tools.rag.retriever import RAGRetriever

        rag_retriever = RAGRetriever()
    except ImportError:
        pass

    try:
        from tools.knowledge_graph.graph_rag import retrieve as _gr

        graph_retrieve = _gr
    except ImportError:
        pass

    if rag_retriever is None and graph_retrieve is None:
        logger.debug("Neither RAG retriever nor graph_rag available — skipping parallel enrichment")
        return {"status": "skipped", "reason": "no_rag_modules_available"}

    rag_hits = 0
    graph_hits = 0

    def _rag_query(text: str) -> Dict[str, Any]:
        """Query RAG retriever for a single pattern."""
        if rag_retriever is None:
            return {"source": "rag", "count": 0}
        try:
            results = rag_retriever.retrieve(query=text, top_k=_RAG_TOP_K)
            return {"source": "rag", "count": len(results)}
        except Exception:
            return {"source": "rag", "count": 0}

    def _graph_query(text: str) -> Dict[str, Any]:
        """Query GraphRAG for a single pattern."""
        if graph_retrieve is None:
            return {"source": "graphrag", "count": 0}
        try:
            result = graph_retrieve(
                query=text,
                profile="compliance",
                top_k=_RAG_TOP_K,
                compress=False,
            )
            return {"source": "graphrag", "count": result.get("nodes_returned", 0)}
        except Exception:
            return {"source": "graphrag", "count": 0}

    # Parallel retrieval: RAG + GraphRAG for each pattern
    with ThreadPoolExecutor(max_workers=_PARALLEL_MAX_WORKERS) as pool:
        futures = []
        for ptext in patterns[:_PARALLEL_PATTERNS_CAP]:
            futures.append(pool.submit(_rag_query, ptext))
            futures.append(pool.submit(_graph_query, ptext))

        for future in as_completed(futures):
            try:
                res = future.result(timeout=_PARALLEL_FUTURE_TIMEOUT)
                if res.get("source") == "rag" and res.get("count", 0) > 0:
                    rag_hits += 1
                elif res.get("source") == "graphrag" and res.get("count", 0) > 0:
                    graph_hits += 1
            except Exception:
                pass

    return {
        "status": "ok",
        "patterns_queried": min(len(patterns), 20),
        "rag_hits": rag_hits,
        "graph_hits": graph_hits,
    }


def _map_opportunity(opp_id: str) -> Dict[str, Any]:
    """Map capabilities for a single opportunity using existing mapper.

    After keyword-based mapping, runs GraphRAG enrichment to discover
    capabilities that compliance-neighborhood expansion reveals (§3.4).
    """
    try:
        from tools.govcon.capability_mapper import map_all_patterns, get_coverage

        map_all_patterns(opp_id)
        coverage = get_coverage(opp_id)
        result = coverage if isinstance(coverage, dict) else {"status": "ok"}
    except ImportError:
        return {"status": "import_error", "message": "capability_mapper not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # GraphRAG enrichment — discover capabilities keyword matching missed
    requirements = _get_requirement_texts(opp_id)
    if requirements:
        graphrag = _enrich_with_graphrag(opp_id, requirements)
        result["graphrag_enrichment"] = graphrag

        # If GraphRAG found compliance neighborhoods that keyword matching
        # didn't cover, log them as graph_discovered.
        graph_discovered = graphrag.get("graph_matches", 0)
        result["graph_discovered_capabilities"] = graph_discovered
    else:
        result["graphrag_enrichment"] = {"status": "no_requirements"}
        result["graph_discovered_capabilities"] = 0

    return result


def _get_partner_capabilities() -> List[Dict[str, str]]:
    """Load teaming partner capabilities from DB (D-PG-6)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, capabilities FROM pg_teaming_partners "
            "WHERE status = 'active' AND capabilities IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _enrich_with_knowledge_base(opp_id: str) -> Dict[str, Any]:
    """Enrich capability mapping with knowledge base past performance (D-PG-4)."""
    try:
        from tools.govcon.knowledge_base import search_blocks

        conn = get_connection()
        try:
            # Get requirement patterns for this opportunity
            rows = conn.execute(
                "SELECT pattern_text FROM rfp_requirement_patterns WHERE opportunity_id = %s LIMIT 20", (opp_id,)
            ).fetchall()
        finally:
            conn.close()

        kb_matches = 0
        for row in rows:
            try:
                results = search_blocks(row["pattern_text"], limit=3)
                if isinstance(results, list) and len(results) > 0:
                    kb_matches += 1
            except Exception:
                pass

        return {"kb_patterns_matched": kb_matches, "patterns_checked": len(rows)}
    except ImportError:
        return {"status": "kb_not_available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Map Reflex (R6).

    Triggered after R5 Extract. Maps extracted requirements to expanded
    capability catalog (ICDEV™ + consulting services + domain expertise +
    partner/teaming capabilities).
    """
    conn = get_connection()
    try:
        # Find opportunities with extracted statements but no capability mapping
        rows = conn.execute("""
            SELECT DISTINCT po.id, po.title
            FROM proposal_opportunities po
            INNER JOIN rfp_shall_statements ss ON ss.opportunity_id = po.id
            LEFT JOIN icdev_capability_map cm ON cm.opportunity_id = po.id
            WHERE po.status IN ('tracking', 'drafting')
            AND cm.id IS NULL
            ORDER BY po.created_at DESC
            LIMIT {_OPP_PROCESS_LIMIT}
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    # Compute adaptive top_k from historical enrichment hit rates.
    anomaly_cfg = config.get("anomaly_detection", {})
    adaptive_top_k = _compute_rag_top_k(anomaly_cfg)
    # Patch module-level globals for this run so all helpers use the computed value.
    global _GRAPHRAG_TOP_K, _RAG_TOP_K  # noqa: PLW0603
    _GRAPHRAG_TOP_K = adaptive_top_k
    _RAG_TOP_K = max(2, adaptive_top_k - 1)

    total_coverage = 0.0
    total_graph_discovered = 0
    mapping_results = []
    partner_caps = _get_partner_capabilities()

    for row in rows:
        opp_id = row["id"]
        result = _map_opportunity(opp_id)
        coverage = result.get("coverage_score", result.get("score", 0))
        if isinstance(coverage, (int, float)):
            total_coverage += coverage

        # Track graph-discovered capabilities
        graph_disc = result.get("graph_discovered_capabilities", 0)
        if isinstance(graph_disc, int):
            total_graph_discovered += graph_disc

        # Enrich with knowledge base
        kb_result = _enrich_with_knowledge_base(opp_id)

        # Parallel RAG enrichment (D-KARL-3) — query KB + RAG + GraphRAG
        conn_pat = get_connection()
        try:
            pat_rows = conn_pat.execute(
                "SELECT pattern_text FROM rfp_requirement_patterns WHERE opportunity_id = %s LIMIT 20",
                (opp_id,),
            ).fetchall()
        except Exception:
            pat_rows = []
        finally:
            conn_pat.close()
        patterns = [r["pattern_text"] for r in pat_rows if r.get("pattern_text")]
        parallel_rag = _enrich_with_parallel_rag(opp_id, patterns)

        mapping_results.append(
            {
                "opportunity_id": opp_id,
                "title": row["title"],
                "coverage": result,
                "kb_enrichment": kb_result,
                "graphrag_enrichment": result.get("graphrag_enrichment", {}),
                "parallel_rag_enrichment": parallel_rag,
            }
        )

    avg_coverage = total_coverage / len(rows) if rows else 0

    return {
        "success": True,
        "metric_value": round(avg_coverage, 2),
        "details": {
            "opportunities_mapped": len(rows),
            "avg_coverage_score": round(avg_coverage, 2),
            "partner_capabilities_available": len(partner_caps),
            "graph_discovered_capabilities": total_graph_discovered,
            "mapping_results": mapping_results,
        },
    }
