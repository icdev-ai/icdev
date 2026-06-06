#!/usr/bin/env python3
"""Seed Kanban tasks for Ontology Integration across ICDEV Ecosystem (onto-*).

Decomposed from research: ICDEV has mature KG/RAG but no formal ontology layer.
This epic adds OWL/RDF ontology governance to unify 12 canvases, Strategos,
GeoSIGINT, Security Framework, Academy, GameDay, and child apps.

Run: python tools/kanban/seed_ontology.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

_NOW = datetime.now(timezone.utc).isoformat()


TASKS = [
    # ═══════════════════════════════════════════════════════════════════════
    # EP1: FOUNDATION — Core Ontology + Schema Extraction + Catalog
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "onto-fnd-01",
        "title": "ONTO: Create args/ontology/icdev_core.ttl (upper ontology)",
        "description": (
            "Create the shared upper ontology for ICDEV at args/ontology/icdev_core.ttl.\n\n"
            "Requirements:\n"
            "  - Pure Turtle syntax (no rdflib dependency for air-gap compat).\n"
            "  - Core classes: icdev:Project, icdev:CanvasDesign, icdev:ComplianceControl, icdev:SecurityPolicy, icdev:Artifact, icdev:Requirement, icdev:Rule.\n"
            "  - Properties: icdev:hasClassification (range icdev:ClassificationLevel), icdev:belongsToCanvas, icdev:hasTenant, icdev:hasImpactLevel.\n"
            "  - Align with existing ICDEV schema: projects table, canvas_kg_nodes, security_policies.\n"
            "  - Import standard namespaces: foaf, prov-o, dcterms, skos (as prefixes, not external HTTP deps).\n"
            "  - Provide JSON-LD @context export for API consumption.\n\n"
            "Reference: tools/db/init_icdev_db.py schema, tools/knowledge_graph/compliance_graph.py."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "onto-fnd-02",
        "title": "ONTO: Create tools/ontology/schema_extractor.py (constants.py → Turtle)",
        "description": (
            "Create deterministic schema extraction tool at tools/ontology/schema_extractor.py.\n\n"
            "Requirements:\n"
            "  - Scan all tools/*/constants.py for typed entity palettes (CLOUD_OBJECTS, DATA_OBJECTS, BOUNDARY_TYPES, etc.).\n"
            "  - Scan tools/strategos/war_kg.py for WAR_NODE_TYPES / WAR_EDGE_TYPES.\n"
            "  - Emit Turtle/RDF files to args/ontology/<domain>.ttl.\n"
            "  - Map Python dict keys to rdfs:label, type strings to rdfs:Class, descriptions to rdfs:comment.\n"
            "  - Deduplicate by normalized label across canvases.\n"
            "  - Air-gap safe: pure string templating, no rdflib.\n"
            "  - CLI: --dry-run, --output-dir, --json.\n"
            "  - Re-runnable: overwrites only changed files, preserves manual edits via merge logic.\n\n"
            "Domains to extract: network, data, security, boundary, orchestration, compliance, war, strategy, geospatial."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-01",
    },
    {
        "id": "onto-fnd-03",
        "title": "ONTO: Create tools/ontology/ontology_catalog.py (registry + validation)",
        "description": (
            "Create ontology catalog manager at tools/ontology/ontology_catalog.py.\n\n"
            "Requirements:\n"
            "  - load_catalog() → reads all args/ontology/*.ttl and builds in-memory registry.\n"
            "  - get_class(uri) → returns class definition with superclasses, properties, labels.\n"
            "  - is_valid_entity_type(type_name) → checks if type exists in any loaded ontology.\n"
            "  - find_nearest_class(type_name) → Levenshtein-based fuzzy match for misspelled types.\n"
            "  - get_subclasses(class_uri) → transitive rdfs:subClassOf traversal.\n"
            "  - get_properties_for_class(class_uri) → list of applicable properties with ranges.\n"
            "  - Cache catalog in memory + optional SQLite cache table for fast lookup.\n"
            "  - Air-gap safe: no HTTP resolution, all ontologies local.\n\n"
            "Provide CLI: --validate --ontology <file> --json."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    {
        "id": "onto-fnd-04",
        "title": "ONTO: Add ontology_id column to kg_nodes + migration",
        "description": (
            "Update knowledge graph schema to link nodes to ontology classes.\n\n"
            "Changes:\n"
            "  - Migration: ALTER TABLE kg_nodes ADD COLUMN ontology_id TEXT.\n"
            "  - ALTER TABLE canvas_kg_nodes ADD COLUMN ontology_id TEXT.\n"
            "  - Backfill: map existing entity_type values to ontology URIs via catalog fuzzy match.\n"
            "  - Update tools/knowledge_graph/ingester.py to populate ontology_id on insert.\n"
            "  - Update tests/conftest.py MINIMAL_ICDEV_SCHEMA.\n"
            "  - Update .claude/hooks/pre_tool_use.py if new audit tables are created.\n\n"
            "Verify: SELECT COUNT(*) FROM kg_nodes WHERE ontology_id IS NOT NULL matches backfill."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-03",
    },
    {
        "id": "onto-fnd-05",
        "title": "ONTO: Update ingester.py with ontology validation",
        "description": (
            "Update tools/knowledge_graph/ingester.py to enforce ontology at ingestion time.\n\n"
            "Changes:\n"
            "  - After entity extraction, validate each entity_type against ontology_catalog.is_valid_entity_type().\n"
            "  - On mismatch: log warning, propose nearest match via find_nearest_class(), and optionally auto-correct.\n"
            "  - Add strict mode: --strict-ontology → reject documents with unmapped entity types.\n"
            "  - Add --ontology flag to specify which domain ontologies to enforce.\n"
            "  - Record validation results in kg_ingestion_log (new column: ontology_validation).\n"
            "  - Performance: catalog loaded once at module init, not per document.\n\n"
            "Test with existing corpus to measure type coverage."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-04",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP2: DOMAIN ONTOLOGIES
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "onto-dom-01",
        "title": "ONTO: Create args/ontology/network.ttl (NDC cloud network objects)",
        "description": (
            "Create network domain ontology from tools/network/constants.py.\n\n"
            "Classes:\n"
            "  - NetworkObject (superclass)\n"
            "  - CloudVPC, CloudSubnet, CloudLoadBalancer, CloudVPN, CloudTransitGateway\n"
            "  - CloudFirewall, CloudDNS, CloudCDN, CloudBastion\n"
            "  - Provider-specific subclasses: AWSVPC, AzureVNet, GCPVPC, OCIVCN, IBMVPC\n"
            "Properties:\n"
            "  - hasCidrBlock, hasRegion, hasAvailabilityZone, hasProvider\n"
            "  - connectsTo (VPN/VPC peering), routesTo (TGW/route table), protectedBy (WAF/FW)\n\n"
            "Extract from CLOUD_OBJECTS dict in constants.py.\n"
            "Include rdfs:label, rdfs:comment, skos:definition for each class."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    {
        "id": "onto-dom-02",
        "title": "ONTO: Create args/ontology/data.ttl (DDC data entities)",
        "description": (
            "Create data domain ontology from tools/data_canvas/constants.py.\n\n"
            "Classes:\n"
            "  - DataEntity (superclass)\n"
            "  - RelationalTable, DatabaseView, NoSQLCollection, StreamTopic, CacheStore\n"
            "  - MessageQueue, DataLake, DataWarehouse, GraphDatabase, TimeSeriesDB, VectorStore, FileStore\n"
            "  - ColumnEntity (superclass)\n"
            "  - PrimaryKey, ForeignKey, DataColumn, PIIColumn, PHIColumn\n"
            "Properties:\n"
            "  - hasColumn, hasSchema, hasStorageEngine, hasRetentionPolicy\n"
            "  - containsPII, containsPHI, complianceTagged (GDPR, HIPAA, PCI)\n\n"
            "Extract from DATA_OBJECTS dict in constants.py.\n"
            "Map PII/PHI types to security ontology for cross-domain queries."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    {
        "id": "onto-dom-03",
        "title": "ONTO: Create args/ontology/security.ttl (SDC threat + STIG types)",
        "description": (
            "Create security domain ontology from SDC + security framework.\n\n"
            "Classes:\n"
            "  - SecurityObject (superclass)\n"
            "  - ThreatType, Vulnerability, CVE, AttackPattern, Control, STIGRule\n"
            "  - SecurityBoundary, SCIF, Enclave, DMZ, ZeroTrustZone\n"
            "  - EncryptionPolicy, AccessPolicy, AuditPolicy\n"
            "Properties:\n"
            "  - hasCVSS, hasSeverity, mitigatedBy, requiresControl\n"
            "  - hasClassification, hasCompartment, impactLevel\n"
            "  - referencesSTIG, referencesNISTControl\n\n"
            "Align with tools/sdc/threat_scanner.py, tools/sdc/stig_checker.py.\n"
            "Map to compliance ontology for cross-domain queries: STIGRule → satisfies → NISTControl."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    {
        "id": "onto-dom-04",
        "title": "ONTO: Create args/ontology/compliance.ttl (NIST/FedRAMP/CMMC crosswalk)",
        "description": (
            "Create compliance domain ontology from tools/knowledge_graph/compliance_graph.py.\n\n"
            "Classes:\n"
            "  - ComplianceFramework (superclass)\n"
            "  - NISTControlFamily, NISTControl, FedRAMPBaseline, CMMCLevel\n"
            "  - DoDImpactLevel, ATOStatus, POAMItem\n"
            "Properties:\n"
            "  - belongsToFamily, satisfiesFramework, overlapsWithControl\n"
            "  - requiredAtIL2/IL4/IL5/IL6, fedrampModerate, fedrampHigh\n"
            "  - cmmcLevel2, cmmcLevel3\n\n"
            "Extract from FRAMEWORK_BOOL_KEYS and FRAMEWORK_ID_KEYS in compliance_graph.py.\n"
            "Add owl:equivalentClass mappings where controls are semantically identical across frameworks.\n"
            "Enable SPARQL: 'Which NIST controls satisfy both FedRAMP High and CMMC Level 3?'"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    {
        "id": "onto-dom-05",
        "title": "ONTO: Create args/ontology/war.ttl (Strategos war KG)",
        "description": (
            "Create war domain ontology from tools/strategos/war_kg.py.\n\n"
            "Classes:\n"
            "  - WarEntity (superclass)\n"
            "  - MilitaryUnit, Equipment, ConflictEvent, CyberOperation, Vessel\n"
            "  - Infrastructure, LogisticsNode, BehaviorProfile, WarReadinessEvent\n"
            "Properties:\n"
            "  - deployedAt, attacked, precedes, correlatesWith, launchedFrom, supplies, damaged\n"
            "  - hasActor, hasDomain, hasStrength, hasStatus, hasCommander\n"
            "  - hasMMSI, hasFlag, hasVesselClass\n"
            "  - hasCompositeScore, hasEscalationLevel\n\n"
            "Extract from WAR_NODE_TYPES, WAR_EDGE_TYPES, NODE_TYPE_META.\n"
            "Map to security ontology: CyberOperation → subclassOf → SecurityObject.\n"
            "Map to geospatial ontology: MilitaryUnit/Vessel → hasLocation → GeoPoint."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    {
        "id": "onto-dom-06",
        "title": "ONTO: Create args/ontology/strategy.ttl (METT-TC, IPB, OPORD, F3EAD)",
        "description": (
            "Create strategy/planning domain ontology from Strategos planning tools.\n\n"
            "Classes:\n"
            "  - PlanningArtifact (superclass)\n"
            "  - METTTCWorksheet, IPBWorksheet, OPORD, SynchronizationMatrix\n"
            "  - CourseOfAction, WarCouncilBrief, CCIR, PIR, ISRRequirement\n"
            "  - F3EADTarget (Find/Fix/Finish/Exploit/Analyze/Disseminate phases)\n"
            "Properties:\n"
            "  - hasTheater, hasScenario, hasCommanderIntent, hasPMESIIVector\n"
            "  - hasPhase, hasPhaseStatus, hasTarget, hasEffect\n"
            "  - hasDoctrineCitation, hasHistoricalPrecedent\n"
            "  - satisfiesCCIR, satisfiesPIR\n\n"
            "Extract from tools/strategos/mett_tc.py, ipb.py, opord.py, f3ead.py, sync_matrix.py.\n"
            "Enable cross-domain: COA → uses → MilitaryUnit (war ontology)."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    {
        "id": "onto-dom-07",
        "title": "ONTO: Create args/ontology/geospatial.ttl (GeoSIGINT A2/AD, landing zones)",
        "description": (
            "Create geospatial domain ontology from apps/geosigint/.\n\n"
            "Classes:\n"
            "  - GeoEntity (superclass)\n"
            "  - WeaponSystem, A2ADZone, LandingZone, StraitCorridor\n"
            "  - IslandChainBase, THAADBattery, Chokepoint\n"
            "  - MaritimeMilitiaVessel, ArtificialIsland, DisputedZone\n"
            "  - SemiconductorSupplyNode, RareEarthFlow\n"
            "Properties:\n"
            "  - hasLatitude, hasLongitude, hasRangeKm, hasCoverageRadius\n"
            "  - locatedInTheater, threatens, defends, supplies\n"
            "  - hasSlopeDeg, hasViabilityScore, hasWeatherWindow\n"
            "  - partOfSupplyChain, hasCriticalityScore\n\n"
            "Extract from a2ad_mapper.py, amphibious_analyzer.py, strait_crossing.py, island_chain_defense.py, militia_classifier.py, semiconductor_chain.py.\n"
            "Map to war ontology: WeaponSystem → subclassOf → Equipment."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-02",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP3: CANVAS INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "onto-cvs-01",
        "title": "ONTO: Apply ontology to NDC + SDC canvases",
        "description": (
            "Update Network Design Canvas (NDC) and Security Design Canvas (SDC) to use ontology-backed entity types.\n\n"
            "Changes:\n"
            "  - Replace hardcoded entity_type strings in canvas templates with ontology URIs.\n"
            "  - Template dropdowns populate from ontology_catalog.get_subclasses(icdev:NetworkObject).\n"
            "  - NDC design JSON stores ontology_id alongside type.\n"
            "  - SDC threat types map to security ontology classes.\n"
            "  - Canvas KG nodes use ontology_id for cross-canvas linking.\n"
            "  - Auto-suggest: when user draws an AWS VPC, suggest related AWS Subnet and ALB based on ontology properties.\n\n"
            "Test: NDC design exported as RDF/Turtle includes full ontology annotations."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-dom-01",
    },
    {
        "id": "onto-cvs-02",
        "title": "ONTO: Apply ontology to PDC + BDC + DDC canvases",
        "description": (
            "Update Policy Design Canvas (PDC), Boundary Design Canvas (BDC), and Data Design Canvas (DDC).\n\n"
            "Changes:\n"
            "  - PDC: policy rules map to compliance ontology classes (NISTControl, FedRAMPBaseline).\n"
            "  - BDC: boundary types (SCIF, DMZ, enclave) map to security ontology.\n"
            "  - DDC: data entities map to data ontology; PII/PHI columns auto-tagged via ontology property ranges.\n"
            "  - All three: canvas KG nodes include ontology_id for cross-canvas queries.\n"
            "  - Cross-canvas link: BDC boundary → contains → DDC data store (via ontology hasContainedDataStore).\n\n"
            "Verify: BDC SCIF design references DDC tables with PII via shared ontology."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-dom-02",
    },
    {
        "id": "onto-cvs-03",
        "title": "ONTO: Apply ontology to ODC + IDC + remaining canvases",
        "description": (
            "Update Orchestration Design Canvas (ODC), Integration Design Canvas (IDC), and all remaining canvases (AADC, MCE, NMCE, OHC, AISG).\n\n"
            "Changes:\n"
            "  - ODC: orchestration nodes (K8s service, Lambda, VM) map to infrastructure ontology classes.\n"
            "  - IDC: integration patterns (API gateway, ETL, event bus) map to data + network ontology.\n"
            "  - AADC: AI model types map to ML ontology; training datasets map to data ontology.\n"
            "  - MCE/NMCE: migration entities map to migration ontology (cloud-to-cloud mappings).\n"
            "  - OHC: observability components (SLO, SLI, alert) map to observability ontology.\n"
            "  - AISG: AI governance concepts map to AI ethics ontology (NIST AI RMF classes).\n\n"
            "All canvases: KG nodes store ontology_id; templates populate from catalog."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-cvs-02",
    },
    {
        "id": "onto-cvs-04",
        "title": "ONTO: Cross-canvas ontology federation",
        "description": (
            "Create unified cross-canvas ontology view at tools/ontology/federation.py.\n\n"
            "Requirements:\n"
            "  - Merge all domain ontologies into a single ICDEV Core Ontology graph.\n"
            "  - Resolve equivalent classes across domains (e.g., AWSVPC in network ontology ↔ CloudVPC in data ontology).\n"
            "  - Add cross-domain properties: network:AWSVPC → data:hosts → data:RelationalTable.\n"
            "  - Enable SPARQL-like queries over the unified graph:\n"
            "      'Show all designs referencing AWS VPCs that contain SECRET-classified tables.'\n"
            "  - Performance: pre-compute transitive closure of rdfs:subClassOf at build time.\n"
            "  - CLI: --build-federation --json.\n\n"
            "Integrate with tools/knowledge_graph/federation.py existing cross-project federation."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-cvs-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP4: ACADEMY + GAMEDAY
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "onto-agd-01",
        "title": "ONTO: Academy course content ontology tagging",
        "description": (
            "Tag all FORGE Academy content with ontology concepts.\n\n"
            "Changes:\n"
            "  - Each lesson/module mapped to ontology classes (icdev:Lesson, icdev:Lab, icdev:Assessment).\n"
            "  - Lesson topics tagged with domain ontology classes:\n"
            "      'AWS VPC Fundamentals' → network:AWSVPC\n"
            "      'STIG Compliance' → security:STIGRule\n"
            "      'War Council Briefing' → strategy:WarCouncilBrief\n"
            "  - Prerequisites expressed as ontology paths: student must understand network:Subnet before network:VPCPeering.\n"
            "  - Curriculum map → competency ontology: Tier 1 = icdev:FundamentalCompetency, Tier 4 = icdev:ExpertCompetency.\n"
            "  - Progress tracking: student's demonstrated competencies stored as KG edges to ontology classes.\n\n"
            "Update tools/forge_academy/content/ YAML frontmatter with ontology_id field."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-cvs-04",
    },
    {
        "id": "onto-agd-02",
        "title": "ONTO: GameDay scenario + role ontology",
        "description": (
            "Tag AI GameDay League scenarios and roles with ontology concepts.\n\n"
            "Changes:\n"
            "  - Game scenarios map to strategy ontology:\n"
            "      'Taiwan Strait Amphibious Assault' → strategy:AmphibiousOperation + geospatial:LandingZone\n"
            "      'Cyber Hunt' → security:CyberOperation + war:BehaviorProfile\n"
            "  - Agent roles map to war ontology classes:\n"
            "      'Blue Team Commander' → war:MilitaryUnit (role=commander)\n"
            "      'Red Team Hacker' → security:ThreatActor\n"
            "  - Game events typed with ontology: move_event, attack_event, intel_event → strategy:GameEvent subclasses.\n"
            "  - Scoreboard filters by ontology class: show only geospatial events, hide cyber events.\n\n"
            "Update tools/ai_game_engine/ scenario registry with ontology tagging."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-cvs-04",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP5: ECOSYSTEM INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "onto-eco-01",
        "title": "ONTO: Ontology-aware GraphRAG scoring profiles",
        "description": (
            "Update tools/knowledge_graph/graph_rag.py scoring profiles to leverage ontology hierarchies.\n\n"
            "Changes:\n"
            "  - New profile: 'ontology' — traverses rdfs:subClassOf to boost related entity types.\n"
            "  - Example: query about 'AWS ALB' also scores aws-nlb, azure-appgw, gcp-lb (siblings under LoadBalancer).\n"
            "  - Modify existing profiles:\n"
            "      compliance: boost nodes linked to NISTControl via ontology satisfiesFramework.\n"
            "      security: boost nodes linked to ThreatActor via ontology hasActor.\n"
            "      network_infrastructure: boost nodes in same provider family via ontology hasProvider.\n"
            "  - Add ontology_path_distance to scoring formula: closer in class hierarchy = higher score.\n"
            "  - Configurable via args/knowledge_graph_config.yaml.\n\n"
            "Test: 'Find all load balancers' should return AWS ALB, Azure AppGW, GCP LB with high scores."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-cvs-04",
    },
    {
        "id": "onto-eco-02",
        "title": "ONTO: Ontology injection into LLM router RAG augment",
        "description": (
            "Update tools/llm/router.py _rag_augment() to inject ontology context into prompts.\n\n"
            "Changes:\n"
            "  - Detect ontology-relevant queries (entity names, class names, relationship types).\n"
            "  - Fetch class definitions from ontology_catalog and prepend to system prompt.\n"
            "  - Example: query 'What is a SCIF?' → inject security:SCIF rdfs:comment + hasClassification property.\n"
            "  - Example: query 'Explain AWS VPC peering' → inject network:AWSVPC + network:connectsTo property definition.\n"
            "  - Citation mode: tag ontology definitions as [SOURCE-O] for traceability.\n"
            "  - Token budget: ontology context capped at 20% of max_tokens, truncated if exceeded.\n\n"
            "Air-gap safe: no external ontology resolution, all local."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-eco-01",
    },
    {
        "id": "onto-eco-03",
        "title": "ONTO: Security framework classification tagging via ontology",
        "description": (
            "Integrate ontology with the 44-task security framework.\n\n"
            "Changes:\n"
            "  - Add icdev:hasClassification property to ontology classes:\n"
            "      network:AWSVPC → hasClassification = 'CUI'\n"
            "      security:STIGRule → hasClassification = 'SECRET'\n"
            "      war:CyberOperation → hasClassification = 'TOP SECRET'\n"
            "  - ABAC engine (sec-fnd-02) checks resource.classification from ontology if not in DB row.\n"
            "  - Column security (sec-fnd-05): map DB columns to ontology properties with range restrictions:\n"
            "      projects.secret_notes → security:hasSecretNotes → range [SECRET]\n"
            "  - Field security (sec-fnd-06): API schema fields tagged with ontology properties carrying clearance annotations.\n"
            "  - Security middleware (sec-db-06): set X-Classification-Ceiling from ontology class property.\n\n"
            "Update security_config.yaml (sec-cfg-01) to include ontology classification map."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-dom-03",
    },
    {
        "id": "onto-eco-04",
        "title": "ONTO: Strategos + GeoSIGINT ontology alignment",
        "description": (
            "Align Strategos and GeoSIGINT ontologies with ICDEV core for cross-domain queries.\n\n"
            "Mappings:\n"
            "  - war:MilitaryUnit ↔ geospatial:GeoEntity (hasLocation)\n"
            "  - war:Equipment ↔ geospatial:WeaponSystem (subclassOf)\n"
            "  - strategy:CourseOfAction ↔ war:MilitaryUnit (uses)\n"
            "  - geospatial:A2ADZone ↔ security:SecurityBoundary (subclassOf)\n"
            "  - geospatial:LandingZone ↔ strategy:AmphibiousOperation (hasLandingZone)\n"
            "  - war:Vessel ↔ geospatial:MaritimeMilitiaVessel (subclassOf)\n\n"
            "Add owl:equivalentClass and owl:subClassOf assertions to federation graph.\n"
            "Update tools/strategos/war_kg.py to emit ontology-aware KG nodes.\n"
            "Update apps/geosigint/blueprint.py API responses to include ontology_id."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-dom-05",
    },
    {
        "id": "onto-eco-05",
        "title": "ONTO: Child app ontology inheritance",
        "description": (
            "Update child_app_generator.py to support ontology inheritance.\n\n"
            "Changes:\n"
            "  - Add args/ontology/ to DIRECTORY_TREE (line ~165).\n"
            "  - Child app scaffold includes:\n"
            "      args/ontology/app.ttl — extends parent ontology via owl:imports\n"
            "      args/ontology/app_config.yaml — lists parent ontologies to import\n"
            "  - Child app can add domain-specific classes (e.g., health:PatientRecord for HealthTech showcase).\n"
            "  - Ontology catalog validates child app ontology against parent on scaffold.\n"
            "  - Security framework: child app inherits parent classification tags from ontology.\n\n"
            "Run forge_validator.py --gate on test child app after changes."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-03",
    },
    {
        "id": "onto-eco-06",
        "title": "ONTO: External interoperability — STIX 2.1 + MITRE ATT&CK + GeoSPARQL",
        "description": (
            "Add external standard mappings to ICDEV ontology for interoperability.\n\n"
            "Mappings:\n"
            "  - security:CyberOperation → owl:equivalentClass → stix:IntrusionSet\n"
            "  - security:ThreatActor → owl:equivalentClass → stix:ThreatActor\n"
            "  - war:CyberOperation → hasTechnique → mitre:AttackPattern (T-code mapping)\n"
            "  - geospatial:GeoEntity → geo:Feature (GeoSPARQL alignment)\n"
            "  - compliance:NISTControl → owl:equivalentClass → osacr:Control (NIST OSCAL)\n"
            "  - icdev:Project → owl:equivalentClass → dcat:Dataset (DCAT alignment for data catalogs)\n\n"
            "Create args/ontology/external_mappings.ttl with all equivalences.\n"
            "Provide export functions: export_to_stix(), export_to_oscal(), export_to_geosparql().\n"
            "Do NOT require external HTTP resolution; store mappings locally."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-dom-04",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP6: TESTING + V&V
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "onto-tst-01",
        "title": "ONTO: Write unit tests for ontology validation",
        "description": (
            "Create tests/tools/ontology/ directory with unit tests.\n\n"
            "Test coverage:\n"
            "  - schema_extractor: constants.py → TTL roundtrip; all entity types present; no syntax errors.\n"
            "  - ontology_catalog: load_catalog(), is_valid_entity_type(), find_nearest_class(), get_subclasses().\n"
            "  - ingestion validation: strict mode rejects unmapped types; non-strict warns.\n"
            "  - security tagging: class hasClassification property parsed correctly.\n"
            "  - federation: merged graph contains all domain classes; equivalentClass resolved.\n\n"
            "Use pytest. Mock DB where possible. Validate Turtle syntax with simple regex parser."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fnd-05",
    },
    {
        "id": "onto-tst-02",
        "title": "ONTO: Integration tests — ontology-aware RAG + cross-canvas queries",
        "description": (
            "Create integration tests for end-to-end ontology pipeline.\n\n"
            "Test scenarios:\n"
            "  1. Ingest document with 'AWS VPC' → verify kg_nodes has ontology_id = network:AWSVPC.\n"
            "  2. Query 'Find all load balancers' → verify results include AWS ALB, Azure AppGW, GCP LB (sibling traversal).\n"
            "  3. Cross-canvas query: 'SCIF designs with PII tables' → verify BDC + DDC results via ontology federation.\n"
            "  4. Security tagging: 'Show SECRET-classified equipment' → verify war:Equipment nodes filtered by hasClassification.\n"
            "  5. Academy tagging: lesson 'AWS VPC Fundamentals' → verify linked to network:AWSVPC in KG.\n"
            "  6. GameDay scenario: 'Taiwan Strait' → verify geospatial:LandingZone + strategy:AmphibiousOperation linkage.\n"
            "  7. Child app: scaffold new app → verify inherits parent ontology classes.\n"
            "  8. External export: SECRET CyberOperation → verify STIX 2.1 IntrusionSet JSON generated.\n\n"
            "Use Flask test client for HTTP tests. Use temp DBs for isolation."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-tst-01",
    },
    {
        "id": "onto-tst-03",
        "title": "ONTO: Final validation — health_check + coherence + companion",
        "description": (
            "Post-implementation validation sequence:\n\n"
            "  1. python tools/testing/health_check.py --json\n"
            "       - Verify no import errors after ontology module changes.\n"
            "  2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "       - Verify manifest registration, CLAUDE.md sync, schema consistency, ruff lint gate.\n"
            "  3. python tools/dx/companion.py --sync --write --json\n"
            "       - Sync to icdev/ package mirror and all AI platforms.\n"
            "  4. python tools/ontology/schema_extractor.py --dry-run --json\n"
            "       - Verify all constants.py files parse into valid Turtle.\n"
            "  5. python tools/ontology/ontology_catalog.py --validate --json\n"
            "       - Verify all ontology files load without errors.\n"
            "  6. Run bandit security scan on new modules.\n\n"
            "Report pass/fail for each gate."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-tst-02",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP7: FORGE + ANVIL + REGISTRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "onto-fan-01",
        "title": "ONTO: Create FORGE goal goals/enable_ontology.md",
        "description": (
            "Create FORGE goal artifact at goals/enable_ontology.md.\n\n"
            "Content:\n"
            "  - Title: Enable Formal Ontology Layer for ICDEV Knowledge Graph\n"
            "  - Objective: Govern entity types across 12 canvases, Strategos, GeoSIGINT, security, Academy, GameDay with OWL/RDF ontology.\n"
            "  - Workflow: extract domain vocabularies → build core ontology → federate → integrate with RAG + security + child apps.\n"
            "  - Tools: schema_extractor.py, ontology_catalog.py, domain ontologies, federation.py.\n"
            "  - Expected outputs: all 7 epics complete, coherence checker passes, cross-canvas SPARQL queries work.\n"
            "  - Success criteria: 'AWS VPC' query returns ALB, Subnet, TGW; 'SCIF + PII' returns BDC + DDC designs.\n\n"
            "Add entry to goals/manifest.md."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "onto-fan-02",
        "title": "ONTO: Create ANVIL workflow .claude/commands/ontology.md",
        "description": (
            "Create ANVIL command at .claude/commands/ontology.md.\n\n"
            "Sections:\n"
            "  ## Extract schema: python tools/ontology/schema_extractor.py --dry-run --json\n"
            "  ## Validate catalog: python tools/ontology/ontology_catalog.py --validate --json\n"
            "  ## Build federation: python tools/ontology/federation.py --build --json\n"
            "  ## Query ontology: python tools/ontology/ontology_catalog.py --query 'AWS VPC' --json\n"
            "  ## Export external: python tools/ontology/external_mappings.py --to stix --json\n"
            "  ## Run RAG with ontology: python tools/llm/router.py --ontology-aware --function code_generation --prompt '...' --json\n\n"
            "Ensure all commands use allowlisted python tools/... prefix.\n"
            "Add to .claude/commands/ manifest."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-fan-01",
    },
    {
        "id": "onto-cfg-01",
        "title": "ONTO: Register in tools/manifest + update CLAUDE.md",
        "description": (
            "Registration checklist:\n\n"
            "  1. tools/manifest/ontology.md — create shard with all ontology tools.\n"
            "  2. tools/manifest.md — add ontology section link.\n"
            "  3. CLAUDE.md docs/reference/commands.md — add ontology CLI commands:\n"
            "       python tools/ontology/schema_extractor.py --dry-run --json\n"
            "       python tools/ontology/ontology_catalog.py --validate --json\n"
            "       python tools/ontology/federation.py --build --json\n"
            "  4. args/security_gates.yaml — add ontology validation gate.\n"
            "  5. tests/conftest.py — add ontology table schemas to MINIMAL_ICDEV_SCHEMA.\n"
            "  6. tools/mcp/tool_registry.py — register ontology tools in MCP gateway.\n"
            "  7. .dockerignore / .gitignore — ensure args/ontology/ is tracked (not ignored).\n\n"
            "Run companion sync after registration."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "onto-cfg-02",
        "title": "ONTO: Companion sync + coherence gate",
        "description": (
            "Final ecosystem synchronization:\n\n"
            "  1. python tools/dx/companion.py --sync --write --json\n"
            "       - Sync ontology modules to icdev/ package mirror.\n"
            "  2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "       - Verify all new files registered in manifest.\n"
            "       - Verify no orphaned imports (ontology_catalog imported from kg tools).\n"
            "       - Verify ruff lint passes on all new modules.\n"
            "  3. python tools/awareness/component_indexer.py --scan --json\n"
            "       - Refresh self-awareness graph to include ontology components.\n"
            "  4. Update .claude/commands/ list in start.md if new dashboard pages are added.\n"
            "  5. Verify no duplicate entity types between extracted ontologies and hardcoded constants.\n\n"
            "Report pass/fail."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "onto-cfg-01",
    },
]


def seed():
    report = create_tasks("onto", TASKS, strict=False, register_project=False)
    print(report.summary())
    return {"inserted": len(report.seeded), "skipped": len(report.skipped)}


if __name__ == "__main__":
    seed()
