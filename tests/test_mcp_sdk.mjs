import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [sdkRoot, python, serverScript, backend] = process.argv.slice(2);
assert.ok(sdkRoot && python && serverScript && backend, 'SDK test requires its runner arguments');

const packageJson = JSON.parse(await readFile(join(sdkRoot, 'package.json'), 'utf8'));
assert.equal(packageJson.version, '1.30.1', 'SDK compatibility test is pinned to 1.30.1');
const { Client } = await import(pathToFileURL(join(sdkRoot, 'dist', 'esm', 'client', 'index.js')));
const { StdioClientTransport } = await import(pathToFileURL(join(sdkRoot, 'dist', 'esm', 'client', 'stdio.js')));

const client = new Client({ name: 'agent-telemetry-sdk-test', version: '1.0.0' }, { capabilities: {} });
const transport = new StdioClientTransport({
  command: python,
  args: [serverScript, '--backend', backend],
  env: process.env,
  stderr: 'pipe',
});
let stderr = '';
transport.stderr?.on('data', (chunk) => { stderr += chunk.toString('utf8'); });

try {
  await client.connect(transport);
  const { tools } = await client.listTools();
  assert.equal(tools.length, 8);
  assert.deepEqual(tools.map((tool) => tool.name), [
    'get_usage', 'compare_usage', 'get_insights', 'list_sessions',
    'get_session_timeline', 'get_data_health', 'get_catalog', 'get_subscription_limits',
  ]);

  const resources = await client.listResources();
  assert.deepEqual(resources.resources.map((resource) => resource.uri), [
    'telemetry://guide', 'telemetry://schema',
  ]);
  const guide = await client.readResource({ uri: 'telemetry://guide' });
  assert.match(guide.contents[0].text, /cloud-backed agent/i);
  assert.match(guide.contents[0].text, /untrusted data/i);

  const prompts = await client.listPrompts();
  assert.deepEqual(prompts.prompts.map((prompt) => prompt.name), ['analyze_usage', 'optimize_project']);
  const prompt = await client.getPrompt({ name: 'analyze_usage', arguments: { period: 'today' } });
  assert.match(prompt.messages[0].content.text, /read-only tools/i);

  const usage = await client.callTool({
    name: 'get_usage', arguments: { period: 'today', group_by: ['tool'] },
  });
  assert.equal(usage.isError, false);
  assert.deepEqual(usage.structuredContent.rows, [{ tool: 'Codex', tokens: 42 }]);
  assert.equal(usage.structuredContent.totals.tokens, 42);
  assert.equal(typeof usage.structuredContent.privacy_warning, 'string');
  assert.doesNotMatch(JSON.stringify(usage), /NATIVE_THREAD_SENTINEL|PROMPT_SENTINEL|Bearer\s/i);

  if (!stderr.toLowerCase().includes('cloud-backed agent')) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.match(stderr.toLowerCase(), /cloud-backed agent/);
} finally {
  await client.close();
}
