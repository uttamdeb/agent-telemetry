'use strict';

const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const cp = require('node:child_process');
const {once} = require('node:events');

async function run() {
  // Codex/editor terminals can themselves run inside Electron's Node mode.
  // This test process must launch a real, isolated editor window instead.
  for (const key of Object.keys(process.env)) {
    if (/^(VSCODE_|ELECTRON_RUN_AS_NODE$)/.test(key)) delete process.env[key];
  }
  const root = path.resolve(__dirname, '..', '..');
  const extension = path.join(root, 'vscode');
  // macOS limits the editor's profile IPC socket to 103 bytes.
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'at-ed-'));
  const home = path.join(temporary, 'home');
  const workspace = path.join(temporary, 'fixture-project');
  const data = path.join(temporary, 'data');
  const profile = path.join(temporary, 'p');
  for (const directory of [home, workspace, data, path.join(profile, 'User')]) fs.mkdirSync(directory, {recursive: true});
  const fixtureEnvironment = {...process.env};
  // Explicit child-process test isolation, never change the user's environment.
  for (const name of ['HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_STATE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME']) fixtureEnvironment[name] = home;
  for (const name of Object.keys(fixtureEnvironment)) {
    if (/^PYTHON|^AGENT_TELEMETRY_|^(CODEX_HOME|CLAUDE_CONFIG_DIR|OPENCODE_DATA_DIR|HERMES_HOME|OPENCLAW_STATE_DIR)$/.test(name)) delete fixtureEnvironment[name];
  }
  const python = process.env.AT_EDITOR_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const backend = cp.spawn(python, ['-I', path.join(root, 'tests', 'preview_editor_extension.py'),
    '--backend', path.join(extension, 'backend'), '--workspace', workspace, '--data-dir', data], {
    cwd: root, env: fixtureEnvironment, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true
  });
  let stderr = '';
  backend.stderr.on('data', chunk => {stderr = (stderr + chunk).slice(-8192);});
  try {
    const ready = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture backend did not start: ' + stderr)), 20000);
      let buffer = '';
      backend.stdout.on('data', chunk => {
        buffer += chunk;
        if (!buffer.includes('\n')) return;
        clearTimeout(timer);
        try {resolve(JSON.parse(buffer.split('\n')[0]));} catch (error) {reject(error);}
      });
      backend.once('error', error => {clearTimeout(timer); reject(error);});
      backend.once('exit', code => {clearTimeout(timer); reject(new Error(`Fixture backend exited ${code}: ${stderr}`));});
    });
    fs.writeFileSync(path.join(profile, 'User', 'settings.json'), JSON.stringify({
      'agentTelemetry.port': ready.port, 'workbench.startupEditor': 'none',
      'workbench.colorTheme': 'Default Dark Modern', 'telemetry.telemetryLevel': 'off',
      'update.mode': 'none', 'extensions.autoUpdate': false, 'extensions.autoCheckUpdates': false,
      'security.workspace.trust.enabled': false
    }, null, 2));
    const {runTests} = require('@vscode/test-electron');
    const options = {
      extensionDevelopmentPath: extension,
      extensionTestsPath: path.join(extension, 'test', 'integration', 'index.js'),
      extensionTestsEnv: {...fixtureEnvironment, AT_EDITOR_TEST_PORT: String(ready.port), AT_EDITOR_TEST_DATA: data},
      launchArgs: [workspace, '--user-data-dir', profile, '--extensions-dir', path.join(temporary, 'extensions'),
        '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust', '--disable-telemetry',
        '--disable-updates', '--disable-extensions', '--disable-gpu', '--no-sandbox'],
      reuseMachineInstall: false
    };
    if (process.env.AT_EDITOR_TEST_EXECUTABLE) options.vscodeExecutablePath = process.env.AT_EDITOR_TEST_EXECUTABLE;
    else options.version = process.env.AT_EDITOR_TEST_VERSION || 'stable';
    await runTests(options);
  } finally {
    backend.kill('SIGTERM');
    if (backend.exitCode === null) await once(backend, 'exit');
    if (process.env.AT_EDITOR_KEEP_FIXTURE) console.log('Editor smoke artifacts: ' + temporary);
    else fs.rmSync(temporary, {recursive: true, force: true});
  }
}

run().catch(error => {console.error(error); process.exitCode = 1;});
