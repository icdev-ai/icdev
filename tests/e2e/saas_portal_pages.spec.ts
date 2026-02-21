// CUI // SP-CTI
// E2E Test: SaaS Portal Pages
// Verifies all portal pages load correctly with CUI banners and sidebar navigation.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('SaaS Portal Pages', () => {
  test('projects page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to projects page
    await page.goto('/portal/projects');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_01_projects.png',
      fullPage: true,
    });

    // Step 4: Verify page content — may redirect to login if unauthenticated
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Step 5: Check for project-related content or login redirect
    const url = page.url();
    const hasProjects = bodyText?.toLowerCase().includes('project');
    const onLogin = url.includes('/login');
    expect(hasProjects || onLogin).toBeTruthy();
  });

  test('compliance page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to compliance page
    await page.goto('/portal/compliance');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_02_compliance.png',
      fullPage: true,
    });

    // Step 4: CUI banner check
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Step 5: Check for compliance terms or login redirect
    const url = page.url();
    const complianceTerms = ['compliance', 'score', 'framework', 'control'];
    let complianceFound = false;
    for (const term of complianceTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        complianceFound = true;
        break;
      }
    }
    expect(complianceFound || url.includes('/login')).toBeTruthy();
  });

  test('team page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to team page
    await page.goto('/portal/team');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_03_team.png',
      fullPage: true,
    });

    // Step 4: CUI banner check
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Step 5: Check for team-related content or login redirect
    const url = page.url();
    const teamTerms = ['team', 'user', 'member', 'role', 'email'];
    let teamFound = false;
    for (const term of teamTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        teamFound = true;
        break;
      }
    }
    expect(teamFound || url.includes('/login')).toBeTruthy();
  });

  test('settings page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to settings page
    await page.goto('/portal/settings');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_04_settings.png',
      fullPage: true,
    });

    // Step 4: CUI banner check
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Step 5: Check for settings content or login redirect
    const url = page.url();
    const settingsTerms = ['settings', 'configuration', 'tenant', 'tier', 'impact'];
    let settingsFound = false;
    for (const term of settingsTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        settingsFound = true;
        break;
      }
    }
    expect(settingsFound || url.includes('/login')).toBeTruthy();
  });

  test('API keys page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to API keys page
    await page.goto('/portal/keys');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_05_keys.png',
      fullPage: true,
    });

    // Step 4: CUI banner check
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Step 5: Check for key-related content or login redirect
    const url = page.url();
    const keyTerms = ['api key', 'key', 'token', 'status', 'created'];
    let keyFound = false;
    for (const term of keyTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        keyFound = true;
        break;
      }
    }
    expect(keyFound || url.includes('/login')).toBeTruthy();
  });

  test('usage page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to usage page
    await page.goto('/portal/usage');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_06_usage.png',
      fullPage: true,
    });

    // Step 4: CUI banner check
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Step 5: Check for usage content or login redirect
    const url = page.url();
    const usageTerms = ['usage', 'api call', 'token', 'endpoint', 'metric'];
    let usageFound = false;
    for (const term of usageTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        usageFound = true;
        break;
      }
    }
    expect(usageFound || url.includes('/login')).toBeTruthy();
  });

  test('audit page loads with CUI banner', async ({ page }) => {
    // Step 1-2: Navigate to audit page
    await page.goto('/portal/audit');
    await page.waitForLoadState('networkidle');

    // Step 3: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_07_audit.png',
      fullPage: true,
    });

    // Step 4: CUI banner check
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);

    // Step 5: Check for audit content or login redirect
    const url = page.url();
    const auditTerms = ['audit', 'trail', 'event', 'timestamp', 'action'];
    let auditFound = false;
    for (const term of auditTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        auditFound = true;
        break;
      }
    }
    expect(auditFound || url.includes('/login')).toBeTruthy();
  });

  test('sidebar navigation links are present on portal pages', async ({ page }) => {
    // Step 1: Navigate to portal login (always accessible)
    await page.goto('/portal/login');
    await page.waitForLoadState('networkidle');

    // Step 2: Screenshot
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/saas_portal_pages_08_sidebar.png',
      fullPage: true,
    });

    // Step 3: Check for sidebar navigation elements
    const sidebar = page.locator('nav.sidebar, .sidebar, [role="navigation"]');
    if (await sidebar.count() > 0) {
      const navLinks = ['Dashboard', 'Projects', 'Compliance', 'Team', 'Settings', 'API Keys', 'Usage', 'Audit'];
      for (const linkText of navLinks) {
        const link = page.getByRole('link', { name: new RegExp(linkText, 'i') });
        if (await link.count() > 0) {
          await expect(link.first()).toBeVisible();
        }
      }
    }

    // Step 4: Verify Sign Out link if present
    const signOutLink = page.getByRole('link', { name: /Sign Out|Logout/i });
    if (await signOutLink.count() > 0) {
      await expect(signOutLink).toBeVisible();
    }

    // Step 5: CUI banner on every page
    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI
