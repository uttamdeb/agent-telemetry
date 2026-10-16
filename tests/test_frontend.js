/* Development checks only. The application itself still has no Node dependency. */
const fs=require('fs'), path=require('path'), vm=require('vm'), assert=require('assert');
const root=path.resolve(__dirname,'..');
const document={addEventListener(){},getElementById(){return null},querySelector(){return null}};
const context=vm.createContext({document,console,URLSearchParams,Set,Date,location:{search:'',hash:''},
  getComputedStyle:()=>({getPropertyValue:()=>"#000"})});
vm.runInContext(fs.readFileSync(path.join(root,'static/core.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root,'static/charts.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root,'static/insights.js'),'utf8'),context);
const views=fs.readFileSync(path.join(root,'static/views.js'),'utf8');
vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),context);
const run=code=>vm.runInContext(code,context);
assert.equal(run(`SRC.codex.icon`),'openai');
assert.equal(run(`SRC.opencode.label`),'OpenCode');
assert.match(run(`toolLabel('opencode')`),/>OpenCode</);
assert.match(run(`srcBadge('opencode')`),/OpenCode/);
assert.equal(run(`sdkUsageNote({source:'copilot'})`),'');
assert.match(run(`sdkUsageNote({copilot_sdk:{usage_status:'unavailable',missing_turns:1}})`),/unavailable, not zero/);
assert.match(run(`sdkUsageNote({copilot_sdk:{usage_status:'partial',errors:2,aborts:1}})`),/2 SDK errors/);
assert.match(run(`sdkUsageBadge({copilot_sdk:{usage_status:'unavailable'}})`),/tabindex="0"/);
assert.equal(run(`sdkUsageBadge({copilot_sdk:{usage_status:'recorded'}})`),'');
assert.equal(run(`sdkField({copilot_sdk:{recorded_fields:['in','out']}},'cc','0')`),'Unavailable');
assert.equal(run(`sdkField({copilot_sdk:{recorded_fields:['cc']}},'cc','0')`),'0');
assert.match(run(`sdkUsageDetails({copilot_sdk:{usage_status:'recorded',usage_sources:['vscode_turn_usage']}})`),/VS Code saved turn totals/);
assert.doesNotMatch(run(`sdkUsageDetails({copilot_sdk:{usage_status:'partial',limitations:['undated_usage'],shutdown_totals:[{model:'<img src=x onerror=alert(1)>',in:100}]}})`),/<img src=x/);
assert.match(run(`quotaWindowHTML({label:'5-hour limit',remaining_percent:89,resets_at:2000},{observed_at:1000},1000)`),/89% left/);
assert.match(run(`quotaWindowHTML({label:'5-hour limit',remaining_percent:0,resets_at:2000},{observed_at:1000},1000)`),/value="0"/);
assert.doesNotMatch(run(`quotaWindowHTML({label:'5-hour limit',remaining_percent:null,resets_at:null},{observed_at:1000},1000)`),/<progress|100% left/);
assert.match(run(`quotaWindowHTML({label:'5-hour limit',remaining_percent:89,resets_at:1100},{observed_at:1000},1301)`),/Stale|Reset passed/);
assert.match(run(`quotaWindowHTML({label:'5-hour limit',remaining_percent:89,resets_at:1100},{observed_at:1000},1301)`),/89% left/);
assert.doesNotMatch(run(`quotaWindowHTML({label:'<img src=x onerror=alert(1)>',remaining_percent:89,resets_at:2000},{observed_at:1000},1000)`),/<img src=x/);
assert.match(run(`sessionAccountDetails({source:'codex',account_metadata:{creator_account_id:'creator-A'}})`),/Thread creator account/);
assert.match(run(`sessionAccountDetails({source:'claude'})`),/Not recorded/);
assert.doesNotMatch(run(`sessionAccountDetails({source:'claude',account_metadata:{recorded_account_ids:['<img src=x onerror=alert(1)>']}})`),/<img src=x/);
assert.doesNotMatch(run(`accountEntryHTML({source:'codex',profile:'<img src=x onerror=alert(1)>',identity:{auth:'apiKey',email:'<img src=x>',plan:null},snapshots:[]})`),/<img src=x/);
{
  const spend=run(`overviewSpendSummary({cost:209.49},['2026-10-14','2026-10-15'],
    [{date:'2026-10-14',cost:100},{date:'2026-10-15',cost:109.49}])`);
  assert.equal(spend.label,'Est. spend');
  assert.equal(spend.value,'$209','Overview spend must use complete priced record costs with the design’s whole-dollar formatting');
  assert.match(spend.title,/cache reads are included/);
  assert.match(spend.spark,/<svg/,'The spend row should retain its wide trend sparkline');
  const cache=run(`overviewCacheDetail({tok:1190000000,cr:1137000000})`);
  assert.equal(cache.value,'53M','Excluded-cache tokens must be total tokens minus cached reads, rounded to whole millions');
  assert.match(cache.title,/Tokens excluding cache reads: 53M\./);
  assert.equal(run(`overviewCacheDetail({tok:1190000000,cr:52700000}).value`),'1.14B');
  assert.match(cache.title,/remain included in estimated spend/);
  assert.equal(run(`overviewCacheDetail({tok:100,cr:120}).value`),'0','Malformed overcount must not produce negative excluded tokens');
  const hero=run(`heroHTML({label:'Tokens',value:'1.19B',
    valueTooltip:{id:'ovTokenTooltip',text:overviewCacheDetail({tok:1190000000,cr:1137000000}).title},
    feature:{label:'Est. spend',value:'$209',title:'API estimate',spark:'<svg></svg>'},
    facts:[{l:'Daily average',v:'27.6M'}]})`);
  assert.match(hero,/<div class="hero-value" tabindex="0" aria-describedby="ovTokenTooltip">1.19B<\/div>/);
  assert.match(hero,/id="ovTokenTooltip" role="tooltip">Tokens excluding cache reads: 53M\./);
  assert.doesNotMatch(hero,/>53M<|Excluded Cache|hero-secondary/,'Cache-excluded count must appear only in the hover tooltip');
  assert.doesNotMatch(hero,/<div class="hero-value"[^>]*title=/,'Use the visible tooltip instead of a delayed browser title');
  assert.match(run(`heroHTML({label:'Tokens',value:'1',valueTooltip:{id:'"<id>',text:'"<unsafe>'}})`),/id="&quot;&lt;id&gt;" role="tooltip">&quot;&lt;unsafe&gt;/);
  assert.ok(hero.indexOf('hero-feature')<hero.indexOf('hero-facts'));
  assert.match(hero,/hero-feature-spark/);
}
{
  // Render the actual Overview with mock DOM/usage, without Chart.js or a real ledger.
  const nodes=new Map();
  const node=id=>{
    if(!nodes.has(id)) nodes.set(id,{tagName:'DIV',innerHTML:'',style:{},attributes:{},
      classList:{toggle(){}},setAttribute(k,v){this.attributes[k]=v},
      querySelector:()=>({addEventListener(){}})});
    return nodes.get(id);
  };
  const overview=vm.createContext({console,Date,Set,URLSearchParams,location:{search:'',hash:''},
    document:{addEventListener(){},getElementById:node,
      querySelector:selector=>selector==='.tv[data-tv="monthlyRhythm"]'?node('monthToggle'):null},
    getComputedStyle:()=>({getPropertyValue:()=>"#000"})});
  for(const file of ['static/core.js','static/charts.js','static/insights.js'])
    vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),overview);
  vm.runInContext(views.slice(0,views.indexOf('document.getElementById("tabs").addEventListener')),overview);
  const check=code=>vm.runInContext(code,overview);
  check(`var calendarDays;
    mk=()=>{}; renderDeviceDistribution=()=>{}; renderHighlights=()=>{};
    renderHeat=()=>{}; renderTokenMix=()=>{};
    renderCalendar=days=>{calendarDays=days};
    var windows=activityMonthWindows(12), today=windows[11].to;
    var tomorrow=new Date(today+'T00:00:00');tomorrow.setDate(tomorrow.getDate()+1);
    var exactRow={date:today,source:'copilot',device:'Mac',model:'M',project:'P',ide:'VS Code',
      in:900000,out:100000,cost:10,asst:9,user:8,active:600,
      exact:{in:1000,out:200,cost:.25,asst:1,user:1,active:60}};
    RAW={device:{id:'Mac'},devices:[],sessions:[],hourly:[],model_vendor:{M:'OpenAI'},
      records:[exactRow,{...exactRow,exact:null},
        {...exactRow,date:windows[0].from,source:'codex',in:300,out:100,exact:null},
        {...exactRow,date:'2000-01-01'},
        {...exactRow,date:dkey(tomorrow)}]};
    S.preset='custom'; S.from=today; S.to=today; S.exactOnly=true;
    S.tools=new Set(['copilot','codex']);
    var originalRecords=JSON.stringify(RAW.records);
    viewOverview(slice());`);
  assert.equal(check('totals(slice().recs).tok'),1200,'Hero must use the exact Copilot subset');
  assert.equal(check('monthlyRhythmRows()[11].value'),1200,'Monthly bars must exclude estimated and future usage');
  assert.equal(check('monthlyRhythmRows()[0].value'),400,'The fixed month window must retain older exact tool usage');
  assert.equal(check('monthlyRhythmRows().reduce((n,r)=>n+r.value,0)'),1600,'Monthly history must exclude dates before its 12-month window');
  assert.equal(check('calendarDays[today]'),1200,'Activity calendar and hero must use the same precision');
  assert.equal(check('JSON.stringify(RAW.records)===originalRecords'),true,'Rendering must not mutate telemetry');
  for(const [filter,value] of [['tools','claude'],['models','Missing'],['provs','Anthropic'],
      ['projs','Missing'],['ides','Missing'],['devs','PC']]){
    check(`var savedFilter=S.${filter}; S.${filter}=new Set([${JSON.stringify(value)}]); viewOverview(slice());`);
    assert.equal(check('monthlyRhythmRows().reduce((n,r)=>n+r.value,0)'),0,filter+' must filter monthly activity');
    assert.equal(check('Object.keys(calendarDays).length'),0,filter+' must filter the calendar');
    check(`S.${filter}=savedFilter;`);
  }
  check(`S.exactOnly=false;viewOverview(slice());`);
  assert.equal(check('monthlyRhythmRows()[11].value'),2000000,'Normal mode must retain estimated contributions');
  assert.equal(check('calendarDays[today]'),2000000);
  for(const metric of ['tokens','cost','messages','time']){
    check(`S.metric=${JSON.stringify(metric)};S.exactOnly=true;viewOverview(slice());`);
    const hero=node('ovHero').innerHTML, tiles=node('kpis').innerHTML;
    assert.equal(hero.includes('role="tooltip"'),metric==='tokens','Only token mode needs the cache tooltip');
    assert.match(hero+tiles,/Est\. spend/,metric+' must retain an estimated spend summary');
    assert.equal((tiles.match(/Est\. spend/g)||[]).length,['messages','time'].includes(metric)?1:0,
      metric+' must not duplicate spend already shown in the hero');
    if(['messages','time'].includes(metric)) assert.match(tiles,/>\$0\.25</,'Spend must use exact filtered costs');
  }
  assert.match(check('chartTable("monthlyRhythm")'),/>1,200</,'Monthly table must show full token counts');
  assert.equal((check('chartTable("monthlyRhythm")').match(/<tr>/g)||[]).length,13,'All 12 months need a table row');
  check(`S.tableView.add('monthlyRhythm');applyTableView('monthlyRhythm');`);
  assert.equal(node('tv-monthlyRhythm').hidden,false);
  assert.equal(node('monthlyRhythm').style.display,'none');
  assert.equal(node('monthlyRhythmCard').style.display,undefined,'Table toggle must keep the card and its controls visible');
  assert.equal(node('monthToggle').attributes['aria-pressed'],'true');
  check(`S.tools=new Set(['claude']);viewOverview(slice());`);
  assert.match(node('monthlyRhythm').innerHTML,/No activity/);
  assert.doesNotMatch(node('tv-monthlyRhythm').innerHTML,/>1,200</,'Open table must refresh when filters change');
  assert.equal((node('tv-monthlyRhythm').innerHTML.match(/>0<\/td>/g)||[]).length,12,'Empty filters must retain zero-valued months');
  assert.equal(node('tv-monthlyRhythm').hidden,false,'Re-render must preserve the table choice');
  check(`S.tableView.delete('monthlyRhythm');applyTableView('monthlyRhythm');`);
  assert.equal(node('tv-monthlyRhythm').hidden,true);
  assert.equal(node('monthlyRhythm').style.display,'');
  // Existing canvas twins must continue hiding only their chart container.
  node('dailyChart').tagName='CANVAS';node('dailyChart').parentElement=node('dailyBox');
  check(`charts.dailyChart={data:{labels:['Day'],datasets:[{label:'Tokens',data:[42]}]}};
    S.tableView.add('dailyChart');applyTableView('dailyChart');`);
  assert.equal(node('dailyBox').style.display,'none');
  assert.match(node('tv-dailyChart').innerHTML,/>42</);
  console.log('Overview retains spend across metrics and applies precision/dimension filters to monthly and daily activity; SVG and canvas table twins remain usable.');
}
const shell=fs.readFileSync(path.join(root,'index.html'),'utf8');
const overviewShell=shell.slice(shell.indexOf('id="v-overview"'),shell.indexOf('id="v-cost"'));
const costShell=shell.slice(shell.indexOf('id="v-cost"'),shell.indexOf('id="v-models"'));
assert.doesNotMatch(overviewShell,/accountLimitsCard/);
assert.ok(costShell.indexOf('accountLimitsCard')>costShell.indexOf('tv-dailyCost'),'Limits must follow every other Cost card');
{
  const host={innerHTML:costShell,dataset:{}}, node={}, today=run('dkey(new Date())');
  let costRenders=0;
  const emptyCost=vm.createContext({Date,Set,console,Object,dkey:date=>run(`dkey(new Date(${date.getTime()}))`),
    RAW:{records:[],device:{id:'local'},sessions:[{source:'codex',days:{[today]:[1]}}]},
    S:{view:'cost',devs:new Set(),tools:new Set(['codex'])},VIEW_TITLES:{cost:'Cost',overview:'Overview'},
    document:{querySelectorAll:()=>[],getElementById:id=>id==='v-cost'?host:node},
    syncRangeUI(){},syncMetricUI(){},buildFilterPanels(){},renderPills(){},renderSidebarDevices(){},filtersToURL(){},
    runFinders:()=>[],slice:()=>({recs:[],sessions:[]}),viewCost(){costRenders++;}});
  emptyCost.hasPrev=()=>false;
  vm.runInContext(views.slice(views.indexOf('function activeQuotaSources('),views.indexOf('function quotaEntries(')),emptyCost);
  vm.runInContext(views.slice(views.indexOf('const VIEW_HTML={};'),views.indexOf('function syncSearchBoxes(')),emptyCost);
  vm.runInContext('renderAll()',emptyCost);
  assert.equal(costRenders,1,'An empty historical date range must not suppress current quotas');
  assert.match(host.innerHTML,/accountLimitsCard/);
  vm.runInContext('RAW.sessions=[];renderAll()',emptyCost);
  assert.match(host.innerHTML,/No activity/,'No current sessions should keep the regular empty view');
}
run(`var previousQuotaRaw=RAW;var quotaDay='2026-10-13';var quotaNow=new Date('2026-10-13T12:00:00');
 RAW={device:{id:'local'},sessions:[{source:'codex',device:'local',days:{[quotaDay]:[0,0,0,0,0,0,1]}},
   {source:'claude',device:'local',days:{'2026-10-12':[1]}},{source:'claude',device:'remote',days:{[quotaDay]:[1]}}]};`);
