// Browser regression tests intercept HTTP, so they need no server or reserved port.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const root = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'benchmarks/manifest.json')));
const bundle = JSON.parse(fs.readFileSync(path.join(root, 'examples/full-dot/bundle.json')));

async function launch(t, custom = () => false) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], 'no uncaught browser errors'));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const json = data => route.fulfill({ json: data });
    if (await custom(route, url, json)) return;
    if (url.pathname === '/v1/health') return json({ toolchain_ready: true });
    if (url.pathname === '/v1/benchmarks') return json({ items: Object.entries(manifest).map(([id, value]) => ({ ...value, id })) });
    if (url.pathname === '/v1/experiments') return json({ items: [], total: 0 });
    let relative = url.pathname === '/' ? 'web/index.html' : url.pathname.startsWith('/static/') ? `web/${url.pathname.slice(8)}` : url.pathname.slice(1);
    const target = path.resolve(root, relative);
    if (target.startsWith(root + path.sep) && fs.existsSync(target) && fs.statSync(target).isFile()) {
      const contentType = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml' }[path.extname(target)];
      return route.fulfill({ body: fs.readFileSync(target), contentType });
    }
    return route.fulfill({ status: 404, json: { error: { message: 'Fixture not found' } } });
  });
  await page.goto('http://autotuner.test/');
  await page.locator('#benchmark option').first().waitFor({ state: 'attached' });
  return page;
}

test('trial help simplifies labels, opens instantly and preserves technical evidence', async t => {
  const page = await launch(t);
  await page.getByRole('button', { name: 'dot · saved full Portable evidence' }).click();
  const rows = page.locator('#trial-rows tr');
  await rows.first().waitFor();
  assert.equal(await rows.count(), bundle.trials.length);
  assert.equal(await page.locator('#trial-rows button').count(), bundle.trials.length);
  const row = rows.first();
  const trial = bundle.trials[0];
  assert.match(await row.innerText(), /Guided search|Random search/);
  assert.doesNotMatch(await row.innerText(), /no_ir_change/);
  await row.getByRole('button', { name: /Understand/ }).click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /not a claim that this exact change happened/);
  assert.equal(await dialog.locator('li').count(), trial.action_ids.length);
  assert.equal(await page.locator('#trial-detail').isVisible(), false);
  await page.keyboard.press('Escape');
  assert.equal(await dialog.isVisible(), false);
  await row.focus(); await page.keyboard.press('Enter');
  await page.locator('#trial-detail').waitFor();
  assert.match(await page.locator('#trial-detail').innerText(), /effective_pipeline/);
  await page.setViewportSize({ width: 390, height: 844 });
  await row.getByRole('button', { name: /Understand/ }).click();
  await dialog.waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await dialog.getByRole('button', { name: 'Close explanation' }).click();
  assert.equal(await dialog.isVisible(), false);
});

test('bundled evidence displays tuning, holdout, controls, and actual settings', async t => {
  const page = await launch(t);
  await page.getByRole('button', { name: 'dot · saved full Portable evidence' }).click();
  await page.locator('#comparisons h3').filter({ hasText: 'Held-out' }).waitFor();
  assert.equal(await page.locator('#comparisons tbody tr').count(), 10);
  assert.match(await page.locator('#comparisons').innerText(), /guided vs random/);
  assert.match(await page.locator('#comparisons').innerText(), /guided vs empty control/);
  assert.match(await page.locator('#result-note').innerText(), /10 confirmation rounds, 5 search repetitions/);
  assert.equal(await page.locator('#empty-state').isVisible(), false);
  assert.match(await page.locator('#environment-link').getAttribute('href'), /environment.json$/);
  await page.locator('#trial-rows tr').first().focus();
  await page.keyboard.press('Enter');
  await page.locator('#trial-detail').waitFor({ state: 'visible' });
  assert.match(await page.locator('#trial-detail').innerText(), /effective_pipeline/);
  if (process.env.BROWSER_SCREENSHOT) await page.screenshot({ path: process.env.BROWSER_SCREENSHOT, fullPage: true });
});

test('browser rejects seeds beyond its exact integer range', async t => {
  let submissions = 0;
  const page = await launch(t, async (route, url, json) => {
    if (route.request().method() === 'POST') { submissions++; await json({}); return true; }
    return false;
  });
  await page.locator('#seed').fill('9007199254740992');
  await page.locator('#run-button').click();
  assert.equal(await page.locator('#seed').evaluate(input => input.checkValidity()), false);
  assert.equal(submissions, 0);
});

