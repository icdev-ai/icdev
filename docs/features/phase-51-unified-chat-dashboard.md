# Phase 51 — Unified Chat Dashboard

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 51 |
| Title | Unified Chat Dashboard |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 44 (Innovation Adaptation — multi-stream chat, extensions), Phase 50 (AI Governance — chat advisory, sidebar) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-24 |

---

## 1. Problem Statement

Prior to Phase 51, the ICDEV dashboard had two separate chat pages serving overlapping purposes:

- **`/chat`** — RICOAS requirements intake chat with session-based conversations, wizard parameter passing, readiness gauges, COA generation, BDD preview, and build pipeline sidebar. Tightly coupled to the intake engine.
- **`/chat-streams`** — Phase 44 multi-stream parallel chat with context sidebar, message stream, intervention controls, and dirty-tracking state queries. General-purpose agent chat without RICOAS features.

This split created user confusion (which page to use?), duplicated frontend code (both pages rendered message streams), and made Phase 50's governance sidebar difficult to maintain across two templates. The separation also meant operators could not seamlessly transition from a general agent conversation into a requirements intake session or vice versa within the same interface.

Phase 51 merges both pages into a single unified `/chat` page that supports both workflows through a context-aware 3-pane layout. General chat contexts use the multi-stream backbone. Intake-linked contexts activate RICOAS sidebar features. The governance sidebar is available on all contexts. The separate `/chat-streams` page, its JavaScript, its E2E test, and its portal template are removed.

---

## 2. Goals

1. Merge `/chat` (RICOAS intake) and `/chat-streams` (multi-stream) into a single unified `/chat` page
2. Preserve all RICOAS sidebar features: readiness gauge, compliance frameworks, complexity indicator, elicitation techniques, BDD preview, COA cards, build pipeline, export actions
3. Preserve all multi-stream features: context creation/switching/closing, intervention bar, dirty-tracking polling, message roles (user, assistant, system, intervention, governance_advisory)
4. Add toggle buttons for RICOAS and Governance sidebars in the chat header
5. Support intake-linked contexts via checkbox in the "New Chat" modal
6. Remove the deprecated `/chat-streams` route, template, JavaScript, E2E spec, and portal template
7. Mirror the unified chat to the SaaS portal as a tenant-scoped page
8. Update the E2E test spec to cover the unified interface

---

## 3. Architecture

```
                     Unified /chat Page (Phase 51)
          ┌──────────────────────────────────────────────┐
          │                  Stats Bar                    │
          │  Active Chats | Processing | Queued | Total   │
          ├─────────┬─────────────────────┬──────────────┤
          │ Context │   Message Stream    │    Right     │
          │ Sidebar │                     │   Sidebar    │
          │         │ user / assistant /  │              │
          │ + New   │ system / interven-  │  RICOAS      │
          │ ctx-1   │ tion / governance_  │  (toggle)    │
          │ ctx-2   │ advisory            │              │
          │ ctx-3   │                     │  Governance  │
          │         │                     │  (toggle)    │
          │         ├─────────────────────┤              │
          │         │ Intervention Bar    │              │
          │         ├─────────────────────┤              │
          │         │ Input + Upload +    │              │
          │         │ Send                │              │
          ├─────────┴─────────────────────┴──────────────┤
          │            New Context Modal                  │
          │  Title | Model | Intake Checkbox | Prompt     │
          └──────────────────────────────────────────────┘

Backend:
  /api/chat/*    → ChatManager (Phase 44, D257-D260)
  /api/intake/*  → IntakeEngine (RICOAS)
  /api/ai-transparency/stats → Governance sidebar
  /api/ai-accountability/stats → Governance sidebar
```

### Key Design Principles

- **Context-aware layout** — Right sidebar shows/hides based on context type and toggle state
- **Backward compatible** — Existing `/chat/<session_id>` routes still work for RICOAS session URLs
- **No new backend** — Reuses Phase 44 chat API and existing RICOAS intake API
- **Wizard passthrough** — Getting Started wizard parameters are passed to the unified page and auto-create an intake context

---

## 4. Page Layout

### Left Panel: Context Sidebar (280px)
- **Header**: "Contexts" label + "New" button
- **Context list**: Scrollable list of all contexts (active and closed)
- Each context shows title, status badge, and click-to-select behavior

### Center Panel: Chat Area (flex)
- **Header**: Context title, status badge, RICOAS toggle, Gov toggle, Close button
- **Message stream**: Scrollable message list with role-specific styling
  - `user` — right-aligned, blue background
  - `assistant` — left-aligned, default background
  - `system` — centered, muted styling
  - `intervention` — yellow left border
  - `governance_advisory` — purple left border (D327)
- **Intervention bar**: Appears during agent processing with text input and "Intervene" button (D265-D267)
- **Input area**: Message text input, file upload button (intake contexts only), Send button

### Right Panel: Sidebars (280px, toggleable)

**RICOAS Sidebar** (visible for intake-linked contexts):
- Compliance framework tags
- Project complexity gauge with recommendation
- Readiness score SVG arc gauge with 5 dimension bars
- Requirements/documents/turns stats
- Elicitation technique chips (activate/deactivate)
- BDD scenario preview
- COA cards with selection
- Action buttons: Generate Plan, Export Requirements
- Post-export actions: Generate Application, Run Simulation, View Requirements, Generate PRD, Validate PRD
- Build pipeline phase progress

**Governance Sidebar** (available on all contexts):
- AI Transparency stats: AI Systems, Model Cards, System Cards
- AI Accountability stats: Oversight Plans, CAIO Designations, Open Appeals, Ethics Reviews, Reassessments
- Auto-refreshes on `governance_advisory` state changes

---

## 5. JavaScript Architecture

The unified [chat.js](tools/dashboard/static/js/chat.js) (~1515 LOC) organizes into 18 sections:

| Section | Purpose |
|---------|---------|
| Config & State | Global variables, context maps, framework names, poll interval |
| Utility Helpers | HTML escaping, localStorage, API fetch helpers |
| Multi-Stream Backbone | Context CRUD, switching, lifecycle management, sidebar visibility |
| Messaging | Routes to `/api/chat` or `/api/intake` depending on context type |
| File Upload | Single/batch document upload with extraction (intake contexts only) |
| Polling | Dirty-tracking state queries with version comparison (D268-D270) |
| Readiness | SVG arc gauge rendering, 5 dimension progress bars |
| Complexity | Project complexity scoring and recommendation text |
| Framework Tags | Compliance framework badge rendering |
| Elicitation | Technique loading, chip rendering, activate/deactivate |
| BDD Preview | Gherkin scenario rendering from intake session data |
| Export & Actions | Plan generation, COA selection, PRD export, post-export workflows |
| COA Rendering | COA card display with tier badges (Speed/Balanced/Comprehensive) |
| Build Pipeline | Phase progress rendering with polling |
| Sidebar Management | Show/hide RICOAS and governance sidebars based on toggles |
| Rendering | Message rendering, context list updates, intervention bar state |
| Intake Bridge | Create/load intake-linked contexts, RICOAS session mapping |
| Event Bindings | Modal handlers, message send, file upload, keyboard shortcuts |

---

## 6. New Context Modal

The "New Chat" modal provides:
- **Title**: Free-text context name
- **Agent Model**: Dropdown (Sonnet default, Opus, Haiku)
- **Link to Requirements Intake (RICOAS)**: Checkbox that creates a linked intake session
- **System Prompt**: Optional custom system prompt for the context

When the RICOAS checkbox is selected, context creation calls both the chat API (create context) and the intake API (create session), then stores the mapping in `localStorage` under `icdev_intake_map`.

---

## 7. Removed Files

| File | Was | Replacement |
|------|-----|-------------|
| `tools/dashboard/templates/chat_streams.html` | Phase 44 multi-stream page | Merged into `chat.html` |
| `tools/dashboard/static/js/chat_streams.js` | Phase 44 multi-stream JS | Merged into `chat.js` |
| `.claude/commands/e2e/chat_streams.md` | Phase 44 E2E test spec | Replaced by `e2e/chat.md` |
| `tools/saas/portal/templates/chat_streams.html` | Phase 44 portal template | Replaced by `chat.html` |

---

## 8. Modified Files

| File | Change |
|------|--------|
| `tools/dashboard/templates/chat.html` | Rewritten: 3-pane layout, context modal, RICOAS sidebar, governance sidebar, intervention bar (~332 LOC) |
| `tools/dashboard/static/js/chat.js` | Rewritten: merged multi-stream backbone + RICOAS features + governance (~1515 LOC) |
| `tools/dashboard/templates/base.html` | Removed `/chat-streams` nav link |
| `tools/dashboard/app.py` | Removed `/chat-streams` route |
| `tools/saas/portal/app.py` | Updated chat route docstring |

---

## 9. New Files

| File | LOC | Purpose |
|------|-----|---------|
| `tools/saas/portal/templates/chat.html` | ~71 | Portal-scoped unified chat (3-pane layout, tenant context) |
| `.claude/commands/e2e/chat.md` | ~54 | E2E test spec for unified chat interface |

---

## 10. Portal Integration

The SaaS portal receives a tenant-scoped version at `/chat` with:
- 4-metric stats bar (Active Chats, Processing, Queued, Total)
- Left sidebar: context list with "New" button
- Center pane: message stream, intervention bar, input area
- The portal template uses `portal_base.html` and portal-specific CSS variables
- Chat API calls are scoped to the tenant via `g.tenant_id`

---

## 11. E2E Test Coverage

The unified E2E spec (`.claude/commands/e2e/chat.md`) covers:
1. Page loads without errors at `/chat`
2. CUI banners present (top and bottom)
3. Context sidebar visible with "New Chat" button
4. Context creation via modal
5. New context appears in sidebar list
6. Message input enabled after context selection
7. Message send and display in stream
8. Navigation preserves context state
9. Screenshots at key interaction points

---

## 12. Testing

```bash
# Chat manager tests (Phase 44 backbone)
pytest tests/test_chat_manager.py -v              # 22 tests

# AI governance chat extension tests (Phase 50)
pytest tests/test_ai_governance_chat_extension.py -v  # 28 tests

# E2E test (requires running dashboard)
python tools/testing/e2e_runner.py --test-file .claude/commands/e2e/chat.md
```

---

## 13. Related

- [Phase 44: Innovation Adaptation](phase-44-innovation-adaptation.md) — Multi-stream chat manager, extension hooks, dirty-tracking
- [Phase 50: AI Governance Integration](phase-50-ai-governance-intake-chat.md) — Governance advisory, sidebar, extension handlers
- [Phase 52: Code Intelligence](phase-52-code-intelligence.md) — Depends on Phase 51 dashboard patterns
