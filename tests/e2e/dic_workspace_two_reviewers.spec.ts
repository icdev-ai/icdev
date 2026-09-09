// CUI // SP-CTI
// E2E Test: two reviewers in one document, without a lost write (dwr-collab-01)
//
// THE CARD'S DONE CRITERION, executed verbatim: "two browser sessions, one
// accepts, the other sees it within a poll and its stale decision is refused."
//
// WHY A BROWSER, AND NOT ONLY THE UNIT LAYER
// ------------------------------------------
// tests/docmod/test_collab_poll.py proves the server side and proves it hard —
// including a deterministic injection of the interleaving that used to lose a
// write, which goes red on the pre-fix tree. What it cannot prove is the claim
// this card is actually about: that a SECOND REVIEWER'S ALREADY-OPEN PAGE
// learns what a first reviewer did, on its own, without anybody touching it.
// That is a statement about two live JavaScript contexts and a clock, and only
// a browser can answer it.
//
// TWO CONTEXTS, NOT TWO TABS. Each reviewer gets their own browser context, so
// they carry separate cookie jars and separate presence sessions — the same
// separation two people at two desks have. Two tabs in one context would share
// a session and would prove nothing about presence.
//
// WHAT IS ASSERTED, and each is a different failure:
//   1. ALICE'S PAGE, ALREADY OPEN, LEARNS. She never reloads, never clicks
//      refresh, and never interacts with the card Bob decides. The card
//      changes under her because the poll delivered it. A `framenavigated`
//      counter and a window sentinel both prove no reload happened — the
//      dwr-ws-03 pair, reused, because "it updated" is worthless if the way it
//      updated was a page load.
//   2. HER STALE DECISION IS REFUSED. She then clicks Accept on the change Bob
//      already rejected. The door answers 409 `already_decided` and her page
//      says so, NAMING BOB. It does not say "error", and it does not say
//      "accepted".
//   3. NOTHING WAS LOST. Bob's decision still stands afterwards, and the
//      append-only decision chain carries exactly one row for that change.
//      This is the assertion the whole card exists for: the refusal must not be
//      cosmetic, with the second write landing behind it.
//   4. SHE CAN SEE HE IS THERE. The presence strip names Bob while his context
//      is open. "Only you" is a measured verdict, and the page must not print
//      it while a second reviewer is in the document.
//
// TIMING IS BOUNDED BY THE POLL AND NOT BY A GUESS. The client polls at 3 s, so
// every wait below allows several intervals and fails loudly rather than
// flaking silently. Nothing here sleeps a fixed amount and then asserts.
//
// WHY A FIXTURE, not the live board: measured 2026-09-08, all 58 pending
// `dic_suggestions` on this deployment carry a NULL `anchor_basis`, so the
// accept door refuses every one of them BY DESIGN (dwr-anchor-05) and a spec
// driven off the board would prove the refusal path and nothing else. The
// fixture is dwr-ws-03's, unchanged, seeding through the store's own
// `create_suggestion` / `whole_section_anchor` and removing what it wrote.

import { spawnSync } from 'child_process';
import path from 'path';

import { test, expect } from './fixtures/auth';
import { webServerDatabaseEnv } from './fixtures/e2e_database';
import type { BrowserContext, Page } from '@playwright/test';

const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = path.resolve(ROOT, 'tests/e2e/fixtures/dic_workspace_fixture.py');
const PYTHON = process.env.ICDEV_PYTHON || 'python';

interface Seeded {
  doc_id: string;
  version_id: string;
  section_ids: string[];
  suggestion_ids: string[];
  proposals: string[];
  current: string[];
}

// A PYTHON FIXTURE SUBPROCESS MUST LAND ON THE DATABASE THE SERVER IS ON.
//
// `webServerDatabaseEnv()` redirects the dashboard Playwright starts
// (playwright.config.ts). It does NOT reach a subprocess a spec spawns, and
// `{ ...process.env }` carries the operator's ambient `ICDEV_DATABASE_URL`
// through unchanged -- which every connection site in `tools/db/storage.py`
// reads BEFORE the discrete `ICDEV_PG_DATABASE`. So the documented isolation
// recipe
//
//   ICDEV_PG_DATABASE=icdev_e2e npx playwright test
//
// put the SERVER on `icdev_e2e` and this fixture on the canonical `icdev`:
// the seed committed to one database and the rail read the other, so
// `#dws-rail .dws-card` resolved to 0 elements with nothing wrong with the
// product. MEASURED 2026-09-09 with exactly that env --
// `get_connection()` -> `icdev` while `/api/health` -> `icdev_e2e`.
// That is qa-fail-6a87916931be3793's defect surviving one layer over, and it
// also means an "isolated" run was still writing fixtures into the canonical
// board.
//
// Applying the SAME function the server env is built from is what keeps the
// two from disagreeing -- a second spelling of the precedence here is how they
// came to disagree in the first place. It returns `{}` when no database was
// requested, so a plain local run is unchanged.
function runFixture(args: string[]): string {
  const res = spawnSync(PYTHON, [FIXTURE, ...args], {
    cwd: ROOT,
    encoding: 'utf-8',
    env: { ...process.env, ...webServerDatabaseEnv(), PYTHONIOENCODING: 'utf-8' },
  });
  if (res.status !== 0) {
    throw new Error(
      `fixture ${args.join(' ')} failed (exit ${res.status}): ${res.stderr || res.stdout}`,
    );
  }
  const line = (res.stdout || '').trim().split(/\r?\n/).filter(Boolean).pop() || '';
  return line;
}

