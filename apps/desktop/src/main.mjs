import { z } from 'zod';
import { app, BrowserWindow, dialog, ipcMain, Menu, net, protocol, session, shell, nativeTheme } from 'electron';
import { createWriteStream, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { Host, ownedURL, proxyRequest } from './host.mjs';
import { messages, localeFor } from './locale.mjs';
import { registerCommand, commandPath } from './command.mjs';

app.setName('OpenDirector');
const here = dirname(fileURLToPath(import.meta.url));
const runtime = app.isPackaged ? join(process.resourcesPath, 'runtime') : join(here, '..', 'runtime');
const isolated = process.env.OPENDIRECTOR_DESKTOP_DATA;
if (isolated) app.setPath('userData', isolated);
protocol.registerSchemesAsPrivileged([{ scheme: 'opendirector', privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true,
} }]);
let host;
let window;
let exporting = false;
let quitting = false;
let quitPending = false;
let locale = localeFor(app.getLocale());
const text = () => messages[locale];
const userData = app.getPath('userData');
const logs = join(userData, 'logs');
const workspace = join(userData, 'workspace');
mkdirSync(logs, { recursive: true });
mkdirSync(workspace, { recursive: true });
const logFile = join(logs, 'desktop.log');
const log = createWriteStream(logFile, { flags: 'a', mode: 0o600 });
try { locale = localeFor(readFileSync(join(userData, 'locale'), 'utf8')); }
catch (error) { if (error.code !== 'ENOENT') log.write(`Locale read failed: ${error.message}\n`); }

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { window?.show(); window?.focus(); });
  app.whenReady().then(start).catch(fatal);
}

function trusted(event) {
  return event.sender === window?.webContents && event.senderFrame === window.webContents.mainFrame && ownedURL(event.senderFrame.url);
}
ipcMain.on('desktop:locale', (event, value) => {
  if (!trusted(event) || !['en-US', 'zh-CN'].includes(value)) return;
  locale = value;
  writeFileSync(join(userData, 'locale'), value, { mode: 0o600 });
  menu();
});
ipcMain.on('desktop:theme', (event, value) => {
  if (trusted(event) && ['light', 'dark'].includes(value)) { nativeTheme.themeSource = value; applyBackdrop(); }
});
ipcMain.on('desktop:session', (event, value) => {
  const parsed = z.uuid().safeParse(value);
  if (trusted(event) && parsed.success) host.registerSession(parsed.data);
});
ipcMain.on('desktop:exporting', (event, value) => {
  if (trusted(event) && typeof value === 'boolean') exporting = value;
});

function menu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: text().app, submenu: [
      { role: 'about', label: text().app },
      { label: text().logs, click: () => shell.openPath(logs) },
      { label: text().data, click: () => shell.openPath(workspace) },
      { label: text().command, click: manageCommand },
      { type: 'separator' }, { label: text().quit, accelerator: 'CmdOrCtrl+Q', click: () => app.quit() },
    ] },
    { label: text().edit, submenu: ['undo', 'redo', 'cut', 'copy', 'paste', 'selectAll'].map(role => ({ role, label: text()[role] })) },
    { label: text().view, submenu: [
      { role: 'togglefullscreen', label: text().fullscreen }, { role: 'toggleDevTools', label: text().devtools },
    ] },
  ]));
}

