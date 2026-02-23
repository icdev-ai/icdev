# [TEMPLATE: CUI // SP-CTI]

# Goal: Evolutionary Intelligence

## Purpose

Manage the lifecycle of ICDEV's capability genome and bidirectional learning system with child applications. This goal orchestrates the discovery, evaluation, staging, deployment, and absorption of new capabilities across the ICDEV parent-child ecosystem.

**Why this matters:** Child applications operate in diverse mission environments. They encounter edge cases, develop workarounds, and learn patterns that the parent ICDEV has never seen. Evolutionary Intelligence provides a governed pipeline for harvesting those learnings, validating them for safety and compliance, and propagating proven capabilities across the entire fleet — turning every child deployment into a force multiplier for the ecosystem.

---

## When to Use

- When the Innovation Engine (Phase 35) discovers new capabilities to propagate
- When child applications report learned behaviors back to the parent
- When cross-pollinating capabilities between children
- When managing the capability genome (versioning, diff, rollback)
- When evaluating capabilities for genome absorption

---

## Prerequisites

- [ ] Phase 19 (Agentic Generation) — child apps must exist (`child_app_registry` table populated)
- [ ] Phase 22 (Marketplace) — for asset sharing infrastructure
- [ ] Phase 35 (Innovation Engine) — for capability discovery
- [ ] Phase 37 (MITRE ATLAS) — for AI security scanning of learned behaviors
- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] Memory system operational (`memory/MEMORY.md` exists)

---

## Workflow

### Step 1: Discover

Collect candidate capabilities from multiple sources: Innovation Engine web scanning, introspective analysis, competitive intelligence, standards monitoring, and child field reports (telemetry + learned behaviors).

**Tools:**
- `tools/innovation/innovation_manager.py --run` — Full innovation pipeline scan
- `tools/registry/learning_collector.py` — Harvest child-reported learned behaviors
- `tools/registry/telemetry_collector.py` — Collect child health and performance data

**Sources:**
| Source | Type | Air-Gap Safe |
|--------|------|-------------|
| Innovation Engine (web) | External | No (requires internet) |
| Introspective analysis | Internal | Yes |
| Competitive intelligence | External | No |
| Standards monitoring | External | Degrades gracefully |
| Child field reports | Internal | Yes (pull-based, D210) |

**Output:** Raw capability candidates stored in `innovation_signals` table with source attribution.

**Error handling:**
- Child unreachable -> skip, mark child as degraded, do not block pipeline
- Innovation Engine offline -> proceed with internal sources only (introspective + child reports)

---

### Step 2: Evaluate

Score each candidate capability across 7 dimensions using deterministic weighted scoring.

**Tool:** `tools/registry/capability_evaluator.py`

| Dimension | Weight | What It Measures |
|-----------|--------|------------------|
| universality | 20% | Applicable to how many children? (1 child = low, all = high) |
| compliance_safety | 20% | Does it weaken any compliance posture? |
| risk | 15% | Blast radius if capability fails |
| evidence | 15% | How much field data supports this capability? |
| novelty | 10% | Does this already exist in the genome? |
| cost | 10% | Token cost, compute cost, maintenance burden |
| security_assessment | 10% | Prompt injection risk, data exfiltration risk |

**Outcome mapping:**

| Score Range | Action |
|-------------|--------|
| >= 0.85 | Auto-queue for staging |
| 0.65 - 0.84 | Recommend for staging (HITL required) |
| 0.40 - 0.64 | Log for future consideration |
| < 0.40 | Archive with rationale |

**Output:** Evaluation records stored in audit trail (append-only, D6).

**Error handling:**
- Missing dimension data -> score available dimensions, flag incomplete assessment, require HITL review regardless of score

---

### Step 3: Stage

Create isolated staging environment using git worktrees (D211). Run the full 9-step test pipeline against the capability in isolation.

**Tool:** `tools/registry/staging_manager.py`

**Staging pipeline:**
1. Create git worktree for capability isolation
2. Apply capability changes to staging worktree
3. Run py_compile syntax validation
4. Run ruff linter
5. Run pytest unit/integration tests
6. Run behave BDD tests (if .feature files affected)
7. Run bandit SAST scan
8. Run secret detection
9. Verify CUI markings preserved

**Output:** Staging report with pass/fail per pipeline step.

**Error handling:**
- Staging tests fail -> reject capability, log failure reason, notify HITL with failure details
- Worktree creation fails -> fall back to branch-based isolation, warn about reduced isolation

**Verify:** All 9 pipeline steps pass. No compliance regression detected.

---

### Step 4: Approve (HITL)

All deployments to production children require human-in-the-loop approval (REQ-36-040). No exceptions.

**Approval workflow:**
1. System presents: capability summary, evaluation score, staging results, affected children, rollback plan
2. Approver reviews and selects: approve / reject / defer
3. Decision recorded in audit trail with approver identity and rationale

**Tool:** `tools/integration/approval_manager.py --submit capability_propagation`

**Error handling:**
- Approval timeout (configurable, default 7 days) -> auto-defer, notify approver
- Approver lacks authority -> escalate to ISSO

---

### Step 5: Propagate

Deploy approved capabilities to target children. Selective deployment supported — not all-or-nothing. Each child can be targeted individually or by capability profile match.

**Tool:** `tools/registry/propagation_manager.py`

**Propagation modes:**
| Mode | Description |
|------|-------------|
| targeted | Specific child IDs |
| profile_match | All children matching a capability profile |
| canary | Single child first, then progressive rollout |
| fleet_wide | All healthy children (requires elevated approval) |

**Security gates (all must pass before propagation):**
- Prompt injection scanning on all propagated content (Phase 37 MITRE ATLAS integration)
- Compliance preservation verification (no framework regression)
- Rollback plan documented and tested
- AI telemetry logging enabled for monitoring period

**Output:** Propagation log entries (append-only, D214) with per-child status.

**Error handling:**
- Child rejects propagation (version conflict) -> log conflict, skip child, continue fleet
- Propagation partially fails -> mark failed children, do not roll back successful ones, notify HITL

---

### Step 6: Verify

72-hour stability window (D212) after propagation. Monitor child health via pull-based telemetry (D210 — air-gap safe, no child outbound required).

**Tool:** `tools/registry/telemetry_collector.py`

**Monitoring dimensions:**
- Child heartbeat regularity (expected interval vs actual)
- Agent health status (all agents operational)
- Error rate delta (pre-propagation vs post-propagation)
- Test pass rate (automated test suites still passing)
- Compliance posture (no regression in framework scores)

**Thresholds:**
| Metric | Threshold | Action |
|--------|-----------|--------|
| Heartbeat miss | > 3 consecutive | Mark child degraded |
| Error rate increase | > 25% | Trigger rollback investigation |
| Test pass rate drop | Any decrease | Block genome absorption |
| Compliance regression | Any framework | Immediate rollback |

**Error handling:**
- Child heartbeat timeout during verification -> mark child degraded, extend verification window, notify HITL
- Compliance regression detected -> automatic rollback for affected child, block genome absorption, alert ISSO

---

### Step 7: Absorb

Capabilities that pass the 72-hour stability window are absorbed into the genome. Genome version is incremented using semantic versioning.

**Tools:**
- `tools/registry/absorption_engine.py` — Merge capability into genome
- `tools/registry/genome_manager.py` — Version management, content hashing, diff, rollback

**Genome versioning:**
| Change Type | Version Bump | Example |
|-------------|-------------|---------|
| New capability added | Minor | 1.2.0 -> 1.3.0 |
| Capability improved | Patch | 1.3.0 -> 1.3.1 |
| Breaking change | Major | 1.3.1 -> 2.0.0 |

**Output:** Updated genome (content-hashed, SHA-256), version record in DB, audit trail entry.

**Verify:** Genome hash matches expected. Version incremented correctly. All absorbed capabilities traceable to evaluation records.

---

### Cross-Pollination

Capabilities proven in one child can be brokered to other children via the parent. This avoids direct child-to-child communication (which would bypass governance).

**Tool:** `tools/registry/cross_pollinator.py`

