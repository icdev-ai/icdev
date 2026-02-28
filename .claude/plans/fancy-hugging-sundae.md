# Phase 50: AI Governance Integration into Intake Pipeline & Multi-Stream Chat

## Context

The AI Transparency tools (model cards, system cards, AI inventory, confabulation detection, fairness assessment, 4 framework assessors) and AI Accountability tools (oversight plans, CAIO designation, appeal tracking, incident response, ethics reviews, reassessment scheduling, cross-framework audit) exist as standalone CLI utilities and dashboard pages. They are never invoked during requirements intake or agent chat conversations.

Meanwhile, the RICOAS intake pipeline already integrates DevSecOps detection, MOSA detection, and dev profile detection using a consistent pattern: keyword detection in YAML config, probe questions for missing dimensions, metadata storage in conversation turns, and readiness scoring. Multi-stream chat (D257-D260) dispatches `CHAT_MESSAGE_BEFORE/AFTER` extension hooks but has no actual extension handlers loaded.

**Problem:** AI governance is planned post-deployment instead of during requirements gathering. A project can go through the entire intake pipeline, COA generation, and decomposition without anyone asking whether the system uses AI/ML or what governance is needed.

**Goal:** Make AI governance a first-class citizen of both the intake pipeline and multi-stream chat by replicating the existing detection pattern and activating the extension hook system.

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D322 | AI governance signals detected via keyword matching in intake pipeline | Replicates DevSecOps/MOSA/dev profile detection pattern (deterministic, no LLM required, YAML-driven) |
| D323 | AI governance readiness as 7th scoring dimension in readiness_scorer.py | Existing `devsecops_readiness` was configured (weight 0.12) but never coded; Phase 50 adds both, completing a 7-dimension model |
| D324 | Extension handlers live in `tools/extensions/builtins/` directory | extension_config.yaml already lists this as a scan directory; Phase 50 creates the first actual handler files |
| D325 | `COMPLIANCE_CHECK_AFTER` extension point activated in chat agent loop | Defined in ExtensionPoint enum but never dispatched; Phase 50 fires it after each assistant response |
| D326 | Governance sidebar in chat_streams refreshed via state_tracker dirty updates | Reuses existing `ai_transparency_api` and `ai_accountability_api` endpoints — no new API needed |
| D327 | Governance interventions are advisory system messages, not blocking | Keeps chat flowing while surfacing gaps; blocking reserved for security gate |
| D328 | Config stored in `args/ai_governance_config.yaml` | Single file for intake keywords, chat thresholds, readiness weights, auto-triggers (D26 pattern) |
| D329 | No new DB tables required | Reuses all existing transparency/accountability tables; intake metadata uses existing JSON column |
| D330 | Security gate `ai_governance` added to security_gates.yaml | Blocking: no CAIO for rights-impacting AI, no oversight plan for high-impact AI, no impact assessment |

---

## Implementation

### Step 1: Config File

**Create:** `args/ai_governance_config.yaml`

Keywords by 6 governance pillars (ai_inventory, model_documentation, human_oversight, impact_assessment, transparency, accountability), auto-trigger rules (federal agencies, AI/ML mention), probe questions for missing pillars, chat advisory cooldown, readiness dimension weights.

### Step 2: AI Governance Scorer

**Create:** `tools/requirements/ai_governance_scorer.py` (~100 LOC)

Helper called by readiness_scorer.py. Checks 6 components against DB: inventory count, model cards, oversight plans, impact assessments, CAIO designation, transparency framework selection. Returns weighted score (0.0-1.0) with gap list.

### Step 3: Intake Engine Detection

**Edit:** `tools/requirements/intake_engine.py`

- Add `_detect_ai_governance_signals(text, session_data)` function (~60 LOC) after `_detect_dev_profile_signals()` — follows exact same pattern as `_detect_mosa_signals()` at line 1678
- Call it from `process_turn()` alongside existing signal detections
- Add fallback response block with probe questions for missing governance pillars
- Store `ai_governance_detected` and `ai_governance_pillars_detected` in conversation metadata JSON
- Add `ai_governance` topic to `_analyze_conversation_coverage()`

### Step 4: Readiness Scorer Update

**Edit:** `tools/requirements/readiness_scorer.py`

- Import and call `score_ai_governance_readiness()` for the 7th dimension
- Also implement the missing `devsecops_readiness` dimension (was configured but never coded)
- Update overall calculation to include all 7 dimensions with rebalanced weights

**Edit:** `args/ricoas_config.yaml`

Rebalance readiness_weights to sum to 1.0 with 7 dimensions:
- completeness: 0.20, clarity: 0.20, feasibility: 0.16, compliance: 0.12, testability: 0.12, devsecops_readiness: 0.10, ai_governance_readiness: 0.10

