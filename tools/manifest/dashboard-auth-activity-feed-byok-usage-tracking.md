# Dashboard Auth, Activity Feed, BYOK & Usage Tracking (Phase 30)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Dashboard Auth, Activity Feed, BYOK & Usage Tracking (Phase 30)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Dashboard Auth | tools/dashboard/auth.py | API key auth, session mgmt, RBAC (5 roles), CLI bootstrap, auth logging | API key / session | User context |
| JWT Auth API | tools/dashboard/api/auth.py | Flask Blueprint + before_request middleware for /api/v1/*: HS256 JWT issuance (15-min access + 7-day refresh), @require_jwt decorator, CSRF double-submit cookie/header enforcement, PUBLIC_ENDPOINTS allow-list; dev credential check via ICDEV_DEV_USERS env var | POST /api/v1/auth/token (username+password), POST /api/v1/auth/refresh (refresh_token) | JWT access+refresh pair JSON; 401/403 on auth/CSRF failure |
| Dashboard BYOK | tools/dashboard/byok.py | BYOK key management: Fernet AES-256 encrypt/decrypt, key resolution (user→dept→env→config) | user_id, provider, key | Encrypted storage |
| WebSocket Manager | tools/dashboard/websocket.py | Flask-SocketIO init, room-based broadcast, graceful fallback to HTTP polling | app | SocketIO instance |
| Activity Feed API | tools/dashboard/api/activity.py | Merged audit_trail + hook_events UNION ALL, filters, polling, stats | source, event_type, actor | Merged events JSON |
| Admin API | tools/dashboard/api/admin.py | User CRUD, API key gen/revoke, auth log query (admin-only) | user data, key_id | User/key records |
| Usage API | tools/dashboard/api/usage.py | Per-user token aggregation, per-provider breakdown, time-series, cost estimates | user_id, days | Usage stats JSON |
| Activity Feed JS | tools/dashboard/static/js/activity.js | WebSocket + HTTP polling client, filter state, CSV export | (browser) | Real-time UI |
| Onboarding State | tools/auth/onboarding.py | Per-user wizard progress: get/update state, mark complete, last-seen version; backed by `user_preferences.onboarding_state` (JSON blob) | user_id, **kwargs | State dict |
| API Key Auth | tools/auth/api_key.py | API key generation (SHA-256 hashed, `ick_` prefix), verification (revocation + expiry checks), `@require_api_key` Flask decorator that sets `g.api_tenant_id` / `g.api_scopes`; stores only the hash — raw key shown once at creation | tenant_id, name, scopes, expires_at / raw_key | (key_prefix, raw_key, key_hash) or (tenant_id, scopes) |
| Auth Blueprint | tools/auth/blueprint.py | Flask blueprints for SAML 2.0 SSO (`auth_saml` at `/auth/saml`) and onboarding API (`onboarding_bp`). SAML routes: SP metadata XML, IdP-redirect login, ACS POST handler, OIDC login + callback. Onboarding routes: GET/PATCH `/api/onboarding/state`. Persists SSO sessions to `sso_sessions` table. | Flask app `register_blueprint(bp)` / `register_blueprint(onboarding_bp)` | HTTP redirects, XML/JSON responses, session cookie |
| SAML 2.0 SP | tools/auth/saml.py | SAML 2.0 Service Provider integration for enterprise SSO — stdlib-only (no external library). SP metadata generation (`generate_sp_metadata`), IdP redirect via HTTP-Redirect binding (`initiate_saml_login`), ACS POST response parsing (`process_acs_response`), and attribute mapping via provider `attr_mapping` JSON (`apply_attr_mapping`). Reads IdP SSO URL and entity ID from `sso_providers` table. | provider_id (str), saml_response_b64, attr_mapping_json | SP metadata XML string; IdP redirect URL; `{name_id, attributes, relay_state}` dict |
| SSO Session Validator | tools/auth/session.py | SSO session validation utility. `validate_sso_session(session_id)` looks up the session in `sso_sessions`, checks expiry, and returns session info dict; raises `ValueError` if not found or expired. | session_id (str) | `{id, tenant_id, provider_id, name_id, expires_at}` dict |