assert.equal(run(`activeQuotaSources(quotaNow).join(',')`),'codex','Yesterday and remote sessions must not show tools');
run(`RAW.sessions.push({source:'claude',device:'local',days:{},compactions:[{date:quotaDay}]});`);
assert.equal(run(`activeQuotaSources(quotaNow).join(',')`),'codex,claude','Compaction-only activity counts as a session today');
run(`S.tools.delete('claude');`);
assert.equal(run(`activeQuotaSources(quotaNow).join(',')`),'codex');
run(`S.tools.add('claude');S.devs.add('remote');`);
assert.equal(run(`activeQuotaSources(quotaNow).length`),0,'Private account cards must not appear for remote-only devices');
run(`S.devs.clear();RAW.sessions=[{source:'codex',start:'2026-10-13T09:00:00',days:{}}];`);
assert.equal(run(`activeQuotaSources(quotaNow).join(',')`),'codex','A newly started session can have no usage yet');
assert.equal(run(`activeQuotaSources(new Date('2026-10-14T12:00:00')).length`),0,'Visibility must update at midnight');
run(`var quotaSnapshot={origin:'Local Codex log',limit_id:'codex',observed_at:1000,windows:[{label:'5-hour limit',remaining_percent:89,resets_at:2000}]};
 ACCOUNTS={entries:[{source:'codex',device:'local',profile:'C:\\\\Users\\\\fixture\\\\.codex',identity:null,snapshots:[]},
  {source:'codex',device:'local',profile:'C:/Users/fixture/.codex',identity:null,snapshots:[quotaSnapshot]},
  {source:'codex',device:'local',profile:'C:/Users/fixture/.codex',identity:null,snapshots:[{...quotaSnapshot,limit_id:'premium',windows:[]}]},
  {source:'codex',device:'remote',profile:'remote',identity:null,snapshots:[quotaSnapshot]}]};`);
