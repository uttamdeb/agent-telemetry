const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'website/index.html'), 'utf8');
const analytics = fs.readFileSync(path.join(root, 'website/analytics.js'), 'utf8');
const site = fs.readFileSync(path.join(root, 'website/site.js'), 'utf8');
for (const [filename, source] of [['analytics.js', analytics], ['site.js', site]]) {
  const expected = require('crypto').createHash('sha256').update(source).digest('hex').slice(0, 12);
  assert.match(html, new RegExp(`${filename}\\?v=${expected}`), `${filename} must use a content-derived cache key`);
}
assert.match(html, /data-analytics-event="at_install_option_click"/);
assert.match(html, /data-analytics-event="at_install_cta_click"/);
assert.doesNotMatch(html, /data-analytics-event="(?!at_)/, 'all landing-page events use the AT namespace');
assert.match(html, /data-analytics-option="macos_arm64"/);
assert.match(html, /data-analytics-option="macos_intel"/);
assert.match(html, /data-analytics-option="windows_x64"/);
assert.match(html, /data-analytics-option="vscode_marketplace"/);
assert.match(html, /data-analytics-option="open_vsx"/);
assert.match(html, /data-analytics-option="vsix"/);
assert.match(html, /data-analytics-option="python_package"/);
assert.match(html, /data-release-asset="AgentTelemetry-2\.1\.0-macos-arm64\.dmg"/);
assert.match(html, /data-release-asset="AgentTelemetry-2\.1\.0-windows-x64-setup\.exe"/);
assert.match(html, /install interactions, including opening the install chooser, which install option and page placement/);
assert.ok(html.indexOf("gtag('config'") < html.indexOf('analytics.js?'), 'the analytics helper loads after the GA tag setup');
assert.ok(html.indexOf('analytics.js?') < html.indexOf('site.js?'), 'the analytics helper loads before interaction code');
assert.match(site, /at_install_command_copy/);
assert.doesNotMatch(analytics, /link_url|command_text|textContent/);

function makeContext({ hasGtag = true } = {}) {
  const listeners = {};
  const calls = [];
  const navigations = [];
  const timers = [];
  const window = {
    location: { href: 'https://agenttelemetry.dev/', assign: (url) => navigations.push(url) },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    gtag: hasGtag ? (...args) => calls.push(args) : undefined,
  };
  const document = { addEventListener: (name, callback) => { listeners[name] = callback; } };
  vm.runInNewContext(analytics, { window, document, URL });
  return { window, listeners, calls, navigations, timers };
}

function clickEvent(link, extra = {}) {
  return {
    target: { closest: (selector) => selector === 'a[data-analytics-event]' ? link : null },
    button: 0,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    ...extra,
  };
}

{
  const ctx = makeContext();
  const eventNames = [
    'at_install_cta_click', 'at_install_option_click',
    'at_install_menu_open', 'at_install_command_copy',
  ];
  for (const eventName of eventNames) {
    ctx.window.AgentTelemetryAnalytics.track(eventName);
    ctx.window.AgentTelemetryAnalytics.track(eventName.slice(3));
  }
  assert.deepEqual(ctx.calls.map((call) => call[1]), eventNames,
    'emit each prefixed event once and reject unprefixed event names');
}

{
  const ctx = makeContext();
  const link = {
    href: 'https://github.com/uttamdeb/agent-telemetry/releases/download/v2.1.0/AgentTelemetry-2.1.0-macos-arm64.dmg',
    target: '',
    dataset: {
      analyticsEvent: 'at_install_option_click',
      analyticsOption: 'macos_arm64',
      analyticsPlacement: 'hero_menu',
      analyticsDestination: 'github_release',
      releaseAsset: 'AgentTelemetry-2.1.0-macos-arm64.dmg',
      installVersion: '2.1.0',
    },
  };
  const event = clickEvent(link);
  ctx.listeners.click(event);
  assert.equal(event.defaultPrevented, true, 'same-tab downloads wait briefly for GA delivery');
  assert.equal(ctx.calls[0][0], 'event');
  assert.equal(ctx.calls[0][1], 'at_install_option_click');
  assert.deepEqual({ ...ctx.calls[0][2], event_callback: undefined, event_timeout: undefined }, {
    install_option: 'macos_arm64',
    placement: 'hero_menu',
    destination: 'github_release',
    release_asset: 'AgentTelemetry-2.1.0-macos-arm64.dmg',
    install_version: '2.1.0',
    event_callback: undefined,
    event_timeout: undefined,
  });
  assert.equal(ctx.navigations.length, 0);
  ctx.calls[0][2].event_callback();
  ctx.calls[0][2].event_callback();
  assert.deepEqual(ctx.navigations, [link.href], 'the download continues once after analytics callback');
  ctx.timers[0]();
  assert.equal(ctx.navigations.length, 1, 'the timeout fallback cannot navigate twice');
}

{
  const ctx = makeContext();
  const link = {
    href: 'https://open-vsx.org/extension/agenttelemetry-team/agenttelemetry',
    target: '_blank',
    dataset: { analyticsEvent: 'at_install_option_click', analyticsOption: 'open_vsx', analyticsPlacement: 'download_section' },
  };
  const event = clickEvent(link);
  ctx.listeners.click(event);
  assert.equal(event.defaultPrevented, false, 'new-tab marketplace links keep their normal behavior');
  assert.equal(ctx.calls[0][1], 'at_install_option_click');
  assert.equal(ctx.calls[0][2].install_option, 'open_vsx');
}

{
  const ctx = makeContext({ hasGtag: false });
  const link = {
    href: 'https://github.com/uttamdeb/agent-telemetry/releases/download/v2.1.0/asset.exe',
    target: '',
    dataset: { analyticsEvent: 'at_install_option_click', analyticsOption: 'windows_x64' },
  };
  const event = clickEvent(link);
  ctx.listeners.click(event);
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(ctx.navigations, [link.href], 'downloads remain usable if Google Analytics is blocked');
}

{
  const ctx = makeContext();
  ctx.window.AgentTelemetryAnalytics.track('at_install_command_copy', {
    install_option: 'python_package',
    install_method: 'pip',
    placement: 'faq',
    command_text: 'pip install private-example',
  });
  assert.equal(ctx.calls[0][1], 'at_install_command_copy');
  assert.deepEqual({ ...ctx.calls[0][2] }, {
    install_option: 'python_package', install_method: 'pip', placement: 'faq',
  }, 'copy events include only the fixed command method, option, and placement');
}

{
  const ctx = makeContext();
  const link = {
    href: 'https://example.com/', target: '',
    dataset: { analyticsEvent: 'arbitrary_event', analyticsOption: 'ignored' },
  };
  ctx.listeners.click(clickEvent(link));
  assert.equal(ctx.calls.length, 0, 'only the tracker’s named events are accepted');
}

console.log('Website analytics interaction tests passed.');
