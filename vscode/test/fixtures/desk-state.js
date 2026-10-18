'use strict';

// Entirely invented data. Never read a developer's ledger or source logs here.
function makeState() {
  const base = {source: 'codex', device: 'fixture-device', project: 'atlas-workspace', parent_key: null, parent_title: null, parent_status: 'none', efforts: ['high'], compactions: 0, start: '2026-10-18T08:10:00Z', end: '2026-10-18T09:42:00Z', archived: false, detail_limited: false, model: 'GPT-6.1 Sol'};
  const sessions = [
    {...base, key: 'fixture-main', id: 'fixture-main-full-id', title: 'Make the search feel instant', subagent: false, tokens: 2480000, input: 110000, output: 42000, cache_read: 2300000, cache_write: 28000, cost: 3.84, compactions: 2},
    {...base, key: 'fixture-child-ui', id: 'fixture-child-ui-full-id', title: 'Shape the command palette', subagent: true, parent_key: 'fixture-main', parent_title: 'Make the search feel instant', parent_status: 'linked', tokens: 322000, input: 45000, output: 22000, cache_read: 240000, cache_write: 15000, cost: .71},
    {...base, key: 'fixture-child-tests', id: 'fixture-child-tests-full-id', title: 'Check keyboard & focus behavior', subagent: true, parent_key: 'fixture-main', parent_title: 'Make the search feel instant', parent_status: 'linked', tokens: 198000, input: 30000, output: 18000, cache_read: 140000, cache_write: 10000, cost: .40, efforts: ['medium']},
    {...base, key: 'fixture-other', id: 'fixture-other-full-id', title: 'Review the index migration', source: 'claude', model: 'Claude Sonnet 5.5', efforts: ['high', 'unrecorded'], subagent: false, tokens: 190000, input: 25000, output: 15000, cache_read: 135000, cache_write: 15000, cost: .57, compactions: 1},
    {...base, key: 'fixture-orphan', id: 'fixture-orphan-full-id', title: 'Explore the edge cases', subagent: true, parent_status: 'unavailable', tokens: 74000, input: 10000, output: 9000, cache_read: 50000, cache_write: 5000, cost: .19, efforts: [], compactions: null, archived: true, detail_limited: true},
    {...base, key: 'fixture-outside', id: 'fixture-outside-full-id', title: 'Inspect the response adapter', subagent: true, parent_status: 'outside_scope', tokens: 58000, input: 8000, output: 6000, cache_read: 42000, cache_write: 2000, cost: .14, efforts: []}
  ];
  return {
    connection: {status: 'connected', message: '', endpoint: 'http://127.0.0.1:7878', version: 'fixture-only', last_success: 1792316400},
    snapshot: {
      schema: 1, generated_at: 1792316400, date: '2026-10-18', from: '2026-10-18', to: '2026-10-18', revision: 'fixture-1',
      scope: {kind: 'workspace', label: 'atlas-workspace', roots: ['atlas-workspace'], matched_sessions: 6, unassigned_sessions: 2},
      summary: {tokens: 3322000, input: 228000, output: 112000, cache_read: 2907000, cache_write: 75000, cost: 5.85, sessions: 6, main_agents: 2, subagents: 4, compactions: 3},
      days: [{date: '2026-10-18', tokens: 3322000, cost: 5.85}],
      models: [{name: 'GPT-6.1 Sol', source: 'codex', tokens: 3132000, cost: 5.28}, {name: 'Claude Sonnet 5.5', source: 'claude', tokens: 190000, cost: .57}],
      sessions, sessions_total: 6, truncated: false, limitations: ['Costs are estimates at API list prices.']
    },
    scope: 'workspace', range: 'today', since: {tokens: 146200, cost: .29, since: 1792314900, reset: false}, selected_session: 'fixture-main', editor_name: 'Visual Studio Code', remote: false, trusted: true
  };
}
function scenario(name) {
  const state = makeState();
  if (name === 'stale') { state.connection.status = 'offline'; state.connection.message = 'The local service could not be reached. Automatic retries are continuing.'; }
  if (name === 'offline' || name === 'restricted' || name === 'upgrade') { state.snapshot = null; state.since = null; state.selected_session = null; state.connection.status = 'offline'; }
  if (name === 'restricted') state.trusted = false;
  if (name === 'upgrade') { state.connection.code = 'UPGRADE'; state.connection.status = 'error'; }
  if (name === 'no-workspace') { state.workspace_available = false; state.scope = 'all'; }
  if (name === 'remote') { state.remote = true; state.scope = 'all'; state.snapshot.scope.kind = 'all'; state.snapshot.scope.label = 'All local projects'; state.editor_name = 'Cursor · SSH'; }
  if (name === 'empty') { state.snapshot.sessions = []; state.snapshot.sessions_total = 0; for (const key of Object.keys(state.snapshot.summary)) state.snapshot.summary[key] = 0; state.since.tokens = state.since.cost = 0; }
  if (name === 'history') {
    state.range = '7d'; state.snapshot.from = '2026-10-12';
    state.snapshot.days = [120000, 910000, 280000, 1300000, 780000, 2200000, 3322000].map((tokens, index) => ({date: '2026-10-' + (12 + index), tokens, cost: .5}));
  }
  if (name === 'long') { state.snapshot.scope.label = 'a-very-long-workspace-name-without-convenient-breaks'; state.snapshot.sessions[0].title = '<script>Local text only</script> A very long session title that should wrap in the inspector and truncate in the outline'; state.snapshot.sessions[0].model = '<img src=x onerror=alert(1)> Model name'; }
  if (name === 'truncated') { state.snapshot.truncated = true; state.snapshot.sessions_total = 1700; }
  return state;
}
module.exports = {makeState, scenario};
