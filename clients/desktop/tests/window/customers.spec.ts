import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { deferred, type Deferred } from '../support/deferred';

// Customers, both preference sets, the segment and the recommendations (F16-03, #414),
// against a fake gateway that answers with the recorded examples of the customers,
// customer-preferences, sales, categories, segments, recommendations and products contracts.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const NS = 'https://mosaiq.maxthecoder.online/xml';
const COMMON = `${NS}/common/v1`;
const AUTH = `${NS}/auth/v1`;

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

// The id the examples are recorded with. Each customer below gets its own copy.
const EXAMPLE_ID = '7b3ad781-c5de-4f40-ae50-1fa825ce2b25';
const WITH_RECOMMENDATIONS = EXAMPLE_ID;
const NONE_MATCH = '0c9f6a3e-5d2b-4c8e-9a41-7e3b2d1f8a66';
const UNASSIGNED = '5d1e7c20-4b8a-4f3e-9c61-2a9d8e7f6b10';
const NO_OPEN = '9a8b7c6d-1e2f-4a3b-8c4d-5e6f7a8b9c0d';

interface Customer {
  id: string;
  name: string;
  email: string;
  phone: string;
  channel: number;
  channels: number[];
  categories: number[];
}
const initial = (): Customer[] => [
  {
    id: WITH_RECOMMENDATIONS,
    name: 'Jordan Lee',
    email: 'jordan@example.com',
    phone: '+52-55-5555-0101',
    channel: 3,
    channels: [1, 3],
    categories: [11, 12],
  },
  {
    id: NONE_MATCH,
    name: 'Ana Torres',
    email: 'ana@example.com',
    phone: '',
    channel: 1,
    channels: [],
    categories: [],
  },
  {
    id: UNASSIGNED,
    name: 'Unassigned Una',
    email: '',
    phone: '',
    channel: 2,
    channels: [2],
    categories: [12],
  },
  {
    id: NO_OPEN,
    name: 'No Open Nora',
    email: 'nora@example.com',
    phone: '',
    channel: 5,
    channels: [],
    categories: [11],
  },
];

/** The fake answers with values a test sent (a PATCH, a PUT, a query), so every one is escaped as XML text. */
const xmlText = (value: string | number): string =>
  String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');

const customerBody = (c: Customer): string =>
  `<customer_id>${xmlText(c.id)}</customer_id><name>${xmlText(c.name)}</name>${
    c.email ? `<email>${xmlText(c.email)}</email>` : ''
  }${c.phone ? `<phone>${xmlText(c.phone)}</phone>` : ''}<registration_channel_id>${xmlText(
    c.channel,
  )}</registration_channel_id><registered_on>2025-09-17</registered_on>`;
const oneCustomer = (c: Customer): string =>
  `<Customer xmlns="${NS}/customers/v1">${customerBody(c)}</Customer>`;
const directory = (list: Customer[]): string =>
  `<Customers xmlns="${NS}/customers/v1" xmlns:common="${COMMON}">${list
    .map((c) => `<Customer>${customerBody(c)}</Customer>`)
    .join(
      '',
    )}<common:PageInfo><common:page>1</common:page><common:size>20</common:size><common:total>${list.length}</common:total></common:PageInfo></Customers>`;
const profile = (c: Customer): string =>
  `<PreferenceProfile xmlns="${NS}/customer-preferences/v1"><customer_id>${xmlText(c.id)}</customer_id>${c.channels
    .map((n) => `<preferred_channel_ids>${xmlText(n)}</preferred_channel_ids>`)
    .join(
      '',
    )}${c.categories.map((n) => `<interest_category_ids>${xmlText(n)}</interest_category_ids>`).join('')}</PreferenceProfile>`;
const productsFor = (ids: number[]): string =>
  `<Products xmlns="${NS}/products/v1" xmlns:common="${COMMON}">${ids
    .map(
      (id) =>
        `<Product><product_id>${xmlText(id)}</product_id><sku>SKU-${xmlText(id)}</sku><name>Product name ${xmlText(id)}</name><category_id>12</category_id><list_price>10.00</list_price><is_active>true</is_active></Product>`,
    )
    .join(
      '',
    )}<common:PageInfo><common:page>1</common:page><common:size>50</common:size><common:total>${ids.length}</common:total></common:PageInfo></Products>`;

