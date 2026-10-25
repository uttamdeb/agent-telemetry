'use strict';
// Synthetic UI contract checks only. No dashboard server, source logs or ledger.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const {validatePalette}=require('./validate_palette');
const root=path.resolve(__dirname,'..'),nodes=new Map(),day='2026-10-25',model='Preview-only unknown model',pricedModel='Gemini 3.8 Flash';
const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',dataset:{},style:{},hidden:false,
  classList:{toggle(){},add(){},remove(){}},setAttribute(){},querySelector(){return {addEventListener(){},toggleAttribute(){}}},querySelectorAll(){return []}});return nodes.get(id)};
const location={pathname:'/',search:'',hash:'#sessions'};
const context=vm.createContext({console,Date,Set,URLSearchParams,location,history:{state:null,replaceState(_s,_t,url){const parsed=new URL(url,'http://localhost');location.pathname=parsed.pathname;location.search=parsed.search;location.hash=parsed.hash;}},
  document:{addEventListener(){},getElementById:node,querySelector(){return null},querySelectorAll(){return []},documentElement:{}},
  getComputedStyle:()=>({getPropertyValue:()=> '#2468ac'}),fetch:async()=>({ok:true,json:async()=>({available:true,events:[]})})});
for(const file of ['static/core.js','static/charts.js','static/insights.js','static/timeline.js'])
  vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context,{filename:file});
const views=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),context,{filename:'static/views.js'});
vm.runInContext(fs.readFileSync(path.join(root,'static/maintenance.js'),'utf8'),context,{filename:'static/maintenance.js'});
const run=code=>vm.runInContext(code,context),json=code=>JSON.parse(run(`JSON.stringify(${code})`));
const vector=[0,12345,678,9000,120,7,3,2,0,240,7,120,0,84,0];
const record={date:day,source:'antigravity',model,project:'antigravity-demo',ide:'Antigravity IDE',device:'fixture-device',
  in:12345,out:678,cr:9000,cc:120,reason:84,asst:7,req:7,user:3,tools:2,active:240,cost:0};
// Checked-in list-price tuple for this known model; the unknown preview model has no rate row.
const knownPrice=[.75,3.75,0,0,.075];
const pricedRecord={date:day,source:'antigravity',model:pricedModel,project:'antigravity-demo',ide:'Antigravity IDE',device:'fixture-device',
  in:4200,out:900,cr:1200,cc:0,reason:90,asst:4,req:4,user:2,tools:1,active:120,
  cost:(4200*knownPrice[0]+900*knownPrice[1]+1200*knownPrice[4])/1e6};
const session={id:'a19a4b25',rename_id:'a19a4b25-7e63-4f5c-9d11-2c86a12e2a01',source:'antigravity',title:'Synthetic Antigravity session',
  project:'antigravity-demo',model,models:[model],nmodels:1,ide:'Antigravity IDE',device:'fixture-device',start:day+'T10:00:00Z',end:day+'T10:10:00Z',
  ...record,usage_status:'recorded',usage_availability:{input_output:'recorded',cache_read:'recorded',cache_write:'recorded',reasoning:'recorded',response_output:'unavailable'},
  days:{[day]:vector},model_days:{[model]:{[day]:vector}},model_scoped:{model,models:[model],nmodels:1,scope:'selected'},
  reasoning_effort:{version:1,days:{[day+'\t'+model]:{levels:{high:7},unrecorded:0}}},
  compactions:[{date:day,timestamp:day+'T10:05:00Z',trigger:'threshold',before:2400000,after:850000}],subagent_children:1};
run(`S.preset='custom';S.from=${JSON.stringify(day)};S.to=${JSON.stringify(day)};S.models.clear();S.provs.clear();
RAW={device:{id:'fixture-device',name:'Synthetic'},devices:[],records:${JSON.stringify([record,pricedRecord])},sessions:${JSON.stringify([session])},session_children:[],
  sessions_total:1,hourly:[],tools:[{date:${JSON.stringify(day)},source:'antigravity',device:'fixture-device',name:'Read',count:3}],
  ctx:[],reads:[],skills:[],ai_lines:[],model_vendor:{${JSON.stringify(model)}:'Google',${JSON.stringify(pricedModel)}:'Google'},
  pricing:{${JSON.stringify(pricedModel)}:${JSON.stringify(knownPrice)}},prices:{},pricing_history:{}};
var session=sessionRow(RAW.sessions[0]);`);

