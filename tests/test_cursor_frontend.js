'use strict';
// Synthetic UI contract checks only. No Cursor database, transcript or ledger.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),nodes=new Map(),day='2026-10-25',device='cursor-fixture-device';
const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:'',dataset:{},style:{},hidden:false,
  classList:{toggle(){},add(){},remove(){}},setAttribute(){},querySelector(){return null},querySelectorAll(){return []}});return nodes.get(id)};
const location={pathname:'/',search:'',hash:'#sessions'};
const context=vm.createContext({console,Date,Set,URLSearchParams,location,history:{state:null,replaceState(_s,_t,url){
  const parsed=new URL(url,'http://localhost');location.pathname=parsed.pathname;location.search=parsed.search;location.hash=parsed.hash;}},
  document:{addEventListener(){},getElementById:node,querySelector(){return null},querySelectorAll(){return []},documentElement:{}},
  getComputedStyle:()=>({getPropertyValue:()=> '#2468ac'}),
  fetch:async()=>({ok:true,json:async()=>({available:true,events:[]})})});
for(const file of ['static/core.js','static/charts.js','static/insights.js','static/timeline.js'])
  vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context,{filename:file});
const views=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),context,{filename:'static/views.js'});
const run=code=>vm.runInContext(code,context),json=code=>JSON.parse(run(`JSON.stringify(${code})`));

const claudeRecord={date:day,source:'claude',model:'Claude Sonnet 5',project:'fixture-project',ide:'Terminal',device,
  in:500,out:100,cr:80,cc:0,asst:1,user:1,tools:0,active:30,cost:.001,usage_status:'recorded',
  usage_availability:{input_output:'recorded',cache_read:'recorded',cache_write:'recorded'}};
const cursorRecord={date:day,source:'cursor',model:'Unknown',project:'Cursor',ide:'Cursor',device,
  in:0,out:0,cr:0,cc:0,asst:2,user:2,tools:3,active:60,cost:0,usage_status:'unavailable',
  usage_availability:{input_output:'unavailable',cache_read:'unavailable',cache_write:'unavailable',
    reasoning:'unavailable',response_output:'unavailable'}};
const cursorSession={id:'cursor-short',rename_id:'cursor-full-composer-id',source:'cursor',device,project:'Cursor',
  title:'Synthetic Cursor session',model:'Current Composer Setting',current_model:'Current Composer Setting',model_status:'unavailable',
  usage_status:'unavailable',usage_availability:{input_output:'unavailable',cache_read:'unavailable',cache_write:'unavailable',
    reasoning:'unavailable',response_output:'unavailable'},detail_status:'partial',rename_available:false,
  in:0,out:0,cr:0,cc:0,asst:2,user:2,tools:3,req:0,cost:0,
  days:{[day]:[0,0,0,0,0,2,2,3,0,60,0,0,0,0,0]},model_days:{Unknown:{[day]:[0,0,0,0,0,2,2,3,0,60,0,0,0,0,0]}},
  record_availability:{[day+'\tUnknown']:{input_output:'unavailable',cache_read:'unavailable',cache_write:'unavailable',
    reasoning:'unavailable',response_output:'unavailable'}}};
const cursorParent={id:'cursor-parent-short',rename_id:'cursor-parent-full-id',source:'cursor',device,title:'Synthetic parent'};
const cursorChild={...cursorSession,id:'cursor-child-short',rename_id:'cursor-child-full-id',subagent:true,
  subagent_parent:{id:cursorParent.rename_id,title:cursorParent.title,source:'cursor',device}};
run(`var cursorSession=${JSON.stringify(cursorSession)},cursorParent=${JSON.stringify(cursorParent)},cursorChild=${JSON.stringify(cursorChild)};
RAW={device:{id:${JSON.stringify(device)},name:'Synthetic'},devices:[],records:${JSON.stringify([claudeRecord,cursorRecord])},
  sessions:[cursorSession],session_children:[],sessions_total:1,hourly:[],tools:[],ctx:[],reads:[],skills:[],ai_lines:[],
  model_vendor:{'Claude Sonnet 5':'Anthropic','Unknown':'Other','Observed Model':'Anthropic','Second Model':'OpenAI'},
  pricing:{'Claude Sonnet 5':[2,10,0,0,.2]},prices:{},pricing_history:{}};
S.preset='all';S.metric='tokens';S.exactOnly=false;S.models.clear();S.provs.clear();S.projs.clear();S.ides.clear();S.devs.clear();
S.tools=new Set(ORDER);
renderUsageChange=()=>{};renderDeviceDistribution=()=>{};renderHighlights=()=>{};renderHeat=()=>{};renderTokenMix=()=>{};
renderCalendar=()=>{};renderMonthlyRhythmCard=()=>{};mk=()=>{};stackDS=()=>({});axes=()=>({});legendHTML=()=>'';
renderModelEff=()=>{};`);

assert.equal(run('SRC.cursor.exact'),false,'Cursor remains excluded from Exact tokens only while its token coverage is incomplete');
assert.equal(run('sessionUsageStatus(cursorSession)'),'unavailable');
assert.equal(run('sessionTokenValue(cursorSession)'),'Unavailable');
assert.equal(run('sessionCostValue(cursorSession)'),'Unavailable');
assert.equal(run('sessionCacheRate(cursorSession)'),'Unavailable','missing cache fields must not appear as a zero cache rate');
assert.equal(run(`sessionUsageField(cursorSession,'cr','0')`),'Unavailable');
assert.equal(run('sessionModelSummary(cursorSession).model'),'Unavailable');
assert.doesNotMatch(run('sessionModelHTML(cursorSession)'),/Current Composer Setting/,'a current composer setting is not a historical model label');
assert.match(run('sessionModelAvailabilityDetails(cursorSession)'),/not substituted/);
assert.match(run('cursorDetailStatusDetails(cursorSession)'),/incomplete/);
assert.equal(run(`reasoningEffortLabel({...cursorSession,thinkingDurationMs:3000})`),'','thinking duration is not displayed as a reasoning token/effort field');
assert.equal(run(`compactionDetails({source:'cursor',compactions:[]})`),'','Cursor hooks are not installed to supply compaction records');

// The real Overview Tool mix keeps a priced source in the share and adds
// Cursor's exact message/tool observations in a separate, non-denominator note.
run(`var overviewData={recs:${JSON.stringify([claudeRecord,cursorRecord])},sessions:[${JSON.stringify(cursorSession)}],
  hourly:[],r:{from:${JSON.stringify(day)},to:${JSON.stringify(day)}}};viewOverview(overviewData);`);
