import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';

const binary = process.argv[2];
if (!binary) throw new Error('Usage: node scripts/smoke.mjs /path/to/packaged/OpenDirector');
const directory = await mkdtemp(join(tmpdir(), 'opendirector-desktop-'));
let previous;
try {
 for (let attempt = 0; attempt < 2; attempt++) {
  const child = spawn(binary, ['--smoke'], { env: { ...process.env, OPENDIRECTOR_DESKTOP_DATA: directory }, stdio: 'inherit' });
  const timeout = setTimeout(() => child.kill('SIGKILL'), 90_000);
  const result = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', resolve);
  }).finally(() => clearTimeout(timeout));
  const log = await readFile(join(directory, 'logs/desktop.log'), 'utf8');
  if (result !== 0 || !log.includes('"health":{"status":"ok"}')) throw new Error(`Packaged desktop smoke failed (${result}):\n${log}`);
  const latest = log.split('\n').filter(line => line.startsWith('smoke ')).at(-1);
  const current = JSON.parse(latest.slice(6)).projectIds;
  if (previous && JSON.stringify(previous) !== JSON.stringify(current)) throw new Error('Reopening changed the project identities');
  previous = current;
 }
  process.stdout.write('Packaged canvas, authenticated API, isolation, both locales, hidden-window Host and reopen: passed\n');
} finally { await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }); }
