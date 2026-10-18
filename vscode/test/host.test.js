'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {Monitor, validMessage} = require('../src/host');
function snapshot(tokens = 100, revision = '1', overrides = {}) {
  return {schema: 1, revision: revision.includes(':') ? revision : 'service:' + revision, date: '2026-10-18', from: '2026-10-18', to: '2026-10-18', scope: {kind: 'all', label: 'All projects'}, summary: {tokens, cost: tokens / 100, input: tokens, output: 0, cache_read: 0, cache_write: 0, sessions: 1, main_agents: 1, subagents: 0, compactions: 0}, sessions: [{key: 'a', tokens, cost: tokens / 100}], days: [], models: [], ...overrides};
}
function fakeClient() {
  return {endpoint: 'http://127.0.0.1:7878', revision: 'service:1', calls: [], value: snapshot(),
    async health() {this.calls.push('health'); return {ready: true, version: '2.0.0'};},
    async sync() {this.calls.push('sync'); return {revision: this.revision};},
    async snapshot() {this.calls.push('snapshot'); return this.value;},
    async refresh() {this.calls.push('refresh');}};
}
test('poll identifies service before clock and usage; unchanged revision skips usage', async () => {
  const client = fakeClient(); const monitor = new Monitor({client});
  await monitor.poll(); await monitor.poll();
  assert.deepEqual(client.calls, ['health', 'sync', 'snapshot', 'health', 'sync']);
  assert.equal(monitor.state.connection.status, 'connected');
});
test('failed snapshot retries same revision and preserves stale last success', async () => {
  const client = fakeClient(); const monitor = new Monitor({client});
  await monitor.poll(); const good = monitor.state.snapshot;
  client.revision = 'service:2'; let fail = true;
  client.snapshot = async () => {if (fail) throw new Error('unavailable'); return snapshot(150, '2');};
  await monitor.poll(); assert.equal(monitor.state.snapshot, good); assert.equal(monitor.state.connection.status, 'error'); assert.match(monitor.state.connection.message, /stale/);
  fail = false; await monitor.poll(); assert.equal(monitor.state.snapshot.summary.tokens, 150); assert.equal(monitor.state.since.tokens, 50);
});
test('scope generation rejects an in-flight snapshot and requests current scope', async () => {
  const client = fakeClient(); let resolveSnapshot; let calls = 0;
  client.snapshot = async scope => {calls++; if (calls === 1) return new Promise(resolve => {resolveSnapshot = resolve;}); return snapshot(200, '2', {scope: {kind: scope}});};
  const monitor = new Monitor({client, roots: ['/work/project']}); const pending = monitor.poll();
  while (!resolveSnapshot) await new Promise(resolve => setImmediate(resolve));
  monitor.setScope('all'); resolveSnapshot(snapshot(999, '1', {scope: {kind: 'workspace'}})); await pending;
  while (monitor.busy) await new Promise(resolve => setImmediate(resolve));
  assert.equal(monitor.state.scope, 'all'); assert.equal(monitor.state.snapshot.summary.tokens, 200);
});
test('decreasing counters and changed dates reset the usage baseline', async () => {
  const client = fakeClient(); const monitor = new Monitor({client}); await monitor.poll();
  client.revision = 'service:2'; client.value = snapshot(150, '2'); await monitor.poll(); assert.equal(monitor.state.since.tokens, 50);
  client.revision = 'service:3'; client.value = snapshot(120, '3'); await monitor.poll(); assert.equal(monitor.state.since.tokens, 0); assert.equal(monitor.state.since.reset, true);
  client.revision = 'service:4'; client.value = snapshot(150, '4', {date: '2026-10-19'}); await monitor.poll(); assert.equal(monitor.state.since.tokens, 0); assert.equal(monitor.state.since.reset, true);
});
test('range changes clear old scoped figures and reset baseline', async () => {
  const client = fakeClient(); const monitor = new Monitor({client}); await monitor.poll();
  await monitor.setRange('7d'); assert.equal(monitor.state.range, '7d'); assert.equal(monitor.state.since.tokens, 0);
});
test('remote workspace never requests or enables local-path workspace scope', async () => {
  const client = fakeClient(); let scope;
  client.snapshot = async value => {scope = value; return snapshot();};
  const monitor = new Monitor({client, roots: ['/remote/project'], remote: true});
  await monitor.setScope('workspace'); await monitor.poll();
  assert.equal(scope, 'all'); assert.match(monitor.state.connection.message, /remote/);
});
test('health failure cannot query analytics and disposing invalidates awaited results', async () => {
  const client = fakeClient(); client.health = async () => {throw Object.assign(new Error('foreign'), {code: 'IDENTITY'});};
  const monitor = new Monitor({client}); await monitor.poll(); assert.deepEqual(client.calls, []); assert.equal(monitor.state.connection.status, 'error');
  let done; client.health = () => new Promise(resolve => {done = resolve;});
  const pending = monitor.poll(); monitor.dispose(); done({ready: true, version: '2'}); await pending;
  assert.deepEqual(client.calls, []);
});
test('manual refresh is serialized and uses only explicit refresh request', async () => {
  const client = fakeClient(); const monitor = new Monitor({client});
  await monitor.poll(true); assert.deepEqual(client.calls, ['health', 'refresh', 'sync', 'snapshot']);
});
test('strict webview allowlist rejects extra keys, unknown actions and forged session ids', () => {
  assert.equal(validMessage({type: 'start', command: 'evil'}, snapshot()), false);
  assert.equal(validMessage({type: 'executeCommand'}, snapshot()), false);
  assert.equal(validMessage({type: 'changeScope', value: 'remote'}, snapshot()), false);
  assert.equal(validMessage({type: 'selectSession', value: 'unknown'}, snapshot()), false);
  assert.equal(validMessage({type: 'selectSession', value: 'a'}, snapshot()), true);
  assert.equal(validMessage({type: 'changeRange', value: '7d'}, snapshot()), true);
});

test('shared service identity reset does not become newly recorded usage', async () => {
  const client = fakeClient(); const monitor = new Monitor({client}); await monitor.poll();
  client.revision = 'other-service:2'; client.value = snapshot(200, 'other-service:2'); await monitor.poll();
  assert.equal(monitor.state.since.tokens, 0); assert.equal(monitor.state.since.reset, true);
});
