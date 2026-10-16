const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const code = fs.readFileSync('static/maintenance.js', 'utf8');
function fixture(){
  const calls=[];
  const nodes={
    '[data-maint-diagnostics]':{innerHTML:''},
    '[data-maint-backups]':{innerHTML:''},
    '[data-maint-msg]':{textContent:'',className:'stg-msg'},
  };
  const root={innerHTML:'',dataset:{},isConnected:true,
    querySelector:s=>nodes[s]||null,querySelectorAll:()=>[],
    addEventListener:(type,fn)=>{root.listener=fn;},contains:()=>true};
  const ctx={
    document:{getElementById:id=>id==='maintenanceSettings'?root:null},
    esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    toolLabel:s=>`<span>${String(s)}</span>`,
    askConfirm:async()=>false,
    fetch:async(url,options={})=>{
      calls.push({url,options});
      if(options.method==='POST') return {ok:true,json:async()=>({ticket:'one-use-ticket',backup:{created_at:'2026-10-16T12:00:00Z',sessions:4,bytes:200},current:{sessions:8}})};
      if(url==='/api/diagnostics') return {ok:true,json:async()=>({read:2,errors:1,sources:[{source:'claude',status:'partial_error',read:2,discovered:3,errors:1,hint:'Refresh after checking the source log.'},{source:'<img src=x onerror=alert(1)>',status:'error',read:0,discovered:1,errors:1}],issues:[]})};
      return {ok:true,json:async()=>({backups:[{id:'x" onmouseover="alert(1)',reason:'C:/private/secret',created_at:'2026-10-16T12:00:00Z',files:2,sessions:4,bytes:2048,valid:false},{id:'20261016T120000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-manual.json',reason:'manual',created_at:'2026-10-16T12:00:00Z',files:2,sessions:4,bytes:2048,valid:true}]})};
    },
  };
  vm.createContext(ctx);
  vm.runInContext(code+'\nglobalThis.api={renderMaintenance,loadMaintenance};',ctx);
  return {ctx,root,nodes,calls};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

(async()=>{
  const f=fixture();
  f.ctx.api.renderMaintenance(f.root);
  await settle();
  assert.match(f.nodes['[data-maint-diagnostics]'].innerHTML,/claude/);
  assert.match(f.nodes['[data-maint-backups]'].innerHTML,/<b>Backup<\/b>/);
  assert.match(f.nodes['[data-maint-backups]'].innerHTML,/data-backup-id="20261016T120000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-manual\.json"/);
  assert.match(f.nodes['[data-maint-backups]'].innerHTML,/data-backup-id="" disabled/);
  assert.match(f.nodes['[data-maint-diagnostics]'].innerHTML,/Unknown source/);
  assert.doesNotMatch(f.nodes['[data-maint-diagnostics]'].innerHTML,/<img src=x/);
  assert.match(f.nodes['[data-maint-backups]'].innerHTML,/Backup/);
  assert.doesNotMatch(f.nodes['[data-maint-backups]'].innerHTML,/data-backup-id="x" onmouseover=/);
  assert.doesNotMatch(f.nodes['[data-maint-backups]'].innerHTML,/C:\/private\/secret/);
  assert.equal(f.calls.filter(x=>x.options.method==='POST').length,0);

  let confirmOptions;
  f.ctx.askConfirm=async options=>{confirmOptions=options;return false;};
  const restoreButton={dataset:{maintAction:'restore',backupId:'20261016T120000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-manual.json'},disabled:false};
  const click=(button)=>f.root.listener({target:{closest:()=>button}});
  await click(restoreButton);
  assert.equal(f.calls.filter(x=>x.options.method==='POST').length,1,'cancel only requests preview');
  assert.equal(JSON.parse(f.calls.at(-1).options.body).action,'preview');
  assert.equal(confirmOptions.danger,true);
  assert.match(confirmOptions.body,/Restoring may replace archived history/);

  f.ctx.askConfirm=async()=>true;
  await click(restoreButton);
  const writes=f.calls.filter(x=>x.options.method==='POST');
  assert.equal(JSON.parse(writes.at(-1).options.body).action,'restore');
  assert.equal(JSON.parse(writes.at(-1).options.body).ticket,'one-use-ticket');
  assert.equal(JSON.parse(writes.at(-1).options.body).confirmed,true);
  assert.equal(writes.at(-1).options.headers['Content-Type'],'application/json');
  console.log('Maintenance diagnostics reject unsafe labels and backup IDs, and require preview confirmation before restore.');
})().catch(error=>{console.error(error);process.exitCode=1;});