assert.equal(run(`quotaEntries('codex').length`),1,'Path separators must not duplicate the same profile');
assert.equal(run(`quotaEntries('codex')[0].snapshots.length`),1,'Empty and remote buckets must be hidden');
const tidyQuota=run(`accountEntryHTML(quotaEntries('codex')[0])`);
assert.match(tidyQuota,/89% left/);
assert.match(tidyQuota,/Account unverified/);
assert.match(tidyQuota,/data-account-refresh="codex"/);
assert.doesNotMatch(tidyQuota,/CLI profile:|Account \/ auth \/ plan unavailable|Quota percentages unavailable|Reported plan:|billing account is not recorded|premium/);
run(`ACCOUNTS.entries[0].identity={auth:'apiKey',email:'fixture@example.invalid'};`);
const apiQuota=run(`accountEntryHTML(quotaEntries('codex')[0])`);
assert.match(apiQuota,/API key · fixture@example.invalid/);
assert.doesNotMatch(apiQuota,/<progress|89% left/,'An API-key sign-in must not show an old subscription quota');
run(`ACCOUNTS.entries[0].identity=null;ACCOUNTS.entries.push({source:'codex',profile:'/other-profile',snapshots:[quotaSnapshot]});`);
assert.equal(run(`quotaEntries('codex').length`),2,'Distinct profiles must remain separate');
assert.match(run(`accountEntryHTML(quotaEntries('codex')[1])`),/Profile 2/);
assert.match(run(`accountEntryHTML({source:'claude',snapshots:[]})`),/How to show Claude limits/);
run(`RAW=previousQuotaRaw;ACCOUNTS=null;`);
assert.equal(run(`SRC.claude.icon`),run(`SRC['claude-desktop'].icon`));
for(const source of run(`ORDER`)){
  const name=run(`SRC[${JSON.stringify(source)}].icon`);
  assert.ok(fs.existsSync(path.join(root,'static/tool-icons',name+'.svg')),source+' lacks its bundled mark');
  assert.ok(run(`toolIcon(${JSON.stringify(source)})`).includes('role="img"'));
  assert.ok(!run(`toolIcon(${JSON.stringify(source)})`).includes('https://'),'Logo fetch would leak a local dashboard visit');
}
assert.ok(run(`toolDot('codex')`).includes('/static/tool-icons/openai.svg'));
assert.ok(run(`srcBadge('copilot')`).includes('/static/tool-icons/copilot.svg'));
assert.ok(run(`toolLabel('codex')`).includes('aria-hidden="true"'));
assert.ok(run(`legendHTML('test',[{source:'codex',label:'Codex',color:'#fff'}])`).includes('data-k="Codex"'));
assert.ok(!run(`toolIcon('<img src=x onerror=alert(1)>')`).includes('<img src=x'));
assert.ok(!run(`renameButton({device:'Mac',source:'codex',id:'fixture',title:'<img src=x onerror=alert(1)>'})`).includes('<img src=x'));
assert.notEqual(run(`sessionIdentity({device:'Mac',source:'codex',id:'same'})`),
                run(`sessionIdentity({device:'PC',source:'codex',id:'same'})`));
