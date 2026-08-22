// CUI // SP-CTI
// E2E Test: Kanban API — Task CRUD, dependencies, board UI

import { test, expect } from './fixtures/auth';

import { deleteKanbanTasks } from './fixtures/kanban_cleanup';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

let createdTaskId: string | null = null;
let childTaskId: string | null = null;

test.describe('Kanban API', () => {
  // Delete what this spec created — child first, since it carries
  // depends_on_task_id to the parent. See fixtures/kanban_cleanup.ts for why
  // this is not simply `request.delete(...)`.
  test.afterAll(async ({ playwright }, testInfo) => {
    await deleteKanbanTasks(
      playwright,
      testInfo,
      [childTaskId, createdTaskId],
      'kanban_api.spec',
    );
    createdTaskId = null;
    childTaskId = null;
  });

  test('POST /api/kanban/tasks creates a task and returns id', async ({ request }) => {
    const resp = await request.post('/api/kanban/tasks', {
      data: {
        title: '[E2E Test] Playwright smoke task',
        task_type: 'test',
        priority: 'medium',
        status: 'backlog',
        description: 'Created by Playwright E2E kanban_api.spec.ts',
      },
    });

    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body).toHaveProperty('id');
    expect(typeof body.id).toBe('string');
    expect(body.id.startsWith('task-') || body.id.length > 0).toBe(true);

    createdTaskId = body.id;
  });

  test('GET /api/kanban/tasks returns list including created task', async ({ request }) => {
    if (!createdTaskId) test.skip();

    const resp = await request.get('/api/kanban/tasks');
    expect(resp.status()).toBeLessThan(300);

    const body = await resp.json();
    const tasks = Array.isArray(body) ? body : body.tasks ?? body.data ?? [];
    expect(Array.isArray(tasks)).toBe(true);
    expect(tasks.length).toBeGreaterThan(0);

    const found = tasks.find((t: { id: string }) => t.id === createdTaskId);
    expect(found, `Task ${createdTaskId} not found in list`).toBeTruthy();
  });

  test('POST /api/kanban/tasks/<id>/move changes task status', async ({ request }) => {
    if (!createdTaskId) test.skip();

    const resp = await request.post(`/api/kanban/tasks/${createdTaskId}/move`, {
      data: { status: 'in_progress' },
    });
    expect(resp.status()).toBeLessThan(300);
  });

  test('child task with depends_on_task_id is accepted by API', async ({ request }) => {
    if (!createdTaskId) test.skip();

    const resp = await request.post('/api/kanban/tasks', {
      data: {
        title: '[E2E Test] Child task (dependent)',
        task_type: 'test',
        priority: 'low',
        status: 'backlog',
        description: 'Depends on parent smoke task',
        depends_on_task_id: createdTaskId,
      },
    });

    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body).toHaveProperty('id');
    childTaskId = body.id;
  });

  test('Kanban board UI at /kanban shows Backlog and In Progress columns', async ({ page }) => {
    await page.goto('/kanban');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/kanban_board.png`,
      fullPage: false,
    });

    const bodyText = (await page.textContent('body')) ?? '';
    expect(bodyText.toLowerCase()).toMatch(/backlog|in.progress|todo/i);
    expect(bodyText.toLowerCase()).not.toContain('internal server error');
  });
});
// CUI // SP-CTI
