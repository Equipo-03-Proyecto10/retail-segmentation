import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';

// Campaigns and their transitions (F16-08, #486), against a fake gateway that answers
// with the recorded examples of contracts/campaigns/ and contracts/segments/. Campaign
// 301 is the one the examples describe; the fake keeps its status, so a refusal such as
// status_changed arises from what is stored, as it does in the service.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
/** Element names and values of a request body, in order, to compare a sent request with a recorded one. */
const fieldsOf = (xml: string): [string, string][] =>
  [...xml.matchAll(/<([a-z_]+)>([^<]*)<\/\1>/g)].map((m) => [m[1] as string, m[2] as string]);

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;
const forbidden = `<Error xmlns="${COMMON}"><code>forbidden_role</code><message>Your role may not do this.</message><correlation_id>corr-forbidden</correlation_id></Error>`;

type Status = 'DRAFT' | 'ACTIVE' | 'FINISHED' | 'CANCELLED';
/** The recorded example that shows campaign 301 in each status. */
const SHOWN: Record<Status, string> = {
  DRAFT: 'campaign',
  ACTIVE: 'draft-to-active',
  FINISHED: 'active-to-finished',
  CANCELLED: 'draft-to-cancelled',
};
const LEGAL: Record<Status, Status[]> = {
  DRAFT: ['ACTIVE', 'CANCELLED'],
  ACTIVE: ['FINISHED', 'CANCELLED'],
  FINISHED: [],
  CANCELLED: [],
};

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'MARKETING';
let seen: Seen[] = [];
let stored: Status = 'DRAFT';
let activation: 'ok' | 'campaign-ended' | 'experiment-not-ready' = 'ok';
let nextRefusal: { file: string; status: number } | null = null;

