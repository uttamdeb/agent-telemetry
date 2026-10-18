(function () {
  'use strict';

  const SOURCE_LABELS = {codex: 'Codex', claude: 'Claude Code', copilot: 'GitHub Copilot', cursor: 'Cursor', opencode: 'OpenCode', hermes: 'Hermes Agent', openclaw: 'OpenClaw', 'claude-desktop': 'Claude Desktop'};
  const RANGE_LABELS = {today: 'Today', '7d': 'Last 7 days', '30d': 'Last 30 days'};
  const EFFORT_LABELS = {none: 'None', minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Max', ultra: 'Ultra', unrecorded: 'Not recorded'};
  const n = value => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0;
  const text = value => typeof value === 'string' ? value : '';
  const sourceLabel = source => SOURCE_LABELS[source] || text(source) || 'Unknown tool';
  const effortLabel = value => EFFORT_LABELS[value] || text(value) || 'Not recorded';
  function compact(value) {
    const amount = n(value);
    if (amount >= 1e9) return (amount / 1e9).toLocaleString('en-US', {maximumFractionDigits: 2}) + 'B';
    if (amount >= 1e6) return (amount / 1e6).toLocaleString('en-US', {maximumFractionDigits: 2}) + 'M';
    if (amount >= 1e3) return (amount / 1e3).toLocaleString('en-US', {maximumFractionDigits: 1}) + 'K';
    return Math.round(amount).toLocaleString('en-US');
  }
  function money(value) {
    const amount = n(value);
    return amount > 0 && amount < 0.01 ? '<$0.01' : '$' + amount.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }
  function time(value) {
    const date = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isFinite(date.getTime()) ? date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : 'Unavailable';
  }
  function shortDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text(value))) return '';
    const date = new Date(value + 'T12:00:00');
    return Number.isFinite(date.getTime()) ? date.toLocaleDateString([], {month: 'short', day: 'numeric'}) : '';
  }
  function composition(value) {
    return [['input', 'Input'], ['output', 'Output'], ['cache_read', 'Cache read'], ['cache_write', 'Cache write']]
      .map(([key, label]) => ({key, label, value: n(value[key])}));
  }
  // Never infer an edge from titles, chronology, counts or tool identity.
  function forest(sessions) {
    const byKey = new Map();
    for (const session of sessions) if (session && typeof session.key === 'string' && !byKey.has(session.key)) byKey.set(session.key, session);
    const parent = new Map();
    for (const session of byKey.values()) {
      if (session.parent_status !== 'linked' || !byKey.has(session.parent_key) || session.parent_key === session.key) continue;
      const seen = new Set([session.key]);
      let ancestor = session.parent_key;
      let cyclic = false;
      while (byKey.has(ancestor)) {
        if (seen.has(ancestor)) { cyclic = true; break; }
        seen.add(ancestor);
        const candidate = byKey.get(ancestor);
        if (candidate.parent_status !== 'linked') break;
        ancestor = candidate.parent_key;
      }
      if (!cyclic) parent.set(session.key, session.parent_key);
    }
    const children = new Map();
    for (const [child, owner] of parent) {
      if (!children.has(owner)) children.set(owner, []);
      children.get(owner).push(byKey.get(child));
    }
    return {byKey, parent, children, roots: [...byKey.values()].filter(session => !parent.has(session.key))};
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {compact, money, composition, forest, effortLabel};
  if (typeof document === 'undefined') return;

  const api = acquireVsCodeApi();
  const byId = id => document.getElementById(id);
  const saved = api.getState() || {};
  let state = {connection: {status: 'connecting'}, snapshot: null, scope: 'workspace', range: 'today'};
  let selected = typeof saved.selected === 'string' ? saved.selected : null;
  let roving = selected;
  let sessionFilter = ['all', 'main', 'subagents'].includes(saved.sessionFilter) ? saved.sessionFilter : 'all';
  let allGraph = forest([]);
  let graph = forest([]);
  const collapsed = new Set(Array.isArray(saved.collapsed) ? saved.collapsed.filter(value => typeof value === 'string').slice(0, 1000) : []);
  const send = (type, value) => api.postMessage(value === undefined ? {type} : {type, value});
  function persist() { api.setState({selected, sessionFilter, collapsed: [...collapsed].slice(0, 1000)}); }
  function el(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = String(content);
    return node;
  }
  function set(id, content) { byId(id).textContent = content; }
  function action(label, type, primary) {
    const button = el('button', primary ? 'primary' : 'quiet', label);
    button.type = 'button';
    button.dataset.action = type;
    button.dataset.focus = 'notice-' + type;
    return button;
  }
  function replace(node, children) { node.replaceChildren(...children); }
  function svg(tag, attributes) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value));
    return node;
  }
  function focusToken() { return document.activeElement && document.activeElement.dataset.focus; }
  function restoreFocus(token) {
    if (!token) return;
    const target = [...document.querySelectorAll('[data-focus]')].find(node => node.dataset.focus === token);
    if (target && !target.disabled) target.focus({preventScroll: true});
  }
  function markControls() {
    for (const button of document.querySelectorAll('[data-scope]')) {
      button.setAttribute('aria-pressed', String(button.dataset.scope === state.scope));
      button.disabled = Boolean((state.remote || state.workspace_available === false) && button.dataset.scope === 'workspace');
      button.title = button.disabled ? state.remote ? 'Remote workspaces cannot be matched to paths on this machine.' : 'Open a local folder to enable workspace scope.' : '';
    }
    for (const button of document.querySelectorAll('[data-session-filter]')) button.setAttribute('aria-pressed', String(button.dataset.sessionFilter === sessionFilter));
    for (const button of document.querySelectorAll('[data-range]')) button.setAttribute('aria-pressed', String(button.dataset.range === state.range));
    for (const button of document.querySelectorAll('[data-action="refresh"]')) button.disabled = ['connecting', 'starting'].includes(state.connection.status);
    for (const button of document.querySelectorAll('[data-action="resetBaseline"]')) button.disabled = !state.snapshot || state.connection.status !== 'connected';
  }
  function renderConnection() {
    const connection = state.connection;
    const connected = connection.status === 'connected';
    const pending = ['connecting', 'starting'].includes(connection.status);
    const upgrade = connection.code === 'UPGRADE';
    byId('connection-dot').className = 'status-dot ' + (connected ? 'connected' : pending ? 'connecting' : 'offline');
    set('connection-label', connected ? 'Connected to local monitoring' : connection.status === 'starting' ? 'Starting local monitoring…' : pending ? 'Connecting to local monitoring…' : state.snapshot ? 'Disconnected · last snapshot retained' : 'Local monitoring is unavailable');
    set('connection-editor', state.editor_name || 'Your editor');
    const notice = byId('notice');
    const showRemote = Boolean(state.remote);
    notice.hidden = connected && !showRemote;
    if (notice.hidden) return;
    const heading = el('h2', '', upgrade ? 'Upgrade your local backend' : showRemote && connected ? 'Showing this machine, not the remote workspace' : pending ? connection.status === 'starting' ? 'Starting your local service' : 'Looking for local monitoring' : state.snapshot ? 'Your last snapshot is still here' : 'Bring your local agent work into view');
    const message = el('p', '', upgrade ? 'The running AgentTelemetry service needs an upgrade for the Agent desk. Open its dashboard to update it, then retry this connection. Your existing service and ledger stay in place.' : showRemote ? 'This editor is connected to a remote workspace. Only agent logs on this machine are available here; workspace matching is disabled.' : text(connection.message) || (pending ? 'The first scan can take a little longer with a large log history.' : state.snapshot ? 'Monitoring could not be reached. These figures stay unchanged until the connection recovers.' : 'Connect to an existing AgentTelemetry service, or explicitly start monitoring. Logs and usage stay on this machine.'));
    const nodes = [heading, message];
    if (!connected && !pending) {
      const actions = el('div', 'notice-actions');
      actions.append(action('Retry connection', 'refresh', Boolean(state.snapshot)));
      if (upgrade) {
        actions.append(action('Open dashboard to upgrade', 'openBrowser', true), action('Connection settings', 'openSettings'));
      } else if (!state.snapshot) {
        const start = action('Start local monitoring', 'start', true);
        start.disabled = state.trusted === false;
        start.title = start.disabled ? 'Trust this workspace before starting a local Python process.' : 'Starts a local service that keeps running when the editor closes.';
        actions.append(start, action('Set up Python', 'setupPython'), action('Connection settings', 'openSettings'));
      }
      nodes.push(actions);
      if (state.trusted === false) nodes.push(el('p', 'detail-scope', 'Restricted Mode allows read-only connections. Starting monitoring requires a trusted workspace.'));
    }
    replace(notice, nodes);
  }
  function renderBrief() {
    const snapshot = state.snapshot;
    const summary = snapshot && snapshot.summary;
    const label = snapshot && snapshot.scope && snapshot.scope.label;
    set('scope-caption', state.scope === 'all' ? 'LOCAL MACHINE BRIEF' : 'WORKSPACE BRIEF');
    set('workspace-title', label || (state.scope === 'all' ? 'All local projects' : 'Your recorded agent work'));
    set('date-label', snapshot ? shortDate(snapshot.from) + (snapshot.from !== snapshot.to ? ' — ' + shortDate(snapshot.to) : '') : RANGE_LABELS[state.range]);
    set('total-tokens', summary ? compact(summary.tokens) : '—');
    byId('total-tokens').title = summary ? n(summary.tokens).toLocaleString('en-US') + ' recorded tokens, including cache reads and writes' : 'Waiting for recorded usage';
    set('total-cost', summary ? money(summary.cost) : '—');
    const evidence = byId('brief-evidence');
    if (!summary) replace(evidence, [el('p', 'muted', 'A local view of the usage your tools record.')]);
    else {
      const counts = el('div', 'evidence-counts');
      for (const [amount, label] of [[summary.sessions, 'sessions'], [summary.subagents, 'Subagents'], [summary.compactions, 'compactions']]) {
        const item = el('span'); item.append(el('strong', '', n(amount).toLocaleString('en-US')), document.createTextNode(' ' + label)); counts.append(item);
      }
      const unassigned = n(snapshot.scope && snapshot.scope.unassigned_sessions);
      const note = state.scope === 'workspace' ? unassigned ? `${unassigned} local sessions lack workspace attribution and are excluded. See All local projects for their history.` : 'Matched to recorded working directories in this workspace.' : 'All recorded projects on this machine. Paired devices are excluded.';
      replace(evidence, [counts, el('p', 'evidence-note', note)]);
    }
    renderDays(snapshot && snapshot.days || []);
    const since = state.since;
    const current = state.connection.status === 'connected';
    set('since-heading', 'Recorded in this window');
    set('since-description', since ? 'Since ' + time(since.since) + ' · this scope & range · in memory only' : 'Newly recorded usage · resets on scope or range changes');
    const value = byId('since-value');
    replace(value, since ? [document.createTextNode('+' + compact(since.tokens) + ' tokens'), el('span', '', money(since.cost) + ' estimated' + (current ? '' : ' · stale'))] : [document.createTextNode('—')]);
    set('snapshot-time', snapshot ? 'Snapshot ' + time(snapshot.generated_at) + (current ? '' : ' · stale') : 'Waiting for a local snapshot');
    const limitations = (snapshot && Array.isArray(snapshot.limitations) ? snapshot.limitations : []).filter(item => typeof item === 'string' && !/costs are estimates at api list prices\.?$/i.test(item));
    replace(byId('limitations'), limitations.slice(0, 20).map(item => el('li', '', item)));
  }
  function renderDays(days) {
    const strip = byId('day-strip');
    const data = days.filter(day => day && /^\d{4}-\d{2}-\d{2}$/.test(text(day.date))).slice(-30);
    strip.hidden = data.length < 2;
    if (strip.hidden) { strip.replaceChildren(); return; }
    const chart = svg('svg', {viewBox: '0 0 600 38', preserveAspectRatio: 'none', role: 'img', 'aria-label': 'Recorded tokens by day. ' + data.map(day => `${shortDate(day.date)}: ${compact(day.tokens)}`).join('; ')});
    const maximum = Math.max(...data.map(day => n(day.tokens)), 1);
    chart.append(svg('line', {x1: 0, x2: 600, y1: 37, y2: 37, stroke: 'currentColor', 'stroke-opacity': '.3'}));
    const step = 600 / data.length;
    data.forEach((day, index) => {
      const height = n(day.tokens) / maximum * 32;
      const bar = svg('rect', {x: index * step + Math.min(3, step / 8), y: 37 - height, width: step - Math.min(6, step / 4), height, fill: 'currentColor', 'fill-opacity': index === data.length - 1 ? '.95' : '.38'});
      const title = svg('title'); title.textContent = shortDate(day.date) + ': ' + n(day.tokens).toLocaleString('en-US') + ' tokens'; bar.append(title); chart.append(bar);
    });
    const wrap = el('div'); const labels = el('div', 'day-labels'); labels.append(el('span', '', shortDate(data[0].date)), el('span', '', shortDate(data[data.length - 1].date))); wrap.append(chart, labels);
    replace(strip, [el('p', '', 'Recorded tokens by day'), wrap]);
  }
  function renderTree() {
    const snapshot = state.snapshot;
    const sessions = snapshot && Array.isArray(snapshot.sessions) ? snapshot.sessions.slice(0, 1000) : [];
    allGraph = forest(sessions);
    const scoped = sessions.filter(session => sessionFilter === 'all' || (sessionFilter === 'subagents') === Boolean(session.subagent));
    graph = forest(scoped);
    if (!graph.byKey.has(selected)) selected = graph.roots.length ? graph.roots[0].key : null;
    if (!graph.byKey.has(roving)) roving = selected;
    set('session-count', snapshot ? (sessionFilter === 'all' ? n(snapshot.sessions_total).toLocaleString('en-US') + ' recorded' : graph.byKey.size.toLocaleString('en-US') + (snapshot.truncated ? ' shown' : ' matching')) : '');
    const cap = byId('session-cap'); cap.hidden = !snapshot || !snapshot.truncated;
    cap.textContent = snapshot && snapshot.truncated ? 'Showing ' + graph.byKey.size + ' of ' + n(snapshot.sessions_total).toLocaleString('en-US') + ' sessions. The brief includes all matching usage.' : '';
    const tree = byId('session-tree');
    const previousScroll = tree.scrollTop;
    tree.replaceChildren();
    if (!graph.byKey.size) {
      const empty = el('div', 'empty-sessions');
      empty.append(el('strong', '', snapshot ? 'No recorded sessions in this view' : 'Waiting for your local snapshot'), el('p', '', snapshot ? sessionFilter !== 'all' ? 'Try All sessions or a wider date range.' : state.scope === 'workspace' ? 'Try a wider date range or All local projects. Older history may lack a recorded workspace path.' : 'Try a wider date range. Only usage available in local tool logs can appear.' : 'Your session outline will appear when monitoring is connected.'));
      tree.append(empty); byId('tree-help').hidden = true;
      return;
    }
    byId('tree-help').hidden = false;
    const ordinary = graph.roots.filter(session => !session.subagent);
    const unlinked = graph.roots.filter(session => session.subagent);
    for (const session of ordinary) tree.append(treeItem(session, 1));
    if (unlinked.length) {
      tree.append(el('div', 'tree-group-label', 'SUBAGENTS · PARENT NOT IN THIS OUTLINE'));
      for (const session of unlinked) tree.append(treeItem(session, 1));
    }
    // A scope change can leave the roving item below a collapsed ancestor.
    const visible = visibleItems();
    if (!visible.some(item => item.dataset.key === roving)) roving = visible[0] && visible[0].dataset.key;
    for (const item of tree.querySelectorAll('[role="treeitem"]')) item.tabIndex = item.dataset.key === roving ? 0 : -1;
    tree.scrollTop = previousScroll;
    persist();
  }
  function treeItem(session, level) {
    const children = graph.children.get(session.key) || [];
    const item = el('div', 'tree-item');
    item.setAttribute('role', 'treeitem'); item.setAttribute('aria-level', level); item.setAttribute('aria-selected', String(session.key === selected));
    item.tabIndex = session.key === roving ? 0 : -1;
    item.dataset.key = session.key; item.dataset.focus = 'session:' + session.key;
    const expanded = !collapsed.has(session.key);
    if (children.length) item.setAttribute('aria-expanded', String(expanded));
    const line = el('div', 'tree-line');
    if (children.length) {
      const toggle = el('button', 'tree-chevron', expanded ? '⌄' : '›'); toggle.type = 'button'; toggle.tabIndex = -1;
      toggle.setAttribute('aria-label', (expanded ? 'Collapse ' : 'Expand ') + (text(session.title) || 'session')); toggle.dataset.toggle = session.key; line.append(toggle);
    } else line.append(el('span', 'tree-chevron placeholder'));
    const copy = el('span', 'tree-copy');
    const title = text(session.title) || 'Untitled session';
    const role = session.subagent ? 'Subagent' : 'Main agent';
    const relation = session.subagent && !graph.parent.has(session.key) ? allGraph.parent.has(session.key) ? ' · parent hidden by filter' : session.parent_status === 'outside_scope' ? ' · parent outside scope' : session.parent_status === 'linked' ? ' · parent outside shown list' : ' · parent unavailable' : '';
    copy.append(el('span', 'tree-title', title), el('span', 'tree-meta', role + ' · ' + sourceLabel(session.source) + relation));
    const amount = el('span', 'tree-amount', session.usage_status === 'unavailable' ? '—' : compact(session.tokens)); amount.append(el('small', '', session.usage_status === 'unavailable' ? 'unavailable' : 'tokens'));
    line.append(copy, amount); line.title = title + '\n' + role + ' · ' + sourceLabel(session.source) + relation + '\n' + (session.usage_status === 'unavailable' ? 'Usage unavailable' : n(session.tokens).toLocaleString('en-US') + ' recorded tokens');
    item.append(line);
    if (children.length) { const group = el('div', 'tree-group'); group.setAttribute('role', 'group'); group.hidden = !expanded; for (const child of children) group.append(treeItem(child, level + 1)); item.append(group); }
    return item;
  }
  function visibleItems() {
    return [...byId('session-tree').querySelectorAll('[role="treeitem"]')].filter(item => !item.closest('[hidden]'));
  }
  function select(key, focus) {
    if (!allGraph.byKey.has(key)) return;
    if (!graph.byKey.has(key)) sessionFilter = 'all';
    selected = roving = key;
    let ancestor = allGraph.parent.get(key);
    while (ancestor) { collapsed.delete(ancestor); ancestor = allGraph.parent.get(ancestor); }
    renderTree(); renderDetail(); markControls(); persist(); send('selectSession', key);
    if (focus) restoreFocus('session:' + key);
  }
  function toggle(key) {
    if (collapsed.has(key)) collapsed.delete(key); else collapsed.add(key);
    roving = key; renderTree(); persist(); restoreFocus('session:' + key);
  }
  function renderDetail() {
    const detail = byId('session-detail');
    const session = graph.byKey.get(selected);
    if (!session) {
      const placeholder = el('div', 'detail-placeholder'); placeholder.append(el('span', '', '⌁'), el('h3', '', 'A closer look at the work'), el('p', '', 'Select a session to inspect its recorded usage, model, effort and context compactions.'));
      replace(detail, [placeholder]); return;
    }
    const kind = el('p', 'detail-kind', (session.subagent ? 'Subagent' : 'Main agent') + ' · ' + sourceLabel(session.source));
    const title = el('h3', 'detail-title', text(session.title) || 'Untitled session');
    const nodes = [kind, title];
    if (allGraph.parent.has(session.key)) {
      const owner = allGraph.byKey.get(allGraph.parent.get(session.key)); const parent = el('p', 'detail-parent', 'Recorded parent: ');
      const button = el('button', 'text-button', text(owner.title) || 'Untitled session'); button.type = 'button'; button.dataset.parent = owner.key; button.dataset.focus = 'parent:' + session.key; parent.append(button); nodes.push(parent);
    } else if (session.subagent) nodes.push(el('p', 'detail-parent', session.parent_status === 'outside_scope' ? 'Parent is outside the selected workspace or date range.' : session.parent_status === 'linked' ? 'The recorded parent is not included in this session list.' : 'Parent link unavailable in the recorded local metadata.'));
    const stats = el('div', 'detail-stats');
    for (const [value, label] of [[session.usage_status === 'unavailable' ? '—' : compact(session.tokens), 'recorded tokens'], [session.usage_status === 'unavailable' ? '—' : money(session.cost), 'estimated API cost']]) { const stat = el('p'); stat.append(el('strong', '', value), el('span', '', label)); stats.append(stat); }
    nodes.push(stats);
    const metadata = el('dl', 'metadata');
    const efforts = Array.isArray(session.efforts) ? session.efforts.filter(value => typeof value === 'string') : [];
    const compactions = typeof session.compactions === 'number' && Number.isFinite(session.compactions) && session.compactions >= 0 ? n(session.compactions).toLocaleString('en-US') + ' recorded' : 'Unavailable';
    const entries = [['Model', text(session.model) || 'Not recorded'], ['Recorded effort', efforts.length ? efforts.map(effortLabel).join(' · ') : 'Not recorded'], ['Compactions', compactions], ['Last record', session.end ? shortDate(session.end.slice(0, 10)) + ' · ' + time(session.end) : 'Unavailable']];
    for (const [label, value] of entries) metadata.append(el('dt', '', label), el('dd', '', value));
    nodes.push(metadata);
    if (session.usage_status !== 'unavailable') nodes.push(renderComposition(session));
    if (session.usage_in_parent) nodes.push(el('p', 'detail-note', 'This child’s recorded usage is already included in its Copilot parent and the overall totals. Do not add it again.'));
    if (session.usage_status === 'unavailable') nodes.push(el('p', 'detail-note', 'Token usage is unavailable in the recorded data. This is not a known zero.'));
    if (session.usage_status === 'partial') nodes.push(el('p', 'detail-note', 'Recorded usage is incomplete; these figures are a lower bound.'));
    if (session.source === 'copilot' && session.usage_status !== 'unavailable') nodes.push(el('p', 'detail-note', 'Cache figures reflect Copilot’s saved report. Custom-model bridges can record zero when provider cache usage is unavailable. Zero does not prove there were no cache hits; estimated cost may overstate the provider’s charge.'));
    const scope = state.snapshot && state.snapshot.from !== state.snapshot.to ? shortDate(state.snapshot.from) + ' — ' + shortDate(state.snapshot.to) : RANGE_LABELS[state.range];
    nodes.push(el('p', 'detail-scope', 'Usage & compactions: ' + scope + ' · ' + (state.scope === 'all' ? 'all local projects' : 'this workspace') + '. Effort reflects recorded metadata, never your current settings.'));
    if (session.detail_limited) nodes.push(el('p', 'detail-note', 'Some historical detail is unavailable. Missing metadata has not been reconstructed from other sessions.'));
    if (session.archived) nodes.push(el('p', 'detail-note', 'Retained history: the source log is no longer available. These figures come from your durable local ledger.'));
    if ((graph.children.get(session.key) || []).length) nodes.push(el('p', 'detail-note', session.source === 'copilot' ? 'Copilot’s recorded parent usage includes its children. Child breakdowns are not added again.' : 'Child rows are linked by recorded identities. This figure remains the session’s own recorded usage; child totals are not added here.'));
    replace(detail, nodes);
  }
  function renderComposition(session) {
    const parts = composition(session); const total = parts.reduce((sum, part) => sum + part.value, 0);
    const section = el('section', 'composition'); section.setAttribute('aria-label', 'Token composition'); section.append(el('h3', '', 'How the tokens are composed'));
    const bar = svg('svg', {viewBox: '0 0 1000 8', preserveAspectRatio: 'none', 'aria-hidden': 'true'});
    const defs = svg('defs'); const pattern = svg('pattern', {id: 'cache-hatch', patternUnits: 'userSpaceOnUse', width: 4, height: 4}); pattern.append(svg('path', {d: 'M-1,1 l2,-2 M0,4 l4,-4 M3,5 l2,-2', stroke: 'var(--desk-fg)', 'stroke-width': 1})); defs.append(pattern); bar.append(defs);
    let x = 0;
    for (const part of parts) {
      const width = total ? part.value / total * 1000 : 0;
      const fill = part.key === 'cache_read' ? 'url(#cache-hatch)' : part.key === 'output' ? 'var(--k-out)' : part.key === 'cache_write' ? 'var(--desk-subtle)' : 'var(--desk-fg)';
      bar.append(svg('rect', {x, width, height: 8, fill, stroke: part.key === 'cache_write' ? 'var(--desk-fg)' : 'none'})); x += width;
    }
    section.append(bar);
    const table = el('table'); table.setAttribute('aria-label', 'Recorded input, output and cache tokens'); const tbody = el('tbody');
    for (const part of parts) {
      const row = el('tr'); const heading = el('th'); heading.scope = 'row'; heading.append(el('span', 'composition-key ' + part.key), document.createTextNode(part.label));
      const value = el('td', '', compact(part.value)); value.title = part.value.toLocaleString('en-US') + ' tokens';
      row.append(heading, value, el('td', '', total ? Math.round(part.value / total * 100) + '%' : '—')); tbody.append(row);
    }
    table.append(tbody); section.append(table); return section;
  }
  function render() {
    const focused = focusToken();
    renderConnection(); renderBrief(); renderTree(); renderDetail(); markControls(); restoreFocus(focused);
  }
  document.addEventListener('click', event => {
    const target = event.target.closest('button, .tree-line');
    if (!target) return;
    if (target.dataset.action) {
      const allowed = ['refresh', 'start', 'openBrowser', 'resetBaseline', 'setupPython', 'openSettings'];
      if (allowed.includes(target.dataset.action) && !target.disabled) send(target.dataset.action);
    } else if (target.dataset.sessionFilter && ['all', 'main', 'subagents'].includes(target.dataset.sessionFilter)) {
      sessionFilter = target.dataset.sessionFilter; renderTree(); renderDetail(); markControls(); persist();
      if (selected) send('selectSession', selected);
    } else if (target.dataset.scope && ['workspace', 'all'].includes(target.dataset.scope) && !target.disabled) send('changeScope', target.dataset.scope);
    else if (target.dataset.range && ['today', '7d', '30d'].includes(target.dataset.range)) send('changeRange', target.dataset.range);
    else if (target.dataset.toggle) toggle(target.dataset.toggle);
    else if (target.dataset.parent) select(target.dataset.parent, true);
    else if (target.classList.contains('tree-line')) select(target.parentElement.dataset.key, true);
  });
  byId('session-tree').addEventListener('focusin', event => {
    const item = event.target.closest('[role="treeitem"]');
    if (!item) return;
    roving = item.dataset.key;
    for (const candidate of byId('session-tree').querySelectorAll('[role="treeitem"]')) candidate.tabIndex = candidate === item ? 0 : -1;
  });
  byId('session-tree').addEventListener('keydown', event => {
    const item = event.target.closest('[role="treeitem"]');
    if (!item || !['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', ' '].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const key = item.dataset.key; const items = visibleItems(); const index = items.indexOf(item);
    let next;
    if (event.key === 'ArrowDown') next = items[Math.min(index + 1, items.length - 1)];
    if (event.key === 'ArrowUp') next = items[Math.max(index - 1, 0)];
    if (event.key === 'Home') next = items[0];
    if (event.key === 'End') next = items[items.length - 1];
    if (event.key === 'ArrowRight') { if (item.getAttribute('aria-expanded') === 'false') toggle(key); else next = item.querySelector('[role="group"] [role="treeitem"]'); }
    if (event.key === 'ArrowLeft') { if (item.getAttribute('aria-expanded') === 'true') toggle(key); else if (graph.parent.has(key)) next = item.parentElement.closest('[role="treeitem"]'); }
    if (event.key === 'Enter' || event.key === ' ') select(key, true);
    if (next) { roving = next.dataset.key; next.focus(); }
  });
  window.addEventListener('message', event => {
    if (!event.data || event.data.type !== 'state' || !event.data.state || !event.data.state.connection) return;
    try {
      state = event.data.state;
      if (typeof state.selected_session === 'string' && state.selected_session !== selected) {
        selected = roving = state.selected_session;
        const incoming = forest(state.snapshot && Array.isArray(state.snapshot.sessions) ? state.snapshot.sessions : []);
        const session = incoming.byKey.get(selected);
        if (session && sessionFilter !== 'all' && (sessionFilter === 'subagents') !== Boolean(session.subagent)) sessionFilter = 'all';
        let ancestor = incoming.parent.get(selected);
        while (ancestor) { collapsed.delete(ancestor); ancestor = incoming.parent.get(ancestor); }
      }
      render();
      delete document.documentElement.dataset.jsError;
    } catch (error) {
      document.documentElement.dataset.jsError = 'render';
      set('connection-label', 'Agent desk could not render this snapshot. Refresh to retry.');
      console.error('AgentTelemetry Agent desk render failed', error && error.name);
    }
  });
  render(); send('ready');
}());
