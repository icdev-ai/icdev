# Plan — Network Migration Config Mapping with AI Assist + HITL Yes/No

## Context

URL: `http://localhost:5050/migration-canvas/network-migration/nmig-028ce3710f35` already has port mapping, but no **configuration mapping from source device to target device**. The user is a Network/System/Software/Data/AI Architect who wants to safely migrate old → new and needs:

1. **Split-screen** config mapping with AI assist to see proposed changes.
2. **Tooltips** that help HITL understand AI-proposed changes.
3. For Network Architects: see the **entire current router/switch configuration mapped to the new device**, regardless of vendor.
4. **Yes/no questions** so the AI can make better recommendations.

## Existing state

- `tools/migration_canvas/network_migration.py` already parses source configs vendor-agnostically (`parse_source_config`), fetches hardware profiles (`fetch_hardware_profiles`), generates port maps, and converts configs line-by-line (`convert_config`).
- The wizard (`tools/dashboard/templates/migration_canvas/network_wizard.html`) has 7 steps; Step 5 is already a split-pane **diff** of source vs generated target config.
- There are already AI routes: `/api/network-migration/<sid>/ai-recommend` and `/api/network-migration/<sid>/ai-assist`.
- There is no persisted **section-level config mapping** table.

## Decisions made with the user (yes/no answers)

1. **Add a new wizard step** — insert as Step 5 "Config Mapping" and push the current Step 5 (Config Preview) to Step 6.
2. **One-screen HITL review** — all AI proposals on one screen with per-row Approve / Reject + tooltip.
3. **Section-level mapping** — capture entire configuration sections (system, protocols, interfaces, routing policies, firewall filters, etc.). Interface renames stay in the existing port map.
4. **Persist proposals** — new `mc_net_config_map` table so reviewers can leave and return.

## Proposed implementation

### 1. Database schema change

Add to `tools/migration_canvas/db/init_db.py` under `NETWORK_MIGRATION_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS mc_net_config_map (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    src_section_type    TEXT NOT NULL,      -- e.g. system, interfaces, bgp, ospf, firewall, mgmt
    src_stanza_text     TEXT NOT NULL,      -- original source stanza (first ~4k chars)
    src_lines_json      TEXT DEFAULT '[]',  -- array of {line_no, text}
    tgt_section_type    TEXT DEFAULT '',    -- target classification
    tgt_stanza_text     TEXT DEFAULT '',    -- AI-proposed target stanza
    mapping_action      TEXT NOT NULL CHECK(mapping_action IN ('direct','rename','merge','split','remove','manual','skip')) DEFAULT 'direct',
    confidence          REAL DEFAULT 0,
    ai_rationale        TEXT DEFAULT '',    -- tooltip text
    ai_question         TEXT DEFAULT '',    -- optional yes/no question this row depends on
    ai_question_key     TEXT DEFAULT '',    -- stable key for answers
    status              TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','skipped','needs_review')) DEFAULT 'pending',
    reviewer_note       TEXT DEFAULT '',
    applied_to_target   INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgmap_session ON mc_net_config_map(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgmap_status ON mc_net_config_map(status);
```

Add a companion table for yes/no answers:

```sql
CREATE TABLE IF NOT EXISTS mc_net_config_questions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    question_key    TEXT NOT NULL,
    question_text   TEXT NOT NULL,
    default_answer  INTEGER DEFAULT NULL,   -- 1 = yes, 0 = no, NULL = unanswered
    user_answer     INTEGER DEFAULT NULL,
    ai_relevance    TEXT DEFAULT '',         -- how answer affects proposals
    UNIQUE(session_id, question_key)
);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgq_session ON mc_net_config_questions(session_id);
```

### 2. Backend — `tools/migration_canvas/network_migration.py`

Add functions:

- `generate_config_map_questions(session_id) -> dict`  
  Uses parsed source config + target model/vendor to ask 4–6 yes/no questions, e.g.:
  - "Source hostname differs from target hostname. Keep target hostname as currently planned?"
  - "Preserve existing VRF/routing-instance names on the target?"
  - "Migrate all firewall/filter stanzas, or drop deprecated ones?"
  - "Convert Juniper `set` syntax to Cisco/Arista style if vendors differ?"
  - "Keep existing BGP neighbor IPs and ASNs exactly?"
  - "Preserve source management / fxp / mgmt interface configuration?"

- `propose_config_mapping(session_id, answers=None) -> dict`  
  1. Loads source config + parsed sections.
  2. Loads target model/vendor from `mc_net_sessions` / `nc_hardware_profiles`.
  3. Uses deterministic rules + LLM to produce section-level mapping.
  4. Returns rows with `src_section_type`, `src_stanza_text`, `tgt_stanza_text`, `mapping_action`, `confidence`, `ai_rationale`, `ai_question_key`.
  5. Persists rows into `mc_net_config_map` and `mc_net_config_questions`.
  6. Falls back to rule-based mapping when LLM is unavailable.

- `apply_approved_config_map(session_id) -> dict`  
  Re-assembles target config from approved mappings + existing port map, updates `mc_net_sessions.target_config`, and returns the generated config for Step 6 preview.

### 3. Backend — `tools/migration_canvas/blueprint.py`

