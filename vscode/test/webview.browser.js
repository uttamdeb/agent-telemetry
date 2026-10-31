#!/usr/bin/env node
'use strict';

// Optional real-browser smoke/visual check with fake data only. No browser runtime
// dependency is added to the extension. Start preview-webview.js first, then:
// node vscode/test/webview.browser.js /path/to/playwright_cli.sh [fixture URL]
async function checkBrowser(page, base) {
  const issues = [];
  const results = [];
  const ensure = (condition, message) => { if (!condition) throw new Error(message); };
  page.on('pageerror', error => issues.push(error.message));
  page.on('console', message => { if (message.type() === 'error') issues.push(message.text()); });
  const open = async (fixture = '', theme = 'dark', width = 1100) => {
    await page.setViewportSize({width, height: 1000});
    await page.goto(base + '?fixture=' + fixture + '&theme=' + theme);
    await page.locator('#total-tokens').filter({hasText: /[\d—]/}).waitFor();
    await page.waitForFunction(() => window.__deskPreview && window.__deskPreview.messages.some(message => message.type === 'ready'));
    await page.waitForFunction(() => document.getElementById('connection-label').textContent !== 'Connecting to local monitoring…');
    ensure(!await page.locator('html').getAttribute('data-js-error'), 'render error in ' + fixture);
    ensure(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'horizontal overflow in ' + fixture + ' at ' + width);
  };
  await open();
  await open('copilot');
  ensure(await page.locator('[role="treeitem"]').count() === 4, 'Copilot parent and three children');
  ensure(await page.locator('#total-tokens').textContent() === '2.48M', 'Copilot child tokens do not increase inclusive total');
  await page.getByRole('button', {name: 'Subagents', exact: true}).click();
  ensure(await page.locator('[role="treeitem"]').count() === 3, 'three Copilot subagents remain visible under filter');
  await page.locator('[role="treeitem"][data-key="fixture-child-ui"]').click();
  ensure((await page.locator('#session-detail').textContent()).includes('already included in its Copilot parent'), 'child inclusive usage disclosure');
  await page.locator('[role="treeitem"][data-key="fixture-orphan"]').click();
  ensure((await page.locator('#session-detail').textContent()).includes('not a known zero'), 'missing child usage is unavailable');
  results.push('Copilot named children, inclusive totals and unavailable usage');
  await open();
  ensure(await page.locator('[role="treeitem"]').count() === 6, 'six fixture sessions');
  const summary = await page.locator('#total-tokens').textContent();
  await page.getByRole('button', {name: 'Main agents', exact: true}).click();
  ensure(await page.locator('[role="treeitem"]').count() === 2, 'main agent filter');
  ensure(await page.locator('#total-tokens').textContent() === summary, 'session filter changed analytics totals');
  await page.getByRole('button', {name: 'Subagents', exact: true}).click();
  ensure(await page.locator('[role="treeitem"]').count() === 4, 'subagent filter');
  ensure(await page.locator('.detail-parent').textContent() === 'Recorded parent: Make the search feel instant', 'known parent remains navigable under subagent filter');
  await page.getByRole('button', {name: 'Make the search feel instant', exact: true}).click();
  ensure(await page.getByRole('button', {name: 'All sessions', exact: true}).getAttribute('aria-pressed') === 'true', 'parent navigation restores outline');
  const root = page.locator('[role="treeitem"][data-key="fixture-main"]');
  await root.focus(); await page.keyboard.press('ArrowLeft');
  ensure(await root.getAttribute('aria-expanded') === 'false', 'left arrow collapses parent');
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('ArrowRight'); await page.keyboard.press('Enter');
  ensure(await page.locator('.detail-title').textContent() === 'Shape the command palette', 'keyboard selects child');
  ensure(await page.locator('.detail-parent').textContent() === 'Recorded parent: Make the search feel instant', 'recorded relationship');
  await page.evaluate(() => { const state = structuredClone(window.__deskPreview.current()); state.snapshot.sessions.find(session => session.key === state.selected_session).efforts = ['ultra', 'unrecorded']; window.__deskPreview.update(state); });
  ensure((await page.locator('.metadata').textContent()).includes('Ultra · Not recorded'), 'recorded effort labels preserve partial evidence');
  const before = await page.evaluate(() => document.activeElement.dataset.focus);
  await page.evaluate(() => { const state = structuredClone(window.__deskPreview.current()); state.snapshot.summary.tokens += 10; window.__deskPreview.update(state); });
  ensure(await page.evaluate(() => document.activeElement.dataset.focus) === before, 'polling preserves keyboard focus');
  await page.getByRole('button', {name: 'Reset counter', exact: true}).click();
  ensure(await page.locator('#since-value').textContent() === '+0 tokens$0.00 estimated', 'counter reset');
  await page.getByRole('button', {name: '7 days', exact: true}).click();
  ensure(await page.getByRole('button', {name: '7 days', exact: true}).getAttribute('aria-pressed') === 'true', 'range bridge');
  ensure(await page.locator('#since-heading').textContent() === 'Recorded in this window', 'counter label stays honest after baseline resets');
  results.push('session filters, parent navigation, keyboard tree, polling focus and range/reset bridge');
  await page.getByRole('button', {name: 'Subagents', exact: true}).click();
  await page.evaluate(() => { const state = structuredClone(window.__deskPreview.current()); state.selected_session = 'fixture-main'; window.__deskPreview.update(state); });
  ensure(await page.getByRole('button', {name: 'All sessions', exact: true}).getAttribute('aria-pressed') === 'true', 'native selection reveals a filtered parent');
  await open('long');
  ensure(await page.locator('#session-detail script, #session-detail img').count() === 0, 'injected telemetry became HTML');
  ensure((await page.locator('.detail-title').textContent()).startsWith('<script>'), 'telemetry renders literally');
  await open('stale');
  ensure((await page.locator('#connection-label').textContent()).includes('last snapshot retained'), 'stale snapshot disclosure');
  ensure((await page.locator('#since-value').textContent()).includes('stale'), 'stale baseline disclosure');
  await page.getByRole('button', {name: 'Retry connection', exact: true}).click();
  ensure(await page.locator('#notice').isHidden(), 'retry recovery');
  await open('upgrade');
  ensure(await page.getByRole('button', {name: 'Start local monitoring', exact: true}).count() === 0, 'outdated service must not offer competing start');
  ensure(await page.getByRole('button', {name: 'Open dashboard to upgrade', exact: true}).isVisible(), 'actionable backend upgrade');
  ensure(await page.getByRole('button', {name: 'Get the desktop app', exact: true}).count() === 0, 'an outdated running service is not offered another app');
  await open('no-workspace');
  ensure(await page.getByRole('button', {name: 'This workspace', exact: true}).isDisabled(), 'empty workspace scope disabled');
  await open('restricted');
  ensure(await page.getByRole('button', {name: 'Start local monitoring', exact: true}).isDisabled(), 'restricted workspace cannot start Python');
  await page.getByRole('button', {name: 'Get the desktop app', exact: true}).click();
  ensure(await page.evaluate(() => window.__deskPreview.messages.some(message => message.type === 'getDesktopApp')), 'desktop app request is a bare host message');
  await open('remote');
  ensure(await page.getByRole('button', {name: 'This workspace', exact: true}).isDisabled(), 'remote workspace scope disabled');
  ensure((await page.locator('#notice').textContent()).includes('remote workspace'), 'local-machine remote disclosure');
  await open('truncated');
  ensure((await page.locator('#session-cap').textContent()).includes('6 of 1,700'), 'bounded list disclosure');
  await open('empty');
  ensure(await page.locator('[role="treeitem"]').count() === 0, 'empty history');
  ensure((await page.locator('.empty-sessions').textContent()).includes('No recorded sessions'), 'empty history guidance');
  results.push('literal telemetry text, stale/retry, restricted and remote behavior, capped and empty history');
  for (const theme of ['dark', 'light', 'contrast']) {
    await open('history', theme);
    await page.screenshot({path: 'output/playwright/agent-desk-' + theme + '.png', fullPage: true});
    ensure(await page.locator('#day-strip').isVisible(), 'multi-day activity strip');
  }
  await open('long', 'light', 320);
  await page.screenshot({path: 'output/playwright/agent-desk-compact.png', fullPage: true});
  await page.emulateMedia({reducedMotion: 'reduce'});
  ensure(await page.locator('button').first().evaluate(node => getComputedStyle(node).transitionDuration) === '0s', 'reduced motion');
  await open('offline', 'light', 420);
  await page.screenshot({path: 'output/playwright/agent-desk-offline.png', fullPage: true});
  results.push('dark/light/high-contrast/320px visuals and reduced motion');
  ensure(issues.length === 0, 'browser errors: ' + issues.join('; '));
  return {passed: results, screenshots: 'output/playwright/agent-desk-{dark,light,contrast,compact,offline}.png', browserErrors: issues};
}
module.exports = {checkBrowser};
if (require.main === module) {
  const fs = require('node:fs');
  const {spawnSync} = require('node:child_process');
  const cli = process.argv[2];
  if (!cli) throw new Error('Supply the Playwright CLI executable/wrapper path. Start the fixture preview first.');
  const base = process.argv[3] || 'http://127.0.0.1:7826/';
  if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(base)) throw new Error('Fixture URL must be loopback');
  fs.mkdirSync('output/playwright', {recursive: true});
  const code = 'async (page) => { const run = ' + checkBrowser.toString() + '; return await run(page, ' + JSON.stringify(base) + '); }';
  const result = spawnSync(cli, ['run-code', code], {stdio: 'inherit'});
  process.exitCode = result.status || (result.error ? 1 : 0);
}
