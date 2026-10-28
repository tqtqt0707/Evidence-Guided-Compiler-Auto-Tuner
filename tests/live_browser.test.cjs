const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

async function applicationBridge(t) {
  const root = path.resolve(__dirname, '..');
  const windows = process.platform === 'win32';
  const python = process.env.AUTOTUNER_TEST_PYTHON || (windows ? '/home/manan_kulshrestha/.venvs/compiler-autotuner/bin/python' : 'python3');
  const command = windows ? 'wsl.exe' : python;
  const args = windows ? ['-d', process.env.AUTOTUNER_TEST_WSL_DISTRO || 'Ubuntu', '-e', python, '-u', 'tests/browser_bridge.py'] : ['-u', 'tests/browser_bridge.py'];
  if (process.env.AUTOTUNER_LIVE_OPENAI === '1') args.push('--live-openai');
  const child = spawn(command, args, { cwd: root, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  let sequence = 0, stderr = '', readyResolve, readyReject;
  const pending = new Map();
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-12000); });
  child.on('error', error => { readyReject(error); for (const value of pending.values()) value.reject(error); });
  const exited = new Promise(resolve => child.on('exit', (code, signal) => {
    const error = new Error(`ASGI bridge stopped (${code}/${signal}): ${stderr}`);
    readyReject(error);
    for (const value of pending.values()) value.reject(error);
    resolve({ code, signal });
  }));
  const lines = readline.createInterface({ input: child.stdout });
  lines.on('line', line => {
    try {
      const value = JSON.parse(line);
      if (value.ready) return readyResolve();
      const request = pending.get(value.id);
      if (request) { pending.delete(value.id); request.resolve(value); }
    } catch (error) { readyReject(new Error(`Invalid bridge response: ${line}`)); }
  });
  t.after(async () => {
    child.stdin.end();
    const result = await exited;
    assert.equal(result.code, 0, stderr);
  });
  await ready;
  return async request => {
    const id = ++sequence;
    const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    child.stdin.write(JSON.stringify({ id, ...request }) + '\n');
    return response;
  };
}

