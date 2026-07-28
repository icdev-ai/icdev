// CUI // SP-CTI
// E2E Test: DWO trigger → event → run linkage (dwo-vv-03-d3)
//
// The second of the three DWO V&V specs. Where dwo_restart_durability.spec.ts
// proves a run outlives the process that started it, this one proves the run
// can say *why* it started: an external event arrives on the gateway, clears
// the eight-gate security chain, matches a trigger bound to a workflow, starts
// a run — and the run-detail modal names the `studio_trigger_events` row that
// caused it.
//
// The flow, end to end:
//
//   1. spawn a gateway process this spec owns, on a free port, with a webhook
//      secret only this spec knows
//   2. bind a channel user (the gateway's own binding ceremony — gate 3 refuses
//      an unbound sender, so this is setup, not decoration)
//   3. create the fixture workflow, an event source named for the channel, and
//      a trigger binding the two, all over /api/studio/*
//   4. POST an unsigned event → assert 401 (the signature check is live)
//   5. POST the same event signed → a run appears for the bound workflow
//   6. open that run's detail modal and assert the DOM carries the trigger
//      event id, then screenshot it
//
// Why the gateway is a child process
// ----------------------------------
// The gateway is a separate Flask app (`tools/gateway/gateway_agent.create_app`),
// not a dashboard blueprint — there is no gateway on :5050 to drive. This spec
// starts its own on a port it took from the OS and stops only that PID, so it
// never disturbs a dashboard or scheduler another session owns. It drives the
// *shared* dashboard through `baseURL` and does not restart it.
//
// The two processes must resolve the SAME database — that shared row store is
// the linkage under test. The child inherits `process.env`, so it follows
// whatever `ICDEV_STORAGE_BACKEND` / `ICDEV_DB_PATH` the harness exported. If
// they diverge the run never appears and step 5 fails with that diagnosis
// spelled out, rather than as a bare timeout.
//
// What "signed" means on this channel
// -----------------------------------
// The gateway ships with `github` disabled and `telegram` enabled
// (`args/remote_gateway_config.yaml`), and `CONFIG_PATH` is a module constant —
// a spec cannot point the gateway at a different config, and rewriting the
// repo's config from a test is not on. So this drives telegram, whose gate-1
// check is a shared secret token compared with `hmac.compare_digest`
// (`TelegramAdapter.verify_signature`), not an HMAC digest over the body. That
// is the real production signature path for this channel; it is a weaker
// primitive than GitHub's X-Hub-Signature-256, and step 4 exists so a green run
// means the check actually ran rather than that it was unconfigured.
//
// Screenshots
// -----------
// `playwright.config.ts` wipes `outputDir` on every run. The screenshot below
// goes straight to playwright/screenshots/ — the repo convention, and not the
// output dir — resolved from __dirname so cwd changes cannot move it.

import { test, expect, type Page } from '@playwright/test';
import { spawn, spawnSync, type ChildProcess } from 'child_process';
import fs from 'fs';
import net from 'net';
import path from 'path';

const ROOT = path.resolve(__dirname, '../..');
const SCREENSHOTS = path.resolve(ROOT, 'playwright/screenshots');
const SCREENSHOT_LINK = path.join(SCREENSHOTS, 'dwo-trigger-link.png');

const PYTHON = process.env.ICDEV_PYTHON || 'python';

const BOOT_TIMEOUT_MS = 120_000;
const SHUTDOWN_TIMEOUT_MS = 30_000;

// The channel this spec drives. Both halves of the binding must agree, and the
// event source's NAME must equal it: dispatch looks triggers up by
// `envelope.channel` (`event_dispatch.dispatch_envelope`).
const CHANNEL = 'telegram';
const WEBHOOK_PATH = '/gateway/telegram';
const SECRET_HEADER = 'X-Telegram-Bot-Api-Secret-Token';

// `parse_command_text` turns this into command `icdev-status`, which the
// shipped `command_allowlist` grants on every channel at IL5 — comfortably
// above telegram's IL4 ceiling, so gate 5 passes. A read-only command is also
// the right choice for a test that fires a real one.
const COMMAND = '/icdev-status';

// The event type triggers match on. A telegram Update carries no `event_type` /
// `type` / `action` key, so `_event_type()` falls back to the envelope's
// command — this must equal that, or the trigger never fires.
const EVENT_TYPE = 'icdev-status';

// Non-gate steps declare an empty `tool` on purpose: `_exec_step` records them
// `skipped` without spawning a subprocess, which is deterministic on any host
// and keeps this spec about linkage rather than about tool health.
const FIXTURE_WORKFLOW = {
  name: 'DWO E2E — trigger linkage',
  description: 'E2E fixture (dwo-vv-03-d3): a run started by an external event.',
  category: 'custom',
  steps: [
    { id: 'ingest', name: 'Ingest', tool: '', node_type: 'tool', depends_on: [] },
    { id: 'report', name: 'Report', tool: '', node_type: 'tool', depends_on: ['ingest'] },
  ],
};