### Step 5: Extension System Activation

**Create:** `tools/extensions/builtins/__init__.py` (empty package init)

**Create:** `tools/extensions/builtins/010_ai_governance_chat.py` (~150 LOC)

Extension handler for `chat_message_after` hook. Detects AI keywords in assistant responses, checks governance artifact status for the project, injects advisory messages when gaps found (with cooldown to avoid spam). Exports `EXTENSION_HOOKS` dict for file scanner registration.

**Edit:** `tools/extensions/extension_manager.py`

Add `_auto_load_builtins()` method to scan `tools/extensions/builtins/*.py` files on init, loading EXTENSION_HOOKS dicts and registering handlers. Add `_load_file()` helper using `importlib.util`.

### Step 6: Chat Manager Integration

**Edit:** `tools/dashboard/chat_manager.py`

- Modify existing `_dispatch_hook("chat_message_after", ...)` call to capture return value
- Check for `governance_advisory` key in hook result
- When present, insert a `role='system'`, `content_type='governance_advisory'` message
- Call `_mark_dirty()` with `"governance_advisory"` change type for state push

### Step 7: Chat Streams UI

**Edit:** `tools/dashboard/templates/chat_streams.html`

- Add collapsible right sidebar panel for governance status (transparency + accountability stats)
- Add "Gov" toggle button in chat header bar
- Add JavaScript to fetch from existing `/api/ai-transparency/stats` and `/api/ai-accountability/stats` endpoints
- Add distinct styling for governance advisory messages (purple accent border)
- Refresh governance sidebar when state_tracker reports `governance_advisory` change

### Step 8: Security Gate

**Edit:** `args/security_gates.yaml`

Add `ai_governance` gate after `observability_xai`:
- Blocking: caio_not_designated_for_rights_impacting_ai, oversight_plan_missing_for_high_impact_ai, impact_assessment_not_completed
- Warning: model_card_missing, fairness_assessment_stale, reassessment_overdue, ai_inventory_incomplete
- Thresholds: caio_required_for_rights_impacting=true, oversight_plan_required=true, impact_assessment_required=true

### Step 9: Feature Documentation

**Create:** `docs/features/phase-48-ai-transparency.md` (~250 LOC)

Feature doc covering: model cards, system cards, AI inventory, confabulation detection, fairness assessment, 4 framework assessors (OMB M-25-21, M-26-04, NIST AI 600-1, GAO-21-519SP), transparency audit, dashboard page, portal page, REST API, MCP tools, security gate.

**Create:** `docs/features/phase-49-ai-accountability.md` (~250 LOC)

Feature doc covering: oversight plans, CAIO designation, appeal tracking, ethics reviews, incident response, reassessment scheduling, impact assessment, cross-framework accountability audit, assessor fixes (14 checks across 4 assessors), dashboard page, portal page, REST API, MCP tools, security gate.

**Create:** `docs/features/phase-50-ai-governance-intake-chat.md` (~200 LOC)

Feature doc covering: intake keyword detection, governance probe questions, 7-dimension readiness scoring, extension hook activation, chat governance advisory, governance sidebar, security gate, architecture decisions D322-D330.

### Step 10: Tests

**Create:** `tests/test_ai_governance_intake.py` (~32 tests)

- Keyword detection (ai_inventory, model_documentation, human_oversight, etc.)
- Federal agency auto-trigger
- Probe question generation for missing pillars
- Metadata storage in conversation turns
- Readiness score with/without governance artifacts
- 7-dimension overall calculation
- Config file loading

**Create:** `tests/test_ai_governance_chat_extension.py` (~26 tests)

- Extension handler file loading from builtins directory
- AI keyword detection in chat responses
- Governance advisory generation when gaps exist
- Advisory cooldown enforcement
- No advisory on non-AI discussions
- Extension manager auto-load

### Step 11: Documentation Updates

**Edit:** `CLAUDE.md` — Add D322-D330, `ai_governance` gate, test commands, `args/ai_governance_config.yaml` to args table, goal entry, readiness dimension documentation

**Edit:** `goals/manifest.md` — Add `goals/ai_governance_intake.md` entry

**Edit:** `tools/manifest.md` — Add `ai_governance_scorer.py`, `010_ai_governance_chat.py`

**Create:** `goals/ai_governance_intake.md` (~80 LOC) — Goal file for the workflow

---

## Files Summary

