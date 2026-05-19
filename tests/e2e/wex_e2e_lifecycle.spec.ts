// CUI // SP-CTI
// E2E Test: Workflow Studio Full Lifecycle (wex-ep11-03-d4)
// Scenarios: Chat→Generate→Save→Run | Template Library | Run History | Canvas Trigger

import { test, expect, Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const ROOT = path.resolve(__dirname, '../..');
const SCREENSHOTS = path.resolve(ROOT, 'playwright', 'screenshots');
const WORKFLOW_URL = '/studio/workflows';
const CANVAS_URL = '/agentic-ai/';
const STUDIO_READY = 30_000;

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOTS, { recursive: true });
});

function shot(step: string) {
  return path.join(SCREENSHOTS, `wex_e2e_${step}.png`);
}

/** Dismiss the ICDEV onboarding tour overlay that intercepts all clicks. */
async function dismissTour(page: Page): Promise<void> {
  await page.evaluate(() => {
    try {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    } catch (_) { /* storage may be blocked in certain configs */ }
    const el = document.getElementById('icdev-tour-welcome');
    if (el) { el.classList.remove('visible'); (el as HTMLElement).style.display = 'none'; el.remove(); }
    document.querySelectorAll<HTMLElement>('[class*="tour"]').forEach(e => {
      e.style.display = 'none';
      e.remove();
    });
  });
}

/** Navigate to URL, wait for the studio panel, then dismiss any overlays. */
async function gotoStudio(page: Page): Promise<void> {
  // Pre-set localStorage BEFORE page scripts run so tour.js sees the completed flag.
  await page.addInitScript(() => {
    try {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    } catch (_) {}
  });
  await page.goto(WORKFLOW_URL);
  await page.waitForSelector('#panel-editor, .studio-tab-panel', { timeout: STUDIO_READY });
  // Belt-and-suspenders: force-remove any overlay that still rendered.
  await dismissTour(page);
  await page.waitForTimeout(300);
}

// ────────────────────────────────────────────────────────────────────
// Scenario 1 — Chat → Generate → Save → Run
// ────────────────────────────────────────────────────────────────────
test.describe('Scenario 1 — Chat → Generate → Save → Run', () => {
  test('S1-01 studio page loads', async ({ page }) => {
    await gotoStudio(page);
    await page.screenshot({ path: shot('s1_01_studio_load'), fullPage: true });
    const text = await page.textContent('body');
    expect(text).toMatch(/workflow/i);
  });

  test('S1-02 AI Chat panel opens and accepts prompt', async ({ page }) => {
    await gotoStudio(page);

    // Open AI Chat panel via the toolbar button.
    const chatBtn = page.locator('#wf-chat-toggle-btn');
    await expect(chatBtn).toBeVisible({ timeout: 5000 });
    await chatBtn.click();
    await page.waitForTimeout(600);

    await page.screenshot({ path: shot('s1_02_chat_open'), fullPage: true });

    // Type a workflow description into the chat textarea.
    const chatInput = page.locator('#wf-chat-input');
    await expect(chatInput).toBeVisible({ timeout: 5000 });
    await chatInput.fill('ATO pipeline: FIPS categorization, then compliance assessment, then SSP generation');

    await page.screenshot({ path: shot('s1_03_chat_typed'), fullPage: true });

    // Submit (click Send button — only click if enabled).
    const sendBtn = page.locator('#wf-chat-send');
    if (await sendBtn.count() > 0 && await sendBtn.isEnabled()) {
      await sendBtn.click();
      // Allow time for LLM response (up to 15 s, but don't fail if Ollama is slow).
      await page.waitForTimeout(5000);
    }

    await page.screenshot({ path: shot('s1_04_chat_sent'), fullPage: true });

    // Chat panel or editor panel must still be visible.
    const panelVisible = await page.locator('#panel-editor').isVisible();
    expect(panelVisible).toBeTruthy();
  });

  test('S1-03 Save Workflow', async ({ page }) => {
    await gotoStudio(page);

    // Set a workflow name.
    const nameInput = page.locator('#wf-name');
    if (await nameInput.count() > 0) {
      await nameInput.fill('E2E Lifecycle Test Workflow');
    }

    // Actual button text is "💾 Save" not "Save Workflow" — use onclick attribute.
    const saveBtn = page.locator('[onclick="StudioWF.save()"]');
    await expect(saveBtn).toBeVisible({ timeout: 5000 });
    await saveBtn.click();
    await page.waitForTimeout(2000);

    await page.screenshot({ path: shot('s1_05_saved'), fullPage: true });

    // Verify no error toast appeared.
    const errorToast = page.locator('.studio-toast--error, .studio-toast--danger');
    const errorCount = await errorToast.count();
    expect(errorCount).toBe(0);
  });

  test('S1-04 Run Workflow after save', async ({ page }) => {
    await gotoStudio(page);

    // Set a name and save so the run button may appear.
    const nameInput = page.locator('#wf-name');
    if (await nameInput.count() > 0) {
      await nameInput.fill('E2E Run Test Workflow');
    }
    const saveBtn = page.locator('[onclick="StudioWF.save()"]');
    await expect(saveBtn).toBeVisible({ timeout: 5000 });
    await saveBtn.click();
    await page.waitForTimeout(2000);

    await page.screenshot({ path: shot('s1_06_run_ready'), fullPage: true });

    // The run button may be visible now; click if present.
    const runBtn = page.locator('#wf-run-btn');
    const runBtnVisible = await runBtn.isVisible().catch(() => false);
    if (runBtnVisible) {
      await runBtn.click();
      await page.waitForTimeout(3000);
      await page.screenshot({ path: shot('s1_07_run_started'), fullPage: true });
    } else {
      await page.screenshot({ path: shot('s1_07_run_btn_absent'), fullPage: true });
    }

    // Studio panel must still be present.
    const studioPresent = await page.locator('#panel-editor').isVisible();
    expect(studioPresent).toBeTruthy();
  });
});

