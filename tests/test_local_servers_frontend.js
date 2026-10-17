/* Local server view checks use mock payloads only; no local process is contacted. */
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
const between=(start,end)=>{
  const a=source.indexOf(start),b=source.indexOf(end,a);
  assert.notEqual(a,-1,`Missing source marker: ${start}`);
  assert.notEqual(b,-1,`Missing source marker: ${end}`);
  return source.slice(a,b);
};
const viewCode=between('const VIEW_HTML={};','function syncSearchBoxes(){');
const loadCode=between('let localServerLoadSequence=0;','/* ---------------- events ---------------- */');
const eventCode=between('/* ---------------- events ---------------- */','document.querySelector(".brand")');
const syncCode=between('/* Refresh interval, in ms. 15s is the historical default; 0 = manual only. */','let nativeSettingsTimer;');

function esc(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function response(payload,status=200){return {status,ok:status>=200&&status<300,json:async()=>payload};}
function makeUi(){
  let doc,html='',inputs=[];
  const rootNode={
    get innerHTML(){return html;},
    set innerHTML(value){
      html=value;inputs=[];
      const re=/data-local-server-(command|folder)="([^"]+)"[^>]*value="([^"]*)"/g;
      let match;
      while((match=re.exec(html))){
        const end=html.indexOf('>',match.index),tag=html.slice(match.index,end<0?undefined:end+1);
        const input={name:match[1]==='folder'?'cwd':'command',dataset:{[match[1]==='folder'?'localServerFolder':'localServerCommand']:match[2]},value:match[3],disabled:/\sdisabled(?:\s|$)/.test(tag),selectionStart:0,selectionEnd:0,focused:false,
          focus(){this.focused=true;doc.activeElement=this;},setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}};
        inputs.push(input);
      }
    },
    querySelectorAll(){return inputs;}
  };
  const filters={style:{display:''},classList:{toggle(){}}},pageTitle={},count={textContent:''},tabs={addEventListener(){}};
  const nodes={localServersRoot:rootNode,filters,pageTitle,localServerCount:count,tabs};
  const listeners={};
  doc={
    title:'',visibilityState:'visible',activeElement:null,
    getElementById(id){return nodes[id]||(nodes[id]={textContent:'',style:{},classList:{toggle(){}}});},
    querySelectorAll(){return [];},querySelector(){return null;},
    addEventListener(type,fn){listeners[type]=fn;}
  };
  return {document:doc,nodes,listeners,root:rootNode,inputs:()=>inputs};
}
function makeContext({withLoad=false,withEvents=false,extra={}}={}){
  const ui=makeUi();
  const ctx=vm.createContext({document:ui.document,console,Date,Set,URLSearchParams,AbortController:class{constructor(){this.signal={};}abort(){}},
    setTimeout:()=>1,clearTimeout(){},esc,location:{search:'',hash:'#local-servers'},history:{replaceState(){}},
    S:{view:'local-servers',live:true},RAW:{records:[]},VIEW_TITLES:{overview:'Overview','local-servers':'Local servers'},
    syncRangeUI(){},syncMetricUI(){},buildFilterPanels(){},renderPills(){},renderSidebarDevices(){},filtersToURL(){},slice(){throw Error('Local servers must not need analytics data');},
    ...extra});
  vm.runInContext('let LOCAL_SERVERS={servers:[],updatedAt:"",supported:null,loading:false,loaded:false,stale:false,unavailable:false}; let LOCAL_SERVER_SEARCH=""; let LOCAL_SERVER_CONFIG_ID=null; let LOCAL_SERVER_BUSY_ID=null; let LOCAL_SERVER_CONFIRM_SEQUENCE=0; let LOCAL_SERVER_COMMAND_DRAFT={}; let LOCAL_SERVER_FOLDER_DRAFT={};',ctx);
  vm.runInContext(viewCode,ctx);
  if(withLoad) vm.runInContext(loadCode,ctx);
  if(withEvents){
    ctx.FormData=function(form){return {get:key=>form[key]??null};};
    vm.runInContext(eventCode,ctx);
  }
  return {ctx,ui};
}
function setState(ctx,state){
  ctx.__state=state;
  vm.runInContext('LOCAL_SERVERS=__state',ctx);
  delete ctx.__state;
}
function setDraft(ctx,id,value){ctx.__id=id;ctx.__draft={};ctx.__draft[id]=value;vm.runInContext('LOCAL_SERVER_CONFIG_ID=__id;LOCAL_SERVER_COMMAND_DRAFT=__draft',ctx);delete ctx.__draft;delete ctx.__id;}
const row=(overrides={})=>({id:'server-1',state:'running',process:'node',project:'Example app',cwd:'C:\\work\\example',host:'127.0.0.1',port:3000,pid:1234,isPinned:false,isConfigured:true,profileId:'profile-1',lastSeen:'2026-10-16T10:00:00Z',...overrides});

