import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

// Executive reports (F28-14, #883), against a fake gateway that answers with the recorded
// examples of analytics-kpis, stores and sales.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

let server: Server;
let base = '';
let role = 'ANALYST';
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
      if (path === '/api/analytics-kpis/v1/kpi-reports/definitions')
        return reply(200, example('analytics-kpis', 'kpi-definitions'));
      if (path === '/api/analytics-kpis/v1/kpi-reports/net-sales')
        return reply(200, example('analytics-kpis', 'net-sales'));
      if (path === '/api/stores/v1/stores') return reply(200, example('stores', 'directory-page'));
      if (path === '/api/sales/v1/channels') return reply(200, example('sales', 'list-channels'));
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

const openReport = async (page: Page): Promise<void> => {
  await page.locator('nav.nav button[data-view="reports"]').click();
  await page.getByTestId('group-store').check();
  await page.getByTestId('group-period').check();
  await page.getByTestId('report-open').click();
  await expect(page.getByTestId('report-table')).toBeVisible();
};

test('a report shows its summary, chart, figures, Synthetic marker and definitions, with export buttons', async () => {
  const { app, page } = await launch('ANALYST');
  await openReport(page);
  await expect(page.locator('[data-testid=report-table] tbody tr')).toHaveCount(4);
  await expect(page.getByTestId('report-synthetic')).toContainText('Synthetic');
  await expect(page.locator('[data-testid=report-table] td.synthetic')).toHaveCount(1);
  await expect(page.locator('[data-testid=report-chart] meter')).toHaveCount(4);
  await expect(page.getByTestId('report-summary')).toContainText('Net sales (definition version 1)');
  await expect(page.getByTestId('report-definitions')).toContainText('Sum of net_amount');
  for (const f of ['pdf', 'csv', 'xlsx']) await expect(page.getByTestId(`export-${f}`)).toBeVisible();
  expect(urls.some((u) => u.includes('group_by=store%2Cperiod'))).toBe(true);
  await app.close();
});

test('a role that may not export reads the report without export buttons', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  await openReport(page);
  await expect(page.getByTestId('export-pdf')).toHaveCount(0);
  const refused = await page.evaluate(() => window.mosaiq.exportReport('pdf'));
  expect(refused).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  await app.close();
});
