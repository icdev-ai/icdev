// CUI // SP-CTI
// E2E Test: decide a change without reloading the page (dwr-ws-03)
//
// THE CARD'S DONE CRITERION: "accepting three changes in one session with no
// reload". This spec is that sentence, executed.
//
// WHY AN E2E AND NOT ONLY THE UNIT LAYER
// --------------------------------------
// tests/document_intelligence/test_workspace_decisions.py already reads the
// template's own source and refuses `location.reload`, refuses a full rail
// rebuild after a decision, and refuses a client-side splice. Those are
// STRUCTURAL — they prove the file says the right thing. None of them proves a
// browser does it. The claim here is about what survives across three
// decisions in one live JavaScript context, and only a browser can answer that.
//
// TWO PROOFS THAT NO RELOAD HAPPENED, AT TWO DIFFERENT LAYERS
// ----------------------------------------------------------
//   the sentinel   a token written onto `window` after the first load. Every
//                  form of reload — `location.reload()`, `location.href = …`, a
//                  form submit, a meta refresh — replaces the document and
//                  takes the JS context with it, so a surviving sentinel is
//                  proof the context was never replaced. This is the PAGE's
//                  view.
//   the counter    `framenavigated` on the MAIN frame, armed after `goto`
//                  returns. This is the BROWSER's view, and it fires for a
//                  navigation whether or not any script survived it.
// They observe different layers rather than being wholly independent — both go
// red on a reload. Stated rather than overclaimed: what the pair buys is that a
// page which somehow rebuilt its own context, or a navigation that somehow left
// a global standing, cannot read as clean.
//
// AND ONE PROOF THE REVIEWER WOULD RECOGNISE, which is the thing the card is
// actually about. The reviewer opens Edit & Accept on the THIRD proposal and
// types their own replacement into it, and only then decides the first two.
// That typed text is still in the box afterwards. A reload loses it; so does a
// wholesale rail rebuild, which is what dwr-ws-02 did and what `reconcile` now
// deliberately refuses to do over a card holding typed text. It is the
// "reviewer loses context on every single decision" complaint, asserted.
//
// WHY A FIXTURE. Measured 2026-09-08: all 58 pending `dic_suggestions` on this
// deployment carry a NULL `anchor_basis`, and the document holding 47 of them
// has ZERO `dic_sections` rows. The accept door refuses an unanchored proposal
// with a 409 BY DESIGN, so on the live board there is nothing acceptable to
// accept — a spec driven off it would prove the refusal path works and nothing
// about the decision path. tests/e2e/fixtures/dic_workspace_fixture.py seeds
// through the store's own `create_suggestion` / `whole_section_anchor` and
// removes exactly what it wrote, by id.

import { spawnSync } from 'child_process';
import path from 'path';

import { test, expect } from './fixtures/auth';
import { webServerDatabaseEnv } from './fixtures/e2e_database';
import type { Page } from '@playwright/test';

const ROOT = path.resolve(__dirname, '../..');
const FIXTURE = path.resolve(ROOT, 'tests/e2e/fixtures/dic_workspace_fixture.py');
const PYTHON = process.env.ICDEV_PYTHON || 'python';

/** What `--seed` hands back. */
interface Seeded {
  doc_id: string;
  version_id: string;
  section_ids: string[];
  suggestion_ids: string[];
  proposals: string[];
  /** Each section's text as seeded, index-aligned with `section_ids`. */
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
  // The fixture prints one JSON line; anything a library logged before it is
  // skipped rather than parsed.
  const line = (res.stdout || '').trim().split(/\r?\n/).filter(Boolean).pop() || '';
  return line;
}

function seed(): Seeded {
  return JSON.parse(runFixture(['--seed'])) as Seeded;
}

function teardown(docId: string): void {
  if (!docId) return;
  try {
    runFixture(['--teardown', docId]);
  } catch (err) {
    // Reported, never swallowed: residue on the board is a finding, and a
    // teardown that failed silently is how it accumulates.
    console.warn(`[dwr-ws-03] teardown of ${docId} FAILED — residue left: ${err}`);
  }
}

const SENTINEL = 'dwr-ws-03-no-reload-sentinel';

/** The left pane's rendering of one section, as the reviewer reads it. */
function sectionText(page: Page, sectionId: string) {
  return page.locator(`#dws-doc-body .dws-sec[data-section-id="${sectionId}"] [data-role="text"]`);
}

/** One proposal's card in the rail. */
function cardFor(page: Page, sid: string) {
  return page.locator(`#dws-rail .dws-card[data-suggestion-id="${sid}"]`);
}

test.describe('DIC workspace — a decision applied in place (dwr-ws-03)', () => {
  test.describe.configure({ mode: 'serial' });

  let fixture: Seeded | null = null;
  // Every document this file seeds, so afterAll removes all of them even if a
  // test throws part-way through.
  const seeded: string[] = [];

  function seedDoc(): Seeded {
    const s = seed();
    seeded.push(s.doc_id);
    return s;
  }

  test.beforeAll(async ({ browser }) => {
    // The Document Intelligence canvas ships `default_enabled: false`
    // (args/component_registry.yaml), so a deployment that has not set
    // ICDEV_DIC_ENABLED serves no workspace at all. That is a DEPLOYMENT
    // CHOICE and the only condition this spec skips for — and it skips
    // NAMING the toggle, because a skip that says nothing is the "unmeasured
    // reads as clean" defect. Everything else (an unreachable board, a
    // fixture that will not seed) is a real failure and is left to fail.
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
    fixture = seedDoc();
  });

  test.afterAll(() => {
    seeded.forEach(teardown);
  });

  test('three changes accepted in one session, with no reload', async ({ page }) => {
    test.setTimeout(120_000);
    const f = fixture!;
    const [sectionA, sectionB, sectionC] = f.section_ids;
    // The fixture's proposals are index-aligned with its sections; the rail
    // orders by recency, so every locator below addresses a proposal by ID and
    // never by position.
    const sidFor: Record<string, string> = {};
    f.section_ids.forEach((sec, i) => { sidFor[sec] = f.suggestion_ids[i]; });
    const proposalFor: Record<string, string> = {};
    f.section_ids.forEach((sec, i) => { proposalFor[sec] = f.proposals[i]; });

    await page.goto(`/document-intelligence/workspace/${f.doc_id}`);
    await page.waitForLoadState('domcontentloaded');

    // ── The browser's view: armed AFTER the first navigation, so any entry is
    // a navigation this session did not want.
    const navigations: string[] = [];
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) navigations.push(frame.url());
    });

    // ── The page's view.
    await page.evaluate((token) => {
      (window as unknown as Record<string, unknown>).__dwrWs03 = token;
    }, SENTINEL);

    // The rail is filled from /api/change-set, so wait for the change set and
    // not for the page.
    await expect(page.locator('#dws-rail .dws-card')).toHaveCount(3, { timeout: 30_000 });

    // The document reads as seeded, and the counts are a MEASURED zero — three
    // proposals exist and none is decided. Not `null` (which is what "nobody
    // could read this" looks like) and not 100%.
    for (const sec of f.section_ids) {
      await expect(sectionText(page, sec)).not.toHaveText(proposalFor[sec]);
    }
    await expect(page.locator('#dws-progress')).toContainText('0 of 3 changes resolved');
    await expect(page.locator('#dws-progress')).toContainText('(0.0%)');

    // ── The reviewer's in-progress context: the third proposal opened for
    // editing, with their OWN wording typed in, BEFORE the other two are
    // decided. This is what a reload — or a rail rebuild — destroys.
    const typed = 'Devices are polled over SNMPv3 with authPriv, per the enclave baseline.';
    await cardFor(page, sidFor[sectionC]).locator('button[data-act="edit"]').click();
    const editor = page.locator(`.dws-edit-wrap[data-sid="${sidFor[sectionC]}"] textarea`);
    await expect(editor).toBeVisible();
    await editor.fill(typed);

    // ── Decision 1 — Accept the AI draft as written.
    await cardFor(page, sidFor[sectionA]).locator('button[data-act="accept"]').click();
    // The LEFT PANE is updated in place, from the content the door RE-READ
    // after its write — never a splice this page computed for itself.
    await expect(sectionText(page, sectionA)).toHaveText(proposalFor[sectionA], { timeout: 20_000 });
    await expect(page.locator('#dws-progress')).toContainText('1 of 3 changes resolved');
    await expect(page.locator('#dws-progress')).toContainText('(33.3%)');
    // The card stays on screen — the rail is the record of the sitting, not
    // just the queue — and offers no second decision.
    await expect(cardFor(page, sidFor[sectionA])).toHaveClass(/is-settled/);
    await expect(cardFor(page, sidFor[sectionA]).locator('button[data-act="accept"]')).toBeDisabled();
    await expect(cardFor(page, sidFor[sectionA]).locator('button[data-act="reject"]')).toBeDisabled();
    // A DECIDED change is no longer marked in the document: its offsets were
    // measured against the text as it read BEFORE the splice.
    await expect(
      sectionText(page, sectionA).locator(`.dws-anchor[data-suggestion-id="${sidFor[sectionA]}"]`),
    ).toHaveCount(0);
    // …and the reviewer's typed text is untouched by somebody else's decision.
    await expect(editor).toHaveValue(typed);

    // ── Decision 2 — the other untouched section.
    await cardFor(page, sidFor[sectionB]).locator('button[data-act="accept"]').click();
    await expect(sectionText(page, sectionB)).toHaveText(proposalFor[sectionB], { timeout: 20_000 });
    await expect(page.locator('#dws-progress')).toContainText('2 of 3 changes resolved');
    await expect(page.locator('#dws-progress')).toContainText('(66.6%)');
    await expect(editor).toHaveValue(typed);
    // Decision 1's result did not vanish when decision 2 redrew its own card.
    await expect(page.locator(`.dws-result[data-sid="${sidFor[sectionA]}"]`)).toContainText('accepted');

    // ── Decision 3 — Edit & Accept. What ships is the HUMAN's sentence, and
    // the door records it as `human_edit` beside the untouched AI draft.
    await page.locator(`.dws-edit-wrap[data-sid="${sidFor[sectionC]}"] button[data-go]`).click();
    await expect(sectionText(page, sectionC)).toHaveText(typed, { timeout: 20_000 });
    // Not the AI draft — the distinction the card asks for, asserted from the
    // document itself rather than from the response.
    await expect(sectionText(page, sectionC)).not.toHaveText(proposalFor[sectionC]);
    await expect(page.locator(`.dws-result[data-sid="${sidFor[sectionC]}"]`)).toContainText('human_edit');

    // ── Three of three, and 100.0% is reachable here because it is TRUE.
    await expect(page.locator('#dws-progress')).toContainText('3 of 3 changes resolved');
    await expect(page.locator('#dws-progress')).toContainText('(100.0%)');
    await expect(page.locator('#dws-progress')).toContainText('3 decided by a reviewer');
    await expect(page.locator('#dws-progress')).toContainText('0 still waiting');

    // ── NO RELOAD. Both proofs, and the reviewer's context still on screen.
    const survived = await page.evaluate(
      () => (window as unknown as Record<string, unknown>).__dwrWs03,
    );
    expect(
      survived,
      'the JS context was replaced — something reloaded or navigated the page',
    ).toBe(SENTINEL);
    expect(
      navigations,
      'the main frame navigated after the initial load',
    ).toEqual([]);

    await page.screenshot({
      path: 'playwright/screenshots/dwr-ws-03-three-decisions-no-reload.png',
      fullPage: true,
    });
  });

  test('a stale anchor is shown as a refusal, and the counts follow it', async ({ page }) => {
    test.setTimeout(120_000);
    // ITS OWN DOCUMENT. The test above accepts all three of its fixture's
    // proposals, so sharing one would leave this test nothing pending to
    // decide — and, worse, would make its meaning depend on the order the two
    // ran in.
    const f = seedDoc();
    const section = f.section_ids[0];

    // A SECOND proposal against a section the FIRST proposal also anchors.
    // Written through the same crowdsource door a reviewer uses, so its anchor
    // is the store's and not this spec's: it records the section as it reads
    // NOW, which the first accept is about to change underneath it.
    const suggest = await page.request.post(
      `/document-intelligence/api/sections/${section}/suggest`,
      {
        data: {
          proposed_content: 'The enclave SHALL use TLS 1.2 or higher for all transport.',
          rationale: 'dwr-ws-03 E2E — the proposal that goes stale',
        },
        failOnStatusCode: false,
      },
    );
    expect(suggest.status(), await suggest.text()).toBe(201);
    const stale = (await suggest.json()).suggestion_id as string;

    await page.goto(`/document-intelligence/workspace/${f.doc_id}`);
    await page.waitForLoadState('domcontentloaded');
    const navigations: string[] = [];
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) navigations.push(frame.url());
    });
    // Its three fixture proposals, plus the one just written above.
    await expect(page.locator('#dws-rail .dws-card')).toHaveCount(4, { timeout: 30_000 });
    await expect(page.locator('#dws-progress')).toContainText('0 of 4 changes resolved');

    // ── The document moves under the reviewer, OUT OF BAND.
    //
    // THIS IS A RACE, AND IT HAS TO BE ONE. Deciding the fixture's proposal
    // through the UI would make the page reconcile, and the re-assembled change
    // set re-runs `verify_anchor`: the stale card would then render its reason
    // and DISABLE Accept — which is the correct common path (`refusal()`
    // withholds a decision the door would refuse rather than offering it), and
    // it is not this one. The 409 is reached only when the section changes
    // between the change set this page is holding and the click, which is
    // exactly what a second reviewer in another tab does. So the accept below
    // goes through the API on the same session, and the page is never told.
    const outOfBand = await page.request.post(
      `/document-intelligence/api/suggestions/${f.suggestion_ids[0]}/accept`,
      { data: {}, failOnStatusCode: false },
    );
    expect(outOfBand.status(), await outOfBand.text()).toBe(200);

    // The page is now showing text the section no longer holds, and its card
    // still offers Accept — because nothing has told it otherwise.
    await expect(sectionText(page, section)).toHaveText(f.current[0]);
    await cardFor(page, stale).locator('button[data-act="accept"]').click();

    const card = cardFor(page, stale);
    // A REFUSAL, IN THOSE WORDS — not "an error occurred".
    await expect(card).toContainText('The document moved under you', { timeout: 20_000 });
    await expect(card).toContainText('Nothing was written');
    // Both sides of the anchor, so the reviewer can see WHAT moved.
    await expect(card.locator('.dws-refusal pre')).toHaveCount(2);
    // The repair offered is a re-read of the DOCUMENT. It is deliberately NOT
    // an offer to re-anchor the PROPOSAL: that one is superseded, no door will
    // take it, and a button that cannot work is worse than none.
    await expect(card.locator('button[data-reanchor]')).toBeVisible();
    await expect(card).toContainText('cannot be re-anchored in place');

    // A REFUSAL MOVES THE COUNTS. The stale proposal was SUPERSEDED by the
    // door, so it has left the queue without a human deciding it: `resolved`
    // rises to 2 while `reviewed` stays at 1. The two are never merged.
    await expect(page.locator('#dws-progress')).toContainText('2 of 4 changes resolved', {
      timeout: 20_000,
    });
    await expect(page.locator('#dws-progress')).toContainText('(50.0%)');
    await expect(page.locator('#dws-progress')).toContainText('1 decided by a reviewer');
    await expect(page.locator('#dws-progress')).toContainText('1 superseded');

    // The refusal is a decision applied in place too.
    expect(navigations, 'the main frame navigated after the initial load').toEqual([]);

    await page.screenshot({
      path: 'playwright/screenshots/dwr-ws-03-stale-anchor-refusal.png',
      fullPage: true,
    });
  });
});