async function main(){
// Empty telemetry still renders this view, and no scan result is described as zero activity.
{
  const {ctx,ui}=makeContext();
  vm.runInContext('S.view="local-servers";RAW={records:[]};renderAll()',ctx);
  assert.equal(ui.nodes.filters.style.display,'none');
  assert.match(ui.root.innerHTML,/Scan local ports to see current listeners/);
  assert.doesNotMatch(ui.root.innerHTML,/Running now|No local servers are listening right now/);
  assert.equal(ui.nodes.localServerCount.textContent,'');
  console.log('Local servers renders independently of analytics records and keeps an unknown scan distinct from zero.');
}

// The legacy response without `supported` stays usable; generic TCP ports are never called HTTP.
{
  const {ctx,ui}=makeContext();
  const unsafe=row({project:'<img src=x onerror=alert(1)>',command:'secret command --token do-not-display'});
  setState(ctx,{servers:[unsafe],updatedAt:'2026-10-16T10:00:00Z',loaded:true});
  vm.runInContext('renderLocalServers()',ctx);
  assert.match(ui.root.innerHTML,/data-local-server-stop="server-1"/);
  assert.match(ui.root.innerHTML,/&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(ui.root.innerHTML,/<img src=x|http:\/\/localhost|Open ↗|secret command/);
  assert.match(ui.root.innerHTML,/TCP ports owned by your user account/);
  console.log('Legacy payloads remain actionable, row data is escaped, saved commands stay hidden, and TCP listeners carry no HTTP assumption.');
}

// Unsupported platforms get a concise capability state with no controls.
{
  const {ctx,ui}=makeContext();
  setState(ctx,{servers:[],supported:false,platform:'windows',message:'Local server detection is available on macOS only.',loaded:true});
  vm.runInContext('renderLocalServers()',ctx);
  assert.match(ui.root.innerHTML,/Unavailable on this platform/);
  assert.match(ui.root.innerHTML,/available on macOS only/);
  assert.doesNotMatch(ui.root.innerHTML,/localServersRefresh|data-local-server-(?:start|stop|restart|pin|config)=/);
  console.log('Unsupported platforms show the backend capability message without nonworking controls.');
}

// Failed scans keep the last good rows, mark them stale, and disable actions.
{
  const {ctx,ui}=makeContext({withLoad:true});
  const first=row();
  let calls=0;
  ctx.fetch=async()=>{calls++;return calls===1?response({servers:[first],updatedAt:'2026-10-16T10:00:00Z'}):response({error:'Scanner unavailable'},503);};
  await vm.runInContext('loadLocalServers(true,true)',ctx);
  assert.equal(vm.runInContext('LOCAL_SERVERS.supported',ctx),true,'A missing supported flag must preserve legacy compatibility');
  assert.equal(vm.runInContext('LOCAL_SERVERS.loaded',ctx),true);
  await vm.runInContext('loadLocalServers(true,true)',ctx);
  assert.equal(vm.runInContext('LOCAL_SERVERS.servers[0].id',ctx),'server-1');
  assert.equal(vm.runInContext('LOCAL_SERVERS.stale',ctx),true);
  assert.match(ui.root.innerHTML,/Showing the last successful scan; actions are disabled/);
  assert.match(ui.root.innerHTML,/data-local-server-stop="server-1"[^>]*disabled/);
  assert.match(ui.root.innerHTML,/Scan again/);
  console.log('A scan failure preserves the last good rows with a stale label, retry button, and disabled actions.');
}

// Older services return 404. The UI offers a deliberate retry and does not query every shared-clock tick.
{
  const {ctx,ui}=makeContext({withLoad:true});
  let calls=0;
  ctx.fetch=async()=>{calls++;return response({error:'Not found'},404);};
  await vm.runInContext('loadLocalServers(true,true)',ctx);
  assert.equal(vm.runInContext('LOCAL_SERVERS.unavailable',ctx),true);
  assert.match(ui.root.innerHTML,/Try again/);
  const after404=calls;
  await vm.runInContext('loadLocalServers(true)',ctx);
  assert.equal(calls,after404,'An automatic scan retried a known 404');
  ctx.fetch=async()=>{calls++;return response({servers:[],updatedAt:'2026-10-16T11:00:00Z'});};
  await vm.runInContext('loadLocalServers(true,true)',ctx);
  assert.equal(vm.runInContext('LOCAL_SERVERS.unavailable',ctx),false,'The explicit retry did not recover from an old backend');
  assert.equal(calls,after404+1);
  console.log('A legacy API 404 becomes unavailable and only the explicit retry queries again.');
}

// Newer overlapping scans win; an older response cannot overwrite them.
{
  const {ctx}=makeContext({withLoad:true});
  let resolveOld,calls=0;
  ctx.fetch=async()=>{
    calls++;
    if(calls===1) return await new Promise(resolve=>{resolveOld=resolve;});
    return response({servers:[row({id:'newer',project:'New result'})],updatedAt:'2026-10-16T12:00:00Z'});
  };
  const old=vm.runInContext('loadLocalServers(true,true)',ctx);
  await vm.runInContext('loadLocalServers(true,true)',ctx);
  resolveOld(response({servers:[row({id:'older',project:'Old result'})],updatedAt:'2026-10-16T11:00:00Z'}));
  await old;
  assert.equal(vm.runInContext('LOCAL_SERVERS.servers[0].id',ctx),'newer');
  console.log('Superseded local server scans cannot overwrite newer results.');
}

// Windows asks for the working folder, retains its draft, and warns about immediate termination.
{
  const {ctx,ui}=makeContext({withLoad:true,withEvents:true});
  setState(ctx,{servers:[row({cwd:'',project:'',isConfigured:false,profileId:null,host:'::1'})],platform:'Windows',loaded:true});
  setDraft(ctx,'server-1','npm run dev');
  vm.runInContext('LOCAL_SERVER_FOLDER_DRAFT["server-1"]="C:\\\\work\\\\draft";renderLocalServers()',ctx);
  assert.match(ui.root.innerHTML,/Working folder/);
  assert.match(ui.root.innerHTML,/\[::1\]:3000/);
  assert.match(ui.root.innerHTML,/data-local-server-pin="server-1"[^>]*disabled/);
  assert.match(ui.root.innerHTML,/Stop ends the listening process immediately/);
  const input=ui.inputs().find(i=>i.name==='cwd');
  input.focus(); input.setSelectionRange(3,7);
  ctx.fetch=async()=>response({servers:[row({cwd:'',project:''})],platform:'Windows'});
  await vm.runInContext('loadLocalServers(true,true)',ctx);
  const refreshed=ui.inputs().find(i=>i.name==='cwd');
  assert.equal(refreshed.value,input.value);
  assert.equal(refreshed.focused,true);
  assert.equal(refreshed.selectionStart,3);
  ctx.__row=row();
  const warning=vm.runInContext('localServerActionConfirmation("restart",__row)',ctx);
  assert.match(warning.body,/ends the current process immediately/);
  assert.match(warning.body,/unsaved work can be lost/);
  console.log('Windows folder drafts survive refresh; IPv6 labels, pin prerequisites and immediate-stop consent are explicit.');
}

// An in-progress command draft survives a successful background refresh and keeps focus/caret.
{
  const {ctx,ui}=makeContext({withLoad:true});
  setState(ctx,{servers:[row({state:'stopped',pid:0})],updatedAt:'2026-10-16T12:00:00Z',supported:true,loaded:true});
  const draft='npm run dev -- --port 3010';
  setDraft(ctx,'server-1',draft);
  const previous={name:'command',dataset:{localServerCommand:'server-1'},selectionStart:8,selectionEnd:12};
  ui.document.activeElement=previous;
  ctx.fetch=async()=>response({servers:[row({state:'stopped',pid:0})],updatedAt:'2026-10-16T12:01:00Z'});
  await vm.runInContext('loadLocalServers(true,true)',ctx);
  assert.equal(ui.inputs()[0].value,draft);
  assert.equal(ui.inputs()[0].focused,true);
  assert.equal(ui.inputs()[0].selectionStart,8);
  assert.doesNotMatch(ui.root.innerHTML,/PID \?/,'A synthetic stopped profile with pid 0 must not show an unknown PID');
  console.log('Refreshing preserves the unsaved launch command and input focus; synthetic stopped profiles omit PID.');
}

function clickEvent(action,id){
  const selector=`[data-local-server-${action}]`,key='localServer'+action[0].toUpperCase()+action.slice(1);
  const target={dataset:{[key]:id}};
  return {target:{closest(value){return value===selector?target:null;}}};
}
function actionContext(server,confirmImpl=async()=>true){
  const {ctx,ui}=makeContext({withLoad:true});
  setState(ctx,{servers:[server],updatedAt:'2026-10-16T13:00:00Z',supported:true,loaded:true});
  ctx.FormData=function(form){return {get:key=>form[key]??null};};
  const requests=[];
  ctx.askConfirm=confirmImpl;
  ctx.fetch=async(url,options={})=>{
    requests.push({url,options});
    if(options.method==='POST') return response({ok:true});
    return response({servers:[server],updatedAt:'2026-10-16T13:01:00Z'});
  };
  vm.runInContext(eventCode,ctx);
  return {ctx,ui,requests,handlers:ui.listeners};
}
function postFor(action,requests){return requests.find(r=>r.options.method==='POST'&&r.url===`/api/local-servers/${action}`);}

// Every shell execution has explicit consent; Restart is bound to the confirmed server id.
{
  let confirmation;
  const start=actionContext(row({id:'start-id',state:'stopped',pid:0}),async value=>{confirmation=value;return true;});
  await start.handlers.click(clickEvent('start','start-id'));
  const startPost=postFor('start',start.requests);
  assert.ok(startPost);
  assert.deepEqual(JSON.parse(startPost.options.body),{profileId:'profile-1',confirmed:true});
  assert.match(confirmation.body,/saved launch command/);
  assert.match(confirmation.body,/account permissions/);
  assert.match(confirmation.body,/C:\\work\\example/);
  assert.equal(confirmation.ok,'Start server');

  const restart=actionContext(row({id:'confirmed-server',state:'running'}),async value=>{confirmation=value;return true;});
  await restart.handlers.click(clickEvent('restart','confirmed-server'));
  const restartPost=postFor('restart',restart.requests);
  assert.deepEqual(JSON.parse(restartPost.options.body),{profileId:'profile-1',confirmed:true,serverId:'confirmed-server'});
  assert.match(confirmation.body,/sends SIGTERM/);

  const stop=actionContext(row({id:'stop-id',state:'running'}),async value=>{confirmation=value;return true;});
  await stop.handlers.click(clickEvent('stop','stop-id'));
  const stopPost=postFor('stop',stop.requests);
  assert.deepEqual(JSON.parse(stopPost.options.body),{id:'stop-id',confirmed:true});
  assert.equal(confirmation.danger,true);
  assert.match(confirmation.body,/Other ports owned/);
  assert.doesNotMatch(eventCode,/\bconfirm\s*\(/,'Process controls must use the shared in-app dialog');
  console.log('Start, Stop, and Restart use the shared confirmation dialog and send confirmed payloads with the restart server id.');
}

// Saving a command only configures it; the explicit Start action remains separate.
{
  const fixture=actionContext(row({id:'configure-id',state:'stopped',pid:0}),async()=>{throw Error('Save must not ask to execute');});
  vm.runInContext('LOCAL_SERVER_CONFIG_ID="configure-id"',fixture.ctx);
  const form={dataset:{localServerForm:'configure-id'},command:'npm run dev -- --port 3010'};
  let prevented=false;
  await fixture.handlers.submit({target:{closest:selector=>selector==='[data-local-server-form]'?form:null},preventDefault(){prevented=true;}});
  const configurePost=postFor('configure',fixture.requests);
  assert.ok(prevented);
  assert.deepEqual(JSON.parse(configurePost.options.body),{id:'configure-id',command:form.command});
  assert.equal(fixture.requests.some(r=>/\/(?:start|restart)$/.test(r.url)),false);
  const windows=actionContext(row({id:'win-config'}));
  const winForm={dataset:{localServerForm:'win-config'},command:'npm run dev',cwd:'C:\\work\\project'};
  await windows.handlers.submit({target:{closest:selector=>selector==='[data-local-server-form]'?winForm:null},preventDefault(){}});
  assert.deepEqual(JSON.parse(postFor('configure',windows.requests).options.body),{id:'win-config',command:winForm.command,cwd:winForm.cwd});
  console.log('Save command stores the text without executing it or requesting execution consent.');
}

// A newer local intent cancels an older pending confirmation.
{
  let resolveConfirm;
  const fixture=actionContext(row({id:'intent-id',state:'stopped',pid:0}),()=>new Promise(resolve=>{resolveConfirm=resolve;}));
  const oldStart=fixture.handlers.click(clickEvent('start','intent-id'));
  await fixture.handlers.click(clickEvent('pin','intent-id'));
  resolveConfirm(true);
  await oldStart;
  assert.equal(fixture.requests.filter(r=>r.options.method==='POST').map(r=>r.url).join(','),'/api/local-servers/pin');
  console.log('A superseded confirmation cannot post an older server action.');
}

// Automatic scans follow the shared clock only while the Local servers view is active and live.
{
  let tick={seconds:15,revision:'first'},loads=0,scans=0;
  const document={getElementById:()=>null};
  const ctx=vm.createContext({document,URLSearchParams,location:{search:''},console,Date,S:{live:true,view:'overview'},
    fetch:async()=>response(tick),load:async()=>{loads++;return true;},loadLocalServers:async()=>{scans++;return true;}});
  vm.runInContext('let LOCAL_SERVERS={supported:true,unavailable:false}',ctx);
  vm.runInContext(syncCode,ctx);
  await vm.runInContext('pollRefreshSync()',ctx);
  assert.equal(loads,1);assert.equal(scans,0,'An unrelated view scanned local servers');
  await vm.runInContext('pollRefreshSync()',ctx);
  assert.equal(loads,1);assert.equal(scans,0,'An unchanged tick rescanned local servers');
  vm.runInContext('S.view="local-servers"',ctx);tick={seconds:15,revision:'next'};
  await vm.runInContext('pollRefreshSync()',ctx);
  assert.equal(scans,1,'The active local view missed a shared refresh tick');
  vm.runInContext('S.live=false',ctx);tick={seconds:15,revision:'paused'};
  await vm.runInContext('pollRefreshSync()',ctx);
  assert.equal(scans,1,'A paused dashboard scanned local servers');
  console.log('Local server scans follow changed shared ticks only in the active, unpaused view.');
}

// Returning to a visible native window must also respect Pause.
{
  let visibilityHandler,scans=0;
  const document={visibilityState:'visible',addEventListener:(type,handler)=>{if(type==='visibilitychange')visibilityHandler=handler;}};
  const ctx=vm.createContext({document,NATIVE_APP_MODE:true,S:{live:false,view:'local-servers'},LOCAL_SERVERS:{supported:true,unavailable:false},
    applyPollInterval(){},load(){},loadStorage(){},loadLocalServers(){scans++;}});
  const start=source.indexOf('if(NATIVE_APP_MODE) document.addEventListener("visibilitychange"');
  assert.notEqual(start,-1,'Missing native visibility refresh handler');
  vm.runInContext(source.slice(start),ctx);
  visibilityHandler();
  assert.equal(scans,0,'Returning to a visible native window ignored Pause');
  vm.runInContext('S.live=true',ctx);
  visibilityHandler();
  assert.equal(scans,1,'A visible, live Local servers view should refresh');
  console.log('Native visibility refresh respects Pause while refreshing an active live Local servers view.');
}

console.log('Local servers frontend checks passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
