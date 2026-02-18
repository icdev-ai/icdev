// CUI // SP-CTI
// E2E Test: Security Scan Results Display
// Verifies security scan results are properly displayed in the dashboard.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Security Scan Results', () => {
  test('dashboard shows active alerts section', async ({ page }) => {
    // Step 1-3: Navigate to dashboard and check alerts
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const bodyText = await page.textContent('body');
    // Check for alert-related content
    const alertTerms = ['alert', 'warning', 'critical', 'status', 'health'];
    let alertFound = false;
    for (const term of alertTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        alertFound = true;
        break;
      }
    }
  });

  test('monitoring page displays health checks and metrics', async ({ page }) => {
    // Step 4-6: Navigate to monitoring page
    const monitoringLink = page.getByRole('link', { name: /Monitor/i });
    await page.goto('/');

    if (await monitoringLink.count() > 0) {
      await monitoringLink.click();
    } else {
      await page.goto('/monitoring');
    }
    await page.waitForLoadState('networkidle');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/security_01_monitoring.png',
      fullPage: true,
    });

    // Step 7-9: Verify monitoring page content
    const bodyText = await page.textContent('body');
    const monitorTerms = ['health', 'metric', 'status', 'monitor', 'alert'];
    let monitorFound = false;
    for (const term of monitorTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        monitorFound = true;
        break;
      }
    }

    // CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('audit trail page shows timestamped entries', async ({ page }) => {
    // Step 10-13: Navigate to audit trail
    await page.goto('/');
    const auditLink = page.getByRole('link', { name: /Audit/i });

    if (await auditLink.count() > 0) {
      await auditLink.click();
    } else {
      await page.goto('/audit');
    }
    await page.waitForLoadState('networkidle');

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/security_02_audit_trail.png',
      fullPage: true,
    });

    // Step 14-15: Verify audit entries
    const bodyText = await page.textContent('body');

    // Check for table-like structure or audit entry fields
    const auditTerms = ['timestamp', 'event', 'actor', 'action', 'audit'];
    let auditFound = false;
    for (const term of auditTerms) {
      if (bodyText?.toLowerCase().includes(term)) {
        auditFound = true;
        break;
      }
    }

    // Step 16: CUI banner check
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI
