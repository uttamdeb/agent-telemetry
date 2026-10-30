'use strict';

const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const files = require('../scripts/backend-files');
const {buildBackend} = require('../scripts/build-backend');

const dashboardRuntimeFiles = [
  'editor_snapshot.py',
  'cursor_data.py',
  'mcp_access.py', 'mcp_backend.py', 'mcp_protocol.py', 'mcp_server.py',
  'mcp_tools.py', 'telemetry_query.py', 'telemetry_insights.py',
  'static/mcp.js'
];

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'at-editor-package-'));
  fs.mkdirSync(path.join(root, 'vscode'));
  fs.writeFileSync(path.join(root, 'vscode', 'package.json'), '{"version":"2.0.1"}');
  fs.writeFileSync(path.join(root, 'pyproject.toml'), '[project]\nname = "agenttelemetry-app"\nversion = "2.0.0"\n');
  execFileSync('git', ['init', '-q', root]);
  execFileSync('git', ['-C', root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
    'commit', '--allow-empty', '-qm', 'Fixture']);
  for (const relative of files) {
    const target = path.join(root, relative);
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(target, relative === 'dashboard.py' ? 'CACHE_VERSION = 60\n' : `fixture ${relative}`);
  }
  return root;
}

function walk(root, prefix = '') {
  return fs.readdirSync(root, {withFileTypes: true}).flatMap(entry => {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    return entry.isDirectory() ? walk(path.join(root, entry.name), relative) : [relative];
  }).sort();
}

test('the store listing README points at every AgentTelemetry asset with absolute links', () => {
  const readme = fs.readFileSync(path.join(__dirname, '../README.md'), 'utf8');
  const desktop = readme.slice(readme.indexOf('## Get the desktop app'), readme.indexOf('## Follow the work'));
  assert.ok(desktop, 'the desktop app section sits right after the screenshot, ahead of the feature list');
  for (const required of [
    'https://github.com/uttamdeb/agent-telemetry/releases/latest', 'https://agenttelemetry-app.vercel.app/',
    'unsigned/unnotarized', 'Open Anyway', 'Run anyway', 'Apple Silicon or Intel', 'Windows 10/11 (x64']) {
    assert.ok(desktop.includes(required), `desktop app section is missing ${required}`);
  }
  for (const required of [
    'https://marketplace.visualstudio.com/items?itemName=agenttelemetry-team.agenttelemetry',
    'https://open-vsx.org/extension/agenttelemetry-team/agenttelemetry',
    'https://github.com/uttamdeb/agent-telemetry#quick-start']) {
    assert.ok(readme.includes(required), `README is missing ${required}`);
  }
  assert.match(readme, /\*\*No Python\?\*\*/);
  // vsce rewrites only the relative forms the VSIX verifier knows (screenshots, media, LICENSE); any other
  // relative link would be broken on both store pages.
  const relative = [...readme.matchAll(/\]\((?!https?:\/\/|#)([^)]+)\)/g)].map(match => match[1]);
  assert.deepEqual(relative.filter(link => !/^(screenshots\/|LICENSE$)/.test(link)), []);
});

test('backend allowlist includes dashboard editor and MCP runtime dependencies', () => {
  for (const relative of dashboardRuntimeFiles) {
    assert.ok(files.includes(relative), `backend allowlist is missing ${relative}`);
  }
});

test('backend packaging copies only the explicit allowlist, with byte digests', () => {
  const root = fixture();
  try {
    fs.writeFileSync(path.join(root, '.usage_cache.json'), 'PRIVATE LEDGER FIXTURE');
    fs.writeFileSync(path.join(root, '.peers.json'), 'PRIVATE DEVICE FIXTURE');
    fs.writeFileSync(path.join(root, 'static', 'unlisted-secret.txt'), 'PRIVATE FIXTURE');
    const target = path.join(root, 'vscode', 'backend');
    fs.mkdirSync(target);
    fs.writeFileSync(path.join(target, 'stale-module.py'), 'must disappear');
    const info = buildBackend(root, target);
    assert.deepEqual(walk(target), [...files, 'BUILD-INFO.json'].sort());
    assert.equal(info.version, '2.0.0', 'the app version (pyproject.toml), not the extension\'s');
    assert.equal(info.extension_version, '2.0.1');
    assert.equal(info.cache_version, 60);
    assert.match(info.source_commit, /^[a-f0-9]{40}$/);
    for (const relative of files) {
      assert.deepEqual(fs.readFileSync(path.join(target, relative)), fs.readFileSync(path.join(root, relative)));
      assert.match(info.files[relative], /^[a-f0-9]{64}$/);
    }
  } finally {fs.rmSync(root, {recursive: true, force: true});}
});

test('packaging validates all source files before replacing a working bundle', () => {
  const root = fixture();
  try {
    const target = path.join(root, 'vscode', 'backend');
    fs.mkdirSync(target);
    fs.writeFileSync(path.join(target, 'keep'), 'last working bundle');
    fs.unlinkSync(path.join(root, 'parser.py'));
    assert.throws(() => buildBackend(root, target), /ENOENT/);
    assert.equal(fs.readFileSync(path.join(target, 'keep'), 'utf8'), 'last working bundle');
  } finally {fs.rmSync(root, {recursive: true, force: true});}
});

test('packaging refuses any destination outside the generated extension bundle', () => {
  const root = fixture();
  try {
    assert.throws(() => buildBackend(root, path.join(root, 'private')), /belongs only/);
    assert.equal(fs.existsSync(path.join(root, 'private')), false);
  } finally {fs.rmSync(root, {recursive: true, force: true});}
});
