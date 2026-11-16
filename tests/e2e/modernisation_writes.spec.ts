// CUI // SP-CTI
// E2E: every modernisation write the feature-conformance audit credits to a page is SENT
// BY THAT PAGE, as a person would send it (icx-mdz-01).
//
// The audit (tools/conformance/feature_audit.py) now judges six modernisation features:
// the 7R assessment, AI-Ify, Process-Ify, an application's waves and CAM jobs,
// cloud-native targets, and the ICDEV[OS] exporter. CI's E2E job runs its --e2e-gate over
// what tests/e2e/fixtures/observed_calls.ts recorded and fails on any credited write no
// page sent, so every test here presses the real control (page.click / fill) and waits for
// the request. A refusal is a fine answer (no model in CI, no pinned ICDEV[OS] JWKS): what
// is proved is that the page sends it.
//
// One request is answered by the test, and only its answer: Process-Ify's digitize asks a
// model to structure a document, and CI has none (ICDEV_NO_LLM). The page still SENDS it
// (recorded as the page's), and the test answers it with a fixed draft so the page goes on
// to send the save and clean-up writes for real.
//
// What the controls need on screen is seeded by tests/e2e/fixtures/modernisation_seed.py
// (a synthetic legacy application, analysed; CAM's cloud-native jobs over it; one observed
// draft). Everything is named mdz01-e2e-*.

import { test, expect } from './fixtures/observed_calls';
import type { Page, Request } from '@playwright/test';
import { spawnSync } from 'child_process';
import path from 'path';
import { PYTHON, icdevSubprocessEnv } from './fixtures/subprocess_env';

const ROOT = path.resolve(__dirname, '../..');
const SEED = path.join(ROOT, 'tests/e2e/fixtures/modernisation_seed.py');
const T = 30_000;

type Seeded = {
  project_id: string; app_id: string; source: string; observed_workflow_id: string;
  cloud_native: { jobs: { id: string; scaffold: string; status: string }[] };
};

function seed(): Seeded {
  // The seeder writes to the database the RUN asked for, never an ambient one.
  const res = spawnSync(PYTHON, [SEED], {
    cwd: ROOT, encoding: 'utf8', env: icdevSubprocessEnv({ PYTHONIOENCODING: 'utf-8' }),
  });
  if (res.status !== 0) throw new Error(`modernisation_seed.py failed: ${res.stderr || res.stdout}`);
  return JSON.parse(res.stdout.trim().split('\n').pop() || '{}');
}

/** Wait for the page to send `method` to a path matching `re`, while `act` runs. */
async function sends(page: Page, method: string, re: RegExp, act: () => Promise<unknown>): Promise<Request> {
  const req = page.waitForRequest(r => r.method() === method && re.test(new URL(r.url()).pathname), { timeout: T });
  await act();
  return req;
}

