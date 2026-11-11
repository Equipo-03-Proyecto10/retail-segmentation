import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';
import { example, explanationXml, kindOf, matrixXml, membersXml, MOVER } from '../support/segments';

// The migration matrix (F28-06, #875), against a fake gateway that answers with the recorded examples
// of contracts/segments/. The customers behind a cell are generated from the matrix's own cells, so
// the list a cell opens holds as many customers as the cell counted.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;
const documents = (name: string): string =>
  readFileSync(join(CONTRACTS, 'segments', 'examples', `${name}.xml`), 'utf8');

let server: Server;
let base = '';
let role = 'ANALYST';
let urls: string[] = [];
let hostile = false;

/** Server text that is not markup but looks like it: it must reach the screen as text. */
const HOSTILE = '<img src=x onerror="window.xss=1">';
const cdata = (text: string): string => `<![CDATA[${text}]]>`;

function vocabulary(): string {
  const xml = documents('list-segments');
  return hostile ? xml.replace('<name>Champion</name>', `<name>${cdata(HOSTILE)}</name>`) : xml;
}

function explanation(kind: 'within' | 'across', customer: string): string {
  const xml = explanationXml(kind, customer);
  return hostile
    ? xml.replace(/<statement>Recency[^<]*<\/statement>/, `<statement>${cdata(HOSTILE)}</statement>`)
    : xml;
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml' });
      res.end(body);
    };
    req.resume();
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      urls.push(url.toString());
      if (req.method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return req.method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (path === '/api/segments/v1/segments') return reply(200, vocabulary());
      const q = url.searchParams;
      const kind = kindOf(q.get('to') ?? '');
      if (path.startsWith('/api/segments/v1/segment-migrations')) {
        // The service refuses a window in the future.
        if ((q.get('to') ?? '') > '2030') return reply(422, documents('invalid-migration-window'));
        if (path.endsWith('/matrix')) return reply(200, matrixXml(kind));
        if (path.endsWith('/explanation')) return reply(200, explanation(kind, q.get('customer_id') ?? ''));
        return reply(200, membersXml(kind, q));
      }
      reply(404);
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(() => {
  urls = [];
  hostile = false;
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

const WITHIN = ['2026-09-14T03:00', '2026-10-12T03:00'];
const ACROSS = ['2026-10-12T03:00', '2026-10-21T03:00'];

async function openMatrix(page: Page, dates: string[]): Promise<void> {
  await page.locator('nav.nav button[data-view="migrations"]').click();
  await page.fill('#m-from', dates[0] as string);
  await page.fill('#m-to', dates[1] as string);
  await page.getByRole('button', { name: 'Open matrix' }).click();
  await expect(page.getByTestId('matrix-table')).toBeVisible();
}

const cell = (page: Page, before: string, after: string) =>
  page.getByTestId(`cell-label:${before}-label:${after}`);
const rows = (page: Page) => page.locator('[data-testid=members-table] tbody tr');

test('two periods open a matrix whose cells open their customers and whose totals reconcile', async () => {
  const { app, page } = await launch('ANALYST');
  await openMatrix(page, WITHIN);
  await page.getByTestId('matrix-table').scrollIntoViewIfNeeded();
  await shot(page, '875-desktop-migration-01-matrix');

  await expect(page.getByTestId('matrix-from')).toHaveText('2026-09-14T03:00:00Z');
  await expect(page.getByTestId('matrix-to')).toHaveText('2026-10-12T03:00:00Z');
  await expect(page.getByTestId('matrix-versions')).toHaveText(
    'Before: model version 3. After: model version 3.',
  );
  await expect(page.getByTestId('matrix-behaviour')).toBeVisible();
  await expect(page.getByTestId('matrix-methodological')).toHaveCount(0);
  await expect(page.getByTestId('matrix-reconciled')).toContainText('Totals reconcile');
  await expect(page.getByTestId('matrix-unreconciled')).toHaveCount(0);

  // The grid is the full grid: the six labels of the vocabulary, Unassigned and New, on both axes.
  await expect(page.locator('[data-testid=matrix-table] tbody tr')).toHaveCount(9);
  await expect(cell(page, 'CHAMPION', 'LOYAL')).toHaveText('3');
  await expect(page.locator('td:has([data-testid="cell-label:CHAMPION-label:LOYAL"])')).toHaveAttribute(
    'data-movement',
    'declined',
  );
  await expect(cell(page, 'LOST', 'CHAMPION')).toHaveText('0');

  // What the screen shows adds up: the cells of each row make its total, those of each column make
  // its total, and both make the customers compared.
  const sums = await page.evaluate(() => {
    const table = document.querySelector('[data-testid=matrix-table]') as HTMLTableElement;
    const body = Array.from(table.tBodies[0]!.rows);
    const number = (td: Element | undefined): number => Number(td?.textContent ?? NaN);
    const cells = body.slice(0, -1).map((tr) => Array.from(tr.cells).slice(1, -1).map(number));
    const rowTotals = body.slice(0, -1).map((tr) => number(tr.cells[tr.cells.length - 1]));
    const columnTotals = Array.from(body[body.length - 1]!.cells)
      .slice(1, -1)
      .map(number);
    const grand = number(body[body.length - 1]!.cells[body[body.length - 1]!.cells.length - 1]);
    const sum = (a: number[]): number => a.reduce((n, v) => n + v, 0);
    return {
      rowsAdd: cells.every((row, i) => sum(row) === rowTotals[i]),
      columnsAdd: columnTotals.every((t, c) => sum(cells.map((row) => row[c] as number)) === t),
      grandFromRows: sum(rowTotals),
      grandFromColumns: sum(columnTotals),
      grand,
    };
  });
  expect(sums).toEqual({
    rowsAdd: true,
    columnsAdd: true,
    grandFromRows: 49,
    grandFromColumns: 49,
    grand: 49,
  });
  await expect(page.getByTestId('row-total-label:CHAMPION')).toHaveText('16');
  await expect(page.getByTestId('column-total-label:CHAMPION')).toHaveText('18');
  await expect(page.getByTestId('grand-total')).toHaveText('49');
  await expect(page.getByTestId('matrix-customers')).toHaveText('49');

  // Entries and exits of a label are the service's figures, and open the customers.
  await expect(page.locator('[data-testid=totals-table] tr[data-state="label:CHAMPION"] td')).toHaveText([
    'Champion',
    '16',
    '18',
    '5',
    '3',
  ]);

  // A cell opens the customers it counted, with both labels and the movement.
  await cell(page, 'CHAMPION', 'LOYAL').click();
  await expect(page.getByTestId('members-title')).toHaveText('Customers who were Champion and are Loyal');
  await expect(rows(page)).toHaveCount(3);
  await expect(page.getByTestId('members-reconciled')).toContainText('all 3 customers');
  await expect(rows(page).first()).toContainText('Champion');
  await expect(rows(page).first()).toContainText('Loyal');
  await expect(rows(page).first()).toContainText('declined');
  expect(
    urls.some((u) => u.includes('previous_label_code=CHAMPION') && u.includes('new_label_code=LOYAL')),
  ).toBe(true);

  // A cell larger than a page pages through every customer.
  await cell(page, 'LOYAL', 'LOYAL').click();
  await expect(rows(page)).toHaveCount(20);
  await expect(page.getByTestId('customers-page')).toHaveText('Page 1 of 2 · 22 customers');
  await page.getByTestId('customers-next').click();
  await expect(rows(page)).toHaveCount(2);
  await expect(page.getByTestId('members-mismatch')).toHaveCount(0);

  // A cell with no customer is still a cell, and opens an empty list.
  await cell(page, 'LOST', 'CHAMPION').click();
  await expect(page.getByTestId('members-empty')).toBeVisible();

  // Entries to a label, and exits from it.
  await page.getByTestId('entries-label:CHAMPION').click();
  await expect(page.getByTestId('members-title')).toHaveText('Customers who entered Champion');
  await expect(rows(page)).toHaveCount(5);
  await page.getByTestId('exits-label:CHAMPION').click();
  await expect(page.getByTestId('members-title')).toHaveText('Customers who left Champion');
  await expect(rows(page)).toHaveCount(3);
  await expect(page.getByTestId('members-reconciled')).toBeVisible();
  // New and Unassigned have no entries or exits to open: the service lists them for a label only.
  await expect(page.getByTestId('entries-unassigned')).toHaveCount(0);
  await app.close();
});

test('a move opens its responsible variables', async () => {
  const { app, page } = await launch('ANALYST');
  await openMatrix(page, WITHIN);
  await cell(page, 'LOYAL', 'CHAMPION').click();
  await expect(rows(page)).toHaveCount(5);
  await expect(page.getByTestId(`responsible-${MOVER.within.id}`)).toHaveText('Recency, Monetary');
  await expect(page.getByTestId(`kind-${MOVER.within.id}`)).toHaveText('Behaviour');

  await page.getByTestId(`explain-${MOVER.within.id}`).click();
  await expect(page.getByTestId('explanation-customer')).toHaveText(MOVER.within.id);
  await expect(page.getByTestId('explanation-move')).toHaveText('Loyal to Champion (improved)');
  await expect(page.getByTestId('explanation-responsible')).toHaveText('Recency, Monetary');
  const measures = page.locator('[data-testid=explanation-table] tbody tr');
  await expect(measures).toHaveCount(3);
  await expect(measures.nth(0)).toHaveAttribute('data-judgement', 'changed');
  await expect(measures.nth(0)).toContainText('41');
  await expect(measures.nth(0)).toContainText('Recency changed: the last purchase was 41 days before');
  await expect(measures.nth(1)).toHaveAttribute('data-judgement', 'stable');
  await expect(measures.nth(2)).toHaveAttribute('data-judgement', 'changed');
  await expect(measures.nth(2)).toContainText('1560.00');
  await expect(page.getByTestId('explanation-methodological')).toHaveCount(0);
  await page.getByTestId('explanation-table').scrollIntoViewIfNeeded();
  await shot(page, '875-desktop-migration-02-explanation');
  expect(urls.some((u) => u.includes('/explanation?') && u.includes(`customer_id=${MOVER.within.id}`))).toBe(
    true,
  );

  await app.close();
});

test('two versions are marked methodological and are not reported as behaviour', async () => {
  const { app, page } = await launch('ANALYST');
  await openMatrix(page, ACROSS);
  await page.getByTestId('matrix-methodological').scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, 330));
  await shot(page, '875-desktop-migration-03-methodological');
  await expect(page.getByTestId('matrix-methodological')).toContainText('Methodological comparison');
  await expect(page.getByTestId('matrix-methodological')).toContainText('not shown as behaviour');
  await expect(page.getByTestId('matrix-versions')).toHaveText(
    'Before: model version 3. After: model version 4.',
  );
  await expect(page.getByTestId('matrix-behaviour')).toHaveCount(0);
  await expect(page.getByTestId('matrix-table')).toHaveClass(/methodological/);
  // It still adds up.
  await expect(page.getByTestId('matrix-reconciled')).toBeVisible();
  await expect(page.getByTestId('grand-total')).toHaveText('49');

  await cell(page, 'CHAMPION', 'LOYAL').click();
  await expect(rows(page)).toHaveCount(9);
  for (const row of await rows(page).all())
    await expect(row.locator('td').nth(7)).toHaveText('Methodological: may come from the model');
  // "Behaviour only" asks the service for nothing a model change may explain.
  await page.locator('#members-methodological').selectOption('false');
  await expect(page.getByTestId('members-empty')).toBeVisible();
  expect(urls.some((u) => u.includes('methodological=false'))).toBe(true);
  await page.locator('#members-methodological').selectOption('');
  await expect(rows(page)).toHaveCount(9);

  await page.getByTestId(`explain-${MOVER.across.id}`).click();
  await expect(page.getByTestId('explanation-methodological')).toContainText('may come from the model');
  await expect(page.getByTestId('explanation-rank-only')).toContainText('Only the rank moved');
  await expect(page.getByTestId('explanation-responsible')).toHaveText('None: only the rank moved');
  await app.close();
});

test('the dates are read in order and a refusal is shown as the service returned it', async () => {
  const { app, page } = await launch('ANALYST');
  await openMatrix(page, [WITHIN[1] as string, WITHIN[0] as string]);
  await expect(page.getByTestId('matrix-reordered')).toBeVisible();
  const asked = urls.filter((u) => u.includes('/matrix')).map((u) => new URL(u).searchParams);
  expect([asked[0]?.get('from'), asked[0]?.get('to')]).toEqual([
    '2026-09-14T03:00:00Z',
    '2026-10-12T03:00:00Z',
  ]);

  await page.fill('#m-from', '2026-09-14T03:00');
  await page.fill('#m-to', '2026-09-14T03:00');
  await page.getByRole('button', { name: 'Open matrix' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('invalid_input');
  await expect(page.getByTestId('matrix-table')).toHaveCount(0);

  await page.fill('#m-to', '2099-01-01T00:00');
  await page.getByRole('button', { name: 'Open matrix' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('invalid_query');
  await expect(page.getByTestId('error-correlation')).toHaveText('corr-invalid-migration-window');
  await expect(page.getByTestId('error-detail')).toHaveText('not_before_to');
  await app.close();
});

test('only the roles the contract allows see the module, and a forced call is refused', async () => {
  for (const as of ['ADMIN', 'ANALYST', 'STORE_MANAGER', 'MARKETING', 'AUDITOR']) {
    const { app, page } = await launch(as);
    await expect(page.locator('nav.nav button[data-view="migrations"]')).toHaveCount(1);
    await app.close();
  }

  let { app, page } = await launch('INVENTORY_PLANNER');
  await expect(page.locator('nav.nav button[data-view="migrations"]')).toHaveCount(0);
  const before = urls.length;
  const forced = await page.evaluate(() =>
    window.mosaiq.migrationMatrix({ from: '2026-09-14T03:00:00Z', to: '2026-10-12T03:00:00Z' }),
  );
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(urls.slice(before).filter((u) => u.includes('segment-migrations'))).toEqual([]);
  await app.close();

  // A role that may read but not export sees the matrix without an export control.
  ({ app, page } = await launch('STORE_MANAGER'));
  await openMatrix(page, WITHIN);
  await expect(page.getByTestId('export')).toHaveCount(0);
  await app.close();

  ({ app, page } = await launch('ADMIN'));
  await openMatrix(page, WITHIN);
  await expect(page.getByTestId('export')).toHaveCount(1);
  await app.close();
});

test('what the service sent is shown as text, never as markup', async () => {
  hostile = true;
  const { app, page } = await launch('ANALYST');
  await openMatrix(page, WITHIN);
  await expect(page.getByRole('columnheader', { name: HOSTILE })).toBeVisible();
  await cell(page, 'LOYAL', 'CHAMPION').click();
  await expect(rows(page)).toHaveCount(5);
  await expect(rows(page).first()).toContainText(HOSTILE);
  await page.getByTestId(`explain-${MOVER.within.id}`).click();
  await expect(page.getByTestId('explanation-table')).toContainText(HOSTILE);
  expect(await page.locator('main img').count()).toBe(0);
  expect(await page.evaluate(() => 'xss' in window)).toBe(false);
  await app.close();
});

test('the recorded matrix examples are the ones the screen reads', () => {
  // The fake answers with the contract's own files, not a copy of them.
  expect(matrixXml('within')).toBe(example('migration-matrix'));
  expect(matrixXml('across')).toBe(example('migration-matrix-across-versions'));
});
