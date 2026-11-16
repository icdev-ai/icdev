// CUI // SP-CTI
// E2E Test: ACE Event feed + Auto-dispatch panels on /coworker (icx-ace-01).
// Every ACE event is a recommendation a person decides at /decisions; a role runs
// on auto only under a live grant. Stop-all is immediate and needs a reason.

import { test, expect } from './fixtures/observed_calls';
import path from 'path';
import fs from 'fs';

const SCREENSHOTS = path.resolve(__dirname, '../../playwright/screenshots');
const PAGE_TIMEOUT = 30_000;

test.describe('ACE event feed and auto-dispatch', () => {
  test.beforeAll(() => {
    fs.mkdirSync(SCREENSHOTS, { recursive: true });
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  test('the Co-Worker page shows the Event feed and Auto-dispatch panels', async ({ page }) => {
    await page.goto('/coworker/');
    await page.waitForSelector('#ace-event-feed', { timeout: PAGE_TIMEOUT });

    const feed = page.locator('#ace-event-feed');
    await expect(feed.getByRole('heading', { name: /Event feed/ })).toBeVisible();
    await expect(feed).toContainText('recommendation');
    await expect(feed.locator('a[href="/decisions/"]').first()).toBeVisible();
    // The feed loaded from /api/ace/events: a row or the empty state, never "Loading".
    await expect(page.locator('#ace-feed-rows')).not.toContainText('Loading', { timeout: PAGE_TIMEOUT });
    await expect(page.locator('#ace-feed-pending')).toContainText('waiting to be processed');

    const auto = page.locator('#ace-auto-dispatch');
    await expect(auto.getByRole('heading', { name: 'Auto-dispatch' })).toBeVisible();
    await expect(auto).toContainText('decl-wft-ace-auto-dispatch');
    await expect(page.locator('#ace-auto-eligible')).not.toContainText('Loading', { timeout: PAGE_TIMEOUT });
    await expect(page.locator('#ace-auto-grants')).not.toContainText('Loading', { timeout: PAGE_TIMEOUT });

    await page.screenshot({ path: path.join(SCREENSHOTS, 'ace_event_dispatch_panels.png'), fullPage: true });
  });

  test('GET /api/ace/events and /api/ace/auto-dispatch answer 200', async ({ page }) => {
    await page.goto('/coworker/');
    const result = await page.evaluate(async () => {
      const ev = await fetch('/api/ace/events?limit=5');
      const evBody = await ev.json();
      const ad = await fetch('/api/ace/auto-dispatch');
      const adBody = await ad.json();
      return {
        events: ev.status, ok: evBody.ok, pendingIsNumber: typeof evBody.pending === 'number',
        resultsIsArray: Array.isArray(evBody.results),
        auto: ad.status, eligibleIsArray: Array.isArray(adBody.eligible),
        grantsIsArray: Array.isArray(adBody.grants), band: adBody.limits && adBody.limits.min_trust_band,
      };
    });
    expect(result.events).toBe(200);
    expect(result.ok).toBe(true);
    expect(result.pendingIsNumber).toBe(true);
    expect(result.resultsIsArray).toBe(true);
    expect(result.auto).toBe(200);
    expect(result.eligibleIsArray).toBe(true);
    expect(result.grantsIsArray).toBe(true);
    expect(result.band).toBe('trusted');
  });

  test('stop all auto needs a reason, then takes effect at once', async ({ page }) => {
    await page.goto('/coworker/');
    await page.waitForSelector('#ace-auto-stop-btn', { timeout: PAGE_TIMEOUT });
    await expect(page.locator('#ace-auto-stopstate')).not.toHaveText('', { timeout: PAGE_TIMEOUT });

    const stop = page.locator('#ace-auto-stop-btn');
    if (await stop.isDisabled()) {
      // Already stopped by an earlier run: the state says so.
      await expect(page.locator('#ace-auto-stopstate')).toContainText('STOPPED');
    } else {
      await stop.click();
      await expect(page.locator('#ace-auto-msg')).toContainText('Say why');
      await page.fill('#ace-auto-stop-reason', 'e2e: icx-ace-01 stop-all check');
      await stop.click();
      await expect(page.locator('#ace-auto-msg')).toContainText('All auto-dispatch stopped');
      await expect(page.locator('#ace-auto-stopstate')).toContainText('STOPPED');
    }

    // Resume is the holder's declared step; it needs a reason.
    const resume = page.locator('#ace-auto-resume-btn');
    if (await resume.isEnabled()) {
      await page.fill('#ace-auto-stop-reason', 'e2e: icx-ace-01 resume check');
      await resume.click();
      await expect(page.locator('#ace-auto-msg')).toContainText(/resumed|Refused|held by/);
    }
    await page.screenshot({ path: path.join(SCREENSHOTS, 'ace_auto_dispatch_stop_all.png'), fullPage: true });
  });
});
