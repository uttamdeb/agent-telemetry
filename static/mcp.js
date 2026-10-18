/* Local, read-only MCP consent UI. This module never edits an MCP client config. */
let mcpSettingsGeneration = 0;
let mcpSettingsActive = false;
let mcpSettingsRoot = null;
let mcpSettingsState = null;
let mcpSettingsBusy = false;
let mcpSettingsBusyLabel = "";
let mcpSettingsIntent = 0;

const MCP_CLOUD_WARNING = "MCP access is off by default. When enabled, AgentTelemetry returns selected local telemetry to this client; a cloud-backed agent may send tool results to its provider. Project and device names, and data from other devices, require separate opt-in scopes.";

function mcpSettingsPanelHTML(){
  return `<section class="mcp-settings" id="mcpSettingsBox">
    <h2 class="stg-h stg-sec">Local MCP access</h2>
    <div class="stg-note">MCP gives a compatible AI client read-only access to selected AgentTelemetry analytics. It cannot edit your logs, settings, accounts, or source sessions. Usage remains on this machine until a client asks for it.</div>
    <div class="stg-note mcp-cloud-warning" id="mcpSettingsWarning" role="note">${MCP_CLOUD_WARNING}</div>
    <div id="mcpSettingsState"><div class="stg-hint">Loading MCP access settings…</div></div>
    <div class="stg-msg" id="mcpSettingsMsg" role="status"></div>
  </section>`;
}

function beginMcpSettings(){
  mcpSettingsGeneration++;
  mcpSettingsActive = true;
  mcpSettingsRoot = null;
  mcpSettingsState = null;
  mcpSettingsBusy = false;
  mcpSettingsBusyLabel = "";
  mcpSettingsIntent++;
  return mcpSettingsGeneration;
}

function mcpSettingsGenerationCurrent(generation){
  return mcpSettingsActive && generation === mcpSettingsGeneration;
}

function closeMcpSettings(){
  mcpSettingsGeneration++;
  mcpSettingsActive = false;
  mcpSettingsRoot = null;
  mcpSettingsState = null;
  mcpSettingsBusy = false;
  mcpSettingsBusyLabel = "";
  mcpSettingsIntent++;
}

function mcpSettingsCurrent(generation, root){
  return mcpSettingsGenerationCurrent(generation) && root === mcpSettingsRoot
    && root?.isConnected === true && document.getElementById("mcpSettingsBox") === root;
}

function mcpSettingsKnown(state){
  return !!state && typeof state === "object" && !Array.isArray(state)
    && typeof state.enabled === "boolean"
    && typeof state.include_names === "boolean"
    && typeof state.include_peers === "boolean";
}

function mcpSafeError(value, fallback){
  if(typeof value !== "string") return fallback;
  const text = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  if(!text || text.length > 240 || /(?:[A-Z]:[\\/]|\\\\[^\\]+\\|\/(?:Users|home|private|tmp|var)\/)/i.test(text)) return fallback;
  return text;
}

function mcpValidPath(value){
  return typeof value === "string" && value.length > 0 && value.length <= 2048
    && !/[\u0000-\u001f\u007f]/.test(value)
    && /^(?:[A-Za-z]:[\\/]|\/)/.test(value);
}

function mcpClientConfigText(value){
  if(!value || typeof value !== "object" || Array.isArray(value)) return "";
  const servers = value.mcpServers;
  const server = servers && typeof servers === "object" && !Array.isArray(servers)
    ? servers["agent-telemetry"] : null;
  if(!server || typeof server !== "object" || Array.isArray(server) || !mcpValidPath(server.command)) return "";
  const args = server.args;
  if(!Array.isArray(args) || args.length !== 3 || !mcpValidPath(args[0])
      || args[1] !== "--backend" || typeof args[2] !== "string"
      || !/^http:\/\/(?:127\.0\.0\.1|\[::1\]):[1-9]\d{0,4}$/.test(args[2])) return "";
  const port = Number(args[2].slice(args[2].lastIndexOf(":") + 1));
  if(!Number.isInteger(port) || port > 65535) return "";
  return JSON.stringify({mcpServers:{"agent-telemetry":{command:server.command,args:[args[0],"--backend",args[2]]}}},null,2);
}

