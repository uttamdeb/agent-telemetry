(function () {
  'use strict';
  let state = JSON.parse(document.getElementById('fixture-state').textContent);
  let saved = {};
  const messages = [];
  function update(next) { state = next; window.dispatchEvent(new MessageEvent('message', {data: {type: 'state', state}})); }
  window.__deskPreview = {messages, update, current: () => state};
  window.acquireVsCodeApi = () => ({
    getState: () => saved,
    setState: value => { saved = value; },
    postMessage: message => {
      messages.push(message);
      if (message.type === 'ready') setTimeout(() => update(state), 0);
      if (message.type === 'selectSession') { state.selected_session = message.value; update(state); }
      if (message.type === 'changeScope') { state.scope = message.value; state.snapshot.scope.kind = message.value; state.snapshot.scope.label = message.value === 'all' ? 'All local projects' : 'atlas-workspace'; state.since = {tokens: 0, cost: 0, since: Date.now() / 1000, reset: true}; update(state); }
      if (message.type === 'changeRange') { state.range = message.value; state.snapshot.from = message.value === 'today' ? '2026-10-18' : message.value === '7d' ? '2026-10-12' : '2026-09-19'; state.since = {tokens: 0, cost: 0, since: Date.now() / 1000, reset: true}; update(state); }
      if (message.type === 'resetBaseline') { state.since = {tokens: 0, cost: 0, since: Date.now() / 1000, reset: true}; update(state); }
      if (message.type === 'refresh' && state.snapshot) { state.connection.status = 'connected'; update(state); }
    }
  });
}());
