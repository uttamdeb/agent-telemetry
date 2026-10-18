/* Synthetic recorded-effort checks. No source logs, settings or ledger are read. */
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',classList:{toggle(){}},setAttribute(){}});return nodes.get(id)};
const context=vm.createContext({console,URLSearchParams,Date,Set,location:{pathname:'/',search:'',hash:'#sessions'},
  document:{addEventListener(){},getElementById:node,querySelector(){return null},querySelectorAll(){return []}},
  getComputedStyle:()=>({getPropertyValue:()=> '#000'})});
for(const file of ['static/core.js','static/charts.js','static/timeline.js'])
  vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context);
const views=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),context);
const run=code=>vm.runInContext(code,context),json=code=>JSON.parse(run(`JSON.stringify(${code})`));
run(`S.preset='custom';S.from='2026-10-01';S.to='2026-10-31';
RAW={device:{id:'local'},devices:[{id:'local',name:'Local'},{id:'remote',name:'Remote'}],
  records:[{date:'2026-10-18',source:'codex',model:'M1',project:'P',ide:'CLI',device:'local',in:100,out:10,cost:1}],
  hourly:[],sessions:[],tools:[],model_vendor:{M1:'OpenAI',M2:'Anthropic'},codex_effort:'max'};
var originalSession={id:'session1',rename_id:'session1-full',source:'codex',device:'local',project:'P',ide:'CLI',
  title:'Synthetic effort',model:'M1',models:['M1','M2'],nmodels:2,end:'2026-10-19T12:00:00Z',
  in:300,out:30,cost:3,user:2,asst:3,req:3,tools:4,reason:9,active:90,
  days:{'2026-10-18':[1,100,10,0,0,1,1,2,0,30,1,0,0,3,0],'2026-10-19':[2,200,20,0,0,2,1,2,0,60,2,0,0,6,0]},
  model_days:{M1:{'2026-10-18':[1,100,10,0,0,1,1,2,0,30,1,0,0,3,0]},M2:{'2026-10-19':[2,200,20,0,0,2,1,2,0,60,2,0,0,6,0]}},
  reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:{high:2},unrecorded:0},'2026-10-19\\tM2':{levels:{max:1},unrecorded:0}}}};
var copySession=()=>JSON.parse(JSON.stringify(originalSession));RAW.sessions=[copySession()];`);

// Date/model/provider scoping uses the same shared selection as the figures.
assert.equal(run(`reasoningEffortSummary(clipSession(originalSession,{from:'2026-10-18',to:'2026-10-18'})).label`),'High');
assert.equal(run(`reasoningEffortSummary(clipSession(originalSession,{from:'2026-10-19',to:'2026-10-19'})).label`),'Max');
assert.equal(run(`reasoningEffortSummary(slice().sessions[0]).label`),'Mixed');
run(`S.models=new Set(['M1']);`);
assert.equal(run(`reasoningEffortSummary(slice().sessions[0]).label`),'High');
run(`S.models.clear();S.provs=new Set(['Anthropic']);`);
assert.equal(run(`reasoningEffortSummary(slice().sessions[0]).label`),'Max');
run(`S.provs.clear();`);
const before=json('originalSession');
run(`var clipped=clipSession(originalSession,{from:'2026-10-18',to:'2026-10-18'});`);
assert.equal(run(`reasoningEffortSummary(clipSession(clipped,{from:'2026-10-19',to:'2026-10-19'})).label`),'Max','A clipped copy keeps its original metadata for rescoping');
assert.deepEqual(json('originalSession'),before,'Scoping never mutates durable metadata or accounting');
assert.deepEqual(json('clipped.reasoning_effort'),before.reasoning_effort);

// Recorded labels never come from current preferences, models or reasoning tokens.
for(const metadata of [null,{},[],{version:2,days:{}},{version:1,days:[]},{version:1,days:{}},
    {version:1,days:{'2026-10-18\tM1':{levels:{},unrecorded:2}}}]){
  run(`var missing={...copySession(),reasoning_effort:${JSON.stringify(metadata)}};`);
  assert.equal(run('reasoningEffortSummary(missing).label'),'Not recorded');
}
assert.equal(run(`reasoningEffortSummary(clipSession(originalSession,{from:'2020-01-01',to:'2020-01-02'})).label`),'Not recorded');
for(const [level,label] of Object.entries({none:'None',minimal:'Minimal',low:'Low',medium:'Medium',high:'High',xhigh:'Extra high',max:'Max',ultra:'Ultra'})){
  run(`var one={...copySession(),reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:{${JSON.stringify(level)}:1},unrecorded:0}}}};`);
  assert.equal(run('reasoningEffortSummary(one).label'),label);
}
run(`var partial={...copySession(),reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:{max:1},unrecorded:2}}}};`);
assert.equal(run('reasoningEffortSummary(partial).label'),'Max · partially recorded');
assert.match(run('reasoningEffortDetails(partial)'),/Some observations did not record/);
assert.match(run(`reasoningEffortDetails({...copySession(),reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:{low:1,ultra:1},unrecorded:0}}}})`),/Low “Light”/);
assert.match(run(`reasoningEffortDetails({...copySession(),reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:{ultra:1},unrecorded:0}}}})`),/Codex delegation setting/);

