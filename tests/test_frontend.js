/* Development checks only. The application itself still has no Node dependency. */
const fs=require('fs'), path=require('path'), vm=require('vm'), assert=require('assert');
const root=path.resolve(__dirname,'..');
const document={addEventListener(){},getElementById(){return null},querySelector(){return null}};
const context=vm.createContext({document,console,URLSearchParams,Set,Date,location:{search:'',hash:''}});
vm.runInContext(fs.readFileSync(path.join(root,'static/core.js'),'utf8'),context);
const views=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),context);
const run=code=>vm.runInContext(code,context);
run(`RAW={device:{id:'Mac'},devices:[{id:'Mac',name:'Mac'},{id:'PC',name:'PC'}],
 records:[],sessions:[],hourly:[],tools:[],pricing:{'Claude Opus 5.5 (US)':[4.4,22,5.5,8.8,.22]},
 prices:{'Claude Opus 5.5 (fast)':[8,40,10,16,.4]}};
 S.tools=new Set(['copilot','codex','claude']);`);
assert.equal(run(`priceOf('Claude Opus 5.5 (US)','2026-10-02')[0]`),4.4);
assert.equal(run(`priceOf('Claude Opus 5.5 (fast)')[0]`),8);
run(`RAW.pricing_history={'Claude Opus 5.5 (US)':[['2026-09-01',[5.5,27.5,6.875,11,.55]]]};`);
assert.equal(run(`priceOf('Claude Opus 5.5 (US)','2026-08-01')[0]`),5.5);
run(`RAW.records=[{date:'2026-10-02',source:'copilot',device:'Mac',model:'M',project:'P',ide:'VS Code',in:500,out:50,cost:1,
 exact:{in:100,out:10,asst:1,user:1,req:1,cost:.2}}]; S.exactOnly=true;`);
assert.equal(run(`slice({from:'2026-10-02',to:'2026-10-02'}).recs[0].in`),100);
assert.equal(run(`slice({from:'2026-10-02',to:'2026-10-02'}).recs[0].cost`),.2);
run(`RAW.records.push({date:'2026-10-02',source:'copilot',device:'Mac',model:'M',in:5});`);
assert.equal(run(`slice({from:'2026-10-02',to:'2026-10-02'}).recs.length`),1);
run(`S.exactOnly=false; S.ides=new Set(['Missing']); RAW.hourly=[{date:'2026-10-02',source:'copilot',device:'Mac',hour:10,tokens:110,msgs:1}];`);
assert.equal(run(`!!dimFiltered()`),true);
run(`S.ides.clear(); S.models=new Set(['Secondary']); RAW.sessions=[{source:'claude',project:'P',model:'Primary',
 model_days:{'Primary':{'2026-10-02':[9,900,90,0,0,9,0,0,0,0,9,0,0,0,0]},
 'Secondary':{'2026-10-02':[1,100,10,0,0,1,0,0,0,0,1,0,0,0,0]}},
 days:{'2026-10-02':[10,1000,100,0,0,10,0,0,0,0,10,0,0,0,0]}}];`);
assert.equal(run(`slice({from:'2026-10-02',to:'2026-10-02'}).sessions[0].cost`),1);
assert.equal(run(`slice({from:'2026-10-02',to:'2026-10-02'}).sessions[0].req`),1);
run(`S.models.clear(); RAW.sessions=[{source:'copilot',model:'M',exact_days:{'2026-10-02':[.2,100,10,0,0,1,1,0,0,0,1]},
 exact_model_days:{'M':{'2026-10-02':[.2,100,10,0,0,1,1,0,0,0,1]}},days:{'2026-10-02':[1,500,50,0,0,5,5,0,0,0,5]}}]; S.exactOnly=true;`);
assert.equal(run(`slice({from:'2026-10-02',to:'2026-10-02'}).sessions[0].in`),100);
run(`S.exactOnly=false; RAW.sessions=[{source:'claude',model:'(user)',model_days:{'(user)':{'2026-10-02':[0,0,0,0,0,0,1]}},days:{'2026-10-02':[0,0,0,0,0,0,1]}}];`);
assert.equal(run(`slice({from:'2026-10-02',to:'2026-10-02'}).sessions[0].user`),1);
run(`S.exactOnly=false; RAW.mcp_servers=[{name:'fixture',tool:'codex'}];
 RAW.tools=[{date:'2026-09-01',source:'codex',device:'Mac',name:'mcp__fixture__call',count:1},
 {date:'2026-10-02',source:'claude',device:'Mac',name:'mcp__fixture__call',count:1}];`);