const CURRENT: Record<string, string> = {
  [WITH_RECOMMENDATIONS]: 'current-assigned',
  [NONE_MATCH]: 'current-assigned',
  [UNASSIGNED]: 'current-unassigned-result',
  [NO_OPEN]: 'current-no-open-assignment',
};
const RECOMMENDATION: Record<string, string> = {
  [WITH_RECOMMENDATIONS]: 'recommended',
  [NONE_MATCH]: 'none-match',
  [UNASSIGNED]: 'unassigned-result',
  [NO_OPEN]: 'no-open-assignment',
};

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'ADMIN';
let seen: Seen[] = [];
let db: Customer[] = initial();
let refusal: { service: string; file: string; status: number } | null = null;
/** Requests for this customer wait until the test releases them (a slow answer for an earlier customer). */
let hold: { customerId: string; gate: Deferred<void> } | null = null;

function reset(): void {
  seen = [];
  db = initial();
  refusal = null;
  hold = null;
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', async () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      seen.push({ method, url: url.toString(), body });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      const service = /^\/api\/([^/]+)\//.exec(path)?.[1] ?? '';
      const forId = (url.searchParams.get('customer_id') ?? path.split('/').pop() ?? '').replace(/\/.*/, '');
      if (hold !== null && (path.includes(hold.customerId) || forId === hold.customerId))
        await hold.gate.promise;
      if (method !== 'GET' && refusal !== null && refusal.service === service) {
        const r = refusal;
        refusal = null;
        return reply(r.status, example(r.service, r.file));
      }
      const mine = (id: string): Customer | undefined => db.find((c) => c.id === id);
      const withId = (xml: string, id: string): string => xml.replaceAll(EXAMPLE_ID, xmlText(id));

      if (path === '/api/sales/v1/channels') return reply(200, example('sales', 'list-channels'));
      if (path === '/api/categories/v1/categories')
        return reply(200, example('categories', 'list-categories'));
      if (path === '/api/products/v1/products') {
        const ids = (url.searchParams.get('ids') ?? '').split(',').filter(Boolean).map(Number);
        return reply(200, productsFor(ids));
      }
      if (path === '/api/customers/v1/customers') return reply(200, directory(db));
      let m = /^\/api\/customers\/v1\/customers\/([^/]+)$/.exec(path);
      if (m) {
        const c = mine(m[1] as string);
        if (!c) return reply(404, example('customers', 'customer-not-found'));
        if (method === 'PATCH') {
          const field = (name: string): string | undefined =>
            new RegExp(`<${name}>([^<]*)</${name}>`).exec(body)?.[1];
          if (field('email') === 'taken@example.com') return reply(409, example('customers', 'email-taken'));
          if (field('registration_channel_id') === '99')
            return reply(422, example('customers', 'unknown-channel'));
          c.name = field('name') ?? c.name;
          c.email = field('email') ?? c.email;
          c.phone = field('phone') ?? c.phone;
          c.channel = Number(field('registration_channel_id') ?? c.channel);
        }
        return reply(200, oneCustomer(c));
      }
      m =
        /^\/api\/customer-preferences\/v1\/preference-profiles\/([^/]+)(?:\/(preferred-channels|interest-categories))?$/.exec(
          path,
        );
      if (m) {
        const c = mine(m[1] as string);
        if (!c) return reply(404, example('customer-preferences', 'customer-not-found'));
        if (method === 'PUT') {
          const ids = [...body.matchAll(/<(?:channel_ids|category_ids)>(\d+)</g)].map((x) => Number(x[1]));
          if (m[2] === 'preferred-channels') c.channels = ids;
          else c.categories = ids;
        }
        return reply(200, profile(c));
      }
      if (path === '/api/segments/v1/segments') return reply(200, example('segments', 'list-segments'));
      if (path === '/api/segments/v1/segment-assignments/current') {
        const id = url.searchParams.get('customer_id') ?? '';
        return reply(200, withId(example('segments', CURRENT[id] ?? 'current-assigned'), id));
      }
      if (path === '/api/segments/v1/segment-assignments') {
        if (url.searchParams.get('label_code')) return reply(200, example('segments', 'label-open-members'));
        return reply(
          200,
          withId(example('segments', 'customer-history'), url.searchParams.get('customer_id') ?? ''),
        );
      }
      if (path === '/api/recommendations/v1/recommendations') {
        const id = url.searchParams.get('customer_id') ?? '';
        const xml = example('recommendations', RECOMMENDATION[id] ?? 'recommended');
        return reply(200, xml.replace(/<customer_id>[^<]*</, `<customer_id>${xmlText(id)}<`));
      }
      return reply(404, example('customers', 'customer-not-found'));
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

/** Opens the screen and one customer, and waits for every panel before anything is touched. */
async function view(page: Page, customerId: string): Promise<void> {
  await nav(page, 'customers').click();
  await expect(page.locator('[data-testid=customers-table] tbody tr')).toHaveCount(4);
  await page.getByTestId(`view-customer-${customerId}`).click();
  await expect(page.getByTestId('customer-id')).toHaveText(customerId);
  await expect(page.getByTestId('pref-channels')).toBeVisible();
  await expect(page.getByTestId('segment-state')).toBeVisible();
  await expect(page.getByTestId('reco-status')).toBeVisible();
}

const writes = (): Seen[] => seen.filter((s) => s.method !== 'GET' && !s.url.includes('/auth/'));

test('searches, opens a customer and shows the four panels with names composed from their owners', async () => {
  const { app, page } = await launch('ANALYST');
  const requested: string[] = [];
  page.on('request', (r) => requested.push(r.url()));
  await nav(page, 'customers').click();
  await expect(page.locator('[data-testid=customers-table] tbody tr')).toHaveCount(4);
  await page.fill('#filter-q', 'Jordan');
  await page.fill('#filter-email', 'jordan@example.com');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect
    .poll(() =>
      seen.some(
        (s) =>
          s.url.includes('/customers/v1/customers?') && s.url.includes('q=Jordan&email=jordan%40example.com'),
      ),
    )
    .toBe(true);

  await page.getByTestId(`view-customer-${WITH_RECOMMENDATIONS}`).click();
  await expect(page.getByTestId('customer-name')).toHaveText('Jordan Lee');
  await expect(page.getByTestId('customer-channel')).toHaveText('physical_store');
  await expect(page.getByTestId('pref-channels')).toHaveText('mobile_app, physical_store');
  await expect(page.getByTestId('pref-categories')).toHaveText('Cheese, Yogurt');
  await expect(page.getByTestId('segment-state')).toHaveText('assigned');
  await expect(page.getByTestId('segment-label')).toHaveText('Loyal (LOYAL)');
  await expect(page.getByTestId('segment-scores')).toHaveText('R 4 · F 5 · M 4');
  await expect(page.getByTestId('segment-since')).toHaveText('2026-10-12T03:00:00Z');
  await expect(page.getByTestId('segment-run')).toHaveText('42');

  await expect(page.getByTestId('reco-status')).toHaveText('recommended');
  await expect(page.locator('[data-testid=reco-table] tbody tr')).toHaveCount(2);
  const first = page.locator('[data-testid=reco-table] tbody tr[data-product-id="245"]');
  await expect(first.locator('td').nth(0)).toHaveText('Product name 245');
  await expect(first.locator('td').nth(2)).toHaveText('8');
  await expect(page.getByTestId('reco-reasons-245')).toHaveText(
    'segment, 17 segment buyers; preferred_category, nearest category Category 10 (unavailable); purchase_history, nearest category Yogurt, share 0.4',
  );
  // One products call for the whole list.
  expect(seen.filter((s) => s.url.includes('/products/v1/products?ids='))).toHaveLength(1);

  // Read-only for this role: no form, and a forced write is refused by the service.
  await expect(page.getByTestId('customer-form')).toHaveCount(0);
  await expect(page.getByTestId('channels-form')).toHaveCount(0);
  await expect(page.getByTestId('categories-form')).toHaveCount(0);
  expect(requested.filter((u) => !u.startsWith('file:'))).toEqual([]);
  await app.close();
});

test('the none_match customer shows the reason returned, with no items', async () => {
  const { app, page } = await launch('MARKETING');
  await view(page, NONE_MATCH);
  await expect(page.getByTestId('reco-status')).toHaveText('none_match');
  await expect(page.getByTestId('reco-table')).toHaveCount(0);
  await expect(page.getByTestId('pref-channels')).toHaveText('none chosen');
  expect(seen.filter((s) => s.url.includes('/products/v1/products?ids='))).toHaveLength(0);
  await app.close();
});

test('unassigned_result is shown as returned: no label, no scores, and nothing filled in', async () => {
  const { app, page } = await launch('AUDITOR');
  await view(page, UNASSIGNED);
  await expect(page.getByTestId('segment-state')).toHaveText('unassigned_result');
  await expect(page.getByTestId('segment-since')).toHaveText('2026-10-12T03:00:00Z');
  await expect(page.getByTestId('segment-run')).toHaveText('42');
  await expect(page.getByTestId('segment-label')).toHaveCount(0);
  await expect(page.getByTestId('segment-scores')).toHaveCount(0);
  await expect(page.getByTestId('reco-status')).toHaveText('unassigned_result');
  await expect(page.getByTestId('reco-label')).toHaveCount(0);
  await expect(page.getByTestId('reco-table')).toHaveCount(0);
  await app.close();
});

test('no_open_assignment is shown as returned, in the segment and the recommendations', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  await view(page, NO_OPEN);
  await expect(page.getByTestId('segment-state')).toHaveText('no_open_assignment');
  await expect(page.locator('[data-testid=segment-panel] dd')).toHaveCount(1);
  await expect(page.getByTestId('reco-status')).toHaveText('no_open_assignment');
  await expect(page.getByTestId('reco-table')).toHaveCount(0);
  await app.close();
});

