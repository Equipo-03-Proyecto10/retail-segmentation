import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CHANNELS } from '../../src/shared/api';

// Source audits for F28-16. They fail when a change widens the renderer's reach, so the
// Playwright test in tests/window/app.spec.ts only has to prove the running window.
const src = new URL('../../src', import.meta.url).pathname;

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? files(join(dir, entry.name))
      : entry.name.endsWith('.ts')
        ? [join(dir, entry.name)]
        : [],
  );
}
const read = (path: string): string => readFileSync(path, 'utf8');
const rel = (path: string): string => path.slice(src.length + 1);
const all = files(src);
const main = read(join(src, 'main', 'main.ts'));
const preload = read(join(src, 'preload', 'preload.ts'));

describe('BrowserWindow', () => {
  it('is constructed only in window.ts, with isolation, no node and the sandbox', () => {
    const builders = all.filter((f) => /new\s+BrowserWindow\s*\(/.test(read(f))).map(rel);
    expect(builders).toEqual(['main/window.ts']);
    const window = read(join(src, 'main', 'window.ts'));
    expect(window).toMatch(/contextIsolation:\s*true/);
    expect(window).toMatch(/nodeIntegration:\s*false/);
    expect(window).toMatch(/sandbox:\s*true/);
  });

  it('never loosens a web preference', () => {
    const loose =
      /webSecurity:\s*false|allowRunningInsecureContent:\s*true|webviewTag:\s*true|nodeIntegrationIn\w+:\s*true|enableRemoteModule|experimentalFeatures:\s*true/;
    expect(all.filter((f) => loose.test(read(f))).map(rel)).toEqual([]);
  });

  it('blocks navigation, webviews and new windows', () => {
    const window = read(join(src, 'main', 'window.ts'));
    expect(window).toMatch(/on\('will-navigate',\s*\(event\)\s*=>\s*event\.preventDefault\(\)\)/);
    expect(window).toMatch(/on\('will-attach-webview',\s*\(event\)\s*=>\s*event\.preventDefault\(\)\)/);
    expect(window).toMatch(/setWindowOpenHandler\(\(\)\s*=>\s*\(\{\s*action:\s*'deny'\s*\}\)\)/);
  });
});

describe('strict CSP', () => {
  it('denies everything by default and allows no inline or remote code', async () => {
    const { CSP } = await import('../../src/main/window');
    expect(CSP).toContain("default-src 'none'");
    expect(CSP).toContain("script-src 'self'");
    expect(CSP).toContain("connect-src 'none'");
    expect(CSP).not.toMatch(/unsafe-|\*|https?:/);
  });
});

describe('preload bridge', () => {
  const exposed = [...preload.matchAll(/CHANNELS\.(\w+)/g)].map((m) => m[1]!);

  it('exposes one object and never the ipcRenderer itself', () => {
    expect(preload.match(/exposeInMainWorld/g)).toHaveLength(1);
    expect(preload).not.toMatch(/exposeInMainWorld\(\s*'\w+',\s*ipcRenderer/);
  });

  it('reaches the main process only through named channels', () => {
    // A string literal channel, or a channel taken from the caller, would be an open pipe.
    expect(preload).not.toMatch(/ipcRenderer\.\w+\(\s*['"`]/);
    const calls = [...preload.matchAll(/ipcRenderer\.(\w+)\(\s*([^,)]+)/g)];
    expect(calls.length).toBeGreaterThan(0);
    for (const [, method, channel] of calls) {
      expect(['invoke', 'on', 'removeListener']).toContain(method);
      expect(channel).toMatch(/^CHANNELS\.\w+$/);
    }
    for (const name of exposed) expect(Object.keys(CHANNELS)).toContain(name);
  });
});

describe('main-process handlers', () => {
  const handlers = [...main.matchAll(/ipcMain\.handle\(\s*CHANNELS\.(\w+),\s*\(([^)]*)\)\s*=>/g)];

  it('answers every request channel exactly once, and nothing else', () => {
    const handled = handlers.map((m) => m[1]!).sort();
    const requests = Object.keys(CHANNELS)
      .filter((name) => name !== 'stateChanged')
      .sort();
    expect(handled).toEqual(requests);
    expect(main.match(/ipcMain\.handle\(/g)).toHaveLength(handlers.length);
    expect(main).not.toMatch(/ipcMain\.(on|once|handleOnce|addListener)\(/);
  });

  it('receives every renderer value as unknown, to be validated before use', () => {
    for (const [, channel, params] of handlers) {
      if (params!.trim() === '') continue;
      const [event, ...values] = params!.split(',').map((p) => p.trim());
      expect(event, channel).toBe('_e');
      for (const value of values) expect(value, channel).toMatch(/^\w+:\s*unknown$/);
    }
  });

  it('lets the renderer name no file', () => {
    const typed = handlers.flatMap((m) => m[2]!.split(',').map((p) => p.trim().split(':')[0]!));
    expect(typed.filter((name) => /path|file|dir/i.test(name))).toEqual([]);
    const bridge = read(join(src, 'shared', 'api.ts'));
    expect(bridge).not.toMatch(/\b(path|filePath|filename|directory)\??:\s*string/i);
  });
});

describe('file writes', () => {
  it('stay in the files that write under the user data directory, or follow a save dialog', () => {
    const writes =
      /\b(writeFile\w*|createWriteStream|appendFile\w*|copyFile\w*|rename\w*|cp\w*Sync|rmSync|unlink\w*)\s*\(/;
    const offenders = all
      .filter((f) => writes.test(read(f)))
      .filter((f) => !read(f).includes('showSaveDialog'))
      .map(rel)
      .filter((f) => f !== 'main/vault.ts');
    // An export writes only the path the main process's own save dialog returned.
    expect(offenders).toEqual([]);
  });

  it('keeps the renderer and the preload away from the file system', () => {
    const touching = all
      .filter((f) => /^(renderer|preload)\//.test(rel(f)))
      .filter((f) => /from\s+'node:|require\(/.test(read(f)));
    expect(touching.map(rel)).toEqual([]);
  });
});
