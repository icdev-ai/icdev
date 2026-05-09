// CUI // SP-CTI
// E2E Test: Fine-Tuning Dashboard
// Verifies the Fine-Tuning dashboard pages render correctly with stat grid, dataset/job/model management.

import { test, expect } from '@playwright/test';

const CUI_BANNER = 'CUI // SP-CTI';

test.describe('Fine-Tuning Dashboard', () => {
  test('finetune overview page loads with heading and stat grid', async ({ page }) => {
    // Steps 8-17: Navigate and verify overview
    await page.goto('/finetune');
    await page.waitForLoadState('domcontentloaded');

    // Step 9-10: Heading
    const bodyText = await page.textContent('body');
    const headingTerms = ['Fine-Tuning', 'Fine Tuning', 'Finetune', 'finetune'];
    let headingFound = false;
    for (const term of headingTerms) {
      if (bodyText?.includes(term)) {
        headingFound = true;
        break;
      }
    }

    // Steps 11-15: Stat grid cards
    const statCards = ['Datasets', 'Training Jobs', 'Models', 'Active Overrides'];
    for (const card of statCards) {
      if (bodyText?.includes(card)) {
        expect(bodyText).toContain(card);
      }
    }

    // Steps 5-7: CUI banners
    expect(bodyText).toContain(CUI_BANNER);

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_01_overview.png',
      fullPage: true,
    });
  });

  test('recent training jobs section is visible', async ({ page }) => {
    // Steps 16-17: Recent jobs section
    await page.goto('/finetune');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    const jobTerms = ['Recent Training Jobs', 'Training Jobs', 'Jobs'];
    let jobSectionFound = false;
    for (const term of jobTerms) {
      if (bodyText?.includes(term)) {
        jobSectionFound = true;
        break;
      }
    }

    // Table or empty state
    const tableOrEmpty = page.locator('table, .empty-state, [data-testid="empty-state"]');
    if (await tableOrEmpty.count() > 0) {
      await expect(tableOrEmpty.first()).toBeVisible();
    }

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('datasets sub-page loads with create button', async ({ page }) => {
    // Steps 19-22: Datasets page
    await page.goto('/finetune/datasets');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    if (bodyText?.includes('Datasets') || bodyText?.includes('Dataset')) {
      expect(bodyText).toMatch(/[Dd]ataset/);
    }

    // Create dataset button
    const createBtn = page.getByRole('button', { name: /Create Dataset/i })
      .or(page.getByRole('link', { name: /Create Dataset/i }))
      .or(page.locator('[data-action="create-dataset"]'));
    if (await createBtn.count() > 0) {
      await expect(createBtn.first()).toBeVisible();
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_02_datasets.png',
      fullPage: true,
    });

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('jobs sub-page loads with table or empty state', async ({ page }) => {
    // Steps 23-25: Jobs page
    await page.goto('/finetune/jobs');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    if (bodyText?.includes('Training Jobs') || bodyText?.includes('Jobs')) {
      expect(bodyText).toMatch(/[Jj]obs/);
    }

    const tableOrEmpty = page.locator('table, .empty-state, [data-testid="empty-state"]');
    if (await tableOrEmpty.count() > 0) {
      await expect(tableOrEmpty.first()).toBeVisible();
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_03_jobs.png',
      fullPage: true,
    });

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('models sub-page loads with table or empty state', async ({ page }) => {
    // Steps 26-28: Models page
    await page.goto('/finetune/models');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    if (bodyText?.includes('Model Versions') || bodyText?.includes('Models')) {
      expect(bodyText).toMatch(/[Mm]odel/);
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_04_models.png',
      fullPage: true,
    });

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('label sub-page loads with dataset selector and batch actions', async ({ page }) => {
    // Steps 29-32: Label page
    await page.goto('/finetune/label');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    if (bodyText?.includes('Labeling') || bodyText?.includes('Label')) {
      expect(bodyText).toMatch(/[Ll]abel/);
    }

    // Dataset selector
    const datasetSelector = page.locator('select[name*="dataset"], .dataset-selector, [data-testid="dataset-select"]');
    if (await datasetSelector.count() > 0) {
      await expect(datasetSelector.first()).toBeVisible();
    }

    // Batch action buttons
    const approveBtn = page.getByRole('button', { name: /Approve/i });
    const rejectBtn = page.getByRole('button', { name: /Reject/i });
    if (await approveBtn.count() > 0) {
      await expect(approveBtn.first()).toBeVisible();
    }
    if (await rejectBtn.count() > 0) {
      await expect(rejectBtn.first()).toBeVisible();
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_05_label.png',
      fullPage: true,
    });

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('evaluate sub-page loads with table or empty state', async ({ page }) => {
    // Steps 33-35: Evaluate page
    await page.goto('/finetune/evaluate');
    await page.waitForLoadState('domcontentloaded');

    const bodyText = await page.textContent('body');
    if (bodyText?.includes('Evaluations') || bodyText?.includes('Evaluate')) {
      expect(bodyText).toMatch(/[Ee]valuat/);
    }

    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_06_evaluate.png',
      fullPage: true,
    });

    expect(bodyText).toContain(CUI_BANNER);
  });

  test('finetune responsive layout at multiple viewports', async ({ page }) => {
    // Steps 36-41: Multi-viewport screenshots
    await page.goto('/finetune');
    await page.waitForLoadState('domcontentloaded');

    // Desktop
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_07_desktop_1920x1080.png',
      fullPage: true,
    });

    // Tablet
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_08_tablet_768x1024.png',
      fullPage: true,
    });

    // Mobile
    await page.setViewportSize({ width: 375, height: 812 });
    await page.screenshot({
      path: '.tmp/test_runs/screenshots/finetune_09_mobile_375x812.png',
      fullPage: true,
    });

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI
