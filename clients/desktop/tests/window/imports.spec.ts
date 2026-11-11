import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { deferred, type Deferred } from '../support/deferred';
import { shot } from '../support/evidence';
import { chunkLines, importXml, salesCsv } from '../support/imports';

// Import and data quality (F28-02, #871), against a fake gateway that answers with the recorded
// examples of contracts/sales/. The file dialog is replaced in the main process, so the file is
// read there as in production, and the renderer never names it.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const PRODUCTS = 'https://mosaiq.maxthecoder.online/xml/products/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

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

interface Seen {
  method: string;
  url: string;
  body: string;
  headers: Record<string, string | string[] | undefined>;
}

/** The import the fake service keeps: chunks are staged as the contract says, rows are applied on each read. */
const imp = {
  total: 0,
  chunkCount: 0,
  staged: 0,
  received: 0,
  applied: 0,
  status: 'RECEIVING',
  bodies: [] as string[],
};
/** The service refuses 4 of the 320 rows, as the recorded report lists. */
const REFUSED = 4;

let server: Server;
let base = '';
let role = 'ADMIN';
let seen: Seen[] = [];
let gate: Deferred<void> | null = null;
let unavailable = 0;
let rejectionsBody = '';

function reset(): void {
  seen = [];
  gate = null;
  unavailable = 0;
  rejectionsBody = example('sales', 'list-sale-import-rejections');
  // Import 17 starts as a finished load of 100 rows, 4 of them refused, as the recorded report says.
  Object.assign(imp, {
    total: 100,
    chunkCount: 1,
    staged: 1,
    received: 100,
    applied: 100,
    status: 'COMPLETED',
    bodies: [],
  });
}

function importResponse(): string {
  const refused = Math.min(REFUSED, Math.floor(imp.applied / 20));
  const done = imp.status !== 'RECEIVING' && imp.status !== 'PROCESSING';
  return importXml(example('sales', done ? 'get-sale-import-completed' : 'get-sale-import'), {
    status: imp.status,
    total_rows: imp.total,
    chunk_count: imp.chunkCount,
    next_sequence: imp.staged,
    received: imp.received,
    accepted: imp.applied - refused,
    rejected: refused,
    pending: imp.received - imp.applied,
  });
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string, headers: Record<string, string> = {}): void => {
      res.writeHead(status, {
        'Content-Type': 'application/xml',
        'X-Content-Type-Options': 'nosniff',
        ...headers,
      });
      res.end(body);
    };
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      seen.push({ method, url: url.toString(), body, headers: req.headers });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (path.startsWith('/api/sales/v1/sale-imports') && role !== 'ADMIN')
        return reply(403, example('sales', 'forbidden-role'));

      if (path === '/api/products/v1/products')
        return reply(200, productsFor((url.searchParams.get('ids') ?? '').split(',')));
      if (path === '/api/stores/v1/stores')
        return reply(
          200,
          example('stores', 'directory-page').replace('<store_id>2</store_id>', '<store_id>3</store_id>'),
        );
      if (path === '/api/sales/v1/channels') return reply(200, example('sales', 'list-channels'));
      if (path === '/api/sales/v1/sales/4790')
        return reply(
          200,
          example('sales', 'get-sale').replace('<sale_id>4812</sale_id>', '<sale_id>4790</sale_id>'),
        );

      if (method === 'POST' && path === '/api/sales/v1/sale-imports') {
        const m = /<total_rows>(\d+)<\/total_rows><chunk_count>(\d+)</.exec(body);
        Object.assign(imp, {
          total: Number(m?.[1]),
          chunkCount: Number(m?.[2]),
          staged: 0,
          received: 0,
          applied: 0,
          status: 'RECEIVING',
          bodies: [],
        });
        return reply(201, importResponse(), { Location: '/v1/sale-imports/17' });
      }
      if (method === 'GET' && path === '/api/sales/v1/sale-imports')
        return reply(200, example('sales', 'list-sale-imports'));
      if (path === '/api/sales/v1/sale-imports/17/rejections') return reply(200, rejectionsBody);
      if (method === 'GET' && path === '/api/sales/v1/sale-imports/17') {
        if (imp.status === 'PROCESSING') {
          imp.applied = Math.min(imp.received, imp.applied + 200);
          if (imp.applied === imp.received) imp.status = 'COMPLETED';
        }
        return reply(200, importResponse());
      }
      if (method === 'PUT' && path === '/api/sales/v1/sale-imports/17/status') {
        imp.status = 'CANCELLED';
        return reply(200, importResponse());
      }
      const chunk = /^\/api\/sales\/v1\/sale-imports\/17\/chunks\/(\d+)$/.exec(path);
      if (method === 'PUT' && chunk) {
        const seq = Number(chunk[1]);
        const stage = (): void => {
          if (imp.status !== 'RECEIVING') return reply(409, example('sales', 'import-not-receiving'));
          if (seq < imp.staged)
            return imp.bodies[seq] === body
              ? reply(200, importResponse(), { 'Idempotent-Replayed': 'true' })
              : reply(409, example('sales', 'chunk-conflict'));
          if (seq > imp.staged) return reply(409, example('sales', 'chunk-out-of-order'));
          imp.bodies[seq] = body;
          imp.staged += 1;
          imp.received += chunkLines(body).length;
          if (imp.staged === imp.chunkCount) imp.status = 'PROCESSING';
          reply(202, importResponse());
        };
        if (seq === 1 && unavailable > 0) {
          unavailable -= 1;
          return reply(503, example('sales', 'database-unavailable'));
        }
        if (seq === 1 && gate !== null) {
          const held = gate;
          gate = null;
          void held.promise.then(stage);
          return;
        }
        return stage();
      }
      reply(404, example('sales', 'sale-import-not-found'));
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(reset);

