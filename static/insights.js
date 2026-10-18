/* Period contributions use the same filtered records as the hero. Each grouping
   is an alternative decomposition of the total, not an additional cause. */
const CHANGE_DIMENSIONS = {project:"Projects",model:"Models",source:"Tools",kind:"Token type"};
const changeDimensions = {overview:"project",cost:"project"};

function changeParts(row, metric){
  if(metric!=="cost") return {in:row.in||0,out:row.out||0,cr:row.cr||0,cc:row.cc||0};
  const total=row.cost||0;
  if(!total) return {};
  const p=tokenCostParts(row), values={in:p.in/1e6,out:p.out/1e6,cr:p.cr/1e6,cc:p.cw/1e6};
  const sum=Object.values(values).reduce((a,b)=>a+b,0);
  // A logged discount or missing price cannot be assigned to token types.
  if(!sum || sum>total+0.000002) return {other:total};
  const remainder=total-sum;
  if(remainder<0){
    const largest=Object.keys(values).sort((a,b)=>values[b]-values[a])[0];
    values[largest]+=remainder;
  }else if(remainder) values.other=remainder;
  return values;
}

function usageChange(current, previous, metric, dimension){
  const groups=new Map(), value=metricOf(metric);
  const add=(rows,field)=>{
    for(const row of rows){
      const entries=dimension==="kind" ? Object.entries(changeParts(row,metric))
        : [[String(row[dimension]||"(unknown)"),value(row)]];
      for(const [key,n] of entries){
        if(!Number.isFinite(n)) continue;
        if(!groups.has(key)) groups.set(key,{key,current:0,previous:0});
        groups.get(key)[field]+=n;
      }
    }
  };
  add(current,"current"); add(previous,"previous");
  const rows=[...groups.values()].map(r=>({...r,delta:r.current-r.previous}))
    .filter(r=>r.current || r.previous)
    .sort((a,b)=>Math.abs(b.delta)-Math.abs(a.delta)||a.key.localeCompare(b.key));
  const cur=current.reduce((n,r)=>n+value(r),0), prev=previous.reduce((n,r)=>n+value(r),0);
  return {current:cur,previous:prev,delta:cur-prev,rows};
}

function changeValue(n,metric,signed=false){
  const abs=Math.abs(n), fmt=metric==="cost"?fmtUSD2:fmtOf(metric);
  const text=metric==="cost" && abs>0 && abs<0.005 ? "<$0.01" : fmt(abs);
  return (signed && n!==0 ? (n>0?"+":"−") : "")+text;
}

function changeGroupLabel(key,dimension){
  if(dimension==="source") return toolLabel(key);
  if(dimension==="kind") return esc(({in:"Input",out:"Output",cr:"Cache reads",cc:"Cache writes",other:"Unsplit / other cost"})[key]||key);
  return esc(key);
}

function changeTable(rows,metric,dimension,total,{full=false}={}){
  const max=rows.reduce((n,r)=>Math.max(n,Math.abs(r.delta)),1e-12);
  const visible=full?rows:rows.slice(0,5);
  const rest=full?[]:rows.slice(5);
  const displayed=rest.length ? [...visible,{key:`Other ${CHANGE_DIMENSIONS[dimension].toLowerCase()} (${rest.length})`,
    current:rest.reduce((n,r)=>n+r.current,0),previous:rest.reduce((n,r)=>n+r.previous,0),
    delta:rest.reduce((n,r)=>n+r.delta,0),other:true}] : visible;
  return `<div class="tbl-wrap change-table"><table aria-label="${esc(CHANGE_DIMENSIONS[dimension])} contributing to the change">
    <thead><tr><th>${esc(CHANGE_DIMENSIONS[dimension])}</th><th class="r">Previous</th><th class="r">Current</th><th class="r">Change</th></tr></thead>
    <tbody>${displayed.map(r=>`<tr><td class="name">${!r.other && dimension!=="kind"?
      `<button class="change-link" data-change-key="${esc(r.key)}" data-change-dimension="${dimension}" data-change-period="current">${changeGroupLabel(r.key,dimension)}</button>`:r.other?esc(r.key):changeGroupLabel(r.key,dimension)}</td>
      <td class="r">${!r.other && dimension!=="kind"?`<button class="change-link num" data-change-key="${esc(r.key)}" data-change-dimension="${dimension}" data-change-period="previous" aria-label="Previous-period sessions for ${esc(r.key)}">${changeValue(r.previous,metric)}</button>`:changeValue(r.previous,metric)}</td>
      <td class="r">${changeValue(r.current,metric)}</td><td class="r num"><span class="change-bar" aria-hidden="true" style="width:${Math.min(100,Math.abs(r.delta)/max*100)}%"></span><span>${changeValue(r.delta,metric,true)}</span></td></tr>`).join("")}</tbody>
    <tfoot><tr><th>Total</th><td class="r">${changeValue(total.previous,metric)}</td><td class="r">${changeValue(total.current,metric)}</td><td class="r num">${changeValue(total.delta,metric,true)}</td></tr></tfoot>
    </table></div>`;
}

