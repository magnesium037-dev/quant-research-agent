#!/usr/bin/env node
'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const { createHash } = require('node:crypto');
const { ensureUv } = require('./bootstrap.cjs');

async function main() {
  const uv = await ensureUv();
  const project = path.resolve(__dirname, '..');
  const environment = path.join(os.homedir(), '.cache', 'quant-research-agent',
    'env-' + createHash('sha256').update(project).digest('hex').slice(0, 12));
  // Keep the caller's cwd: --file and QAGENT_HOME may be relative paths.
  const result = spawnSync(uv, [
    'run', '--project', project,
    '--locked', '--no-dev', '--no-editable', '--python', '3.12',
    '--', 'qagent', ...process.argv.slice(2),
  ], { stdio: 'inherit', shell: false, env: {
    PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', ...process.env,
    UV_PROJECT_ENVIRONMENT: environment,
  } });
  if (result.error) console.error(`Unable to start uv (${result.error.code || 'unknown error'}).`);
  process.exitCode = result.status ?? (result.signal === 'SIGINT' ? 130 : 1);
}

main().catch(error => {
  // Network exceptions may include URLs with credentials; do not echo their details.
  console.error(error.message?.startsWith('uv ') || error.message?.startsWith('Automatic setup') || error.message?.startsWith('Unable to extract')
    ? error.message : 'qagent setup failed. Check access to GitHub Releases, then retry.');
  process.exitCode = 1;
});
