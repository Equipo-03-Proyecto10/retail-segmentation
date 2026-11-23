import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, launchElectron, test, type Page } from './electron-test';

// F20-02 (#760): the desktop window against a live gateway, after `npm run evidence:f20-02`
// recorded its calls. It is skipped unless MOSAIQ_F20_02_ELECTRON_DIR names that run's
// directory, so the normal window run never reaches a network. It reads the sale and the
// draft from that run's result.json, and the accounts from the same MOSAIQ_F20_02_<STEP>_*
// variables (never an argument), and it creates nothing:
//
// 1. the staff account opens the sale on the Sales screen;
// 2. the MARKETING account opens the draft the run created on the Campaigns screen;
// 3. the forbidden account opens the Campaigns screen, which draws no create control for its
//    role, and the same write is then sent through the window's own bridge
//    (window.mosaiq.createCampaign, the call the form's submit makes), as campaigns.spec.ts
//    forces one. Its answer is written to window-forced-write.json beside the screenshots.
//
// Full-page screenshots go to MOSAIQ_EVIDENCE_DIR, as tests/support/evidence.ts names it, so a
// whole screen (the absent create form included) is in the picture. The runbook is
// docs/evidence/f20-02-electron-sale-campaign.md.

const RUN = process.env.MOSAIQ_F20_02_ELECTRON_DIR ?? '';

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.MOSAIQ_EVIDENCE_DIR;
  if (dir === undefined || dir === '') return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`), fullPage: true });
}

interface Run {
  base_url: string;
  sale_id: number;
  draft: { name: string; label_code: string; starts_on: string; ends_on: string };
  steps: {
    staff: { sale?: { store_id: number; occurred_at: string; total: string } };
    marketing: { campaign?: { campaign_id: number } };
  };
}

const account = (step: string): { email: string; password: string } => {
  const key = `MOSAIQ_F20_02_${step.toUpperCase()}`;
  const email = process.env[`${key}_EMAIL`];
  const password = process.env[`${key}_PASSWORD`];
  if (!email || !password) throw new Error(`set ${key}_EMAIL and ${key}_PASSWORD`);
  return { email, password };
};

/** The minute of a timestamp, and the next one, as the datetime-local filters take them (UTC). */
function minute(at: string): [string, string] {
  const start = new Date(at);
  start.setUTCSeconds(0, 0);
  const end = new Date(start.getTime() + 60_000);
  return [start.toISOString().slice(0, 16), end.toISOString().slice(0, 16)];
}

test.describe('F20-02 against a live gateway', () => {
  test.skip(RUN === '', 'set MOSAIQ_F20_02_ELECTRON_DIR to an evidence:f20-02 run');

  const run = (): Run => JSON.parse(readFileSync(join(RUN, 'result.json'), 'utf8')) as Run;

  async function signIn(step: string): Promise<{ page: Page; close: () => Promise<void> }> {
    const { app } = await launchElectron(run().base_url);
    const page = await app.firstWindow();
    await page.waitForSelector('form');
    const { email, password } = account(step);
    await page.fill('#f-email', email);
    await page.fill('#f-password', password);
    await page.click('button[type=submit]');
    await expect(page.getByTestId('role')).toHaveText(/^Signed in as /);
    return {
      page,
      close: async () => {
        await page.getByText('Sign out').click();
        await page.waitForSelector('form');
        await app.close();
      },
    };
  }

  const nav = (page: Page, view: string) => page.locator(`nav.nav button[data-view="${view}"]`);

  test('1. the staff account reads the sale', async () => {
    const r = run();
    const sale = r.steps.staff.sale;
    expect(sale, 'the evidence run read the sale').toBeDefined();
    const { page, close } = await signIn('staff');
    await nav(page, 'sales').click();
    const filters = page.getByTestId('sales-filters');
    const [from, to] = minute(sale!.occurred_at);
    await filters.locator('select').selectOption(String(sale!.store_id));
    await filters.locator('input[type=datetime-local]').nth(0).fill(from);
    await filters.locator('input[type=datetime-local]').nth(1).fill(to);
    await filters.getByRole('button', { name: 'Search' }).click();
    await page.getByTestId(`view-sale-${r.sale_id}`).click();
    await expect(page.getByTestId('sale-total')).toHaveText(sale!.total);
    await shot(page, 'f20-02-desktop-01-sale');
    await close();
  });

  test('2. the MARKETING account sees the draft it created', async () => {
    const r = run();
    const id = r.steps.marketing.campaign?.campaign_id;
    expect(id, 'the evidence run created a draft').toBeDefined();
    const { page, close } = await signIn('marketing');
    await nav(page, 'campaigns').click();
    await expect(page.getByTestId('campaign-create-form')).toHaveCount(1);
    // Only the drafts of the run's label, on the largest page, so the new one is listed.
    const filters = page.getByTestId('campaign-filters');
    const [status, label, size] = [0, 1, 2].map((i) => filters.locator('select').nth(i));
    await status!.selectOption('DRAFT');
    await expect(label!.locator(`option[value="${r.draft.label_code}"]`)).toHaveCount(1);
    await label!.selectOption(r.draft.label_code);
    const largest = await size!.locator('option').last().getAttribute('value');
    await size!.selectOption(largest ?? '20');
    await filters.getByRole('button', { name: 'Search' }).click();
    await page.getByTestId(`view-campaign-${id}`).click();
    await expect(page.getByTestId('campaign-name')).toHaveText(r.draft.name);
    await expect(page.getByTestId('campaign-status')).toHaveText('DRAFT');
    await shot(page, 'f20-02-desktop-02-draft-campaign');
    await close();
  });

  test('3. a role outside x-mosaiq-roles: no create control, and the forced write is refused', async () => {
    const r = run();
    const { page, close } = await signIn('forbidden');
    await nav(page, 'campaigns').click();
    await expect(page.getByTestId('campaigns-table')).toBeVisible();
    await expect(page.getByTestId('campaign-create-form')).toHaveCount(0);
    await shot(page, 'f20-02-desktop-03-no-create-control');
    const forced = await page.evaluate((draft) => window.mosaiq.createCampaign(draft), r.draft);
    expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    const dir = process.env.MOSAIQ_EVIDENCE_DIR;
    if (dir) writeFileSync(join(dir, 'window-forced-write.json'), JSON.stringify(forced, null, 2) + '\n');
    await close();
  });
});
