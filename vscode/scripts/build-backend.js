'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const files = require('./backend-files');

function buildBackend(repoRoot, destination) {
  const root = fs.realpathSync(repoRoot);
  // Validate the full allowlist before replacing an existing generated bundle.
  const contents = files.map(relative => {
    if (path.isAbsolute(relative) || relative.split('/').some(part => part === '..' || !part)) {
      throw new Error(`Invalid backend allowlist entry: ${relative}`);
    }
    const source = path.join(root, relative);
    if (!fs.lstatSync(source).isFile() || fs.realpathSync(source) !== source) {
      throw new Error(`Backend sources must be regular files: ${relative}`);
    }
    return [relative, fs.readFileSync(source)];
  });
  // The backend reports the app's version (pyproject.toml), not the extension's: a pre-release extension (2.1.x)
  // bundles the backend of the app version it previews. The extension's own version is recorded beside it.
  const extensionVersion = JSON.parse(fs.readFileSync(path.join(root, 'vscode', 'package.json'), 'utf8')).version;
  const pyproject = path.join(root, 'pyproject.toml');
  const appVersion = fs.existsSync(pyproject) ? /^version\s*=\s*"([^"]+)"$/m.exec(fs.readFileSync(pyproject, 'utf8'))?.[1] : null;
  const version = appVersion || extensionVersion;
  const cache = /^CACHE_VERSION\s*=\s*(\d+)$/m.exec(contents.find(([file]) => file === 'dashboard.py')[1].toString());
  if (!cache) throw new Error('Backend cache version is unavailable.');
  const commit = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], {encoding: 'utf8'}).trim();
  const target = path.join(fs.realpathSync(path.dirname(path.resolve(destination))), path.basename(destination));
  if (target !== path.join(root, 'vscode', 'backend')) {
    throw new Error('The generated backend belongs only in vscode/backend.');
  }
  if (fs.existsSync(target) && fs.lstatSync(target).isSymbolicLink()) {
    throw new Error('Refusing to replace a linked backend directory.');
  }
  fs.rmSync(target, {recursive: true, force: true});
  fs.mkdirSync(target, {recursive: true});
  const digests = {};
  for (const [relative, content] of contents) {
    const output = path.join(target, relative);
    fs.mkdirSync(path.dirname(output), {recursive: true});
    fs.writeFileSync(output, content);
    digests[relative] = crypto.createHash('sha256').update(content).digest('hex');
  }
  const info = {version, extension_version: extensionVersion, source_commit: commit, cache_version: Number(cache[1]), files: digests};
  fs.writeFileSync(path.join(target, 'BUILD-INFO.json'), JSON.stringify(info, null, 2) + '\n');
  return info;
}

if (require.main === module) {
  const root = path.resolve(__dirname, '..', '..');
  const info = buildBackend(root, path.join(root, 'vscode', 'backend'));
  process.stdout.write(`Bundled ${Object.keys(info.files).length} backend files; ledger schema ${info.cache_version}.\n`);
}

module.exports = {buildBackend};
