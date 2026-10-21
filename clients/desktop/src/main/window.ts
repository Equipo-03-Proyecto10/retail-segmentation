import { join } from 'node:path';
import { BrowserWindow, session } from 'electron';

export const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self'",
  "connect-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

/** The only place a BrowserWindow is created. */
export function createSecureWindow(distDir: string): BrowserWindow {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    show: false,
    title: 'MOSAIQ back office',
    webPreferences: {
      preload: join(distDir, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  const contents = win.webContents;
  contents.on('will-navigate', (event) => event.preventDefault());
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.once('ready-to-show', () => win.show());
  void win.loadFile(join(distDir, 'renderer', 'index.html'));
  return win;
}

/** Denies every permission request and repeats the CSP as a response header. */
export function hardenSession(): void {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.webRequest.onHeadersReceived((details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [CSP] } });
  });
}
