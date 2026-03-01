# Phase 36 — Evolutionary Intelligence System

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 36 |
| Title | Evolutionary Intelligence System |
| Status | Requirements |
| Priority | P2 |
| Dependencies | Phase 19 (Agentic Generation), Phase 22 (Marketplace), Phase 35 (Innovation Engine) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-21 |

---

## 1. Problem Statement

ICDEV currently births child applications via Phase 19 (Agentic Generation) and walks away. There is no ongoing parent-child relationship, no feedback loop, no capability propagation, and no mechanism for children to report learned behaviors back to the parent. Each child is a snapshot frozen at birth — it cannot benefit from innovations discovered after its creation, and the parent cannot learn from the child's field experience.

ICDEV and its child applications are living organisms. They must **learn, adapt, improve, innovate, and grow intellectually**. In some cases, they reproduce (via Phase 19) to advance their cause and function. This phase establishes the biological evolution metaphor as an architectural reality.

---

## 2. Goals

1. Maintain a **Living Registry** of child applications with real-time capability tracking, health telemetry, and version management
2. Connect the **Innovation Engine** (Phase 35) to a propagation pipeline that delivers new capabilities to children
3. Enable **bidirectional learning** — parent pushes innovations to children; children report field experience back to parent
4. Maintain a **Capability Genome** — a versioned set of core capabilities that new children inherit at birth and that evolves over time
5. Evaluate newly learned behaviors for **absorption into the core genome** so future children start smarter
6. Support **cross-pollination** — capabilities proven in one child can be brokered to other children via the parent

---

## 3. Biological Metaphor Mapping

| Biological Concept | ICDEV Equivalent | Implementation |
|--------------------|------------------|----------------|
| DNA/Genome | Capability Genome | Versioned set of capabilities, patterns, and knowledge |
| Birth | Agentic Generation (Phase 19) | Child inherits current genome snapshot at creation |
| Nervous System | A2A Heartbeat Protocol | Parent-child telemetry channel for health and learning |
| Learning | Innovation Engine + Field Experience | Web intelligence + child-reported patterns |
| Memory | Knowledge Base + Memory System | Bidirectional, shared across parent and children |
| Immune System | Compliance Gates + Security Scanning | Prevents propagation of harmful capabilities |
| Growth | Capability Propagation + Genome Evolution | Proven capabilities absorbed into core |
| Natural Selection | Capability Evaluation Scoring | Keep what works, discard what doesn't |
| Reproduction | Agentic Generation | New children inherit the latest genome |

---

## 4. Architecture

### 4.1 System Overview

```
+-----------------------------------------------------+
|                 ICDEV PARENT (Brain)                  |
|                                                       |
|  +----------+  +----------+  +-------------------+   |
|  | Innovate |->| Evaluate |->| Capability Genome |   |
|  | (Ph. 35) |  | (NEW)    |  | (NEW - versioned) |   |
|  +----------+  +----------+  +-------------------+   |
|       ^              |              |                 |
|       |              v              v                 |
|  +----------+  +----------+  +------------------+    |
|  | Absorb   |<-| Stage &  |->| Deploy Manager   |    |
|  | (NEW)    |  | Test     |  | (NEW - HITL)     |    |
|  +----------+  | (NEW)    |  +--------+---------+    |
|       ^        +----------+           |               |
|       |                               |               |
|  +----+------------------------------+|               |
|  |     Living Registry (Enhanced)    ||               |
|  |  +-------+ +-------+ +-------+   ||               |
|  |  |Child A| |Child B| |Child C|   ||               |
|  |  |v2.3   | |v1.1   | |v3.0   |   ||               |
|  |  |healthy | | warn  | |healthy|   ||               |
|  |  +---+---+ +---+---+ +---+---+   ||               |
|  +------+----------+--------+-------+|               |
+---------+----------+--------+--------+---------------+
          |          |        |        |
          v          v        v        v
     +---------++---------++---------+
     | Child A || Child B || Child C |  <- Living instances
     | (field) || (field) || (field) |     reporting back
     +---------++---------++---------+
```

### 4.2 Pipeline

