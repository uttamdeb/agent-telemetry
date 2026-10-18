'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const {candidates, ledgerDirectory, safeEnvironment, startBackend, acquireStartLock} = require('../src/runtime');
test('shared ledger follows native OS support location rather than workspace or extension storage', () => {
  assert.equal(ledgerDirectory('darwin', {}, '/home/user'), path.join('/home/user', 'Library', 'Application Support', 'AgentTelemetry'));
  assert.equal(ledgerDirectory('linux', {XDG_STATE_HOME: '/state'}, '/home/user'), path.join('/state', 'AgentTelemetry'));
});
test('interpreter discovery refuses relative and workspace executable settings and PATH entries', async t => {
  const base = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'at-python-path-')); t.after(() => fs.promises.rm(base, {recursive: true, force: true}));
  const workspace = path.join(base, 'workspace'); const trusted = path.join(base, 'trusted-bin');
  await fs.promises.mkdir(workspace); await fs.promises.mkdir(trusted);
  assert.throws(() => candidates('./python3', []), {code: 'PYTHON'});
  assert.throws(() => candidates(path.join(workspace, 'python3'), [workspace]), {code: 'PYTHON'});
  const found = candidates('', [workspace], {platform: process.platform, home: base, env: {PATH: ['.', workspace, trusted].join(path.delimiter)}});
  const real = fs.realpathSync(trusted);
  assert.deepEqual(found.filter(candidate => candidate.startsWith(real + path.sep)), (process.platform === 'win32' ? ['python.exe', 'python3.exe'] : ['python3', 'python']).map(name => path.join(real, name)));
  assert.equal(found.some(candidate => candidate.startsWith(workspace + path.sep)), false);
});
test('Python environment cannot inherit Python injection paths or native shutdown capability', () => {
  const system = path.dirname(process.execPath);
  assert.deepEqual(safeEnvironment({PATH: system, PYTHONPATH: '/workspace', PYTHONHOME: '/bad', AGENT_TELEMETRY_CONTROL_TOKEN: 'private'}), {PATH: fs.realpathSync(system), PYTHONNOUSERSITE: '1'});
});
test('shared start lock serializes editor launches without stopping processes', async t => {
  const base = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'at-lock-test-')); t.after(() => fs.promises.rm(base, {recursive: true, force: true}));
  const release = await acquireStartLock(base); await assert.rejects(acquireStartLock(base), {code: 'STARTING'}); await release(); const next = await acquireStartLock(base); await next();
});
test('start refuses a nonstandard port before discovering or executing Python', async () => {
  await assert.rejects(startBackend({client: {port: 9999}, extensionPath: '/not-present'}), {code: 'CONFIG'});
});
test('isolated Python bootstrap points to bundled files and keeps paths as argv data', () => {
  const {launchArguments} = require('../src/runtime');
  const root = path.join(os.tmpdir(), 'Agent Telemetry & files');
  const argv = launchArguments(root);
  assert.deepEqual(argv.slice(0, 2), ['-I', '-c']);
  assert.equal(argv[3], root);
  assert.equal(argv.includes('--rebuild'), false);
  assert.equal(argv[2].includes(root), false);
  assert.equal(argv[argv.indexOf('--host') + 1], '127.0.0.1');
  assert.equal(argv[argv.indexOf('--port') + 1], '7878');
});
test('closing an older launch lock cannot delete a newer owner marker', async t => {
  const base = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'at-lock-generation-')); t.after(() => fs.promises.rm(base, {recursive: true, force: true}));
  const oldRelease = await acquireStartLock(base);
  await fs.promises.unlink(path.join(base, 'editor-start.lock'));
  const newRelease = await acquireStartLock(base);
  await oldRelease();
  assert.equal(fs.existsSync(path.join(base, 'editor-start.lock')), true);
  await newRelease();
});
test('backend PATH rejects relative entries, lexical workspace bins and aliases into symlinked workspaces', async t => {
  const base = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'at-path-security-')); t.after(() => fs.promises.rm(base, {recursive: true, force: true}));
  const workspace = path.join(base, 'workspace'); const bin = path.join(workspace, 'bin'); const trusted = path.join(base, 'trusted-bin');
  await fs.promises.mkdir(bin, {recursive: true}); await fs.promises.mkdir(trusted);
  const workspaceAlias = path.join(base, 'workspace-alias'); const outsideAlias = path.join(base, 'outside-bin-alias');
  await fs.promises.symlink(workspace, workspaceAlias, process.platform === 'win32' ? 'junction' : 'dir');
  await fs.promises.symlink(bin, outsideAlias, process.platform === 'win32' ? 'junction' : 'dir');
  const inherited = ['', '.', 'relative-bin', bin, path.join(workspaceAlias, 'bin'), outsideAlias, trusted].join(path.delimiter);
  const environment = safeEnvironment({PATH: inherited, NODE_OPTIONS: '--require=workspace-hook', NODE_PATH: bin, ELECTRON_RUN_AS_NODE: '1'}, [workspaceAlias]);
  assert.equal(environment.PATH, fs.realpathSync(trusted));
  assert.equal(Object.hasOwn(environment, 'NODE_OPTIONS'), false);
  assert.equal(Object.hasOwn(environment, 'NODE_PATH'), false);
  assert.equal(Object.hasOwn(environment, 'ELECTRON_RUN_AS_NODE'), false);
  const discovered = candidates('', [workspaceAlias], {platform: process.platform, home: base, env: {PATH: inherited}});
  assert.deepEqual(discovered.filter(candidate => candidate.startsWith(fs.realpathSync(trusted) + path.sep)), (process.platform === 'win32' ? ['python.exe', 'python3.exe'] : ['python3', 'python']).map(name => path.join(fs.realpathSync(trusted), name)));
  assert.equal(discovered.some(candidate => candidate.startsWith(workspace + path.sep) || candidate.startsWith(outsideAlias + path.sep)), false);
});
test('backend child helper discovery cannot execute a workspace binary inherited through PATH', {skip: process.platform === 'win32' ? 'POSIX executable fixture' : false}, async t => {
  const base = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'at-helper-security-')); t.after(() => fs.promises.rm(base, {recursive: true, force: true}));
  const workspace = path.join(base, 'workspace'); const unsafeBin = path.join(workspace, 'bin'); const safeBin = path.join(base, 'safe-bin');
  await fs.promises.mkdir(unsafeBin, {recursive: true}); await fs.promises.mkdir(safeBin);
  await fs.promises.writeFile(path.join(unsafeBin, 'at-test-helper'), '#!/bin/sh\nprintf unsafe', {mode: 0o700});
  await fs.promises.writeFile(path.join(safeBin, 'at-test-helper'), '#!/bin/sh\nprintf trusted', {mode: 0o700});
  const alias = path.join(base, 'helper-alias'); await fs.promises.symlink(unsafeBin, alias, 'dir');
  const environment = safeEnvironment({PATH: [unsafeBin, alias, safeBin].join(path.delimiter)}, [workspace]);
  const output = require('node:child_process').execFileSync('at-test-helper', [], {env: environment, encoding: 'utf8'});
  assert.equal(output, 'trusted');
});
for (const revokeAt of [1, 2]) test('startup cancellation during health await prevents later Python execution at stage ' + revokeAt, async t => {
  const base = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'at-runtime-cancel-')); t.after(() => fs.promises.rm(base, {recursive: true, force: true}));
  const backend = path.join(base, 'extension', 'backend'); await fs.promises.mkdir(backend, {recursive: true}); await fs.promises.writeFile(path.join(backend, 'dashboard.py'), '# mock bundled backend\n');
  let probes = 0; let spawns = 0; let allowed = true; let healthCalls = 0;
  const mockedChildProcess = {execFile(_, __, ___, callback) {probes++; callback(null, '[3,13,0]');}, spawn() {spawns++; throw new Error('A cancelled startup must never spawn');}};
  const module = {exports: {}};
  const mockedProcess = {platform: process.platform, env: {...process.env, LOCALAPPDATA: base, XDG_STATE_HOME: base}};
  const customRequire = name => name === 'node:os' ? {...os, homedir: () => base} : name === 'node:child_process' ? mockedChildProcess : name === './client' ? require('../src/client') : require(name);
  require('node:vm').runInNewContext(fs.readFileSync(path.join(__dirname, '../src/runtime.js'), 'utf8'), {require: customRequire, module, exports: module.exports, process: mockedProcess, setTimeout, clearTimeout});
  const client = {port: 7878, async health() {healthCalls++; await Promise.resolve(); if (healthCalls === revokeAt) allowed = false; throw Object.assign(new Error('not running'), {code: 'ECONNREFUSED'});}};
  await assert.rejects(module.exports.startBackend({client, extensionPath: path.join(base, 'extension'), configuredPython: process.execPath, stillAllowed: () => allowed}), {code: 'TRUST'});
  assert.equal(probes, revokeAt === 1 ? 0 : 1); assert.equal(spawns, 0);
});
