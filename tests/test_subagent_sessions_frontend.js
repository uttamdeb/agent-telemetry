/* Synthetic Sessions UX checks; never reads tool logs or a user's ledger. */
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),nodes=new Map(),buttons=new Map();
function element(){
  const classes=new Set(),attributes={};
  return {innerHTML:'',textContent:'',attributes,
    classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),
      toggle:(k,on)=>on?classes.add(k):classes.delete(k)},
    setAttribute:(k,v)=>{attributes[k]=String(v)},focus(){this.focused=true}};
}
const node=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)};
for(const type of ['all','subagents','other'])buttons.set(type,element());
const location={pathname:'/',search:'',hash:'#sessions'};
const context=vm.createContext({console,URLSearchParams,Date,Set,location,
  history:{state:null,replaceState(_state,_title,next){
    const url=new URL(next,'https://fixture.invalid');
    Object.assign(location,{pathname:url.pathname,search:url.search,hash:url.hash});
  }},
  document:{addEventListener(){},getElementById:node,querySelectorAll:()=>[],
    querySelector(selector){const m=selector.match(/^\[data-session-type="(all|subagents|other)"\]$/);return m?buttons.get(m[1]):null}},
  getComputedStyle:()=>({getPropertyValue:()=> '#000'})});
for(const file of ['static/core.js','static/charts.js','static/insights.js','static/timeline.js'])
  vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context);
const views=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),context);
const run=code=>vm.runInContext(code,context),json=code=>JSON.parse(run(`JSON.stringify(${code})`));

// Only allowed values survive a URL round trip; unrelated URL state is retained.
for(const type of ['subagents','other']){
  location.search='?theme=dark&side=collapsed&session_type='+type;
  run('filtersFromURL();filtersToURL()');
  assert.equal(run('S.sessionType'),type);
  const params=new URLSearchParams(location.search);
  assert.equal(params.get('session_type'),type);
  assert.equal(params.get('theme'),'dark');assert.equal(params.get('side'),'collapsed');
}
for(const value of ['', 'all','main','<img src=x onerror=alert(1)>']){
  location.search='?session_type='+encodeURIComponent(value);
  run('S.sessionType="subagents";filtersFromURL();filtersToURL()');
  assert.equal(run('S.sessionType'),'all');
  assert.equal(new URLSearchParams(location.search).has('session_type'),false);
}

run(`S.preset='custom';S.from='2026-10-01';S.to='2026-10-31';S.search='';
RAW={device:{id:'local'},devices:[{id:'local',name:'Local'},{id:'remote',name:'Remote'}],
  records:[{date:'2026-10-18',source:'codex',model:'M',project:'P',device:'local',in:100,cost:1}],hourly:[],
  sessions:[
    {id:'child001',rename_id:'child001-full-native-id',source:'codex',title:'Same task',project:'P',model:'M',device:'local',subagent:true,in:40,out:4,cost:.4,end:'2026-10-18T10:00:00',subagents:1},
    {id:'child002',rename_id:'child002-full-native-id',source:'claude',title:'<img src=x onerror=alert(1)>',project:'P',model:'M',device:'remote',subagent:true,in:20,out:2,cost:.2,end:'2026-10-18T11:00:00',archived:true},
    {id:'parent01',source:'codex',title:'Same task',project:'P',model:'M',device:'local',subagent:false,in:100,out:10,cost:1,end:'2026-10-18T12:00:00',subagents:2},
    {id:'legacy01',source:'claude',title:'Legacy row',project:'P',model:'M',device:'local',in:10,out:1,cost:.1,end:'2026-10-18T13:00:00'}],
  model_vendor:{M:'OpenAI'}};`);

const originalSlice=json('slice()');
for(const type of ['all','subagents','other']){
  run(`S.sessionType='${type}'`);
  assert.deepEqual(json('slice()'),originalSlice,'A Sessions-only type selection must not change shared analytics slices');
}
assert.deepEqual(json('sessionTypeCounts(sessionRows(slice()))'),{all:4,subagents:2,other:2});
assert.deepEqual(json(`sessionRowsForType(sessionRows(slice()),'subagents').map(s=>s.id)`),['child001','child002']);
assert.deepEqual(json(`sessionRowsForType(sessionRows(slice()),'other').map(s=>s.id)`),['parent01','legacy01']);
assert.equal(json(`sessionRowsForType(sessionRows(slice()),'all')`).length,4);

