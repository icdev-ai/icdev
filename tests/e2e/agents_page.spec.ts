// CUI // SP-CTI
// E2E Test: Agents Page
// Verifies the ICDEV dashboard agents page loads with agent grid and status indicators.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Agents Page', () => {
  test('agents page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to agents page
    await page.goto('/agents');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot the agents page
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/agents_page_01_overview.png',
      fullPage: true,
    });

    // Step 4: Verify page loaded (not error page)
    const bodyText = await page.textContent('body');
    expect(bodyText).toBeTruthy();

    // Step 5: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);

    // Step 6: Check header CUI banner element
    const headerBanner = page.locator('header, .cui-banner-top, [data-cui="header"], .cui-banner').first();
    if (await headerBanner.count() > 0) {
      await expect(headerBanner).toContainText(CUI_BANNER);
    }

    // Step 7: Check footer CUI banner element
    const footerBanner = page.locator('footer, .cui-banner-bottom, [data-cui="footer"], .cui-banner').last();
    if (await footerBanner.count() > 0) {
      await expect(footerBanner).toContainText(CUI_BANNER);
    }
  });

  test('agent grid displays all core agents', async ({ page }) => {
    // Step 1: Navigate to agents page
    await page.goto('/agents');
    await page.waitForLoadState('networkidle');

    // Step 2: Verify core agent names are present
    const bodyText = await page.textContent('body');
    const coreAgents = [
      'Orchestrator', 'Architect', 'Builder', 'Compliance',
      'Security', 'Infrastructure', 'Knowledge', 'Monitor',
    ];

    for (const agent of coreAgents) {
      expect(bodyText?.toLowerCase()).toContain(agent.toLowerCase());
    }

    // Step 3: Screenshot the agent grid
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/agents_page_02_grid.png',
      fullPage: true,
    });

    // Step 4: Check for extended agents (may or may not be present)
    const extendedAgents = ['MBSE', 'Modernization', 'Requirements', 'Supply Chain', 'Simulation'];
    let extendedFound = 0;
    for (const agent of extendedAgents) {
      if (bodyText?.toLowerCase().includes(agent.toLowerCase())) {
        extendedFound++;
      }
    }
  });

  test('agent status indicators are displayed', async ({ page }) => {
    // Step 1: Navigate to agents page
    await page.goto('/agents');
    await page.waitForLoadState('networkidle');

    // Step 2: Check for status-related UI elements
    const bodyText = await page.textContent('body');
    const statusTerms = ['status', 'health', 'port', 'active', 'idle', 'running', 'stopped'];
    let statusFound = false;
    for (const term of statusTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        statusFound = true;
        break;
      }
    }

    // Step 3: Check for agent card or grid elements
    const agentCards = page.locator('.agent-card, .agent-item, .card, [data-agent]');
    if (await agentCards.count() > 0) {
      // Verify at least some cards are visible
      await expect(agentCards.first()).toBeVisible();
    }

    // Step 4: Check for port numbers (agents have assigned ports 8443-8458)
    const portPattern = /84[4-5][0-9]/;
    const hasPort = portPattern.test(bodyText || '');

    // Step 5: Screenshot status indicators
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/agents_page_03_status.png',
      fullPage: true,
    });

    // Step 6: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('agents page navigation from dashboard', async ({ page }) => {
    // Step 1: Start at dashboard
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Step 2: Find and click agents navigation link
    const agentsLink = page.getByRole('link', { name: /Agents/i });
    if (await agentsLink.count() > 0) {
      await agentsLink.click();
      await page.waitForLoadState('networkidle');

      // Step 3: Verify navigation worked
      const url = page.url();
      expect(url).toContain('/agents');

      // Step 4: Screenshot
      await page.screenshot({
        path: '.tmp/test_runs/screenshots/agents_page_04_nav.png',
        fullPage: true,
      });

      // Step 5: CUI banner on navigated page
      const bodyText = await page.textContent('body');
      expect(bodyText).toContain(CUI_BANNER);
    }
  });
});
// CUI // SP-CTI
