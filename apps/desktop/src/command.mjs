import { lstat, readFile, readlink, mkdir, symlink, unlink, writeFile, rename } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, join } from 'node:path';

export async function registerCommand({ destination, launcher, previousLauncher, remove = false }) {
  await mkdir(dirname(destination), { recursive: true });
  const existing = await lstat(destination).catch(error => { if (error.code !== 'ENOENT') throw error; return null; });
  if (existing) {
    const targets = [launcher, previousLauncher].filter(Boolean);
    const owned = existing.isSymbolicLink() ? targets.includes(await readlink(destination))
      : await readFile(destination, 'utf8').then(value => targets.some(target => value === `@"${target}" %*\r\n`));
    if (!owned) throw new Error('command_collision');
    if (remove) { await unlink(destination); return; }
  }
  if (remove) return;
  const temporary = destination + '.' + randomUUID();
  try {
    if (process.platform === 'win32') await writeFile(temporary, `@"${launcher}" %*\r\n`, { flag: 'wx' });
    else await symlink(launcher, temporary);
    await rename(temporary, destination);
  } finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}

export const commandPath = home => join(home, '.local', 'bin', process.platform === 'win32' ? 'opendirector.cmd' : 'opendirector');