assert.equal(run('SRC.antigravity.label'),'Antigravity');
assert.equal(run('SRC.antigravity.provider'),'Google');
assert.deepEqual(json('ORDER.slice(-2)'),['hermes','antigravity']);
assert.ok(run('toolIcon("antigravity")').includes('/static/tool-icons/antigravity.svg'));
assert.ok(run('toolLabel("antigravity")').includes('Antigravity'));
assert.ok(fs.readFileSync(path.join(root,'static/tool-icons/antigravity.svg'),'utf8').includes('<svg'));
location.search='?range=custom&from='+day+'&to='+day+'&tools=antigravity&ides=Antigravity+IDE';
run('filtersFromURL()');
assert.deepEqual(json('[...S.tools]'),['antigravity']);
assert.deepEqual(json('[...S.ides]'),['Antigravity IDE']);
run('filtersToURL()');
assert.match(location.search,/tools=antigravity/);assert.match(location.search,/ides=Antigravity\+IDE/);

assert.equal(run('reasoningEffortSummary(session).label'),'High');
assert.match(run('reasoningEffortDetails(session)'),/own scales/);
assert.match(run('sessionUsageField(session,"cr",fmtNum(session.cr))'),/9,000/);
assert.equal(run('sessionUsageField(session,"response_out","0")'),'Unavailable');
assert.equal(run('sessionUsageField({...session,usage_status:"unavailable"},"asst","7")'),'7',
  'reply counts remain independent of token subtype availability');
assert.equal(run('sessionCostValue(session)'),'Unavailable');
assert.match(run('sessionUsageNote(session)'),/unavailable, not free/);
assert.equal(run('sessionCacheRate({...session,usage_availability:{cache_read:"unavailable"}})'),'Unavailable');

run('renderModelEff=()=>{};mk=()=>{};viewModels({recs:RAW.records,sessions:RAW.sessions,r:range()})');
const modelHTML=node('modelTable').innerHTML;
assert.match(modelHTML,/Antigravity/);assert.match(modelHTML,/Unavailable/);
assert.match(modelHTML,/No API price row for Preview-only unknown model/);
assert.match(modelHTML,/Google/);
assert.match(run('overviewSpendSummary(totals(RAW.records),dateList(range()),RAW.records).value'),/^Partial · /);
run('viewSessions({recs:RAW.records,sessions:RAW.sessions,r:range()})');
assert.match(node('sessTable').innerHTML,/Antigravity/);
assert.match(node('sessTable').innerHTML,/Estimated cost and rate are unavailable, not free/);
let opened='';run('openDrawer=html=>{opened=html};openSessionDetails(session)');
assert.match(run('opened'),/response output token subtype \(whole session\)/i);
assert.match(run('opened'),/>Assistant replies<\/span><span class="v">7/);
assert.match(run('opened'),/Recorded effort/);

const priorDay='2026-10-24',alphaModel='Preview Alpha',betaModel='Preview Beta';
const vAlpha=[0,10,2,3,4,1,1,0,0,60,1,0,0,1,0],vBeta=[0,20,4,6,8,2,1,0,0,90,2,0,0,2,0];
const scopedAvailabilitySession={source:'antigravity',usage_status:'recorded',
  usage_availability:{input_output:'recorded',cache_read:'recorded',cache_write:'recorded',reasoning:'recorded',response_output:'recorded'},
  days:{[priorDay]:vAlpha,[day]:vBeta},model_days:{[alphaModel]:{[priorDay]:vAlpha,[day]:vAlpha},[betaModel]:{[priorDay]:vBeta,[day]:vBeta}},
  record_availability:{
    [priorDay+'\t'+alphaModel]:{input_output:'recorded',cache_read:'recorded',cache_write:'unavailable',reasoning:'recorded',response_output:'unavailable'},
    [priorDay+'\t'+betaModel]:{input_output:'partial',cache_read:'recorded',cache_write:'recorded',reasoning:'partial',response_output:'unavailable'},
    [day+'\t'+alphaModel]:{input_output:'recorded',cache_read:'partial',cache_write:'recorded',reasoning:'recorded',response_output:'partial'},
    [day+'\t'+betaModel]:{input_output:'partial',cache_read:'unavailable',cache_write:'unavailable',reasoning:'unavailable',response_output:'recorded'}}};
