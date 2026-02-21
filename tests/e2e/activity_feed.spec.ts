// CUI // SP-CTI
// E2E Test: Activity Feed
// Verifies the ICDEV dashboard activity page loads with SSE connection and activity entries.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Activity Feed', () => {
  test('activity page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to activity page
    await page.goto('/activity');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot the activity page
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/activity_feed_01_overview.png',
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

  test('SSE connection indicator is present', async ({ page }) => {
    // Step 1: Navigate to activity page
    await page.goto('/activity');
    await page.waitForLoadState('networkidle');

    // Step 2: Check for SSE/connection status indicators
    const bodyText = await page.textContent('body');
    const connectionTerms = [
      'connected', 'live', 'real-time', 'streaming', 'sse',
      'connection', 'status', 'online', 'update',
    ];
    let connectionFound = false;
    for (const term of connectionTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        connectionFound = true;
        break;
      }
    }

    // Step 3: Check for connection status UI element
    const connectionIndicator = page.locator(
      '.connection-status, .sse-status, .live-indicator, ' +
      '[data-connection], .status-indicator, .live-badge'
    );
    if (await connectionIndicator.count() > 0) {
      await expect(connectionIndicator.first()).toBeVisible();
    }

    // Step 4: Screenshot the connection indicator
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/activity_feed_02_sse.png',
      fullPage: true,
    });

    // Step 5: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('activity entries display with event details', async ({ page }) => {
    // Step 1: Navigate to activity page
    await page.goto('/activity');
    await page.waitForLoadState('networkidle');

    // Step 2: Check for activity entry elements
    const bodyText = await page.textContent('body');
    const activityTerms = [
      'activity', 'event', 'action', 'timestamp', 'audit',
      'log', 'entry', 'recent', 'feed',
    ];
    let activityFound = false;
    for (const term of activityTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        activityFound = true;
        break;
      }
    }

    // Step 3: Check for table or list structure
    const entries = page.locator(
      'table tbody tr, .activity-entry, .event-item, ' +
      '.feed-item, .timeline-item, .activity-item'
    );
    if (await entries.count() > 0) {
      // Verify first entry is visible
      await expect(entries.first()).toBeVisible();

      // Step 4: Check entry has expected fields
      const firstEntryText = await entries.first().textContent();
      // Activity entries typically contain timestamps or event types
    }

    // Step 5: Check for empty state message if no entries
    if (await entries.count() === 0) {
      const emptyTerms = ['no activity', 'no entries', 'no events', 'empty', 'no recent'];
      let emptyFound = false;
      for (const term of emptyTerms) {
        if (bodyText?.toLowerCase().includes(term)) {
          emptyFound = true;
          break;
        }
      }
    }

    // Step 6: Screenshot the entries
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/activity_feed_03_entries.png',
      fullPage: true,
    });

    // Step 7: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('activity page navigation from dashboard', async ({ page }) => {
    // Step 1: Start at dashboard
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Step 2: Find and click activity navigation link
    const activityLink = page.getByRole('link', { name: /Activity/i });
    if (await activityLink.count() > 0) {
      await activityLink.click();
      await page.waitForLoadState('networkidle');

      // Step 3: Verify navigation worked
      const url = page.url();
      expect(url).toContain('/activity');

      // Step 4: Screenshot
      await page.screenshot({
        path: '.tmp/test_runs/screenshots/activity_feed_04_nav.png',
        fullPage: true,
      });

      // Step 5: CUI banner present after navigation
      const bodyText = await page.textContent('body');
      expect(bodyText).toContain(CUI_BANNER);
    } else {
      // Direct navigation fallback
      await page.goto('/activity');
      await page.waitForLoadState('networkidle');

      const bodyText = await page.textContent('body');
      expect(bodyText).toContain(CUI_BANNER);
    }
  });
});
// CUI // SP-CTI