function renderUsageChange(view,d){
  const host=document.getElementById(view+"Changes"); if(!host) return;
  host.hidden=!hasPrev();
  if(host.hidden){host.innerHTML="";return;}
  const metric=view==="cost"?"cost":S.metric, previous=filteredRecords(prevRange());
  if(!d.recs.length && !previous.length){host.hidden=true;host.innerHTML="";return;}
  let dimension=changeDimensions[view];
  if(dimension==="kind" && !["cost","tokens"].includes(metric)) dimension="project";
  const result=usageChange(d.recs,previous,metric,dimension);
  const coverageFloor=dataFloor(), pr=prevRange();
  const warnings=[];
  if(!previous.length) warnings.push("No recorded usage in the previous period.");
  else if(pr.from<coverageFloor) warnings.push("The previous period starts before the available history.");
  if(d.r.to===dkey(new Date())) warnings.push("The current period is still in progress.");
  const direction=result.delta===0?"No net change":`${changeValue(result.delta,metric,true)} ${metricNoun(metric)}`;
  const dimensions=Object.entries(CHANGE_DIMENSIONS).filter(([k])=>k!=="kind"||["cost","tokens"].includes(metric));
  host.innerHTML=`<div class="card-h change-head"><div><h3>What changed?</h3><p class="card-sub">${esc(direction)} · vs ${esc(prevLabel())}</p></div>
    <div class="seg sm change-seg" role="group" aria-label="Break down change by">${dimensions.map(([k,label])=>`<button type="button" data-change-group="${k}" data-change-view="${view}" class="${dimension===k?"on":""}" aria-pressed="${dimension===k}">${label}</button>`).join("")}</div></div>
    <p class="change-periods">${esc(pr.from)} – ${esc(pr.to)} → ${esc(d.r.from)} – ${esc(d.r.to)}</p>
    ${warnings.length?`<p class="change-note">${warnings.map(esc).join(" ")}</p>`:""}
    ${changeTable(result.rows,metric,dimension,result)}
    ${result.rows.length>5?`<details class="change-all"><summary>All ${result.rows.length} contributions</summary>${changeTable(result.rows,metric,dimension,result,{full:true})}</details>`:""}
    <details class="change-method"><summary>How this is calculated</summary><p>Each change is current minus previous for the same filters. Each breakdown adds to the total; the breakdowns overlap and must be read separately. These contributions describe where usage changed, rather than proving why.</p>
    ${dimension==="kind"&&metric==="cost"?"<p>Token costs use dated list prices. Costs that cannot be assigned reliably stay unsplit; these are estimates, not subscription charges.</p>":""}</details>`;
}

function drillUsageChange(dimension,key,period){
  if(!["project","model","source"].includes(dimension)) return;
  if(period==="previous"){
    const r=prevRange(); S.preset="custom"; S.from=r.from; S.to=r.to;
  }
  if(dimension==="project") S.projs=new Set([key]);
  else if(dimension==="model") S.models=new Set([key]);
  else S.tools=new Set([key]);
  // Search scopes the session list only; clear it so it cannot conceal evidence.
  S.search=""; syncSearchBoxes(); setView("sessions");
}

document.addEventListener("click",e=>{
  const group=e.target.closest("[data-change-group]");
  if(group){changeDimensions[group.dataset.changeView]=group.dataset.changeGroup;renderAll();return;}
  const row=e.target.closest("[data-change-key]");
  if(row){drillUsageChange(row.dataset.changeDimension,row.dataset.changeKey,row.dataset.changePeriod);}
});
