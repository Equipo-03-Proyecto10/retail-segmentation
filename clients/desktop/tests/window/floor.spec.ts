import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { deferred, type Deferred } from '../support/deferred';

// Stock, sales, engagement and check-ins (F16-04, #417), against a fake gateway
// that answers with the recorded examples of the six contracts involved.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const PRODUCTS = 'https://mosaiq.maxthecoder.online/xml/products/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';
const VOIDABLE = 'a3c1f7e2-5b48-4d0e-9f6a-1c2b3d4e5f61';
const REVIEWED = '7c1f9a52-3b0e-4d8e-9a64-5f2d1c0b8e72';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

/** The recorded product, renamed for each requested id, so every line and level has a name. */
function productsFor(ids: string[]): string {
  const template = /<Product>[\s\S]*?<\/Product>/.exec(example('products', 'products'))![0];
  const items = ids
    .map(Number)
    .filter((id) => Number.isSafeInteger(id) && id > 0)
    .map((id) =>
      template
        .replace(/<product_id>\d+<\/product_id>/, `<product_id>${id}</product_id>`)
        .replace(/<name>[^<]*<\/name>/, `<name>Item ${id}</name>`),
    );
  return `<Products xmlns="${PRODUCTS}" xmlns:common="${COMMON}">${items.join('')}<common:PageInfo><common:page>1</common:page><common:size>50</common:size><common:total>${items.length}</common:total></common:PageInfo></Products>`;
}

/** The recorded directory, with its second store moved to id 3, where the recorded floor activity happened. */
const directory = (): string =>
  example('stores', 'directory-page').replace('<store_id>2</store_id>', '<store_id>3</store_id>');

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'ADMIN';
let seen: Seen[] = [];
let voided = false;
let reviews: string[] = [];
let nextRefusal: { service: string; file: string; status: number } | null = null;
let delayedFirstSales: Deferred<void> | null = null;
let salesReads = 0;

function reset(): void {
  seen = [];
  voided = false;
  nextRefusal = null;
  reviews = [];
  delayedFirstSales = null;
  salesReads = 0;
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
      if (method !== 'GET' && role !== 'ADMIN') return reply(403, example('engagement', 'forbidden-role'));
      if (method !== 'GET' && nextRefusal !== null) {
        const r = nextRefusal;
        nextRefusal = null;
        return reply(r.status, example(r.service, r.file));
      }

      if (path === '/api/products/v1/products')
        return reply(200, productsFor((url.searchParams.get('ids') ?? '').split(',')));
      if (path === '/api/stores/v1/stores') return reply(200, directory());
      if (path === '/api/sales/v1/channels') return reply(200, example('sales', 'list-channels'));
      if (path === '/api/stock/v1/stock-levels') return reply(200, example('stock', 'stock-levels-filtered'));
      if (path === '/api/sales/v1/sales') {
        salesReads += 1;
        const response =
          url.searchParams.has('store_id') || url.searchParams.has('from')
            ? example('sales', 'list-sales')
                .replace(/\s*<Sale>[\s\S]*?<\/Sale>/g, '')
                .replace('<common:total>2</common:total>', '<common:total>0</common:total>')
            : example('sales', 'list-sales');
        if (salesReads === 1 && delayedFirstSales !== null) {
          void delayedFirstSales.promise.then(() => reply(200, response));
          return;
        }
        return reply(200, response);
      }
      if (path === '/api/sales/v1/sales/4812') return reply(200, example('sales', 'get-sale'));
      if (path.startsWith('/api/sales/v1/sales/')) return reply(404, example('sales', 'sale-not-found'));

      if (path === '/api/engagement/v1/engagement-summaries') {
        let summary = example('engagement', `summary-by-${url.searchParams.get('group_by') ?? 'store'}`);
        // The voided event was at store 3: after the void the summary no longer counts it.
        if (voided) summary = summary.replace(/(<key>3<\/key>\s*<count>)57(<\/count>)/, '$156$2');
        return reply(200, summary);
      }
      if (path === '/api/engagement/v1/engagement-events') {
        let page = example('engagement', 'events-page');
        if (voided)
          page = page.replace(
            new RegExp(`(<event_id>${VOIDABLE}</event_id>[\\s\\S]*?)<voided>false</voided>`),
            '$1<voided>true</voided><history><action>VOIDED</action><reason>Scanned the wrong shelf label.</reason><by>9d4e7a10-52c3-4b8f-a1e6-0c3b5d7f9a21</by><at>2026-10-15T18:05:00Z</at></history>',
          );
        return reply(200, page);
      }
      if (path === `/api/engagement/v1/engagement-events/${VOIDABLE}/void`) {
        if (voided) return reply(409, example('engagement', 'already-voided'));
        voided = true;
        return reply(
          200,
          example('engagement', 'voided-event')
            .replace(/a3c1f7e2-5b48-4d0e-9f6a-1c2b3d4e5f63/, VOIDABLE)
            .replace('<product_id>311</product_id>', '<product_id>120</product_id>'),
        );
      }

      if (path === '/api/check-ins/v1/check-ins') return reply(200, example('check-ins', 'check-ins-page'));
      if (path === `/api/check-ins/v1/check-ins/${REVIEWED}`)
        return reply(
          200,
          example('check-ins', 'check-in-with-reviews').replace(
            '</CheckInDetail>',
            `${reviews.join('')}</CheckInDetail>`,
          ),
        );
      if (path === `/api/check-ins/v1/check-ins/${REVIEWED}/reviews`) {
        const reason = 'Footage shows the car park, not the store.';
        expect(body).toContain(`<reason>${reason}</reason>`);
        const review = example('check-ins', 'review')
          .replace('<review_id>42</review_id>', `<review_id>${43 + reviews.length}</review_id>`)
          .replace(/<reason>[^<]*<\/reason>/, `<reason>${reason}</reason>`);
        reviews.push(
          /<Review [^>]*>([\s\S]*)<\/Review>/.exec(review)![1]!.replace(/^/, '<reviews>') + '</reviews>',
        );
        return reply(201, review);
      }
      if (path.startsWith('/api/check-ins/v1/check-ins/'))
        return reply(404, example('check-ins', 'check-in-not-found'));
      reply(404, example('sales', 'sale-not-found'));
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

const nav = (page: Page) =>
  page.locator('nav.nav button').evaluateAll((bs) => bs.map((b) => (b as HTMLElement).dataset.view));
const open = (page: Page, name: string) => page.locator(`nav.nav button[data-view="${name}"]`).click();

test('stock below a threshold, named from products-service and stores-service', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'stock');
  await expect(page.locator('[data-testid=stock-table] tbody tr')).toHaveCount(3);
  await page.selectOption('#filter-store_id', '3');
  await page.fill('#filter-below', '15');
  await page.getByRole('button', { name: 'Show stock' }).click();
  await expect
    .poll(() => seen.filter((s) => s.url.includes('/stock-levels')).at(-1)?.url)
    .toContain('below=15');
  const row = page.locator('tr[data-product-id="120"]');
  await expect(row).toContainText('Item 120 (#120)');
  await expect(row).toContainText('Coyoacan');
  // One ids= call for the page, never one per product.
  const lookups = seen.filter((s) => s.url.includes('/api/products/v1/products'));
  expect(new URL(lookups.at(-1)!.url).searchParams.get('ids')).toBe('120,245,311');
  await app.close();
});

