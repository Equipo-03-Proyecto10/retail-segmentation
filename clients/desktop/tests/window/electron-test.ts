import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';

const applicationCleanups = new Set<() => Promise<void>>();
const directoryCleanups = new Set<() => void>();

function temporaryDirectory(prefix: string): { dir: string; remove: () => void } {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  let removed = false;
  const remove = (): void => {
    if (removed) return;
    removed = true;
    rmSync(dir, { recursive: true, force: true });
    directoryCleanups.delete(remove);
  };
  directoryCleanups.add(remove);
  return { dir, remove };
}

export function makeTemporaryDirectory(prefix: string): string {
  return temporaryDirectory(prefix).dir;
}

export async function launchElectron(baseUrl: string): Promise<{ app: ElectronApplication; dir: string }> {
  const { dir, remove } = temporaryDirectory('mosaiq-desktop-');
  writeFileSync(join(dir, 'settings.json'), JSON.stringify({ baseUrl }));

  let app: ElectronApplication;
  try {
    app = await electron.launch({
      args: ['.'],
      env: { ...process.env, MOSAIQ_DESKTOP_CONFIG_DIR: dir },
    });
  } catch (error) {
    remove();
    throw error;
  }

  const closeApplication = app.close.bind(app);
  let closed = false;
  const cleanup = async (): Promise<void> => {
    if (closed) return;
    closed = true;
    try {
      await closeApplication();
    } finally {
      remove();
      applicationCleanups.delete(cleanup);
    }
  };
  app.close = cleanup;
  applicationCleanups.add(cleanup);
  return { app, dir };
}

test.afterEach(async () => {
  try {
    for (const cleanup of [...applicationCleanups]) await cleanup();
  } finally {
    for (const cleanup of [...directoryCleanups]) cleanup();
  }
});

export { expect, test };
export type { ElectronApplication, Page };
