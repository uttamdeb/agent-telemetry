/* ==========================================================================
   core.js — constants, state, formatting, date ranges, filtering, controls.
   Loaded before charts.js / views.js; everything here is intentionally global.
   ========================================================================== */

/* Tool identity. ORDER is also the categorical-hue order that was validated
   for colour-blind separation (adjacent pairs, light + dark) — see app.css. */
const SRC = {
  claude:           {label:"Claude Code",    v:"--t-claude",          exact:true, icon:"claude", provider:"Anthropic", mono:true},
  codex:            {label:"Codex",          v:"--t-codex",           exact:true, icon:"openai", provider:"OpenAI", mono:true},
  copilot:          {label:"Copilot",        v:"--t-copilot",         exact:false, icon:"copilot", provider:"GitHub", mono:true},
  cursor:           {label:"Cursor",         v:"--t-cursor",          exact:false, icon:"cursor", provider:"Cursor", mono:true},
  "claude-desktop": {label:"Claude Desktop", v:"--t-claude-desktop",  exact:true, icon:"claude", provider:"Anthropic", mono:true},
  opencode:         {label:"OpenCode",       v:"--t-opencode",        exact:true, icon:"opencode", provider:"OpenCode", mono:true},
  hermes:           {label:"Hermes Agent",   v:"--t-hermes",          exact:true, icon:"hermes", provider:"Nous Research"},
  openclaw:         {label:"OpenClaw",       v:"--t-openclaw",        exact:true, icon:"openclaw", provider:"OpenClaw"},
  antigravity:      {label:"Antigravity",    v:"--t-antigravity",     exact:true, icon:"antigravity", provider:"Google", mono:true},
};
// adjacency is what the palette validator checks — openclaw's red sits between amber
// and blue, never beside hermes's olive (red/green fails deutan CVD in dark mode)
const ORDER = ["claude","codex","copilot","cursor","claude-desktop","openclaw","opencode","hermes","antigravity"];
const PROVIDERS = ["Anthropic","OpenAI","Google","Other"];
const PROV_VAR = {Anthropic:"--p-anthropic",OpenAI:"--p-openai",Google:"--p-google",Other:"--p-other"};
const DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

