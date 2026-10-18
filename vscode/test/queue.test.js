'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {Monitor} = require('../src/host');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const health = {ready: true, version: '2.0.0'};
function snapshot(tokens, revision, scope = 'all') {
  return {schema: 1, revision, date: '2026-10-18', from: '2026-10-18', to: '2026-10-18',
    scope: {kind: scope, label: 'Mock projects'}, summary: {tokens, input: tokens, output: 0,
      cache_read: 0, cache_write: 0, cost: tokens / 100, sessions: 1, main_agents: 1, subagents: 0, compactions: 0},
    sessions: [{key: 'mock-session', tokens, cost: tokens / 100}], days: [], models: []};
}

test('outage retries settle each attempt without retaining earlier polling promises', async t => {
  const attempts = [];
  const client = {endpoint: 'http://127.0.0.1:7878', health() {const request = deferred(); attempts.push(request); return request.promise;}};
  const monitor = new Monitor({client}); t.after(() => monitor.dispose());
  let firstSettled = false, nextSettled = false;
  const first = monitor.poll().then(() => {firstSettled = true;});
  const next = monitor.poll().then(() => {nextSettled = true;});
  attempts[0].reject(new Error('Mock outage'));
  await tick();
  assert.equal(attempts.length, 2);
  assert.equal(firstSettled, true, 'the first failed request settles while its retry is still waiting');
  assert.equal(nextSettled, false, 'a queued caller awaits the next actual attempt');
  let previous = next;
  for (let index = 1; index < 30; index++) {
    let pendingSettled = false;
    const pending = monitor.poll().then(() => {pendingSettled = true;});
    attempts[index].reject(new Error('Mock outage'));
    await tick();
    await previous;
    assert.equal(pendingSettled, false);
    assert.equal(attempts.length, index + 2);
    assert.equal(monitor.waiting.size, 1, 'only the next attempt retains a queued caller');
    assert.equal(monitor.waiters.length, 0);
    previous = pending;
  }
  monitor.dispose();
  await previous;
  attempts[30].resolve(health);
  await tick();
  await Promise.all([first, next]);
  assert.equal(attempts.length, 31, 'disposal does not start another queued request');
  assert.equal(monitor.waiting.size, 0);
});

test('coalesced manual refresh callers await their refresh while later timer polls remain independent', async t => {
  const firstHealth = deferred(), refreshRequest = deferred(), laterHealth = deferred();
  let healthCalls = 0, refreshCalls = 0, manualSettled = false;
  const client = {endpoint: 'http://127.0.0.1:7878',
    health() {healthCalls++; return healthCalls === 1 ? firstHealth.promise : healthCalls === 2 ? Promise.resolve(health) : laterHealth.promise;},
    async sync() {return {revision: 'service:1'};},
    async snapshot() {return snapshot(refreshCalls ? 150 : 100, 'service:1');},
    refresh() {refreshCalls++; return refreshRequest.promise;}};
  const monitor = new Monitor({client}); t.after(() => monitor.dispose());
  const initial = monitor.poll();
  const manual = monitor.poll(true).then(() => {manualSettled = true;});
  const sameManual = monitor.poll(true);
  firstHealth.resolve(health);
  await initial;
  await tick();
  assert.equal(refreshCalls, 1);
  assert.equal(manualSettled, false);
  const periodic = monitor.poll();
  refreshRequest.resolve({ok: true});
  await Promise.all([manual, sameManual]);
  assert.equal(manualSettled, true, 'manual Refresh resolves after its own figures arrive');
  assert.equal(monitor.state.snapshot.summary.tokens, 150);
  assert.equal(monitor.state.since.tokens, 50);
  assert.equal(healthCalls, 3, 'the later timer attempt can remain in flight independently');
  assert.equal(monitor.waiting.size, 1);
  monitor.dispose();
  await periodic;
  laterHealth.resolve(health);
});

test('queued generation changes discard superseded payloads and resolve with the latest scope', async t => {
  const requests = [];
  const client = {endpoint: 'http://127.0.0.1:7878', async health() {return health;},
    async sync() {return {revision: 'service:1'};},
    snapshot(scope, range) {const request = deferred(); requests.push({scope, range, ...request}); return request.promise;}};
  const monitor = new Monitor({client, roots: ['/mock/workspace']}); t.after(() => monitor.dispose());
  const old = monitor.poll(); await tick();
  const scope = monitor.setScope('all');
  const range = monitor.setRange('7d');
  requests[0].resolve(snapshot(999, 'service:1', 'workspace'));
  await old;
  await tick();
  assert.equal(monitor.state.snapshot, null, 'superseded workspace figures cannot enter current state');
  assert.deepEqual(requests.map(({scope, range}) => [scope, range]), [['workspace', 'today'], ['all', '7d']]);
  requests[1].resolve(snapshot(200, 'service:1'));
  await Promise.all([scope, range]);
  assert.equal(monitor.state.snapshot.summary.tokens, 200);
  assert.equal(monitor.state.range, '7d');
  assert.equal(monitor.state.since.tokens, 0);
  assert.equal(monitor.waiting.size, 0);
});

test('payload outage retries the same revision, preserves stale figures and recovers without leaking waiters', async t => {
  const failed = deferred(), recovered = deferred();
  let snapshotCalls = 0;
  const client = {endpoint: 'http://127.0.0.1:7878', revision: 'service:1', async health() {return health;},
    async sync() {return {revision: this.revision};},
    snapshot() {snapshotCalls++; return snapshotCalls === 1 ? Promise.resolve(snapshot(100, 'service:1')) : snapshotCalls === 2 ? failed.promise : recovered.promise;}};
  const monitor = new Monitor({client}); t.after(() => monitor.dispose());
  await monitor.poll();
  const retained = monitor.state.snapshot;
  client.revision = 'service:2';
  const failure = monitor.poll(); await tick();
  const recovery = monitor.poll();
  failed.reject(new Error('Mock response interruption'));
  await failure;
  await tick();
  assert.equal(monitor.state.snapshot, retained);
  assert.equal(monitor.state.connection.status, 'error');
  assert.match(monitor.state.connection.message, /stale/);
  assert.equal(snapshotCalls, 3);
  recovered.resolve(snapshot(175, 'service:2'));
  await recovery;
  assert.equal(monitor.state.connection.status, 'connected');
  assert.equal(monitor.state.since.tokens, 75);
  assert.equal(monitor.revision, 'service:2');
  assert.equal(monitor.waiting.size, 0);
});

test('disposal settles queued callers immediately and suppresses in-flight state changes', async () => {
  const pending = deferred(); let healthCalls = 0, emits = 0;
  const monitor = new Monitor({client: {endpoint: 'mock', health() {healthCalls++; return pending.promise;}}, onChange() {emits++;}});
  const active = monitor.poll();
  const queued = [monitor.poll(), monitor.poll(true), monitor.poll()];
  monitor.dispose();
  await Promise.all(queued);
  assert.equal(monitor.waiting.size, 0);
  assert.equal(monitor.waiters.length, 0);
  pending.resolve(health);
  await active;
  assert.equal(healthCalls, 1);
  assert.equal(emits, 0);
  assert.equal(monitor.state.snapshot, null);
});

test('an unexpected queued observer failure rejects its callers without stranded waiters', async t => {
  const firstHealth = deferred(); let calls = 0;
  const monitor = new Monitor({client: {endpoint: 'mock', health() {return ++calls === 1 ? firstHealth.promise : Promise.resolve({ready: false});}},
    onChange() {throw new Error('Mock observer failure');}});
  t.after(() => monitor.dispose());
  const initial = monitor.poll();
  const queued = monitor.poll();
  const checked = Promise.all([assert.rejects(initial, /Mock observer failure/), assert.rejects(queued, /Mock observer failure/)]);
  firstHealth.resolve({ready: false});
  await checked;
  assert.equal(calls, 2);
  assert.equal(monitor.waiting.size, 0);
  assert.equal(monitor.busy, false);
});
