// CUI // SP-CTI
// E2E Test: DWO restart durability (dwo-vv-03-d2)
//
// Proves the headline claim of the DWO card in a real browser: a Studio run
// parked on a human approval gate is STILL parked after the dashboard process
// that started it has died and been restarted, and can then be approved to
// completion. See .claude/commands/e2e/dwo_workflow.md (the skill card this
// spec implements) and tests/test_dwo_vv_durability.py (the pytest layer).
//
// Why this spec owns its own dashboard process
// --------------------------------------------
// The restart is the test. Playwright's `webServer` is started once for the
// whole run and cannot be recycled from inside a spec, and killing whatever is
// listening on 5050 would take out the dashboard another session owns (plus its
// kanban scheduler child). So this spec starts its OWN dashboard on a port it
// picked from the OS, drives that, and stops only the PID it started — killing
// the tree on Windows so a scheduler this instance spawned goes with it.
//
// Consequence: the spec ignores `baseURL` and navigates absolute URLs. Set
// `ICDEV_NO_SERVER=1` when running it alone to stop Playwright from also
// booting a 5050 instance it does not need. It is also opt-in via
// `ICDEV_E2E_DWO_RESTART=1` so the shared CI sweep skips it — see the skip
// guard on the describe block below, which also records the current blocker:
//
//   ICDEV_E2E_DWO_RESTART=1 ICDEV_NO_SERVER=1 \
//     npx playwright test tests/e2e/dwo_restart_durability.spec.ts --headed
//
// Screenshots
// -----------
// `playwright.config.ts` wipes `outputDir` (.tmp/test_runs/playwright-artifacts)
// on every run. These screenshots are written straight to playwright/screenshots/
// — the repo convention — which is NOT the output dir, so they survive without a
// copy step. Paths are resolved from __dirname so cwd changes cannot move them.

import { test, expect, type Page } from '@playwright/test';
import { spawn, spawnSync, type ChildProcess } from 'child_process';
import fs from 'fs';
import net from 'net';
import path from 'path';

const ROOT = path.resolve(__dirname, '../..');
const SCREENSHOTS = path.resolve(ROOT, 'playwright/screenshots');

const SCREENSHOT_BEFORE = path.join(SCREENSHOTS, 'dwo-restart-before.png');
const SCREENSHOT_AFTER = path.join(SCREENSHOTS, 'dwo-restart-after.png');
const SCREENSHOT_COMPLETE = path.join(SCREENSHOTS, 'dwo-restart-complete.png');

const PYTHON = process.env.ICDEV_PYTHON || 'python';

// Boot (imports + blueprint registration + DB init) is slow on a cold cache,
// and this spec pays it twice.
const BOOT_TIMEOUT_MS = 120_000;
const SHUTDOWN_TIMEOUT_MS = 30_000;

// Env the spawned dashboard needs, mirroring playwright.config.ts's webServer
// block: dev auto-login so the browser reaches /studio/workflows without a key.
const DASHBOARD_ENV: Record<string, string> = {
  ICDEV_STORAGE_BACKEND: 'sqlite',
  ICDEV_AUTH_BYPASS: 'true',
  ICDEV_DASHBOARD_DEV_AUTOLOGIN: 'true',
  ICDEV_CUI_BANNER_ENABLED: 'true',
};

// The fixture workflow. Both non-gate steps declare an empty `tool` on purpose:
// `_exec_step` records them `skipped` without spawning a subprocess, which is
// deterministic on any host. The d1 card suggests `tools/testing/health_check.py`
// there, but that tool exits 1 on an unhealthy host — a red run for a reason
// that has nothing to do with durability. `skipped` is in the same replay set as
// `success` (workflow_runner.resume_run), so the "replayed, not re-executed"
// assertion below is unaffected.
//
// `depends_on` is load-bearing: step order comes from a TopologicalSorter over
// it (`_resolve_dag`), not from array position.
const GATE_TIMEOUT_SECONDS = 3600; // comfortably longer than a restart; an
// expired gate is failed by reconcile_runs_on_boot(), which is a legitimate but
// different outcome and would mask the behaviour under test.

