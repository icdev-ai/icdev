// CUI // SP-CTI
// E2E Test: Network Migration Wizard — Config Mapping Full Lifecycle
// Verifies the new Step 5 AI-assisted configuration mapping end-to-end:
// create session, import Juniper config, answer yes/no questions, generate
// rule-based proposals, approve/reject/skip, apply to target config, generate
// and preview the converted target config, and download it.

import { test, expect } from '@playwright/test';
import { archiveNetSessions } from './fixtures/migration_cleanup';

const CUI_BANNER = 'CUI // SP-CTI';
const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';

const SAMPLE_JUNIPER_CONFIG = `set system host-name core-rtr-01
set system domain-name example.com
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/30
set protocols bgp group external neighbor 10.0.0.2 peer-as 65001
set routing-options static route 0.0.0.0/0 next-hop 10.0.0.2
set firewall filter protect term 1 from source-address 192.0.2.0/24
set firewall filter protect term 1 then accept`;

async function dismissTour(page: any) {
  await page.evaluate(() => {
    const overlay = document.getElementById('icdev-tour-overlay');
    if (overlay) overlay.remove();
    const welcome = document.getElementById('icdev-tour-welcome');
    if (welcome) welcome.remove();
    document.querySelectorAll('.icdev-tour-welcome, .tour-overlay, .modal-overlay.active').forEach((e) => e.remove());
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });
}

async function goToStep(page: any, step: number) {
  await page.evaluate((s: number) => {
    if (typeof goStep === 'function') goStep(s);
  }, step);
  await page.waitForTimeout(500);
}

async function answerAllYes(page: any) {
  const keys = [
    'preserve_hostname',
    'convert_vendor_syntax',
    'preserve_mgmt_interfaces',
    'preserve_bgp_peers',
  ];
  for (const key of keys) {
    const yesBtn = page.locator(`#q-${key}-yes`);
    if (await yesBtn.count()) {
      await yesBtn.click();
    }
  }
}

