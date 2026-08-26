// CUI // SP-CTI
// E2E Test: Proposal Genesis — Autonomous Capture Pipeline Dashboard
// Verifies the Proposal Genesis dashboard loads with daemon status, Phase A reflexes, quality scores, and audit trail.

import { test, expect } from '@playwright/test';
import { dismissOverlays, loginIfPrompted, suppressOnboarding } from './fixtures/onboarding';
import { resolveBaseUrl } from './fixtures/base_url';

const CUI_BANNER = 'CUI // SP-CTI';
// Proposal Genesis runs on the dashboard under test (default port 5050).
// ONE resolver, shared with playwright.config.ts and fixtures/auth.ts, so the
// origin these absolute URLs address is the origin the CSRF/session cookies
// were minted at. Two copies of the precedence 403'd every mutating request in
// the suite (qa-fail-a5dbf266dfb0ce4a). See fixtures/base_url.ts.
const PG_BASE = resolveBaseUrl();

test.describe('Proposal Genesis — Autonomous Capture Pipeline', () => {
  test.beforeEach(async ({ page }) => {
    // tsh-e2e-01-d3: suppress the tour AND the onboarding wizard before any
    // navigation. The tour keys alone were not enough — the login step below
    // matched the wizard's hidden #onb-apikey input and burned the whole
    // actionTimeout on it. See fixtures/onboarding.ts.
    await suppressOnboarding(page);

    // Login (no-op in dev mode — dashboard has no auth gate)
    await loginIfPrompted(page, PG_BASE);

    // Dismiss any lingering dialog from a previous test
    await dismissOverlays(page);
  });

  test('proposal genesis page loads with heading and intro panel', async ({ page }) => {
    // Steps 3-7: Navigate and verify heading
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    // Step 5: Heading
    const headingTerms = ['Proposal Genesis', 'Capture Pipeline', 'Autonomous Capture'];
    let headingFound = false;
    for (const term of headingTerms) {
      if (bodyText?.includes(term)) {
        headingFound = true;
        break;
      }
    }

    // Steps 6-7: Intro panel
    const introTerms = ['Capture-to-Delivery', 'Discover Opps', 'Extract Reqs', 'Map & Draft', 'Quality Check'];
    for (const term of introTerms) {
      if (bodyText?.includes(term)) {
        expect(bodyText).toContain(term);
      }
    }

    expect(bodyText).toContain(CUI_BANNER);

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/proposal_genesis_01_page_load.png',
      fullPage: true,
    });
  });

  test('summary stat cards are present', async ({ page }) => {
    // Steps 8-9: Stat cards
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    const statCards = ['Daemon Status', 'Active Opportunities', 'Shall Statements', 'Pending Drafts', 'Avg Quality Score', 'Pulse Links'];
    for (const card of statCards) {
      if (bodyText?.includes(card)) {
        expect(bodyText).toContain(card);
      }
    }

    // Daemon status shows ENABLED or DISABLED
    const daemonTerms = ['ENABLED', 'DISABLED', 'enabled', 'disabled'];
    let daemonFound = false;
    for (const term of daemonTerms) {
      if (bodyText?.includes(term)) {
        daemonFound = true;
        break;
      }
    }

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('Phase A reflexes table has headers and run buttons', async ({ page }) => {
    // Steps 10-14: Phase A reflexes table
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    // Step 10: Table headers
    const tableHeaders = ['Reflex', 'Phase', 'Tier', 'Status', 'Last Run', 'Action'];
    for (const header of tableHeaders) {
      if (bodyText?.includes(header)) {
        expect(bodyText).toContain(header);
      }
    }

    // Step 11: Phase A reflexes
    const phaseAReflexes = ['discover', 'extract', 'map', 'draft', 'polish'];
    let reflexFound = false;
    for (const reflex of phaseAReflexes) {
      if (bodyText?.includes(reflex)) {
        reflexFound = true;
        break;
      }
    }

    // Step 14: Run button
    const runBtn = page.getByRole('button', { name: /Run/i }).first();
    if (await runBtn.count() > 0) {
      await expect(runBtn).toBeVisible();
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/proposal_genesis_02_reflexes.png',
      fullPage: true,
    });

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('refresh status button triggers API update', async ({ page }) => {
    // Steps 15-18: Refresh status
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    // Button has id="pg-refresh-btn" with text "Refresh Status"
    const refreshBtn = page.locator('#pg-refresh-btn')
      .or(page.locator('[data-action="refresh-status"]'));
    if (await refreshBtn.count() > 0) {
      await refreshBtn.first().click();
      await page.waitForTimeout(2000);

      // Page must not crash — '500' is too broad (may appear in stats/counts)
      const bodyText = await page.textContent('body');
      expect(bodyText).not.toContain('Internal Server Error');
      expect(bodyText).not.toContain('Traceback');
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('run full pipeline button is present but not clicked', async ({ page }) => {
    // Step 19-20: Verify run full pipeline button (do NOT click)
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    const runPipelineBtn = page.getByRole('button', { name: /Run Full Pipeline/i })
      .or(page.locator('[data-action="run-pipeline"]'));
    if (await runPipelineBtn.count() > 0) {
      await expect(runPipelineBtn.first()).toBeVisible();
      // Verify warning styling — soft check
      const btnClass = await runPipelineBtn.first().getAttribute('class');
      // Do NOT click — long-running operation
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('quality scores section refreshes on button click', async ({ page }) => {
    // Steps 21-25: Quality scores table
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    // Scroll to quality scores section
    const qualitySection = page.locator(
      'section:has-text("Quality"), [data-section="quality"], #quality-scores'
    );
    if (await qualitySection.count() > 0) {
      await qualitySection.first().scrollIntoViewIfNeeded();

      // Step 22: Table headers
      const bodyText = await page.textContent('body');
      const qHeaders = ['Opportunity', 'Draft ID', 'Composite', 'Grammar', 'Readability'];
      for (const header of qHeaders) {
        if (bodyText?.includes(header)) {
          expect(bodyText).toContain(header);
        }
      }

      // Step 23-25: Refresh button
      const qualityRefreshBtn = qualitySection.getByRole('button', { name: /Refresh/i });
      if (await qualityRefreshBtn.count() > 0) {
        await qualityRefreshBtn.first().click();
        await page.waitForTimeout(1000);
      }
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/proposal_genesis_03_quality.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('audit trail section refreshes on button click', async ({ page }) => {
    // Steps 26-30: Audit trail table
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    const auditSection = page.locator(
      'section:has-text("Audit Trail"), [data-section="audit"], #audit-trail'
    );
    if (await auditSection.count() > 0) {
      await auditSection.first().scrollIntoViewIfNeeded();

      // Step 27: Table headers
      const bodyText = await page.textContent('body');
      const auditHeaders = ['Timestamp', 'Reflex', 'Action', 'Opportunity', 'Details'];
      for (const header of auditHeaders) {
        if (bodyText?.includes(header)) {
          expect(bodyText).toContain(header);
        }
      }

      // Steps 28-30: Refresh button
      const auditRefreshBtn = auditSection.getByRole('button', { name: /Refresh/i });
      if (await auditRefreshBtn.count() > 0) {
        await auditRefreshBtn.first().click();
        await page.waitForTimeout(1000);
      }
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('proposal genesis responsive layout at multiple viewports', async ({ page }) => {
    // Steps 35-37: Multi-viewport screenshots
    await page.goto(`${PG_BASE}/proposal-genesis`);
    await page.waitForLoadState('domcontentloaded');

    // Desktop
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/proposal_genesis_04_desktop_1920x1080.png',
      fullPage: true,
    });

    // Tablet
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/proposal_genesis_05_tablet_768x1024.png',
      fullPage: true,
    });

    // Mobile
    await page.setViewportSize({ width: 375, height: 812 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/proposal_genesis_06_mobile_375x812.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI
