/* ==========================================================================
   views.js — controls, the eight views, and the boot sequence.
   ========================================================================== */

const VIEW_TITLES = {overview:"Overview", cost:"Cost", models:"Models", tools:"Tools & agents",
  projects:"Projects", sessions:"Sessions", optimize:"Optimize", storage:"Storage"};

/* ---------------- measure (one switch for every view) ---------------- */
function metricOf(kind){
  kind = kind || S.metric;
  return kind==="cost" ? (r=>r.cost||0)
       : kind==="messages" ? (r=>r.asst||0)
       : kind==="time" ? (r=>r.active||0)
       : (r=>recTokens(r));
}
function fmtOf(kind){
  kind = kind || S.metric;
  return kind==="cost" ? fmtUSD : kind==="messages" ? fmtNum : kind==="time" ? fmtDur : fmtTok;
}
function tickOf(kind){
  kind = kind || S.metric;
  return kind==="cost" ? fmtUSDk : kind==="time" ? fmtDur : kind==="messages" ? fmtNum : fmtTok;
}
const metricNoun = k => ({cost:"est. spend", tokens:"tokens", time:"active time", messages:"messages"})[k||S.metric];
const metricTitle = k => ({cost:"Est. spend", tokens:"Tokens", time:"Active time", messages:"Messages"})[k||S.metric];
/* "All time" has no period before it, so a delta would compare against nothing. */
const hasPrev = () => S.preset !== "all";
const prevLabel = () => { const n = rangeDays(); return n === 1 ? "previous day" : `previous ${n} days`; };

/* ---------------- the query sentence ----------------
   "Tokens from all tools over the last 30 days ‹ ›" — each bold phrase opens its
   menu, and the arrows step the period back or forward by its own length. */
const MEASURES = [
  ["tokens","Tokens","everything the models read and wrote, cache included"],
  ["cost","Cost","estimated at API list prices — not what a subscription charges"],
  ["messages","Messages","replies the assistants sent"],
  ["time","Time","active time, counting only gaps under 5 minutes"],
];
const PERIOD_GROUPS = [
  ["Recent", ["today","yesterday","7d","14d","30d","90d"]],
  ["Calendar", ["wtd","mtd","lastmonth","qtd","ytd"]],
  ["Longer", ["180d","365d","all"]],
];
/* the period as it reads mid-sentence, and the word that introduces it */
function periodPhrase(){
  const p = S.preset;
  if(p==="custom"){ const r = range(), same = r.from.slice(0,4)===r.to.slice(0,4);
    return ["from", r.from===r.to ? fmtDay(r.from,true) : fmtDay(r.from,!same)+" – "+fmtDay(r.to,true)]; }
  if(p==="today"||p==="yesterday") return ["", p];
  if(p==="all") return ["across", "all time"];
  if(p==="lastmonth") return ["in", "last month"];
  if(["wtd","mtd","qtd","ytd"].includes(p)) return ["", presetLabel(p).toLowerCase()];
  return ["over", "the " + presetLabel(p).toLowerCase()];
}
function buildRangePanel(){
  const p = document.getElementById("rangePanel");
  const r = range();
  p.innerHTML = `<div class="q-periods">` + PERIOD_GROUPS.map(([g, keys]) =>
      `<div class="q-pgroup"><div class="dd-head">${g}</div>` + keys.map(k =>
        `<button class="q-chip${S.preset===k?" sel":""}" data-preset="${k}">${esc(presetLabel(k))}</button>`).join("") + `</div>`).join("")
    + `</div>
     <div class="dd-pin">
       <div class="dd-head">Custom range</div>
       <div class="dd-item" style="gap:6px;cursor:default">
         <input type="date" class="field" id="dFrom" value="${r.from}" style="flex:1;min-width:0">
         <span class="dim">→</span>
         <input type="date" class="field" id="dTo" value="${r.to}" style="flex:1;min-width:0">
       </div>
       <button class="btn primary" data-apply="1" style="width:100%;justify-content:center;margin-top:6px">Apply custom range</button>
     </div>`;
}
function buildMetricPanel(){
  document.getElementById("metricPanel").innerHTML = `<div class="dd-head">Measure everything in</div>` +
    MEASURES.map(([k,l,d],i) => `<button class="q-opt${S.metric===k?" sel":""}" data-m="${k}">
      <span class="q-opt-l">${l}<kbd>${"TCMA"[i]}</kbd></span><span class="q-opt-d">${esc(d)}</span></button>`).join("");
}
function fmtDay(k, withYear){
  const d = new Date(k+"T00:00:00");
  return MONTHS[d.getMonth()]+" "+d.getDate()+(withYear?", "+d.getFullYear():"");
}
function rangeText(){
  const r = range(), sameYear = r.from.slice(0,4) === r.to.slice(0,4);
  const span = r.from === r.to ? fmtDay(r.from, true)
    : fmtDay(r.from, !sameYear) + " – " + fmtDay(r.to, true);
  return (S.preset==="custom" ? "Custom range" : presetLabel(S.preset)) + " · " + span;
}
function syncRangeUI(){
  const [over, phrase] = periodPhrase(), r = range();
  document.getElementById("qOver").textContent = over;
  document.getElementById("qOver").hidden = !over;
  document.getElementById("rangeLabel").textContent = phrase;
  // the dates themselves, unless the phrase already is the dates
  const span = r.from===r.to ? fmtDay(r.from,true) : fmtDay(r.from, r.from.slice(0,4)!==r.to.slice(0,4)) + " – " + fmtDay(r.to,true);
  document.getElementById("rangeText").textContent = S.preset==="custom" ? "" : span;
  document.getElementById("stepFwd").disabled = r.to >= dkey(new Date());
  document.getElementById("stepBack").disabled = S.preset==="all";
  if(!document.getElementById("ddRange").classList.contains("open")) buildRangePanel();
}
function syncMetricUI(){
  document.getElementById("qMetric").textContent = metricTitle().replace("Est. spend","Cost");
  buildMetricPanel();
}
/* Move the whole window back or forward by its own length: last 30 days → the 30
   before them. The result is a custom range; forward stops at today. */
function stepPeriod(dir){
  if(S.preset==="all") return;
  const r = range(), len = Math.round((new Date(r.to+"T00:00:00")-new Date(r.from+"T00:00:00"))/86400000)+1;
  const shift = k => { const d = new Date(k+"T00:00:00"); d.setDate(d.getDate()+dir*len); return dkey(d); };
  const today = dkey(new Date());
  let from = shift(r.from), to = shift(r.to);
  if(dir > 0 && r.to >= today) return;
  if(to > today){ to = today; }
  S.preset = "custom"; S.from = from; S.to = to; renderAll();
}

/* ---------------- filters ---------------- */
/* An empty set means "no filter" for provider/project/model/IDE; the tool picker is
   different — it always holds an explicit selection of at least one tool. */
function multiPanel(panelId, items, set, isTools){
  const p = document.getElementById(panelId);
  const on = k => isTools ? set.has(k) : (set.size===0 || set.has(k));
  p.innerHTML = items.map(it=>`<div class="dd-item" data-k="${esc(it.key)}">
      <input type="checkbox" ${on(it.key)?"checked":""} tabindex="-1" aria-label="${esc(it.label)}">
      ${it.color?`<span class="sw" style="background:${it.color}"></span>`:""}
      <span class="t">${esc(it.label)}</span>
      <span class="v">${it.value||""}</span><span class="only" data-only="1">only</span>
    </div>`).join("") || `<div class="dd-item dim">nothing in this range</div>`;
}
function buildFilterPanels(){
  const r = range();
  const tokBySrc = {}, tokByProv = {}, tokByProj = {}, tokByModel = {}, tokByIde = {}, tokByDev = {}, seen = new Set();
  for(const x of RAW.records){
    seen.add(x.source);
    if(x.date<r.from||x.date>r.to) continue;
    const t = recTokens(x);
    tokBySrc[x.source]=(tokBySrc[x.source]||0)+t;
    if(x.model!=="(user)"){
      tokByProv[providerOf(x.model)]=(tokByProv[providerOf(x.model)]||0)+t;
      tokByModel[x.model]=(tokByModel[x.model]||0)+t;
    }
    tokByProj[x.project||"(unknown)"]=(tokByProj[x.project||"(unknown)"]||0)+t;
    tokByIde[x.ide||"(unknown)"]=(tokByIde[x.ide||"(unknown)"]||0)+t;
    const dv = x.device || RAW.device.id;
    tokByDev[dv]=(tokByDev[dv]||0)+t;
  }
  multiPanel("toolsPanel",
    ORDER.filter(s=>seen.has(s)).map(s=>({key:s,label:SRC[s].label,color:srcColor(s),value:fmtTok(tokBySrc[s]||0)})),
    S.tools, true);
  multiPanel("provPanel",
    PROVIDERS.filter(p=>tokByProv[p]).map(p=>({key:p,label:p,color:provColor(p),value:fmtTok(tokByProv[p])})), S.provs);
  multiPanel("modelPanel",
    Object.entries(tokByModel).sort((a,b)=>b[1]-a[1]).slice(0,60)
      .map(([k,v])=>({key:k,label:k,color:modelColor(k),value:fmtTok(v)})), S.models);
  multiPanel("projPanel",
    Object.entries(tokByProj).sort((a,b)=>b[1]-a[1]).slice(0,80)
      .map(([k,v])=>({key:k,label:k,value:fmtTok(v)})), S.projs);
  multiPanel("idePanel",
    Object.entries(tokByIde).sort((a,b)=>b[1]-a[1]).map(([k,v])=>({key:k,label:k,value:fmtTok(v)})), S.ides);
  // devices appear only once another one is connected; this one is listed first
  const multi = multiDevice();
  document.getElementById("devSec").hidden = !multi;
  if(multi) multiPanel("devPanel",
    devices().map(v=>({key:v.id, label:v.name+(v.local?" (this device)":""), value:fmtTok(tokByDev[v.id]||0)})), S.devs);
  const n = (S.tools.size!==ORDER.length?1:0) + (S.provs.size?1:0) + (S.models.size?1:0)
          + (S.projs.size?1:0) + (S.ides.size?1:0) + (S.exactOnly?1:0);
  // the tools phrase: "all tools", "Claude Code", "Claude Code & Codex", "3 tools";
  // any other filter shows as a count beside it
  const picked = ORDER.filter(x => S.tools.has(x) && seen.has(x)), all = ORDER.filter(x => seen.has(x));
  document.getElementById("qTools").textContent = picked.length===all.length ? "all tools"
    : !picked.length ? "no tools" : picked.length<=2 ? picked.map(x=>SRC[x].label).join(" & ")
    : `${picked.length} tools`;
  // "... from all tools on all devices over ..." — only said when there is a choice
  const devPicked = devices().filter(v=>!S.devs.size || S.devs.has(v.id));
  document.getElementById("qDev").textContent = !multi ? ""
    : devPicked.length===devices().length ? " on all devices"
    : devPicked.length===1 ? " on " + devPicked[0].name : ` on ${devPicked.length} devices`;
  const other = n - (S.tools.size!==ORDER.length?1:0);
  document.getElementById("filterCount").textContent = other ? `+${other} filter${other>1?"s":""}` : "";
  document.getElementById("filtersBtn").classList.toggle("on", n>0 || S.devs.size>0);
  document.getElementById("reliableBtn").checked = S.exactOnly;
}
function renderPills(){
  const out = [];
  if(S.tools.size!==ORDER.length) out.push(["tools", [...S.tools].map(s=>SRC[s].label).join(", ")]);
  S.provs.forEach(p=>out.push(["prov:"+p, p]));
  S.models.forEach(m=>out.push(["model:"+m, m]));
  S.projs.forEach(p=>out.push(["proj:"+p, p]));
  S.ides.forEach(i=>out.push(["ide:"+i, i]));
  S.devs.forEach(v=>out.push(["dev:"+v, deviceName(v)]));
  if(S.exactOnly) out.push(["exact","Exact tokens only"]);
  if(S.search) out.push(["search",`“${S.search}”`]);
  document.getElementById("pills").innerHTML = out.map(([k,l])=>
      `<span class="pill">${esc(l)}<button data-pill="${esc(k)}" title="Remove" aria-label="Remove ${esc(l)}">×</button></span>`).join("")
    + (out.length>1 ? `<span class="pill reset"><button data-pill="__all" style="color:inherit;font-size:11.5px;padding:0">Clear all</button></span>` : "");
}
function resetFilters(){
  S.tools = new Set(ORDER); S.provs.clear(); S.projs.clear(); S.models.clear(); S.ides.clear(); S.devs.clear();
  S.search = ""; S.exactOnly = false;
  document.querySelectorAll(".search-in").forEach(x=>x.value="");
}

/* ---------------- shared bits ---------------- */
function srcBadge(s){
  return `<span class="badge"><span class="dot" style="background:${srcColor(s)}"></span>${esc((SRC[s]||{label:s}).label)}</span>`;
}
function domSource(srcMap){
  let best=null,bv=-1; for(const k in srcMap) if(srcMap[k]>bv){bv=srcMap[k];best=k;}
  return best;
}
const bySrcOrder = m => ORDER.filter(s => m[s]);
const topSources = (m, n) => Object.entries(m).filter(([,v])=>v>0).sort((a,b)=>b[1]-a[1]).slice(0,n||3).map(([s])=>s);
const shortDay = d => d.slice(5).replace("-","/");
function sortRows(rows, st){
  return rows.slice().sort((a,b)=>{
    let av=a[st.key], bv=b[st.key];
    if(typeof av==="string"||typeof bv==="string")
      return st.dir*((""+(av==null?"":av)).localeCompare(""+(bv==null?"":bv)));
    return st.dir*((av||0)-(bv||0));
  });
}
const TEXTCOL=new Set(["model","source","project","name","prov","cat","server","tool",
  "path","last","when","label","title","branch","entry","toolsKey","skill"]);
function thead(cols, st, tag){
  return "<thead><tr>"+cols.map(([k,l,title])=>
    `<th data-k="${k}" data-t="${tag}" class="${TEXTCOL.has(k)?"":"r"}"${
      title?` title="${esc(title)}"`:""}>${l}${
      st.key===k?(st.dir<0?" ↓":" ↑"):""}</th>`).join("")+"</tr></thead>";
}
/* Today's rate — or, given a record's date, the rate in force that day, so a vendor
   price cut never re-prices history (mirrors parser.price_of + PRICE_HISTORY). */
function priceOf(m, date){
  if(date) for(const [until,p] of (RAW.pricing_history && RAW.pricing_history[m]) || [])
    if(date <= until) return p;
  return (RAW.pricing && RAW.pricing[m]) || (RAW.prices && RAW.prices[m]) || [0,0,0,0,0];
}
/* Per-day series for the metric, plus the same for the previous period (for tiles). */
function dailySeries(recs, days, f){
  const m = {}; for(const r of recs) m[r.date] = (m[r.date]||0) + f(r);
  return days.map(x=>m[x]||0);
}
function sparkFor(vals){ return vals.length > 1 ? sparkline(vals, cssv("--text-3"), 72, 22) : ""; }

