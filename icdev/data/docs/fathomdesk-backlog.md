# FathomDesk Backlog

> Durable log of requests + ideas surfaced during sessions but not yet built.
> Each entry: **what**, **why**, **roughly when** to land it, **dependencies**.
> When picking up work, scan this list alongside the active TaskList.

Last updated: 2026-04-19 (Phase 5B + 6.1-6.6 + 6.3.5 follow-ups #1-3 + 2C + 2D + 7.5 paper options + 7.11 news intelligence shipped)

---

## Phase 1 (current — single-user, profile-as-config)

### ✅ Phase 1 — Profile-as-config (DONE)
Persona-aware UX without auth. Users pick a persona via Settings → Profile;
sidebar pages + cards reshape to match. 8 personas defined in
`args/persona_presets.yaml`. Hot-reloaded.

### ✅ Phase 1B — Reading-voice transformations (DONE)
`tools/trading/analytics/reading_voice.py` post-processes Reading dicts per
voice config (rookie/pm/technical/standard). Wired into all 11 Reading
endpoints. Rookie voice strips ratio jargon (sharpe/sortino/etc) and
rephrases technical terms.

### ✅ Phase 1C — Persona alert-default seeding + flags consumers (DONE — shipped in a prior session; backlog was stale)
- **Suggested rules card** on `/alerts` (persona pill + "+ Add selected" button). Uses `GET /api/alerts/suggested` which tags each rule with `evaluator_supports: bool` — placeholder-subject rules (WATCHLIST_ANY, PORTFOLIO_DRAWDOWN, etc.) are rendered unchecked-by-default with a ⚠ "inert until Phase 4" warning so users know not to expect them to fire. `POST /api/alerts/suggested/seed` inserts selected rules into `ad_alert_rules`; duplicate names are skipped.
- **Flag consumer framework** in `base.html` — `window.AD_PROFILE.flags` populated from profile; `applyPersonaFlags()` sets `body[data-persona-flag-X="1"]` attributes that `trading.css` consumes:
  - `sandbox_mode` (student) → PAPER ONLY badge on every page-header h1 via `::after`
  - `compact_layout` (quant) → tighter padding on cards, stat blocks, data tables
  - `api_first` (quant) → `.btn-export` elements get a highlight border
  - `requires_realtime` (day_trader) → dismissible banner pointing at Phase 7+ specialized infra
  - `requires_multi_asset` (family_office) → dismissible banner pointing at Phase 7+ alts / tax-aware reporting
- **Deferred inside 1C (intentionally):**
  - `keyboard_shortcuts_visible` hot-key card — blocked on the hot-key engine itself (Phase 7+ day-trader specialized infra)
  - Virtual-subject evaluator (WATCHLIST_ANY, PORTFOLIO_DRAWDOWN resolution) — substantive alerts/evaluator work; the suggestion UI warns rather than fires for these

---

## Phase 2 (auth + per-user data + MFA)

### Phase 2A — Email/social auth + TOTP + backup codes
Per the multi-session plan. ~3 sessions. Schema additions + Authlib +
Flask-Login + pyotp + flask-mailman.

  - ✅ **2A.1 (DONE 2026-04-18)** — `ad_users` + `ad_user_sessions`,
    argon2id, login/signup/logout pages, auth middleware, Account section
    in Settings, bootstrap migration of legacy `'default'` profile rows
    to first registered user.
  - ✅ **2A.2 (DONE 2026-04-18)** — Password reset (`ad_password_reset_tokens`,
    30-min single-use, sha256-hashed, no-enumeration response,
    rate-limited to 3/hour). Email backend (dev → `.tmp/sent_emails/`,
    SMTP swappable via `ICDEV_MAIL_BACKEND=smtp`). Google OAuth via
    Authlib (auto-appears when `GOOGLE_CLIENT_ID/SECRET` set in `.env`).
    Account-linking (Google email → existing local account = link).
  - ✅ **2A.2.5 (DONE 2026-04-18)** — Microsoft (OIDC, common tenant)
    + GitHub (OAuth 2.0, /user + /user/emails fallback for private
    primaries) wired. Buttons auto-render when env creds set:
    `MICROSOFT_CLIENT_ID/SECRET` (+ optional `MICROSOFT_TENANT_ID`)
    and `GITHUB_CLIENT_ID/SECRET`.
  - ✅ **2A.3 (DONE 2026-04-18)** — pyotp TOTP + backup codes (10×10char,
    sha256-hashed, single-use). AES-GCM secret-at-rest via
    `ICDEV_KEYSTORE_KEY` env (`python -m tools.trading.auth.crypto --gen`
    to generate). MFA-aware login flow + `/mfa/verify` page + middleware
    gate (sessions track `mfa_satisfied_at`). Step-up auth on disable +
    backup-code regen. 5/15min lockout via append-only `ad_mfa_attempts`
    audit table. **Forced enrollment after grace period** (`mfa_required_at`
    column on `ad_users`) is plumbed but no enforcement timer yet —
    operator can set it manually; auto-prompt at login lands in 2A.3.5
    if needed.
  - ✅ **2B (DONE 2026-04-18)** — WebAuthn / Passkeys via Duo's `webauthn`
    lib. `ad_user_webauthn_credentials` table (one row per registered
    credential, supports many per user). Hardware keys (YubiKey, Google
    Titan), platform authenticators (Touch ID, Windows Hello, Face ID,
    Android biometrics). Settings panel for register/list/delete (step-up
    enforced). "🔑 Use passkey" button on /mfa/verify. NIST AAL3 path.
    Operator config: `ICDEV_WEBAUTHN_RP_ID`, `ICDEV_WEBAUTHN_RP_NAME`,
    `ICDEV_WEBAUTHN_ORIGIN` (defaults work for localhost dev; production
    requires HTTPS).

### ✅ Operator admin CLI — DONE 2026-04-18
`tools/trading/auth/admin_cli.py` — operator-side recovery for when SMTP
isn't configured or you've lost access entirely. Commands: `list`,
`reset-password`, `issue-reset-token`, `last-reset-link`,
`disable`/`enable`. Survives air-gap; no email backend required.

### ✅ Legacy single-user data migration — DONE 2026-04-18
- `tools/trading/migrations/add_user_id_to_legacy_tables.py` — idempotent migration script
- 9 tables gained `user_id TEXT` (ad_portfolios, ad_positions, ad_orders, ad_pf_daily_snapshots, ad_strategy_runs, ad_strategy_holdings, ad_cis_recommendations, ad_analysis_runs, ad_alerts_log)
- 11 tables gained `tenant_id TEXT` (the 9 above plus ad_alert_rules + ad_watchlists which already had user_id)
- 31,720 rows backfilled to first registered user
- DDL files updated (tools/trading/db.py, tools/trading/alerts/db.py, tools/trading/dashboard/app.py) so fresh installs include both columns from the start
- **What's NOT done (Phase 3 work):** query sites still don't filter by user_id. The schema is ready; per-user filtering lands when Phase 3 makes multi-tenancy real. For now everything functionally still behaves as single-user — but the columns are populated correctly so Phase 3 just needs to add `WHERE user_id = ?` to read paths.

### Phase 2B — WebAuthn / Passkeys
~1 session. Adds NIST AAL3 capability via hardware keys + platform
authenticators. Builds on 2A's `ad_user_mfa.webauthn_credentials` JSON column.

### ✅ BYOK (Path A — operator-held AES-GCM) — DONE 2026-04-18
- `tools/trading/credentials/db.py` — `ad_user_credentials` (provider × user, encrypted at rest) + `ad_credential_audit` (append-only, NIST AU)
- `resolver.py` — single read path, fallback order: per-user DB → env var → None. Daemon callers pass `user_id=None` → env-only.
- `tester.py` — server-side no-op probes per provider (Anthropic /v1/messages w/ 1 token, OpenAI /v1/models, Ollama /api/tags, Alpaca /v2/account)
- API routes: GET/PUT/DELETE `/api/credentials/<provider>`, POST `.../test`, GET `/api/credentials/audit`
- Settings UI: 2 cards ("Your LLM API Keys" + "Your Broker Connection") — hidden in beginner_mode (retail/student personas)
- Step-up MFA enforced on PUT/DELETE when user has MFA enabled
- alpaca_adapter wired to use resolver (request-bound = user keys, daemon = env)
- Encryption: AES-GCM via `ICDEV_KEYSTORE_KEY` (same key as MFA secret-at-rest)
- **Threat model:** protects against DB dumps; does NOT protect against operator
- Schema includes `vault_mode INTEGER DEFAULT 0` for forward-compat with Path B opt-in (lands in 2C below)

### ✅ Phase 2C — BYOK Path B opt-in (envelope encryption with user passphrase) (DONE 2026-04-18)
- New module `tools/trading/credentials/vault.py` — HKDF-SHA256 KEK derivation + AES-GCM envelope. Uses the `cryptography` package (already in requirements for Path A); `is_available()` gates the Settings UI when it's missing.
- **Opt-in lifecycle:** `provision()` generates fresh random 32-byte master_dek + 16-byte per-user salt. Derives password_kek and recovery_kek via HKDF, wraps the master_dek twice, returns the recovery code. Caller (the Flask opt-in route) persists both wrapped blobs + the salt on the new `ad_user_vault` table + migrates existing Path A credentials through a decrypt-and-reencrypt helper (`_vault_migrate_keys`).
- **Session binding:** on every successful `/api/auth/login`, if the user has a vault row, the handler derives password_kek from the just-verified plaintext password, decrypts the master_dek, and base64-stashes it into the Flask session cookie. The session cookie is signed by `SECRET_KEY` + httpOnly — master_dek never touches disk. On `/api/auth/logout` the DEK is popped.
- **Resolver branch:** `get_raw_credential()` now reads `vault_mode` and branches. vault_mode=1 without a session DEK (daemon context) → audited `vault_dek_missing` event + `None` returned → callers fall back to env vars — exactly the B1 "daemon ops disabled" behavior the backlog specified.
- **4 new routes** — `POST /api/credentials/vault/enable` (migrates + returns recovery code once), `POST .../disable` (migrates back to Path A), `POST .../rotate-recovery` (new code, new wrapping), `POST .../recover` (forgot-password path: takes recovery code + new password, re-wraps DEK, also rotates recovery code + updates argon2 hash + re-unlocks session).
- **Settings UI** card renders three states: UNAVAILABLE (cryptography package missing), DISABLED (enable form with password prompt), ENABLED (status pill + rotate/recover/disable tiles). Recovery-code reveal modal requires user to tick "I've saved it" before the Done button unlocks — reduces risk of accidental dismiss.
- **Threat model**: protects against operator reading DB + `ICDEV_KEYSTORE_KEY` simultaneously; doesn't protect against a compromised server process that sees DEK mid-request. Forgot-password WITHOUT recovery code = keys permanently lost — documented in the UI copy. Registered on the manifest shard. Coherence 14/17 (same 3 pre-existing warns).

### 🔜 Phase 2D — Real secrets manager backend (HashiCorp Vault / OpenBao)

### ✅ Phase 2D — Real secrets manager backend (HashiCorp Vault / OpenBao) (DONE 2026-04-18)
- New module `tools/trading/credentials/secret_store.py` — `SecretStore` ABC with `put/get/delete/health`. Two implementations shipped:
  - `LocalEncryptedStore` — thin wrapper that delegates to existing `credentials.db.get_raw_credential/upsert_credential` — **zero behavior change** when `ICDEV_SECRET_BACKEND=local` (default). Path A + per-user Path B continue as before.
  - `HashiCorpVaultStore` — KV v2 via `hvac` HTTP client. Lazy client construction + clear `SecretStoreError` on missing `hvac`, missing `VAULT_ADDR/VAULT_TOKEN`, or auth failure. Reads/writes `{mount}/data/fathomdesk/credentials/{user_id}/{provider}`. Vault handles its own encryption — we just write plaintext over mTLS to the Vault endpoint.
- Selection via `ICDEV_SECRET_BACKEND` env (local | vault); optional `VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_MOUNT_PATH`, `VAULT_NAMESPACE`. `get_active_store()` factory + `reset_cache()` for hot-switches.
- `credentials.db.upsert_credential` + `get_raw_credential` short-circuit to `HashiCorpVaultStore` when `is_vault_backend()`. Local backend (default) path is untouched, so Path A + Path B stay fully functional.
- When Vault is the active backend: per-user Path B is effectively bypassed (Vault IS the trust root). A stub row in `ad_user_credentials` tracks provider-presence + `last4` so the Settings UI still enumerates configured providers — the actual ciphertext lives in Vault. Audit rows carry `via=hashi_vault` detail.
- New route `GET /api/credentials/secret-backend` — returns active backend + health probe ({ok, detail, extra: addr/mount_path/namespace/authenticated/sealed}). Informational, visible to any authed user.
- Settings UI gains a thin top-of-card badge: **LOCAL ENCRYPTED** (blue) / **HASHICORP VAULT** (green when healthy, red when unreachable). Shows the Vault address + mount when healthy, or the error detail when not.
- `.env.example` documents the full Vault env set. `hvac>=2.0` added as commented optional in `requirements.txt` (matching Stripe's optional-import pattern).
- Registered in manifest shard. Coherence 14/17 (3 pre-existing warns). Smoke test: factory resolves to `LocalEncryptedStore` by default; flipping env to `vault` returns `HashiCorpVaultStore` whose health reports the `hvac not installed` message cleanly.
- **Not in 2D (deferred, documented):** per-tenant Vault policies (once Phase 3 per-tenant deployments land), AppRole / K8s / AWS IAM auth methods (currently only raw `VAULT_TOKEN`), Transit engine integration, one-way Local→Vault data migration script (operator concern — note it in runbooks), dynamic-secrets + auto-rotation.

### Original BYOK request (now superseded by above) — kept for history
**What:** UI in Settings to paste API keys (OpenAI, Anthropic, Ollama base URL,
Alpaca key + secret). Encrypted at rest in `ad_user_credentials`. Resolution
order: per-user DB credential → env var → fail closed. "Test connection" button
hits provider with no-op call. Audit log entry on every key change.

**Why:** Each persona (advisor, family office, even individual retail) needs
their own keys — currently every user shares operator's `.env`. Without this,
multi-user (Phase 2) is meaningless because one user's API spend lands on the
operator's bill.

**When:** Lands with Phase 2A as a prerequisite (per-user identity makes BYOK
actually make sense). Schema lays the `user_id` column now (default 'default'),
gains real values in Phase 2A. `tenant_id` for Phase 3 tenant-shared keys.

**Crypto:** AES-GCM at rest with key derived from `ICDEV_KEYSTORE_KEY` env var
(or shared with `ICDEV_MFA_KEY` from Phase 2A). Keys never returned to client
in plaintext — only `sk-•••KEY` mask + `last4`.

**Scope:**
- New table `ad_user_credentials (provider, key_encrypted, secret_encrypted,
  base_url, last_tested_at, last_test_status, last4, user_id, tenant_id,
  updated_at)`
- New audit table `ad_credential_audit` (append-only, NIST AU)
- 4 providers MVP: anthropic, openai, ollama, alpaca
- Settings page gains 2 cards: "LLM Providers" + "Broker Connections"
- "Test connection" endpoint per provider (server-side no-op call)
- Hide BYOK card in beginner_mode (retail/student personas)
- Code-site change: `os.environ.get('X_API_KEY')` → `get_credential('X', 'api_key')`
  helper that falls back to env

**Effort:** ~1 focused session AFTER Phase 2A lands. Could ship a
single-user-mode MVP earlier (no `user_id` enforcement) if user wants it sooner
— flag this if/when needed.

---

## Phase 3 (tenants + branding)

  - ✅ **3.1 (DONE 2026-04-18)** — `ad_tenants` table, `ensure_default_tenant`
    bootstrap, auth middleware injects `g.current_tenant`, context_processor
    exposes `tenant` to templates, sidebar shows 🏢 workspace badge,
    Settings → Workspace card (name + slug + role + member count + edit for
    owner/admin), `/api/tenant` GET/PATCH endpoints, first-signup flow
    auto-claims default tenant ownership.
  - ✅ **3.2 (DONE 2026-04-18)** — `ad_tenant_memberships` (many-to-many
    user↔tenant) + `ad_tenant_invitations` (sha256-hashed token, 7-day
    TTL, single-use). 4 roles: owner/admin/member/viewer. Public peek
    endpoint, public landing page (`/accept-invite`), gated accept
    endpoint. Step-up MFA enforced on send-invite. Email integration
    via existing `tools/trading/auth/email.py` (dev console / SMTP
    swappable). Settings → Workspace gains: invite form, pending
    invitations list, members list with role-change + remove (owner
    only). Tenant switcher in sidebar (auto-shown when user has 2+
    memberships). Backfill helper imports existing `ad_users` rows
    into `ad_tenant_memberships`. `ad_tenant_invitations` added to
    APPEND_ONLY_TABLES (NIST AU).
  - ✅ **3.3 (DONE 2026-04-18)** — Per-tenant logo URL + accent color hex
    + `white_label_enabled` toggle. CSS variable `--accent` injected via
    `<style>` block in base.html when accent set. Sidebar swaps to
    workspace name (+ logo when URL set, fallback to text + "Powered by
    FathomDesk" tagline). PDF brief uses tenant name in classification
    banner + footer + filename slug, fetches + embeds logo from URL when
    provided. Server-side validation: 6-digit hex required, only http(s)://
    or data: URLs accepted. Settings → Workspace gains "Branding &
    white-label" panel with native color picker + URL input + toggle +
    live logo preview. **Logo upload deferred** (URL-only for now —
    operator uploads to their own CDN/S3).

### ✅ Per-tenant query-site sweep — DONE 2026-04-18 (high-leakage tables)
- New helper `_active_tenant_id()` + `_scope_clause()` in `tools/trading/dashboard/app.py`
- Web endpoints now filter by `user_id + tenant_id`:
  - `/api/portfolio` — portfolio + positions reads
  - `/api/portfolio/state` — analytics view
  - `/api/portfolio/snapshot/now` (insert + update + read of `ad_pf_daily_snapshots`)
  - `/api/portfolio/history` — snapshot series read
  - `/api/orders` — orders list
  - `/api/risk` — positions read
  - `/` overview helper (`_current_portfolio`)
- INSERT paths also include user_id + tenant_id for new portfolios + snapshots
- DB-level isolation verified: a fake other-user query returns 0 portfolios

### Still deferred (lower-leakage paths — sweep when needed)
- **Daemon code paths** (`auto_trader.py`, `exit_executor.py`, `position_reconciler.py`, etc.) — still iterate globally. Operator-controlled, no leak in single-tenant deployment. When real multi-tenant lands, these need to either iterate by tenant OR explicitly run as a "service" with all-tenant scope.
- **Strategy / advisor / analysis read tables** (`ad_strategy_runs`, `ad_strategy_holdings`, `ad_cis_recommendations`, `ad_analysis_runs`) — these have `user_id`/`tenant_id` columns populated but the read endpoints don't filter yet. Less sensitive than portfolio data; lands when 2nd tenant is real.

### Notes from 3.1 (worth recording)
- `ad_tenants.is_default = 1` on the bootstrap tenant to distinguish it
  from later user-created tenants
- Phase 3.2 will need to migrate `ad_users.tenant_id` (single column) into
  `ad_tenant_memberships` (many-to-many) — keep `tenant_id` on ad_users
  as the user's "primary/default tenant" for backward compat with
  Phase 3.1 code paths
- Query-site sweep: still deferred. `tenant_id` columns are populated on
  every per-user table (Legacy migration), but reads don't filter by
  `tenant_id` yet. Lands when 3.2 introduces second tenant.

---

## Phase 4 (persona-specific surfaces)

### Per-persona Reading variants beyond rookie/pm/technical
- `reading_voice_passive` — long-horizon framing, glide-path commentary
- `reading_voice_advisor` — client-tone (less first-person, more
  "the portfolio is X" framing)

### Per-persona dedicated pages
- ✅ **Student: `/lessons` + starter curriculum — DONE 2026-04-18**
  `tools/trading/lessons/catalog.py` + `args/lessons_catalog.yaml` +
  `docs/lessons/{level}/{slug}.md`. 5 beginner lessons shipped
  (what-is-a-stock, reading-a-signal, diversification, macro-regimes,
  paper-to-live) + 3 intermediate lesson slugs defined (content TBD:
  reading-engines, personas, alerts-and-rules). Page has progress bars
  per level, catalog view with per-lesson status markers, detail view
  with rendered markdown + prev/next navigation + "Mark complete"
  button. `ad_user_lesson_progress` table tracks per-user completion.
  Featured for student + retail. Hidden for quant + day_trader. Quiz
  support + "explain this signal" buttons deferred.

- ✅ **Quant: `/api-keys` + Bearer token auth — DONE 2026-04-18**
  `tools/trading/auth/api_tokens.py` — `ad_user_api_tokens` (sha256-hashed,
  last4 preview, optional TTL, scopes column reserved for Phase 5).
  Token format `ad_live_<32-urlsafe>`. Middleware extended to check
  `Authorization: Bearer` header before cookie session; Bearer auth
  skips MFA gate (token issuance required a fresh MFA session already).
  `/api-keys` page: create/list/revoke + copy-once UI + 6 curl examples
  incl. a pandas recipe. Step-up MFA on create. Sidebar link shown for
  quant/pro_trader/advisor personas.

- ✅ **Advisor: `/clients` + share-link generator — DONE 2026-04-18**
  `tools/trading/share/tokens.py` — itsdangerous-signed tokens keyed
  off Flask `secret_key`, 7-day default TTL, audit row per token in
  `ad_share_tokens` (hash + tenant + use_count + revoked_at). Public
  route `/share/portfolio-brief?token=...` serves a PDF without auth.
  `/clients` page shows tenant members with per-client activity summary
  (orders / analyses / alert rules / last login), plus a token-audit
  table with Revoke buttons. Admin/owner only. Featured for advisor
  persona; sidebar link conditional on role being owner/admin.

- ✅ **Passive: `/rebalance` page — DONE 2026-04-18**
  `tools/trading/analytics/rebalance.py` + `ad_target_allocations` table.
  User sets target mix (60/40, Three-Fund, Permanent Portfolio, or custom);
  engine computes drift (|current - target|), trade-plan (buy/sell $ to
  reach target), and tax-loss candidates (positions down ≥ 8% AND ≥ $500).
  Sum-validation rejects > 100%. Featured for passive persona; hidden for
  quant + day_trader. Sidebar link in Book group.

- ✅ **Retail: `/today` "What should I do today?" page — DONE 2026-04-18**
  `tools/trading/analytics/today_digest.py` — deterministic 10-rule engine.
  Rules: concentration risk, regime mismatch, SROR danger, watchlist BUY,
  news impact on holdings, stale snapshot, cash deployment, unack alerts,
  big-winner profit take, big-loser stop review. Prioritized by urgency,
  capped at top 5. Plain-English with transparent evidence. Featured for
  retail+student personas; hidden for quant. Auto-refreshes 5 min.
- Advisor: client roster, share-link generator
- Quant: API tokens page, factor decomposition page
- Student: lesson mode with guided tours
- Passive: rebalance calculator, glide-path tracker, tax-loss harvest suggester
- Day trader: hot-key cheat sheet, multi-monitor layouts (real exec stays
  Phase 6)

### ✅ Subject placeholders for alert rules — virtual subject evaluator (DONE 2026-04-18)
- New module `tools/trading/alerts/virtual_subjects.py` — `resolve_for_rule(rule, user_id)` returns one of:
  - `{kind: "not_virtual"}` — passthrough, existing per-ticker path runs
  - `{kind: "expand", tickers: [...]}` — for `WATCHLIST_ANY`; evaluator loops concrete tickers, fires once on the first match (no N-fold alert spam), reports the triggering ticker in `evidence.resolved_ticker`
  - `{kind: "aggregate", value, evidence}` — for AGGs (`WATCHLIST_AVG`, `WATCHLIST_TOP`, `PORTFOLIO_DRAWDOWN`, `PORTFOLIO_DRIFT`); single scalar compared against threshold
  - `{kind: "skip", reason}` — empty watchlist / no snapshot history / no target allocations set / etc.
- **Data sources per placeholder:**
  - `WATCHLIST_ANY` / `AVG` / `TOP` → `ad_watchlists` × latest `ad_signals` (composite_score, confidence) OR per-ticker price_pct_change / news_impact via the existing single-subject resolvers
  - `PORTFOLIO_DRAWDOWN` → `ad_pf_daily_snapshots` peak vs current, returned as negative %; skips when <2 snapshots
  - `PORTFOLIO_DRIFT` → delegates to `analytics.rebalance.build_plan().summary.total_abs_drift_pp`; skips when no target allocations set
- `alerts/evaluator.py::evaluate_all()` now dispatches through the resolver before falling back to the per-ticker path. `signal_direction` rule_type also expands for WATCHLIST_ANY.
- `_format_message()` gets virtual-subject-aware templates — e.g., `"Watchlist: NVDA composite 82.0 gt 75"` instead of the generic one.
- `/api/alerts/suggested` — now marks **every** persona-default rule as `evaluator_supports: true`. The UI "⚠ uses placeholder subject — inert until Phase 4" warning is gone; users can seed all persona-default rules directly.
- Verified end-to-end: WATCHLIST_ANY expands to the user's 2-ticker watchlist; WATCHLIST_AVG computes avg composite = 54.095 across watchlist; PORTFOLIO_DRAWDOWN correctly skips with `insufficient snapshot history` for a fresh account; evaluate_all() runs clean. Coherence 17/17 (no regression).

---

## Phase 5 (monetization)

### ✅ Phase 5A — Plan tiers + quota enforcement framework (DONE 2026-04-17)
- `args/plan_tiers.yaml` — 3 tiers (free / pro / enterprise) with feature
  matrix + quotas per tier. Hot-reloaded.
- `tools/trading/billing/tiers.py` — loader, `tier_for_tenant()`,
  `quota()`, `feature()`, `check_quota()` (raises `QuotaExceeded`),
  `usage_summary()`.
- Quota enforcement wired at 4 creation sites:
  - `api_alerts_create_rule` → tenant `alert_rules` quota
  - `api_api_tokens_create` → per-user `api_tokens_per_user` quota
  - `api_share_portfolio_brief_create` → monthly `share_links_per_month`
  - `api_tenant_invitations_create` → tenant `members` + monthly
    `invitations_per_month`
- `/billing` page shows current tier, feature matrix, usage bars,
  upgrade buttons. Owner-only tier switch via `/api/billing/tier`
  (direct set — Phase 5B will route through Stripe checkout).
- Admin CLI: `python -m tools.trading.billing.admin_cli`
  (`list-tiers`, `list-tenants`, `set-tier`, `usage`).
- Tier column already present on `ad_tenants.plan_tier` from Phase 3.1.

### ✅ Phase 5B — Stripe checkout + webhook + invoicing (DONE 2026-04-18)
- `tools/trading/billing/stripe_client.py` — lazy `import stripe` wrapper; `ensure_customer()`, `create_checkout_session()`, `create_portal_session()`, `construct_webhook_event()`, `tier_from_price_id()`. Raises `StripeNotInstalled` / `StripeNotConfigured` so air-gap deployments degrade gracefully with HTTP 501.
- `tools/trading/billing/db.py` — `ad_stripe_customers` (tenant → Stripe Customer), `ad_stripe_subscriptions` (status + period + cancel_at_period_end), `ad_stripe_events` (append-only NIST AU, idempotency key = `stripe_event_id`), `ad_stripe_invoices` (full history w/ hosted URL + PDF).
- `tools/trading/billing/webhooks.py` — dispatcher for `customer.subscription.{created,updated,deleted}` and `invoice.{payment_succeeded,payment_failed,finalized}`. Syncs tenant `plan_tier` on state change (active/trialing → target tier; canceled/unpaid/incomplete_expired → free). Dunning email on `invoice.payment_failed` via `tools/trading/auth/email.py`. Idempotent replay via `event_already_processed()`.
- Flask routes in `tools/trading/dashboard/app.py`:
  - `POST /api/billing/checkout` — owner-only, step-up MFA, returns Checkout Session URL
  - `POST /api/billing/portal` — owner-only, Self-service Portal URL
  - `POST /api/billing/webhook` — public (allowlisted in middleware); verifies Stripe signature; 500 on handler failure so Stripe retries
  - `GET /api/billing/subscription` — current-sub snapshot for UI
  - `GET /api/billing/invoices` — owner/admin, last 24 invoices
  - `POST /api/billing/tier` retained for operator/dev direct-set + Free downgrade path
- `/billing` page: Stripe Upgrade buttons for paid tiers, Manage-subscription (Portal) button, invoice history table, `?checkout=success|cancel` banner.
- Env: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_PRO`, `STRIPE_PRICE_ID_ENTERPRISE` documented in `.env.example`; Price IDs resolve YAML → env; `stripe>=11.0` commented in `requirements.txt` (operator installs on connected deploys).
- **Proration:** no manual code — Stripe handles it automatically via the Portal when a user upgrades/downgrades mid-period.
- Registered in `tools/manifest/fathomdesk-trading-engine.md`; `ad_stripe_events` added to `.claude/hooks/pre_tool_use.py:APPEND_ONLY_TABLES`. Coherence gate passes 16/17 (1 pre-existing warn unrelated).

---

## Phase 6 — Gamification & Trade Challenges *(future, requested 2026-04-18)*

**Inspiration:** *Rich Dad Poor Dad* + Robert Kiyosaki's **CashFlow 101 / 102**
board games. CashFlow 101 teaches basic financial literacy; 102 teaches
advanced investing — natural mapping to a multi-tier progression system.

**The cornerstone request:** *"Once graduated from Beginner to Intermediate,
they can trade live."* A mandatory paper-trade graduation gate before live
trading is unlocked — both an educational win and a regulatory/compliance
benefit.

**Hard prereqs:**
- Phase 2 (auth + per-user data) — ✅ DONE
- Phase 3 (tenants — for league scoping) — pending

**Persona alignment:** the `student` persona becomes the natural default-
state for new users. `retail` graduates through the system. `pro_trader` /
`advisor` / `quant` opt out via "I have prior trading experience"
attestation (operator can disable that opt-out for compliance contexts —
e.g., a DoD educational tenant might require everyone to graduate).

**Connects to:** existing alert system (achievements fire alerts), Reading
engine (could explain *why* a trade was a level-up moment), persona-aware
UI (each persona could have its own progression curve).

### ✅ Phase 6.1 — Level system + progression metrics (DONE 2026-04-18)
- Schema: `ad_user_progression` (level/xp/opt_in/paper_trades/drawdown/regime_aware_count/level_unlocked_at/live_trading_unlocked_at) + `ad_xp_events` (append-only NIST AU, idempotent via UNIQUE(user_id, dedup_key)).
- 4 levels in `args/progression_config.yaml`: **Beginner (0) → Intermediate (100) → Advanced (500) → Pro (2000)** — operator-tunable.
- 11 XP rules in `args/xp_rules.yaml` with daily caps: signal_approved (+15), signal_rejected (+3), order_filled (+10), order_profitable_close (+25), regime_aware_decision (+25), first_profitable_week (+100), survived_drawdown (+50), diversified_portfolio (+30), lesson_completed (+20), scenario_run (+5), alert_acknowledged (+2).
- `tools/trading/progression/engine.py` — `grant_xp()` idempotent + daily-cap + opt-in aware; `progression_summary()` packs level/XP/next/rules/events for UI; `grant_xp_safe()` never raises so hook sites can't break trading flow.
- Hooks wired: signal approve/reject, scenario run, lesson complete — all use dedup keys tied to the underlying action ID so replays don't double-award.
- `/progression` page: level badge + XP counter + 4-level track + XP rules catalog + recent events table + opt-in toggle. Sidebar link under Research group.
- Persona gating via `args/persona_presets.yaml`: `opt_in_personas` = student/retail/passive (auto-tracked); `opt_out_personas` = quant/pro_trader/advisor/day_trader/family_office (page hidden; user can re-opt-in via Settings card).
- API: `GET /api/progression`, `GET /api/progression/events`, `POST /api/progression/opt-in`.
- `ad_xp_events` added to `APPEND_ONLY_TABLES`; registered in `tools/manifest/fathomdesk-trading-engine.md`. Coherence passes 14/17 (3 pre-existing warns unrelated to 6.1).
- **Not in 6.1:** order-fill hook — auto_trader daemon runs without user_id in scope; the `order_filled` rule is defined but awaits a per-user order path (would hook into `/api/orders POST` when that lands, or a reconcile poller).

### ✅ Phase 6.2 — Achievements + badges (DONE 2026-04-18)
- Catalog: `args/achievements_catalog.yaml` (hot-reload, 8 MVP badges — bronze / silver / gold tiers with per-badge xp_bonus + progress_hint). Operator-curated.
- `ad_user_achievements` table — append-only (NIST AU), idempotent via UNIQUE(user_id, achievement_id).
- `tools/trading/progression/achievements.py` — predicate registry covering 7 types: xp_event_count, xp_total_threshold, order_count, distinct_sectors, portfolio_value, lesson_track_complete, level_reached. All deterministic — no LLM calls. `achievements_summary()` packs earned badges + locked badges with live progress counters for the UI.
- Hooked into `grant_xp()` — every successful XP award triggers `evaluate_for_user()` which auto-grants any newly-satisfied badges + appends their xp_bonus as a normal XP event (recursion guarded via `reason == 'achievement_bonus'` skip).
- Wired: `regime_aware_decision` events now increment `ad_user_progression.regime_aware_decisions_count`; `achievement_bonus` added to `args/xp_rules.yaml` (amount=0, per-badge amount comes from the catalog).
- UI: `/progression` page adds a Badges card — responsive grid of earned (full color, tier-bordered) + locked (dimmed, with progress hints like "3/5 sectors") badges.
- API: `GET /api/progression/achievements`.
- 8 MVP badges: First Step, Getting the Hang of It, Diversified, Scenario Explorer, Regime Aware, Beginner Graduate, Intermediate Unlocked, Paper Millionaire.
- Registered: `ad_user_achievements` → APPEND_ONLY_TABLES; manifest shard updated. Coherence 14/17 (3 pre-existing warns unrelated). Verified end-to-end — first grant auto-earned `first_signal_approved`; 10 scenarios auto-earned `scenario_explorer`; XP pump auto-earned `intermediate_level`.
- **Not in 6.2:** Settings → Profile badge tile + persona picker badges — deferred (trivial UI wiring once we need it).

### ✅ Phase 6.3 — Single-player trade challenges (DONE 2026-04-18)
- Catalog: `args/challenges_catalog.yaml` (hot-reload, 6 MVP challenges across easy/medium/hard difficulties + 14/30/60/90-day durations). Operator-curated.
- `ad_challenge_attempts` table — **mutable** (status: active → completed/expired/abandoned). Rows carry start/close snapshots as JSON, final_score, deadline_at. Not append-only — attempts rewrite their state.
- `tools/trading/challenges/engine.py` — predicate registry covering 5 types: **return_pct_over_period, beats_benchmark (uses SPY), holdings_whitelist (inverse — fails on violation, passes on clean expiry), scenario_completion, hold_for_days**. Each predicate returns (state, progress) where state ∈ {passing, failing, violated}.
- Lifecycle: `start_attempt()` snapshots portfolio (+ SPY price) at T0. `evaluate_attempt()` re-runs predicate + deadline check lazily on every summary call; closes attempt when predicate passes / deadline hits / violation fires.
- Completion awards bonus XP via `grant_xp_safe(reason='challenge_completed')` with per-challenge `xp_on_complete` from catalog. `challenge_completed` added to `args/xp_rules.yaml`.
- UI: `/challenges` page — Active attempts card (progress bars + deadline countdown + abandon), Available grid (6 tiles with difficulty/duration/XP badges), History table (status-colored). Sidebar link under Research.
- Persona gating: auto-opt-in for student/retail/passive; hidden for quant/pro_trader/advisor/day_trader/family_office (same policy as /progression).
- API: `GET /api/challenges`, `POST /api/challenges/<id>/start`, `POST /api/challenges/attempts/<id>/abandon`.
- Registered in `tools/manifest/fathomdesk-trading-engine.md`. Coherence gate passes 14/17 (3 pre-existing warns unrelated). Verified end-to-end — start → deadline + snapshot captured → duplicate-start blocked → evaluate → abandon → summary reflects state.
- **Deferred from 6.3 (documented):** sandboxed paper portfolio per attempt (backlog called for this; 6.3 MVP uses start-snapshot deltas on the live paper portfolio — simpler, works for all 5 predicate types without duplicating the positions/orders stack). "Continuously held" precision for hold_for_days uses current-state check (not day-by-day sweep); if operator asks for true continuity, layer a daily snapshot sweep in 6.3.5.

### ✅ Phase 6.3.5 — Sandboxed paper portfolios per attempt (DONE 2026-04-18)
- 3 new tables — `ad_sandbox_portfolios` (1:1 with attempt, mutable cash+status+starting_spy_price), `ad_sandbox_positions` (mutable qty/avg_cost/market_value per (attempt, ticker)), `ad_sandbox_orders` (append-only NIST AU — every fill).
- `tools/trading/challenges/sandbox_db.py` — CRUD for all three tables. Positions use UNIQUE(attempt_id, ticker); orders append-only.
- `tools/trading/challenges/sandbox_engine.py` — `ensure_sandbox()` seeds $100k cash (operator-tunable via `args/challenges_sandbox_config.yaml` + per-challenge override) + freezes starting SPY price. `place_order()` fills market orders at live `fetch_latest_quote()` (zero slippage, zero fees, long-only MVP); validates cash + holdings; uses weighted-avg cost basis on buys. `sandbox_snapshot()` mark-to-markets positions on read + returns cash/total_value/return_pct/unrealized_pnl/benchmark_edge.
- `challenges/engine.py` refactored: `_current_state()` resolves sandbox first, falls back to live-paper snapshot for pre-6.3.5 attempts (backwards compat). `_start_reference()` resolves starting-value + starting-SPY from sandbox (preferred) or legacy JSON snapshot. `start_attempt()` provisions the sandbox; `abandon_attempt()` + `close_attempt()` archive it.
- Predicates now evaluate against sandbox (except scenario_completion, which stays action-based). `holdings_whitelist` now correctly fails only when the user buys outside the whitelist *inside* the sandbox — their live account can hold anything.
- UI: /challenges active-attempt cards gain a **sandbox summary strip** (Sandbox total / Cash / Positions / Return %) and a collapsible **Trade panel** — mark-to-market position table, buy/sell form (market orders, auto-uppercased ticker), recent orders log. Uses `/api/challenges/attempts/<id>/sandbox` + `/api/challenges/attempts/<id>/orders`.
- Config: `args/challenges_sandbox_config.yaml` — default_starting_cash_usd, fill_policy (market/slippage/commission — slippage+commission deferred), limits (max_position_pct, allow_short_sell=false).
- `ad_sandbox_orders` added to `APPEND_ONLY_TABLES`; manifest shard updated. Coherence 14/17. Smoke test verified buy/sell/validation/archive flow end-to-end.
- **Out of scope for 6.3.5:** limit/stop orders, short selling, slippage + commission model, daily-reconcile poller (mark-to-market happens on every snapshot read — fresh enough for the UI refresh cadence). Extension points are left in the fill_policy config for a follow-up.

### ✅ Phase 6.3.5 follow-up — slippage + commission + daily-snapshot reflex (DONE 2026-04-18)
- **Slippage** now applied on every sandbox fill — `slippage_bps` (default 5 = 0.05%) moves the fill price *against* the trader (buys higher, sells lower) vs the live quote from `fetch_latest_quote()`. Round-trip of 10 AAPL @ $270 now costs ~$2.70 in slippage, which is pedagogically the right order of magnitude and complements the graduation gate's "paper overstates live" warning.
- **Commission** — flat `commission_usd` (default 0) deducted from `cash_after` per fill. Both values live in `args/challenges_sandbox_config.yaml.fill_policy` (hot-reloaded). `place_order()` return dict now surfaces `quote`, `fill_price`, `slippage_bps`, `slippage_cost`, and `commission` so the UI can show them transparently. Order notes column records the slippage + commission for audit.
- **Daily snapshot reflex** — new `ad_sandbox_daily_snapshots` append-only table (one row per attempt per day, UNIQUE(attempt_id, snapshot_date)). `sandbox_engine.snapshot_day()` captures the row; `snapshot_day_all_active()` sweeps every active attempt. A new `challenge_snapshot_daily` reflex in `market_intel/daemon.py` runs every 24h (configured in `args/trading_daemon_config.yaml`) calling the sweep. Idempotent — re-running on the same day is a no-op, so daemon restarts don't corrupt history.
- **`hold_for_days` continuity check** — predicate now prefers the daily series when ≥ `min_days` snapshots exist. Walks the last `min_days` recorded days; requires EVERY day's `position_count ≥ min_positions`. Falls back to the point-in-time check when history is thin (new attempts, daemon downtime). Progress dict carries `source: daily_series|point_in_time`, `passing_days`, `failing_days`, `earliest_failure_date`.
- Smoke-tested: fresh attempt → buy AAPL → snapshot today → hand-seed 21 backdated snapshots with 5 positions → predicate correctly returns `failing` with the one failing day (today's 1-position state) correctly identified. Sweep across 2 concurrent active attempts inserts idempotently.
- Coherence 14/17 (same 3 pre-existing warns unrelated to this work). Companion synced.
- **Still deferred from 6.3.5 (documented):** limit / stop / short orders — these require a dedicated price-watch daemon to fill conditional orders, substantially bigger than what fits this follow-up; leave as its own iteration if user demand appears.

### ✅ Phase 6.3.5 follow-up #2 — limit + stop orders with watcher reflex (DONE 2026-04-18)
- New **`ad_sandbox_pending_orders`** table (mutable — rows flow `pending → filled | canceled`). The immutable fill ledger stays `ad_sandbox_orders` — every triggered pending order appends one fill row there, preserving the NIST-AU audit invariant.
- `sandbox_engine.place_order()` extended: accepts `order_type` ∈ {`market`, `limit`, `stop`} + optional `limit_price`/`stop_price`. Market path unchanged; limit/stop queue with placement-time validation (price > 0, rough cash-sufficiency for buys, held-qty for sells).
- **Trigger semantics:** `limit buy` fills when slipped_quote ≤ limit (user pays no worse than limit); `limit sell` fills when slipped_quote ≥ limit; `stop buy` fires at quote ≥ stop; `stop sell` fires at quote ≤ stop. Fills flow through the market path — inherit slippage + commission + cash/holdings validation.
- **`check_pending_orders(attempt_id)`** + **`check_pending_orders_all_active()`** — the watcher evaluates every active pending row; on trigger, fills via market path; on fill-time validation failure (cash drained since placement), auto-cancels with a note instead of zombie-ing the order.
- New reflex **`challenge_order_watcher`** wired into `market_intel/daemon.py` + `args/trading_daemon_config.yaml` — runs **every 15 min** (continuous). Matches existing `approved_monitor` cadence.
- **User cancel** — `cancel_pending_order()` + `DELETE /api/challenges/attempts/<id>/pending-orders/<order_id>`. **Mass-cancel on close** — `abandon_attempt()` + `evaluate_attempt()` close paths call `cancel_all_pending_for_attempt()` so triggers don't fire against archived sandboxes.
- **UI** — `/challenges` Trade panel: Type dropdown (Market/Limit/Stop) + conditional price input; Pending Orders table with per-row Cancel buttons. Status message distinguishes filled vs queued.
- API: `POST .../orders` return shape is `{ok, fill}` for market / `{ok, pending}` for limit/stop. `GET .../pending-orders` lists pending rows.
- **Coherence 17/17 clean.** Smoke-tested end-to-end: limit-at-260 stays pending (quote ≈ 270); limit-at-280 fills on first check (slipped 270.14 ≤ 280); stop-loss-at-250 queues; user cancel works; invalid placements rejected; abandon mass-cancels leftover pendings.
- **Out of scope for this follow-up (documented):** short selling (separate iteration — margin/locate/borrow + compliance), stop-limit combo, trailing stops, GTC vs day (treated as GTC; challenge deadline caps them), market-hours awareness (reflex runs 24/7; after-hours fills documented as sandbox-sim limitation), partial fills.

### ✅ Phase 6.3.5 follow-up #3 — market-hours + stop-limit + trailing stops + simplified short selling (DONE 2026-04-18)
- **Market-hours awareness** — new `tools/trading/challenges/market_hours.py`. `is_market_open()` checks weekday + 9:30–16:00 ET with rough DST boundary math (air-gap-safe; degrades to fixed offset if `zoneinfo` tz data missing). Env `ICDEV_SANDBOX_MARKET_HOURS_AWARE=false` disables for tests. `check_pending_orders_all_active()` short-circuits when closed — trailing-stop `best_seen_price` updates ONLY happen during market hours, which is correct. Holidays NOT modeled (scope choice).
- **Stop-limit** — new `order_type='stop_limit'`. Takes BOTH `stop_price` (trigger) and `limit_price` (cap). Two-stage evaluation: stage 1 waits for stop crossover, stage 2 applies the limit via slippage-gated check. Fills at the slipped quote if it satisfies the limit.
- **Trailing stops** — new `order_type='trailing_stop'`. Added `trailing_pct` + `best_seen_price` columns (idempotent ALTER). `best_seen_price` is seeded at placement from the live quote, then updated on every watcher tick in the favorable direction (highest for sell/long-protect, lowest for buy/short-cover). Fires when quote crosses `best_seen * (1 ∓ pct/100)`. `trailing_pct` capped at 50 (sanity).
- **Simplified short selling** — flipped `allow_short_sell: true` in `args/challenges_sandbox_config.yaml`. Sell qty > held creates a negative position; cash credited with proceeds (minus slippage + commission). Weighted-avg entry-price logic for short adds. Buy-to-cover handled in the buy path (covers + optionally flips long). Margin check at placement: `(post_cash + other_positions_mv) ≥ short_margin_ratio × |short_notional|`, defaulting to 1.5× (configurable via `short_margin_ratio`). **Documented simplifications:** no locate/borrow availability check, no overnight borrow fees, no continuous margin-call simulation (only placement-time check).
- **DB** — `list_positions(only_open=True)` changed from `qty > 0` to `qty != 0` so shorts surface in UI + margin calc. `update_best_seen()` helper for trailing-stop state. `create_pending_order()` gains `trailing_pct` + `best_seen_price` params.
- **UI** — Trade panel: Type dropdown extended to 5 options (Market/Limit/Stop/Stop-Limit/Trailing Stop). Second conditional input appears for stop-limit (limit price on top of stop). Pending Orders table shows trailing% + running best_seen, stop-limit shows both prices.
- **Verified:** stop-limit triggers + fills when conditions cross; trailing best_seen seeds at placement; short sale of MSFT correctly produced negative qty + credited cash; 10k-share over-short blocked by margin check ($6.3M needed, $4.3M available); market-closed sweep returns `skipped_reason: market closed`; abandon still mass-cancels all pending types.
- **Coherence 17/17 clean** (no regression from yesterday). Companion synced. Registered in manifest shard.
- **Remaining deferred (documented, not shipping):** partial fills — the paper-sim quote feed has no volume-at-price data; synthesizing liquidity layers would be arbitrary and adds complexity without fidelity. Real brokers model this; our sandbox explicitly doesn't. With this follow-up #3, **the original Phase 6.3.5 scope is now fully delivered** (all 5 items except partial fills which won't be built).

### ✅ Phase 6.4 — Multi-team competitions + leaderboards (DONE 2026-04-18)
- Two tables (no standings cache — compute on-demand): `ad_leagues` (mutable; tenant-scoped with `UNIQUE(tenant_id, slug)`; 3 visibility modes: public / private / code with auto-generated 8-char codes) + `ad_league_members` (mutable role: captain / member; `UNIQUE(league_id, user_id)`).
- Config: `args/leagues_config.yaml` — 3 ranking windows (weekly=7d / monthly=30d / all_time=since league creation), scoring (xp_weight=1.0 + challenge_bonus=50 per completed challenge), limits (max_league_size=100, max_leagues_per_user=20, max_leagues_per_tenant=50).
- `tools/trading/leagues/engine.py` — `compute_standings(league_id, window)` aggregates `SUM(ad_xp_events.amount)` + `COUNT(ad_challenge_attempts WHERE status='completed')` per member, ranks by composite `score`. Tenant-boundary enforced — user must be in `ad_tenant_memberships` to create or join. Owner cannot be kicked (must delete league or transfer ownership first).
- UI: `/leagues` page — **Create a league** form (name + description + visibility dropdown), **Join by code** form, **My leagues** list, **Public leagues** grid (peek or join), and an inline detail panel with **Standings** (window selector ↔ live re-compute), **Members** (captains can kick), and actions (leave / delete).
- API: `/api/leagues` (GET summary / POST create), `/api/leagues/<id>` (GET detail + standings / DELETE), `/api/leagues/<id>/join`, `/api/leagues/join-by-code`, `/api/leagues/<id>/members` (POST add / DELETE kick).
- Persona gating: visible for student/retail/passive, hidden for quant/pro_trader/advisor/day_trader/family_office (same policy as /progression + /challenges).
- Coherence 14/17 (3 pre-existing warns). Companion synced. Smoke test verified: Alice creates public league → Bob joins → Carol joins → XP events per user correctly rank Alice #1 (150 XP) > Bob #2 (120 XP) > Carol #3 (0 XP) in the weekly window.
- **Out of scope for 6.4 (documented):** invitation tokens (private leagues use captain-add; an invitation-link flow could land in 6.4.5), seasons + playoffs, real-time push, league chat, cross-tenant leagues, cash prizes (SEC/FINRA sensitive — explicitly not exposed).

### ✅ Phase 6.5 — Educational curriculum tied to levels (DONE 2026-04-18)
- Phase 4 already shipped `/lessons` + 5 beginner lesson bodies; 6.5 layered the curriculum, gating, and quiz pieces on top.
- **Chain-based level gating** — `beginner` always unlocked; `intermediate` unlocks when all beginner lessons completed; `advanced` unlocks when intermediate completed. Decided against XP-based level gating — the `intermediate_level` achievement (Phase 6.2) already rewards XP progression; the curriculum is about pedagogy, so chain-based keeps them orthogonal.
- **Per-lesson prerequisites** — `prerequisites: [slug, slug]` field forces intra-level linear ordering where the operator specifies; beginner lessons 2–5 now chain.
- **Inline quizzes** — YAML-declared multi-choice with `pass_percent` (default 70), per-question `explanation:` shown post-submit. 2 sample quizzes on `what-is-a-stock` (3 q) + `reading-a-signal` (2 q); other lessons can add them incrementally by editing YAML.
- **`ad_user_quiz_attempts`** — append-only NIST AU audit of every attempt (user_id, lesson_slug, score_pct, passed, correct_count, total, answers_json, attempted_at). Unlimited retries; each attempt logged for learning analytics.
- **Auto-completion on quiz pass** — on pass, lesson is idempotently marked complete + `grant_xp(reason='lesson_completed')` fires via the existing hook (dedup key prevents double-grants on retries). Lessons without a quiz keep the manual "Mark complete" button — zero BC break. Lessons WITH a quiz reject the manual-complete path (HTTP 400 `quiz_required`) so the curriculum gate can't be bypassed.
- **UI:** `/lessons` page gets lock icons + unlock hints ("Complete first: X") on locked lesson cards; locked lessons are click-dead. Inline Quick Check form at end of lesson content with per-question radio options; submit reveals green/red highlighting on chosen + correct answers + explanation text. Best attempt badge shown in page header.
- **API changes:**
  - `/api/lessons/catalog` → returns new shape: `levels: [{key, label, icon, unlocked, lock_hint, total, completed, percent, order}]` list + `lessons: [...with unlocked/lock_hint/has_quiz/quiz_pass_percent]`. Legacy `summary:{beginner:{total,completed,started}}` kept for BC.
  - `/api/lessons/<slug>` → returns 403 + hint when locked; includes `quiz: {pass_percent, questions[{id,text,options}], best_attempt}` when quiz present (correct answers stripped from payload)
  - `/api/lessons/<slug>/complete` (existing) → now refuses lessons with quizzes (redirects user to quiz path)
  - `/api/lessons/<slug>/quiz` (NEW) → `{answers: {qid: idx}}` → returns grade + feedback + auto-completion
- **Coherence 14/17** (same 3 pre-existing warns). End-to-end smoke verified — beginner unlocked, 2nd lesson gated on 1st, quiz grades 0%→fail + 100%→pass, completion propagates to gating.
- **Out of scope for 6.5 (documented):** certificates, free-text answers, authoring UI (operators edit YAML), new lesson content for intermediate/advanced (slugs remain, content is user work), per-attempt cooldowns/retries limits.

### ✅ Phase 6.6 — Live-trading graduation gate (DONE 2026-04-18) ⚠ compliance-sensitive
- **Enforcement point:** `alpaca_adapter.submit_order()` — before any non-paper `base_url` is hit, calls `graduation.enforce_live_order(user_id)` which raises `NotGraduatedError` (bubbled up as `AlpacaError`) with a specific "Remaining: X, Y, Z" message. Paper URLs bypass the gate entirely; the sandbox and all Phase 6.3.5 paper flows are unaffected.
- **Schema migration:** added `risk_disclosure_acknowledged_at` column to `ad_user_progression` via idempotent ALTER TABLE (the `live_trading_unlocked_at` column was already present from 6.1 DDL). New helpers `set_risk_disclosure_acknowledged(user_id)` + `set_live_trading_unlocked(user_id)` — both idempotent via COALESCE.
- **Criteria** (`args/graduation_criteria.yaml`, hot-reload, operator-tunable):
  - `paper_days` — 30 days since first XP event
  - `analyses_completed` — 25 rows in `ad_analysis_runs`
  - `achievements_earned` — 5 rows in `ad_user_achievements`
  - `sharpe_floor` — annualized Sharpe ≥ 0.5 over last 90 days of `ad_pf_daily_snapshots` daily returns. Requires ≥ 30 daily snapshots (thin history auto-fails with a specific note).
  - `risk_disclosure` — acknowledged by clicking through the modal (persists timestamp)
- **Escape hatch:** `ICDEV_GRADUATION_ENFORCED=false` env var disables the gate globally. Intentionally LOUD — UI surfaces an orange warning banner when override is active. YAML-level `enabled: false` has the same effect. **Operators should NEVER ship production with enforcement off.**
- **Risk-disclosure modal** renders from YAML `risk_disclosure.{heading, text}` — default copy is a reasonable paper-to-live warning, but the file carries a visible comment warning operators to replace with legal-counsel-reviewed language before production.
- **UI:** new **Live-trading graduation** card on `/progression` page — per-criterion progress tiles (green ✓ when met, amber ○ when not) + descriptions + actionable notes (e.g. "Need at least 30 daily snapshots to score — currently have 2"). Separate "Read & acknowledge risk disclosure" button + fullscreen modal + "Unlock live trading" button (disabled until all criteria green). Post-unlock: green banner shows the unlock date.
- **3 new routes:** `GET /api/progression/graduation` (status w/ per-criterion breakdown), `POST /api/progression/graduation/acknowledge` (records timestamp), `POST /api/progression/graduation/unlock` (flips the gate; HTTP 400 + specific missing-criteria list when not eligible).
- **Verified end-to-end:** fresh user shows all 5 criteria fail, risk-ack flips that one to met, unlock blocked with specific missing list, `enforce_live_order` raises `NotGraduatedError`, env-var escape hatch disables enforcement. Coherence 14/17 (same 3 pre-existing warns). Companion synced.
- **Out of scope for 6.6:** de-graduation (once unlocked, stays unlocked — backlog doesn't describe reverse path), admin override CLI (could land 6.6.5 if needed), daily Sharpe recalc job (currently computed on each `/api/progression/graduation` hit), per-broker / per-tenant risk profiles.

---

## 🎉 Phase 6 complete (2026-04-18)

All 6 sub-phases shipped: 6.1 (level + XP) · 6.2 (achievements) · 6.3 (challenges) · 6.3.5 (sandbox portfolios) · 6.4 (leagues) · 6.5 (curriculum + quizzes) · 6.6 (graduation gate). New pages: `/progression`, `/challenges`, `/leagues`. 8 new schema tables across 2 modules. XP events → achievements → levels → challenge wins → league standings → graduation — one coherent pipeline.

> ⚠ **Real-money / regulatory implications.** Multi-team challenges with
> *monetary* prizes touch SEC/FINRA territory in the US (could be
> regulated as "investment contests"). Recommended v1 approach:
> bragging-rights-only leaderboards, no cash prizes. Cash prizes need
> per-tenant legal review (DoD educational tenant won't have this issue,
> public-facing SaaS will). Phase 5 Stripe integration is NOT a
> prerequisite — and shouldn't be wired to challenge prizes without
> explicit legal sign-off.

---

## ✅ Phase 7.5 — Paper options (DONE 2026-04-19)

Single-leg option contracts inside the existing sandbox framework. Paper-only — no live-broker path in this iteration.

- **Data layer** `tools/trading/options/chain.py` — Alpaca options snapshot endpoint (`/v1beta1/options/snapshots/{underlying}`) primary, yfinance fallback when Alpaca creds missing (Greeks degrade). 5-minute cache. OCC symbol parse/format helpers.
- **Schema** — new mutable `ad_sandbox_option_positions` (1 row per (attempt, contract), signed qty, strike, expiry, avg_cost, last_price, greeks_json, settled_at). `ad_sandbox_orders.asset_class` column added (idempotent ALTER) distinguishing equity from option fills.
- **Engine** (`sandbox_engine.place_option_order`) — 4 action types: `buy_to_open`, `sell_to_close`, `sell_to_open`, `buy_to_close`. Fill at live mid ± 5 bps slippage + $0.65/contract commission (both config-tunable). Short-option margin check: `(cash_after + other_positions_mv) ≥ short_margin_ratio × strike × 100 × qty`, default 0.20× (simplified vs real Reg-T options margin).
- **Cash + MV math** — 100× share multiplier baked in. `sandbox_snapshot()` folds options into the same `total_value` as stocks; new `options_market_value` field surfaces separately in API response.
- **Expiry reflex** `challenge_option_expiry` (every 24h) — cash-settles ITM intrinsic × 100 × qty (long receives, short pays), zeroes OTM worthless, marks rows with `settled_at`. Idempotent.
- **UI** — new `/options` page with sandbox picker + chain viewer (calls/puts side-by-side at each strike, Greeks, IV) + Trade modal (action dropdown + qty). Open-position table per sandbox.
- **"Options Lab" challenge** — new catalog entry, 365-day duration, predicate never fires (uses scenario_completion with a never-run scenario key). Provides a long-running playground sandbox for pure options practice without a performance gate.
- **API:** `GET /api/options/chain/<ticker>`, `GET /api/options/contract/<symbol>`, `GET /api/options/active-attempts`, `POST /api/challenges/attempts/<id>/option-orders`, `GET /api/challenges/attempts/<id>/option-positions`.
- **Verified:** OCC parse/format roundtrip, position upsert + mark-to-market, expiry reflex (OTM path — no underlying quote = treated as OTM, position zeroed cleanly), snapshot shows options MV folded into total_value, 6 options routes registered. Coherence 17/17 clean.
- **Simplifications documented:**
  - European-style at expiry (no early exercise); ITM cash-settles intrinsic instead of exercising to shares. Real American options can be exercised any time.
  - No assignment simulation (short-option holder always receives cash settlement, never has to deliver shares).
  - Greeks trusted from Alpaca; no Black-Scholes recomputation. yfinance fallback shows `—` for Greeks.
  - Short-option margin = flat `0.20 × notional`. Real Reg-T options margin is much more nuanced (naked-short covered-short differences, etc.).
  - Payoff chart shows P&L at expiry only (no mid-life Black-Scholes projection).

### Deferred to follow-ups
- **Early exercise simulation** — only matters if we model assignment cascades.
- **Mid-life payoff projection** — Black-Scholes per-day P&L chart (slider UI).

## ✅ Phase 7.5 follow-ups A + B + C (DONE 2026-04-19)

### A — Multi-leg spreads (atomic sandbox fill)
- `ad_sandbox_orders.multileg_group_id` column (idempotent ALTER) links legs of one strategy.
- `sandbox_engine.place_multileg_order(legs=[])` — validates + pre-fetches all quotes, then fills legs sequentially via the existing `place_option_order` path. On any leg failure, auto-reverses already-filled legs (rollback). 1–4 legs (matching Alpaca MLeg cap). Enforces single underlying across legs.
- Route: `POST /api/challenges/attempts/<id>/multileg-orders`.

### B — Strategy library + payoff visualizer (OptionStrat-inspired)
- New module `tools/trading/options/strategies.py`. Hot-reloads `args/options_strategies.yaml` — 8 canonical strategies (long/short single-leg, covered call, cash-secured put, bull call / bear put spreads, iron condor, long straddle). Each entry carries leg templates + risk formulas (max_loss, max_profit, breakevens).
- `build_legs()` expands a template into concrete OCC symbols given user-picked strikes + expiry. `compute_payoff()` samples P&L across a price range at expiry; returns Chart.js-ready `{x, y, breakevens, max_profit, max_loss}`. Pure math — no network.
- UI: Strategy Builder tab on `/options` with dropdown + per-leg strike + premium inputs + live Chart.js payoff chart (green fill when positive, red when negative, zero-line highlighted). Submit-to-sandbox button routes through the multi-leg fill endpoint.
- Routes: `GET /api/options/strategies`, `POST /api/options/payoff`.
- Verified: long call $200 strike + $5 premium payoff correctly shows max_loss=-$500 + breakeven=$205; bull call spread (195/205 strikes, $8/$3) max_profit=$500 + breakeven=$200.

### C — Live-broker options path + graduation extension
- `alpaca_adapter.submit_option_order(contract_symbol, qty, side, ...)` + `submit_multileg_order(legs=[])` — the latter uses `order_class=mleg` per Alpaca's multi-leg spec. Both route through the graduation gate before hitting the live endpoint (paper URL bypasses, matching equity policy). `position_intent` parameter exposed so callers can pass buy_to_open / sell_to_close semantics.
- `alpaca_adapter.get_account_configurations()` — fetches `max_options_trading_level` for the approval-tier check.
- `graduation.enforce_live_option_order(user_id, alpaca_adapter, min_approval_level=2)` — new gate hook. Three checks in order: (1) global enforcement switch, (2) equity gate (user must be equity-graduated first — options inherit all equity criteria), (3) options-specific criteria (default: `paper_option_trades_completed ≥ 10`), (4) Alpaca approval level check (single-leg needs 2, multi-leg needs 3 per Alpaca conventions). Fail-closed when the broker API is unreachable (`OptionsApprovalMismatch` exception).
- `args/graduation_criteria.yaml` extended with `options_criteria:` + `options_risk_disclosure:` — the latter is an operator-replaceable addendum to the main risk modal (naked-short uncapped loss, 100x multiplier, assignment risk, IV crush warning).
- `GET /api/progression/graduation/options` — status endpoint for the options-specific UI banner.

### Coherence 17/17 clean. Companion synced.

### Still deferred (documented, non-blocking)
- **Early exercise simulation** — would need assignment cascades (ITM short call gets assigned → seller must deliver 100 shares → if no shares, borrow at margin). Not pedagogically needed for the paper sandbox.
- **Mid-life payoff projection** — Black-Scholes re-pricing at intermediate dates. UI would need a date slider. The static at-expiry payoff is accurate for MVP pedagogy.

## Phase 7+ (specialized infra — wait for actual demand)

- **Day-trader real-time stack — PARTIAL (DONE 2026-04-19)**: hot-keys (j/k/b/s) + 5-sec polling shipped (gated on `requires_realtime` persona flag). **Still infra-gated:** WebSocket tick feeds + L2 order book + sub-second exec.
- **Crypto universe — PARTIAL (DONE 2026-04-19)**: spot 10-pair routing through Alpaca `v1beta3/crypto/us` (BTC/ETH/SOL/AVAX/DOGE/LTC/LINK/BCH/UNI/AAVE), 24/7 pending-order sweep. **Still out:** on-chain signals, perps, lending, staking.
- **Family-office multi-asset — DONE 2026-04-19**: tax-lot tracking (FIFO/LIFO/specific_id) + ±30-day wash-sale flagging (IRS §1091, flag-only) + ST/LT report. `/api/taxes/{report,realizations,lots,wash-sale-flags}` live. Section 1256 + K-1 pass-throughs remain out.
- **DoD smart-card auth** — PIV/CAC cert-based auth. **Infra-gated** — needs reverse-proxy cert validation + DoD root CA trust chain at the deployment layer.
- ✅ **Compliance-officer dashboard — DONE 2026-04-19** — see below.
- ✅ **Phase 7.6 — AI-Assisted Options — DONE 2026-04-19** — see below.
- ✅ **Phase 7.7 — Probability & Compare — DONE 2026-04-19** — see below.
- ✅ **Phase 7.8 — Greeks Deep Dive + Quick Wins — DONE 2026-04-19** — see below.
- ✅ **Phase 7.9 — TA Foundation (Swing Pivots, Volume Profile, S/R, Patterns) — DONE 2026-04-19** — see below.

### ✅ Phase 7.9 — TA Foundation: Swing Pivots, Volume Profile, S/R, Patterns (DONE 2026-04-19)
Pure-Python, deterministic TA primitives layer. Project `args/projects.yaml → fathomdesk-ta`, prefix `ad79-`. No numpy. No external charting libs.
- **Swing-pivot detector** — `tools/trading/ta/swings.py::find_swings(bars, threshold_pct=1.5)`. Two-phase percentage-retracement algorithm (not N-bar lookback). Guarantees strict alternation `high → low → high …`. Config: `args/ta_config.yaml::swing_threshold_pct`.
- **Volume profile** — `tools/trading/ta/volume_profile.py::volume_profile(bars, bucket_count=40)`. 40 equal-width price buckets; volume distributed uniformly across spanned buckets. Returns `{buckets, poc, value_area{low,high}, hvns, lvns}`. VA = contiguous 70% of total volume around POC. HVN/LVN = top/bottom 20% by bucket volume.
- **S/R strength scoring** — `tools/trading/ta/support_resistance.py::compute_sr(bars, swings, cluster_pct=0.5)`. Merges swings within 0.5% by price; strength = `touches / max_touches` → [0, 1]. Sorted by strength descending. Visual encoding: opacity `0.15 + s×0.55`, stroke `0.8 + s×1.8 px`; resistance = dashed red, support = solid green.
- **Pattern detectors** — orchestrated by `tools/trading/ta/patterns/__init__.py::detect_patterns(bars)`. Deduplicates by type + bar-range overlap (keeps widest span).
  - `double.py` — double top/bottom: 3-swing geometry, 3% tolerance on matching swings.
  - `triple.py` — triple top/bottom: 3 same-kind swings within 3% of group mean.
  - `wedge.py` — rising/falling wedge: independent OLS trendlines through swing-highs and swing-lows; classified by slope signs and convergence direction.
- **Chart overlay** — `tools/dashboard/templates/fathomdesk.html::drawChart`. Pure-SVG, 9-layer render order (grid → VA rect → S/R lines → HVN lines → POC line → wedge trendlines → candlesticks → pattern badges → VP histogram). VP histogram shares Y axis with candle panel (85%/15% width split). Interactive: S/R hover tooltip, VP hover tooltip, pattern badge click → detail modal (geometry fields, confidence bar).
- **Route:** `GET /api/trading/chart/{ticker}?tf={tf}&limit=120` — returns `{bars, volume_profile, patterns, sr_levels}`.
- **Config:** `args/ta_config.yaml` — `swing_threshold_pct: 1.5`, `vp_bucket_count: 40`, `sr_proximity_pct: 0.5`, `pattern_tolerance_pct: 3.0`.
- **Tests:** `tests/test_ta_primitives.py` (zigzag alternation, V/W-shape counts, VA 65–75%, POC max-bucket, strength in [0,1]) + `tests/test_ta_patterns.py` (double/triple on synthetic swings, wedge OLS slopes, deduplication). Selenium E2E `tests/e2e_selenium/test_ad79_ta_foundation.py`.
- **Deferred:** Fibonacci retracements (needs user-selected pivots), VWAP (needs intraday ticks), Elliott Wave, pattern confidence ML scoring.

### ✅ Phase 7.8 — Greeks Deep Dive + Quick Wins (DONE 2026-04-19)
Three bundled OptionStrat-parity upgrades on top of 7.7. 18 tasks / 4 epics / prefix `ad78-`. No new LLM calls.
- **Black-Scholes pricer** — `tools/trading/options/pricing.py`. Closed-form BSM via `math.erf` (no numpy). `bs_price` + `bs_greeks` (Δ/Γ/Θ/ν/ρ). Graceful on degenerate inputs. 24 pytest cases green (parity, ATM symmetry, T→0 intrinsic, monotone invariants, textbook reference value, greek ranges, edge cases).
- **Time-T payoff** — `probability.compute_payoff_at_time(legs, spot_range, dte_remaining_days, iv)` prices each leg with BS at interim time. Same `{x, y, max_profit, max_loss, breakevens}` shape as `compute_payoff` so frontend swaps frames seamlessly. `proposal_builder` returns 5 frames (`+1d`, 25%, 50%, 75%, expiry). Alternates stay expiry-only for payload size.
- **Time-T slider UI** — drag above the payoff chart to reshape the curve as theta eats extrinsic. Label shows DTE-remaining. Auto-hides when fewer than 2 frames.
- **Portfolio Net Greeks** — `portfolio_greeks.compute_portfolio_greeks(user_id)` sums Δ/Γ/Θ/ν × 100 × signed qty over `ad_sandbox_option_positions`. Stale (no cached greeks) rows contribute zero and surface in `stale_count`. New route `GET /api/options/portfolio/greeks`. "📊 Portfolio Greeks" card on `/portfolio` with severity coloring; auto-refreshes 30s; auto-hides when position_count=0.
- **Shareable trade URLs** — `share.encode_proposal` / `decode_proposal` with 2kB size cap, no user_id/secrets/server-state in payload. `POST /api/options/ai-assist/share` returns `{token, url}`. "🔗 Share" button copies to clipboard. `/options?aiproposal=<token>` auto-loads AI Assist tab + pre-fills + submits; **server re-fetches chain + reruns preflight** — URL payload is untrusted.
- **Registration:** manifest entries + coherence 17/17 at every gate + companion sync 10 platforms. Selenium E2E `test_ad78_greeks_share.py`.

### ✅ Phase 7.7 — Probability & Compare (DONE 2026-04-19)
OptionStrat-inspired add-ons on top of the 7.6 proposal pipeline. 15 tasks / 3 epics / prefix `ad77-`, no new LLM calls.
- **Probability of Profit + price cone** — new `tools/trading/options/probability.py`. GBM Monte Carlo (default 10 000 paths, deterministic seed keyed on underlying+expiry+strikes). Returns `{pop_pct, expected_pnl, percentile_prices (p5-p95), pnl_distribution, iv_used_pct, model}` or `None` for intraday. Shares `_leg_pnl_at_expiry` with the payoff engine so the overlay is mathematically consistent.
- **Config:** `args/options_prob_config.yaml` — `n_samples: 10000`, `deterministic_seed: true`, `cone_bands: [5,25,50,75,95]`, `default_iv_fallback_pct: 40`, `min_dte_for_monte_carlo: 1`.
- **Chart overlay:** payoff canvas gets p5–p95 wide + p25–p75 dense goldenrod bands + dashed p50 line. Badge `POP: XX% (IV-implied)` sits next to the strategy title — the qualifier is load-bearing.
- **build_proposal upgrade:** alternates now carry full payoffs + POP + preflight (not just summary). `alternates_compact` kept for backward compat.
- **New helper:** `proposal_builder.build_for_strategy(intent, strategy_id)` — bypasses `rank_strategies` for explicit-strategy compare requests.
- **New endpoint:** `POST /api/options/ai-assist/compare` — returns N full proposals + preflight for side-by-side rendering. Server-side preflight on every one.
- **UI:** "Compare alternates" button reveals a 3-column grid (strategy, POP, max P/L, mini payoff chart, collapsible legs, "Use this one" promote button). Promoting swaps the primary card; disabled when that alternate has preflight blocks.
- **Tests:** 13 pytest cases in `tests/test_options_probability.py` (POP range, percentile ordering, monotone invariants, determinism, guards, short-vs-long premium asymmetry). Selenium E2E in `tests/e2e_selenium/test_ad77_probability_compare.py`.
- **Coherence 17/17 at every phase gate.** Companion synced 10 platforms.

### ✅ Phase 7.6 — AI-Assisted Options Strategy Creation (DONE 2026-04-19)
Hybrid design: LLM only for natural-language intent parsing + post-event coach recommendations; deterministic rules own strategy selection + strike/expiry picking (NIST AU auditable).
- **Flow:** intent textarea → `parse_intent` → `rank_strategies` (top-3) → `pick_expiry` + `pick_strikes` (delta-target) → `compute_payoff` → `run_preflight` → LLM rationale (grounded, no new numbers) → confirm modal → `place_multileg_order`.
- **Coach daemon** (`options_coach` reflex, 10m): scans `ad_sandbox_option_positions`, emits events on 50% profit / 2× loss / 7 DTE / 21 DTE roll window, LLM writes ≤3-sentence recommendations. Never auto-closes.
- **New modules:** `intent_parser`, `strategy_selector`, `strike_picker`, `proposal_builder`, `preflight`, `coach_db`, `coach_engine`, `coach_llm`.
- **New configs:** `args/options_intent_schema.yaml`, `options_strike_targets.yaml`, `options_risk_gates.yaml`, `options_coach_thresholds.yaml`.
- **New DB:** migration 020 → `ad_options_coach_events` (append-only NIST AU; mutable `recommendation` column only).
- **New routes:** `POST /api/options/ai-assist/{propose,execute}`, `GET /api/options/coach/events{,/id}`.
- **UI:** "AI Assist" tab in `/options` (intent → proposal modal with payoff chart + rationale + warnings/blocks + Execute button). "🎯 Options Coach" card on `/portfolio` (auto-hides when no events).
- **Project registry:** `args/projects.yaml → fathomdesk-7-6`, prefix `ad76-`, 26 tasks across 5 epics — all green.
- **Coherence 17/17 at every phase gate.** Companion synced 10 platforms. Selenium E2E `tests/e2e_selenium/test_ad76_ai_options_assist.py`.

### ✅ Compliance-officer dashboard (DONE 2026-04-19)
- **New module** `tools/trading/compliance/audit_aggregator.py` — unified read-only query layer across 12 append-only audit tables. Hot-reloads `args/nist_au_crosswalk.yaml` for per-table column mappings + NIST 800-53 AU control tags.
- **Sources unified:** `audit_trail`, `ad_credential_audit` (BYOK), `ad_stripe_events` (billing), `ad_mfa_attempts`, `ad_password_reset_tokens`, `ad_tenant_invitations`, `ad_trade_audit` (auto-trading), `ad_xp_events` (gamification), `ad_user_achievements`, `ad_sandbox_orders` (paper fills), `ad_sandbox_daily_snapshots`, `hook_events` (agent observability).
- **Normalization** — heterogeneous per-table schemas (different column names for timestamp, user, action, detail) collapse into one shape `{source, timestamp, user_id, action, detail, severity, category, raw}`. Category-based default severity, overridable via per-row `severity_hint` column mapping.
- **NIST 800-53 AU controls documented per table:** AU-2 (event logging), AU-3 (content), AU-4 (storage), AU-6 (review), AU-9 (protection), AU-12 (generation). Plus cross-control references (IA-2, AC-2, SI-4) where relevant.
- **New `/compliance` page (owner/admin only)** — 4 cards:
  1. **Activity summary** — 24h / 7d / 30d event totals + severity breakdown (info/warn/crit) + top-5 actions per window
  2. **Filters** — since/until datetime, category, source table, user_id, free-text search (ILIKE across action + detail)
  3. **Events table** — normalized rows with severity-colored cells, source + category tags, timestamp + user + action + detail columns
  4. **NIST AU crosswalk** — which controls each audit source satisfies; ATO evidence at a glance
- **CSV export** — `/api/compliance/audit.csv` with all filter params honored; timestamped filename; suitable for direct ATO package inclusion
- **Role gate** — `/compliance` + all `/api/compliance/*` routes require `role_in_tenant ∈ {owner, admin}`. Sidebar link only renders for admins. 403 from API on role mismatch.
- **Routes:** `/compliance`, `GET /api/compliance/{audit, audit.csv, summary, crosswalk}`
- **Verified end-to-end:** 12 tables registered, 528 events aggregated in 30d window from real audit data, severity breakdown correct (48 info + 1 warn last 24h), CSV export with normalized column order, role gate blocks non-admins.
- **Coherence 17/17 clean** (no regression). Companion synced.

### ✅ Phase 7.11 — News Intelligence Dashboard + Pattern Detection (DONE 2026-04-19)
News layer connecting macro/geopolitical events to the FathomDesk price chart. Full-page `/news` dashboard with RSS ingestion, 7-tab category filtering, sentiment aggregation, pattern detection, and one-click chart annotation.
- **`/news` dashboard** — 7 tabs: `all | macro | geopolitical | earnings | regulatory | sector | corporate`. Summary cards: News Reading (mood + sentiment counts), Cross-Signal Divergences, Regime Watch (`clusters` status=regime), Emerging Clusters (status=emerging). Per-item: impact badge, net\_direction badge, ticker chips, source, time-ago.
- **RSS ingestor** `tools/trading/news/rss_ingestor.py` — ingests + categorizes items at ingest time.
- **API endpoints** `tools/dashboard/api/news.py` — `GET /api/news`, `/api/news/category-summary/<cat>`, `/api/news/reading`, `/api/news/clusters`, `/api/news/divergences`, `/api/news/export.csv`, `/api/news/<id>`, `POST /api/news/<id>/analyze` (INTaaS stub → 501).
- **"Show on chart"** — `📈 Show on chart` on each card navigates to `/fathomdesk?ticker=<ticker>&highlight=<id>`; `maybeAnnotateNews()` draws a yellow dashed vertical annotation with "N" marker + hover tooltip at the matching bar timestamp.
- **Pattern analyzer** `tools/trading/news/pattern_analyzer.py` — two detectors: `regime_shift` (≥70% bearish in any category, ≥4 items, 24h window; severity: info/warn/critical) and `crackdown` (≥4 bearish regulatory items all mentioning same ticker; always critical). 4h cooldown guard prevents re-emission of same `(pattern_type, category)`. Results persisted to `ad_news_patterns` (migration 023, append-only NIST AU).
- **Genesis reflex** `tools/genesis/reflexes/alphadesk_news_patterns.py` — GREEN tier (read+write, no LLM, air-gap safe); promotes top-5 new patterns as GKP `capability_update` artifacts; cooldown guard prevents duplicate kanban suggestions across the 3h Genesis cadence.
- **DB tables** (`data/fathomdesk.db`): `ad_news_items`, `ad_news_scenario_links`, `ad_news_clusters`, `ad_news_patterns`.
- **Coherence 15/17** (openapi\_parity pre-existing gap). Companion synced 10 platforms. Selenium E2E `tests/e2e_selenium/test_ad711_news_2.py`.

### Phase 7+ remaining — honest status

With compliance shipped, the Phase 7+ list now has:
- 2 items **infra-gated** (day-trader real-time, DoD CAC) — these need deployment-layer infrastructure that doesn't exist yet (SIP subscription, reverse-proxy cert validation). Can't meaningfully ship without that foundation.
- 1 item **upstream-blocked** (crypto) — Alpaca Crypto API isn't mature enough per the project's own recorded constraint.
- 1 item **shippable but narrow audience** (family-office multi-asset) — tax-lot + wash-sale work benefits anyone who holds positions long-term; not just family offices. Could be worth ~1.5 sessions if a user asks.

**Net:** the "wait for actual demand" framing is honest. The items left aren't punting on complexity, they're punting on missing prerequisites or narrower use cases.

---

## Cross-cutting / always-on

### Light theme
`profile.theme` field exists; only `dark` is implemented. Light theme would
require a dual-CSS pass + theme-aware `--bg-*` / `--text-*` variables.
Persona presets already declare `theme: dark` for everyone, but `light` is
selectable in Settings. Land when retail/passive personas ask for it.

### i18n / localization
`profile.locale` field exists (defaults `en-US`); no gettext wrapping on
strings yet. Land when first non-English-speaking user lands.

### Reading voice — per-page customization
Currently the same voice applies to every page's Reading. Could add
"voice override per page" in Settings (e.g., quant might want technical for
/signals but pm-tone for /portfolio). Speculative; wait for ask.