assert.notEqual(run(`sessionIdentity({device:'Mac',source:'claude',id:'same',rename_id:'same-uuid-one'})`),
                run(`sessionIdentity({device:'Mac',source:'claude',id:'same',rename_id:'same-uuid-two'})`));
run(`let haikuPreviousRaw=RAW;
 RAW={pricing:{'Claude Haiku 5.5':[.1,.5,.125,.2,.01]},
      prices:{'Claude Haiku 5.5':[.1,.5,.125,.2,.01]},
      pricing_long_context:{'Claude Haiku 5.5':[.5,2.5,.625,1,.05]}};
 let haikuRow={model:'Claude Haiku 5.5',date:'2026-10-12',in:2,out:3,cc5:4,cc1:5,cr:100001,
     asst:2,lc_in:1,lc_out:2,lc_cc5:3,lc_cc1:4,lc_cr:100000,lc_asst:1};`);
const near=(value,expected)=>assert.ok(Math.abs(value-expected)<1e-10,`${value} != ${expected}`);
near(run(`Object.values(tokenCostParts(haikuRow)).reduce((a,b)=>a+b,0)/1e6`),.00501231);
near(run(`cacheSavings(haikuRow)`),.04500009);
near(run(`cachedRequestRate(haikuRow)`),.03);
near(run(`cacheWriteRate([haikuRow])`),6.2/9/1e6);
near(run(`rateOf('Claude Haiku 5.5')[0]`),.5);
run(`RAW.pricing['Legacy']=[1,5,0,0,.1];`);
near(run(`tokenCostParts({model:'Legacy',in:1,out:2,cc:3,cr:4}).cw`),3);
run(`RAW.prices['Legacy']=[1,5,0,0,.1]; RAW.pricing_history={'Legacy':[['2026-01-01',[2,10,0,0,.2]]]};`);
near(run(`tokenCostParts({model:'Legacy',date:'2025-12-31',cc:3}).cw`),6);
near(run(`cacheWriteRate([{model:'Legacy',date:'2025-12-31',cc:3}])`),1e-6);
near(run(`cachedRequestRate({model:'Legacy',date:'2025-12-31',asst:1})`),.1);
run(`RAW=haikuPreviousRaw;`);
run(`RAW={device:{id:'Mac'},devices:[{id:'Mac',name:'Mac'},{id:'PC',name:'PC'}],
 records:[],sessions:[],hourly:[],tools:[],pricing:{'Claude Opus 5.5 (US)':[4.4,22,5.5,8.8,.22]},
 prices:{'Claude Opus 5.5 (fast)':[8,40,10,16,.4]}};
 S.tools=new Set(['copilot','codex','claude']);`);
