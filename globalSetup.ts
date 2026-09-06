// CUI // SP-CTI
/**
 * ICDEV™ E2E environment diagnostics — local vs CI (tsh-e2e-01-d1)
 *
 * WHY THIS EXISTS
 * ---------------
 * The local Playwright suite fails ~55 specs that CI passes, and every attempt
 * to explain the gap so far has been a guess: the backend was blamed and
 * measured innocent (e2p-back-01), sixteen missing feature flags were copied
 * into `webServer.env` and changed nothing (e2p-back-03). What was never
 * available was a side-by-side of what the two environments actually are. This
 * module prints that, every run, before a single test is collected.
 *
 * WHAT IT COMPARES AGAINST
 * ------------------------
 * By default the baseline is parsed straight out of `.github/workflows/icdev-ci.yml`
 * (`jobs.e2e`) — the file CI actually executes, so the baseline cannot drift from
 * CI the way a checked-in copy would. Point `ICDEV_E2E_ENV_BASELINE` at a
 * diagnostics JSON downloaded from a real CI run to diff against that instead:
 *
 *   ICDEV_E2E_ENV_BASELINE=~/Downloads/e2e-env-diagnostics.json npx playwright test --list
 *
 * Every run also writes its own snapshot to
 * `.tmp/test_runs/e2e-env-diagnostics<RUN_TAG>.json`, which the CI job's "Upload
 * Playwright artifacts" step collects — so the CI-produced baseline above is
 * just this same file from the other side.
 *
 * WHY IT IS CALLED FROM THE CONFIG AND NOT ONLY WIRED AS `globalSetup`
 * -------------------------------------------------------------------
 * Playwright does NOT run `globalSetup` for `npx playwright test --list`. Listing
 * is exactly when you want the diagnostics — it is the cheap command you run when
 * a local run disagrees with CI. So `playwright.config.ts` calls
 * `logEnvironmentDiagnostics()` at module load, which covers listing, real runs
 * and `--ui` alike. The default export below stays wired as the documented hook
 * and is a no-op when the config already printed (guarded by an env marker, so
 * the guard holds even if Playwright loads this file into a second module
 * registry).
 *
 * This is diagnostics only: every probe is wrapped, nothing here can fail a run.
 */

import { execFileSync } from 'node:child_process';
import dns from 'node:dns';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import { baseUrlSource as resolveBaseUrlSource, resolveBaseUrl } from './tests/e2e/fixtures/base_url';
import { requestedDatabase, RequestedDatabase } from './tests/e2e/fixtures/e2e_database';

/** Set once diagnostics have been emitted in this process. */
const DONE_MARKER = 'ICDEV_E2E_ENV_DIAG_DONE';

/** Values whose contents must never reach a terminal or an uploaded artifact. */
const SECRET_KEY_RE = /(PASSWORD|SECRET|TOKEN|CREDENTIAL|_KEY$|APIKEY)/i;

/** Plain on/off values — never a secret, whatever the key is called. */
const BOOLEAN_VALUE_RE = /^(true|false|0|1|yes|no|on|off)$/i;

/**
 * The key regex is deliberately broad, which makes it over-match flags like
 * `ICDEV_CREDENTIAL_BROKER_ENABLED=true` — redacting those would hide a real
 * difference to protect the string "true". A boolean is never the secret.
 */
function isSecret(key: string, value: string | undefined): boolean {
  return SECRET_KEY_RE.test(key) && !BOOLEAN_VALUE_RE.test(value ?? '');
}

/**
 * Prefixes worth reporting as "set locally, not set by CI". The whole
 * environment is far too noisy — PATH, HOME and several hundred Windows
 * variables say nothing about why a spec fails.
 */
const LOCAL_ONLY_PREFIXES = ['ICDEV_', 'AIMC_', 'MC_', 'SC_', 'PLAYWRIGHT_', 'PW_'];

/**
 * Files that change how the suite behaves and that CI, which starts from a bare
 * `actions/checkout`, cannot have unless they are tracked in git.
 */
const INFLUENTIAL_PATHS = [
  '.env',
  '.env.local',
  '.env.e2e',
  '.env.test',
  'playwright/.auth',
  'playwright/.cache',
  'storageState.json',
  'data/icdev.db',
  'data/platform.db',
  'node_modules',
];

export interface EnvDiagnosticsOptions {
  /** Repo root. Defaults to the directory holding this file. */
  root?: string;
  /** The env `playwright.config.ts` hands to a Playwright-managed dashboard. */
  webServerEnv?: Record<string, string>;
  /** True when Playwright will start the dashboard itself (ICDEV_NO_SERVER unset). */
  webServerActive?: boolean;
  /** Resolved base URL under test. */
  dashboardUrl?: string;
}

interface CiBaseline {
  source: string;
  env: Record<string, string>;
  nodeVersion?: string;
  pythonVersion?: string;
  runsOn?: string;
  playwrightVersion?: string;
}

// ── baseline: the CI workflow ────────────────────────────────────────────────

/**
 * Pull `jobs.e2e` out of the CI workflow without a YAML dependency.
 *
 * The block is a flat `KEY: value` map at a known indent, so a scanner is enough
 * and keeps this file dependency-free (the repo installs no YAML package for
 * Node). Anything unrecognised yields `null` and the caller reports the baseline
 * as unavailable rather than inventing one.
 */