function mcpSettingsMessage(root, text, kind=""){
  const node = root?.querySelector?.("#mcpSettingsMsg");
  if(!node) return;
  node.textContent = text || "";
  node.className = "stg-msg" + (kind ? " "+kind : "");
}

function mcpBindSettings(root, generation){
  if(root.dataset?.mcpBound === "1") return;
  root.dataset.mcpGeneration = String(generation);
  if(root.dataset) root.dataset.mcpBound = "1";
  else root._mcpBound = true;
  root.addEventListener("change", event=>{
    const control = event.target;
    const key = control?.dataset?.mcpSetting;
    if(!key || !mcpSettingsCurrent(generation, root)) return;
    if(!mcpSettingsKnown(mcpSettingsState) || mcpSettingsBusy || control.disabled){
      return mcpFetchSettings(root, generation);
    }
    if(!["enabled","include_names","include_peers"].includes(key)) return;
    const next = {...mcpSettingsState, [key]:control.checked === true};
    return mcpSaveSettings(root, generation, next);
  });
  root.addEventListener("click", event=>{
    const action = event.target?.closest?.("[data-mcp-action]")?.dataset?.mcpAction
      || event.target?.dataset?.mcpAction;
    if(!action || !mcpSettingsCurrent(generation, root)) return;
    if(action === "retry") return mcpFetchSettings(root, generation);
    if(action === "copy") return mcpCopyConfig(root, generation);
    if(action === "revoke") return mcpSaveSettings(root, generation,
      {enabled:false,include_names:false,include_peers:false}, {revoke:true});
  });
}

function mcpRenderSettings(root, generation, state, message="", error=false){
  if(!mcpSettingsCurrent(generation, root)) return;
  mcpSettingsState = mcpSettingsKnown(state) ? state : null;
  const warning = root.querySelector?.("#mcpSettingsWarning");
  if(warning) warning.textContent = typeof state?.warning === "string" && state.warning.trim() && state.warning.length <= 1000
    ? state.warning : MCP_CLOUD_WARNING;
  const body = root.querySelector?.("#mcpSettingsState");
  if(!body) return;
  const known = mcpSettingsKnown(mcpSettingsState);
  const current = mcpSettingsState;
  const disabled = !known || mcpSettingsBusy;
  const configText = known && current.enabled ? mcpClientConfigText(state.client_config) : "";
  const status = known ? (current.enabled ? "Enabled" : "Off") : "Status unavailable";
  const revoke = !known || current.enabled;
  body.innerHTML = `<div class="mcp-status"><b>Access: ${status}</b>${mcpSettingsBusy?` · ${mcpSettingsBusyLabel||"Working…"}`:""}</div>
    <label class="mcp-toggle" style="display:flex;align-items:center;gap:10px;margin-top:16px"><input type="checkbox" data-mcp-setting="enabled" style="width:16px;height:16px;padding:0;margin:0;flex:0 0 16px" ${known&&current.enabled?"checked":""} ${disabled?"disabled":""}> Enable local MCP access</label>
    <div class="stg-hint mcp-scope-intro">Choose separately what the client can see. Changes take effect only while access is enabled.</div>
    <label class="mcp-toggle" style="display:flex;align-items:center;gap:10px;margin-top:12px"><input type="checkbox" data-mcp-setting="include_names" style="width:16px;height:16px;padding:0;margin:0;flex:0 0 16px" ${known&&current.include_names?"checked":""} ${disabled?"disabled":""}> Include project and device display names</label>
    <label class="mcp-toggle" style="display:flex;align-items:center;gap:10px;margin-top:12px"><input type="checkbox" data-mcp-setting="include_peers" style="width:16px;height:16px;padding:0;margin:0;flex:0 0 16px" ${known&&current.include_peers?"checked":""} ${disabled?"disabled":""}> Include usage from other connected devices</label>
    ${known&&current.enabled?`<div class="mcp-config"><div class="stg-hint">Copy this read-only setup into your MCP client. AgentTelemetry never edits client files or includes an access key in this configuration.</div>
      ${configText?`<textarea class="field" id="mcpClientConfig" rows="7" readonly spellcheck="false" aria-label="MCP client configuration" style="display:block;width:100%;box-sizing:border-box;margin-top:10px;resize:vertical;font-family:var(--mono)"></textarea>
        <div class="stg-actions"><button class="btn" type="button" data-mcp-action="copy">Copy configuration</button></div>`:
        `<div class="stg-hint mcp-config-missing">Client configuration is unavailable. Retry the connection to AgentTelemetry.</div>`}</div>`:""}
    <div class="stg-actions mcp-actions"><button class="btn" type="button" data-mcp-action="retry" ${mcpSettingsBusy?"disabled":""}>Retry</button>
      ${revoke?`<button class="btn" type="button" data-mcp-action="revoke" ${mcpSettingsBusy?"disabled":""}>Revoke access</button>`:""}</div>`;
  if(configText){
    const textarea = body.querySelector?.("#mcpClientConfig");
    if(textarea) textarea.value = configText;
  }
  mcpSettingsMessage(root, message, error ? "err" : message ? "ok" : "");
}

async function mcpResponseJson(response){
  let body = {};
  try{ body = await response.json(); }catch{}
  if(!response.ok) throw new Error(mcpSafeError(body?.error, "Could not read MCP access settings."));
  return body;
}

async function mcpFetchSettings(root=mcpSettingsRoot, generation=mcpSettingsGeneration, message="", error=false){
  if(!mcpSettingsCurrent(generation, root)) return;
  if(mcpSettingsBusy) return;
  const intent = ++mcpSettingsIntent;
  mcpSettingsBusy = false;
  mcpSettingsBusyLabel = "";
  mcpRenderSettings(root, generation, null, "Loading MCP access settings…");
  try{
    const response = await fetch("/api/mcp", {cache:"no-store"});
    const state = await mcpResponseJson(response);
    if(!mcpSettingsCurrent(generation, root) || intent !== mcpSettingsIntent) return;
    if(!mcpSettingsKnown(state)){
      mcpRenderSettings(root, generation, null, message || "AgentTelemetry returned an unrecognized access state. Retry or revoke access.", true);
      return;
    }
    mcpRenderSettings(root, generation, state, message, error);
  }catch(e){
    if(!mcpSettingsCurrent(generation, root) || intent !== mcpSettingsIntent) return;
    mcpRenderSettings(root, generation, null, message || mcpSafeError(e?.message, "Could not read MCP access settings."), true);
  }
}

function mcpNeedsConfirmation(next, previous){
  return (next.enabled && !previous.enabled)
    || (next.include_names && !previous.include_names)
    || (next.include_peers && !previous.include_peers);
}

function mcpConfirmation(next, previous){
  const details = [];
  if(next.enabled && !previous.enabled) details.push("The selected AgentTelemetry analytics will be available to the configured local MCP client.");
  if(next.include_names && !previous.include_names) details.push("Project and device display names will be included with matching results.");
  if(next.include_peers && !previous.include_peers) details.push("Usage from other connected devices will be included with matching results.");
  return {
    title:next.enabled&&!previous.enabled?"Enable local MCP access?":"Share more analytics with MCP?",
    ok:next.enabled&&!previous.enabled?"Enable access":"Save choices",
    body:`<p>${details.join(" ")}</p><div class="modal-note">A cloud-backed AI client may send tool results to its provider. You can revoke access in Settings.</div>`,
  };
}