function reset(): void {
  seen = [];
  stored = 'DRAFT';
  activation = 'ok';
  nextRefusal = null;
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      seen.push({ method, url: url.toString(), body });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (path === '/api/segments/v1/segments') return reply(200, example('segments', 'list-segments'));
      const writer = role === 'ADMIN' || role === 'MARKETING';
      if (method !== 'GET' && !writer) return reply(403, forbidden);
      if (method !== 'GET' && nextRefusal !== null) {
        const r = nextRefusal;
        nextRefusal = null;
        return reply(r.status, example('campaigns', r.file));
      }
      if (path === '/api/campaigns/v1/campaigns') {
        if (method === 'POST') return reply(201, example('campaigns', 'campaign'));
        return reply(200, example('campaigns', 'campaigns'));
      }
      const match = /^\/api\/campaigns\/v1\/campaigns\/(\d+)(\/transitions)?$/.exec(path);
      if (!match || match[1] !== '301') return reply(404, example('campaigns', 'campaign-not-found'));
      if (method === 'GET') return reply(200, example('campaigns', SHOWN[stored]));
      if (method === 'PATCH')
        return stored === 'DRAFT'
          ? reply(200, example('campaigns', 'campaign'))
          : reply(409, example('campaigns', 'campaign-not-draft'));
      const field = (name: string): string => new RegExp(`<${name}>([^<]*)</${name}>`).exec(body)?.[1] ?? '';
      const to = field('to') as Status;
      if (field('expected_status') !== stored) return reply(409, example('campaigns', 'status-changed'));
      if (!LEGAL[stored].includes(to)) return reply(409, example('campaigns', 'illegal-transition'));
      if (to === 'ACTIVE' && activation !== 'ok') return reply(409, example('campaigns', activation));
      const file = `${stored.toLowerCase()}-to-${to.toLowerCase()}`;
      stored = to;
      return reply(200, example('campaigns', file));
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(reset);

async function launch(as: string): Promise<{ app: ElectronApplication; page: Page }> {
  role = as;
  const dir = mkdtempSync(join(tmpdir(), 'mosaiq-desktop-'));
  writeFileSync(join(dir, 'settings.json'), JSON.stringify({ baseUrl: base }));
  const app = await electron.launch({ args: ['.'], env: { ...process.env, MOSAIQ_DESKTOP_CONFIG_DIR: dir } });
  const page = await app.firstWindow();
  await page.waitForSelector('form');
  await page.fill('#f-email', 'a@b.c');
  await page.fill('#f-password', 'pw');
  await page.click('button[type=submit]');
  await expect(page.getByTestId('role')).toHaveText(`Signed in as ${as}`);
  return { app, page };
}

const nav = (page: Page, name: string) => page.locator(`nav.nav button[data-view="${name}"]`);
const openScreen = (page: Page) => nav(page, 'campaigns').click();
const writes = (): Seen[] => seen.filter((s) => s.method !== 'GET' && s.url.includes('/campaigns'));
const transitionsSent = (): Seen[] => writes().filter((s) => s.url.endsWith('/transitions'));

/** Opens campaign 301 and waits for its detail before a form is touched: a late detail replaces the form. */
async function openDetail(page: Page): Promise<void> {
  await page.getByTestId('view-campaign-301').click();
  await expect(page.getByTestId('campaign-name')).toHaveText('Holiday Loyalty Preview');
}

test('the screen is shown to ADMIN, ANALYST, MARKETING and AUDITOR and hidden from the other roles', async () => {
  for (const [as, visible] of [
    ['ADMIN', true],
    ['ANALYST', true],
    ['MARKETING', true],
    ['AUDITOR', true],
    ['STORE_MANAGER', false],
    ['INVENTORY_PLANNER', false],
  ] as const) {
    const { app, page } = await launch(as);
    await expect(nav(page, 'campaigns')).toHaveCount(visible ? 1 : 0);
    await app.close();
  }
});

test('the list shows label names from segments-service, filters and opens one campaign', async () => {
  const { app, page } = await launch('MARKETING');
  await openScreen(page);
  await expect(page.locator('[data-testid=campaigns-table] tbody tr')).toHaveCount(2);
  await expect(page.getByTestId('campaign-label-301')).toHaveText('Loyal (LOYAL)');
  await expect(page.getByTestId('campaign-label-302')).toHaveText('At risk (AT_RISK)');
  await expect(page.getByTestId('campaign-status-301')).toHaveText('DRAFT');
  await expect(page.getByTestId('campaigns-page')).toHaveText('Page 1 of 1 · 2 campaigns');
  await shot(page, '486-desktop-campaigns-01-list');
  expect(seen.some((s) => s.url.endsWith('/api/segments/v1/segments'))).toBe(true);

  await page.selectOption('#filter-status', 'ACTIVE');
  await page.selectOption('#filter-label_code', 'AT_RISK');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect
    .poll(() => seen.filter((s) => s.url.includes('/campaigns?')).at(-1)?.url ?? '')
    .toContain('status=ACTIVE&label_code=AT_RISK&page=1&size=20');

  await openDetail(page);
  await expect(page.getByTestId('campaign-label')).toHaveText('Loyal (LOYAL)');
  await expect(page.getByTestId('campaign-starts-on')).toHaveText('2026-11-01');
  await expect(page.getByTestId('campaign-ends-on')).toHaveText('2026-12-31');
  await shot(page, '486-desktop-campaigns-02-detail');
  await expect(page.getByTestId('campaign-status')).toHaveText('DRAFT');
  expect(seen.some((s) => s.method === 'GET' && s.url.endsWith('/campaigns/301'))).toBe(true);

  await page
    .evaluate(() => window.mosaiq.getCampaign(9999))
    .then((r) => expect(r).toMatchObject({ ok: false, error: { code: 'campaign_not_found' } }));
  await app.close();
});

for (const as of ['ANALYST', 'AUDITOR']) {
  test(`${as} reads only: no control is drawn and a forced write is refused as returned`, async () => {
    const { app, page } = await launch(as);
    await openScreen(page);
    await expect(page.locator('[data-testid=campaigns-table] tbody tr')).toHaveCount(2);
    await openDetail(page);
    await expect(page.getByTestId('campaign-create-form')).toHaveCount(0);
    await expect(page.getByTestId('campaign-edit-form')).toHaveCount(0);
    await expect(page.getByTestId('campaign-transitions')).toHaveCount(0);
    const forced = await page.evaluate(() => window.mosaiq.transitionCampaign(301, 'ACTIVE', 'DRAFT'));
    expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    const created = await page.evaluate(() =>
      window.mosaiq.createCampaign({
        name: 'N',
        label_code: 'LOYAL',
        starts_on: '2026-11-01',
        ends_on: '2026-11-02',
      }),
    );
    expect(created).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    await app.close();
  });
}

test('creating sends the recorded request and shows the campaign as DRAFT', async () => {
  const { app, page } = await launch('MARKETING');
  await openScreen(page);
  const form = page.getByTestId('campaign-create-form');
  await expect(form.locator('#create-label_code option')).toHaveCount(6);
  await form.locator('#create-name').fill('Holiday Loyalty Preview');
  await form.locator('#create-label_code').selectOption('LOYAL');
  await form.locator('#create-starts_on').fill('2026-11-01');
  await form.locator('#create-ends_on').fill('2026-12-31');
  await form.getByRole('button', { name: 'Create campaign' }).click();
  await expect(page.getByTestId('notice')).toHaveText('Campaign Holiday Loyalty Preview created as DRAFT.');
  await expect(page.getByTestId('campaign-status')).toHaveText('DRAFT');
  const post = writes().at(-1)!;
  expect(post.method).toBe('POST');
  expect(fieldsOf(post.body)).toEqual(fieldsOf(example('campaigns', 'create-campaign.request')));
  await app.close();
});

test('an end before the start shows ends_before_start next to the dates; duplicate_draft shows as a message', async () => {
  const { app, page } = await launch('ADMIN');
  await openScreen(page);
  const form = page.getByTestId('campaign-create-form');
  await form.locator('#create-name').fill('Holiday Loyalty Preview');
  await form.locator('#create-label_code').selectOption('LOYAL');
  await form.locator('#create-starts_on').fill('2026-12-31');
  await form.locator('#create-ends_on').fill('2026-11-01');
  nextRefusal = { file: 'ends-before-start', status: 422 };
  await form.getByRole('button', { name: 'Create campaign' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('ends_before_start');
  await expect(page.getByTestId('error-message')).toHaveText('ends_on cannot be before starts_on.');
  await expect(page.getByTestId('error-correlation')).toHaveText('corr-campaign-422b');
  await expect(form.getByTestId('detail-ends_on')).toHaveText('ends_on: before_start');
  await shot(page, '486-desktop-campaigns-03-ends-before-start');
  await expect(form.locator('#create-ends_on')).toHaveValue('2026-11-01');

  nextRefusal = { file: 'duplicate-draft', status: 409 };
  await form.getByRole('button', { name: 'Create campaign' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('duplicate_draft');
  await expect(page.getByTestId('error-correlation')).toHaveText('corr-campaign-409a');
  await expect(form.getByTestId('detail-ends_on')).toHaveCount(0);

  nextRefusal = { file: 'unknown-label', status: 422 };
  await form.getByRole('button', { name: 'Create campaign' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('unknown_label');
  await expect(form.getByTestId('detail-label_code')).toHaveText('label_code: unknown');
  expect(writes()).toHaveLength(3);
  await app.close();
});

test('a DRAFT is edited with only the changed fields; outside DRAFT the controls are disabled', async () => {
  const { app, page } = await launch('MARKETING');
  await openScreen(page);
  await openDetail(page);
  const form = page.getByTestId('campaign-edit-form');
  await expect(form.locator('#edit-name')).toBeEnabled();
  await form.locator('#edit-name').fill('Holiday Loyalty Launch');
  await form.locator('#edit-ends_on').fill('2027-01-15');
  await form.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('notice')).toHaveText('Campaign Holiday Loyalty Preview saved.');
  const patch = writes().at(-1)!;
  expect(patch.method).toBe('PATCH');
  expect(patch.url).toContain('/campaigns/301');
  expect(fieldsOf(patch.body)).toEqual(fieldsOf(example('campaigns', 'update-campaign.request')));

  // A change the service refuses is shown as returned, with its field. The form was drawn again
  // from the saved campaign, so no reload is awaited here.
  nextRefusal = { file: 'ends-before-start', status: 422 };
  await page.locator('#edit-ends_on').fill('2026-10-01');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('ends_before_start');
  await expect(page.getByTestId('campaign-edit-form').getByTestId('detail-ends_on')).toHaveText(
    'ends_on: before_start',
  );

  // Once ACTIVE the edit controls are disabled, and a forced edit gets 409 campaign_not_draft.
  stored = 'ACTIVE';
  await openDetail(page);
  await expect(page.getByTestId('campaign-status')).toHaveText('ACTIVE');
  await expect(page.getByTestId('edit-locked')).toBeVisible();
  for (const control of await page
    .locator('[data-testid=campaign-edit-form] :is(input, select, button)')
    .all())
    await expect(control).toBeDisabled();
  const forced = await page.evaluate(() => window.mosaiq.updateCampaign(301, { name: 'Forced' }));
  expect(forced).toMatchObject({ ok: false, error: { code: 'campaign_not_draft' } });
  await app.close();
});

test('only the moves the status allows are offered; each is sent with the status that was displayed', async () => {
  const { app, page } = await launch('MARKETING');
  await openScreen(page);
  await openDetail(page);
  await expect(page.locator('[data-testid=campaign-transitions] button')).toHaveText([
    'Move to ACTIVE',
    'Move to CANCELLED',
  ]);
  await page.getByTestId('transition-ACTIVE').click();
  await expect(page.getByTestId('notice')).toHaveText('Campaign Holiday Loyalty Preview is now ACTIVE.');
  await expect(page.getByTestId('campaign-status')).toHaveText('ACTIVE');
  await shot(page, '486-desktop-campaigns-04-moved-to-active');
  expect(fieldsOf(transitionsSent().at(-1)!.body)).toEqual(
    fieldsOf(example('campaigns', 'draft-to-active.request')),
  );
  await expect(page.locator('[data-testid=campaign-transitions] button')).toHaveText([
    'Move to FINISHED',
    'Move to CANCELLED',
  ]);
  await page.getByTestId('transition-FINISHED').click();
  await expect(page.getByTestId('campaign-status')).toHaveText('FINISHED');
  expect(fieldsOf(transitionsSent().at(-1)!.body)).toEqual(
    fieldsOf(example('campaigns', 'active-to-finished.request')),
  );
  // FINISHED is terminal: nothing is offered.
  await expect(page.locator('[data-testid=campaign-transitions] button')).toHaveCount(0);
  // The list shows the stored status after each move.
  await expect(page.getByTestId('campaign-status-301')).toBeVisible();
  await app.close();
});

test('the two cancellations send their recorded requests', async () => {
  const { app, page } = await launch('ADMIN');
  await openScreen(page);
  await openDetail(page);
  await page.getByTestId('transition-CANCELLED').click();
  await expect(page.getByTestId('campaign-status')).toHaveText('CANCELLED');
  expect(fieldsOf(transitionsSent().at(-1)!.body)).toEqual(
    fieldsOf(example('campaigns', 'draft-to-cancelled.request')),
  );
  stored = 'ACTIVE';
  await openDetail(page);
  await expect(page.getByTestId('campaign-status')).toHaveText('ACTIVE');
  await page.getByTestId('transition-CANCELLED').click();
  await expect.poll(() => transitionsSent().length).toBe(2);
  await expect(page.getByTestId('campaign-status')).toHaveText('CANCELLED');
  expect(fieldsOf(transitionsSent().at(-1)!.body)).toEqual(
    fieldsOf(example('campaigns', 'active-to-cancelled.request')),
  );
  await app.close();
});

test('illegal_transition, campaign_ended and experiment_not_ready are shown as returned', async () => {
  const { app, page } = await launch('MARKETING');
  await openScreen(page);
  await openDetail(page);
  // A forced illegal move reaches the service, which refuses it.
  const illegal = await page.evaluate(() => window.mosaiq.transitionCampaign(301, 'FINISHED', 'DRAFT'));
  expect(illegal).toMatchObject({ ok: false, error: { code: 'illegal_transition' } });
  nextRefusal = { file: 'illegal-transition', status: 409 };
  await page.getByTestId('transition-ACTIVE').click();
  await expect(page.getByTestId('error-code')).toHaveText('illegal_transition');
  await expect(page.getByTestId('error-correlation')).toHaveText(/corr-/);
  await shot(page, '486-desktop-campaigns-05-illegal-transition');

  for (const code of ['campaign-ended', 'experiment-not-ready'] as const) {
    activation = code;
    await page.getByTestId('transition-ACTIVE').click();
    await expect(page.getByTestId('error-code')).toHaveText(code.replace(/-/g, '_'));
    // The status shown is still the stored one: nothing moved.
    await expect(page.getByTestId('campaign-status')).toHaveText('DRAFT');
  }
  await app.close();
});

test('after status_changed the campaign is reloaded and the move is not retried', async () => {
  const { app, page } = await launch('MARKETING');
  await openScreen(page);
  await openDetail(page);
  // Someone else activates it while this screen still shows DRAFT.
  stored = 'ACTIVE';
  const before = seen.length;
  await page.getByTestId('transition-CANCELLED').click();
  await expect(page.getByTestId('error-code')).toHaveText('status_changed');
  await expect(page.getByTestId('error-correlation')).toHaveText('corr-transition-409b');
  await expect(page.getByTestId('campaign-status')).toHaveText('ACTIVE');
  await expect(page.locator('[data-testid=campaign-transitions] button')).toHaveText([
    'Move to FINISHED',
    'Move to CANCELLED',
  ]);
  const after = seen.slice(before);
  expect(after.filter((s) => s.url.endsWith('/transitions'))).toHaveLength(1);
  expect(fieldsOf(after.find((s) => s.url.endsWith('/transitions'))!.body)).toEqual([
    ['to', 'CANCELLED'],
    ['expected_status', 'DRAFT'],
  ]);
  expect(after.some((s) => s.method === 'GET' && s.url.endsWith('/campaigns/301'))).toBe(true);
  await app.close();
});
