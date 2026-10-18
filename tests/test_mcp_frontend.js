/* MCP settings consent UI checks with a mock DOM and backend. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'mcp.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const viewsSource = fs.readFileSync(path.join(__dirname, '..', 'static', 'views.js'), 'utf8');
assert(indexHtml.indexOf('/static/mcp.js') < indexHtml.indexOf('/static/views.js'), 'MCP helpers load before Settings views');
assert(viewsSource.includes('mcpSettingsPanelHTML()') && viewsSource.includes('loadMcpSettings()'));
assert(viewsSource.includes('mcpSettingsGenerationCurrent(mcpGeneration)'), 'Settings fetch checks its open generation');
const openSettingsStart=viewsSource.indexOf('async function openSettings(){');
const openSettingsEnd=viewsSource.indexOf('/* Offline app (PWA)',openSettingsStart);
assert(openSettingsStart>=0&&openSettingsEnd>openSettingsStart,'Settings lifecycle function is present');
const openSettingsSource=viewsSource.slice(openSettingsStart,openSettingsEnd);

function config(){
  return {mcpServers:{'agent-telemetry':{
    command:'/usr/bin/python3',
    args:['/opt/agenttelemetry/mcp_server.py','--backend','http://127.0.0.1:7878'],
  }}};
}
function state(enabled=false, include_names=false, include_peers=false, warning){
  return {enabled,include_names,include_peers,warning:warning||
    'MCP access is off by default. A cloud-backed agent may send tool results to its provider.',
    client_config:enabled?config():null};
}
function response(body, ok=true){return {ok,status:ok?200:400,json:async()=>body};}

function harness(options={}){
  const nodes = new Map();
  function node(id){
    if(!nodes.has(id)) nodes.set(id,{
      id,innerHTML:'',textContent:'',className:'',value:'',disabled:false,checked:false,
      isConnected:true,dataset:{},listeners:{},selected:false,focused:false,
      addEventListener(type,fn){this.listeners[type]=fn;},
      querySelector(selector){
        const childId=selector.startsWith('#')?selector.slice(1):selector;
        if(this.id==='mcpSettingsBox'&&['mcpSettingsWarning','mcpSettingsState','mcpSettingsMsg'].includes(childId)) return node(childId);
        if(this.id==='mcpSettingsBox'&&childId==='mcpClientConfig'&&node('mcpSettingsState').innerHTML.includes('id="mcpClientConfig"')) return node(childId);
        if(this.id==='mcpSettingsState'&&childId==='mcpClientConfig'&&this.innerHTML.includes('id="mcpClientConfig"')) return node(childId);
        return null;
      },
      focus(){this.focused=true;},select(){this.selected=true;},
      setSelectionRange(start,end){this.selection=[start,end];},
    });
    return nodes.get(id);
  }
  const root=node('mcpSettingsBox');
  let server=state(options.enabled===true,options.include_names===true,options.include_peers===true);
  let failGets=options.failGets||0;
  let failPosts=options.failPosts||0;
  let confirmation=options.confirmation!==false;
  const confirmations=[], calls=[];
  let clipboardText=null;
  let deferredGet=null;
  const context=vm.createContext({
    console,Promise,JSON,Array,String,Number,Math,RegExp,
    document:{getElementById:id=>node(id)},
    askConfirm:async prompt=>{confirmations.push(prompt);return confirmation;},
    navigator:{clipboard:{writeText:async value=>{
      if(options.clipboardFails) throw new Error('clipboard unavailable');
      clipboardText=value;
    }}},
    fetch:async(url,init={})=>{
      calls.push({url,init});
      if(init.method==='POST'){
        const body=JSON.parse(init.body);
        if((body.enabled&&!server.enabled)||(body.include_names&&!server.include_names)
            ||(body.include_peers&&!server.include_peers)) assert.strictEqual(body.confirmed,true,'enable/scope rise requires confirmation');
        if(failPosts>0){failPosts--;return response({error:'MCP settings could not be saved.'},false);}
        server=state(body.enabled,body.include_names,body.include_peers);
        return response(server);
      }
      if(deferredGet){const pending=deferredGet;deferredGet=null;return pending;}
      if(failGets>0){failGets--;return response({error:'<img src=x onerror=alert(1)>'},false);}
      return response(server);
    },
  });
  vm.runInContext(source,context,{filename:'static/mcp.js'});
  async function open(){context.beginMcpSettings();await context.loadMcpSettings();return root;}
  return {context,root,node,open,calls,confirmations,setConfirmation:value=>{confirmation=value;},
    get server(){return server;},get clipboardText(){return clipboardText;},setDeferredGet:value=>{deferredGet=value;},
    setFailGets:value=>{failGets=value;},setFailPosts:value=>{failPosts=value;}};
}

async function wait(){await new Promise(resolve=>setTimeout(resolve,0));}

function settingsDrawerHarness(fetchImpl){
  let active=true, generation=1, renderCount=0, closes=0;
  const drawer={isConnected:true,firstElementChild:null};
  function openDrawer(html){
    if(drawer.firstElementChild) drawer.firstElementChild.isConnected=false;
    drawer.firstElementChild={isConnected:true,html};
  }
  const context=vm.createContext({
    document:{getElementById:id=>id==='drawerBody'?drawer:null},
    beginMcpSettings:()=>generation,
    mcpSettingsGenerationCurrent:value=>active&&value===generation,
    closeMcpSettings:()=>{active=false;closes++;},
    openDrawer,
    fetch:fetchImpl,
    renderSettings:()=>{renderCount++;},
  });
  vm.runInContext(openSettingsSource,context,{filename:'static/views.js:openSettings'});
  return {context,drawer,openDrawer,get renderCount(){return renderCount;},get closes(){return closes;}};
}

async function settingsDrawerLifecycleRace(){
  let resolveSettings;
  const h=settingsDrawerHarness(()=>new Promise(resolve=>{resolveSettings=resolve;}));
  const pending=h.context.openSettings();
  await wait();
  assert.match(h.drawer.firstElementChild.html,/Loading/);
  h.openDrawer('<section>Session details</section>');
  resolveSettings({ok:true,json:async()=>({claude_cleanup_days:30})});
  await pending;
  assert.strictEqual(h.renderCount,0,'late Settings response must not overwrite a different drawer');
  assert.match(h.drawer.firstElementChild.html,/Session details/);
  assert.strictEqual(h.closes,1,'replaced Settings session is invalidated');

  const failed=settingsDrawerHarness(async()=>({ok:false,json:async()=>({error:'do not render this'})}));
  await failed.context.openSettings();
  assert.strictEqual(failed.renderCount,0,'non-OK response must not render its JSON error as Settings');
  assert.match(failed.drawer.firstElementChild.html,/Could not read settings/);
}

async function cancelEnableAndConfirmScopeRise(){
  const h=harness({confirmation:false});
  await h.open();
  const root=h.root, generation=Number(root.dataset.mcpGeneration);
  await root.listeners.change({target:{dataset:{mcpSetting:'enabled'},checked:true,disabled:false}});
  assert.strictEqual(h.confirmations.length,1);
  assert.strictEqual(h.calls.filter(call=>call.init.method==='POST').length,0,'cancel must not POST');
  assert.strictEqual(h.server.enabled,false);
  assert.match(h.node('mcpSettingsMsg').textContent,/No changes made/);

  h.setConfirmation(true);
  await root.listeners.change({target:{dataset:{mcpSetting:'enabled'},checked:true,disabled:false}});
  assert.strictEqual(h.server.enabled,true);
  assert.strictEqual(JSON.parse(h.calls.filter(call=>call.init.method==='POST').at(-1).init.body).confirmed,true);
  const posted=JSON.parse(h.calls.filter(call=>call.init.method==='POST').at(-1).init.body);
  assert.deepStrictEqual([posted.enabled,posted.include_names,posted.include_peers],[true,false,false]);
  assert.strictEqual(h.node('mcpClientConfig').value,JSON.stringify(config(),null,2));
  assert.doesNotMatch(h.node('mcpClientConfig').value,/token|secret|authorization/i);

  await root.listeners.change({target:{dataset:{mcpSetting:'include_names'},checked:true,disabled:false}});
  const expanded=JSON.parse(h.calls.filter(call=>call.init.method==='POST').at(-1).init.body);
  assert.deepStrictEqual([expanded.enabled,expanded.include_names,expanded.include_peers,expanded.confirmed],[true,true,false,true]);
  assert.match(h.confirmations[0].body,/cloud-backed AI client/);
  assert.match(h.confirmations[2].body,/Project and device display names/);
  await root.listeners.change({target:{dataset:{mcpSetting:'include_names'},checked:false,disabled:false}});
  const reduced=JSON.parse(h.calls.filter(call=>call.init.method==='POST').at(-1).init.body);
  assert.deepStrictEqual([reduced.include_names,reduced.confirmed],[false,false],'reducing scope needs no confirmation');
  await root.listeners.change({target:{dataset:{mcpSetting:'include_peers'},checked:true,disabled:false}});
  const peerScope=JSON.parse(h.calls.filter(call=>call.init.method==='POST').at(-1).init.body);
  assert.deepStrictEqual([peerScope.include_peers,peerScope.confirmed],[true,true]);
  assert.match(h.confirmations.at(-1).body,/other connected devices/);
  assert.strictEqual(generation,Number(root.dataset.mcpGeneration));
}

async function failedFetchRetryAndRevoke(){
  const h=harness({failGets:1});
  await h.open();
  assert.match(h.node('mcpSettingsState').innerHTML,/Status unavailable/);
  assert.match(h.node('mcpSettingsState').innerHTML,/data-mcp-action="retry"/);
  assert.match(h.node('mcpSettingsState').innerHTML,/data-mcp-action="revoke"/);
  assert.match(h.node('mcpSettingsState').innerHTML,/data-mcp-setting="enabled"[^>]*disabled/);
  assert.doesNotMatch(h.node('mcpSettingsState').innerHTML,/<img src=x/);
  assert.match(h.node('mcpSettingsMsg').textContent,/<img src=x/,'error strings are plain text, never markup');
  await h.root.listeners.click({target:{dataset:{mcpAction:'retry'}}});
  assert.match(h.node('mcpSettingsState').innerHTML,/Access: Off/);
  await h.root.listeners.click({target:{dataset:{mcpAction:'revoke'}}});
  const revoke=JSON.parse(h.calls.filter(call=>call.init.method==='POST').at(-1).init.body);
  assert.deepStrictEqual([revoke.enabled,revoke.include_names,revoke.include_peers,revoke.confirmed],[false,false,false,false]);
  assert.strictEqual(h.confirmations.length,0,'revocation never needs an enable/scope confirmation');
}

async function staleResponsesAndCloudWarning(){
  const h=harness({enabled:false});
  h.context.beginMcpSettings();
  const oldRoot=h.root;
  let resolveOld;
  h.setDeferredGet(new Promise(resolve=>{resolveOld=resolve;}));
  const oldLoad=h.context.loadMcpSettings();
  await wait();
  oldRoot.isConnected=false;
  h.context.closeMcpSettings();
  const newRoot=h.node('new-mcp-root');
  newRoot.id='mcpSettingsBox';
  h.node('mcpSettingsBox').isConnected=false;
  h.context.beginMcpSettings();
  // The fake document now resolves the active panel through the original key.
  const nodes=h.node;
  const originalGet=h.context.document.getElementById;
  h.context.document.getElementById=id=>id==='mcpSettingsBox'?newRoot:nodes(id);
  await h.context.loadMcpSettings();
  const newHTML=newRoot.querySelector('#mcpSettingsState').innerHTML;
  resolveOld(response(state(true,true,true,'<img src=x onerror=alert(2)>')));
  await oldLoad;
  assert.strictEqual(newRoot.querySelector('#mcpSettingsState').innerHTML,newHTML,'closed/reopened settings must ignore stale results');
  assert.match(newRoot.querySelector('#mcpSettingsState').innerHTML,/Access: Off/);
  assert.strictEqual(newRoot.querySelector('#mcpSettingsWarning').textContent,h.server.warning);
  assert.notStrictEqual(originalGet,undefined);
}

async function failedSaveReconcilesFromServer(){
  const h=harness({failPosts:1});
  await h.open();
  const root=h.root;
  await root.listeners.change({target:{dataset:{mcpSetting:'enabled'},checked:true,disabled:false}});
  assert.strictEqual(h.server.enabled,false);
  assert.match(h.node('mcpSettingsState').innerHTML,/Access: Off/);
  assert.match(h.node('mcpSettingsMsg').textContent,/could not be saved/);
  assert.strictEqual(h.calls.filter(call=>call.init.method!=='POST').length,2,'failed POST must be followed by a GET reconciliation');
}

async function pendingConfirmationAndSaveAreSerialized(){
  const h=harness();
  await h.open();
  let resolveConfirmation;
  h.context.askConfirm=async prompt=>{
    h.confirmations.push(prompt);
    return new Promise(resolve=>{resolveConfirmation=resolve;});
  };
  const first=h.root.listeners.change({target:{dataset:{mcpSetting:'enabled'},checked:true,disabled:false}});
  await wait();
  assert.match(h.node('mcpSettingsState').innerHTML,/Waiting for confirmation/);
  assert.match(h.node('mcpSettingsState').innerHTML,/data-mcp-setting="enabled"[^>]*disabled/);
  await h.root.listeners.change({target:{dataset:{mcpSetting:'enabled'},checked:true,disabled:false}});
  assert.strictEqual(h.confirmations.length,1,'a pending confirmation prevents duplicate prompts');
  assert.strictEqual(h.calls.filter(call=>call.init.method==='POST').length,0);
  resolveConfirmation(true);
  await first;
  assert.strictEqual(h.calls.filter(call=>call.init.method==='POST').length,1);
  assert.strictEqual(h.server.enabled,true);
}

async function detachedSettingsCannotSaveAfterConfirmation(){
  const h=harness();
  await h.open();
  let resolveConfirmation;
  h.context.askConfirm=async prompt=>{
    h.confirmations.push(prompt);
    return new Promise(resolve=>{resolveConfirmation=resolve;});
  };
  const pending=h.root.listeners.change({target:{dataset:{mcpSetting:'enabled'},checked:true,disabled:false}});
  await wait();
  const replacement=h.node('replacement-mcp-root');
  replacement.id='mcpSettingsBox';
  h.root.isConnected=false;
  h.context.document.getElementById=id=>id==='mcpSettingsBox'?replacement:h.node(id);
  resolveConfirmation(true);
  await pending;
  assert.strictEqual(h.calls.filter(call=>call.init.method==='POST').length,0,
    'confirming a detached/replaced Settings panel must not enable MCP access');
  assert.strictEqual(h.server.enabled,false);
}

async function olderGetCannotUndoRevoke(){
  const h=harness({enabled:true,include_names:true,include_peers:true});
  await h.open();
  const root=h.root, generation=Number(root.dataset.mcpGeneration);
  let resolveOld;
  h.setDeferredGet(new Promise(resolve=>{resolveOld=resolve;}));
  const oldGet=h.context.mcpFetchSettings(root,generation);
  await wait();
  await root.listeners.click({target:{dataset:{mcpAction:'revoke'}}});
  assert.strictEqual(h.server.enabled,false);
  assert.match(h.node('mcpSettingsState').innerHTML,/Access: Off/);
  resolveOld(response(state(true,true,true)));
  await oldGet;
  assert.match(h.node('mcpSettingsState').innerHTML,/Access: Off/,'an older GET must not resurrect access after revoke');
  assert.strictEqual(h.server.enabled,false);
}

async function configCopyFallbackAndXssSafety(){
  const h=harness({enabled:true,clipboardFails:true});
  await h.open();
  await h.root.listeners.click({target:{dataset:{mcpAction:'copy'}}});
  const textarea=h.node('mcpClientConfig');
  assert.strictEqual(textarea.focused,true);
  assert.strictEqual(textarea.selected,true);
  assert.match(h.node('mcpSettingsMsg').textContent,/selected/);

  h.server.warning='<img src=x onerror=alert(1)>';
  await h.context.mcpFetchSettings(h.root,Number(h.root.dataset.mcpGeneration));
  assert.strictEqual(h.node('mcpSettingsWarning').textContent,'<img src=x onerror=alert(1)>');
  assert.doesNotMatch(h.node('mcpSettingsState').innerHTML,/<img src=x/);
  assert.doesNotMatch(h.node('mcpClientConfig').innerHTML||'',/<img src=x/);
  assert.match(h.context.mcpSettingsPanelHTML(),/MCP access is off by default/,'cloud warning is always present in Settings');
}

(async()=>{
  await settingsDrawerLifecycleRace();
  await cancelEnableAndConfirmScopeRise();
  await failedFetchRetryAndRevoke();
  await failedSaveReconcilesFromServer();
  await pendingConfirmationAndSaveAreSerialized();
  await detachedSettingsCannotSaveAfterConfirmation();
  await olderGetCannotUndoRevoke();
  await staleResponsesAndCloudWarning();
  await configCopyFallbackAndXssSafety();
  process.stdout.write('MCP settings frontend tests passed\n');
})().catch(error=>{process.stderr.write(String(error.stack||error)+'\n');process.exitCode=1;});
