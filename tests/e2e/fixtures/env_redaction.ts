// CUI // SP-CTI
/**
 * ICDEV™ E2E diagnostics redaction — what an env-diagnostics snapshot may say
 * about a value (qa-fail-0992fb60b78c0b2e).
 *
 * THE DEFECT. `globalSetup.ts` writes `.tmp/test_runs/e2e-env-diagnostics*.json`
 * on every run and CI uploads it as an artifact. Every value went through a
 * `display()` that redacted by KEY NAME ONLY, so
 * `ICDEV_DATABASE_URL=postgresql://<user>:<password>@<host>/<db>` — a key that
 * matches no secret pattern — landed in the snapshot's `env` map and
 * `diff[].local` with the password verbatim. Measured on a QA run 2026-09-10.
 *
 * THE RULE NOW. A value is redacted WHOLE when its key names a secret, and
 * otherwise has any credential EMBEDDED in it masked in place: the password in
 * URL userinfo, and a `password=` keyword or query parameter. Scheme, user, host
 * and database stay visible, because the database name is exactly what these
 * diagnostics exist to show (qa-fail-6a87916931be3793, qa-fail-679a43311f34d5c9).
 *
 * WHEN IN DOUBT IT MASKS MORE, NEVER LESS. The userinfo rule runs to the LAST
 * `@` in a whitespace-delimited token, so a password carrying an unencoded `@`
 * does not leak its tail. The cost is stated: a credential-free URL with an `@`
 * later in its path (`http://h:5050/x?e=a@b`) loses the text between the port
 * and that `@`. That hides a diagnostic detail; the other direction publishes a
 * secret.
 *
 * Pure and import-free, so a Playwright spec can exercise it directly — the CI
 * pytest jobs have no Node, and `globalSetup.ts` itself cannot be imported by a
 * bare `node` (extensionless imports under `"type": "commonjs"`).
 */

/** What a redacted value looks like once it has been through `display()`. */
export const REDACTED = '<redacted>';

/** Keys whose values must never reach a terminal or an uploaded artifact. */
const SECRET_KEY_RE = /(PASSWORD|SECRET|TOKEN|CREDENTIAL|_KEY$|APIKEY)/i;

/** Plain on/off values — never a secret, whatever the key is called. */
const BOOLEAN_VALUE_RE = /^(true|false|0|1|yes|no|on|off)$/i;

/**
 * The key regex is deliberately broad, which makes it over-match flags like
 * `ICDEV_CREDENTIAL_BROKER_ENABLED=true` — redacting those would hide a real
 * difference to protect the string "true". A boolean is never the secret.
 */
export function isSecretKey(key: string, value: string | undefined): boolean {
  return SECRET_KEY_RE.test(key) && !BOOLEAN_VALUE_RE.test(value ?? '');
}

/** `scheme://user:PASSWORD@` — up to the last `@` in the token (see above). */
const URL_USERINFO_PASSWORD_RE = /([a-z][a-z0-9+.-]*:\/\/[^\s:@/?#]*):\S*@/gi;

/** `password=…` as a libpq keyword or a URL query parameter (`?password=`). */
const KEYWORD_PASSWORD_RE = /(\b(?:password|passwd|pwd)\s*=\s*)('(?:[^'\\]|\\.)*'|[^\s&;]+)/gi;

/** Mask credentials embedded in a value, leaving everything else as it was. */
export function maskEmbeddedCredentials(value: string): string {
  return value
    .replace(URL_USERINFO_PASSWORD_RE, `$1:${REDACTED}@`)
    .replace(KEYWORD_PASSWORD_RE, `$1${REDACTED}`);
}

/** The value as a snapshot may carry it: whole-redacted, masked, or unchanged. */
export function redactValue(key: string, value: string): string {
  if (isSecretKey(key, value)) return REDACTED;
  return maskEmbeddedCredentials(value);
}

/** Render a value for the report and the snapshot. */
export function display(key: string, value: string | undefined): string {
  if (value === undefined) return '<unset>';
  const shown = redactValue(key, value);
  return shown === '' ? '<empty>' : shown;
}

export type BaselineState = 'match' | 'differs' | 'missing-locally' | 'redacted';

/**
 * Compare one local value against a baseline value for the same key.
 *
 * A snapshot baseline stores values AFTER redaction, so a redacted local value
 * can never compare equal to it. When the baseline equals the local value's own
 * redaction the two are `redacted` — not compared — rather than `differs`, or
 * every artifact-baselined run would open with permanent false positives. A
 * masked baseline that disagrees on anything still visible (user, host,
 * database) is a real difference and says so.
 */
export function classifyBaselineValue(
  key: string,
  ciValue: string,
  local: string | undefined,
): BaselineState {
  if (local === undefined) return 'missing-locally';
  if (local === ciValue) return 'match';
  const shown = redactValue(key, local);
  if (shown !== local && shown === ciValue) return 'redacted';
  return 'differs';
}
// CUI // SP-CTI