async function launch(as: string): Promise<{ app: ElectronApplication; page: Page; dir: string }> {
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
  return { app, page, dir };
}

/** The user's choice in the file dialog: the main process opens this file and nothing else. */
async function picks(app: ElectronApplication, file: string): Promise<void> {
  await app.evaluate(({ dialog }, f) => {
    dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [f] })) as never;
  }, file);
}

function csvFile(text: string): string {
  const dir = mkdtempSync(join(tmpdir(), 'mosaiq-import-'));
  const file = join(dir, 'sales-october.csv');
  writeFileSync(file, text);
  return file;
}

const openImports = (page: Page) => page.locator('nav.nav button[data-view="imports"]').click();
const rejectionRows = (page: Page) => page.locator('[data-testid=rejections-table] tbody tr');
const sentImportCalls = (): string[] =>
  seen
    .filter((s) => s.url.includes('/sale-imports'))
    .map((s) => `${s.method} ${new URL(s.url).pathname.replace('/api/sales', '')}`);

test('the administrator loads a CSV: progress is shown, the counts add up, and the report is reviewed', async () => {
  const { app, page } = await launch('ADMIN');
  await picks(app, csvFile(salesCsv(320)));
  await openImports(page);
  await expect(page.getByTestId('imports-recent')).toContainText('Import 17');

  await page.getByTestId('import-choose').click();
  await expect(page.getByTestId('import-file-name')).toHaveText('sales-october.csv');
  await expect(page.getByTestId('import-file-rows')).toHaveText('320');
  await expect(page.getByTestId('import-file-chunks')).toHaveText('3');

  // The second chunk is held at the service, so the screen can be read mid-load.
  gate = deferred<void>();
  const release = gate;
  await page.getByTestId('import-start').click();
  await expect(page.getByTestId('import-chunks')).toContainText('Chunks staged: 1 of 3');
  await expect(page.getByTestId('import-rows')).toContainText('Rows received: 150 of 320');
  await expect(page.getByTestId('import-counts')).toHaveText(
    'Received 150 = accepted 0 + rejected 0 + pending 150',
  );
  await expect(page.getByTestId('import-identity')).toContainText('The counts add up.');
  expect(await page.getByTestId('import-progress').evaluate((n) => (n as HTMLProgressElement).value)).toBe(1);
  expect(await page.getByTestId('import-progress').evaluate((n) => (n as HTMLProgressElement).max)).toBe(3);
  await expect(page.getByTestId('import-cancel')).toBeVisible();
  await shot(page, '871-desktop-import-01-progress');
  release.resolve();

  // The service applies the rows; the identity holds at every reading, and the review opens by itself.
  await expect(page.getByTestId('import-phase')).toHaveText('Finished.', { timeout: 30_000 });
  await expect(page.getByTestId('import-counts')).toHaveText(
    'Received 320 = accepted 316 + rejected 4 + pending 0',
  );
  await expect(page.getByTestId('review-counts')).toHaveText(
    'Received 320 = accepted 316 + rejected 4 + pending 0',
  );
  await expect(page.getByTestId('review-identity')).toHaveText('The counts add up.');
  await expect(rejectionRows(page)).toHaveCount(4);
  await shot(page, '871-desktop-import-02-review');

  // What travelled: Idempotency-Key once, chunks 0 to 2 in order, all XML.
  const calls = sentImportCalls();
  expect(calls.slice(0, 1)).toEqual(['GET /v1/sale-imports']);
  expect(calls.filter((c) => c.startsWith('PUT') && c.includes('/chunks/'))).toEqual([
    'PUT /v1/sale-imports/17/chunks/0',
    'PUT /v1/sale-imports/17/chunks/1',
    'PUT /v1/sale-imports/17/chunks/2',
  ]);
  const create = seen.find((s) => s.method === 'POST' && s.url.endsWith('/sale-imports'))!;
  expect(String(create.headers['idempotency-key'])).toMatch(/^[0-9a-f-]{36}$/);
  expect(create.headers['content-type']).toBe('application/xml');
  expect(create.body).toContain('<total_rows>320</total_rows><chunk_count>3</chunk_count>');
  for (const s of seen.filter((x) => x.url.includes('/chunks/')))
    expect(Buffer.byteLength(s.body)).toBeLessThanOrEqual(60_000);
  // No file name, path or CSV text reached the renderer's bridge.
  // Whole words of the member's name, so `exploreProfile` is not taken for a file.
  const named = await page.evaluate(() =>
    Object.keys(window.mosaiq).filter((k) =>
      k.split(/(?=[A-Z])/).some((word) => /^(files?|paths?)$/i.test(word)),
    ),
  );
  expect(named).toEqual(['chooseImportFile']);
  await app.close();
});