const FIXTURE_WORKFLOW = {
  name: 'DWO E2E — restart durability',
  description: 'E2E fixture (dwo-vv-03-d2): a step, a human gate, a step behind it.',
  category: 'custom',
  steps: [
    { id: 'prep', name: 'Prep', tool: '', node_type: 'tool', depends_on: [] },
    {
      id: 'gate',
      name: 'Human Gate',
      node_type: 'human',
      role: 'approver',
      approval_timeout: GATE_TIMEOUT_SECONDS,
      depends_on: ['prep'],
    },
    { id: 'post', name: 'Post-Gate', tool: '', node_type: 'tool', depends_on: ['gate'] },
  ],
};

interface Dashboard {
  proc: ChildProcess;
  port: number;
  base: string;
  log: string[];
}

let dashboard: Dashboard | null = null;

// ── process + probe helpers ────────────────────────────────────────────────

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const port = (srv.address() as net.AddressInfo).port;
      srv.close(() => resolve(port));
    });
  });
}

/** True when something answers on the port at all — any status counts as up. */
async function isUp(base: string): Promise<boolean> {
  try {
    await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(4000) });
    return true;
  } catch {
    return false;
  }
}

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

async function startDashboard(port: number): Promise<Dashboard> {
  const base = `http://127.0.0.1:${port}`;
  const log: string[] = [];
  const proc = spawn(PYTHON, [path.join(ROOT, 'tools', 'dashboard', 'app.py'), '--port', String(port)], {
    cwd: ROOT,
    env: { ...process.env, ...DASHBOARD_ENV, ICDEV_DASHBOARD_PORT: String(port) },
    stdio: ['ignore', 'pipe', 'pipe'],
    // POSIX: own process group so the whole tree can be signalled at once.
    // Windows has no process groups here — taskkill /T covers it in stop().
    detached: process.platform !== 'win32',
  });
  const capture = (buf: Buffer) => {
    log.push(buf.toString());
    if (log.length > 400) log.splice(0, log.length - 400);
  };
  proc.stdout?.on('data', capture);
  proc.stderr?.on('data', capture);

  let exited = false;
  proc.on('exit', () => { exited = true; });

  const dash: Dashboard = { proc, port, base, log };
  try {
    await waitFor(async () => {
      if (exited) throw new Error(`Dashboard exited during boot:\n${log.join('')}`);
      return isUp(base);
    }, BOOT_TIMEOUT_MS, `the dashboard to serve ${base}`);
  } catch (err) {
    await stopDashboard(dash);
    throw new Error(`${(err as Error).message}\n--- dashboard output ---\n${log.join('')}`);
  }
  return dash;
}

/**
 * Stop ONLY the process this spec started, then wait for the port to go quiet.
 *
 * The repo rule against blanket-killing processes applies: no `pkill python`,
 * no port sweep. On Windows the kill is tree-scoped (`/T`) so a kanban
 * scheduler THIS instance spawned dies with it — the dashboard skips spawning
 * one when another session's heartbeat is fresh, so the tree holds only ours.
 */
async function stopDashboard(dash: Dashboard): Promise<void> {
  const pid = dash.proc.pid;
  if (pid !== undefined && dash.proc.exitCode === null) {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      try { process.kill(-pid, 'SIGTERM'); } catch { /* already gone */ }
    }
  }
  await waitFor(async () => !(await isUp(dash.base)), SHUTDOWN_TIMEOUT_MS,
    `${dash.base} to stop answering`);
}

// ── API helpers (page.request shares the browser's session cookie) ──────────

async function getRun(page: Page, base: string, runId: string): Promise<any> {
  const resp = await page.request.get(`${base}/api/studio/workflows/runs/${runId}`);
  expect(resp.status(), 'run detail should be readable').toBe(200);
  return resp.json();
}

function stepOf(run: any, stepId: string): any {
  return (run.steps || []).find((s: any) => s.step_id === stepId);
}

/** Rows recorded for a step — more than one means the step was re-executed. */
function rowsFor(run: any, stepId: string): any[] {
  return (run.steps || []).filter((s: any) => s.step_id === stepId);
}

