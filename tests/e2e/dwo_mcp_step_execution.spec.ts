// CUI // SP-CTI
// E2E Test: DWO — a `node_type: mcp` step runs a registry tool (dwo-vv-03-d4)
//
// dwo-mcp-03 taught Workflow Studio a fourth node type: instead of naming a
// script path in `tool`, a step names a TOOL_REGISTRY entry in `mcp_tool` and
// the runner dispatches it through one shared executor. The pytest layer covers
// the pieces — test_dwo_mcp_node_type.py asserts the command line the runner
// builds, test_dwo_vv_mcp_dispatch.py asserts the executor's refusals,
// test_dwo_mcp_allowlist.py asserts the policy's shape. None of them proves the
// thing an operator actually experiences: that an MCP step's RESULT comes back
// through the run and is legible in the Run Detail modal.
//
// That is this spec. It creates a workflow whose only step is an MCP node, runs
// it, and reads the result out of the DOM — not out of the API response the DOM
// was supposed to render. The distinction is the point: `_renderRunDetail`
// (workflow-studio-exec.js:602) renders the output cell as
//
//     ${artLinks || _esc((s.stdout || s.stderr || '—').slice(0, 150))}
//
// so a stdout that parses as JSON carrying `artifacts` is REPLACED by links, and
// anything past 150 characters is cut. A result present in the API and absent
// from the table is exactly the defect this spec exists to catch, and asserting
// on the API alone would sail straight past it.
//
// Why the tool is `nist_lookup`
// -----------------------------
// It must clear the dwo-mcp-02 allowlist without a human gate (so it has to sit
// in `mcp_workflow_tools.allowed`, not `requires_approval`) and it must answer
// identically on every host. `nist_lookup` reads the in-repo 800-53 catalog: no
// DB, no network, no credentials, and `AC-2` resolves to a fixed control record.
// `health_check` — what the dispatch unit tests use — is also allowlisted but
// reports on the host it runs on, so its payload is not a constant to assert on.
//
// The executor emits `json.dumps({"status": "success", **payload})` with payload
// keyed `tool, category, handler, duration_ms, …, result`. So `"status"` lands at
// character 2 and `"tool"` at ~30 — both comfortably inside the 150-character
// cut — and the payload declares no `artifacts` key, so the text is rendered
// rather than swapped for links. Both DOM assertions below depend on those two
// facts; if the executor's payload shape changes, they are what will catch it.
//
// Location note: the dwo-vv-03-d4 card names
// `playwright/e2e/dwo/mcp-step-execution.spec.ts`. `playwright.config.ts` sets
// `testDir: tests/e2e`, so a spec under playwright/e2e/ would never be collected
// and could not run at all. It lives here with the other 60-odd specs, next to
// its dwo-vv-03-d2 sibling; only the screenshot keeps the card's name.
//
// Screenshot
// ----------
// `playwright.config.ts` wipes `outputDir` (.tmp/test_runs/playwright-artifacts)
// on every run, so the evidence goes straight to playwright/screenshots/ — the
// repo convention, and not the output dir. The path resolves from __dirname so a
// cwd change cannot move it.
//
// Running it
// ----------
//   ICDEV_E2E_DWO_MCP=1 npx playwright test tests/e2e/dwo_mcp_step_execution.spec.ts
//
// against a dashboard that satisfies both preconditions in the skip guard below.
// `ICDEV_DASHBOARD_URL` retargets it at a dashboard on another port.

import { test, expect, type Page } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(__dirname, '../..');
const SCREENSHOTS = path.resolve(ROOT, 'playwright/screenshots');
const SCREENSHOT = path.join(SCREENSHOTS, 'dwo-mcp-result.png');

//: On `mcp_workflow_tools.allowed` in args/security_gates.yaml — dispatches
//: unattended, with no human gate to approve first.
const MCP_TOOL = 'nist_lookup';
const CONTROL_ID = 'AC-2';

const STEP_ID = 'lookup';
const STEP_NAME = 'NIST Control Lookup';

//: The one executor every `node_type: mcp` step shells out to —
//: workflow_runner.MCP_EXECUTOR. Asserted below, because "the step ran" and
//: "the step ran through the MCP path" are different claims.
const MCP_EXECUTOR_REL = 'tools/studio/executors/mcp_executor.py';

const FIXTURE_WORKFLOW = {
  name: 'DWO E2E — MCP step execution',
  description: 'E2E fixture (dwo-vv-03-d4): one node_type: mcp step, no gate.',
  category: 'custom',
  steps: [
    {
      id: STEP_ID,
      name: STEP_NAME,
      node_type: 'mcp',
      mcp_tool: MCP_TOOL,
      mcp_params: { control_id: CONTROL_ID },
      depends_on: [],
    },
  ],
};

