import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

// Demand and stock (F28-12, #881), against a fake gateway that answers with the recorded examples of
// analytics-kpis, products, stores and categories, and with stock levels for the two products of the
// demand example. The planner reads the plan; no customer appears in it.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;
const STOCK = `<?xml version="1.0" encoding="UTF-8"?>
<StockLevels xmlns="https://mosaiq.maxthecoder.online/xml/stock/v1"
             xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1">
  <StockLevel>
    <store_id>1</store_id>
    <product_id>1001</product_id>
    <quantity_on_hand>30</quantity_on_hand>
    <updated_at>2026-10-15T16:42:10Z</updated_at>
  </StockLevel>
  <StockLevel>
    <store_id>1</store_id>
    <product_id>1002</product_id>
    <quantity_on_hand>5</quantity_on_hand>
    <updated_at>2026-10-15T16:42:10Z</updated_at>
  </StockLevel>
  <common:PageInfo>
    <common:page>1</common:page>
    <common:size>100</common:size>
    <common:total>2</common:total>
  </common:PageInfo>
</StockLevels>`;

let server: Server;
let base = '';
let role = 'INVENTORY_PLANNER';
let urls: string[] = [];

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml' });
      res.end(body);
    };
    req.on('data', () => undefined);
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      urls.push(url.toString());
      if (req.method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return req.method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (path === '/api/analytics-kpis/v1/kpi-reports/demand')
        return reply(200, example('analytics-kpis', 'demand'));
      if (path === '/api/stock/v1/stock-levels') return reply(200, STOCK);
      if (path === '/api/products/v1/products') return reply(200, example('products', 'products'));
      if (path === '/api/stores/v1/stores') return reply(200, example('stores', 'directory-page'));
      if (path === '/api/categories/v1/categories')
        return reply(200, example('categories', 'list-categories'));
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

test('the planner reads demand next to the units on hand, with no customer in view', async () => {
  const { app, page } = await launch('INVENTORY_PLANNER');
  await page.locator('nav.nav button[data-view="inventory"]').click();
  await page.locator('select#r-store_id').selectOption('1');
  await page.getByTestId('inventory-open').click();
  await expect(page.getByTestId('inventory-categories')).toBeVisible();
  await expect(page.locator('[data-testid=inventory-categories] tbody tr')).toHaveCount(2);
  await expect(page.locator('[data-testid=inventory-segments] tbody tr')).toHaveCount(2);
  await expect(page.getByTestId('inventory-categories')).toContainText('Yogurt');
  await expect(page.getByTestId('inventory-categories')).toContainText('Short by');
  await expect(page.getByText('The plan holds no customer.')).toBeVisible();
  expect(urls.some((u) => u.includes('group_by=category%2Csegment'))).toBe(true);
  await app.close();
});

test('a role that may not read demand does not see the module', async () => {
  const { app, page } = await launch('MARKETING');
  await expect(page.locator('nav.nav button[data-view="inventory"]')).toHaveCount(0);
  await app.close();
});