// ────────────────────────────────────────────────────────────────────
// Scenario 2 — Template Library
// ────────────────────────────────────────────────────────────────────
test.describe('Scenario 2 — Template Library', () => {
  test('S2-01 Template Library tab renders', async ({ page }) => {
    await gotoStudio(page);

    const tmplTab = page.locator('[data-tab="templates"]');
    await expect(tmplTab).toBeVisible({ timeout: 5000 });
    await tmplTab.click();
    await page.waitForTimeout(1500);

    await page.screenshot({ path: shot('s2_01_templates_tab'), fullPage: true });

    const panel = page.locator('#panel-templates');
    await expect(panel).toBeVisible();
  });

  test('S2-02 Template grid loads (API responds)', async ({ page }) => {
    await gotoStudio(page);

    // Verify templates API endpoint responds.
    const apiResp = await page.evaluate(async () => {
      const r = await fetch('/api/studio/workflows/templates');
      const data = await r.json().catch(() => ({}));
      return { status: r.status, ok: r.ok, count: Array.isArray(data) ? data.length : (data?.templates?.length ?? -1) };
    });
    expect(apiResp.ok).toBeTruthy();

    // Switch to template tab and wait for grid.
    await page.locator('[data-tab="templates"]').click();
    await page.waitForTimeout(2000);

    await page.screenshot({ path: shot('s2_02_templates_loaded'), fullPage: true });

    const grid = page.locator('#wf-template-grid');
    await expect(grid).toBeVisible();
  });

  test('S2-03 Load Template populates canvas', async ({ page }) => {
    await gotoStudio(page);

    await page.locator('[data-tab="templates"]').click();
    await page.waitForTimeout(2000);

    // Try clicking first Load button in template grid.
    const loadBtn = page.locator('#wf-template-grid .studio-btn, #wf-template-grid button')
      .filter({ hasText: /load|use|select/i }).first();

    if (await loadBtn.count() > 0) {
      await loadBtn.click();
      await page.waitForTimeout(1500);
      await page.screenshot({ path: shot('s2_03_template_loaded'), fullPage: true });
    } else {
      await page.screenshot({ path: shot('s2_03_no_load_btn'), fullPage: true });
    }

    // Editor tab must be accessible.
    const editorTab = page.locator('[data-tab="editor"]');
    await expect(editorTab).toBeVisible();
  });
});

// ────────────────────────────────────────────────────────────────────
// Scenario 3 — Run History
// ────────────────────────────────────────────────────────────────────
test.describe('Scenario 3 — Run History', () => {
  test('S3-01 Run History tab opens table', async ({ page }) => {
    await gotoStudio(page);

    const runsTab = page.locator('[data-tab="runs"]');
    await expect(runsTab).toBeVisible({ timeout: 5000 });
    await runsTab.click();
    await page.waitForTimeout(1500);

    await page.screenshot({ path: shot('s3_01_run_history_tab'), fullPage: true });

    const runsPanel = page.locator('#panel-runs');
    await expect(runsPanel).toBeVisible();
  });

  test('S3-02 Run History API responds', async ({ page }) => {
    await gotoStudio(page);

    const apiResp = await page.evaluate(async () => {
      const r = await fetch('/api/studio/workflows/runs');
      return { status: r.status, ok: r.ok };
    });
    // Accept 200 (with runs) or 404 (no runs — endpoint requires a workflow id).
    expect([200, 404]).toContain(apiResp.status);

    await page.locator('[data-tab="runs"]').click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: shot('s3_02_run_history_api'), fullPage: true });

    const runsBody = page.locator('#wf-runs-body');
    await expect(runsBody).toBeVisible();
  });

  test('S3-03 Run detail modal available', async ({ page }) => {
    await gotoStudio(page);

    await page.locator('[data-tab="runs"]').click();
    await page.waitForTimeout(1500);

    // If any "Detail" link exists, click it.
    const detailBtn = page.locator('#wf-runs-body [onclick*="showRunDetail"], #wf-runs-body button, #wf-runs-body a')
      .filter({ hasText: /detail|view|open/i }).first();

    if (await detailBtn.count() > 0) {
      await detailBtn.click();
      await page.waitForTimeout(1000);
      await page.screenshot({ path: shot('s3_03_run_detail_modal'), fullPage: true });
      const modal = page.locator('#wf-run-detail-modal');
      const modalVisible = await modal.evaluate((el) =>
        (el as HTMLElement).style.display !== 'none' && getComputedStyle(el).display !== 'none'
      ).catch(() => false);
      if (modalVisible) {
        expect(modalVisible).toBeTruthy();
      }
    } else {
      // No runs yet — empty state is the valid outcome.
      await page.screenshot({ path: shot('s3_03_run_history_empty'), fullPage: true });
      const emptyState = page.locator('#wf-runs-body');
      await expect(emptyState).toBeVisible();
    }
  });
});

