import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test as base } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';

// The one place a window test creates a directory in the temporary directory (#1028). Electron fills
// a profile with caches, so a spec that makes its own leaves about 2 MB behind per launch. Every
// spec imports `test` from here: an automatic fixture removes what the test made, after the
// application is closed, whether the test passed or failed.
//
//   const { app } = await launchElectron(baseUrl);     // a profile, removed at the end of the test
//   const dir = makeTemporaryDirectory('mosaiq-out-'); // any other scratch directory, likewise
//
// `tests/unit/window-profile-ownership.test.ts` fails when a spec creates its own.

interface Made {
  apps: Array<{ app: ElectronApplication; closed: () => boolean }>;
  directories: string[];
}

// One worker runs one test at a time (playwright.config.ts), so one set of made things is current.
let made: Made = { apps: [], directories: [] };

/** A scratch directory, removed when the test ends. */
export function makeTemporaryDirectory(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  made.directories.push(dir);
  return dir;
}

/**
 * Launches the desktop app against `baseUrl` with a fresh profile (`MOSAIQ_DESKTOP_CONFIG_DIR`). The profile
 * is removed when the test ends, after the application has been closed; a test may close the application itself.
 */
export async function launchElectron(baseUrl: string): Promise<{ app: ElectronApplication; dir: string }> {
  const dir = makeTemporaryDirectory('mosaiq-desktop-');
  writeFileSync(join(dir, 'settings.json'), JSON.stringify({ baseUrl }));
  const app = await electron.launch({
    args: ['.'],
    env: { ...process.env, MOSAIQ_DESKTOP_CONFIG_DIR: dir },
  });
  let closed = false;
  app.on('close', () => {
    closed = true;
  });
  made.apps.push({ app, closed: () => closed });
  return { app, dir };
}

async function cleanUp(finished: Made): Promise<void> {
  try {
    for (const { app, closed } of finished.apps) {
      if (!closed()) await app.close().catch(() => undefined);
    }
  } finally {
    for (const dir of finished.directories) rmSync(dir, { recursive: true, force: true });
  }
}

export const test = base.extend<{ temporaryDirectories: void }>({
  temporaryDirectories: [
    // eslint-disable-next-line no-empty-pattern -- Playwright reads a fixture's dependencies from this pattern
    async ({}, use) => {
      const mine: Made = { apps: [], directories: [] };
      made = mine;
      try {
        await use();
      } finally {
        await cleanUp(mine);
      }
    },
    { auto: true },
  ],
});

export { expect };
export type { ElectronApplication, Page };

/**
 * Pages forward with the screen's own Next button until `wanted` is listed. On an instance
 * where earlier evidence runs left many drafts of the label, the new one need not be on page 1.
 */
export async function findOnPages(page: Page, noun: string, wanted: string): Promise<void> {
  const summary = page.getByTestId(`${noun}-page`);
  await expect(summary).toBeVisible();
  for (;;) {
    if ((await page.getByTestId(wanted).count()) > 0) return;
    const next = page.getByTestId(`${noun}-next`);
    const at = await summary.textContent();
    expect(await next.isDisabled(), `${wanted} is on no page (${at ?? ''})`).toBe(false);
    await next.click();
    await expect(summary).not.toHaveText(at ?? '');
  }
}
