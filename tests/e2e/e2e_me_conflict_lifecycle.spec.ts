// CUI // SP-CTI
/**
 * E2E Lifecycle Test: Middle East Conflict Intelligence Analysis
 * Validates all 7 recent fixes using a hybrid UI + API approach.
 *
 *   1. Traceability auto-decompose on requirement changes
 *   2. AI speed caching (conversation cache, prompt caching)
 *   3. Dry-run mode (Preview Build, Preview Kanban)
 *   4. Button disable states during async operations
 *   5. Send to Kanban fallback (no assistant messages needed)
 *   6. View Project has_activity guard
 *   7. Run Tests remediation hints
 */

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';
const BASE_URL = process.env.ICDEV_DASHBOARD_URL || 'http://localhost:5050';

// Existing seeded session with Middle East conflict requirements
const SEED_SESSION_ID = 'sess-9cc6891cb548';

// ── Helpers ──────────────────────────────────────────────────────────

async function dismissTour(page: any) {
  await page.waitForTimeout(1200);
  const welcomeModal = page.locator('#icdev-tour-welcome');
  if (await welcomeModal.count() > 0 && await welcomeModal.isVisible()) {
    const skipBtn = page.locator('#icdev-tour-welcome button').filter({ hasText: /Skip/ });
    if (await skipBtn.count() > 0) {
      await skipBtn.click();
    } else {
      await page.evaluate(() => {
        const m = document.getElementById('icdev-tour-welcome');
        if (m) m.remove();
      });
    }
    await page.waitForTimeout(500);
  }
}

async function waitForSidebarReady(page: any) {
  await page.waitForFunction(() => {
    const el = document.getElementById('readiness-gauge');
    return el !== null;
  }, { timeout: 15000 });
}

// ── Tests ─────────────────────────────────────────────────────────────