const toolHTML=node('toolShare').innerHTML;
assert.match(toolHTML,/cursor-activity-note/);
assert.match(toolHTML,/2 prompts · 2 replies · 3 tool calls/);
assert.match(toolHTML,/Token coverage is 1 unavailable/);
assert.match(toolHTML,/separate from the share/);
assert.equal((toolHTML.match(/class="bar-row/g)||[]).length,1,'the unavailable Cursor session creates no zero-value share bar');
assert.match(toolHTML,/Claude/);
assert.match(toolHTML,/100%/,'recorded-share denominator contains only the priced Claude record');
assert.match(node('toolShareSub').textContent,/share of tokens/);

run(`S.metric='messages';viewOverview(overviewData);`);
assert.equal((node('toolShare').innerHTML.match(/class="bar-row/g)||[]).length,2,
  'recorded Cursor replies can appear in the messages share without changing token or cost coverage');
assert.doesNotMatch(node('toolShare').innerHTML,/cursor-activity-note/);
run(`S.metric='tokens';S.models.add('Unknown');viewOverview(overviewData);`);
assert.doesNotMatch(node('toolShare').innerHTML,/cursor-activity-note/,'model-filtered activity is not assigned from the current composer setting');
run(`S.models.clear();S.exactOnly=true;viewOverview(overviewData);`);
assert.doesNotMatch(node('toolShare').innerHTML,/cursor-activity-note/,'Exact tokens only omits Cursor activity as well as Cursor usage');
assert.equal(run(`slice({from:${JSON.stringify(day)},to:${JSON.stringify(day)}}).sessions.some(s=>s.source==='cursor')`),false);

// The Models view labels unknown token/cache coverage while retaining the
// message row and without adding missing usage to the token-share denominator.
run(`S.exactOnly=false;S.metric='tokens';S.models.clear();S.provs.clear();S.tools=new Set(ORDER);
viewModels({recs:${JSON.stringify([claudeRecord,cursorRecord])},sessions:[${JSON.stringify(cursorSession)}],r:{from:${JSON.stringify(day)},to:${JSON.stringify(day)}}});`);
const modelHTML=node('modelTable').innerHTML;
assert.match(modelHTML,/>Unknown</);
assert.match(modelHTML,/Unavailable/);
assert.doesNotMatch(modelHTML,/Current Composer Setting/);
assert.match(modelHTML,/share of recorded tokens; usage coverage is incomplete/);

const partialCursorSession={...cursorSession,id:'cursor-partial-short',rename_id:'cursor-partial-full-id',
  title:'Synthetic partial Cursor session',model:'Observed Model',models:['Observed Model'],nmodels:1,
  model_status:'partial',usage_status:'partial',in:40,out:15,tok:55,cost:.0002,
  undated_usage:{in:8084,out:587,asst:1},
  usage_availability:{input_output:'partial',cache_read:'unavailable',cache_write:'unavailable',
    reasoning:'unavailable',response_output:'unavailable'},
  days:{[day]:[.0002,40,15,0,0,2,1,1,0,60,1,0,0,0,0]},
  model_days:{'Observed Model':{[day]:[.0002,40,15,0,0,1,1,1,0,30,1,0,0,0,0]},
    Unknown:{[day]:[0,20,5,0,0,1,0,0,0,30,0,0,0,0,0]}},
  record_availability:{[day+'\tObserved Model']:{input_output:'partial',cache_read:'unavailable',cache_write:'unavailable',
    reasoning:'unavailable',response_output:'unavailable'},
    [day+'\tUnknown']:{input_output:'partial',cache_read:'unavailable',cache_write:'unavailable',
      reasoning:'unavailable',response_output:'unavailable'}}};
assert.equal(run(`sessionModelSummary(${JSON.stringify(partialCursorSession)}).model`),'Observed Model',
  'partial model coverage retains an explicitly observed assistant model');
assert.match(run(`sessionModelHTML(${JSON.stringify(partialCursorSession)})`),/Partial model labels/);
assert.equal(run(`sessionTokenValue(${JSON.stringify(partialCursorSession)})`),'Partial · 55');
assert.equal(run(`sessionCacheRate(${JSON.stringify(partialCursorSession)})`),'Unavailable',
  'an absent cache receipt is not shown as a zero cache rate');
assert.equal(run(`sessionUsageField(${JSON.stringify(partialCursorSession)},'cr','0')`),'Unavailable');
assert.equal(run(`sessionCostValue(${JSON.stringify(partialCursorSession)})`),'Partial · $0.00',
  'priced rows remain a partial estimate when an Unknown model also has recorded tokens');
const knownOnlyPartial={...partialCursorSession,model_days:{'Observed Model':{[day]:[.0002,40,15,0,0,1,1,1,0,30,1]}}};
assert.deepEqual(json(`sessionUnpricedModels(${JSON.stringify(knownOnlyPartial)})`),[],
  'an unavailable model label with no Unknown token receipt does not create an unpriced-model warning');
assert.deepEqual(json(`sessionUnpricedModels(${JSON.stringify(partialCursorSession)})`),['Unknown'],
  'an Unknown bucket with positive recorded tokens keeps the cost warning');
run(`RAW.sessions=[${JSON.stringify(partialCursorSession)}];openSessionDetails(sessionRow(${JSON.stringify(partialCursorSession)}));`);
assert.match(node('drawerBody').innerHTML,/usage is partial/i);
assert.match(node('drawerBody').innerHTML,/Additional undated recorded usage \(whole session\): 8,671 tokens \(input 8,084; output 587\)\. Model: Unavailable\. Not included in selected range\./);
assert.match(node('drawerBody').innerHTML,/<span class="k">Tokens<\/span><span class="v">Partial · 55<\/span>/,
  'undated Cursor usage is disclosed in the drawer but does not change the selected-range total');
assert.match(node('drawerBody').innerHTML,/<span class="k">Est\. cost<\/span><span class="v">Partial · \$0\.00<\/span>/,
  'undated Cursor tokens do not produce an estimated cost');
assert.match(node('drawerBody').innerHTML,/cache read<\/span><span class="v">Unavailable/);
assert.match(node('drawerBody').innerHTML,/Observed Model/);
assert.doesNotMatch(node('drawerBody').innerHTML,/Current Composer Setting/);
assert.equal(run(`sessionUndatedUsageDetails({...cursorSession,undated_usage:{in:0,out:0,asst:1}})`),'',
  'no note is shown without recorded undated token counts');
assert.equal(run(`sessionUndatedUsageDetails({...cursorSession,source:'claude',undated_usage:{in:8084,out:587}})`),'',
  'the Cursor note is scoped to Cursor sessions');

const prev='2026-10-24',alpha='Observed Model',beta='Second Model';
const avA={input_output:'partial',cache_read:'unavailable',cache_write:'recorded',reasoning:'unavailable',response_output:'unavailable'};
const avB={input_output:'recorded',cache_read:'recorded',cache_write:'recorded',reasoning:'recorded',response_output:'recorded'};
const scopedSession={source:'cursor',usage_status:'partial',model_status:'partial',
  days:{[prev]:[0,20,2,0,0,1,1,0,0,10,1],[day]:[0,30,3,0,0,1,1,0,0,10,1]},
  model_days:{[alpha]:{[prev]:[0,20,2,0,0,1,1,0,0,10,1],[day]:[0,30,3,0,0,1,1,0,0,10,1]},
    [beta]:{[day]:[0,40,4,0,0,1,1,0,0,10,1]}},
  record_availability:{[prev+'\t'+alpha]:{input_output:'recorded',cache_read:'recorded',cache_write:'unavailable',reasoning:'recorded',response_output:'unavailable'},
    [prev+'\t'+beta]:avB,[day+'\t'+alpha]:avA,[day+'\t'+beta]:avB}};
const scoped=json(`(()=>{S.provs=new Set(['Anthropic']);S.models.clear();
  RAW.model_vendor[${JSON.stringify(alpha)}]='Anthropic';RAW.model_vendor[${JSON.stringify(beta)}]='OpenAI';
  const clipped=clipSession(${JSON.stringify(scopedSession)},{from:${JSON.stringify(day)},to:${JSON.stringify(day)}});
  const result={availability:clipped.usage_availability,recordCount:Object.keys(clipped.record_availability).length,
    input:sessionUsageField(clipped,'in','30'),cache:sessionUsageField(clipped,'cr','0')};
  S.provs.clear();return result;})()`);
assert.deepEqual(scoped.availability,avA,'Cursor field availability follows the selected day and provider');
assert.equal(scoped.recordCount,4,'the unfiltered record-quality map remains intact');
assert.equal(scoped.input,'Partial · 30');
assert.equal(scoped.cache,'Unavailable');

// Session type, delegation, and detailed local timeline all use Cursor's full
// composer identity while keeping unsupported title edits disabled.
run(`RAW.sessions=[${JSON.stringify(cursorParent)},${JSON.stringify(cursorChild)}];`);
assert.match(run('sessionTypeDetails(cursorChild)'),/data-session-parent/);
assert.match(run('sessionTypeDetails(cursorChild)'),/Synthetic parent/);
assert.match(run(`sessionDelegationDetails({source:'cursor',subagents:2})`),/2 subagents recorded/);
assert.match(run('sessionTimelineHTML(cursorSession)'),/data-timeline-id="cursor-full-composer-id"/);
assert.match(run('sessionTimelineHTML(cursorSession)'),/Unavailable/);
assert.match(run('sessionTimelineHTML(cursorSession)'),/Not recorded/,'Cursor model-call counts are not inferred from replies');
assert.equal(run('renameButton(cursorSession).includes("disabled")'),true);

(async()=>{
  let calls=0;
  context.fetch=async url=>{
    calls++;assert(url.includes('source=cursor'));
    assert(url.includes('id=cursor-full-composer-id'),'Cursor detail uses the full composer ID');
    return {ok:true,json:async()=>({available:true,events:[
      {timestamp:day+'T10:02:00Z',kind:'request',model:'Observed Model',input:120,output:45,
        usage_availability:{input_output:'recorded',cache_read:'unavailable'}},
    ]})};
  };
  run('S.models.clear();S.provs.clear();');
  const localNode={innerHTML:'',isConnected:true,closest:()=>null};
  await context.loadSessionTimeline(cursorSession,localNode,'requests');
  assert(localNode.innerHTML.includes('Observed Model'));
  assert(localNode.innerHTML.includes('cache read unavailable'));
  const remoteNode={innerHTML:'',isConnected:true,closest:()=>null};
  await context.loadSessionTimeline({...cursorSession,device:'remote-device'},remoteNode,'requests');
  assert.equal(calls,1,'remote Cursor detail is not fetched');
  assert(remoteNode.innerHTML.includes('unavailable'));
  process.stdout.write('Cursor frontend tests passed (synthetic data only).\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