test('rejections show line, field and reason; they are filtered, told apart and exported', async () => {
  const { app, page, dir } = await launch('ADMIN');
  await openImports(page);
  await page.getByTestId('review-17').click();
  await expect(rejectionRows(page)).toHaveCount(4);
  const first = rejectionRows(page).first();
  await expect(first.locator('td').nth(0)).toHaveText('4');
  await expect(first.locator('td').nth(1)).toHaveText('quantity');
  await expect(first.locator('td').nth(2)).toHaveText('invalid_quantity');
  await expect(first.locator('td').nth(3)).toContainText('quantity must be a positive whole number');

  // Duplicates in the file and duplicates already recorded are told apart.
  await expect(page.getByTestId('duplicate-summary')).toContainText(
    '1 repeats in the file · 1 already recorded',
  );
  await expect(page.locator('tr[data-line="8"] td').nth(4)).toHaveText('Repeat in the file');
  await expect(page.locator('tr[data-line="8"] td').nth(5)).toHaveText('3');
  await expect(page.locator('tr[data-line="9"] td').nth(4)).toHaveText('Already recorded');
  await expect(page.locator('tr[data-line="9"] td').nth(6)).toHaveText('4790');
  await expect(page.locator('tr[data-line="8"] td').nth(1)).toHaveText('whole row');

  // Filters.
  await page.locator('#filter-code').selectOption('duplicate_row');
  await expect(rejectionRows(page)).toHaveCount(2);
  await page.locator('#filter-kind').selectOption('RECORDED_SALE');
  await expect(rejectionRows(page)).toHaveCount(1);
  await page.locator('#filter-kind').selectOption('');
  await page.locator('#filter-code').selectOption('');
  await page.locator('#filter-field').selectOption('quantity');
  await expect(rejectionRows(page)).toHaveCount(1);
  await page.locator('#filter-field').selectOption('');
  await page.locator('#filter-kind').selectOption('NONE');
  await expect(rejectionRows(page)).toHaveCount(2);
  await page.locator('#filter-kind').selectOption('');

  // Export: the shared action writes the filtered rows on screen, through the save dialog.
  const out = join(mkdtempSync(join(tmpdir(), 'mosaiq-out-')), 'rejections.csv');
  await app.evaluate(({ dialog }, f) => {
    dialog.showSaveDialog = (async () => ({ canceled: false, filePath: f })) as never;
  }, out);
  await page.locator('#filter-code').selectOption('duplicate_row');
  await page.getByTestId('export').click();
  await expect(page.getByTestId('notice')).toContainText('Exported 2 rows');
  const csv = readFileSync(out, 'utf8');
  expect(csv.charCodeAt(0)).toBe(0xfeff);
  expect(csv.split('\r\n')[0]).toBe(
    `${String.fromCharCode(0xfeff)}Line,Field,Code,Reason,Kind,First line,Sale`,
  );
  expect(csv).toContain('Repeat in the file,3,');
  expect(csv).toContain('Already recorded,,4790');
  expect(csv).not.toContain('invalid_quantity');
  const audit = readFileSync(join(dir, 'export-audit.log'), 'utf8')
    .trim()
    .split('\n')
    .map((l) => JSON.parse(l));
  expect(audit).toHaveLength(1);
  expect(audit[0]).toMatchObject({ module: 'imports', rows: 2, role: 'ADMIN' });
  await app.close();
});

