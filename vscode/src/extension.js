'use strict';
const vscode = require('vscode');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {LocalClient} = require('./client');
const {Monitor, validMessage} = require('./host');
const {startBackend} = require('./runtime');
const {getHtml} = require('./webview');
const {forest} = require('./session-forest');
let active;
const COMMAND = 'agentTelemetry.';
const compact = value => Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1}).format(value || 0);
const money = value => Intl.NumberFormat(undefined, {style: 'currency', currency: 'USD', maximumFractionDigits: 2}).format(value || 0);
function localRoots() {
  if (vscode.env.remoteName) return [];
  const roots = [];
  for (const folder of vscode.workspace.workspaceFolders || []) {
    if (folder.uri.scheme !== 'file') continue;
    roots.push(folder.uri.fsPath);
    // Log cwd and editor paths can use different spellings of a local symlink
    // (notably /var vs /private/var on macOS). Both are verified local roots.
    try { roots.push(fs.realpathSync(folder.uri.fsPath)); } catch (_) {}
  }
  return [...new Set(roots)].slice(0, 20);
}
function appSetting(name, fallback) {
  const value = vscode.workspace.getConfiguration('agentTelemetry').inspect(name);
  return value?.globalValue ?? value?.defaultValue ?? fallback;
}
class ActivityTree {
  constructor(open) { this.changed = new vscode.EventEmitter(); this.onDidChangeTreeData = this.changed.event; this.open = open; this.state = null; }
  update(state) { this.state = state; this.changed.fire(); }
  getTreeItem(row) {
    const item = new vscode.TreeItem(row.label, row.children?.length ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    item.id = row.id; item.description = row.description; item.tooltip = row.tooltip || row.label;
    item.iconPath = new vscode.ThemeIcon(row.icon || 'circle-outline');
    item.command = {command: COMMAND + (row.action || 'open'), title: row.action === 'start' ? 'Start monitoring' : 'Open Agent desk', arguments: row.session ? [row.session] : []};
    item.accessibilityInformation = {label: `${row.label}${row.description ? ', ' + row.description : ''}`};
    return item;
  }
  getChildren(row) {
    if (row) return row.children || [];
    const state = this.state; if (!state) return [];
    const connected = state.connection.status === 'connected';
    const rows = [{id: 'connection', label: state.connection.code === 'UPGRADE' ? 'Backend upgrade needed' : connected ? 'Connected locally' : state.connection.status === 'starting' ? 'Reading local logs…' : state.snapshot ? 'Disconnected · stale figures' : 'Local monitoring is offline', description: state.connection.endpoint, tooltip: state.connection.message || state.connection.endpoint, icon: connected ? 'plug' : 'warning', action: state.connection.status === 'offline' ? 'start' : 'open'}];
    if (state.remote) rows.push({id: 'remote', label: 'This computer only', description: 'Remote workspace excluded', icon: 'remote', tooltip: 'The extension runs locally. Remote workspaces and remote devices are not included.'});
    const snapshot = state.snapshot;
    if (!snapshot) { rows.push(state.connection.code === 'UPGRADE' ? {id: 'setup', label: 'Upgrade the local backend', action: 'openBrowser', icon: 'info', tooltip: state.connection.message} : {id: 'setup', label: state.trusted ? 'Start monitoring' : 'Trust workspace to start', action: 'start', icon: 'play'}); return rows; }
    rows.push({id: 'summary', label: `${compact(snapshot.summary.tokens)} tokens`, description: `${money(snapshot.summary.cost)} est.`, icon: 'pulse', tooltip: `${snapshot.scope.label} · ${snapshot.from} to ${snapshot.to}. Costs are estimates at API list prices.`});
    if (state.since) rows.push({id: 'since', label: `+${compact(state.since.tokens)} newly recorded`, description: `${money(state.since.cost)} est.`, icon: 'history', tooltip: 'Newly recorded usage since the in-memory baseline. This does not indicate currently running agents.'});
    rows.push({id: 'scope', label: snapshot.scope.label, description: state.range, icon: 'folder'});
    const graph = forest(snapshot.sessions);
    const make = session => {
      const children = (graph.children.get(session.key) || []).map(child => make(child));
      return {id: `session:${session.key}`, session: session.key, label: session.title || 'Untitled session', description: `${session.subagent ? 'Subagent · ' : ''}${compact(session.tokens)} tokens`, icon: session.subagent ? 'git-branch' : 'account', tooltip: `${session.title || 'Untitled session'}\n${session.model || 'Model unavailable'}\n${compact(session.tokens)} tokens · ${money(session.cost)} estimated cost${session.parent_status === 'unavailable' ? '\nParent metadata unavailable' : ''}`, children};
    };
    rows.push(...graph.roots.slice(0, 10).map(session => make(session)));
    if (snapshot.truncated) rows.push({id: 'truncated', label: 'Recent sessions shown', description: `${snapshot.sessions_total} total`, icon: 'info'});
    return rows;
  }
  dispose() { this.changed.dispose(); }
}
function activate(context) {
  let panel; let startPending = false;
  const tree = new ActivityTree();
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.name = 'AgentTelemetry'; status.command = COMMAND + 'open';
  const monitor = new Monitor({client: new LocalClient(appSetting('port', 7878)), roots: localRoots(), remote: Boolean(vscode.env.remoteName), trusted: vscode.workspace.isTrusted, editorName: vscode.env.appName, onChange: state => {
    tree.update(state);
    if (panel) void panel.webview.postMessage({type: 'state', state});
    const snapshot = state.snapshot;
    status.text = snapshot ? `$(pulse) ${compact(snapshot.summary.tokens)} · ${money(snapshot.summary.cost)}${state.connection.status === 'connected' ? '' : ' $(warning)'}` : `$(pulse) AgentTelemetry${state.connection.status === 'starting' ? '…' : state.connection.status === 'error' || state.connection.status === 'offline' ? ' $(warning)' : ''}`;
    status.tooltip = snapshot ? `${snapshot.scope.label} · ${snapshot.from} to ${snapshot.to}\n${snapshot.summary.tokens.toLocaleString()} tokens · ${money(snapshot.summary.cost)} estimated API cost\n${state.connection.status === 'connected' ? 'Local machine only' : 'Stale: ' + state.connection.message}` : state.connection.message || 'Open local agent usage';
    status.accessibilityInformation = {label: `AgentTelemetry. ${status.tooltip}`};
    appSetting('showStatusBar', true) ? status.show() : status.hide();
  }});
  const open = key => {
    if (!panel) {
      panel = vscode.window.createWebviewPanel('agentTelemetry.desk', 'Agent desk', vscode.ViewColumn.Active, {enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'media')], retainContextWhenHidden: true});
      panel.webview.html = getHtml(panel.webview, context.extensionUri, crypto.randomBytes(24).toString('base64url'));
      context.subscriptions.push(panel.onDidDispose(() => {panel = undefined;}));
      context.subscriptions.push(panel.webview.onDidReceiveMessage(message => {
        if (!validMessage(message, monitor.state.snapshot)) return;
        switch (message.type) {
          case 'ready': monitor.emit(); break;
          case 'refresh': void monitor.poll(true); break;
          case 'start': void start(); break;
          case 'openBrowser': void browser(); break;
          case 'changeScope': void monitor.setScope(message.value); break;
          case 'changeRange': void monitor.setRange(message.value); break;
          case 'selectSession': monitor.select(message.value); break;
          case 'resetBaseline': monitor.resetBaseline(); break;
          case 'setupPython': void setupPython(); break;
          case 'openSettings': void settings(); break;
        }
      }));
    } else panel.reveal();
    if (typeof key === 'string') monitor.select(key);
    monitor.emit();
  };
  const settings = () => vscode.commands.executeCommand('workbench.action.openSettings', '@ext:agenttelemetry-team.agenttelemetry');
  const setupPython = async () => {
    const choice = await vscode.window.showInformationMessage('AgentTelemetry needs a local Python 3.9+ interpreter. Choose an existing executable in settings, or open Python’s download page. No packages are installed automatically.', 'Choose executable', 'Python downloads');
    if (choice === 'Choose executable') await vscode.commands.executeCommand('workbench.action.openSettings', 'agentTelemetry.pythonPath');
    if (choice === 'Python downloads') await vscode.env.openExternal(vscode.Uri.parse('https://www.python.org/downloads/'));
  };
  const browser = async () => {
    try { await monitor.client.health(); await vscode.env.openExternal(vscode.Uri.parse(monitor.client.endpoint)); }
    catch (error) { void vscode.window.showWarningMessage(error.message); }
  };
  const start = async () => {
    if (startPending) return;
    if (!vscode.workspace.isTrusted) { void vscode.window.showWarningMessage('Trust this workspace before starting Python. Read-only local monitoring remains available.'); return; }
    startPending = true;
    try {
      if (!context.globalState.get('sharedServiceDisclosure', false)) {
        const accepted = await vscode.window.showInformationMessage('Start a local AgentTelemetry service? It reads coding-tool logs on this computer and keeps monitoring after this editor closes so other editors and native clients can share it. It does not install packages or send usage to the internet.', {modal: true}, 'Start monitoring');
        if (accepted !== 'Start monitoring') return;
        await context.globalState.update('sharedServiceDisclosure', true);
      }
      monitor.state.connection.status = 'starting'; monitor.state.connection.message = 'Starting the shared local service…'; monitor.emit();
      await startBackend({client: monitor.client, extensionPath: context.extensionPath || context.extensionUri.fsPath, configuredPython: appSetting('pythonPath', ''), roots: localRoots(), stillAllowed: () => !monitor.disposed && vscode.workspace.isTrusted});
      if (!monitor.disposed) await monitor.poll();
    } catch (error) {
      monitor.state.connection.status = 'error'; monitor.state.connection.message = error.message; monitor.emit();
      const choice = await vscode.window.showWarningMessage(error.message, ...(error.code === 'NO_PYTHON' || error.code === 'PYTHON' ? ['Set up Python'] : ['Open settings']));
      if (choice === 'Set up Python') await setupPython(); else if (choice === 'Open settings') await settings();
    } finally { startPending = false; }
  };
  context.subscriptions.push(status, tree, vscode.window.createTreeView('agentTelemetry.activity', {treeDataProvider: tree, showCollapseAll: true}));
  for (const [name, handler] of Object.entries({open, refresh: () => monitor.poll(true), start, openBrowser: browser, resetBaseline: () => monitor.resetBaseline(), setupPython, settings})) context.subscriptions.push(vscode.commands.registerCommand(COMMAND + name, handler));
  context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(() => void monitor.setWorkspace(localRoots(), Boolean(vscode.env.remoteName), vscode.workspace.isTrusted)));
  context.subscriptions.push(vscode.workspace.onDidGrantWorkspaceTrust(() => void monitor.setWorkspace(localRoots(), Boolean(vscode.env.remoteName), true)));
  context.subscriptions.push(vscode.workspace.onDidChangeConfiguration(event => { if (event.affectsConfiguration('agentTelemetry')) { const port = appSetting('port', 7878); if (port !== monitor.client.port) void monitor.setClient(new LocalClient(port)); else monitor.emit(); } }));
  const timer = setInterval(() => void monitor.poll(), 1000);
  context.subscriptions.push({dispose() {clearInterval(timer); monitor.dispose(); if (panel) panel.dispose();}});
  active = monitor; monitor.emit(); void monitor.poll();
  return Object.freeze({getState: () => structuredClone(monitor.state), refresh: () => monitor.poll(true), setScope: scope => monitor.setScope(scope), setRange: range => monitor.setRange(range)});
}
function deactivate() { if (active) active.dispose(); active = undefined; }
module.exports = {activate, deactivate, ActivityTree, appSetting, localRoots};
