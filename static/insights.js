/* Period contributions: current minus previous for the same filtered records, split by one
   grouping at a time (each grouping is an alternative decomposition of the total, not a cause).
   The dashboard no longer shows a "What changed?" card; this is the reference the MCP
   compare_usage engine (telemetry_insights.py) is kept in parity with
   (tests/test_mcp_insights_parity.js). */

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
