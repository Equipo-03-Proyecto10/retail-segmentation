import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';
import {
  CATEGORIES,
  CHANNELS_LIST,
  CUSTOMER,
  DECISION,
  categoriesPage,
  channelsPage,
  customersPage,
  productsPage,
  STORES,
  storesPage,
} from '../support/recommendations';

// The recommendations inspection (F28-08, #877), against a fake gateway that answers with the
// recorded v2 examples of recommendations-service (published ahead of the service, ADR-0073) and
// pages of the services that own names.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts', 'recommendations', 'examples');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const v2 = (name: string): string => readFileSync(join(CONTRACTS, 'v2', `${name}.xml`), 'utf8');
const shared = (name: string): string => readFileSync(join(CONTRACTS, `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

/** Markup in a product name must reach the screen as text and run nothing. */
const HOSTILE = '<img src=x onerror="window.__pwned=1">Hostile milk';
const OTHER_CUSTOMER = '8d2f6a41-0b97-4c53-a1e8-7f3c5b9d2e60';

let server: Server;
let base = '';
let role = 'ANALYST';
let urls: string[] = [];

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    req.on('data', () => undefined);
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      urls.push(url.toString());
      const q = url.searchParams;
      if (req.method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return req.method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (path === '/api/recommendations/v2/recommendations') {
        if (role === 'STORE_MANAGER' && q.get('store_id') === '3')
          return reply(403, shared('forbidden-store'));
        if (q.get('channel_id') === '2') return reply(422, shared('unknown-channel'));
        if (q.get('customer_id') === OTHER_CUSTOMER) return reply(200, v2('personalization-withdrawn'));
        return reply(200, v2('recommended-with-alternatives'));
      }
      if (path === '/api/recommendations/v2/recommendation-decisions') {
        if (q.get('status') === 'none_match') return reply(200, v2('decisions-none'));
        return reply(200, v2('decisions-page'));
      }
      if (path === `/api/recommendations/v2/recommendation-decisions/${DECISION}`)
        return reply(200, v2('decision'));
      if (path.startsWith('/api/recommendations/v2/recommendation-decisions/'))
        return reply(404, shared('decision-not-found'));
      if (path === '/api/products/v1/products')
        return reply(
          200,
          productsPage((q.get('ids') ?? '').split(',').map(Number), (id) =>
            id === 120 ? HOSTILE : `Item ${id}`,
          ),
        );
      if (path === '/api/categories/v1/categories') return reply(200, categoriesPage(CATEGORIES));
      if (path === '/api/stores/v1/stores') return reply(200, storesPage(STORES));
      if (path === '/api/sales/v1/channels') return reply(200, channelsPage(CHANNELS_LIST));
      if (path === '/api/customers/v1/customers')
        return reply(200, customersPage([[CUSTOMER, 'Ada Analyst', 'ada@example.com']]));
      reply(404);
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(() => {
  urls = [];
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

const nav = (page: Page): Promise<(string | undefined)[]> =>
  page.locator('nav.nav button').evaluateAll((bs) => bs.map((b) => (b as HTMLElement).dataset.view));
const open = (page: Page): Promise<void> =>
  page.locator('nav.nav button[data-view="recommendations"]').click();

/** Chooses the customer through the finder, then the store and the channel, and inspects. */
async function inspect(page: Page, store = '5', channel = '1'): Promise<void> {
  await page.fill('#rf-q', 'ada');
  await page.getByRole('button', { name: 'Find customer' }).click();
  await page.getByTestId(`pick-customer-${CUSTOMER}`).click();
  await expect(page.locator('#rc-customer_id')).toHaveValue(CUSTOMER);
  await page.selectOption('#rc-store_id', store);
  await page.selectOption('#rc-channel_id', channel);
  await page.getByTestId('inspect').click();
}

test('an inspection shows each item with its factors, availability and reasons, and marks alternatives', async () => {
  const { app, page } = await launch('ANALYST');
  await open(page);
  await inspect(page);

  const rows = page.locator('[data-testid=answer-items] tbody tr');
  await expect(rows).toHaveCount(4);
  const asked = urls.find((u) => u.includes('/v2/recommendations'))!;
  const params = new URL(asked).searchParams;
  expect([params.get('customer_id'), params.get('store_id'), params.get('channel_id')]).toEqual([
    CUSTOMER,
    '5',
    '1',
  ]);

  // A recommended item: its factors, its availability, its reasons.
  const primary = page.locator('tr[data-product-id="331"]');
  await expect(primary).toHaveAttribute('data-kind', 'primary');
  await expect(primary).toContainText('Recommended');
  await expect(primary).toContainText('1 of 3 signals matched; category share 0.4 (40%)');
  await expect(primary).toContainText('4 on hand at Polanco (#5) on mobile_app (#1)');
  await expect(primary).toContainText("Purchase history: Yogurt (#12) is 0.4 (40%) of the customer's buying");

  // The alternative is marked as one, names the product it replaces and why that is unavailable.
  const alternative = page.locator('tr[data-product-id="246"]');
  await expect(alternative).toHaveAttribute('data-kind', 'alternative');
  await expect(alternative).toContainText(
    'Alternative for Item 245 (#245), which is unavailable here (no unit is on hand)',
  );
  await expect(alternative).toContainText('Not scored');
  await expect(alternative).toContainText('11 on hand at Polanco (#5)');
  await expect(alternative).toContainText('Declared substitute');

  // A product held by another store is marked as another store, not as stock here.
  const elsewhere = page.locator('tr[data-product-id="88"]');
  await expect(elsewhere).toHaveAttribute('data-kind', 'elsewhere');
  await expect(elsewhere).toContainText('Another store: unavailable at Polanco (#5)');
  await expect(elsewhere).toContainText('6 on hand at Coyoacan (#3) on mobile_app (#1), another store');
  await expect(elsewhere).toContainText(
    '2 of 3 signals matched; 5 segment buyers; category share 0.25 (25%)',
  );

  // What was excluded, and why.
  const exclusions = page.getByTestId('answer-exclusions');
  await expect(exclusions).toContainText('2 products: The customer bought it in the window.');
  await expect(exclusions).toContainText('1 product: Its commercial status does not allow the sale');
  await expect(exclusions).toContainText('2 products: No unit is on hand at the reference store.');
  await expect(page.getByTestId('answer-store')).toHaveText('Polanco (#5), named by the caller');
  await shot(page, '877-desktop-recommendations-01-inspection');
  await app.close();
});

test('markup in a name from another service is shown as text and runs nothing', async () => {
  const { app, page } = await launch('ANALYST');
  await open(page);
  await inspect(page);
  const row = page.locator('tr[data-product-id="120"]');
  // The parser leaves entities undecoded (processEntities: false), so the markup arrives as inert text.
  await expect(row).toContainText('Hostile milk');
  await expect(row).toContainText('onerror');
  await expect(page.locator('[data-testid=answer-items] img')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  await app.close();
});

test('a stored decision opens showing what was offered, what was excluded and why', async () => {
  const { app, page } = await launch('ANALYST');
  await open(page);
  const list = page.locator('[data-testid=decisions-table] tbody tr');
  await expect(list).toHaveCount(3);
  await expect(list.nth(2)).toContainText('Anonymized');
  await expect(list.nth(0)).toContainText('Polanco (#5), named by the caller');
  await expect(list.nth(1)).toContainText("Coyoacan (#3), the customer's usual store");

  await page.getByTestId(`open-decision-${DECISION}`).click();
  const items = page.locator('[data-testid=decision-items] tbody tr');
  await expect(items).toHaveCount(4);
  await expect(page.getByTestId('decision-decision-id')).toHaveText(DECISION);
  await expect(page.locator('[data-testid=decision-items] tr[data-kind=alternative]')).toContainText(
    'Alternative for Item 245 (#245)',
  );
  await expect(page.locator('[data-testid=decision-items] tr[data-kind=elsewhere]')).toContainText(
    'Another store',
  );
  await expect(page.locator('[data-testid=decision-items] tr[data-product-id="120"]')).toContainText(
    'Segment: 9 other customers with the same label bought it in the window',
  );
  const exclusions = page.getByTestId('decision-exclusions');
  await expect(exclusions.locator('dd')).toHaveCount(3);
  await expect(exclusions.locator('dd[data-reason=out_of_stock]')).toContainText('2 products');
  await expect(page.getByTestId('decision-customer')).toHaveText(CUSTOMER);
  expect(urls.some((u) => u.endsWith(`/recommendation-decisions/${DECISION}`))).toBe(true);
  await shot(page, '877-desktop-recommendations-02-decision');

  // A decision id that is not held is shown as the service refused it.
  await page.fill('#ri-decision_id', '00000000-0000-4000-8000-000000000000');
  await page.getByRole('button', { name: 'Open decision' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('decision_not_found');
  await app.close();
});

test('the decision behind an inspection opens from it, and filters narrow the list', async () => {
  const { app, page } = await launch('AUDITOR');
  await open(page);
  await inspect(page);
  await page.getByTestId('open-stored-decision').click();
  await expect(page.locator('[data-testid=decision-items] tbody tr')).toHaveCount(4);

  await page.selectOption('#rd-status', 'none_match');
  await page.fill('#rd-from', '2026-10-01');
  await page.fill('#rd-to', '2026-10-31');
  await page.getByRole('button', { name: 'Search decisions' }).click();
  await expect(page.getByTestId('decisions-empty')).toHaveText('No decision matches.');
  const filtered = urls.filter((u) => u.includes('/recommendation-decisions?')).at(-1)!;
  const params = new URL(filtered).searchParams;
  expect(params.get('status')).toBe('none_match');
  expect(params.get('from')).toBe('2026-10-01T00:00:00Z');
  expect(params.get('to')).toBe('2026-11-01T00:00:00Z');
  await app.close();
});

test('a status without items says why nothing was offered', async () => {
  const { app, page } = await launch('ANALYST');
  await open(page);
  await page.fill('#rc-customer_id', OTHER_CUSTOMER);
  await page.selectOption('#rc-store_id', '5');
  await page.getByTestId('inspect').click();
  await expect(page.getByTestId('answer-status')).toContainText('Personalization withdrawn');
  await expect(page.getByTestId('answer-status')).toContainText('Nothing about them was read');
  await expect(page.getByTestId('answer-empty')).toHaveText('No product was offered.');
  await expect(page.getByTestId('answer-items')).toHaveCount(0);
  await app.close();
});

test('a refusal is shown as returned, with its field named next to the control', async () => {
  const { app, page } = await launch('ANALYST');
  await open(page);
  await inspect(page, '5', '2');
  await expect(page.getByTestId('error-code')).toHaveText('unknown_channel');
  await expect(page.getByTestId('detail-channel_id')).toHaveText('channel_id: unknown');
  await app.close();

  const manager = await launch('STORE_MANAGER');
  await open(manager.page);
  await inspect(manager.page, '3', '1');
  await expect(manager.page.getByTestId('error-code')).toHaveText('forbidden_store');
  await expect(manager.page.getByTestId('detail-store_id')).toHaveText('store_id: not_assigned');
  await manager.app.close();
});

test('decisions are narrower than recommendations: a role that may inspect but not read them sees none', async () => {
  const { app, page } = await launch('MARKETING');
  expect(await nav(page)).toContain('recommendations');
  await open(page);
  await expect(page.getByTestId('inspect-form')).toBeVisible();
  await expect(page.getByTestId('decisions-unavailable')).toBeVisible();
  await expect(page.getByTestId('decision-filters')).toHaveCount(0);
  await expect(page.getByTestId('decisions-table')).toHaveCount(0);
  expect(urls.some((u) => u.includes('/recommendation-decisions'))).toBe(false);
  const forced = await page.evaluate((id) => window.mosaiq.openRecommendationDecision(id), DECISION);
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  const listed = await page.evaluate(() => window.mosaiq.listRecommendationDecisions({}));
  expect(listed).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(urls.some((u) => u.includes('/recommendation-decisions'))).toBe(false);
  await app.close();
});

test('a role without the operation has no entry and a forced call is refused before it is sent', async () => {
  const { app, page } = await launch('INVENTORY_PLANNER');
  expect(await nav(page)).not.toContain('recommendations');
  const forced = await page.evaluate(
    (id) => window.mosaiq.inspectRecommendation({ customer_id: id }),
    CUSTOMER,
  );
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(urls.some((u) => u.includes('/v2/recommendations'))).toBe(false);
  await app.close();
});

test('the main process refuses a value it cannot send', async () => {
  const { app, page } = await launch('ANALYST');
  const bad = await page.evaluate(() => window.mosaiq.inspectRecommendation({ customer_id: 'x' } as never));
  expect(bad).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
  const badStore = await page.evaluate(
    (id) => window.mosaiq.inspectRecommendation({ customer_id: id, store_id: 0 }),
    CUSTOMER,
  );
  expect(badStore).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
  expect(urls.some((u) => u.includes('/v2/recommendations'))).toBe(false);
  await app.close();
});
