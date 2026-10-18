'use strict';

// No vscode import: this shell can also be rendered by the fixture preview.
function escapeAttribute(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));
}

function getHtml(webview, extensionUri, nonce) {
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(nonce)) throw new Error('Invalid webview nonce');
  const mediaUri = name => escapeAttribute(webview.asWebviewUri(extensionUri.with({
    path: extensionUri.path.replace(/\/$/, '') + '/media/' + name
  })).toString());
  const source = escapeAttribute(webview.cspSource);
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${source}; style-src ${source}; script-src 'nonce-${nonce}'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none';">
  <title>AgentTelemetry · Agent desk</title>
  <link rel="stylesheet" href="${mediaUri('desk.css')}">
  <script nonce="${nonce}" src="${mediaUri('desk.js')}" defer></script>
</head>
<body>
  <a class="skip-link" href="#sessions-heading">Skip to sessions</a>
  <div class="desk">
    <header class="topbar">
      <div class="brand"><img class="brand-mark" src="${mediaUri('app-icon.png')}" alt="" width="28" height="28"><span>AgentTelemetry<span class="brand-separator">/</span><strong>Agent desk</strong></span></div>
      <nav aria-label="Agent desk actions" class="top-actions">
        <button type="button" class="quiet" data-action="refresh" data-focus="refresh" title="Refresh recorded usage"><span aria-hidden="true">↻</span> Refresh</button>
        <button type="button" class="quiet" data-action="openBrowser" data-focus="browser" title="Open the full local dashboard">Dashboard <span aria-hidden="true">↗</span></button>
        <button type="button" class="quiet icon-button" data-action="openSettings" data-focus="settings" title="AgentTelemetry settings" aria-label="AgentTelemetry settings">⚙</button>
      </nav>
    </header>
    <main>
      <div class="connection-line"><span id="connection-dot" class="status-dot" aria-hidden="true"></span><span id="connection-label" role="status">Connecting to local monitoring…</span><span id="connection-editor" class="muted"></span></div>
      <section class="notice" id="notice" aria-label="Monitoring status" hidden></section>
      <section class="controls" aria-label="Usage scope and range">
        <div class="segmented" role="group" aria-label="Project scope">
          <button type="button" data-scope="workspace" data-focus="scope-workspace" aria-pressed="true">This workspace</button>
          <button type="button" data-scope="all" data-focus="scope-all" aria-pressed="false">All local projects</button>
        </div>
        <div class="segmented range-controls" role="group" aria-label="Date range">
          <button type="button" data-range="today" data-focus="range-today" aria-pressed="true">Today</button>
          <button type="button" data-range="7d" data-focus="range-7d" aria-pressed="false">7 days</button>
          <button type="button" data-range="30d" data-focus="range-30d" aria-pressed="false">30 days</button>
        </div>
      </section>
      <section class="brief" aria-labelledby="workspace-title">
        <div class="brief-heading"><p id="scope-caption" class="eyebrow">WORKSPACE BRIEF</p><p id="date-label" class="muted"></p></div>
        <h1 id="workspace-title">Your recorded agent work</h1>
        <div class="brief-body">
          <div class="hero"><div class="hero-number"><span id="total-tokens">—</span><span class="hero-unit">tokens</span></div><p><span id="total-cost">—</span> <span class="muted">estimated API cost</span></p></div>
          <div class="brief-evidence" id="brief-evidence"><p class="muted">A local view of the usage your tools record.</p></div>
        </div>
        <div id="day-strip" class="day-strip" hidden></div>
      </section>
      <section class="since" aria-labelledby="since-heading">
        <span class="since-symbol" aria-hidden="true">↳</span><div class="since-copy"><h2 id="since-heading">Recorded in this window</h2><p id="since-description" class="muted">An in-memory counter of newly recorded usage.</p></div><p id="since-value" class="since-value">—</p><button class="quiet" type="button" data-action="resetBaseline" data-focus="baseline" title="Start a new in-memory recorded-usage counter">Reset counter</button>
      </section>
      <section class="session-workspace" aria-labelledby="sessions-heading">
        <div class="session-section-heading"><div><p class="eyebrow">FOLLOW THE WORK</p><h2 id="sessions-heading" tabindex="-1">Sessions &amp; delegation</h2></div><span id="session-count" class="muted"></span></div>
        <p class="section-note">Recorded session roles and parent links. Missing relationships stay unavailable.</p>
        <div class="session-controls"><div class="segmented" role="group" aria-label="Session type">
          <button type="button" data-session-filter="all" data-focus="sessions-all" aria-pressed="true">All sessions</button>
          <button type="button" data-session-filter="main" data-focus="sessions-main" aria-pressed="false">Main agents</button>
          <button type="button" data-session-filter="subagents" data-focus="sessions-subagents" aria-pressed="false">Subagents</button>
        </div></div>
        <div class="workspace-split">
          <div class="sessions-pane"><div id="session-tree" role="tree" aria-label="Recorded sessions and subagent relationships" aria-describedby="tree-help"></div><p id="tree-help" class="tree-help muted">↑ ↓ to move · ← → to fold · Enter to inspect</p><p id="session-cap" class="muted" hidden></p></div>
          <section class="session-detail" id="session-detail" aria-label="Selected session"><div class="detail-placeholder"><span aria-hidden="true">⌁</span><h3>A closer look at the work</h3><p>Select a session to inspect its recorded usage, model, effort and context compactions.</p></div></section>
        </div>
      </section>
      <section class="reading-notes" aria-label="How to read these numbers"><h2>What these numbers mean</h2><p>Usage comes from local tool logs. It can arrive after the work happened; it does not indicate which agents are running.</p><p>Cost is an estimate at API list prices, not a bill or subscription allowance. Parent and child rows show their own recorded usage; do not add a child to a parent that already includes it.</p><ul id="limitations"></ul></section>
    </main>
    <footer><span><span class="local-mark" aria-hidden="true">◉</span> On this machine</span><span id="snapshot-time">Waiting for a local snapshot</span></footer>
  </div>
</body>
</html>`;
}

module.exports = {getHtml};
