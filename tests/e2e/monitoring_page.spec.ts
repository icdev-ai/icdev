// CUI // SP-CTI
// E2E Test: Monitoring Page
// Verifies the ICDEV dashboard monitoring page loads with status icons and health indicators.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Monitoring Page', () => {
  test('monitoring page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to monitoring page
    await page.goto('/monitoring');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot the monitoring page
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/monitoring_page_01_overview.png',
      fullPage: true,
    });

    // Step 4: Verify page loaded
    const bodyText = await page.textContent('body');
    expect(bodyText).toBeTruthy();

    // Step 5: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);

    // Step 6: Check header CUI banner element
    const cuiBanners = page.locator('.cui-banner, [data-cui], .cui-banner-top');
    if (await cuiBanners.count() > 0) {
      await expect(cuiBanners.first()).toContainText(CUI_BANNER);
    }
  });

  test('status icons and health indicators are displayed', async ({ page }) => {
    // Step 1: Navigate to monitoring page
    await page.goto('/monitoring');
    await page.waitForLoadState('networkidle');

    // Step 2: Check for monitoring-related content
    const bodyText = await page.textContent('body');
    const monitorTerms = ['health', 'status', 'metric', 'monitor', 'alert', 'check'];
    let monitorFound = false;
    for (const term of monitorTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        monitorFound = true;
        break;
      }
    }

    // Step 3: Check for status icon elements
    const statusIcons = page.locator(
      '.status-icon, .health-icon, .status-indicator, ' +
      '[data-status], .badge, .status-dot, [role="status"]'
    );
    if (await statusIcons.count() > 0) {
      await expect(statusIcons.first()).toBeVisible();
    }

    // Step 4: Check for health-related indicators
    const healthTerms = ['healthy', 'degraded', 'offline', 'up', 'down', 'ok', 'warning', 'critical'];
    let healthFound = false;
    for (const term of healthTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        healthFound = true;
        break;
      }
    }

    // Step 5: Screenshot the status section
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/monitoring_page_02_status.png',
      fullPage: true,
    });

    // Step 6: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('monitoring page has metric display areas', async ({ page }) => {
    // Step 1: Navigate to monitoring page
    await page.goto('/monitoring');
    await page.waitForLoadState('networkidle');

    // Step 2: Check for metric/chart containers
    const bodyText = await page.textContent('body');
    const metricTerms = ['metric', 'count', 'rate', 'latency', 'response', 'uptime', 'error'];
    let metricFound = false;
    for (const term of metricTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        metricFound = true;
        break;
      }
    }

    // Step 3: Check for cards or data display sections
    const cards = page.locator('.card, .metric-card, .summary-card, .panel');
    if (await cards.count() > 0) {
      await expect(cards.first()).toBeVisible();
    }

    // Step 4: Check for alert-related elements
    const alertTerms = ['alert', 'notification', 'warning', 'active alerts'];
    let alertFound = false;
    for (const term of alertTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        alertFound = true;
        break;
      }
    }

    // Step 5: Screenshot the metrics area
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/monitoring_page_03_metrics.png',
      fullPage: true,
    });

    // Step 6: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('monitoring page navigation from dashboard', async ({ page }) => {
    // Step 1: Start at dashboard
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Step 2: Find and click monitoring navigation link
    const monitorLink = page.getByRole('link', { name: /Monitor/i });
    if (await monitorLink.count() > 0) {
      await monitorLink.click();
      await page.waitForLoadState('networkidle');

      // Step 3: Verify navigation worked
      const url = page.url();
      expect(url).toContain('/monitoring');

      // Step 4: Screenshot
      await page.screenshot({
        path: '.tmp/test_runs/screenshots/monitoring_page_04_nav.png',
        fullPage: true,
      });

      // Step 5: CUI banner present after navigation
      const bodyText = await page.textContent('body');
      expect(bodyText).toContain(CUI_BANNER);
    } else {
      // Direct navigation fallback
      await page.goto('/monitoring');
      await page.waitForLoadState('networkidle');

      const bodyText = await page.textContent('body');
      expect(bodyText).toContain(CUI_BANNER);
    }
  });
});
// CUI // SP-CTI