function parseCiE2eJob(root: string): CiBaseline | null {
  const workflow = path.join(root, '.github', 'workflows', 'icdev-ci.yml');
  let lines: string[];
  try {
    lines = fs.readFileSync(workflow, 'utf8').split(/\r?\n/);
  } catch {
    return null;
  }

  const start = lines.findIndex((l) => /^ {2}e2e:\s*$/.test(l));
  if (start === -1) return null;

  // The job ends at the next key at the same indent (or EOF).
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^ {2}\S/.test(lines[i])) {
      end = i;
      break;
    }
  }
  const job = lines.slice(start + 1, end);

  const env: Record<string, string> = {};
  const envStart = job.findIndex((l) => /^ {4}env:\s*$/.test(l));
  if (envStart !== -1) {
    for (let i = envStart + 1; i < job.length; i++) {
      const line = job[i];
      if (line.trim() === '' || /^ {6}#/.test(line)) continue;
      const m = /^ {6}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/.exec(line);
      if (!m) break; // dedent — end of the env block
      env[m[1]] = stripYamlScalar(m[2]);
    }
  }

  const lookahead = (needle: string, re: RegExp): string | undefined => {
    const at = job.findIndex((l) => l.includes(needle));
    if (at === -1) return undefined;
    for (let i = at; i < Math.min(at + 6, job.length); i++) {
      const m = re.exec(job[i]);
      if (m) return m[1];
    }
    return undefined;
  };

  const runsOnMatch = job.find((l) => /^ {4}runs-on:/.test(l));

  return {
    source: '.github/workflows/icdev-ci.yml → jobs.e2e',
    env,
    nodeVersion: lookahead('actions/setup-node@', /node-version:\s*["']?([^"'\s]+)/),
    pythonVersion: lookahead('actions/setup-python@', /python-version:\s*["']?([^"'\s]+)/),
    runsOn: runsOnMatch ? stripYamlScalar(runsOnMatch.split(':').slice(1).join(':').trim()) : undefined,
    playwrightVersion: lockfilePlaywrightVersion(root),
  };
}

function stripYamlScalar(raw: string): string {
  const v = raw.trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1);
  }
  return v;
}

/** What `npm ci` will install in CI — the lockfile is the contract. */
function lockfilePlaywrightVersion(root: string): string | undefined {
  try {
    const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
    return lock?.packages?.['node_modules/@playwright/test']?.version;
  } catch {
    return undefined;
  }
}

/** An `ICDEV_E2E_ENV_BASELINE` override: a diagnostics JSON from a real CI run. */
function loadBaselineArtifact(): CiBaseline | null {
  const file = process.env.ICDEV_E2E_ENV_BASELINE;
  if (!file) return null;
  try {
    const snap = JSON.parse(fs.readFileSync(file, 'utf8'));
    const env: Record<string, string> = {};
    for (const [k, v] of Object.entries(snap?.env ?? {})) {
      if (typeof v === 'string') env[k] = v;
    }
    return {
      source: `CI artifact ${file}`,
      env,
      nodeVersion: snap?.runtime?.node,
      pythonVersion: snap?.runtime?.python,
      runsOn: snap?.runtime?.platform,
      playwrightVersion: snap?.runtime?.playwright,
    };
  } catch (err) {
    console.log(`  ! baseline artifact unreadable (${(err as Error).message}) — falling back to the workflow`);
    return null;
  }
}

// ── local probes ─────────────────────────────────────────────────────────────

function localPlaywrightVersion(root: string): string {
  try {
    const pkg = JSON.parse(
      fs.readFileSync(path.join(root, 'node_modules', '@playwright', 'test', 'package.json'), 'utf8'),
    );
    return pkg.version ?? 'unknown';
  } catch {
    return 'not installed';
  }
}

function localPythonVersion(): string {
  for (const exe of ['python', 'python3']) {
    try {
      const out = execFileSync(exe, ['--version'], { timeout: 5000, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
      const m = /(\d+\.\d+\.\d+)/.exec(out);
      if (m) return m[1];
    } catch {
      /* try the next interpreter */
    }
  }
  return 'not found';
}

function git(root: string, args: string[]): string[] {
  try {
    const out = execFileSync('git', ['-C', root, ...args], {
      timeout: 10000,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      maxBuffer: 8 * 1024 * 1024,
    });
    return out.split(/\r?\n/).filter(Boolean);
  } catch {
    return [];
  }
}

interface PathFinding {
  path: string;
  state: 'local-only' | 'tracked' | 'absent';
  note: string;
}

/**
 * Classify the files that decide whether a local run and a CI run are the same
 * run. Tracked-in-git is the discriminator that matters: CI checks the repo out
 * clean, so a file that exists here and is not tracked does not exist there.
 */
function inspectPaths(root: string): PathFinding[] {
  // `git ls-files <dir>` expands to the files under it, so a directory counts as
  // tracked when anything beneath it is.
  const tracked = new Set(git(root, ['ls-files', '--', ...INFLUENTIAL_PATHS]));

  return INFLUENTIAL_PATHS.map((rel) => {
    const exists = fs.existsSync(path.join(root, rel));
    const isTracked = tracked.has(rel) || [...tracked].some((t) => t.startsWith(`${rel}/`));
    if (!exists) {
      return {
        path: rel,
        state: 'absent' as const,
        note: isTracked ? 'tracked but missing here — CI will have it' : 'absent in both',
      };
    }
    if (isTracked) return { path: rel, state: 'tracked' as const, note: 'tracked — CI has it too' };
    return {
      path: rel,
      state: 'local-only' as const,
      note: rel === 'node_modules' ? 'CI recreates this via `npm ci`' : 'NOT in CI — CI checks out clean',
    };
  });
}

/**
 * Untracked, non-ignored files under the test directories. These change which
 * tests exist, so they make `--list` itself differ from CI's.
 */
function untrackedTestFiles(root: string): string[] {
  return git(root, ['ls-files', '--others', '--exclude-standard', '--', 'tests/e2e', 'playwright']);
}

// ── env diff ─────────────────────────────────────────────────────────────────

type DiffState = 'match' | 'differs' | 'missing-locally' | 'local-only' | 'redacted';

/** What a redacted value looks like once it has been through `display()`. */
const REDACTED = '<redacted>';

/** Diagnostics' own knobs — reporting them as differences is just noise. */
const SELF_KEYS = new Set([DONE_MARKER, 'ICDEV_E2E_ENV_BASELINE', 'ICDEV_E2E_ENV_DIAG']);

interface EnvDiffRow {
  key: string;
  state: DiffState;
  ci?: string;
  local?: string;
  source: string;
}

function display(key: string, value: string | undefined): string {
  if (value === undefined) return '<unset>';
  if (isSecret(key, value)) return '<redacted>';
  return value === '' ? '<empty>' : value;
}

function diffEnv(baseline: Record<string, string>, opts: EnvDiagnosticsOptions): EnvDiffRow[] {
  const webServerEnv = opts.webServerActive ? opts.webServerEnv ?? {} : {};

  /**
   * The effective local value. A variable the config injects into
   * `webServer.env` is genuinely in force for the dashboard under test even
   * though it is absent from `process.env` — reporting those as "missing" would
   * bury the handful of real differences under a wall of false ones.
   */
  const effective = (key: string): { value?: string; source: string } => {
    if (process.env[key] !== undefined) return { value: process.env[key], source: 'process.env' };
    if (webServerEnv[key] !== undefined) return { value: webServerEnv[key], source: 'webServer.env' };
    return { value: undefined, source: '—' };
  };

  const rows: EnvDiffRow[] = [];

  for (const [key, ciValue] of Object.entries(baseline)) {
    if (SELF_KEYS.has(key)) continue;
    const { value, source } = effective(key);
    let state: DiffState;
    if (value === undefined) state = 'missing-locally';
    else if (value === ciValue) state = 'match';
    // A snapshot baseline stores secrets as `<redacted>`, so a secret can never
    // compare equal to one. Calling that a difference would put two permanent
    // false positives at the top of every artifact-baselined run.
    else if (ciValue === REDACTED && isSecret(key, value)) state = 'redacted';
    else state = 'differs';
    rows.push({ key, state, ci: ciValue, local: value, source });
  }

  const seen = new Set(Object.keys(baseline));
  for (const key of [...Object.keys(process.env), ...Object.keys(webServerEnv)]) {
    if (seen.has(key) || SELF_KEYS.has(key)) continue;
    if (!LOCAL_ONLY_PREFIXES.some((p) => key.startsWith(p))) continue;
    seen.add(key);
    const { value, source } = effective(key);
    rows.push({ key, state: 'local-only', local: value, source });
  }

  const order: Record<DiffState, number> = {
    differs: 0,
    'missing-locally': 1,
    'local-only': 2,
    redacted: 3,
    match: 4,
  };
  rows.sort((a, b) => order[a.state] - order[b.state] || a.key.localeCompare(b.key));
  return rows;
}

// ── report ───────────────────────────────────────────────────────────────────

const RULE = '─'.repeat(78);

/** Pack names into comma-separated lines no wider than `width`. */
function wrap(items: string[], width: number): string[] {
  const lines: string[] = [];
  let current = '';
  for (const item of items) {
    const piece = current ? `${current}, ${item}` : item;
    if (piece.length > width && current) {
      lines.push(`${current},`);
      current = item;
    } else {
      current = piece;
    }
  }
  if (current) lines.push(current);
  return lines;
}

/**
 * Print the local-vs-CI report and write the machine-readable snapshot.
 *
 * Safe to call more than once: the second call is a no-op. Set
 * `ICDEV_E2E_ENV_DIAG=0` to silence the report (the snapshot is still written).
 */
export function logEnvironmentDiagnostics(opts: EnvDiagnosticsOptions = {}): void {
  if (process.env[DONE_MARKER] === '1') return;
  process.env[DONE_MARKER] = '1';

  // Workers re-load the config; the report belongs to the run, not to each worker.
  if (process.env.TEST_WORKER_INDEX !== undefined) return;

  const quiet = process.env.ICDEV_E2E_ENV_DIAG === '0';
  const say = (line = '') => {
    if (!quiet) console.log(line);
  };

  try {
    const root = opts.root ?? __dirname;
    const inCi = process.env.CI === 'true' || process.env.CI === '1' || !!process.env.GITHUB_ACTIONS;
    const baseline = loadBaselineArtifact() ?? parseCiE2eJob(root);

    const runtime = {
      node: process.version.replace(/^v/, ''),
      python: localPythonVersion(),
      playwright: localPlaywrightVersion(root),
      platform: `${process.platform} ${os.release()}`,
      cwd: process.cwd(),
      root,
      dashboardUrl: opts.dashboardUrl ?? '',
      webServer: opts.webServerActive ? 'playwright-managed' : 'external (ICDEV_NO_SERVER)',
    };

    say();
    say(`╔${'═'.repeat(78)}╗`);
    say(`  ICDEV™ E2E ENVIRONMENT DIAGNOSTICS — ${inCi ? 'RUNNING IN CI' : 'RUNNING LOCALLY'}`);
    say(`╚${'═'.repeat(78)}╝`);
    say(`  baseline     : ${baseline ? baseline.source : 'UNAVAILABLE — could not parse jobs.e2e'}`);
    say(`  repo root    : ${runtime.root}`);
    say(`  cwd          : ${runtime.cwd}`);
    say(`  base URL     : ${runtime.dashboardUrl || '<unset>'}   (server: ${runtime.webServer})`);
    say();

    // ── runtime versions ────────────────────────────────────────────────────
    say('  RUNTIME                    this run                    CI');
    say(`  ${RULE}`);
    const versionRow = (label: string, local: string, ci?: string) => {
      const flag = ci && !local.startsWith(ci) && ci !== local ? ' ⚠' : '';
      say(`  ${label.padEnd(12)} ${local.padEnd(26)} ${(ci ?? 'unknown').padEnd(24)}${flag}`);
    };
    versionRow('node', runtime.node, baseline?.nodeVersion);
    versionRow('python', runtime.python, baseline?.pythonVersion);
    versionRow('playwright', runtime.playwright, baseline?.playwrightVersion);
    versionRow('platform', runtime.platform, baseline?.runsOn);
    say();

    // ── env diff ────────────────────────────────────────────────────────────
    const rows = baseline ? diffEnv(baseline.env, opts) : [];
    const counts = rows.reduce<Record<string, number>>((acc, r) => {
      acc[r.state] = (acc[r.state] ?? 0) + 1;
      return acc;
    }, {});

    say(
      `  ENVIRONMENT vs CI — ${counts.differs ?? 0} differ, ${counts['missing-locally'] ?? 0} missing locally, ` +
        `${counts['local-only'] ?? 0} local-only, ${counts.match ?? 0} match` +
        (counts.redacted ? `, ${counts.redacted} redacted` : ''),
    );
    say(`  ${RULE}`);
    if (!baseline) {
      say('  (no baseline — nothing to compare against)');
    } else if (rows.every((r) => r.state === 'match')) {
      say('  ✓ identical to CI');
    } else {
      // Only the variables CI also cares about get a line each. A developer
      // environment carries ~80 ICDEV_* flags CI never sets, and printing those
      // one per line buries the handful of differences that explain a failure —
      // so they are listed by name and their values kept in the snapshot.
      for (const r of rows) {
        if (r.state !== 'differs' && r.state !== 'missing-locally') continue;
        const marker = r.state === 'differs' ? '≠' : '−';
        say(
          `  ${marker} ${r.key.padEnd(32)} local=${display(r.key, r.local)}` +
            `  ci=${display(r.key, r.ci)}   [${r.source}]`,
        );
      }
      const localOnlyKeys = rows.filter((r) => r.state === 'local-only').map((r) => r.key);
      if (localOnlyKeys.length) {
        say(`  + set here, not by CI (${localOnlyKeys.length}, values in the snapshot):`);
        for (const line of wrap(localOnlyKeys, 72)) say(`      ${line}`);
      }
      const redactedKeys = rows.filter((r) => r.state === 'redacted').map((r) => r.key);
      if (redactedKeys.length) {
        say(`  ~ redacted in the baseline, so not compared: ${redactedKeys.join(', ')}`);
      }
      if ((counts.match ?? 0) > 0) say(`  = ${counts.match} variable(s) match CI (not listed)`);
    }
    say();

    // ── files ───────────────────────────────────────────────────────────────
    const findings = inspectPaths(root);
    const untracked = untrackedTestFiles(root);
    const localOnly = findings.filter((f) => f.state === 'local-only');
    const missingHere = findings.filter((f) => f.state === 'absent' && f.note.startsWith('tracked'));

    say(`  FILES — ${localOnly.length} present here but not in CI, ${untracked.length} untracked test file(s)`);
    say(`  ${RULE}`);
    for (const f of findings) {
      const marker = f.state === 'local-only' ? '+' : f.state === 'tracked' ? '=' : '·';
      say(`  ${marker} ${f.path.padEnd(24)} ${f.state.padEnd(12)} ${f.note}`);
    }
    for (const f of untracked.slice(0, 10)) {
      say(`  + ${f.padEnd(24)} untracked    NOT in CI — changes the collected test list`);
    }
    if (untracked.length > 10) say(`  + ... and ${untracked.length - 10} more untracked file(s) under tests/e2e, playwright`);
    if (missingHere.length) {
      say(`  ! ${missingHere.length} tracked path(s) missing from this checkout — CI would have them`);
    }
    say();

    // ── snapshot ────────────────────────────────────────────────────────────
    const snapshot = {
      generatedBy: 'globalSetup.ts (tsh-e2e-01-d1)',
      context: inCi ? 'ci' : 'local',
      baseline: baseline?.source ?? null,
      runtime,
      // Redacted on the way out: this file is uploaded as a CI artifact.
      env: Object.fromEntries(
        rows.filter((r) => r.local !== undefined).map((r) => [r.key, display(r.key, r.local)]),
      ),
      diff: rows.map((r) => ({
        key: r.key,
        state: r.state,
        source: r.source,
        local: display(r.key, r.local),
        ci: r.state === 'local-only' ? null : display(r.key, r.ci),
      })),
      files: findings,
      untrackedTestFiles: untracked,
    };

    const runTag = process.env.ICDEV_PW_RUN_TAG ? `-${process.env.ICDEV_PW_RUN_TAG}` : '';
    const outFile = path.resolve(root, '.tmp', 'test_runs', `e2e-env-diagnostics${runTag}.json`);
    try {
      fs.mkdirSync(path.dirname(outFile), { recursive: true });
      fs.writeFileSync(outFile, `${JSON.stringify(snapshot, null, 2)}\n`, 'utf8');
      say(`  snapshot     : ${outFile}`);
      say('  compare      : ICDEV_E2E_ENV_BASELINE=<ci-snapshot.json> npx playwright test --list');
    } catch (err) {
      say(`  snapshot     : not written (${(err as Error).message})`);
    }
    say();
  } catch (err) {
    // Diagnostics must never be the reason a suite does not run.
    console.log(`  ! E2E environment diagnostics failed: ${(err as Error).message}`);
  }
}

// ── baseURL reachability — fail fast, once (qa-fail-e2e-baseurl-01) ──────────
//
// WHY THIS EXISTS
// ---------------
// `.env` carried `ICDEV_DASHBOARD_URL=http://host.docker.internal:5050` — a
// value that is CORRECT for an agent inside a container reaching the host, and
// wrong for a test runner ON the host. `playwright.config.ts` read it as
// `baseURL`, so every `page.goto` spent the full 30s `navigationTimeout` and
// died with net::ERR_CONNECTION_TIMED_OUT. MEASURED on this box, same three
// spec files, nothing else changed:
//
//   baseURL host.docker.internal:5050   43 of 45 FAILED
//   baseURL localhost:5050              14 passed, 1 failed
//
// So the suite reported a wall of product-looking failures that were one
// hostname, and burned a 30s timeout per navigation doing it. A suite that
// cannot reach the app under test must say so ONCE, not 838 times.
//
// WHY IT IS ONLY CALLED FROM THE `globalSetup` HOOK
// ------------------------------------------------
// `webServer` is a Playwright PLUGIN, and plugin setup runs BEFORE globalSetup
// (node_modules/playwright/lib/runner/tasks.js::createGlobalSetupTasks). So by
// the time this runs, a Playwright-managed dashboard is already up — probing
// here cannot produce a false "unreachable" for a healthy run. Calling it from
// config load, the way `logEnvironmentDiagnostics` is called, would probe
// before the server had started and fail every correct local run. Do NOT move
// it. (CI sets ICDEV_NO_SERVER=1 and starts the dashboard itself, so there this
// hook is the ONLY reachability guard there is.)
//
// Turn it off with ICDEV_E2E_REACHABILITY_CHECK=0 — which SAYS SO on stdout,
// because a guard that disables itself quietly is the `|| true` defect again.

/** Hostnames that only resolve meaningfully from INSIDE a container. */
const CONTAINER_GATEWAY_HOSTS = new Set([
  'host.docker.internal',
  'gateway.docker.internal',
  'kubernetes.docker.internal',
  'host.containers.internal',
  'host.lima.internal',
]);

type ReachabilityVerdict = 'reachable' | 'dns_failure' | 'refused' | 'unreachable' | 'bad_url';

export interface ReachabilityResult {
  verdict: ReachabilityVerdict;
  url: string;
  /** Addresses the hostname resolved to, or [] when it did not resolve. */
  addresses: string[];
  /** HTTP status when one was received — ANY status means the app answered. */
  status?: number;
  /** Human-readable cause, always populated. */
  detail: string;
  /** Milliseconds the probe spent. */
  elapsedMs: number;
}

/**
 * Classify a socket-level failure. The three buckets go to different fixes and
 * are never merged: nothing resolved (`dns_failure`), something resolved and
 * actively said no (`refused` — a server that is not running), and something
 * resolved and swallowed the packet (`unreachable` — the wrong host, or a
 * firewall). `host.docker.internal` is the THIRD case, not the first: on this
 * box it resolves fine, to the LAN address Docker Desktop writes into hosts,
 * and the dashboard binds 127.0.0.1 only — so the SYN is dropped and the
 * connection times out. "Does not resolve" would have been the wrong diagnosis.
 */
function classifySocketError(err: NodeJS.ErrnoException): ReachabilityVerdict {
  switch (err.code) {
    case 'ENOTFOUND':
    case 'EAI_AGAIN':
      return 'dns_failure';
    case 'ECONNREFUSED':
      return 'refused';
    default:
      return 'unreachable';
  }
}

async function resolveAddresses(hostname: string): Promise<string[]> {
  try {
    const found = await dns.promises.lookup(hostname, { all: true });
    return found.map((a) => a.address);
  } catch {
    return [];
  }
}

/**
 * One HTTP GET. ANY response — 200, 403, 404, 500 — proves the app under test
 * is answering on this URL, which is the only question being asked here.
 * Certificate validity deliberately is not: a self-signed cert would otherwise
 * be reported as "unreachable", which sends the reader to the wrong fix.
 */
function probeOnce(url: URL, timeoutMs: number): Promise<{ status: number } | NodeJS.ErrnoException> {
  return new Promise((resolve) => {
    const transport = url.protocol === 'https:' ? https : http;
    const req = transport.request(
      url,
      { method: 'GET', timeout: timeoutMs, ...(url.protocol === 'https:' ? { rejectUnauthorized: false } : {}) },
      (res) => {
        const status = res.statusCode ?? 0;
        res.resume(); // drain, we only wanted the status line
        resolve({ status });
      },
    );
    req.on('timeout', () => {
      const err: NodeJS.ErrnoException = new Error(`no response within ${timeoutMs}ms`);
      err.code = 'ETIMEDOUT';
      req.destroy(err);
    });
    req.on('error', (err) => resolve(err as NodeJS.ErrnoException));
    req.end();
  });
}

/**
 * Probe `url` and report whether the app under test is answering there.
 * Never throws — the caller decides what an unreachable base URL means.
 */
export async function probeBaseUrl(
  rawUrl: string,
  opts: { timeoutMs?: number; attempts?: number } = {},
): Promise<ReachabilityResult> {
  const timeoutMs = opts.timeoutMs ?? Number(process.env.ICDEV_E2E_REACHABILITY_TIMEOUT_MS ?? 5000);
  const attempts = Math.max(1, opts.attempts ?? 2);
  const started = Date.now();

  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return {
      verdict: 'bad_url',
      url: rawUrl,
      addresses: [],
      detail: 'not a parseable URL',
      elapsedMs: Date.now() - started,
    };
  }

  const addresses = await resolveAddresses(url.hostname);

  let last: NodeJS.ErrnoException | undefined;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    const outcome = await probeOnce(url, timeoutMs);
    if ('status' in outcome) {
      return {
        verdict: 'reachable',
        url: rawUrl,
        addresses,
        status: outcome.status,
        detail: `HTTP ${outcome.status}`,
        elapsedMs: Date.now() - started,
      };
    }
    last = outcome;
  }

  const err = last ?? Object.assign(new Error('no attempt completed'), { code: 'UNKNOWN' });
  return {
    verdict: classifySocketError(err),
    url: rawUrl,
    addresses,
    detail: `${err.code ?? 'ERROR'}: ${err.message}`,
    elapsedMs: Date.now() - started,
  };
}

/** The one error message a broken base URL is allowed to produce. */
function unreachableMessage(result: ReachabilityResult, source: string): string {
  const host = (() => {
    try {
      return new URL(result.url).hostname;
    } catch {
      return result.url;
    }
  })();

  const lines = [
    '',
    `E2E ABORTED — the app under test is not answering at ${result.url}`,
    '',
    `  baseURL      : ${result.url}   (from ${source})`,
    `  verdict      : ${result.verdict}`,
    `  detail       : ${result.detail}`,
    `  resolves to  : ${result.addresses.length ? result.addresses.join(', ') : '<did not resolve>'}`,
    `  probe took   : ${result.elapsedMs}ms`,
    '',
  ];

  if (CONTAINER_GATEWAY_HOSTS.has(host)) {
    lines.push(
      `  "${host}" is a CONTAINER-TO-HOST gateway name. It is how a process`,
      '  INSIDE a container reaches the host — it is not how a test runner ON the',
      '  host reaches the dashboard. This exact value in ICDEV_DASHBOARD_URL is what',
      '  turned 43 of 45 specs into ERR_CONNECTION_TIMED_OUT (qa-fail-e2e-baseurl-01).',
      '',
      '  Fix: set ICDEV_DASHBOARD_URL=http://localhost:5050 in .env, or leave it unset',
      '  (the config derives http://localhost:$ICDEV_DASHBOARD_PORT). To keep the',
      '  container value for agents and still run the suite, set the dedicated var:',
      '',
      '      ICDEV_E2E_BASE_URL=http://localhost:5050 npx playwright test',
      '',
    );
  } else if (result.verdict === 'refused') {
    lines.push(
      '  Nothing is listening there. Start the dashboard, or let Playwright start it',
      '  (unset ICDEV_NO_SERVER).',
      '',
    );
  } else if (result.verdict === 'dns_failure') {
    lines.push(`  "${host}" does not resolve on this machine.`, '');
  } else {
    lines.push(
      `  "${host}" resolves but does not accept connections on that port. Check the`,
      '  dashboard is bound to an address this host can reach (ICDEV_DASHBOARD_HOST)',
      '  and that a firewall is not dropping the connection.',
      '',
    );
  }

  lines.push(
    '  Every spec would otherwise fail here, one full navigationTimeout at a time.',
    '  Skip this check with ICDEV_E2E_REACHABILITY_CHECK=0.',
    '',
  );
  return lines.join('\n');
}

/** Where the in-force baseURL came from, for the error message. */
function baseUrlSource(): string {
  return resolveBaseUrlSource();
}

/**
 * THROWS when the base URL is not answering, so the run reports one cause
 * instead of N navigation timeouts.
 *
 * Deliberately NOT wrapped the way `logEnvironmentDiagnostics` is: diagnostics
 * must never fail a run, and this must be able to.
 */
export async function assertBaseUrlReachable(baseUrl: string | undefined): Promise<void> {
  if (process.env.ICDEV_E2E_REACHABILITY_CHECK === '0' || process.env.ICDEV_E2E_REACHABILITY_CHECK === 'off') {
    console.log('  ! E2E baseURL reachability check DISABLED (ICDEV_E2E_REACHABILITY_CHECK=0)');
    return;
  }
  if (!baseUrl) {
    console.log('  ! E2E baseURL is unset — reachability not checked');
    return;
  }

  const result = await probeBaseUrl(baseUrl);
  if (result.verdict === 'reachable') {
    console.log(
      `  ✓ baseURL reachable: ${result.url} → ${result.detail} in ${result.elapsedMs}ms` +
        (result.addresses.length ? ` (${result.addresses.join(', ')})` : ''),
    );
    return;
  }
  throw new Error(unreachableMessage(result, baseUrlSource()));
}

// ── database isolation — the run must be ON the database it asked for ───────
//
// THE DEFECT (qa-fail-6a87916931be3793). `playwright.config.ts` documented a
// throwaway-database recipe and stated its reason plainly: the suite writes
// fixtures, and running it against the canonical `icdev` leaves them there.
// The documented command redirected NOTHING — `.env` supplies
// `ICDEV_DATABASE_URL` and every connection site in `tools/db/storage.py`
// reads the DSN before the discrete `ICDEV_PG_DATABASE`. So the operator held
// a false belief in isolation and ~840 tests' worth of fixture writes went to
// the canonical board. `webServerDatabaseEnv()` fixes the plumbing; this
// asserts the plumbing WORKED, which is a different claim and the one that
// was missing.
//
// IT MEASURES THE SERVER, NOT THE ENVIRONMENT. `/api/health` reports
// `current_database()` off the live connection. Re-reading `ICDEV_PG_DATABASE`
// back out of our own environment would prove only that we can echo a
// variable — precisely the reasoning that shipped the broken recipe. It is
// also the only way to catch the `reuseExistingServer` hole: when a dashboard
// is already up on the port, Playwright starts no server, `webServer.env` is
// never applied, and every variable this run exported is inert.
//
// FOUR VERDICTS, AND `confirmed` IS NOT A SYNONYM FOR `isolated`:
//   confirmed      requested, and the server is measurably on that database
//   mismatch       requested, and the server is somewhere else      -> THROWS
//   unmeasured     requested, and we could not confirm it           -> THROWS
//   not_requested  nothing was asked for. NOT a clean bill of health.
//
// The success verdict is deliberately NOT called `isolated`. What this can
// prove is that the server is on the database the run named; whether that
// database is disposable is not knowable from here, and on this deployment an
// ordinary shell already exports `ICDEV_DATABASE_URL` naming the CANONICAL
// board. Printing a green "isolated" over that is the same false comfort the
// broken recipe gave. So the line always NAMES the database, and a run whose
// database came from the ambient configuration rather than a per-run knob is
// told so explicitly.
//
// `unmeasured` throws ON PURPOSE when a database was requested. Degrading it
// to a warning restores the exact false belief this exists to remove: "I asked
// for isolation and nothing complained". When nothing was requested no claim
// was made, so it only warns — which keeps a plain local run working, and CI,
// whose database really is named `icdev` in a disposable container.
//
// Stand it down with ICDEV_E2E_DB_CHECK=0 — auditable, unlike a neutraliser.

export type DatabaseIsolationVerdict = 'confirmed' | 'mismatch' | 'unmeasured' | 'not_requested';

export interface DatabaseIsolationResult {
  verdict: DatabaseIsolationVerdict;
  requested: string | null;
  requestedSource: string | null;
  measured: string | null;
  backend: string | null;
  detail: string;
}

export interface DatabaseProbe {
  measured: string | null;
  backend: string | null;
  error: string | null;
}

/** Ask the SERVER which database it is on. Never throws — the caller decides. */
export async function probeServerDatabase(baseUrl: string, timeoutMs = 15000): Promise<DatabaseProbe> {
  try {
    const res = await fetch(new URL('/api/health', baseUrl).toString(), {
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) {
      return { measured: null, backend: null, error: `/api/health returned HTTP ${res.status}` };
    }
    const body = (await res.json()) as {
      database?: unknown;
      backend?: unknown;
      database_measured?: unknown;
    };
    if (body.database_measured !== true || typeof body.database !== 'string' || !body.database) {
      return {
        measured: null,
        backend: typeof body.backend === 'string' ? body.backend : null,
        // A server that predates this field reports nothing rather than
        // agreeing — an absent measurement must never read as a matching one.
        error: '/api/health did not report a measured database',
      };
    }
    return {
      measured: body.database,
      backend: typeof body.backend === 'string' ? body.backend : null,
      error: null,
    };
  } catch (err) {
    return { measured: null, backend: null, error: (err as Error).message };
  }
}

/** Compute the verdict without printing or throwing — the testable core. */
export function classifyDatabaseIsolation(
  requested: RequestedDatabase,
  probe: DatabaseProbe,
): DatabaseIsolationResult {
  const base = {
    requested: requested.name,
    requestedSource: requested.source,
    measured: probe.measured,
    backend: probe.backend,
  };
  if (!requested.name) {
    return {
      ...base,
      verdict: 'not_requested',
      detail: probe.measured
        ? `no database was requested — this run writes its fixtures into '${probe.measured}'`
        : 'no database was requested, and the server did not report one',
    };
  }
  if (!probe.measured) {
    return {
      ...base,
      verdict: 'unmeasured',
      detail: probe.error ?? 'the server did not report a measured database',
    };
  }
  if (probe.measured === requested.name) {
    return { ...base, verdict: 'confirmed', detail: `server is on '${probe.measured}'` };
  }
  return {
    ...base,
    verdict: 'mismatch',
    detail: `${requested.source}=${requested.name} but the server is on '${probe.measured}'`,
  };
}

function isolationFailure(result: DatabaseIsolationResult): string {
  const lines = [
    '',
    'E2E DATABASE ISOLATION FAILED — refusing to run.',
    '',
    `  requested : ${result.requested} (via ${result.requestedSource})`,
    `  measured  : ${result.measured ?? '<not measured>'}${result.backend ? ` (${result.backend})` : ''}`,
    `  verdict   : ${result.verdict}`,
    `  detail    : ${result.detail}`,
    '',
  ];
  if (result.verdict === 'mismatch') {
    lines.push(
      'The suite writes fixtures. Running it here would leave them in a database',
      'you did not ask for. Two things cause this:',
      '',
      '  1. A dashboard was ALREADY running on this port, so Playwright started',
      '     no server and none of this run’s environment reached it. Give the',
      '     run its own server:  ICDEV_DASHBOARD_PORT=5090',
      '  2. The database name was outranked. `.env` sets ICDEV_DATABASE_URL and',
      '     tools/db/storage.py reads the DSN before ICDEV_PG_DATABASE, so pass',
      '     the DSN instead:  ICDEV_DATABASE_URL=postgresql://.../<db>',
      '',
    );
  } else {
    lines.push(
      'A database was requested and the isolation could NOT be confirmed, which',
      'is not the same as confirming it. Check that the dashboard is reachable',
      'and that /api/health reports `database_measured`.',
      '',
    );
  }
  lines.push('Stand this check down deliberately with ICDEV_E2E_DB_CHECK=0.', '');
  return lines.join('\n');
}

/**
 * THROWS when this run asked for a database and the server is not on it.
 *
 * Like `assertBaseUrlReachable`, and unlike the diagnostics, this is allowed to
 * fail the run — a run that silently writes into the canonical board is worse
 * than a run that does not start.
 */
export async function assertDatabaseIsolated(
  baseUrl: string | undefined,
): Promise<DatabaseIsolationResult | null> {
  if (process.env.ICDEV_E2E_DB_CHECK === '0' || process.env.ICDEV_E2E_DB_CHECK === 'off') {
    console.log('  ! E2E database isolation check DISABLED (ICDEV_E2E_DB_CHECK=0)');
    return null;
  }
  if (!baseUrl) {
    console.log('  ! E2E baseURL is unset — database isolation not checked');
    return null;
  }

  const requested = requestedDatabase();
  const probe = await probeServerDatabase(baseUrl);
  const result = classifyDatabaseIsolation(requested, probe);

  if (result.verdict === 'confirmed') {
    console.log(`  ✓ E2E database confirmed: ${result.detail} (via ${result.requestedSource})`);
    if (!requested.explicit) {
      // The database came from the deployment's own configuration, not from a
      // per-run knob — so nothing about this run is isolated, and the fixtures
      // land here. Said plainly on every such run, because the alternative is a
      // tick beside the canonical board's name.
      console.log(
        `    ! Nothing requested a throwaway database — the suite writes its fixtures into '${result.measured}'.`,
      );
      console.log('      Point this run elsewhere with  ICDEV_PG_DATABASE=icdev_e2e  (see playwright.config.ts).');
    }
    return result;
  }
  if (result.verdict === 'not_requested') {
    // Loud, and it NAMES the database, because the common local run is the
    // canonical board and the operator should see that before 840 tests write
    // to it. Not a failure: they may have meant it.
    console.log(`  ! E2E database NOT isolated — ${result.detail}`);
    console.log('    Request one with  ICDEV_PG_DATABASE=icdev_e2e  (see playwright.config.ts).');
    return result;
  }
  throw new Error(isolationFailure(result));
}

/** Read the baseURL Playwright actually resolved, not a second copy of it. */
function baseUrlFromConfig(config?: unknown): string | undefined {
  const projects = (config as { projects?: Array<{ use?: { baseURL?: string } }> } | undefined)?.projects;
  for (const project of projects ?? []) {
    if (project?.use?.baseURL) return project.use.baseURL;
  }
  const fromConfigUse = (config as { use?: { baseURL?: string } } | undefined)?.use?.baseURL;
  if (fromConfigUse) return fromConfigUse;
  return resolveBaseUrl();
}

/**
 * Playwright's documented hook. `playwright.config.ts` already emits the report
 * at load time — which is what makes `--list` print it, since Playwright skips
 * `globalSetup` when listing — so the diagnostics call is normally a no-op and
 * exists so they still run if that call is ever removed.
 *
 * The reachability assert, by contrast, runs ONLY here and only for a real run:
 * `--list` collects no tests and navigates nowhere, so refusing to list because
 * a dashboard is down would be a gate on the wrong thing.
 */
export default async function globalSetup(config?: unknown): Promise<void> {
  logEnvironmentDiagnostics();
  const baseUrl = baseUrlFromConfig(config);
  await assertBaseUrlReachable(baseUrl);
  // Ordered AFTER reachability on purpose: an unreachable dashboard cannot
  // answer /api/health, and reporting that as 'isolation unmeasured' would
  // send the reader to the wrong fix for a cause already named above.
  await assertDatabaseIsolated(baseUrl);
}
// CUI // SP-CTI
