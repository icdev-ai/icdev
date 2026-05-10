# Intake Readiness Scoring — Implementation Plan

> Tracer-bullet vertical-slice plan (OPT-53). Each phase is a complete
> end-to-end path (data model → API → UI → tests) that is demoable alone.
> Upstream reference: https://github.com/mattpocock/skills/tree/main/prd-to-plan

## Source PRD

- **PRD:** `.tmp\sample_prd.md`
- **Generated:** `2026-04-13T00:53:53.316791+00:00`
- **Mode:** deterministic-skeleton

## Durable Architectural Decisions

These are stable across implementation (route paths, schema shapes, data model
names). Implementation details — file layouts, function names — are deliberately
omitted, since those will change during build.

- Route: POST /api/intake/score
- Schema: readiness_score record — session_id, score, dimensions[], computed_at
- UI: /intake/<session>/score page
- Each dimension returns a number 0–100 and a rationale string

## Phased Vertical Slices

Each phase below is a tracer bullet: thin end-to-end path, demoable on its own.

### Phase 1 — Skeleton tracer bullet

**User stories:**

- As an operator I can submit one minimal input through the new data model.
- As an operator I can fetch that input back via the new route.

**Demo checklist:**

- [ ] POST to the new route returns 200 with the submitted payload echoed.
- [ ] GET returns the stored record.
- [ ] A minimal UI button round-trips the payload.

### Phase 2 — Validation + error paths

**User stories:**

- As an operator I see clear validation errors on bad input.
- As an operator I see a graceful error state when the backend fails.

**Demo checklist:**

- [ ] Invalid payload returns 422 with a structured error body.
- [ ] UI renders the error inline with the offending field.
- [ ] Server logs record the failure with a correlation id.

### Phase 3 — Persistence + audit

**User stories:**

- As a reviewer I can see an append-only audit trail of submissions.
- As an operator my submission survives a server restart.

**Demo checklist:**

- [ ] Restarting the service preserves submissions.
- [ ] Audit list endpoint returns the sequence of actions with actor + timestamp.
- [ ] UI shows a read-only audit log for each record.


## Acceptance

- Every phase has a demo checklist and at least one verifiable outcome.
- Plan passes the tracer-bullet lint validator (no file or function names leak into phase descriptions).
- The durable decisions above do not change between phases; only implementation does.