test.describe('Network Migration Wizard — Config Mapping Lifecycle', () => {
  // The wizard creates a real session on the board. Archive it again, or every
  // run leaves an `in_progress` row the NMCE reflex flags as a stalled cutover
  // seven days later. `authState` is captured while the test still has an
  // authenticated page, because afterAll has no test-scoped `request` fixture
  // and /migration-canvas/api/* 401s an anonymous one.
  let createdSessionId: string | null = null;
  let authState: any = null;

  test.afterAll(async ({ playwright }, testInfo) => {
    await archiveNetSessions(
      playwright,
      testInfo,
      [createdSessionId],
      'migration_network_config_map.spec',
      authState,
    );
    createdSessionId = null;
    authState = null;
  });

  test('full lifecycle: create session, import config, map, apply, preview, download', async ({ page }) => {
    // Step 1: Navigate to new migration wizard.
    await page.goto('/migration-canvas/network-migration/new');
    await page.waitForLoadState('domcontentloaded');
    await dismissTour(page);

    const bodyText = await page.textContent('body');
    expect(bodyText).toContain(CUI_BANNER);
    expect(bodyText).toContain('Step 1 — Device Selection');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_01_step1.png`,
      fullPage: true,
    });

    // Step 2: Select source and target models.
    await page.locator('#src-model').selectOption('Juniper MX204');
    await page.locator('#tgt-model').selectOption('Cisco ASR-9901');
    await page.locator('#src-device-name').fill('core-rtr-01');
    await page.locator('#tgt-device-name').fill('core-rtr-01-new');
    await page.locator('#src-site').fill('DC1');

    // Step 1 — COA: describe engineer context and answer a questionnaire toggle.
    await page.locator('#engineer-context').fill(
      'Replacement is layer-2 only; all IGP happens downstream and is not under my control. Maintenance window is tight.'
    );
    await page.locator('#coa-questionnaire').waitFor({ state: 'visible' });
    await page.locator('.coa-question').filter({ hasText: 'spare ports' }).locator('button.coa-no').click();

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_02_devices.png`,
      fullPage: true,
    });

    // Step 3: Create session and continue.
    //
    // The id is taken from the create POST's own response, never from the URL
    // after a fixed sleep. The route runs a synchronous Telegram notify (10s
    // timeout) and a KG update BEFORE it returns the id, and the wizard only
    // rewrites the URL once the response lands — so under nightly-suite load a
    // `waitForTimeout(1000)` read the URL while it still said `/new`, recorded
    // no id, and afterAll archived nothing, silently. Every nightly run from
    // 2026-08-21 to 2026-09-03 left its session `in_progress` that way (22 on
    // the live board, all this fixture's footprint) while reporting green.
    const createResponse = page.waitForResponse(
      (r) =>
        r.request().method() === 'POST' &&
        /\/migration-canvas\/api\/network-migration$/.test(r.url()),
    );
    await page.getByRole('button', { name: /Create Session & Continue/ }).click();
    const createResp = await createResponse;
    expect(createResp.ok()).toBeTruthy();
    const createdBody = await createResp.json();
    // Record the session before asserting anything else about it: a failure
    // here is exactly when residue is most likely, and afterAll can only clean
    // up what it was told about.
    if (typeof createdBody.id === 'string' && createdBody.id.startsWith('nmig')) {
      createdSessionId = createdBody.id;
      authState = await page.context().storageState();
    }
    // A test that made a real row on the board and cannot say which one is not
    // allowed to pass — that is the exact shape that left the residue.
    expect(createdSessionId).toMatch(/^nmig-[0-9a-f]{12}$/);

    // The wizard rewrites the URL to the session id once the POST returns.
    await expect(page).toHaveURL(/\/migration-canvas\/network-migration\/nmig-[0-9a-f]{12}$/);
    const afterCreateText = await page.textContent('body');
    expect(afterCreateText).toContain('Step 2 — Config / Diagram Import');

    // Step 1 COA recommendation should default to side-by-side parallel.
    const sessionId = createdSessionId as string;
    {
      const recResponse = await page.request.get(
        `/migration-canvas/api/network-migration/${sessionId}/recommend-coa`
      );
      expect(recResponse.ok()).toBeTruthy();
      const rec = await recResponse.json();
      expect(rec.recommended).toBe('coa_a');

      const qsResponse = await page.request.get(
        `/migration-canvas/api/network-migration/${sessionId}/coa-questions`
      );
      expect(qsResponse.ok()).toBeTruthy();
      const questions = await qsResponse.json();
      expect(questions.length).toBe(6);
    }

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_03_step2.png`,
      fullPage: true,
    });

    await dismissTour(page);

    // Step 4: Paste config and parse.
    await page
      .getByRole('textbox', { name: /Paste Juniper|Paste Running Config/i })
      .fill(SAMPLE_JUNIPER_CONFIG);
    await page.getByRole('button', { name: /Parse Config/ }).click();
    await page.waitForTimeout(1500);

    const parsedText = await page.textContent('body');
    expect(parsedText).toContain('Parsed');

    // Step 2b: The auto-generated topology sidecar should render from parsed config.
    expect(parsedText).toContain('Auto-generated Topology');
    await expect(page.locator('#topology-paper svg')).toBeVisible({ timeout: 5000 });
    const topologyNodeCount = await page.locator('#topology-paper svg .joint-cell').count();
    expect(topologyNodeCount).toBeGreaterThan(0);

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_04_parsed.png`,
      fullPage: true,
    });

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_04b_topology.png`,
      fullPage: true,
    });

    // Step 5: Go to Config Mapping.
    await goToStep(page, 5);
    await dismissTour(page);

    const step5Text = await page.textContent('body');
    expect(step5Text).toContain('Step 5 — Configuration Mapping (AI-assisted)');
    expect(step5Text).toContain('AI Preference Questions');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_05_step5_empty.png`,
      fullPage: true,
    });

    // Step 6: Answer all questions Yes.
    await answerAllYes(page);

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_06_questions_answered.png`,
      fullPage: true,
    });

    // Step 7: Save answers and generate proposals.
    await page.getByRole('button', { name: /Save Answers & Generate Proposals/ }).click();
    await page.waitForTimeout(2000);

    const proposalText = await page.textContent('body');
    expect(proposalText).toContain('Pending:');
    expect(proposalText).toContain('Total: 5');
    expect(proposalText).toContain('system');
    expect(proposalText).toContain('interfaces');
    expect(proposalText).toContain('protocols');
    expect(proposalText).toContain('routing-options');
    expect(proposalText).toContain('firewall');
    expect(proposalText).toContain('AI rationale');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_07_proposals_generated.png`,
      fullPage: true,
    });

    await dismissTour(page);

    // Step 8: Approve system, reject interfaces, skip firewall.
    const proposalRows = page.locator('#cfgmap-tgt-scroll .cfgmap-mapping-row');
    await expect(proposalRows).toHaveCount(5);
    await proposalRows.nth(0).locator('button:has-text("Approve")').click();
    await page.waitForTimeout(500);
    await proposalRows.nth(1).locator('button:has-text("Reject")').click();
    await page.waitForTimeout(500);
    await proposalRows.nth(4).locator('button:has-text("Skip")').click();
    await page.waitForTimeout(1000);

    const decisionText = await page.textContent('body');
    expect(decisionText).toContain('Approved: 1');
    expect(decisionText).toContain('Rejected: 1');
    expect(decisionText).toContain('Skipped: 1');
    expect(decisionText).toContain('Pending: 2');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_08_decisions.png`,
      fullPage: true,
    });

    // Step 9: Apply approved mappings to target config.
    await page.getByRole('button', { name: /Apply Approved to Target Config/ }).click();
    await page.waitForTimeout(2000);

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_09_applied.png`,
      fullPage: true,
    });

    // Step 10: Continue to Step 6 Config Preview.
    await page.getByRole('button', { name: /Continue/ }).click();
    await page.waitForTimeout(1000);

    const step6Text = await page.textContent('body');
    expect(step6Text).toContain('Step 6 — Config Conversion & Preview');
    expect(step6Text).not.toContain('Internal Server Error');
    await dismissTour(page);

    // Step 11: Generate and preview the target config before downloading.
    await page.getByRole('button', { name: 'Generate Target Config' }).click();
    await page.waitForTimeout(2000);

    // Verify the diff view and stats bar appear with rendered content.
    await expect(page.locator('#diff-view')).toBeVisible();
    await expect(page.locator('#diff-stats-bar')).toBeVisible();
    const sourceConfigText = await page.locator('#diff-source').textContent();
    expect(sourceConfigText).toContain('set system host-name');
    const targetConfigText = await page.locator('#diff-target').textContent();
    expect(targetConfigText).toContain('host-name');
    expect(targetConfigText).toContain('core-rtr-01');

    // Verify commit-check simulation panel renders.
    await expect(page.locator('#cc-panel')).toBeVisible();
    const ccText = await page.locator('#cc-panel').textContent();
    expect(ccText).toContain('Commit-Check Simulation');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_10_step6_preview.png`,
      fullPage: true,
    });

    // Step 12: Download the generated target config and verify suggested filename.
    const suggestedFilename = await page.evaluate(() => {
      const origCreateObjectURL = URL.createObjectURL;
      URL.createObjectURL = () => 'blob:captured';
      let capturedDownload: string | null = null;
      const origClick = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () {
        capturedDownload = this.download;
        return;
      };
      (window as any).downloadTargetConfig();
      URL.createObjectURL = origCreateObjectURL;
      HTMLAnchorElement.prototype.click = origClick;
      return capturedDownload;
    });
    expect(suggestedFilename).toMatch(/-config\.txt$/);

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_11_downloaded.png`,
      fullPage: true,
    });
  });
});
