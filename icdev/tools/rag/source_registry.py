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
#   chunking:       Optional chunking template name, resolved against
#                   args/chunking_templates.yaml (e.g. 'oscal_catalog',
#                   'stig_checklist'). Omitted = configured default.
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
    "anvil_critique_findings": {
        "table": "anvil_critique_findings",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "description", "evidence", "suggested_fix"],
        "metadata_cols": ["severity", "finding_type", "critic_agent", "nist_controls"],
        "priority": 1,
        "mode": "realtime",
        "description": "ANVIL-CR adversarial critique findings",
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
    # --- Design Canvases (Task 17: Canvas-to-RAG Indexing) ---
    # IDC — Infrastructure Design Canvas
    "idc_designs": {
        "table": "infra_designs",
        "db": "infra_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["created_at", "updated_at"],
        "priority": 2,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "IDC",
        "description": "Infrastructure Design Canvas graph snapshots (per-node chunked)",
    },
    "idc_assessments": {
        "table": "idc_assessments",
        "db": "infra_canvas",
        "pk": "id",
        "content_cols": ["findings_json", "recommendations_json"],
        "metadata_cols": ["design_id", "score", "trigger_source", "created_at"],
        "priority": 2,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "IDC",
        "description": "Infrastructure Design Canvas assessment findings",
    },
    # NDC — Network Design Canvas
    "ndc_designs": {
        "table": "topologies",
        "db": "network_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["created_at", "updated_at"],
        "priority": 2,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "NDC",
        "description": "Network Design Canvas topology snapshots (per-node chunked)",
    },
    "ndc_compliance": {
        "table": "nc_compliance_findings",
        "db": "network_canvas",
        "pk": "id",
        "content_cols": ["rule_id", "description", "remediation"],
        "metadata_cols": ["topology_id", "severity", "status", "created_at"],
        "priority": 2,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "NDC",
        "description": "Network Design Canvas compliance findings",
    },
    # NII — Network Infrastructure Intelligence
    "network_devices": {
        "table": "ni_devices",
        "db": "network_canvas",
        "pk": "id",
        "content_cols": ["label", "device_type", "vendor", "model", "notes"],
        "metadata_cols": ["topology_id", "firmware_version", "eol_date", "criticality_score", "site"],
        "priority": 2,
        "mode": "realtime",
        "description": "Network Infrastructure Intelligence device inventory with EOL tracking",
    },
    "network_intelligence_analyses": {
        "table": "ni_analyses",
        "db": "network_canvas",
        "pk": "id",
        "content_cols": ["analysis_type", "query_text", "result_summary"],
        "metadata_cols": ["topology_id", "created_at"],
        "priority": 3,
        "mode": "batch",
        "description": "Network Intelligence analysis results (redundancy, EOL, blast radius, capacity)",
    },
    "ndc_configs": {
        "table": "ni_device_configs",
        "db": "network_canvas",
        "pk": "id",
        "content_cols": ["config_text"],
        "metadata_cols": ["device_id", "config_type", "source", "created_at"],
        "priority": 2,
        "mode": "realtime",
        "description": "Network device running/startup configurations for RAG search",
    },
    "ndc_documents": {
        "table": "nc_documents",
        "db": "network_canvas",
        "pk": "id",
        "content_cols": ["extracted_text"],
        "metadata_cols": ["file_name", "file_hash", "doc_type", "classification", "ingested_at"],
        "priority": 2,
        "mode": "batch",
        "filter": "status = 'ingested'",
        "description": "Network runbooks, SOPs, as-built docs, change requests",
    },
    "ndc_sops": {
        "table": "ndc_sops",
        "db": "network_canvas",
        "pk": "sop_id",
        "content_cols": ["title", "description", "prerequisites", "steps", "validation", "rollback", "escalation"],
        "metadata_cols": ["category", "version", "status", "classification", "author", "csp"],
        "priority": 1,
        "mode": "batch",
        "description": "Network Design Canvas SOPs — change windows, provisioning, firewall rules, DNS, etc.",
    },
    # SDC — Security Design Canvas
    "disa_stig": {
        "table": "sdc_rag_stigs",
        "db": "security_canvas",
        "pk": "vuln_id",
        "content_cols": ["title", "description", "fix_text"],
        "metadata_cols": ["severity", "nist_control", "benchmark"],
        "priority": 1,
        "mode": "batch",
        "description": "DISA STIG vulnerability rules — severity, fix text, and NIST control mappings",
    },
    "mitre_attack": {
        "table": "sdc_rag_attack_techniques",
        "db": "security_canvas",
        "pk": "technique_id",
        "content_cols": ["name", "description", "detection", "mitigations"],
        "metadata_cols": ["tactic", "platform", "is_subtechnique", "source_url"],
        "priority": 1,
        "mode": "batch",
        "stix_url": "https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json",
        "description": "MITRE ATT&CK Enterprise techniques (STIX 2.1) — tactics, platforms, detection, and mitigations",
    },
    "sdc_designs": {
        "table": "security_designs",
        "db": "security_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["created_at", "updated_at"],
        "priority": 1,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "SDC",
        "description": "Security Design Canvas graph snapshots (per-node chunked)",
    },
    "sdc_assessments": {
        "table": "sc_assessments",
        "db": "security_canvas",
        "pk": "id",
        "content_cols": ["findings_json", "stride_json", "nist_coverage_json"],
        "metadata_cols": ["design_id", "risk_score", "posture_grade", "trigger_source", "created_at"],
        "priority": 1,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "SDC",
        "description": "Security Design Canvas assessment findings (STRIDE, NIST, risk score)",
    },
    # BDC — Boundary Design Canvas
    "bdc_designs": {
        "table": "boundary_designs",
        "db": "boundary_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["created_at", "updated_at"],
        "priority": 2,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "BDC",
        "description": "Boundary Design Canvas graph snapshots (per-node chunked)",
    },
    "bdc_assessments": {
        "table": "bd_assessments",
        "db": "boundary_canvas",
        "pk": "id",
        "content_cols": ["findings_json", "recommendations_json"],
        "metadata_cols": ["design_id", "score", "grade", "trigger_source", "created_at"],
        "priority": 2,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "BDC",
        "description": "Boundary Design Canvas assessment findings",
    },
    # PDC — Pipeline Design Canvas
    "pdc_designs": {
        "table": "pipelines",
        "db": "pipeline_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["created_at", "updated_at"],
        "priority": 3,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "PDC",
        "description": "Pipeline Design Canvas graph snapshots (per-node chunked)",
    },
    "pdc_compliance": {
        "table": "pc_compliance_findings",
        "db": "pipeline_canvas",
        "pk": "id",
        "content_cols": ["rule_id", "description", "remediation"],
        "metadata_cols": ["pipeline_id", "severity", "status", "created_at"],
        "priority": 3,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "PDC",
        "description": "Pipeline Design Canvas compliance findings",
    },
    # ODC — Observability Design Canvas
    "odc_designs": {
        "table": "observability_designs",
        "db": "observability_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["created_at", "updated_at"],
        "priority": 2,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "ODC",
        "description": "Observability Design Canvas graph snapshots (per-node chunked)",
    },
    "odc_assessments": {
        "table": "od_assessments",
        "db": "observability_canvas",
        "pk": "id",
        "content_cols": ["findings_json", "recommendations_json"],
        "metadata_cols": ["design_id", "score", "trigger_source", "created_at"],
        "priority": 2,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "ODC",
        "description": "Observability Design Canvas assessment findings",
    },
    # DDC — Data Design Canvas
    "ddc_designs": {
        "table": "data_designs",
        "db": "data_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["created_at", "updated_at"],
        "priority": 2,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "DDC",
        "description": "Data Design Canvas graph snapshots (per-node chunked)",
    },
    "ddc_assessments": {
        "table": "dd_assessments",
        "db": "data_canvas",
        "pk": "id",
        "content_cols": ["findings_json", "recommendations_json"],
        "metadata_cols": ["design_id", "score", "trigger_source", "created_at"],
        "priority": 2,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "DDC",
        "description": "Data Design Canvas assessment findings",
    },
    # --- Bayesian Autoresearch (Phase 67, D-AR-1 through D-AR-10) ---
    "experiment_results": {
        "table": "experiment_results",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["hypothesis", "decision_rationale"],
        "metadata_cols": ["domain", "metric_delta", "improvement_pct", "decision", "created_at"],
        "priority": 1,
        "mode": "realtime",
        "description": "Bayesian Autoresearch experiment results and learnings",
    },
    "experiment_candidates": {
        "table": "experiment_candidates",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["hypothesis", "modifications"],
        "metadata_cols": ["domain", "category", "status", "info_gain_score", "created_at"],
        "priority": 2,
        "mode": "batch",
        "filter": "status IN ('completed', 'discarded')",
        "description": "Autoresearch experiment candidate hypotheses",
    },
    # --- Codebase Index (Phase 1b, D-AWARE-2) ---
    "icdev_codebase_files": {
        "table": "codebase_index",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["file_path", "module", "symbols"],
        "metadata_cols": ["file_type", "chunk_count", "last_indexed_at"],
        "priority": 2,
        "mode": "batch",
        "description": "ICDEV codebase (file metadata + AST-parsed symbols) — shallow metadata source for 'where is X defined' queries",
    },

    # --- Internal Component Graph (Phase 4, D-AWARE-4) ---
    # All 10 icdev_* entries below point at kg_nodes with a filter
    # scoping to graph_id='kg-icdev-self-awareness' plus an
    # entity_type filter. The content column `label` carries the
    # human-readable component name; `properties` is serialized JSON
    # containing description, file_path, blueprint_routes, etc.
    # Priority 1 for component-level sources since they're the most
    # directly relevant to Q&A queries ("what does X do").

    "icdev_skills": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 1,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'skill'",
        "description": "ICDEV Claude Code skills (.agents/skills/*/SKILL.md)",
    },
    "icdev_mcp_servers": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 1,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'mcp_server'",
        "description": "ICDEV MCP servers (tools/mcp/*_server.py) with class docstrings and tool counts",
    },
    "icdev_canvas_modules": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 1,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'canvas_module'",
        "description": "ICDEV design canvases (tools/*_canvas/) with blueprint routes and db tables",
    },
    "icdev_goals": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 1,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'goal'",
        "description": "ICDEV goal workflows (goals/*.md) — ANVIL, TDD, compliance, MBSE, etc.",
    },
    "icdev_tools": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 2,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'tool'",
        "description": "ICDEV tools (tools/manifest.md rows) — ~842 individual tool scripts with descriptions",
    },
    "icdev_a2a_agents": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 1,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'a2a_agent'",
        "description": "ICDEV A2A agents (tools/a2a/*.py) — core/domain/support tiers",
    },
    "icdev_dashboard_routes": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 2,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'dashboard_route'",
        "description": "ICDEV dashboard routes (Flask app.url_map) — 240+ URL endpoints",
    },
    "icdev_reflexes": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 1,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'reflex'",
        "description": "Genesis daemon reflexes (tools/genesis/reflexes/*.py) — 19 autonomous behaviors",
    },
    "icdev_db_tables": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 2,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'db_table'",
        "description": "ICDEV database tables (from init_icdev_db.py + canvas init_db.py files)",
    },
    "icdev_tool_categories": {
        "table": "kg_nodes",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["label", "properties"],
        "metadata_cols": ["entity_type", "graph_id"],
        "priority": 3,
        "mode": "batch",
        "filter": "graph_id = 'kg-icdev-self-awareness' AND entity_type = 'tool_category'",
        "description": "ICDEV tool category groupings (manifest.md section headers)",
    },

    # --- Strategos Conflict Intelligence ---
    "sg_corpus_documents": {
        "table": "sg_corpus_documents",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["title", "content"],
        "metadata_cols": ["source_type", "published_at", "url"],
        "priority": 1,
        "mode": "realtime",
        "description": "Strategos doctrine corpus — military doctrine and historical documents",
    },
    "sg_conflict_events": {
        "table": "sg_conflict_events",
        "db": "icdev",
        "pk": "id",
        "content_cols": ["description", "event_type", "actor1", "actor2"],
        "metadata_cols": ["theater_id", "severity", "event_ts", "source"],
        "priority": 1,
        "mode": "realtime",
        "description": "Strategos historical conflict events (GDELT, STIX, ACLED, frontlines)",
    },

    # --- AI Canvases — DoD/IC (AADC, AIMC, AAC) ---
    "aadc_designs": {
        "table": "aadc_designs",
        "db": "agentic_ai_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["domain", "classification", "autonomy_max",
                          "safety_impacting", "rights_impacting", "hitl_required",
                          "created_at"],
        "priority": 1,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "AADC",
        "description": "Agentic AI Design Canvas — DoD/IC AI system designs with threat models and ATO data",
    },
    "aadc_assessments": {
        "table": "aadc_assessments",
        "db": "agentic_ai_canvas",
        "pk": "id",
        "content_cols": ["findings_json", "atlas_threats"],
        "metadata_cols": ["design_id", "score", "nist_rmf_score", "owasp_score",
                          "omb_compliant", "created_at"],
        "priority": 1,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "AADC",
        "description": "AADC assessment findings — NIST AI RMF, OWASP LLM, OMB M-25-21, ATLAS threats",
    },
    "aiml_designs": {
        "table": "aiml_designs",
        "db": "aiml_canvas",
        "pk": "id",
        "content_cols": ["name", "description", "graph_json"],
        "metadata_cols": ["il_level", "classification", "primary_use_case",
                          "adaptation_strategy", "created_at"],
        "priority": 1,
        "mode": "batch",
        "chunking": "canvas_graph",
        "canvas_type": "AIMC",
        "description": "AI/ML Model Canvas — DoD/IC model designs with IL suitability and DoD RAI assessments",
    },
    "aiml_assessments": {
        "table": "aiml_assessments",
        "db": "aiml_canvas",
        "pk": "id",
        "content_cols": ["findings_json"],
        "metadata_cols": ["design_id", "framework_id", "framework_name",
                          "score", "passed", "created_at"],
        "priority": 1,
        "mode": "realtime",
        "chunking": "canvas_assessment",
        "canvas_type": "AIMC",
        "description": "AIMC assessment findings — NIST AI RMF, DoD RAI 5 Principles, IL compliance rules",
    },
    "aiml_artifacts": {
        "table": "aiml_artifacts",
        "db": "aiml_canvas",
        "pk": "id",
        "content_cols": ["title", "content_json"],
        "metadata_cols": ["design_id", "artifact_type", "format", "created_at"],
        "priority": 2,
        "mode": "batch",
        "canvas_type": "AIMC",
        "description": "AIMC model card artifacts — base model, IL suitability, metrics, training data provenance",
    },
    "aac_opportunities": {
        "table": "aac_opportunities",
        "db": "aac_canvas",
        "pk": "opportunity_id",
        "content_cols": ["module_path", "function_name", "pattern_detail",
                         "data_requirements"],
        "metadata_cols": ["scan_id", "language", "pattern_type", "ai_paradigm",
                          "il_recommended_model", "created_at"],
        "priority": 2,
        "mode": "batch",
        "canvas_type": "AAC",
        "description": "AAC AI augmentation opportunities — legacy code patterns with AI paradigm recommendations",
    },
    "aac_roadmaps": {
        "table": "aac_roadmaps",
        "db": "aac_canvas",
        "pk": "id",
        "content_cols": ["title", "phases"],
        "metadata_cols": ["scan_id", "roadmap_id", "total_effort_days",
                          "aimc_links", "aadc_links", "created_at"],
        "priority": 2,
        "mode": "batch",
        "canvas_type": "AAC",
        "description": "AAC modernization roadmaps — phased effort plans with AIMC/AADC cross-links",
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


def get_chunking_template(source_type: str) -> str:
    """Get the chunking template name for a source type (oss-chunk-01).

    The ``chunking`` key on a registry entry names a template defined in
    ``args/chunking_templates.yaml``. ``tools/rag/ingestion_manager.py`` passes
    the result to ``chunk_fields(template=...)``.

    Args:
        source_type: Source type key.

    Returns:
        Template name, or "" when the entry declares none (chunker then uses
        the configured default, i.e. today's sliding window).
    """
    return SOURCE_REGISTRY.get(source_type, {}).get("chunking", "") or ""


def get_realtime_sources() -> List[str]:
    """Get all source types configured for real-time ingestion."""
    return [k for k, v in SOURCE_REGISTRY.items() if v.get("mode") == "realtime"]


def get_batch_sources() -> List[str]:
    """Get all source types configured for batch ingestion."""
    return [k for k, v in SOURCE_REGISTRY.items() if v.get("mode") == "batch"]


def get_all_sources() -> List[str]:
    """Get all registered source types."""
    return list(SOURCE_REGISTRY.keys())


def get_canvas_sources(canvas_type: str = None) -> List[str]:
    """Get source types for design canvas RAG sources.

    Args:
        canvas_type: Filter by canvas type (e.g. 'SDC', 'IDC'). None returns all.

    Returns:
        List of source type keys for canvas sources.
    """
    result = []
    for k, v in SOURCE_REGISTRY.items():
        if v.get("chunking") in ("canvas_graph", "canvas_assessment"):
            if canvas_type is None or v.get("canvas_type") == canvas_type:
                result.append(k)
    return result


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
