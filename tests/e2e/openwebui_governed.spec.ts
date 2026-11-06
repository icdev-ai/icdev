// CUI // SP-CTI
// E2E Test: Open WebUI as a governed ICDEV client (owui-ship-02, owui-idp-01, ADR D-OWUI-1)
//
// A person signs in to Open WebUI through the shared IdP, picks an ICDEV
// capability, and gets an answer. The UI reply alone proves nothing about
// governance, so the spec then reads ICDEV's own database: the exchange must
// have left an audit row whose actor is THIS user (their forwarded IdP token,
// not a shared key) and a router telemetry row.
//
// It needs a stack this suite does not start, so it SKIPS unless OWUI_E2E_URL
// is set (CI's dashboard shards do not run it):
//   OWUI_E2E_URL        Open WebUI base URL, e.g. http://127.0.0.1:3001
//   OWUI_E2E_USER       IdP username; OWUI_E2E_PASSWORD its password
//   OWUI_E2E_EMAIL      that user's email in the ICDEV platform users table
//   OWUI_E2E_DB_URL     PostgreSQL URL of the (throwaway) ICDEV database
//   OWUI_E2E_MODEL      capability to pick (default icdev/local-only)
//   OWUI_E2E_PYTHON     python with psycopg2 (default: python3)
// Setup: docs/features/openwebui-governed-client.md ("End-to-end test").
//
// Run: OWUI_E2E_URL=... ICDEV_E2E_REACHABILITY_CHECK=0 ICDEV_E2E_DB_CHECK=0 \
//      ICDEV_E2E_WARMUP=0 npx playwright test tests/e2e/openwebui_governed.spec.ts

import { test, expect } from '@playwright/test';
import { execFileSync } from 'child_process';

const OWUI = process.env.OWUI_E2E_URL || '';
const MODEL = process.env.OWUI_E2E_MODEL || 'icdev/local-only';

// One read of ICDEV's database. Parameters go in as argv, never into the SQL text.
const QUERY = `
import json, sys, psycopg2
url, email, since = sys.argv[1:4]
c = psycopg2.connect(url); cur = c.cursor()
cur.execute("SELECT id, tenant_id FROM users WHERE email = %s", (email,))
uid, tid = cur.fetchone()
cur.execute("SELECT count(*) FROM audit_trail WHERE event_type = 'chat.message_received' "
            "AND actor = %s AND created_at > %s", (f"openai_compat:{tid}/{uid}", since))
audit = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM ai_telemetry WHERE function LIKE 'openai_compat_%%' AND logged_at > %s", (since,))
telemetry = cur.fetchone()[0]
cur.execute("SELECT now()")
print(json.dumps({"audit": audit, "telemetry": telemetry, "now": str(cur.fetchone()[0])}))
`;

function icdevRows(since: string): { audit: number; telemetry: number; now: string } {
  const out = execFileSync(process.env.OWUI_E2E_PYTHON || 'python3',
    ['-c', QUERY, process.env.OWUI_E2E_DB_URL || '', process.env.OWUI_E2E_EMAIL || '', since],
    { encoding: 'utf-8' });
  return JSON.parse(out);
}

test.describe('Open WebUI governed client', () => {
  test.skip(!OWUI, 'OWUI_E2E_URL not set: needs the Open WebUI + IdP + gateway stack');
  test.setTimeout(180_000);

  test('IdP login -> ICDEV capability -> reply, audited as this user', async ({ page }) => {
    const before = icdevRows('1970-01-01');

    // Step 1: sign in through the shared IdP -- Open WebUI's own login form is off.
    await page.goto(`${OWUI}/auth`);
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await page.getByRole('button', { name: /continue with/i }).first().click();
    await page.locator('#username').fill(process.env.OWUI_E2E_USER || '');
    await page.locator('#password').fill(process.env.OWUI_E2E_PASSWORD || '');
    await page.locator('#kc-login').click();
    await page.waitForURL((u) => u.toString().startsWith(OWUI) && !u.pathname.startsWith('/auth'));

    // Step 2: the model list is ICDEV's capabilities, and only those.
    await page.goto(`${OWUI}/?models=${encodeURIComponent(MODEL)}`);
    const input = page.locator('#chat-input');
    await expect(input).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: 'playwright/screenshots/owui_governed_01_signed_in.png' });

    // Step 3: one prompt through the governed endpoint.
    await input.click();
    await page.keyboard.type('In one short sentence, what is a system security plan?');
    await page.keyboard.press('Enter');

    // Step 4: the reply renders (generation finished: the regenerate control appears).
    const reply = page.locator('#response-content-container').last();
    await expect(reply).toBeVisible({ timeout: 120_000 });
    await expect(page.getByRole('button', { name: /regenerate/i }).last()).toBeVisible({ timeout: 120_000 });
    expect(((await reply.textContent()) || '').trim().length).toBeGreaterThan(0);
    await page.screenshot({ path: 'playwright/screenshots/owui_governed_02_reply.png', fullPage: true });

    // Step 5: ICDEV recorded it -- as this person, through the router.
    const after = icdevRows(before.now);
    expect(after.audit, 'an audit row whose actor is the signed-in IdP user').toBeGreaterThan(0);
    expect(after.telemetry, 'a router telemetry row for an openai_compat function').toBeGreaterThan(0);
  });
});
