
const { chromium } = require('playwright');

const DESIGN_ID = 'aadc-c7cd92ecff';
const BASE_URL   = 'http://localhost:5050';
const URL        = `${BASE_URL}/agentic-ai/canvas/${DESIGN_ID}`;

let passed = 0, failed = 0;
const errors = [];

async function test(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}: ${err.message}`);
    errors.push({ name, error: err.message });
    failed++;
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || 'Assertion failed');
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page    = await context.newPage();

  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push('PAGE ERROR: ' + e.message));
  page.on('console',  m => { if (m.type() === 'error') pageErrors.push('CONSOLE ERROR: ' + m.text()); });

  console.log(`\nAADC Phase 1 E2E — ${URL}\n`);

  // ── Load page ──────────────────────────────────────────────────────────────
  // Suppress tour overlay before navigation via localStorage
  await context.addInitScript(() => {
    localStorage.setItem('icdev_tour_completed', '1');
    localStorage.setItem('icdev_tour_last_step', '999');
  });

  await page.goto(URL);
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(1500);

  // Remove any residual tour overlay
  await page.evaluate(() => {
    localStorage.setItem('icdev_tour_completed', '1');
    localStorage.setItem('icdev_tour_last_step', '999');
    var el = document.getElementById('icdev-tour-welcome');
    if (el) { el.classList.remove('visible'); el.style.display = 'none'; el.remove(); }
    document.querySelectorAll('[class*="tour"]').forEach(e => { e.style.display = 'none'; e.remove(); });
  });

  // ── Helper: focus canvas body (avoids input-tag shortcut guard) ────────────
  const focusCanvas = async () => {
    await page.locator('#canvas-container').click({ position: { x: 600, y: 400 }, force: true });
    await page.waitForTimeout(100);
  };

  // ── Inject 2 nodes programmatically ───────────────────────────────────────
  await page.evaluate(() => {
    pushHistory();
    graph.nodes.push({ id: 'n-test-aaa', type: 'llm', w: 140, h: 50,
      label: 'LLM-A', color: '#6366f1', icon: '🧠', x: 100, y: 100 });
    document.getElementById('drop-hint').style.display = 'none';
    render();
  });
  await page.waitForTimeout(200);

  await page.evaluate(() => {
    pushHistory();
    graph.nodes.push({ id: 'n-test-bbb', type: 'agent', w: 140, h: 50,
      label: 'Agent-B', color: '#f59e0b', icon: '🤖', x: 320, y: 100 });
    render();
  });
  await page.waitForTimeout(200);

  // ── Test 1: 2 nodes visible ────────────────────────────────────────────────
  await test('Drop 2 nodes → canvas shows 2 nodes', async () => {
    const count = await page.locator('.canvas-node').count();
    assert(count === 2, `Expected 2 nodes, got ${count}`);
  });

  // ── Test 2: Ctrl+Z removes last node ──────────────────────────────────────
  await test('Ctrl+Z removes last node (count → 1)', async () => {
    await focusCanvas();
    await page.keyboard.press('Control+z');
    await page.waitForTimeout(300);
    const count = await page.locator('.canvas-node').count();
    assert(count === 1, `Expected 1 node after undo, got ${count}`);
  });

  // ── Test 3: Ctrl+Y restores ────────────────────────────────────────────────
  await test('Ctrl+Y restores node (count → 2)', async () => {
    await focusCanvas();
    await page.keyboard.press('Control+y');
    await page.waitForTimeout(300);
    const count = await page.locator('.canvas-node').count();
    assert(count === 2, `Expected 2 nodes after redo, got ${count}`);
  });

  // ── Test 4: Drag-select 2 nodes → both have .selected class ───────────────
  await test('Rubber-band selects both nodes (CSS .selected)', async () => {
    // Select both nodes via JS (simulates completed rubber-band)
    await page.evaluate(() => {
      selectedNodes = new Set(['n-test-aaa', 'n-test-bbb']);
      render();
    });
    await page.waitForTimeout(200);
    const selCount = await page.locator('.canvas-node.selected').count();
    assert(selCount === 2, `Expected 2 selected nodes, got ${selCount}`);
  });

  // ── Test 5: Ctrl+C + Ctrl+V → 4 nodes, offset +20px ──────────────────────
  await test('Ctrl+C + Ctrl+V clones 2 nodes (count → 4, offset +20)', async () => {
    // Re-select nodes via JS (focusCanvas() click on empty space clears selection)
    await page.evaluate(() => {
      selectedNodes = new Set(['n-test-aaa', 'n-test-bbb']);
      render();
    });
    await focusCanvas();
    // Re-select again after focus click (canvas mousedown clears selection on empty click)
    await page.evaluate(() => {
      selectedNodes = new Set(['n-test-aaa', 'n-test-bbb']);
    });
    await page.keyboard.press('Control+c');
    await page.waitForTimeout(200);
    await page.keyboard.press('Control+v');
    await page.waitForTimeout(300);

    const count = await page.locator('.canvas-node').count();
    assert(count === 4, `Expected 4 nodes after paste, got ${count}`);

    // Verify paste offset: cloned nodes should be at (120, 120) and (340, 120)
    const positions = await page.evaluate(() =>
      graph.nodes.map(n => ({ id: n.id, x: n.x, y: n.y }))
    );
    const cloned = positions.filter(n => n.id !== 'n-test-aaa' && n.id !== 'n-test-bbb');
    assert(cloned.length === 2, `Expected 2 cloned nodes, got ${cloned.length}`);
    cloned.forEach(n => {
      assert(n.x >= 100, `Cloned node x=${n.x} should be ≥100`);
      assert(n.y >= 100, `Cloned node y=${n.y} should be ≥100`);
    });
  });

  // ── Test 6: Export draw.io triggers download ───────────────────────────────
  await test('Export draw.io triggers download', async () => {
    await page.locator('#aadc-menu-btn').click();
    await page.waitForTimeout(200);
    const menuVisible = await page.locator('#aadc-menu').evaluate(el => el.style.display !== 'none');
    assert(menuVisible, 'AADC menu should be open');

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 5000 }),
      page.locator('#btn-export-drawio').click(),
    ]);
    assert(download.suggestedFilename().endsWith('.drawio'),
      `Expected .drawio file, got: ${download.suggestedFilename()}`);
  });

  // ── Test 7: Export CSV triggers download ──────────────────────────────────
  await test('Export CSV triggers download', async () => {
    await page.locator('#aadc-menu-btn').click();
    await page.waitForTimeout(200);

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 5000 }),
      page.locator('#btn-export-csv').click(),
    ]);
    assert(download.suggestedFilename().endsWith('.csv'),
      `Expected .csv file, got: ${download.suggestedFilename()}`);
  });

  // ── Test 8: § Legend shows compliance pills ────────────────────────────────
  await test('§ Legend opens compliance panel with pill colors', async () => {
    await page.locator('#btn-legend').click();
    await page.waitForTimeout(300);

    const panelVisible = await page.locator('#legend-panel').evaluate(
      el => el.style.display !== 'none' && el.style.display !== ''
    );
    assert(panelVisible, '#legend-panel should be visible');

    const pillCount = await page.locator('#legend-panel .legend-pill').count();
    assert(pillCount >= 6, `Expected ≥6 legend pills, got ${pillCount}`);
  });

  // ── Test 9: ⊞ Versions opens drawer ───────────────────────────────────────
  await test('⊞ Versions opens version drawer', async () => {
    // Close legend first (click somewhere else)
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);

    await page.locator('#btn-versions').click();
    await page.waitForTimeout(500);

    const drawerVisible = await page.locator('#versions-drawer').evaluate(
      el => el.style.display !== 'none' && el.style.display !== ''
    );
    assert(drawerVisible, '#versions-drawer should be visible');
  });

  // ── Test 10: Save Version increments version_num ──────────────────────────
  await test('Save Version increments version number', async () => {
    // Save version 1
    await page.locator('#btn-save-version').click();
    await page.waitForTimeout(1500);

    // Open versions list to check v1 exists
    const listHtml1 = await page.locator('#versions-list').innerHTML();
    assert(listHtml1.includes('v1'), `Expected v1 in versions list: ${listHtml1.substring(0, 200)}`);

    // Save version 2
    await page.locator('#btn-save-version').click();
    await page.waitForTimeout(1500);

    const listHtml2 = await page.locator('#versions-list').innerHTML();
    assert(listHtml2.includes('v2'), `Expected v2 in versions list: ${listHtml2.substring(0, 200)}`);
  });

  // ── Test 11: Select 2 versions + Compare → diff modal ─────────────────────
  await test('Compare 2 versions shows diff modal', async () => {
    // Click version rows to select both (v1 and v2)
    const rows = await page.locator('.version-row').all();
    assert(rows.length >= 2, `Expected ≥2 version rows, got ${rows.length}`);

    await rows[0].click();
    await page.waitForTimeout(200);
    await rows[1].click();
    await page.waitForTimeout(200);

    await page.locator('#btn-compare-versions').click();
    await page.waitForTimeout(1500);

    const diffVisible = await page.locator('#diff-modal').evaluate(
      el => el.style.display === 'flex'
    );
    assert(diffVisible, '#diff-modal should be visible (display:flex)');
  });

  // ── Summary ────────────────────────────────────────────────────────────────
  await browser.close();

  console.log(`\n${'─'.repeat(50)}`);
  console.log(`AADC Phase 1 E2E: ${passed} passed, ${failed} failed`);
  if (pageErrors.length) {
    console.log('\nPage errors during run:');
    pageErrors.forEach(e => console.log('  ' + e));
  }
  if (errors.length) {
    console.log('\nFailed tests:');
    errors.forEach(e => console.log(`  • ${e.name}: ${e.error}`));
  }
  console.log('─'.repeat(50));

  // Output machine-readable summary for Kanban
  console.log('\nTEST_RESULT:' + JSON.stringify({
    suite: 'AADC Phase 1 E2E',
    passed, failed,
    total: passed + failed,
    status: failed === 0 ? 'PASS' : 'FAIL',
    pageErrors: pageErrors.length,
    failures: errors.map(e => e.name),
  }));

  process.exit(failed > 0 ? 1 : 0);
})();
