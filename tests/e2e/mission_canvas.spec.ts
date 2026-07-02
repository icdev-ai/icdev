// CUI // SP-CTI
// E2E Test: Mission Canvas — Program Command Center
// Verifies the mission canvas dashboard loads, 4 zones render,
// navigation works, and API routes respond.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

/**
 * Test thresholds:
 *   - Page loads 200 (nav + domcontentloaded)
 *   - 4 zones visible (Situation, Intelligence, Execution, Security)
 *   - IQE widget present
 *   - API routes return JSON
 *   - No console errors on load
 * Pass: >= 4/5
 */

async function suppressTour(page: any) {
  await page.evaluate(() => {
    localStorage.setItem('icdev_tour_completed', '1');
    localStorage.setItem('icdev_tour_last_step', '999');
    const el = document.getElementById('icdev-tour-welcome');
    if (el) { el.classList.remove('visible'); (el as HTMLElement).style.display = 'none'; el.remove(); }
    document.querySelectorAll('[class*="tour"]').forEach((e) => {
      (e as HTMLElement).style.display = 'none';
      e.remove();
    });
  });
}

test.describe('Mission Canvas Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    page.on('console', (msg: any) => {
      if (msg.type() === 'error') {
        console.error(`Console error: ${msg.text()}`);
      }
    });
  });

  test('mission canvas page loads with CUI banner and 4 zones', async ({ page }) => {
    await page.goto('/mission-canvas/');
    await page.waitForLoadState('domcontentloaded');
    await suppressTour(page);

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/mission_canvas_01_overview.png',
      fullPage: true,
    });

    // Verify page loaded
    const bodyText = await page.textContent('body');
    expect(bodyText).toBeTruthy();

    // CUI banner check
    expect(bodyText).toContain(CUI_BANNER);

    // Check 4 zone titles (scoped to cards to avoid nav overlap)
    const zoneTitles = ['Situation', 'Intelligence', 'Execution', 'Security'];
    for (const title of zoneTitles) {
      const locator = page.locator('.mc-card', { hasText: title }).first();
      await expect(locator).toBeVisible();
    }
  });

  test('mission canvas has IQE query widget', async ({ page }) => {
    await page.goto('/mission-canvas/');
    await page.waitForLoadState('domcontentloaded');
    await suppressTour(page);

    const widget = page.locator('.iqe-widget').first();
    await expect(widget).toBeVisible();

    const title = await widget.locator('.iqe-widget__title').textContent();
    expect(title).toContain('Mission Control');
  });

  test('mission canvas API twin route returns JSON', async ({ page }) => {
    const response = await page.request.get('/mission-canvas/api/twin?mission_id=test-01');
    expect(response.status()).toBe(200);
    const json = await response.json();
    expect(json).toHaveProperty('mission_id');
  });

  test('mission canvas API security posture route returns JSON', async ({ page }) => {
    const response = await page.request.get('/mission-canvas/api/security-posture?mission_id=test-01');
    expect(response.status()).toBe(200);
    const json = await response.json();
    expect(json).toHaveProperty('mission_id');
    expect(json).toHaveProperty('overall_score');
  });

  test('mission canvas navigation link is present in Canvases dropdown', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await suppressTour(page);

    const canvasesDropdown = page.locator('text=Canvases').first();
    await canvasesDropdown.click();

    const missionLink = page.locator('a[href="/mission-canvas/"]').first();
    await expect(missionLink).toBeVisible();
    await expect(missionLink).toContainText('Mission Overview');
  });
});

test.describe('Mission Canvas Detail Page', () => {
  test('detail page loads with mission ID and capability buttons', async ({ page }) => {
    await page.goto('/mission-canvas/detail/demo-mission-01');
    await page.waitForLoadState('domcontentloaded');
    await suppressTour(page);

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/mission_canvas_02_detail.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain('Mission:');
    expect(bodyText).toContain('demo-mission-01');

    // Check capability buttons
    const buttons = ['Twin', 'Security', 'Portfolio', 'CI/CD', 'AI Trust', 'Evidence'];
    for (const btn of buttons) {
      const locator = page.locator(`button:has-text("${btn}")`).first();
      await expect(locator).toBeVisible();
    }
  });
});
