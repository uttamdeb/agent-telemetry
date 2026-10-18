#!/usr/bin/env node
'use strict';

// Fixture-only browser preview. Nothing in this server imports the backend or ledger.
// node vscode/test/preview-webview.js [port]
// ?theme=light|dark|contrast&fixture=stale|offline|restricted|remote|empty|history|long|truncated
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {getHtml} = require('../src/webview');
const {scenario} = require('./fixtures/desk-state');
const root = path.resolve(__dirname, '..');
const nonce = 'fixture-preview-nonce-2026';
function page(theme, fixture) {
  const webview = {cspSource: "'self'", asWebviewUri: uri => ({toString: () => uri.path})};
  const extension = {path: '', with: value => value};
  const html = getHtml(webview, extension, nonce);
  const className = theme === 'light' ? 'vscode-light' : theme === 'contrast' ? 'vscode-high-contrast' : 'vscode-dark';
  return html.replace('</head>', '<link rel="stylesheet" href="/preview.css"></head>')
    .replace('<body>', '<body class="' + className + '">')
    .replace('<script nonce=', '<script id="fixture-state" type="application/json" nonce="' + nonce + '">' + JSON.stringify(scenario(fixture)).replace(/</g, '\\u003c') + '</script><script nonce="' + nonce + '" src="/preview-bridge.js"></script><script nonce=');
}
function createServer() {
  return http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const allowed = {'/media/app-icon.png': ['media/app-icon.png', 'image/png'], '/media/desk.css': ['media/desk.css', 'text/css'], '/media/desk.js': ['media/desk.js', 'text/javascript'], '/preview.css': ['test/fixtures/preview.css', 'text/css'], '/preview-bridge.js': ['test/fixtures/preview-bridge.js', 'text/javascript']};
    response.setHeader('Cache-Control', 'no-store');
    if (url.pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
    if (url.pathname === '/') { response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(page(url.searchParams.get('theme'), url.searchParams.get('fixture'))); return; }
    const asset = allowed[url.pathname];
    if (!asset) { response.writeHead(404); response.end('Fixture resource unavailable'); return; }
    response.setHeader('Content-Type', asset[1] + '; charset=utf-8'); response.end(fs.readFileSync(path.join(root, asset[0])));
  });
}
if (require.main === module) {
  const port = Number(process.argv[2] || 7826);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Supply a valid port');
  createServer().listen(port, '127.0.0.1', () => process.stdout.write(`Fixture-only Agent desk: http://127.0.0.1:${port}\n`));
}
module.exports = {createServer, page};