// ── SSE helpers (EventSource runs in the page, same-origin with the API) ────

/**
 * Attach an EventSource to a run's stream and buffer every event on `window`.
 *
 * There is no race with a fast-parking run: `start_run` puts the run's queue in
 * `_run_queues` before it returns the 202, and the queue buffers (maxsize 500)
 * whether or not anything is reading it, so events emitted before this call are
 * still delivered.
 */
async function attachSse(page: Page, base: string, runId: string): Promise<void> {
  await page.evaluate(({ b, id }) => {
    const w = window as any;
    if (w.__dwoEs) { try { w.__dwoEs.close(); } catch { /* noop */ } }
    w.__dwoSse = { events: [], failed: false };
    const es = new EventSource(`${b}/api/studio/workflows/runs/${id}/stream`);
    es.onmessage = (ev: MessageEvent) => {
      try { w.__dwoSse.events.push(JSON.parse(ev.data)); }
      catch { w.__dwoSse.events.push({ type: '_unparsed', raw: ev.data }); }
    };
    es.onerror = () => { w.__dwoSse.failed = true; };
    w.__dwoEs = es;
  }, { b: base, id: runId });
}

async function sseEvents(page: Page): Promise<any[]> {
  return page.evaluate(() => ((window as any).__dwoSse?.events ?? []) as any[]);
}

async function waitForSseType(page: Page, type: string, timeoutMs: number): Promise<any[]> {
  await waitFor(
    async () => (await sseEvents(page)).some((e) => e.type === type),
    timeoutMs,
    `SSE event '${type}'`,
  );
  return sseEvents(page);
}

async function detachSse(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as any;
    if (w.__dwoEs) { try { w.__dwoEs.close(); } catch { /* noop */ } }
  });
}

// ── UI helpers ─────────────────────────────────────────────────────────────