test('reconciling a duplicate: open the recorded sale, or follow a line that was repeated', async () => {
  const { app, page } = await launch('ADMIN');
  await openImports(page);
  await page.getByTestId('review-17').click();
  await expect(rejectionRows(page)).toHaveCount(4);

  await page.getByTestId('open-sale-9').click();
  await expect(page.getByTestId('sale-id')).toHaveText('4790');
  await expect(page.getByTestId('sale-total')).toHaveText('203.25');
  await expect(page.getByTestId('sale-store')).not.toHaveText('');
  await expect(page.getByTestId('sale-lines')).toContainText('Item 1007 × 1 at 12.50 = 12.50');
  await expect(page.getByTestId('sale-rejection')).toContainText("The file's line 9");
  await shot(page, '871-desktop-import-03-recorded-sale');

  await page.getByTestId('repeats-8').click();
  await expect(page.getByTestId('repeats-text')).toContainText(
    'Line 3 of the file was accepted and recorded once',
  );
  await expect(page.getByTestId('repeats-text')).toContainText('Lines 8 repeat it');
  await expect(rejectionRows(page)).toHaveCount(1);
  await page.getByTestId('clear-repeats').click();
  await expect(rejectionRows(page)).toHaveCount(1 + 3);
  await app.close();
});

test('text from the service is shown as text', async () => {
  rejectionsBody = example('sales', 'list-sale-import-rejections').replace(
    'the cell holds "0".',
    'the cell holds &quot;&lt;img src=x onerror=&quot;window.pwned=1&quot;&gt;&lt;b&gt;&quot;.',
  );
  const { app, page } = await launch('ADMIN');
  await openImports(page);
  await page.getByTestId('review-17').click();
  await expect(rejectionRows(page)).toHaveCount(4);
  await expect(rejectionRows(page).first().locator('td').nth(3)).toContainText(
    '<img src=x onerror="window.pwned=1"><b>',
  );
  await expect(
    page.locator('[data-testid=rejections-table] img, [data-testid=rejections-table] b'),
  ).toHaveCount(0);
  expect(await page.evaluate(() => 'pwned' in window)).toBe(false);
  await app.close();
});