```
DISCOVER (web + introspective + competitive + standards + child field reports)
    -> SCORE (5-dimension weighted average)
        -> TRIAGE (5-stage compliance gate)
            -> EVALUATE (6-dimension capability evaluation)
                -> STAGE (isolated build + test)
                    -> DEPLOY (HITL approval -> push to children)
                        -> VERIFY (72-hour stability window)
                            -> ABSORB (proven capability -> genome)
                                -> MEASURE -> CALIBRATE
```

### 4.3 Learning Flows

**Parent -> Child (Push):**
- Security patches (urgent, can bypass staging for critical CVEs)
- Compliance updates (new framework requirements)
- New capabilities (features, tools, optimizations)
- Genome upgrades (versioned capability bundles)

**Child -> Parent (Report):**
- Learned optimizations (e.g., "caching STIG checks reduced scan time 40%")
- Edge case discoveries (new failure modes, unexpected inputs)
- Failure patterns (self-healing events, root cause data)
- Usage telemetry (which tools used most, performance metrics)
- Self-healing successes (proven remediation patterns)

**Child -> Child (via Parent as Broker):**
- Cross-pollinated capabilities (optimization from Child A useful to Child C)
- Shared knowledge patterns (failure patterns, solutions)
- Marketplace assets (skills, goals, compliance extensions)

---

## 5. Requirements

### 5.1 Living Registry (Enhanced Child App Registry)

#### REQ-36-001: Child Capability Tracking
The system SHALL maintain a real-time inventory of each child application's capabilities, including capability name, version, status (active/deprecated/pending), and the date learned.

#### REQ-36-002: Child Telemetry Collection
The system SHALL collect heartbeat telemetry from child applications including: health status, uptime, error rates, compliance posture scores, active capabilities, and learned behaviors.

#### REQ-36-003: Pull-Based Telemetry (D210)
Telemetry collection SHALL be pull-based (parent polls child) rather than push-based, to support air-gapped deployments where children may not have outbound connectivity to the parent.

#### REQ-36-004: Heartbeat Payload
Each child heartbeat SHALL include:
- Child ID and genome version
- List of active capabilities with versions
- Health status (healthy/degraded/unhealthy)
- Uptime in hours
- Learned behaviors (array of {id, type, description, evidence, confidence})
- Compliance posture scores per active framework
- Pending upgrades list

#### REQ-36-005: Registry Dashboard
The system SHALL provide a dashboard page (`/children`) displaying all registered children with health status, genome version, capability count, last heartbeat time, and pending upgrades.

### 5.2 Capability Genome

#### REQ-36-010: Genome Versioning
The system SHALL maintain a versioned Capability Genome using semantic versioning (major.minor.patch) with content hashing (SHA-256) for integrity verification.

#### REQ-36-011: Genome Snapshot at Birth (D215)
When a new child application is generated (Phase 19), it SHALL inherit a snapshot of the current genome version. The child does not auto-upgrade; upgrades are explicit and require HITL approval.

#### REQ-36-012: Genome Contents
The genome SHALL include:
- Core capability definitions (tools, goals, args, context, hardprompts)
- Compliance framework configurations
- Security gate definitions
- Self-healing patterns
- Knowledge base patterns
- Default configurations

#### REQ-36-013: Genome Diff
The system SHALL support diffing between genome versions to show what capabilities were added, removed, or modified between any two versions.

#### REQ-36-014: Genome Rollback
The system SHALL support rolling back a child's genome to a previous version, creating a new version entry (consistent with D183 append-only versioning).

### 5.3 Capability Evaluation

#### REQ-36-020: 6-Dimension Evaluation
When a new capability is discovered (from Innovation Engine or child field reports), the system SHALL evaluate it across 6 dimensions:

| Dimension | Weight | Question |
|-----------|--------|----------|
| Universality | 0.25 | How many children could benefit? |
| Compliance Safety | 0.25 | Does it maintain or improve compliance posture? |
| Risk | 0.20 | What could go wrong? What is the blast radius? |
| Evidence | 0.15 | How well-tested is this in the field? |
| Novelty | 0.10 | Does this fill a gap or duplicate existing capability? |
| Cost | 0.05 | Resource/token cost of integration? |

