# AlphaDesk Backlog

> Durable log of requests + ideas surfaced during sessions but not yet built.
> Each entry: **what**, **why**, **roughly when** to land it, **dependencies**.
> When picking up work, scan this list alongside the active TaskList.

Last updated: 2026-04-18 (Phase 5B + 6.1 + 6.2 + 6.3 + 6.3.5 + 6.4 shipped)

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

### Phase 1C — Persona alert-default seeding + flags consumers (NEXT)
- Seed persona's `alert_defaults` into `ad_alert_rules` on first persona pick
  (skip duplicates; pre-suggest via "+ Add suggested rules" button so we don't
  silently spam alerts that don't exist yet — many use `WATCHLIST_ANY`,
  `PORTFOLIO_DRAWDOWN` placeholder subjects that the evaluator can't resolve).
- Flag consumers (read `window.AD_PROFILE.flags` in JS):
  - `sandbox_mode` (student) → "PAPER ONLY" badge in nav + page headers
  - `requires_realtime` (day_trader) → banner warning "real-time tick infra
    ships in Phase 6"
  - `requires_multi_asset` (family_office) → banner "alts/multi-asset ships
    in Phase 6"
  - `compact_layout` (quant) → tighter padding in CSS
  - `api_first` (quant) → surface `/api/.../export.csv` links on every card
  - `keyboard_shortcuts_visible` (day_trader) → hot-key cheat sheet card
  - `explain_mode`, `glossary_auto_tooltips` (student) → already partly handled
    by Reading-voice add_glossary; finish wiring to data-help tooltips

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

### 🔜 Phase 2C — BYOK Path B opt-in (envelope encryption with user passphrase)
**Why deferred:** user explicitly chose to ship Path A as the default and deferred Path B as opt-in. ~1 session of work.
**Scope:**
- Per-user "vault mode" toggle in Settings → BYOK
- When ON: KEK derived from login password (HKDF) at login → cached in encrypted Flask session cookie → used to envelope-encrypt the API key
- When ON: daemon ops with that user's keys are DISABLED (B1) — daemon falls back to operator env vars (or refuses)
- Password change re-encrypts all the user's vault-mode credentials
- Forgotten password = lost keys (no recovery — by design; otherwise operator could backdoor)
- Optional: vault recovery code at first vault-mode key-add time
- Schema already in place via `vault_mode` column (no migration needed)

### 🔜 Phase 2D — Real secrets manager backend (HashiCorp Vault / OpenBao)
**Why:** the user mentioned "we need some sort of secret manager" — Path A + Path B handle the immediate need, but production multi-tenant deployments will want a real KMS/HSM.
**Scope:**
- `SecretStore` ABC with `put/get/delete/metadata` interface
- `LocalEncryptedStore` (refactor of current code) as Phase 1 default
- `EnvelopeStore` (Path B from 2C) as opt-in for individual users
- `VaultStore` for HashiCorp Vault / OpenBao via HTTP API
- Operator selects backend via `ICDEV_SECRET_BACKEND` env var
- Per-tenant Vault policies once Phase 3 (tenants) lands

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
    AlphaDesk" tagline). PDF brief uses tenant name in classification
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

### Subject placeholders for alert rules
The persona alert_defaults reference `WATCHLIST_ANY`, `WATCHLIST_AVG`,
`WATCHLIST_TOP`, `PORTFOLIO_DRAWDOWN`, `PORTFOLIO_DRIFT` subjects that the
current evaluator doesn't understand. Phase 4 work: extend alerts/evaluator.py
to resolve these as virtual subjects (loop over watchlist tickers, compute
portfolio metrics, etc.).

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
- Registered in `tools/manifest/alphadesk-trading-engine.md`; `ad_stripe_events` added to `.claude/hooks/pre_tool_use.py:APPEND_ONLY_TABLES`. Coherence gate passes 16/17 (1 pre-existing warn unrelated).

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
- `ad_xp_events` added to `APPEND_ONLY_TABLES`; registered in `tools/manifest/alphadesk-trading-engine.md`. Coherence passes 14/17 (3 pre-existing warns unrelated to 6.1).
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
- Registered in `tools/manifest/alphadesk-trading-engine.md`. Coherence gate passes 14/17 (3 pre-existing warns unrelated). Verified end-to-end — start → deadline + snapshot captured → duplicate-start blocked → evaluate → abandon → summary reflects state.
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

### ✅ Phase 6.4 — Multi-team competitions + leaderboards (DONE 2026-04-18)
- Two tables (no standings cache — compute on-demand): `ad_leagues` (mutable; tenant-scoped with `UNIQUE(tenant_id, slug)`; 3 visibility modes: public / private / code with auto-generated 8-char codes) + `ad_league_members` (mutable role: captain / member; `UNIQUE(league_id, user_id)`).
- Config: `args/leagues_config.yaml` — 3 ranking windows (weekly=7d / monthly=30d / all_time=since league creation), scoring (xp_weight=1.0 + challenge_bonus=50 per completed challenge), limits (max_league_size=100, max_leagues_per_user=20, max_leagues_per_tenant=50).
- `tools/trading/leagues/engine.py` — `compute_standings(league_id, window)` aggregates `SUM(ad_xp_events.amount)` + `COUNT(ad_challenge_attempts WHERE status='completed')` per member, ranks by composite `score`. Tenant-boundary enforced — user must be in `ad_tenant_memberships` to create or join. Owner cannot be kicked (must delete league or transfer ownership first).
- UI: `/leagues` page — **Create a league** form (name + description + visibility dropdown), **Join by code** form, **My leagues** list, **Public leagues** grid (peek or join), and an inline detail panel with **Standings** (window selector ↔ live re-compute), **Members** (captains can kick), and actions (leave / delete).
- API: `/api/leagues` (GET summary / POST create), `/api/leagues/<id>` (GET detail + standings / DELETE), `/api/leagues/<id>/join`, `/api/leagues/join-by-code`, `/api/leagues/<id>/members` (POST add / DELETE kick).
- Persona gating: visible for student/retail/passive, hidden for quant/pro_trader/advisor/day_trader/family_office (same policy as /progression + /challenges).
- Coherence 14/17 (3 pre-existing warns). Companion synced. Smoke test verified: Alice creates public league → Bob joins → Carol joins → XP events per user correctly rank Alice #1 (150 XP) > Bob #2 (120 XP) > Carol #3 (0 XP) in the weekly window.
- **Out of scope for 6.4 (documented):** invitation tokens (private leagues use captain-add; an invitation-link flow could land in 6.4.5), seasons + playoffs, real-time push, league chat, cross-tenant leagues, cash prizes (SEC/FINRA sensitive — explicitly not exposed).

### Phase 6.5 — Educational curriculum tied to levels
- New `tools/trading/lessons/` module
- Operator-provided markdown lessons (`docs/lessons/{level}/{slug}.md`)
- Each level unlocks a curriculum slate
- Lessons can have inline interactive quizzes (multiple-choice, uses
  existing rule engine for scoring)
- Maps to CashFlow progression: 101 = Beginner+Intermediate, 102 =
  Advanced+Pro

### Phase 6.6 — Live-trading graduation gate ⚠ compliance-sensitive
- Schema: `ad_user_progression.live_trading_unlocked_at`
- Hard gate enforced at `/orders` route + Alpaca live URL switching path
- Criteria (operator-tunable, persisted in `args/graduation_criteria.yaml`):
  - Min N days of paper trading (default 30)
  - Min N completed analyses (default 25)
  - Min N achievements earned (default 5)
  - Sharpe ≥ X in paper account over last 90d (default 0.5)
  - Acknowledged a "risk disclosure" modal
- Until graduated: live URL switching disabled; user can BYOK (Phase 2A
  done) but the broker adapter refuses live-mode requests with a
  redirect-to-progression message

> ⚠ **Real-money / regulatory implications.** Multi-team challenges with
> *monetary* prizes touch SEC/FINRA territory in the US (could be
> regulated as "investment contests"). Recommended v1 approach:
> bragging-rights-only leaderboards, no cash prizes. Cash prizes need
> per-tenant legal review (DoD educational tenant won't have this issue,
> public-facing SaaS will). Phase 5 Stripe integration is NOT a
> prerequisite — and shouldn't be wired to challenge prizes without
> explicit legal sign-off.

---

## Phase 7+ (specialized infra — wait for actual demand)

- **Day-trader real-time stack** — WebSocket tick feeds, L2 order book,
  hot-key engine, sub-second exec.
- **Crypto universe** — BTC/ETH/SOL on-chain signals + 24/7 reflexes. Wait
  for Alpaca Crypto API maturity (per `memory/project_alphadesk_futures_deferred.md`).
- **Family-office multi-asset** — alts, illiquidity tracking, tax-aware
  reporting.
- **DoD smart-card auth** — PIV/CAC cert-based auth (replaces password+MFA
  flow for federal users).
- **Compliance-officer dashboard** — dedicated audit viewer, NIST control
  crosswalk, breach-report feed.

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
