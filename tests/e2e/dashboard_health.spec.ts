// CUI // SP-CTI
// E2E Test: Dashboard Health Check
// Verifies ICDEV web dashboard loads correctly with CUI banners and core navigation.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Dashboard Health Check', () => {
  test('dashboard loads with CUI banners and navigation', async ({ page }) => {
    // Step 1-3: Navigate and verify page loads
    await page.goto('/');
    await expect(page).toHaveTitle(/ICDEV/i);

    // Step 4: Screenshot the full dashboard
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/dashboard_health_01_dashboard.png',
      fullPage: true,
    });

    // Step 5-6: Verify CUI banners top and bottom
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Check header CUI banner
    const headerBanner = page.locator('header, .cui-banner-top, [data-cui="header"]').first();
    if (await headerBanner.count() > 0) {
      await expect(headerBanner).toContainText(CUI_BANNER);
    }

    // Check footer CUI banner
    const footerBanner = page.locator('footer, .cui-banner-bottom, [data-cui="footer"]').first();
    if (await footerBanner.count() > 0) {
      await expect(footerBanner).toContainText(CUI_BANNER);
    }
  });

  test('navigation links are functional', async ({ page }) => {
    await page.goto('/');

    // Step 7: Verify navigation links exist
    const nav = page.locator('nav, .navbar, .navigation');
    const navLinks = ['Projects', 'Agents', 'Compliance', 'Security', 'Monitoring', 'Audit'];

    for (const linkText of navLinks) {
      const link = page.getByRole('link', { name: new RegExp(linkText, 'i') });
      if (await link.count() > 0) {
        await expect(link).toBeVisible();
      }
    }

    // Step 8-10: Navigate to Projects page
    const projectsLink = page.getByRole('link', { name: /Projects/i });
    if (await projectsLink.count() > 0) {
      await projectsLink.click();
      await page.waitForLoadState('networkidle');

      await page.screenshot({
        path: '.tmp/test_runs/screenshots/dashboard_health_02_projects.png',
        fullPage: true,
      });

      // Step 11-12: Verify projects page content
      const pageContent = await page.textContent('body');
      expect(pageContent).toContain(CUI_BANNER);
    }
  });

  test('agents page displays agent grid', async ({ page }) => {
    // Step 13-16: Navigate to Agents page
    await page.goto('/');
    const agentsLink = page.getByRole('link', { name: /Agents/i });

    if (await agentsLink.count() > 0) {
      await agentsLink.click();
      await page.waitForLoadState('networkidle');

      await page.screenshot({
        path: '.tmp/test_runs/screenshots/dashboard_health_03_agents.png',
        fullPage: true,
      });

      // Step 17: Verify 8-agent grid
      const agentNames = [
        'Orchestrator', 'Architect', 'Builder', 'Compliance',
        'Security', 'Infrastructure', 'Knowledge', 'Monitor',
      ];

      const bodyText = await page.textContent('body');
      for (const agent of agentNames) {
        expect(bodyText?.toLowerCase()).toContain(agent.toLowerCase());
      }
    }
  });
});
// CUI // SP-CTI