**Pipeline:** Child A reports learned behavior -> Parent evaluates (Step 2) -> Parent stages (Step 3) -> HITL approves (Step 4) -> Parent propagates to Child B (Step 5) -> Verify (Step 6)

**Security:** Child-learned behaviors are scanned for prompt injection before cross-pollination (D213, Phase 37 integration). Trust level tagging ensures behaviors are marked as `child`-origin, not `system`-origin.

---

## Outputs

- Updated capability genome (versioned, content-hashed, SHA-256)
- Propagation log entries (append-only, D214)
- Child capability updates (per-child records in `child_capabilities` table)
- Evaluation audit trail (append-only, D6)
- AI telemetry records (72-hour verification window data)
- Genome diff reports (version-to-version changesets)

---

## Error Handling

- If staging tests fail: reject capability, log failure, notify HITL
- If child heartbeat timeout: mark child as degraded, skip propagation to that child
- If compliance regression detected: block propagation, alert ISSO, trigger automatic rollback
- If prompt injection detected in learned behavior: reject, log to `prompt_injection_log`, quarantine source child for review
- If genome absorption fails (hash mismatch): retry once, then escalate to HITL
- If propagation partially fails: do not roll back successful children, report mixed status

---

## Safety & Guardrails

1. No autonomous deployment — all pushes require HITL approval (D200)
2. Compliance preservation — no capability can weaken compliance posture
3. Rollback mandatory — every propagation has documented rollback plan
4. 72-hour stability window before genome absorption (D212)
5. Grandchild prevention — children cannot self-reproduce (D52)
6. Append-only audit — all actions permanently recorded (D6)
7. Air-gap safe — pull-based telemetry, no child outbound required (D210)
8. Budget cap — max 10 propagations per PI (D201)
9. Prompt injection scanning on all child-reported behaviors (Phase 37)
10. Trust level tagging — behaviors tagged system/user/external/child

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D210 | Pull-based telemetry (parent polls children) | Air-gap safe — children in restricted enclaves cannot initiate outbound connections |
| D211 | Git worktree staging isolation | Reuses existing worktree infrastructure (D32), zero-conflict capability testing |
| D212 | 72-hour stability window before absorption | Sufficient time to detect latent failures; configurable per capability risk level |
| D213 | Prompt injection scanning on child-learned behaviors | Children operate in untrusted environments; learned behaviors may contain adversarial content |
| D214 | Append-only propagation log | Consistent with D6 audit trail pattern; full traceability from discovery to absorption |

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Discover | Tools | `innovation_manager.py`, `learning_collector.py`, `telemetry_collector.py` |
| Evaluate | Tools | `capability_evaluator.py` |
| Stage | Tools | `staging_manager.py` |
| Approve | Orchestration | AI (you) + HITL approval workflow |
| Propagate | Tools | `propagation_manager.py` |
| Verify | Tools | `telemetry_collector.py` |
| Absorb | Tools | `absorption_engine.py`, `genome_manager.py` |
| Cross-Pollinate | Tools | `cross_pollinator.py` |
| Scoring weights | Args | `args/innovation_config.yaml` |
| Trust policies | Context | `context/evolutionary/trust_policy.yaml` |
| Evaluation prompts | Hard Prompts | `hardprompts/evolutionary/capability_review.md` |

---

## Related Files

- **Goals:** `goals/agentic_generation.md` (Phase 19 — child app generation), `goals/innovation_engine.md` (Phase 35 — capability discovery), `goals/marketplace.md` (Phase 22 — asset sharing)
- **Tools:** `tools/registry/` (genome manager, evaluator, propagation, telemetry, absorption, cross-pollinator)
- **Args:** `args/innovation_config.yaml` (scoring, thresholds, scheduling)
- **Context:** `context/evolutionary/trust_policy.yaml`
- **Dashboard:** `tools/dashboard/templates/children.html` (child application registry UI)

---

## Changelog

- 2026-02-21: Initial creation — Evolutionary Intelligence goal with 7-step lifecycle, genome management, cross-pollination, safety guardrails, and architecture decisions D210-D214
