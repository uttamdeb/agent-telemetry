'use strict';

const vscode = require('vscode');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

async function until(predicate, message, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function run() {
  console.log(`AT_EXTENSION_SMOKE_START ${vscode.env.appName} ${vscode.version}`);
  const extension = vscode.extensions.getExtension('uttamdeb.agenttelemetry');
  assert.ok(extension, 'The packaged extension must be registered in the real host');
  const config = vscode.workspace.getConfiguration('agentTelemetry');
  await config.update('port', Number(process.env.AT_EDITOR_TEST_PORT), vscode.ConfigurationTarget.Global);
  const api = await extension.activate();
  assert.equal(typeof api.getState, 'function');
  await until(() => api.getState().connection.status === 'connected', 'AgentTelemetry did not connect to its fixture backend');
  let state = api.getState();
  assert.equal(state.connection.endpoint, `http://127.0.0.1:${process.env.AT_EDITOR_TEST_PORT}`);
  assert.equal(state.editor_name, vscode.env.appName);
  assert.equal(state.remote, false);
  assert.equal(state.scope, 'workspace');
  assert.equal(state.workspace_available, true);
  assert.equal(state.snapshot.sessions_total, 5);
  assert.equal(state.snapshot.summary.main_agents, 2);
  assert.equal(state.snapshot.summary.subagents, 3);
  assert.equal(state.snapshot.sessions.filter(session => session.parent_status === 'linked').length, 2);
  assert.equal(state.since.tokens, 0);

  const initial = state.snapshot.summary.tokens;
  // Mutating a returned state must never alter the extension's internal state.
  state.snapshot.summary.tokens = -1;
  assert.equal(api.getState().snapshot.summary.tokens, initial);

  await vscode.commands.executeCommand('agentTelemetry.open');
  await until(() => vscode.window.tabGroups.all.some(group => group.tabs.some(tab => tab.label === 'Agent desk')),
    'The Agent desk did not open as an editor tab');
  await api.refresh();
  await until(() => api.getState().snapshot.summary.tokens === initial + 1000, 'Manual refresh did not update usage');
  assert.equal(api.getState().since.tokens, 1000);

  await api.setScope('all');
  await until(() => api.getState().scope === 'all' && api.getState().snapshot?.scope.kind === 'all', 'All-project scope did not load');
  assert.equal(api.getState().snapshot.sessions_total, 6);
  assert.equal(api.getState().since.tokens, 0);
  await api.setRange('7d');
  await until(() => api.getState().range === '7d' && api.getState().snapshot?.days.length === 7, 'Date range did not load');
  assert.equal(api.getState().since.tokens, 0);
  await api.setScope('workspace');
  await until(() => api.getState().snapshot?.scope.kind === 'workspace', 'Workspace scope did not restore');
  await api.setRange('today');
  await until(() => api.getState().range === 'today' && api.getState().snapshot?.days.length === 1, 'Today did not restore');
  await vscode.commands.executeCommand('agentTelemetry.resetBaseline');
  assert.equal(api.getState().since.tokens, 0);
  await vscode.commands.executeCommand('workbench.view.extension.agentTelemetry');
  const commands = await vscode.commands.getCommands(true);
  for (const command of ['open', 'refresh', 'start', 'openBrowser', 'resetBaseline', 'settings', 'setupPython']) {
    assert.ok(commands.includes('agentTelemetry.' + command));
  }
  // The fixture backend changes only in memory. Neither activation nor read-only
  // controls may create/rebuild a durable ledger in the fixture data directory.
  assert.equal(fs.existsSync(path.join(process.env.AT_EDITOR_TEST_DATA, '.usage_cache.json')), false);
  console.log(`AT_EXTENSION_SMOKE_PASS ${vscode.env.appName} ${vscode.version}`);
}

module.exports = {run};