test('sales read back in XML, with lines, total, channel and store; refusals as returned', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'sales');
  await expect(page.locator('[data-testid=sales-table] tbody tr')).toHaveCount(2);
  await page.getByTestId('view-sale-4812').click();
  await expect(page.getByTestId('sale-total')).toHaveText('203.25');
  await expect(page.getByTestId('sale-channel')).toHaveText('physical_store');
  await expect(page.getByTestId('sale-store')).toHaveText('Coyoacan');
  const lines = page.locator('[data-testid=sale-lines] tbody tr');
  await expect(lines).toHaveCount(3);
  await expect(lines.nth(1)).toContainText('Item 1020 (#1020)');
  await expect(lines.nth(1)).toContainText('12.75');

  await page.getByTestId('view-sale-4811').click();
  await expect(page.getByTestId('error-code')).toHaveText('sale_not_found');

  const before = seen.filter((s) => s.url.includes('/api/sales/v1/sales?')).length;
  await page.getByTestId('refresh-sales').click();
  await expect.poll(() => seen.filter((s) => s.url.includes('/api/sales/v1/sales?')).length).toBe(before + 1);
  await app.close();
});

test('sales keeps a newer filtered page when the initial page answers late', async () => {
  delayedFirstSales = deferred<void>();
  const { app, page } = await launch('ADMIN');
  await open(page, 'sales');
  await expect.poll(() => salesReads).toBe(1);
  await page.selectOption('#filter-store_id', '3');
  await page.fill('#filter-from', '2026-10-22T00:00');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect.poll(() => salesReads).toBe(2);
  await expect(page.getByTestId('sales-table')).toBeVisible();
  await expect(page.locator('[data-testid=sales-table] tbody tr')).toHaveCount(0);

  delayedFirstSales.resolve();
  await expect
    .poll(() => seen.filter((s) => s.url.includes('/api/sales/v1/channels')).length)
    .toBeGreaterThan(0);
  // Observe the renderer after the old page's name lookups, including its IPC delivery.
  for (let sample = 0; sample < 10; sample += 1) {
    await page.waitForTimeout(100);
    await expect(page.locator('[data-testid=sales-table] tbody tr')).toHaveCount(0);
  }
  await app.close();
});