test('the history pages through the customer assignments, and a label view lists its members in business order', async () => {
  const { app, page } = await launch('ANALYST');
  await view(page, WITH_RECOMMENDATIONS);
  await page.getByTestId('show-history').click();
  await expect(page.locator('[data-testid=history-table] tbody tr')).toHaveCount(3);
  await expect(page.locator('[data-testid=history-table] tbody tr').nth(1)).toContainText('none');
  await expect(page.locator('[data-testid=history-table] tbody tr').nth(0)).toContainText('open');
  await expect(page.locator('[data-testid=history-table] tbody tr').nth(2)).toContainText(
    'At risk (AT_RISK)',
  );
  await expect(page.getByTestId('history-page')).toHaveText('Page 1 of 1 · 3 history');
  expect(seen.some((s) => s.url.includes(`segment-assignments?customer_id=${WITH_RECOMMENDATIONS}`))).toBe(
    true,
  );

  const labels = page.locator('[data-testid=labels-panel] ol button');
  await expect(labels).toHaveText([
    'Champion (CHAMPION) · 18',
    'Loyal (LOYAL) · 31',
    'Potential (POTENTIAL) · 22',
    'At risk (AT_RISK) · 17',
    'Hibernating (HIBERNATING) · 24',
    'Lost (LOST) · 9',
  ]);
  await expect(page.getByTestId('labels-unassigned')).toHaveText('14 customers unassigned in the latest run');
  await page.getByTestId('label-LOYAL').click();
  await expect(page.locator('[data-testid=members-table] tbody tr')).toHaveCount(2);
  expect(seen.some((s) => s.url.includes('segment-assignments?label_code=LOYAL&open=true'))).toBe(true);
  await page.getByTestId('members-open-only').uncheck();
  await expect
    .poll(() => seen.some((s) => s.url.includes('segment-assignments?label_code=LOYAL&page=1')))
    .toBe(true);
  await app.close();
});

