/* Settings maintenance panel: private parser diagnostics and durable backups. */
let maintenanceRoot = null;
let maintenanceLoadId = 0;
let maintenanceWorking = false;

function maintenanceEsc(value){
  return esc(value == null ? "" : String(value));
}
function maintenanceBytes(value){
  let n=Number(value);
  if(!Number.isFinite(n)||n<0) return "Unknown size";
  const units=["B","KB","MB","GB","TB"]; let i=0;
  while(n>=1024&&i<units.length-1){n/=1024;i++;}
  return `${i===0?Math.round(n):n.toFixed(1)} ${units[i]}`;
}
function maintenanceWhen(value){
  if(typeof value==="string"&&value.trim()){
    const parsed=Date.parse(value);
    if(Number.isFinite(parsed)) return new Date(parsed).toLocaleString();
  }
  const n=Number(value); if(!Number.isFinite(n)||n<=0) return "Not recorded";
  try{return new Date(n*1000).toLocaleString();}catch{return "Not recorded";}
}
function maintenanceCount(value){
  const n=Number(value); return Number.isFinite(n)&&n>=0?Math.floor(n).toLocaleString():"0";
}
function maintenanceToolLabel(value){
  const source=typeof value==="string"?value:"";
  return /^[a-z0-9][a-z0-9_-]{0,39}$/i.test(source)?toolLabel(source):"Unknown source";
}
function safeMaintenanceError(error, fallback="Could not complete the maintenance request."){
  const message=typeof error?.message==="string"?error.message:"";
  if(!message||message.length>240||/[\r\n]|[A-Z]:\\|\/(?:Users|home|private|tmp)\//i.test(message)) return fallback;
  return message;
}
function maintenanceMessage(text, kind=""){
  const node=maintenanceRoot?.querySelector("[data-maint-msg]");
  if(!node) return;
  node.textContent=text||"";
  node.className="stg-msg"+(kind?` ${kind}`:"");
}
function setMaintenanceBusy(on){
  maintenanceWorking=on;
  const root=maintenanceRoot; if(!root) return;
  root.querySelectorAll("button[data-maint-action]").forEach(button=>button.disabled=on);
}
function maintenanceStillCurrent(root, loadId){
  return root===maintenanceRoot&&root?.isConnected&&loadId===maintenanceLoadId;
}
async function maintenanceJson(url, options){
  const response=await fetch(url,options);
  let body={};
  try{body=await response.json();}catch{}
  if(!response.ok) throw new Error(typeof body.error==="string"?body.error:"Could not complete the maintenance request.");
  return body;
}
function maintenancePost(url, body){
  return maintenanceJson(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
}
function maintenanceStat(label, value){
  return `<span class="maintenance-stat"><b>${maintenanceEsc(value)}</b> ${maintenanceEsc(label)}</span>`;
}
function renderMaintenanceDiagnostics(data){
  const rows=Array.isArray(data?.sources)?data.sources:[];
  const issues=Array.isArray(data?.issues)?data.issues:[];
  const rowHTML=rows.map(row=>{
    const source=typeof row.source==="string"?row.source:"unknown";
    const status=({available:"Logs available",partial_error:"Some logs could not be read",error:"Parse error",idle:"Waiting for scan",retained_only:"Retained history only"})[row.status]||"Status unavailable";
    const error=row.hint?`<div class="stg-hint">${maintenanceEsc(row.hint)}</div>`:"";
    return `<div class="maintenance-row"><div class="maintenance-row-head"><b>${maintenanceToolLabel(source)}</b><span>${maintenanceEsc(status)}</span></div>
      <div class="stg-hint">${maintenanceCount(row.read)} read of ${maintenanceCount(row.discovered)} discovered · ${maintenanceCount(row.errors)} errors</div>${error}</div>`;
  }).join("");
  const issueHTML=issues.slice(-8).map(issue=>`<li><b>${maintenanceToolLabel(issue.source)}</b> · ${maintenanceEsc(issue.hint||"A log could not be read.")} <span class="dim">${maintenanceEsc(maintenanceWhen(issue.at))}</span></li>`).join("");
  const summary=`${maintenanceCount(data?.read)} logs read · ${maintenanceCount(data?.errors)} errors`
    +(data?.finished_at?` · ${maintenanceWhen(data.finished_at)}`:"");
  const ledger=data?.ledger&&typeof data.ledger==="object"?`<div class="maintenance-row"><div class="maintenance-row-head"><b>Analytics ledger</b><span>${maintenanceEsc(data.ledger.status||"Status unavailable")}</span></div>${data.ledger.hint?`<div class="stg-hint">${maintenanceEsc(data.ledger.hint)}</div>`:""}</div>`:"";
  const missing=Array.isArray(data?.missing_prices)?data.missing_prices:[];
  const priceHTML=missing.length?`<div class="maintenance-row"><div class="maintenance-row-head"><b>Models without an estimate</b><span>${maintenanceCount(missing.length)}</span></div><div class="stg-hint">${missing.slice(0,12).map(item=>maintenanceEsc(typeof item==="string"?item:item?.model||"Unknown model")).join(" · ")}${missing.length>12?" · …":""}</div></div>`:"";
  const unsupported=Array.isArray(data?.unsupported_formats)?data.unsupported_formats:[];
  const unsupportedHTML=unsupported.filter(item=>item&&item.source==="antigravity"&&item.format==="legacy_pb"&&Number(item.count)>0).map(item=>{
    const rawHint=typeof item.hint==="string"?item.hint:"";
    const hint=/^[A-Za-z0-9 .,:;()'’-]{1,240}$/.test(rawHint)?rawHint:"Older Antigravity conversation files are not parsed by this source reader.";
    return `<div class="maintenance-row"><div class="maintenance-row-head"><b>${maintenanceToolLabel(item.source)}</b><span>Legacy format not parsed</span></div>
      <div class="stg-hint">${maintenanceCount(item.count)} older conversation file${Number(item.count)===1?"":"s"} found · ${maintenanceEsc(hint)}</div></div>`;
  }).join("");
  return `<div class="maintenance-summary">${maintenanceEsc(summary)}</div>${rowHTML||'<div class="stg-hint">No source logs were detected yet. Refresh the dashboard after using a supported coding tool.</div>'}${ledger}${priceHTML}${unsupportedHTML}
    ${issueHTML?`<details class="maintenance-issues"><summary>Recent issues (${Math.min(issues.length,8)})</summary><ul>${issueHTML}</ul></details>`:""}`;
}
function renderMaintenanceBackups(data){
  const backups=Array.isArray(data?.backups)?data.backups:[];
  if(!backups.length) return '<div class="stg-hint">No backups yet. Create one before a cache rebuild or other maintenance.</div>';
  return `<div class="maintenance-backups">${backups.map(backup=>{
    const rawId=typeof backup.id==="string"?backup.id:"";
    const id=/^\d{8}T\d{6}Z-[a-f0-9]{32}-(?:scheduled|manual|pre-restore|safeguard)\.json$/.test(rawId)?rawId:"";
    const reason=({scheduled:"Scheduled backup",manual:"Manual backup","pre-restore":"Before restore",safeguard:"Safety snapshot"})[backup.reason]||"Backup";
    const details=backup.valid===false ? "Not restorable · "+maintenanceBytes(backup.bytes)
      : [maintenanceCount(backup.files)+" files",maintenanceCount(backup.sessions)+" stored sessions",maintenanceBytes(backup.bytes)].join(" · ");
    return `<div class="maintenance-backup"><div><b>${maintenanceEsc(reason)}</b><div class="stg-hint">${maintenanceEsc(maintenanceWhen(backup.created_at))} · ${maintenanceEsc(details)}</div></div>
      <button class="btn" data-maint-action="restore" data-backup-id="${maintenanceEsc(id)}"${id&&backup.valid!==false?"":" disabled"}>Restore</button></div>`;
  }).join("")}</div>`;
}
async function loadMaintenance(){
  const root=maintenanceRoot||document.getElementById("maintenanceSettings");
  if(!root) return;
  maintenanceRoot=root;
  const id=++maintenanceLoadId;
  const diag=root.querySelector("[data-maint-diagnostics]");
  const backups=root.querySelector("[data-maint-backups]");
  if(!diag||!backups) return;
  diag.innerHTML='<div class="stg-hint">Loading diagnostics…</div>';
  backups.innerHTML='<div class="stg-hint">Loading backups…</div>';
  try{
    const [health,backupData]=await Promise.all([
      maintenanceJson("/api/diagnostics"),maintenanceJson("/api/backups")
    ]);
    if(id!==maintenanceLoadId||root!==maintenanceRoot||!root.isConnected) return;
    diag.innerHTML=renderMaintenanceDiagnostics(health);
    backups.innerHTML=renderMaintenanceBackups(backupData);
    if(maintenanceWorking) setMaintenanceBusy(true);
    if(backupData.error) maintenanceMessage(safeMaintenanceError(new Error(backupData.error),"Backup details could not be read."),"err");
    else if(health.error||health.backup_error) maintenanceMessage(safeMaintenanceError(new Error(health.error||health.backup_error),"Check free space and backup permissions."),"err");
    else maintenanceMessage("");
  }catch(error){
    if(id!==maintenanceLoadId||root!==maintenanceRoot||!root.isConnected) return;
    diag.innerHTML='<div class="stg-hint">Could not load source diagnostics.</div>';
    backups.innerHTML='<div class="stg-hint">Could not load backups. Check that the dashboard is running, then retry.</div>';
    maintenanceMessage(safeMaintenanceError(error,"Could not load maintenance details. Check that the dashboard is running, then retry."),"err");
  }
}
function renderMaintenance(root=document.getElementById("maintenanceSettings")){
  if(!root) return;
  maintenanceRoot=root;
  maintenanceLoadId++;
  root.innerHTML=`<section class="maintenance-panel">
    <h2 class="stg-h stg-sec">Troubleshooting</h2>
    <div data-maint-diagnostics><div class="stg-hint">Loading diagnostics…</div></div>
    <h2 class="stg-h stg-sec">Backups</h2>
    <div class="stg-note">A local backup is kept on the first save each UTC day, plus safety copies before updates and cache resets. Keeps 7 daily copies and up to 5 each of manual, pre-restore and recovery copies.</div>
    <div class="stg-actions"><button class="btn primary" data-maint-action="create">Back up now</button><button class="btn" data-maint-action="refresh">Refresh</button></div>
    <div data-maint-backups><div class="stg-hint">Loading backups…</div></div>
    <div class="stg-msg" data-maint-msg aria-live="polite"></div>
  </section>`;
  if(!root.dataset.maintenanceBound){
    root.dataset.maintenanceBound="1";
    root.addEventListener("click",maintenanceClick);
  }
  loadMaintenance();
}
async function maintenanceClick(event){
  const button=event.target?.closest?.("button[data-maint-action]");
  if(!button||!maintenanceRoot?.contains(button)||maintenanceWorking) return;
  const action=button.dataset.maintAction;
  if(action==="refresh"){loadMaintenance();return;}
  setMaintenanceBusy(true); maintenanceMessage(action==="create"?"Creating backup…":"Preparing restore preview…");
  try{
    if(action==="create"){
      await maintenancePost("/api/backups",{action:"create"});
      await loadMaintenance();
      maintenanceMessage("Backup created.","ok");
      return;
    }
    if(action!=="restore") return;
    const actionRoot=maintenanceRoot, actionLoadId=maintenanceLoadId;
    const backupId=button.dataset.backupId||"";
    const preview=await maintenancePost("/api/backups",{action:"preview",id:backupId});
    if(!maintenanceStillCurrent(actionRoot,actionLoadId)) return;
    const backup=preview.backup||{}, current=preview.current||{};
    const body=`<p>Restore the backup from <b>${maintenanceEsc(maintenanceWhen(backup.created_at))}</b> (${maintenanceEsc(maintenanceCount(backup.sessions))} stored sessions, ${maintenanceEsc(maintenanceBytes(backup.bytes))})?</p>
      <p>The current ledger has ${maintenanceEsc(maintenanceCount(current.sessions))} stored sessions. Restoring replaces it with the selected backup. AgentTelemetry will save a safety snapshot of the current ledger first.</p>
      <p>Restoring may replace archived history. Continue only if this is the version you want to use.</p>`;
    const confirmed=await askConfirm({title:"Restore analytics backup?",body,ok:"Restore backup",danger:true});
    if(!confirmed){maintenanceMessage("Restore cancelled.");return;}
    if(!maintenanceStillCurrent(actionRoot,actionLoadId)) return;
    if(!preview.ticket) throw new Error("The restore preview expired. Refresh the backup list and try again.");
    await maintenancePost("/api/backups",{action:"restore",ticket:preview.ticket,confirmed:true});
    if(typeof load==="function") await load();
    await loadMaintenance();
    maintenanceMessage("Backup restored.","ok");
  }catch(error){
    maintenanceMessage(safeMaintenanceError(error,"Maintenance action failed. Refresh the backup list and check the current ledger status before retrying."),"err");
  }finally{setMaintenanceBusy(false);}
}