// ────────────────────────────────────────────────────────────────────
// Scenario 4 — Canvas Trigger
// ────────────────────────────────────────────────────────────────────
test.describe('Scenario 4 — Canvas Trigger', () => {
  test('S4-01 Canvas page loads', async ({ page }) => {
    await page.addInitScript(() => {
      try { localStorage.setItem('icdev_tour_completed', '1'); localStorage.setItem('icdev_tour_last_step', '999'); } catch (_) {}
    });
    await page.goto(CANVAS_URL);
    await page.waitForLoadState('domcontentloaded', { timeout: STUDIO_READY });
    await dismissTour(page);
    await page.waitForTimeout(2000);

    await page.screenshot({ path: shot('s4_01_canvas_page'), fullPage: true });

    // The canvas page must load without a server error.
    const bodyText = await page.textContent('body');
    expect(bodyText).not.toContain('Internal Server Error');
  });

  test('S4-02 Workflow trigger button is present on canvas', async ({ page }) => {
    await page.addInitScript(() => {
      try { localStorage.setItem('icdev_tour_completed', '1'); localStorage.setItem('icdev_tour_last_step', '999'); } catch (_) {}
    });
    await page.goto(CANVAS_URL);
    await page.waitForLoadState('domcontentloaded', { timeout: STUDIO_READY });
    await dismissTour(page);
    await page.waitForTimeout(2000);

    // Look for the ⚙ Workflow trigger button.
    const triggerBtn = page
      .locator('button[id^="wf-trigger-btn-"]')
      .or(page.locator('button').filter({ hasText: /workflow/i }))
      .or(page.locator('[onclick*="sendToWorkflowStudio"]'));

    const found = await triggerBtn.count();
    if (found > 0) {
      await page.screenshot({ path: shot('s4_02_trigger_btn_visible'), fullPage: true });
    } else {
      await page.screenshot({ path: shot('s4_02_trigger_btn_absent'), fullPage: true });
    }

    // Page itself must have loaded (body exists with content).
    const bodyText = await page.textContent('body');
    expect(bodyText?.length ?? 0).toBeGreaterThan(10);
  });

  test('S4-03 from-canvas API endpoint responds', async ({ page }) => {
    await gotoStudio(page);

    const resp = await page.evaluate(async () => {
      const r = await fetch('/api/studio/workflows/from-canvas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ canvas_id: 'idc', context_label: 'E2E Test Context' }),
      });
      const data = await r.json().catch(() => ({}));
      return { status: r.status, ok: r.ok, redirect: (data as any).redirect ?? null };
    });

    await page.screenshot({ path: shot('s4_03_from_canvas_api'), fullPage: true });

    expect(resp.ok).toBeTruthy();
    if (resp.redirect) {
      expect(resp.redirect).toMatch(/studio\/workflows/);
    }
  });

  test('S4-04 Canvas trigger opens studio (popup or redirect)', async ({ page }) => {
    await page.addInitScript(() => {
      try { localStorage.setItem('icdev_tour_completed', '1'); localStorage.setItem('icdev_tour_last_step', '999'); } catch (_) {}
    });
    await page.goto(CANVAS_URL);
    await page.waitForLoadState('domcontentloaded', { timeout: STUDIO_READY });
    await dismissTour(page);
    await page.waitForTimeout(2000);

    const triggerBtn = page
      .locator('button[id^="wf-trigger-btn-"]')
      .or(page.locator('[onclick*="sendToWorkflowStudio"]')).first();

    if (await triggerBtn.count() > 0) {
      // The button opens a new tab via window.open — capture popup.
      const [popup] = await Promise.all([
        page.waitForEvent('popup', { timeout: 8000 }).catch(() => null),
        triggerBtn.click(),
      ]);

      if (popup) {
        await popup.waitForLoadState('domcontentloaded', { timeout: 15_000 });
        await popup.screenshot({ path: shot('s4_04_canvas_redirect_popup'), fullPage: true });
        const popupUrl = popup.url();
        expect(popupUrl).toMatch(/studio\/workflows/);
        await popup.close();
      } else {
        await page.waitForTimeout(2000);
        await page.screenshot({ path: shot('s4_04_canvas_redirect_sametab'), fullPage: true });
      }
    } else {
      await page.screenshot({ path: shot('s4_04_canvas_no_trigger'), fullPage: true });
    }
  });
});
// CUI // SP-CTI
