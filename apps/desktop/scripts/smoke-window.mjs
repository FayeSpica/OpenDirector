import { app } from 'electron';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

export async function smokeWindow({ window, host, getLocale }) {
  const result = await window.webContents.executeJavaScript(`(async () => {
    const r = await fetch('/api/health', {headers: {'X-OpenDirector-Request': '1'}});
    const health = await r.json();
    const projects = await fetch('/api/creation/projects', {headers: {'X-OpenDirector-Request': '1'}});
    return {health, projects: projects.status, isolated: typeof require === 'undefined', bridge: !!window.opendirectorDesktop};
  })()`);
  if (result.health.status !== 'ok' || result.projects !== 200 || !result.isolated || !result.bridge) throw new Error('Desktop smoke failed');
  await waitFor(() => window.webContents.executeJavaScript('document.querySelectorAll("button").length > 2'), 'Workbench did not become interactive');
  await window.webContents.executeJavaScript('document.querySelector(".cw-welcome-secondary")?.click()');
  await waitFor(() => window.webContents.executeJavaScript('!!document.querySelector(".react-flow") && !!document.querySelector(".cw-save-status i.saved")'), 'Canvas did not open');
  result.projectIds = await waitFor(() => window.webContents.executeJavaScript(`fetch('/api/creation/projects', {headers: {'X-OpenDirector-Request': '1'}})
    .then(r => r.json()).then(value => value.projects.length ? value.projects.map(project => project.id).sort() : null)`), 'Canvas project was not persisted');
  await delay(500);
  await window.webContents.executeJavaScript('document.querySelector(".cw-welcome-secondary")?.click()');
  await delay(300);
  if (await window.webContents.executeJavaScript('!!document.querySelector("dialog[open]")')) throw new Error('A dialog obscures the canvas');
  if (process.platform === 'win32') {
    if (window.isMenuBarVisible()) throw new Error('Windows application menu is visible');
    let bounds;
    try {
      await waitFor(async () => {
        bounds = await window.webContents.executeJavaScript(`(() => {
          const overlay = navigator.windowControlsOverlay;
          const actions = document.querySelector('.cw-top-actions');
          return {
            visible: overlay?.visible,
            titlebarRight: overlay?.getTitlebarAreaRect().right,
            toolbarRight: actions?.getBoundingClientRect().right,
          };
        })()`);
        return bounds.visible && bounds.toolbarRight <= bounds.titlebarRight;
      }, 'Window controls overlap the workspace toolbar');
    } catch (error) {
      throw new Error(`${error.message}: ${JSON.stringify(bounds)}`, { cause: error });
    }
  }
  result.sidebarInitiallyOpen = await window.webContents.executeJavaScript("document.querySelector('.cw-workspace').dataset.desktopSidebar === 'open'");
  if (!result.sidebarInitiallyOpen) {
    await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
    await waitFor(() => window.webContents.executeJavaScript("document.querySelector('.cw-workspace').dataset.desktopSidebar === 'open'"), 'Sidebar did not expand');
  }
  if (result.projectIds.length === 1) {
    await window.webContents.executeJavaScript("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', metaKey: navigator.platform.startsWith('Mac'), ctrlKey: !navigator.platform.startsWith('Mac'), bubbles: true }))");
    result.projectIds = await waitFor(() => window.webContents.executeJavaScript(`fetch('/api/creation/projects', {headers: {'X-OpenDirector-Request': '1'}})
      .then(r => r.json()).then(value => value.projects.length === 2 ? value.projects.map(project => project.id).sort() : null)`), 'New project shortcut did not persist a project');
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.cw-project-name-editor input')"), 'New project name did not become editable');
    await window.webContents.executeJavaScript("document.querySelector('.cw-project-name-editor input').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.cw-save-status i.saved')"), 'New project did not finish saving');
  }
  await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.od-project-sidebar nav button')"), 'Sidebar projects did not load');
  await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-search-toggle').click()");
  await waitFor(() => window.webContents.executeJavaScript("document.activeElement === document.querySelector('.od-sidebar-search')"), 'Project search did not take focus');
  const firstProject = await window.webContents.executeJavaScript("document.querySelector('.od-project-sidebar nav button span').textContent");
  for (const query of ['__no_matching_project__', `  ${firstProject.toLocaleUpperCase()}  `, '']) {
    await window.webContents.executeJavaScript(`(() => {
      const input = document.querySelector('.od-sidebar-search');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(query)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await waitFor(() => window.webContents.executeJavaScript(query === '__no_matching_project__' ? "!!document.querySelector('.od-sidebar-empty') && !document.querySelector('.od-project-sidebar nav button')" : "!!document.querySelector('.od-project-sidebar nav button') && !document.querySelector('.od-sidebar-empty')"), 'Sidebar project search failed');
  }
  await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-search-close').click()");
  await waitFor(() => window.webContents.executeJavaScript("!document.querySelector('.od-sidebar-search') && document.activeElement === document.querySelector('.od-sidebar-search-toggle')"), 'Closing search did not restore focus');
  await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
  await delay(200);
  await window.webContents.executeJavaScript("window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: navigator.platform.startsWith('Mac'), ctrlKey: !navigator.platform.startsWith('Mac'), bubbles: true }))");
  await waitFor(() => window.webContents.executeJavaScript("document.activeElement === document.querySelector('.od-sidebar-search')"), 'Project search shortcut failed');
  await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-search').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
  await waitFor(() => window.webContents.executeJavaScript("!document.querySelector('.od-sidebar-search')"), 'Escape did not close project search');
  const captionInteractive = await window.webContents.executeJavaScript("[...document.querySelectorAll('.od-caption-actions button, .od-caption-actions svg')].every(element => getComputedStyle(element).getPropertyValue('-webkit-app-region') === 'no-drag')");
  if (!captionInteractive) throw new Error('Caption buttons overlap native drag regions');
  const evidence = process.env.OPENDIRECTOR_DESKTOP_EVIDENCE;
  for (const language of ['en-US', 'zh-CN']) {
    await window.webContents.executeJavaScript(`window.dispatchEvent(new StorageEvent('storage', { key: 'opendirector.locale.v1', newValue: '${language}' }))`);
    await delay(500);
    const current = await window.webContents.executeJavaScript('document.documentElement.lang');
    if (current !== language || getLocale() !== language) throw new Error('Desktop locale synchronization failed');
    if (!(await window.webContents.executeJavaScript('!!document.querySelector(".react-flow") && !!document.querySelector(".cw-save-status i.saved")'))) throw new Error('Canvas was lost during language switching');
    const settingsStyle = await window.webContents.executeJavaScript(`(() => {
      const button = document.querySelector('.od-sidebar-settings .cw-icon-button');
      return button && getComputedStyle(button).visibility === 'visible' && button.querySelector('svg') && !button.textContent.trim();
    })()`);
    if (!settingsStyle) throw new Error('Sidebar settings did not reuse the canvas icon button');
    await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-settings .cw-icon-button').click()");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.cw-settings-dialog[open]')"), 'Sidebar settings did not open');
    await window.webContents.executeJavaScript("document.querySelector('.cw-settings-close').click()");
    await waitFor(() => window.webContents.executeJavaScript("!document.querySelector('.cw-settings-dialog[open]')"), 'Settings did not close');
    await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-project-more').click()");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.od-sidebar-project-menu [role=menuitem]')"), 'Project menu did not open');
    const deleteLabel = await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-project-menu').textContent");
    if (!deleteLabel.includes(language === 'en-US' ? 'Delete' : '删除')) throw new Error('Project delete action was not localized');
    if (evidence) {
      mkdirSync(evidence, { recursive: true });
      writeFileSync(join(evidence, `project-menu-${language}.png`), (await window.webContents.capturePage()).toPNG());
    }
    await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-project-menu button').click()");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('dialog[open] .cw-project-dialog-actions')"), 'Delete confirmation did not open');
    await window.webContents.executeJavaScript("document.querySelector('dialog[open] .cw-project-dialog-actions button').click()");
    await waitFor(() => window.webContents.executeJavaScript("!document.querySelector('dialog[open]')"), 'Delete cancellation did not close confirmation');
    if (evidence) {
      mkdirSync(evidence, { recursive: true });
      const button = await window.webContents.executeJavaScript("(() => { const r = document.querySelector('.od-sidebar-create').getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()");
      window.webContents.sendInputEvent({ type: 'mouseMove', ...button });
      await waitFor(() => window.webContents.executeJavaScript("getComputedStyle(document.querySelector('.od-sidebar-create-shortcut')).opacity === '1'"), 'New project hover shortcut was not visible');
      writeFileSync(join(evidence, `new-project-hover-${language}.png`), (await window.webContents.capturePage()).toPNG());
      window.webContents.sendInputEvent({ type: 'mouseMove', x: 300, y: 100 });
      await waitFor(() => window.webContents.executeJavaScript("getComputedStyle(document.querySelector('.od-sidebar-create-shortcut')).opacity === '0'"), 'New project shortcut stayed visible after hover');
      await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-search-toggle').click()");
      await delay(200);
      writeFileSync(join(evidence, `search-open-${language}.png`), (await window.webContents.capturePage()).toPNG());
      await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-search-close').click()");
      await delay(200);
      for (const state of ['expanded', 'collapsed']) {
        if (state === 'collapsed') {
          await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
          await delay(500);
        }
        const placements = await window.webContents.executeJavaScript(`(() => {
          const open = document.querySelector('.cw-workspace').dataset.desktopSidebar === 'open';
          return { open, create: document.querySelectorAll(open ? '.od-sidebar-create' : '.od-caption-actions button').length };
        })()`);
        if (placements.open !== (state === 'expanded') || placements.create !== (state === 'expanded' ? 1 : 2)) throw new Error('New project button placement failed');
        window.webContents.invalidate();
        await delay(200);
        writeFileSync(join(evidence, `sidebar-${state}-${language}.png`), (await window.webContents.capturePage()).toPNG());
      }
      await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
      await delay(500);
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
    await delay(500);
    for (const fullscreen of [true, false]) {
      window.setFullScreen(fullscreen);
      await waitFor(() => window.webContents.executeJavaScript(`document.documentElement.hasAttribute('data-desktop-fullscreen') === ${fullscreen}`),
        `Fullscreen chrome synchronization failed: target=${fullscreen}`, 100);
    }
    await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
    await delay(500);
    const bounds = window.getBounds();
    window.setSize(800, 600);
    await delay(200);
    const fits = await window.webContents.executeJavaScript(`(() => {
      const language = document.querySelector('.cw-topbar .cw-language-toggle').getBoundingClientRect();
      const title = document.querySelector('.cw-topbar .cw-project-selector').getBoundingClientRect();
      const actions = document.querySelector('.cw-top-actions').getBoundingClientRect();
      return title.right <= language.left && language.right <= actions.left && actions.right <= innerWidth;
    })()`);
    if (!fits) throw new Error('Desktop controls overlap in a narrow window');
    window.setBounds(bounds);
    await delay(200);
    await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
    await delay(500);
    result.chrome = chrome;
  }
  if (process.env.OPENDIRECTOR_DESKTOP_SMOKE_DELETE === '1') {
    await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-create').click()");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.cw-project-name-editor input')"), 'Deletion test project was not created');
    await window.webContents.executeJavaScript("document.querySelector('.cw-project-name-editor input').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.cw-save-status i.saved') && !document.querySelector('.cw-project-name-editor input')"), 'Deletion test project was not saved');
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.od-sidebar-project-select[aria-current=page]')"), 'New project row did not load');
    await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-project-select[aria-current=page]').parentElement.querySelector('.od-sidebar-project-more').click()");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('.od-sidebar-project-menu button')"), 'Delete test menu did not open');
    await window.webContents.executeJavaScript("document.querySelector('.od-sidebar-project-menu button').click()");
    await waitFor(() => window.webContents.executeJavaScript("!!document.querySelector('dialog[open] button[type=submit]')"), 'Delete test confirmation did not open');
    await window.webContents.executeJavaScript("document.querySelector('dialog[open] button[type=submit]').click()");
    await waitFor(() => window.webContents.executeJavaScript(`(async () => {
      const response = await fetch('/api/creation/projects', {headers: {'X-OpenDirector-Request': '1'}});
      const value = await response.json();
      return !document.querySelector('dialog[open]') && !!document.querySelector('.cw-save-status i.saved') &&
        JSON.stringify(value.projects.map(p => p.id).sort()) === '${JSON.stringify(result.projectIds)}';
    })()`), 'Deleting the open test project did not restore the remaining projects');
    result.projectDeletion = true;
  }
  result.sidebarFinallyOpen = process.env.OPENDIRECTOR_DESKTOP_SMOKE_SIDEBAR === 'open';
  if (await window.webContents.executeJavaScript("document.querySelector('.cw-workspace').dataset.desktopSidebar === 'open'") !== result.sidebarFinallyOpen) {
    await window.webContents.executeJavaScript("document.querySelector('.od-caption-actions button').click()");
  }
  await waitFor(() => window.webContents.executeJavaScript(`localStorage.getItem('opendirector.desktop.sidebar.v1') === '${result.sidebarFinallyOpen ? 'open' : 'closed'}'`), 'Sidebar preference was not saved');
  await window.webContents.executeJavaScript('window.opendirectorDesktop.setExporting(true)');
  await delay(50);
  window.close();
  if (window.isVisible() || !(await host.inspect())) throw new Error('Closing a window stopped the Host');
  return result;
}

async function waitFor(check, message, attempts = 150) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    const value = await check();
    if (value) return value;
    await delay(100);
  }
  throw new Error(message);
}
