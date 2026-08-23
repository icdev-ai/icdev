// CUI // SP-CTI
// E2E Test: Slide Deck Generator Canvas — native asset-generator integration smoke test
// Verifies /slides pages load without server errors and the native asset generation
// path (used by GraphicsGenerator -> AssetGenerator) is reachable via a lightweight
// smoke endpoint.

import { test, expect } from './fixtures/auth';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';
const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Slide Deck Generator Canvas', () => {
  test('/slides index loads with heading, CUI banner, and generate button', async ({ page }) => {
    const resp = await page.request.get('/slides/');
    expect(resp.status(), `/slides/ returned HTTP ${resp.status()}`).toBeLessThan(400);

    await page.goto('/slides/');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/slides_index.png`,
      fullPage: false,
    });

    const bodyText = (await page.textContent('body')) ?? '';
    expect(bodyText).toContain(CUI_BANNER);
    expect(bodyText).toContain('Slide Deck Generator');
    expect(bodyText.toLowerCase()).not.toContain('internal server error');
    expect(bodyText).not.toContain('Traceback');

    const generateBtn = page.getByRole('link', { name: /Generate New Deck/i });
    expect(await generateBtn.count()).toBeGreaterThan(0);
  });

  test('/slides/new wizard loads with form fields and CUI banner', async ({ page }) => {
    const resp = await page.request.get('/slides/new');
    expect(resp.status(), `/slides/new returned HTTP ${resp.status()}`).toBeLessThan(400);

    await page.goto('/slides/new');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/slides_new.png`,
      fullPage: false,
    });

    const bodyText = (await page.textContent('body')) ?? '';
    expect(bodyText).toContain(CUI_BANNER);
    expect(bodyText.toLowerCase()).not.toContain('internal server error');
    expect(bodyText).not.toContain('Traceback');

    // Core wizard form elements should be present
    await expect(page.locator('input[name="title"], #title').first()).toBeAttached();
    await expect(page.locator('select[name="deck_type"], #deck_type').first()).toBeAttached();
    await expect(page.locator('select[name="theme"], #theme').first()).toBeAttached();
  });

  test('native asset generator smoke endpoint returns generated SVG path', async ({ request }) => {
    // Lightweight endpoint that exercises AssetGenerator -> slides_svg provider.
    const resp = await request.post('/slides/api/asset-smoke', {
      data: {
        title: 'E2E Smoke Slide',
        bullets: ['Bullet one', 'Bullet two'],
        theme: 'midnight_executive',
      },
    });

    expect(resp.status(), `asset-smoke returned HTTP ${resp.status()}`).toBeLessThan(400);

    const body = await resp.json().catch(() => ({}));
    expect(body).toHaveProperty('success', true);
    expect(body).toHaveProperty('path');
    expect(body).toHaveProperty('method', 'slides_svg');
    expect(typeof body.path).toBe('string');
    expect(body.path.length).toBeGreaterThan(0);
  });
});
// CUI // SP-CTI