| # | File | Action | LOC (est) |
|---|------|--------|-----------|
| 1 | `args/ai_governance_config.yaml` | **Create** | ~90 |
| 2 | `tools/requirements/ai_governance_scorer.py` | **Create** | ~100 |
| 3 | `tools/requirements/intake_engine.py` | Edit — add detection + probes | ~80 |
| 4 | `tools/requirements/readiness_scorer.py` | Edit — add 7th dimension | ~50 |
| 5 | `args/ricoas_config.yaml` | Edit — rebalance weights | ~5 |
| 6 | `tools/extensions/builtins/__init__.py` | **Create** | ~2 |
| 7 | `tools/extensions/builtins/010_ai_governance_chat.py` | **Create** | ~150 |
| 8 | `tools/extensions/extension_manager.py` | Edit — add auto-load | ~50 |
| 9 | `tools/dashboard/chat_manager.py` | Edit — capture advisory | ~20 |
| 10 | `tools/dashboard/templates/chat_streams.html` | Edit — governance sidebar | ~80 |
| 11 | `args/security_gates.yaml` | Edit — add ai_governance gate | ~15 |
| 12 | `docs/features/phase-48-ai-transparency.md` | **Create** | ~250 |
| 13 | `docs/features/phase-49-ai-accountability.md` | **Create** | ~250 |
| 14 | `docs/features/phase-50-ai-governance-intake-chat.md` | **Create** | ~200 |
| 15 | `tests/test_ai_governance_intake.py` | **Create** | ~250 |
| 16 | `tests/test_ai_governance_chat_extension.py` | **Create** | ~200 |
| 17 | `goals/ai_governance_intake.md` | **Create** | ~80 |
| 18 | `CLAUDE.md` | Edit — multiple sections | ~40 |
| 19 | `goals/manifest.md` | Edit — add entry | ~3 |
| 20 | `tools/manifest.md` | Edit — add entries | ~5 |
| **Total** | **10 new + 10 modified** | | **~1,920** |

---

## Dependency Sequencing

```
Step 1 (config) ──┬──> Step 2 (scorer)
                  └──> Step 3 (intake detection)
Step 2 ───────────> Step 4 (readiness update)
Step 4 ───────────> Step 5 (ricoas weights)
                    Step 6 (builtins init, no deps)
Step 6 ───────────> Step 7 (chat extension handler)
Step 7 ───────────> Step 8 (extension manager auto-load)
Step 8 ───────────> Step 9 (chat manager integration)
Step 9 ───────────> Step 10 (chat streams UI)
                    Step 11 (security gate, no deps)
                    Step 12-14 (feature docs, no code deps)
Steps 1-11 ──────> Step 15-16 (tests)
Steps 1-16 ──────> Step 17-20 (goal + manifest + CLAUDE.md updates)
```

Steps 1, 6, 11, 12-14 can run in parallel.

---

## Verification

1. **Intake detection**: Create intake session, send "We need a machine learning model for fraud detection with automated decisions", verify `ai_governance_detected: true` and pillars detected in response metadata
2. **Probe questions**: Send minimal AI text, verify probe questions for missing pillars appear
3. **Readiness scoring**: `python tools/requirements/readiness_scorer.py --session-id <id> --json` — verify `ai_governance_readiness` in dimensions
4. **Chat extension loads**: Start dashboard, create chat context with project_id, send message about "AI model", verify governance advisory system message appears
5. **Governance sidebar**: Open `/chat-streams`, click "Gov" button, verify stats load from transparency/accountability APIs
6. **Security gate**: Verify `ai_governance` gate in security_gates.yaml is syntactically valid
7. **Tests pass**: `pytest tests/test_ai_governance_intake.py tests/test_ai_governance_chat_extension.py -v`
8. **No regressions**: `pytest tests/ --co -q` — verify 0 collection errors
9. **Feature docs**: Verify `docs/features/phase-48-ai-transparency.md`, `phase-49-ai-accountability.md`, `phase-50-ai-governance-intake-chat.md` exist and follow project format
10. **Claude dir validator**: `python tools/testing/claude_dir_validator.py --json` — exit code 0

---

## Critical Files Reference

| File | Why It Matters |
|------|---------------|
| `tools/requirements/intake_engine.py` | Core intake pipeline — add `_detect_ai_governance_signals()` following `_detect_mosa_signals()` at line 1678 |
| `tools/requirements/readiness_scorer.py` | Readiness scoring — add 7th dimension following existing 5-dimension pattern at line 99 |
| `tools/dashboard/chat_manager.py` | Chat agent loop — capture `_dispatch_hook` return value and check for `governance_advisory` at line 426 |
| `tools/extensions/extension_manager.py` | Extension system — add `_auto_load_builtins()` to load handler files from `tools/extensions/builtins/` |
| `tools/dashboard/templates/chat_streams.html` | Chat UI — add governance sidebar panel reusing existing API endpoints |
| `args/ricoas_config.yaml` | Intake config — rebalance readiness_weights for 7 dimensions |
| `args/security_gates.yaml` | Security gates — add `ai_governance` gate after `observability_xai` |