interface Gateway {
  proc: ChildProcess;
  port: number;
  base: string;
  secret: string;
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

/** True when the gateway answers its health route at all — any status counts. */
async function isUp(base: string): Promise<boolean> {
  try {
    await fetch(`${base}/health`, { signal: AbortSignal.timeout(4000) });
    return true;
  } catch {
    return false;
  }
}

async function startGateway(port: number, secret: string): Promise<Gateway> {
  const base = `http://127.0.0.1:${port}`;
  const log: string[] = [];
  const proc = spawn(PYTHON, [path.join(ROOT, 'tools', 'gateway', 'gateway_agent.py')], {
    cwd: ROOT,
    env: {
      ...process.env,
      // `gateway_agent.main()` reads PORT, falling back to the config's 8458.
      PORT: String(port),
      // Gate 1's shared secret. Generated per run, so a stale gateway from an
      // earlier run cannot accept this spec's events (or the reverse).
      TELEGRAM_WEBHOOK_SECRET: secret,
      // The adapter reads a token to *reply* on. There is no real bot here;
      // the reply attempt happens after dispatch has already fired and cannot
      // affect the linkage under test.
      TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN || 'e2e-not-a-real-token',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
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

  const gw: Gateway = { proc, port, base, secret, log };
  try {
    await waitFor(async () => {
      if (exited) throw new Error(`Gateway exited during boot:\n${log.join('')}`);
      return isUp(base);
    }, BOOT_TIMEOUT_MS, `the gateway to serve ${base}`);
  } catch (err) {
    await stopGateway(gw).catch(() => { /* best effort */ });
    throw new Error(`${(err as Error).message}\n--- gateway output ---\n${log.join('')}`);
  }
  return gw;
}

/**
 * Stop ONLY the process this spec started.
 *
 * The repo rule against blanket-killing processes applies: no `pkill python`,
 * no port sweep. The kill is tree-scoped on Windows so anything this gateway
 * spawned goes with it.
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
  await waitFor(async () => !(await isUp(gw.base)), SHUTDOWN_TIMEOUT_MS,
    `${gw.base} to stop answering`);
}

// ── event fixtures ─────────────────────────────────────────────────────────

/** A telegram Update carrying an ICDEV command, as the real webhook delivers it. */
function telegramUpdate(userId: string, nonce: number): Record<string, unknown> {
  return {
    update_id: nonce,
    message: {
      message_id: nonce,
      from: { id: Number(userId), username: 'dwo-e2e', first_name: 'DWO' },
      chat: { id: Number(userId), type: 'private' },
      date: Math.floor(nonce / 1000),
      text: COMMAND,
    },
  };
}

/**
 * Deliver an event to the gateway webhook.
 *
 * `signed: false` omits the secret header, which gate 1 must refuse.
 *
 * The response is not awaited for long: dispatch is fire-and-forget at step 6b
 * of the handler, well before the command executes and the adapter tries to
 * post a reply to a bot token that does not exist. A timeout here says nothing
 * about whether the run started — that is what the poll below is for.
 */
async function deliver(
  gw: Gateway,
  body: Record<string, unknown>,
  { signed }: { signed: boolean },
): Promise<number | null> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (signed) headers[SECRET_HEADER] = gw.secret;
  try {
    const resp = await fetch(`${gw.base}${WEBHOOK_PATH}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
    return resp.status;
  } catch {
    return null; // the handler is still running; the poll decides the verdict
  }
}

// ── dashboard API helpers (page.request carries the browser session) ────────

async function studioGet(page: Page, url: string): Promise<{ status: number; body: any }> {
  const resp = await page.request.get(url);
  let body: any = null;
  try { body = await resp.json(); } catch { /* non-JSON — status is enough */ }
  return { status: resp.status(), body };
}

/** Open the run-detail modal for `runId` and wait for its step table. */
async function openRunDetail(page: Page, runId: string): Promise<void> {
  await page.evaluate((id) => (window as any).StudioWF.showRunDetail(id), runId);
  await expect(page.locator('#wf-run-detail-modal')).toBeVisible();
  await expect(page.locator('#wf-run-detail-body table.studio-table')).toBeVisible({
    timeout: 15_000,
  });
}

// ── The test ───────────────────────────────────────────────────────────────

test.describe.serial('DWO — a triggered run links back to the event that started it', () => {
  test.beforeAll(() => {
    fs.mkdirSync(SCREENSHOTS, { recursive: true });
  });

  test.afterAll(async () => {
    if (gateway) {
      await stopGateway(gateway).catch(() => { /* best effort */ });
      gateway = null;
    }
  });

  test('a signed gateway event fires a bound trigger, and the run names its trigger event', async ({
    page,
  }) => {
    // A gateway boot plus a workflow run — past the 60s default.
    test.setTimeout(240_000);

    await page.addInitScript(() => {
      localStorage.setItem('icdev_tour_completed', '1');
      localStorage.setItem('icdev_tour_last_step', '999');
    });

    // Distinct per run so a re-run cannot collide with an earlier binding, and
    // so `update_id` is a fresh delivery id (a replayed one is deduplicated by
    // design — see tests/test_dwo_event_dispatch.py, which owns that case).
    const nonce = Date.now();
    const channelUserId = String(nonce % 1_000_000_000);

    let workflowId = '';
    let sourceId = '';
    let triggerId = '';
    let runId = '';
    let eventId = '';

    await test.step('Workflow Studio renders', async () => {
      await page.goto('/studio/workflows', { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#wf-studio-layout')).toBeVisible({ timeout: 30_000 });
      await expect(page.locator('#wf-canvas')).toBeAttached();
    });

    await test.step('the trigger surface is present', async () => {
      // dwo-evt-02 (`tools/studio/event_sources.py`, the gateway dispatch hook)
      // and dwo-evt-04 (`/api/studio/event-sources`, `/api/studio/triggers`,
      // `run.trigger_event`) deliver everything below. Migration 304 ships the
      // three tables on their own, so a checkout can have the schema and none
      // of the routing — asserting against that would report an unbuilt feature
      // as a regression. Skip precisely instead.
      const probe = await studioGet(page, '/api/studio/event-sources');
      test.skip(
        probe.status !== 200,
        `the Studio trigger surface is not in this checkout: `
        + `GET /api/studio/event-sources answered ${probe.status}. `
        + `It lands with dwo-evt-02 (event_sources + gateway dispatch) and `
        + `dwo-evt-04 (the triggers API and run.trigger_event).`,
      );
    });

    await test.step('start a gateway this spec owns', async () => {
      const port = await freePort();
      gateway = await startGateway(port, `dwo-e2e-${nonce}`);

      // The webhook route only exists for adapters that loaded. Without this,
      // a disabled channel shows up two steps later as a 404 where a 401 was
      // expected, which reads as a broken signature check.
      expect(
        gateway.log.join(''),
        `the ${CHANNEL} adapter did not load, so ${WEBHOOK_PATH} is not registered. `
        + `Check channels.${CHANNEL}.enabled in args/remote_gateway_config.yaml and `
        + `environment.mode (air_gapped disables internet channels).`,
      ).toContain(CHANNEL);
    });

    await test.step('bind the channel user (gate 3 refuses an unbound sender)', async () => {
      const initiate = await fetch(`${gateway!.base}/gateway/bind`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'initiate', channel: CHANNEL, channel_user_id: channelUserId }),
      });
      expect(initiate.status, await initiate.text()).toBe(200);
      const code = (await initiate.json()).challenge_code;
      expect(code, 'the binding ceremony should issue a challenge code').toBeTruthy();

      const verify = await fetch(`${gateway!.base}/gateway/bind`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'verify',
          challenge_code: code,
          icdev_user_id: `dwo-e2e-${nonce}`,
        }),
      });
      expect(verify.status, await verify.text()).toBe(200);
      expect((await verify.json()).success).toBe(true);
    });

    await test.step('create the fixture workflow', async () => {
      const resp = await page.request.post('/api/studio/workflows', { data: FIXTURE_WORKFLOW });
      expect(resp.status(), await resp.text()).toBe(201);
      workflowId = (await resp.json()).workflow_id;
      expect(workflowId).toBeTruthy();
    });

    await test.step('bind a trigger to an event source', async () => {
      // The source NAME is the channel: dispatch resolves triggers by
      // `envelope.channel`, so a source named anything else never matches.
      const src = await page.request.post('/api/studio/event-sources', {
        data: { name: CHANNEL, kind: 'gateway_channel', config: { channel: CHANNEL } },
      });
      expect(src.status(), await src.text()).toBe(201);
      sourceId = (await src.json()).source_id;
      expect(sourceId).toBeTruthy();

      const trg = await page.request.post('/api/studio/triggers', {
        data: {
          source_id: sourceId,
          workflow_id: workflowId,
          event_type: EVENT_TYPE,
          // One condition DSL: these are automation_builder conditions with a
          // dotted field path, not a second filter language.
          conditions: [{ field: 'message.text', operator: 'contains', value: 'icdev-status' }],
          input_mapping: { sender: 'message.from.username' },
          enabled: true,
        },
      });
      expect(trg.status(), await trg.text()).toBe(201);
      triggerId = (await trg.json()).trigger_id;
      expect(triggerId).toBeTruthy();

      const listed = await studioGet(page, `/api/studio/triggers?workflow_id=${workflowId}`);
      expect(listed.status).toBe(200);
      expect(
        (listed.body.triggers || []).map((t: any) => t.trigger_id),
        'the trigger should be listed against the workflow it is bound to',
      ).toContain(triggerId);
    });

    await test.step('an unsigned event is refused — the signature check is live', async () => {
      const status = await deliver(gateway!, telegramUpdate(channelUserId, nonce + 1), {
        signed: false,
      });
      expect(
        status,
        'gate 1 accepted an event with no secret token — either the check is off '
        + '(TELEGRAM_WEBHOOK_SECRET unset in the child) or the adapter changed. '
        + 'Everything below would then prove nothing about signing.',
      ).toBe(401);
    });

    await test.step('a signed event starts a run for the bound workflow', async () => {
      const status = await deliver(gateway!, telegramUpdate(channelUserId, nonce + 2), {
        signed: true,
      });
      // Any non-401 is fine: the handler may still be executing the command or
      // failing to post a reply to a bot that does not exist. It must not have
      // been rejected at the signature gate.
      expect(status === null || status !== 401, `signed delivery was refused (${status})`).toBe(true);

      await waitFor(async () => {
        const { body } = await studioGet(page, `/api/studio/workflows/runs?workflow_id=${workflowId}`);
        const runs = (body && body.runs) || [];
        if (runs.length) { runId = runs[0].run_id; return true; }
        return false;
      }, 90_000,
        `a run of ${workflowId} started by the event. Nothing appeared. Either the `
        + `trigger did not match (check studio_trigger_events for an unmatched row), `
        + `the event was rejected by a later gate (see the gateway log), or the `
        + `gateway and the dashboard are not resolving the same database — the child `
        + `inherits ICDEV_STORAGE_BACKEND / ICDEV_DB_PATH from this process, which `
        + `must match the dashboard under test.`);

      expect(runId).toMatch(/^run-/);
    });

    await test.step('the run records the trigger event that started it', async () => {
      const { status, body } = await studioGet(page, `/api/studio/workflows/runs/${runId}`);
      expect(status, 'run detail should be readable').toBe(200);

      const ev = body.trigger_event;
      expect(
        ev,
        'the run carries no trigger_event — it started, but nothing links it back '
        + 'to the delivery that caused it, which is the whole point of the audit row.',
      ).toBeTruthy();

      eventId = ev.event_id;
      expect(eventId, 'the trigger event needs an id to link to').toBeTruthy();
      expect(ev.trigger_id, 'the event should name the trigger that matched').toBe(triggerId);
      expect(ev.run_id, 'the event should point back at this run').toBe(runId);
    });

    await test.step('the run-detail modal shows the trigger event id', async () => {
      await openRunDetail(page, runId);

      const badge = page.locator('#wf-run-trigger-badge');
      await expect(
        badge,
        'the run-detail modal renders no "Started by" line for a triggered run',
      ).toBeVisible();

      // The DOM assertion the card asks for: the id is readable in the page,
      // not merely present in the JSON the page fetched.
      await expect(badge).toContainText(eventId);
      await expect(badge).toHaveAttribute('data-event-id', eventId);
      await expect(badge).toHaveAttribute('data-trigger-id', triggerId);

      await page.screenshot({ path: SCREENSHOT_LINK, fullPage: true });
      expect(fs.existsSync(SCREENSHOT_LINK)).toBe(true);
    });

    await test.step('a manually started run claims no trigger', async () => {
      // The negative half: the badge is evidence, so it must be absent when
      // there is nothing to evidence. Without this, a badge that renders
      // unconditionally would pass every assertion above.
      const started = await page.request.post(`/api/studio/workflows/${workflowId}/run`, {
        data: { project_id: 'default' },
      });
      expect(started.status(), await started.text()).toBe(202);
      const manualRunId = (await started.json()).run_id;

      const { body } = await studioGet(page, `/api/studio/workflows/runs/${manualRunId}`);
      expect(body.trigger_event ?? null).toBeNull();

      await openRunDetail(page, manualRunId);
      await expect(page.locator('#wf-run-trigger-badge')).toHaveCount(0);

      await page.request.delete(`/api/studio/workflows/runs/${manualRunId}`);
    });

    await test.step('clean up the fixture', async () => {
      // studio_trigger_events is append-only (NIST AU-9) — the audit rows this
      // spec created stay. The trigger, source and workflow do not.
      if (triggerId) await page.request.delete(`/api/studio/triggers/${triggerId}`);
      if (runId) await page.request.delete(`/api/studio/workflows/runs/${runId}`);
      if (workflowId) await page.request.delete(`/api/studio/workflows/${workflowId}`);
    });
  });
});
// CUI // SP-CTI