/* ---------------- OVERVIEW ---------------- */
function deviceDistribution(d){
  const val=metricOf(), by={};
  for(const r of d.recs){
    const id=r.device || (RAW.device && RAW.device.id);
    by[id]=(by[id]||0)+val(r);
  }
  return devices().filter(x=>passDev(x.id)).map(x=>({id:x.id,name:x.name,value:by[x.id]||0}))
    .sort((a,b)=>b.value-a.value);
}
function renderDeviceDistribution(d){
  const card=document.getElementById("deviceDistributionCard");
  card.hidden=!multiDevice();
  if(card.hidden){ if(charts.deviceChart){charts.deviceChart.destroy();delete charts.deviceChart;} return; }
  const rows=deviceDistribution(d), total=rows.reduce((n,x)=>n+x.value,0), fmt=fmtOf();
  document.getElementById("deviceDistributionSub").textContent="share of " + metricNoun() + " in the selected range and filters";
  document.getElementById("deviceChart").parentElement.style.height=Math.max(150,rows.length*38+35)+"px";
  mk("deviceChart",{type:"bar", $fmt:fmt, $xLabel:"Device",
    data:{labels:rows.map(x=>x.name),datasets:[{label:metricTitle(),data:rows.map(x=>x.value),
      backgroundColor:cssv("--bar"),borderRadius:3,maxBarThickness:22}]},
    options:{indexAxis:"y",scales:axes({x:{ticks:{callback:v=>tickOf()(v)}},y:{grid:{display:false}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+fmt(c.parsed.x)+" · "+fmtPct(total?c.parsed.x/total:0)}}}}});
}
function viewOverview(d){
  const pd = hasPrev() ? slice(prevRange()) : null;
  const days = dateList(d.r), val = metricOf(), fmt = fmtOf();
  const t = totals(d.recs), pt = pd ? totals(pd.recs) : null;
  const cur = d.recs.reduce((a,r)=>a+val(r),0);
  const prev = pd ? pd.recs.reduce((a,r)=>a+val(r),0) : null;
  const bySrc = {}; for(const r of d.recs) bySrc[r.source] = (bySrc[r.source]||0) + val(r);
  const top = topSources(bySrc, 1)[0];
  const activeDays = t.days.size;

  // hero — the one number this view leads with, in the measure the user picked
  const big = S.metric==="cost" ? fmtUSD2(cur) : fmtOf()(cur);
  document.getElementById("ovHero").innerHTML = heroHTML({
    label: metricTitle() + (S.metric==="cost" ? " at API list prices" : ""),
    value: big,
    delta: pd ? deltaHTML(cur, prev, S.metric==="cost") : "",
    note: pd ? `vs the ${prevLabel()}` : "",
    facts: [
      {l:"Daily average", v: fmt(activeDays ? cur/activeDays : 0), title:"per active day"},
      {l:"Active days", v: `${fmtNum(activeDays)} <span class="dim" style="font-size:12px;font-weight:500">of ${fmtNum(days.length)}</span>`},
      {l:"Previous period", v: pd ? fmt(prev) : "—"},
      {l:"Top tool", v: top ? `${esc(SRC[top].label)} <span class="dim" style="font-size:12px;font-weight:500">${fmtPct(bySrc[top]/(cur||1))}</span>` : "—"},
    ],
  });

  // per-day, stacked by tool
  const srcs = ORDER.filter(s=>S.tools.has(s) && d.recs.some(r=>r.source===s));
  document.getElementById("dailyTitle").textContent = metricTitle() + " per day, by tool";
  const ds = srcs.filter(s=>!isMuted("dailyChart",SRC[s].label)).map(s=>
    stackDS(SRC[s].label, dailySeries(d.recs.filter(r=>r.source===s), days, val).map(v=>+v.toFixed(4)), srcColor(s)));
  document.getElementById("dailyLegend").innerHTML =
    legendHTML("dailyChart", srcs.map(s=>({label:SRC[s].label,color:srcColor(s)})));
  mk("dailyChart",{type:"bar", $fmt:fmt, $stacked:true,
    data:{labels:days.map(shortDay),datasets:ds},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({x:{stacked:true},y:{stacked:true,ticks:{callback:v=>tickOf()(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmt(c.parsed.y),
        footer:it=>"Total "+fmt(it.reduce((a,x)=>a+x.parsed.y,0))}}}}});

  // tiles — everything the hero isn't, each against the previous period
  const ser = f => sparkFor(dailySeries(d.recs, days, f));
  const tile = (l, curV, prevV, f, fmtV, invert, s) => ({l, v: fmtV(curV),
    d: pd ? deltaHTML(curV, prevV, invert) : "", s, spark: ser(f)});
  const tiles = [];
  if(S.metric!=="cost") tiles.push(tile("Est. spend", t.cost, pt&&pt.cost, r=>r.cost||0, fmtUSD, true));
  if(S.metric!=="tokens") tiles.push(tile("Tokens", t.tok, pt&&pt.tok, recTokens, fmtTok));
  if(S.metric!=="time"){ const a = d.recs.reduce((x,r)=>x+(r.active||0),0), pa = pd ? pd.recs.reduce((x,r)=>x+(r.active||0),0) : null;
    tiles.push(tile("Active time", a, pa, r=>r.active||0, fmtDur, false)); }
  tiles.push(tile("Your prompts", t.user, pt&&pt.user, r=>r.user||0, fmtNum));
  if(S.metric!=="messages") tiles.push(tile("Assistant replies", t.msgs, pt&&pt.msgs, r=>r.asst||0, fmtNum));
  tiles.push({l:"Sessions", v:fmtNum(d.sessions.length), d: pd ? deltaHTML(d.sessions.length, pd.sessions.length) : ""});
  const ctx = t.in+t.cr+t.cc, pctx = pt ? pt.in+pt.cr+pt.cc : 0;
  tiles.push({l:"Cache hit rate", v: ctx ? fmtPct(t.cr/ctx) : "—",
    d: pd && pctx && ctx ? deltaHTML(t.cr/ctx, pt.cr/pctx) : "", s: ctx ? "" : "no cached context"});
  document.getElementById("kpis").innerHTML = tilesHTML(tiles.slice(0,6));
  renderDeviceDistribution(d);

  // tool mix
  document.getElementById("toolShareSub").textContent = "share of " + metricNoun();
  const sBy = {}; for(const x of d.sessions) sBy[x.source]=(sBy[x.source]||0)+1;
  const uBy = {}; for(const r of d.recs) uBy[r.source]=(uBy[r.source]||0)+(r.user||0);
  barList("toolShare", bySrcOrder(bySrc).map(s=>({label:SRC[s].label, value:bySrc[s], color:srcColor(s),
      sub:`${fmtNum(sBy[s]||0)} session${sBy[s]===1?"":"s"} · ${fmtNum(uBy[s]||0)} prompts`}))
    .sort((a,b)=>b.value-a.value), {fmt});

  // model mix
  document.getElementById("modelMixSub").textContent = `share of ${metricNoun()}, and the tools that ran each`;
  const bm = {};
  for(const r of d.recs){ if(r.model==="(user)") continue;
    const e = bm[r.model] || (bm[r.model] = {model:r.model, v:0, cost:0, tok:0, src:{}});
    e.v += val(r); e.cost += r.cost||0; e.tok += recTokens(r); e.src[r.source] = (e.src[r.source]||0) + recTokens(r)+ (r.cost||0); }
  const models = Object.values(bm).filter(e=>e.v>0).sort((a,b)=>b.v-a.v);
  const mTot = models.reduce((a,e)=>a+e.v,0) || 1;
  document.getElementById("topModels").innerHTML = models.length
    ? `<thead><tr><th class="nosort">Model</th><th class="nosort">Tools</th><th class="r nosort">Est. $</th><th class="r nosort">Tokens</th><th class="r nosort">Share</th></tr></thead><tbody>` +
      models.slice(0,7).map(e=>`<tr><td class="name"><span class="sw" style="background:${modelColor(e.model)};margin-right:8px"></span>${esc(e.model)}${
          priceOf(e.model)[0]===0 && e.tok && !e.cost ? '<span class="warn-ic" title="No price row for this model — its cost reads as $0">⚠</span>' : ""}</td>
        <td>${topSources(e.src,3).map(toolDot).join(" ")}</td>
        <td class="r">${fmtUSD(e.cost)}</td><td class="r">${fmtTok(e.tok)}</td><td class="r dim">${fmtPct(e.v/mTot)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty">Nothing in range.</td></tr></tbody>`;

  // project mix
  document.getElementById("projectMixSub").textContent = `ranked by ${metricNoun()} · click one for its sessions`;
  const bp = {};
  for(const r of d.recs){ const p = r.project || "(unknown)";
    const e = bp[p] || (bp[p] = {project:p, v:0, cost:0, tok:0, src:{}});
    e.v += val(r); e.cost += r.cost||0; e.tok += recTokens(r); e.src[r.source] = (e.src[r.source]||0) + recTokens(r); }
  const sess = {}; for(const s of d.sessions) sess[s.project||"(unknown)"] = (sess[s.project||"(unknown)"]||0) + 1;
  const projs = Object.values(bp).filter(e=>e.v>0).sort((a,b)=>b.v-a.v);
  document.getElementById("topProjects").innerHTML = projs.length
    ? `<thead><tr><th class="nosort">Project</th><th class="nosort">Tools</th><th class="r nosort">Est. $</th><th class="r nosort">Tokens</th><th class="r nosort">Sessions</th></tr></thead><tbody>` +
      projs.slice(0,7).map(e=>`<tr class="clickable" data-proj="${esc(e.project)}" title="Show this project's sessions"><td class="name">${esc(e.project)}</td>
        <td>${topSources(e.src,3).map(toolDot).join(" ")}</td>
        <td class="r">${fmtUSD(e.cost)}</td><td class="r">${fmtTok(e.tok)}</td><td class="r">${fmtNum(sess[e.project]||0)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty">Nothing in range.</td></tr></tbody>`;

  renderHighlights(d);
  renderHeat(d);
  renderTokenMix(d);

  const byDay={}; let max=0;
  for(const r of RAW.records){
    if(!passSrc(r.source)||!passModel(r.model)||!passProj(r.project)||!passIde(r.ide)||!passDev(r.device)) continue;
    byDay[r.date]=(byDay[r.date]||0)+recTokens(r); max=Math.max(max,byDay[r.date]);
  }
  renderCalendar(byDay, max, day=>{ S.preset="custom"; S.from=day; S.to=day; renderAll(); });
}

function renderHighlights(d){
  const byDay={}, byProj={}, byHour={};
  for(const r of d.recs){ byDay[r.date]=(byDay[r.date]||0)+recTokens(r);
    byProj[r.project]=(byProj[r.project]||0)+(r.cost||0); }
  for(const h of d.hourly) byHour[h.hour]=(byHour[h.hour]||0)+h.tokens;
  const top = o => Object.entries(o).sort((a,b)=>b[1]-a[1])[0];
  const bigDay=top(byDay), topProj=top(byProj), busiest=top(byHour);
  const pricey = d.sessions.slice().sort((a,b)=>(b.cost||0)-(a.cost||0))[0];
  const active = new Set(Object.keys(byDay).filter(k=>byDay[k]>0));
  let best=0, run=0, cur=0;
  const days=dateList(d.r);
  for(const day of days){ if(active.has(day)){run++;best=Math.max(best,run);} else run=0; }
  for(let i=days.length-1;i>=0;i--){ if(active.has(days[i]))cur++; else break; }
  const items=[
    bigDay&&{k:"Biggest day",v:fmtTok(bigDay[1])+" tokens",s:fmtDay(bigDay[0],true)},
    pricey&&pricey.cost>0&&{k:"Priciest session",v:fmtUSD(pricey.cost),s:(pricey.title||pricey.project||pricey.id)},
    topProj&&topProj[1]>0&&{k:"Top project by spend",v:fmtUSD(topProj[1]),s:topProj[0]},
    busiest&&{k:"Busiest hour",v:pad2(busiest[0])+":00",s:fmtTok(busiest[1])+" tokens"},
    {k:"Longest streak",v:best+(best===1?" day":" days"),s:cur?`current streak ${cur}`:"not active on the last day"},
  ].filter(Boolean);
  document.getElementById("highlights").innerHTML = items.map(i=>
    `<div class="fact"><span class="k">${i.k}</span><span class="x"><b class="num">${esc(i.v)}</b><div title="${esc(i.s)}">${esc(i.s)}</div></span></div>`).join("")
    || `<div class="empty">Nothing in range.</div>`;
}
function renderHeat(d){
  const cells = Array.from({length:7},()=>Array(24).fill(0));
  let max=0;
  for(const h of d.hourly){
    const dow=(new Date(h.date+"T00:00:00").getDay()+6)%7;
    cells[dow][h.hour]+=h.tokens; max=Math.max(max,cells[dow][h.hour]);
  }
  renderHeatmap(cells,max);
  document.getElementById("hmHint").textContent =
    "local hour × weekday, by tokens" + (dimFiltered()?" · tool, date and device filters only":"");
}
const TOKEN_KINDS = () => [
  {key:"in", label:"Input", color:cssv("--k-in")}, {key:"cr", label:"Cache read", color:cssv("--k-cr")},
  {key:"cc", label:"Cache write", color:cssv("--k-cw")}, {key:"out", label:"Output", color:cssv("--k-out")}];
function renderTokenMix(d){
  const agg={};
  for(const r of d.recs){ const a=agg[r.source]||(agg[r.source]={in:0,cr:0,cc:0,out:0});
    a.in+=r.in||0; a.cr+=r.cr||0; a.cc+=r.cc||0; a.out+=r.out||0; }
  stackMeters("tokenMix", ORDER.filter(s=>agg[s] && (agg[s].in+agg[s].cr+agg[s].cc+agg[s].out)>0)
    .map(s=>({label:SRC[s].label, color:srcColor(s), parts:agg[s]})), TOKEN_KINDS(), fmtTok);
}

/* ---------------- COST ---------------- */
function viewCost(d){
  const pd = hasPrev() ? slice(prevRange()) : null;
  const t = totals(d.recs), pt = pd ? totals(pd.recs) : null;
  const days = dateList(d.r), active = Math.max(1, t.days.size);
  let saved=0;
  for(const r of d.recs){ const p=priceOf(r.model, r.date); saved += (r.cr||0)*(p[0]-p[4])/1e6; }
  document.getElementById("costHero").innerHTML = heroHTML({
    label:"Est. spend at API list prices",
    value: fmtUSD2(t.cost),
    delta: pd ? deltaHTML(t.cost, pt.cost, true) : "",
    note: pd ? `vs ${fmtUSD(pt.cost)} the ${prevLabel()}` : "",
    facts:[
      {l:"Per active day", v:fmtUSD(t.cost/active)},
      {l:"30-day run rate", v:fmtUSD(t.cost/active*30), title:"at the current pace per active day"},
      {l:"Per session", v:fmtUSD(d.sessions.length?t.cost/d.sessions.length:0)},
      {l:"Per prompt", v:fmtUSD(t.user?t.cost/t.user:0)},
    ]});

  // cumulative, by tool, with the previous period as a dashed reference
  const srcs = ORDER.filter(s=>S.tools.has(s) && d.recs.some(r=>r.source===s));
  const ds = srcs.filter(s=>!isMuted("cumChart",SRC[s].label)).map(s=>{
    let run=0;
    return areaDS(SRC[s].label, dailySeries(d.recs.filter(r=>r.source===s), days, r=>r.cost||0)
      .map(v=>+(run+=v).toFixed(2)), srcColor(s));
  });
  if(pd){
    let run=0; const prevData = dailySeries(pd.recs, dateList(prevRange()), r=>r.cost||0)
      .map(v=>+(run+=v).toFixed(2)).slice(0, days.length);
    ds.push({label:"Previous period (all tools)", data:prevData, borderColor:cssv("--text-3"),
      borderDash:[5,4], borderWidth:1.5, pointRadius:soloPoint(prevData), fill:false, tension:.25, stack:"prev"});
  }
  document.getElementById("cumLegend").innerHTML =
    legendHTML("cumChart", srcs.map(s=>({label:SRC[s].label,color:srcColor(s)})));
  mk("cumChart",{type:"line", $fmt:fmtUSD2, data:{labels:days.map(shortDay),datasets:ds},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({y:{stacked:true,min:0,ticks:{callback:v=>fmtUSDk(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmtUSD2(c.parsed.y)}}}}});

  // tiles
  const ctx = t.in+t.cr+t.cc, pctx = pt ? pt.in+pt.cr+pt.cc : 0;
  const byDay = {}; for(const r of d.recs) byDay[r.date]=(byDay[r.date]||0)+(r.cost||0);
  const topDay = Object.entries(byDay).sort((a,b)=>b[1]-a[1])[0];
  const topSess = d.sessions.slice().sort((a,b)=>(b.cost||0)-(a.cost||0))[0];
  const subCost = d.sessions.filter(s=>s.subagent).reduce((a,s)=>a+(s.cost||0),0);
  document.getElementById("costStats").innerHTML = tilesHTML([
    {l:"Cache hit rate", v:ctx?fmtPct(t.cr/ctx):"—", d:pd&&pctx&&ctx?deltaHTML(t.cr/ctx, pt.cr/pctx):"", s:`${fmtTok(t.cr)} read from cache`},
    {l:"Saved by caching", v:fmtUSD(saved), s:"vs re-sending that context"},
    {l:"Effective rate", v:t.tok?"$"+(t.cost/t.tok*1e6).toFixed(2):"—", s:"per 1M tokens, blended"},
    {l:"Output share", v:t.tok?fmtPct(t.out/t.tok):"—", s:`${fmtTok(t.out)} generated`},
    {l:"Priciest day", v:topDay?fmtUSD(topDay[1]):"—", s:topDay?fmtDay(topDay[0],true):""},
    {l:subCost?"Spent in subagents":"Priciest session", v:subCost?fmtUSD(subCost):(topSess?fmtUSD(topSess.cost):"—"),
     s:subCost?fmtPct(subCost/(t.cost||1))+" of spend":(topSess?(topSess.title||topSess.project||""):"")},
  ]);

  // spend by model / by project
  const bm={}, bp={}, bpSrc={};
  for(const r of d.recs){ if(!r.cost) continue;
    if(r.model!=="(user)") bm[r.model]=(bm[r.model]||0)+r.cost;
    const p=r.project||"(unknown)"; bp[p]=(bp[p]||0)+r.cost;
    (bpSrc[p]=bpSrc[p]||{})[r.source]=(bpSrc[p][r.source]||0)+r.cost; }
  barList("costByModel", Object.entries(bm).sort((a,b)=>b[1]-a[1])
    .map(([m,v])=>({label:m, value:v, color:modelColor(m)})), {fmt:fmtUSD, total:t.cost, limit:10, more:"see Models"});
  barList("costByProj", Object.entries(bp).sort((a,b)=>b[1]-a[1])
    .map(([p,v])=>({label:p, value:v, dots:topSources(bpSrc[p],3), attr:`data-proj="${esc(p)}"`})),
    {fmt:fmtUSD, total:t.cost, limit:10, more:"see Projects"});

  // spend by token type — list price × tokens, per record's own date
  const kinds = {in:0, cr:0, cw:0, out:0}; let priced = 0;
  for(const r of d.recs){
    if(!r.cost) continue;
    const p = priceOf(r.model, r.date);
    if(!p[0] && !p[1]) continue;
    const cw5 = (r.cc5||r.cc1) ? (r.cc5||0) : (r.cc||0);
    const parts = {in:(r.in||0)*p[0], out:(r.out||0)*p[1], cr:(r.cr||0)*p[4],
      cw:cw5*(p[2]||p[0])+(r.cc1||0)*(p[3]||p[2]||p[0])};
    const sum = parts.in+parts.out+parts.cr+parts.cw;
    if(!sum) continue;
    const scale = r.cost/(sum/1e6);        // match the record's real cost (logged-cost sources, multipliers)
    for(const k in parts) kinds[k] += parts[k]/1e6*scale;
    priced += r.cost;
  }
  const K = TOKEN_KINDS(), kcol = {in:K[0].color, cr:K[1].color, cw:K[2].color, out:K[3].color};
  barList("costByKind", [["in","Input"],["cr","Cache read"],["cw","Cache write"],["out","Output"]]
    .map(([k,l])=>({label:l, value:kinds[k], color:kcol[k]})).sort((a,b)=>b.value-a.value), {fmt:fmtUSD, total:priced});
  document.getElementById("costByKindSub").textContent = priced < t.cost - 0.01
    ? `${fmtUSD(t.cost-priced)} can't be split — logged cost with no per-token price` : "what each kind of token cost you";

  // cache hit rate over time
  const cd={}; for(const r of d.recs){ const c=cd[r.date]||(cd[r.date]={cr:0,ctx:0}); c.cr+=r.cr||0; c.ctx+=ctxTokens(r); }
  const cacheData = days.map(x=>cd[x]&&cd[x].ctx?+(cd[x].cr/cd[x].ctx*100).toFixed(1):null);
  const ink = cssv("--bar");
  mk("cacheChart",{type:"line", $fmt:v=>v.toFixed(1)+"%",
    data:{labels:days.map(shortDay),datasets:[{label:"Cache hit rate", data:cacheData,
      borderColor:ink, backgroundColor:ink+"1f", borderWidth:2, pointRadius:soloPoint(cacheData),
      pointHoverRadius:4, tension:.3, fill:true, spanGaps:true}]},
    options:{interaction:{mode:"index",intersect:false},scales:axes({y:{min:0,max:100,ticks:{callback:v=>v+"%"}}}),
      plugins:{tooltip:{displayColors:false,callbacks:{label:c=>" cache hit "+c.parsed.y+"%"}}}}});

  // effective rate by model.
  //   "all" = cost / every token, cache reads included — mostly tracks how well-cached
  //     a model was, NOT comparable across providers.
  //   "out" = cost / output tokens — comparable across models and providers.
  const perOut = S.rateMetric==="out";
  const rm={};
  for(const r of d.recs){ if(r.model==="(user)") continue;
    const b=rm[r.model]||(rm[r.model]={tok:0,out:0,cost:0}); b.tok+=recTokens(r); b.out+=r.out||0; b.cost+=r.cost||0; }
  barList("rateList", Object.entries(rm).filter(([,v])=>v.cost>0 && (perOut ? v.out>1000 : v.tok>10000))
    .map(([m,v])=>({label:m, value:v.cost/(perOut?v.out:v.tok)*1e6, color:modelColor(m)}))
    .sort((a,b)=>b.value-a.value), {fmt:v=>"$"+(v>=100?Math.round(v).toLocaleString():v.toFixed(2)), share:false, limit:10});
  document.getElementById("rateHint").textContent = perOut
    ? "total cost ÷ output tokens — comparable across models and providers"
    : "cost per 1M tokens of every kind — a rate, not a total";
  document.querySelectorAll("#rateSeg button").forEach(b=>b.classList.toggle("on", b.dataset.r===S.rateMetric));

  // daily spend stacked
  document.getElementById("dailyCostLegend").innerHTML =
    legendHTML("dailyCost", srcs.map(s=>({label:SRC[s].label,color:srcColor(s)})));
  mk("dailyCost",{type:"bar", $fmt:fmtUSD2, $stacked:true, data:{labels:days.map(shortDay),
      datasets:srcs.filter(s=>!isMuted("dailyCost",SRC[s].label)).map(s=>
        stackDS(SRC[s].label, dailySeries(d.recs.filter(r=>r.source===s), days, r=>r.cost||0).map(v=>+v.toFixed(3)), srcColor(s)))},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({x:{stacked:true},y:{stacked:true,ticks:{callback:v=>fmtUSDk(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmtUSD2(c.parsed.y),
        footer:it=>"Total "+fmtUSD2(it.reduce((a,x)=>a+x.parsed.y,0))}}}}});
}

/* ---------------- MODELS ---------------- */
function viewModels(d){
  renderModelEff(d);
  const val = metricOf(), fmt = fmtOf();
  const recs = d.recs.filter(r=>r.model!=="(user)");
  const prov={}, provModels={}, provTools={}, byModel={};
  for(const r of recs){
    const p=providerOf(r.model), v=val(r), tk=recTokens(r);
    prov[p]=(prov[p]||0)+v;
    (provModels[p]=provModels[p]||{})[r.model]=(provModels[p][r.model]||0)+v;
    (provTools[p]=provTools[p]||{})[r.source]=(provTools[p][r.source]||0)+v;
    const e=byModel[r.model]||(byModel[r.model]={model:r.model,tok:0,cost:0,msgs:0,in:0,out:0,cr:0,cc:0,tools:{}});
    e.tok+=tk; e.cost+=r.cost||0; e.msgs+=r.asst||0; e.in+=r.in||0; e.out+=r.out||0; e.cr+=r.cr||0; e.cc+=r.cc||0;
    const te=e.tools[r.source]||(e.tools[r.source]={source:r.source,tok:0,cost:0,msgs:0,in:0,out:0,cr:0,cc:0});
    te.tok+=tk; te.cost+=r.cost||0; te.msgs+=r.asst||0; te.in+=r.in||0; te.out+=r.out||0; te.cr+=r.cr||0; te.cc+=r.cc||0;
  }
  const provs = PROVIDERS.filter(p=>prov[p]);
  const grand = provs.reduce((a,p)=>a+prov[p],0)||1;

  // provider cards
  document.getElementById("provH2H").innerHTML = provs.map(p=>{
    const models=Object.entries(provModels[p]).sort((a,b)=>b[1]-a[1]);
    const tools=Object.entries(provTools[p]).filter(([,v])=>v>0).sort((a,b)=>b[1]-a[1]);
    return `<div class="prov">
      <div class="prov-h"><span class="sw" style="background:${provColor(p)}"></span>${esc(p)}<span class="pct">${fmtPct(prov[p]/grand)}</span></div>
      <div class="prov-v">${fmt(prov[p])}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(prov[p]/grand*100).toFixed(1)}%;background:${provColor(p)}"></div></div>
      <div class="prov-s">${models.length} model${models.length>1?"s":""} · top ${esc(models[0][0])}</div>
      <div class="prov-s" style="margin-top:3px">ran in ${tools.map(([s])=>toolDot(s)+" "+esc(SRC[s].label)).join(" · ")}</div>
    </div>`;
  }).join("") || `<div class="card"><div class="empty">Nothing in range.</div></div>`;

  // model table — one row per model; several tools → expandable per-tool split
  const tokGrand = Object.values(byModel).reduce((a,x)=>a+x.tok,0) || 1;
  const rows = Object.values(byModel).filter(e=>e.tok||e.cost||e.msgs).map(e=>{
    const toolList = Object.values(e.tools).sort((a,b)=>b.tok-a.tok);
    return {...e, toolList, toolsKey:toolList.map(t=>SRC[t.source].label).join(","),
      share:e.tok/tokGrand, rate:e.tok?e.cost/e.tok*1e6:0, prov:providerOf(e.model)};
  });
  const cols=[["model","Model"],["toolsKey","Tool"],["prov","Provider"],["tok","Tokens"],
    ["out","Output"],["cr","Cache read"],["cost","Est. $"],["msgs","Replies"],
    ["rate","$/Mtok","effective blended rate"],["share","Share","share of tokens"]];
  const subRow = t=>{
    const rate = t.tok?t.cost/t.tok*1e6:0;
    return `<tr class="sub-row"><td colspan="2" class="dim">└ ${srcBadge(t.source)}</td><td></td>
      <td class="r dim">${fmtTok(t.tok)}</td><td class="r dim">${fmtTok(t.out)}</td>
      <td class="r dim">${fmtTok(t.cr)}</td><td class="r dim">${fmtUSD(t.cost)}</td>
      <td class="r dim">${fmtNum(t.msgs)}</td><td class="r dim">$${rate.toFixed(2)}</td><td></td></tr>`;
  };
  document.getElementById("modelTable").innerHTML = thead(cols,S.modelSort,"model")+"<tbody>"+
    sortRows(rows, S.modelSort).map(r=>{
      const multi = r.toolList.length>1, expanded = multi && S.modelExpanded.has(r.model);
      const main = `<tr class="${multi?"clickable":""}" ${multi?`data-model="${esc(r.model)}"`:""}>
        <td class="name">${multi?`<span class="caret">${expanded?"▾":"▸"}</span>`:'<span class="caret"></span>'}<span class="sw" style="background:${modelColor(r.model)};margin-right:8px"></span>${esc(r.model)}${
          priceOf(r.model)[0]===0 && r.tok && !r.cost ? '<span class="warn-ic" title="No price row for this model — its cost reads as $0">⚠</span>':''}</td>
        <td>${r.toolList.map(t=>srcBadge(t.source)).join(" ")}</td><td class="dim">${esc(r.prov)}</td>
        <td class="r">${fmtTok(r.tok)}</td><td class="r">${fmtTok(r.out)}</td>
        <td class="r">${fmtTok(r.cr)}</td><td class="r"><b>${fmtUSD(r.cost)}</b></td>
        <td class="r">${fmtNum(r.msgs)}</td><td class="r">$${r.rate.toFixed(2)}</td>
        <td class="r dim">${fmtPct(r.share)}</td></tr>`;
      return expanded ? main+r.toolList.map(subRow).join("") : main;
    }).join("")+"</tbody>";

  // model timeline
  const days = dateList(d.r);
  const mtot={}; for(const r of recs) mtot[r.model]=(mtot[r.model]||0)+val(r);
  const topM = Object.entries(mtot).filter(([,v])=>v>0).sort((a,b)=>b[1]-a[1]).slice(0,8).map(x=>x[0]);
  const byDM={}; for(const r of recs){ if(!topM.includes(r.model)) continue;
    (byDM[r.model]=byDM[r.model]||{})[r.date]=(byDM[r.model][r.date]||0)+val(r); }
  document.getElementById("modelTLLegend").innerHTML =
    legendHTML("modelTL", topM.map(m=>({label:m,color:modelColor(m)})));
  mk("modelTL",{type:"line", $fmt:fmt, $stacked:true, data:{labels:days.map(shortDay),
      datasets:topM.filter(m=>!isMuted("modelTL",m)).map(m=>areaDS(m, days.map(x=>+((byDM[m]||{})[x]||0).toFixed(4)), modelColor(m)))},
    options:{interaction:{mode:"index",intersect:false},
      // a stacked area must start at zero, or a one-day range auto-scales to a sliver
      scales:axes({y:{stacked:true,min:0,ticks:{callback:v=>tickOf()(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmt(c.parsed.y)}}}}});

  // provider share over time (100%) — muting would make the rest not add up, so a static legend
  const per={}; for(const r of recs){ const p=providerOf(r.model); (per[r.date]=per[r.date]||{})[p]=(per[r.date][p]||0)+val(r); }
  const shareDS = provs.map(p=>{
    const data=days.map(x=>{ const row=per[x]; if(!row) return null;
      const tot=Object.values(row).reduce((a,b)=>a+b,0); return tot?+(100*(row[p]||0)/tot).toFixed(1):null; });
    return {label:p,data,borderColor:provColor(p),backgroundColor:provColor(p)+"3d",borderWidth:1.5,
      pointRadius:soloPoint(data),pointHoverRadius:4,tension:.25,fill:true,stack:"a",spanGaps:false};
  });
  document.getElementById("provShareLegend").innerHTML = legendStatic(provs.map(p=>({label:p,color:provColor(p)})));
  mk("provShare",{type:"line", $fmt:v=>v.toFixed(1)+"%", data:{labels:days.map(shortDay),datasets:shareDS},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({y:{stacked:true,min:0,max:100,ticks:{callback:v=>v+"%"}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+(c.parsed.y||0)+"%"}}}}});

  // provider x tool matrix
  const srcs = ORDER.filter(s=>S.tools.has(s) && recs.some(r=>r.source===s));
  let h = `<thead><tr><th class="nosort">Provider</th>${srcs.map(s=>`<th class="r nosort">${esc(SRC[s].label)}</th>`).join("")}<th class="r nosort">Total</th></tr></thead><tbody>`;
  for(const p of provs){
    h += `<tr><td><span class="sw" style="background:${provColor(p)};margin-right:8px"></span>${esc(p)}</td>` +
      srcs.map(s=>`<td class="r">${(provTools[p]||{})[s]?fmt(provTools[p][s]):'<span class="dim">—</span>'}</td>`).join("") +
      `<td class="r"><b>${fmt(prov[p])}</b></td></tr>`;
  }
  document.getElementById("provMatrix").innerHTML = h+"</tbody>";
}

/* ---------------- TOOLS & AGENTS ---------------- */
const CATS=[
  ["Read / search", /^(read|glob|grep|ls|list_dir|read_file|file_search|semantic_search|codebase_search|grep_search|search|list_code_usages|toolsearch|tool_search|view_image|view)/i],
  ["Edit / write",  /^(edit|write|multiedit|notebookedit|apply_patch|str_replace|create_file|insert_edit|replace_string|create_directory|patch)/i],
  ["Execute",       /^(bash|shell|exec|wait|run_in_terminal|run_command|execute|terminal|python|get_terminal|kill|write_stdin)/i],
  ["Web",           /^(webfetch|websearch|web_search|web_fetch|fetch|fetch_webpage|open_simple_browser)/i],
  ["Agents / tasks",/^(task|agent|workflow|sendmessage|todowrite|update_plan|manage_todo|think|skill|askuserquestion|exitplanmode|enterplanmode|spawn_agent)/i],
];
function categorize(name){
  if(/^mcp__|^mcp_/i.test(name)) return "MCP";
  for(const [lab,re] of CATS) if(re.test(name)) return lab;
  return "Other";
}
/* ---------------- ACTIVITY ----------------
   RAW.activity: one row per (date, tool, model, category) — a turn is one typed
   prompt plus all the work until the next one, classified mostly by what the agent
   did — files edited, commands run (parser.py ACTIVITY). Only Claude Code,
   Claude Desktop and Codex log enough to classify. */
const ACT_LABEL = {build:"Building features", fix:"Fixing bugs", refactor:"Refactoring",
  test:"Testing", docs:"Docs", review:"Reviewing", explore:"Exploring code",
  research:"Web research", data:"Data & MCP tools", plan:"Planning", delegate:"Delegating to agents",
  vcs:"Git & PRs", ops:"Build, install & deploy", chat:"Q&A / chat"};
function actRows(d){
  const r = d.r;
  return (RAW.activity||[]).filter(x => passSrc(x.source) && passModel(x.model)
    && passProj(x.project) && passIde(x.ide) && passDev(x.device) && x.date>=r.from && x.date<=r.to);
}
function actSum(rows, key){
  const out = {};
  for(const x of rows){
    const k = key(x), e = out[k] || (out[k] = {key:k, turns:0, edits:0, oneshot:0, retries:0, cost:0, tok:0, edit_cost:0, retry_cost:0, src:{}});
    e.turns+=x.turns; e.edits+=x.edits; e.oneshot+=x.oneshot; e.retries+=x.retries;
    e.cost+=x.cost; e.tok+=x.tok; e.retry_cost+=x.retry_cost; e.edit_cost+=x.edit_cost||0;
    e.src[x.source] = (e.src[x.source]||0) + (x.cost||x.tok);
  }
  return out;
}
/* the global measure, for activity rows: time isn't split per turn, so it counts prompts */
const actVal = x => S.metric==="cost" ? x.cost : S.metric==="tokens" ? x.tok : x.turns;
const actFmt = () => S.metric==="cost" ? fmtUSD : S.metric==="tokens" ? fmtTok : fmtNum;
function renderActivity(d){
  const rows = actRows(d);
  const cats = Object.values(actSum(rows, x=>x.category));
  const all = actSum(rows, ()=>"all").all;
  const noun = S.metric==="cost" ? "est. cost" : S.metric==="tokens" ? "tokens" : "prompts";
  document.getElementById("actSub").textContent = all
    ? `${fmtNum(all.turns)} prompts · by ${noun} · Claude Code & Codex only`
    : "each prompt and the work it set off";
  barList("actList", cats.map(c=>({label:ACT_LABEL[c.key]||c.key, value:actVal(c),
      sub:`${fmtNum(c.turns)} prompt${c.turns===1?"":"s"}${c.edits?` · ${fmtPct(c.oneshot/c.edits)} one-shot`:""}`,
      dots:[domSource(c.src)]})).sort((a,b)=>b.value-a.value),
    {fmt:actFmt(), empty:"No Claude Code or Codex prompts in range."});

  const bySrc = Object.values(actSum(rows, x=>x.source)).filter(x=>x.edits);
  barList("osList", ORDER.map(k=>bySrc.find(x=>x.key===k)).filter(Boolean).map(x=>({
      label:SRC[x.key].label, value:x.oneshot/x.edits, color:`var(${SRC[x.key].v})`,
      sub:`${fmtNum(x.oneshot)} of ${fmtNum(x.edits)} editing prompts`})),
    {fmt:fmtPct, share:false, empty:"No edits in range."});
  const edits = all ? all.edits : 0;
  document.getElementById("osFacts").innerHTML = edits ? [
    {k:"Retries", v:fmtNum(all.retries), s:`${(all.retries/edits).toFixed(2)} per editing prompt`},
    {k:"Prompts that needed one", v:fmtNum(edits-all.oneshot), s:`${fmtPct((edits-all.oneshot)/edits)} of editing prompts`},
    {k:"What those prompts cost", v:fmtUSD(all.retry_cost), s:all.cost?`${fmtPct(all.retry_cost/all.cost)} of activity spend · the whole prompt, not just the redo`:""},
  ].map(i=>`<div class="fact"><span class="k">${i.k}</span><span class="x"><b class="num">${esc(i.v)}</b><div title="${esc(i.s)}">${esc(i.s)}</div></span></div>`).join("") : "";
  return all;
}
/* per model: how often its edits land, and what a prompt costs on it */
function renderModelEff(d){
  const el = document.getElementById("modelEff");
  if(!el) return;
  const rows = Object.values(actSum(actRows(d), x=>x.model)).filter(x=>x.turns);
  if(!rows.length){ el.innerHTML = `<tbody><tr><td class="empty">No Claude Code or Codex prompts in range.</td></tr></tbody>`; return; }
  for(const r of rows){ r.model=r.key; r.os = r.edits ? r.oneshot/r.edits : -1; r.rpe = r.edits ? r.retries/r.edits : -1;
    r.cpt = r.cost/r.turns; r.tpt = r.tok/r.turns; }
  const sorted = sortRows(rows, S.effSort);
  el.innerHTML = thead([["model","Model"],["turns","Prompts"],["edits","Editing prompts"],["os","One-shot"],
      ["rpe","Retries / edit"],["tpt","Tokens / prompt"],["cpt","Est. $ / prompt"]], S.effSort, "eff") + "<tbody>" +
    sorted.map(r=>`<tr><td class="name">${toolDot(domSource(r.src))} ${esc(r.model)}</td><td class="r">${fmtNum(r.turns)}</td>
      <td class="r">${fmtNum(r.edits)}</td><td class="r"><b>${r.edits?fmtPct(r.os):'<span class="dim">—</span>'}</b></td>
      <td class="r">${r.edits?r.rpe.toFixed(2):'<span class="dim">—</span>'}</td><td class="r">${fmtTok(r.tpt)}</td>
      <td class="r">${fmtUSD2(r.cpt)}</td></tr>`).join("") + "</tbody>";
}

function viewTools(d){
  const tools = toolCounts(d.r), t = totals(d.recs);
  const totalCalls = tools.reduce((a,x)=>a+x.count,0);
  const side = d.sessions.reduce((a,s)=>a+(s.side||0),0);
  const mcp = tools.filter(x=>categorize(x.name)==="MCP");
  const web = tools.filter(x=>categorize(x.name)==="Web").reduce((a,x)=>a+x.count,0);
  // Gap-capped (parser.py ACTIVE_GAP_CAP): a lower bound, not wall-clock length.
  const activeSecs = d.recs.reduce((a,r)=>a+(r.active||0),0);
  const prem = d.recs.reduce((a,r)=>a+(r.prem||0),0);
  const al=dimFiltered() ? [] : (RAW.ai_lines||[]).filter(x=>passSrc("cursor") && passDev(x.device)
      && x.date>=d.r.from&&x.date<=d.r.to);
  const acc=al.reduce((a,x)=>a+x.tab_accepted+x.composer_accepted,0);
  const sug=al.reduce((a,x)=>a+x.tab_suggested+x.composer_suggested,0);
  const act = renderActivity(d);
  document.getElementById("agentStats").innerHTML = tilesHTML([
    {l:"Active time", v:activeSecs?fmtDur(activeSecs):"—", s:"gap-capped estimate", title:"Consecutive turns no more than 5 minutes apart — a lower bound, not wall-clock length"},
    {l:"Tool calls", v:fmtNum(totalCalls), s:`${fmtNum(tools.length)} distinct tools`},
    act && act.edits ? {l:"One-shot edits", v:fmtPct(act.oneshot/act.edits), s:`${fmtNum(act.edits)} editing prompts`,
      title:"Editing prompts whose edits needed no edit → run → re-edit of the same file (Claude Code & Codex)"} : null,
    {l:"Calls per prompt", v:!dimFiltered()&&t.user?(totalCalls/t.user).toFixed(1):"—", s:dimFiltered()?"tool calls lack this filter detail":`${fmtNum(t.user)} prompts`},
    {l:"Tokens per prompt", v:t.user?fmtTok(t.tok/t.user):"—", s:"context amplification"},
    {l:"Replies per session", v:d.sessions.length?(t.msgs/d.sessions.length).toFixed(1):"—", s:`${fmtNum(d.sessions.length)} sessions`},
    {l:"Subagent tokens", v:t.tok?fmtPct(side/t.tok):"—", s:`${fmtTok(side)} in spawned agents`},
    {l:"MCP calls", v:fmtNum(mcp.reduce((a,x)=>a+x.count,0)), s:`${mcp.length} MCP tools`},
    {l:"Web lookups", v:fmtNum(web), s:"search / fetch calls"},
    prem ? {l:"Premium requests", v:fmtNum(Math.round(prem)), s:"Copilot's billing unit"} : null,
    al.length ? {l:"AI lines kept", v:fmtNum(acc), s:sug?`${fmtPct(acc/sug)} of ${fmtNum(sug)} · Cursor`:"Cursor"} : null,
  ]);

  barList("toolList", tools.map(x=>({label:x.name, value:x.count, dots:[domSource(x.src)]})),
    {fmt:fmtNum, total:totalCalls, limit:15, more:"full list below"});
  const cat={}; for(const x of tools) cat[categorize(x.name)]=(cat[categorize(x.name)]||0)+x.count;
  barList("catList", ["Read / search","Edit / write","Execute","Web","Agents / tasks","MCP","Other"]
    .filter(c=>cat[c]).map(c=>({label:c, value:cat[c]})).sort((a,b)=>b.value-a.value), {fmt:fmtNum, total:totalCalls});

  // IDE x tool matrix, in the global measure
  {
    const val = metricOf(), fmt = fmtOf();
    const byIde = {}, ideTot = {}, srcTot = {};
    for(const r of d.recs){
      const i = r.ide || "(unknown)", v = val(r);
      if(!v) continue;
      (byIde[i] = byIde[i] || {})[r.source] = (byIde[i][r.source] || 0) + v;
      ideTot[i] = (ideTot[i] || 0) + v; srcTot[r.source] = (srcTot[r.source] || 0) + v;
    }
    const ides = Object.keys(ideTot).sort((a,b)=>ideTot[b]-ideTot[a]);
    const srcs = ORDER.filter(x => srcTot[x]);
    const grand = ides.reduce((a,i)=>a+ideTot[i],0) || 1;
    document.getElementById("ideMatrix").innerHTML = ides.length ? `<table><thead><tr><th class="nosort">IDE / surface</th>${
      srcs.map(x=>`<th class="r nosort">${esc(SRC[x].label)}</th>`).join("")}<th class="r nosort">Total</th><th class="r nosort">Share</th></tr></thead><tbody>` +
      ides.map(i=>`<tr><td>${esc(i)}</td>` + srcs.map(x=>`<td class="r">${byIde[i][x] ? fmt(byIde[i][x]) : '<span class="dim">—</span>'}</td>`).join("")
        + `<td class="r"><b>${fmt(ideTot[i])}</b></td><td class="r dim">${fmtPct(ideTot[i]/grand)}</td></tr>`).join("") + "</tbody></table>"
      : `<div class="empty">Nothing in range.</div>`;
    document.getElementById("ideHint").textContent = (ides.length > 1
      ? `${ides.length} surfaces · a VS Code extension logs "VS Code" whatever fork hosts it`
      : "which IDE or surface each tool ran in") + " · " + metricNoun();
  }

  const byServer={};
  for(const x of mcp){ const parts=x.name.replace(/^mcp__/,"").split("__");
    const e=byServer[parts[0]||"?"]||(byServer[parts[0]||"?"]={server:parts[0]||"?",count:0,tools:0,src:{}});
    e.count+=x.count; e.tools++; for(const s in x.src) e.src[s]=(e.src[s]||0)+x.src[s]; }
  // What Claude Code OFFERED each session (RAW.mcp_inventory), so a server that was
  // loaded but never called shows up too, and coverage reads used / offered.
  const inv = RAW.mcp_inventory || {servers:{}, loaded:[]};
  const loadedIn = {};
  if(passSrc("claude"))
    for(const x of inv.loaded) if(x.date>=d.r.from && x.date<=d.r.to) loadedIn[x.server]=(loadedIn[x.server]||0)+x.sessions;
  for(const sv in loadedIn) if(!byServer[sv]) byServer[sv]={server:sv,count:0,tools:0,src:{claude:1}};
  const srv=Object.values(byServer).sort((a,b)=>b.count-a.count || (loadedIn[b.server]||0)-(loadedIn[a.server]||0));
  const offered = sv => ((inv.servers[sv]||{}).tools||[]).length;
  document.getElementById("mcpTable").innerHTML = srv.length
    ? `<thead><tr><th class="nosort">Server</th><th class="nosort">Tools</th><th class="r nosort" title="distinct tools called / tools the server offers">Tools used</th><th class="r nosort" title="Claude Code sessions it was loaded in">Sessions</th><th class="r nosort">Calls</th></tr></thead><tbody>`+
      srv.map(r=>`<tr><td class="name">${esc(r.server)}</td><td>${topSources(r.src,3).map(toolDot).join(" ")}</td>
        <td class="r">${r.tools}${offered(r.server)?`<span class="dim"> / ${offered(r.server)}</span>`:""}</td>
        <td class="r">${loadedIn[r.server]?fmtNum(loadedIn[r.server]):'<span class="dim">—</span>'}</td>
        <td class="r"><b>${r.count?fmtNum(r.count):'<span class="dim">0</span>'}</b></td></tr>`).join("")+"</tbody>"
    : `<tbody><tr><td class="empty">No MCP tool calls in range.</td></tr></tbody>`;

  const sk={};
  for(const x of (RAW.skills||[])){ if(x.date<d.r.from||x.date>d.r.to||!passSrc("claude")||!passDev(x.device)) continue;
    const e=sk[x.name]||(sk[x.name]={skill:x.name,asst:0,tok:0,cost:0}); e.asst+=x.asst; e.tok+=x.tok; e.cost+=x.cost; }
  const skills=Object.values(sk).sort((a,b)=>b.cost-a.cost);
  document.getElementById("skillTable").innerHTML = skills.length
    ? `<thead><tr><th class="nosort">Skill</th><th class="r nosort">Requests</th><th class="r nosort">Tokens</th><th class="r nosort">Est. $</th></tr></thead><tbody>`+
      skills.map(r=>`<tr><td class="name">/${esc(r.skill)}</td><td class="r">${fmtNum(r.asst)}</td><td class="r">${fmtTok(r.tok)}</td><td class="r"><b>${fmtUSD(r.cost)}</b></td></tr>`).join("")+"</tbody>"
    : `<tbody><tr><td class="empty">No Skill-driven requests in range.</td></tr></tbody>`;

  const trows = tools.map(x=>({name:x.name,count:x.count,cat:categorize(x.name),source:SRC[domSource(x.src)].label,src:domSource(x.src)}));
  document.getElementById("toolTable").innerHTML = thead([["name","Tool"],["cat","Category"],["source","Mostly from"],["count","Calls"]],S.toolSort,"tool")+"<tbody>"+
    sortRows(trows,S.toolSort).map(r=>`<tr><td class="name">${esc(r.name)}</td><td class="dim">${r.cat}</td>
      <td>${toolDot(r.src)} <span class="dim">${esc(r.source)}</span></td><td class="r">${fmtNum(r.count)}</td></tr>`).join("")+"</tbody>";
}

/* ---------------- PROJECTS ---------------- */
function viewProjects(d){
  const val = metricOf(), fmt = fmtOf();
  const by={};
  for(const r of d.recs){
    const p = r.project || "(unknown)";
    const e=by[p]||(by[p]={project:p,tokens:0,cost:0,messages:0,prompts:0,tools:0,time:0,v:0,src:{}});
    e.tokens+=recTokens(r); e.cost+=r.cost||0; e.messages+=r.asst||0; e.prompts+=r.user||0;
    e.tools+=r.tools||0; e.time+=r.active||0; e.v+=val(r); e.src[r.source]=(e.src[r.source]||0)+recTokens(r);
  }
  const sess={}; for(const s of d.sessions) sess[s.project||"(unknown)"]=(sess[s.project||"(unknown)"]||0)+1;
  const q=S.search.toLowerCase();
  const rows=Object.values(by).filter(e=>!q||(e.project||"").toLowerCase().includes(q)).map(e=>({...e,sessions:sess[e.project]||0}));
  const top=rows.slice().sort((a,b)=>b.v-a.v);
  document.getElementById("projSub").textContent = `by working directory · ${metricNoun()}` + (q?` · matching “${S.search}”`:"");
  barList("projList", top.map(r=>({label:r.project, value:r.v, dots:topSources(r.src,3), attr:`data-proj="${esc(r.project)}"`})),
    {fmt, limit:16, more:"see the table below"});
  const grand=top.reduce((a,r)=>a+r.v,0)||1, top3=top.slice(0,3).reduce((a,r)=>a+r.v,0);
  document.getElementById("projStats").innerHTML = tilesHTML([
    {l:"Projects", v:fmtNum(rows.length), s:"with activity in range"},
    {l:"Top project", v:fmtPct((top[0]||{v:0}).v/grand), s:(top[0]||{}).project||"—"},
    {l:"Top 3 share", v:fmtPct(top3/grand), s:"concentration of effort"},
    {l:"Per project", v:fmt(grand/(rows.length||1)), s:"average"},
  ]);
  const cols=[["project","Project"],["tokens","Tokens"],["cost","Est. $"],["messages","Replies"],
    ["prompts","Prompts"],["tools","Tool calls"],["time","Time"],["sessions","Sessions"]];
  document.getElementById("projTable").innerHTML=thead(cols,S.projSort,"proj")+"<tbody>"+
    sortRows(rows,S.projSort).map(r=>`<tr class="clickable" data-proj="${esc(r.project)}">
      <td class="name">${topSources(r.src,3).map(toolDot).join(" ")} <span style="margin-left:4px">${esc(r.project)}</span></td>
      <td class="r">${fmtTok(r.tokens)}</td><td class="r"><b>${fmtUSD(r.cost)}</b></td>
      <td class="r">${fmtNum(r.messages)}</td><td class="r">${fmtNum(r.prompts)}</td>
      <td class="r">${fmtNum(r.tools)}</td><td class="r">${r.time?fmtDur(r.time):'<span class="dim">—</span>'}</td>
      <td class="r">${fmtNum(r.sessions)}</td></tr>`).join("")+"</tbody>";
}

/* ---------------- SESSIONS ---------------- */
function sessionRows(d){
  const q=S.search.toLowerCase();
  return d.sessions.map(s=>({...s,
    tok:(s.in||0)+(s.out||0)+(s.cr||0)+(s.cc||0),
    when:s.end||s.start||"",
    cache:((s.in||0)+(s.cr||0)+(s.cc||0))?(s.cr||0)/((s.in||0)+(s.cr||0)+(s.cc||0)):0,
    name:s.title||s.project||s.id,
  })).filter(s=>!q || (s.name+" "+(s.project||"")+" "+(s.model||"")+" "+(s.branch||"")).toLowerCase().includes(q));
}
let SESS_CACHE=[];
function viewSessions(d){
  const rows=sessionRows(d);
  const cols=[["when","When"],["source","Tool"],["name","Session"],["project","Project"],
    ["model","Model"],["tok","Tokens"],["cost","Est. $"],["user","Prompts"],["asst","Replies"],
    ["tools","Tools"],
    ["active","Time","estimated active time — consecutive turns no more than 5 minutes apart, so a resumed session's idle days don't count"],
    ["cache","Cache %"]];
  const sorted=sortRows(rows,S.sessSort).slice(0,400);
  document.getElementById("sessTable").innerHTML=thead(cols,S.sessSort,"sess")+"<tbody>"+
    sorted.map((s,i)=>`<tr class="clickable" data-sess="${i}">
      <td class="dim">${s.when?s.when.slice(0,16).replace("T"," "):"—"}</td>
      <td>${srcBadge(s.source)}</td>
      <td class="name" title="${esc(s.title||"")}">${
        s.clipped?`<span class="dim" style="margin-right:5px" title="Figures reflect the selected range and model filters.">◔</span>`:""}${
        s.subagent?`<span class="sub-badge" title="A subagent transcript — work its parent delegated.">sub</span>`:""}${esc(s.name)}</td>
      <td class="dim">${esc(s.project||"—")}</td>
      <td>${esc(s.model)}${s.nmodels>1?` <span class="dim" title="${esc((s.models||[]).join(" · "))}">+${s.nmodels-1}</span>`:""}</td>
      <td class="r">${fmtTok(s.tok)}</td><td class="r"><b>${fmtUSD(s.cost)}</b></td>
      <td class="r">${fmtNum(s.user)}</td><td class="r">${fmtNum(s.asst||s.req)}</td>
      <td class="r">${fmtNum(s.tools)}</td>
      <td class="r">${s.active?fmtDur(s.active):'<span class="dim">—</span>'}</td>
      <td class="r">${s.cache?fmtPct(s.cache):'<span class="dim">—</span>'}</td></tr>`).join("")+"</tbody>";
  const capped = RAW.sessions_total && RAW.sessions_total > RAW.sessions.length;
  const clipped = rows.filter(x=>x.clipped).length;
  document.getElementById("sessHint").textContent =
    `${fmtNum(rows.length)} sessions active in range`
    + (clipped?` · ${clipped} with scoped figures (◔ = range and model filters)`:"")
    + (rows.length>400?" · showing the top 400 by the current sort":"")
    + (capped?` · of the ${fmtNum(RAW.sessions_total)} most recent loaded`:"")
    + " · click a row for detail";
  SESS_CACHE = sorted;
}
function openSession(i){
  const s=SESS_CACHE[i]; if(!s) return;
  const row=(k,v)=>v==null||v===""?"":`<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  openDrawer(`
    <div class="eyebrow">Session</div>
    <h2>${esc(s.title||s.project||s.id)}</h2>
    <div style="margin-bottom:16px;display:flex;gap:8px;align-items:center">${srcBadge(s.source)} <span class="dim" style="font-size:12px">${esc(s.id)}</span></div>
    ${row("Device",multiDevice()?esc(deviceName(s.device||RAW.device.id)):null)}
    ${row("Project",esc(s.project||"—"))}
    ${row("Model",esc((s.models||[s.model]).join(" · ")))}
    ${row("Git branch",s.branch?esc(s.branch):null)}
    ${row("Entrypoint",s.entry?esc(s.entry):null)}
    ${row("Daily detail",s.detail_limited?"Limited in this older ledger entry":null)}
    ${row("Tool version",s.cliver?esc(s.cliver):null)}
    ${s.clipped?`<div class="warnbar">Figures below reflect the selected range and model filters.${s.range_clipped?` This session spans ${s.span} days in total.`:""}</div>`:""}
    ${row("Started",s.start?s.start.slice(0,19).replace("T"," "):"—")}
    ${row("Last activity",s.end?s.end.slice(0,19).replace("T"," "):"—")}
    ${row("Est. cost",fmtUSD2(s.cost))}
    ${row("Tokens",fmtNum(s.tok))}
    ${row("· input",fmtNum(s.in))}
    ${row("· cache read",fmtNum(s.cr))}
    ${row("· cache write",fmtNum(s.cc))}
    ${row("· output",fmtNum(s.out))}
    ${row("Cache hit rate",s.cache?fmtPct(s.cache):"—")}
    ${row("Your prompts",fmtNum(s.user))}
    ${row("Assistant replies",fmtNum(s.asst||s.req))}
    ${row("Model calls",s.asst&&s.req&&s.req!==s.asst?fmtNum(s.req):null)}
    ${row("Tool calls",fmtNum(s.tools))}
    ${row("Active time",s.active?fmtDur(s.active):null)}
    ${row("Mode",s.mode?esc(s.mode):null)}
    ${row("Premium requests",s.prem?fmtNum(Math.round(s.prem)):null)}
    ${row("Lines added",s.lines_add?fmtNum(s.lines_add):null)}
    ${row("Lines removed",s.lines_del?fmtNum(s.lines_del):null)}
    ${row("Thinking time",s.think_ms?Math.round(s.think_ms/1000)+"s":null)}
    ${row("Subagents",s.subagents?fmtNum(s.subagents):null)}
    ${row("Subagent tokens",s.side?fmtNum(s.side):null)}
    ${row("Log size",s.bytes?fmtBytes(s.bytes):null)}
    ${s.archived?'<div class="warnbar">This log has been pruned from disk; its numbers are kept from an earlier scan.</div>':""}
  `);
}

/* ---------------- VERSION + UPDATE ----------------
   The running version comes from the checkout's git metadata. Checking for an
   update is the one action that contacts the network, and only on a click. */
let UPD = null;              // last check result, or {busy|error}
function versionLabel(v){
  if(!v || !v.git) return "Unknown build";
  const m = /^(v[\d.]+)(?:-(\d+)-g[0-9a-f]+)?$/.exec(v.describe||"");
  return m ? `${m[1]}${m[2]?` +${m[2]}`:""} · ${v.commit}` : v.commit;
}
function renderVersion(){
  const el = document.getElementById("version"); if(!el) return;
  const v = RAW.version || {};
  const title = v.git ? `${v.describe||v.commit} · ${v.branch} · committed ${v.date}` : "not a git checkout";
  let action = "";
  if(!v.git) action = "";
  else if(UPD && UPD.busy) action = `<span class="dim">${esc(UPD.busy)}</span>`;
  else if(UPD && UPD.error) action = `<span class="ver-err" title="${esc(UPD.error)}">${esc(UPD.error)}</span> <button class="link" data-upd="check">Retry</button>`;
  else if(UPD && UPD.behind) action = `<button class="btn sm primary" data-upd="apply" title="${esc(UPD.changes.join("\n"))}">Update · ${UPD.behind} new</button>`;
  else if(UPD) action = `<span class="dim">Up to date</span>`;
  else action = `<button class="link" data-upd="check">Check for updates</button>`;
  el.innerHTML = `<span class="ver" title="${esc(title)}">${esc(versionLabel(v))}</span>${action?`<span class="ver-a">${action}</span>`:""}`;
}
async function updateAction(action){
  UPD = {busy: action==="apply" ? "Updating…" : "Checking…"}; renderVersion();
  try{
    const r = await fetch("/api/update",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({action})});
    const out = await r.json();
    if(!r.ok) throw new Error(out.error||"request failed");
    if(out.restarting){
      UPD = {busy:"Restarting…"}; renderVersion();
      const was = (RAW.version||{}).commit;
      for(let i=0;i<60;i++){                  // wait for the new process, then reload
        await new Promise(res=>setTimeout(res,1000));
        try{ const x = await (await fetch("/api/data",{cache:"no-store"})).json();
          if(x.version && x.version.commit!==was && !x.meta.building){ location.reload(); return; } }catch(e){}
      }
      UPD = {error:"Restart is taking long — reload the page"}; renderVersion(); return;
    }
    UPD = out;
    if(out.current && RAW) RAW.version = out.current;   // a new tag on this same commit
  }catch(e){ UPD = {error: String(e.message||e)}; }
  renderVersion();
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-upd]"); if(!b) return;
  e.preventDefault(); updateAction(b.dataset.upd);
});

/* ---------------- STORAGE ---------------- */
function viewStorage(){
  if(!STORAGE){ document.getElementById("stHero").innerHTML='<div class="empty">Measuring…</div>'; return; }
  const st=STORAGE;
  const total=st.sources.reduce((a,s)=>a+s.bytes,0);
  const files=st.sources.reduce((a,s)=>a+s.files,0);
  const extras=st.extras.reduce((a,e)=>a+e.bytes,0);
  const disk=st.disk||{total:0,free:0,used:0};
  const biggest=st.files[0];
  const plan = st.cleanup || {commands:[], days:90, shell:""};
  const rc = (st.reclaimable||{})[String(plan.days)] || {files:0, bytes:0};
  document.getElementById("stHero").innerHTML = `
    <div class="hero-stat" style="min-width:220px"><div class="hero-label">AI logs on this disk</div>
      <div class="hero-value">${fmtBytes(total)}</div>
      <div class="hero-delta"><span>${fmtNum(files)} files across ${st.sources.length} tools · all-time, not date-filtered</span></div></div>
    <div class="hero-stat"><div class="l">Share of drive</div><div class="v">${disk.total?fmtPct(total/disk.total):"—"}</div><div class="s">of ${fmtBytes(disk.total)}</div></div>
    <div class="hero-stat"><div class="l">Free space</div><div class="v">${fmtBytes(disk.free)}</div><div class="s">${disk.total?fmtPct(disk.free/disk.total)+" free":""}</div></div>
    <div class="hero-stat"><div class="l">Reclaimable</div><div class="v">${rc.files?fmtBytes(rc.bytes):"—"}</div><div class="s">${rc.files?`${fmtNum(rc.files)} logs untouched ${plan.days}+ days`:"nothing old enough"}</div></div>`;

  const otherUsed=Math.max(0,(disk.used||0)-total-extras);
  document.getElementById("diskMeter").innerHTML=`
    <div class="meter" style="height:12px;margin-top:18px" role="img" aria-label="drive usage">
      <i style="width:${disk.total?total/disk.total*100:0}%;background:var(--text)" title="AI logs ${fmtBytes(total)}"></i>
      <i style="width:${disk.total?extras/disk.total*100:0}%;background:var(--warn)" title="related AI data ${fmtBytes(extras)}"></i>
      <i style="width:${disk.total?otherUsed/disk.total*100:0}%;background:var(--border-2)" title="everything else ${fmtBytes(otherUsed)}"></i>
    </div>
    <div class="legend" style="margin-top:10px">
      <span class="li static"><span class="sw" style="background:var(--text)"></span>AI logs ${fmtBytes(total)}</span>
      <span class="li static"><span class="sw" style="background:var(--warn)"></span>related AI data ${fmtBytes(extras)}</span>
      <span class="li static"><span class="sw" style="background:var(--border-2)"></span>everything else ${fmtBytes(otherUsed)}</span>
      <span class="li static dim">free ${fmtBytes(disk.free)}</span>
    </div>
    ${disk.total && disk.free/disk.total < 0.1 ? `<div class="warnbar bad"><span>⚠</span><div><b>Only ${fmtBytes(disk.free)} free (${fmtPct(disk.free/disk.total)}).</b>
      These logs alone are ${(total/Math.max(1,disk.free)).toFixed(1)}× your remaining headroom — the cleanup
      commands below reclaim the oldest of them without losing any analytics.</div></div>`:""}`;

  document.getElementById("stStats").innerHTML = tilesHTML([
    {l:"Largest single log", v:biggest?fmtBytes(biggest.bytes):"—", s:biggest?(SRC[biggest.source]||{label:biggest.source}).label+" · "+biggest.project:""},
    {l:"Related, not analysed", v:fmtBytes(extras), s:"see the panel below"},
    {l:"Dashboard cache", v:fmtBytes(st.cache_bytes), s:".usage_cache.json"},
    {l:"Live log files", v:fmtNum(st.files_total||st.files.length), s:"across every tool"},
  ]);

  barList("stByTool", st.sources.filter(s=>s.bytes).sort((a,b)=>b.bytes-a.bytes).map(s=>({
    label:(SRC[s.source]||{label:s.source}).label, value:s.bytes, color:srcColor(s.source)})), {fmt:fmtBytes});
  const tokBySrc={};
  for(const r of RAW.records) tokBySrc[r.source]=(tokBySrc[r.source]||0)+recTokens(r);
  barList("stEff", st.sources.filter(s=>s.bytes&&tokBySrc[s.source]>1e6).map(s=>({
    label:(SRC[s.source]||{label:s.source}).label, value:s.bytes/(tokBySrc[s.source]/1e6), color:srcColor(s.source)}))
    .sort((a,b)=>b.value-a.value), {fmt:fmtBytes, share:false, empty:"Needs at least 1M tokens per tool."});

  const days=[...new Set(st.growth.map(g=>g.date))].filter(x=>x&&x!=="unknown").sort();
  const srcs=[...new Set(st.growth.map(g=>g.source))].sort((a,b)=>ORDER.indexOf(a)-ORDER.indexOf(b));
  const ds=srcs.filter(s=>!isMuted("stGrowth",(SRC[s]||{label:s}).label)).map(s=>{
    const m={}; for(const g of st.growth) if(g.source===s) m[g.date]=(m[g.date]||0)+g.bytes;
    let run=0; return areaDS((SRC[s]||{label:s}).label, days.map(x=>(run+=(m[x]||0))), srcColor(s));
  });
  document.getElementById("stGrowthLegend").innerHTML=
    legendHTML("stGrowth", srcs.map(s=>({label:(SRC[s]||{label:s}).label,color:srcColor(s)})));
  mk("stGrowth",{type:"line", $fmt:fmtBytes, $stacked:true, data:{labels:days.map(shortDay),datasets:ds},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({y:{stacked:true,min:0,ticks:{callback:v=>fmtBytes(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmtBytes(c.parsed.y)}}}}});

  const cols=[["bytes","Size"],["source","Tool"],["project","Project"],["last","Last written"],["path","Path"]];
  const rows=sortRows(st.files,S.fileSort).slice(0,120);
  document.getElementById("stFiles").innerHTML=thead(cols,S.fileSort,"file")+"<tbody>"+
    rows.map(f=>`<tr><td class="r"><b>${fmtBytes(f.bytes)}</b></td>
      <td>${srcBadge(f.source)}</td><td class="dim">${esc(f.project)}</td>
      <td class="dim">${(f.last||"").slice(0,10)||"—"}</td>
      <td class="name dim" title="${esc(f.path)}">…/${esc(shortPath(f.path))}</td></tr>`).join("")+"</tbody>";
  const nTotal=st.files_total||st.files.length;
  document.getElementById("stBigHint").textContent =
    `showing ${Math.min(120,rows.length)} of ${fmtNum(nTotal)} live log files`
    + (nTotal>st.files.length?` · sortable over the largest ${fmtNum(st.files.length)}`:"");

  document.getElementById("stExtras").innerHTML=
    `<thead><tr><th class="nosort">What</th><th class="r nosort">Size</th></tr></thead><tbody>`+
    st.extras.map(e=>`<tr><td class="name" title="${esc(e.path)}">${esc(e.label)}
      ${e.note?`<div class="dim" style="font-size:11.5px;white-space:normal">${esc(e.note)}</div>`:""}</td>
      <td class="r">${fmtBytes(e.bytes)}</td></tr>`).join("")+"</tbody>";

  // reclaimable + the commands are computed server-side, over every live file and
  // for the shell this machine actually runs
  document.getElementById("cleanup").innerHTML=`
    <div class="hint" style="margin:16px 0 4px"><b>Reclaim space.</b> AgentTelemetry keeps every session it has
      already parsed, so deleting old logs does <b>not</b> shrink your analytics.
      ${rc.files?`<br><b>${fmtBytes(rc.bytes)}</b> sits in ${fmtNum(rc.files)} log(s) untouched for ${plan.days}+ days.`:""}</div>
    ${plan.commands.length?`<div class="hint">Run in <b>${esc(plan.shell)}</b> — paths are this machine's:</div>
      <code class="cmd">${plan.commands.map(esc).join("\n")}</code>`:""}
    <div class="hint" style="margin-top:8px">Claude Code prunes on its own after <code>cleanupPeriodDays</code>
      (default 30) in its <code>settings.json</code>; Codex keeps everything forever.</div>`;
}
// last two path components, on either separator (logs may come from either OS)
function shortPath(p){ return String(p||"").split(/[\\/]/).filter(Boolean).slice(-2).join("/"); }
/* ---------------- SETTINGS ---------------- */
async function openSettings(){
  openDrawer('<div class="empty">Loading…</div>');
  let cfg; try{ cfg=await (await fetch("/api/settings")).json(); }
  catch(e){ openDrawer('<div class="empty">Could not read settings.</div>'); return; }
  renderSettings(cfg);
}
/* Offline app (PWA) — strictly opt-in. A service worker keeps controlling this
   origin until it is unregistered, and localhost ports get reused by other tools,
   so nothing is registered unless the user turns it on here. */
const PWA_KEY = "aiu.pwa";
function pwaWanted(){ try{ return localStorage.getItem(PWA_KEY)==="1"; }catch(e){ return false; } }
function pwaSupported(){ return "serviceWorker" in navigator; }
async function pwaEnable(){
  try{ localStorage.setItem(PWA_KEY,"1"); }catch(e){}
  await navigator.serviceWorker.register("/sw.js");
}
async function pwaDisable(){
  try{ localStorage.removeItem(PWA_KEY); }catch(e){}
  const regs = await navigator.serviceWorker.getRegistrations();
  await Promise.all(regs.map(r=>r.unregister()));
  if(window.caches){
    const keys = await caches.keys();
    await Promise.all(keys.filter(k=>k.startsWith("ai-usage-")||k.startsWith("agenttelemetry-")).map(k=>caches.delete(k)));
  }
}

/* Refresh interval, in ms. 15s is the historical default; 0 = manual only. */
const POLL_KEY="aiu.poll";
const POLL_CHOICES=[["15000","15s"],["60000","1m"],["300000","5m"],["0","Manual"]];
const NATIVE_APP_MODE=new URLSearchParams(location.search).get("nativeApp")==="1";
function pollMs(){
  if(NATIVE_APP_MODE){
    const seconds=Number(new URLSearchParams(location.search).get("nativePollSeconds"))||300;
    return Math.max(60,seconds)*1000;
  }
  try{ const v=localStorage.getItem(POLL_KEY); return v===null?15000:Math.max(0,+v||0); }
  catch(e){ return 15000; }
}
function setPollMs(v){
  try{ localStorage.setItem(POLL_KEY,String(v)); }catch(e){}
  applyPollInterval();
}

function renderSettings(cfg){
  const days=cfg.claude_cleanup_days, def=cfg.claude_cleanup_default;
  openDrawer(`
    <div class="eyebrow">Settings</div>
    <h2 class="stg-h">Claude Code log retention</h2>
    <div class="stg-note">Claude Code deletes its own session transcripts after this
      many days &mdash; the <code>cleanupPeriodDays</code> setting. Codex, by contrast,
      keeps everything forever. Raise it to keep more history on disk.</div>
    <div class="stg-note">Shortening it never shrinks your analytics: this dashboard
      keeps every session it has already parsed, even after the tool deletes the log.</div>
    <div class="stg-field">
      <input class="field" type="number" id="stgDays" min="1" max="36500" step="1"
        placeholder="${def}" value="${days==null?"":days}" aria-label="Retention in days">
      <span class="stg-hint">days<br>blank = tool default (${def})</span>
    </div>
    <div class="stg-actions">
      <button class="btn primary" id="stgSave">Save</button>
      <button class="btn" id="stgReset">Reset to default</button>
    </div>
    <div class="stg-msg" id="stgMsg"></div>
    <div class="stg-path"><b>Writes to</b><span>${esc(cfg.claude_settings_path)}</span>${
      cfg.claude_settings_exists?"":"<br>Not created yet &mdash; it will be on first save."}
      <br>Other settings in the file are preserved, and a <span>.bak</span> is kept.</div>

    <h2 class="stg-h stg-sec">Install as an app</h2>
    <div class="stg-note">Off by default. Turning this on registers a service worker so
      the dashboard can be installed to your Dock or taskbar and still open its shell when
      <code>dashboard.py</code> isn't running. Your usage data is never cached &mdash;
      <code>/api/</code> always goes to the live server.</div>
    <div class="stg-note">A service worker keeps controlling <code>${esc(location.host)}</code>
      until you turn it off here, including for any <em>other</em> tool you later run on this
      port. Turning it off unregisters it and clears its cache.</div>
    <div class="stg-actions">
      <button class="btn${pwaWanted()?"":" primary"}" id="pwaBtn"${pwaSupported()?"":" disabled"}>${
        pwaWanted()?"Turn off":"Turn on"}</button>
      <span class="stg-hint" id="pwaState" style="align-self:center">${
        !pwaSupported() ? "Not available in this browser"
        : pwaWanted() ? "On \u2014 installable, shell cached" : "Off"}</span>
    </div>
    <div class="stg-msg" id="pwaMsg"></div>

    <h2 class="stg-h stg-sec">Refresh interval</h2>
    <div class="stg-note">How often the page re-fetches <code>/api/data</code> (about
      ${fmtBytes(cfg.cache_bytes||0)} of JSON each time). Slower saves CPU and disk churn;
      <b>Manual</b> updates only when you press <b>&#8635;</b>. The server keeps parsing
      either way &mdash; this is just how often the browser asks.</div>
    <div class="seg" id="pollSeg" style="margin-top:14px">${
      POLL_CHOICES.map(([v,l])=>`<button data-ms="${v}"${
        String(pollMs())===v?' class="on"':''}>${l}</button>`).join("")}</div>

    <h2 class="stg-h stg-sec">Your devices</h2>
    <div id="devBox"><div class="stg-hint">Loading…</div></div>

    <h2 class="stg-h stg-sec">Analytics cache</h2>
    <div class="stg-note">This dashboard's own parsed data &mdash;
      <b>${fmtBytes(cfg.cache_bytes||0)}</b> across ${fmtNum(cfg.cache_files||0)} files:
      your prompts, project names and costs. <b>Rebuild</b> re-reads every log from
      scratch. <b>Delete</b> removes the file now, but the next refresh writes it again
      from whatever logs are still on disk &mdash; to keep it gone, stop
      <code>dashboard.py</code> first.</div>
    <div class="stg-note" style="border-left-color:var(--bad)">Both discard the
      <b>durable ledger</b>: sessions whose logs a tool has already deleted exist only in
      this cache, and nothing can bring them back. Your totals will drop by whatever those
      sessions contributed.</div>
    <div class="stg-actions">
      <button class="btn" id="cacheRebuild">Rebuild now</button>
      <button class="btn" id="cacheDelete">Delete</button>
    </div>
    <div class="stg-msg" id="cacheMsg"></div>
    <div class="stg-path"><b>Cache file</b><span>${esc(cfg.cache_path||"")}</span></div>
  `);
  const msgEl=document.getElementById("stgMsg");
  const msg=(t,cls)=>{ msgEl.textContent=t; msgEl.className="stg-msg"+(cls?" "+cls:""); };
  const send=async(value,okText,btn)=>{
    const btns=[...document.querySelectorAll(".stg-actions .btn")];
    btns.forEach(b=>b.disabled=true); msg("Saving\u2026");
    try{
      const r=await fetch("/api/settings",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({cleanupPeriodDays:value})});
      const out=await r.json();
      if(!r.ok) throw new Error(out.error||"request failed");
      renderSettings(out); msg(okText,"ok");
    }catch(e){ btns.forEach(b=>b.disabled=false); msg(e.message,"err"); }
  };
  document.getElementById("stgSave").addEventListener("click",()=>{
    const raw=document.getElementById("stgDays").value.trim();
    if(raw===""){ send(null,"Cleared \u2014 Claude Code's default now applies."); return; }
    const v=Number(raw);
    if(!Number.isInteger(v)||v<1||v>36500){
      msg("Enter a whole number of days between 1 and 36500, or leave it blank.","err"); return; }
    send(v,`Saved \u2014 transcripts now kept for ${v} day${v===1?"":"s"}.`);
  });
  document.getElementById("stgReset").addEventListener("click",()=>
    send(null,"Reset \u2014 Claude Code's default now applies."));

  const seg=document.getElementById("pollSeg");
  if(seg) seg.addEventListener("click",e=>{
    const b=e.target.closest("button[data-ms]"); if(!b) return;
    setPollMs(+b.dataset.ms);
    [...seg.children].forEach(x=>x.classList.toggle("on",x===b));
  });

  const cacheMsg=()=>document.getElementById("cacheMsg");
  const cacheDo=async(action,ask)=>{
    const btns=[document.getElementById("cacheRebuild"),document.getElementById("cacheDelete")];
    if(!await askConfirm(ask)) return;
    btns.forEach(b=>b.disabled=true);
    cacheMsg().className="stg-msg"; cacheMsg().textContent=
      action==="rebuild"?"Re-reading every log\u2026 this can take a minute.":"Deleting\u2026";
    try{
      const r=await fetch("/api/cache",{method:"POST",
        headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
      const out=await r.json();
      if(!r.ok) throw new Error(out.error||"request failed");
      await load(); await loadStorage();
      const cfg2=await (await fetch("/api/settings")).json();
      renderSettings(cfg2);
      const m=document.getElementById("cacheMsg"); m.className="stg-msg ok";
      m.textContent = out.action==="rebuild"
        ? `Rebuilt ${fmtNum(out.files)} files in ${out.seconds}s.`
        : `Deleted. ${fmtNum(out.dropped)} parsed files dropped from memory.`;
    }catch(e){ btns.forEach(b=>b.disabled=false);
      cacheMsg().className="stg-msg err"; cacheMsg().textContent=e.message; }
  };
  document.getElementById("cacheRebuild").addEventListener("click",()=>cacheDo("rebuild",{
    title:"Rebuild the analytics cache?", ok:"Rebuild", danger:true,
    body:`<p>Every log is re-read from scratch. This can take a minute.</p>
      <div class="modal-note bad">Sessions whose logs a tool has already deleted <b>can't be
      recovered</b> and will disappear from your totals.</div>`}));
  document.getElementById("cacheDelete").addEventListener("click",()=>cacheDo("delete",{
    title:"Delete the analytics cache?", ok:"Delete", danger:true,
    body:`<p>The file is removed now, but a running server writes it again on the next refresh,
      from the logs still on disk.</p>
      <div class="modal-note bad">What does <b>not</b> come back: sessions whose logs a tool
      already deleted.</div>`}));

  loadDevices();

  const pwaBtn=document.getElementById("pwaBtn");
  if(pwaBtn && pwaSupported()) pwaBtn.addEventListener("click",async()=>{
    const turningOn=!pwaWanted(); const m=document.getElementById("pwaMsg");
    pwaBtn.disabled=true; m.className="stg-msg"; m.textContent=turningOn?"Registering\u2026":"Removing\u2026";
    try{
      if(turningOn){ await pwaEnable(); renderSettings(cfg);
        document.getElementById("pwaMsg").className="stg-msg ok";
        document.getElementById("pwaMsg").textContent=
          "On. Use your browser's Install / Add to Dock to place it alongside your apps.";
      }else{ await pwaDisable(); renderSettings(cfg);
        document.getElementById("pwaMsg").className="stg-msg ok";
        document.getElementById("pwaMsg").textContent=
          "Off. Service worker unregistered and its cache cleared.";
      }
    }catch(e){ pwaBtn.disabled=false; m.className="stg-msg err"; m.textContent=e.message; }
  });
}

/* Your devices — see another computer's usage here, and let it see this one's.
   Off until turned on, and the only thing that sends data off this machine, so
   each switch asks first. Sharing and connecting are separate: do both on both
   machines to see each from the other. */
/* Why the other device can't get in, as each OS puts it. */
const firewallHint=me=>({
  macos:"If macOS asks whether Python may accept incoming connections, allow it",
  windows:"If Windows asks whether to allow Python through the firewall, allow it on private "+
    "networks, and set this Wi-Fi to Private (Settings \u2192 Network & internet)",
  }[me.platform] || `If a firewall is on (ufw, firewalld), allow TCP port ${me.port}`)+
  ", or the other device can't reach this one.";
async function loadDevices(msg){
  const box=document.getElementById("devBox"); if(!box) return;
  try{ renderDevices(await (await fetch("/api/devices")).json(), msg); }
  catch(e){ box.innerHTML='<div class="stg-msg err">Could not read the device settings.</div>'; }
}
function renderDevices(st, msg){
  const box=document.getElementById("devBox"); if(!box) return;
  const me=st.this||{}, peers=st.peers||[];
  const addrs=(me.addresses||[]).map(a=>`<code>${esc(a)}:${me.port}</code>`).join(" or ");
  const by=(me.pulled_by||[]).map(x=>`${esc(x.name||x.ip)}${x.name?` <span class="dev-meta">(${esc(x.ip)})</span>`:""} ${ago(x.at)}`);
  box.innerHTML = `
    <div class="stg-note">See your usage from your other computers here, all in one place. It works
      over your <b>local network</b> and only if you turn it on. One device <b>shares</b>, and the
      other <b>connects</b> to it with its address and pairing code. To see each device from the
      other, do both on both.</div>
    <div class="dev-sub">Share this device</div>
    ${me.sharing ? `<div class="dev-card">
        <div>Address: ${addrs || '<span class="dev-meta">no network address found</span>'}</div>
        <div>Pairing code: <code class="dev-code">${esc(me.code||"")}</code></div>
        <div class="dev-meta">${by.length ? "Read by "+by.join(", ") : "No device has connected yet."}</div>
        <div class="stg-actions"><button class="btn" id="devShareOff">Stop sharing</button>
          <button class="btn" id="devNewCode">New code</button></div>
      </div>
      <div class="stg-hint" style="margin-top:8px">${esc(firewallHint(me))}</div>`
    : `<div class="stg-hint">Off. Nothing on this device can be reached from the network.</div>
      <div class="stg-actions"><button class="btn primary" id="devShareOn">Share this device…</button></div>`}
    ${me.error?`<div class="stg-msg err">${esc(me.error)}</div>`:""}
    <div class="dev-sub">Connected devices</div>
    ${peers.map(p=>`<div class="dev-card">
        <div><span class="dev-name">${esc(p.name)}</span> <span class="dev-meta">${esc(p.os||"")} · ${esc(p.addr||"")}${p.via?` (answering at ${esc(p.via)})`:""}</span></div>
        <div class="dev-meta">${p.last_ok?`Synced ${ago(p.last_ok)}`:"Never synced"}${p.shown?` · ${fmtNum(p.logs)} logs`:""}${p.app?` · ${esc(p.app)}`:""}</div>
        ${p.error?`<div class="dev-err">${esc(p.error)}</div>`:""}
        <div class="stg-actions"><button class="btn" data-dev-sync>Sync now</button>
          <button class="btn" data-dev-off="${esc(p.id)}" data-dev-name="${esc(p.name)}">Disconnect</button></div>
      </div>`).join("") || `<div class="stg-hint">None yet. On the other device, turn on sharing, then enter its address and code here.</div>`}
    <div class="dev-form">
      <input class="field" id="devAddr" placeholder="other-mac.local or 192.168.1.20" aria-label="Other device's address" autocomplete="off" spellcheck="false">
      <input class="field" id="devCode" placeholder="Pairing code" aria-label="Pairing code" autocomplete="off" spellcheck="false">
      <button class="btn" id="devConnect">Connect…</button>
    </div>
    <div class="stg-msg" id="devMsg"></div>`;
  const m=document.getElementById("devMsg");
  if(msg){ m.textContent=msg[0]; m.className="stg-msg "+(msg[1]||""); }
  const act=async(body, busy, ok)=>{
    box.querySelectorAll(".btn").forEach(b=>b.disabled=true);
    m.className="stg-msg"; m.textContent=busy;
    try{
      const r=await fetch("/api/devices",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify(body)});
      const out=await r.json();
      if(!r.ok) throw new Error(out.error||"request failed");
      renderDevices(out, ok?[ok,"ok"]:null);
      if(body.action!=="share" && body.action!=="new_code") await load();
    }catch(e){ box.querySelectorAll(".btn").forEach(b=>b.disabled=false);
      m.className="stg-msg err"; m.textContent=e.message; }
  };
  const on=(id,fn)=>{ const el=document.getElementById(id); if(el) el.addEventListener("click",fn); };
  on("devShareOn",async()=>{
    if(!await askConfirm({title:"Share this device's usage on your network?", ok:"Start sharing",
      body:`<p>This is the only mode in which AgentTelemetry sends anything off this machine.
        It opens port <code>${me.port}</code> on your local network, and any device there with
        the pairing code can read:</p>
        <ul><li>session titles (often the start of a prompt)</li>
          <li>project and branch names, and log file paths</li>
          <li>models, token counts, costs and times</li></ul>
        <p>Full prompts, replies and file contents are <b>never</b> sent.</p>
        <div class="modal-note">The connection is <b>not encrypted</b>. Turn this on only on a
        network you trust. You can turn it off here at any time.</div>`})) return;
    act({action:"share",on:true},"Opening the port…","Sharing. Enter this address and code on your other device.");
  });
  on("devShareOff",()=>act({action:"share",on:false},"Stopping…","Sharing stopped. This device can't be reached from the network."));
  on("devNewCode",async()=>{
    if(!await askConfirm({title:"Make a new pairing code?", ok:"New code",
      body:`<p>Devices connected with the old code lose access until you enter the new one on
        them.</p>`})) return;
    act({action:"new_code"},"Making a new code…","New code made. The old one no longer works.");
  });
  on("devConnect",async()=>{
    const address=document.getElementById("devAddr").value.trim(), code=document.getElementById("devCode").value.trim();
    if(!address||!code){ m.className="stg-msg err"; m.textContent="Enter the other device's address and its pairing code."; return; }
    if(!await askConfirm({title:"Connect to this device?", ok:"Connect",
      body:`<p><code>${esc(address)}</code></p>
        <p>This device will fetch its usage over your local network every
        ${Math.round((st.pull_every||60)/60)} min and keep a copy here, in <code>.peers/</code>,
        so it still shows while the other one is off. Disconnecting deletes that copy.</p>`})) return;
    act({action:"connect",address,code},"Connecting…","Connected. Its usage is now in the data, and a Devices filter has been added.");
  });
  box.querySelectorAll("[data-dev-sync]").forEach(b=>b.addEventListener("click",()=>
    act({action:"sync"},"Syncing…","Synced.")));
  box.querySelectorAll("[data-dev-off]").forEach(b=>b.addEventListener("click",async()=>{
    if(!await askConfirm({title:`Disconnect ${b.dataset.devName}?`, ok:"Disconnect", danger:true,
      body:`<p>Its usage leaves this dashboard and the copy kept here is deleted. Nothing on
        that device changes.</p>`})) return;
    act({action:"disconnect",id:b.dataset.devOff},"Disconnecting…","Disconnected.");
  }));
}

/* ---------------- OPTIMIZE ----------------
   Every finding below is derived from the user's own logs in the selected range
   and must carry (a) a number it is based on and (b) something to actually do.
   A finding that does not apply is not rendered — no filler, no scolding. */

/* sessionRows() adds a display `name`; the finders run on the raw clipped
   sessions, so derive it the same way rather than rendering a blank cell. */
const sessName = s => s.title || s.project || s.id || "(unnamed)";

/* Anthropic bills a cache WRITE at 1.25-2x input and a read at 0.1x. Writing a
   cache you never read back is the one unambiguous waste in the data. */
function findCacheWaste(d){
  const bad = d.sessions.filter(s => s.cc > 20000 && s.cr < s.cc * 0.5);
  if(!bad.length) return null;
  const cc = bad.reduce((a,s)=>a+s.cc,0);
  return {
    id:"cache-waste", impact:0, sev:"info", tools:[...new Set(bad.map(x=>x.source))],
    title:"Cache written with little reuse",
    body:`${bad.length} session${bad.length>1?"s":""} wrote <b>${fmtNum(cc)}</b> cache tokens and `
      +`read back less than half that amount. Cache billing differs by model and retention tier; `
      +`low reuse alone cannot establish how much money could have been saved.`,
    todo:"Keep related questions in the same session when useful. Check your tool's cache retention "
      +"before paying for longer-lived writes.",
    rows:bad.sort((a,b)=>b.cc-a.cc).slice(0,5).map(s=>
      [sessName(s), `${fmtNum(s.cc)} written`, `${fmtNum(s.cr)} read`, fmtUSD(s.cost)])
  };
}

/* An expensive model doing light work.

   Nothing here is a hardcoded model list. The candidate replacements are the models
   THIS user actually ran, priced from the rates the server sent, so it stays true as
   models come and go and reflects what they realistically have access to. */
/* NB: a global priceOf() already exists above (RAW.pricing, zero-tuple fallback).
   This one is deliberately separate — it returns null for an unpriced model so the
   savings math can skip it rather than quietly costing it at zero. */
function rateOf(m){ return (RAW.prices && RAW.prices[m]) || null; }
function costAt(m, t){
  const p = rateOf(m); if(!p) return null;
  const [pin,pout,pcw5,pcw1,pcr] = p;
  // a write with no tier split is a 5-minute write; a vendor with no write price
  // (older OpenAI models) bills the write as plain input — never as free
  const cw5 = (t.cc5||t.cc1) ? (t.cc5||0) : (t.cc||0);
  return ((t.in||0)*pin + (t.out||0)*pout + (t.cr||0)*pcr
        + cw5*(pcw5||pin) + (t.cc1||0)*(pcw1||pcw5||pin)) / 1e6;
}
/* The cheapest model the user ALSO used from the same vendor — a realistic swap,
   not a recommendation to adopt something they've never touched. */
function cheaperPeer(model, usedModels){
  const p = rateOf(model); if(!p || !p[1]) return null;
  const vendor = (RAW.model_vendor||{})[model];
  let best=null, bestOut=p[1];
  for(const m of usedModels){
    if(m===model) continue;
    if((RAW.model_vendor||{})[m] !== vendor) continue;
    const q = rateOf(m);
    if(q && q[1] && q[1] < bestOut){ bestOut=q[1]; best=m; }
  }
  return best;
}
function findModelFit(d){
  const usedModels = [...new Set(d.recs.filter(r=>r.model!=="(user)").map(r=>r.model))];
  const groups = {};
  for(const s of d.sessions){
    if(s.subagent) continue;
    // "light work": short answer, barely any tool use — the shape where a smaller
    // model rarely shows a quality difference. Thresholds are on the session's own
    // output, not on any particular model's name.
    if(!(s.out < 4000 && s.tools <= 3 && s.cost > 0.02)) continue;
    (groups[s.model] = groups[s.model] || []).push(s);
  }
  const rows=[]; let saving=0, n=0, srcs=new Set();
  for(const [model, list] of Object.entries(groups)){
    const alt = cheaperPeer(model, usedModels);
    if(!alt) continue;
    let now=0, then=0;
    for(const s of list){
      const at = costAt(alt, s);
      if(at===null) continue;
      now += s.cost; then += at; srcs.add(s.source);
    }
    if(then >= now || now-then < 0.5) continue;
    saving += now-then; n += list.length;
    rows.push([`${list.length} session${list.length>1?"s":""} on ${model}`,
               `→ ${alt}`, fmtUSD(now)+" spent", fmtUSD(now-then)+" saved"]);
  }
  if(!rows.length) return null;
  rows.sort((a,b)=>parseFloat(b[3].replace(/[^0-9.]/g,""))-parseFloat(a[3].replace(/[^0-9.]/g,"")));
  return {
    id:"model-fit", impact: saving, sev: saving>20?"high":"low", tools:[...srcs],
    title:"A costly model doing very light work",
    body:`${n} session${n>1?"s":""} produced under 4k output tokens with 3 or fewer tool calls, `
      +`yet ran on a model you also pay a premium for. Re-priced at the cheapest model of the `
      +`same maker <i>you already use</i>, the same tokens would have cost `
      +`<b>${fmtUSD(saving)}</b> less.`,
    todo:"For quick lookups and one-shot questions, start the session on the smaller model.",
    rows
  };
}

/* MCP tool definitions ride in the system prompt of EVERY request. A server you
   never call is a standing cost on every turn. */
function findIdleMCP(d){
  if(!RAW.mcp_servers || !RAW.mcp_servers.length) return null;
  const norm = x => String(x||"").toLowerCase().replace(/[^a-z0-9]/g,"");
  const used = {};
  const wanted = arguments[1];
  if(!passSrc(wanted) || (RAW.device && !passDev(RAW.device.id))) return null;
  // Inventory calls retain every server, even when the top-tools table folds a
  // rare tool into "(other)". Fall back only for older payloads without source tags.
  const calls=RAW.mcp_inventory && RAW.mcp_inventory.calls;
  if(calls && calls.every(x=>x.source)){
    for(const x of calls) if(x.source===wanted && x.date>=d.r.from && x.date<=d.r.to)
      used[norm(x.server)]=(used[norm(x.server)]||0)+x.calls;
  }else for(const t of toolCounts(d.r, x=>isLocal(x) && x.source===wanted)){
    if(!t.name.startsWith("mcp__")) continue;
    const rest = t.name.slice(5), i = rest.indexOf("__");
    const srv = norm(i>0 ? rest.slice(0,i) : rest);
    used[srv] = (used[srv]||0) + t.count;
  }
  const idle = RAW.mcp_servers.filter(m => (m.tool||"claude")===wanted).filter(m => {
    const n = norm(m.name);
    // loose match: a server may appear in tool names under a shortened alias
    return !Object.keys(used).some(u => u===n || u.includes(n) || n.includes(u));
  });
  if(!idle.length) return null;
  const label = (SRC[wanted]||{label:wanted}).label;
  return {
    id:"idle-mcp-"+wanted, impact: 0, sev:"info", tools:[wanted],
    title:`${idle.length} ${label} MCP server${idle.length>1?"s":""} configured but never called`,
    body:(wanted==="claude" && RAW.mcp_inventory && Object.keys(RAW.mcp_inventory.servers).length
        ? `Claude Code announces every connected server's tools to each session and loads a tool's full `
          +`definition when it's searched for, so an idle server costs little context — but it is still `
          +`started with every session and offered to the model. `
        : `Every connected MCP server's tool definitions are injected into the system prompt of `
          +`<b>every request</b>, whether you use them or not. `)
      +`These ${idle.length} were not called once in the selected range: <b>${idle.map(m=>esc(m.name)).join(", ")}</b>.`,
    todo: wanted==="codex"
      ? "Remove the ones you don't reach for from the [mcp_servers.*] blocks in "
        +"~/.codex/config.toml, and re-add when you next need them."
      : "Disconnect the ones you don't reach for — in Claude Code, /mcp, or remove them from "
        +"~/.claude.json. Re-add when you next need them.",
    rows: idle.map(m => [m.name, m.scope==="global"?"global":"project-scoped",
      m.projects && m.projects.length ? m.projects.slice(0,3).join(", ") : "—", "0 calls"])
  };
}

/* Thinking tokens bill at the output rate. Worth surfacing only when the share is
   high enough that dropping effort would actually move the bill. */
function findThinking(d){
  let reason=0, out=0, cost=0;
  for(const r of d.recs){ reason += r.reason||0; out += r.out||0; cost += r.cost||0; }
  if(!out || reason/out < 0.15) return null;
  const share = reason/out;
  return {
    id:"thinking", impact: cost*share*0.3, sev: share>0.3?"high":"low", tools:[...new Set(d.recs.filter(r=>r.reason).map(r=>r.source))],
    title:"Extended thinking is a large share of your output",
    body:`<b>${fmtNum(reason)}</b> of ${fmtNum(out)} output tokens (${fmtPct(share)}) were `
      +`thinking tokens, billed at the full output rate.`,
    todo:"Thinking earns its cost on genuinely hard problems and wastes it on routine edits. "
      +"Lower the default effort and raise it per-task rather than leaving it at max.",
    rows: []
  };
}

/* Sessions where each prompt costs far more than your own norm — usually context
   that grew huge and is now re-read on every single turn. */
function findContextTax(d){
  const withPrompts = d.sessions.filter(s => s.user >= 5 && s.cost > 0);
  if(withPrompts.length < 5) return null;
  const per = withPrompts.map(s => s.cost/s.user).sort((a,b)=>a-b);
  const med = per[Math.floor(per.length/2)];
  const bad = withPrompts.filter(s => s.cost/s.user > Math.max(med*8, 1)).sort((a,b)=>b.cost-a.cost);
  if(!bad.length) return null;
  const excess = bad.reduce((a,s)=>a + (s.cost - med*s.user), 0);
  return {
    id:"context-tax", impact: Math.max(excess,0), sev: excess>50?"high":"low", tools:[...new Set(bad.map(x=>x.source))],
    title:"Long sessions re-reading a very large context",
    body:`${bad.length} session${bad.length>1?"s":""} cost more than 8× your median of `
      +`<b>${fmtUSD2(med)}</b> per prompt. Every turn re-reads the whole conversation, so a session `
      +`that has grown large keeps paying for it on each new question.`,
    todo:"When a session drifts to a new task, start a fresh one — or /compact to drop the "
      +"history you no longer need.",
    rows: bad.slice(0,5).map(s => [sessName(s), `${fmtNum(s.user)} prompts`,
      `${fmtUSD2(s.cost/s.user)}/prompt`, fmtUSD(s.cost)])
  };
}

/* Delegating exploration to a subagent keeps the parent's context small. */
function findSubagents(d){
  const parents = d.sessions.filter(s => !s.subagent);
  // "delegated" shows up two different ways depending on the tool: Claude Code's
  // subagent tokens land back on the parent's own token stream (s.side); Codex's
  // subagents are wholly separate sessions, so the parent only carries a spawn
  // COUNT (s.subagents, from SubAgentActivity "started" markers in its own log).
  const heavy = parents.filter(s => s.tools >= 150 && !s.side && !s.subagents);
  if(heavy.length < 2) return null;
  const spend = heavy.reduce((a,s)=>a+s.cost,0);
  const used = parents.filter(s => s.side > 0 || s.subagents > 0).length;
  return {
    id:"subagents", impact: spend*0.15, sev: spend>100?"high":"low", tools:[...new Set(heavy.map(x=>x.source))],
    title:"Tool-heavy sessions that never delegated to a subagent",
    body:`${heavy.length} session${heavy.length>1?"s":""} made 150+ tool calls without spawning a `
      +`subagent, costing <b>${fmtUSD(spend)}</b>. ${used ? `You do use them elsewhere (${used} `
      +`session${used>1?"s":""} did).` : ""} Every file a search reads lands in the main context and `
      +`is re-read on every later turn; a subagent reads it in its own context and returns only the answer.`,
    todo:"For broad searching or codebase exploration, hand it to a subagent and keep the summary.",
    rows: heavy.sort((a,b)=>b.cost-a.cost).slice(0,5).map(s =>
      [sessName(s), `${fmtNum(s.tools)} tool calls`, `${fmtNum(s.user)} prompts`, fmtUSD(s.cost)])
  };
}

/* Cache hit rate, but only when it is genuinely low — the Cost tab already shows
   the healthy case. */
function findLowCache(d){
  let cr=0, ctx=0;
  for(const r of d.recs){ cr += r.cr||0; ctx += ctxTokens(r); }
  if(!ctx || cr/ctx >= 0.7) return null;
  const rate = cr/ctx;
  return {
    id:"low-cache", impact: 0, sev: rate<0.4?"high":"low", tools:[...new Set(d.recs.filter(r=>r.cr||r.cc).map(r=>r.source))],
    title:"Prompt cache is doing less work than it could",
    body:`Only <b>${fmtPct(rate)}</b> of your context tokens came from cache. A cache read costs `
      +`a tenth of a fresh input token, so the gap is close to pure overhead.`,
    todo:"Caching rewards stable context. Editing files near the top of the conversation, or "
      +"switching models mid-session, invalidates it and forces a re-read.",
    rows: []
  };
}


/* Claude Code stamps attributionSkill on the requests a Skill drove. A skill that
   fires often and pulls a lot of context is worth tightening or scoping. */
function findSkills(d){
  if(!RAW.skills || !RAW.skills.length) return null;
  const inRange = RAW.skills.filter(x => passDev(x.device) && x.date>=d.r.from && x.date<=d.r.to);
  if(!inRange.length) return null;
  const by={};
  for(const x of inRange){
    const e=by[x.name]||(by[x.name]={tok:0,asst:0,cost:0});
    e.tok+=x.tok; e.asst+=x.asst; e.cost+=x.cost;
  }
  const rows=Object.entries(by).sort((a,b)=>b[1].cost-a[1].cost);
  const total=d.recs.reduce((a,r)=>a+(r.cost||0),0);
  const spend=rows.reduce((a,[,v])=>a+v.cost,0);
  if(!spend) return null;
  return {
    id:"skills", impact: 0, sev:"info", tools:["claude"],
    title:"Where your Skills are spending",
    body:`Skills drove <b>${fmtUSD(spend)}</b>${total?` of ${fmtUSD(total)} (${fmtPct(spend/total)})`:""} `
      +`in this range. A skill that runs often carries its whole instruction file into context `
      +`each time, so a frequently-fired one is worth scoping tightly.`,
    todo:"For the expensive ones, narrow when they trigger, or point their subagents at a "
      +"cheaper model.",
    rows: rows.slice(0,6).map(([n,v]) =>
      [`/${n}`, `${fmtNum(v.asst)} requests`, fmtNum(v.tok)+" tokens", fmtUSD(v.cost)])
  };
}

/* Every request re-sends the whole conversation. Past ~150k that is expensive even
   at cache-read rates, because the read itself scales with the context. */
function findBigContext(d){
  if(!RAW.ctx || !RAW.ctx.length) return null;
  // ctx rows carry a source but no model/project, so only the tool filter applies
  const inRange = RAW.ctx.filter(x => passSrc(x.source) && passDev(x.device) && x.date>=d.r.from && x.date<=d.r.to);
  if(!inRange.length) return null;
  const by={}; let tot=0; const srcs=new Set();
  for(const x of inRange){ by[x.bucket]=(by[x.bucket]||{tok:0,n:0});
    by[x.bucket].tok+=x.tok; by[x.bucket].n+=x.n; tot+=x.tok;
    if(x.source) srcs.add(x.source); }
  if(!tot) return null;
  const big=(by["150-400k"]?.tok||0)+(by["400k+"]?.tok||0);
  const share=big/tot;
  if(share < 0.4) return null;
  // the share was measured on the tools that log context size, so apply it to theirs
  const total=d.recs.reduce((a,r)=>a+(srcs.has(r.source)?(r.cost||0):0),0);
  const order=["0-50k","50-150k","150-400k","400k+"];
  return {
    id:"big-context", impact: total*share*0.15, sev: share>0.7?"high":"low", tools:[...srcs],
    title:`${fmtPct(share)} of your tokens were sent at over 150k context`,
    body:`Every turn re-sends the whole conversation. Past roughly 150k the re-read dominates `
      +`the bill even when it is cached, because a cache read is charged per token too.`,
    todo: srcs.has("claude")
      ? "/compact once a task is done to drop the history behind it, and /clear (or a fresh "
        +"session) when you switch to something unrelated."
      : "Start a fresh session when you switch tasks rather than continuing a long one — "
        +"the whole history is re-sent on every turn.",
    rows: order.filter(b=>by[b]).map(b =>
      [b+" context", `${fmtNum(by[b].n)} requests`, fmtNum(by[b].tok)+" tokens",
       fmtPct(by[b].tok/tot)])
  };
}

/* Subagents each run their own request loop, so a subagent-heavy day is a real cost
   centre even though it is often the right call. */
function findSubagentShare(d){
  const subs=d.sessions.filter(s=>s.subagent);
  if(!subs.length) return null;
  const subCost=subs.reduce((a,s)=>a+s.cost,0);
  const total=d.sessions.reduce((a,s)=>a+s.cost,0);
  if(!total || subCost/total < 0.2) return null;
  return {
    id:"subagent-share", impact: 0, sev:"info", tools:[...new Set(subs.map(x=>x.source))],
    title:`${fmtPct(subCost/total)} of your spend ran inside subagents`,
    body:`${fmtNum(subs.length)} subagent transcript${subs.length>1?"s":""} cost <b>${fmtUSD(subCost)}</b>. `
      +`Each subagent runs its own request loop with its own context, which is exactly why they keep `
      +`the parent small — but it does mean spawning one is never free.`,
    todo:"Worth it for broad exploration; wasteful for a task you could answer inline. For simple "
      +"delegated work, point the subagent at a cheaper model.",
    rows: subs.sort((a,b)=>b.cost-a.cost).slice(0,5).map(s =>
      [sessName(s), `${fmtNum(s.tools)} tools`, fmtNum(s.tok)+" tokens", fmtUSD(s.cost)])
  };
}


/* Codex pins a global reasoning effort in ~/.codex/config.toml. Worth raising only
   when the setting is at the top of the scale AND the logs show reasoning actually
   dominating the output — otherwise it is someone's deliberate choice, not a finding. */
function findCodexEffort(d){
  const eff = RAW.codex_effort;
  if(!eff || !["high","xhigh","ultra","max"].includes(String(eff).toLowerCase())) return null;
  let reason=0, out=0, cost=0;
  for(const r of d.recs){
    if(r.source!=="codex") continue;
    reason += r.reason||0; out += r.out||0; cost += r.cost||0;
  }
  if(!out || !cost) return null;
  const share = reason/out;
  if(share < 0.2) return null;
  return {
    id:"codex-effort", impact: cost*share*0.25, sev: share>0.5?"high":"low", tools:["codex"],
    title:`Codex reasoning effort is set to "${esc(eff)}"`,
    body:`<b>${fmtNum(reason)}</b> of ${fmtNum(out)} Codex output tokens (${fmtPct(share)}) were `
      +`reasoning, billed at the output rate, against <b>${fmtUSD(cost)}</b> of Codex spend in `
      +`this range. <code>model_reasoning_effort</code> is a global default, so it applies to `
      +`trivial turns as much as hard ones.`,
    todo:"Lower model_reasoning_effort in ~/.codex/config.toml and raise it per-task when a "
      +"problem actually warrants it.",
    rows: []
  };
}

/* An editing prompt that needed edit → run → re-edit of the same file cost more
   than one that landed first time. The saving is that difference, from this user's
   own averages: (avg retried prompt − avg one-shot prompt) × retried prompts. */
function findRetryTax(d){
  const by = actSum(actRows(d), x=>x.source);
  const rows = [];
  let impact = 0, retried = 0, spend = 0;
  for(const k of ORDER){ const x = by[k]; if(!x || x.edits < 10) continue;
    const nr = x.edits - x.oneshot; if(!nr || !x.oneshot) continue;
    const avgR = x.retry_cost/nr, avgO = (x.edit_cost - x.retry_cost)/x.oneshot;
    const extra = Math.max(0, (avgR - avgO) * nr);
    if(extra <= 0) continue;
    impact += extra; retried += nr; spend += x.retry_cost;
    rows.push([SRC[k].label, `${fmtPct(x.oneshot/x.edits)} one-shot`,
      `${fmtUSD2(avgR)} vs ${fmtUSD2(avgO)} per prompt`, `${fmtUSD(extra)} extra`]);
  }
  if(!rows.length || impact < 1) return null;
  return {
    id:"retry-tax", impact, sev: impact > 50 ? "high" : "low",
    tools: rows.map(r => ORDER.find(k => SRC[k].label === r[0])),
    title:`Edits that didn't land first time cost ${fmtUSD(impact)} extra`,
    body:`<b>${fmtNum(retried)}</b> editing prompts needed a retry — an edit, a run that checked it, and another `
      +`edit to the same file. Together they cost <b>${fmtUSD(spend)}</b>; at the price of a prompt that `
      +`landed first time they'd have cost ${fmtUSD(Math.max(0, spend-impact))}.`,
    todo:"Say how to check the change up front — the test or build command, the expected output — so the "
      +"agent verifies before it edits again. For a fiddly change, ask for a plan first.",
    rows
  };
}

/* ---- setup checks: what every request carries before any work starts ---- */
const inRangeD = (x, d) => x.date>=d.r.from && x.date<=d.r.to;
/* requests and what one more token costs on each, for records matching fn */
function perTokenCost(d, fn, useReq){
  let n = 0, per = 0;
  for(const r of d.recs){ if(r.model==="(user)" || !fn(r)) continue;
    const calls = useReq ? (r.req||r.asst||0) : (r.asst||0);
    n += calls; per += calls * ((RAW.prices && RAW.prices[r.model]) || priceOf(r.model, r.date))[4] / 1e6; }
  return {n, per};           // per = $ for one extra token on every one of those requests
}
const famOf = src => src==="codex" ? "codex" : (src==="claude"||src==="claude-desktop") ? "claude" : null;
/* $ per token written to cache across these records, weighted by the writes they made
   (a model with no write rate writes at its input rate, as _cost() bills it) */
function cacheWriteRate(recs){
  let tok = 0, usd = 0;
  for(const r of recs){
    const p = (RAW.prices && RAW.prices[r.model]) || priceOf(r.model, r.date);
    const c5 = r.cc5||0, c1 = r.cc1||0, flat = (c5||c1) ? 0 : (r.cc||0);
    const w5 = p[2] || p[0], w1 = p[3] || w5;
    tok += c5 + c1 + flat; usd += (c5 + flat) * w5 + c1 * w1;
  }
  return tok ? usd / tok / 1e6 : 0;
}

/* CLAUDE.md / AGENTS.md ride along on every request (cached, so at the cache-read
   rate). Priced from this user's own request count and rates in range. */
function findInstructionFiles(d){
  // `sent` is what the agent actually includes (Codex stops at 32 KiB); `since` is the
  // first day it could have (Claude Code reads AGENTS.md only from 2.1.277)
  const files = (RAW.context_files||[]).filter(f => (f.sent ?? f.bytes) > 8192);
  if(!files.length) return null;
  const rows = []; let impact = 0; const tools = new Set();
  for(const f of files){
    const sent = f.sent ?? f.bytes, tok = sent/4;
    const {n, per} = perTokenCost(d, r => famOf(r.source)===f.source && (f.project==="*" || r.project===f.project)
      && (!f.since || r.date >= f.since), f.source==="codex");
    if(!n) continue;
    const cost = tok * per, save = cost * (sent-4096)/sent;
    impact += save; tools.add(f.source==="codex" ? "codex" : "claude");
    rows.push([f.file.replace(RAW.home||"~","~"),
      `${fmtBytes(f.bytes)}${sent<f.bytes ? ` (first ${fmtBytes(sent)} sent)` : ""} · ~${fmtTok(tok)} tokens`,
      `${fmtNum(n)} requests`, fmtUSD(cost)]);
  }
  if(!rows.length || impact < 0.5) return null;
  return {
    id:"instruction-files", impact, sev: impact>20?"high":"low", tools:[...tools],
    title:`${rows.length} instruction file${rows.length>1?"s are":" is"} over 8 KB and sent with every request`,
    body:`CLAUDE.md and AGENTS.md are loaded into the context of <b>every</b> request in that project — `
      +`cheap per request because they're cached, but it adds up. Trimmed to 4 KB, these would cost about `
      +`<b>${fmtUSD(impact)}</b> less over the range.`,
    todo:"Keep only what the agent needs on every turn: commands, conventions, gotchas. Move long "
      +"reference material into separate files the agent reads when relevant.",
    rows
  };
}

/* The first request of a session carries the fixed opening load: system prompt,
   tool definitions, instruction files, memory. Compared with the user's OWN leanest
   sessions (10th percentile), not with a guessed baseline. */
function findHeavyOpeners(d){
  const out = [], tools = [];
  let impact = 0;
  for(const src of ["claude","codex"]){
    const ss = d.sessions.filter(s => famOf(s.source)===src && !s.subagent && s.open_ctx);
    if(ss.length < 5) continue;
    const v = ss.map(s=>s.open_ctx).sort((a,b)=>a-b);
    const lean = v[Math.floor(v.length*0.1)], med = v[Math.floor(v.length/2)];
    if(med < lean*1.3 || med < 20000) continue;
    const {n, per} = perTokenCost(d, r => famOf(r.source)===src, src==="codex");
    const reqPerSess = n / Math.max(1, ss.length);
    const extra = ss.reduce((a,s)=>a+Math.max(0, s.open_ctx-lean),0) * reqPerSess * (per/Math.max(1,n));
    if(extra <= 0) continue;
    impact += extra; tools.push(src);
    out.push([src==="codex"?"Codex":"Claude Code", `median ${fmtTok(med)} tokens`, `leanest ${fmtTok(lean)}`, `${fmtUSD(extra)} over the range`]);
  }
  if(!out.length || impact < 1) return null;
  return {
    id:"heavy-openers", impact, sev: impact>30?"high":"low", tools,
    title:"Sessions start heavier than they need to",
    body:`Before you type anything, a session already carries its system prompt, tool definitions, `
      +`instruction files and memory — and every later request re-reads them. Your typical session opens well `
      +`above your leanest ones; the difference costs about <b>${fmtUSD(impact)}</b> in this range.`,
    todo:"Disconnect MCP servers and plugins you don't use in this project, trim CLAUDE.md / AGENTS.md, "
      +"and prune stale memory. Compare a fresh session's first request after each change.",
    rows: out
  };
}

/* Re-reading an unchanged file, or reading generated / vendored folders, adds tokens
   to context for nothing. Lower-bound cost: each such token written to cache once. */
function findReadWaste(d){
  const rs = (RAW.reads||[]).filter(x => passSrc(x.source) && passProj(x.project) && passDev(x.device) && inRangeD(x, d));
  const t = rs.reduce((a,x)=>({rr:a.rr+x.rereads, j:a.j+x.junk, rt:a.rt+x.reread_tok, jt:a.jt+x.junk_tok, n:a.n+x.reads}), {rr:0,j:0,rt:0,jt:0,n:0});
  const tok = t.rt + t.jt;
  if(tok < 20000) return null;
  // each day × project's wasted tokens at the cache-write rate that day's own requests
  // there paid (its models, its 5-minute / 1-hour mix), not one model's rate for all
  const recs = d.recs.filter(r => famOf(r.source)==="claude" && r.model!=="(user)");
  const at = {};
  for(const r of recs){ const k = r.date+"\t"+r.project; (at[k] = at[k] || []).push(r); }
  const anyRate = cacheWriteRate(recs);
  const impact = rs.reduce((a,x) => a + (x.reread_tok + x.junk_tok)
    * (at[x.date+"\t"+x.project] ? cacheWriteRate(at[x.date+"\t"+x.project]) : anyRate), 0);
  if(impact < 0.5) return null;
  return {
    id:"read-waste", impact, sev:"low", tools:["claude"],
    title:`${fmtTok(tok)} tokens of reads that added nothing new`,
    body:`${fmtNum(t.rr)} of ${fmtNum(t.n)} file reads re-read a file that hadn't changed since the last `
      +`read (${fmtTok(t.rt)} tokens), and ${fmtNum(t.j)} read generated or dependency folders — `
      +`node_modules, dist, build, lockfiles (${fmtTok(t.jt)} tokens). Each stays in context for the rest `
      +`of the session.`,
    todo:"Point the agent at source, not build output: add those folders to .gitignore / a "
      +"permissions deny list, and ask for a specific range when a file is large.",
    rows: []
  };
}

/* Installed skills and subagents are described in every session's context whether
   used or not. Only judged over at least two weeks, so a quiet week isn't "unused". */
function findUnusedInstalled(d){
  const inst = (RAW.installed||{}).items||[];
  if(!inst.length || !passSrc("claude") || rangeDays() < 14) return null;
  const norm = x => String(x||"").toLowerCase().replace(/^.*:/,"");
  const used = new Set();
  for(const u of (RAW.installed.used||[])) if(inRangeD(u, d)) used.add(u.kind+"\t"+norm(u.name));
  for(const x of (RAW.skills||[])) if(isLocal(x) && inRangeD(x, d)) used.add("skill\t"+norm(x.name));
  const idle = inst.filter(i => !used.has(i.kind+"\t"+norm(i.name)));
  if(!idle.length) return null;
  const {n, per} = perTokenCost(d, r => famOf(r.source)==="claude", false);
  const tok = idle.reduce((a,i)=>a+i.desc_chars/4, 0);
  const impact = tok * per;
  return {
    id:"unused-installed", impact, sev:"info", tools:["claude"],
    title:`${idle.length} installed skill${idle.length>1?"s":""} or agent${idle.length>1?"s":""} never used in this range`,
    body:`Each installed skill and subagent is listed, with its description, in every Claude Code session — `
      +`about ${fmtTok(tok)} tokens for these ${idle.length} across ${fmtNum(n)} requests.`,
    todo:"Remove the ones you don't reach for from ~/.claude/skills and ~/.claude/agents (or the "
      +"project's .claude/), or move project-specific ones into that project.",
    rows: idle.slice(0,8).map(i => [i.name, i.kind, i.scope==="user"?"all projects":i.project,
      i.desc_chars ? `~${fmtTok(i.desc_chars/4)} tokens` : "no description"])
  };
}

/* MCP servers whose tools are mostly unused, and servers used in one project but
   loaded in many — both from what Claude Code offered each session. */
function findMcpShape(d){
  const inv = RAW.mcp_inventory; if(!inv || !passSrc("claude")) return null;
  const loaded = {}, calls = {}, projL = {}, projC = {};
  for(const x of inv.loaded) if(inRangeD(x, d)){ loaded[x.server]=(loaded[x.server]||0)+x.sessions;
    (projL[x.server]=projL[x.server]||new Set()).add(x.project); }
  for(const x of (inv.calls||[])) if((!x.source || x.source==="claude") && inRangeD(x, d)){ calls[x.server]=(calls[x.server]||0)+x.calls;
    (projC[x.server]=projC[x.server]||new Set()).add(x.project); }
  const usedTools = {};
  const names=new Set();
  for(const x of (inv.calls||[])) if(x.source==="claude" && inRangeD(x,d))
    for(const name of x.tools||[]) names.add(name);
  if(!(inv.calls||[]).every(x=>x.source))
    for(const t of toolCounts(d.r,x=>isLocal(x)&&x.source==="claude")) if(t.name.startsWith("mcp__")) names.add(t.name);
  for(const name of names){ const sv=name.slice(5).split("__")[0];usedTools[sv]=(usedTools[sv]||0)+1; }
  const rows = [];
  for(const sv in loaded){
    const offered = ((inv.servers[sv]||{}).tools||[]).length, used = usedTools[sv]||0;
    const nl = (projL[sv]||new Set()).size, nc = (projC[sv]||new Set()).size;
    if(loaded[sv] >= 3 && offered >= 10 && used && used/offered <= 0.1)
      rows.push([sv, `${used} of ${offered} tools used`, `${fmtNum(loaded[sv])} sessions`, "mostly unused"]);
    else if(nc === 1 && nl >= 3 && calls[sv])
      rows.push([sv, `used only in ${[...projC[sv]][0]}`, `loaded in ${nl} projects`, "scope it"]);
  }
  if(!rows.length) return null;
  return {
    id:"mcp-shape", impact:0, sev:"info", tools:["claude"],
    title:`${rows.length} MCP server${rows.length>1?"s":""} loaded far more widely than they're used`,
    body:`Every session a server is loaded in starts it and offers its tools to the model. These are either `
      +`used for a sliver of what they offer, or only ever used in one project while loaded in several.`,
    todo:"For a one-project server, move it from ~/.claude.json into that project's .mcp.json. For a "
      +"mostly-unused one, check whether a lighter server or a plain CLI covers the few tools you call.",
    rows
  };
}

/* The setup checks judge THIS machine's files (instruction files, installed skills,
   MCP config), so only this machine's usage is measured against them — another
   device has its own files, which never leave it. */
const isLocal = x => !x.device || !RAW.device || x.device === RAW.device.id;
function localSlice(d){
  return Object.assign({}, d, {recs:d.recs.filter(isLocal), sessions:d.sessions.filter(isLocal)});
}
const onThisDevice = fn => d => {
  if(RAW.device && !passDev(RAW.device.id)) return null;
  const f=fn(localSlice(d));if(f) f.local=true;return f;
};
const OPT_FINDERS = [findBigContext, findCacheWaste, findModelFit, findContextTax,
                     findSubagents, findSubagentShare, findSkills, findCodexEffort,
                     onThisDevice(d => findIdleMCP(d, "claude")), onThisDevice(d => findIdleMCP(d, "codex")),
                     findThinking, findLowCache, findRetryTax, onThisDevice(findInstructionFiles),
                     findHeavyOpeners, findReadWaste, onThisDevice(findUnusedInstalled),
                     onThisDevice(findMcpShape)];

/* Say plainly when a suggestion only applies to one tool — the MCP, Skills and
   /compact advice is Claude Code's, and a Codex or Cursor user should not read it
   as advice about their own setup. Nothing is labelled when it spans every tool
   present in the range, because then the label is noise. */
/* The left stripe carries TOOL identity, using the very same --t-<source> tokens as
   every chart and badge in the app — so orange still means Claude Code and green
   still means Codex here. It is only painted when exactly one tool is in scope;
   a finding spanning several has no single owner and stays neutral.

   It deliberately does NOT encode severity any more. --accent is byte-identical to
   --t-opencode and --warn sits on top of --t-claude-desktop, so a severity stripe
   was painting findings in tool colours that had nothing to do with the tool. */
function stripeClass(f){
  const t=(f.tools||[]).filter(Boolean);
  return t.length===1 && SRC[t[0]] ? "" : " sev-neutral";
}
function stripeStyle(f){
  const t=(f.tools||[]).filter(Boolean);
  if(t.length!==1 || !SRC[t[0]]) return "";
  return ` style="border-left-color:var(${SRC[t[0]].v})"`;
}

function scopeLabel(f){
  const chips = [];
  const t = (f.tools||[]).filter(Boolean);
  const present = new Set(SLICE_SOURCES);
  if(t.length && !(t.length >= present.size && [...present].every(x=>t.includes(x)))){
    const names = t.map(x=>(SRC[x]||{label:x}).label);
    // "X only" reads right for a single tool; for several it is nonsense ("A only,
    // B only"), so just name them.
    chips.push(names.length === 1 ? names[0] + " only" : names.join(" · "));
  }
  // a setup check reads this machine's files; with other devices in the data, say so
  if(f.local && multiDevice()) chips.push(deviceName(RAW.device.id) + " only");
  return chips.length ? `<div class="finding-scope">${chips.map(c=>
    `<span class="scope-chip">${esc(c)}</span>`).join(" ")}</div>` : "";
}
let SLICE_SOURCES=[];

function runFinders(d){
  SLICE_SOURCES=[...new Set(d.recs.map(r=>r.source))];
  return OPT_FINDERS.map(f => { try{ return f(d); }catch(e){ console.error("finder failed", e); return null; } })
                    .filter(Boolean).sort((a,b) => b.impact - a.impact);
}
function viewOptimize(d){
  const found = runFinders(d);
  const totalSpend = d.recs.reduce((a,r)=>a+(r.cost||0),0);
  // Findings overlap — the same long session feeds "large context", "never delegated"
  // and "thinking share" — so their savings are NOT additive. Lead with the largest.
  const best = found.filter(f=>f.impact>0.5)[0];
  document.getElementById("optHero").innerHTML = `
    <div class="hero-stat" style="min-width:240px"><div class="hero-label">Largest single saving</div>
      <div class="hero-value">${best?fmtUSD(best.impact):"—"}</div>
      <div class="hero-delta"><span>${best?`${fmtPct(best.impact/(totalSpend||1))} of ${fmtUSD(totalSpend)} spent in range · estimates overlap, so they don't add up`:"nothing with a dollar figure in this range"}</span></div></div>
    <div class="hero-stat"><div class="l">Findings</div><div class="v">${found.length}</div><div class="s">${found.length?"ranked by estimated saving":"nothing to flag"}</div></div>
    <div class="hero-stat"><div class="l">Sessions analysed</div><div class="v">${fmtNum(d.sessions.length)}</div><div class="s">from your own logs</div></div>`;
  const host = document.getElementById("optFindings");
  if(!found.length){
    host.innerHTML = `<div class="card"><div class="empty">Nothing worth flagging in this range — your cache hit
      rate, model mix and session lengths all look reasonable.</div></div>`;
    return;
  }
  host.innerHTML = found.map(f => `
    <div class="finding${stripeClass(f)}"${stripeStyle(f)}>
      <div class="finding-head">
        <div><div class="finding-title">${f.title}</div>${scopeLabel(f)}</div>
        ${f.impact > 0.5 ? `<div class="finding-impact num">${fmtUSD(f.impact)}<span>est. saving</span></div>` : ""}
      </div>
      <div class="finding-body">${f.body}</div>
      <div class="finding-todo"><b>What to do</b>${f.todo}</div>
      ${f.rows && f.rows.length ? `<table class="finding-tbl"><tbody>${
        f.rows.map(r=>`<tr>${r.map((c,i)=>
          `<td${i===0?' class="name"':(i===r.length-1?' class="r"':' class="dim"')}>${esc(c)}</td>`
        ).join("")}</tr>`).join("")}</tbody></table>` : ""}
    </div>`).join("");
}

/* ---------------- dispatch ---------------- */
const VIEW_HTML={};
function renderAll(){
  if(!RAW) return;
  // model colour ranking: siblings of a provider separate by lightness
  const tot={};
  for(const r of RAW.records){ if(r.model==="(user)") continue; tot[r.model]=(tot[r.model]||0)+recTokens(r); }
  MODEL_RANK={};
  Object.entries(tot).sort((a,b)=>b[1]-a[1]).forEach(([m])=>{
    const p=providerOf(m); (MODEL_RANK[p]=MODEL_RANK[p]||[]).push(m); });

  syncRangeUI(); syncMetricUI(); buildFilterPanels(); renderPills();
  filtersToURL();
  document.querySelectorAll(".view").forEach(v=>v.classList.toggle("on", v.id==="v-"+S.view));
  document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on", b.dataset.v===S.view));
  document.getElementById("pageTitle").textContent = VIEW_TITLES[S.view] || "Overview";
  document.title = (S.view==="overview" ? "" : (VIEW_TITLES[S.view]||"") + " · ") + "AgentTelemetry";
  const d = slice();
  const host = document.getElementById("v-"+S.view);
  if(!VIEW_HTML[S.view]) VIEW_HTML[S.view] = host.innerHTML;
  let nFound = 0;
  try{ nFound = runFinders(d).length; }catch(e){}
  document.getElementById("optCount").textContent = nFound || "";
  const empty = !d.recs.length && !d.sessions.length;
  if(empty && S.view!=="storage"){
    host.innerHTML = `<div class="grid"><div class="card col-12"><div class="empty">
      No activity for this period and filter combination.</div></div></div>`;
    host.dataset.emptied = "1";
    return;
  }
  if(host.dataset.emptied==="1"){ host.innerHTML = VIEW_HTML[S.view]; host.dataset.emptied="0"; syncSearchBoxes(); }
  try{
    if(S.view==="overview") viewOverview(d);
    else if(S.view==="cost") viewCost(d);
    else if(S.view==="models") viewModels(d);
    else if(S.view==="tools") viewTools(d);
    else if(S.view==="projects") viewProjects(d);
    else if(S.view==="sessions") viewSessions(d);
    else if(S.view==="optimize") viewOptimize(d);
    else if(S.view==="storage") viewStorage();
  }catch(err){
    console.error("render failed", err);
    document.documentElement.dataset.jsError = "render "+S.view+": "+(err&&err.message||err);
  }
}
function syncSearchBoxes(){ document.querySelectorAll(".search-in").forEach(x=>{ if(x.value!==S.search) x.value=S.search; }); }
function setView(v){
  if(!VIEW_TITLES[v]) v = "overview";
  S.view = v;
  if(location.hash.slice(1) !== v) history.replaceState(null, "", "#"+v);
  if(v==="storage" && !STORAGE) loadStorage();
  renderAll();
  scrollTo({top:0});
}

/* ---------------- data ---------------- */
async function load(){
  try{
    const r=await fetch("/api/data"); RAW=await r.json();
  }catch(e){
    console.error(e);
    document.getElementById("statusText").innerHTML='<span style="color:var(--bad)">cannot reach /api/data — is dashboard.py running?</span>';
    document.getElementById("livedot").classList.add("off");
    return;
  }
  // a render failure is a bug, not a dead server — say so, and let the headless
  // sweep see it (data-js-error) instead of reporting "cannot reach"
  try{
    const dates=RAW.records.map(x=>x.date).filter(x=>x!=="0000-00-00");
    const lo=dates.length?dates.reduce((a,b)=>a<b?a:b):"—";
    const hi=dates.length?dates.reduce((a,b)=>a>b?a:b):"—";
    const fresh=new Date(RAW.meta.last_refresh*1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
    document.getElementById("statusText").textContent = (S.live ? "Live" : "Paused") + " · updated " + fresh;
    document.getElementById("coverage").textContent = `${fmtNum(RAW.meta.files)} logs · ${lo} → ${hi}`
      + (RAW.openclaw_undecoded ? ` · ${fmtNum(RAW.openclaw_undecoded)} OpenClaw events unread — install zstd` : "");
    if(RAW.cache_error) document.getElementById("coverage").textContent += " · " + RAW.cache_error;
    renderVersion();
    const dev = RAW.device || {};
    document.getElementById("devName").textContent = dev.name || "This computer";
    const others = devices().filter(v=>!v.local);
    document.getElementById("devOs").textContent = others.length
      ? `+ ${others.length} other device${others.length>1?"s":""}` : (dev.os || "");
    document.getElementById("device").title = [dev.name, dev.host, dev.os].filter(Boolean).join(" · ")
      + others.map(v=>`\n+ ${v.name}${v.synced?" · synced "+ago(v.synced):""}${v.error?" · "+v.error:""}`).join("");
    // a device that was disconnected can't stay selected, or it would filter out everything
    for(const id of [...S.devs]) if(!devices().some(v=>v.id===id)) S.devs.delete(id);
    document.getElementById("pricingNote").textContent=RAW.pricing_note;
    renderAll();
  }catch(e){
    console.error(e);
    document.documentElement.dataset.jsError = "load: " + (e && e.message || e);
    document.getElementById("statusText").innerHTML='<span style="color:var(--bad)">display error — see the console</span>';
  }
}
async function loadStorage(){
  try{ const r=await fetch("/api/storage"); STORAGE=await r.json();
    if(S.view==="storage") renderAll(); }catch(e){ console.error(e); }
}

/* ---------------- events ---------------- */
document.getElementById("tabs").addEventListener("click",e=>{
  const b=e.target.closest("button[data-v]"); if(b) setView(b.dataset.v);
});
document.querySelector(".brand").addEventListener("click",e=>{ e.preventDefault(); setView("overview"); });
addEventListener("hashchange",()=>{ const v=location.hash.slice(1); if(v!==S.view && VIEW_TITLES[v]) setView(v); });
document.getElementById("stepBack").addEventListener("click",()=>stepPeriod(-1));
document.getElementById("stepFwd").addEventListener("click",()=>stepPeriod(1));
document.getElementById("rangePanel").addEventListener("click",e=>{
  const it=e.target.closest("[data-preset]");
  if(it){ S.preset=it.dataset.preset; closeDD(); renderAll(); return; }
  if(e.target.closest("[data-apply]")){
    const a=document.getElementById("dFrom").value, b=document.getElementById("dTo").value;
    if(a&&b){ S.preset="custom"; S.from=a<b?a:b; S.to=a<b?b:a; closeDD(); renderAll(); }
  }
});
document.getElementById("metricPanel").addEventListener("click",e=>{
  const b=e.target.closest("button[data-m]"); if(!b) return;
  S.metric=b.dataset.m; closeDD(); renderAll();
});
document.getElementById("rateSeg").addEventListener("click",e=>{
  const b=e.target.closest("button[data-r]"); if(!b) return;
  S.rateMetric=b.dataset.r; renderAll();
});
function wireMulti(panelId, get, isTools){
  document.getElementById(panelId).addEventListener("click",e=>{
    const set=get();
    const it=e.target.closest(".dd-item[data-k]"); if(!it) return;
    e.preventDefault();
    const k=it.dataset.k;
    if(e.target.closest("[data-only]")){ set.clear(); set.add(k); renderAll(); return; }
    if(isTools){ if(set.has(k)){ if(set.size>1) set.delete(k); } else set.add(k); }
    else if(!set.size){ // "no filter" = everything on: unticking one keeps all the others
      for(const x of it.parentElement.querySelectorAll(".dd-item[data-k]")) if(x.dataset.k!==k) set.add(x.dataset.k);
    }
    else if(set.has(k)) set.delete(k); else set.add(k);
    renderAll();
  });
}
wireMulti("toolsPanel",()=>S.tools,true);
wireMulti("provPanel",()=>S.provs,false);
wireMulti("modelPanel",()=>S.models,false);
wireMulti("projPanel",()=>S.projs,false);
wireMulti("idePanel",()=>S.ides,false);
wireMulti("devPanel",()=>S.devs,false);
document.getElementById("filtersPanel").addEventListener("click",e=>{
  const a=e.target.closest("[data-fall],[data-fnone],[data-fclear]"); if(!a) return;
  e.preventDefault();
  if(a.dataset.fall) S.tools=new Set(ORDER);
  else if(a.dataset.fnone){ S.tools.clear(); S.tools.add(ORDER.find(s=>RAW.records.some(r=>r.source===s))||ORDER[0]); }
  else S[a.dataset.fclear].clear();
  renderAll();
});
document.getElementById("reliableBtn").addEventListener("change",e=>{ S.exactOnly=e.target.checked; renderAll(); });
document.getElementById("filtersReset").addEventListener("click",()=>{ resetFilters(); closeDD(); renderAll(); });
document.getElementById("pills").addEventListener("click",e=>{
  const b=e.target.closest("[data-pill]"); if(!b) return;
  const k=b.dataset.pill;
  if(k==="__all") resetFilters();
  else if(k==="tools") S.tools=new Set(ORDER);
  else if(k==="exact") S.exactOnly=false;
  else if(k==="search"){ S.search=""; syncSearchBoxes(); }
  else if(k.startsWith("prov:")) S.provs.delete(k.slice(5));
  else if(k.startsWith("proj:")) S.projs.delete(k.slice(5));
  else if(k.startsWith("ide:")) S.ides.delete(k.slice(4));
  else if(k.startsWith("dev:")) S.devs.delete(k.slice(4));
  else if(k.startsWith("model:")) S.models.delete(k.slice(6));
  renderAll();
});
let searchT=null;
document.addEventListener("input",e=>{
  if(!e.target.classList.contains("search-in")) return;
  clearTimeout(searchT); const v=e.target.value;
  searchT=setTimeout(()=>{ S.search=v.trim(); syncSearchBoxes(); renderAll(); },220);
});
document.getElementById("themeBtn").addEventListener("click",cycleTheme);
document.getElementById("settingsBtn").addEventListener("click",openSettings);
document.getElementById("refreshBtn").addEventListener("click",async e=>{
  const b=e.currentTarget; b.style.opacity=".4";
  try{ await fetch("/api/refresh",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}); await load(); await loadStorage(); } finally { b.style.opacity=""; }
});
document.getElementById("liveBtn").addEventListener("click",e=>{
  S.live=!S.live;
  document.getElementById("livedot").classList.toggle("off",!S.live);
  e.currentTarget.title = S.live ? "Auto-refresh on — click to pause" : "Auto-refresh paused — click to resume";
  const st=document.getElementById("statusText"); st.textContent = st.textContent.replace(/^(Live|Paused)/, S.live?"Live":"Paused");
});
document.addEventListener("click",e=>{
  const tv=e.target.closest(".tv[data-tv]");
  if(tv){ const id=tv.dataset.tv; if(S.tableView.has(id)) S.tableView.delete(id); else S.tableView.add(id);
    applyTableView(id); return; }
  const go=e.target.closest("[data-go]");
  if(go){ e.preventDefault(); setView(go.dataset.go); return; }
  const lg=e.target.closest(".li[data-lg]");
  if(lg){ const id=lg.dataset.lg, k=lg.dataset.k;
    S.muted[id]=S.muted[id]||new Set();
    if(S.muted[id].has(k)) S.muted[id].delete(k); else S.muted[id].add(k);
    renderAll(); return; }
  const th=e.target.closest("th[data-k]");
  if(th){ const k=th.dataset.k, t=th.dataset.t;
    const st={sess:S.sessSort,model:S.modelSort,tool:S.toolSort,proj:S.projSort,file:S.fileSort,eff:S.effSort}[t];
    if(st){ st.dir = st.key===k ? -st.dir : -1; st.key=k; renderAll(); } return; }
  const pr=e.target.closest("[data-proj]");
  if(pr && !pr.closest(".dd-panel")){ S.projs.clear(); S.projs.add(pr.dataset.proj); setView("sessions"); return; }
  const sr=e.target.closest("tr[data-sess]");
  if(sr){ openSession(+sr.dataset.sess); return; }
  const mr=e.target.closest("tr[data-model]");
  if(mr){ const m=mr.dataset.model;
    if(S.modelExpanded.has(m)) S.modelExpanded.delete(m); else S.modelExpanded.add(m);
    renderAll(); return; }
});
document.getElementById("drawerX").addEventListener("click",closeDrawer);
document.getElementById("scrim").addEventListener("click",closeDrawer);
addEventListener("scroll",()=>{ document.getElementById("filters").classList.toggle("stuck", scrollY>8); },{passive:true});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change",()=>{
  let m="auto"; try{ m=localStorage.getItem("aiu.theme")||"auto"; }catch(e){}
  if(m==="auto") applyTheme("auto");
});

/* ---------------- boot ---------------- */
const QP=new URLSearchParams(location.search);
{ let saved="auto"; try{ saved=localStorage.getItem("aiu.theme")||"auto"; }catch(e){}
  applyTheme(QP.get("theme")||saved); }
filtersFromURL(); syncSearchBoxes();
if(location.hash && VIEW_TITLES[location.hash.slice(1)]) S.view=location.hash.slice(1);
load().then(()=>{ if(S.view==="storage") loadStorage(); });
setTimeout(loadStorage, 1200);
/* Poll interval is user-configurable (Settings). The payload is ~1MB, so a
   tighter loop is real CPU and disk churn; 0 means "only when I press refresh". */
let pollTimers=[];
function clearPollTimers(){ pollTimers.forEach(clearInterval); pollTimers=[]; }
function applyPollInterval(){
  clearPollTimers();
  const ms=pollMs();
  if(!ms || (NATIVE_APP_MODE && document.visibilityState==="hidden")) return;
  pollTimers.push(setInterval(()=>{ if(S.live) load(); }, ms));
  pollTimers.push(setInterval(()=>{ if(S.live) loadStorage(); }, Math.max(ms*8,120000)));
}
applyPollInterval();
if(NATIVE_APP_MODE) document.addEventListener("visibilitychange",()=>{
  applyPollInterval();
  if(document.visibilityState==="visible"){
    load();
    loadStorage();
  }
});
