'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('launcher preserves arguments, cwd, environment, and failure status without a shell', async () => {
  const source = readFileSync(path.join(__dirname, '../bin/qagent.cjs'), 'utf8');
  for (const result of [{ status: 7 }, { status: null, error: { code: 'ENOENT' } }, { status: null, signal: 'SIGINT' }]) {
    const messages = [];
    const process = { argv: ['node', 'launcher', 'ask', '中文 ; $(echo bad)', '--file', 'a b.pdf'], env: { LLM_MODEL: 'test' } };
    await vm.runInNewContext(source, {
      __dirname: path.join(__dirname, '../bin'), process,
      console: { error: message => messages.push(message) },
      require: name => ['node:path', 'node:os', 'node:crypto'].includes(name) ? require(name) : name === './bootstrap.cjs' ? { ensureUv: async () => 'cached-uv' } : { spawnSync: (command, args, options) => {
        assert.equal(command, 'cached-uv');
        assert.deepEqual(Array.from(args.slice(-4)), process.argv.slice(2));
        assert.equal(options.shell, false);
        assert.equal(options.cwd, undefined);
        assert.equal(options.env.LLM_MODEL, 'test');
        assert.equal(options.env.PYTHONIOENCODING, 'utf-8');
        assert.ok(options.env.UV_PROJECT_ENVIRONMENT.includes('env-'));
        assert.ok(args.includes('--locked'));
        return result;
      } },
    });
    assert.equal(process.exitCode, result.status ?? (result.signal === 'SIGINT' ? 130 : 1));
    assert.equal(messages.length, result.error ? 1 : 0);
  }
});