test('a file with a wrong header is refused in the main process and nothing is sent', async () => {
  const { app, page } = await launch('ADMIN');
  await picks(app, csvFile(salesCsv(5).replace('channel_id', 'channel')));
  await openImports(page);
  await page.getByTestId('import-choose').click();
  await expect(page.getByTestId('error-code')).toHaveText('invalid_file');
  await expect(page.getByTestId('error-message')).toContainText('Column 4 is "channel"');
  await expect(page.getByTestId('error-detail')).toHaveText('unexpected_column');
  await expect(page.getByTestId('import-start')).toBeDisabled();
  expect(sentImportCalls().filter((c) => c.startsWith('POST') || c.startsWith('PUT'))).toEqual([]);
  await app.close();
});

test('a chunk the service cannot take pauses the import, and Resume continues at the chunk it expects', async () => {
  const { app, page } = await launch('ADMIN');
  await picks(app, csvFile(salesCsv(320)));
  await openImports(page);
  await page.getByTestId('import-choose').click();
  await expect(page.getByTestId('import-file-rows')).toHaveText('320');
  unavailable = 3;
  await page.getByTestId('import-start').click();
  await expect(page.getByTestId('import-phase')).toContainText('Paused', { timeout: 30_000 });
  await expect(page.getByTestId('error-code')).toHaveText('database_unavailable');
  await expect(page.getByTestId('import-chunks')).toContainText('Chunks staged: 1 of 3');
  await shot(page, '871-desktop-import-04-paused');
  await page.getByTestId('import-resume').click();
  await expect(page.getByTestId('import-phase')).toHaveText('Finished.', { timeout: 30_000 });
  await expect(page.getByTestId('import-counts')).toHaveText(
    'Received 320 = accepted 316 + rejected 4 + pending 0',
  );
  // Chunk 0 once, then again as a replay that proves it is the same file, then chunks 1 and 2.
  const puts = sentImportCalls().filter((c) => c.includes('/chunks/'));
  expect(puts.filter((c) => c.endsWith('/0'))).toHaveLength(2);
  expect(puts.at(-1)).toBe('PUT /v1/sale-imports/17/chunks/2');
  await app.close();
});

test('cancelling stops the import at the service', async () => {
  const { app, page } = await launch('ADMIN');
  await picks(app, csvFile(salesCsv(320)));
  await openImports(page);
  await page.getByTestId('import-choose').click();
  await expect(page.getByTestId('import-file-rows')).toHaveText('320');
  gate = deferred<void>();
  const held = gate;
  await page.getByTestId('import-start').click();
  await expect(page.getByTestId('import-chunks')).toContainText('Chunks staged: 1 of 3');
  await page.getByTestId('import-cancel').click();
  await expect(page.getByTestId('import-phase')).toHaveText('Finished.');
  await expect(page.getByTestId('import-rows')).toContainText('status CANCELLED');
  held.resolve();
  expect(sentImportCalls()).toContain('PUT /v1/sale-imports/17/status');
  await app.close();
});

for (const as of ['MARKETING', 'STORE_MANAGER', 'ANALYST']) {
  test(`${as} is not offered the module, and a forced call is refused in the main process`, async () => {
    const { app, page } = await launch(as);
    await expect(page.locator('nav.nav button[data-view="imports"]')).toHaveCount(0);
    // The dialog would open a file; it must not even be asked for.
    await app.evaluate(({ dialog }) => {
      dialog.showOpenDialog = (async () => {
        throw new Error('the file dialog was opened');
      }) as never;
    });
    const calls: (() => Promise<unknown>)[] = [
      () => window.mosaiq.chooseImportFile(),
      () => window.mosaiq.startImport(),
      () => window.mosaiq.listImports({}),
      () => window.mosaiq.listImportRejections({ import_id: 17 }),
      () => window.mosaiq.cancelImport(17),
    ];
    for (const call of calls)
      expect(await page.evaluate(call)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(sentImportCalls()).toEqual([]);
    await app.close();
  });
}