test('selected test set opens complete evidence and runs every exact preset', async t => {
  const catalog = JSON.parse(fs.readFileSync(path.join(root, 'examples/showcase/catalog.json')));
  assert.ok(catalog.items.length >= 3, 'multiple measured cases must be available');
  const submissions = [];
  const page = await launch(t, async (route, url, json) => {
    if (url.pathname === '/v1/experiments' && route.request().method() === 'POST') {
      submissions.push(route.request().postDataJSON());
      await json({ id: 'showcase-fresh' }); return true;
    }
    if (url.pathname === '/v1/experiments/showcase-fresh') {
      await json({ id: 'showcase-fresh', status: 'running', stage: 'search', progress: { planned_candidates: 40, attempted_candidates: 1, valid_candidates: 1 } }); return true;
    }
    return false;
  });
  await page.locator('.showcase-case').first().waitFor();
  assert.equal(await page.locator('.showcase-case').count(), catalog.items.length);
  assert.match(await page.locator('#showcase-selection').innerText(), /Selected showcase, not a representative evaluation/);
  assert.match(await page.locator('#showcase-selection').innerText(), new RegExp(`excluded ${catalog.excluded}`));
  const wins = catalog.items.filter(item => item.comparisons.holdout.guided_vs_o3.label === 'win').length;
  assert.match(await page.locator('#showcase-win-count').innerText(), new RegExp(`^${wins} confirmed held-out wins`));
  await page.locator('#showcase-wins-only').check();
  assert.equal(await page.locator('.showcase-case:visible').count(), wins);
  await page.locator('#showcase-wins-only').uncheck();
  assert.equal(await page.locator('.showcase-case:visible').count(), catalog.items.length);
  for (const item of catalog.items) {
    const card = page.locator(`[data-case-id="${item.id}"]`);
    await card.getByRole('button', { name: 'Open measured evidence' }).click();
    await page.waitForFunction(id => document.getElementById('run-id').textContent === id, item.experiment_id);
    await page.locator('#results').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#comparisons tbody tr').count(), 10);
    assert.match(await page.locator('#summary-link').getAttribute('href'), new RegExp(`/showcase/${item.id}/summary.json$`));
    await card.getByRole('button', { name: 'Run this test' }).click();
    await page.waitForFunction(() => document.getElementById('run-id').textContent === 'showcase-fresh');
    assert.deepEqual(submissions.at(-1), item.request);
  }
  assert.equal(submissions.length, catalog.items.length);
  await page.locator('.showcase-panel').evaluate(element => element.scrollIntoView({ block: 'start' }));
  if (process.env.SHOWCASE_SCREENSHOT) await page.screenshot({ path: process.env.SHOWCASE_SCREENSHOT });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.showcase-case').first().evaluate(element => element.scrollIntoView({ block: 'start' }));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  if (process.env.SHOWCASE_MOBILE_SCREENSHOT) await page.screenshot({ path: process.env.SHOWCASE_MOBILE_SCREENSHOT });
});

test('showcase rerun reports a busy worker error without losing saved cases', async t => {
  const page = await launch(t, async (route, url) => {
    if (url.pathname === '/v1/experiments' && route.request().method() === 'POST') {
      await route.fulfill({ status: 409, json: { error: { message: 'An experiment is already running' } } }); return true;
    }
    return false;
  });
  await page.locator('.showcase-run').first().click();
  await page.locator('#showcase-error').waitFor({ state: 'visible' });
  assert.match(await page.locator('#showcase-error').innerText(), /already running/);
  assert.equal(await page.locator('.showcase-run').first().isEnabled(), true);
  assert.ok(await page.locator('.showcase-case').count() > 0);
});

test('showcase stays readable without LLVM and disables fresh runs', async t => {
  const page = await launch(t, async (route, url, json) => {
    if (url.pathname === '/v1/health') { await json({ toolchain_ready: false }); return true; }
    return false;
  });
  await page.locator('.showcase-case').first().waitFor();
  assert.equal(await page.locator('.showcase-run').first().isEnabled(), false);
  await page.locator('.showcase-case').first().getByRole('button', { name: 'Open measured evidence' }).click();
  await page.locator('#results').waitFor({ state: 'visible' });
});

test('saved uint64 seeds display exactly without JavaScript rounding', async t => {
  const raw = JSON.stringify(bundle).replaceAll('"seed":17', '"seed":18446744073709551615');
  const page = await launch(t, async (route, url) => {
    if (url.pathname === '/examples/full-dot/bundle.json') {
      await route.fulfill({ body: raw, contentType: 'application/json' }); return true;
    }
    return false;
  });
  await page.getByRole('button', { name: 'dot · saved full Portable evidence' }).click();
  await page.locator('#results').waitFor({ state: 'visible' });
  assert.match(await page.locator('#comparisons').innerText(), /seed 18446744073709551615/);
  assert.doesNotMatch(await page.locator('#comparisons').innerText(), /seed 18446744073709552000/);
});

