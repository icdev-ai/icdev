# AlphaDesk Backlog

> Durable log of requests + ideas surfaced during sessions but not yet built.
> Each entry: **what**, **why**, **roughly when** to land it, **dependencies**.
> When picking up work, scan this list alongside the active TaskList.

Last updated: 2026-04-18

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
- Retail: "What should I do today?" daily prompt page
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

### Plan tiers + Stripe
Per the multi-session plan. ~3-5 sessions.

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

### Phase 6.1 — Level system + progression metrics
- New schema: `ad_user_progression` (user_id, level, xp, paper_trades_count,
  paper_pnl_total, paper_max_drawdown, paper_sharpe_window,
  regime_aware_decisions_count, ...)
- 4 levels: **Beginner → Intermediate → Advanced → Pro**
- "XP" earned per: completed analysis, signal acted on, regime-aware
  decision, alert acknowledged, etc.
- `/progression` page: current level, XP bar, next-level criteria, history

### Phase 6.2 — Achievements + badges
- New schema: `ad_achievements_catalog` (operator-curated) +
  `ad_user_achievements` (earned per user)
- Examples: "First Profitable Week", "Survived Drawdown" (held through a
  -15% then recovered), "Regime-Aware" (5 trades aligned with macro
  regime), "Diversified" (5+ sectors), "First 10 Trades", "First Million"
  (paper)
- Badge display: Settings → Profile, persona tile, leaderboard avatar

### Phase 6.3 — Single-player trade challenges
- New schema: `ad_challenges` (operator-curated) + `ad_challenge_attempts`
- Examples: "Achieve +5% in 30 days using only DJIA stocks", "Build a 60/40
  portfolio that beats SPY over 6 months", "Survive a STAGFLATION regime
  simulation"
- Time-boxed; sandboxed paper portfolio per attempt; final score recorded
- CashFlow-style narrative scenarios (you have $X, monthly expenses $Y,
  what do you buy?) — uses existing rule engine

### Phase 6.4 — Multi-team competitions + leaderboards
- New schema: `ad_leagues`, `ad_league_members`, `ad_league_standings`
- Tenant-scoped (Phase 3 prereq) — classroom league, firm-internal league,
  friend group
- Periodic standings (weekly / monthly / per-challenge)
- League visibility: public, private (invite-only), or league code
- Optional: "season" framing with playoffs

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
