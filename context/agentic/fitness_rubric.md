# [TEMPLATE: CUI // SP-CTI]
# Agentic Fitness Scoring Rubric

## Purpose
This rubric guides the Architect agent when evaluating whether a component should use agent architecture, NLQ interfaces, traditional REST/CRUD, or a hybrid approach.

## 6 Scoring Dimensions (each 0-10)

### 1. Data Complexity (Weight: 10%)
- **1-3:** Simple flat data, key-value, single table CRUD
- **4-6:** Relational data with joins, versioning, search indices
- **7-10:** Graph relationships, event-sourcing, CQRS, unstructured data, multi-tenant sharding

### 2. Decision Complexity (Weight: 25%)
- **1-3:** Simple CRUD, static lookups, deterministic validation
- **4-6:** Workflow with branching, state machines, rule-based scoring
- **7-10:** Classification, intent routing, NLP, prediction, anomaly detection, adaptive behavior

### 3. User Interaction (Weight: 20%)
- **1-3:** API-only, headless, batch processing, CLI
- **4-6:** Dashboard, forms, wizards, filtered search, reports
- **7-10:** Natural language queries, conversational, voice, exploratory search, chatbot

### 4. Integration Density (Weight: 15%)
- **1-3:** Standalone, self-contained, no external dependencies
- **4-6:** API integrations, webhooks, SSO, database connections
- **7-10:** Multi-agent orchestration, event-driven mesh, federated, cross-system sync

### 5. Compliance Sensitivity (Weight: 15%)
- **1-3:** Public data, no compliance requirements, prototype
- **4-6:** Standard compliance (GDPR, HIPAA), RBAC, logging
- **7-10:** CUI/SECRET, FedRAMP, CMMC, NIST 800-53, FIPS encryption, audit non-repudiation

### 6. Scale Variability (Weight: 15%)
- **1-3:** Fixed users, low traffic, single instance
- **4-6:** Moderate scale, predictable growth, load balanced
- **7-10:** Burst patterns, auto-scaling, real-time streaming, millions concurrent

## Recommendation Mapping

| Overall Score | Architecture |
|---------------|-------------|
| >= 6.0 | Full agent architecture |
| 4.0 - 5.9 | Hybrid (agent + traditional) |
| < 4.0 | Traditional REST/CRUD |

**NLQ Interface:** Added when user_interaction dimension >= 5.0 (regardless of overall score).

## Always-On Capabilities
These are included in EVERY generated app regardless of fitness score:
- Self-healing with pattern detection
- A2A interoperability (agent card)
- AIOps (predictive scaling, anomaly detection)
- GOTCHA framework (goals, tools, args, context, hardprompts)
- AI governance (token budgets, prompt injection defense)
- User feedback collection (RLHF-lite)
