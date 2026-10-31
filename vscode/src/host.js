'use strict';
const {LocalClient} = require('./client');
const COUNTERS = ['tokens', 'input', 'output', 'cache_read', 'cache_write', 'cost', 'sessions', 'main_agents', 'subagents', 'compactions'];
function validMessage(message, snapshot) {
  if (!message || typeof message !== 'object' || Array.isArray(message) || typeof message.type !== 'string') return false;
  const simple = ['ready', 'refresh', 'start', 'openBrowser', 'resetBaseline', 'setupPython', 'getDesktopApp', 'openSettings'];
  if (simple.includes(message.type)) return Object.keys(message).length === 1;
  if (Object.keys(message).length !== 2 || !Object.hasOwn(message, 'value')) return false;
  if (message.type === 'changeScope') return ['workspace', 'all'].includes(message.value);
  if (message.type === 'changeRange') return ['today', '7d', '30d'].includes(message.value);
  return message.type === 'selectSession' && typeof message.value === 'string' && Boolean(snapshot?.sessions.some(session => session.key === message.value));
}
class Monitor {
  constructor({client = new LocalClient(), roots = [], remote = false, trusted = true, editorName = 'VS Code', onChange = () => {}} = {}) {
    this.client = client; this.roots = roots; this.onChange = onChange; this.generation = 0; this.busy = false; this.pending = false; this.waiters = []; this.waiting = new Set(); this.disposed = false; this.revision = null; this.baseline = null;
    this.state = {connection: {status: 'connecting', message: '', endpoint: client.endpoint, version: '', last_success: null}, snapshot: null, scope: roots.length && !remote ? 'workspace' : 'all', range: 'today', since: null, selected_session: null, editor_name: editorName, remote, trusted, workspace_available: Boolean(roots.length && !remote)};
  }
  emit() { if (!this.disposed) this.onChange(this.state); }
  invalidate() { this.generation++; this.revision = null; this.baseline = null; this.state.snapshot = null; this.state.since = null; this.state.selected_session = null; }
  setClient(client) { this.invalidate(); this.client = client; this.state.connection = {status: 'connecting', message: '', endpoint: client.endpoint, version: '', last_success: null}; this.emit(); return this.poll(); }
  setWorkspace(roots, remote, trusted) {
    const changed = JSON.stringify(this.roots) !== JSON.stringify(roots) || remote !== this.state.remote;
    this.roots = roots; this.state.remote = remote; this.state.trusted = trusted; this.state.workspace_available = Boolean(roots.length && !remote);
    if (changed) { this.invalidate(); if (remote || !roots.length) this.state.scope = 'all'; }
    this.emit(); return this.poll();
  }
  setScope(scope) {
    if (!['workspace', 'all'].includes(scope) || (scope === 'workspace' && (this.state.remote || !this.roots.length)) || scope === this.state.scope) return;
    this.invalidate(); this.state.scope = scope; this.emit(); return this.poll();
  }
  setRange(range) { if (!['today', '7d', '30d'].includes(range) || range === this.state.range) return; this.invalidate(); this.state.range = range; this.emit(); return this.poll(); }
  select(key) { if (this.state.snapshot?.sessions.some(session => session.key === key)) { this.state.selected_session = key; this.emit(); } }
  resetBaseline(reset = true) {
    const snapshot = this.state.snapshot;
    if (!snapshot) { this.baseline = null; this.state.since = null; return; }
    this.baseline = {signature: this.signature(snapshot), counters: {...snapshot.summary}, since: Date.now() / 1000, previous: {...snapshot.summary}};
    this.state.since = {tokens: 0, cost: 0, since: this.baseline.since, reset};
    this.emit();
  }
  signature(snapshot) { return JSON.stringify([this.state.scope, this.roots, this.state.range, snapshot.date, snapshot.from, snapshot.to, snapshot.revision.split(':')[0]]); }
  updateBaseline(snapshot) {
    const baseline = this.baseline;
    const changed = baseline && (baseline.signature !== this.signature(snapshot) || COUNTERS.some(key => snapshot.summary[key] < baseline.previous[key]));
    if (!baseline || changed) { this.resetBaseline(Boolean(changed)); return; }
    baseline.previous = {...snapshot.summary};
    this.state.since = {tokens: snapshot.summary.tokens - baseline.counters.tokens, cost: Math.max(0, snapshot.summary.cost - baseline.counters.cost), since: baseline.since, reset: false};
  }
  async poll(force = false) {
    if (this.disposed) return;
    if (this.busy) {
      this.pending = true; this.forcePending = this.forcePending || force;
      return new Promise((resolve, reject) => { const waiter = {resolve, reject}; this.waiters.push(waiter); this.waiting.add(waiter); });
    }
    this.busy = true;
    const generation = this.generation; const client = this.client;
    const current = () => !this.disposed && generation === this.generation && client === this.client;
    try {
      const health = await client.health(); if (!current()) return;
      this.state.connection.version = health.version;
      if (!health.ready) { this.state.connection.status = 'starting'; this.state.connection.message = 'AgentTelemetry is reading local logs. The first scan can take a minute.'; this.emit(); return; }
      if (force) { await client.refresh(); if (!current()) return; }
      const sync = await client.sync(); if (!current()) return;
      if (force || sync.revision !== this.revision || !this.state.snapshot) {
        const snapshot = await client.snapshot(this.state.scope, this.state.range, this.roots); if (!current()) return;
        if (snapshot.scope.kind !== this.state.scope) throw new Error('The backend returned a different usage scope.');
        this.state.snapshot = snapshot;
        if (!snapshot.sessions.some(session => session.key === this.state.selected_session)) this.state.selected_session = null;
        this.updateBaseline(snapshot);
        // Use the revision of the returned snapshot: a tick may change mid-request.
        this.revision = snapshot.revision;
      }
      this.state.connection = {...this.state.connection, status: 'connected', code: '', message: this.state.remote ? 'This editor is remote. Showing this computer only; remote workspace attribution is unavailable.' : this.state.scope === 'all' ? 'Local machine only. Usage from shared devices is excluded.' : '', last_success: Date.now() / 1000};
      this.emit();
    } catch (error) {
      if (current()) { this.state.connection.status = error.code === 'ECONNREFUSED' ? 'offline' : 'error'; this.state.connection.code = typeof error.code === 'string' ? error.code : 'ERROR'; this.state.connection.message = `${error.message}${this.state.snapshot ? ' Showing the last successful snapshot; figures are stale.' : ''}`; this.emit(); }
    } finally {
      this.busy = false;
      const waiters = this.waiters.splice(0);
      const settle = error => {
        for (const waiter of waiters) { this.waiting.delete(waiter); error ? waiter.reject(error) : waiter.resolve(); }
      };
      if (this.pending && !this.disposed) {
        const forceNext = this.forcePending; this.pending = false; this.forcePending = false;
        // Each caller waits for its own requested attempt. Awaiting the queued
        // successor here retains every previous attempt during a slow outage,
        // because the one-second timer keeps another successor queued forever.
        void this.poll(forceNext).then(() => settle(), error => settle(error));
      } else settle();
    }
  }
  dispose() {
    this.disposed = true; this.generation++; this.pending = false; this.forcePending = false;
    for (const waiter of this.waiting) waiter.resolve();
    this.waiting.clear(); this.waiters.length = 0;
  }
}
module.exports = {Monitor, validMessage, COUNTERS};
