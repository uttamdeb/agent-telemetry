/* Cross-language numeric parity on the same synthetic fixture as Python tests. */
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'tests/fixtures/mcp_insights_parity.json'), 'utf8'));
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML: '', hidden: false});
  return nodes.get(id);
};
const context = vm.createContext({console, URLSearchParams, Set, Date, location: {search: '', hash: ''},
  document: {addEventListener() {}, getElementById: node, querySelector() { return null; }},
  getComputedStyle: () => ({getPropertyValue: () => '#000'})});
for (const file of ['static/core.js', 'static/charts.js', 'static/insights.js']) {
  vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context, {filename: file});
}
const views = fs.readFileSync(path.join(root, 'static/views.js'), 'utf8');
vm.runInContext(views.slice(0, views.indexOf('document.getElementById("tabs").addEventListener')),
  context, {filename: 'static/views.js'});
const run = code => vm.runInContext(code, context);
const payloadJson = JSON.stringify(fixture.payload);
run(`RAW=${payloadJson};`);
const {from, to} = fixture.range;
const local = fixture.payload.device.id;
const records = fixture.payload.records.filter(row => row.device === local);
const current = records.filter(row => row.date >= from && row.date <= to);
const previous = records.filter(row => row.date >= '2026-10-03' && row.date <= '2026-10-04');
const expected = fixture.expected;

const projectChange = JSON.parse(run(`JSON.stringify(usageChange(${JSON.stringify(current)},${JSON.stringify(previous)},'tokens','project'))`));
assert.equal(projectChange.current, expected.current_tokens);
assert.equal(projectChange.previous, expected.baseline_tokens);
assert.equal(projectChange.delta, expected.delta_tokens);
assert.deepEqual(Object.fromEntries(projectChange.rows.map(row => [
  row.key.split('\\').pop() === 'Alpha Workspace' ? 'alpha' : 'beta', row.current
])), expected.current_by_project);

const tokenTypes = JSON.parse(run(`JSON.stringify(usageChange(${JSON.stringify(current)},${JSON.stringify(previous)},'tokens','kind'))`));
assert.deepEqual(Object.fromEntries(tokenTypes.rows.map(row => [row.key, row.current])), expected.current_token_types);
assert.deepEqual(Object.fromEntries(tokenTypes.rows.map(row => [row.key, row.previous])), expected.baseline_token_types);

const codexCurrent = current.filter(row => row.source === 'codex' && row.date === '2026-10-05');
const costParts = JSON.parse(run(`JSON.stringify(usageChange(${JSON.stringify(codexCurrent)},[],'cost','kind'))`));
assert.equal(costParts.current, 0.001);
for (const [key, value] of Object.entries(expected.october_5_codex_cost_parts)) {
  assert.ok(Math.abs((costParts.rows.find(row => row.key === key)?.current || 0) - value) < 1e-12,
    `dated price split mismatch for ${key}`);
}

const cache = JSON.parse(run(`JSON.stringify(findCacheWaste({sessions:RAW.sessions}))`));
assert.equal(cache.id, 'cache-waste');
assert.equal(cache.impact, 0);
const thinkingRows = current.filter(row => row.source === 'codex' || row.source === 'claude');
const thinking = JSON.parse(run(`JSON.stringify(findThinking({recs:${JSON.stringify(thinkingRows)}}))`));
assert.equal(thinking.id, 'thinking');
assert.ok(Math.abs(thinking.impact - 0.000132) < 1e-12);
run(`S.tools.clear();S.tools.add('codex');S.provs.clear();S.models.clear();S.projs.clear();S.ides.clear();S.devs.clear();S.exactOnly=false;`);
const retry = JSON.parse(run(`JSON.stringify(findRetryTax({r:{from:'2026-10-05',to:'2026-10-06'}}))`));
assert.equal(retry.id, 'retry-tax');
assert.ok(Math.abs(retry.impact - 2) < 1e-12);
console.log('Python/UI parity fixture matches comparison partitions, dated token-cost splits, and Optimize thresholds.');
