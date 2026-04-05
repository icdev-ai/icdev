# [TEMPLATE: CUI // SP-CTI]
"""Declarative source registry for RAG ingestion (D-RAG-9).

Maps 20+ source types to their DB tables, content columns, metadata columns,
priority, and chunking strategy. Add new sources without code changes.
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Source registry: source_type → table/column mappings
# ---------------------------------------------------------------------------
# Each entry defines:
#   table:          DB table name
#   db:             Database file ('icdev' or 'memory')
#   pk:             Primary key column
#   content_cols:   Columns to index (concatenated for chunking)
#   metadata_cols:  Columns to include as metadata on VectorChunk
#   priority:       Ingestion priority (1=highest)
#   mode:           'realtime' or 'batch'
#   description:    Human-readable description

SOURCE_REGISTRY: Dict[str, Dict[str, Any]] = {
    # --- Innovation Engine ---
    "innovation_signals": {
        "table": "innovation_signals",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "description"],
        "metadata_cols": ["innovation_score", "status", "category", "source", "discovered_at"],
        "priority": 1,
        "mode": "realtime",
        "description": "Innovation Engine discovered signals",
    },
    "innovation_solutions": {
        "table": "innovation_solutions",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["spec_content"],
        "metadata_cols": ["spec_quality_score", "status", "asset_type", "gotcha_layer"],
        "priority": 2,
        "mode": "realtime",
        "description": "Innovation Engine generated solution specs",
    },
    "innovation_trends": {
        "table": "innovation_trends",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["pattern_description", "keywords"],
        "metadata_cols": ["trend_type", "signal_count", "velocity", "status"],
        "priority": 3,
        "mode": "batch",
        "description": "Innovation Engine detected trends",
    },
    # --- Creative Engine ---
    "creative_pain_points": {
        "table": "creative_pain_points",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "description"],
        "metadata_cols": ["composite_score", "category", "severity", "frequency"],
        "priority": 1,
        "mode": "realtime",
        "description": "Creative Engine customer pain points",
    },
    "creative_feature_gaps": {
        "table": "creative_feature_gaps",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["feature_name", "description"],
        "metadata_cols": ["gap_score", "market_demand", "requested_by_count"],
        "priority": 1,
        "mode": "realtime",
        "description": "Creative Engine feature gaps",
    },
    "creative_specs": {
        "table": "creative_specs",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "spec_content", "justification"],
        "metadata_cols": ["composite_score", "status", "estimated_effort"],
        "priority": 2,
        "mode": "realtime",
        "description": "Creative Engine feature specifications",
    },
    "creative_trends": {
        "table": "creative_trends",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["trend_description", "keywords"],
        "metadata_cols": ["trend_type", "velocity", "signal_count"],
        "priority": 3,
        "mode": "batch",
        "description": "Creative Engine detected trends",
    },
    # --- Research Engine ---
    "research_dossiers": {
        "table": "research_dossiers",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "content", "executive_summary"],
        "metadata_cols": ["overall_opportunity_score", "vertical_id", "capability_coverage", "status"],
        "priority": 1,
        "mode": "realtime",
        "description": "Research Engine dossiers",
    },
    "research_challenges": {
        "table": "research_challenges",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "description"],
        "metadata_cols": ["composite_score", "severity", "category", "icdev_readiness"],
        "priority": 1,
        "mode": "realtime",
        "description": "Research Engine industry challenges",
    },
    "research_forecasts": {
        "table": "research_forecasts",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "description"],
        "metadata_cols": ["confidence", "surprise_score", "composite_rank", "prediction_type", "time_horizon"],
        "priority": 2,
        "mode": "realtime",
        "description": "Research Engine cross-engine forecasts",
    },
    # --- Compliance & Security ---
    "compliance_artifacts": {
        "table": "audit_trail",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["action", "details"],
        "metadata_cols": ["event_type", "actor", "project_id", "created_at"],
        "priority": 3,
        "mode": "realtime",
        "description": "Compliance-related audit trail entries",
        "filter": "event_type LIKE 'ssp_%' OR event_type LIKE 'poam_%' OR event_type LIKE 'stig_%' OR event_type LIKE 'sbom_%' OR event_type LIKE 'compliance_%'",  # noqa: E501
    },
    "atlas_critique_findings": {
        "table": "atlas_critique_findings",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "description", "evidence", "suggested_fix"],
        "metadata_cols": ["severity", "finding_type", "critic_agent", "nist_controls"],
        "priority": 1,
        "mode": "realtime",
        "description": "ATLAS-CR adversarial critique findings",
    },
    # --- Memory ---
    "memory_entries": {
        "table": "memory_entries",
        "db": "memory",
        "pk": "id",
        "content_cols": ["content"],
        "metadata_cols": ["type", "importance", "created_at"],
        "priority": 2,
        "mode": "realtime",
        "description": "Memory system entries (facts, preferences, insights)",
    },
    # --- Batch sources ---
    "audit_trail": {
        "table": "audit_trail",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["action", "details"],
        "metadata_cols": ["event_type", "actor", "project_id", "created_at"],
        "priority": 5,
        "mode": "batch",
        "description": "Full audit trail (batch ingest)",
    },
    "ai_telemetry": {
        "table": "ai_telemetry",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["function", "model_id"],
        "metadata_cols": ["provider", "input_tokens", "output_tokens", "latency_ms", "agent_id"],
        "priority": 5,
        "mode": "batch",
        "description": "AI telemetry (function+model indexed, not prompt content)",
    },
    "agent_memory": {
        "table": "agent_memory",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["content"],
        "metadata_cols": ["memory_type", "importance", "agent_id", "project_id"],
        "priority": 4,
        "mode": "batch",
        "description": "Agent memory entries",
    },
    "code_quality_metrics": {
        "table": "code_quality_metrics",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["file_path", "smells"],
        "metadata_cols": ["cyclomatic_complexity", "maintainability_score", "language"],
        "priority": 5,
        "mode": "batch",
        "description": "Code quality metrics and smells",
    },
    # --- PDF Documents (D-RAG-15) ---
    "pdf_documents": {
        "table": "rag_pdf_documents",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["extracted_text"],
        "metadata_cols": ["file_name", "file_hash", "page_count", "provider_used", "classification"],
        "priority": 1,
        "mode": "batch",
        "filter": "status = 'ingested'",
        "description": "PDF documents ingested into RAG",
    },
}


def get_source_config(source_type: str) -> dict:
    """Get configuration for a source type.

    Args:
        source_type: Source type key.

    Returns:
        Source configuration dict, or empty dict if not found.
    """
    return SOURCE_REGISTRY.get(source_type, {})


def get_realtime_sources() -> List[str]:
    """Get all source types configured for real-time ingestion."""
    return [k for k, v in SOURCE_REGISTRY.items() if v.get("mode") == "realtime"]


def get_batch_sources() -> List[str]:
    """Get all source types configured for batch ingestion."""
    return [k for k, v in SOURCE_REGISTRY.items() if v.get("mode") == "batch"]


def get_all_sources() -> List[str]:
    """Get all registered source types."""
    return list(SOURCE_REGISTRY.keys())


def list_sources_summary() -> List[Dict[str, Any]]:
    """List all sources with summary info."""
    result = []
    for key, cfg in SOURCE_REGISTRY.items():
        result.append(
            {
                "source_type": key,
                "table": cfg["table"],
                "db": cfg["db"],
                "mode": cfg["mode"],
                "priority": cfg["priority"],
                "content_cols": cfg["content_cols"],
                "description": cfg["description"],
            }
        )
    return sorted(result, key=lambda x: (x["mode"], x["priority"]))
