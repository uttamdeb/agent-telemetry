/* Optional extension seam (core.js registry + the views.js hooks): mock DOM, no extension installed. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const coreSource = fs.readFileSync(path.join(root, 'static/core.js'), 'utf8');
const views = fs.readFileSync(path.join(root, 'static/views.js'), 'utf8');
const shell = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

/* ---- a tiny DOM: just enough for registerView ---- */
function dom() {
  const byId = new Map();
  function el(tag, id) {
    const node = {
      tag, id: id || '', dataset: {}, children: [], parentNode: null, innerHTML: '', className: '', textContent: '',
      after(other) { const i = this.parentNode.children.indexOf(this); this.parentNode.children.splice(i + 1, 0, other); adopt(other, this.parentNode); },
      before(other) { const i = this.parentNode.children.indexOf(this); this.parentNode.children.splice(i, 0, other); adopt(other, this.parentNode); },
      appendChild(other) { this.children.push(other); adopt(other, this); return other; },
      querySelector(selector) {
        const match = /^button\[data-v="(.+)"\]$/.exec(selector);
        return match ? this.children.find(c => c.tag === 'button' && c.dataset.v === match[1]) || null : null;
      },
    };
    if (id) byId.set(id, node);
    return node;
  }
  function adopt(node, parent) {
    node.parentNode = parent;
    if (node.id) byId.set(node.id, node);
  }
  const tabs = el('nav', 'tabs');
  for (const id of ['overview', 'sessions', 'storage']) {
    const b = el('button'); b.dataset.v = id; tabs.appendChild(b);
  }
  const main = el('main');
  const cost = el('section', 'v-cost'); cost.className = 'view';
  const storage = el('section', 'v-storage'); storage.className = 'view';
  main.appendChild(cost); main.appendChild(storage);
  const document = {
    addEventListener() {},
    createElement: tag => el(tag),
    getElementById: id => byId.get(id) || null,
    querySelector: selector => selector === '.view' ? cost : null,
    querySelectorAll: () => [],
  };
  return {document, tabs, main, byId};
}

function core() {
  const d = dom();
  const context = vm.createContext({document: d.document, console, URLSearchParams, Set, Date, location: {search: '', hash: ''},
    getComputedStyle: () => ({getPropertyValue: () => '#000'})});
  d.byId.set('tabs', d.tabs);
  vm.runInContext(coreSource, context, {filename: 'static/core.js'});
  return Object.assign(d, {context, run: code => vm.runInContext(code, context)});
}

const render = () => ({});
const baseView = {id: 'extra', title: 'Extra', label: 'Extra <b>', icon: '<path d="M1 1"/>', after: 'sessions',
  html: '<div id="extraBody"></div>', render, badge: () => 3};

/* ---- nothing registered: the lists are empty and views.js keeps its own views ---- */
{
  const c = core();
  assert.equal(c.run('EXT.views.length+EXT.panels.length'), 0);
  vm.runInContext(views.slice(0, views.indexOf('let LOCAL_SERVERS=')), c.context, {filename: 'static/views.js'});
  assert.deepEqual(JSON.parse(c.run('JSON.stringify(Object.keys(VIEW_TITLES))')),
    ['overview', 'cost', 'models', 'tools', 'projects', 'sessions', 'storage', 'local-servers']);
  assert.doesNotMatch(shell, /data-v="optimize"|id="v-optimize"|\/static\/mcp\.js|\/static\/insights\.js/);
  assert.match(shell, /id="mcpTable"/, 'the Tools tab keeps its MCP usage table');
}