const TERMINAL = ['success', 'failed', 'cancelled'];

// ── helpers ────────────────────────────────────────────────────────────────

async function waitFor(
  predicate: () => Promise<boolean>,
  timeoutMs: number,
  what: string,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Timed out after ${timeoutMs}ms waiting for ${what}`);
}

/**
 * The CSRF token the dashboard expects on every mutating request.
 *
 * tools/security/csrf.py gates POST/PUT/PATCH/DELETE on a double-submit token,
 * accepted either as the `X-CSRF-Token` header or via a browser-stamped
 * `Sec-Fetch-Site: same-origin`. base.html covers page code by wrapping
 * `window.fetch`, but `page.request` is a separate APIRequestContext: it never
 * runs that wrapper and sends no Sec-Fetch-Site, so an unheaded write comes back
 * 403 CSRF_FAILED. Read the token the page was served and echo it.
 *
 * Must be called after a navigation, so the meta tag / cookie exists.
 */
async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const token = await page.evaluate(() => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const fromMeta = meta?.getAttribute('content');
    if (fromMeta) return fromMeta;
    const m = document.cookie.match(/(?:^|;\s*)icdev_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  });
  expect(token, 'no CSRF token was issued to the page').toBeTruthy();
  return { 'X-CSRF-Token': token };
}

async function getRun(page: Page, runId: string): Promise<any> {
  const resp = await page.request.get(`/api/studio/workflows/runs/${runId}`);
  // A 500 here is the known run-table RLS gap named in the skip guard, not a
  // flake — surface the body so the reader is not left guessing.
  expect(resp.status(), await resp.text()).toBe(200);
  return resp.json();
}

function stepOf(run: any, stepId: string): any {
  return (run.steps || []).find((s: any) => s.step_id === stepId);
}

/**
 * Open the Run Detail modal and wait for its step table to render.
 *
 * workflow-studio.js declares the namespace as `const StudioWF = (() => …)()`
 * at the top level of a classic script. A top-level `const` lives in script
 * scope, not on `window`, so `window.StudioWF` is undefined — the inline
 * `onclick="StudioWF.showRunDetail(…)"` handlers reach it through the scope
 * chain. Passing the call as a STRING makes Playwright evaluate it in that same
 * global scope, which is exactly what the Details button does. A function
 * argument would be serialized and lose the binding.
 */
async function openRunDetail(page: Page, runId: string): Promise<void> {
  await page.evaluate(`StudioWF.showRunDetail(${JSON.stringify(runId)})`);
  await expect(page.locator('#wf-run-detail-modal')).toBeVisible();
  await expect(page.locator('#wf-run-detail-body table.studio-table')).toBeVisible({
    timeout: 15_000,
  });
}

// ── The test ───────────────────────────────────────────────────────────────

test.describe('DWO — a node_type: mcp step dispatches a registry tool', () => {
  // Opt-in, for two independent reasons that are both product gaps rather than
  // anything wrong with this spec. Recorded here rather than in a commit message
  // nobody will re-read:
  //
  //  1. `tools/studio/executors/mcp_executor.py` is not on main. workflow_runner
  //     names it (MCP_EXECUTOR) and dwo-mcp-03 built everything around it, but
  //     the file itself is still only on the unmerged kanban/dwo-mcp-01 …
  //     kanban/dwo-mcp-02-d5-audit chain. Without it the runner's existence
  //     check fails and the step is recorded `skipped — Tool not found`.
  //
  //  2. GET /api/studio/workflows/runs/<id> answers 500 under a request context:
  //     studio_workflow_runs and studio_workflow_run_steps have no
  //     classification / tenant_id columns, but get_connection() attaches the
  //     global RLS predicate that names them. Migration 305 fixed studio_workflows
  //     only. The same read succeeds from a plain script, which is why the pytest
  //     layer is green and the browser is not — the defect is reachable only over
  //     HTTP. Its sibling dwo-vv-03-d2 is parked on the identical blocker.
  //
  // BOTH OF THOSE ARE NOW FIXED (2026-07-28). The executor merged with
  // dwo-mcp-01 (#976) and its authorization layer with dwo-mcp-02 (#978/#979);
  // the run tables got classification/tenant_id in migration 309 (#989).
  // Verified: this spec passes end to end against a PostgreSQL dashboard.
  //
  // It stays opt-in for a different, narrower reason. It uses the SHARED
  // webServer, and playwright.config.ts pins that to sqlite — while PostgreSQL
  // is the platform's primary backend (CLAUDE.md) and data/icdev.db is an
  // unmaintained fallback that drifts. The pass above was against PG, so
  // un-gating this now would put a spec into the sweep on a backend it has not
  // been shown green on.
  //
  // Drop the guard in e2p-back-03, which moves the suite to PostgreSQL. That
  // task exists precisely because ~800 E2E tests currently never touch the
  // primary backend.
  test.skip(
    !process.env.ICDEV_E2E_DWO_MCP,
    'opt-in until e2p-back-03 moves the E2E suite to PostgreSQL: proven green on '
      + 'PG, not yet on the sqlite the shared webServer pins — set ICDEV_E2E_DWO_MCP=1',
  );

  test.beforeAll(() => {
    fs.mkdirSync(SCREENSHOTS, { recursive: true });
  });

  test('the MCP tool result reaches the run detail table', async ({ page }) => {
    // Creating the workflow, running it, and polling to a terminal status is
    // well past the 60s default.
    test.setTimeout(180_000);

    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });

    let workflowId = '';
    let runId = '';
    let csrf: Record<string, string> = {};

    await test.step('Workflow Studio renders', async () => {
      await page.goto('/studio/workflows', { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#wf-studio-layout')).toBeVisible({ timeout: 30_000 });
      await expect(page.locator('#wf-canvas')).toBeAttached();
      csrf = await csrfHeaders(page);
    });

    await test.step('create a workflow whose only step is an MCP node', async () => {
      const resp = await page.request.post('/api/studio/workflows', {
        data: FIXTURE_WORKFLOW,
        headers: csrf,
      });
      expect(resp.status(), await resp.text()).toBe(201);
      workflowId = (await resp.json()).workflow_id;
      expect(workflowId).toBeTruthy();
    });

    await test.step('run it to a terminal status', async () => {
      const resp = await page.request.post(`/api/studio/workflows/${workflowId}/run`, {
        data: { project_id: 'default' },
        headers: csrf,
      });
      expect(resp.status(), await resp.text()).toBe(202);
      runId = (await resp.json()).run_id;
      expect(runId).toMatch(/^run-/);

      await waitFor(
        async () => TERMINAL.includes((await getRun(page, runId)).status),
        120_000,
        'the run to reach a terminal status',
      );
    });

    await test.step('the step dispatched through the MCP executor and succeeded', async () => {
      const run = await getRun(page, runId);
      const step = stepOf(run, STEP_ID);
      expect(step, 'the MCP step was never recorded').toBeTruthy();

      // `skipped` is the signature of a missing executor rather than a failed
      // dispatch, so name both stdout and stderr — this message is what a future
      // reader gets instead of a bare boolean.
      expect(
        step.status,
        `MCP step did not succeed (status=${step.status}, `
          + `stdout=${step.stdout}, stderr=${step.stderr})`,
      ).toBe('success');
      expect(run.status).toBe('success');

      // dwo-mcp-03: an mcp node runs the shared executor, not an authored path.
      // Without this, a step that happened to succeed some other way would pass.
      expect(step.tool).toBe(MCP_EXECUTOR_REL);

      // The executor's stdout contract: one JSON object naming the tool it
      // dispatched and carrying the handler's return value.
      const payload = JSON.parse(step.stdout);
      expect(payload.status).toBe('success');
      expect(payload.tool).toBe(MCP_TOOL);
      expect(payload.result.control.id).toBe(CONTROL_ID);
    });

    await test.step('the result is legible in the Details modal', async () => {
      await openRunDetail(page, runId);

      const row = page
        .locator('#wf-run-detail-body table.studio-table tbody tr')
        .filter({ hasText: STEP_NAME });
      await expect(row, 'the MCP step has no row in the run detail table').toHaveCount(1);

      // Status column — the badge text, not the raw status value.
      await expect(row).toContainText('Success');

      // The output column, and the reason this spec is a browser test at all:
      // it proves the executor's payload survives `_renderRunDetail` rather than
      // being swapped for artifact links or cut off before the tool name.
      // toContainText reads rendered text, so `_esc`'s &quot; is back to `"`.
      const output = row.locator('td').last();
      await expect(output).toContainText(`"tool": "${MCP_TOOL}"`);
      await expect(output).toContainText('"status": "success"');

      await page.screenshot({ path: SCREENSHOT, fullPage: true });
      expect(fs.existsSync(SCREENSHOT), 'the evidence screenshot was not written').toBe(true);
    });

    await test.step('clean up the fixture', async () => {
      await page.request.delete(`/api/studio/workflows/runs/${runId}`, { headers: csrf });
      await page.request.delete(`/api/studio/workflows/${workflowId}`, { headers: csrf });
    });
  });
});
// CUI // SP-CTI
