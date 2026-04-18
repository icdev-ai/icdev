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
  - 🔜 **2A.2.5** — Microsoft + GitHub OAuth (~30 min — add 2 more
    Authlib `register()` blocks + `configured_providers()` entries +
    update `is_any_provider_configured()`. Same shape as Google).
  - 🔜 **2A.3** — TOTP + backup codes + step-up MFA decorator + forced
    enrollment after `mfa_required_at` grace period.

### Legacy single-user data migration *(deferred subtask)*
Phase 2A.1 added `user_id` to the new auth + profile tables, but legacy
AlphaDesk tables still implicitly assume one user (`ad_portfolios`,
`ad_positions`, `ad_orders`, `ad_pf_daily_snapshots`, `ad_strategy_runs`,
`ad_strategy_holdings`, `ad_cis_recommendations`, `ad_analysis_runs`,
`ad_alerts_log`). Add `user_id TEXT` columns + backfill all rows to
the first user's id. Should land in a dedicated migration session before
multi-user really matters (i.e., before serious BYOK adoption).

### Phase 2B — WebAuthn / Passkeys
~1 session. Adds NIST AAL3 capability via hardware keys + platform
authenticators. Builds on 2A's `ad_user_mfa.webauthn_credentials` JSON column.

### **🆕 BYOK (Bring Your Own Key) for LLM + Alpaca** *(requested 2026-04-18)*
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

### Phase 3 — Multi-tenant data + white-label
Per the multi-session plan. ~3 sessions. `ad_tenants` table, `tenant_id`
columns on per-user tables, role-based perms, per-tenant logo + accent color.

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

## Phase 6+ (specialized infra — wait for actual demand)

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
