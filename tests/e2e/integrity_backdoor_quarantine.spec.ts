// CUI // SP-CTI
// E2E Test: SIPA Backdoor Assessment — QUARANTINE (sipa-vv-04-d3)
//
// Submits the committed fixture tests/e2e/fixtures/integrity_backdoor_pkg/ — a
// package that CLAIMS "formatting only" (README) while hiding base64-decoded
// socket / exec payloads (formatter.py) — to the SIPA assess API, asserts the
// verdict is QUARANTINE, then opens the assessment detail view and verifies it
// surfaces the required fields: the network_egress / dynamic_code / obfuscation
// capabilities and the undisclosed ("undiscovered") + known-bad-grade flags.
//
// SIPA is static-only: the artifact is staged into quarantine and scanned, never
// executed. The deterministic, server-less counterpart of this flow lives in
// tests/e2e/test_integrity_backdoor_quarantine.py (real engine + Flask client).
//
// The integrity canvas only mounts when the dashboard runs from a build that
// carries tools/integrity/ (flag ICDEV_INTEGRITY_ENABLED, default-on). When the
// target dashboard predates the canvas, /api/integrity/* 404s and the whole suite
// skips rather than failing.

import { test, expect } from './fixtures/auth';
import path from 'path';

const CUI_BANNER = 'CUI // SP-CTI';
const SCREENSHOT_DIR = '.tmp/test_runs/screenshots';
const REQUIRED_CAPABILITIES = ['network_egress', 'dynamic_code', 'obfuscation'];

// Absolute path to the committed fixture (the dashboard reads it from disk; the
// Playwright runner and the server share the same host).
const FIXTURE_DIR = path.resolve(__dirname, 'fixtures', 'integrity_backdoor_pkg');

let assessmentId: number | null = null;

test.describe('SIPA Backdoor Assessment — QUARANTINE', () => {
  test.beforeAll(async ({ request }) => {
    // Skip the whole suite when the integrity canvas isn't mounted on the target.
    const probe = await request.get('/api/integrity/assessments?limit=1');
    test.skip(probe.status() === 404, 'integrity canvas not mounted on target dashboard');
  });

  test('assess fixture -> verdict is QUARANTINE', async ({ request }) => {
    // `/api/integrity/assess` does the whole assessment SYNCHRONOUSLY: it stages
    // the artifact, then runs four scanner subprocesses (sast, secrets, deps,
    // semgrep) in series before scoring. Its cost is dominated by process spawn
    // + Semgrep, so it tracks how loaded the host is, NOT how big the input is —
    // this fixture is two files totalling ~2 KB.
    //
    // MEASURED, server-side, over every recorded assessment of this fixture
    // (integrity_assessments JOIN integrity_verdicts, n=21, 2026-07-29..08-26):
    // 5.2s min / 9.0s median / 45.0s max in the population since 2026-08-09
    // (n=13; two 2026-07-29 outliers ran 144.7s and 173.6s). EIGHT of those 21
    // runs — 38% — took longer than the 30s this call used to allow, which is
    // why it flaked: the budget sat in the middle of the observed spread.
    //
    // ALL 21 RETURNED `assessed` / `quarantine`. The assertions below have never
    // once failed; only the clock did. So this budget is not a performance
    // assertion and relaxing it cannot let a wrong verdict through — a broken
    // endpoint still fails here, just later. 120s is ~2.7x the modern maximum.
    // The per-test budget must exceed it, or the 60s default in
    // playwright.config.ts fires first and the request budget is dead code.
    test.setTimeout(180_000);
    const resp = await request.post('/api/integrity/assess', {
      data: { source: FIXTURE_DIR, mode: 'provenance_blind' },
      timeout: 120_000,
    });
    expect(resp.status(), await resp.text()).toBe(201);

    const body = await resp.json();
    expect(body).toHaveProperty('assessment_id');
    expect(String(body.verdict).toLowerCase()).toBe('quarantine');
    expect(Number(body.risk_score)).toBeGreaterThanOrEqual(70);

    assessmentId = body.assessment_id;
  });

  test('detail API surfaces required capabilities + undisclosed/known-bad flags', async ({ request }) => {
    if (assessmentId == null) test.skip();

    const resp = await request.get(`/api/integrity/assessment/${assessmentId}`);
    expect(resp.status()).toBe(200);
    const detail = await resp.json();

    const capTypes = (detail.capabilities ?? []).map((c: { capability_type: string }) => c.capability_type);
    for (const cap of REQUIRED_CAPABILITIES) {
      expect(capTypes, `detail view missing capability ${cap}`).toContain(cap);
    }

    const findingTypes = (detail.findings ?? []).map((f: { finding_type: string }) => f.finding_type);
    const severities = (detail.findings ?? []).map((f: { severity: string }) => f.severity);
    // "undiscovered" gap flag (disclosed-vs-exercised) is deterministic.
    expect(findingTypes, 'expected an undisclosed_capability flag').toContain('undisclosed_capability');
    // "known-bad"-grade severity is deterministic; a literal known_bad_signature
    // additionally fires only when Semgrep is unavailable (regex fallback).
    expect(severities, 'expected a critical (known-bad-grade) finding').toContain('critical');

    expect(String(detail.verdict?.verdict).toLowerCase()).toBe('quarantine');
  });

  test('detail PAGE renders the QUARANTINE verdict and capabilities', async ({ page }) => {
    if (assessmentId == null) test.skip();

    await page.goto(`/integrity/${assessmentId}`);
    await page.waitForLoadState('domcontentloaded');
    await page.screenshot({ path: `${SCREENSHOT_DIR}/sipa_backdoor_quarantine.png`, fullPage: true });

    const bodyText = (await page.textContent('body')) ?? '';
    const lc = bodyText.toLowerCase();

    expect(lc).not.toContain('internal server error');
    expect(lc).toContain('quarantine');
    // Capability pills render with underscores replaced by spaces; accept either.
    for (const cap of REQUIRED_CAPABILITIES) {
      const spaced = cap.replace(/_/g, ' ');
      expect(lc.includes(cap) || lc.includes(spaced), `detail page missing ${cap}`).toBe(true);
    }
    expect(bodyText).toContain(CUI_BANNER);
  });
});
// CUI // SP-CTI
