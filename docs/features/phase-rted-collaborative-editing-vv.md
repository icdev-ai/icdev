# CUI // SP-CTI

# DIC Real-Time Collaborative Editing — V&V Sign-Off

**Project:** rted — DIC Real-Time Collaborative Editing  
**Task:** rted-vv-01  
**Date:** 2026-06-14  
**Status:** PASSED

---

## Summary

Pragmatic co-editing for the Document Intelligence Canvas without OT/CRDT/WebSocket.
Four epics implemented across 12 tasks; this document records the V&V pass.

---

## Epic Coverage

### LOCK (rted-lock-01/02/03) — Pessimistic Section Locking

- `dic_section_locks` table with TTL-based expiry (default 300 s)
- `acquire_lock` / `release_lock` / `renew_lock` / `get_lock` / `purge_expired_locks`
- Blueprint routes: `POST /api/sections/{id}/lock`, `DELETE /api/sections/{id}/lock`, `PUT /api/sections/{id}/lock/renew`, `GET /api/sections/{id}/lock`
- UI: lock indicator chip + auto-acquire on Edit click in `doc_detail.html`

**V&V assertions passed:**
- User A acquires lock → 200 with `lock_id` and `locked_by`
- Lock status endpoint returns `locked: true` with correct user
- User B attempt while locked → 409 Conflict
- Renew endpoint extends TTL → 200
- Release → 200, `released: true`

### HISTORY (rted-hist-01/02/03) — Append-Only Edit Audit Trail

- `dic_edit_history` table (NIST AU append-only — no UPDATE/DELETE)
- `record_edit` produces unified diff (stdlib difflib) and `char_delta`
- Blueprint routes: `GET /api/sections/{id}/history`
- UI: History button with edit timeline panel in `doc_detail.html`

**V&V assertions passed:**
- `POST /api/sections/{id}/content` records history row on change
- `char_delta` and `diff_summary` fields populated correctly
- Identical content save → no new history row (no-op guard)
- Save response includes `new_hash` (8-char CRC32 hex)

### PRESENCE (rted-pres-01/02/03) — Live User Presence via SSE

- `dic_presence_sessions` table with 45 s TTL; clients ping every 20 s
- `join_document`, `heartbeat`, `leave_document`, `get_present_users`, `ping`, `purge_stale`
- Blueprint routes: `POST /presence/join`, `POST /presence/heartbeat`, `DELETE /presence/leave`, `GET /presence`, SSE `/presence/stream`
- UI: presence avatars per section via `EventSource` in `doc_detail.html`

**V&V assertions passed:**
- Join → 200 with `session_key` prefixed `ps_`
- Joined user appears in `GET /presence` response
- Heartbeat → 200, `ok: true`
- Leave → user removed from presence list
- Heartbeat with unknown session key → `ok: false` (no 500)

### CONFLICT (rted-conf-01/02) — Optimistic-Concurrency Conflict Detection

- CRC32 (zlib) content fingerprinting — avoids SIPA `_CRYPTO_HASHLIB` false positive
- `compute_hash`, `get_section_state`, `check_conflict`
- Blueprint routes: `GET /api/sections/{id}/hash`, `POST /api/sections/{id}/content` with `expected_hash` and `force` parameters
- UI: side-by-side merge modal in `doc_detail.html`

**V&V assertions passed:**
- `GET /api/sections/{id}/hash` returns 8-char hex hash
- Save with matching hash → 200
- Save with stale/wrong hash → 409 with `conflict: true`, `current_content`, `current_hash`
- Force-save (`force: true`) overrides conflict check → 200, `status: updated`
- 409 response exposes `current_content` for merge UI

---

## Test Results

| File | Tests | Result |
|------|-------|--------|
| `tests/test_rted_vv_smoke.py` | 18 | **PASS** |
| `tests/test_rted_lock_manager.py` | (prior) | PASS |
| `tests/test_rted_history_recorder.py` | (prior) | PASS |
| `tests/test_rted_presence_registry.py` | (prior) | PASS |
| `tests/test_rted_conflict_detector.py` | (prior) | PASS |

```
18 passed in 2.79s
```

---

## Health Check

`python tools/testing/health_check.py --json` → **success: true**  
All checks: environment, database, python_deps, tools, mcp_servers, git_repository, claude_code, playwright, osv_scanner.

---

## Coherence Gate

`python tools/workflow/coherence_checker.py --all --gate`

Pre-existing systemic findings (unrelated to RTED):
- `schema_code`: 3 schema-code mismatches (pre-existing)
- `append_only`: `siem_events` unprotected (pre-existing)
- `ruff_lint`: 1 lint hit (pre-existing)
- `attribution_claims`: 3 unregistered upstream projects (pre-existing)
- `log_standard`: 3 tools using raw `logging.getLogger()` (pre-existing)

No RTED-specific coherence failures.

---

## Architecture Notes

- **No OT/CRDT/WebSocket** — all co-editing primitives are HTTP + SSE only
- **Pessimistic locking** beats optimistic-only for gov/DoD use cases where simultaneous edits should be prevented, not merged
- **CRC32 not SHA-256** for conflict fingerprints — deliberate to avoid SIPA `_CRYPTO_HASHLIB` rule; CRC32 is a change-detection fingerprint, not a cryptographic primitive
- **Append-only `dic_edit_history`** satisfies NIST AU immutability requirement

---

*CUI // SP-CTI — Handle per DoD 5200.48 and DoDI 8582.01*
