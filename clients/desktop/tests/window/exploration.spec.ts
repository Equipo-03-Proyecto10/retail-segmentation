import { createServer, type Server } from 'node:http';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { deferred, type Deferred } from '../support/deferred';
import { shot } from '../support/evidence';
import {
  agreeingProfile,
  answer,
  CUSTOMER,
  example,
  OBJECTED,
  PENDING,
  xmlText,
  type Behaviour,
} from '../support/exploration';

// Customer exploration (F28-03, #872), against a fake gateway that answers with the recorded examples of the
// rfm-engine, sales, customer-preferences, customers, products, categories and stores contracts. The
// window is chosen so that every figure is the same whatever day the test runs on.

const NS = 'https://mosaiq.maxthecoder.online/xml';
const AUTH = `${NS}/auth/v1`;
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

const NAMES: Record<string, string> = {
  [CUSTOMER]: 'Jordan Lee',
  [OBJECTED]: 'Ana Torres',
  [PENDING]: 'Pending Pat',
};

/** Server text that is not markup but looks like it: it must reach the screen as text. */
const HOSTILE = '<img src=x onerror="window.xss=1">';
const cdata = (text: string): string => `<![CDATA[${text}]]>`;

let server: Server;
let base = '';
let role = 'ANALYST';
let urls: string[] = [];
let behaviour: Behaviour = {};
let hostile = false;
/** Requests for the profile of this customer wait until the test releases them. */
let hold: { customerId: string; gate: Deferred<void> } | null = null;

const customerXml = (id: string): string =>
  `<Customer xmlns="${NS}/customers/v1"><customer_id>${xmlText(id)}</customer_id><name>${
    hostile ? cdata(HOSTILE) : xmlText(NAMES[id] ?? 'Someone')
  }</name><registration_channel_id>3</registration_channel_id><registered_on>2025-09-17</registered_on></Customer>`;

