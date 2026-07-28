// CUI // SP-CTI
// E2E Test: DWO trigger → run linkage (dwo-vv-03-d3)
//
// The DWO card's event-driven claim is that a run can be *explained*: an event
// arrived, a bound trigger matched it, a run started, and the run points back
// at the exact event that started it. This spec proves that end to end in a
// browser — source, trigger, event, run, and the run-detail badge carrying the
// trigger event id.
//
// Two tests, split by what each can prove on a given checkout
// -----------------------------------------------------------
// 1. "the gateway accepts a correctly signed event…" — depends only on
//    tools/gateway/gateway_agent.py, which is on main, so it RUNS. It boots a
//    gateway this spec owns and asserts unsigned → 401, signature-over-other-
//    bytes → 401, correct signature → not 401.
//
// 2. "a signed event binds through a trigger to a run…" — the full flow:
//      bind      POST /api/studio/event-sources + POST /api/studio/triggers
//      signed    a HMAC-SHA256 signed webhook at /gateway/github
//      fire      the event reaches the trigger registry and starts a run
//      linkage   GET /api/studio/workflows/runs/<id> → run.trigger_event
//      DOM       #wf-run-trigger-badge carries data-trigger-event-id
//    It skips until the trigger stack lands (see below).
//
// Two negative controls run alongside the second: a payload that fails the
// trigger's filter must start nothing, and a hand-started run must render no
// badge. Without them a trigger that fired on everything, or a badge that
// rendered on every run, would satisfy every other assertion here.
//
// Where the implementation lives (read this before debugging a skip)
// ------------------------------------------------------------------
// As of 2026-07-28 the event/trigger stack is spread over open PRs, none of
// them merged, and two of them divergent:
//
//   PR #967  kanban/dwo-evt-04  the surface this spec targets — REST API for
//                               event sources and triggers, start_run(inputs=,
//                               trigger_event_id=), studio_workflow_runs
//                               .trigger_event_id, and get_run_trigger_event()
//                               feeding run["trigger_event"].
//   PR #953  kanban/dwo-evt-03  a tools/studio/event_sources.py with a
//                               different API (log_trigger_event, dispatch_event).
//   PR #947  kanban/dwo-evt-02  the gateway hop: gateway_agent hands a
//                               chain-cleared envelope to event_dispatch
//                               .dispatch_envelope_async, plus a THIRD
//                               event_sources.py.
//
// This spec targets #967 because that is the branch that owns the run linkage
// the card is about, and it is self-contained (its trigger model lives in
// workflow_editor.py and imports none of the other two). Every leg is probed
// for at runtime rather than assumed, so on a checkout without the stack the
// spec skips with the name of the thing that is missing instead of failing
// with a 404 someone has to go decode.
//
// Screenshot
// ----------
// playwright.config.ts wipes outputDir (.tmp/test_runs/playwright-artifacts)
// on every run. The screenshot goes straight to playwright/screenshots/ — the
// repo convention, and not the output dir — so it survives. The path is
// resolved from __dirname so a cwd change cannot move it.

import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { spawn, spawnSync, type ChildProcess } from 'child_process';
import crypto from 'crypto';
import fs from 'fs';
import net from 'net';
import path from 'path';

const ROOT = path.resolve(__dirname, '../..');
const SCREENSHOTS = path.resolve(ROOT, 'playwright/screenshots');
const SCREENSHOT_LINK = path.join(SCREENSHOTS, 'dwo-trigger-link.png');

const PYTHON = process.env.ICDEV_PYTHON || 'python';
const STUDIO_URL = '/studio/workflows';
const STUDIO_READY_TIMEOUT = 30_000;

// The gateway is a separate Flask app (tools/gateway/gateway_agent.py, its own
// port), not a blueprint on the dashboard — so the signed leg needs its own
// process. Boot is imports + adapter loading, well under the dashboard's.
const GATEWAY_BOOT_TIMEOUT_MS = 90_000;

// A secret this spec invents for the process it owns. It is a test fixture,
// never read from .env: signing with the host's real GITHUB_WEBHOOK_SECRET
// would make the assertion depend on an operator's configuration, and a spec
// that passes only on a correctly-configured host proves nothing.
const WEBHOOK_SECRET = 'dwo-vv-03-d3-e2e-fixture-secret';

