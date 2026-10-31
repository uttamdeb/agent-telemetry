'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const cp = require('node:child_process');
const {ServiceError} = require('./client');

function supportBase(platform = process.platform, env = process.env, home = os.homedir()) {
  if (platform === 'darwin') return path.join(home, 'Library', 'Application Support');
  if (platform === 'win32') return env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  return env.XDG_STATE_HOME || path.join(home, '.local', 'state');
}
function ledgerDirectory(platform, env, home) { return path.join(supportBase(platform, env, home), 'AgentTelemetry'); }
function inside(candidate, root) { const rel = path.relative(root, candidate); return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel)); }
function canonicalRoots(roots) {
  return [...new Set(roots.flatMap(root => {
    const lexical = path.resolve(root);
    try { return [lexical, fs.realpathSync(lexical)]; } catch (_) { return [lexical]; }
  }))];
}
function externalPath(candidate, roots) {
  if (!path.isAbsolute(candidate)) return null;
  const lexical = path.resolve(candidate);
  if (roots.some(root => inside(lexical, root))) return null;
  try {
    const resolved = fs.realpathSync(lexical);
    return roots.some(root => inside(resolved, root)) ? null : resolved;
  } catch (_) { return null; }
}
function trustedPathDirectories(roots = [], {platform = process.platform, env = process.env} = {}) {
  const resolvedRoots = canonicalRoots(roots);
  const inheritedPath = Object.entries(env).find(([key]) => key.toUpperCase() === 'PATH')?.[1] || '';
  const directories = [];
  for (const entry of inheritedPath.split(platform === 'win32' ? ';' : path.delimiter)) {
    // Empty and relative entries implicitly execute from cwd; aliases into a
    // workspace are equally unsafe even when the lexical spelling is outside it.
    if (!entry) continue;
    const resolved = externalPath(entry, resolvedRoots);
    if (!resolved) continue;
    try { if (fs.statSync(resolved).isDirectory()) directories.push(resolved); } catch (_) {}
  }
  return [...new Set(directories)];
}
function candidates(configured, roots, {platform = process.platform, env = process.env, home = os.homedir()} = {}) {
  const resolvedRoots = canonicalRoots(roots);
  if (configured) {
    if (!path.isAbsolute(configured)) throw new ServiceError('Python path must be an absolute executable path.', 'PYTHON');
    if (!externalPath(configured, resolvedRoots)) throw new ServiceError('Choose an existing Python executable outside the workspace.', 'PYTHON');
    return [configured];
  }
  const result = [];
  if (platform === 'darwin') {
    for (const apps of [path.join(home, 'Applications'), '/Applications']) {
      for (const runtime of ['AgentTelemetryRuntime', 'Python']) result.push(path.join(apps, 'AgentTelemetry.app', 'Contents', 'Resources', runtime, 'bin', 'python3'));
    }
  } else if (platform === 'win32') {
    const local = env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
    result.push(path.join(local, 'Programs', 'AgentTelemetry', 'runtime', 'python.exe'));
    result.push(path.join(local, 'Programs', 'AgentTelemetry', 'runtime', 'pythonw.exe'));
  }
  for (const directory of trustedPathDirectories(roots, {platform, env})) {
    for (const name of platform === 'win32' ? ['python.exe', 'python3.exe'] : ['python3', 'python']) result.push(path.join(directory, name));
  }
  return [...new Set(result)].filter(candidate => !resolvedRoots.some(root => inside(path.resolve(candidate), root)));
}
function safeEnvironment(env = process.env, roots = [], {platform = process.platform} = {}) {
  const result = {...env};
  for (const key of Object.keys(result)) {
    if (/^PYTHON/i.test(key) || /^AGENT_TELEMETRY_/i.test(key) || /^(PATH|NODE_OPTIONS|NODE_PATH|ELECTRON_RUN_AS_NODE)$/i.test(key)) delete result[key];
  }
  // Python itself is selected by absolute executable path. Backend subprocesses
  // must never discover workspace-controlled helpers such as scutil or zstd.
  result.PATH = trustedPathDirectories(roots, {platform, env}).join(platform === 'win32' ? ';' : path.delimiter);
  result.PYTHONNOUSERSITE = '1';
  return result;
}
function probePython(executable, cwd, roots = [], options = {}) {
  return new Promise(resolve => {
    cp.execFile(executable, ['-I', '-c', 'import sys,json;print(json.dumps(list(sys.version_info[:3])))'], {cwd, env: safeEnvironment(options.env || process.env, roots, options), windowsHide: true, timeout: 5000, maxBuffer: 8192}, (error, stdout) => {
      if (error) return resolve(false);
      try { const version = JSON.parse(stdout); resolve(Array.isArray(version) && version[0] === 3 && Number.isInteger(version[1]) && version[1] >= 9); }
      catch (_) { resolve(false); }
    });
  });
}
async function findPython(configured, roots, cwd, options = {}) {
  const resolvedRoots = canonicalRoots(roots);
  for (const executable of candidates(configured, roots, options)) {
    let real;
    try { real = fs.realpathSync(executable); if (resolvedRoots.some(root => inside(real, root))) continue; fs.accessSync(real, fs.constants.X_OK); }
    catch (_) { continue; }
    if (options.stillAllowed && !options.stillAllowed()) throw new ServiceError('Starting Python requires a trusted active editor.', 'TRUST');
    const supported = await probePython(real, cwd, roots, options);
    if (options.stillAllowed && !options.stillAllowed()) throw new ServiceError('Starting Python requires a trusted active editor.', 'TRUST');
    if (supported) return real;
  }
  throw new ServiceError('Python 3.9 or newer was not found. Install Python locally, choose an existing trusted executable in AgentTelemetry settings, or install the AgentTelemetry desktop app, which includes its own Python.', 'NO_PYTHON');
}
async function acquireStartLock(base) {
  await fs.promises.mkdir(base, {recursive: true, mode: 0o700});
  const lock = path.join(base, 'editor-start.lock');
  // An editor crash can leave this coordination marker; no service is killed.
  try { const stat = await fs.promises.stat(lock); if (Date.now() - stat.mtimeMs > 180000) await fs.promises.unlink(lock); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  let file;
  try { file = await fs.promises.open(lock, 'wx', 0o600); }
  catch (error) { if (error.code === 'EEXIST') throw new ServiceError('Another editor is starting AgentTelemetry. Wait a moment and refresh.', 'STARTING'); throw error; }
  const identity = await file.stat();
  await file.writeFile(String(Date.now()));
  return async () => {
    await file.close();
    try { const current = await fs.promises.stat(lock); if (current.ino === identity.ino && current.birthtimeMs === identity.birthtimeMs) await fs.promises.unlink(lock); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  };
}
function launchArguments(backend) {
  // -I also works with native Windows embeddable Python's isolated ._pth.
  // Only this extension's allowlisted backend is inserted, never a workspace.
  const bootstrap = 'import sys,runpy; root=sys.argv.pop(1); sys.path.insert(0,root); sys.argv[0]=root+"/dashboard.py"; runpy.run_path(sys.argv[0],run_name="__main__")';
  return ['-I', '-c', bootstrap, backend, '--host', '127.0.0.1', '--port', '7878', '--data-dir', ledgerDirectory()];
}
function bundledVersion(backend) {
  try { const info = JSON.parse(fs.readFileSync(path.join(backend, 'BUILD-INFO.json'), 'utf8')); if (typeof info.version === 'string' && /^[0-9A-Za-z.+_-]{1,64}$/.test(info.version)) return info.version; } catch (_) {}
  return '2.2.0';
}
function startupFailure(logFile) {
  return new ServiceError('AgentTelemetry exited before it could start. Check the private startup log at ' + logFile + ' and retry after fixing the Python setup.', 'BACKEND_START');
}
async function startBackend({client, extensionPath, configuredPython = '', roots = [], stillAllowed = () => true}) {
  if (client.port !== 7878) throw new ServiceError('Start monitoring uses shared port 7878. Restore the default port, or attach to your existing service.', 'CONFIG');
  const backend = path.join(extensionPath, 'backend');
  const script = path.join(backend, 'dashboard.py');
  if (!fs.existsSync(script)) throw new ServiceError('The bundled backend is missing. Reinstall the AgentTelemetry extension.', 'BACKEND');
  const release = await acquireStartLock(path.join(supportBase(), 'AgentTelemetryNative'));
  try {
    if (!stillAllowed()) throw new ServiceError('Starting Python requires a trusted local extension host.', 'TRUST');
    try { await client.health(); if (!stillAllowed()) throw new ServiceError('The editor closed or workspace trust changed during startup.', 'TRUST'); return {attached: true}; }
    catch (error) { if (error.code !== 'ECONNREFUSED') throw error; }
    if (!stillAllowed()) throw new ServiceError('Starting Python requires a trusted active editor.', 'TRUST');
    const python = await findPython(configuredPython, roots, backend, {stillAllowed});
    if (!stillAllowed()) throw new ServiceError('Starting Python requires a trusted workspace.', 'TRUST');
    // Re-check after discovery; never claim a foreign or concurrently started port.
    try { await client.health(); if (!stillAllowed()) throw new ServiceError('The editor closed or workspace trust changed during startup.', 'TRUST'); return {attached: true}; }
    catch (error) { if (error.code !== 'ECONNREFUSED') throw error; }
    if (!stillAllowed()) throw new ServiceError('The editor closed or workspace trust changed during startup.', 'TRUST');
    const logFile = path.join(supportBase(), 'AgentTelemetryNative', 'editor-backend.log');
    const flags = fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_APPEND | (fs.constants.O_NOFOLLOW || 0);
    const log = fs.openSync(logFile, flags, 0o600);
    if (process.platform !== 'win32') fs.fchmodSync(log, 0o600);
    if (fs.fstatSync(log).size > 1024 * 1024) fs.ftruncateSync(log, 0);
    let child;
    try {
      child = cp.spawn(python, launchArguments(backend), {
        cwd: backend, env: {...safeEnvironment(process.env, roots), AGENT_TELEMETRY_VERSION: bundledVersion(backend)}, detached: true,
        stdio: ['ignore', 'ignore', log], windowsHide: true, shell: false
      });
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
    } finally { fs.closeSync(log); }
    child.unref();
    // The Python server reserves the one shared port before touching the ledger.
    // Keep editor launch coordination until it responds or fails; never kill it.
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw startupFailure(logFile);
      try { await client.health(); return {attached: false}; }
      catch (error) { if (error.code !== 'ECONNREFUSED') throw error; }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new ServiceError('AgentTelemetry has not answered yet. It may still be starting. Refresh shortly; the shared service was left running. Startup log: ' + logFile, 'STARTING');
  } finally { await release(); }
}
module.exports = {trustedPathDirectories, externalPath, launchArguments, canonicalRoots, bundledVersion, supportBase, ledgerDirectory, inside, candidates, safeEnvironment, probePython, findPython, startBackend, acquireStartLock};
