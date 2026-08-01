// CUI // SP-CTI
// E2E Test: Governed Delivery Pipeline — full-lifecycle proof (Phase 4)
//
// Drives a task through the delivery-pipeline view and asserts the canonical
// 9-stage lifecycle is both MODELED (pipeline API) and RENDERED (the shared
// task_pipeline.js stepper on the board), with `merged` as the terminal DONE
// gate. This spec is BOTH the regression test AND the canonical example of the
// pipeline the system always follows: if args/pipeline.yaml changes, STAGES
// below must change with it.

import { test, expect } from '@playwright/test';

import { deleteKanbanTasks } from './fixtures/kanban_cleanup';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

// The canonical 9 stages, in order — mirrors args/pipeline.yaml.
const STAGES = [
  'implement', 'code_quality', 'coherence', 'conformance',
  'unit_tests', 'e2e', 'pr', 'ci', 'merged',
];

let taskId: string | null = null;

test.describe('Governed Delivery Pipeline — full lifecycle', () => {
  // Delete the proof task. Without this it stays on the real board as a backlog
  // item the scheduler can promote and dispatch an agent against — see the
  // matching note in kanban_api.spec.ts. `request` is test-scoped, so afterAll
  // builds its own context from the worker-scoped `playwright` fixture.
  test.afterAll(async ({ playwright }, testInfo) => {
    await deleteKanbanTasks(playwright, testInfo, [taskId], 'kanban_pipeline.spec');
    taskId = null;
  });

  test('create a task for the pipeline proof', async ({ request }) => {
    const resp = await request.post('/api/kanban/tasks', {
      data: {
        title: '[E2E Pipeline] Full-lifecycle proof task',
        task_type: 'test',
        priority: 'low',
        status: 'backlog',
        description: 'Drives the delivery-pipeline view end to end (Phase 4 proof).',
        acceptance_criteria: 'The pipeline stepper renders all 9 canonical stages.',
      },
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body).toHaveProperty('id');
    taskId = body.id;
  });

  test('pipeline API returns the canonical 9-stage lifecycle with tooltips', async ({ request }) => {
    if (!taskId) test.skip();
    const resp = await request.get(`/api/kanban/tasks/${taskId}/pipeline`);
    expect(resp.status()).toBeLessThan(300);
    const d = await resp.json();
    expect(d.error, `pipeline error: ${d.error}`).toBeFalsy();

    const stages = d.stages ?? [];
    // exact canonical keys, in order
    expect(stages.map((s: { key: string }) => s.key)).toEqual(STAGES);

    // every stage is self-explanatory (tooltip requirement) + well-formed state
    for (const s of stages) {
      expect(s.tooltip, `stage ${s.key} missing tooltip`).toBeTruthy();
      expect(s.label, `stage ${s.key} missing label`).toBeTruthy();
      expect(['completed', 'current', 'pending']).toContain(s.state);
    }

    // `merged` is the terminal DONE gate — a task only becomes done here
    const merged = stages[stages.length - 1];
    expect(merged.key).toBe('merged');
    expect(String(merged.tooltip).toLowerCase()).toContain('done');
    // a fresh backlog task has NOT reached merged (done only after merge-verify)
    expect(merged.state).not.toBe('completed');
  });

  test('advancing status keeps the current stage well-formed', async ({ request }) => {
    if (!taskId) test.skip();
    const mv = await request.post(`/api/kanban/tasks/${taskId}/move`, {
      data: { status: 'in_progress' },
    });
    expect(mv.status()).toBeLessThan(300);

    const d = await (await request.get(`/api/kanban/tasks/${taskId}/pipeline`)).json();
    // current stage is always one of the canonical keys and never past `merged`
    expect(STAGES).toContain(d.current_stage);
    const mergedStage = (d.stages ?? []).find((s: { key: string }) => s.key === 'merged');
    expect(mergedStage?.state).not.toBe('completed');
  });

  test('board UI renders the 9-stage stepper for the task', async ({ page }) => {
    if (!taskId) test.skip();
    await page.goto('/kanban');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = (await page.textContent('body')) ?? '';
    expect(bodyText.toLowerCase()).not.toContain('internal server error');

    // Open the task card -> edit modal -> lifecycle section -> renderLifecycle()
    // (the shared task_pipeline.js fetches /pipeline and renders .progress-pipeline).
    const card = page.locator(`[data-task-id="${taskId}"]`).first();
    await expect(card).toBeVisible({ timeout: 15000 });
    await card.click();

    const stepper = page.locator('#task-lifecycle-body .progress-pipeline');
    await expect(stepper).toBeVisible({ timeout: 15000 });
    await expect(stepper.locator('.pipeline-step')).toHaveCount(STAGES.length);

    const stepperText = (await stepper.textContent()) ?? '';
    for (const label of ['Implement', 'Code Quality', 'Coherence', 'Conformance', 'Unit Tests', 'Merged']) {
      expect(stepperText, `stepper missing stage label "${label}"`).toContain(label);
    }

    // The lifecycle view surfaces the enforcement mode (ENFORCED vs RECORD-ONLY)
    // so users can tell at a glance whether the gates block done.
    const lifecycle = page.locator('#task-lifecycle-body');
    const lifecycleText = (await lifecycle.textContent()) ?? '';
    expect(lifecycleText).toContain('Pipeline mode');
    expect(lifecycleText).toMatch(/ENFORCED|RECORD-ONLY/);

    await page.screenshot({ path: `${SCREENSHOT_DIR}/kanban_pipeline_stepper.png`, fullPage: false });
  });
});
// CUI // SP-CTI