const monthWindows=JSON.parse(run(`JSON.stringify(activityMonthWindows(3,new Date(2026,9,11)).map(x=>({from:x.from,to:x.to,label:x.label,current:x.current})))`));
assert.deepEqual(monthWindows,[
  {from:'2026-08-01',to:'2026-08-31',label:'Aug',current:false},
  {from:'2026-09-01',to:'2026-09-30',label:'Sep',current:false},
  {from:'2026-10-01',to:'2026-10-11',label:'Oct',current:true},
]);
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
// Sidebar selection uses the existing device filter rather than a separate scope.
run(`S.devs.clear();`);
assert.equal(run(`sidebarDeviceState().value`),'all');
assert.equal(run(`selectSidebarDevice('device:PC')`),true);
assert.equal(run(`deviceDistribution(deviceData).length`),1);
assert.equal(run(`deviceDistribution(deviceData)[0].value`),4);
assert.equal(run(`sidebarDeviceState().name`),'PC');
assert.equal(run(`selectSidebarDevice('device:missing')`),false);
assert.equal(run(`sidebarDeviceState().value`),'device:PC');
assert.equal(run(`selectSidebarDevice('all')`),true);
assert.equal(run(`deviceDistribution(deviceData).length`),2);
run(`RAW.devices.push({id:'Third',name:'Third'});S.devs=new Set(['Mac','PC']);`);
assert.equal(run(`sidebarDeviceState().value`),'selected');
assert.ok(run(`sidebarDeviceState().options`).includes('value="selected" disabled'));
run(`RAW.devices=[{id:'Mac',name:'Mac'}];S.devs.clear();`);
assert.equal(run(`sidebarDeviceState().multiple`),false);
run(`RAW.devices=[{id:'Mac',name:'Mac'},{id:'PC',name:'PC'}];`);
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
run(`S.tools=new Set(['claude']);S.devs.clear();S.projs.clear();S.ides.clear();S.models.clear();S.provs.clear();S.exactOnly=false;
 var compactSession={source:'claude',model:'M',title:'Fixture',device:'Mac',
   days:{'2026-10-09':[1,100,10,0,0,1,1,0,0,0,1]},model_days:{M:{'2026-10-09':[1,100,10,0,0,1,1,0,0,0,1]}},
   compactions:[{id:'one',date:'2026-10-10',timestamp:'2026-10-10T13:09:08+06:00',trigger:'manual',before:558980,after:16124,duration_ms:93029},
   {id:'two',date:'2026-10-11',timestamp:'2026-10-11T13:09:08+06:00',trigger:'auto',before:100,after:200,duration_ms:null}]};
 RAW.sessions=[compactSession];RAW.records=[];RAW.hourly=[];`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),1);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0].compaction_count`),1);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0].in`),0);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0].user`),0);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0].cost`),0);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0].span`),3);
assert.equal(run(`sessionWhen(slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0])`),'2026-10-10T13:09:08+06:00');
assert.equal(run(`sessionWhen({in:100,end:'2026-10-09',compactions:compactSession.compactions})`),'2026-10-09');
assert.equal(run(`slice({from:'2026-10-12',to:'2026-10-12'}).sessions.length`),0);
run(`S.models=new Set(['M']);`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),0,'Unknown event model must not satisfy a billed model filter');
assert.equal(run(`slice({from:'2026-10-09',to:'2026-10-09'}).sessions[0].in`),100);
run(`S.models.clear();S.devs=new Set(['PC']);`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),0);
run(`S.devs.clear();S.tools=new Set(['codex']);`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),0);
run(`S.tools=new Set(['claude']);`);
assert.equal(run(`clipSession({source:'claude',days:{}},{from:'2026-10-10',to:'2026-10-10'}).compaction_count`),null);
assert.match(run(`compactionDetails({source:'claude'})`),/Not recorded in this ledger entry/);
assert.match(run(`compactionDetails({source:'claude',compactions:[]})`),/Compactions · 0/);
const compactHTML=run(`compactionDetails(clipSession(compactSession,{from:'2026-10-10',to:'2026-10-11'}))`);
assert.match(compactHTML,/1 manual · 1 auto/);
assert.match(compactHTML,/100 → 200/,'A context increase must remain visible');
assert.match(compactHTML,/Before: 558,980; after: 16,124/);
assert.match(compactHTML,/93s/);
assert.match(compactHTML,/tokens and cost are not recorded/);
assert.match(run(`compactionDetails({source:'claude',compactions:[{timestamp:'<script>',trigger:'unexpected',before:null,after:null,duration_ms:null}]})`),/&lt;script&gt;/);
assert.match(run(`compactionDetails({source:'codex'})`),/Not recorded in this ledger entry/);
const timedCodexHTML=run(`compactionDetails({source:'codex',compactions:[{timestamp:'2026-10-12T11:42:38+06:00',trigger:'unknown',before:null,after:null,duration_ms:163391}]})`);
assert.match(timedCodexHTML,/163s/);
assert.match(timedCodexHTML,/do not report a manual\/auto trigger or context sizes/);
assert.match(timedCodexHTML,/uniquely matched completion/);
assert.doesNotMatch(timedCodexHTML,/does not record type, context sizes or duration/);
assert.match(timedCodexHTML,/without a recorded trigger/);
assert.match(timedCodexHTML,/>Trigger</);
assert.match(timedCodexHTML,/>Not recorded</);
assert.doesNotMatch(timedCodexHTML,/Unknown|\bunknown\b|— → —/);
assert.match(run(`compactionDetails({source:'codex',compactions:[{timestamp:'2026-10-12T11:42:38+06:00',trigger:'unknown',before:null,after:null,duration_ms:null}]})`),/missing or cannot be matched uniquely\."\>Unavailable<\/td>/);
assert.match(run(`compactionDetails({source:'codex',compactions:[{timestamp:'2026-10-12T11:42:38+06:00',trigger:'unknown',before:null,after:null,duration_ms:0}]})`),/>0s<\/td>/);
assert.equal(run(`compactionDetails({source:'copilot'})`),'');
run(`S.tools=new Set(['codex']);var codexCompactSession={...compactSession,source:'codex',compactions:[
  {id:'response:fixture',date:'2026-10-10',timestamp:'2026-10-10T13:09:08+06:00',trigger:'unknown',before:null,after:null,duration_ms:null}]};
 RAW.sessions=[codexCompactSession];`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0].compaction_count`),1);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0].cost`),0);
const codexHTML=run(`compactionDetails(slice({from:'2026-10-10',to:'2026-10-10'}).sessions[0])`);
assert.match(codexHTML,/1 without a recorded trigger/);
assert.match(codexHTML,/Not recorded/);
assert.match(codexHTML,/already included in session totals/);
assert.doesNotMatch(codexHTML,/tokens and cost are not recorded/);
assert.doesNotMatch(codexHTML,/— → —/);
run(`S.models=new Set(['M']);`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),0);
assert.equal(run(`slice({from:'2026-10-09',to:'2026-10-09'}).sessions[0].in`),100);
run(`S.models.clear();S.tools=new Set(['claude']);`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),0);
run(`S.tools=new Set(['codex']);S.devs=new Set(['PC']);`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),0);
run(`S.devs.clear();S.projs=new Set(['Missing']);`);
assert.equal(run(`slice({from:'2026-10-10',to:'2026-10-10'}).sessions.length`),0);
run(`S.projs.clear();S.tools=new Set(['claude']);`);
console.log('Frontend regression checks passed: pricing, precision, scope, model contributions, Optimize, device distribution, shared refresh and compaction metadata.');
(async()=>{
  const accountNode={innerHTML:''}, accountCard={hidden:true}; let accountCalls=[], resolveAccountOld;
  const fixtureDay=run('dkey(new Date())');
  const accountFixture=vm.createContext({Date,Number,Set,console,
    dkey:date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`,
    RAW:{device:{id:'local'},sessions:[{source:'codex',days:{[fixtureDay]:[1]}}]},S:{devs:new Set()},
    esc:value=>String(value??'').replaceAll('<','&lt;'),toolLabel:value=>value,
    document:{getElementById:id=>id==='accountLimitsCard'?accountCard:accountNode},askConfirm:async()=>false,
    fetch:async(url,options)=>{accountCalls.push({url,options});return {ok:true,json:async()=>({entries:[]})}}});
  vm.runInContext(views.slice(views.indexOf('let ACCOUNTS='),views.indexOf('function viewOverview(')),accountFixture);
  await vm.runInContext('loadAccounts()',accountFixture);
  await vm.runInContext('refreshAccount("codex")',accountFixture);
  assert.equal(accountCalls.length,1,'Declined consent ran a live lookup');
  assert.equal(accountCalls[0].options,undefined,'Local read unexpectedly posted a live lookup');
  accountFixture.askConfirm=async()=>true;
  accountFixture.fetch=async(url,options)=>{
    accountCalls.push({url,options});
    if(!options) return await new Promise(resolve=>{resolveAccountOld=resolve});
    return {ok:true,json:async()=>({entries:[{source:'codex',profile:'new-account',snapshots:[],identity:{auth:'apiKey'}}]})};
  };
  const oldAccountLoad=vm.runInContext('loadAccounts()',accountFixture);
  await vm.runInContext('refreshAccount("codex")',accountFixture);
  resolveAccountOld({ok:true,json:async()=>({entries:[{source:'codex',profile:'old-account',snapshots:[]}]})});
  await oldAccountLoad;
  assert.equal(vm.runInContext('ACCOUNTS.entries[0].profile',accountFixture),'new-account','Late GET undid a newer account lookup');
  assert.equal(JSON.parse(accountCalls.at(-1).options.body).confirmed,true);
  assert.equal(accountCalls.at(-1).options.headers['Content-Type'],'application/json');
  accountFixture.fetch=async()=>({ok:false});
  await vm.runInContext('loadAccounts()',accountFixture);
  assert.equal(vm.runInContext('ACCOUNTS.entries[0].profile',accountFixture),'new-account','Failed read dropped last successful account');
  assert.match(accountNode.innerHTML,/stale/i);
  // A failed opt-in lookup may have discarded a switched account on the server.
  // Re-read only local observations instead of indefinitely displaying its identity.
  accountCalls=[];
  accountFixture.fetch=async(url,options)=>{
    accountCalls.push({url,options});
    return options?{ok:false,json:async()=>({error:'Account changed during lookup'})}
      :{ok:true,json:async()=>({entries:[{source:'codex',profile:'current-profile',identity:null,snapshots:[]}]})};
  };
  await vm.runInContext('refreshAccount("codex")',accountFixture);
  assert.equal(accountCalls.length,2,'Failed lookup did not reconcile local account state');
  assert.equal(accountCalls[1].options,undefined,'Failure triggered another provider lookup');
  assert.doesNotMatch(accountNode.innerHTML,/new-account/,'Switched account remained visible after failure');
  assert.match(accountNode.innerHTML,/Account changed during lookup/);
  accountFixture.fetch=async(url,options)=>options
    ?{ok:false,json:async()=>({error:'Provider unavailable'})}
    :{ok:true,json:async()=>({entries:[{source:'codex',profile:'same-account',identity:{auth:'chatgpt',email:'verified@example.invalid'},snapshots:[]}]})};
  await vm.runInContext('refreshAccount("codex")',accountFixture);
  assert.match(accountNode.innerHTML,/verified@example.invalid/,'Verified same-account failure discarded local state');
  assert.match(accountNode.innerHTML,/stale/i);
  accountFixture.fetch=async()=>{throw new Error('Dashboard unreachable')};
  await vm.runInContext('refreshAccount("codex")',accountFixture);
  assert.doesNotMatch(accountNode.innerHTML,/verified@example.invalid/,'Unverified identity remained visible after failed reconciliation');
  assert.equal(vm.runInContext('accountBusy',accountFixture),false,'Failure left refresh disabled');
  assert.match(accountNode.innerHTML,/data-account-refresh="codex"/,'Failed reconciliation removed the retry control');
  vm.runInContext('S.devs.add("remote");renderAccountLimits()',accountFixture);
  assert.equal(accountCard.hidden,true);assert.equal(accountNode.innerHTML,'');
  vm.runInContext('S.devs.clear();RAW.sessions=[];renderAccountLimits()',accountFixture);
  assert.equal(accountCard.hidden,true,'No sessions today must hide the entire card');
  vm.runInContext(`RAW.sessions=[{source:'claude',days:{[dkey(new Date())]:[1]}}];renderAccountLimits()`,accountFixture);
  assert.equal(accountCard.hidden,false);
  assert.match(accountNode.innerHTML,/data-account-refresh="claude"/);
  assert.doesNotMatch(accountNode.innerHTML,/data-account-refresh="codex"/,'Unused tools must not have refresh controls');
  console.log('Account quota consent, privacy, stale values and superseded requests passed.');
  // A completed check must leave a way to check again without reloading.
  const versionNode={innerHTML:''}, updateRequests=[];
  let updateResult={behind:0,changes:[]};
  const updating=vm.createContext({RAW:{version:{git:true,commit:'fixture'}},esc:value=>String(value),
    document:{getElementById:()=>versionNode,addEventListener(){}},
    fetch:async(url,options)=>{updateRequests.push({url,options});return {ok:true,json:async()=>updateResult}}});
  vm.runInContext(views.slice(views.indexOf('let UPD ='),views.indexOf('/* ---------------- STORAGE')),updating);
  await vm.runInContext("updateAction('check')",updating);
  assert.match(versionNode.innerHTML,/Up to date/);
  assert.match(versionNode.innerHTML,/data-upd="check">Check for updates/);
  await vm.runInContext("updateAction('check')",updating);
  assert.equal(updateRequests.length,2,'Second update check did not issue a new request');
  for(const request of updateRequests){
    assert.equal(request.url,'/api/update');
    assert.equal(request.options.headers['Content-Type'],'application/json');
    assert.equal(JSON.parse(request.options.body).action,'check');
  }
  updateResult={behind:1,changes:['fixture update']};
  await vm.runInContext("updateAction('check')",updating);
  assert.match(versionNode.innerHTML,/data-upd="apply"/);
  assert.doesNotMatch(versionNode.innerHTML,/Up to date/);
  vm.runInContext("UPD={busy:'Checking…'};renderVersion()",updating);
  assert.doesNotMatch(versionNode.innerHTML,/data-upd=/,'Busy check allows duplicate clicks');
  vm.runInContext("UPD={error:'Offline'};renderVersion()",updating);
  assert.match(versionNode.innerHTML,/data-upd="check">Retry/);
  vm.runInContext("RAW.version.git=false;renderVersion()",updating);
  assert.doesNotMatch(versionNode.innerHTML,/data-upd=/,'Packaged build offers unsupported git update');
  console.log('Update checks remain repeatable after an up-to-date result.');
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
    devices:()=>[],renderVersion(){},loadAccounts(){},fmtNum:value=>value,renderAll(){rendered++}});
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