const SENTINEL = 'dwr-collab-01-no-reload-sentinel';

function cardFor(page: Page, sid: string) {
  return page.locator(`#dws-rail .dws-card[data-suggestion-id="${sid}"]`);
}

test.describe('DIC workspace — two reviewers, no lost write (dwr-collab-01)', () => {
  test.describe.configure({ mode: 'serial' });

  let fixture: Seeded | null = null;
  const seeded: string[] = [];

  test.beforeAll(async ({ browser }) => {
    // The DIC canvas ships `default_enabled: false`, so a deployment that has
    // not set ICDEV_DIC_ENABLED serves no workspace at all. That deployment
    // choice is the ONLY condition this spec skips for, and it skips NAMING
    // the toggle — a skip that says nothing is the "unmeasured reads as clean"
    // defect this card series refuses.
    const probe = await browser.newContext();
    let status = 0;
    try {
      const res = await probe.request.get('/document-intelligence/', { failOnStatusCode: false });
      status = res.status();
    } catch {
      status = 0;
    } finally {
      await probe.close();
    }
    test.skip(
      status !== 200,
      `the Document Intelligence canvas is not served here (GET /document-intelligence/ -> ${status}). ` +
        'It is default_enabled: false in args/component_registry.yaml; set ICDEV_DIC_ENABLED=1 to measure this.',
    );
    fixture = JSON.parse(runFixture(['--seed'])) as Seeded;
    seeded.push(fixture.doc_id);
  });

  test.afterAll(() => {
    seeded.forEach((docId) => {
      try {
        runFixture(['--teardown', docId]);
      } catch (err) {
        // Reported, never swallowed: residue on the board is a finding.
        console.warn(`[dwr-collab-01] teardown of ${docId} FAILED — residue left: ${err}`);
      }
    });
  });

  test("a second reviewer's page learns, and their stale decision is refused",
    async ({ browser }) => {
      test.setTimeout(180_000);
      const f = fixture!;
      const contested = f.suggestion_ids[0];   // the change both reviewers touch
      const untouched = f.suggestion_ids[1];   // a control: it must not move

      let alice: BrowserContext | null = null;
      let bob: BrowserContext | null = null;
      try {
        alice = await browser.newContext();
        bob = await browser.newContext();
        const aPage = await alice.newPage();
        const bPage = await bob.newPage();

        // ── Both reviewers open the same document.
        await aPage.goto(`/document-intelligence/workspace/${f.doc_id}`);
        await aPage.waitForLoadState('domcontentloaded');
        await bPage.goto(`/document-intelligence/workspace/${f.doc_id}`);
        await bPage.waitForLoadState('domcontentloaded');

        // Alice's no-reload proof, armed AFTER her navigation so any entry is
        // a navigation this session did not ask for. Both layers, as dwr-ws-03
        // established: the sentinel is the PAGE's view (a reload replaces the
        // JS context and takes it with it) and the counter is the BROWSER's.
        const navigations: string[] = [];
        aPage.on('framenavigated', (frame) => {
          if (frame === aPage.mainFrame()) navigations.push(frame.url());
        });
        await aPage.evaluate((token) => {
          (window as unknown as Record<string, unknown>).__dwrCollabSentinel = token;
        }, SENTINEL);

        // Both rails have drawn the contested change as pending.
        await expect(cardFor(aPage, contested)).toBeVisible({ timeout: 30_000 });
        await expect(cardFor(bPage, contested)).toBeVisible({ timeout: 30_000 });
        const aliceAccept = cardFor(aPage, contested).locator('button[data-act="accept"]');
        await expect(aliceAccept).toBeVisible({ timeout: 30_000 });

        // ── 4. Alice can see a second reviewer is in the document. Asserted
        // BEFORE anything is decided, because presence is about who MIGHT be
        // deciding — a strip that only appears after a collision is no use.
        //
        // BOB ANNOUNCES A DISTINCT IDENTITY, and that is a statement about this
        // deployment rather than a convenience. Presence is keyed per PERSON
        // (`join_document` renews an existing row for the same doc+user, so one
        // reviewer with three tabs is one row — deliberately, since "3 other
        // reviewers" for one person is a fabrication in the direction that
        // stops somebody deciding). This dashboard runs DEV AUTO-LOGIN, so both
        // browser contexts are the SAME user: measured while writing this spec,
        // two independent contexts produced ONE presence row and Alice's strip
        // correctly read "Only you are in this document". Two contexts alone
        // therefore cannot produce two reviewers here, and asserting they do
        // would be asserting something this deployment cannot do.
        //
        // So Bob joins through the EXISTING join route with the `user_id` a
        // real per-user deployment would supply from his session. Everything
        // downstream is unchanged and real: the registry writes the row, the
        // poll reads it back through `presence_registry.get_present_users`, and
        // Alice's strip renders it.
        const bobId = `bob.reviewer.${Date.now()}`;
        const joined = await bPage.evaluate(async ([docId, uid]) => {
          const r = await fetch(
            `/document-intelligence/api/documents/${docId}/presence/join`,
            { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ user_id: uid }) },
          );
          return { status: r.status, body: await r.json().catch(() => ({})) };
        }, [f.doc_id, bobId]);
        expect(joined.status, JSON.stringify(joined.body)).toBe(200);

        // Alice's ALREADY-OPEN page picks him up on its own poll — she does not
        // reload, and the strip names him.
        await expect(aPage.locator('#dws-live-who')).toContainText(
          /other reviewer/i, { timeout: 60_000 },
        );
        await expect(aPage.locator('#dws-live-who')).toContainText(bobId);
        // …and stops printing the measured "only you" verdict, which would tell
        // her nobody else can be deciding the change in front of her.
        await expect(aPage.locator('#dws-live-who')).not.toContainText(/Only you/i);

        // ── Bob rejects the contested change. Alice does not touch her page.
        const bobReject = cardFor(bPage, contested).locator('button[data-act="reject"]');
        await expect(bobReject).toBeVisible({ timeout: 30_000 });
        await bobReject.click();
        await expect(cardFor(bPage, contested)).toHaveAttribute(
          'data-status', /rejected/, { timeout: 30_000 },
        );

        // ── 1. ALICE'S PAGE LEARNS ON ITS OWN. Nobody has touched it. The
        // poll runs at 3 s; 60 s allows many intervals, so a failure here is a
        // feed that is not working rather than a slow one.
        await expect(cardFor(aPage, contested)).toHaveAttribute(
          'data-status', /rejected/, { timeout: 60_000 },
        );
        // Named, not merely "changed elsewhere": the poll carries the decision
        // row, so the reviewer gets a person to go and talk to.
        await expect(cardFor(aPage, contested)).toContainText(
          /another session|decided this change/i, { timeout: 30_000 },
        );
        // The control did NOT move — a page that redrew everything would prove
        // nothing about a delta.
        await expect(cardFor(aPage, untouched)).not.toHaveAttribute('data-status', /rejected/);

        // …and it learned without a page load, at both layers.
        expect(navigations, `alice's page navigated: ${navigations.join(', ')}`).toEqual([]);
        expect(await aPage.evaluate(
          () => (window as unknown as Record<string, unknown>).__dwrCollabSentinel,
        )).toBe(SENTINEL);

        // ── 2. HER STALE DECISION IS REFUSED. The button may already be gone
        // — the rail redraws a settled card without actions, which is the
        // correct outcome — so the refusal is driven through the DOOR with her
        // own session, which is the case that matters: two people can always
        // click inside one poll interval, and the poll is not the guard.
        const refusal = await aPage.evaluate(async (sid) => {
          const r = await fetch(
            `/document-intelligence/api/suggestions/${sid}/accept`,
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
          );
          return { status: r.status, body: await r.json().catch(() => ({})) };
        }, contested);
        expect(refusal.status).toBe(409);
        expect(refusal.body.error).toBe('already_decided');
        expect(refusal.body.applied).toBe(false);
        expect(refusal.body.decision_recorded).toBe(false);
        expect(refusal.body.attempted).toBe('accept');
        expect(refusal.body.current?.status).toBe('rejected');
        // The refusal NAMES the person whose decision stands.
        expect(refusal.body.current?.decided_by).toBeTruthy();
        expect(String(refusal.body.message)).toContain(String(refusal.body.current.decided_by));

        // ── 3. NOTHING WAS LOST. The refusal is not cosmetic: the change is
        // still rejected, and the append-only chain carries ONE decision. A
        // second row here would be the lost write with a 409 painted over it.
        const after = await aPage.evaluate(async (sid) => {
          const r = await fetch(`/document-intelligence/api/suggestions/${sid}`);
          return r.json();
        }, contested);
        expect(after.status).toBe('rejected');

        const chain = JSON.parse(runFixture(['--decisions', contested])) as {
          decisions: { decision: string; decided_by: string }[];
        };
        expect(chain.decisions.map((d) => d.decision)).toEqual(['rejected']);
      } finally {
        if (alice) await alice.close();
        if (bob) await bob.close();
      }
    });
});