const directoryXml = (list: string[]): string =>
  `<Customers xmlns="${NS}/customers/v1" xmlns:common="${NS}/common/v1">${list
    .map(
      (id) =>
        `<Customer><customer_id>${xmlText(id)}</customer_id><name>${xmlText(NAMES[id] ?? 'Someone')}</name><email>${xmlText(
          (NAMES[id] ?? 'someone').split(' ')[0]?.toLowerCase() ?? 'someone',
        )}@example.com</email><registration_channel_id>3</registration_channel_id><registered_on>2025-09-17</registered_on></Customer>`,
    )
    .join(
      '',
    )}<common:PageInfo><common:page>1</common:page><common:size>10</common:size><common:total>${list.length}</common:total></common:PageInfo></Customers>`;

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    req.resume();
    req.on('end', async () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      urls.push(url.toString());
      if (req.method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return req.method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (hold !== null && path.endsWith(`/rfm-profiles/${hold.customerId}`)) await hold.gate.promise;
      if (path === '/api/customers/v1/customers') return reply(200, directoryXml([CUSTOMER, OBJECTED]));
      const one = /^\/api\/customers\/v1\/customers\/([^/]+)$/.exec(path);
      if (one !== null) return reply(200, customerXml(one[1] as string));
      if (path.endsWith(`/${OBJECTED}/restrictions`) && behaviour.restrictions === undefined)
        return reply(200, example('customer-preferences', 'get-restrictions-personalization-withdrawn'));
      if (path.endsWith(`/rfm-profiles/${PENDING}`) && behaviour.profile === undefined)
        return reply(200, example('rfm-engine', 'profile-pending'));
      let found = answer(url, behaviour);
      if (found === undefined) return reply(404);
      if (hostile && found.status === 200 && /\/(channels|categories)$/.test(path))
        found = {
          ...found,
          body: found.body.replace(/<name>(physical_store|Yogurt)<\/name>/, `<name>${cdata(HOSTILE)}</name>`),
        };
      reply(found.status, found.body);
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(() => {
  urls = [];
  behaviour = {};
  hostile = false;
  hold = null;
});

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

/** The chosen window of the recorded sales: 1 to 26 October, so it ends where 27 October starts. */
async function openModule(page: Page): Promise<void> {
  await nav(page, 'exploration').click();
  await expect(page.getByTestId('exploration-form')).toBeVisible();
  await page.fill('#x-from', '2026-10-01');
  await page.fill('#x-to', '2026-10-26');
}

/** Explores a customer by id and waits for the panels that need the sales. */
async function explore(page: Page, customerId: string, sales = true): Promise<void> {
  await page.fill('#x-customer', customerId);
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await expect(page.getByTestId('exploration-profile')).not.toContainText('Loading');
  if (sales) await expect(page.getByTestId('exploration-purchases')).not.toContainText('Loading');
}

const text = (page: Page, id: string) => page.getByTestId(id);

test('a customer is explored with their purchases, recency, frequency, spend, categories and channels, each with its window', async () => {
  const { app, page } = await launch('ANALYST');
  await openModule(page);
  await page.fill('#x-q', 'Jordan');
  await page.getByRole('button', { name: 'Search customers' }).click();
  await expect(page.locator('[data-testid=exploration-search-table] tbody tr')).toHaveCount(2);
  await page.getByTestId(`explore-${CUSTOMER}`).click();
  await expect(page.getByTestId('exploration-purchases')).toContainText('#4812');
  await page.getByTestId('exploration-profile').scrollIntoViewIfNeeded();
  await shot(page, '872-desktop-exploration-01-figures');

  await expect(page.getByTestId('exploration-customer')).toContainText('Jordan Lee');
  await expect(text(page, 'customer-id')).toHaveText(CUSTOMER);

  // The profile is rfm-engine's, with the rule set that holds its window.
  await expect(text(page, 'profile-state')).toHaveText('scored');
  await expect(text(page, 'profile-rule-set')).toHaveText('#3 Seeded bands, 180-day window (published)');
  await expect(text(page, 'profile-window')).toHaveText(
    '2026-05-03T14:22:05Z to 2026-10-30T14:22:05Z (180 days)',
  );
  await expect(text(page, 'profile-recency')).toHaveText('30 days (score 4)');
  await expect(text(page, 'profile-frequency')).toHaveText('9 purchases (score 5)');
  await expect(text(page, 'profile-spend')).toHaveText('842.50 (score 4)');
  await expect(text(page, 'profile-label')).toHaveText('LOYAL');

  // Three windows side by side, each stated on its own cell.
  await expect(text(page, 'window-profile')).toHaveText(
    '2026-05-03T14:22:05Z to 2026-10-30T14:22:05Z (180 days)',
  );
  await expect(text(page, 'window-compare')).toHaveText(
    '2026-05-03T14:22:05Z to 2026-10-30T14:22:05Z (180 days)',
  );
  await expect(text(page, 'window-chosen')).toHaveText(
    '2026-10-01T00:00:00Z to 2026-10-27T00:00:00Z (26 days)',
  );
  await expect(text(page, 'figure-profile-recency')).toHaveText('30 days');
  await expect(text(page, 'figure-profile-frequency')).toHaveText('9');
  await expect(text(page, 'figure-profile-spend')).toHaveText('842.50');
  // The recorded sale 4812 is returned in part and 4811 is cancelled: one purchase, 110.00 left of 203.25.
  await expect(text(page, 'figure-compare-frequency')).toContainText('1');
  await expect(text(page, 'figure-compare-frequency')).toContainText('the profile says 9');
  await expect(text(page, 'figure-compare-spend')).toContainText('110.00');
  await expect(text(page, 'figure-compare-spend')).toContainText('the profile says 842.50');
  await expect(text(page, 'figure-compare-recency')).toContainText('14 days');
  await expect(text(page, 'figure-compare-recency')).toContainText('counted to 2026-10-30T14:22:05Z');
  await expect(text(page, 'figure-chosen-recency')).toContainText('11 days');
  await expect(text(page, 'figure-chosen-recency')).toContainText('latest purchase 2026-10-15T20:32:10Z');
  await expect(text(page, 'figure-chosen-recency')).toContainText('counted to 2026-10-27T00:00:00Z');
  await expect(text(page, 'figure-chosen-frequency')).toHaveText('1');
  await expect(text(page, 'figure-chosen-spend')).toHaveText('110.00');
  await expect(text(page, 'figures-net')).toContainText('net of returns and cancellations');
  await expect(text(page, 'figures-charged')).toHaveCount(0);

  // Categories and channels are worked out from the sales lines and the products' categories, one table each, both windows named.
  await expect(text(page, 'categories-window-compare')).toContainText(
    'Profile’s window: 2026-05-03T14:22:05Z to 2026-10-30T14:22:05Z (180 days)',
  );
  await expect(text(page, 'categories-window-chosen')).toContainText(
    'Chosen window: 2026-10-01T00:00:00Z to 2026-10-27T00:00:00Z (26 days)',
  );
  await expect(page.locator('[data-testid=categories-table] tbody tr[data-id="12"] td')).toHaveText([
    'Yogurt',
    '1',
    '97.50',
    '1',
    '3',
    '97.50',
  ]);
  await expect(page.locator('[data-testid=categories-table] tbody tr[data-id="11"] td')).toHaveText([
    'Cheese',
    '1',
    '12.50',
    '1',
    '1',
    '12.50',
  ]);
  await expect(page.locator('[data-testid=channels-table] tbody tr')).toHaveCount(1);
  await expect(page.locator('[data-testid=channels-table] tbody tr td')).toHaveText([
    'physical_store',
    '1',
    '110.00',
    '1',
    '4',
    '110.00',
  ]);
  await expect(text(page, 'channels-window-chosen')).toContainText('Chosen window:');

  // The purchases of the chosen window, the reversed one marked and not counted.
  await expect(text(page, 'purchases-window')).toContainText(
    '2026-10-01T00:00:00Z to 2026-10-27T00:00:00Z (26 days)',
  );
  const rows = page.locator('[data-testid=purchases-table] tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveAttribute('data-status', 'partly_reversed');
  await expect(rows.nth(0)).toContainText('Polanco');
  await expect(rows.nth(0)).toContainText('203.25');
  await expect(rows.nth(0)).toContainText('110.00');
  await expect(rows.nth(1)).toHaveAttribute('data-status', 'reversed');
  await expect(rows.nth(1)).toContainText('Not counted: reversed in full');

  // The interests, declared and inferred apart, the inferred ones with their window.
  await expect(text(page, 'declared-interests')).toContainText('Cheese, Yogurt');
  await expect(page.locator('[data-testid=inferred-table] tbody tr').first()).toContainText('180 days');

  // What was asked: the customer's sales over both windows in one read, and the profile.
  const sales = urls.find((u) => u.includes('/api/sales/v1/sales?'));
  expect(sales).toContain(`customer_id=${CUSTOMER}`);
  expect(sales).toContain('from=2026-05-03T14%3A22%3A05Z');
  expect(sales).toContain('to=2026-10-30T14%3A22%3A05Z');
  expect(urls.some((u) => u.endsWith(`/api/rfm-engine/v1/rfm-profiles/${CUSTOMER}`))).toBe(true);
  expect(urls.some((u) => u.endsWith('/api/rfm-engine/v1/rfm-rule-sets/3'))).toBe(true);
  await app.close();
});

test('a profile that the sales confirm says so, and a customer the engine has not scored has nothing to set beside', async () => {
  const { app, page } = await launch('ANALYST');
  behaviour = { profile: agreeingProfile() };
  await openModule(page);
  await page.fill('#x-to', '2026-10-30');
  await explore(page, CUSTOMER);
  await expect(text(page, 'figure-compare-frequency')).toContainText('same as the profile');
  await expect(text(page, 'figure-compare-spend')).toContainText('same as the profile');
  await expect(text(page, 'figure-compare-recency')).toContainText('same as the profile');

  behaviour = {};
  await explore(page, PENDING);
  await expect(text(page, 'profile-pending')).toContainText('No profile has been computed');
  await expect(text(page, 'window-profile')).toContainText('Not available: no profile computed yet');
  await expect(text(page, 'window-compare')).toContainText('Not available');
  await expect(text(page, 'figure-profile-frequency')).toHaveText('—');
  // The chosen window is still read and shown with its own window.
  await expect(text(page, 'figure-chosen-frequency')).toHaveText('1');
  expect(urls.filter((u) => u.includes(`customer_id=${PENDING}`) && u.includes('/sales?'))).toHaveLength(1);
  await app.close();
});

test('a customer who objected has their restriction visible, and a role that reads the consent record sees it too', async () => {
  const { app, page } = await launch('ADMIN');
  await openModule(page);
  await explore(page, OBJECTED);
  await page.getByTestId('exploration-restrictions').scrollIntoViewIfNeeded();
  await shot(page, '872-desktop-exploration-02-objected');
  await expect(text(page, 'personalization-not-allowed')).toContainText('Personalization is not allowed');
  await expect(text(page, 'personalization-not-allowed')).toContainText('withdrawn or was never recorded');
  await expect(text(page, 'personalization-allowed')).toHaveCount(0);
  await expect(text(page, 'no-restrictions')).toBeVisible();

  // The consent record is the administrator's to read: each purpose, with its basis said as it is.
  await expect(page.locator('[data-testid=consent-table] tbody tr')).toHaveCount(3);
  await expect(text(page, 'consent-state-PERSONALIZATION')).toHaveText('GRANTED');
  await expect(text(page, 'consent-state-MARKETING_CONTACT')).toHaveText('WITHDRAWN');
  await expect(page.locator('tr[data-purpose=MARKETING_CONTACT]')).toContainText(
    'Programme default (not the customer’s own consent)',
  );

  // A customer with a channel and a category restricted shows them by name, personalization allowed.
  await explore(page, CUSTOMER);
  await expect(text(page, 'personalization-allowed')).toBeVisible();
  await expect(text(page, 'restricted-channels')).toHaveText('call_center');
  await expect(text(page, 'restricted-categories')).toHaveText('Beverages');
  await app.close();
});

test('a role without permission is refused, and the screen says who refused it', async () => {
  // The contract leaves the consent record to ADMIN, AUDITOR, STORE_MANAGER and LOYALTY_OFFICER: the analyst's
  // console refuses before any call, and the rest of the screen is unaffected.
  const { app, page } = await launch('ANALYST');
  await openModule(page);
  await explore(page, OBJECTED);
  await page.getByTestId('exploration-consent').scrollIntoViewIfNeeded();
  await shot(page, '872-desktop-exploration-03-refused');
  await expect(text(page, 'refusal-consent')).toContainText('Your role may not read the consent record');
  await expect(text(page, 'refusal-consent')).toContainText('refused it before calling the service');
  await expect(page.getByTestId('exploration-consent').getByTestId('error-code')).toHaveText(
    'forbidden_role',
  );
  await expect(page.getByTestId('exploration-consent').getByTestId('error-correlation')).toHaveText('-');
  expect(urls.some((u) => u.includes('/consent'))).toBe(false);
  // What the analyst may read is still there: the restriction of the customer who objected.
  await expect(text(page, 'personalization-not-allowed')).toBeVisible();
  await expect(page.locator('[data-testid=purchases-table] tbody tr')).toHaveCount(2);
  await app.close();
});

test('a refusal by the service itself is shown as the service sent it, beside the panels that were served', async () => {
  const { app, page } = await launch('ANALYST');
  behaviour = {
    override: { '/v1/sales': { status: 403, body: example('sales', 'forbidden-role') } },
  };
  await openModule(page);
  await explore(page, CUSTOMER);
  for (const panel of ['figures', 'categories', 'channels', 'purchases']) {
    const box = page.getByTestId(`exploration-${panel}`);
    await expect(box.getByTestId('refusal-sales').or(box.getByTestId(`refusal-${panel}`))).toContainText(
      'The service refused your role',
    );
    await expect(box.getByTestId('error-code')).toHaveText('forbidden_role');
    await expect(box.getByTestId('error-correlation')).toHaveText('corr-forbidden-role');
  }
  await shot(page, '872-desktop-exploration-04-service-refusal');
  // The panels the other services answered are drawn all the same.
  await expect(text(page, 'profile-state')).toHaveText('scored');
  await expect(text(page, 'restricted-channels')).toHaveText('call_center');
  await app.close();
});

test('a sales read the service cannot record is shown as the service’s refusal, beside the other panels', async () => {
  const { app, page } = await launch('ANALYST');
  behaviour = {
    override: { '/v1/sales': { status: 503, body: example('customer-preferences', 'audit-unavailable') } },
  };
  await openModule(page);
  await explore(page, CUSTOMER);
  const box = page.getByTestId('exploration-figures');
  await expect(box.getByTestId('refusal-sales')).toContainText(
    'could not record this read in the audit trail',
  );
  await expect(box.getByTestId('error-code')).toHaveText('audit_unavailable');
  await expect(box.getByTestId('error-correlation')).toHaveText('corr-audit-unavailable');
  await expect(text(page, 'profile-state')).toHaveText('scored');
  await app.close();
});

test('a consent read the service cannot record is shown as the service’s refusal', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  behaviour = {
    override: { '/consent': { status: 503, body: example('customer-preferences', 'audit-unavailable') } },
  };
  await openModule(page);
  await explore(page, CUSTOMER);
  await expect(text(page, 'refusal-consent')).toContainText('could not record this read in the audit trail');
  const box = page.getByTestId('exploration-consent');
  await expect(box.getByTestId('error-code')).toHaveText('audit_unavailable');
  await expect(box.getByTestId('error-message')).toHaveText(
    'The read could not be recorded, so it was not served.',
  );
  await expect(box.getByTestId('error-correlation')).toHaveText('corr-audit-unavailable');
  // A manager cannot read the rule set, so the profile has no window and the sales are read over the chosen one only.
  await expect(text(page, 'profile-window')).toHaveText('unknown');
  await expect(page.getByTestId('exploration-profile').getByTestId('refusal-rule-set')).toContainText(
    'Your role may not read the rule set',
  );
  await expect(text(page, 'window-compare')).toContainText('Not available');
  expect(urls.some((u) => u.includes('/rfm-rule-sets/'))).toBe(false);
  const sales = urls.find((u) => u.includes('/api/sales/v1/sales?')) ?? '';
  expect(sales).toContain('from=2026-10-01T00%3A00%3A00Z');
  await app.close();
});

test('returns that cannot be read leave the sales as charged, and the screen says so', async () => {
  const { app, page } = await launch('ANALYST');
  behaviour = { override: { '/sale-reversals': { status: 404, body: '' } } };
  await openModule(page);
  await explore(page, CUSTOMER);
  await expect(text(page, 'figures-charged')).toContainText('counted as charged');
  await expect(page.getByTestId('exploration-figures').getByTestId('error-code')).toHaveText(
    'contract_defect',
  );
  // Both sales count, as charged: 203.25 and 4.25.
  await expect(text(page, 'figure-chosen-frequency')).toHaveText('2');
  await expect(text(page, 'figure-chosen-spend')).toHaveText('207.50');
  await expect(text(page, 'figures-net')).toHaveCount(0);
  await app.close();
});

test('the module follows the role, and the main process refuses a forced call before any service is asked', async () => {
  let { app, page } = await launch('INVENTORY_PLANNER');
  await expect(nav(page, 'exploration')).toHaveCount(0);
  const before = urls.length;
  const forced = await page.evaluate(async (id) => {
    const query = { customer_id: id, from: '2026-10-01T00:00:00Z', to: '2026-10-27T00:00:00Z' };
    return [
      await window.mosaiq.exploreProfile(id),
      await window.mosaiq.exploreSales(query),
      await window.mosaiq.exploreRestrictions(id),
      await window.mosaiq.exploreConsent(id),
      await window.mosaiq.exploreInterests(id),
    ];
  }, CUSTOMER);
  for (const r of forced) expect(r).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(urls.slice(before)).toEqual([]);
  await app.close();

  ({ app, page } = await launch('ANALYST'));
  await expect(nav(page, 'exploration')).toHaveCount(1);
  const bad = await page.evaluate(() =>
    window.mosaiq.exploreSales({ customer_id: 'x/y', from: 'a', to: 'b' }),
  );
  expect(bad).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
  await app.close();
});

test('a window that cannot be read is refused before anything is asked', async () => {
  const { app, page } = await launch('ANALYST');
  await openModule(page);
  await page.fill('#x-customer', CUSTOMER);
  await page.fill('#x-from', '2026-10-26');
  await page.fill('#x-to', '2026-10-01');
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await expect(text(page, 'exploration-problem')).toHaveText('The last day cannot come before the first.');
  await page.fill('#x-to', '');
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await expect(text(page, 'exploration-problem')).toHaveText(
    'Choose the first and the last day of the sales window.',
  );
  await page.fill('#x-customer', '');
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await expect(text(page, 'exploration-problem')).toHaveText('Choose a customer, or enter a customer id.');
  expect(urls.filter((u) => u.includes('/sales'))).toEqual([]);
  await app.close();
});

test('a slow answer for the customer opened before is dropped', async () => {
  const { app, page } = await launch('ANALYST');
  hold = { customerId: CUSTOMER, gate: deferred<void>() };
  await openModule(page);
  await page.fill('#x-customer', CUSTOMER);
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  // The second customer is opened while the first one's profile is still on its way.
  await page.fill('#x-customer', PENDING);
  await page.getByRole('button', { name: 'Explore', exact: true }).click();
  await expect(text(page, 'profile-pending')).toBeVisible();
  await expect(page.getByTestId('exploration-customer')).toContainText('Pending Pat');
  hold.gate.resolve();
  await expect.poll(() => urls.filter((u) => u.endsWith(`/rfm-profiles/${CUSTOMER}`)).length).toBe(1);
  await page.waitForTimeout(300);
  // The late profile paints nothing, and its sales are never asked for.
  await expect(text(page, 'profile-pending')).toBeVisible();
  await expect(text(page, 'profile-state')).toHaveCount(0);
  await expect(page.getByTestId('exploration-customer')).toContainText('Pending Pat');
  expect(urls.filter((u) => u.includes('/sales?') && u.includes(`customer_id=${CUSTOMER}`))).toEqual([]);
  await app.close();
});

test('text from the services reaches the screen as text, under the CSP, with no inline style', async () => {
  hostile = true;
  const { app, page } = await launch('ADMIN');
  const violations: string[] = [];
  page.on('console', (m) => {
    if (/content security policy|refused to/i.test(m.text())) violations.push(m.text());
  });
  await openModule(page);
  await explore(page, CUSTOMER);
  await expect(page.getByTestId('exploration-customer')).toContainText(HOSTILE);
  await expect(page.getByTestId('exploration-channels')).toContainText(HOSTILE);
  await expect(page.getByTestId('exploration-categories')).toContainText(HOSTILE);
  expect(await page.evaluate(() => 'xss' in window)).toBe(false);
  await expect(page.locator('.exploration img')).toHaveCount(0);
  expect(await page.locator('.exploration [style]').count()).toBe(0);
  expect(violations).toEqual([]);
  await app.close();
});

test('the screen fits a narrow window and a wide one, and its tables scroll in their own area', async () => {
  const { app, page } = await launch('ADMIN');
  await openModule(page);
  await explore(page, CUSTOMER);
  await expect(page.getByTestId('exploration-consent')).toContainText('Purpose');
  for (const [width, height] of [
    [375, 900],
    [1440, 900],
  ] as const) {
    await app.evaluate(
      ({ BrowserWindow }, size) => {
        BrowserWindow.getAllWindows()[0]?.setSize(size[0], size[1]);
      },
      [width, height],
    );
    await expect.poll(async () => page.evaluate(() => window.innerWidth)).toBeLessThanOrEqual(width);
    const fits = await page.evaluate(() => {
      const wide = window.innerWidth + 1;
      const offenders = Array.from(document.querySelectorAll('.exploration *'))
        .filter((e) => e.closest('.scroll') === null || e.classList.contains('scroll'))
        .filter((e) => e.getBoundingClientRect().right > wide)
        .map((e) => e.tagName.toLowerCase() + (e.getAttribute('data-testid') ?? ''));
      return {
        page: document.documentElement.scrollWidth <= wide,
        tables: document.querySelectorAll('.exploration .scroll table').length,
        offenders,
      };
    });
    expect(fits.page).toBe(true);
    expect(fits.offenders).toEqual([]);
    // Figures, categories, channels, purchases, consent, inferred interests.
    expect(fits.tables).toBeGreaterThanOrEqual(6);
    await page.getByTestId('exploration-figures').scrollIntoViewIfNeeded();
    await shot(page, `872-desktop-exploration-05-${width}px`);
  }
  await app.close();
});

test('products that break their contract fail the sales panels, and nothing of them is drawn', async () => {
  const { app, page } = await launch('ANALYST');
  behaviour = { override: { '/products': { status: 200, body: '<nope/>' } } };
  await openModule(page);
  await explore(page, CUSTOMER);
  for (const panel of ['figures', 'categories', 'channels', 'purchases'])
    await expect(page.getByTestId(`exploration-${panel}`).getByTestId('error-code')).toHaveText(
      'contract_defect',
    );
  await expect(page.locator('[data-testid=categories-table]')).toHaveCount(0);
  await expect(page.locator('[data-testid=purchases-table]')).toHaveCount(0);
  // The panels of the other services are still drawn.
  await expect(text(page, 'profile-state')).toHaveText('scored');
  await app.close();
});

test('the categories listed carry the unreadable products under no category', async () => {
  const { app, page } = await launch('ANALYST');
  behaviour = { override: { '/products': { status: 403, body: example('rfm-engine', 'forbidden-role') } } };
  await openModule(page);
  await explore(page, CUSTOMER);
  await expect(text(page, 'categories-unknown')).toContainText('3 products could not be read');
  await expect(page.locator('[data-testid=categories-table] tbody tr[data-id="none"] td').first()).toHaveText(
    'No category known',
  );
  await app.close();
});