// The fixture workflow. Both steps declare an empty `tool` on purpose:
// _exec_step records them `skipped` without spawning a subprocess, which is
// deterministic on any host. What matters here is that a run row exists and
// carries its trigger event id, not what the run computed.
const FIXTURE_WORKFLOW = {
  name: 'DWO E2E — trigger to run linkage',
  description: 'E2E fixture (dwo-vv-03-d3): a run started by an event, not a person.',
  category: 'custom',
  steps: [
    { id: 'ingest', name: 'Ingest', tool: '', node_type: 'tool', depends_on: [] },
    { id: 'report', name: 'Report', tool: '', node_type: 'tool', depends_on: ['ingest'] },
  ],
};

// The event under test. Flat keys, deliberately: match_trigger evaluates the
// filter with automation_builder.evaluate_conditions, which does a top-level
// event.get(field) — it does NOT walk dotted paths. The input mapping DOES
// (workflow_editor.resolve_path), so `pr.number` below is a real assertion
// that the two resolvers are wired to the right halves of the event.
const MATCHING_PAYLOAD = {
  event_type: 'pr.merged',
  branch: 'main',
  repo: 'icdev',
  pr: { number: 4231, title: 'trigger to run linkage' },
};

// Same shape, wrong branch — the filter must reject it.
const NON_MATCHING_PAYLOAD = {
  event_type: 'pr.merged',
  branch: 'feature/scratch',
  repo: 'icdev',
  pr: { number: 4232, title: 'should not fire' },
};

const TRIGGER_CONDITIONS = [{ field: 'branch', operator: 'equals', value: 'main' }];
const TRIGGER_INPUT_MAPPING = { branch: 'branch', pr_number: 'pr.number' };

interface Gateway {
  proc: ChildProcess;
  base: string;
  log: string[];
}

let gateway: Gateway | null = null;

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

/** Anything answering on the port counts as up. */
async function gatewayIsUp(base: string): Promise<boolean> {
  try {
    await fetch(`${base}/health`, { signal: AbortSignal.timeout(4000) });
    return true;
  } catch {
    return false;
  }
}

/**
 * Start a gateway agent this spec owns, with the github channel enabled.
 *
 * Why github, and why in-process config
 * -------------------------------------
 * Of the channels the gateway ships, only GitHubAdapter.verify_signature does
 * real HMAC — SlackAdapter's returns True unconditionally, and telegram /
 * mattermost compare a shared token rather than signing the body. So github is
 * the only channel against which "signed" can actually be asserted. It ships
 * `enabled: false` in args/remote_gateway_config.yaml.
 *
 * The override is applied to the config object in the spawned interpreter, not
 * to the YAML on disk: enabling a public-internet channel repo-wide is a real
 * posture change, and this spec has no business making one. gateway_agent.py
 * also reads its port from $PORT — there is no --port flag — which is why the
 * launcher below calls app.run() itself rather than main().
 */
const GATEWAY_LAUNCHER = `
import os
import tools.gateway.gateway_agent as ga

config = ga._load_config()
config.setdefault("channels", {}).setdefault("github", {}).update({
    "enabled": True,
    # The host may be configured air_gapped, which would drop an
    # internet-requiring adapter. Nothing leaves the box: the only client is
    # the spec, on loopback.
    "requires_internet": False,
    "webhook_path": "/gateway/github",
    "signature_header": "X-Hub-Signature-256",
})
ga._load_config = lambda: config

ga.create_app().run(host="127.0.0.1", port=int(os.environ["ICDEV_GATEWAY_TEST_PORT"]))
`;

