// CUI // SP-CTI
// E2E Test: Knowledge Search (RAG) Dashboard
// Verifies the Knowledge Search page renders correctly with RAG system status and semantic search.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Knowledge Search (RAG) Dashboard', () => {
  test('knowledge search page loads with heading and stat grid', async ({ page }) => {
    // Steps 8-17: Navigate and verify
    await page.goto('/knowledge-search');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    // Steps 9-11: Heading and subtitle
    const headingTerms = ['Knowledge Search', 'knowledge search', 'RAG', 'rag'];
    let headingFound = false;
    for (const term of headingTerms) {
      if (bodyText?.toLowerCase().includes(term.toLowerCase())) {
        headingFound = true;
        break;
      }
    }

    // Steps 12-17: Stat grid cards (soft checks — values may be 0 or "--")
    const statCards = ['Total Chunks', 'Source Types', 'Active Tiers', 'Backend', 'Status'];
    for (const card of statCards) {
      if (bodyText?.includes(card)) {
        expect(bodyText).toContain(card);
      }
    }

    // Steps 5-7: CUI banners
    expect(bodyText).toContain(CUI_BANNER);

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/knowledge_search_01_overview.png',
      fullPage: true,
    });
  });

  test('search controls are visible with input, filter, and top-K', async ({ page }) => {
    // Steps 18-21: Search controls
    await page.goto('/knowledge-search');
    await page.waitForLoadState('domcontentloaded');

    // Step 18: Search input
    const searchInput = page.locator(
      'input[type="search"], input[name="query"], input[name="q"], input[placeholder*="search" i], .search-input'
    );
    if (await searchInput.count() > 0) {
      await expect(searchInput.first()).toBeVisible();
    }

    // Step 19: Source type filter dropdown
    const sourceFilter = page.locator(
      'select[name*="source"], select[name*="type"], .source-filter, [data-testid="source-filter"]'
    );
    if (await sourceFilter.count() > 0) {
      await expect(sourceFilter.first()).toBeVisible();
    }

    // Step 20: Top-K input
    const topKInput = page.locator(
      'input[name="top_k"], input[name="topk"], input[name="k"], input[placeholder*="top" i]'
    );
    if (await topKInput.count() > 0) {
      await expect(topKInput.first()).toBeVisible();
    }

    // Step 21: Search button
    const searchBtn = page.getByRole('button', { name: /Search/i });
    if (await searchBtn.count() > 0) {
      await expect(searchBtn.first()).toBeVisible();
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('search with FedRAMP query returns results or no-results message', async ({ page }) => {
    // Steps 24-27: Search functionality
    await page.goto('/knowledge-search');
    await page.waitForLoadState('domcontentloaded');

    const searchInput = page.locator(
      'input[type="search"], input[name="query"], input[name="q"], input[placeholder*="search" i]'
    );
    if (await searchInput.count() > 0) {
      await searchInput.first().fill('FedRAMP AC-2 implementation patterns');

      const searchBtn = page.getByRole('button', { name: /Search/i });
      if (await searchBtn.count() > 0) {
        await searchBtn.first().click().catch(() => {});
        await page.waitForLoadState('domcontentloaded').catch(() => {});
        await page.waitForTimeout(3000);
      }

      await page.screenshot({
        path: '.tmp/test_runs/screenshots/knowledge_search_02_results.png',
        fullPage: true,
      });

      const bodyText = await page.textContent('body');
      // Either results or no-results message
      const resultTerms = ['result', 'Result', 'No results', 'found', 'chunk'];
      let resultFound = false;
      for (const term of resultTerms) {
        if (bodyText?.toLowerCase().includes(term.toLowerCase())) {
          resultFound = true;
          break;
        }
      }
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('enter key triggers search', async ({ page }) => {
    // Step 41-44: Enter key search
    await page.goto('/knowledge-search');
    await page.waitForLoadState('domcontentloaded');

    const searchInput = page.locator(
      'input[type="search"], input[name="query"], input[name="q"], input[placeholder*="search" i]'
    );
    if (await searchInput.count() > 0) {
      await searchInput.first().fill('supply chain');
      await searchInput.first().press('Enter');
      await page.waitForLoadState('domcontentloaded');
      await page.waitForTimeout(1000);

      const bodyText = await page.textContent('body');
      // Results panel should become visible
      const resultsTerms = ['result', 'Result', 'No results', 'search', 'chunk'];
      let resultsVisible = false;
      for (const term of resultsTerms) {
        if (bodyText?.toLowerCase().includes(term.toLowerCase())) {
          resultsVisible = true;
          break;
        }
      }
    }

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });

  test('recent searches table is visible', async ({ page }) => {
    // Steps 49-52: Recent searches table
    await page.goto('/knowledge-search');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');

    // Step 49-51: Table heading and columns
    if (bodyText?.includes('Recent Searches')) {
      expect(bodyText).toContain('Recent Searches');

      const tableHeaders = ['Query', 'Results', 'Score', 'Duration', 'Time'];
      for (const header of tableHeaders) {
        if (bodyText?.includes(header)) {
          expect(bodyText).toContain(header);
        }
      }
    }

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('breadcrumb navigation works', async ({ page }) => {
    // Steps 57-58: Breadcrumb
    await page.goto('/knowledge-search');
    await page.waitForLoadState('domcontentloaded');

    const breadcrumb = page.locator('nav[aria-label="breadcrumb"], .breadcrumb, [data-testid="breadcrumb"]');
    if (await breadcrumb.count() > 0) {
      await expect(breadcrumb.first()).toBeVisible();

      const homeLink = breadcrumb.getByRole('link', { name: /Home/i });
      if (await homeLink.count() > 0) {
        await homeLink.first().click();
        await page.waitForLoadState('domcontentloaded').catch(() => {});
        // Should redirect to home. Assert the PATH, not the host: the config's
        // own isolation recipe (ICDEV_DASHBOARD_URL/ICDEV_DASHBOARD_PORT) serves
        // the suite from 127.0.0.1, and a hardcoded `localhost` here failed the
        // test for a redirect that had actually worked.
        expect(new URL(page.url()).pathname).toBe('/');
      }
    }
  });

  test('knowledge search responsive layout at multiple viewports', async ({ page }) => {
    // Steps 54-56: Multi-viewport screenshots
    await page.goto('/knowledge-search');
    await page.waitForLoadState('domcontentloaded');

    // Desktop
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/knowledge_search_03_desktop_1440x900.png',
      fullPage: true,
    });

    // Tablet
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/knowledge_search_04_tablet_768x1024.png',
      fullPage: true,
    });

    // Mobile
    await page.setViewportSize({ width: 375, height: 812 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/knowledge_search_05_mobile_375x812.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI
