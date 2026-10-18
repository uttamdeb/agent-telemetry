'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {LocalClient, requestJSON, validateSnapshot, healthIdentity} = require('../src/client');
async function server(t, handler) {
  const instance = http.createServer(handler);
  await new Promise(resolve => instance.listen(0, '127.0.0.1', resolve));
  t.after(() => {instance.closeAllConnections(); instance.close();});
  return instance.address().port;
}
test('fixed-loopback client refuses redirects without following them', async t => {
  let calls = 0;
  const port = await server(t, (_, res) => {calls++; res.writeHead(302, {location: 'https://example.invalid/'}); res.end();});
  await assert.rejects(requestJSON(port, '/api/health'), {code: 'HTTP_302'});
  assert.equal(calls, 1);
});
test('client enforces total timeout even when response never ends', async t => {
  const port = await server(t, (_, res) => {res.writeHead(200, {'content-type': 'application/json'}); res.write('{');});
  await assert.rejects(requestJSON(port, '/api/health', {timeout: 30}), {code: 'TIMEOUT'});
});
test('client rejects oversized JSON and non-JSON responses', async t => {
  const port = await server(t, (req, res) => {res.writeHead(200, {'content-type': req.url.includes('bad') ? 'text/html' : 'application/json'}); res.end(JSON.stringify('x'.repeat(100)));});
  await assert.rejects(requestJSON(port, '/api/health', {maxBytes: 32}), {code: 'SIZE'});
  await assert.rejects(requestJSON(port, '/api/bad'), {code: 'IDENTITY'});
});
test('health identity rejects unrelated service and malformed readiness', () => {
  assert.throws(() => healthIdentity({service: 'other', ready: true, version: '1'}), {code: 'IDENTITY'});
  assert.throws(() => healthIdentity({service: 'agent-telemetry', ready: 'true', version: '1'}), {code: 'IDENTITY'});
});
test('explicit refresh sends JSON with same-origin CSRF headers', async t => {
  let seen;
  const port = await server(t, (req, res) => {seen = req; req.resume(); res.writeHead(200, {'content-type': 'application/json'}); res.end('{"ok":true}');});
  await new LocalClient(port).refresh();
  assert.equal(seen.method, 'POST');
  assert.equal(seen.headers.origin, `http://127.0.0.1:${port}`);
  assert.equal(seen.headers['content-type'], 'application/json');
});
test('snapshot requests encode roots as data, never routes or executable commands', async () => {
  let route;
  const client = new LocalClient(7878, async (_, value) => {route = value; return {};});
  await assert.rejects(client.snapshot('workspace', 'today', ['/home/me/a&scope=all']));
  const query = new URL(route, 'http://127.0.0.1').searchParams;
  assert.equal(query.get('scope'), 'workspace'); assert.deepEqual(query.getAll('root'), ['/home/me/a&scope=all']);
});
test('unsupported and invalid snapshot schemas fail explicitly', () => {
  assert.throws(() => validateSnapshot({schema: 0}), {code: 'SCHEMA'});
  assert.throws(() => validateSnapshot({schema: 1, summary: {tokens: -1}, sessions: [], days: [], models: [], scope: {kind: 'all'}}), {code: 'SCHEMA'});
});
test('an older identified backend gets an actionable upgrade message without starting a competitor', async () => {
  const client = new LocalClient(7878, async () => {throw Object.assign(new Error('404'), {code: 'HTTP_404'});});
  await assert.rejects(client.snapshot('all', 'today', []), error => error.code === 'UPGRADE' && /Upgrade or restart/.test(error.message));
});
function validSnapshot() {
  return {schema: 1, generated_at: 1790000000, date: '2026-10-18', from: '2026-10-18', to: '2026-10-18', revision: 'service:15:1:0',
    scope: {kind: 'all', label: 'All projects', roots: [], matched_sessions: 1, unassigned_sessions: 0},
    summary: {tokens: 100, input: 20, output: 10, cache_read: 70, cache_write: 0, cost: 0.1, sessions: 1, main_agents: 1, subagents: 0, compactions: 0},
    days: [{date: '2026-10-18', tokens: 100, cost: 0.1}], models: [{name: 'Model', source: 'codex', tokens: 100, cost: 0.1}],
    sessions: [{key: 'session', id: 'full-id', source: 'codex', device: 'local', title: 'Session', project: 'app', model: 'Model', subagent: false, parent_key: null, parent_title: null, parent_status: 'none', efforts: ['high'], tokens: 100, input: 20, output: 10, cache_read: 70, cache_write: 0, cost: 0.1, compactions: 0, start: '2026-10-18T10:00:00Z', end: '2026-10-18T10:01:00Z', archived: false, detail_limited: false}],
    sessions_total: 1, truncated: false, limitations: ['Costs are estimates at API list prices.']};
}
test('snapshot schema strips unknown private fields and preserves typed optional metadata', () => {
  const fixture = validSnapshot(); fixture.private_metadata = {account: 'do not forward'}; fixture.sessions[0].prompt = 'do not forward';
  const value = validateSnapshot(fixture);
  assert.equal(Object.hasOwn(value, 'private_metadata'), false); assert.equal(Object.hasOwn(value.sessions[0], 'prompt'), false);
  assert.equal(value.sessions[0].end, '2026-10-18T10:01:00Z');
  delete fixture.sessions[0].end; assert.equal(validateSnapshot(fixture).sessions[0].end, null);
});
test('Copilot inclusive child usage and unavailable status survive validation', () => {
  const fixture = validSnapshot();
  Object.assign(fixture.sessions[0], {source: 'copilot', usage_in_parent: true, usage_status: 'unavailable'});
  const child = validateSnapshot(fixture).sessions[0];
  assert.equal(child.usage_in_parent, true); assert.equal(child.usage_status, 'unavailable');
  fixture.sessions[0].usage_status = 'made-up';
  assert.throws(() => validateSnapshot(fixture), {code: 'SCHEMA'});
});
test('all rendered snapshot fields reject malformed nested types without TypeError', () => {
  const mutations = [
    value => {value.sessions[0].end = {};}, value => {value.sessions[0].title = [];}, value => {value.sessions[0].efforts = [{}];},
    value => {value.sessions[0].subagent = 'false';}, value => {value.sessions[0].parent_status = {};}, value => {value.sessions[0].parent_key = {};},
    value => {value.sessions[0].model = {};}, value => {value.sessions[0].input = '20';}, value => {value.sessions[0].archived = {};},
    value => {value.days[0].date = {};}, value => {value.days[0].cost = NaN;}, value => {value.models[0].name = {};}, value => {value.models[0].tokens = -1;},
    value => {value.scope.label = {};}, value => {value.scope.roots = [{}];}, value => {value.scope.unassigned_sessions = -1;},
    value => {value.limitations = [{}];}, value => {value.from = '2026-02-30';}, value => {value.generated_at = -1;},
    value => {value.sessions_total = 0;}, value => {value.truncated = {};}, value => {value.revision = 'x'.repeat(513);}
  ];
  for (const mutate of mutations) {const fixture = validSnapshot(); mutate(fixture); assert.throws(() => validateSnapshot(fixture), error => error.name === 'ServiceError' && error.code === 'SCHEMA');}
});
for (const [status, type, code] of [[302, 'application/json', 'HTTP_302'], [200, 'text/html', 'IDENTITY']]) {
  test('rejected response headers close an unfinished HTTP socket: ' + code, async t => {
    let closed; const socketClosed = new Promise(resolve => {closed = resolve;});
    const port = await server(t, (req, res) => {req.socket.once('close', closed); res.writeHead(status, {'content-type': type, location: '/elsewhere'}); res.write('never-ending');});
    await assert.rejects(requestJSON(port, '/api/health', {timeout: 30}), {code});
    const result = await Promise.race([socketClosed.then(() => true), new Promise(resolve => setTimeout(() => resolve(false), 100))]);
    assert.equal(result, true, 'rejected local response must not leave an unbounded draining socket');
  });
}
