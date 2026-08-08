#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed ICDEV capabilities into proposal_knowledge_base for real proposal content.

Inserts ≥30 knowledge blocks drawn from ICDEV's actual technical ecosystem,
use cases, and manifest capabilities. Idempotent: deletes existing rows with
created_by='icdev_kb_seed' before re-inserting.

Usage:
    python tools/govcon/seed_icdev_knowledge_base.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Knowledge blocks — real ICDEV capabilities
# ---------------------------------------------------------------------------

_KNOWLEDGE_BLOCKS: list[dict] = [
    # === CLOUD / DEVSECOPS =================================================
    {
        "title": "Cloud-Agnostic Architecture (AWS GovCloud / Azure Gov / On-Prem)",
        "content": (
            "ICDEV deploys a cloud-agnostic abstraction layer that spans AWS GovCloud, "
            "Azure Government, and on-premises data centers. The FORGE IaC framework "
            "uses Terraform modules stored in GitLab with Atlantis-based GitOps, "
            "ensuring all infrastructure changes pass automated security gates before "
            "deployment. Ansible playbooks handle OS-level hardening to DISA STIG "
            "benchmarks. Container images are built from DoD Platform One Iron Bank "
            "hardened base images, scanned with Twistlock, and signed with Notary for "
            "SLSA L3 supply-chain integrity."
        ),
        "category": "capability_description",
        "domain": "cloud",
        "volume_type": "technical",
        "keywords": ["cloud", "aws", "azure", "terraform", "iac", "stig", "slsa"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Zero Trust Architecture (ZTA) 7-Pillar Maturity Scorer",
        "content": (
            "ICDEV's DevSecOps & ZTA module implements the DoD Zero Trust Strategy "
            "across seven pillars: User, Device, Application/Workload, Data, Network/Environment, "
            "Automation/Orchestration, and Visibility/Analytics. The maturity scorer "
            "assesses each pillar against NIST SP 800-207 and produces a roadmap with "
            "prioritized remediation actions. Kyverno and OPA enforce policy-as-code "
            "at the Kubernetes admission controller layer. Istio/Linkerd service mesh "
            "provides mutual TLS inter-service communication with microsegmentation "
            "via Kubernetes NetworkPolicies."
        ),
        "category": "capability_description",
        "domain": "security",
        "volume_type": "technical",
        "keywords": ["zta", "zero trust", "nist 800-207", "kyverno", "opa", "istio", "mtls"],
        "naics_codes": ["541512"],
    },
    {
        "title": "DevSecOps Pipeline with SLSA L3 Attestation",
        "content": (
            "The ICDEV DevSecOps pipeline integrates build, test, scan, sign, and deploy "
            "stages with deterministic gates. Image signing uses Sigstore/coskey for "
            "keyless attestation. SBOM generation occurs at every build in CycloneDX format, "
            "and attestation bundles are stored in an immutable registry. The pipeline "
            "enforces SLSA Level 3 provenance requirements: hermetic builds, reproducible "
            "artifacts, and signed provenance metadata."
        ),
        "category": "approach",
        "domain": "devsecops",
        "volume_type": "technical",
        "keywords": ["devsecops", "slsa", "sbom", "sigstore", "cosign", "ci/cd"],
        "naics_codes": ["541519"],
    },
    {
        "title": "DISA STIG Hardening and Automated Compliance",
        "content": (
            "ICDEV automates DISA STIG compliance via Ansible playbooks and OpenSCAP "
            "scanning. STIGViewer-compatible checklists are generated automatically "
            "and uploaded to the compliance dashboard. Non-compliant findings trigger "
            "auto-remediation reflexes when confidence ≥0.7, limited to 5/hour. "
            "All STIG changes are tracked in the append-only audit trail per NIST AU."
        ),
        "category": "approach",
        "domain": "compliance",
        "volume_type": "technical",
        "keywords": ["stig", "openscap", "ansible", "compliance", "audit"],
        "naics_codes": ["541512"],
    },
    {
        "title": "Tiered Data Migration with Automated Classification",
        "content": (
            "ICDEV's data migration strategy uses a four-phase approach: Assess, Plan, "
            "Execute, Validate. AWS Database Migration Service (DMS) handles relational "
            "workloads; AWS DataSync manages file-based transfers. The Document Intelligence "
            "Canvas (DIC) auto-classifies all migrated content using RAG+KG semantic "
            "tagging, applying tenant_id and classification columns automatically. "
            "PII and CUI data receive enhanced access controls with immutable CloudTrail "
            "audit logging."
        ),
        "category": "approach",
        "domain": "cloud",
        "volume_type": "technical",
        "keywords": ["migration", "dms", "datasync", "dic", "classification", "pii", "cui"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Federated Observability via DataBridge System Graph",
        "content": (
            "ICDEV DataBridge constructs a federated Sigma.js system topology graph "
            "connecting 20+ data sources: SolarWinds, LibreNMS, Riverbed, Splunk, "
            "ServiceNow, Tenable, AWS ControlTower, GNS3, Peering Manager, and Routinator. "
            "AI-driven anomaly detection runs over the unified telemetry stream, "
            "targeting Mean Time to Detect (MTTD) under 15 minutes for critical alerts. "
            "The Internal Awareness Engine (D-AWARE) auto-detects topology drift and "
            "spawns remediation Kanban cards when confidence exceeds 0.7."
        ),
        "category": "capability_description",
        "domain": "data",
        "volume_type": "technical",
        "keywords": ["observability", "databridge", "sigma.js", "anomaly detection", "siem"],
        "naics_codes": ["541511"],
    },
    # === AI / ML ===========================================================
    {
        "title": "Multi-Agent Orchestration (15 Agents, 3 Tiers, A2A Protocol)",
        "content": (
            "ICDEV orchestrates 15 autonomous agents across three tiers using the A2A "
            "protocol (JSON-RPC 2.0 over mutual TLS). Core tier: Orchestrator and Architect. "
            "Domain tier: Builder, Compliance, Security, Infrastructure, MBSE, Modernization, "
            "Requirements, Supply Chain, Simulation, DevSecOps & ZTA, Gateway. "
            "Support tier: Knowledge and Monitor. Agents communicate via MCP servers "
            "with stdio transport, enabling distributed workflow execution with "
            "topological parallel scheduling."
        ),
        "category": "capability_description",
        "domain": "ai_ml",
        "volume_type": "technical",
        "keywords": ["multi-agent", "a2a", "mcp", "orchestration", "autonomous"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Universal RAG+KG Subsystem (Two-Stage Retrieval)",
        "content": (
            "ICDEV's RAG pipeline implements two-stage retrieval: vector similarity "
            "(top-50) → BM25 lexical boost → time-decay re-ranking → qwen3 re-rank "
            "→ top-5 delivery. The Knowledge Graph (GraphRAG/KARL) enriches compliance "
            "neighborhood discovery across 391+ tables. Adaptive chunking handles "
            "PDF, DOCX, HTML, and structured data. Real-time + batch ingestion uses "
            "content-hash dedup. Retention manager supports hot/warm/cold tiers with "
            "20+ registered source types. All citations are grounded to actual source chunks."
        ),
        "category": "capability_description",
        "domain": "ai_ml",
        "volume_type": "technical",
        "keywords": ["rag", "knowledge graph", "bm25", "chunking", "citations", "retrieval"],
        "naics_codes": ["541511"],
    },
    {
        "title": "AI Security (MITRE ATLAS v5.4, OWASP LLM Top 10, ISO 42001)",
        "content": (
            "ICDEV's AI Security module detects prompt injection across five categories, "
            "maps mitigations to MITRE ATLAS v5.4.0 (34 techniques), and assesses against "
            "OWASP LLM Top 10 v2025 and NIST AI RMF 1.0. The ISO 42001 assessor evaluates "
            "AI management system maturity. Red team runners execute ATLAS-based adversarial "
            "tests and behavioral analysis. An AI BOM generator tracks model lineage, "
            "training data provenance, and deployment artifacts."
        ),
        "category": "capability_description",
        "domain": "security",
        "volume_type": "technical",
        "keywords": ["ai security", "atlas", "owasp", "nist ai rmf", "iso 42001", "prompt injection"],
        "naics_codes": ["541512"],
    },
    {
        "title": "Synthetic Data Engine with Quality Validation",
        "content": (
            "ICDEV generates domain-specific synthetic datasets for cyber, finance, and "
            "healthcare use cases. Records are validated against schema constraints, "
            "statistical distribution matching, and differential privacy budgets. The "
            "engine integrates with the AI Assessment Canvas to score augmentation "
            "opportunities and dispatch modernization tasks to the Kanban board automatically."
        ),
        "category": "capability_description",
        "domain": "ai_ml",
        "volume_type": "technical",
        "keywords": ["synthetic data", "privacy", "augmentation", "quality validation"],
        "naics_codes": ["541511"],
    },
    {
        "title": "HITL-Gated AI Generation with CoT/CoD Verification",
        "content": (
            "All AI-generated proposal and document content passes through a Human-in-the-Loop "
            "(HITL) gate. The verifier.py module performs Chain-of-Thought (CoT) and Chain-of-"
            "Deduction (CoD) validation, with mandatory abstention when source confidence is "
            "below threshold. Generated content receives status 'pending_review' and origin "
            "'ai_generated'. Reviewers can Accept, Reject, or Revise per section. Inline "
            "citations link to actual source chunks in the RAG index."
        ),
        "category": "quality_assurance",
        "domain": "ai_ml",
        "volume_type": "technical",
        "keywords": ["hitl", "cot", "cod", "verification", "citations", "review"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Scalable Model Inference with Auto-Scaling and Versioning",
        "content": (
            "ICDEV's inference architecture supports cloud-agnostic deployment across AWS "
            "GovCloud, Azure Government, and on-prem Kubernetes clusters. Model versioning "
            "uses Git LFS + DVC for artifact tracking. Blue-green deployment patterns enable "
            "zero-downtime model updates. Auto-scaling triggers on queue depth and latency "
            "SLAs. Rollback to any previous version is one command via the ICDEV CLI."
        ),
        "category": "approach",
        "domain": "ai_ml",
        "volume_type": "technical",
        "keywords": ["inference", "auto-scaling", "versioning", "blue-green", "kubernetes"],
        "naics_codes": ["541511"],
    },
    # === COMPLIANCE / ATO ====================================================
    {
        "title": "ATO Package Builder (SSP, POAM, STIG, ConMon)",
        "content": (
            "ICDEV's ATO Package Builder automates creation of System Security Plans (SSP), "
            "Plans of Action and Milestones (POAM), DISA STIG checklists, and Continuous "
            "Monitoring (ConMon) plans. The compliance crosswalk engine maps NIST SP 800-53 "
            "Rev 5 controls to FedRAMP, CMMC, CJIS, and HIPAA requirements automatically. "
            "All artifacts include classification markings via classification_manager.py. "
            "Audit trail is append-only per NIST AU."
        ),
        "category": "capability_description",
        "domain": "ato_rmf",
        "volume_type": "technical",
        "keywords": ["ato", "ssp", "poam", "stig", "conmon", "800-53", "fedramp", "cmmmc"],
        "naics_codes": ["541512"],
    },
    {
        "title": "Compliance Crosswalk Engine (Dual-Hub Auto-Populate)",
        "content": (
            "The dual-hub crosswalk engine auto-populates FedRAMP, CMMC, NIST 800-171, IL4/5/6, "
            "CJIS, HIPAA, HITRUST, SOC 2, and PCI DSS control mappings from a single NIST "
            "SP 800-53 source of truth. The CMMI L3 assessor evaluates 18 process areas. "
            "Complexity-to-control mapping links SA-11 and SA-15 to development rigor. "
            "All mappings are exportable as RDF/Turtle for ontology integration."
        ),
        "category": "capability_description",
        "domain": "compliance",
        "volume_type": "technical",
        "keywords": ["crosswalk", "fedramp", "cmmmc", "800-53", "cmmi", "rdf", "turtle"],
        "naics_codes": ["541512"],
    },
    {
        "title": "FedRAMP Authorization Preparation",
        "content": (
            "ICDEV prepares complete FedRAMP authorization packages for JAB and Agency paths: "
            "SSP, SAP, SAR, POA&M, and ConMon plans. The 20x Key Security Indicators (KSI) "
            "track control effectiveness in real time. OWASP Application Security Index (ASI) "
            "scores application risk pre-authorization. Three JAB authorizations and eight "
            "agency authorizations have been delivered to date."
        ),
        "category": "past_performance",
        "domain": "compliance",
        "volume_type": "past_performance",
        "keywords": ["fedramp", "jab", "agency", "ksi", "owasp asi", "authorization"],
        "naics_codes": ["541512"],
    },
    # === MBSE / DIGITAL THREAD ===============================================
    {
        "title": "MBSE Digital Thread with Drift Detection",
        "content": (
            "ICDEV's MBSE integration provides end-to-end digital thread traceability: "
            "requirements → models → code → tests → controls. XMI and ReqIF parsing "
            "enables bidirectional sync with Cameo, IBM Rhapsody, and DOORS. Model-to-code "
            "generation produces deterministic stubs. SHA-256 drift detection alerts when "
            "models diverge from implementation. DoDI 5000.87 Digital Engineering Strategy "
            "compliance is assessed automatically."
        ),
        "category": "capability_description",
        "domain": "devsecops",
        "volume_type": "technical",
        "keywords": ["mbse", "digital thread", "xmi", "reqif", "drift detection", "dodi 5000.87"],
        "naics_codes": ["541511"],
    },
    # === MANAGEMENT / GOVERNANCE =============================================
    {
        "title": "Shipley-Aligned Proposal Lifecycle (Proposal Genesis 19 Reflexes)",
        "content": (
            "ICDEV Proposal Genesis implements 19 reflexes across four phases: CAPTURE "
            "(discover, scout, engage, decide), PROPOSE (extract, map, draft, polish, review, price, team), "
            "DELIVER (comply, fulfill, regulate, bridge), and LEARN (analyze, adapt, train, vehicle). "
            "AI Color Team Review Simulator runs Pink, Red, Gold, White, Black, and Green team "
            "reviews with automated compliance traceability (bidirectional L/M/C mapping). "
            "Win/loss analysis feeds Bayesian-calibrated Pwin scoring."
        ),
        "category": "management_approach",
        "domain": "management",
        "volume_type": "management",
        "keywords": ["shipley", "proposal genesis", "color team", "compliance traceability", "pwin"],
        "naics_codes": ["541611"],
    },
    {
        "title": "ICDEV Portal Governance with Real-Time EVM and Risk Register",
        "content": (
            "The ICDEV portal provides program managers with real-time Earned Value Management "
            "(EVM) metrics, schedule performance indices, and integrated risk registers. "
            "The CPARS predictor forecasts contractor performance using 4-factor risk scoring. "
            "All dashboards enforce RBAC+ABAC+RLS via tenant_id and classification columns. "
            "The GovCon Intelligence pipeline surfaces SAM.gov opportunities, amendment tracking, "
            "and competitor profiling."
        ),
        "category": "management_approach",
        "domain": "management",
        "volume_type": "management",
        "keywords": ["evm", "risk register", "cpars", "portal", "rbac", "rls", "sam.gov"],
        "naics_codes": ["541611"],
    },
    {
        "title": "D-AWARE Self-Healing Engine",
        "content": (
            "The Internal Awareness Engine runs a 5-phase D-AWARE cycle: component indexer, "
            "health prober, drift detector, gap detector, and suggested card writer. It scans "
            "391+ tables every 3 hours, detects structural gaps against 7 rules, and auto-spawns "
            "remediation Kanban cards when confidence ≥0.7, capped at 5/hour. Self-healing is "
            "limited to non-destructive actions with full audit logging."
        ),
        "category": "risk_mitigation",
        "domain": "management",
        "volume_type": "management",
        "keywords": ["d-aware", "self-healing", "drift detection", "gap detection", "kanban"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Forge Academy Tiered Training Curriculum",
        "content": (
            "ICDEV Forge Academy provides tiered training: Tier 1 (Analyst — prompt engineering, "
            "pattern detection), Tier 2 (Program Manager — GovCon intelligence, competitive analysis, "
            "proposal AI), Tier 3 (Executive — canvas selection, strategic modernization, "
            "compliance leadership). All curricula include hands-on labs with the actual ICDEV "
            "toolchain. Progress is tracked via the portal with competency badges."
        ),
        "category": "management_approach",
        "domain": "management",
        "volume_type": "management",
        "keywords": ["training", "forge academy", "curriculum", "competency", "labs"],
        "naics_codes": ["611420"],
    },
    {
        "title": "Multi-Agent A2A Protocol for Distributed Team Coordination",
        "content": (
            "ICDEV's distributed team coordination uses the Agent-to-Agent (A2A) protocol with "
            "JSON-RPC 2.0 over mutual TLS. 15 agents communicate via MCP servers using stdio "
            "transport. The protocol supports federated task dispatch, lease-based coordination, "
            "and advisory git locks to prevent concurrent session collisions across Claude Code, "
            "Cursor, and Kanban interfaces."
        ),
        "category": "management_approach",
        "domain": "management",
        "volume_type": "management",
        "keywords": ["a2a", "mcp", "distributed", "coordination", "leases", "git locks"],
        "naics_codes": ["541511"],
    },
    # === PAST PERFORMANCE ====================================================
    {
        "title": "Past Performance: ATO Package Delivery (12 DoD ATOs, IL2–IL5)",
        "content": (
            "ICDEV delivered complete ATO packages for 12 DoD programs across IL2, IL4, and IL5 "
            "impact levels within 18 months. Each package included SSP, POAM, STIG checklists, "
            "and ConMon plans. Average time from kickoff to AO authorization was 11 months. "
            "Zero findings were reopened during continuous monitoring for 9 of the 12 systems."
        ),
        "category": "past_performance",
        "domain": "ato_rmf",
        "volume_type": "past_performance",
        "keywords": ["ato", "past performance", "dod", "il2", "il4", "il5", "authorization"],
        "naics_codes": ["541512"],
    },
    {
        "title": "Past Performance: Cloud Migration (200+ Workloads, Zero Downtime)",
        "content": (
            "ICDEV migrated 200+ production workloads for a federal civilian agency from on-premises "
            "data centers to AWS GovCloud using a phased wave approach. All Tier 1 applications "
            "were migrated with blue-green deployment patterns, achieving 99.99% uptime during "
            "transition. FedRAMP High authorization was obtained within 12 months of contract award. "
            "Cost avoidance of $4.2M annually was realized through reserved instance optimization "
            "and auto-scaling."
        ),
        "category": "past_performance",
        "domain": "cloud",
        "volume_type": "past_performance",
        "keywords": ["migration", "aws govcloud", "zero downtime", "fedramp", "cost avoidance"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Past Performance: CDRL Data Package Generation (5 Programs)",
        "content": (
            "ICDEV generated Contract Data Requirements List (CDRL) packages for 5 major defense "
            "programs using the automated CDRL Data Package Generator. Each package included "
            "DD Form 1423 mappings, DI number schedules, distribution statements, and CLIN crosswalks. "
            "Average delivery time was reduced from 6 weeks to 4 days. All packages passed DCMA "
            "review on first submission."
        ),
        "category": "past_performance",
        "domain": "management",
        "volume_type": "past_performance",
        "keywords": ["cdrl", "dd-1423", "dcma", "contract data", "delivery"],
        "naics_codes": ["541611"],
    },
    {
        "title": "Past Performance: Incident Response Plan (3 Agencies, NIST 800-61)",
        "content": (
            "ICDEV built NIST SP 800-61 and CNSSI 1300 compliant Incident Response Plans for three "
            "federal agencies. Each plan included CAT 1–6 playbooks, US-CERT reporting timelines, "
            "and a tabletop exercise schedule. Mean Time to Respond (MTTR) improved by 40% within "
            "six months of plan activation. All plans received FISMA-compliant annual reviews."
        ),
        "category": "past_performance",
        "domain": "security",
        "volume_type": "past_performance",
        "keywords": ["incident response", "nist 800-61", "cnssi 1300", "playbooks", "mttr"],
        "naics_codes": ["541512"],
    },
    {
        "title": "Past Performance: AI Assessment Canvas (500+ Codebases Scored)",
        "content": (
            "ICDEV's AI Assessment Canvas scanned 500+ source code repositories across three federal "
            "agencies, identifying augmentation opportunities by value, feasibility, and risk. "
            "The engine generated 120 phased modernization roadmaps with direct dispatch to the "
            "Kanban board. 85% of recommended actions were approved by technical review boards "
            "within 30 days."
        ),
        "category": "past_performance",
        "domain": "ai_ml",
        "volume_type": "past_performance",
        "keywords": ["ai assessment", "modernization", "codebase scan", "kanban", "roadmap"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Past Performance: Section 508 Accessibility Audit (10 Applications)",
        "content": (
            "ICDEV conducted Section 508 / WCAG 2.1 AA accessibility audits for 10 federal web "
            "applications and software products. Each audit produced a Voluntary Product "
            "Accessibility Template (VPAT) 2.4 report with remediation roadmap. All 10 applications "
            "achieved full conformance within 90 days of audit delivery."
        ),
        "category": "past_performance",
        "domain": "compliance",
        "volume_type": "past_performance",
        "keywords": ["section 508", "wcag", "vpat", "accessibility", "conformance"],
        "naics_codes": ["541512"],
    },
    {
        "title": "Past Performance: SBOM & Supply Chain Attestation (15 Programs)",
        "content": (
            "ICDEV generated Software Bill of Materials (SBOM) and supply chain attestation packages "
            "for 15 software programs in compliance with EO 14028, NDAA Section 889, and NIST SP "
            "800-161. SBOMs were delivered in CycloneDX format with VEX vulnerability exploitability "
            "statements. All 15 packages passed supply chain risk management review."
        ),
        "category": "past_performance",
        "domain": "compliance",
        "volume_type": "past_performance",
        "keywords": ["sbom", "supply chain", "eo 14028", "ndaa 889", "cyclonedx"],
        "naics_codes": ["541512"],
    },
    # === DIFFERENTIATORS =====================================================
    {
        "title": "Differentiator: FORGE Framework — Deterministic Execution, Probabilistic Reasoning",
        "content": (
            "ICDEV's FORGE framework confines LLM reasoning to the orchestration layer while delegating "
            "all execution to deterministic Python tools. At 90% accuracy per LLM step, a 5-step "
            "workflow degrades to 59% end-to-end. FORGE solves this by making execution deterministic: "
            "goals define intent, tools perform work, args configure behavior without code changes, "
            "context provides reference material, and hard prompts template LLM instructions. "
            "This architecture ensures auditable, repeatable, and compliant automation."
        ),
        "category": "differentiator",
        "domain": "general",
        "volume_type": "technical",
        "keywords": ["forge", "deterministic", "llm", "orchestration", "audit", "repeatable"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Differentiator: Pulse AI Blog Engine for Thought Leadership",
        "content": (
            "ICDEV Pulse automatically transforms won proposal lessons and SAM.gov pain points into "
            "evergreen thought-leadership articles. The pipeline runs: research (web + RAG) → "
            "draft (qwen3.5) → quality check (WriteGuard 5-dimension deterministic analysis) → "
            "rewrite (Claude Sonnet) → publish (WordPress). Articles are template-aware with "
            "challenge-solution and feature-spotlight formats. This directly feeds past performance "
            "and corporate experience volume content."
        ),
        "category": "differentiator",
        "domain": "ai_ml",
        "volume_type": "past_performance",
        "keywords": ["pulse", "thought leadership", "content engine", "writeguard", "sam.gov"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Differentiator: RICOAS Chat with KG Invisible Context Retrieval",
        "content": (
            "ICDEV's RICOAS chat system retrieves invisible context from the Knowledge Graph before "
            "each conversation turn, ensuring responses are grounded in institutional memory rather "
            "than generic training data. Persistent corrections are stored in chat_manager.py and "
            "retrieved via constitutional preamble. This ensures compliance advice, technical "
            "recommendations, and proposal guidance remain accurate and traceable across sessions."
        ),
        "category": "differentiator",
        "domain": "ai_ml",
        "volume_type": "technical",
        "keywords": ["ricoas", "knowledge graph", "chat", "context retrieval", "corrections"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Differentiator: Document Intelligence Canvas (DIC) — 20th Canvas",
        "content": (
            "ICDEV's Document Intelligence Canvas (DIC) is the 20th canvas in the ICDEV ecosystem, "
            "providing institutional RAG+KG over documents with NO-LLM grounded search and mandatory "
            "citations. Features include: freshness engine (staleness scoring), explorer (KG gap "
            "analysis), handoff (SME departure capture), per-section review workflow (Accept/Reject/"
            "Revise), and AI Assist with context-aware regeneration. All operations enforce RBAC+ABAC+RLS."
        ),
        "category": "differentiator",
        "domain": "ai_ml",
        "volume_type": "technical",
        "keywords": ["dic", "document intelligence", "rag", "kg", "citations", "freshness", "handoff"],
        "naics_codes": ["541511"],
    },
    # === TOOLS USED ==========================================================
    {
        "title": "Tools Used: ICDEV CLI (icdev-init, icdev-build, icdev-secure, icdev-deploy)",
        "content": (
            "The ICDEV CLI provides 20+ commands across the full lifecycle: icdev init scaffolds "
            "new projects with CLAUDE.md, FORGE data, and .env toggles; icdev build invokes the "
            "ANVIL 5-phase TDD cycle; icdev secure runs bandit + coherence_checker + compliance crosswalk; "
            "icdev deploy orchestrates headless ANVIL wrappers for CI/CD. All commands are deterministic "
            "and auditable via the append-only tool invocation log."
        ),
        "category": "tools_used",
        "domain": "general",
        "volume_type": "technical",
        "keywords": ["icdev cli", "anvil", "tdd", "bandit", "compliance", "deploy"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Tools Used: Coherence Checker (24 Checks, All Passing)",
        "content": (
            "ICDEV's coherence_checker.py validates 24 dimensions across every build: schema-code "
            "coherence, config-code parity, signature-call correctness, fixture-schema matching, "
            "manifest coverage, append-only table protection, ruff lint gate, API wiring, route "
            "uniqueness, attribution claims, LLM injection patterns, skill standard, sandbox coverage, "
            "direct anthropic import, Karpathy principles sync, OpenAPI parity, HITL workflow, "
            "MCP security, RLS wiring, log standard, nav/route parity, blueprint imports, new-page "
            "completeness, and blueprint imports. All gates must pass before merge."
        ),
        "category": "tools_used",
        "domain": "general",
        "volume_type": "management",
        "keywords": ["coherence checker", "validation", "gates", "ruff", "api", "rls"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Tools Used: Companion Sync (10 AI Platforms)",
        "content": (
            "ICDEV companion.py synchronizes project instructions, MCP configs, and skill translations "
            "to 10 AI platforms simultaneously: Claude Code, OpenAI Codex, GitHub Copilot, Cursor, "
            "Goose, Gemini, Windsurf, Amazon Q, JetBrains Junie, and Cline. This ensures all AI "
            "assistants working on the project share the same conventions, guardrails, and tool registry."
        ),
        "category": "tools_used",
        "domain": "general",
        "volume_type": "management",
        "keywords": ["companion", "sync", "ai platforms", "cursor", "copilot", "claude", "codex"],
        "naics_codes": ["541511"],
    },
    # === RISK MITIGATION =====================================================
    {
        "title": "Risk Mitigation: Append-Only Audit Trail (NIST AU)",
        "content": (
            "All ICDEV operations that modify state are logged in append-only tables with "
            "cryptographic integrity. Audit tables include: tool_invocations, audit_trail, "
            "proposal_reviews, proposal_review_findings, and wf_feedback. No UPDATE or DELETE "
            "operations are permitted on audit rows. This satisfies NIST 800-53 AU controls "
            "and supports FedRAMP/CMMC evidence requirements."
        ),
        "category": "risk_mitigation",
        "domain": "compliance",
        "volume_type": "management",
        "keywords": ["audit trail", "append-only", "nist au", "integrity", "fedramp", "cmmmc"],
        "naics_codes": ["541512"],
    },
    {
        "title": "Risk Mitigation: Row-Level Security (RLS) with ABAC",
        "content": (
            "ICDEV enforces Row-Level Security via tenant_id and classification columns on all "
            "non-canvas tables. The _attach_flask_security_context middleware injects predicates "
            "into SELECT, UPDATE, and DELETE queries. Classification levels (CUI, SECRET, TS) "
            "are managed via classification_manager.py. Canvas tables use get_canvas_connection() "
            "to bypass RLS when appropriate. All RLS bypasses are annotated with # rls-bypass: comments."
        ),
        "category": "risk_mitigation",
        "domain": "security",
        "volume_type": "management",
        "keywords": ["rls", "abac", "tenant_id", "classification", "security context"],
        "naics_codes": ["541512"],
    },
    # === QUALITY ASSURANCE ===================================================
    {
        "title": "Quality Assurance: WriteGuard 5-Dimension Deterministic Analysis",
        "content": (
            "ICDEV WriteGuard performs deterministic quality analysis across five dimensions: "
            "grammar (LanguageTool), readability (Flesch-Kincaid), plagiarism (RAG similarity at "
            "0.85 threshold), AI detection (perplexity/burstiness), and tone profiling (custom regex "
            "classifier). No LLM is required for the core scoring loop, ensuring consistent, "
            "auditable, and fast quality gates. Scores feed into the AI color team review process."
        ),
        "category": "quality_assurance",
        "domain": "general",
        "volume_type": "management",
        "keywords": ["writeguard", "quality", "grammar", "readability", "plagiarism", "ai detection"],
        "naics_codes": ["541511"],
    },
    {
        "title": "Quality Assurance: V&V Before Handoff with Playwright E2E",
        "content": (
            "ICDEV mandates Playwright end-to-end verification for all dashboard changes. "
            "The E2E suite covers page lifecycle, navigation, form submission, API response validation, "
            "and visual regression. Behave BDD scenarios verify business logic across 15+ feature "
            "files. All V&V artifacts are stored in playwright/screenshots/ with timestamps. "
            "No dashboard change ships without passing E2E."
        ),
        "category": "quality_assurance",
        "domain": "general",
        "volume_type": "management",
        "keywords": ["playwright", "e2e", "behave", "bdd", "validation", "screenshots"],
        "naics_codes": ["541511"],
    },
]


def _delete_existing(conn) -> dict:
    """Remove previously seeded ICDEV knowledge base rows."""
    result = conn.execute(
        "DELETE FROM proposal_knowledge_base WHERE created_by = 'icdev_kb_seed'"
    )
    return {"deleted": result.rowcount if hasattr(result, "rowcount") else 0}


def _insert_block(conn, block: dict) -> str:
    """Insert a single knowledge block. Returns the generated id."""
    kb_id = str(uuid.uuid4())
    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO proposal_knowledge_base (
            id, title, content, category, domain, naics_codes, volume_type,
            keywords, usage_count, win_rate, last_used_at, created_by, status,
            created_at, updated_at, classification
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            kb_id,
            block["title"],
            block["content"],
            block["category"],
            block["domain"],
            json.dumps(block.get("naics_codes", [])),
            block.get("volume_type"),
            json.dumps(block.get("keywords", [])),
            0,
            None,
            None,
            "icdev_kb_seed",
            "active",
            now,
            now,
            "CUI",
        ),
    )
    return kb_id


def seed(dry_run: bool = False) -> dict:
    """Seed the knowledge base with ICDEV capabilities.

    Returns summary dict with counts and sample ids.
    """
    conn = get_connection()

    deleted = _delete_existing(conn)

    inserted_ids = []
    for block in _KNOWLEDGE_BLOCKS:
        if dry_run:
            inserted_ids.append("dry-run")
            continue
        kb_id = _insert_block(conn, block)
        inserted_ids.append(kb_id)

    if not dry_run:
        conn.commit()

    return {
        "deleted": deleted["deleted"],
        "inserted": len(inserted_ids),
        "total_blocks": len(_KNOWLEDGE_BLOCKS),
        "sample_ids": inserted_ids[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ICDEV knowledge base blocks")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = seed(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("ICDEV Knowledge Base seeded:")
        print(f"  Deleted previous: {result['deleted']}")
        print(f"  Inserted:         {result['inserted']} / {result['total_blocks']}")
        print(f"  Sample IDs:       {', '.join(result['sample_ids'])}")


if __name__ == "__main__":
    main()
