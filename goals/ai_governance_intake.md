# CUI // SP-CTI
# AI Governance Intake & Chat Integration

> Phase 50 — Integrate AI governance into RICOAS intake pipeline and multi-stream chat.

---

## Overview

This goal connects AI governance (Phases 48-49) to the two primary user-facing interaction surfaces:

1. **RICOAS Intake Pipeline** — Detect AI governance needs during conversational requirements intake, probe for missing governance pillars, score governance readiness as the 7th readiness dimension
2. **Multi-Stream Chat** — Inject governance advisory messages when AI topics arise, provide real-time governance status via sidebar panel

## Prerequisites

- Phase 48 (AI Transparency) — model cards, system cards, AI inventory, fairness assessment, 4 framework assessors
- Phase 49 (AI Accountability) — oversight plans, CAIO, appeals, incidents, ethics reviews, reassessment scheduling
- Phase 44 (Innovation Adaptation) — multi-stream chat, extension hooks, state tracker

## Intake Integration

### Detection Pipeline

The intake engine detects AI governance signals using keyword matching from `args/ai_governance_config.yaml`:

1. **AI/ML Keywords** — Detect AI-related terms in customer messages (machine learning, neural network, LLM, etc.)
2. **Federal Agency Keywords** — Auto-trigger governance for federal customers (DoD, DHS, HHS, etc.)
3. **6 Governance Pillars** — Detect mentions of specific governance areas:
   - `inventory` — AI system registration, asset tracking
   - `transparency` — Model cards, system cards, explainability
   - `oversight` — Human oversight, CAIO, accountability
   - `risk_management` — Impact assessment, risk mitigation
   - `fairness` — Bias testing, equity, disparate impact
   - `monitoring` — Reassessment, incident response, drift detection

### Probe Questions

When AI governance is detected but specific pillars are missing, the intake engine generates targeted probe questions:

```
inventory: "Do you have an inventory of AI/ML systems that will be used or built?"
transparency: "Will you need model cards or system documentation for AI transparency?"
oversight: "Who will serve as the responsible AI official (CAIO) for oversight?"
risk_management: "Has an algorithmic impact assessment been planned for AI components?"
fairness: "Are there fairness or bias testing requirements for AI decision-making?"
monitoring: "What ongoing monitoring and reassessment cadence is needed for AI systems?"
```

### 7th Readiness Dimension

The `ai_governance_readiness` dimension (D323) checks 6 components against the database:
- `inventory_registered` — AI use case inventory exists
- `model_cards_present` — Model cards documented
- `oversight_plan_exists` — Human oversight plan registered
- `impact_assessment_done` — Algorithmic impact assessment completed
- `caio_designated` — Chief AI Officer designated
- `transparency_frameworks_selected` — AI frameworks selected in compliance detection

Weights configurable in `args/ricoas_config.yaml` (default: 0.10 of overall readiness).

## Chat Integration

### Extension Hook Pattern

The `010_ai_governance_chat.py` builtin extension hooks into `chat_message_after`:

1. Scan assistant response for AI keywords
2. Apply cooldown (default: 5 turns between advisories)
3. Check governance gaps for the project via DB queries
4. Inject advisory message with highest-priority gap

### Governance Sidebar

The unified chat page includes a collapsible "Gov" sidebar showing:
- AI Transparency stats (inventory count, model cards, system cards)
- Accountability stats (oversight plans, CAIO designations, open appeals, ethics reviews, reassessments)

### Advisory Messages

Advisory messages appear as system messages with `content_type="governance_advisory"` and are styled with purple left-border in the chat UI.

## Configuration

**Primary config:** `args/ai_governance_config.yaml`

Sections:
- `intake_detection` — Keywords by pillar, auto-trigger rules, probe questions
- `chat_governance` — Advisory cooldown, AI keyword list, priority order
- `readiness_dimension` — Component weights for 7th readiness dimension
- `auto_trigger_rules` — Federal agency list, impact level threshold

## Security Gate

`ai_governance` gate in `args/security_gates.yaml`:
- **Blocking:** CAIO not designated for rights-impacting AI, oversight plan missing for high-impact AI, impact assessment not completed
- **Warning:** Model card missing, fairness assessment stale, reassessment overdue, AI inventory incomplete

## Architecture Decisions

- **D322:** AI governance keyword detection reuses existing `_detect_*_signals()` intake pattern (D119, D125)
- **D323:** AI governance readiness is the 7th readiness dimension (extends D21 weighted average)
- **D324:** Extension builtins stored in `tools/extensions/builtins/` with numbered Python files (Agent Zero pattern)
- **D325:** `chat_message_after` hook activated for governance advisory injection
- **D326:** Governance sidebar fetches from existing transparency/accountability APIs (no new endpoints)
- **D327:** Advisory messages are non-blocking system messages (advisory-only, not enforcing)
- **D328:** Single config file (`args/ai_governance_config.yaml`) for all governance integration settings
- **D329:** No new database tables — reuses Phase 48/49 tables for all governance checks
- **D330:** `ai_governance` security gate is separate from `ai_transparency` and `ai_accountability` gates

## Tools

| Tool | Purpose |
|------|---------|
| `tools/requirements/ai_governance_scorer.py` | Score AI governance readiness (6 components) |
| `tools/extensions/builtins/010_ai_governance_chat.py` | Chat extension: AI keyword detection + advisory injection |
| `args/ai_governance_config.yaml` | Configuration for intake detection, chat governance, readiness weights |

## Tests

```bash
pytest tests/test_ai_governance_intake.py -v       # 37 tests — intake detection, scorer, 7th dimension
pytest tests/test_ai_governance_chat_extension.py -v  # 28 tests — chat advisory, cooldown, extension loading
```

## Verification

1. Start intake session for AI project → governance signals detected, probe questions generated
2. Score readiness → 7 dimensions shown (including ai_governance_readiness)
3. Open chat, discuss AI topics → governance advisory appears after cooldown
4. Click "Gov" button → sidebar shows transparency + accountability stats
5. Run `pytest tests/test_ai_governance_intake.py tests/test_ai_governance_chat_extension.py -v` → all 65 tests pass
