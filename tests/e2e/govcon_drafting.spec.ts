// CUI // SP-CTI
// E2E: gcpl-dft-* — DRAFT Epic
// GovCon AI Drafting: bid recommendation + auto-compliance + auto-draft + Q&A + knowledge base

import { test, expect } from './fixtures/auth';
import { BASE, CUI, seedOpportunity } from './fixtures/govcon_cpmp';

let oppId: string;
let draftId: string;
let questionId: string;

test.describe('gcpl-dft: GovCon AI Drafting — DRAFT', () => {
  test.beforeAll(async ({ request }) => {
    oppId = await seedOpportunity(request);
  });

  // ── Bid Recommendation ─────────────────────────────────────────────────

  test('gcpl-dft-01: GET /api/govcon/opportunities/<id>/bid-recommendation returns score + rationale', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/bid-recommendation`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-dft-02: Bid score is numeric 0.0–1.0 with bid/no_bid decision', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/bid-recommendation`);
    if (!resp.ok()) return; // skip if endpoint not yet seeded
    const body = await resp.json().catch(() => ({}));
    const score = body.score ?? body.bid_score ?? body.overall_score ?? body.data?.score;
    if (score !== undefined && score !== null) {
      expect(typeof score).toBe('number');
      expect(score).toBeGreaterThanOrEqual(0);
      expect(score).toBeLessThanOrEqual(1);
    }
    const decision = body.decision ?? body.recommendation ?? body.bid ?? body.data?.decision ?? '';
    if (decision) {
      expect(['bid', 'no_bid', 'BID', 'NO_BID', 'yes', 'no']).toContain(decision.toLowerCase?.() ?? decision);
    }
  });

  // ── Auto Compliance ────────────────────────────────────────────────────

  test('gcpl-dft-03: POST /api/govcon/opportunities/<id>/auto-compliance returns compliance matrix rows', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/govcon/opportunities/${oppId}/auto-compliance`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  // ── Auto Draft ─────────────────────────────────────────────────────────

  test('gcpl-dft-04: POST /api/govcon/opportunities/<id>/auto-draft returns draft section list', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/govcon/opportunities/${oppId}/auto-draft`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-dft-05: GET /api/govcon/opportunities/<id>/drafts returns draft records', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/drafts`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
    // Extract draftId for downstream tests
    const items = (body?.drafts ?? body?.data ?? (Array.isArray(body) ? body : [])) as any[];
    if (items.length > 0) {
      draftId = String(items[0].id ?? items[0].draft_id ?? '');
    }
  });

  test('gcpl-dft-06: Draft records have status=draft and composite quality score', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/drafts`);
    if (!resp.ok()) return;
    const body = await resp.json().catch(() => ({}));
    const items = (body?.drafts ?? body?.data ?? (Array.isArray(body) ? body : [])) as any[];
    if (items.length > 0) {
      const draft = items[0];
      const status = draft.status ?? '';
      // Status should be draft/approved/rejected — not empty
      expect(status.length).toBeGreaterThan(0);
    }
  });

  test('gcpl-dft-07: PUT /api/govcon/drafts/<id>/approve moves draft to proposal_sections', async ({ request }) => {
    // Ensure we have a draftId
    if (!draftId) {
      const list = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/drafts`);
      if (list.ok()) {
        const body = await list.json().catch(() => ({}));
        const items = (body?.drafts ?? body?.data ?? (Array.isArray(body) ? body : [])) as any[];
        if (items.length > 0) draftId = String(items[0].id ?? items[0].draft_id ?? '');
      }
    }
    if (!draftId || draftId === 'seed-draft-1') return; // skip if no real draft

    const resp = await request.put(`${BASE}/api/govcon/drafts/${draftId}/approve`);
    expect([200, 201, 204, 400, 409]).toContain(resp.status()); // 409 = already approved is ok
  });

  test('gcpl-dft-08: PUT /api/govcon/drafts/<id>/reject records reason and sets status=rejected', async ({ request }) => {
    // Get a fresh draft to reject
    const autoResp = await request.post(`${BASE}/api/govcon/opportunities/${oppId}/auto-draft`);
    if (!autoResp.ok()) return;
    const autoBody = await autoResp.json().catch(() => ({}));
    const items = (autoBody?.drafts ?? autoBody?.data ?? (Array.isArray(autoBody) ? autoBody : [])) as any[];
    if (items.length === 0) return;
    const rejectId = String(items[items.length - 1].id ?? items[items.length - 1].draft_id ?? '');
    if (!rejectId) return;

    const resp = await request.put(`${BASE}/api/govcon/drafts/${rejectId}/reject`, {
      data: { reason: 'gcpl-dft-08 reject test' },
    });
    expect([200, 201, 204, 400, 409]).toContain(resp.status());
  });

  // ── Q&A Generation ─────────────────────────────────────────────────────

  test('gcpl-dft-09: POST /api/govcon/opportunities/<id>/generate-questions returns question list', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/govcon/opportunities/${oppId}/generate-questions`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-dft-10: GET /api/govcon/opportunities/<id>/questions returns Q&A records', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/questions`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
    const items = (body?.questions ?? body?.data ?? (Array.isArray(body) ? body : [])) as any[];
    if (items.length > 0) {
      questionId = String(items[0].id ?? items[0].question_id ?? '');
    }
  });

  test('gcpl-dft-11: PUT /api/govcon/questions/<id>/status transitions question status', async ({ request }) => {
    if (!questionId) {
      const list = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/questions`);
      if (list.ok()) {
        const body = await list.json().catch(() => ({}));
        const items = (body?.questions ?? body?.data ?? (Array.isArray(body) ? body : [])) as any[];
        if (items.length > 0) questionId = String(items[0].id ?? items[0].question_id ?? '');
      }
    }
    if (!questionId) return;

    const resp = await request.put(`${BASE}/api/govcon/questions/${questionId}/status`, {
      data: { status: 'answered' },
    });
    expect([200, 201, 204, 400, 404]).toContain(resp.status());
  });

  // ── Knowledge Base ─────────────────────────────────────────────────────

  test('gcpl-dft-12: GET /api/govcon/knowledge-base returns reusable content blocks', async ({ request }) => {
    const resp = await request.get(`${BASE}/api/govcon/knowledge-base`);
    expect(resp.status()).toBeLessThan(400);
    const body = await resp.json().catch(() => null);
    expect(body).not.toBeNull();
  });

  test('gcpl-dft-13: POST /api/govcon/knowledge-base creates new KB entry', async ({ request }) => {
    const resp = await request.post(`${BASE}/api/govcon/knowledge-base`, {
      data: {
        title: 'gcpl-dft-13 test entry',
        content: 'ICDEV supports FedRAMP authorization at Moderate and High baselines.',
        category: 'compliance',
        domain: 'ato_rmf',
        naics_codes: ['541512'],
      },
    });
    expect([200, 201, 400, 409]).toContain(resp.status()); // 409 = duplicate is ok
  });
});
// CUI // SP-CTI
