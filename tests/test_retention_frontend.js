/* Synthetic kept-usage checks. No source logs, settings or ledger are read. */
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
const run=code=>vm.runInContext(code,context);
const day=[1,100,10,0,0,1,1,2,0,30,0,0,0,0,0];
run(`S.preset='custom';S.from='2026-10-01';S.to='2026-10-31';
RAW={device:{id:'local'},devices:[{id:'local',name:'Local'}],
  records:[{date:'2026-10-18',source:'opencode',model:'M1',project:'P',ide:'CLI',device:'local',in:200,out:20,cost:2}],
  hourly:[],tools:[],model_vendor:{M1:'Zhipu'},
  sessions:[
    {id:'ses_gone',rename_id:'ses_gone_full',source:'opencode',device:'local',project:'P',ide:'CLI',title:'Deleted in tool',
     model:'M1',models:['M1'],nmodels:1,end:'2026-10-18T12:00:00Z',in:100,out:10,cost:1,user:1,asst:1,
     days:{'2026-10-18':${JSON.stringify(day)}},model_days:{M1:{'2026-10-18':${JSON.stringify(day)}}},
     retained:true,archived:true,rename_available:false},
    {id:'ses_part',rename_id:'ses_part_full',source:'opencode',device:'local',project:'P',ide:'CLI',title:'Undo in tool',
     model:'M1',models:['M1'],nmodels:1,end:'2026-10-18T13:00:00Z',in:100,out:10,cost:1,user:1,asst:1,
     days:{'2026-10-18':${JSON.stringify(day)}},model_days:{M1:{'2026-10-18':${JSON.stringify(day)}}},
     retained_usage:true,archived:false},
    {id:'old',rename_id:'old-full',source:'claude',device:'local',project:'P',ide:'CLI',title:'Pruned log',
     model:'M1',models:['M1'],nmodels:1,end:'2026-10-18T14:00:00Z',in:1,out:1,cost:0,user:1,asst:1,
     days:{'2026-10-18':${JSON.stringify(day)}},model_days:{M1:{'2026-10-18':${JSON.stringify(day)}}},archived:true}]};
var opened='';openDrawer=html=>{opened=html};loadSessionTimeline=()=>{};`);

const drawerFor=title=>{run(`opened='';openSessionDetails(RAW.sessions.find(s=>s.title===${JSON.stringify(title)}))`);return run('opened')};
const gone=drawerFor('Deleted in tool');
assert.match(gone,/This session was deleted in OpenCode; its numbers are kept from an earlier scan\./);
assert.doesNotMatch(gone,/pruned from disk/,'A session deleted in its tool is not described as a pruned log');
const part=drawerFor('Undo in tool');
assert.match(part,/Part of this session was deleted in OpenCode; that usage is kept from an earlier scan\./);
assert.doesNotMatch(part,/This session was deleted/);
const pruned=drawerFor('Pruned log');
assert.match(pruned,/This log has been pruned from disk/);
assert.doesNotMatch(pruned,/deleted in/);
// Kept sessions stay in every figure: the flags never filter a session out.
assert.equal(run('slice().sessions.length'),3);
console.log('Kept-usage drawer notes and unchanged session counts passed.');
