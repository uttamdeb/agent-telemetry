/* Focused UI lifecycle and safe Markdown checks; the app has no Node runtime dependency. */
const fs=require('fs'), path=require('path'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync(path.join(__dirname,'..','static','release-notes.js'),'utf8');
const release=(tag,body='## Changes\n\n- A useful fix.')=>({key:tag,tag,name:`AgentTelemetry ${tag}`,published_at:'2026-10-18T12:00:00Z',url:`https://github.com/uttamdeb/agent-telemetry/releases/tag/${tag}`,body,truncated:false,availability:'available'});
const response=(value,ok=true)=>({ok,json:async()=>value});

function harness({status,claim,requests=[],hidden=false,otherOpen=false,showFails=false}={}){
  const calls=[], listeners=new Map(), nodes=new Map();
  const node=()=>({innerHTML:'',textContent:'',disabled:false,isConnected:true,focus(){this.focused=true;}});
  for(const key of ['[data-rn-title]','[data-rn-meta]','[data-rn-body]','[data-rn-footer]','[data-release-notes-load]']) nodes.set(key,node());
  const dialog={id:'releaseNotesDialog',open:false,
    querySelector(selector){return nodes.get(selector)||null;},
    querySelectorAll(selector){return selector==='[data-release-notes-load]'?[nodes.get(selector)]:[];},
    showModal(){if(showFails)throw new Error('top layer unavailable');this.open=true;},
    close(){this.open=false;for(const fn of listeners.get('close')||[])fn({target:this});}
  };
  const otherDialog={id:'sessionNameDialog',open:otherOpen};
  const document={visibilityState:hidden?'hidden':'visible',activeElement:null,
    getElementById(id){return id==='releaseNotesDialog'?dialog:null;},
    querySelector(selector){return selector==='[data-release-notes-open]'?this.releaseNotesLink||null:null;},
    querySelectorAll(selector){return selector==='dialog[open]'?[...(otherDialog.open?[otherDialog]:[]),...(dialog.open?[dialog]:[])]:[];},
    addEventListener(type,fn){const all=listeners.get(type)||[];all.push(fn);listeners.set(type,all);},
    removeEventListener(type,fn){listeners.set(type,(listeners.get(type)||[]).filter(item=>item!==fn));}
  };
  let requestIndex=0;
  const context=vm.createContext({document,URL,Date,console,fetch:async(url,options={})=>{
    const body=options.body?JSON.parse(options.body):null;
    calls.push({url,method:options.method||'GET',body});
    if(requests.length){const item=requests[requestIndex++];return response(typeof item==='function'?item(url,options):item.value,item.ok!==false);}
    if(url==='/api/release-notes'&&!options.method)return response(status||{current_version:'2.0.0',pending:false,release:release('v2.0.0'),latest:release('v2.0.0'),update_available:false});
    if(url==='/api/release-notes'&&body&&body.action==='claim')return response(claim||{current_version:'2.0.0',pending:false,claim:null,release:release('v2.0.0')});
    if(url==='/api/release-notes'&&body&&body.action==='shown')return response({shown:true});
    if(url==='/api/release-notes'&&body&&body.action==='check')return response({current_version:'2.0.0',pending:false,release:release('v2.0.0','## Loaded notes'),latest:release('v2.0.0'),update_available:false});
    throw new Error(`Unexpected request ${url}`);
  }});
  vm.runInContext(source,context,{filename:'release-notes.js'});
  return {context,document,dialog,otherDialog,nodes,calls,listeners};
}

async function main(){
  const md=harness();
  const sample=[
    '# What changed','',
    '| Platform | Download | Docs |','| --- | --- | --- |',
    '| Windows | [EXE](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.0.0/AgentTelemetrySetup.exe) | [MCP guide](https://github.com/uttamdeb/agent-telemetry/blob/main/docs/mcp.md) |',
    '| macOS | [DMG](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.0.0/AgentTelemetry.dmg) | [Marketplace](https://marketplace.visualstudio.com/items?itemName=example.extension) |',
    '', '- **A bold fix** with `inline code`.',
    '', '<script>alert(1)</script><img src=x onerror=alert(1)>',
    '', '[bad](javascript:alert(1))',
    '', '![remote pixel](https://attacker.invalid/pixel.png)'
  ].join('\n');
  const rendered=vm.runInContext(`releaseNotesMarkdown(${JSON.stringify(sample)})`,md.context);
  assert.match(rendered,/<h1>What changed<\/h1>/);
  assert.match(rendered,/<table>/,'GFM download table was not rendered as a table');
  assert.match(rendered,/releases\/download\/v2\.0\.0\/AgentTelemetrySetup\.exe/,'Release asset links should remain usable');
  assert.match(rendered,/marketplace\.visualstudio\.com/,'External HTTPS links should remain usable');
  assert.match(rendered,/rel="noopener noreferrer"/,'External links need opener isolation');
  assert.match(rendered,/&lt;script&gt;alert\(1\)&lt;\/script&gt;/,'Raw HTML must be escaped');
  assert.match(rendered,/&lt;img src=x onerror=alert\(1\)&gt;/,'Raw image markup must be escaped');
  assert.doesNotMatch(rendered,/<img|<script|javascript:|src="https:\/\//,'Unsafe HTML or URL became active');
  assert.match(rendered,/remote pixel/,'Image Markdown should retain its caption');
  assert.doesNotMatch(rendered,/attacker\.invalid/,'Image Markdown must not create a remote request link');
  assert.match(vm.runInContext("releaseNotesMarkdown('**bold** and *italic* with `code`')",md.context),/<strong>bold<\/strong> and <em>italic<\/em>/);
  const codeBlock=['```js','const x = "<x>";','```'].join('\n');
  assert.match(vm.runInContext(`releaseNotesMarkdown(${JSON.stringify(codeBlock)})`,md.context),/<pre><code>const x = &quot;&lt;x&gt;&quot;;<\/code><\/pre>/);
  assert.equal(vm.runInContext(`releaseNotesSafeUrl('https://github.com/uttamdeb/agent-telemetry/releases/tag/v2.0.0')`,md.context),'https://github.com/uttamdeb/agent-telemetry/releases/tag/v2.0.0');
  assert.equal(vm.runInContext(`releaseNotesSafeUrl('https://example.com/releases/tag/v2.0.0')`,md.context),'','Footer links must target the official release page');

  // A first install establishes its baseline on a normal dashboard open, with no popup.
  const first=harness({status:{current_version:'2.0.0',pending:false,release:release('v2.0.0'),latest:null,update_available:false},
    claim:{current_version:'2.0.0',pending:false,claim:null,release:release('v2.0.0')}});
  assert.equal(first.calls.length,0,'Loading the module must not fetch or claim automatically');
  assert.equal(await vm.runInContext('checkForReleaseNotes()',first.context),false);
  assert.deepEqual(first.calls.map(call=>[call.method,call.body&&call.body.action]),[['GET',null],['POST','claim']]);
  assert.equal(first.dialog.open,false,'First install should not show old notes');
  await vm.runInContext('checkForReleaseNotes()',first.context);
  assert.equal(first.calls.length,2,'Completed baseline check must not repeat on polling');

  // An update available ahead of the installed version is not an installed-version popup.
  const beforeInstall=harness({status:{current_version:'2.0.0',pending:false,release:release('v2.0.0'),latest:release('v3.0.0'),update_available:true},
    claim:{current_version:'2.0.0',pending:false,claim:null,release:release('v2.0.0')}});
  assert.equal(await vm.runInContext('checkForReleaseNotes()',beforeInstall.context),false);
  assert.equal(beforeInstall.dialog.open,false);

  // Current installed notes remain eligible even when a still newer release exists.
  const pending=harness({status:{current_version:'2.0.0',pending:true,release:release('v2.0.0','## Installed version'),latest:release('v3.0.0'),update_available:true},
    claim:{current_version:'2.0.0',pending:true,claim:'a'.repeat(64),release:release('v2.0.0','## Installed version')}});
  pending.context.NATIVE_APP_MODE=true; // nativeApp=1 is the dashboard WebView, not a tray-only launch
  assert.equal(await vm.runInContext('checkForReleaseNotes()',pending.context),true);
  assert.equal(pending.dialog.open,true);
  assert.match(pending.nodes.get('[data-rn-body]').innerHTML,/Installed version/);
  assert.deepEqual(pending.calls.map(call=>call.body&&call.body.action),[null,'claim','shown']);
  await vm.runInContext('checkForReleaseNotes()',pending.context);
  assert.equal(pending.calls.length,3,'A popup should be claimed only once per page');

  // A failed showModal leaves the server lease unacknowledged.
  const failed=harness({status:{current_version:'2.0.0',pending:true,release:release('v2.0.0'),latest:null,update_available:false},
    claim:{current_version:'2.0.0',pending:true,claim:'b'.repeat(64),release:release('v2.0.0')},showFails:true});
  assert.equal(await vm.runInContext('checkForReleaseNotes()',failed.context),false);
  assert.equal(failed.calls.some(call=>call.body&&call.body.action==='shown'),false,'Do not ack a popup that failed to open');
  await vm.runInContext('checkForReleaseNotes()',failed.context);
  assert.equal(failed.calls.length,2,'A failed show must not loop on every data poll');

  // Hidden pages defer before the claim; becoming visible resumes once.
  const hidden=harness({hidden:true});
  assert.equal(await vm.runInContext('checkForReleaseNotes()',hidden.context),false);
  assert.equal(hidden.calls.length,0,'Hidden view must not reserve the popup lease');
  hidden.document.visibilityState='visible';
  for(const fn of hidden.listeners.get('visibilitychange')||[]) fn({});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(hidden.calls.length,2,'Visible dashboard should resume its single baseline claim');

  // Existing dialogs defer the claim until close rather than competing for the top layer.
  const blocked=harness({otherOpen:true});
  assert.equal(await vm.runInContext('checkForReleaseNotes()',blocked.context),false);
  assert.equal(blocked.calls.length,0,'An existing dialog must defer the lease');
  blocked.otherDialog.open=false;
  for(const fn of blocked.listeners.get('close')||[]) fn({target:blocked.otherDialog});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(blocked.calls.length,2,'Closing the other dialog should resume once');

  // Manual retry uses only the release-notes endpoint, and closing restores focus.
  const manual=harness();
  vm.runInContext(`RELEASE_NOTES_STATE.loaded=true; RELEASE_NOTES_STATE.currentVersion='2.0.0'; RELEASE_NOTES_STATE.release=null`,manual.context);
  const opener={isConnected:true,focused:false,focus(){this.focused=true;}};
  manual.context.opener=opener;
  assert.equal(await vm.runInContext('openReleaseNotes(opener)',manual.context),true);
  assert.equal(manual.dialog.open,true);
  assert.equal(await vm.runInContext('loadReleaseNotes()',manual.context),true);
  assert.deepEqual(manual.calls.map(call=>call.body&&call.body.action),['check'],'Load notes should not run the Git updater');
  assert.match(manual.nodes.get('[data-rn-body]').innerHTML,/Loaded notes/);
  manual.dialog.close();
  assert.equal(opener.focused,true,'Closing the modal should restore focus');
  const replacedOpener={isConnected:true,focus(){this.focused=true;}}, fallback={isConnected:true,focus(){this.focused=true;}};
  manual.context.replacedOpener=replacedOpener; manual.document.releaseNotesLink=fallback;
  assert.equal(await vm.runInContext('openReleaseNotes(replacedOpener)',manual.context),true);
  replacedOpener.isConnected=false;
  manual.dialog.close();
  assert.equal(fallback.focused,true,'If polling replaces the opener, focus should return to the current What’s new link');

  // Empty published releases and unidentified builds are described accurately.
  const empty=vm.runInContext(`releaseNotesPayload(${JSON.stringify(release('v2.0.0',''))}).content`,manual.context);
  assert.match(empty,/No release notes were published/);
  assert.doesNotMatch(empty,/data-release-notes-load/,'Published empty notes should not offer a pointless retry');
  const unavailable=vm.runInContext("releaseNotesPayload("+JSON.stringify({...release('v2.0.0',''),availability:'unavailable',empty:true})+").content",manual.context);
  assert.match(unavailable,/Release notes aren’t available offline yet/,'Unavailable notes must not be described as an intentionally empty release');
  assert.match(unavailable,/data-release-notes-load/,'Unavailable notes should offer an explicit retry');
  vm.runInContext('RELEASE_NOTES_STATE.currentVersion=null',manual.context);
  const unknown=vm.runInContext('releaseNotesPayload({}).content',manual.context);
  assert.match(unknown,/This build version can’t be identified/);
  assert.doesNotMatch(unknown,/v2\.0\.0/,'Unknown builds must not claim notes for a version');

  const oversized='x'.repeat(49000);
  const bounded=vm.runInContext(`releaseNotesPayload({body:${JSON.stringify(oversized)}})`,manual.context);
  assert.equal(bounded.truncated,true,'Frontend should mark content clipped at its render bound');
  assert.ok(bounded.content.length<49000,'Markdown output should respect its bounded input size');

  const offline=harness({requests:[{value:{error:'offline'},ok:false}]});
  vm.runInContext(`RELEASE_NOTES_STATE.loaded=true; RELEASE_NOTES_STATE.currentVersion='2.0.0'; RELEASE_NOTES_STATE.release=null`,offline.context);
  const offlineOpener={isConnected:true,focus(){}}; offline.context.offlineOpener=offlineOpener;
  await vm.runInContext('openReleaseNotes(offlineOpener)',offline.context);
  assert.equal(await vm.runInContext('loadReleaseNotes()',offline.context),false);
  assert.match(offline.nodes.get('[data-rn-body]').innerHTML,/Release notes could not be loaded/,'Load failure should appear inline');
  assert.match(offline.nodes.get('[data-rn-body]').innerHTML,/data-release-notes-load/,'Load failure should leave a retry control');

  const mismatched=harness({requests:[{value:{current_version:'2.0.0',release:null,latest:release('v3.0.0','## Latest release body'),update_available:true,error:'Mock offline'}}]});
  vm.runInContext("RELEASE_NOTES_STATE.loaded=true; RELEASE_NOTES_STATE.currentVersion='2.0.0'; RELEASE_NOTES_STATE.release=null",mismatched.context);
  mismatched.dialog.open=true;
  assert.equal(await vm.runInContext('loadReleaseNotes()',mismatched.context),true);
  assert.doesNotMatch(mismatched.nodes.get('[data-rn-body]').innerHTML,/Latest release body/,'A later release body must never be substituted for unavailable installed-version notes');
  assert.match(mismatched.nodes.get('[data-rn-meta]').innerHTML,/v2\.0\.0/,'Unavailable fallback should identify the installed version');
  assert.match(mismatched.nodes.get('[data-rn-body]').innerHTML,/data-release-notes-load/,'Installed-version fallback should retain a retry');

  // A pending tab that wakes after its two-minute claim expires must reclaim.
  const lease=harness({requests:[
    {value:{current_version:'2.0.0',pending:true,release:release('v2.0.0','## Installed version'),latest:release('v3.0.0'),update_available:true}},
    {value:{current_version:'2.0.0',pending:true,claim:'fresh-claim',release:release('v2.0.0','## Installed version')}},
    {value:{shown:true}}
  ]});
  vm.runInContext("RELEASE_NOTES_STATE.started=true; RELEASE_NOTES_STATE.deferred=true; RELEASE_NOTES_STATE.pendingShow={release:"+JSON.stringify(release('v2.0.0','## Installed version'))+",claim:'expired-claim',claimedAt:Date.now()-111000}",lease.context);
  lease.document.visibilityState='visible';
  vm.runInContext('releaseNotesResume()',lease.context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(lease.calls.map(call=>call.body&&call.body.action),[null,'claim','shown'],'Expired deferred leases should be refreshed before opening');
  assert.equal(lease.dialog.open,true);
}

main().then(()=>console.log('Release notes Markdown and first-open lifecycle passed.')).catch(error=>{console.error(error);process.exitCode=1;});