// Old archives, remote rows, unsupported tools and malformed data remain safe.
for(const source of ['cursor','copilot','claude-desktop','opencode','unknown'])
  assert.equal(run(`reasoningEffortLabel({...copySession(),source:${JSON.stringify(source)}})`),'');
for(const level of ['none','minimal','ultra'])
  assert.equal(run(`reasoningEffortSummary({...copySession(),source:'claude',reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:{${JSON.stringify(level)}:1},unrecorded:0}}}}).label`),'Not recorded');
assert.match(run(`reasoningEffortDetails({...partial,source:'claude'})`),/Inline child usage is not assigned the parent's effort/);
run(`var old={...copySession(),source:'claude',archived:true,device:'remote'};delete old.reasoning_effort;`);
assert.match(run('reasoningEffortLabel(old)'),/Not recorded/);
for(const levels of [null,[],{high:true},{high:-1},{high:1.5},{high:9007199254740992},{unknown:1},{'<img src=x onerror=alert(1)>':1}]){
  run(`var bad={...copySession(),reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:${JSON.stringify(levels)},unrecorded:0}}}};`);
  assert.equal(run('reasoningEffortSummary(bad).label'),'Not recorded');
}
run(`var unsafe={...copySession(),reasoning_effort:{version:1,days:{
  '2026-10-18\\t<img src=x onerror=alert(1)>':{levels:{low:1,max:1},unrecorded:0},
  '<script>\\tM1':{levels:{high:1},unrecorded:0},
  '2026-10-99\\tM1':{levels:{high:1},unrecorded:0},
  '2026-10-18\\tM1\\textra':{levels:{high:1},unrecorded:0},
  '2026-10-18\\tM1':{levels:{high:1},unrecorded:true}}}};`);
const unsafeHTML=run('reasoningEffortDetails(unsafe)');
assert.match(unsafeHTML,/&lt;img src=x/);assert.doesNotMatch(unsafeHTML,/<img src=x|<script>/);
assert.doesNotMatch(unsafeHTML,/2026-10-99/);
assert.equal(run(`reasoningEffortSummary({...copySession(),reasoning_effort:{version:1,days:{'2026-10-18\\tM1':{levels:{high:1},unrecorded:-1}}}}).label`),'High · partially recorded');

// Effort observations are not another metric; every accounting vector is intact.
run(`var plain=copySession();delete plain.reasoning_effort;
var decorated=clipSession(originalSession,range()),undecorated=clipSession(plain,range());`);
for(const field of ['cost','in','out','cr','cc','asst','user','tools','active','req','reason','days','model_days','model','models','nmodels'])
  assert.deepEqual(json(`decorated[${JSON.stringify(field)}]`),json(`undecorated[${JSON.stringify(field)}]`),field+' is unchanged');
const originalTotals=json('totals(slice().recs)');
run(`RAW.sessions=[partial,old];`);
assert.deepEqual(json('totals(slice().recs)'),originalTotals);
run(`S.devs=new Set(['remote']);`);assert.equal(run('slice().sessions.length'),1);
run(`S.devs.clear();S.tools=new Set(['codex']);`);assert.equal(run('slice().sessions.length'),1);
run(`S.tools=new Set(ORDER);S.ides=new Set(['missing']);`);assert.equal(run('slice().sessions.length'),0);
run(`S.ides.clear();S.projs=new Set(['missing']);`);assert.equal(run('slice().sessions.length'),0);
run(`S.projs.clear();RAW.sessions=[copySession()];viewSessions(slice());`);
const html=node('sessTable').innerHTML;
assert.match(html,/Recorded effort: Mixed/);
assert.match(html,/aria-label="Recorded effort: Mixed\. Recorded values: High, Max\./);
assert.match(html,/<td>M1 /);assert.doesNotMatch(html,/M1 \(High\)|M1 high/i,'Effort does not become model identity');
run(`var opened='';openDrawer=html=>{opened=html};loadSessionTimeline=()=>{};openSession(0);`);
const drawer=run('opened');
assert.ok(drawer.indexOf('>Model</span>')<drawer.indexOf('>Recorded effort</span>'));
assert.ok(drawer.indexOf('>Recorded effort</span>')<drawer.indexOf('>Started</span>'));
assert.match(drawer,/Recorded effort by date and model/);
assert.match(drawer,/2026-10-18/);assert.match(drawer,/2026-10-19/);
assert.match(drawer,/>M1<\/b> · High/);assert.match(drawer,/>M2<\/b> · Max/);
assert.match(drawer,/Selected date range and model\/provider filters/);
assert.match(drawer,/separate from reasoning token counts/);
assert.match(drawer,/own scales/);
assert.doesNotMatch(drawer,/2 observations|3 observations|2 requests|3 requests/,'Metadata observations are not a displayed request metric');
console.log('Recorded effort scope, rendering, partial/missing/none, escaping and unchanged accounting checks passed.');
