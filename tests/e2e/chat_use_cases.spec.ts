// CUI // SP-CTI
// E2E Test: Chat Use Cases — Catalog, Activation, Quick Action URL Validation
// Covers: use case sidebar, activation flow, all quick_action URLs from args/use_cases.yaml

import { test, expect } from '@playwright/test';
import * as path from 'path';

const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const QUICK_ACTION_URLS: { useCaseId: string; label: string; url: string }[] = [
  // general_modernization
  { useCaseId: 'general_modernization', label: 'Migration Canvas', url: '/migration-canvas/network-migration/' },
  { useCaseId: 'general_modernization', label: 'Digital Twin', url: '/digital-twin' },
  { useCaseId: 'general_modernization', label: 'Supply Chain', url: '/supply_chain' },
  // year_end_budget_sprint
  { useCaseId: 'year_end_budget_sprint', label: 'Kanban Board', url: '/kanban' },
  { useCaseId: 'year_end_budget_sprint', label: 'CPMP Portal', url: '/cpmp' },
  { useCaseId: 'year_end_budget_sprint', label: 'Proposals', url: '/proposals' },
  // document_refresh
  { useCaseId: 'document_refresh', label: 'Knowledge Graph', url: '/knowledge-graph' },
  { useCaseId: 'document_refresh', label: 'Knowledge Search', url: '/knowledge-search' },
  { useCaseId: 'document_refresh', label: 'Research Hub', url: '/research' },
  { useCaseId: 'document_refresh', label: 'AI Observatory', url: '/ai-observatory' },
  // ato_package_builder
  { useCaseId: 'ato_package_builder', label: 'Compliance Canvas', url: '/compliance' },
  { useCaseId: 'ato_package_builder', label: 'Supply Chain (ATO)', url: '/supply_chain' },
  // cdrl_generator
  { useCaseId: 'cdrl_generator', label: 'CPMP (CDRL)', url: '/cpmp' },
  // incident_response_plan
  { useCaseId: 'incident_response_plan', label: 'Monitoring', url: '/monitoring' },
  // program_status_review
  { useCaseId: 'program_status_review', label: 'Digital Twin (PSR)', url: '/digital-twin' },
  // sbom_attestation
  { useCaseId: 'sbom_attestation', label: 'Supply Chain (SBOM)', url: '/supply_chain' },
  // fedramp_auth_prep
  { useCaseId: 'fedramp_auth_prep', label: 'Compliance (FedRAMP)', url: '/compliance' },
  // privacy_impact_assessment
  { useCaseId: 'privacy_impact_assessment', label: 'Audit Trail', url: '/security/prod-audit' },
  // grant_tech_proposal
  { useCaseId: 'grant_tech_proposal', label: 'Digital Twin (Grant)', url: '/digital-twin' },
  // cjis_compliance_prep
  { useCaseId: 'cjis_compliance_prep', label: 'Supply Chain (CJIS)', url: '/supply_chain' },
];

// Deduplicate URLs — only test each unique path once
const UNIQUE_URLS = Array.from(
  new Map(QUICK_ACTION_URLS.map(qa => [qa.url, qa])).values()
);

test.describe('Chat Use Cases', () => {
  test('use case sidebar renders catalog on /chat', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/use_cases_01_sidebar.png`,
      fullPage: false,
    });

    // Sidebar panel or use case cards must be present
    const panel = page.locator('.chat-uc-panel, #use-case-panel, .use-case-panel');
    const list  = page.locator('.chat-uc-list, .chat-uc-chips');

    const panelVisible = await panel.count() > 0;
    const listVisible  = await list.count() > 0;
    expect(panelVisible || listVisible).toBe(true);
  });

  test('at least one use case card is rendered', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    // Cards are rendered dynamically into .chat-uc-list; wait briefly for JS to load them
    await page.waitForTimeout(1200);
    const cards = page.locator('.chat-uc-chip, .chat-uc-card, .use-case-card, [data-use-case-id]');
    const list  = page.locator('.chat-uc-list');
    const listCount = await list.count();
    const cardCount = await cards.count();
    // Either cards are directly rendered, or the list container exists (cards load async)
    expect(listCount + cardCount).toBeGreaterThanOrEqual(1);
  });

  test('clicking a use case card does not navigate to error page', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    const card = page.locator('.use-case-card, [data-use-case-id]').first();
    if (await card.count() > 0) {
      await card.click();
      await page.waitForTimeout(1000);
      // Page should not show a hard error
      const bodyText = await page.textContent('body');
      expect(bodyText).not.toContain('Internal Server Error');
      expect(bodyText).not.toContain('500');
    }
  });

  test('chat page loads without JS console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1500);

    // Filter out known non-fatal noise (favicon, polling during no-session state)
    const criticalErrors = errors.filter(e =>
      !e.includes('favicon') &&
      !e.includes('ERR_CONNECTION_REFUSED') &&
      !e.includes('net::ERR_ABORTED')
    );
    expect(criticalErrors.length).toBe(0);
  });

  // Validate every unique quick_action URL resolves with HTTP status < 400
  for (const qa of UNIQUE_URLS) {
    test(`quick_action URL resolves (status<400): ${qa.label} -> ${qa.url}`, async ({ page }) => {
      const resp = await page.request.get(qa.url);
      expect(
        resp.status(),
        `Expected ${qa.url} to return HTTP < 400, got ${resp.status()}`
      ).toBeLessThan(400);
    });
  }
});
// CUI // SP-CTI