test('switching runs ignores a late response from the previous selection', async t => {
  let releaseSlow, slowStarted;
  const slowGate = new Promise(resolve => { releaseSlow = resolve; });
  const started = new Promise(resolve => { slowStarted = resolve; });
  const run = id => ({ id, status: 'completed', stage: 'completed', progress: { planned_candidates: 40, attempted_candidates: 40, valid_candidates: 30 } });
  const page = await launch(t, async (route, url, json) => {
    if (url.pathname === '/v1/experiments') {
      await json({ items: ['slow', 'fast'].map(id => ({ id, benchmark_id: id, mode: 'full', status: 'completed', created_at: '2026-10-16T00:00:00Z' })), total: 2 }); return true;
    }
    if (url.pathname === '/v1/experiments/slow') { slowStarted(); await slowGate; await json(run('slow')); return true; }
    if (url.pathname === '/v1/experiments/fast') { await json(run('fast')); return true; }
    if (url.pathname.endsWith('/report')) { await json(bundle.summary); return true; }
    if (url.pathname.endsWith('/trials')) { await json({ items: bundle.trials }); return true; }
    if (url.pathname.includes('/artifacts/')) { await route.fulfill({ body: '<svg xmlns="http://www.w3.org/2000/svg"/>', contentType: 'image/svg+xml' }); return true; }
    return false;
  });
  await page.locator('#saved-runs button').filter({ hasText: 'slow' }).click();
  await started;
  await page.locator('#saved-runs button').filter({ hasText: 'fast' }).click();
  await page.locator('#results').waitFor({ state: 'visible' });
  const response = page.waitForResponse(response => response.url().endsWith('/slow'));
  releaseSlow();
  await response;
  assert.equal(await page.locator('#run-id').innerText(), 'fast');
});

test('custom confirmation counts and rejected finalists render accurately', async t => {
  const modified = structuredClone(bundle);
  modified.summary.request.confirmation_rounds = 12;
  modified.summary.request.search_repetitions = 7;
  modified.summary.winners.guided = null;
  modified.summary.comparisons.tuning.guided_vs_o3 = { label: 'unavailable', reason: 'Rejected finalist' };
  const page = await launch(t, async (route, url, json) => {
    if (url.pathname === '/examples/full-dot/bundle.json') { await json(modified); return true; }
    return false;
  });
  await page.getByRole('button', { name: 'dot · saved full Portable evidence' }).click();
  await page.locator('#results').waitFor({ state: 'visible' });
  assert.match(await page.locator('#result-note').innerText(), /12 confirmation rounds, 7 search repetitions/);
  assert.match(await page.locator('#metrics').innerText(), /No valid confirmed trial/);
  assert.equal((await page.locator('#result-label').innerText()).toLowerCase(), 'unavailable');
});

test('mobile evidence remains accessible without overflowing the viewport', async t => {
  const page = await launch(t);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'dot · saved full Portable evidence' }).click();
  await page.locator('#results').waitFor({ state: 'visible' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
});

test('cancel shows partial evidence and retry selects a fresh experiment', async t => {
  let cancelled = false, retried = false;
  const page = await launch(t, async (route, url, json) => {
    if (url.pathname === '/v1/experiments') {
      await json({ items: [{ id: 'active', benchmark_id: 'active', mode: 'quick', status: 'running', created_at: '2026-10-16T00:00:00Z' }], total: 1 }); return true;
    }
    if (url.pathname === '/v1/experiments/active/cancel') { cancelled = true; await json({ cancellation_requested: true }); return true; }
    if (url.pathname === '/v1/experiments/active/retry') { retried = true; await json({ id: 'fresh' }); return true; }
    if (url.pathname === '/v1/experiments/active' || url.pathname === '/v1/experiments/fresh') {
      const id = url.pathname.endsWith('/fresh') ? 'fresh' : 'active';
      await json({ id, status: id === 'active' && cancelled ? 'cancelled' : 'running', stage: 'search', progress: { planned_candidates: 5, attempted_candidates: 1, valid_candidates: 1 } }); return true;
    }
    if (url.pathname.endsWith('/trials')) { await json({ items: bundle.trials.slice(0, 1) }); return true; }
    return false;
  });
  await page.locator('#saved-runs button').first().click();
  await page.locator('#cancel-run').click();
  await page.locator('#results').waitFor({ state: 'visible' });
  assert.match(await page.locator('#result-note').innerText(), /Partial evidence only/);
  assert.equal(await page.locator('#comparison-panel').isVisible(), false);
  assert.equal(await page.locator('#trial-rows tr').count(), 1);
  await page.locator('#retry-run').click();
  await page.waitForFunction(() => document.getElementById('run-id').textContent === 'fresh');
  assert.equal(retried, true);
});
