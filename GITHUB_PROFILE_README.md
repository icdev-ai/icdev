<div align="center">

<!-- Hero Banner -->
<img src="https://img.shields.io/badge/ICDEV-The_System_That_Builds_Systems-0d1117?style=for-the-badge&labelColor=1a1a2e&color=16213e" alt="ICDEV" />

<br/>

# Intelligent Certified Development Platform

### Autonomous AI Engineering for Trusted Government & Defense Software

<br/>

[![Python](https://img.shields.io/badge/Python-3.9+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Compliance](https://img.shields.io/badge/Compliance_Frameworks-34-00875a?style=flat-square)](.)
[![Agents](https://img.shields.io/badge/AI_Agents-15-7c3aed?style=flat-square)](.)
[![Tools](https://img.shields.io/badge/Deterministic_Tools-500+-e05d44?style=flat-square)](.)
[![Languages](https://img.shields.io/badge/Languages-6_First--Class-0078d4?style=flat-square)](.)
[![Cloud](https://img.shields.io/badge/Cloud_Providers-6_CSPs-ff6f00?style=flat-square)](.)
[![Tests](https://img.shields.io/badge/Tests-3,800+-25a162?style=flat-square)](.)
[![License](https://img.shields.io/badge/License-AGPL--3.0_+_Commercial-blue?style=flat-square)](.)

<br/>

> **ICDEV doesn't write code. It engineers complete, ATO-ready, production-grade applications —**
> **then those applications engineer their own features.**

> **DISCLAIMER:** This repository does NOT contain classified or Controlled Unclassified Information (CUI). Terms like "CUI", "SECRET", "IL4", "IL5", "IL6" appear throughout as **configuration values and template strings** — not as indicators that this repository itself is classified. Classification terminology references publicly available U.S. government standards ([EO 13526](https://www.archives.gov/isoo/policy-documents/cnsi-eo.html), [32 CFR Part 2002](https://www.ecfr.gov/current/title-32/subtitle-B/chapter-XX/part-2002), [NIST SP 800-53](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)). File headers containing `[TEMPLATE: CUI // SP-CTI]` are **template markers** demonstrating the format ICDEV applies to generated artifacts.

<br/>

[Architecture](#architecture) &bull; [Why ICDEV](#why-icdev-exists) &bull; [Capabilities](#capabilities) &bull; [Compliance](#compliance-coverage) &bull; [Agents](#multi-agent-architecture) &bull; [Cloud](#multi-cloud-deployment) &bull; [Metrics](#by-the-numbers)

</div>

---

## The Problem

Building software for the U.S. Government is broken.

| Pain Point | Reality |
|:---|:---|
| **ATO takes 12-18 months** | Teams spend more time on compliance paperwork than building features |
| **$2.4M average cost per ATO** | Most of it on manual evidence collection, SSP writing, and STIG remediation |
| **34+ compliance frameworks** | NIST, FedRAMP, CMMC, HIPAA, CJIS, PCI DSS — each with hundreds of controls that overlap but aren't mapped |
| **Developer shortage** | 500K+ unfilled cybersecurity roles; cleared developers are 3x harder to hire |
| **Air-gap constraints** | IL5/IL6 environments can't reach PyPI, npm, or cloud AI services |
| **Legacy modernization backlog** | $100B+ in aging government IT systems that can't be replaced fast enough |

**The industry's answer?** More consultants. More spreadsheets. More manual checklists.

**Our answer?** A system that builds systems.

---

## What ICDEV Actually Does

ICDEV is a **meta-builder** — an autonomous AI engineering platform that generates complete, production-ready, compliance-certified applications. Every application it builds inherits:

- Full test-driven development (RED → GREEN → REFACTOR)
- Behavior-driven specifications (Gherkin/Cucumber)
- NIST 800-53 control mappings that cascade to 34 frameworks simultaneously
- STIG-hardened containers with read-only rootfs and dropped capabilities
- CUI markings applied at generation time (not post-processing)
- Append-only audit trails satisfying NIST AU controls
- Multi-cloud deployment manifests (Terraform + Ansible + K8s + Helm)

**And the applications it builds can build their own features** — a recursive, self-improving engineering system with grandchild prevention guardrails.

---

## Architecture

### GOTCHA Framework — 6-Layer Separation of Concerns

The core architectural principle: **LLMs are probabilistic. Business logic must be deterministic.**

At 90% accuracy per step, a 5-step LLM chain yields ~59% end-to-end accuracy. GOTCHA solves this by isolating the AI to orchestration while all execution is deterministic.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   ┌─────────┐    The AI reads goals, decides tool order,               │
│   │  GOALS  │    applies args, references context, handles errors.     │
│   │         │    It never executes work directly.                      │
│   └────┬────┘                                                          │
│        │                                                                │
│   ┌────▼──────────────┐                                                │
│   │   ORCHESTRATION   │◄── Claude / Bedrock (the AI brain)             │
│   │   (AI Layer)      │    Routes tasks, manages workflows,            │
│   └────┬──────────────┘    enforces domain authority vetoes             │
│        │                                                                │
│   ┌────▼────┐  ┌──────┐  ┌─────────┐  ┌──────────────┐               │
│   │  TOOLS  │  │ ARGS │  │ CONTEXT │  │ HARD PROMPTS │               │
│   │ 500+    │  │ 54   │  │ 90+     │  │ Reusable LLM │               │
│   │ Python  │  │ YAML │  │ JSON    │  │ instruction  │               │
│   │ scripts │  │ configs│ │ refs    │  │ templates    │               │
│   └─────────┘  └──────┘  └─────────┘  └──────────────┘               │
│                                                                         │
│   Every tool: one job. Deterministic. Testable. Air-gap safe.          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### ATLAS Workflow — How Applications Get Built

Every application follows the ATLAS methodology (with optional Model phase for MBSE):

```
  ┌──────────┐   ┌───────────┐   ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────┐
  │  MODEL   │──▶│ ARCHITECT │──▶│  TRACE  │──▶│   LINK   │──▶│ ASSEMBLE │──▶│ STRESS-TEST │
  │ (MBSE)   │   │  (Design) │   │ (Map)   │   │ (Connect)│   │ (Build)  │   │  (Verify)   │
  └──────────┘   └───────────┘   └─────────┘   └──────────┘   └──────────┘   └─────────────┘
       │               │              │              │              │               │
   SysML/DOORS    Architecture    NIST 800-53    Digital       TDD Code      Security Gates
   import &       decisions &     control        thread &      generation    + BDD + E2E
   digital        schema          mapping        SBOM          (6 languages) + Compliance
   thread         design          (34 frameworks) linkage                     verification
```

### System Topology — 15 Coordinated AI Agents

```
                            ┌──────────────────┐
                            │   ORCHESTRATOR   │ Port 8443
                            │  Task Routing &  │ DAG Execution
                            │  Workflow Mgmt   │ Domain Authority
                            └────────┬─────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
     ┌────────▼────────┐   ┌────────▼────────┐   ┌────────▼────────┐
     │    ARCHITECT    │   │     BUILDER     │   │    KNOWLEDGE    │
     │  System Design  │   │   TDD Code Gen  │   │  Self-Healing   │
     │  Port 8444      │   │   Port 8445     │   │  Port 8449      │
     └─────────────────┘   └─────────────────┘   └─────────────────┘

  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
  │ COMPLIANCE  │ │  SECURITY   │ │   INFRA     │ │   MONITOR   │
  │ SSP/POAM    │ │ SAST/DAST   │ │ Terraform   │ │ Logs/Alerts │
  │ STIG/SBOM   │ │ CVE/Secrets │ │ Ansible/K8s │ │ Self-Heal   │
  │ Port 8446   │ │ Port 8447   │ │ Port 8448   │ │ Port 8450   │
  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘

  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
  │    MBSE     │ │MODERNIZATION│ │ REQUIREMENTS│ │SUPPLY CHAIN │
  │ SysML/DOORS │ │ 7R Assess   │ │ AI Intake   │ │ SBOM/ISA    │
  │ Port 8451   │ │ Port 8452   │ │ Port 8453   │ │ Port 8454   │
  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘

  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
  │ SIMULATION  │ │DEVSECOPS/ZTA│ │   GATEWAY   │
  │ Digital Twin│ │ Zero Trust  │ │ Remote Cmd  │
  │ Port 8455   │ │ Port 8457   │ │ Port 8458   │
  └─────────────┘ └─────────────┘ └─────────────┘
```

**Communication**: A2A Protocol (JSON-RPC 2.0 over mutual TLS), domain authority vetoes, HMAC-signed mailbox, W3C traceparent propagation.

---

## Why ICDEV Exists

### The Flywheel

```
                    ┌─────────────────────┐
                    │   DISCOVER (need)   │
                    │  Innovation Engine  │
                    │  Creative Engine    │
                    │  Standards Monitor  │
                    └─────────┬───────────┘
                              │
                              ▼
┌──────────────┐    ┌─────────────────────┐    ┌──────────────┐
│   MEASURE    │    │   INTAKE (require)  │    │    BUILD     │
│  Runtime     │◄───│  RICOAS: AI-driven  │───▶│  ATLAS/TDD   │
│  Feedback    │    │  gap detection,     │    │  6 languages │
│  Code Intel  │    │  SAFe decomposition │    │  500+ tools  │
└──────┬───────┘    └─────────────────────┘    └──────┬───────┘
       │                                              │
       │            ┌─────────────────────┐           │
       └───────────▶│   DEPLOY (deliver)  │◄──────────┘
                    │  Multi-cloud IaC    │
                    │  STIG containers    │
                    │  cATO monitoring    │
                    └─────────────────────┘
```

Every cycle strengthens the system:
- **Failures become patterns** → Knowledge agent learns → Next build avoids the same mistake
- **Compliance evidence compounds** → One NIST control maps to 34 frameworks → ATO accelerates
- **Child apps report back** → Evolutionary intelligence absorbs proven capabilities → Platform improves
- **Innovation Engine scans** → CVE/standard/community signals → Auto-generates protective solutions

---

## Capabilities

### For Government Program Managers

| Capability | What It Means |
|:---|:---|
| **ATO in weeks, not months** | Automated SSP, POAM, STIG, SBOM generation with NIST 800-53 control mapping that cascades to FedRAMP, CMMC, and 32 other frameworks simultaneously |
| **Requirements → Working Software** | AI-driven conversational intake (RICOAS) transforms vague SOW language into decomposed, traced, compliance-validated user stories with BDD acceptance criteria |
| **Digital Program Twin** | 6-dimension what-if simulation (schedule, cost, risk, compliance, technical, staffing) with Monte Carlo estimation and automated COA generation |
| **ATO Boundary Protection** | 4-tier impact classification (GREEN/YELLOW/ORANGE/RED) automatically generates alternative COAs when requirements threaten existing ATO |
| **Supply Chain Intelligence** | Dependency graph, ISA lifecycle management, NIST 800-161 SCRM assessment, CVE triage with SLA enforcement |

### For Systems Integrators & Developers

| Capability | What It Means |
|:---|:---|
| **True TDD** | RED → GREEN → REFACTOR with Cucumber/Gherkin BDD, Playwright E2E, and 9-step test pipeline (compile → lint → unit → BDD → SAST → E2E → vision → acceptance → gates) |
| **6 First-Class Languages** | Python, Java, Go, Rust, C#, TypeScript — each with scaffold, lint, format, SAST, dep audit, BDD steps, and code generation |
| **Cross-Language Translation** | 5-phase hybrid pipeline (Extract → Type-Check → Translate → Assemble → Validate+Repair) across 30 directional language pairs with pass@k candidate selection |
| **Legacy Modernization** | 7R assessment, version/framework migration, monolith decomposition, strangler fig tracking, ATO compliance bridge — all with digital thread traceability |
| **MBSE Integration** | SysML import, DOORS NG ReqIF, digital thread auto-linking, model-code drift detection, DoDI 5000.87 DES compliance |

### For CISOs & Security Teams

| Capability | What It Means |
|:---|:---|
| **Zero Trust Architecture** | NIST 800-207 compliance, 7-pillar maturity scoring (DoD ZTA Strategy), service mesh generation (Istio/Linkerd), network segmentation, PDP/PEP configuration |
| **AI Security** | MITRE ATLAS v5.4 assessment, OWASP LLM Top 10, OWASP Agentic AI (behavioral drift, trust scoring, tool chain validation), prompt injection detection (5 categories), AI BOM |
| **AI Transparency & Accountability** | OMB M-25-21/M-26-04, NIST AI 600-1, GAO-21-519SP — model cards, system cards, AI inventory, fairness assessment, confabulation detection, oversight plans, CAIO designation |
| **Self-Healing** | Confidence-based auto-remediation (≥0.7 auto-fix, 0.3-0.7 suggest, <0.3 escalate) with rate limiting (5/hour) and pattern learning |
| **Observability** | Distributed tracing (OpenTelemetry + SQLite dual-mode), W3C PROV-AGENT provenance, AgentSHAP tool attribution (Shapley values), XAI compliance assessment |

### For Enterprise Architecture

| Capability | What It Means |
|:---|:---|
| **Child App Generation** | ICDEV generates mini-ICDEV clones — production applications with their own GOTCHA framework, ATLAS workflow, 10-12 agents, 21 goals, and full compliance stack |
| **Evolutionary Intelligence** | Capability genome versioning (semver + SHA-256), 72-hour staging window, bidirectional parent-child learning, cross-pollination with HITL approval |
| **Federated Marketplace** | Share skills, goals, compliance extensions across tenant organizations through a 9-gate security pipeline with IL-aware content filtering |
| **Innovation Engine** | Autonomous self-improvement: web scanning (CVE, standards, community), 5-dimension scoring, compliance triage, trend detection, solution generation |
| **DoD MOSA Compliance** | 10 U.S.C. §4401 Modular Open Systems Approach — modularity analysis, ICD/TSP generation, code enforcement, cATO evidence |

---

## Compliance Coverage

ICDEV implements a **dual-hub crosswalk model**: implement a control once at either hub, and it cascades to every connected framework automatically.

```
                     ┌─────────────────────────────────┐
                     │       US Hub: NIST 800-53       │
                     │         (850 controls)          │
                     └──────┬──────────────────┬───────┘
                            │                  │
            ┌───────────────┼──────────┐       │
            │               │          │       │
     ┌──────▼─────┐  ┌─────▼────┐ ┌───▼───┐   │    ┌───────────────┐
     │  FedRAMP   │  │  CMMC    │ │ CJIS  │   │    │  Bridge Layer │
     │  Mod/High  │  │  L2/L3  │ │       │   ├───▶│  (bidirectional│
     │  + 20x KSI │  │         │ │       │   │    │   mapping)     │
     └────────────┘  └─────────┘ └───────┘   │    └───────┬───────┘
                                              │            │
     ┌────────────┐  ┌─────────┐ ┌───────┐   │    ┌───────▼───────┐
     │   HIPAA    │  │ PCI DSS │ │ SOC 2 │   │    │ International │
     │  + HITRUST │  │  v4.0   │ │Type II│   │    │Hub: ISO 27001 │
     └────────────┘  └─────────┘ └───────┘   │    └───────┬───────┘
                                              │            │
     ┌────────────┐  ┌─────────┐ ┌───────┐   │    ┌───────▼───────┐
     │  NIST 207  │  │  MOSA   │ │  DES  │   │    │  ISO 42001    │
     │  (ZTA)     │  │ 10 USC  │ │ 5000  │   │    │  EU AI Act    │
     └────────────┘  └─────────┘ └───────┘   │    └───────────────┘
                                              │
     ┌────────────┐  ┌─────────┐ ┌───────┐   │
     │ ATLAS v5.4 │  │OWASP LLM│ │AI RMF │   │
     │  (MITRE)   │  │ Top 10  │ │ + 600 │   │
     └────────────┘  └─────────┘ └───────┘   │
                                              │
     ┌────────────┐  ┌─────────┐ ┌───────┐   │
     │  OMB M-25  │  │ GAO AI  │ │SAFE-AI│   │
     │  M-26      │  │ 519SP   │ │Overlay│   │
     └────────────┘  └─────────┘ └───────┘   │
```

### Frameworks at a Glance

| Category | Frameworks | Controls |
|:---|:---|:---|
| **NIST Core** | 800-53 Rev 5, 800-171, 800-207 (ZTA), AI RMF, AI 600-1 | 850+ |
| **FedRAMP** | Moderate, High, 20x KSI (61 KSIs) | Baseline-dependent |
| **DoD** | CMMC L2/L3, CSSP (8530.01), DES (5000.87), MOSA (§4401) | Framework-specific |
| **Healthcare** | HIPAA Security Rule, HITRUST CSF v11 | 75+ / 156 |
| **Financial** | PCI DSS v4.0, SOC 2 Type II | 64+ / 60+ |
| **Law Enforcement** | CJIS Security Policy | 13 policy areas |
| **International** | ISO 27001:2022, ISO 42001:2023, EU AI Act | 93+ / 42+ / 12 |
| **AI Security** | MITRE ATLAS v5.4, OWASP LLM Top 10, OWASP Agentic, SAFE-AI | 30+ / 10 / 17 / 100 |
| **Federal AI** | OMB M-25-21, M-26-04, GAO-21-519SP, NIST AI 600-1 | 18+ / 12+ / 24+ / 12+ |
| **Secure Dev** | CISA Secure by Design, IEEE 1012 IV&V, FIPS 199/200 | Domain-specific |

---

## Multi-Cloud Deployment

ICDEV applications deploy to any authorized cloud — or air-gapped on-premises environments — from a single codebase.

| Cloud Provider | Region | Impact Levels | Key Services |
|:---|:---|:---|:---|
| **AWS GovCloud** | us-gov-west-1 | IL2-IL5 | EKS, RDS, Secrets Manager, Bedrock, S3 |
| **Azure Government** | usgovvirginia | IL2-IL5 | AKS, Azure SQL, Key Vault, Azure OpenAI |
| **GCP Assured Workloads** | us-central1 | IL2-IL4 | GKE, Cloud SQL, Secret Manager, Vertex AI |
| **OCI Government** | us-gov-ashburn-1 | IL2-IL5 | OKE, Autonomous DB, Vault, GenAI |
| **IBM Cloud (IC4G)** | us-south | IL2-IL5 | IKS, Cloud Object Storage, Key Protect, watsonx.ai |
| **On-Premises / Air-Gap** | Any | IL2-IL6/SECRET | K8s, Docker Compose, SQLite, Ollama |

Every deployment includes:
- **STIG-hardened containers** — Non-root (UID 1000), read-only rootfs, dropped capabilities, minimal packages
- **Network segmentation** — Default-deny NetworkPolicy, namespace isolation, mTLS service mesh
- **Auto-scaling** — HPA (CPU/memory), PDB, cross-AZ topology spread, Cluster Autoscaler
- **Helm chart** — Single `helm install` for on-premises deployment with per-CSP value overlays

---

## Multi-Agent Architecture

### Agent Communication Protocol

```
┌──────────────┐     A2A Protocol      ┌──────────────┐
│   Agent A    │◄──────────────────────▶│   Agent B    │
│              │   JSON-RPC 2.0        │              │
│  /.well-known│   mutual TLS          │  /.well-known│
│  /agent.json │   W3C traceparent     │  /agent.json │
│              │   HMAC-signed msgs    │              │
│  Domain:     │   Domain authority    │  Domain:     │
│  Authority   │   veto capability     │  Authority   │
└──────────────┘                       └──────────────┘
```

### Domain Authority Matrix

| Agent | Veto Type | Scope |
|:---|:---|:---|
| **Security** | Hard veto | Code generation, dependency changes, infrastructure |
| **Compliance** | Hard veto | Compliance artifacts, deployment decisions |
| **Architect** | Soft veto | System design, schema changes |

**Hard veto** = blocks the action. **Soft veto** = flags for review, doesn't block.

### Self-Healing Decision Engine

```
  Confidence ≥ 0.7  ──▶  Auto-remediate (max 5/hour, 10min cooldown)
  Confidence 0.3-0.7 ──▶  Suggest fix, require human approval
  Confidence < 0.3  ──▶  Escalate with full diagnostic context
```

---

## Child Application Generation

ICDEV generates production-ready child applications — each a mini-ICDEV with its own agents, goals, and compliance stack.

```
┌─────────────────────────────────────────────────────────────────────┐
│  ICDEV (Parent Platform)                                            │
│                                                                     │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐  │
│  │  Child App A    │   │  Child App B    │   │  Child App C    │  │
│  │  ┌───────────┐  │   │  ┌───────────┐  │   │  ┌───────────┐  │  │
│  │  │ 10-12     │  │   │  │ 10-12     │  │   │  │ 10-12     │  │  │
│  │  │ Agents    │  │   │  │ Agents    │  │   │  │ Agents    │  │  │
│  │  │ 21 Goals  │  │   │  │ 21 Goals  │  │   │  │ 21 Goals  │  │  │
│  │  │ GOTCHA    │  │   │  │ GOTCHA    │  │   │  │ GOTCHA    │  │  │
│  │  │ ATLAS     │  │   │  │ ATLAS     │  │   │  │ ATLAS     │  │  │
│  │  │ Full TDD  │  │   │  │ Full TDD  │  │   │  │ Full TDD  │  │  │
│  │  │ Compliance│  │   │  │ Compliance│  │   │  │ Compliance│  │  │
│  │  └───────────┘  │   │  └───────────┘  │   │  └───────────┘  │  │
│  │                 │   │                 │   │                 │  │
│  │  Reports back   │   │  Reports back   │   │  Reports back   │  │
│  │  via A2A ──────────────────────────────────────────▶ ICDEV  │  │
│  └─────────────────┘   └─────────────────┘   └─────────────────┘  │
│                                                                     │
│  Evolutionary Intelligence: genome versioning, capability           │
│  evaluation, 72-hour staging, HITL-approved absorption              │
│                                                                     │
│  ⛔ Grandchild prevention: children CANNOT generate their own      │
│     child applications (3-layer enforcement)                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Security Posture

### 19 Security Gates (Blocking)

Every code change, merge, and deployment must pass through security gates. Any single failure blocks progression.

| Gate | Key Blocking Conditions |
|:---|:---|
| Code Review | SAST clean, no secrets, CUI markings present |
| Merge | 0 CAT1 STIG, 0 critical vulns, ≥80% coverage, SBOM current |
| Deploy | Staging tests pass, compliance artifacts current, rollback plan exists |
| FedRAMP | 0 other-than-satisfied on high-priority controls, FIPS 140-2 encryption |
| CMMC | 0 not-met Level 2 practices, evidence current within 90 days |
| cATO | 0 expired evidence on critical controls |
| AI Security | Prompt injection defense active, AI telemetry enabled, ≥80% ATLAS coverage |
| ZTA | Maturity ≥ Advanced for IL4+, mTLS enforced, default-deny NetworkPolicy |
| Supply Chain | 0 critical SCRM risks, 0 expired ISAs, 0 overdue critical CVE SLAs |
| MOSA | 0 interfaces without ICD, modularity score ≥0.6 |
| AI Accountability | CAIO designated for high-impact AI, oversight plan exists |

### AI-Specific Security

- **Prompt Injection Detection** — 5 categories: role hijacking, delimiter attacks, instruction injection, data exfiltration, encoded payloads
- **Behavioral Drift Detection** — Z-score baseline with 7-day sliding window per agent
- **Tool Chain Validation** — Sliding-window sequence matching for suspicious tool invocation patterns
- **Agent Trust Scoring** — Dynamic decay/recovery factors, 3 trust levels (trusted/monitored/untrusted)
- **MCP Per-Tool RBAC** — Deny-first authorization, 5 roles (admin, pm, developer, isso, co)
- **ATLAS Red Teaming** — 6 automated adversarial techniques (opt-in only)
- **AI Bill of Materials** — Tracks all AI/ML components, models, training data lineage

---

## RICOAS — Requirements to Delivery

### The Full Pipeline

```
Customer Need
    │
    ▼
┌─────────────────────────┐
│  CONVERSATIONAL INTAKE  │  AI-guided Q&A, ambiguity detection
│  Gap Detection (7 dims) │  Completeness, clarity, feasibility,
│  Document Upload (SOW)  │  compliance, testability, devsecops,
│  Readiness Scoring      │  AI governance readiness
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  ATO BOUNDARY ANALYSIS  │  4-tier impact classification
│  Supply Chain Intel     │  GREEN → proceed
│  ISA Lifecycle          │  YELLOW → SSP addendum
│  CVE Triage             │  ORANGE → SSP revision + ISSO review
│                         │  RED → FULL STOP → Alternative COAs
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  DIGITAL PROGRAM TWIN   │  6-dimension what-if simulation
│  Monte Carlo            │  Schedule, cost, risk, compliance,
│  COA Generation         │  technical, staffing
│  COA Comparison         │  Speed / Balanced / Comprehensive
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  SAFe DECOMPOSITION     │  Epic → Capability → Feature →
│  WSJF Scoring           │  Story → Enabler
│  BDD Criteria           │  With NIST control inheritance
│  Parallel Task Groups   │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  ATLAS BUILD PIPELINE   │  TDD code generation in 6 languages
│  9-Step Test Pipeline   │  STIG-hardened containers
│  Multi-Cloud Deploy     │  cATO monitoring
│  Self-Healing Monitor   │  Pattern-based auto-remediation
└─────────────────────────┘
```

---

## Technology Stack

### Core Platform

| Layer | Technology |
|:---|:---|
| **Language** | Python 3.9+ (stdlib-first design for air-gap safety) |
| **Database** | SQLite (platform internals), PostgreSQL (SaaS/apps) |
| **Web Framework** | Flask (SSR, auditable, minimal STIG surface) |
| **LLM Routing** | Vendor-agnostic router with function-level model selection |
| **LLM Providers** | AWS Bedrock, Azure OpenAI, Vertex AI, OCI GenAI, IBM watsonx.ai, Ollama (local) |
| **Containers** | Docker (STIG-hardened, 17 Dockerfile variants) |
| **Orchestration** | Kubernetes 1.25+ (RBAC, NetworkPolicy, HPA, PDB) |
| **IaC** | Terraform (multi-cloud generators) + Ansible |
| **Packaging** | Helm chart with per-CSP value overlays |
| **CI/CD** | GitHub Actions + GitLab CI (dual-platform) |
| **Testing** | pytest + behave/Gherkin + Playwright + security gates |
| **Observability** | OpenTelemetry + SQLite dual-mode, W3C PROV, AgentSHAP |
| **MCP** | 241 tools across unified gateway (stdio + Streamable HTTP) |

### Application Languages Supported

| Language | Scaffold | Lint | SAST | BDD | Code Gen |
|:---|:---|:---|:---|:---|:---|
| Python | Flask/FastAPI | ruff | bandit | behave | Full |
| Java | Spring Boot | checkstyle/PMD | SpotBugs | Cucumber-JVM | Full |
| Go | net/http, Gin | golangci-lint | gosec | godog | Full |
| Rust | Actix-web | clippy | cargo-audit | cucumber-rs | Full |
| C# | ASP.NET Core | dotnet analyzers | SecurityCodeScan | SpecFlow | Full |
| TypeScript | Express | eslint + tsc | eslint-security | cucumber-js | Full |

---

## SaaS Multi-Tenancy

ICDEV operates as a multi-tenant SaaS platform with IL-appropriate isolation:

| Impact Level | Compute Isolation | Database Isolation | Network Isolation |
|:---|:---|:---|:---|
| IL2-IL4 | Dedicated K8s namespace | Dedicated database | NetworkPolicy |
| IL5 | Dedicated namespace + node pool | Dedicated RDS instance | VPC peering |
| IL6/SECRET | Dedicated AWS sub-account | Isolated VPC database | Air-gapped |

**Authentication**: API key, OAuth 2.0/OIDC, CAC/PIV (DoD PKI)
**Transport**: REST API + MCP Streamable HTTP (JSON-RPC 2.0)
**Marketplace**: Federated GOTCHA asset sharing with 9-gate security pipeline

---

## By the Numbers

<div align="center">

| Metric | Value |
|:---|:---|
| **Compliance Frameworks** | 34 (dual-hub crosswalk) |
| **AI Agents** | 15 (3-tier architecture) |
| **Deterministic Tools** | 500+ (air-gap safe) |
| **MCP Tools** | 241 (unified gateway) |
| **Database Tables** | 255 (append-only audit) |
| **Test Functions** | 3,800+ (pytest + BDD + E2E) |
| **Python Modules** | 630+ |
| **Security Gates** | 19 (blocking) |
| **Config Files** | 54 YAML + 90 JSON |
| **Goal Workflows** | 56 |
| **Dashboard Pages** | 40+ |
| **K8s Manifests** | 29 |
| **Dockerfiles** | 17 (STIG-hardened) |
| **Cloud Providers** | 6 (+ air-gap) |
| **Languages** | 6 (first-class support) |
| **Development Phases** | 61 (and counting) |

</div>

---

## What Makes This Different

Most "AI coding tools" are glorified autocomplete. ICDEV is fundamentally different:

| Typical AI Tool | ICDEV |
|:---|:---|
| Suggests code snippets | Engineers complete applications |
| No compliance awareness | 34 compliance frameworks with dual-hub crosswalk |
| Single language | 6 first-class languages with cross-translation |
| Cloud-specific | 6 CSPs + air-gap from single codebase |
| No security posture | 19 blocking security gates, ZTA, ATLAS red teaming |
| Stateless | Evolutionary intelligence with capability genome |
| One-shot | Self-improving flywheel (discover → build → measure → improve) |
| Human-dependent | Autonomous with human-in-the-loop for high-risk decisions |
| Template-based | Meta-builder — generates applications that generate features |

---

## Built with ICDEV

These production applications were autonomously generated by ICDEV — each with its own agent architecture, compliance stack, TDD pipeline, and full GOTCHA framework.

| Application | Description | Stack | Status |
|:---|:---|:---|:---|
| [**GovProposal**](https://github.com/icdev-ai/govproposal) | Government proposal lifecycle platform — SAM.gov opportunity scanning, RFP analysis, AI-assisted section drafting, compliance matrix generation, review workflows, and CPMP post-award tracking | Python, Flask, SQLite, 10 agents, 34 compliance frameworks | Production |

> *This list grows as ICDEV generates more applications. Each child app inherits 10-12 AI agents, 21 goal workflows, full TDD/BDD testing, and multi-cloud deployment — autonomously, from a single requirements intake session.*

---

## Getting Started

```bash
# Clone the repository
git clone https://github.com/icdev-ai/icdev.git && cd icdev

# Install dependencies
pip install -r requirements.txt

# Initialize the platform
python tools/db/init_icdev_db.py

# Start the dashboard
python tools/dashboard/app.py

# Run the full test suite
pytest tests/ -v --tb=short
```

For modular installation with compliance profiles:

```bash
# DoD team with FedRAMP High + CMMC
python tools/installer/installer.py --profile dod_team --compliance fedramp_high,cmmc --platform k8s

# Healthcare with HIPAA + HITRUST
python tools/installer/installer.py --profile healthcare --compliance hipaa,hitrust

# Interactive guided setup
python tools/installer/installer.py --interactive
```

---

## License

Dual-licensed:
- **AGPL-3.0** — Open source (government transparency obligations)
- **Commercial** — Available for proprietary deployments

---

<div align="center">

**ICDEV — A system that builds systems.**

*Autonomous AI engineering for the missions that matter.*

<br/>

[![Python](https://img.shields.io/badge/Built_with-Python-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Kubernetes](https://img.shields.io/badge/Deployed_on-Kubernetes-326ce5?style=for-the-badge&logo=kubernetes&logoColor=white)](https://kubernetes.io)
[![Flask](https://img.shields.io/badge/Powered_by-Flask-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com)

</div>