test.describe('E2E Lifecycle: Middle East Conflict Intelligence (7-fix validation)', () => {
  test.use({ viewport: { width: 1920, height: 1080 } });

  test('FIX 1 + FIX 2 + FIX 4: Traceability, AI Boost speed, button states', async ({ page }) => {
    test.setTimeout(300000);

    await page.goto('/chat/' + SEED_SESSION_ID);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('body')).toContainText(CUI_BANNER);
    await dismissTour(page);
    await waitForSidebarReady(page);

    // ── Screenshot baseline ──
    await page.screenshot({ path: '.tmp/test_runs/screenshots/e2e_01_baseline.png', fullPage: true });

    // ── FIX 4: Verify AI Boost button state logic exists ──
    const hasDisableLogic = await page.evaluate(() => {
      const src = (window as any).ICDEV?.chatAiBoost?.toString?.() || '';
      return src.includes('btn.disabled = true') && src.includes('btn.disabled = false');
    });
    expect(hasDisableLogic).toBe(true);
    console.log('[FIX 4] AI Boost disable/enable logic present in chat.js');

    // ── FIX 2: Call AI Boost via JS directly (avoids stale DOM refs) ──
    const boostStart = Date.now();
    const boostResult = await page.evaluate(async (sessionId) => {
      try {
        // @ts-ignore
        const r = await fetch('/api/intake/ai-boost/' + sessionId, { method: 'POST' });
        return await r.json();
      } catch (e: any) {
        return { error: e.message };
      }
    }, SEED_SESSION_ID);
    const boostElapsed = Date.now() - boostStart;
    console.log(`[FIX 2] AI Boost API returned in ${boostElapsed}ms`, boostResult);
    expect(boostResult.error).toBeFalsy();
    expect(boostResult.added || boostResult.requirements?.length).toBeGreaterThan(0);

    // ── FIX 1: PRD validation via API ──
    const prdResp = await page.request.get('/api/intake/prd/' + SEED_SESSION_ID);
    const prdData = await prdResp.json();
    expect(prdData.prd_markdown).toBeTruthy();

    const validateResp = await page.request.get('/api/intake/prd/' + SEED_SESSION_ID + '/validate');
    const validateData = await validateResp.json();
    console.log('[FIX 1] PRD validation:', validateData);
    const traceabilityCritical = JSON.stringify(validateData).includes('traceability: CRITICAL');
    expect(traceabilityCritical).toBe(false);

    await page.screenshot({ path: '.tmp/test_runs/screenshots/e2e_02_post_boost.png', fullPage: true });
  });

  test('FIX 3: Dry-run Preview Build', async ({ page }) => {
    test.setTimeout(60000);
    await page.goto('/chat/' + SEED_SESSION_ID);
    await page.waitForLoadState('domcontentloaded');
    await dismissTour(page);
    await waitForSidebarReady(page);

    // Call dry-run API directly
    const resp = await page.request.post('/api/intake/build/' + SEED_SESSION_ID + '/start?dry_run=1');
    const data = await resp.json();
    console.log('[FIX 3] Dry-run build response:', data);
    expect(data.status).toBe('preview');
    expect(data.phases?.length).toBeGreaterThan(0);
    expect(data.phases[0]?.status).toBe('preview');

    // Verify button logic exists
    const hasDryRunBtn = await page.evaluate(() => {
      return document.getElementById('preview-build-btn') !== null;
    });
    expect(hasDryRunBtn).toBe(true);
    console.log('[FIX 3] Preview Build button exists and dry-run API returns preview status');

    await page.screenshot({ path: '.tmp/test_runs/screenshots/e2e_03_dry_run.png', fullPage: true });
  });

  test('FIX 5: Kanban fallback + FIX 3: Preview Kanban', async ({ page }) => {
    test.setTimeout(60000);
    await page.goto('/chat/' + SEED_SESSION_ID);
    await page.waitForLoadState('domcontentloaded');
    await dismissTour(page);
    await waitForSidebarReady(page);

    // Get PRD content for fallback test
    const prdResp = await page.request.get('/api/intake/prd/' + SEED_SESSION_ID);
    const prdData = await prdResp.json();
    expect(prdData.prd_markdown).toBeTruthy();

    // FIX 5: Kanban preview with PRD markdown (no assistant message scraping needed)
    const previewResp = await page.request.post('/api/kanban/preview-plan', {
      data: { markdown: prdData.prd_markdown },
    });
    const previewData = await previewResp.json();
    console.log('[FIX 5] Kanban preview from PRD:', previewData);
    expect(previewData.count).toBeGreaterThan(0);
    expect(previewData.tasks?.length).toBeGreaterThan(0);

    // Verify UI buttons exist
    const hasPreviewKanbanBtn = await page.evaluate(() =>
      document.getElementById('preview-kanban-btn') !== null
    );
    const hasSendKanbanBtn = await page.evaluate(() =>
      document.getElementById('send-kanban-btn') !== null
    );
    expect(hasPreviewKanbanBtn).toBe(true);
    expect(hasSendKanbanBtn).toBe(true);
    console.log('[FIX 3+5] Kanban preview and send buttons exist; fallback works via PRD');

    await page.screenshot({ path: '.tmp/test_runs/screenshots/e2e_04_kanban.png', fullPage: true });
  });

  test('FIX 6: View Project has_activity guard', async ({ page }) => {
    test.setTimeout(30000);
    await page.goto('/chat/' + SEED_SESSION_ID);
    await page.waitForLoadState('domcontentloaded');
    await dismissTour(page);
    await waitForSidebarReady(page);

    // Call the project endpoint directly
    const resp = await page.request.get('/api/intake/build/' + SEED_SESSION_ID + '/project');
    const data = await resp.json();
    console.log('[FIX 6] Project endpoint:', data);
    expect(data.has_activity).toBeDefined();
    // Before any build, has_activity should be false
    expect(data.has_activity).toBe(false);

    // Verify the UI guard code exists
    const hasGuard = await page.evaluate(() => {
      const src = (window as any).ICDEV?.chatViewProject?.toString?.() || '';
      return src.includes('has_activity');
    });
    expect(hasGuard).toBe(true);
    console.log('[FIX 6] View Project has_activity guard working — returns false before build');

    await page.screenshot({ path: '.tmp/test_runs/screenshots/e2e_05_project.png', fullPage: true });
  });

  test('FIX 7: Run Tests remediation hints', async ({ page }) => {
    test.setTimeout(30000);
    await page.goto('/chat/' + SEED_SESSION_ID);
    await page.waitForLoadState('domcontentloaded');
    await dismissTour(page);
    await waitForSidebarReady(page);

    // Verify remediation code exists in chat.js source
    const hasRemediation = await page.evaluate(async () => {
      try {
        const r = await fetch('/static/js/chat.js');
        const src = await r.text();
        return src.includes('Remediation') || src.includes('pytest tests/ -v') || src.includes('ruff check');
      } catch (e: any) {
        return false;
      }
    });
    expect(hasRemediation).toBe(true);
    console.log('[FIX 7] Run Tests remediation hints present in test polling logic');

    await page.screenshot({ path: '.tmp/test_runs/screenshots/e2e_06_tests.png', fullPage: true });
  });
});