test('real browser, FastAPI worker, LLVM, saved evidence, cancel, and retry', { timeout: 360000 }, async t => {
  const bridge = await applicationBridge(t);
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const pageErrors = [], httpErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('response', response => { if (response.status() >= 400) httpErrors.push(`${response.status()} ${response.url()}`); });
  t.after(async () => { await browser.close(); assert.deepEqual(pageErrors, []); assert.deepEqual(httpErrors, []); });
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    const response = await bridge({ method: request.method(), url: url.pathname + url.search,
      headers: { 'content-type': request.headers()['content-type'] || 'application/octet-stream' },
      body: (request.postDataBuffer() || Buffer.alloc(0)).toString('base64') });
    await route.fulfill({ status: response.status, headers: response.headers, body: Buffer.from(response.body, 'base64') });
  });
  await page.goto('http://autotuner.test/');
  await page.waitForFunction(() => document.getElementById('health').textContent.startsWith('Toolchain ready'));
  // A showcase button must execute its real full preset, with local guided rules.
  await page.locator('.showcase-run').first().click();
  await page.waitForFunction(() => document.getElementById('status-badge').textContent === 'completed', null, { timeout: 180000 });
  await page.locator('#comparisons h3').filter({ hasText: 'Held-out' }).waitFor();
  assert.equal(await page.locator('#trial-rows tr').count(), 40);
  assert.equal(await page.locator('#comparisons tbody tr').count(), 10);
  const showcaseId = await page.locator('#run-id').innerText();
  const showcaseRun = await page.evaluate(async id => (await fetch(`/v1/experiments/${id}`)).json(), showcaseId);
  assert.equal(showcaseRun.request.llm_mode, 'off');
  assert.equal(showcaseRun.request.confirmation_rounds, 10);
  assert.equal(showcaseRun.request.mode, 'full');
  if (process.env.AUTOTUNER_LIVE_OPENAI === '1') {
    assert.match(await page.locator('#health').textContent(), /OpenAI: gpt-6-luna/);
  }
  // Inspect a function without main, runtime inputs, or expected output.
  await page.getByRole('button', { name: 'Load example code' }).click();
  await page.locator('#source-code').fill('int twice(int x) { int redundant = x + 0; return redundant * 2; }');
  await page.getByRole('button', { name: 'Inspect code' }).click();
  await page.locator('#inspection-results').waitFor({ state: 'visible', timeout: 90000 });
  assert.match(await page.locator('#inspection-notice').innerText(), /not executed/);
  assert.match(await page.locator('#inspection-before').textContent(), /define.*twice/);
  assert.match(await page.locator('#inspection-after').textContent(), /define.*twice/);
  assert.match(await page.locator('#inspection-assembly').textContent(), /twice:/);
  assert.ok(await page.locator('#inspection-stages details').count() >= 3);
  const inspectionDownload = await page.locator('#inspection-download').getAttribute('href');
  const inspected = await page.evaluate(async url => (await fetch(url)).json(), inspectionDownload);
  assert.equal(inspected.performance, 'not_measured');
  assert.equal(inspected.executed, false);
  assert.equal(await page.locator('#inspection-error').isVisible(), false);
  const completed = [];
  for (const name of ['dot', 'matmul', 'histogram', 'pipeline', 'skew_histogram']) {
    await page.locator('#benchmark').selectOption(name);
    await page.locator('#mode').selectOption('full');
    if (!await page.getByText('Measurement settings', { exact: true }).evaluate(element => element.parentElement.open)) await page.getByText('Measurement settings', { exact: true }).click();
    await page.locator('#budget').fill('2');
    await page.locator('#size').fill(name === 'matmul' ? '5' : '64');
    await page.locator('#repeat').fill('2');
    await page.locator('#holdout-size').fill(name === 'matmul' ? '6' : '65');
    await page.locator('#holdout-repeat').fill('2');
    const previous = await page.locator('#run-id').textContent();
    await page.locator('#run-button').click();
    await page.waitForFunction(old => document.getElementById('run-id').textContent !== old, previous);
    await page.waitForFunction(() => document.getElementById('status-badge').textContent === 'completed', null, { timeout: 45000 });
    await page.locator('#comparisons h3').filter({ hasText: 'Held-out' }).waitFor();
    const id = await page.locator('#run-id').innerText();
    completed.push(id);
    assert.equal(await page.locator('#trial-rows tr').count(), 4);
    assert.equal(await page.locator('#comparisons tbody tr').count(), 10);
    await page.locator('#trial-rows tr').first().click();
    await page.locator('#trial-detail').waitFor({ state: 'visible' });
    assert.match(await page.locator('#trial-detail').innerText(), /expected_checksums/);
    for (const selector of ['#summary-link', '#trials-link', '#samples-link', '#environment-link']) {
      const url = await page.locator(selector).getAttribute('href');
      const download = await page.evaluate(async url => { const response = await fetch(url); return { status: response.status, body: await response.text() }; }, url);
      assert.equal(download.status, 200);
      assert.ok(download.body.length > 10);
    }
    assert.ok(await page.locator('#search-chart').evaluate(img => img.complete && img.naturalWidth > 0));
    assert.equal(await page.locator('#form-error').isVisible(), false);
  }
  // A fresh, longer search gives the real worker time to observe cancellation.
  await page.locator('#benchmark').selectOption('dot');
  await page.locator('#mode').selectOption('full');
  await page.locator('#size').fill('2000000');
  await page.locator('#holdout-size').fill('2000001');
  await page.locator('#repeat').fill('5');
  await page.locator('#holdout-repeat').fill('5');
  await page.locator('#run-button').click();
  await page.locator('#cancel-run').waitFor({ state: 'visible' });
  await page.locator('#cancel-run').click();
  await page.waitForFunction(() => document.getElementById('status-badge').textContent === 'cancelled', null, { timeout: 45000 });
  const cancelledId = await page.locator('#run-id').innerText();
  await page.locator('#results').waitFor({ state: 'visible' });
  assert.match(await page.locator('#result-note').innerText(), /Partial evidence only/);
  await page.locator('#retry-run').click();
  await page.waitForFunction(old => document.getElementById('run-id').textContent !== old, cancelledId);
  const retryId = await page.locator('#run-id').innerText();
  const retry = await page.evaluate(async id => (await fetch(`/v1/experiments/${id}`)).json(), retryId);
  assert.equal(retry.parent_experiment_id, cancelledId);
  await page.locator('#cancel-run').click();
  await page.waitForFunction(() => document.getElementById('status-badge').textContent === 'cancelled', null, { timeout: 45000 });
  await page.reload();
  await page.locator('#saved-runs button').filter({ hasText: 'completed' }).first().click();
  await page.locator('#results').waitFor({ state: 'visible' });
  assert.ok(completed.includes(await page.locator('#run-id').innerText()));
  if (process.env.BROWSER_SCREENSHOT) await page.screenshot({ path: process.env.BROWSER_SCREENSHOT, fullPage: true });
});
