// CUI // SP-CTI
// icx-cnf-08: every request a page makes in these tests, matched to its route in the
// generated table (tools/dashboard/static/js/routes.gen.js), is recorded into
// .tmp/test_runs/observed-calls${ICDEV_PW_RUN_TAG}.json, uploaded with each shard's
// artifacts. CI's E2E aggregator then runs
//   python tools/conformance/feature_audit.py --e2e-gate <every shard's file>
// which fails unless every write the conformance audit credits to a page by its
// endpoint id was SENT BY A PAGE in the run.
//
// A page's own request and a test's own request are told apart by who started it.
// Chromium reports a request's initiator stack (Network.requestWillBeSent). Code a
// test runs through page.evaluate has no script URL, so a stack with a frame whose URL
// is empty is the TEST calling the route (page.evaluate(() => fetch(...)), or clicking
// a button from inside evaluate), recorded as `test`. A stack of the page's own
// scripts only (a real page.click on a button) is recorded as `page`. page.request
// never reaches the browser at all. Measured on Playwright 1.58 / Chromium (icx-cnf-08).
// Only `page` satisfies the gate, so a dead button cannot hide behind a test's fetch.
//
// Every spec imports `test` from here (fixtures/auth.ts extends it).
import { test as base, expect, type BrowserContext, type CDPSession, type Page } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(__dirname, '../../..');
const RUN_TAG = process.env.ICDEV_PW_RUN_TAG ? `-${process.env.ICDEV_PW_RUN_TAG}` : '';
export const OBSERVED = path.join(ROOT, '.tmp', 'test_runs', `observed-calls${RUN_TAG}.json`);

type Rule = { endpoint: string; method: string; re: RegExp; literal: number };

function loadRules(): Rule[] {
  const src = fs.readFileSync(path.join(ROOT, 'tools/dashboard/static/js/routes.gen.js'), 'utf8');
  const win: { ICDEV_ROUTES?: Record<string, [string, string[]][]> } = {};
  new Function('window', src)(win);
  const out: Rule[] = [];
  for (const [endpoint, rules] of Object.entries(win.ICDEV_ROUTES ?? {})) {
    for (const [rule, methods] of rules) {
      const parts = rule.split(/(<[^>]+>)/).filter(Boolean);
      const body = parts.map(p => p.startsWith('<')
        ? (p.startsWith('<path:') ? '.+' : '[^/]+')
        : p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('');
      const literal = parts.filter(p => !p.startsWith('<')).join('').split('/').filter(Boolean).length;
      for (const method of methods) out.push({ endpoint, method, re: new RegExp('^' + body + '$'), literal });
    }
  }
  return out;
}

let RULES: Rule[] | null = null;

/** The route a request is for: same method, the rule with the most literal segments. */
export function routeOf(method: string, pathname: string): Rule | null {
  RULES = RULES ?? loadRules();
  const hits = RULES.filter(r => r.method === method && r.re.test(pathname)).sort((a, b) => b.literal - a.literal);
  return hits[0] ?? null;
}

type Seen = { endpoint: string; method: string; page: boolean; test: boolean; tests: Set<string> };
const seen = new Map<string, Seen>();
const unmatched = new Set<string>();

type Stack = { callFrames: { url: string }[]; parent?: Stack } | undefined;

function byTest(stack: Stack): boolean {
  for (let s = stack; s; s = s.parent) {
    if (s.callFrames.some(f => !f.url)) return true;
  }
  return false;
}

function record(method: string, url: string, test: boolean, title: string) {
  let u: URL;
  try { u = new URL(url); } catch { return; }
  if (!/^https?:$/.test(u.protocol) || u.pathname.startsWith('/static/')) return;
  const r = routeOf(method, u.pathname);
  if (!r) { unmatched.add(`${method} ${u.pathname}`); return; }
  const key = `${method} ${r.endpoint}`;
  const s = seen.get(key) ?? { endpoint: r.endpoint, method, page: false, test: false, tests: new Set<string>() };
  if (test) s.test = true; else s.page = true;
  s.tests.add(title);
  seen.set(key, s);
}

async function watch(page: Page, origin: () => string, title: string) {
  let cdp: CDPSession;
  try { cdp = await page.context().newCDPSession(page); } catch { return; }   // not Chromium
  cdp.on('Network.requestWillBeSent', (e: { request: { url: string; method: string }; initiator: { stack?: Stack } }) => {
    const o = origin();
    if (o && !e.request.url.startsWith(o)) return;   // same origin only
    record(e.request.method, e.request.url, byTest(e.initiator.stack), title);
  });
  await cdp.send('Network.enable').catch(() => undefined);
  // Async parents, so a fetch after an `await` inside page.evaluate is still the test's.
  // The debugger is on for async stacks only: it never pauses (a `debugger;` would hang the page).
  await cdp.send('Debugger.enable').catch(() => undefined);
  await cdp.send('Debugger.setSkipAllPauses', { skip: true }).catch(() => undefined);
  await cdp.send('Debugger.setAsyncCallStackDepth', { maxDepth: 32 }).catch(() => undefined);
}

async function observe(ctx: BrowserContext, baseURL: string | undefined, title: string) {
  const origin = () => (baseURL ? new URL(baseURL).origin : '');
  ctx.on('page', p => { void watch(p, origin, title); });
  await Promise.all(ctx.pages().map(p => watch(p, origin, title)));
}

/** Merge what this worker saw into the run's file (a restarted worker adds to it). */
function flush() {
  type Out = { endpoint: string; method: string; page: boolean; test: boolean; tests: string[] };
  let prior: { routes?: Record<string, Out>; unmatched?: string[] } = {};
  try { prior = JSON.parse(fs.readFileSync(OBSERVED, 'utf8')); } catch { /* first write */ }
  const routes: Record<string, Out> = prior.routes ?? {};
  for (const [key, s] of seen) {
    const was = routes[key] ?? { endpoint: s.endpoint, method: s.method, page: false, test: false, tests: [] };
    routes[key] = { endpoint: s.endpoint, method: s.method, page: was.page || s.page, test: was.test || s.test,
      tests: [...new Set([...was.tests, ...s.tests])].sort() };
  }
  const ordered = Object.fromEntries(Object.keys(routes).sort().map(k => [k, routes[k]]));
  fs.mkdirSync(path.dirname(OBSERVED), { recursive: true });
  fs.writeFileSync(OBSERVED, JSON.stringify({ schema: 'icdev-observed-calls/v1', routes: ordered,
    unmatched: [...new Set([...(prior.unmatched ?? []), ...unmatched])].sort() }, null, 1) + '\n');
}

export const test = base.extend<{ observeCalls: void }>({
  // Depends on `page` so the default page is watched before the test first navigates.
  observeCalls: [async ({ context, browser, baseURL, page }, use, info) => {
    void page;
    await observe(context, baseURL, info.title);
    const open = browser.newContext.bind(browser);
    browser.newContext = async (...a: Parameters<typeof open>) => {
      const c = await open(...a);
      await observe(c, baseURL, info.title);
      return c;
    };
    try { await use(); } finally { browser.newContext = open; flush(); }
  }, { auto: true }],
});
export { expect };
