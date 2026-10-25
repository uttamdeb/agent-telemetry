'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {Monitor} = require('../src/host');
function harness({trusted = false, remote = false, workspacePath = '/work/example', connected = false, summary} = {}) {
  const handlers = new Map(); const notices = []; let starts = 0; let selectedPort; let panel; let receives; let requestedRoots;
  let statusItem;
  const dispose = () => {};
  class EventEmitter { constructor() {this.event = () => ({dispose});} fire() {} dispose() {} }
  const vscode = {
    EventEmitter, StatusBarAlignment: {Right: 2}, ViewColumn: {Active: 1}, TreeItemCollapsibleState: {None: 0, Collapsed: 1},
    TreeItem: class {constructor(label) {this.label = label;}}, ThemeIcon: class {constructor(id) {this.id = id;}},
    Uri: {joinPath: (_, part) => ({fsPath: '/extension/' + part}), parse: value => value},
    env: {appName: 'Cursor', remoteName: remote ? 'ssh-remote' : undefined, openExternal: async () => true},
    workspace: {isTrusted: trusted, workspaceFolders: [{uri: {scheme: remote ? 'vscode-remote' : 'file', fsPath: workspacePath}}],
      getConfiguration: () => ({inspect: name => ({globalValue: name === 'port' ? 7878 : undefined, workspaceValue: name === 'port' ? 9999 : '/work/python', defaultValue: name === 'showStatusBar' ? true : ''})}),
      onDidChangeWorkspaceFolders: () => ({dispose}), onDidGrantWorkspaceTrust: () => ({dispose}), onDidChangeConfiguration: () => ({dispose})},
    commands: {registerCommand: (name, handler) => {handlers.set(name, handler); return {dispose};}, executeCommand: async () => {}},
    window: {createStatusBarItem: () => (statusItem = {show() {}, hide() {}, dispose}), createTreeView: () => ({dispose}),
      showWarningMessage: async message => {notices.push(message);}, showInformationMessage: async () => undefined,
      createWebviewPanel: () => {panel = {webview: {postMessage: async () => {}, onDidReceiveMessage: callback => {receives = callback; return {dispose};}}, reveal() {}, onDidDispose: () => ({dispose}), dispose}; return panel;}}
  };
  class LocalClient {
    constructor(port) {this.port = port; selectedPort = port; this.endpoint = `http://127.0.0.1:${port}`;}
    async health() {if (connected) return {ready: true, version: '2.0.0'}; throw Object.assign(new Error('Offline'), {code: 'ECONNREFUSED'});}
    async sync() {return {revision: 'service:1'};}
    async refresh() {return {};}
    async snapshot(scope, range, roots) {requestedRoots = [...roots]; return {revision: 'service:1', date: '2026-10-18', from: '2026-10-18', to: '2026-10-18', scope: {kind: scope, label: 'Fixture'}, summary: {tokens: 0, cost: 0, input: 0, output: 0, cache_read: 0, cache_write: 0, sessions: 0, main_agents: 0, subagents: 0, compactions: 0, ...(summary || {})}, sessions: []};}
  }
  const module = {exports: {}};
  const customRequire = name => name === 'vscode' ? vscode : name === './client' ? {LocalClient} : name === './session-forest' ? require('../src/session-forest') : name === './host' ? {Monitor, validMessage: require('../src/host').validMessage} : name === './runtime' ? {startBackend: async () => {starts++;}} : name === './webview' ? {getHtml: (_, __, nonce) => {assert.match(nonce, /^[A-Za-z0-9_-]{16,128}$/); return '<!doctype html>';}} : require(name);
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/extension.js'), 'utf8'), {require: customRequire, module, exports: module.exports, setInterval: () => 1, clearInterval: () => {}, structuredClone, Intl, console});
  const context = {subscriptions: [], extensionUri: {fsPath: '/extension'}, extensionPath: '/extension', globalState: {get: () => false, update: async () => {}}};
  const api = module.exports.activate(context);
  return {requestedRoots: () => requestedRoots, ActivityTree: module.exports.ActivityTree, api, handlers, notices, receives: () => receives, starts: () => starts, selectedPort, statusItem: () => statusItem, cleanup: () => {module.exports.deactivate(); for (const subscription of context.subscriptions.reverse()) subscription.dispose();}};
}
test('restricted workspace cannot execute Python and cannot override application settings', async t => {
  const host = harness(); t.after(host.cleanup);
  assert.equal(host.selectedPort, 7878);
  await host.handlers.get('agentTelemetry.start')();
  assert.equal(host.starts(), 0); assert.match(host.notices[0], /Trust this workspace/);
});
test('editor identity follows compatible host and remote paths never become local workspace roots', async t => {
  const host = harness({remote: true}); t.after(host.cleanup);
  const state = host.api.getState(); assert.equal(state.editor_name, 'Cursor'); assert.equal(state.scope, 'all'); assert.equal(state.workspace_available, false);
  state.scope = 'workspace'; assert.equal(host.api.getState().scope, 'all');
  await host.api.setScope('workspace'); assert.equal(host.api.getState().scope, 'all');
});
test('Agent desk uses a CSP-compatible nonce and rejects arbitrary webview commands', async t => {
  const host = harness(); t.after(host.cleanup);
  host.handlers.get('agentTelemetry.open')();
  host.receives()({type: 'start', shell: 'workspace-code'});
  assert.equal(host.starts(), 0); assert.deepEqual(host.notices, []);
});

for (const mixed of [false, true]) test('native tree keeps cycle sessions visible exactly once' + (mixed ? ' beside ordinary roots' : ''), t => {
  const host = harness(); t.after(host.cleanup); const tree = new host.ActivityTree();
  const sessions = [{key: 'a', parent_key: 'b', parent_status: 'linked', title: 'Agent A', tokens: 1, cost: 0}, {key: 'b', parent_key: 'a', parent_status: 'linked', title: 'Agent B', tokens: 1, cost: 0}];
  if (mixed) sessions.push({key: 'c', parent_key: null, parent_status: 'none', title: 'Agent C', tokens: 1, cost: 0});
  tree.update({...host.api.getState(), connection: {status: 'connected', endpoint: 'http://127.0.0.1:7878'}, snapshot: {sessions, summary: {tokens: 3, cost: 0}, scope: {label: 'All projects'}, from: '2026-10-18', to: '2026-10-18'}});
  const seen = [];
  const visit = rows => {for (const row of rows) {if (row.session) seen.push(tree.getTreeItem(row).id); visit(tree.getChildren(row));}};
  visit(tree.getChildren()); assert.equal(new Set(seen).size, seen.length); assert.equal(seen.length, sessions.length);
});
test('identified older backend offers upgrade instead of starting a competing monitor', t => {
  const host = harness(); t.after(host.cleanup); const tree = new host.ActivityTree();
  tree.update({...host.api.getState(), connection: {status: 'error', code: 'UPGRADE', endpoint: 'http://127.0.0.1:7878', message: 'Upgrade the running backend.'}});
  const rows = tree.getChildren();
  assert.equal(rows[0].label, 'Backend upgrade needed');
  assert.equal(rows.some(row => row.action === 'start'), false);
  assert.equal(rows.find(row => row.id === 'setup').action, 'openBrowser');
});

test('workspace attribution requests both lexical and canonical local symlink roots', async t => {
  const os = require('node:os'); const base = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'at-workspace-roots-'));
  t.after(() => fs.promises.rm(base, {recursive: true, force: true}));
  const real = path.join(base, 'actual-project'); const alias = path.join(base, 'editor-project');
  await fs.promises.mkdir(real); await fs.promises.symlink(real, alias, process.platform === 'win32' ? 'junction' : 'dir');
  const host = harness({workspacePath: alias, connected: true}); t.after(host.cleanup);
  await host.api.refresh();
  assert.deepEqual(new Set(host.requestedRoots()), new Set([alias, fs.realpathSync(alias)]));
  assert.equal(host.api.getState().scope, 'workspace');
});

test('status bar marks Cursor-only usage and cost as unavailable instead of zero', async t => {
  const host = harness({connected: true, summary: {usage_status: 'unavailable', cost_status: 'unavailable'}}); t.after(host.cleanup);
  await host.api.refresh();
  assert.match(host.statusItem().text, /Unavailable/);
  assert.doesNotMatch(host.statusItem().text, /0\.00|\b0\b/);
  assert.match(host.statusItem().tooltip, /Token usage unavailable/);
  assert.match(host.statusItem().tooltip, /Estimated API cost unavailable/);
  assert.match(host.statusItem().tooltip, /not a known zero/);
});

test('status bar labels mixed recorded totals as partial lower bounds', async t => {
  const host = harness({connected: true, summary: {tokens: 1240, cost: .125, usage_status: 'partial', cost_status: 'partial'}}); t.after(host.cleanup);
  await host.api.refresh();
  assert.match(host.statusItem().text, /Partial/);
  assert.match(host.statusItem().text, /1\.2K/);
  assert.match(host.statusItem().tooltip, /1,240 recorded tokens · partial coverage/);
  assert.match(host.statusItem().tooltip, /lower bounds/);
  assert.match(host.statusItem().tooltip, /partial estimate/);
});

test('status bar keeps the existing numeric labels for summaries without coverage fields', async t => {
  const host = harness({connected: true, summary: {tokens: 480, cost: .25}}); t.after(host.cleanup);
  await host.api.refresh();
  assert.equal(host.statusItem().text, '$(pulse) 480 · $0.25');
  assert.match(host.statusItem().tooltip, /480 tokens · \$0\.25 estimated API cost/);
  assert.doesNotMatch(host.statusItem().tooltip, /partial|unavailable|lower bound/i);
});