async function openStudio(page: Page, base: string): Promise<void> {
  await page.goto(`${base}/studio/workflows`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#wf-studio-layout')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#wf-canvas')).toBeAttached();
}

/** Open the run-detail modal for `runId` and wait for it to render. */
async function openRunDetail(page: Page, runId: string): Promise<void> {
  await page.evaluate((id) => (window as any).StudioWF.showRunDetail(id), runId);
  await expect(page.locator('#wf-run-detail-modal')).toBeVisible();
  await expect(page.locator('#wf-run-detail-body table.studio-table')).toBeVisible({
    timeout: 15_000,
  });
}

// ── The test ───────────────────────────────────────────────────────────────

test.describe('DWO — a parked approval gate survives a dashboard restart', () => {
  // Opt-in, and deliberately out of the shared CI sweep.
  //
  // This spec spawns a dashboard, kills it, and starts it again. The CI E2E job
  // drives ONE long-lived dashboard that ~800 other tests share, and it exports
  // ICDEV_STORAGE_BACKEND=postgresql while this spec's child runs on sqlite — so
  // in the sweep the child comes up without a provisioned DB and /studio/workflows
  // never renders. Left ungated it is the single red test in an otherwise green
  // run, which reddens main for a reason that has nothing to do with durability.
  //
  // Run it deliberately, on a host where you own the dashboard:
  //
  //   ICDEV_E2E_DWO_RESTART=1 ICDEV_NO_SERVER=1 \
  //     npx playwright test tests/e2e/dwo_restart_durability.spec.ts --headed
  //
  // KNOWN BLOCKER (2026-07-28): even opted in, this currently fails at
  // `the run parks at awaiting_approval` with a 500 from
  // GET /api/studio/workflows/runs/<run_id>:
  //
  //   sqlite3.OperationalError: no such column: classification
  //     tools/studio/workflow_runner.py::get_run
  //
  // studio_workflow_runs and studio_workflow_run_steps have no classification /
  // tenant_id columns (studio_workflows does), so the global RLS predicate that
  // get_connection() attaches under a request context cannot be satisfied. It is
  // a product defect, not a defect in this spec — the same read succeeds outside
  // a request context, which is why the pytest layer never caught it. The fix
  // belongs to the studio-RLS-columns migration, not here; re-run this spec once
  // that lands and drop this paragraph.
  test.skip(
    !process.env.ICDEV_E2E_DWO_RESTART,
    'opt-in: set ICDEV_E2E_DWO_RESTART=1 — this spec restarts a dashboard process it owns',
  );

  test.beforeAll(() => {
    fs.mkdirSync(SCREENSHOTS, { recursive: true });
  });

  test.afterAll(async () => {
    if (dashboard) {
      await stopDashboard(dashboard).catch(() => { /* best effort */ });
      dashboard = null;
    }
  });

  test('a run parked on a human gate is still parked after a restart, then approves to completion', async ({
    page,
  }) => {
    // Two full dashboard boots plus a workflow run — far past the 60s default.
    test.setTimeout(360_000);

    // The approve button goes through window.confirm(); Playwright dismisses
    // dialogs by default, which would silently skip the approval.
    page.on('dialog', (d) => d.accept());
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });

    let base = '';
    let workflowId = '';
    let runId = '';
    let gateStepRunId = '';
    let prepBefore: any = null;

    await test.step('start a dashboard this spec owns', async () => {
      const port = await freePort();
      dashboard = await startDashboard(port);
      base = dashboard.base;
    });

    await test.step('Workflow Studio renders', async () => {
      await openStudio(page, base);
      await expect(page.locator('#wf-studio-layout')).toBeVisible();
    });

    await test.step('create the fixture workflow', async () => {
      const resp = await page.request.post(`${base}/api/studio/workflows`, {
        data: FIXTURE_WORKFLOW,
      });
      expect(resp.status(), await resp.text()).toBe(201);
      workflowId = (await resp.json()).workflow_id;
      expect(workflowId).toBeTruthy();
    });

    await test.step('start a run and capture its SSE stream', async () => {
      const resp = await page.request.post(
        `${base}/api/studio/workflows/${workflowId}/run`,
        { data: { project_id: 'default' } },
      );
      expect(resp.status(), await resp.text()).toBe(202);
      runId = (await resp.json()).run_id;
      expect(runId).toMatch(/^run-/);

      await attachSse(page, base, runId);
      const events = await waitForSseType(page, 'step_awaiting_approval', 60_000);
      // The stream is the live view of the same transition the DB records.
      expect(
        events.some((e) => e.type === 'step_awaiting_approval'),
        'the gate should announce itself on the run stream',
      ).toBe(true);
      expect(
        events.some((e) => e.type === 'error'),
        `stream reported an error: ${JSON.stringify(events.filter((e) => e.type === 'error'))}`,
      ).toBe(false);
    });

    await test.step('the run parks at awaiting_approval', async () => {
      await waitFor(
        async () => (await getRun(page, base, runId)).status === 'awaiting_approval',
        60_000,
        'the run to reach awaiting_approval',
      );
      const run = await getRun(page, base, runId);
      expect(run.status).toBe('awaiting_approval');

      const gate = stepOf(run, 'gate');
      expect(gate, 'the gate step should be recorded').toBeTruthy();
      expect(gate.status).toBe('awaiting_approval');
      gateStepRunId = gate.step_run_id;
      expect(gateStepRunId).toBeTruthy();

      // Recorded before the restart so the post-restart comparison is a real
      // before/after, not a re-read of the same fetch.
      prepBefore = stepOf(run, 'prep');
      expect(prepBefore, 'the pre-gate step should be recorded').toBeTruthy();
    });

    await test.step('pre-restart evidence', async () => {
      await openRunDetail(page, runId);
      const approve = page.locator('#wf-run-detail-body button', { hasText: 'Approve' });
      await expect(approve).toBeVisible();
      await expect(page.locator('#wf-run-detail-body')).toContainText('Awaiting approval');
      await page.screenshot({ path: SCREENSHOT_BEFORE, fullPage: true });
      expect(fs.existsSync(SCREENSHOT_BEFORE)).toBe(true);
    });

    await test.step('restart the dashboard', async () => {
      const port = dashboard!.port;
      await detachSse(page);
      await stopDashboard(dashboard!);
      // Same port, new process: nothing of the parked run survives in memory —
      // the worker thread was a daemon and died with the interpreter. Only the
      // studio_workflow_runs / _run_steps rows are left, which is the point.
      dashboard = await startDashboard(port);
      expect(dashboard.base).toBe(base);
    });

    await test.step('the run is still awaiting approval after the restart', async () => {
      const run = await getRun(page, base, runId);
      // dwo-dur-01: not force-failed by the restart.
      expect(run.status, 'the restart must not have failed the parked run').toBe(
        'awaiting_approval',
      );
      const gate = stepOf(run, 'gate');
      // dwo-dur-02/03: resume is in place, so the gate keeps its identity and
      // no second run row was forked — an approval issued against the
      // pre-restart step_run_id still resolves this run.
      expect(gate.step_run_id, 'the gate must keep its step_run_id').toBe(gateStepRunId);
      expect(gate.status).toBe('awaiting_approval');
      expect(rowsFor(run, 'gate')).toHaveLength(1);

      const runs = await (await page.request.get(
        `${base}/api/studio/workflows/runs?workflow_id=${workflowId}`,
      )).json();
      expect(
        runs.runs.map((r: any) => r.run_id),
        'reconciliation must not fork a second run row',
      ).toEqual([runId]);
    });

    await test.step('post-restart evidence', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.locator('#wf-studio-layout')).toBeVisible({ timeout: 30_000 });
      await openRunDetail(page, runId);
      const approve = page.locator('#wf-run-detail-body button', { hasText: 'Approve' });
      await expect(approve, 'approval controls must survive the restart').toBeVisible();
      await expect(page.locator('#wf-run-detail-body')).toContainText('Awaiting approval');
      await page.screenshot({ path: SCREENSHOT_AFTER, fullPage: true });
      expect(fs.existsSync(SCREENSHOT_AFTER)).toBe(true);
    });

    await test.step('approve the surviving gate from the UI', async () => {
      // A live stream here also proves reconcile_runs_on_boot() re-attached a
      // worker: stream_run answers `{type:'error'}` when no queue owns the run.
      await attachSse(page, base, runId);
      await page.locator('#wf-run-detail-body button', { hasText: 'Approve' }).click();

      await waitFor(
        async () => (await getRun(page, base, runId)).status !== 'awaiting_approval',
        90_000,
        'the approval to release the run',
      );
      const events = await sseEvents(page);
      expect(
        events.some((e) => e.type === 'error'),
        'the resumed run had no worker attached — reconcile_runs_on_boot did not re-attach it',
      ).toBe(false);
    });

    await test.step('the run completes, replaying rather than re-running its finished step', async () => {
      await waitFor(
        async () => ['success', 'failed'].includes((await getRun(page, base, runId)).status),
        90_000,
        'the run to reach a terminal status',
      );
      const run = await getRun(page, base, runId);
      expect(run.status, JSON.stringify(run.steps)).toBe('success');
      expect(stepOf(run, 'gate').status).toBe('approved');

      // The step behind the gate had never been recorded before the restart —
      // its presence is the proof the run actually continued.
      expect(stepOf(run, 'post'), 'the run did not continue past the gate').toBeTruthy();

      // dwo-dur-03: a step already recorded is replayed from its row. Same row,
      // same identity, same start — and exactly one of it.
      const prepAfter = stepOf(run, 'prep');
      expect(prepAfter.step_run_id).toBe(prepBefore.step_run_id);
      expect(prepAfter.started_at).toBe(prepBefore.started_at);
      expect(prepAfter.status).toBe(prepBefore.status);
      expect(rowsFor(run, 'prep'), 'resume re-executed a finished step').toHaveLength(1);

      await detachSse(page);
      await openRunDetail(page, runId);
      await page.screenshot({ path: SCREENSHOT_COMPLETE, fullPage: true });
    });

    await test.step('clean up the fixture', async () => {
      await page.request.delete(`${base}/api/studio/workflows/runs/${runId}`);
      await page.request.delete(`${base}/api/studio/workflows/${workflowId}`);
    });
  });
});
// CUI // SP-CTI
