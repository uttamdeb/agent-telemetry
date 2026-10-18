'use strict';

// Deliberately list every distributable file. Never copy a checkout directory:
// it may contain a user's sole remaining ledger or private device metadata.
module.exports = Object.freeze([
  'dashboard.py', 'editor_snapshot.py', 'parser.py', 'native.py',
  'session_names.py', 'accounts.py', 'local_servers.py',
  'local_servers_windows.py', 'ledger_backups.py', 'session_timeline.py',
  'diagnostics.py', 'index.html', 'chart.umd.min.js', 'manifest.json', 'sw.js',
  'LICENSE',
  'static/app.css', 'static/charts.js', 'static/core.js', 'static/views.js',
  'static/insights.js', 'static/maintenance.js', 'static/timeline.js',
  'static/apple-touch-icon.png', 'static/icon-192.png', 'static/icon-512.png',
  'static/tool-icons/claude.svg', 'static/tool-icons/copilot.svg',
  'static/tool-icons/cursor.svg', 'static/tool-icons/hermes.svg',
  'static/tool-icons/openai.svg', 'static/tool-icons/openclaw.svg',
  'static/tool-icons/opencode.svg', 'static/tool-icons/README.md',
  'static/tool-icons/LICENSE.hermes.txt',
  'static/tool-icons/LICENSE.openclaw.txt',
  'static/tool-icons/LICENSE.simple-icons.txt'
]);