// Search recognizes existing IDs as well as titles; global source/date/device
// filters and search are applied before the type counts are displayed.
run(`S.search='CHILD001-full-native-id'`);
assert.deepEqual(json('sessionRows(slice()).map(s=>s.id)'),['child001']);
run(`S.search='';S.tools=new Set(['codex'])`);
assert.deepEqual(json('sessionTypeCounts(sessionRows(slice()))'),{all:2,subagents:1,other:1});
run(`S.tools=new Set(ORDER);S.devs=new Set(['remote'])`);
assert.deepEqual(json('sessionTypeCounts(sessionRows(slice()))'),{all:1,subagents:1,other:0});
run(`S.devs.clear();S.from='2026-10-19'`);
assert.deepEqual(json('sessionTypeCounts(sessionRows(slice()))'),{all:0,subagents:0,other:0});
run(`S.from='2026-10-01';S.search='Same task'`);
assert.deepEqual(json('sessionTypeCounts(sessionRows(slice()))'),{all:2,subagents:1,other:1});

run(`S.search='';S.sessionType='subagents';viewSessions(slice())`);
const html=node('sessTable').innerHTML;
assert.match(html,/session-subagent/);
assert.match(html,/>Subagent</);
assert.match(html,/<button[^>]+class="session-open"/,'A session title is keyboard actionable');
assert.match(html,/aria-label="Open session details for Subagent:/);
assert.match(html,/child001-full-native-id/);
assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
assert.doesNotMatch(html,/<img src=x/);
assert.equal(buttons.get('subagents').attributes['aria-pressed'],'true');
assert.equal(buttons.get('all').attributes['aria-pressed'],'false');
assert.equal(node('sessTypeCountAll').textContent,'4');
assert.equal(node('sessTypeCountSubagents').textContent,'2');
assert.equal(node('sessTypeCountOther').textContent,'2');
assert.equal(buttons.get('other').attributes['aria-label'],'Main agents, 2');
const typeControls=[...fs.readFileSync(path.join(root,'index.html'),'utf8')
  .matchAll(/<button[^>]*data-session-type="([^"]+)"[^>]*>([^<]*)<span/g)]
  .map(match=>[match[1],match[2].trim()]);
assert.deepEqual(typeControls,[['all','All sessions'],['other','Main agents'],['subagents','Subagents']],
  'The selector follows the requested visible labels and order while preserving saved-link values');
assert.deepEqual(json('SESS_CACHE.map(s=>s.id)'),['child002','child001'],'Row indices must match only the displayed, sorted sessions');

run(`S.sessionType='other';viewSessions(slice())`);
assert.match(node('sessHint').textContent,/^2 main agent sessions active in range/);
assert.deepEqual(json('SESS_CACHE.map(s=>s.id)'),['legacy01','parent01'],'The wording change must not reclassify unmarked older sessions');
run(`S.search='Same task';viewSessions(slice())`);
assert.match(node('sessHint').textContent,/^1 main agent session active in range/);

run(`S.search='child001';S.sessionType='other';viewSessions(slice())`);
assert.match(node('sessTable').innerHTML,/No main agent sessions match/);
assert.doesNotMatch(node('sessTable').innerHTML,/other sessions/i);
assert.match(node('sessTable').innerHTML,/data-session-type-reset/);
assert.match(node('sessTable').innerHTML,/Show all sessions/);
assert.equal(json('SESS_CACHE').length,0);

// Main agents is a display label for unmarked rows, with the source limitation
// retained. Delegation counts and Claude side tokens keep their original scope.
assert.match(run(`sessionTypeDetails({subagent:true})`),/Subagent session/);
assert.match(run(`sessionTypeDetails({})`),/>Main agent session</);
assert.match(run(`sessionTypeDetails({})`),/not record|unmarked|Not marked/i);
assert.doesNotMatch(run(`sessionTypeDetails({})`),/confirmed primary|never delegated/i);
assert.match(run(`sessionDelegationDetails({source:'codex',subagents:2})`),/whole session/);
assert.match(run(`sessionDelegationDetails({source:'codex',subagents:2})`),/not tied|not limited|regardless|whole.session/i);
const side=run(`sessionDelegationDetails({source:'claude',subagent:false,side:1000})`);
assert.match(side,/already included/);assert.match(side,/not additional/);
assert.equal(run(`sessionDelegationDetails({source:'claude',subagent:true,side:1000})`),'','A child transcript must not label its own side flag as evidence that it delegated');
assert.doesNotMatch(run(`sessionDelegationBadge({source:'cursor',subagents:2})`),/start marker/,'Cursor retains counts rather than Codex start events');
assert.doesNotMatch(run(`sessionTypeDetails({subagent:true,parent_thread_id:'inferred-parent'})`),/data-parent|<a\b|inferred-parent/,'No parent relationship is manufactured from unsupported fields');

// Claude's source directory can associate retained child transcripts with a
// parent even when the parent has no inline sidechain or spawn-count records.
const children=run(`sessionDelegationBadge({source:'claude',subagent_children:25,subagents:3,side:1000})`);
assert.match(children,/25 subagents recorded/);
assert.match(children,/At least 25 recorded child transcripts/);
assert.doesNotMatch(children,/28 subagents/,'Observed child transcripts and spawn counts are independent evidence');
const childDetail=run(`sessionDelegationDetails({source:'claude',subagent_children:25,subagents:3,side:1000})`);
assert.match(childDetail,/whole session/);
assert.match(childDetail,/does not follow date or model filters/);
assert.match(childDetail,/adds no tokens or cost/);
assert.match(childDetail,/3 subagents recorded/);
assert.doesNotMatch(childDetail,/28 subagents/,'Independent evidence must not be summed');
assert.match(childDetail,/already included/,'Inline usage explanations remain separate');
for(const value of ['undefined','0','-1','1.5','"<img src=x>"'])
  assert.equal(run(`sessionDelegationBadge({source:'claude',subagent_children:${value}})`),'','Missing/invalid child evidence must not assert zero or a count');
run(`var parentScope=clipSession({source:'claude',subagent_children:25,days:{'2026-10-18':[1,100,10,0,0,1,1,0,0,0,1]}},{from:'2026-10-18',to:'2026-10-18'})`);
assert.equal(run('parentScope.subagent_children'),25,'The count keeps its stated whole-session scope');
assert.equal(run(`findSubagents({sessions:[{source:'claude',tools:200,cost:20,subagent_children:2},{source:'claude',tools:200,cost:20,subagent_children:1}]})`),null,'Observed child transcripts rule out a no-delegation recommendation');
assert.match(run(`findSubagents({sessions:[{source:'claude',tools:200,cost:20,title:'Unknown one'},{source:'claude',tools:200,cost:20,title:'Unknown two'},{source:'claude',tools:200,cost:20,subagent_children:2}]}).body`),/Delegation was recorded in 1 other session/);

run(`S.search='';S.sessionType='all';viewSessions(slice());var opened='';openDrawer=html=>{opened=html};loadSessionTimeline=()=>{};openSession(2)`);
assert.match(run('opened'),/Subagent session/);
assert.match(run('opened'),/session-type-detail/);
assert.doesNotMatch(run('opened'),/<img src=x/);
run('openSession(0)');
assert.match(run('opened'),/<div class="eyebrow">Main agent session<\/div>/);
assert.match(run('opened'),/Not marked as a subagent/);
assert.doesNotMatch(run('opened'),/Other session|confirmed primary|never delegated/i);
run(`S.sessionType='subagents';S.search='hidden';resetFilters();filtersToURL()`);
assert.equal(run('S.sessionType'),'all','Reset and Clear all must also clear the Sessions-only selector');
assert.equal(run('S.search'),'');
assert.equal(new URLSearchParams(location.search).has('session_type'),false);
// Parent navigation resolves full device/tool/native identity, even while the
// table shows only children or a search excludes its parent. It keeps filters.
run(`RAW.sessions[2].rename_id='parent01-full-native-id';
RAW.sessions[2].days={'2026-10-18':[1,100,10,0,0,1,1,0,0,0,1]};
RAW.sessions[2].model='Historical';RAW.sessions[2].models=['Historical','Fable'];RAW.sessions[2].nmodels=2;
RAW.sessions[2].model_days={Historical:{'2026-10-17':[20,2000,200,0,0,20]},Fable:{'2026-10-18':[1,100,10,0,0,1]}};
RAW.sessions[0].subagent_parent={id:'parent01-full-native-id',source:'codex',device:'local',title:'Parent <safe>'};
S.preset='custom';S.from='2026-10-18';S.to='2026-10-18';S.search='child001';S.sessionType='subagents';`);
const parentLink=run('sessionTypeDetails(RAW.sessions[0])');
assert.match(parentLink,/data-session-parent/);
assert.match(parentLink,/Parent &lt;safe&gt;/);
assert.doesNotMatch(parentLink,/<safe>/);
assert.match(parentLink,/aria-label="Open parent session/);
const stateBefore=json('[S.search,S.sessionType,S.preset,S.from,S.to]');
const rawParentBefore=json('RAW.sessions[2]');
run(`openSessionParent(JSON.stringify(['local','codex','parent01-full-native-id']))`);
assert.match(run('opened'),/<h2>Same task<\/h2>/);
assert.match(run('opened'),/Parent session\. Figures follow the selected date and model filters/);
assert.match(run('opened'),/>Models in selected range<\/span><span class="v">Fable<\/span>/,'Opening a parent scopes its displayed models through the shared drawer flow');
assert.doesNotMatch(run('opened'),/>Historical</);
assert.deepEqual(json('RAW.sessions[2]'),rawParentBefore,'Parent navigation must not overwrite whole-session model metadata');
assert.deepEqual(json('[S.search,S.sessionType,S.preset,S.from,S.to]'),stateBefore);
run(`RAW.sessions.push({...RAW.sessions[2]});opened='unchanged';openSessionParent(JSON.stringify(['local','codex','parent01-full-native-id']))`);
assert.equal(run('opened'),'unchanged','Ambiguous native IDs never open an arbitrary parent');
assert.doesNotMatch(run('sessionTypeDetails(RAW.sessions[0])'),/data-session-parent/);
run(`RAW.sessions.pop();opened='unchanged';openSessionParent(JSON.stringify(['remote','codex','parent01-full-native-id']))`);
assert.equal(run('opened'),'unchanged','A same-named session on another device is not a parent match');
run(`delete RAW.sessions[2].days;delete RAW.sessions[2].model_days;
RAW.sessions[2].end='2026-09-01T10:00:00';RAW.sessions[2].cost=123;
openSessionParent(JSON.stringify(['local','codex','parent01-full-native-id']))`);
assert.match(run('opened'),/whole-session figures/);
assert.match(run('opened'),/cannot be scoped to your date or model filters/);
assert.doesNotMatch(run('opened'),/Figures follow the selected date and model filters/);
// Copilot inline children are a Sessions-only projection. Their receipt usage
// must never enter Overview/Cost/Optimize slices a second time.
run(`resetFilters();S.preset='custom';S.from='2026-10-18';S.to='2026-10-18';
  S.tools=new Set(['copilot']);S.search='';S.sessionType='all';
  var copilotParent={id:'cp-parent',rename_id:'copilot-full-parent',source:'copilot',title:'SDK parent',
    project:'SDK project',ide:'VS Code',device:'local',model:'M',
    days:{'2026-10-18':[1,100,10,0,0,1,1,0,0,0,1]},
    model_days:{M:{'2026-10-18':[1,100,10,0,0,1,1,0,0,0,1]}}};
  RAW.sessions.push(copilotParent);
  RAW.session_children=[
    {id:'sdk-child',rename_id:'copilot-full-parent/agent/child-full-id',source:'copilot',
      title:'Review <unsafe>',project:'SDK project',ide:'VS Code',device:'local',subagent:true,usage_in_parent:true,
      rename_available:false,model:'M',start:'2026-10-18T10:00:00',end:'2026-10-18T11:00:00',
      subagent_parent:{id:'copilot-full-parent',title:'SDK parent',source:'copilot',device:'local'},
      days:{'2026-10-18':[.2,20,5,0,0,0,0,0,0,0,0]},model_days:{M:{'2026-10-18':[.2,20,5,0,0,0,0,0,0,0,0]}},
      exact_days:{'2026-10-18':[.2,20,5,0,0,0,0,0,0,0,0]},exact_model_days:{M:{'2026-10-18':[.2,20,5,0,0,0,0,0,0,0,0]}},
      activity_dates:['2026-10-18'],copilot_sdk:{usage_status:'recorded',recorded_fields:['in','out']},compactions:[]},
    {id:'sdk-missing',rename_id:'copilot-full-parent/agent/missing',source:'copilot',title:'Missing child usage',
      project:'SDK project',ide:'VS Code',device:'local',subagent:true,usage_in_parent:true,rename_available:false,
      start:'2026-10-18T10:00:00',end:'2026-10-18T11:00:00',days:{},model_days:{},exact_days:{},exact_model_days:{},
      activity_dates:['2026-10-18'],copilot_sdk:{usage_status:'unavailable'},compactions:[]},
    {id:'sdk-idle',rename_id:'copilot-full-parent/agent/idle',source:'copilot',title:'Idle child',
      project:'SDK project',device:'local',subagent:true,usage_in_parent:true,
      start:'2026-10-17T10:00:00',end:'2026-10-19T11:00:00',days:{},activity_dates:['2026-10-17','2026-10-19']}];`);
const analyticsBefore=json('slice()');
assert.deepEqual(json('sessionTypeCounts(sessionRows(slice()))'),{all:3,subagents:2,other:1});
assert.deepEqual(json('slice()'),analyticsBefore,'Inclusive children must not enter analytics slices');
run(`S.sessionType='subagents';viewSessions(slice())`);
assert.match(node('sessTable').innerHTML,/Review &lt;unsafe&gt;/);
assert.match(node('sessTable').innerHTML,/Not recorded for this child/);
assert.doesNotMatch(node('sessTable').innerHTML,/Idle child|SDK parent/);
run(`S.search='child-full-id'`);
assert.deepEqual(json('sessionRows(slice()).map(s=>s.id)'),['sdk-child']);
run(`S.search='';S.models=new Set(['M'])`);
assert.deepEqual(json('sessionRowsForType(sessionRows(slice())).map(s=>s.id)'),['sdk-child']);
run(`S.models.clear();S.ides=new Set(['Other editor'])`);
assert.equal(json('sessionRows(slice())').length,0);
run(`S.ides.clear();S.devs=new Set(['remote'])`);
assert.equal(json('sessionRows(slice())').length,0);
run(`S.devs.clear();S.projs=new Set(['Other project'])`);
assert.equal(json('sessionRows(slice())').length,0);
run(`S.projs.clear();S.exactOnly=true`);
assert.deepEqual(json('sessionRowsForType(sessionRows(slice())).map(s=>s.id)'),['sdk-child']);
run(`S.exactOnly=false;viewSessions(slice());openSession(SESS_CACHE.findIndex(s=>s.id==='sdk-child'))`);
assert.match(run('opened'),/already included in its Copilot parent/);
assert.match(run('opened'),/data-session-parent/);
assert.match(run('opened'),/Not recorded/);
assert.doesNotMatch(run('opened'),/Your prompts<\/span>|Tool calls<\/span>/);
run(`openSessionParent(JSON.stringify(['local','copilot','copilot-full-parent']))`);
assert.match(run('opened'),/SDK parent/);
assert.equal(run('S.sessionType'),'subagents');
assert.deepEqual(json('slice()'),analyticsBefore);

console.log('Subagent Sessions state, recorded evidence, parent navigation, scope and escaping checks passed.');
