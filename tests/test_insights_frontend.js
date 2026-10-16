/* Synthetic contribution fixtures; no personal logs or browser dependencies. */
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.resolve(__dirname,'..'), nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',hidden:false});return nodes.get(id)};
const context=vm.createContext({console,URLSearchParams,Set,Date,location:{search:'',hash:''},
  document:{addEventListener(){},getElementById:node,querySelector(){return null}},
  getComputedStyle:()=>({getPropertyValue:()=> '#000'})});
for(const file of ['static/core.js','static/charts.js','static/insights.js'])
  vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context);
const views=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),context);
const run=code=>vm.runInContext(code,context);
run(`RAW={records:[],sessions:[],hourly:[],device:{id:'local'},prices:{M:[2,10,2.5,4,.2]},model_vendor:{M:'OpenAI'}};
var before=[{project:'A',source:'codex',model:'M',in:100,out:20,cr:200,cc:10,cost:10,asst:2,active:20},
 {project:'B',source:'claude',model:'M',in:50,cost:5,asst:1,active:5}];
var after=[{project:'A',source:'codex',model:'M',in:500,out:30,cr:100,cc:10,cost:20,asst:3,active:40},
 {project:'C',source:'claude',model:'M',in:20,cost:3,asst:1,active:1}];`);
for(const metric of ['tokens','cost','messages','time']){
  for(const dimension of ['project','model','source',...(['tokens','cost'].includes(metric)?['kind']:[])]){
    const data=JSON.parse(run(`JSON.stringify(usageChange(after,before,'${metric}','${dimension}'))`));
    assert.ok(Math.abs(data.rows.reduce((sum,r)=>sum+r.delta,0)-data.delta)<1e-8,`${metric}/${dimension} must reconcile`);
    assert.ok(Math.abs(data.rows.reduce((sum,r)=>sum+r.current,0)-data.current)<1e-8);
    assert.ok(Math.abs(data.rows.reduce((sum,r)=>sum+r.previous,0)-data.previous)<1e-8);
  }
}
assert.equal(run(`usageChange(after,before,'cost','project').rows.find(r=>r.key==='B').delta`),-5,'Departed projects must remain');
assert.equal(run(`usageChange(after,before,'cost','project').rows.find(r=>r.key==='C').previous`),0,'New projects have zero recorded prior contribution');
assert.equal(run(`usageChange([{cost:10,project:'A'},{cost:10,project:'B'}],[{cost:20,project:'A'}],'cost','project').delta`),0);
assert.equal(run(`usageChange([{cost:10,project:'A'},{cost:10,project:'B'}],[{cost:20,project:'A'}],'cost','project').rows.length`),2,'Offsetting movements must remain visible');
assert.equal(run(`Object.values(changeParts({model:'M',cost:2,in:100},'cost')).reduce((a,b)=>a+b,0)`),2);
assert.equal(run(`changeParts({model:'M',cost:.00001,in:100},'cost').other`),.00001,'Discounted logged cost cannot invent token attribution');
assert.equal(run(`Object.values(changeParts({in:100,out:20,cr:200,cc:10,reason:18},'tokens')).reduce((a,b)=>a+b,0)`),330,'Reasoning is already within output');
run(`S.preset='custom';S.from='2026-10-10';S.to='2026-10-11';S.metric='tokens';
RAW.records=[{date:'2026-10-08',source:'codex',model:'M',project:'<img src=x>',in:100},
{date:'2026-10-10',source:'codex',model:'M',project:'<img src=x>',in:300}];
renderUsageChange('overview',slice());`);
assert.match(node('overviewChanges').innerHTML,/&lt;img src=x&gt;/);
assert.doesNotMatch(node('overviewChanges').innerHTML,/<img src=x>/);
assert.match(node('overviewChanges').innerHTML,/data-change-period="previous"/);
assert.match(node('overviewChanges').innerHTML,/2026-10-08 – 2026-10-09/);
run(`RAW.records=RAW.records.slice(0,1);renderUsageChange('overview',slice());`);
assert.equal(node('overviewChanges').hidden,false,'No current usage still needs a decline explanation');
assert.match(node('overviewChanges').innerHTML,/−100/);
run(`S.preset='all';renderUsageChange('overview',slice());`);
assert.equal(node('overviewChanges').hidden,true,'All time has no valid previous period');
run(`S.preset='custom';S.from='2026-10-10';S.to='2026-10-11';S.exactOnly=true;S.tools=new Set(['copilot']);
RAW.records=[{date:'2026-10-08',source:'copilot',model:'M',project:'P',in:999,cost:99,exact:{in:10,cost:1}},
{date:'2026-10-10',source:'copilot',model:'M',project:'P',in:999,cost:99,exact:{in:20,cost:2}}];
renderUsageChange('overview',slice());`);
assert.match(node('overviewChanges').innerHTML,/\+10 tokens/);
assert.doesNotMatch(node('overviewChanges').innerHTML,/>999</);
run(`var nextView;setView=v=>{nextView=v};syncSearchBoxes=()=>{};
S.search='hidden';S.provs=new Set(['OpenAI']);S.ides=new Set(['VS Code']);S.devs=new Set(['local']);
drillUsageChange('project','P','previous');`);
assert.equal(run(`S.from`),'2026-10-08');
assert.equal(run(`S.to`),'2026-10-09');
assert.equal(run(`S.search`),'');
assert.equal(run(`S.projs.has('P')&&S.provs.has('OpenAI')&&S.ides.has('VS Code')&&S.devs.has('local')&&S.exactOnly`),true,'Drilldown preserves unrelated filters');
assert.equal(run(`nextView`),'sessions');
console.log('Usage-change contributions reconcile across metrics and filters; decline, zero baseline, escaping and session drilldowns passed.');
