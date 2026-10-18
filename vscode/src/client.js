'use strict';
const http = require('node:http');

class ServiceError extends Error {
  constructor(message, code) { super(message); this.name = 'ServiceError'; this.code = code; }
}
function portNumber(value) {
  if (!Number.isInteger(value) || value < 1 || value > 65535) throw new ServiceError('Choose a port from 1 to 65535.', 'CONFIG');
  return value;
}
function requestJSON(port, pathname, { method = 'GET', body, timeout = 5000, maxBytes = 4 * 1024 * 1024 } = {}) {
  portNumber(port);
  if (!pathname.startsWith('/api/') || pathname.includes('\r') || pathname.includes('\n')) throw new ServiceError('Invalid local API route.', 'CONFIG');
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(value); };
    const encoded = body === undefined ? undefined : JSON.stringify(body);
    const headers = {Accept: 'application/json', Origin: `http://127.0.0.1:${port}`};
    if (encoded !== undefined) Object.assign(headers, {'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(encoded)});
    const req = http.request({hostname: '127.0.0.1', port, path: pathname, method, headers, agent: false}, res => {
      if (res.statusCode !== 200) {
        res.destroy(); req.destroy();
        finish(new ServiceError(res.statusCode >= 300 && res.statusCode < 400 ? 'Local service redirects are refused.' : `Local service returned HTTP ${res.statusCode}.`, `HTTP_${res.statusCode}`));
        return;
      }
      if (!(res.headers['content-type'] || '').toLowerCase().includes('application/json')) {
        res.destroy(); req.destroy(); finish(new ServiceError('The local port did not return AgentTelemetry JSON.', 'IDENTITY')); return;
      }
      let length = 0; const chunks = [];
      res.on('data', chunk => {
        length += chunk.length;
        if (length > maxBytes) { finish(new ServiceError('Local response exceeded its size limit.', 'SIZE')); req.destroy(); res.destroy(); }
        else chunks.push(chunk);
      });
      res.on('end', () => {
        if (settled) return;
        try { finish(null, JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
        catch (_) { finish(new ServiceError('The local service returned invalid JSON.', 'JSON')); }
      });
      res.on('error', error => finish(new ServiceError('The local response was interrupted.', error.code || 'NETWORK')));
    });
    const timer = setTimeout(() => { finish(new ServiceError('The local service did not respond in time.', 'TIMEOUT')); req.destroy(); }, timeout);
    req.on('error', error => finish(new ServiceError(error.code === 'ECONNREFUSED' ? 'AgentTelemetry is not running on this local port.' : 'Cannot reach the local AgentTelemetry service.', error.code || 'NETWORK')));
    if (encoded !== undefined) req.write(encoded);
    req.end();
  });
}
function healthIdentity(value) {
  if (!value || value.service !== 'agent-telemetry' || typeof value.ready !== 'boolean' || typeof value.version !== 'string') throw new ServiceError('This port belongs to another service. AgentTelemetry will not start over it.', 'IDENTITY');
  return value;
}
function validateSnapshot(value) {
  const invalid = () => { throw new ServiceError('The local backend returned an invalid Agent desk snapshot. Update the backend or retry Refresh.', 'SCHEMA'); };
  const record = item => item && typeof item === 'object' && !Array.isArray(item);
  const number = item => { if (!Number.isFinite(item) || item < 0) invalid(); return item; };
  const integer = item => { if (!Number.isSafeInteger(item) || item < 0) invalid(); return item; };
  const string = (item, maximum = 2048) => { if (typeof item !== 'string' || item.length > maximum || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(item)) invalid(); return item; };
  const optionalString = (item, maximum) => item == null ? '' : string(item, maximum);
  const boolean = (item, fallback = false) => { if (item === undefined) return fallback; if (typeof item !== 'boolean') invalid(); return item; };
  const list = (item, maximum) => { if (!Array.isArray(item) || item.length > maximum) invalid(); return item; };
  const date = item => {
    if (typeof item !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(item)) invalid();
    const parsed = new Date(item + 'T00:00:00Z');
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== item) invalid();
    return item;
  };
  const timestamp = item => {
    if (item == null) return null;
    string(item, 48); date(item.slice(0, 10));
    if (!Number.isFinite(Date.parse(item))) invalid();
    return item;
  };
  const usage = item => {
    if (!record(item)) invalid();
    return Object.fromEntries(['tokens', 'input', 'output', 'cache_read', 'cache_write', 'cost'].map(key => [key, number(item[key])]));
  };
  if (!record(value) || value.schema !== 1 || !record(value.scope) || !['workspace', 'all'].includes(value.scope.kind)) invalid();
  const summary = usage(value.summary);
  for (const key of ['sessions', 'main_agents', 'subagents', 'compactions']) summary[key] = integer(value.summary[key]);
  const keys = new Set();
  const sessions = list(value.sessions, 500).map(session => {
    if (!record(session)) invalid();
    const key = string(session.key);
    if (!key || keys.has(key)) invalid(); keys.add(key);
    const subagent = boolean(session.subagent);
    const usageStatus = session.usage_status === undefined ? 'recorded' : session.usage_status;
    if (!['recorded', 'partial', 'unavailable'].includes(usageStatus)) invalid();
    const parentStatus = session.parent_status === undefined ? subagent ? 'unavailable' : 'none' : session.parent_status;
    if (!['linked', 'outside_scope', 'unavailable', 'none'].includes(parentStatus)) invalid();
    return {key, id: optionalString(session.id, 2048), source: string(session.source, 80), device: optionalString(session.device, 256),
      title: optionalString(session.title, 2048), project: optionalString(session.project, 512), subagent,
      parent_key: session.parent_key == null ? null : string(session.parent_key),
      parent_title: session.parent_title == null ? null : string(session.parent_title), parent_status: parentStatus,
      model: optionalString(session.model, 512), efforts: session.efforts === undefined ? [] : list(session.efforts, 16).map(item => string(item, 32)),
      ...usage(session), compactions: session.compactions == null ? null : integer(session.compactions),
      start: timestamp(session.start), end: timestamp(session.end), archived: boolean(session.archived), detail_limited: boolean(session.detail_limited),
      usage_in_parent: boolean(session.usage_in_parent), usage_status: usageStatus};
  });
  const total = integer(value.sessions_total);
  if (total < sessions.length || total !== summary.sessions || typeof value.truncated !== 'boolean' || value.truncated !== (total > sessions.length)) invalid();
  const from = date(value.from); const to = date(value.to); const today = date(value.date);
  if (from > to) invalid();
  return {schema: 1, generated_at: number(value.generated_at), date: today, from, to,
    revision: string(value.revision, 512),
    scope: {kind: value.scope.kind, label: string(value.scope.label, 8192), roots: list(value.scope.roots, 20).map(item => string(item, 512)),
      matched_sessions: integer(value.scope.matched_sessions), unassigned_sessions: integer(value.scope.unassigned_sessions)}, summary,
    days: list(value.days, 30).map(day => { if (!record(day)) invalid(); return {date: date(day.date), tokens: number(day.tokens), cost: number(day.cost)}; }),
    models: list(value.models, 5000).map(model => { if (!record(model)) invalid(); return {name: string(model.name, 512), source: string(model.source, 80), tokens: number(model.tokens), cost: number(model.cost)}; }),
    sessions, sessions_total: total, truncated: value.truncated,
    limitations: list(value.limitations, 50).map(item => string(item, 4096))};
}
class LocalClient {
  constructor(port = 7878, transport = requestJSON) { this.port = portNumber(port); this.transport = transport; }
  get endpoint() { return `http://127.0.0.1:${this.port}`; }
  async health() { return healthIdentity(await this.transport(this.port, '/api/health', {maxBytes: 32768})); }
  async sync() {
    const value = await this.transport(this.port, '/api/sync', {maxBytes: 32768});
    if (!value || typeof value.revision !== 'string' || value.revision.length > 512) throw new ServiceError('Update AgentTelemetry to use its shared refresh clock.', 'SCHEMA');
    return value;
  }
  async snapshot(scope, range, roots) {
    const query = new URLSearchParams({scope, range});
    if (scope === 'workspace') for (const root of roots) query.append('root', root);
    try {
      return validateSnapshot(await this.transport(this.port, `/api/editor?${query}`, {maxBytes: 8 * 1024 * 1024, timeout: 15000}));
    } catch (error) {
      if (error.code === 'HTTP_404') throw new ServiceError('The running AgentTelemetry backend needs an upgrade for the Agent desk. Upgrade or restart it from an updated installation, then Refresh. Existing monitoring is still running.', 'UPGRADE');
      throw error;
    }
  }
  async refresh() { return this.transport(this.port, '/api/refresh', {method: 'POST', body: {}, timeout: 90000, maxBytes: 65536}); }
}
module.exports = {LocalClient, ServiceError, requestJSON, portNumber, healthIdentity, validateSnapshot};