async function startGateway(port: number): Promise<Gateway> {
  const base = `http://127.0.0.1:${port}`;
  const log: string[] = [];
  const proc = spawn(
    PYTHON,
    ['-c', GATEWAY_LAUNCHER],
    {
      cwd: ROOT,
      env: {
        // The storage backend is deliberately INHERITED, not pinned. Pinning it
        // to sqlite is what took the sibling restart-durability spec out of the
        // CI sweep: that job exports ICDEV_STORAGE_BACKEND=postgresql, and a
        // child that disagrees comes up against a database nobody provisioned.
        // This child follows whatever the host is already using.
        ...process.env,
        PYTHONPATH: ROOT,
        ICDEV_GATEWAY_TEST_PORT: String(port),
        GITHUB_WEBHOOK_SECRET: WEBHOOK_SECRET,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    },
  );
  const capture = (buf: Buffer) => {
    log.push(buf.toString());
    if (log.length > 400) log.splice(0, log.length - 400);
  };
  proc.stdout?.on('data', capture);
  proc.stderr?.on('data', capture);

  let exited = false;
  proc.on('exit', () => { exited = true; });

  const gw: Gateway = { proc, base, log };
  try {
    await waitFor(async () => {
      if (exited) throw new Error(`Gateway exited during boot:\n${log.join('')}`);
      return gatewayIsUp(base);
    }, GATEWAY_BOOT_TIMEOUT_MS, `the gateway to serve ${base}`);
  } catch (err) {
    await stopGateway(gw);
    throw new Error(`${(err as Error).message}\n--- gateway output ---\n${log.join('')}`);
  }
  return gw;
}

/**
 * Stop ONLY the process this spec started.
 *
 * The repo rule against blanket-killing processes applies: no `pkill python`,
 * no sweep of whatever is listening. The kill is tree-scoped on Windows so
 * anything this instance spawned goes with it.
 */
async function stopGateway(gw: Gateway): Promise<void> {
  const pid = gw.proc.pid;
  if (pid !== undefined && gw.proc.exitCode === null) {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      try { process.kill(-pid, 'SIGTERM'); } catch { /* already gone */ }
    }
  }
  await waitFor(async () => !(await gatewayIsUp(gw.base)), 30_000,
    `${gw.base} to stop answering`);
}

/** GitHub's scheme: sha256=<hex hmac of the exact bytes sent>. */
function sign(body: string): string {
  return 'sha256=' + crypto.createHmac('sha256', WEBHOOK_SECRET).update(body).digest('hex');
}

// ── capability probes ──────────────────────────────────────────────────────

/**
 * Is the dwo-evt-04 trigger stack on this checkout?
 *
 * Probed rather than assumed so the skip message names what is missing: a 404
 * here would otherwise surface as an assertion failure several steps later,
 * against a symptom rather than the cause.
 *
 * "Absent" and "unreachable" are deliberately not the same answer. A studio
 * route that main already has is fetched first; if THAT fails there is no
 * dashboard to test and the spec fails, because skipping would quietly report
 * a broken environment as a clean run.
 */
async function triggerApiPresent(request: APIRequestContext): Promise<boolean> {
  const baseline = await request.get('/api/studio/workflows');
  expect(
    baseline.status(),
    'the dashboard is not serving Workflow Studio — this is an environment failure, not a missing feature',
  ).toBe(200);

  return (await request.get('/api/studio/event-sources')).status() === 200;
}

/** Does the gateway hand chain-cleared envelopes to the trigger registry? */
function gatewayDispatchHookPresent(): boolean {
  const probe = spawnSync(
    PYTHON,
    ['-c', 'import tools.studio.event_dispatch as d; print(hasattr(d, "dispatch_envelope_async"))'],
    { cwd: ROOT, encoding: 'utf-8', env: { ...process.env, PYTHONPATH: ROOT } },
  );
  return probe.status === 0 && (probe.stdout || '').includes('True');
}

// ── UI helpers ─────────────────────────────────────────────────────────────