async function manageCommand() {
  const destination = commandPath(homedir());
  const { response } = await dialog.showMessageBox(window, { message: text().command, detail: destination,
    buttons: [text().install, text().remove, text().cancel], cancelId: 2 });
  if (response === 2) return;
  try {
    const receipt = join(userData, 'command-registration.json');
    let previousLauncher;
    try {
      const previous = JSON.parse(readFileSync(receipt, 'utf8'));
      if (previous.destination === destination && typeof previous.launcher === 'string') previousLauncher = previous.launcher;
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const launcher = join(runtime, 'bin', process.platform === 'win32' ? 'opendirector.cmd' : 'opendirector');
    await registerCommand({ destination, previousLauncher, launcher, remove: response === 1 });
    writeFileSync(receipt, JSON.stringify({ destination, launcher }), { mode: 0o600 });
    await dialog.showMessageBox(window, { message: text().commandDone, detail: destination });
  } catch (error) {
    await dialog.showMessageBox(window, { type: 'error', message: text().failed,
      detail: error.message === 'command_collision' ? text().collision : error.message });
  }
}

async function start() {
  menu();
  const manifest = JSON.parse(readFileSync(join(runtime, 'manifest.json'), 'utf8'));
  if (typeof manifest.python !== 'string' || manifest.python.startsWith('/') || manifest.python.split(/[\\/]/).includes('..')) throw new Error('Invalid runtime Python path');
  if (manifest.version !== 1 || manifest.platform !== process.platform || manifest.arch !== process.arch) throw new Error('Runtime platform does not match this application');
  host = new Host({ python: join(runtime, manifest.python), directory: workspace, log,
    env: { ...process.env, OPENDIRECTOR_ROOT: workspace,
      PATH: join(runtime, 'bin') + (process.platform === 'win32' ? ';' : ':') + process.env.PATH },
    onExit: () => { if (!quitting) void fatal(new Error(text().interrupted)); },
  });
  await host.ready;
  const ownedSession = session.fromPartition('persist:opendirector');
  ownedSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  ownedSession.protocol.handle('opendirector', request => proxyRequest(request, host, async (url, options) => {
    const response = await net.fetch(url, options);
    if (response.status >= 400) log.write(`Host request ${options.method} ${new URL(url).pathname}: ${response.status}\n`);
    return response;
  }));
  ownedSession.on('will-download', (_event, item) => item.setSaveDialogOptions({ title: text().save }));
  window = new BrowserWindow({ width: 1440, height: 960, minWidth: 800, minHeight: 600, show: false, title: 'OpenDirector',
    ...(process.platform === 'darwin' ? { titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 18, y: 18 },
      vibrancy: 'sidebar', visualEffectState: 'active', backgroundColor: '#00000000' } : {}),
    webPreferences: { session: ownedSession, preload: join(here, 'preload.cjs'), nodeIntegration: false,
      contextIsolation: true, sandbox: true, webSecurity: true },
  });
  if (process.platform === 'darwin') {
    const fullscreen = () => window.webContents.send('desktop:fullscreen', window.isFullScreen());
    window.on('enter-full-screen', fullscreen);
    window.on('leave-full-screen', fullscreen);
    window.webContents.on('did-finish-load', fullscreen);
    for (const event of ['minimize', 'hide', 'restore', 'show']) window.on(event, applyBackdrop);
  }
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (new URL(url).protocol === 'https:') void shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => { if (!ownedURL(url)) event.preventDefault(); });
  window.webContents.on('render-process-gone', () => { exporting = false; });
  window.on('close', event => { if (!quitting) { event.preventDefault(); window.hide(); } });
  app.on('activate', () => { window?.show(); window?.focus(); });
  await window.loadURL('opendirector://app/');
  window.show();
  if (process.argv.includes('--smoke')) {
    try {
      const result = await window.webContents.executeJavaScript(`(async () => {
        const r = await fetch('/api/health', {headers: {'X-OpenDirector-Request': '1'}});
        const health = await r.json();
        const projects = await fetch('/api/creation/projects', {headers: {'X-OpenDirector-Request': '1'}});
        return {health, projects: projects.status, isolated: typeof require === 'undefined', bridge: !!window.opendirectorDesktop};
      })()`);
      if (result.health.status !== 'ok' || result.projects !== 200 || !result.isolated || !result.bridge) throw new Error('Desktop smoke failed');
      let rendered = false;
      for (let attempt = 0; attempt < 150; attempt++) {
        if (await window.webContents.executeJavaScript('document.querySelectorAll("button").length > 2')) { rendered = true; break; }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (!rendered) throw new Error('Workbench did not become interactive');
      await window.webContents.executeJavaScript('document.querySelector(".cw-welcome-secondary")?.click()');
      let canvasReady = false;
      for (let attempt = 0; attempt < 150; attempt++) {
        if (await window.webContents.executeJavaScript('!!document.querySelector(".react-flow") && !!document.querySelector(".cw-save-status i.saved")')) { canvasReady = true; break; }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (!canvasReady) throw new Error('Canvas did not open');
      for (let attempt = 0; attempt < 150; attempt++) {
      result.projectIds = await window.webContents.executeJavaScript(`fetch('/api/creation/projects', {headers: {'X-OpenDirector-Request': '1'}}).then(r => r.json()).then(value => value.projects.map(project => project.id).sort())`);
        if (result.projectIds.length) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (!result.projectIds.length) throw new Error('Canvas project was not persisted');
      await new Promise(resolve => setTimeout(resolve, 500));
      await window.webContents.executeJavaScript('document.querySelector(".cw-welcome-secondary")?.click()');
      await new Promise(resolve => setTimeout(resolve, 300));
      if (await window.webContents.executeJavaScript('!!document.querySelector("dialog[open]")')) throw new Error('A dialog obscures the canvas');
      const evidence = process.env.OPENDIRECTOR_DESKTOP_EVIDENCE;
      for (const language of ['en-US', 'zh-CN']) {
        await window.webContents.executeJavaScript(`window.dispatchEvent(new StorageEvent('storage', { key: 'opendirector.locale.v1', newValue: '${language}' }))`);
        await new Promise(resolve => setTimeout(resolve, 500));
        const current = await window.webContents.executeJavaScript('document.documentElement.lang');
        if (current !== language || locale !== language) throw new Error('Desktop locale synchronization failed');
        if (!(await window.webContents.executeJavaScript('!!document.querySelector(".react-flow") && !!document.querySelector(".cw-save-status i.saved")'))) throw new Error('Canvas was lost during language switching');
        if (evidence) {
          mkdirSync(evidence, { recursive: true });
          writeFileSync(join(evidence, `desktop-${language}.png`), (await window.webContents.capturePage()).toPNG());
          for (const state of ['expanded', 'collapsed']) {
            if (state === 'collapsed') {
              await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
              await new Promise(resolve => setTimeout(resolve, 500));
            }
            const placements = await window.webContents.executeJavaScript(`(() => {
              const open = document.querySelector('.cw-workspace').dataset.desktopSidebar === 'open';
              return { open, create: document.querySelectorAll(open ? '.od-sidebar-create' : '.od-caption-actions button').length };
            })()`);
            if (placements.open !== (state === 'expanded') || placements.create !== (state === 'expanded' ? 1 : 2)) throw new Error('New project button placement failed');
            const layout = await window.webContents.executeJavaScript(`(() => {
              const elements = ['.cw-topbar', '.cw-top-actions', '.cw-language-toggle'];
              return { width: innerWidth, elements: elements.map(selector => { const e = document.querySelector(selector); const r = e.getBoundingClientRect(); return {selector, left:r.left, right:r.right, width:r.width, transform:getComputedStyle(e).transform}; }) };
            })()`);
            writeFileSync(join(evidence, `layout-${state}-${language}.json`), JSON.stringify(layout));
            window.webContents.invalidate();
            await new Promise(resolve => setTimeout(resolve, 200));
            writeFileSync(join(evidence, `sidebar-${state}-${language}.png`), (await window.webContents.capturePage()).toPNG());
          }
          await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
          await new Promise(resolve => setTimeout(resolve, 500));
        }
      }
      if (process.platform === 'darwin') {
        const chrome = await window.webContents.executeJavaScript(`(() => {
          const header = document.querySelector('.cw-topbar');
          return { top: header.getBoundingClientRect().top,
            canvasTop: document.querySelector('.react-flow').getBoundingClientRect().top,
            padding: getComputedStyle(header).paddingLeft,
            drag: getComputedStyle(header).getPropertyValue('-webkit-app-region') };
        })()`);
        if (chrome.top !== 0 || chrome.canvasTop !== 48 || chrome.padding !== '20px' || chrome.drag !== 'drag') throw new Error('Desktop chrome layout failed: ' + JSON.stringify(chrome));
        app.focus({ steal: true });
        window.show();
        window.focus();
        await new Promise(resolve => setTimeout(resolve, 500));
        for (const fullscreen of [true, false]) {
          window.setFullScreen(fullscreen);
          let synchronized = false;
          for (let attempt = 0; attempt < 100; attempt++) {
            synchronized = await window.webContents.executeJavaScript(`document.documentElement.hasAttribute('data-desktop-fullscreen') === ${fullscreen}`);
            if (synchronized) break;
            await new Promise(resolve => setTimeout(resolve, 100));
          }
          if (!synchronized) throw new Error('Fullscreen chrome synchronization failed: target=' + fullscreen + ', native=' + window.isFullScreen());
        }
        await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
        await new Promise(resolve => setTimeout(resolve, 500));
        const bounds = window.getBounds();
        window.setSize(800, 600);
        await new Promise(resolve => setTimeout(resolve, 200));
        const fits = await window.webContents.executeJavaScript(`(() => {
          const language = document.querySelector('.cw-topbar .cw-language-toggle').getBoundingClientRect();
          const title = document.querySelector('.cw-topbar .cw-project-selector').getBoundingClientRect();
          const actions = document.querySelector('.cw-top-actions').getBoundingClientRect();
          return title.right <= language.left && language.right <= actions.left && actions.right <= innerWidth;
        })()`);
        if (!fits) throw new Error('Desktop controls overlap in a narrow window');
        window.setBounds(bounds);
        await new Promise(resolve => setTimeout(resolve, 200));
        await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
        await new Promise(resolve => setTimeout(resolve, 500));
        result.chrome = chrome;

      }
      await window.webContents.executeJavaScript('window.opendirectorDesktop.setExporting(true)');
      await new Promise(resolve => setTimeout(resolve, 50));
      window.close();
      if (window.isVisible() || !(await host.inspect())) throw new Error('Closing a window stopped the Host');
      log.write(`smoke ${JSON.stringify(result)}\n`);
      quitting = true;
      window.destroy();
      await host.stop();
      app.exit(0);
    } catch (error) { log.write(`smoke failed ${error.message}\n`); quitting = true; window?.destroy(); await host.stop(); app.exit(1); }
  }
}

function applyBackdrop() {
  if (process.platform !== 'darwin' || !window || window.isDestroyed()) return;
  if (window.isMinimized() || !window.isVisible()) {
    window.setVibrancy(null);
    window.setBackgroundColor(nativeTheme.shouldUseDarkColors ? '#202022' : '#eeeef1');
  } else {
    window.setVibrancy('sidebar');
    window.setBackgroundColor('#00000000');
  }
}

async function fatal(error) {
  log.write(`Host failure: ${error.message}\n`);
  await host?.stop();
  if (process.argv.includes('--smoke')) { app.exit(1); return; }
  await dialog.showMessageBox({ type: 'error', message: text().startup, detail: `${error.message}\n${logFile}`, buttons: [text().close] });
  quitting = true;
  app.quit();
}

app.on('before-quit', event => {
  if (quitting) return;
  event.preventDefault();
  if (quitPending) return;
  quitPending = true;
  void (async () => {
    try {
      const state = await host?.inspect();
      if (exporting || state === null || state && (state.agents || state.generations || state.tools)) {
        const { response } = await dialog.showMessageBox({ type: 'warning', message: text().running,
          buttons: [text().cancel, text().quit], defaultId: 0, cancelId: 0 });
        if (response !== 1) return;
      }
      quitting = true;
      window?.destroy();
      await host?.stop();
      app.quit();
    } finally { quitPending = false; }
  })();
});
