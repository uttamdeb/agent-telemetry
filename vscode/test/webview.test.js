'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {getHtml} = require('../src/webview');
const {compact, money, forest, composition} = require('../media/desk');
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
test('shell offers exact session controls, accessible scope/range and truthful onboarding', () => {
  const html = getHtml(webview, uri, nonce);
  for (const label of ['All sessions', 'Main agents', 'Subagents', 'Project scope', 'Date range', 'Selected session']) assert.ok(html.includes(label));
  assert.doesNotMatch(html, /Other sessions|currently running workers|live agents/i);
  assert.match(html, /role="tree"/);
  assert.match(html, /estimated API cost/);
  assert.match(html, /already includes it/);
});
test('webview uses text DOM, exact message actions and no telemetry storage', () => {
  const js = fs.readFileSync(path.join(__dirname, '../media/desk.js'), 'utf8');
  assert.doesNotMatch(js, /\.innerHTML|insertAdjacentHTML|\beval\(|new Function|\bfetch\(|XMLHttpRequest|localStorage|sessionStorage/);
  assert.match(js, /textContent/);
  assert.match(js, /ArrowDown/); assert.match(js, /aria-expanded/); assert.match(js, /preventScroll/);
  assert.match(js, /Start local monitoring/);
  assert.match(js, /sessionFilter/);
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