Add routes under the existing `/migration-canvas` prefix:

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/network-migration/<sid>/config-map/questions` | Get current yes/no question set + answers |
| POST | `/api/network-migration/<sid>/config-map/questions` | Save user answers, regenerate proposals |
| POST | `/api/network-migration/<sid>/config-map/generate` | Trigger AI proposal generation |
| GET | `/api/network-migration/<sid>/config-map` | List persisted mapping rows |
| POST | `/api/network-migration/<sid>/config-map/<mid>/decide` | Approve / reject / skip + note |
| POST | `/api/network-migration/<sid>/config-map/apply` | Apply approved rows to target config |
| GET | `/api/network-migration/<sid>/config-map/export` | Export approved mapping as JSON/CSV |

All routes use existing `@mdc_login_required` decorator.

### 4. Frontend — `tools/dashboard/templates/migration_canvas/network_wizard.html`

- **Re-number steps**: current Step 5 → Step 6, Step 6 → Step 7, Step 7 → Step 8.
- **New Step 5 — Config Mapping**:
  - Header: "Step 5 — Configuration Mapping (AI-assisted)"
  - Top panel: **AI Questions** — 4–6 yes/no toggles. Each question has a tooltip explaining why it matters.
  - Middle action bar: "Generate AI Proposals" button + "Apply Approved to Target Config" button.
  - **Split-pane table**:
    - Left column: source section type + collapsed stanza preview.
    - Right column: AI-proposed target section + collapsed stanza preview.
    - Hover / info icon tooltip showing `ai_rationale`.
    - Per-row buttons: ✅ Approve, ❌ Reject, ⏭️ Skip.
    - Inline textarea for reviewer note.
  - Summary chips: approved / rejected / pending / total.
  - "Next →" only enabled after at least one row is approved or explicitly skipped all.

- **Step 6 (Config Preview)** remains the split-pane diff, but now renders from the **applied target config** produced by the mapping step.

Add CSS classes for the mapping table, tooltips, and yes/no toggles inside the existing `<style>` block.

### 5. AI prompt design

LLM prompt used by `propose_config_mapping`:

```text
You are a senior network architect migrating a {src_vendor} {src_model} to a {tgt_vendor} {tgt_model}.
Source hostname: {hostname}
User answered the following migration preferences:
{answers_json}

Source config sections:
{sections_json}

For each section, propose a target configuration stanza and classify the action as one of:
direct, rename, merge, split, remove, manual, skip.

Return valid JSON only:
{
  "sections": [
    {
      "src_section_type": "...",
      "src_stanza_text": "...",
      "tgt_section_type": "...",
      "tgt_stanza_text": "...",
      "mapping_action": "direct",
      "confidence": 0.92,
      "ai_rationale": "Tooltip explanation for HITL reviewer",
      "ai_question_key": "..."
    }
  ],
  "questions": [
    {"question_key":"...", "question_text":"...", "default_answer":1, "ai_relevance":"..."}
  ]
}
```

Confidence threshold for auto-approval suggestion: ≥ 0.85. Rows below 0.70 default to `needs_review`.

### 6. Deterministic fallback

When LLM is unavailable, generate mappings using existing functions:

- Interface sections → use existing port map (`_generate_port_map`).
- Deprecated patterns → `remove` using `_get_deprecated_patterns()`.
- Hostname/system → `direct` if same vendor, `manual` if different vendor.
- Protocol stanzas (BGP/OSPF/IS-IS/MPLS) → `manual` with note "Verify syntax conversion".
- Firewall/filter → `manual` if vendor differs.

### 7. Testing

Add tests in `tests/test_migration_canvas_network.py` (or new `tests/test_mc_config_map.py`):

- `test_generate_config_map_questions` — returns expected question keys.
- `test_propose_config_mapping_persists_rows` — DB contains rows after generation.
- `test_approve_and_apply_mapping` — approved rows produce a non-empty target config.
- `test_reject_mapping_does_not_apply` — rejected rows excluded from target config.
- `test_cross_vendor_mapping_flags_manual` — different vendors produce `manual` or `remove` actions.

Run existing test suite after changes:

```bash
pytest tests/test_migration_canvas*.py -v --tb=short
```

### 8. Coherence / registration updates

- Add new table names to `tests/conftest.py` `MINIMAL_ICDEV_SCHEMA` if the test harness rebuilds schema.
- No new dashboard page required — it extends the existing wizard; however, update `.claude/commands/start.md` Pages line if it lists wizard routes.
- No new append-only table — `mc_net_config_map` is mutable draft data.
- Run `python tools/dx/companion.py --sync --write --json` after code changes.
- Run `python tools/workflow/coherence_checker.py --all --fix --gate` before merge.

### 9. V&V plan

1. Start dashboard: `python tools/dashboard/app.py`
2. Open `http://localhost:5050/migration-canvas/network-migration/new`
3. Upload a sample router config (Juniper MX → Cisco ASR or same-vendor MX → MX).
4. Complete Steps 1–4 (device info + port mapping).
5. On new Step 5:
   - Answer yes/no questions.
   - Click "Generate AI Proposals".
   - Verify tooltips appear on hover.
   - Approve/reject rows.
   - Click "Apply Approved to Target Config".
6. Proceed to Step 6 — verify the generated target config reflects approved mappings.
7. Run Playwright E2E snapshot via `python tools/testing/e2e_runner.py --run-all` or targeted route smoke.

## Success criteria

- [ ] New Step 5 renders with yes/no questions and split-pane mapping table.
- [ ] AI proposals include tooltips explaining each recommendation.
- [ ] Approved/rejected decisions persist across page reload.
- [ ] "Apply Approved" produces a target config visible in Step 6.
- [ ] Cross-vendor migrations surface `manual` actions with clear rationale.
- [ ] All existing network migration tests still pass.
- [ ] New tests cover question generation, persistence, approve/reject, and apply.
- [ ] Coherence checker passes with zero high-severity findings.

## Out of scope

- Live config push to devices (remains manual / out-of-band).
- Automatic rollback execution (remains runbook-driven).
- Server/application migration mapping (separate wizard; not touched).
