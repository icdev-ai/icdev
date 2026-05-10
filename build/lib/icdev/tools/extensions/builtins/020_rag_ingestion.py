# [TEMPLATE: CUI // SP-CTI]
"""RAG real-time ingestion extension hook (D-RAG-9, D261).

Hooks into TOOL_EXECUTE_AFTER to automatically ingest new records from
high-priority source tables into the RAG vector store in real-time.

Extension Point: TOOL_EXECUTE_AFTER (observational tier)
Trigger: When tools write to tables registered as realtime sources.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Source tables that trigger real-time RAG ingestion
REALTIME_TABLES = {
    "innovation_signals",
    "innovation_solutions",
    "creative_pain_points",
    "creative_feature_gaps",
    "creative_specs",
    "research_dossiers",
    "research_challenges",
    "research_forecasts",
    "memory_entries",
    "anvil_critique_findings",
    "ni_device_configs",
    "nc_documents",
}

# Map table names to source_type keys
TABLE_TO_SOURCE_TYPE = {
    "innovation_signals": "innovation_signals",
    "innovation_solutions": "innovation_solutions",
    "creative_pain_points": "creative_pain_points",
    "creative_feature_gaps": "creative_feature_gaps",
    "creative_specs": "creative_specs",
    "research_dossiers": "research_dossiers",
    "research_challenges": "research_challenges",
    "research_forecasts": "research_forecasts",
    "memory_entries": "memory_entries",
    "anvil_critique_findings": "anvil_critique_findings",
    "ni_device_configs": "ndc_configs",
    "nc_documents": "ndc_documents",
}


def extension_info() -> Dict[str, Any]:
    """Extension metadata for ExtensionManager."""
    return {
        "id": "020_rag_ingestion",
        "name": "RAG Real-Time Ingestion",
        "description": "Auto-ingest new records into RAG vector store",
        "hook_point": "TOOL_EXECUTE_AFTER",
        "tier": "observational",
        "phase": 64,
        "enabled": True,
    }


def handle(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle TOOL_EXECUTE_AFTER events for RAG ingestion.

    Checks if the tool execution wrote to a realtime source table.
    If so, triggers async ingestion of the new record.

    Args:
        context: Extension context with tool_name, tool_result, etc.

    Returns:
        Optional dict with ingestion result (observational — does not modify flow).
    """
    tool_result = context.get("tool_result", {})
    if not isinstance(tool_result, dict):
        return None

    # Check if RAG is enabled
    try:
        from pathlib import Path
        import yaml

        config_path = Path(__file__).resolve().parent.parent.parent.parent / "args" / "rag_config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            if not cfg.get("rag", {}).get("enabled", True):
                return None
    except Exception:
        pass

    # Detect which table was written to
    # Convention: tools return {"table": "...", "id": "..."} or similar
    table_name = tool_result.get("table", "")
    if not table_name:
        # Try to infer from tool name patterns
        tool_name = context.get("tool_name", "")
        for tbl in REALTIME_TABLES:
            if tbl in tool_name.lower():
                table_name = tbl
                break

    if table_name not in REALTIME_TABLES:
        return None

    source_type = TABLE_TO_SOURCE_TYPE.get(table_name)
    if not source_type:
        return None

    # Build record from tool result
    record = tool_result.copy()
    tenant_id = context.get("tenant_id", "")
    project_id = context.get("project_id", "")

    # D-RAG-18: Thread correlation ID from extension context
    correlation_id = context.get("correlation_id", "")

    try:
        from tools.rag.ingestion_manager import ingest_single_record

        result = ingest_single_record(
            source_type=source_type,
            record=record,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
        )
        return {"rag_ingestion": result}
    except Exception:
        # Observational tier — never fail the main tool execution
        return None
