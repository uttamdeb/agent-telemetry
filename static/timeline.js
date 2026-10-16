/* Session timeline rendering. Daily ledger figures are always available; the
   optional event detail is fetched from the local source log on drawer open. */
let SESSION_TIMELINE_INTENT = 0;

function timelineDate(timestamp){
  if(typeof timestamp!=="string" || !timestamp) return "";
  const parsed=new Date(timestamp);
  if(!Number.isFinite(parsed.getTime())) return timestamp.slice(0,10);
  return typeof dkey==="function"?dkey(parsed):`${parsed.getFullYear()}-${String(parsed.getMonth()+1).padStart(2,"0")}-${String(parsed.getDate()).padStart(2,"0")}`;
}
function timelineWhen(timestamp){
  const parsed=new Date(timestamp);
  if(!Number.isFinite(parsed.getTime())) return String(timestamp||"").slice(0,16).replace("T"," ");
  const date=timelineDate(timestamp);
  const pad=n=>String(n).padStart(2,"0");
  return `${date} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}
function timelineInRange(event){
  const r=typeof range==="function"?range():null;
  const day=event&&event.date?String(event.date):timelineDate(event&&event.timestamp);
  return !r || !day || (day>=r.from && day<=r.to);
}
function timelineEventModelPass(event){
  if(event.model) return typeof passModel!=="function" || passModel(event.model);
  if(event.kind!=="request") return true;
  const filtered=typeof S!=="undefined"&&(S.models?.size||S.provs?.size);
  if(filtered&&!event.model) return false;
  return true;
}
function timelineCompactionEvents(session){
  return Array.isArray(session&&session.compactions)?session.compactions.map(e=>Object.assign({},e,{kind:"compaction"})):[];
}
function timelineEventsHTML(payload,session){
  const available=!!(payload&&payload.available);
  const events=[...(available&&Array.isArray(payload.events)?payload.events:[]),...timelineCompactionEvents(session)]
    .filter(e=>timelineInRange(e) && timelineEventModelPass(e))
    .sort((a,b)=>{
      const at=Date.parse(a.timestamp||""), bt=Date.parse(b.timestamp||"");
      return (Number.isFinite(at)?at:0)-(Number.isFinite(bt)?bt:0);
    });
  if(!available&&!events.length){
    return '<p class="hint">Detailed events are unavailable for this source log. Daily ledger detail remains available above.</p>';
  }
  const rows=events.map(e=>{
    const when=esc(timelineWhen(e.timestamp));
    let what="", detail="";
    if(e.kind==="user_turn") what="Your turn";
    else if(e.kind==="tool") { what=`Tool · ${esc(e.label||"Tool")}`; detail=e.model?`<span>${esc(e.model)}</span>`:""; }
    else if(e.kind==="subagent") what=esc(e.label||"Subagent started");
    else if(e.kind==="compaction") what=`Completed compaction · ${esc(e.trigger==="manual"?"Manual":e.trigger==="auto"?"Auto":"trigger not recorded")}`;
    else if(e.kind==="request"){
      what=`Model request${e.model?` · ${esc(e.model)}`:""}`;
      const fields=[];
      for(const [key,label] of [["input","in"],["cache_read","cache read"],["cache_write","cache write"],["output","out"],["reasoning","reasoning within output"],["web_search","web searches"]]){
        if(Number.isSafeInteger(e[key]) && e[key]>=0) fields.push(`${label} ${fmtTok(e[key])}`);
      }
      detail=fields.length?`<span>${fields.map(esc).join(" · ")}</span>`:"<span>Usage not recorded</span>";
    } else return "";
    return `<li><time datetime="${esc(e.timestamp||"")}">${when}</time><div><strong>${what}</strong>${detail}</div></li>`;
  }).filter(Boolean).join("");
  const partial=payload&&payload.truncated
    ? '<p class="hint">Partial source detail. The log exceeded the local read limit or event limit.</p>'
    : available?'<p class="hint">Events use recorded timestamps; missing activity is not inferred. Compactions and subagent markers are scoped to this session and date, without model attribution.</p>':'<p class="hint">Recorded compactions are included from the ledger; detailed source events are unavailable.</p>';
  return `${partial}${rows?`<ol class="session-timeline-events" aria-label="Recorded session events">${rows}</ol>`:'<p class="hint">No recorded events in the selected range.</p>'}`;
}
function sessionTimelineHTML(session){
  const days=typeof sessionDays==="function"?sessionDays(session):(session.days||{});
  const r=typeof range==="function"?range():null;
  const dayRows=Object.entries(days||{}).filter(([day])=>!r||(day>=r.from&&day<=r.to)).sort(([a],[b])=>a.localeCompare(b));
  const daily=dayRows.map(([day,v])=>{
    const values=Array.isArray(v)?v:[];
    const inTok=values[1]||0, outTok=values[2]||0, cache=values[3]||0;
    const cost=values[0]||0, calls=values[10]||0;
    const callsLabel=values.length>10?fmtTok(calls):"Not recorded";
    const unavailable=session.copilot_sdk?.usage_status==="unavailable";
    return `<tr><th scope="row">${esc(day)}</th><td>${unavailable?"Unavailable":fmtTok(inTok+outTok+cache+(values[4]||0))}</td><td>${unavailable?"Unavailable":fmtUSD(cost)}</td><td>${callsLabel}</td></tr>`;
  }).join("");
  const compactions=typeof compactionsInRange==="function"&&r?compactionsInRange(session,r):session.compactions;
  const id=session.rename_id||session._sid||"";
  const identity=id?` data-timeline-id="${esc(id)}"`:"";
  return `<section class="session-timeline" aria-label="Session timeline">
    <h3>Timeline</h3>
    <p class="hint">Daily usage comes from the ledger. Events use the local session date and model filters where recorded.</p>
    ${dayRows.length?`<div class="session-timeline-table"><table><caption>Daily usage in the selected range</caption><thead><tr><th scope="col">Day</th><th scope="col">${session.copilot_sdk?.usage_status==="partial"?"Recorded tokens":"Tokens"}</th><th scope="col">Est. cost</th><th scope="col">Calls</th></tr></thead><tbody>${daily}</tbody></table></div>`:`<p class="hint">${days?"No daily usage in the selected range.":"Daily detail was not recorded in this ledger entry."}</p>`}
    ${compactions===null?'<p class="hint">Compaction detail was not recorded in this ledger entry.</p>':""}
    <div class="session-timeline-detail"${identity} aria-live="polite">${timelineEventsHTML({available:false},session)}</div>
  </section>`;
}
async function loadSessionTimeline(session,target){
  const intent=++SESSION_TIMELINE_INTENT;
  const node=typeof target==="string"?document.querySelector(target):target;
  if(!node) return;
  const id=session&&(session.rename_id||session._sid);
  const localDevice=typeof RAW!=="undefined"&&RAW.device?RAW.device.id:null;
  if(!id || session.archived || (localDevice&&session.device&&session.device!==localDevice) || !["claude","claude-desktop","codex"].includes(session.source)){
    node.innerHTML=timelineEventsHTML(null,session); return;
  }
  node.innerHTML='<p class="hint">Loading detailed events…</p>';
  try{
    const selected=typeof range==="function"?range():null;
    const params={device:String(session.device||""),source:String(session.source),id:String(id)};
    if(selected){ params.from=String(selected.from||""); params.to=String(selected.to||""); }
    const query=new URLSearchParams(params);
    const response=await fetch(`/api/session-timeline?${query}`,{headers:{Accept:"application/json"}});
    const payload=response.ok?await response.json():null;
    if(intent!==SESSION_TIMELINE_INTENT || !node.isConnected) return;
    node.innerHTML=timelineEventsHTML(payload,session);
  }catch(_error){
    if(intent===SESSION_TIMELINE_INTENT && node.isConnected) node.innerHTML=timelineEventsHTML(null,session);
  }
}
