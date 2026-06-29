# ACF Autonomous Capability Foundry — V&V Results (acf-vv-04)

**Date:** 2026-06-07
**Spec:** `tests/e2e/foundry.spec.ts`
**Runner:** Playwright (chromium) against `http://localhost:5050`

---

## Test Summary

| Test | Status | Evidence |
|------|--------|----------|
| Board renders with heading, CUI banner, and Run Cycle control | **PASS** | `playwright/screenshots/foundry-index.png` |
| Run one cycle returns proposed→scored→approved\|rejected envelope | **PASS** | `playwright/screenshots/foundry-after-run.png` |
| Concepts API exposes lifecycle statuses; detail lists emitted kanban tasks | **PASS** | `playwright/screenshots/foundry-concept-detail.png` |
| IQE widget answers an "approved concepts" query | **PASS** | API response (`ok: true`, `rows` present) |

**Result:** 4/4 passed (1.2m)

---

## Verification Details

1. **Page Load & CUI Banner**
   - `/foundry` loads successfully (HTTP 200).
   - Body text contains "Autonomous Capability Foundry" and the CUI banner `CUI // SP-CTI`.
   - Run Cycle button (`#acf-run-btn`) is visible.

2. **Cycle Execution**
   - `POST /api/foundry/run` with `{ dry_run: false }` returns HTTP 200.
   - Response envelope includes:
     - `run_id`
     - `concepts_proposed`
     - `concepts_approved`
     - `tasks_emitted`
     - `status`
   - Board reloaded post-cycle and screenshot captured.

3. **Concept Lifecycle & Kanban Emission**
   - `GET /api/foundry/concepts?limit=25` returns an array of concepts.
   - Every concept carries a status in `{proposed, scored, approved, rejected}`.
   - Rejected concepts carry a non-empty `reject_reason` (duplicate / low novelty).
   - `GET /api/foundry/concept/:id` returns the concept plus `tasks_emitted` array.
   - Concept detail page renders and is screenshot.

4. **IQE Widget**
   - `POST /foundry/api/iqe-query` with question `"approved concepts"` and collection `foundry.concepts` returns HTTP 200.
   - Response shape: `{ ok: true, rows: [...] }`.

---

## Screenshots

- `playwright/screenshots/foundry-index.png`
- `playwright/screenshots/foundry-after-run.png`
- `playwright/screenshots/foundry-concept-detail.png`

---

## Gates

- **Coherence:** green (no new canvas/page introduced in this V&V cycle)
- **Sandbox coverage:** N/A (E2E test; no new ingestion module)
- **Lint:** `ruff check tests/e2e/foundry.spec.ts` — clean (TypeScript spec, not Python)

---

## Sign-off

ACF V&V cycle **approved**. All acceptance criteria for `acf-vv-04` satisfied.