#### REQ-36-021: Evaluation Outcomes
Evaluation SHALL produce deterministic outcomes:
- Score >= 0.85: Auto-queue for staging (notification only to HITL)
- Score 0.65–0.84: Recommend for staging (HITL approval required)
- Score 0.40–0.64: Log for future consideration (no action)
- Score < 0.40: Archive (no action)

#### REQ-36-022: Evaluation Audit Trail
All evaluation decisions SHALL be recorded in the append-only audit trail (D6 pattern) with full scoring details, dimension breakdowns, and rationale.

### 5.4 Staging and Testing

#### REQ-36-030: Staging Environment
The system SHALL create isolated staging environments using git worktrees (D32/D211) for testing new capabilities before propagation.

#### REQ-36-031: Full Test Suite in Staging
Capabilities in staging SHALL pass the complete test pipeline: syntax validation, linting, unit tests, BDD tests, security scanning, compliance gates, and acceptance validation.

#### REQ-36-032: Compliance Preservation Gate
No capability SHALL be propagated if it would weaken any child's existing compliance posture. The staging environment SHALL verify compliance scores before and after capability integration.

### 5.5 Deployment and Propagation

#### REQ-36-040: HITL Approval for Deployment
All capability deployments to production children SHALL require human-in-the-loop approval. No autonomous deployment to production children is permitted.

#### REQ-36-041: Rollback Plan
Every propagated capability SHALL have a documented rollback plan. Deployment SHALL not proceed without a verified rollback path.

#### REQ-36-042: Propagation Log (D214)
All propagations SHALL be recorded in an append-only propagation log including: capability ID, source (innovation/child/cross-pollination), target child IDs, deployer identity, timestamp, genome version before/after.

#### REQ-36-043: Selective Propagation
The system SHALL support deploying capabilities to a subset of children (not all-or-nothing), based on compatibility scoring, compliance posture, and capability prerequisites.

### 5.6 Absorption (Genome Evolution)

#### REQ-36-050: 72-Hour Stability Window (D212)
A capability SHALL NOT be absorbed into the genome until it has been stable in at least one child's staging environment for 72 hours with zero failures.

#### REQ-36-051: Child-Learned Behavior Evaluation (D213)
Behaviors reported by children SHALL be evaluated by the parent before cross-pollination. One child's optimization might break another child's compliance posture.

#### REQ-36-052: Genome Version Increment
When a capability is absorbed, the genome version SHALL be incremented (minor version for new capabilities, patch for optimizations, major for breaking changes).

#### REQ-36-053: Grandchild Prevention (D52)
The genome SHALL NOT include the capability to generate further children. The grandchild prevention flag (D52) SHALL be preserved in all genome versions.

---

## 6. Database Schema

### New Tables

| Table | Purpose |
|-------|---------|
| `child_capabilities` | What each child can do (child_id, capability_name, version, status, learned_at) |
| `child_telemetry` | Heartbeat data (child_id, timestamp, health, uptime, error_rate, compliance_json) |
| `child_learned_behaviors` | Patterns discovered by children (child_id, type, description, evidence_json, confidence, evaluated, absorbed) |
| `capability_genome` | Versioned capability set (version, content_hash, capabilities_json, created_at, created_by) |
| `genome_versions` | Version history with diffs (version_from, version_to, diff_json, change_type, change_summary) |
| `capability_evaluations` | Evaluation results (capability_id, score, dimensions_json, outcome, evaluator, timestamp) |
| `staging_environments` | Active staging instances (env_id, capability_id, worktree_path, status, created_at, expires_at) |
| `propagation_log` | Append-only deployment record (capability_id, source_type, target_child_ids, deployer, genome_before, genome_after, timestamp) |

---

## 7. New Tools

| Tool | Purpose |
|------|---------|
| `tools/registry/child_registry.py` | Enhanced CRUD with capability tracking, health queries |
| `tools/registry/telemetry_collector.py` | Collect and store child heartbeats |
| `tools/registry/genome_manager.py` | Version, diff, rollback the capability genome |
| `tools/registry/capability_evaluator.py` | 6-dimension scoring engine |
| `tools/registry/staging_manager.py` | Create/destroy staging environments (worktree-based) |
| `tools/registry/propagation_manager.py` | Deploy capabilities to children with HITL gates |
| `tools/registry/absorption_engine.py` | Evaluate and absorb capabilities into genome |
| `tools/registry/learning_collector.py` | Process child-reported learned behaviors |
| `tools/registry/cross_pollinator.py` | Broker capabilities between children via parent |

