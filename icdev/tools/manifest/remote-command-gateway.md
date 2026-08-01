# Remote Command Gateway (Phase 28 — D133-D140)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Remote Command Gateway (Phase 28 — D133-D140)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gateway Agent | tools/gateway/gateway_agent.py | Remote command reception across channels (Telegram, Slack, Teams, Mattermost, Skype, GitHub, GitLab, internal chat, **Email**), 8-gate security chain, IL-aware response filtering, agent-mode (sag-gw-01) | --port 8458 | Flask server |
| User Binder | tools/gateway/user_binder.py | Pre-provision user bindings (air-gapped mode), binding ceremony, revocation | --provision, --list, --revoke, --json | Binding records |
| Email Adapter | tools/gateway/adapters/email_channel.py | Email channel (sag-gw-02) — **stdlib** `imaplib` poll + `smtplib` send, **no new dependency**, air-gap/on-prem safe. `poll_once()` fetches UNSEEN → normalises → `parse_webhook` → `CommandEnvelope` through the **identical** security chain; sender identity enforced by the identity-binding gate on `From`. Threaded via In-Reply-To; RFC 3834 auto-submitted → `is_bot`. `enabled:false` by default. Discord was evaluated and **rejected** (discord.py heavyweight async dep, not air-gap capable) — see `docs/spikes/sag-gw-02-discord-email-adapters.md`. | (library / config-driven) | Command envelopes / SMTP replies |
| Gateway Agent | tools/gateway/gateway_agent.py | Remote command reception from 5 channels (Telegram, Slack, Teams, Mattermost, internal chat), 8-gate security chain, IL-aware response filtering | --port 8458 | Flask server |


## Remote Command Gateway (Phase 28)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Command Router | tools/gateway/command_router.py | Route remote commands to ICDEV™ tools | (library) | Routed results |
| Event Envelope | tools/gateway/event_envelope.py | HMAC-signed event envelope (D31) | (library) | Signed events |
| Response Filter | tools/gateway/response_filter.py | IL-aware response classification filter (D135) | (library) | Filtered responses |
| Security Chain | tools/gateway/security_chain.py | 8-gate security chain for remote commands | (library) | Chain results |


## Event → Trigger Routing (DWO / dwo-evt)

The gateway receives external events; `tools/studio/workflow_runner.py` runs DAGs. Nothing bound
the two, so migration **304** (`tools/db/migrations/304_studio_event_tables.sql`) adds the
registry that a routing hop reads. **The schema is merged; the routing hop that consumes it is
not in the tree yet** — see *Current state* below.

### Registry tables (merged — dwo-evt-01-d1)

| Table | Role |
|-------|------|
| `studio_event_sources` | One row per source. `kind` is CHECK-constrained to `gateway_channel｜canvas_bus｜schedule｜manual` — **`gateway_channel` is the kind reserved for this gateway's channels** (Telegram, Slack, Teams, Mattermost, Skype, GitHub, GitLab, internal chat, Email). Plus `config_json`, `enabled`. |
| `studio_workflow_triggers` | "When an event of type X matching filter Y arrives on source S, start workflow Z." FK to `studio_event_sources(source_id)` and `studio_workflows(workflow_id)`; carries `event_type`, `filter_json`, `input_mapping_json`, `enabled`. |
| `studio_trigger_events` | **APPEND-ONLY** (NIST AU) audit of every event evaluated — matched or not. Non-matches are recorded with `matched=0`, `run_id` NULL and a `reason`, so a trigger that silently never fires stays diagnosable. This table is the answer to "why did this run start". |

**Filter language:** `filter_json` holds `tools/studio/automation_builder.py:CONDITION_OPERATORS`
conditions — `equals`, `not_equals`, `contains`, `greater_than`, `less_than`, `in_list`,
`is_empty`, `is_not_empty`. Deliberately **no second condition DSL**.

**PG portability:** the JSON columns are TEXT and are parsed in Python — never with SQLite JSON
functions (`json_extract` / `json_each`), per the CLAUDE.md PG portability rule.

Registered in `tools/studio/init_db.py:STUDIO_TABLES` (so a fresh install gets the tables without
running the migration) and in `init_db.APPEND_ONLY_TABLES` +
`.claude/hooks/pre_tool_use.py:APPEND_ONLY_TABLES`; schemas mirrored into `tests/conftest.py` and
`icdev/tools/studio/init_db.py`. Tests: `tests/test_dwo_event_tables.py`.

### Intended routing point in the gateway

A gateway event becomes a candidate trigger event only **after** it clears the existing pipeline
in `gateway_agent.py:handle_webhook()` — adapter signature verification → `parse_webhook` →
`CommandEnvelope` → allowlist → the 8-gate `run_security_chain()`. The insertion point is
step 8, alongside `execute_command()` / `handle_agent_message()`; a rejected envelope never
reaches the trigger registry, and IL response filtering is unchanged.

### Current state

- **Merged:** the three registry tables and their indexes only.
- **Not in the tree:** the CRUD / `match_event()` layer (rest of dwo-evt-01) and the routing that
  consumes these tables (dwo-evt-02 "route cleared gateway events into workflow runs",
  dwo-evt-03). Until they merge, no gateway event starts a workflow run — populating
  `studio_workflow_triggers` by hand has no runtime effect.

### Not to be confused with

`tools/ci/core/event_router.py` (D132/D133) is a **different** subsystem: it routes CI/CD
`EventEnvelope` objects (GitHub/GitLab webhooks, poll triggers) to agent runs with a lane-aware
session queue. It is unrelated to `studio_workflow_triggers` and does not read these tables.

Studio-side detail: `tools/manifest/icdev-studio-low-code-no-code-platform.md` → *Event Source Registry*.

