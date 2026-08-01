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
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

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

/**
 * Playwright's documented hook. `playwright.config.ts` already emits the report
 * at load time — which is what makes `--list` print it, since Playwright skips
 * `globalSetup` when listing — so this is normally a no-op and exists so the
 * diagnostics still run if that call is ever removed.
 */
export default async function globalSetup(): Promise<void> {
  logEnvironmentDiagnostics();
}
// CUI // SP-CTI