---

## 8. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D209 | Genome is versioned with semver + content hash | Children can pin to a genome version; upgrades are explicit |
| D210 | Child telemetry is pull-based (parent polls) not push-based | Air-gap safe; child doesn't need outbound connectivity to parent |
| D211 | Staging uses git worktrees (D32) for isolation | Reuses existing infrastructure, zero new dependencies |
| D212 | 72-hour stability window before genome absorption | Prevents premature integration of unstable capabilities |
| D213 | Child-learned behaviors require parent evaluation before cross-pollination | One child's optimization might break another's compliance posture |
| D214 | Propagation is append-only audited (D6 pattern) | Full traceability of what was deployed where and when |
| D215 | Genome inheritance at birth is snapshot-based | Child gets current genome; doesn't auto-upgrade (explicit HITL) |

---

## 9. Sub-Phases

### Sub-Phase 36A: Living Registry + Telemetry
**Scope:** Enhance `child_app_registry`, add telemetry tables, implement heartbeat protocol, build registry dashboard page.

**Deliverables:**
- Enhanced `child_app_registry` with capability fields
- `child_capabilities`, `child_telemetry`, `child_learned_behaviors` tables
- `tools/registry/child_registry.py`
- `tools/registry/telemetry_collector.py`
- Modified `tools/builder/child_app_generator.py` — inject heartbeat agent into children at birth
- Dashboard page `/children`

**Why first:** You cannot propagate capabilities to children you cannot see or talk to.

### Sub-Phase 36B: Innovation-to-Staging Pipeline
**Scope:** Connect Innovation Engine output to staging environments, build capability evaluator, integrate with existing test orchestrator.

**Deliverables:**
- `capability_genome`, `genome_versions`, `capability_evaluations`, `staging_environments` tables
- `tools/registry/genome_manager.py`
- `tools/registry/capability_evaluator.py`
- `tools/registry/staging_manager.py`
- `tools/registry/propagation_manager.py`
- Modified `tools/innovation/solution_generator.py` — output includes propagation metadata

**Why second:** Innovations exist (Phase 35) but have no deployment path to children.

### Sub-Phase 36C: Genome Evolution + Bidirectional Learning
**Scope:** Absorption pipeline (field-tested capabilities -> genome), child-to-parent learning, cross-pollination brokering.

**Deliverables:**
- `propagation_log` table
- `tools/registry/absorption_engine.py`
- `tools/registry/learning_collector.py`
- `tools/registry/cross_pollinator.py`
- Modified `tools/builder/child_app_generator.py` — new children inherit latest genome snapshot
- Genome version management and diff/rollback UI

**Why last:** Requires both registry (36A) and propagation (36B) to be functional.

---

## 10. Security Guardrails

1. **No autonomous deployment** — All pushes to production children require HITL approval (D200 pattern extended)
2. **Compliance preservation** — No capability can weaken a child's compliance posture (crosswalk gate)
3. **Rollback mandatory** — Every propagated capability must have a rollback plan
4. **72-hour stability window** — Capabilities must run stable in staging before genome absorption (D212)
5. **Grandchild prevention still holds** — Children cannot evolve to the point of self-reproduction (D52)
6. **Append-only audit** — Every propagation, evaluation, and absorption is permanently recorded (D6)
7. **Air-gap safe** — Pull-based telemetry, no child-initiated outbound connections required (D210)
8. **Budget cap** — Max 10 capability propagations per PI (extends D201 pattern)
9. **Classification filtering** — Capabilities from higher-IL children cannot propagate to lower-IL children

---

## 11. Security Gate

**Evolutionary Intelligence Gate:**
- 0 capabilities propagated without HITL approval
- 0 capabilities with compliance regression
- Genome integrity hash verified on every read
- 72-hour stability window satisfied for all absorptions
- Rollback plan documented for every propagation
- 0 grandchild generation attempts in child genome
