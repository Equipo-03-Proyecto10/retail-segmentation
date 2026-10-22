import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';

/**
 * Saves a screenshot for docs/evidence/ when MOSAIQ_EVIDENCE_DIR is set, and does nothing
 * otherwise, so the normal window run takes no pictures. Run it with `npm run evidence`.
 */
export async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.MOSAIQ_EVIDENCE_DIR;
  if (dir === undefined || dir === '') return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}
