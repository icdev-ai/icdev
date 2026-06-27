// CUI // SP-CTI
// E2E Test: Network Migration Wizard — Config Mapping Full Lifecycle
// Verifies the new Step 5 AI-assisted configuration mapping end-to-end:
// create session, import Juniper config, answer yes/no questions, generate
// rule-based proposals, approve/reject/skip, apply to target config, and
// navigate to Step 6 Config Preview.

import { test, expect } from '@playwright/test';

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
  test('full lifecycle: create session, import config, map, apply, preview', async ({ page }) => {
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

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_02_devices.png`,
      fullPage: true,
    });

    // Step 3: Create session and continue.
    await page.getByRole('button', { name: 'Create Session & Continue →' }).click();
    await page.waitForTimeout(1000);

    const url = page.url();
    expect(url).toMatch(/\/migration-canvas\/network-migration\/(nmig|new)/);
    // The wizard may redirect to a real session id or stay on /new; both are OK if config import UI appears.
    const afterCreateText = await page.textContent('body');
    expect(afterCreateText).toContain('Step 2 — Config / Diagram Import');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_03_step2.png`,
      fullPage: true,
    });

    // Step 4: Paste config and parse.
    await page
      .getByRole('textbox', { name: /Paste Juniper|Paste Running Config/i })
      .fill(SAMPLE_JUNIPER_CONFIG);
    await page.getByRole('button', { name: 'Parse Config →' }).click();
    await page.waitForTimeout(1500);

    const parsedText = await page.textContent('body');
    expect(parsedText).toContain('Parsed');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_04_parsed.png`,
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
    await page.getByRole('button', { name: 'Save Answers & Generate Proposals' }).click();
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

    // Step 8: Approve system, reject interfaces, skip firewall.
    const proposalRows = page.locator('#cfgmap-tgt-scroll .cfgmap-mapping-row');
    await expect(proposalRows).toHaveCount(5);
    await proposalRows.nth(0).locator('button:has-text("Approve")').click();
    await page.waitForTimeout(300);
    await proposalRows.nth(1).locator('button:has-text("Reject")').click();
    await page.waitForTimeout(300);
    await proposalRows.nth(4).locator('button:has-text("Skip")').click();
    await page.waitForTimeout(300);

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
    await page.getByRole('button', { name: 'Apply Approved to Target Config' }).click();
    await page.waitForTimeout(2000);

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_09_applied.png`,
      fullPage: true,
    });

    // Step 10: Continue to Step 6 Config Preview.
    await page.getByRole('button', { name: 'Continue →' }).click();
    await page.waitForTimeout(1000);

    const step6Text = await page.textContent('body');
    expect(step6Text).toContain('Step 6 — Config Conversion & Preview');
    expect(step6Text).not.toContain('Internal Server Error');

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/mig_net_10_step6_preview.png`,
      fullPage: true,
    });
  });
});
