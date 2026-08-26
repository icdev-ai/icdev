// CUI // SP-CTI
// Shared seed fixture for GovCon + CPMP lifecycle E2E tests.
// Provides oppId, contractId, wbsId, deliverableId via API calls (no UI).

import { APIRequestContext } from '@playwright/test';
import { resolveBaseUrl } from './base_url';

// ONE resolver, shared with playwright.config.ts and fixtures/auth.ts. Reading
// ICDEV_DASHBOARD_URL here while the config preferred ICDEV_E2E_BASE_URL is what
// sent the CSRF bootstrap to one origin and these requests to another, so every
// POST/PUT in the CPMP + GovCon specs came back 403 CSRF_FAILED
// (qa-fail-a5dbf266dfb0ce4a). See fixtures/base_url.ts.
export const BASE = resolveBaseUrl();
export const SS   = '.tmp/test_runs/screenshots';
export const CUI  = 'CUI // SP-CTI';

// A deliverable due date must be RELATIVE to the run, never a literal.
//
// These fixtures hardcoded '2026-06-30', which was comfortably in the future
// when written. It passed, and because the GCPL suite runs against the shared
// dashboard database, every run since has left a permanently-overdue CDRL
// behind. Five accumulated, and the CPMP monitor reflex — correctly reading the
// table — filed a program-management alarm card ("5 CDRL(s) are past due and
// not yet accepted... document delays with rationale for COR") against a
// contract that exists only for these tests. An absolute date in a fixture is a
// finding waiting to happen; a relative one keeps the tests' intent (a CDRL
// that has a due date) and cannot rot into a false alarm.
export function futureDate(daysAhead = 90): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + daysAhead);
  return d.toISOString().slice(0, 10);
}

export interface GovConCpmpFixture {
  oppId:          string;
  contractId:     string;
  wbsId:          string;
  deliverableId:  string;
  clinId:         string;
  draftId:        string;
}

// Seed a minimal opportunity directly into the proposals table.
// Falls back to the first existing opportunity if creation is not supported.
export async function seedOpportunity(request: APIRequestContext): Promise<string> {
  // Try to get existing opportunities first
  const listResp = await request.get(`${BASE}/api/govcon/sam/opportunities`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const opps = body.opportunities ?? body.data ?? body ?? [];
    if (Array.isArray(opps) && opps.length > 0) {
      const id = opps[0].id ?? opps[0].opp_id ?? opps[0].solicitation_number ?? String(opps[0].id ?? 'seed');
      return String(id);
    }
  }

  // Try proposals list as fallback (govcon opps are also in proposals)
  const propResp = await request.get(`${BASE}/proposals`);
  if (propResp.ok()) {
    // Extract first opportunity ID from proposals page HTML (rough parse)
    const html = await propResp.text();
    const match = html.match(/href="\/proposals\/([^"]+)"/);
    if (match) return match[1];
  }

  // Last resort: create a minimal seed record via import (uses test solicitation number)
  const importResp = await request.post(`${BASE}/api/govcon/sam/import/SEED-TEST-001`);
  if (importResp.ok()) {
    const body = await importResp.json().catch(() => ({}));
    return String(body.opp_id ?? body.id ?? 'seed-001');
  }

  return 'seed-001';
}

// Ensure a contract exists for the given opportunity. Returns contractId.
export async function seedContract(request: APIRequestContext, oppId: string): Promise<string> {
  // Check existing contracts
  const listResp = await request.get(`${BASE}/api/cpmp/contracts`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const contracts = body.contracts ?? body.data ?? body ?? [];
    if (Array.isArray(contracts) && contracts.length > 0) {
      return String(contracts[0].id ?? contracts[0].contract_id);
    }
  }

  // Create from opportunity
  const createResp = await request.post(`${BASE}/api/cpmp/from-opportunity/${oppId}`);
  if (createResp.ok()) {
    const body = await createResp.json().catch(() => ({}));
    const id = body.contract_id ?? body.id ?? body.data?.id;
    if (id) return String(id);
  }

  // Direct contract creation as final fallback
  const directResp = await request.post(`${BASE}/api/cpmp/contracts`, {
    data: {
      title: 'GCPL Seed Contract',
      agency: 'Test Agency',
      contract_type: 'FFP',
      cor_email: 'cor@test.gov',
      total_value: 1000000,
    },
  });
  if (directResp.ok()) {
    const body = await directResp.json().catch(() => ({}));
    return String(body.contract_id ?? body.id ?? 'seed-contract-1');
  }

  return 'seed-contract-1';
}

// Ensure a WBS element exists for the contract. Returns wbsId.
export async function seedWbs(request: APIRequestContext, contractId: string): Promise<string> {
  const listResp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/wbs`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const items = body.wbs ?? body.data ?? body ?? [];
    if (Array.isArray(items) && items.length > 0) {
      return String(items[0].id ?? items[0].wbs_id);
    }
  }

  const createResp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/wbs`, {
    data: {
      name: 'GCPL Seed WBS',
      bac: 500000,
      planned_start: '2026-01-01',
      planned_end: '2026-12-31',
      percent_complete: 0,
    },
  });
  if (createResp.ok()) {
    const body = await createResp.json().catch(() => ({}));
    return String(body.wbs_id ?? body.id ?? 'seed-wbs-1');
  }

  return 'seed-wbs-1';
}

