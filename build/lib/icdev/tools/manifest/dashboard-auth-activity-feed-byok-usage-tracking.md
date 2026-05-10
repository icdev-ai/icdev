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