async function openStudio(page: Page): Promise<void> {
  await page.goto(STUDIO_URL, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#wf-studio-layout')).toBeVisible({ timeout: STUDIO_READY_TIMEOUT });
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

test.describe('DWO — an event-started run points back at the event that started it', () => {
  test.beforeAll(() => {
    fs.mkdirSync(SCREENSHOTS, { recursive: true });
  });

  test.afterAll(async () => {
    if (gateway) {
      await stopGateway(gateway).catch(() => { /* best effort */ });
      gateway = null;
    }
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });
  });

  /**
   * The signature leg, on its own, because it can run today.
   *
   * The linkage test below skips on any checkout without the dwo-evt-04 stack,
   * and on 2026-07-28 that is every checkout — so without this test the whole
   * file would be 500 lines that have never executed. This half depends on
   * nothing but tools/gateway/gateway_agent.py, which is on main, so it runs
   * and asserts for real: an unsigned webhook and one signed over different
   * bytes are both refused, and a correct signature is not.
   *
   * It also leaves `gateway` up for the linkage test, which needs the same
   * process; whichever test runs first pays for the boot.
   */
  test('the gateway accepts a correctly signed event and refuses everything else', async ({
    page,
  }) => {
    test.setTimeout(180_000);
    if (!gateway) gateway = await startGateway(await freePort());

    const body = JSON.stringify(MATCHING_PAYLOAD);

    const unsigned = await page.request.post(`${gateway.base}/gateway/github`, {
      headers: { 'Content-Type': 'application/json' },
      data: body,
      failOnStatusCode: false,
    });
    expect(unsigned.status(), 'an unsigned webhook must not be accepted').toBe(401);

    const tampered = await page.request.post(`${gateway.base}/gateway/github`, {
      headers: {
        'Content-Type': 'application/json',
        'X-Hub-Signature-256': sign(body + ' '),
      },
      data: body,
      failOnStatusCode: false,
    });
    expect(tampered.status(), 'a signature over different bytes must not verify').toBe(401);

    const signed = await page.request.post(`${gateway.base}/gateway/github`, {
      headers: {
        'Content-Type': 'application/json',
        'X-Hub-Signature-256': sign(body),
        'X-GitHub-Event': 'pull_request',
        'X-GitHub-Delivery': `dwo-vv-03-d3-sig-${Date.now()}`,
      },
      data: body,
      failOnStatusCode: false,
    });
    // Not-401 is the assertion, not 200: past the HMAC the envelope meets the
    // security chain, which may legitimately decline the *command*. What is
    // proven here is that a correct signature gets through a door an incorrect
    // one does not.
    expect(signed.status(), await signed.text()).not.toBe(401);
  });

  test('a signed event binds through a trigger to a run whose detail names the event', async ({
    page,
  }) => {
    // Gateway boot + a workflow run, on top of the shared dashboard.
    test.setTimeout(300_000);

    test.skip(
      !(await triggerApiPresent(page.request)),
      'the dwo-evt-04 trigger stack is not on this checkout: '
      + 'GET /api/studio/event-sources does not answer 200. It ships on '
      + 'kanban/dwo-evt-04 (PR #967) — event-source and trigger routes in '
      + 'tools/dashboard/api/studio.py, backed by tools/studio/workflow_editor.py.',
    );

    let workflowId = '';
    let sourceId = '';
    let triggerId = '';
    let runId = '';
    let eventId = '';

    await test.step('Workflow Studio renders', async () => {
      await openStudio(page);
      await expect(page.locator('#wf-canvas')).toBeAttached();
    });

    await test.step('create the fixture workflow', async () => {
      const resp = await page.request.post('/api/studio/workflows', { data: FIXTURE_WORKFLOW });
      expect(resp.status(), await resp.text()).toBe(201);
      workflowId = (await resp.json()).workflow_id;
      expect(workflowId).toBeTruthy();
    });

    await test.step('register an event source', async () => {
      const resp = await page.request.post('/api/studio/event-sources', {
        data: {
          name: `dwo-vv-03-d3 github ${Date.now()}`,
          kind: 'gateway_channel',
          config: { channel: 'github' },
        },
      });
      expect(resp.status(), await resp.text()).toBe(201);
      sourceId = (await resp.json()).source_id;
      expect(sourceId).toMatch(/^src-/);
    });

    await test.step('bind a trigger to the source and the workflow', async () => {
      const resp = await page.request.post('/api/studio/triggers', {
        data: {
          source_id: sourceId,
          workflow_id: workflowId,
          event_type: MATCHING_PAYLOAD.event_type,
          conditions: TRIGGER_CONDITIONS,
          input_mapping: TRIGGER_INPUT_MAPPING,
          enabled: true,
        },
      });
      expect(resp.status(), await resp.text()).toBe(201);
      const trigger = await resp.json();
      triggerId = trigger.trigger_id;
      expect(triggerId).toMatch(/^trg-/);

      // Read it back: the binding must be what the board will later blame for
      // a run, so it has to survive the round trip, not just the INSERT.
      const readBack = await page.request.get(`/api/studio/triggers/${triggerId}`);
      expect(readBack.status()).toBe(200);
      const stored = await readBack.json();
      expect(stored.source_id).toBe(sourceId);
      expect(stored.workflow_id).toBe(workflowId);
      expect(stored.enabled).toBe(true);
      expect(stored.filter).toEqual(TRIGGER_CONDITIONS);
      expect(stored.input_mapping).toEqual(TRIGGER_INPUT_MAPPING);
    });

    await test.step('a correctly signed event clears the gateway', async () => {
      // The reject path is asserted by the signature test above; this is the
      // accept path, fired at the trigger the steps above just bound.
      if (!gateway) gateway = await startGateway(await freePort());

      const body = JSON.stringify({ ...MATCHING_PAYLOAD, action: 'closed' });
      const signed = await page.request.post(`${gateway.base}/gateway/github`, {
        headers: {
          'Content-Type': 'application/json',
          'X-Hub-Signature-256': sign(body),
          'X-GitHub-Event': 'pull_request',
          'X-GitHub-Delivery': `dwo-vv-03-d3-${Date.now()}`,
        },
        data: body,
        failOnStatusCode: false,
      });
      // Anything but 401 means the HMAC verified — the envelope then goes on
      // to the security chain, which may still reject the *command*. That is
      // the gateway's business, not this spec's; what is asserted here is that
      // a correct signature is accepted where an incorrect one was not.
      expect(signed.status(), await signed.text()).not.toBe(401);
    });

    await test.step('the event reaches the trigger registry and starts a run', async () => {
      // Two ingress paths exist, and they belong to different branches:
      //
      //   dwo-evt-02 (PR #947) hooks gateway_agent to event_dispatch, so the
      //     signed POST above starts the run by itself.
      //   dwo-evt-04 (PR #967) — the branch this spec targets — exposes the
      //     registry directly at /triggers/<id>/simulate {execute:true}.
      //
      // Both hand the same trigger the same event. When the gateway hook is
      // present it is used, because that is the path a real event takes; the
      // registry call is the fallback so this spec still proves the linkage on
      // a checkout that has #967 but not #947. Which one ran is logged, so a
      // green result is never mistaken for coverage it did not have.
      if (gatewayDispatchHookPresent()) {
        console.log('[dwo-vv-03-d3] ingress: gateway → event_dispatch (dwo-evt-02 present)');
        await waitFor(async () => {
          const resp = await page.request.get(`/api/studio/workflows/runs?workflow_id=${workflowId}`);
          return ((await resp.json()).runs || []).length > 0;
        }, 60_000, 'the signed gateway event to start a run');

        const runs = await (await page.request.get(
          `/api/studio/workflows/runs?workflow_id=${workflowId}`,
        )).json();
        runId = runs.runs[0].run_id;
      } else {
        console.log('[dwo-vv-03-d3] ingress: trigger registry (dwo-evt-02 gateway hook absent)');
        const resp = await page.request.post(`/api/studio/triggers/${triggerId}/simulate`, {
          data: { payload: MATCHING_PAYLOAD, execute: true },
        });
        expect(resp.status(), await resp.text()).toBe(200);
        const fired = await resp.json();
        expect(fired.matched, `trigger did not match: ${fired.reason}`).toBe(true);
        expect(fired.executed).toBe(true);
        // The input mapping resolved both a flat field and a nested one.
        expect(fired.inputs).toEqual({ branch: 'main', pr_number: 4231 });
        runId = fired.run_id;
        eventId = fired.event_id;
        expect(eventId).toMatch(/^evt-/);
      }
      expect(runId, 'no run was started').toMatch(/^run-/);
    });

    await test.step('a payload that fails the filter starts nothing', async () => {
      // Without this, a trigger that fired on every event would satisfy every
      // other assertion in this spec.
      const before = await (await page.request.get(
        `/api/studio/workflows/runs?workflow_id=${workflowId}`,
      )).json();

      const resp = await page.request.post(`/api/studio/triggers/${triggerId}/simulate`, {
        data: { payload: NON_MATCHING_PAYLOAD, execute: true },
      });
      expect(resp.status(), await resp.text()).toBe(200);
      const result = await resp.json();
      expect(result.matched, 'the filter accepted the wrong branch').toBe(false);
      expect(result.executed).toBe(false);
      expect(result.run_id).toBeFalsy();
      expect(result.reason, 'a non-match must say why').toContain('branch');

      const after = await (await page.request.get(
        `/api/studio/workflows/runs?workflow_id=${workflowId}`,
      )).json();
      expect(
        (after.runs || []).length,
        'a non-matching event started a run',
      ).toBe((before.runs || []).length);
    });

    await test.step('the run API reports the event that started it', async () => {
      const resp = await page.request.get(`/api/studio/workflows/runs/${runId}`);
      expect(resp.status(), await resp.text()).toBe(200);
      const run = await resp.json();

      expect(run.trigger_event, 'the run does not name the event that started it').toBeTruthy();
      expect(run.trigger_event.trigger_id).toBe(triggerId);
      expect(run.trigger_event.source_id).toBe(sourceId);
      expect(run.trigger_event.matched).toBe(true);
      expect(run.trigger_event.run_id).toBe(runId);
      expect(run.trigger_event.event_id).toMatch(/^evt-/);

      // The trigger payload became the run's inputs — the other half of the
      // linkage: the run knows not just which event, but what it carried.
      expect(run.inputs).toEqual({ branch: 'main', pr_number: 4231 });

      // When the registry path ran, this is the id it already handed back;
      // when the gateway path ran, this is where the id first becomes known.
      if (eventId) expect(run.trigger_event.event_id).toBe(eventId);
      eventId = run.trigger_event.event_id;
    });

    await test.step('the run-detail badge carries the trigger event id', async () => {
      await openStudio(page);
      await openRunDetail(page, runId);

      const badge = page.locator('#wf-run-trigger-badge');
      await expect(badge, 'run detail renders no trigger badge').toBeVisible();

      // The id is asserted twice, the way it is rendered twice: as an
      // attribute an audit can read, and as text a reviewer sees on screen.
      await expect(badge).toHaveAttribute('data-trigger-event-id', eventId);
      await expect(badge).toHaveAttribute('data-trigger-id', triggerId);
      await expect(badge).toContainText(eventId);
      await expect(page.locator('#wf-run-detail-body')).toContainText(eventId);

      await page.screenshot({ path: SCREENSHOT_LINK, fullPage: true });
      expect(fs.existsSync(SCREENSHOT_LINK)).toBe(true);
    });

    await test.step('a manually started run shows no badge', async () => {
      // The badge must mean something. A badge that rendered on every run
      // would carry no information, and the assertion above would be blind to
      // it — the manual run is the control for the badge itself.
      const started = await page.request.post(`/api/studio/workflows/${workflowId}/run`, {
        data: { project_id: 'default' },
      });
      expect(started.status(), await started.text()).toBe(202);
      const manualRunId = (await started.json()).run_id;

      const manual = await (await page.request.get(
        `/api/studio/workflows/runs/${manualRunId}`,
      )).json();
      expect(manual.trigger_event, 'a hand-started run claimed an event').toBeNull();

      await openRunDetail(page, manualRunId);
      await expect(page.locator('#wf-run-trigger-badge')).toHaveCount(0);

      await page.request.delete(`/api/studio/workflows/runs/${manualRunId}`);
    });

    await test.step('clean up the fixture', async () => {
      // studio_trigger_events rows are append-only and stay behind on purpose:
      // deleting the trigger must not erase why past runs started.
      await page.request.delete(`/api/studio/triggers/${triggerId}`);
      await page.request.delete(`/api/studio/workflows/runs/${runId}`);
      await page.request.delete(`/api/studio/workflows/${workflowId}`);
    });
  });
});
// CUI // SP-CTI
