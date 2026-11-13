import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';

// Inventory planning (F28-12, #881), against a fake gateway that answers with the recorded
// examples of analytics-kpis (demand-coverage), stores and categories.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

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
      if (path === '/api/analytics-kpis/v1/kpi-reports/demand-coverage')
        return reply(200, example('analytics-kpis', 'demand-coverage'));
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

const openPlan = async (page: Page): Promise<void> => {
  await page.locator('nav.nav button[data-view="planning"]').click();
  await page.fill('#p-from', '2026-10-01');
  await page.fill('#p-to', '2026-10-28');
  await page.getByTestId('planning-open').click();
  await expect(page.getByTestId('planning-table')).toBeVisible();
};

test('a planner sees demand by segment and category beside availability and needs', async () => {
  const { app, page } = await launch('INVENTORY_PLANNER');
  await openPlan(page);
  const rows = page.locator('[data-testid=planning-table] tbody tr');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText('LOYAL: 168 (50.0%)');
  await expect(rows.nth(0)).toContainText('No open assignment: 56 (16.7%)');
  await expect(rows.nth(1)).toContainText('No demand');
  await expect(page.locator('[data-testid=planning-table] tr.short')).toHaveCount(2);
  await expect(page.getByTestId('planning-needs')).toHaveText('2 of 3 rows, 69 units');
  await expect(page.getByTestId('planning-window')).toHaveText('2026-10-01 to 2026-10-28 (28 days)');
  await expect(page.getByTestId('planning-calculation')).toContainText('replenishment_units = max(0');
  await expect(page.getByTestId('planning-synthetic')).toContainText('Synthetic');
  await expect(page.locator('body')).not.toContainText(/customer/i);
  // INVENTORY_PLANNER is not an export role (ADR-0074).
  await expect(page.getByTestId('export')).toHaveCount(0);
  const asked = new URL(urls.find((u) => u.includes('/demand-coverage'))!);
  expect(asked.searchParams.get('target_cover_days')).toBe('14');
  await shot(page, '881-desktop-inventory-planning-01-plan');
  await app.close();
});

test('a window that has not ended is refused on screen and nothing is sent', async () => {
  const { app, page } = await launch('INVENTORY_PLANNER');
  await page.locator('nav.nav button[data-view="planning"]').click();
  await page.fill('#p-to', new Date().toISOString().slice(0, 10));
  await page.getByTestId('planning-open').click();
  await expect(page.getByTestId('error-message')).toContainText('ended');
  expect(urls.some((u) => u.includes('/demand-coverage'))).toBe(false);
  await app.close();
});

test('an analyst may export the plan on screen', async () => {
  const { app, page } = await launch('ANALYST');
  await openPlan(page);
  await expect(page.getByTestId('export')).toBeVisible();
  await app.close();
});