async function mcpSaveSettings(root, generation, next, options={}){
  if(!mcpSettingsCurrent(generation, root) || mcpSettingsBusy) return;
  const intent = ++mcpSettingsIntent;
  const previous = mcpSettingsKnown(mcpSettingsState)
    ? {enabled:mcpSettingsState.enabled,include_names:mcpSettingsState.include_names,include_peers:mcpSettingsState.include_peers}
    : {enabled:false,include_names:false,include_peers:false};
  const revoke = options?.revoke === true;
  let confirmed = false;
  if(!revoke && mcpNeedsConfirmation(next, previous)){
    mcpSettingsBusy = true;
    mcpSettingsBusyLabel = "Waiting for confirmation…";
    mcpRenderSettings(root, generation, mcpSettingsState, "Waiting for confirmation…");
    const accepted = typeof askConfirm === "function" && await askConfirm(mcpConfirmation(next, previous));
    if(!mcpSettingsCurrent(generation, root) || intent !== mcpSettingsIntent) return;
    if(!accepted){
      mcpSettingsBusy = false;
      mcpSettingsBusyLabel = "";
      const currentRoot = mcpSettingsRoot;
      await mcpFetchSettings(currentRoot, generation, "No changes made.");
      return;
    }
    confirmed = true;
  }
  mcpSettingsBusy = true;
  mcpSettingsBusyLabel = "Saving…";
  if(mcpSettingsRoot?.isConnected !== false)
    mcpRenderSettings(mcpSettingsRoot, generation, mcpSettingsState, "Saving MCP access settings…");
  const request = {enabled:next.enabled === true,include_names:next.include_names === true,
    include_peers:next.include_peers === true,confirmed};
  try{
    const response = await fetch("/api/mcp", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});
    const state = await mcpResponseJson(response);
    if(!mcpSettingsGenerationCurrent(generation) || intent !== mcpSettingsIntent) return;
    mcpSettingsBusy = false;
    mcpSettingsBusyLabel = "";
    const currentRoot = mcpSettingsRoot;
    if(mcpSettingsKnown(state) && currentRoot?.isConnected !== false)
      mcpRenderSettings(currentRoot, generation, state, revoke?"MCP access revoked.":"MCP access settings saved.", false);
    else if(currentRoot?.isConnected !== false)
      await mcpFetchSettings(currentRoot, generation, "Saved, but AgentTelemetry returned an unrecognized state.", true);
  }catch(e){
    if(!mcpSettingsGenerationCurrent(generation) || intent !== mcpSettingsIntent) return;
    mcpSettingsBusy = false;
    mcpSettingsBusyLabel = "";
    const failure = mcpSafeError(e?.message, "Could not save MCP access settings.");
    const currentRoot = mcpSettingsRoot;
    if(currentRoot?.isConnected !== false)
      await mcpFetchSettings(currentRoot, generation, failure+" Current settings were reloaded where possible.", true);
  }
}

async function mcpCopyConfig(root, generation){
  if(!mcpSettingsCurrent(generation, root)) return;
  const textarea = root.querySelector?.("#mcpClientConfig");
  if(!textarea || typeof textarea.value !== "string" || !textarea.value) return;
  try{
    if(typeof navigator !== "undefined" && navigator.clipboard?.writeText){
      await navigator.clipboard.writeText(textarea.value);
      if(mcpSettingsCurrent(generation, root)) mcpSettingsMessage(root, "Configuration copied.", "ok");
      return;
    }
  }catch{}
  if(!mcpSettingsCurrent(generation, root)) return;
  if(typeof textarea.focus === "function") textarea.focus();
  if(typeof textarea.select === "function") textarea.select();
  if(typeof textarea.setSelectionRange === "function") textarea.setSelectionRange(0, textarea.value.length);
  mcpSettingsMessage(root, "Configuration selected. Copy it with your keyboard shortcut.");
}

async function loadMcpSettings(){
  if(!mcpSettingsActive) return;
  const root = document.getElementById("mcpSettingsBox");
  if(!root) return;
  mcpSettingsRoot = root;
  const generation = mcpSettingsGeneration;
  mcpBindSettings(root, generation);
  if(mcpSettingsBusy){
    mcpRenderSettings(root, generation, mcpSettingsState, "MCP access operation is still in progress…");
    return;
  }
  await mcpFetchSettings(root, generation);
}