assert.equal(run(`findIdleMCP({r:{from:'2026-10-02',to:'2026-10-02'}},'codex').rows.length`),1);
run(`RAW.tools.push({date:'2026-10-02',source:'codex',device:'Mac',name:'mcp__fixture__call',count:1});`);
assert.equal(run(`findIdleMCP({r:{from:'2026-10-02',to:'2026-10-02'}},'codex')`),null);
run(`S.devs=new Set(['PC']);`);
assert.equal(run(`onThisDevice(()=>({id:'fixture'}))({recs:[],sessions:[]})`),null);
run(`S.devs.clear();`);
run(`RAW.tools=[];RAW.mcp_inventory={servers:{},calls:[{date:'2026-10-02',source:'codex',server:'fixture',calls:1,tools:['mcp__fixture__call']}]};`);
assert.equal(run(`findIdleMCP({r:{from:'2026-10-02',to:'2026-10-02'}},'codex')`),null);
assert.equal(run(`findCacheWaste({sessions:[{source:'claude',model:'Haiku',cc:1000000,cr:0,cost:1.25}]}).impact`),0);
run(`var deviceData={recs:[{device:'Mac',in:100,out:10,asst:2,cost:1},{device:'PC',in:200,out:20,asst:1,cost:4}]}; S.metric='tokens';`);
assert.equal(run(`deviceDistribution(deviceData).reduce((n,x)=>n+x.value,0)`),330);
assert.equal(run(`deviceDistribution(deviceData)[0].id`),'PC');
run(`S.metric='messages';`);assert.equal(run(`deviceDistribution(deviceData)[0].id`),'Mac');
run(`S.metric='cost';`);assert.equal(run(`deviceDistribution(deviceData)[0].value`),4);
run(`S.devs=new Set(['Mac']);`);assert.equal(run(`deviceDistribution(deviceData).length`),1);
assert.equal(run(`deviceDistribution(deviceData)[0].value`),1);
const syncCode=views.slice(views.indexOf('const POLL_KEY='),views.indexOf('let nativeSettingsTimer;'));
for(const search of ['', '?nativeApp=1&nativePollSeconds=300']){
  const fixture=vm.createContext({URLSearchParams,location:{search}, document, console});
  vm.runInContext(syncCode,fixture);
  assert.equal(vm.runInContext('pollMs()',fixture),15000);
  assert.equal(vm.runInContext('acceptRefreshSync({seconds:60,revision:"tick"})',fixture),true);
  assert.equal(vm.runInContext('pollMs()',fixture),60000);
  assert.equal(vm.runInContext('acceptRefreshSync({seconds:60,revision:"tick"})',fixture),false);
  assert.equal(vm.runInContext('acceptRefreshSync({seconds:0,revision:"manual"})',fixture),true);
  assert.equal(vm.runInContext('pollMs()',fixture),0);
  for(const seconds of [true,1,-1,Infinity,'60']){
    assert.throws(()=>vm.runInContext(`acceptRefreshSync({seconds:${JSON.stringify(seconds)},revision:'bad'})`,fixture));
    assert.equal(vm.runInContext('pollMs()',fixture),0);
  }
}
console.log('Frontend regression checks passed: pricing, precision, scope, model contributions, Optimize, device distribution and shared refresh validation.');
(async()=>{
  let tick={seconds:15,revision:'first'}, loads=0, requested;
  const clock=vm.createContext({URLSearchParams,location:{search:''},document,console,S:{live:true},
    load:async()=>{loads++;return true},fetch:async(url,options)=>{requested=options; if(options){tick={seconds:JSON.parse(options.body).seconds,revision:'native-setting'};}return {ok:true,json:async()=>tick}}});
  vm.runInContext(syncCode,clock);
  await vm.runInContext('pollRefreshSync()',clock);
  await vm.runInContext('pollRefreshSync()',clock);
  assert.equal(loads,1,'Unchanged sync tick refetched the entire payload');
  tick={seconds:0,revision:'manual'};
  await vm.runInContext('pollRefreshSync()',clock);
  await vm.runInContext('pollRefreshSync()',clock);
  assert.equal(loads,2,'Manual mode updated without a new shared tick');
  tick={seconds:0,revision:'tray-refreshed'};
  await vm.runInContext('pollRefreshSync()',clock);
  assert.equal(loads,3,'Native manual refresh did not update the browser');
  await vm.runInContext('setPollMs(60000)',clock);
  assert.equal(JSON.parse(requested.body).seconds,60);
  assert.equal(vm.runInContext('pollMs()',clock),60000);
  vm.runInContext('S.live=false',clock); tick={seconds:15,revision:'paused'};
  await vm.runInContext('pollRefreshSync()',clock);
  assert.equal(loads,4,'Paused dashboard updated its figures');
  vm.runInContext('S.live=true',clock);
  let failed=true;
  clock.load=async()=>{loads++;if(failed){failed=false;return false;}return true};
  await vm.runInContext('pollRefreshSync()',clock);
  await vm.runInContext('pollRefreshSync()',clock);
  await vm.runInContext('pollRefreshSync()',clock);
  assert.equal(loads,6,'Failed payload did not retry on the same shared tick');
  console.log('Browser follows shared ticks, tray refreshes, Manual and Pause; setting writes use the guarded API.');
  // Execute the actual load function with delayed HTTP responses. A failed
  // payload remains retryable; an older response must not replace newer data.
  let resolveOld, pending=0, rendered=0;
  const payload=token=>({records:[{date:'2026-10-10',in:token}],meta:{last_refresh:1,files:1},device:{},pricing_note:''});
  const loadNodes={statusText:{},livedot:{classList:{add(){}}},coverage:{},devName:{},devOs:{},device:{},pricingNote:{}};
  const loading=vm.createContext({console,Date,Set,S:{live:true,devs:new Set()},RAW:null,
    document:{getElementById:id=>loadNodes[id],documentElement:{dataset:{}}},
    fetch:async()=>{pending++;if(pending===1)return await new Promise(resolve=>{resolveOld=resolve});return {ok:true,json:async()=>payload(200)}},
    devices:()=>[],renderVersion(){},fmtNum:value=>value,renderAll(){rendered++}});
  vm.runInContext(views.slice(views.indexOf('let loadSequence='),views.indexOf('async function loadStorage()')),loading);
  const oldLoad=vm.runInContext('load()',loading);
  await vm.runInContext('load()',loading);
  resolveOld({ok:true,json:async()=>payload(100)});
  await oldLoad;
  assert.equal(vm.runInContext('RAW.records[0].in',loading),200);
  assert.equal(rendered,1,'Superseded payload rendered after a newer one');
  loading.console={error(){}};
  loading.fetch=async()=>({ok:false});
  assert.equal(await vm.runInContext('load()',loading),false);
  assert.equal(vm.runInContext('RAW.records[0].in',loading),200);
  console.log('Actual dashboard load discards superseded results and retains data on HTTP failure.');
  let state={supported:true,installed:true,running:false,requested:false,label:'Menu bar app',error:null};
  const nodes={nativeAppToggle:{dataset:{}},nativeAppMsg:{}};
  const box={dataset:{},isConnected:true,set innerHTML(value){nodes.nativeAppToggle={dataset:{}};nodes.nativeAppMsg={};}};
  const fixture=vm.createContext({esc:value=>String(value),
    document:{getElementById:id=>id==='nativeAppBox'?box:nodes[id]},
    fetch:async()=>({ok:true,json:async()=>state})});
  vm.runInContext(views.slice(views.indexOf('async function loadNativeSettings()'),views.indexOf('function renderSettings(')),fixture);
  await vm.runInContext('loadNativeSettings()',fixture);
  const toggle=nodes.nativeAppToggle;
  await vm.runInContext('loadNativeSettings()',fixture);
  assert.strictEqual(nodes.nativeAppToggle,toggle,'Idle status polling replaced the focused native toggle');
  state={...state,running:true,requested:true};
  await vm.runInContext('loadNativeSettings()',fixture);
  assert.notStrictEqual(nodes.nativeAppToggle,toggle,'Changed status was not rendered');
  console.log('Native Settings retains the toggle across unchanged status polls.');
})().catch(error=>{console.error(error);process.exitCode=1;});
