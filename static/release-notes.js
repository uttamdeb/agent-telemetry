/* Release notes UI. The initial API read and claim are local only; the explicit
   Load notes action is the only path here that asks the server to contact GitHub. */
const RELEASE_NOTES_STATE = {
  loaded:false, started:false, inFlight:false, deferred:false, retryAfter:0, pendingShow:null, pending:false, updateAvailable:false,
  release:null, latest:null, currentVersion:null, error:"", claim:null,
  getPromise:null, loadBusy:false, opener:null
};

function releaseNotesEscape(value){
  return String(value == null ? "" : value).replace(/[&<>"']/g,ch=>({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
  })[ch]);
}

function releaseNotesSafeUrl(value){
  if(typeof value!=="string" || value.length>2048) return "";
  try{
    const url=new URL(value);
    if(url.protocol!=="https:" || url.hostname.toLowerCase()!=="github.com" ||
       !/^\/uttamdeb\/agent-telemetry\/releases\/(?:tag\/[^/]+|latest)\/?$/i.test(url.pathname)) return "";
    return url.href;
  }catch(error){ return ""; }
}

function releaseNotesInlineUrl(value){
  if(typeof value!=="string" || value.length>2048 || /[\u0000-\u0020\u007f]/.test(value)) return "";
  try{
    const url=new URL(value);
    if(url.protocol!=="https:" || !url.hostname || url.username || url.password) return "";
    return url.href;
  }catch(error){ return ""; }
}

function releaseNotesInline(source){
  source=String(source||"").slice(0,48000);
  const token=/(!\[([^\]\n]*)\]\(([^)\n]*)\)|`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|\*([^*\n]+)\*|_([^_\n]+)_)/g;
  let out="", pos=0;
  while(pos<source.length){
    token.lastIndex=pos;
    const match=token.exec(source);
    if(!match){ out+=releaseNotesEscape(source.slice(pos)); break; }
    out+=releaseNotesEscape(source.slice(pos,match.index));
    if(match[1]&&match[1][0]==="!"){
      // Never turn release-note images into requests. Keep their useful caption.
      out+=releaseNotesEscape(match[2]);
    }else if(match[4]!==undefined){
      out+=`<code>${releaseNotesEscape(match[4])}</code>`;
    }else if(match[5]!==undefined){
      const label=releaseNotesEscape(match[5]), href=releaseNotesInlineUrl(match[6]);
      out+=href?`<a href="${releaseNotesEscape(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`:label;
    }else if(match[7]!==undefined || match[8]!==undefined){
      out+=`<strong>${releaseNotesEscape(match[7]||match[8])}</strong>`;
    }else{
      out+=`<em>${releaseNotesEscape(match[9]||match[10])}</em>`;
    }
    pos=token.lastIndex;
  }
  return out;
}

function releaseNotesTableCells(line){
  let text=String(line||"").trim();
  if(text.startsWith("|")) text=text.slice(1);
  if(text.endsWith("|")) text=text.slice(0,-1);
  const cells=[]; let cell="", escaped=false, ticks=false;
  for(const ch of text){
    if(escaped){ cell+=ch; escaped=false; continue; }
    if(ch==="\\"){ cell+=ch; escaped=true; continue; }
    if(ch==="`"){ ticks=!ticks; cell+=ch; continue; }
    if(ch==="|"&&!ticks){ cells.push(cell.trim()); cell=""; }
    else cell+=ch;
  }
  cells.push(cell.trim());
  return cells.slice(0,12);
}

function releaseNotesTableStart(lines,index){
  if(index+1>=lines.length || !lines[index].includes("|")) return false;
  const cells=releaseNotesTableCells(lines[index+1]);
  return cells.length>1 && cells.every(cell=>/^:?-{3,}:?$/.test(cell));
}

function releaseNotesMarkdown(markdown){
  const source=String(markdown||"").slice(0,48000);
  const lines=source.replace(/\r\n?/g,"\n").split("\n",3001).slice(0,3000);
  const out=[]; let paragraph=[], listType="", code=null;
  const flushParagraph=()=>{ if(paragraph.length){ out.push(`<p>${paragraph.map(releaseNotesInline).join("<br>")}</p>`); paragraph=[]; } };
  const closeList=()=>{ if(listType){ out.push(`</${listType}>`); listType=""; } };
  for(let i=0;i<lines.length;i++){
    const line=lines[i];
    if(code!==null){
      if(/^\s*```/.test(line)){
        out.push(`<pre><code>${releaseNotesEscape(code.join("\n"))}</code></pre>`); code=null;
      }else code.push(line);
      continue;
    }
    if(/^\s*```/.test(line)){
      flushParagraph(); closeList(); code=[]; continue;
    }
    if(!line.trim()){ flushParagraph(); closeList(); continue; }
    if(releaseNotesTableStart(lines,i)){
      flushParagraph(); closeList();
      const heads=releaseNotesTableCells(line), rows=[];
      i+=2;
      while(i<lines.length && rows.length<200 && lines[i].trim() && lines[i].includes("|")){
        rows.push(releaseNotesTableCells(lines[i])); i++;
      }
      i--;
      out.push(`<div class="release-notes-table-wrap"><table><thead><tr>${heads.map(cell=>`<th>${releaseNotesInline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${heads.map((_,n)=>`<td>${releaseNotesInline(row[n]||"")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const heading=/^\s{0,3}(#{1,4})\s+(.+?)\s*#*\s*$/.exec(line);
    if(heading){ flushParagraph(); closeList(); const level=heading[1].length; out.push(`<h${level}>${releaseNotesInline(heading[2])}</h${level}>`); continue; }
    if(/^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)){ flushParagraph(); closeList(); out.push('<hr>'); continue; }
    const unordered=/^\s*[-+*]\s+(.+)$/.exec(line), ordered=/^\s*\d+[.)]\s+(.+)$/.exec(line);
    if(unordered||ordered){
      flushParagraph(); const nextType=unordered?"ul":"ol";
      if(listType!==nextType){ closeList(); listType=nextType; out.push(`<${listType}>`); }
      out.push(`<li>${releaseNotesInline((unordered||ordered)[1])}</li>`); continue;
    }
    closeList(); paragraph.push(line);
  }
  flushParagraph(); closeList();
  if(code!==null) out.push(`<pre><code>${releaseNotesEscape(code.join("\n"))}</code></pre>`);
  return out.join("");
}

function releaseNotesDate(value){
  if(typeof value!=="string" || !value) return "";
  const parsed=new Date(value);
  return Number.isNaN(parsed.getTime())?value:parsed.toLocaleDateString(undefined,{year:"numeric",month:"long",day:"numeric"});
}

function releaseNotesSnapshot(payload){
  if(!payload || typeof payload!=="object") return;
  if(Object.hasOwn(payload,"release") || Object.hasOwn(payload,"latest") || Object.hasOwn(payload,"pending")){
    RELEASE_NOTES_STATE.release=payload.release&&typeof payload.release==="object"?payload.release:null;
    RELEASE_NOTES_STATE.latest=payload.latest&&typeof payload.latest==="object"?payload.latest:null;
    RELEASE_NOTES_STATE.pending=!!payload.pending;
    RELEASE_NOTES_STATE.updateAvailable=!!payload.update_available;
    RELEASE_NOTES_STATE.currentVersion=payload.current_version||RELEASE_NOTES_STATE.currentVersion;
    RELEASE_NOTES_STATE.error=payload.error?String(payload.error):"";
    RELEASE_NOTES_STATE.loaded=true;
  }
}

function releaseNotesInstalledFallback(){
  if(RELEASE_NOTES_STATE.release&&typeof RELEASE_NOTES_STATE.release==="object") return RELEASE_NOTES_STATE.release;
  const version=typeof RELEASE_NOTES_STATE.currentVersion==="string"?RELEASE_NOTES_STATE.currentVersion.trim():"";
  if(!version) return null;
  const tag="v"+version.replace(/^v/i,"");
  return {tag,name:"AgentTelemetry "+tag,url:"https://github.com/uttamdeb/agent-telemetry/releases/tag/"+encodeURIComponent(tag),
          body:"",empty:false,availability:"unavailable"};
}

async function releaseNotesGet(force){
  if(!force && RELEASE_NOTES_STATE.getPromise) return RELEASE_NOTES_STATE.getPromise;
  const request=(async()=>{
    const response=await fetch("/api/release-notes",{cache:"no-store"});
    const payload=await response.json();
    if(!response.ok) throw new Error(payload&&payload.error||"Release notes are unavailable.");
    releaseNotesSnapshot(payload);
    return payload;
  })();
  RELEASE_NOTES_STATE.getPromise=request;
  try{return await request;}finally{ if(RELEASE_NOTES_STATE.getPromise===request) RELEASE_NOTES_STATE.getPromise=null; }
}

function releaseNotesPayload(release){
  const item=release&&typeof release==="object"?release:{};
  const title=typeof item.name==="string"&&item.name.trim()?item.name.trim():"What's new";
  const tag=typeof item.tag==="string"?item.tag:"";
  const published=releaseNotesDate(item.published_at);
  const url=releaseNotesSafeUrl(item.url);
  const body=typeof item.body==="string"?item.body:"";
  const truncated=!!item.truncated||body.length>48000||body.split(/\r\n?|\n/).length>3000;
  let content;
  if(body.trim()) content=releaseNotesMarkdown(body);
  else if(!RELEASE_NOTES_STATE.currentVersion&&!item.tag&&!RELEASE_NOTES_STATE.error){
    content='<div class="release-notes-empty"><div class="release-notes-empty-mark" aria-hidden="true">?</div><h3>This build version can’t be identified</h3><p>Load the latest published notes, or check the build details before treating them as notes for this installation.</p><button type="button" class="btn sm" data-release-notes-load>Load latest notes</button></div>';
  }else if(item.availability==="available"){
    content=`<div class="release-notes-empty"><div class="release-notes-empty-mark" aria-hidden="true">✓</div><h3>No release notes were published${item.tag?` for ${releaseNotesEscape(item.tag)}`:" for this version"}</h3><p>${url?"The official release page is still available if you need more detail.":"There’s no published note text for this release."}</p></div>`;
  }else{
    const message=RELEASE_NOTES_STATE.error||"Load the notes when you’re online, or open the official release page.";
    content=`<div class="release-notes-empty"><div class="release-notes-empty-mark" aria-hidden="true">↗</div><h3>Release notes aren’t available offline yet</h3><p>${releaseNotesEscape(message)}</p><button type="button" class="btn sm" data-release-notes-load>Load notes</button></div>`;
  }
  return {title,tag,published,url,body,truncated,content};
}

function renderReleaseNotes(release){
  const dialog=document.getElementById("releaseNotesDialog");
  if(!dialog) return false;
  const item=releaseNotesPayload(release);
  const title=dialog.querySelector("[data-rn-title]"), meta=dialog.querySelector("[data-rn-meta]"), body=dialog.querySelector("[data-rn-body]"), footer=dialog.querySelector("[data-rn-footer]");
  if(title) title.textContent=item.title;
  if(meta) meta.innerHTML=`${item.tag?`<span class="release-notes-tag">${releaseNotesEscape(item.tag)}</span>`:""}${item.published?`<span>${releaseNotesEscape(item.published)}</span>`:""}`;
  if(body) body.innerHTML=item.content+(item.truncated?'<p class="release-notes-truncated">This is an excerpt. Read the full notes on GitHub.</p>':"");
  if(footer) footer.innerHTML=`${item.url?`<a class="btn sm" href="${releaseNotesEscape(item.url)}" target="_blank" rel="noopener noreferrer">Read on GitHub <span aria-hidden="true">↗</span></a>`:""}<button type="button" class="btn sm primary" data-release-notes-close>Done</button>`;
  return true;
}

function releaseNotesOtherDialogOpen(dialog){
  if(typeof document.querySelectorAll!=="function") return false;
  return [...document.querySelectorAll("dialog[open]")].some(open=>open!==dialog);
}

async function showReleaseNotes(release,options){
  options=options||{};
  const dialog=document.getElementById("releaseNotesDialog");
  if(!dialog || dialog.open || document.visibilityState==="hidden" || releaseNotesOtherDialogOpen(dialog)) return false;
  renderReleaseNotes(release);
  RELEASE_NOTES_STATE.opener=options.opener||document.activeElement;
  try{ dialog.showModal(); }
  catch(error){ RELEASE_NOTES_STATE.opener=null; return false; }
  if(options.claim){
    RELEASE_NOTES_STATE.pendingShow=null;
    // Acknowledge only once the modal has actually entered the top layer.
    try{
      const response=await fetch("/api/release-notes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"shown",claim:options.claim})});
      if(!response.ok) RELEASE_NOTES_STATE.retryAfter=Date.now()+30000;
      else RELEASE_NOTES_STATE.pendingShow=null;
    }catch(error){ /* The lease expires and can be claimed on the next open. */ }
  }
  return true;
}

async function checkForReleaseNotes(){
  if(RELEASE_NOTES_STATE.started || RELEASE_NOTES_STATE.inFlight || !document.getElementById("releaseNotesDialog")) return false;
  if(document.visibilityState==="hidden" || releaseNotesOtherDialogOpen(document.getElementById("releaseNotesDialog"))){
    releaseNotesDefer(); return false;
  }
  if(Date.now()<RELEASE_NOTES_STATE.retryAfter) return false;
  RELEASE_NOTES_STATE.inFlight=true;
  let snapshot;
  try{ snapshot=await releaseNotesGet(false); }
  catch(error){ RELEASE_NOTES_STATE.error="Release notes could not be read."; RELEASE_NOTES_STATE.retryAfter=Date.now()+30000; RELEASE_NOTES_STATE.inFlight=false; return false; }
  if(document.visibilityState==="hidden" || releaseNotesOtherDialogOpen(document.getElementById("releaseNotesDialog"))){
    RELEASE_NOTES_STATE.inFlight=false; releaseNotesDefer(); return false;
  }
  let claim;
  try{
    const response=await fetch("/api/release-notes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"claim"})});
    claim=await response.json();
    if(!response.ok) throw new Error("claim failed");
  }catch(error){ RELEASE_NOTES_STATE.error="Release notes could not be prepared."; RELEASE_NOTES_STATE.retryAfter=Date.now()+30000; RELEASE_NOTES_STATE.inFlight=false; return false; }
  RELEASE_NOTES_STATE.inFlight=false;
  RELEASE_NOTES_STATE.started=true;
  if(!claim || !claim.pending || !claim.claim) return false;
  const release=claim.release&&typeof claim.release==="object"?claim.release:(releaseNotesInstalledFallback()||snapshot.release||RELEASE_NOTES_STATE.release);
  if(!release) return false;
  RELEASE_NOTES_STATE.claim=claim.claim;
  RELEASE_NOTES_STATE.pendingShow={release,claim:claim.claim,claimedAt:Date.now()};
  const opened=await showReleaseNotes(release,{claim:claim.claim});
  if(!opened) releaseNotesDefer();
  return opened;
}

function releaseNotesResume(){
  if(document.visibilityState==="hidden" || releaseNotesOtherDialogOpen(document.getElementById("releaseNotesDialog"))) return;
  if(RELEASE_NOTES_STATE.deferred){
    document.removeEventListener("visibilitychange",releaseNotesResume);
    document.removeEventListener("close",releaseNotesResume,true);
    RELEASE_NOTES_STATE.deferred=false;
  }
  if(RELEASE_NOTES_STATE.pendingShow){
    const pending=RELEASE_NOTES_STATE.pendingShow;
    // Claims expire after two minutes. Reclaim before opening if a hidden page
    // or another modal held the presentation long enough to outlive its lease.
    if(!pending.claimedAt || Date.now()-pending.claimedAt>=110000){
      RELEASE_NOTES_STATE.pendingShow=null;
      RELEASE_NOTES_STATE.claim=null;
      RELEASE_NOTES_STATE.started=false;
      checkForReleaseNotes();
      return;
    }
    showReleaseNotes(pending.release,{claim:pending.claim}).then(opened=>{if(!opened) releaseNotesDefer();});
  }else checkForReleaseNotes();
}

function releaseNotesDefer(){
  if(RELEASE_NOTES_STATE.deferred) return;
  RELEASE_NOTES_STATE.deferred=true;
  document.addEventListener("visibilitychange",releaseNotesResume);
  document.addEventListener("close",releaseNotesResume,true);
}

async function openReleaseNotes(opener){
  let release=releaseNotesInstalledFallback()||RELEASE_NOTES_STATE.latest;
  if(!RELEASE_NOTES_STATE.loaded){
    try{ await releaseNotesGet(false); }catch(error){ RELEASE_NOTES_STATE.error="Release notes could not be read."; }
    release=releaseNotesInstalledFallback()||RELEASE_NOTES_STATE.latest;
  }
  return showReleaseNotes(release||{}, {opener});
}

async function loadReleaseNotes(){
  if(RELEASE_NOTES_STATE.loadBusy) return false;
  const dialog=document.getElementById("releaseNotesDialog");
  if(!dialog || !dialog.open) return false;
  RELEASE_NOTES_STATE.loadBusy=true;
  dialog.querySelectorAll("[data-release-notes-load]").forEach(button=>{button.disabled=true;button.textContent="Loading notes…";});
  try{
    const response=await fetch("/api/release-notes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"check"})});
    const result=await response.json();
    if(!response.ok) throw new Error(result&&result.error||"Could not check for release notes.");
    releaseNotesSnapshot(result);
    // For a known installed version, never substitute the latest release's body.
    // Only the explicit unknown-version "Load latest notes" action may do that.
    const release=RELEASE_NOTES_STATE.currentVersion
      ?(releaseNotesInstalledFallback()||{})
      :(result.latest||RELEASE_NOTES_STATE.latest||result.release||{});
    RELEASE_NOTES_STATE.loadBusy=false;
    renderReleaseNotes(release);
    return true;
  }catch(error){
    RELEASE_NOTES_STATE.error="Release notes could not be loaded. Check your connection and try again.";
    RELEASE_NOTES_STATE.loadBusy=false;
    renderReleaseNotes(releaseNotesInstalledFallback()||{});
    return false;
  }
}

function releaseNotesLink(){
  return '<span class="ver-a"><button class="link" type="button" data-release-notes-open>What’s new</button></span>';
}

function releaseNotesDownloadAction(result){
  if(!result || !result.download_only) return "";
  const notes=result.release_notes&&typeof result.release_notes==="object"?result.release_notes:{};
  if(!notes.update_available) return "";
  const release=notes.latest||notes.release||{};
  const url=releaseNotesSafeUrl(release.url);
  if(!url) return '<span class="ver-err">Update available</span>';
  const label=release.tag||release.name||"new release";
  return `<a class="link" href="${releaseNotesEscape(url)}" target="_blank" rel="noopener noreferrer">Download ${releaseNotesEscape(label)}</a>`;
}

document.addEventListener("click",event=>{
  const open=event.target.closest("[data-release-notes-open]");
  if(open){ event.preventDefault(); openReleaseNotes(open); return; }
  const close=event.target.closest("[data-release-notes-close]");
  if(close){ event.preventDefault(); document.getElementById("releaseNotesDialog")?.close(); return; }
  const load=event.target.closest("[data-release-notes-load]");
  if(load){ event.preventDefault(); loadReleaseNotes(); }
});

document.addEventListener("close",event=>{
  if(event.target&&event.target.id==="releaseNotesDialog"){
    let target=RELEASE_NOTES_STATE.opener;
    RELEASE_NOTES_STATE.opener=null;
    if(!target||!target.isConnected) target=typeof document.querySelector==="function"?document.querySelector('[data-release-notes-open]'):null;
    if(target&&typeof target.focus==="function") target.focus();
  }
},true);
