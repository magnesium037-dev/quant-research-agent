'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');

// Official astral-sh/uv release asset digests, pinned with the npm release.
const VERSION = '0.12.17';
const ASSETS = {
  'win32-x64': ['uv-x86_64-pc-windows-msvc.zip', 'a252121d5b59398fcb137c6ea448176459a44010f33f67e0072305a637119ca7'],
  'linux-x64': ['uv-x86_64-unknown-linux-gnu.tar.gz', 'fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63'],
};

async function download(url, fetchImpl = fetch) {
  const signal = AbortSignal.timeout(180000);
  for (let redirects = 0; redirects <= 5; redirects++) {
    const target = new URL(url);
    if (target.protocol !== 'https:' || !['github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'].includes(target.hostname)) {
      throw new Error('Unexpected uv download destination.');
    }
    const response = await fetchImpl(target, { redirect: 'manual', signal });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      await response.body?.cancel();
      url = new URL(response.headers.get('location'), target).href;
      continue;
    }
    if (!response.ok || !response.body) throw new Error(`uv download failed (HTTP ${response.status}).`);
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > 64 * 1024 * 1024) throw new Error('uv download exceeds 64 MiB.');
      chunks.push(chunk);
    }
    return Buffer.concat(chunks);
  }
  throw new Error('Too many uv download redirects.');
}

async function ensureUv({ platform = process.platform, arch = process.arch,
  cache = path.join(os.homedir(), '.cache', 'quant-research-agent'), run = spawnSync,
  fetchArchive = download } = {}) {
  const existing = run('uv', ['--version'], { stdio: 'ignore', shell: false, timeout: 10000 });
  if (existing.status === 0) return 'uv';
  const asset = ASSETS[`${platform}-${arch}`];
  if (!asset) throw new Error(`Automatic setup does not support ${platform}/${arch}; install uv from https://docs.astral.sh/uv/getting-started/installation/ and retry.`);
  const folder = path.join(cache, `uv-${VERSION}-${platform}-${arch}`);
  const executable = path.join(folder, platform === 'win32' ? 'uv.exe' : 'uv');
  if (fs.existsSync(executable)) return executable;
  console.error(`qagent: preparing uv ${VERSION}, then Python 3.12 and application dependencies...`);
  fs.mkdirSync(folder, { recursive: true });
  const temp = fs.mkdtempSync(path.join(folder, 'download-'));
  try {
    const bytes = await fetchArchive(`https://github.com/astral-sh/uv/releases/download/${VERSION}/${asset[0]}`);
    if (createHash('sha256').update(bytes).digest('hex') !== asset[1]) throw new Error('uv SHA256 verification failed; nothing was executed.');
    const archive = path.join(temp, asset[0]);
    const output = path.join(temp, 'extracted');
    fs.writeFileSync(archive, bytes);
    fs.mkdirSync(output);
    const extracted = platform === 'win32'
      ? run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
        'Expand-Archive -LiteralPath $env:QAGENT_UV_ARCHIVE -DestinationPath $env:QAGENT_UV_EXTRACT -Force'],
        { shell: false, stdio: 'ignore', timeout: 60000, env: { ...process.env, QAGENT_UV_ARCHIVE: archive, QAGENT_UV_EXTRACT: output } })
      : run('tar', ['-xzf', archive, '-C', output], { shell: false, stdio: 'ignore', timeout: 60000 });
    if (extracted.status !== 0) throw new Error('Unable to extract uv; Windows requires PowerShell, Linux requires tar.');
    const unpacked = platform === 'win32' ? path.join(output, 'uv.exe') : path.join(output, 'uv-x86_64-unknown-linux-gnu', 'uv');
    fs.chmodSync(unpacked, 0o755);
    try { fs.renameSync(unpacked, executable); }
    catch (error) { if (!fs.existsSync(executable)) throw error; }
    return executable;
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

module.exports = { ensureUv, download, VERSION, ASSETS };
