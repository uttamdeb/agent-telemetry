'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {getHtml} = require('../src/webview');
const {compact, money, forest, composition, effortLabel, sourceLabel, sessionUsageDisplay,
  summaryUsageDisplay, modelDisplay, contextSnapshot} = require('../media/desk');
const {makeState, scenario} = require('./fixtures/desk-state');
const {createServer, page} = require('./preview-webview');
const nonce = 'test-webview-nonce-2026';
const uri = {path: '/extension', with: value => value};
const webview = {cspSource: 'vscode-webview://test', asWebviewUri: value => ({toString: () => 'vscode-resource:' + value.path})};

test('webview has a nonce-only script and no network/frame/navigation bridge', () => {
  const html = getHtml(webview, uri, nonce);
  assert.match(html, /default-src 'none'/);
  assert.match(html, /connect-src 'none'; frame-src 'none'/);
  assert.match(html, /object-src 'none'; base-uri 'none'; form-action 'none'/);
  assert.match(html, new RegExp(`script-src 'nonce-${nonce}'`));
  assert.doesNotMatch(html, /unsafe-inline|unsafe-eval|<iframe|command:|https?:\/\//);
  assert.equal((html.match(/<script /g) || []).length, 1);
  assert.match(html, /vscode-resource:\/extension\/media\/desk.js/);
  assert.match(html, /vscode-resource:\/extension\/media\/app-icon.png/);
  assert.throws(() => getHtml(webview, uri, 'x" onload="bad'), /nonce/);
});
test('resource values are attribute escaped and depend only on media', () => {
  const malicious = {cspSource: 'test" invalid="', asWebviewUri: value => ({toString: () => value.path + '" onerror="bad'})};
  const html = getHtml(malicious, uri, nonce);
  assert.match(html, /&quot; onerror=&quot;bad/);
  assert.doesNotMatch(html, /src="[^"]*" onerror=/);
  const paths = [];
  getHtml({cspSource: 'test', asWebviewUri: value => {paths.push(value.path); return value.path;}}, uri, nonce);
  assert.ok(paths.every(value => value.startsWith('/extension/media/')));
});
test('session outline links only recorded full keys and retains unresolved children', () => {
  const state = makeState(); const graph = forest(state.snapshot.sessions);
  assert.equal(graph.children.get('fixture-main').length, 2);
  assert.equal(graph.parent.get('fixture-child-ui'), 'fixture-main');
  assert.ok(graph.roots.some(session => session.key === 'fixture-orphan'));
  assert.ok(graph.roots.some(session => session.key === 'fixture-outside'));
  assert.equal(graph.byKey.get('fixture-main').tokens, 2480000, 'child totals never change the parent');
  const missing = forest([{key: 'a', parent_key: 'same-title', parent_status: 'linked', title: 'same-title'}, {key: 'b', title: 'same-title'}]);
  assert.equal(missing.parent.size, 0);
});
test('cyclic, self, missing and unverified parent identities do not invent links', () => {
  const graph = forest([{key: 'a', parent_key: 'b', parent_status: 'linked'}, {key: 'b', parent_key: 'a', parent_status: 'linked'}, {key: 'c', parent_key: 'c', parent_status: 'linked'}, {key: 'd', parent_key: 'missing', parent_status: 'linked'}, {key: 'e', parent_key: 'a', parent_status: 'unavailable'}]);
  assert.equal(graph.parent.size, 0);
  assert.equal(graph.roots.length, 5);
});
test('token composition is disjoint and handles unavailable numeric fields', () => {
  const state = makeState(); const session = state.snapshot.sessions[0];
  assert.equal(composition(session).reduce((sum, part) => sum + part.value, 0), session.tokens);
  assert.deepEqual(composition({input: NaN, output: -1, cache_read: Infinity}).map(part => part.value), [0, 0, 0, 0]);
  assert.equal(compact(3322000), '3.32M'); assert.equal(money(.001), '<$0.01'); assert.equal(money(0), '$0.00');
});
test('Cursor editor rows and summary distinguish unknown, partial and unavailable usage', () => {
  const unavailable = sessionUsageDisplay({source: 'cursor', usage_status: 'unavailable', cost_status: 'unavailable', tokens: 0, cost: 0});
  assert.equal(unavailable.tokens, '—');
  assert.equal(unavailable.cost, 'Unavailable');
  assert.equal(unavailable.tokenLabel, 'unavailable');
  assert.match(unavailable.note, /not a known zero/);

  const partial = sessionUsageDisplay({source: 'cursor', usage_status: 'partial', cost_status: 'partial', tokens: 240, cost: .001});
  assert.equal(partial.tokens, '240');
  assert.equal(partial.cost, 'Partial · <$0.01');
  assert.equal(partial.tokenLabel, 'partial tokens');
  assert.match(partial.note, /recorded values only/);

  const unpriced = sessionUsageDisplay({source: 'cursor', usage_status: 'recorded', cost_status: 'unavailable', tokens: 240, cost: 0});
  assert.equal(unpriced.tokens, '240');
  assert.equal(unpriced.cost, 'Unavailable');

  const legacy = sessionUsageDisplay({source: 'cursor', tokens: 0, cost: 0});
  assert.equal(legacy.usage, 'unknown');
  assert.equal(legacy.tokens, '—');
  assert.equal(legacy.cost, 'Not recorded');
  assert.match(legacy.note, /older session entry/);

  const mixed = summaryUsageDisplay({usage_status: 'partial', cost_status: 'partial', tokens: 200, cost: .002});
  assert.equal(mixed.tokens, '200');
  assert.equal(mixed.cost, 'Partial · <$0.01');
  assert.match(mixed.note, /lower bounds/);
  const onlyMetadata = summaryUsageDisplay({usage_status: 'unavailable', cost_status: 'unavailable', tokens: 0, cost: 0});
  assert.equal(onlyMetadata.tokens, '—');
  assert.equal(onlyMetadata.cost, 'Unavailable');

  assert.equal(modelDisplay({source: 'cursor', model: 'Unknown', model_status: 'unavailable', current_model: 'Last chosen model'}), 'Unavailable');
  assert.equal(contextSnapshot({current_context_tokens: 12000, current_context_percent: 24,
    current_context_limit: 50000, current_context_asof: '2026-10-25T11:45:00Z'}), '12K used · 24% · 50K limit · as of Oct 25');
  const fields = composition({source: 'cursor', usage_status: 'partial', input: 40, output: 15, cache_read: 0, cache_write: 0,
    usage_availability: {input_output: 'partial', cache_read: 'unavailable', cache_write: 'unavailable'}});
  assert.deepEqual(fields.map(part => part.status), ['partial', 'partial', 'unavailable', 'unavailable']);
  assert.deepEqual(fields.map(part => part.value), [40, 15, 0, 0]);
});
test('shell offers exact session controls, accessible scope/range and truthful onboarding', () => {
  const html = getHtml(webview, uri, nonce);
  for (const label of ['All sessions', 'Main agents', 'Subagents', 'Project scope', 'Date range', 'Selected session']) assert.ok(html.includes(label));
  assert.doesNotMatch(html, /Other sessions|currently running workers|live agents/i);
  assert.match(html, /role="tree"/);
  assert.match(html, /estimated API cost/);
  assert.match(html, /already includes it/);
});
test('recorded effort uses the app labels, including Ultra and partial evidence', () => {
  assert.equal(effortLabel('xhigh'), 'Extra high');
  assert.equal(effortLabel('ultra'), 'Ultra');
  assert.equal(['ultra', 'unrecorded'].map(effortLabel).join(' · '), 'Ultra · Not recorded');
  assert.equal(effortLabel(null), 'Not recorded');
});
test('Antigravity sessions use their own source label in the editor UI', () => {
  assert.equal(sourceLabel('antigravity'), 'Antigravity');
  assert.equal(sourceLabel('gemini'), 'gemini', 'Model/provider names do not replace the tool source');
});
test('webview uses text DOM, exact message actions and no telemetry storage', () => {
  const js = fs.readFileSync(path.join(__dirname, '../media/desk.js'), 'utf8');
  assert.doesNotMatch(js, /\.innerHTML|insertAdjacentHTML|\beval\(|new Function|\bfetch\(|XMLHttpRequest|localStorage|sessionStorage/);
  assert.match(js, /textContent/);
  assert.match(js, /ArrowDown/); assert.match(js, /aria-expanded/); assert.match(js, /preventScroll/);
  assert.match(js, /Start local monitoring/);
  assert.match(js, /sessionFilter/);
  assert.match(js, /Current composer setting · whole session/);
  assert.match(js, /Context is a saved snapshot, not billed tokens/);
});
test('preview scenarios are fake, exercise partial and stale evidence, and escape injected text', () => {
  assert.equal(scenario('stale').connection.status, 'offline');
  assert.equal(scenario('restricted').trusted, false);
  assert.equal(scenario('remote').scope, 'all');
  assert.equal(scenario('empty').snapshot.sessions.length, 0);
  const html = page('light', 'long');
  assert.match(html, /vscode-light/);
  assert.doesNotMatch(html, /<script>Local text only/);
  assert.match(html, /\\u003cscript>/);
});
test('fixture preview serves only an explicit allowlist', async () => {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const endpoint = 'http://127.0.0.1:' + server.address().port;
    assert.equal((await fetch(endpoint + '/')).status, 200);
    assert.equal((await fetch(endpoint + '/media/app-icon.png')).status, 200);
    assert.equal((await fetch(endpoint + '/api/data')).status, 404);
    assert.equal((await fetch(endpoint + '/%2e%2e/parser.py')).status, 404);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
