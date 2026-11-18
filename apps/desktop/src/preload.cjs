const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('opendirectorDesktop', {
  registerSession: id => ipcRenderer.send('desktop:session', id),
  setLocale: locale => ipcRenderer.send('desktop:locale', locale),
  setExporting: active => ipcRenderer.send('desktop:exporting', active),
});

function markDesktop() {
  document.documentElement.dataset.desktopPlatform = process.platform;
  const theme = () => ipcRenderer.send('desktop:theme', document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
  theme();
  new MutationObserver(theme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}
if (document.documentElement) markDesktop();
else window.addEventListener('DOMContentLoaded', markDesktop, { once: true });
ipcRenderer.on('desktop:fullscreen', (_event, fullscreen) => {
  if (fullscreen) document.documentElement.dataset.desktopFullscreen = 'true';
  else delete document.documentElement.dataset.desktopFullscreen;
});