test('switching customers quickly never shows another customer in a panel', async () => {
  const { app, page } = await launch('ADMIN');
  await nav(page, 'customers').click();
  await expect(page.locator('[data-testid=customers-table] tbody tr')).toHaveCount(4);
  // The first customer's answers arrive last.
  hold = { customerId: WITH_RECOMMENDATIONS, gate: deferred<void>() };
  const gate = hold.gate;
  await page.getByTestId(`view-customer-${WITH_RECOMMENDATIONS}`).click();
  await expect
    .poll(() => seen.filter((s) => s.url.includes(WITH_RECOMMENDATIONS)).length)
    .toBeGreaterThanOrEqual(4);
  await page.getByTestId(`view-customer-${UNASSIGNED}`).click();
  await expect(page.getByTestId('customer-id')).toHaveText(UNASSIGNED);
  await expect(page.getByTestId('segment-state')).toHaveText('unassigned_result');
  await expect(page.getByTestId('reco-status')).toHaveText('unassigned_result');
  await expect(page.getByTestId('pref-channels')).toHaveText('web');
  gate.resolve();
  // Give the late answers every chance to land.
  await page.waitForTimeout(500);
  await expect(page.getByTestId('customer-id')).toHaveText(UNASSIGNED);
  await expect(page.getByTestId('segment-state')).toHaveText('unassigned_result');
  await expect(page.getByTestId('reco-status')).toHaveText('unassigned_result');
  await expect(page.getByTestId('pref-channels')).toHaveText('web');
  await expect(page.getByTestId('customer-form')).toHaveCount(1);
  await expect(page.getByTestId('customer-name')).toHaveText('Unassigned Una');
  await app.close();
});