test('an ADMIN voids an event: it stays listed as voided and the summary stops counting it', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'engagement');
  await expect(page.locator('[data-testid=summary-table] tr[data-key="3"]')).toContainText('57');
  await expect(page.locator('[data-testid=summary-table] tr[data-key="3"]')).toContainText('Coyoacan');
  await expect(page.locator('[data-testid=events-table] tbody tr')).toHaveCount(3);

  await page.getByTestId(`view-event-${VOIDABLE}`).click();
  await page.getByTestId('void').click();
  await page.getByTestId('confirm-void').click();
  await expect(page.getByTestId('detail-reason')).toHaveText('reason: required');
  expect(seen.some((s) => s.url.endsWith('/void'))).toBe(false);

  await page.fill('#c-reason', 'Scanned the wrong shelf label.');
  await page.getByTestId('void').click();
  await page.getByTestId('confirm-void').click();
  await expect(page.getByTestId('notice')).toContainText('The event is voided');
  await expect(page.getByTestId('event-status')).toHaveText('Voided');
  await expect(page.getByTestId('event-history')).toContainText('Scanned the wrong shelf label.');
  await expect(page.locator(`tr[data-event-id="${VOIDABLE}"]`)).toContainText('Voided');
  await expect(page.locator('[data-testid=summary-table] tr[data-key="3"]')).toContainText('56');
  expect(seen.filter((s) => s.url.endsWith('/void')).at(-1)!.body).toContain(
    '<reason>Scanned the wrong shelf label.</reason>',
  );
  await expect(page.getByTestId('event-detail').getByText('Delete')).toHaveCount(0);
  await app.close();
});

test('a void that another user already made is shown as already_voided', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'engagement');
  await page.getByTestId(`view-event-${VOIDABLE}`).click();
  nextRefusal = { service: 'engagement', file: 'already-voided', status: 409 };
  await page.fill('#c-reason', 'Duplicate scan.');
  await page.getByTestId('void').click();
  await page.getByTestId('confirm-void').click();
  await expect(page.getByTestId('error-code')).toHaveText('already_voided');
  await expect(page.getByTestId('error-correlation')).toHaveText('corr-already-voided');
  await app.close();
});

test('check-ins show reason and verified; an ADMIN appends a review below the earlier ones', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'check-ins');
  const rows = page.locator('[data-testid=check-ins-table] tbody tr');
  await expect(rows.first()).toContainText("Inside the store's geofence (38 m from the store)");
  await expect(page.locator('[data-testid=check-ins-table]')).toContainText('No location was sent');

  await page.getByTestId(`view-check-in-${REVIEWED}`).click();
  await expect(page.getByTestId('check-in-reason-code')).toHaveText('outside_geofence');
  await expect(page.locator('[data-testid=reviews] li')).toHaveCount(2);
  await expect(page.locator('[data-testid=reviews] li').first()).toHaveAttribute('data-review-id', '41');

  await page.getByRole('button', { name: 'Add review' }).click();
  await expect(page.getByTestId('detail-reason')).toHaveText('reason: required');

  await page.selectOption('[data-testid=review-form] #c-verified', 'false');
  await page.fill('[data-testid=review-form] #c-reason', 'Footage shows the car park, not the store.');
  await page.getByRole('button', { name: 'Add review' }).click();
  await expect(page.getByTestId('notice')).toHaveText('Review added.');
  const items = page.locator('[data-testid=reviews] li');
  await expect(items).toHaveCount(3);
  await expect(items.last()).toHaveAttribute('data-review-id', '43');
  await expect(items.last()).toContainText('Footage shows the car park');
  const post = seen.filter((s) => s.url.endsWith('/reviews')).at(-1)!;
  expect(post.body).toContain('<verified>false</verified>');
  await expect(page.getByTestId('check-in-detail').getByText(/Delete|Edit/)).toHaveCount(0);
  await app.close();
});

test('screens and controls follow each role', async () => {
  let { app, page } = await launch('INVENTORY_PLANNER');
  expect(await nav(page)).toEqual(['products', 'categories', 'stock', 'engagement', 'stores', 'services']);
  await open(page, 'engagement');
  await expect(page.locator('[data-testid=summary-table] tbody tr')).toHaveCount(3);
  await expect(page.getByTestId('event-filters')).toHaveCount(0);
  await app.close();

  ({ app, page } = await launch('ANALYST'));
  expect(await nav(page)).toEqual([
    'products',
    'categories',
    'stock',
    'sales',
    'engagement',
    'stores',
    'campaigns',
    'customers',
    'services',
  ]);
  await open(page, 'engagement');
  await page.getByTestId(`view-event-${VOIDABLE}`).click();
  await expect(page.getByTestId('event-status')).toHaveText('Counted');
  await expect(page.getByTestId('void')).toHaveCount(0);
  await app.close();

  ({ app, page } = await launch('STORE_MANAGER'));
  expect(await nav(page)).toContain('check-ins');
  await open(page, 'check-ins');
  await page.getByTestId(`view-check-in-${REVIEWED}`).click();
  await expect(page.locator('[data-testid=reviews] li')).toHaveCount(2);
  await expect(page.getByTestId('review-form')).toHaveCount(0);
  const forced = await page.evaluate((id) => window.mosaiq.reviewCheckIn(id, true, 'Forced.'), REVIEWED);
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  await app.close();
});

test('a check-in that does not exist is shown as check_in_not_found', async () => {
  const { app, page } = await launch('AUDITOR');
  await open(page, 'check-ins');
  await page.getByTestId('view-check-in-7c1f9a52-3b0e-4d8e-9a64-5f2d1c0b8e71').click();
  await expect(page.getByTestId('error-code')).toHaveText('check_in_not_found');
  await app.close();
});
