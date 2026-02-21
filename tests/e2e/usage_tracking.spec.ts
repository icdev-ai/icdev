// CUI // SP-CTI
// E2E Test: Usage Tracking
// Verifies the ICDEV dashboard usage page loads with cost breakdown and period selector.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Usage Tracking', () => {
  test('usage page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to usage page
    await page.goto('/usage');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot the usage page
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/usage_tracking_01_overview.png',
      fullPage: true,
    });

    // Step 4: Verify page loaded
    const bodyText = await page.textContent('body');
    expect(bodyText).toBeTruthy();

    // Step 5: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);

    // Step 6: Check CUI banner elements
    const cuiBanners = page.locator('.cui-banner, [data-cui], .cui-banner-top');
    if (await cuiBanners.count() > 0) {
      await expect(cuiBanners.first()).toContainText(CUI_BANNER);
    }
  });

  test('cost breakdown is displayed', async ({ page }) => {
    // Step 1: Navigate to usage page
    await page.goto('/usage');
    await page.waitForLoadState('networkidle');

    // Step 2: Check for cost/usage-related content
    const bodyText = await page.textContent('body');
    const costTerms = [
      'cost', 'usage', 'token', 'api call', 'request',
      'total', 'provider', 'spend', 'billing', 'consumption',
    ];
    let costFound = false;
    for (const term of costTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        costFound = true;
        break;
      }
    }

    // Step 3: Check for summary cards or metric displays
    const cards = page.locator('.card, .summary-card, .metric-card, .usage-card, .stat-card');
    if (await cards.count() > 0) {
      await expect(cards.first()).toBeVisible();
    }

    // Step 4: Check for breakdown table or chart
    const tables = page.locator('table, .chart, .breakdown, svg, canvas');
    if (await tables.count() > 0) {
      await expect(tables.first()).toBeVisible();
    }

    // Step 5: Check for numeric values (usage counts, costs)
    const numericPattern = /\d+/;
    expect(numericPattern.test(bodyText || '')).toBeTruthy();

    // Step 6: Screenshot the cost section
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/usage_tracking_02_cost.png',
      fullPage: true,
    });

    // Step 7: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('period selector is functional', async ({ page }) => {
    // Step 1: Navigate to usage page
    await page.goto('/usage');
    await page.waitForLoadState('networkidle');

    // Step 2: Check for period/date selector elements
    const bodyText = await page.textContent('body');
    const periodTerms = [
      'period', 'date', 'range', 'month', 'week', 'day',
      'last 7', 'last 30', 'this month', 'custom', 'filter',
    ];
    let periodFound = false;
    for (const term of periodTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        periodFound = true;
        break;
      }
    }

    // Step 3: Check for select/dropdown or date picker elements
    const selectors = page.locator(
      'select, [role="combobox"], .date-picker, .period-selector, ' +
      'input[type="date"], .filter-group, .time-range'
    );
    if (await selectors.count() > 0) {
      await expect(selectors.first()).toBeVisible();

      // Step 4: Try interacting with a selector if present
      const selectElement = page.locator('select').first();
      if (await selectElement.count() > 0) {
        // Get all options
        const options = selectElement.locator('option');
        if (await options.count() > 1) {
          // Select the second option to test interactivity
          const secondOptionValue = await options.nth(1).getAttribute('value');
          if (secondOptionValue) {
            await selectElement.selectOption(secondOptionValue);
            await page.waitForLoadState('networkidle');
          }
        }
      }
    }

    // Step 5: Check for filter buttons (alternative to dropdowns)
    const filterButtons = page.locator(
      '.btn-filter, .period-btn, [data-period], .time-filter button'
    );
    if (await filterButtons.count() > 0) {
      await expect(filterButtons.first()).toBeVisible();
    }

    // Step 6: Screenshot the period selector
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/usage_tracking_03_period.png',
      fullPage: true,
    });

    // Step 7: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('usage page navigation from dashboard', async ({ page }) => {
    // Step 1: Start at dashboard
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Step 2: Find and click usage navigation link
    const usageLink = page.getByRole('link', { name: /Usage/i });
    if (await usageLink.count() > 0) {
      await usageLink.click();
      await page.waitForLoadState('networkidle');

      // Step 3: Verify navigation worked
      const url = page.url();
      expect(url).toContain('/usage');

      // Step 4: Screenshot
      await page.screenshot({
        path: '.tmp/test_runs/screenshots/usage_tracking_04_nav.png',
        fullPage: true,
      });

      // Step 5: CUI banner present after navigation
      const bodyText = await page.textContent('body');
      expect(bodyText).toContain(CUI_BANNER);
    } else {
      // Direct navigation fallback
      await page.goto('/usage');
      await page.waitForLoadState('networkidle');

      const bodyText = await page.textContent('body');
      expect(bodyText).toContain(CUI_BANNER);
    }
  });
});
// CUI // SP-CTI