// Ensure a deliverable exists for the contract. Returns deliverableId.
export async function seedDeliverable(request: APIRequestContext, contractId: string): Promise<string> {
  const listResp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/deliverables`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const items = body.deliverables ?? body.data ?? body ?? [];
    if (Array.isArray(items) && items.length > 0) {
      return String(items[0].id ?? items[0].deliverable_id);
    }
  }

  const createResp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/deliverables`, {
    data: {
      title: 'GCPL Seed CDRL',
      cdrl_number: 'A001',
      did_number: 'DI-MGMT-81466',
      cdrl_type: 'ssp',
      frequency: 'monthly',
      due_date: futureDate(),
    },
  });
  if (createResp.ok()) {
    const body = await createResp.json().catch(() => ({}));
    return String(body.deliverable_id ?? body.id ?? 'seed-del-1');
  }

  return 'seed-del-1';
}

// Ensure a CLIN exists for the contract. Returns clinId.
export async function seedClin(request: APIRequestContext, contractId: string): Promise<string> {
  const listResp = await request.get(`${BASE}/api/cpmp/contracts/${contractId}/clins`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const items = body.clins ?? body.data ?? body ?? [];
    if (Array.isArray(items) && items.length > 0) {
      return String(items[0].id ?? items[0].clin_id);
    }
  }

  const createResp = await request.post(`${BASE}/api/cpmp/contracts/${contractId}/clins`, {
    data: {
      clin_number: '0001',
      description: 'Labor — Software Engineering',
      clin_type: 'labor',
      total_value: 250000,
      funded_value: 250000,
    },
  });
  if (createResp.ok()) {
    const body = await createResp.json().catch(() => ({}));
    return String(body.clin_id ?? body.id ?? 'seed-clin-1');
  }

  return 'seed-clin-1';
}

// Ensure a draft exists for the opportunity. Returns draftId.
export async function seedDraft(request: APIRequestContext, oppId: string): Promise<string> {
  const listResp = await request.get(`${BASE}/api/govcon/opportunities/${oppId}/drafts`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const items = body.drafts ?? body.data ?? body ?? [];
    if (Array.isArray(items) && items.length > 0) {
      return String(items[0].id ?? items[0].draft_id);
    }
  }

  const createResp = await request.post(`${BASE}/api/govcon/opportunities/${oppId}/auto-draft`);
  if (createResp.ok()) {
    const body = await createResp.json().catch(() => ({}));
    const drafts = body.drafts ?? body.data ?? [];
    if (Array.isArray(drafts) && drafts.length > 0) {
      return String(drafts[0].id ?? drafts[0].draft_id ?? 'seed-draft-1');
    }
  }

  return 'seed-draft-1';
}

// Ensure a volume exists for the opportunity. Returns volumeId.
export async function seedVolume(request: APIRequestContext, oppId: string): Promise<string> {
  const listResp = await request.get(`${BASE}/api/proposals/opportunities/${oppId}/volumes`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const items = body.volumes ?? body.data ?? body ?? [];
    if (Array.isArray(items) && items.length > 0) {
      return String(items[0].id ?? items[0].volume_id);
    }
  }

  const createResp = await request.post(`${BASE}/api/proposals/opportunities/${oppId}/volumes`, {
    data: { title: 'Volume I — Technical', volume_number: 1, sort_order: 0 },
  });
  if (createResp.ok()) {
    const body = await createResp.json().catch(() => ({}));
    return String(body.id ?? body.volume_id ?? 'seed-vol-1');
  }

  return 'seed-vol-1';
}

// Ensure a proposal section exists for the opportunity. Returns sectionId.
export async function seedSection(
  request: APIRequestContext,
  oppId: string,
  volumeId: string,
): Promise<string> {
  const listResp = await request.get(`${BASE}/api/proposals/opportunities/${oppId}/sections`);
  if (listResp.ok()) {
    const body = await listResp.json().catch(() => ({}));
    const items = body.sections ?? body.data ?? body ?? [];
    if (Array.isArray(items) && items.length > 0) {
      return String(items[0].id ?? items[0].section_id);
    }
  }

  const createResp = await request.post(`${BASE}/api/proposals/opportunities/${oppId}/sections`, {
    data: {
      volume_id: volumeId,
      section_number: '1.1',
      title: 'GCPL Seed Section — Technical Approach',
      description: 'E2E seed section for route coverage.',
      priority: 'standard',
      sort_order: 0,
    },
  });
  if (createResp.ok()) {
    const body = await createResp.json().catch(() => ({}));
    return String(body.id ?? body.section_id ?? 'seed-sec-1');
  }

  return 'seed-sec-1';
}

// Full seed — builds the complete fixture chain and returns all IDs.
export async function buildFullFixture(request: APIRequestContext): Promise<GovConCpmpFixture> {
  const oppId        = await seedOpportunity(request);
  const contractId   = await seedContract(request, oppId);
  const wbsId        = await seedWbs(request, contractId);
  const deliverableId = await seedDeliverable(request, contractId);
  const clinId       = await seedClin(request, contractId);
  const draftId      = await seedDraft(request, oppId);

  return { oppId, contractId, wbsId, deliverableId, clinId, draftId };
}
// CUI // SP-CTI
