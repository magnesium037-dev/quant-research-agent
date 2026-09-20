'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { ensureUv, download, ASSETS } = require('../bin/bootstrap.cjs');

test('existing uv reused; unsupported setup fails clearly', async () => {
  assert.equal(await ensureUv({ run: () => ({ status: 0 }) }), 'uv');
  await assert.rejects(ensureUv({ platform: 'other', run: () => ({ status: 1 }) }), /does not support/);
});

test('bootstrap verifies before extraction, cleans failures, caches successful binary', async () => {
  const cache = fs.mkdtempSync(path.join(os.tmpdir(), 'qagent bootstrap '));
  const bytes = Buffer.from('synthetic verified archive');
  const original = ASSETS['win32-x64'][1];
  let extractions = 0;
  const run = (command, args, options) => {
    assert.equal(options.shell, false);
    if (command === 'uv') return { status: null, error: { code: 'ENOENT' } };
    extractions++;
    assert.equal(command, 'powershell.exe');
    assert.ok(args.at(-1).includes('$env:QAGENT_UV_ARCHIVE'));
    fs.writeFileSync(path.join(options.env.QAGENT_UV_EXTRACT, 'uv.exe'), 'fake exe');
    return { status: 0 };
  };
  const options = { platform: 'win32', arch: 'x64', cache, run, fetchArchive: async () => bytes };
  try {
    await assert.rejects(ensureUv(options), /SHA256/);
    assert.equal(extractions, 0);
    assert.equal(fs.readdirSync(path.join(cache, fs.readdirSync(cache)[0])).length, 0);
    ASSETS['win32-x64'][1] = createHash('sha256').update(bytes).digest('hex');
    const executable = await ensureUv(options);
    assert.equal(fs.readFileSync(executable, 'utf8'), 'fake exe');
    assert.equal(await ensureUv({ ...options, fetchArchive: () => { throw new Error('unexpected network'); } }), executable);
    assert.equal(extractions, 1);
  } finally {
    ASSETS['win32-x64'][1] = original;
    fs.rmSync(cache, { recursive: true, force: true });
  }
});

test('download limits destinations, redirects, status and body size', async () => {
  const url = 'https://github.com/astral-sh/uv/releases/download/test.zip';
  await assert.rejects(download('http://github.com/a'), /destination/);
  await assert.rejects(download(url, async () => new Response(null, { status: 302, headers: { location: 'https://evil.example/a' } })), /destination/);
  await assert.rejects(download(url, async () => new Response(null, { status: 302, headers: { location: url } })), /redirects/);
  await assert.rejects(download(url, async () => new Response(null, { status: 500 })), /HTTP 500/);
  await assert.rejects(download(url, async () => ({ ok: true, body: (async function* () { yield Buffer.alloc(64 * 1024 * 1024 + 1); })() })), /64 MiB/);
  assert.equal((await download(url, async () => new Response('archive'))).toString(), 'archive');
});