const scoped=json(`(()=>{
  RAW.model_vendor[${JSON.stringify(alphaModel)}]='Google';RAW.model_vendor[${JSON.stringify(betaModel)}]='Anthropic';
  S.models.clear();S.provs.clear();S.provs.add('Google');
  const first=clipSession(${JSON.stringify(scopedAvailabilitySession)},{from:${JSON.stringify(priorDay)},to:${JSON.stringify(priorDay)}});
  S.provs.clear();S.models.add(${JSON.stringify(betaModel)});
  const second=clipSession(${JSON.stringify(scopedAvailabilitySession)},{from:${JSON.stringify(day)},to:${JSON.stringify(day)}});
  const result={first:first.usage_availability,second:second.usage_availability,
    firstMapSize:Object.keys(first.record_availability).length,secondMapSize:Object.keys(second.record_availability).length,
    secondInput:sessionUsageField(second,'in','20')};
  S.models.clear();S.provs.clear();return result;
})()`);
assert.deepEqual(scoped.first,{input_output:'recorded',cache_read:'recorded',cache_write:'unavailable',reasoning:'recorded',response_output:'unavailable'});
assert.deepEqual(scoped.second,{input_output:'partial',cache_read:'unavailable',cache_write:'unavailable',reasoning:'unavailable',response_output:'recorded'});
assert.equal(scoped.firstMapSize,4);assert.equal(scoped.secondMapSize,4,'scoping leaves the complete record map intact');
assert.equal(scoped.secondInput,'Partial · 20','session detail values use date/model/provider-scoped availability');

const events=[
  {timestamp:day+'T10:01:00Z',kind:'tool',label:'Read',status:'error',model},
  {timestamp:day+'T10:02:00Z',kind:'tool',label:'Run',status:'done',model},
  {timestamp:day+'T10:03:00Z',kind:'subagent',label:'Subagent started',agent:1,model},
  {timestamp:day+'T10:04:00Z',kind:'checkpoint',label:'Preview-only intent checkpoint',model,input:37,output:4,
    usage_availability:{input_output:'recorded',cache_read:'unavailable',cache_write:'unavailable',reasoning:'unavailable',response_output:'unavailable'}},
  {timestamp:day+'T10:05:00Z',kind:'compaction',trigger:'threshold',before:2400000,after:850000},
];
const activity=run(`timelineEventsHTML({available:true,events:${JSON.stringify(events)}},session,'activity')`);
assert.match(activity,/Preview-only intent checkpoint/);assert.match(activity,/Input 37/);assert.match(activity,/Output 4/);
assert.match(activity,/Cache read unavailable/);
assert.match(activity,/Subagent started/);assert.match(activity,/Context threshold/);
const allEvents=run(`timelineEventsHTML({available:true,events:${JSON.stringify(events)}},session,'all')`);
assert.match(allEvents,/Tool · Read/);assert.match(allEvents,/Status · Error/);assert.match(allEvents,/Status · Done/);
const compactView=run(`timelineEventsHTML({available:true,events:${JSON.stringify(events)}},session,'compactions')`);
assert.match(compactView,/Completed compaction/);assert.doesNotMatch(compactView,/Recorded checkpoint/);
assert.equal((activity.match(/Completed compaction/g)||[]).length,1,'source and ledger compactions deduplicate by matching timestamp');
assert.match(run(`sessionTimelineHTML({...session,usage_status:'partial'})`),/Partial · 22\.1K/);

const maintenance=run(`renderMaintenanceDiagnostics({sources:[{source:'antigravity',status:'available',read:2,discovered:2,errors:0}],
  missing_prices:[${JSON.stringify(model)}],unsupported_formats:[{source:'antigravity',format:'legacy_pb',count:2,
    hint:'Older conversation files are counted but not parsed; current database logs are supported.'}]})`);
assert.match(maintenance,/Models without an estimate/);assert.match(maintenance,/Preview-only unknown model/);
assert.match(maintenance,/2 older conversation files found/);assert.match(maintenance,/Antigravity/);
assert.doesNotMatch(maintenance,/\/Users\/|\/home\//);
const palette=validatePalette();assert.equal(palette.sources,9);assert.equal(palette.themes,2);
process.stdout.write('Antigravity frontend tests passed (mock data only).\n');