test('an ADMIN sends the whole edited set, and an empty list clears it', async () => {
  const { app, page } = await launch('ADMIN');
  await view(page, WITH_RECOMMENDATIONS);
  // mobile_app and physical_store are chosen; add call_center and drop mobile_app.
  await expect(page.getByTestId('channel-option-1')).toBeChecked();
  await page.getByTestId('channel-option-5').check();
  await page.getByTestId('channel-option-1').uncheck();
  await page.locator('[data-testid=channels-form] button[type=submit]').click();
  await expect(page.getByTestId('notice')).toHaveText('Preferred channels saved.');
  await expect(page.getByTestId('pref-channels')).toHaveText('physical_store, call_center');
  const put = writes().at(-1)!;
  expect(put.method).toBe('PUT');
  expect(put.url).toContain(`/preference-profiles/${WITH_RECOMMENDATIONS}/preferred-channels`);
  expect(put.body).toContain('<channel_ids>3</channel_ids><channel_ids>5</channel_ids>');
  expect(put.body).not.toContain('<channel_ids>1<');

  // Clear the categories: every box unchecked sends an empty body, not a partial list.
  await page.getByTestId('category-option-11').uncheck();
  await page.getByTestId('category-option-12').uncheck();
  await page.locator('[data-testid=categories-form] button[type=submit]').click();
  await expect(page.getByTestId('pref-categories')).toHaveText('none chosen');
  const cleared = writes().at(-1)!;
  expect(cleared.url).toContain('/interest-categories');
  expect(cleared.body).not.toContain('category_ids');
  expect(db[0]!.categories).toEqual([]);
  await app.close();
});

test('unknown_channel and unknown_category from the preference service are shown as returned', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  await view(page, WITH_RECOMMENDATIONS);
  refusal = { service: 'customer-preferences', file: 'unknown-channel', status: 422 };
  await page.locator('[data-testid=channels-form] button[type=submit]').click();
  await expect(page.getByTestId('error-code').first()).toHaveText('unknown_channel');
  await expect(page.getByTestId('error-detail').first()).toHaveText('unknown');
  refusal = { service: 'customer-preferences', file: 'unknown-category', status: 422 };
  await page.locator('[data-testid=categories-form] button[type=submit]').click();
  await expect(page.getByTestId('error-code').first()).toHaveText('unknown_category');
  await app.close();
});

test('an ADMIN corrects a customer; email_taken, unknown_channel and invalid_body are shown as returned', async () => {
  const { app, page } = await launch('ADMIN');
  await view(page, WITH_RECOMMENDATIONS);
  await page.fill('#c-name', 'Jordan Lee Ruiz');
  await page.selectOption('#c-registration_channel_id', '5');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('notice')).toHaveText('Jordan Lee saved.');
  await expect(page.getByTestId('customer-name')).toHaveText('Jordan Lee Ruiz');
  await expect(page.getByTestId('customer-channel')).toHaveText('call_center');
  const patch = writes().at(-1)!;
  expect(patch.method).toBe('PATCH');
  // Only what changed was sent.
  expect(patch.body).toContain(
    '<name>Jordan Lee Ruiz</name><registration_channel_id>5</registration_channel_id>',
  );
  expect(patch.body).not.toContain('<email>');
  expect(patch.body).not.toContain('<phone>');
  // The directory shows what was stored.
  await expect(page.locator(`tr[data-customer-id="${WITH_RECOMMENDATIONS}"] td`).first()).toHaveText(
    'Jordan Lee Ruiz',
  );

  await page.fill('#c-email', 'taken@example.com');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('email_taken');
  await expect(page.getByTestId('detail-email')).toHaveText('email: not_unique');

  await page.fill('#c-email', 'jordan@example.com');
  refusal = { service: 'customers', file: 'unknown-channel', status: 422 };
  await page.fill('#c-name', 'Jordan L');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('unknown_channel');
  await expect(page.getByTestId('detail-registration_channel_id')).toHaveText(
    'registration_channel_id: unknown_reference',
  );

  refusal = { service: 'customers', file: 'invalid-body', status: 422 };
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('invalid_body');

  // A value that cannot be valid is refused in the main process, naming its field.
  const before = writes().length;
  await page.fill('#c-email', 'not an email');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('detail-email')).toHaveText('email: invalid_format');
  expect(writes()).toHaveLength(before);
  await app.close();
});

test('INVENTORY_PLANNER is not offered the customers screen', async () => {
  const { app, page } = await launch('INVENTORY_PLANNER');
  await expect(nav(page, 'customers')).toHaveCount(0);
  await expect(nav(page, 'products')).toHaveCount(1);
  await app.close();
});