test.describe('Every credited modernisation write is sent by its page (icx-mdz-01)', () => {
  let s: Seeded;
  test.beforeAll(() => { s = seed(); });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
    page.on('dialog', d => { void d.accept(d.type() === 'prompt' ? 'mdz01 e2e: decided for the test' : undefined); });
  });

  // ── 7R: assess, then record the strategy ICDEV proposes ───────────────────
  test('7R: assess an application, then record the strategy it proposes', async ({ page }) => {
    await page.goto('/migration');
    await expect(page.locator('#seven-r-help')).toContainText('proposal to ICDEV[OS]');
    await page.fill('#assess-project', s.project_id);
    await page.fill('#assess-app', s.app_id);
    await sends(page, 'POST', /^\/api\/migration\/assess$/, () => page.click('button[onclick="runAssessment()"]'));
    await expect(page.locator('#assess-status')).toContainText('Assessment complete', { timeout: T });

    const row = page.locator('#assessments-body tr', { hasText: s.app_id }).first();
    await expect(row.locator('.record-reason')).toBeVisible({ timeout: T });
    await row.locator('.record-reason').fill('mdz01 e2e: recorded for the test');
    await sends(page, 'POST', /^\/api\/migration\/assessments\/[^/]+\/strategy$/,
      () => row.locator('button[data-decision="record"]').click());
    await expect(page.locator('#assessments-body tr', { hasText: s.app_id }).first())
      .toContainText('not binding', { timeout: T });
  });

  // ── AI-Ify: scan, promote, PRD, Kanban, IQE, posture, delete ──────────────
  test('AI-Ify: scan, accept the PRD, dry-run, send to Kanban, ask IQE, snapshot, delete', async ({ page }) => {
    await page.goto('/ai-ify/');
    await page.fill('#inputRef', s.source);
    await page.locator('#p1 button', { hasText: 'Next' }).click();
    await page.locator('#p2 button', { hasText: 'Next' }).click();
    await sends(page, 'POST', /^\/ai-ify\/api\/scan$/, () => page.click('#runBtn'));
    await expect(page.locator('#p4')).toBeVisible({ timeout: 60_000 });

    await page.locator('#p4 button', { hasText: 'Review' }).last().click();
    await sends(page, 'POST', /^\/ai-ify\/api\/generate-prd$/,
      () => page.locator('#p5 button', { hasText: 'Generate PRD' }).click());
    await expect(page.locator('#prdHitlBar')).toBeVisible({ timeout: T });
    await sends(page, 'POST', /^\/ai-ify\/api\/hitl-decision$/,
      () => page.locator('#prdHitlBar button', { hasText: 'Accept PRD' }).click());
    await expect(page.locator('#prdHitlMsg')).toContainText('Recorded by', { timeout: T });
    await sends(page, 'POST', /^\/ai-ify\/api\/prd-dry-run$/, () => page.locator('#p6 button', { hasText: 'Dry-run' }).click());
    await sends(page, 'POST', /^\/ai-ify\/api\/send-to-kanban$/,
      () => page.locator('#p6 button', { hasText: 'Send phase to Kanban' }).click());

    const widget = page.locator('.iqe-widget').first();
    await widget.locator('.iqe-widget__header').click();
    await widget.locator('textarea.iqe-ta').fill('show top opportunities by composite score');
    await sends(page, 'POST', /^\/ai-ify\/api\/iqe-query$/, () => widget.locator('.iqe-run-btn').click());

    await page.goto('/ai-ify/posture');
    await sends(page, 'POST', /^\/ai-ify\/api\/posture\/snapshot$/, () => page.click('#snap-btn'));

    await page.goto('/ai-ify/');
    const del = page.locator('[data-delete-scan]').first();
    await expect(del).toBeVisible({ timeout: T });
    await sends(page, 'DELETE', /^\/ai-ify\/api\/scan\/\d+$/, () => del.click());
    await expect(page.locator('#deleteMsg')).toContainText('Deleted', { timeout: T });
    await sends(page, 'DELETE', /^\/ai-ify\/api\/scan\/all$/, () => page.click('#deleteAllScans'));
  });

  // ── Process-Ify: digitize, save, clean up; approve an observed draft ──────
  test('Process-Ify: launch a process, clean up old runs, approve an observed draft', async ({ page }) => {
    await page.route('**/workflow-canvas/api/workflows/processify', route => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        source_doc_text: 'mdz01 e2e fixture process (synthetic)',
        workflow: {
          workflow_name: `mdz01 e2e process ${Date.now()}`, industry: 'General', basis: 'document', label: 'modelled',
          steps: [
            { id: 'step-1', title: 'Receive the request', assignee_role: 'Clerk', dependencies: [],
              checklist_items: [], form_fields: [{ label: 'Request id', type: 'text', required: true }] },
            { id: 'step-2', title: 'Approve the request', assignee_role: 'Manager', dependencies: ['step-1'],
              checklist_items: [], form_fields: [{ label: 'Decision', type: 'text', required: true }] },
          ],
        },
      }),
    }));
    await page.goto('/workflow-canvas/processify');
    await page.fill('#paste-text', 'mdz01 e2e: receive a request, then a manager approves it.');
    await page.check('#skip-kanban-chk');
    const saved = page.waitForRequest(r => r.method() === 'POST'
      && /^\/workflow-canvas\/api\/workflows\/save-processify$/.test(new URL(r.url()).pathname), { timeout: T });
    await sends(page, 'POST', /^\/workflow-canvas\/api\/workflows\/processify$/, () => page.click('#launch-btn'));
    await saved;
    await expect(page.locator('#btn-cleanup')).toBeVisible({ timeout: T });
    await sends(page, 'POST', /^\/workflow-canvas\/api\/workflows\/cleanup-processify$/, () => page.click('#btn-cleanup'));
    await page.unroute('**/workflow-canvas/api/workflows/processify');

    await page.goto('/workflow-canvas/processify');
    const approve = page.locator(`[data-observed-approve="${s.observed_workflow_id}"]`);
    await expect(approve).toBeVisible({ timeout: T });
    await sends(page, 'POST', new RegExp(`^/workflow-canvas/api/workflows/${s.observed_workflow_id}/observed-review$`),
      () => approve.click());
    await expect(page.locator('#observed-drafts-msg')).toContainText('approved by', { timeout: T });
  });

  // ── Waves, CAM jobs, cloud-native, the exporter ───────────────────────────
  test('Modernisation: waves, approve a cloud-native job, the bundle, check an ICDEV[OS] export', async ({ page }) => {
    await page.goto(`/migration-canvas/modernisation?app=${encodeURIComponent(s.app_id)}`);
    await expect(page.locator('#mdz-waves-body table')).toBeVisible({ timeout: T });
    await expect(page.locator('#mdz-bundle-body')).toContainText('icos-apptwin/v1', { timeout: T });
    const job = s.cloud_native.jobs.find(j => j.status === 'completed');
    expect(job, 'a completed cloud-native job was seeded').toBeTruthy();
    const row = page.locator(`#mdz-cloud-body tr[data-job-id="${job!.id}"]`);
    await expect(row.locator('.mdz-reason')).toBeVisible({ timeout: T });
    await row.locator('.mdz-reason').fill('mdz01 e2e: reviewed for the test');
    await sends(page, 'POST', new RegExp(`^/migration-canvas/api/modernisation/refactor-jobs/${job!.id}/approve$`),
      () => row.locator(`[data-approve-job="${job!.id}"]`).click());
    await expect(page.locator('#mdz-jobs-msg')).toContainText('Recorded approve', { timeout: T });

    await page.fill('#mdz-icos-export', '{"document": {}, "signature": {}}');
    await sends(page, 'POST', /^\/migration-canvas\/api\/modernisation\/icos-decisions\/check$/,
      () => page.click('#mdz-icos-check'));
    await expect(page.locator('#mdz-icos-body')).toContainText('untrusted', { timeout: T });
  });
});
