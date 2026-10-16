const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const context = {
  URLSearchParams,
  esc: value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])),
  fmtTok: value => String(value),
  fmtUSD: value => '$' + Number(value).toFixed(2),
  range: () => ({from:'2026-10-01',to:'2026-10-31'}),
  sessionDays: session => session.days || {},
  compactionsInRange: (session, r) => (session.compactions || []).filter(e => e.date >= r.from && e.date <= r.to),
  passModel: model => model === 'Model A',
  document: {querySelector: () => null},
  fetch: async () => ({ok:true,json:async()=>({available:true,events:[]})}),
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/timeline.js','utf8'), context);

const session = {
  source:'codex', rename_id:'full-id-123', days:{'2026-10-02':[0.5,10,5,2,1,1,1,0,0,1,1]},
  compactions:[{timestamp:'2026-10-03T11:12:00Z',date:'2026-10-03',trigger:'unknown'}],
};
const html = context.sessionTimelineHTML(session);
assert(html.includes('Daily usage in the selected range'));
assert(html.includes('2026-10-02'));
assert(html.includes('Completed compaction'));
assert(html.includes('trigger not recorded'));
assert(html.includes('data-timeline-id="full-id-123"'));
const legacyHtml = context.sessionTimelineHTML({days:{'2026-10-02':[0,10,5,2,1,1,1,0,0,1]}});
assert(legacyHtml.includes('Not recorded'));
const sdkHtml = context.sessionTimelineHTML({source:'copilot',copilot_sdk:{usage_status:'unavailable'},days:{'2026-10-02':[0,0,0,0,0,1,1,0,0,1,0]}});
assert(sdkHtml.includes('Unavailable'),'SDK missing usage must not become a recorded zero');
const boundary = '2026-10-01T20:00:00Z';
const localBoundary = new Date(boundary);
assert.strictEqual(context.timelineDate(boundary), `${localBoundary.getFullYear()}-${String(localBoundary.getMonth()+1).padStart(2,'0')}-${String(localBoundary.getDate()).padStart(2,'0')}`);

const eventHTML = context.timelineEventsHTML({available:true,truncated:true,events:[
  {timestamp:'2026-10-04T10:00:00Z',kind:'request',model:'Model B',input:7},
  {timestamp:'2026-10-04T10:01:00Z',kind:'request',model:'Model A',input:0,output:4},
  {timestamp:'2026-10-04T10:01:30Z',kind:'tool',model:'Model B',label:'filtered-tool'},
  {timestamp:'2026-10-04T10:02:00Z',kind:'tool',label:'<img src=x onerror=alert(1)>'},
  {timestamp:'2026-10-04T10:03:00Z',kind:'user_turn'},
]});
assert(eventHTML.includes('Partial source detail'));
assert(!eventHTML.includes('Model B'));
assert(eventHTML.includes('in 0'));
assert(eventHTML.includes('&lt;img'));
assert(!eventHTML.includes('<img src=x'));
assert(eventHTML.includes('Your turn'));
assert(!eventHTML.includes('filtered-tool'));
context.S = {models:new Set(['Model A']),provs:new Set()};
const filteredHTML = context.timelineEventsHTML({available:true,events:[
  {timestamp:'2026-10-04T10:00:00Z',kind:'request',input:3},
  {timestamp:'2026-10-04T10:01:00Z',kind:'request',model:'Model A',input:4},
]});
assert(!filteredHTML.includes('Usage not recorded'));
assert(filteredHTML.includes('in 4'));

async function latestRequestWins(){
  let resolveFirst;
  const firstResponse = new Promise(resolve => { resolveFirst = resolve; });
  let calls = 0;
  context.fetch = async url => {
    calls++;
    assert(url.includes('id=full-id-123'));
    assert(url.includes('from=2026-10-01'));
    assert(url.includes('to=2026-10-31'));
    return calls === 1 ? firstResponse : {ok:true,json:async()=>({available:true,events:[
      {timestamp:'2026-10-04T10:00:00Z',kind:'tool',label:'new-session'}]})};
  };
  const node = {innerHTML:'',isConnected:true};
  const a = context.loadSessionTimeline(session,node);
  const b = context.loadSessionTimeline(session,node);
  await b;
  resolveFirst({ok:true,json:async()=>({available:true,events:[
    {timestamp:'2026-10-04T10:00:00Z',kind:'tool',label:'old-session'}]})});
  await a;
  assert(node.innerHTML.includes('new-session'));
  assert(!node.innerHTML.includes('old-session'));
  const archivedNode = {innerHTML:'',isConnected:true};
  await context.loadSessionTimeline({...session,archived:true},archivedNode);
  assert.strictEqual(calls,2);
  assert(archivedNode.innerHTML.includes('unavailable'));
}

latestRequestWins().then(() => process.stdout.write('session timeline frontend tests passed\n'));