/* ---- registerView builds the nav button and section, and refuses collisions ---- */
{
  const c = core();
  c.context.baseView = baseView;
  assert.equal(c.run('registerView(baseView)'), true);
  const labels = c.tabs.children.map(b => b.dataset.v);
  assert.deepEqual(labels, ['overview', 'sessions', 'extra', 'storage'], 'the button follows its `after` view');
  const button = c.tabs.children[2];
  assert.match(button.innerHTML, /<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M1 1"\/><\/svg>/);
  assert.match(button.innerHTML, /Extra &lt;b&gt;/, 'the label is escaped');
  assert.match(button.innerHTML, /id="navCount-extra"/);
  const section = c.byId.get('v-extra');
  assert.equal(section.className, 'view');
  assert.equal(section.innerHTML, '<div id="extraBody"></div>');
  assert.equal(c.main.children.indexOf(section), c.main.children.indexOf(c.byId.get('v-storage')) - 1, 'the section precedes Storage');

  assert.equal(c.run('registerView(baseView)'), false, 'a second registration of one id is ignored');
  for (const bad of [
    '{...baseView,id:"cost"}',                     // collides with an existing section
    '{...baseView,id:"Bad"}', '{...baseView,id:"x\\"><img>"}', '{...baseView,id:""}', '{...baseView,id:7}',
    '{...baseView,id:"no-render",render:undefined}', '{...baseView,id:"no-title",title:undefined}', 'null',
  ]) assert.equal(c.run(`registerView(${bad})`), false, bad);
  assert.equal(c.run('EXT.views.length'), 1);

  c.run('registerView({id:"plain",title:"Plain",after:"missing",html:"",render(){}})');
  assert.equal(c.tabs.children[c.tabs.children.length - 1].dataset.v, 'plain', 'an unknown `after` appends');
  assert.doesNotMatch(c.tabs.children[c.tabs.children.length - 1].innerHTML, /nav-count/, 'no badge, no counter');

  assert.equal(c.run('registerSettingsPanel({html:()=>"<p>x</p>"})'), true);
  assert.equal(c.run('registerSettingsPanel({})'), false);
  assert.equal(c.run('registerSettingsPanel(null)'), false);
  assert.equal(c.run('EXT.panels.length'), 1);
}

/* ---- views.js: titles, dispatch, badge and the error path ---- */
function renderHarness(extViews, session) {
  const host = {innerHTML: '<i>shell</i>', dataset: {}};
  const badge = {textContent: ''};
  const errors = [];
  const filtersNode = {style: {display: ''}, classList: {toggle() {}}};
  const nodes = {'v-extra': host, 'v-cost': {innerHTML: '', dataset: {}}, 'navCount-extra': badge, filters: filtersNode};
  const context = vm.createContext({Date, Set, console: {error: (...args) => errors.push(args.join(' ')), log() {}}, Object,
    RAW: {records: [], device: {id: 'local'}, sessions: []}, S: {view: 'extra', devs: new Set(), tools: new Set(['codex'])},
    VIEW_TITLES: {extra: 'Extra'}, EXT: {views: extViews, panels: []},
    document: {querySelectorAll: () => [], getElementById: id => nodes[id] || {textContent: '', style: {}},
      documentElement: {dataset: {}}},
    syncRangeUI() {}, syncMetricUI() {}, buildFilterPanels() {}, renderPills() {}, renderSidebarDevices() {}, filtersToURL() {},
    providerOf: () => 'p', hasPrev: () => false, filteredRecords: () => [], activeQuotaSources: () => [],
    syncSearchBoxes() {}, viewCost() {}, slice: () => ({recs: [{model: 'M', in: 1}], sessions: []}), MODEL_RANK: {}});
  context.session = session;
  vm.runInContext(views.slice(views.indexOf('const VIEW_HTML={};'), views.indexOf('function syncSearchBoxes(')), context);
  return {context, host, badge, errors};
}
{
  const calls = [];
  const h = renderHarness([{id: 'extra', title: 'Extra', render: d => calls.push(d.recs.length), badge: d => d.recs.length + 4}]);
  vm.runInContext('renderAll()', h.context);
  assert.deepEqual(calls, [1], 'the registered view renders with the shared slice');
  assert.equal(h.badge.textContent, 5, 'the badge is shown after each render');

  const broken = renderHarness([{id: 'extra', title: 'Extra', render() { throw new Error('boom'); }, badge() { throw new Error('count'); }}]);
  vm.runInContext('renderAll()', broken.context);
  assert.match(broken.context.document.documentElement.dataset.jsError, /render extra: boom/);
  assert.ok(broken.errors.some(e => /view badge failed/.test(e)), 'a failing badge is reported, never fatal');

  const none = renderHarness([]);
  vm.runInContext("S.view='cost'; VIEW_TITLES.cost='Cost'; renderAll()", none.context);
  assert.equal(none.context.document.documentElement.dataset.jsError, undefined, 'no extension, no extra behaviour');
}

/* ---- the Settings drawer calls panel hooks and drops a stale load ---- */
const start = views.indexOf('async function openSettings(){');
const end = views.indexOf('/* Offline app (PWA)', start);
assert(start >= 0 && end > start, 'Settings lifecycle function is present');
const openSettingsSource = views.slice(start, end);

function drawerHarness(fetchImpl, panels) {
  let renderCount = 0;
  const drawer = {isConnected: true, firstElementChild: null};
  function openDrawer(html) {
    if (drawer.firstElementChild) drawer.firstElementChild.isConnected = false;
    drawer.firstElementChild = {isConnected: true, html};
  }
  const context = vm.createContext({document: {getElementById: id => id === 'drawerBody' ? drawer : null},
    EXT: {views: [], panels}, openDrawer, fetch: fetchImpl, renderSettings: () => { renderCount++; }});
  vm.runInContext(openSettingsSource, context, {filename: 'static/views.js:openSettings'});
  return {context, drawer, openDrawer, get renderCount() { return renderCount; }};
}
function panel() {
  const log = {begins: 0, closes: 0, generation: 0, active: false};
  return {log, html: () => '<section id="p"></section>',
    begin() { log.begins++; log.generation++; log.active = true; return log.generation; },
    current: token => log.active && token === log.generation,
    close() { log.closes++; log.generation++; log.active = false; }};
}
const wait = () => new Promise(resolve => setTimeout(resolve, 0));
(async () => {
  let release;
  const p = panel();
  const race = drawerHarness(() => new Promise(resolve => { release = resolve; }), [p]);
  const pending = race.context.openSettings();
  await wait();
  assert.match(race.drawer.firstElementChild.html, /Loading/);
  race.openDrawer('<section>Session details</section>');
  release({ok: true, json: async () => ({claude_cleanup_days: 30})});
  await pending;
  assert.strictEqual(race.renderCount, 0, 'a late Settings response must not overwrite a different drawer');
  assert.match(race.drawer.firstElementChild.html, /Session details/);
  assert.strictEqual(p.log.closes, 1, 'the replaced Settings session invalidates its panel');

  const failedPanel = panel();
  const failed = drawerHarness(async () => ({ok: false, json: async () => ({error: 'do not render this'})}), [failedPanel]);
  await failed.context.openSettings();
  assert.strictEqual(failed.renderCount, 0);
  assert.match(failed.drawer.firstElementChild.html, /Could not read settings/);
  assert.strictEqual(failedPanel.log.closes, 1);

  const okPanel = panel();
  const ok = drawerHarness(async () => ({ok: true, json: async () => ({})}), [okPanel]);
  await ok.context.openSettings();
  assert.strictEqual(ok.renderCount, 1, 'a current load renders');
  assert.strictEqual(okPanel.log.closes, 0);

  const bare = drawerHarness(async () => ({ok: true, json: async () => ({})}), []);
  await bare.context.openSettings();
  assert.strictEqual(bare.renderCount, 1, 'without panels Settings renders as before');

  assert.ok(views.includes('${EXT.panels.map(p=>p.html()).join("")}'), 'Settings renders each panel\'s markup');
  console.log('Optional extension seam: registration, collisions, dispatch, badge, Settings panels and the empty default passed.');
})().catch(error => { console.error(error); process.exit(1); });
