// CUI // SP-CTI
/**
 * The E2E env-diagnostics snapshot never carries an embedded credential
 * (qa-fail-0992fb60b78c0b2e).
 *
 * `globalSetup.ts` redacted by KEY NAME only, so a DSN under the innocuous key
 * `ICDEV_DATABASE_URL` reached `.tmp/test_runs/e2e-env-diagnostics*.json` — a
 * file CI uploads — with its password verbatim. This spec lives here, not in
 * pytest, because the CI pytest jobs install no Node; the E2E job does, and
 * Playwright transpiles the TypeScript. Nothing here opens a page.
 *
 * The first test writes a REAL snapshot through `logEnvironmentDiagnostics`, so
 * it proves the wiring and not just the helper. It was run RED first, against
 * the key-name-only `display()`: the sentinel password was in the file.
 */
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { logEnvironmentDiagnostics } from '../../globalSetup';
import {
  REDACTED,
  classifyBaselineValue,
  display,
  maskEmbeddedCredentials,
} from './fixtures/env_redaction';

// A made-up password. It must never appear in the snapshot.
const SENTINEL = 'hunter2-sentinel-0992fb60';

test.describe('E2E env-diagnostics snapshot redaction (qa-fail-0992fb60b78c0b2e)', () => {
  test('a snapshot never carries a URL-embedded password', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'icdev-envdiag-'));
    const baselineFile = path.join(root, 'ci-snapshot.json');
    fs.writeFileSync(
      baselineFile,
      JSON.stringify({ env: { ICDEV_DATABASE_URL: `postgresql://u:${REDACTED}@h:5432/db` } }),
      'utf8',
    );

    const touched = [
      'ICDEV_E2E_ENV_DIAG_DONE',
      'TEST_WORKER_INDEX',
      'ICDEV_E2E_ENV_DIAG',
      'ICDEV_E2E_ENV_BASELINE',
      'ICDEV_PW_RUN_TAG',
      'ICDEV_DATABASE_URL',
      'ICDEV_E2E_REDACTION_PROBE_URL',
    ];
    const saved = Object.fromEntries(touched.map((k) => [k, process.env[k]]));
    try {
      // logEnvironmentDiagnostics runs once per run and never inside a worker;
      // lift both guards for this one call, restored below.
      delete process.env.ICDEV_E2E_ENV_DIAG_DONE;
      delete process.env.TEST_WORKER_INDEX;
      process.env.ICDEV_E2E_ENV_DIAG = '0';
      process.env.ICDEV_E2E_ENV_BASELINE = baselineFile;
      process.env.ICDEV_PW_RUN_TAG = 'redaction-probe';
      process.env.ICDEV_DATABASE_URL = `postgresql://u:${SENTINEL}@h:5432/db`;
      // Not in the baseline, so it lands as a `local-only` row.
      process.env.ICDEV_E2E_REDACTION_PROBE_URL = `https://svc:${SENTINEL}@example.invalid/x`;

      logEnvironmentDiagnostics({ root, webServerActive: false });

      const outFile = path.join(root, '.tmp', 'test_runs', 'e2e-env-diagnostics-redaction-probe.json');
      const raw = fs.readFileSync(outFile, 'utf8');
      expect(raw).not.toContain(SENTINEL);

      const snap = JSON.parse(raw);
      expect(snap.env.ICDEV_DATABASE_URL).toBe(`postgresql://u:${REDACTED}@h:5432/db`);
      expect(snap.env.ICDEV_E2E_REDACTION_PROBE_URL).toBe(`https://svc:${REDACTED}@example.invalid/x`);

      const row = snap.diff.find((r: { key: string }) => r.key === 'ICDEV_DATABASE_URL');
      expect(row.local).toBe(`postgresql://u:${REDACTED}@h:5432/db`);
      // The baseline stores the masked form; that is "not compared", never a difference.
      expect(row.state).toBe('redacted');

      const probe = snap.diff.find((r: { key: string }) => r.key === 'ICDEV_E2E_REDACTION_PROBE_URL');
      expect(probe.state).toBe('local-only');
      expect(probe.local).toBe(`https://svc:${REDACTED}@example.invalid/x`);
    } finally {
      for (const [k, v] of Object.entries(saved)) {
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
      }
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  test('the password in URL userinfo is masked; scheme, user, host and database stay', () => {
    expect(maskEmbeddedCredentials(`postgresql://u:${SENTINEL}@h:5432/db`)).toBe(
      `postgresql://u:${REDACTED}@h:5432/db`,
    );
    // An empty user still hides the password.
    expect(maskEmbeddedCredentials(`postgresql://:${SENTINEL}@h/db`)).toBe(`postgresql://:${REDACTED}@h/db`);
    // An unencoded `@` in the password does not leak its tail.
    expect(maskEmbeddedCredentials(`postgresql://u:${SENTINEL}@tail@h/db`)).toBe(`postgresql://u:${REDACTED}@h/db`);
  });

  test('a password given as a keyword or a query parameter is masked', () => {
    expect(maskEmbeddedCredentials(`postgresql://h/db?sslmode=require&password=${SENTINEL}`)).toBe(
      `postgresql://h/db?sslmode=require&password=${REDACTED}`,
    );
    expect(maskEmbeddedCredentials(`host=h password=${SENTINEL} dbname=db`)).toBe(
      `host=h password=${REDACTED} dbname=db`,
    );
    expect(maskEmbeddedCredentials(`host=h password='${SENTINEL} x' dbname=db`)).toBe(
      `host=h password=${REDACTED} dbname=db`,
    );
  });

  test('a value with no credential is left exactly as it was', () => {
    for (const plain of [
      'postgresql://u@h:5432/db',
      'http://localhost:5050',
      'postgresql://h/db?sslmode=require',
      'icdev_e2e',
      'true',
    ]) {
      expect(maskEmbeddedCredentials(plain)).toBe(plain);
    }
  });

  test('display() redacts by key, masks by value, and keeps its unset/empty markers', () => {
    expect(display('ICDEV_DATABASE_URL', `postgresql://u:${SENTINEL}@h/db`)).toBe(`postgresql://u:${REDACTED}@h/db`);
    expect(display('ICDEV_PG_PASSWORD', SENTINEL)).toBe(REDACTED);
    // A boolean under a secret-looking key is a flag, not a secret.
    expect(display('ICDEV_CREDENTIAL_BROKER_ENABLED', 'true')).toBe('true');
    expect(display('ICDEV_PG_DATABASE', 'icdev_e2e')).toBe('icdev_e2e');
    expect(display('ICDEV_PG_DATABASE', undefined)).toBe('<unset>');
    expect(display('ICDEV_PG_DATABASE', '')).toBe('<empty>');
  });

  test('a baseline carrying the masked form reads redacted; a visible difference still differs', () => {
    const local = `postgresql://u:${SENTINEL}@h:5432/db`;
    expect(classifyBaselineValue('ICDEV_DATABASE_URL', `postgresql://u:${REDACTED}@h:5432/db`, local)).toBe('redacted');
    // The database is visible, so a different one is a real difference.
    expect(classifyBaselineValue('ICDEV_DATABASE_URL', `postgresql://u:${REDACTED}@h:5432/other`, local)).toBe('differs');
    // The key-name rule it replaced still holds.
    expect(classifyBaselineValue('ICDEV_PG_PASSWORD', REDACTED, SENTINEL)).toBe('redacted');
    expect(classifyBaselineValue('ICDEV_PG_DATABASE', 'icdev', 'icdev')).toBe('match');
    expect(classifyBaselineValue('ICDEV_PG_DATABASE', 'icdev', 'icdev_e2e')).toBe('differs');
    expect(classifyBaselineValue('ICDEV_PG_DATABASE', 'icdev', undefined)).toBe('missing-locally');
  });
});
// CUI // SP-CTI
