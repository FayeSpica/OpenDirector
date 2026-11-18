import { test } from 'node:test';
import assert from 'node:assert/strict';
import { eventSchema, proxyRequest } from '../src/host.mjs';
import { messages } from '../src/locale.mjs';
import { registerCommand } from '../src/command.mjs';
import { mkdtemp, readlink, rm, writeFile, lstat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('Host readiness rejects foreign origins and malformed control messages', () => {
  const ready = { type: 'ready', version: 1, runtime: {} };
  for (const url of ['https://evil.test', 'http://localhost:80', 'http://127.0.0.1:42/?token=x', 'http://u:p@127.0.0.1:42']) {
    assert.equal(eventSchema.safeParse({ ...ready, url }).success, false);
  }
  assert.equal(eventSchema.safeParse({ ...ready, url: 'http://127.0.0.1:23456' }).success, true);
});

test('Proxy authenticates only the owned Host and streams media range responses', async () => {
  const host = { url: 'http://127.0.0.1:23456', token: 'private-token' };
  let calls = 0;
  const fetcher = async (url, options) => {
    calls += 1;
    assert.equal(url, host.url + '/api/media');
    assert.equal(options.headers.get('x-opendirector-desktop'), host.token);
    assert.equal(options.headers.get('range'), 'bytes=2-4');
    assert.equal(options.headers.has('cookie'), false);
    assert.equal(options.redirect, 'manual');
    return new Response('abc', { status: 206, headers: { 'content-range': 'bytes 2-4/10', 'set-cookie': 'secret=x', 'connection': 'close' } });
  };
  const response = await proxyRequest(new Request('opendirector://app/api/media', { headers: { range: 'bytes=2-4', cookie: 'x=y' } }), host, fetcher);
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('content-range'), 'bytes 2-4/10');
  assert.equal(response.headers.has('set-cookie'), false);
  assert.equal(await response.text(), 'abc');
  assert.equal((await proxyRequest(new Request('opendirector://other/api/media'), host, fetcher)).status, 403);
  assert.equal((await proxyRequest(new Request('opendirector://app//other.test/media'), host, fetcher)).status, 403);
  assert.equal(calls, 1);
});

test('Native dialogs have matching bilingual keys', () => {
  assert.deepEqual(Object.keys(messages['en-US']).sort(), Object.keys(messages['zh-CN']).sort());
});

test('Command registration refuses collisions and only removes its own launcher', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'od-command-'));
  const destination = join(directory, 'opendirector');
  const launcher = join(directory, 'launcher');
  try {
    await writeFile(destination, 'another installation');
    await assert.rejects(registerCommand({ destination, launcher }), /command_collision/);
    await assert.rejects(registerCommand({ destination, launcher, remove: true }), /command_collision/);
    await rm(destination);
    await registerCommand({ destination, launcher });
    if (process.platform !== 'win32') assert.equal(await readlink(destination), launcher);
    await registerCommand({ destination, launcher });
    await registerCommand({ destination, launcher, remove: true });
    await assert.rejects(lstat(destination), { code: 'ENOENT' });
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test('Proxy never follows redirects carrying the desktop credential', async () => {
  const response = await proxyRequest(new Request('opendirector://app/api/test'),
    { url: 'http://127.0.0.1:23456', token: 'private' },
    async () => new Response(null, { status: 302, headers: { location: 'https://other.test' } }));
  assert.equal(response.status, 502);
});

test('Command repair after moving the app only replaces the recorded launcher', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'od-command-move-'));
  const destination = join(directory, 'opendirector');
  const previousLauncher = join(directory, 'old-app');
  const launcher = join(directory, 'new-app');
  try {
    await registerCommand({ destination, launcher: previousLauncher });
    await assert.rejects(registerCommand({ destination, launcher }), /command_collision/);
    await registerCommand({ destination, launcher, previousLauncher });
    if (process.platform !== 'win32') assert.equal(await readlink(destination), launcher);
    await registerCommand({ destination, launcher, remove: true });
  } finally { await rm(directory, { recursive: true, force: true }); }
});