/* ---------- css variable access (theme-aware) ---------- */
function cssv(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function srcColor(s){ return cssv((SRC[s]||{}).v || "--text-3"); }
function provColor(p){ return cssv(PROV_VAR[p] || "--p-other"); }
function seqRamp(){ return [0,1,2,3,4,5,6].map(i=>cssv("--seq-"+i)); }
/* A model gets its provider's hue; sibling models are separated by lightness,
   never by an invented hue (identity stays with the provider). */
function modelColor(model){
  const prov = (RAW && RAW.model_vendor && RAW.model_vendor[model]) || "Other";
  const base = provColor(prov);
  const sibs = MODEL_RANK[prov] || [];
  const i = Math.max(0, sibs.indexOf(model));
  return shade(base, i);
}
let MODEL_RANK = {};
function shade(hex, step){
  if(step<=0) return hex;
  const m = hex.replace("#","");
  let r=parseInt(m.slice(0,2),16), g=parseInt(m.slice(2,4),16), b=parseInt(m.slice(4,6),16);
  // alternate lighter / darker so sibling models of one provider stay separable
  // without walking off the end of the lightness band in one direction
  const f = Math.min(0.52, 0.22 + (Math.ceil(step/2)-1)*0.19);
  if(step % 2 === 1){ r+=(255-r)*f; g+=(255-g)*f; b+=(255-b)*f; }
  else             { r*=(1-f);      g*=(1-f);      b*=(1-f); }
  const h=n=>Math.round(n).toString(16).padStart(2,"0");
  return "#"+h(r)+h(g)+h(b);
}

/* ---------- formatting ---------- */
const pad2 = n => (""+n).padStart(2,"0");
const dkey = d => d.getFullYear()+"-"+pad2(d.getMonth()+1)+"-"+pad2(d.getDate());
const fmtTok = n => { n=n||0;
  if(n>=1e9) return (n/1e9).toFixed(2)+"B";
  if(n>=1e6) return (n/1e6).toFixed(1)+"M";
  if(n>=1e3) return (n/1e3).toFixed(1)+"K";
  return ""+Math.round(n); };
const fmtUSD = n => { n=n||0;
  if(n>=100) return "$"+Math.round(n).toLocaleString();
  if(n>0 && n<0.01) return "<$0.01";
  return "$"+n.toFixed(2); };
/* Axis ticks only — a tick has no room for "$1,752", and the tooltip carries the exact figure. */
const fmtUSDk = n => { n=n||0;
  if(n>=1e6) return "$"+(n/1e6).toFixed(1)+"M";
  if(n>=1000) return "$"+(n/1000).toFixed(n>=1e4?0:1)+"k";
  if(n>=10) return "$"+n.toFixed(0);
  return "$"+(+n.toFixed(2)); };
const fmtUSD2 = n => "$"+(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtNum = n => (n||0).toLocaleString();
const fmtPct = n => (n*100).toFixed(n<0.1?1:0)+"%";
/* Seconds -> a duration string, same tiering style as fmtTok. This is ACTIVE
   time (gap-capped, see parser.py's ACTIVE_GAP_CAP) — a lower bound on time
   actually spent driving the tool, not wall-clock session length. */
const fmtDur = secs => { secs=secs||0;
  if(secs<60) return Math.round(secs)+"s";
  const m=secs/60;
  if(m<90) return Math.round(m)+"m";
  const h=m/60;
  if(h<48) return h.toFixed(1)+"h";
  return (h/24).toFixed(1)+"d"; };
const fmtBytes = n => { n=n||0;
  if(n>=1e9) return (n/1e9).toFixed(2)+" GB";
  if(n>=1e6) return (n/1e6).toFixed(0)+" MB";
  if(n>=1e3) return (n/1e3).toFixed(0)+" KB";
  return n+" B"; };
/* A unix time (seconds) as "just now", "4m ago", "3h ago", "2d ago". */
const ago = t => { const s = Math.max(0, Date.now()/1000 - (t||0));
  return s<45 ? "just now" : s<3600 ? Math.round(s/60)+"m ago"
       : s<172800 ? Math.round(s/3600)+"h ago" : Math.round(s/86400)+"d ago"; };
const esc = s => (s==null?"":(""+s)).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
/* Tool identity comes from the source, never the model it happens to run. */
function toolIcon(source,decorative=false){
  const item=SRC[source];
  if(!item) return decorative ? "" : `<span class="tool-icon tool-icon-unknown" role="img" aria-label="${esc(source)}" title="${esc(source)}">?</span>`;
  const label=item.label+" · "+item.provider;
  return `<span class="tool-icon${item.mono?' mono':''}" title="${esc(label)}" ${decorative?'aria-hidden="true"':`role="img" aria-label="${esc(label)}"`}><img src="/static/tool-icons/${item.icon}.svg" width="20" height="20" alt="" decoding="async"></span>`;
}
function toolLabel(source){
  return `<span class="tool-label">${toolIcon(source,true)}${esc((SRC[source]||{label:source}).label)}</span>`;
}
const recTokens = r => (r.in||0)+(r.out||0)+(r.cr||0)+(r.cc||0);
const ctxTokens = r => (r.in||0)+(r.cr||0)+(r.cc||0);

/* ---------- optional extensions ----------
   A plain <script src> placed between core.js and views.js may add a view or a Settings
   panel. Nothing registers in this repository, so both lists stay empty and every loop
   over them is a no-op. views.js reads EXT when it loads and on every render.
     view:  {id, title, label, icon (inner SVG markup, 16x16 viewBox), after (id of an
            existing view to follow), html (the section's markup), render(d), badge(d)}
     panel: {html(), begin() -> token, current(token), close(), load()} */
const EXT = {views:[], panels:[]};
function registerView(v){
  if(!v || typeof v.id!=="string" || !/^[a-z][a-z0-9-]*$/.test(v.id)
      || typeof v.title!=="string" || typeof v.render!=="function"
      || EXT.views.some(x=>x.id===v.id) || document.getElementById("v-"+v.id)) return false;
  const tabs = document.getElementById("tabs");
  const first = document.querySelector(".view");
  if(!tabs || !first) return false;
  EXT.views.push(v);
  const button = document.createElement("button");
  button.dataset.v = v.id;
  button.innerHTML = `<svg class="ic" viewBox="0 0 16 16" aria-hidden="true">${v.icon||""}</svg>`
    + `<span>${esc(v.label||v.title)}</span>`
    + (typeof v.badge==="function" ? `<span class="nav-count" id="navCount-${v.id}"></span>` : "");
  const after = tabs.querySelector(`button[data-v="${v.after}"]`);
  if(after) after.after(button); else tabs.appendChild(button);
  const section = document.createElement("section");
  section.className = "view";
  section.id = "v-"+v.id;
  section.innerHTML = v.html || "";
  const storage = document.getElementById("v-storage");
  if(storage) storage.before(section); else first.parentNode.appendChild(section);
  return true;
}
function registerSettingsPanel(p){
  if(!p || typeof p.html!=="function") return false;
  EXT.panels.push(p);
  return true;
}

/* ---------- state ---------- */
let RAW = null, STORAGE = null;
const S = {
  view:"overview",
  preset:"30d", from:null, to:null,
  compare:false,
  tools:new Set(ORDER),
  provs:new Set(),      // empty = all
  projs:new Set(),
  models:new Set(),
  ides:new Set(),        // which IDE / surface the work ran in
  devs:new Set(),        // which device (only offered once another one is connected)
  search:"",
  sessionType:"all",    // session-list-only classification; never changes shared slices
  exactOnly:false,
  metric:"tokens", rateMetric:"all",   // tokens lead; cost is an estimate
  tableView:new Set(),   // chart ids currently shown as their table twin
  live:true,
  muted:{},             // chartId -> Set of muted series labels
  sessSort:{key:"end",dir:-1}, modelSort:{key:"cost",dir:-1},
  toolSort:{key:"count",dir:-1}, effSort:{key:"turns",dir:-1}, projSort:{key:"tokens",dir:-1},
  fileSort:{key:"bytes",dir:-1},
  modelExpanded:new Set(),  // model names expanded to show their per-tool breakdown
};

const PRESETS = [
  ["today","Today"],["yesterday","Yesterday"],["7d","Last 7 days"],["14d","Last 14 days"],
  ["30d","Last 30 days"],["90d","Last 90 days"],["180d","Last 180 days"],["365d","Last 365 days"],
  ["wtd","This week"],["mtd","This month"],["qtd","This quarter"],["ytd","This year"],
  ["lastmonth","Last month"],["all","All time"],
];
function presetLabel(p){ const f=PRESETS.find(x=>x[0]===p); return f?f[1]:"Custom"; }

function dataFloor(){
  if(!RAW || !RAW.records.length) return "2000-01-01";
  let lo="9999"; for(const r of RAW.records) if(r.date!=="0000-00-00" && r.date<lo) lo=r.date;
  return lo==="9999"?"2000-01-01":lo;
}
/* Resolve the active window to inclusive local-time YYYY-MM-DD bounds. */
function range(){
  const today = new Date(); today.setHours(0,0,0,0);
  const back = n => { const d=new Date(today); d.setDate(d.getDate()-n); return dkey(d); };
  const p = S.preset;
  if(p==="custom") return {from:S.from||dataFloor(), to:S.to||dkey(today)};
  if(p==="today") return {from:dkey(today), to:dkey(today)};
  if(p==="yesterday") return {from:back(1), to:back(1)};
  if(p==="all") return {from:dataFloor(), to:dkey(today)};
  if(p==="wtd"){ const d=new Date(today); const wd=(d.getDay()+6)%7; d.setDate(d.getDate()-wd); return {from:dkey(d),to:dkey(today)}; }
  if(p==="mtd"){ const d=new Date(today.getFullYear(),today.getMonth(),1); return {from:dkey(d),to:dkey(today)}; }
  if(p==="qtd"){ const d=new Date(today.getFullYear(),Math.floor(today.getMonth()/3)*3,1); return {from:dkey(d),to:dkey(today)}; }
  if(p==="ytd"){ const d=new Date(today.getFullYear(),0,1); return {from:dkey(d),to:dkey(today)}; }
  if(p==="lastmonth"){ const a=new Date(today.getFullYear(),today.getMonth()-1,1),
                             b=new Date(today.getFullYear(),today.getMonth(),0); return {from:dkey(a),to:dkey(b)}; }
  const n = parseInt(p,10)||30;
  return {from:back(n-1), to:dkey(today)};
}
function prevRange(){
  const r = range();
  const a = new Date(r.from+"T00:00:00"), b = new Date(r.to+"T00:00:00");
  const days = Math.round((b-a)/86400000)+1;
  const pb = new Date(a); pb.setDate(pb.getDate()-1);
  const pa = new Date(pb); pa.setDate(pa.getDate()-(days-1));
  return {from:dkey(pa), to:dkey(pb), days};
}
function rangeDays(){ const r=range();
  return Math.round((new Date(r.to+"T00:00:00")-new Date(r.from+"T00:00:00"))/86400000)+1; }
function dateList(r){
  const out=[]; const d=new Date(r.from+"T00:00:00"), end=new Date(r.to+"T00:00:00");
  while(d<=end){ out.push(dkey(d)); d.setDate(d.getDate()+1); if(out.length>1200)break; }
  return out;
}

/* ---------- filtering ---------- */
const providerOf = m => (RAW && RAW.model_vendor && RAW.model_vendor[m]) || "Other";
function passSrc(s){ return S.tools.has(s) && (!S.exactOnly || (SRC[s]||{}).exact || s==="copilot"); }
function passModel(m){
  if(m==="(user)") return true;                       // user-turn marker rows
  if(S.provs.size && !S.provs.has(providerOf(m))) return false;
  if(S.models.size && !S.models.has(m)) return false;
  return true;
}
function passProj(p){ return !S.projs.size || S.projs.has(p||"(unknown)"); }
function canonicalProjectFilters(){
  const aliases=RAW?.project_aliases;
  if(!aliases || typeof aliases!=="object" || Array.isArray(aliases)) return;
  S.projs=new Set([...S.projs].map(original=>{
    let value=original;
    const seen=new Set();
    while(Object.prototype.hasOwnProperty.call(aliases,value) && typeof aliases[value]==="string"){
      if(seen.has(value) || seen.size>=100) return original;
      seen.add(value); value=aliases[value];
    }
    return value;
  }));
}
function passIde(i){ return !S.ides.size || S.ides.has(i||"(unknown)"); }
/* Rows without a device are this one's (an older payload, or a single-device install). */
function passDev(d){ return !S.devs.size || S.devs.has(d || (RAW && RAW.device && RAW.device.id)); }
function devices(){ return (RAW && RAW.devices) || []; }
function multiDevice(){ return devices().length > 1; }
function deviceName(id){ const d=devices().find(x=>x.id===id); return d ? d.name : "This device"; }

/* ---------- filters ⇄ the URL ----------
   Every filter is in the query string too, so a reload, a bookmark or a pasted link opens
   the same view. Only what differs from the default is written, and a param that isn't
   ours (?theme=, ?side=) is left alone. The tab stays in the hash. A change replaces the
   history entry rather than adding one, the way switching tab does. */
const Q_DEFAULT = {preset:S.preset, metric:S.metric, rateMetric:S.rateMetric};
const Q_SETS = [["provs","providers"],["models","models"],["projs","projects"],["ides","ides"],["devs","devices"]];
const Q_OWNED = ["range","from","to","metric","rate","tools","exact","q","session_type", ...Q_SETS.map(x=>x[1])];
const isDay = s => /^\d{4}-\d{2}-\d{2}$/.test(s||"") && dkey(new Date(s+"T00:00:00"))===s;

/* A link is somebody else's text. Dates, tool ids, the range and the measure are checked
   here; project, model and search text is only ever put on the page through esc() or
   .textContent, and a value that matches nothing shows as a chip you can remove. */
function filtersFromURL(){
  const q = new URLSearchParams(location.search), rg = q.get("range");
  if(PRESETS.some(p=>p[0]===rg) || /^[1-9]\d{0,3}d$/.test(rg||"")) S.preset = rg;
  else if(isDay(q.get("from")) && isDay(q.get("to"))){
    const a = q.get("from"), b = q.get("to");
    S.preset = "custom"; S.from = a<b ? a : b; S.to = a<b ? b : a;
  }
  if(["tokens","cost","messages","time"].includes(q.get("metric"))) S.metric = q.get("metric");
  if(["all","out"].includes(q.get("rate"))) S.rateMetric = q.get("rate");
  const tools = q.getAll("tools").filter(s=>ORDER.includes(s));
  if(tools.length) S.tools = new Set(tools);
  for(const [key, param] of Q_SETS){
    const vals = q.getAll(param).map(v=>v.slice(0,200)).filter(Boolean).slice(0,200);
    if(vals.length) S[key] = new Set(vals);
  }
  S.exactOnly = ["1","true"].includes(q.get("exact"));
  S.search = (q.get("q")||"").trim().slice(0,200);
  S.sessionType = ["all","subagents","other"].includes(q.get("session_type"))
    ? q.get("session_type") : "all";
}
/* Called after every render, so a filter set by a key, a chip, a link or a click on the
   calendar is covered without each of them knowing about the URL. */
function filtersToURL(){
  const q = new URLSearchParams(location.search);
  Q_OWNED.forEach(k=>q.delete(k));
  if(S.preset==="custom"){
    if(isDay(S.from) && isDay(S.to)){ q.set("range","custom"); q.set("from",S.from); q.set("to",S.to); }
  } else if(S.preset!==Q_DEFAULT.preset) q.set("range", S.preset);
  if(S.metric!==Q_DEFAULT.metric) q.set("metric", S.metric);
  if(S.rateMetric!==Q_DEFAULT.rateMetric) q.set("rate", S.rateMetric);
  if(ORDER.some(s=>!S.tools.has(s))) ORDER.filter(s=>S.tools.has(s)).forEach(s=>q.append("tools", s));
  for(const [key, param] of Q_SETS) [...S[key]].sort().forEach(v=>q.append(param, v));
  if(S.exactOnly) q.set("exact","1");
  if(S.search) q.set("q", S.search);
  if(S.sessionType!=="all") q.set("session_type",S.sessionType);
  const qs = q.toString(), next = location.pathname + (qs ? "?"+qs : "") + location.hash;
  if(next !== location.pathname + location.search + location.hash){
    try{ history.replaceState(history.state, "", next); }catch(e){}
  }
}

/* Shared precision and dimension filters; omit r for fixed-window activity views. */
function filteredRecords(r){
  return RAW.records.map(x => S.exactOnly && x.source==="copilot"
      ? (x.exact ? Object.assign({}, x, x.exact) : null) : x).filter(Boolean).filter(x => passSrc(x.source) && passModel(x.model)
      && passProj(x.project) && passIde(x.ide) && passDev(x.device) && (!r || (x.date>=r.from && x.date<=r.to)));
}
function slice(r){
  r = r || range();
  const recs = filteredRecords(r);
  const hourly = RAW.hourly.filter(x => passSrc(x.source) && passDev(x.device) && x.date>=r.from && x.date<=r.to);
  const sessions = RAW.sessions
    .filter(x => passSrc(x.source) && passProj(x.project) && sessionMatchesModel(x)
                 && passIde(x.ide) && passDev(x.device) && activeInRange(x, r))
    .map(x => clipSession(x, r));
  return {recs, hourly, sessions, r};
}
/* A session is "in range" if it did anything inside it — not merely if it
   ENDED there. A resumed rollout can span weeks. */
function sessionMatchesModel(s){
  if(!S.models.size && !S.provs.size) return true;
  // Older Cursor ledger rows may have a composer-level current model but no
  // per-message model evidence. Never use that setting as historical usage.
  if(s.source==="cursor"&&!(["recorded","partial"].includes(s.model_status))) return false;
  const maps = S.exactOnly && s.source==="copilot" ? s.exact_model_days : s.model_days;
  return maps ? Object.keys(maps).some(m=>m!=="(user)" && passModel(m)) : passModel(s.model);
}
function sessionDays(s){
  const exact = S.exactOnly && s.source==="copilot";
  const maps = exact ? s.exact_model_days : s.model_days;
  if(maps && (S.models.size || S.provs.size)){
    const days={};
    for(const [model, entries] of Object.entries(maps)){
      if(!passModel(model)) continue;
      for(const [date, values] of Object.entries(entries)){
        const row = days[date] || (days[date]=Array(15).fill(0));
        values.forEach((v,i)=>{ row[i]+=v||0; });
      }
    }
    return days;
  }
  return exact ? (s.exact_days || {}) : s.days;
}
function activeInRange(s, r){
  const D = sessionDays(s);
  if(D){ for(const d in D) if(d>=r.from && d<=r.to) return true; }
  // Boundaries have no billed model attribution. They can make an otherwise
  // empty day visible when no model/provider filter asks for model-specific work.
  if(!S.models.size && !S.provs.size && (compactionsInRange(s,r)||[]).length) return true;
  // SDK child lifecycles have explicit activity dates even without saved usage.
  // Never fill the idle dates between start and end, or guess a billed model.
  if(s.usage_in_parent && !S.exactOnly && !S.models.size && !S.provs.size
      && Array.isArray(s.activity_dates) && s.activity_dates.some(d=>isDay(d)&&d>=r.from&&d<=r.to)) return true;
  if(D) return false;
  const e = (s.end||s.start||"").slice(0,10);
  return e>=r.from && e<=r.to;
}
function compactionsInRange(s, r){
  if(!Array.isArray(s.compactions)) return null; // older archive/peer: unknown
  return s.compactions.filter(e=>e.date>=r.from && e.date<=r.to);
}
/* Antigravity and Cursor record usage availability by date and model. Keep the
   full record map, but make the shared detail map match the clipped figures. */
function sessionUsageAvailabilityInRange(s,r){
  const availability=s.usage_availability&&typeof s.usage_availability==="object"&&!Array.isArray(s.usage_availability)
    ?{...s.usage_availability}:{};
  const map=s.record_availability;
  if(!["antigravity","cursor"].includes(s.source)||!map||typeof map!=="object"||Array.isArray(map)) return availability;
  const fields=["input_output","cache_read","cache_write","reasoning","response_output"];
  const states=new Set(["recorded","partial","unavailable"]);
  const selected=[];
  for(const [key,value] of Object.entries(map)){
    const separator=key.indexOf("\t");
    if(separator<1||separator===key.length-1) continue;
    const date=key.slice(0,separator),model=key.slice(separator+1);
    if(!isDay(date)||date<r.from||date>r.to||!passModel(model)
        ||!value||typeof value!=="object"||Array.isArray(value)) continue;
    selected.push(value);
  }
  if(!selected.length){
    for(const field of fields) availability[field]="unavailable";
    return availability;
  }
  for(const field of fields){
    const scoped=selected.map(item=>states.has(item[field])?item[field]:"unavailable");
    availability[field]=scoped.every(state=>state==="recorded")?"recorded"
      :scoped.every(state=>state==="unavailable")?"unavailable":"partial";
  }
  return availability;
}
/* Effort is recorded metadata, never a model identity or another usage metric.
   Keep its original date/model map so a clipped session can be scoped again. */
const REASONING_EFFORT_LEVELS = ["none","minimal","low","medium","high","xhigh","max","ultra"];
function reasoningEffortInRange(s, r){
  const result={levels:[],unrecorded:false,entries:[]};
  const metadata=s.reasoning_effort;
  if(!["claude","codex","antigravity"].includes(s.source) || !metadata || metadata.version!==1
      || !metadata.days || typeof metadata.days!=="object" || Array.isArray(metadata.days)) return result;
  const allowed=s.source==="claude"?["low","medium","high","xhigh","max"]:REASONING_EFFORT_LEVELS;
  const observed=new Set();
  for(const [key,value] of Object.entries(metadata.days)){
    const parts=key.split("\t");
    if(parts.length!==2) continue;
    const [date,model]=parts;
    if(!isDay(date) || date<r.from || date>r.to || !model || model==="(user)" || !passModel(model)
        || !value || typeof value!=="object" || Array.isArray(value)) continue;
    const levels=[];
    let unrecorded=!Number.isSafeInteger(value.unrecorded) || value.unrecorded<0 || value.unrecorded>0;
    if(value.levels && typeof value.levels==="object" && !Array.isArray(value.levels)){
      for(const [level,count] of Object.entries(value.levels)){
        if(!Number.isSafeInteger(count) || count<0){unrecorded=true;continue;}
        if(!count) continue;
        if(!allowed.includes(level)){unrecorded=true;continue;}
        levels.push(level);observed.add(level);
      }
    }else unrecorded=true;
    if(!levels.length && !unrecorded) continue;
    levels.sort((a,b)=>REASONING_EFFORT_LEVELS.indexOf(a)-REASONING_EFFORT_LEVELS.indexOf(b));
    result.entries.push({date,model,levels,unrecorded});
    result.unrecorded ||= unrecorded;
  }
  result.levels=REASONING_EFFORT_LEVELS.filter(level=>observed.has(level));
  result.entries.sort((a,b)=>a.date.localeCompare(b.date)||a.model.localeCompare(b.model));
  return result;
}
/* Display-only model identity follows the same date/model/provider precision as
   the session figures. Keep historical fields and complete maps untouched. */
function sessionModelsInRange(s, r, nameLimit=6){
  const maps=S.exactOnly && s.source==="copilot"?s.exact_model_days:s.model_days;
  const cursorStatus=s.source==="cursor"?s.model_status:null;
  if(s.source==="cursor"&&!(["recorded","partial"].includes(cursorStatus)))
    return {model:cursorStatus==="unavailable"?"Unavailable":"Not recorded",models:[],nmodels:0,
      scope:"selected",availability:cursorStatus||"not_recorded"};
  if(s.source==="cursor"&&cursorStatus==="partial"&&(!maps||typeof maps!=="object"||Array.isArray(maps)))
    return {model:"Partially recorded",models:[],nmodels:0,scope:"whole-session",availability:"partial"};
  if(!maps || typeof maps!=="object" || Array.isArray(maps)){
    const model=typeof s.model==="string" && s.model?s.model:"Not recorded";
    const models=Array.isArray(s.models)?s.models.filter(m=>typeof m==="string" && m && m!=="(user)"):[model];
    const count=Number.isSafeInteger(s.nmodels) && s.nmodels>0?s.nmodels:models.length;
    return {model,models:models.slice(0,nameLimit),nmodels:Math.max(count,models.length),scope:"whole-session",
      ...(s.source==="cursor"?{availability:cursorStatus}:{})};
  }
  const observed=[];
  for(const [model,days] of Object.entries(maps)){
    if(!model || model==="(user)" || (s.source==="cursor"&&model==="Unknown")
        || !passModel(model) || !days || typeof days!=="object" || Array.isArray(days)) continue;
    let tokens=0,calls=0,replies=0,present=false;
    for(const [date,row] of Object.entries(days)){
      if(!isDay(date) || date<r.from || date>r.to || !Array.isArray(row)) continue;
      const value=i=>Number.isFinite(row[i]) && row[i]>0?row[i]:0;
      const total=value(1)+value(2)+value(3)+value(4);
      const requests=value(10), responses=value(5);
      // A user-only or compaction-only day does not name a model that ran.
      if(total || requests || responses || value(0)) present=true;
      tokens+=total;calls+=requests;replies+=responses;
    }
    if(present) observed.push({model,tokens,calls,replies});
  }
  observed.sort((a,b)=>b.tokens-a.tokens || b.calls-a.calls || b.replies-a.replies || a.model.localeCompare(b.model));
  return {model:observed[0]?.model||"No model usage",models:observed.slice(0,nameLimit).map(x=>x.model),
    nmodels:observed.length,scope:"selected",...(s.source==="cursor"?{availability:cursorStatus}:{})};
}
/* Keep the first ten legacy slots; newer maps add calls and write tiers. */
function clipSession(s, r){
  const compactions=compactionsInRange(s,r);
  const events={compactions,compaction_count:compactions===null?null:compactions.length,
    reasoning_effort_scoped:reasoningEffortInRange(s,r),model_scoped:sessionModelsInRange(s,r),
    ...(["antigravity","cursor"].includes(s.source)
      ?{usage_availability:sessionUsageAvailabilityInRange(s,r)}:{})};
  const D = sessionDays(s);
  if(!D) return Object.assign({}, s, {span:1, clipped:false, detail_limited:true}, events);
  const total=Array(15).fill(0); let inN=0,allN=0,callsKnown=true;
  for(const d in D){
    allN++;
    if(d<r.from || d>r.to) continue;
    const v=D[d]; inN++;
    if(v.length<11) callsKnown=false;
    v.forEach((n,i)=>{total[i]+=n||0;});
  }
  const dates=new Set(Object.keys(D));
  for(const e of s.compactions||[]) if(e.date) dates.add(e.date);
  const inRange=[...dates].filter(d=>d>=r.from && d<=r.to).length;
  const [cost,i,o,cr,cc,asst,user,tools,prem,active,req,cc5,cc1,reason,ws]=total;
  return Object.assign({}, s, {cost, in:i, out:o, cr, cc, asst, user, tools, prem, active,
    cc5, cc1, reason, ws, req:callsKnown?req:(inN<allN?asst:s.req),
    span:dates.size, range_clipped:inRange<dates.size,
    clipped:inRange<dates.size || !!(S.models.size || S.provs.size)}, events);
}

/* Tool-call rows carry a date + source but no project/model dimension, so the
   project / provider / model filters cannot apply to them — the UI says so. */
function toolCounts(r, only){
  const out = {};
  for(const t of RAW.tools){
    if(!passSrc(t.source) || !passDev(t.device) || (only && !only(t))) continue;
    if(t.date < r.from || t.date > r.to) continue;
    const e = out[t.name] || (out[t.name] = {name:t.name, count:0, src:{}});
    e.count += t.count;
    e.src[t.source] = (e.src[t.source]||0) + t.count;
  }
  return Object.values(out).sort((a,b)=>b.count-a.count);
}
function dimFiltered(){ return S.projs.size || S.provs.size || S.models.size || S.ides.size || S.exactOnly; }
function totals(recs){
  const t = {tok:0,cost:0,msgs:0,user:0,tools:0,in:0,out:0,cr:0,cc:0,reason:0,req:0,prem:0,days:new Set()};
  for(const r of recs){
    t.tok+=recTokens(r); t.cost+=r.cost||0; t.msgs+=r.asst||0; t.user+=r.user||0;
    t.tools+=r.tools||0; t.in+=r.in||0; t.out+=r.out||0; t.cr+=r.cr||0; t.cc+=r.cc||0;
    t.reason+=r.reason||0; t.req+=r.req||0; t.prem+=r.prem||0;
    if(recTokens(r)||r.asst||r.user) t.days.add(r.date);
  }
  return t;
}

/* ---------- shared UI helpers ---------- */
/* Interactive legend — ONLY for charts whose render actually honours S.muted.
   A legend that looks clickable but changes nothing is worse than a plain one. */
function legendHTML(id, items){
  return items.map(it=>{
    const off = (S.muted[id]&&S.muted[id].has(it.label))?" off":"";
    return `<span class="li${off}" data-lg="${id}" data-k="${esc(it.label)}">
      <span class="sw" style="background:${it.color}"></span>${it.source?toolLabel(it.source):esc(it.label)}</span>`;
  }).join("");
}
/* Read-only legend — part-to-whole and normalised charts, where hiding a series
   would misstate the total. */
function legendStatic(items){
  return items.map(it=>`<span class="li static">
      <span class="sw" style="background:${it.color}"></span>${it.source?toolLabel(it.source):esc(it.label)}</span>`).join("");
}
function isMuted(id,label){ return !!(S.muted[id] && S.muted[id].has(label)); }
function sparkline(vals, color, w, h){
  w=w||64; h=h||20;
  if(!vals.length) return "";
  const max=Math.max(...vals,1), min=Math.min(...vals,0);
  const span=(max-min)||1;
  const pts=vals.map((v,i)=>[i*(w/Math.max(1,vals.length-1)), h-2-((v-min)/span)*(h-4)]);
  const d=pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
  const area=d+` L ${w} ${h} L 0 ${h} Z`;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <path d="${area}" fill="${color}" opacity=".13"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.5"
      stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
/* Change vs the equal-length period just before this one. `invert` = up is bad
   (spend). An arrow always rides with the colour so direction is never hue-alone. */
function deltaHTML(cur, prev, invert){
  if(prev==null || !isFinite(prev) || prev===0) return `<span class="delta flat">new</span>`;
  const d=(cur-prev)/Math.abs(prev);
  if(Math.abs(d)<0.005) return `<span class="delta flat">no change</span>`;
  const up=d>0, good = invert ? !up : up;
  const pct = Math.abs(d)>=1000 ? Math.round(Math.abs(d)/1000)+"k×" : Math.abs(d)>=10 ? Math.round(Math.abs(d))+"×" : Math.abs(d*100).toFixed(0)+"%";
  return `<span class="delta ${good?"up":"down"}">${up?"▲":"▼"} ${pct}</span>`;
}

/* ---------- tooltip ---------- */
const TIP = () => document.getElementById("tip");
function showTip(html, x, y){
  const t=TIP(); t.innerHTML=html; t.classList.add("on");
  const r=t.getBoundingClientRect();
  let left=x+14, top=y+14;
  if(left+r.width>innerWidth-8) left = x-r.width-14;
  if(top+r.height>innerHeight-8) top = y-r.height-14;
  t.style.left=left+"px"; t.style.top=Math.max(8,top)+"px";
}
function hideTip(){ TIP().classList.remove("on"); }

/* ---------- theme ---------- */
function applyTheme(mode){
  const root=document.documentElement;
  if(mode==="auto") root.removeAttribute("data-theme");
  else root.dataset.theme=mode;
  const dark = mode==="dark" || (mode==="auto" &&
    matchMedia("(prefers-color-scheme: dark)").matches);
  root.dataset.resolved = dark?"dark":"light";
  try{ localStorage.setItem("aiu.theme", mode); }catch(e){}
  document.getElementById("themeBtn").title = "Theme: "+mode+" (t)";
  if(RAW) renderAll();
}
function cycleTheme(){
  let cur="auto"; try{ cur = localStorage.getItem("aiu.theme")||"auto"; }catch(e){}
  applyTheme(cur==="auto"?"light":cur==="light"?"dark":"auto");
}

/* ---------- dropdown plumbing ---------- */
/* Dropdowns normally open from their trigger's left edge. When that would run past
   the viewport, flip them against the trigger's right edge instead. This keeps the
   query sentence natural while letting its right-most controls remain usable. */
function placeDD(dd){
  const panel=dd.querySelector(".dd-panel");
  if(!panel) return;
  dd.classList.remove("flip-x");
  if(panel.getBoundingClientRect().right > innerWidth-16) dd.classList.add("flip-x");
}
function closeDD(except){
  document.querySelectorAll(".dd.open").forEach(d=>{
    if(d!==except){ d.classList.remove("open"); d.classList.remove("flip-x"); }
  });
}
document.addEventListener("click", e=>{
  const tog = e.target.closest("[data-dd=toggle]");
  if(tog){ const dd=tog.closest(".dd"); const open=dd.classList.contains("open");
    closeDD(); if(!open){ dd.classList.add("open"); placeDD(dd); } e.stopPropagation(); return; }
  if(!e.target.closest(".dd-panel")) closeDD();
});
if(typeof window!=="undefined") window.addEventListener("resize",()=>document.querySelectorAll(".dd.open").forEach(placeDD));
document.addEventListener("keydown", e=>{
  // an open dialog owns the keyboard: Esc closes it alone, not the drawer behind it
  if(document.querySelector("dialog.modal[open]")) return;
  if(e.key==="Escape"){ closeDD(); closeDrawer(); }
  if(e.target.tagName==="INPUT"||e.target.tagName==="SELECT"||e.target.tagName==="TEXTAREA"
     ||e.target.isContentEditable) return;
  // the browser's own shortcuts (Cmd+A select all, Cmd+T new tab, Cmd+[ back, Cmd+Shift+T…)
  // must not also switch the range, theme or measure behind them. Alt/AltGr stay allowed:
  // some layouts type "[" with them (Option+5, AltGr+8 on German keyboards)
  if((e.metaKey||e.ctrlKey) && !(e.getModifierState && e.getModifierState("AltGraph"))) return;
  const map={"1":"today","7":"7d","3":"30d","9":"90d","a":"all","m":"mtd"};
  if(map[e.key]){ S.preset=map[e.key]; syncRangeUI(); renderAll(); }
  if(e.key==="t") cycleTheme();
  if(e.key==="r") document.getElementById("refreshBtn").click();
  if(e.key==="[") toggleSide();
  if(e.key===",") stepPeriod(-1);
  if(e.key===".") stepPeriod(1);
  const mk={"T":"tokens","C":"cost","M":"messages","A":"time"}[e.key];   // shift+letter
  if(mk){ S.metric=mk; renderAll(); }
  if(e.key==="/"){ const box=document.querySelector(".view.on .search-in");
    if(box){ e.preventDefault(); box.focus(); } }
});

/* ---------- collapsible sidebar (remembered per browser) ---------- */
function setSide(collapsed){
  document.getElementById("app").classList.toggle("side-collapsed", collapsed);
  const b = document.getElementById("sideToggle");
  if(b){ const l = collapsed ? "Expand sidebar ([)" : "Collapse sidebar ([)";
    b.title = l; b.setAttribute("aria-label", l.replace(" ([)","")); b.setAttribute("aria-expanded", String(!collapsed)); }
  try{ localStorage.setItem("aiu.side", collapsed ? "collapsed" : "open"); }catch(e){}
}
function toggleSide(){ setSide(!document.getElementById("app").classList.contains("side-collapsed")); }
document.addEventListener("DOMContentLoaded", () => {
  // each nav item's label doubles as its tooltip when the rail hides the text
  for(const b of document.querySelectorAll("#tabs button"))
    if(!b.title) b.title = (b.querySelector("span")||{}).textContent || "";
  const t = document.getElementById("sideToggle");
  if(t) t.addEventListener("click", toggleSide);
  let saved = null; try{ saved = localStorage.getItem("aiu.side"); }catch(e){}
  const qs = new URLSearchParams(location.search).get("side");   // ?side= presets it (screenshots)
  if(qs === "collapsed" || qs === "open") setSide(qs === "collapsed");
  else if(saved === "collapsed") setSide(true);
});

/* ---------- confirm dialog ----------
   The app's own, never the browser's confirm(): that one wears the browser's chrome,
   says "127.0.0.1:7878 says", and can't carry emphasis or a list. Resolves true only
   on the confirm button; Esc, Cancel and a click on the backdrop all resolve false.
   `body` is HTML, so callers escape anything that came from data. */
function askConfirm({title, body, ok="Continue", cancel="Cancel", danger=false}){
  return new Promise(resolve=>{
    const d = document.createElement("dialog");
    d.className = "modal"; d.setAttribute("aria-labelledby", "modalTitle");
    d.innerHTML = `<div class="modal-in">
        <h2 class="modal-h" id="modalTitle">${esc(title)}</h2>
        <div class="modal-body">${body}</div>
        <div class="modal-actions">
          <button class="btn" value="cancel">${esc(cancel)}</button>
          <button class="btn ${danger?"danger":"primary"}" value="ok">${esc(ok)}</button>
        </div></div>`;
    let yes = false;
    d.addEventListener("click", e=>{
      const b = e.target.closest("button[value]");
      if(b){ yes = b.value==="ok"; d.close(); }
      else if(e.target===d) d.close();              // the backdrop, outside .modal-in
    });
    d.addEventListener("close", ()=>{ d.remove(); resolve(yes); });
    document.body.appendChild(d);
    d.showModal();                                  // focus lands on Cancel, the safe choice
  });
}

/* ---------- drawer ---------- */
function openDrawer(html){
  document.getElementById("drawerBody").innerHTML=html;
  document.getElementById("drawer").classList.add("open");
  document.getElementById("scrim").classList.add("on");
}
function closeDrawer(){
  document.getElementById("drawer").classList.remove("open");
  document.getElementById("scrim").classList.remove("on");
}
