import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

// The audit trail (F16-09, #487), against a fake gateway that answers with the
// recorded examples of contracts/audit-trail/ and records the product edits it
// is given, so the trail names the signed-in user for them.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const AUDIT = 'https://mosaiq.maxthecoder.online/xml/audit-trail/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';
const ENTITIES = [
  'category',
  'product',
  'store',
  'customer',
  'app_user',
  'segment',
  'segment_rule',
  'campaign',
  'experiment',
  'channel',
  'role',
  'customer_segment_history',
  'store_location',
];
const ACTOR = '5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>${ACTOR}</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;
const forbidden = `<Error xmlns="${COMMON}"><code>forbidden_role</code><message>The authenticated role cannot perform this operation.</message><correlation_id>corr-role-001</correlation_id></Error>`;

/** The AuditEntry elements of a recorded list, as text. */
const blocks = (xml: string): string[] => xml.match(/<AuditEntry>[\s\S]*?<\/AuditEntry>/g) ?? [];
const field = (block: string, name: string): string =>
  new RegExp(`<${name}>([^<]*)</${name}>`).exec(block)?.[1] ?? '';
const withNs = (block: string): string => block.replace('<AuditEntry>', `<AuditEntry xmlns="${AUDIT}">`);
const page = (list: string[]): string =>
  `<AuditEntries xmlns="${AUDIT}" xmlns:common="${COMMON}">${list.join('')}<common:PageInfo><common:page>1</common:page><common:size>20</common:size><common:total>${list.length}</common:total></common:PageInfo></AuditEntries>`;

interface Seen {
  method: string;
  url: string;
}

let server: Server;
let base = '';
let role = 'AUDITOR';
let seen: Seen[] = [];
let recorded: string[] = [];

const entries = (): string[] => [...recorded, ...blocks(example('audit-trail', 'audit-entries'))];

function reset(): void {
  seen = [];
  recorded = [];
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    req.resume();
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      seen.push({ method, url: url.toString() });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      // A product edit: the service's trigger records it, attributed to the caller.
      const product = /^\/api\/products\/v1\/products\/(\d+)$/.exec(path);
      if (product && method === 'PATCH') {
        recorded.unshift(
          `<AuditEntry><audit_id>2001</audit_id><user_id>${ACTOR}</user_id><entity>product</entity><entity_pk>${product[1]}</entity_pk><action>UPDATE</action><data_before>{"name":"Old name","product_id":${product[1]}}</data_before><data_after>{"name":"New name","product_id":${product[1]}}</data_after><executed_at>2026-10-20T10:00:00.000Z</executed_at></AuditEntry>`,
        );
        return reply(200, example('products', 'update-product'));
      }
      if (!path.startsWith('/api/audit-trail/v1/audit-entries'))
        return reply(404, example('audit-trail', 'audit-entry-not-found'));
      if (role !== 'ADMIN' && role !== 'AUDITOR') return reply(403, forbidden);
      const one = /\/audit-entries\/(\d+)$/.exec(path);
      if (one) {
        const found = entries().find((b) => field(b, 'audit_id') === one[1]);
        return found
          ? reply(200, withNs(found))
          : reply(404, example('audit-trail', 'audit-entry-not-found'));
      }
      const q = url.searchParams;
      const entity = q.get('entity');
      const [from, to] = [q.get('from'), q.get('to')];
      if ((entity !== null && !ENTITIES.includes(entity)) || (from && to && to < from))
        return reply(422, example('audit-trail', 'invalid-query'));
      const list = entries().filter(
        (b) =>
          (entity === null || field(b, 'entity') === entity) &&
          (q.get('entity_pk') === null || field(b, 'entity_pk') === q.get('entity_pk')) &&
          (q.get('user_id') === null || field(b, 'user_id') === q.get('user_id')) &&
          (q.get('action') === null || field(b, 'action') === q.get('action')),
      );
      return reply(200, page(list));
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
  const window = await app.firstWindow();
  await window.waitForSelector('form');
  await window.fill('#f-email', 'a@b.c');
  await window.fill('#f-password', 'pw');
  await window.click('button[type=submit]');
  await expect(window.getByTestId('role')).toHaveText(`Signed in as ${as}`);
  return { app, page: window };
}

const open = (p: Page) => p.locator('nav.nav button[data-view="audit"]').click();

for (const as of ['ADMIN', 'AUDITOR']) {
  test(`${as} lists, filters and opens entries; the screen has no write control`, async () => {
    const { app, page: p } = await launch(as);
    await open(p);
    await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(4);
    expect(seen.some((s) => s.url.includes('/audit-entries?') && s.url.includes('size=20'))).toBe(true);

    await p.locator('#filter-entity').selectOption('category');
    await p.getByRole('button', { name: 'Search' }).click();
    await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(2);
    await expect(p.getByTestId('audit-user-1196')).toHaveText('no known actor');

    await p.getByTestId('view-audit-1196').click();
    await expect(p.getByTestId('audit-entity')).toHaveText('category');
    await expect(p.getByTestId('audit-action')).toHaveText('DELETE');
    await expect(p.getByTestId('audit-after')).toHaveText('none');
    // Formatted JSON text in a <pre>.
    await expect(p.getByTestId('audit-before')).toContainText('"name": "Seasonal"');
    expect(await p.locator('[data-testid=audit-before]').evaluate((n) => n.tagName)).toBe('PRE');

    // Append-only: nothing to create, edit or delete, and the bridge has no such operation.
    const screen = p.locator('div:has(> h2:text-is("Audit trail"))');
    await expect(screen.locator('textarea')).toHaveCount(0);
    const labels = await screen.locator('button').allTextContents();
    expect(labels).toContain('Search');
    expect(labels.filter((t) => /create|add|new|edit|save|delete|remove|void|update/i.test(t))).toEqual([]);
    const bridge = await p.evaluate(() => Object.keys(window.mosaiq));
    expect(bridge.filter((k) => /audit/i.test(k)).sort()).toEqual(['getAuditEntry', 'listAuditEntries']);
    expect(seen.filter((s) => s.method !== 'GET' && s.url.includes('audit'))).toEqual([]);
    await app.close();
  });
}

test('an unknown id is audit_entry_not_found, shown as returned; an invalid filter is invalid_query', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await open(p);
  await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(4);
  const unknown = await p.evaluate(() => window.mosaiq.getAuditEntry('999999'));
  expect(unknown).toMatchObject({
    ok: false,
    error: { code: 'audit_entry_not_found', correlation_id: 'corr-audit-404' },
  });
  // The main process refuses a malformed id before anything is sent.
  const before = seen.length;
  const bad = await p.evaluate(() => window.mosaiq.getAuditEntry('1/../2'));
  expect(bad).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
  const badEntity = await p.evaluate(() => window.mosaiq.listAuditEntries({ entity: 'nope' }));
  expect(badEntity).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
  expect(seen).toHaveLength(before);

  // A refusal by the service (here, `to` before `from`) reaches the screen with its detail.
  await p.locator('#filter-from').fill('2026-10-17T00:00:00Z');
  await p.locator('#filter-to').fill('2026-10-16T00:00:00Z');
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.getByTestId('error-code')).toHaveText('invalid_query');
  await expect(p.getByTestId('error-correlation')).toHaveText('corr-audit-422');
  await expect(p.getByTestId('error-detail')).toHaveText('enum');
  await app.close();
});

test('any other staff role is not offered the screen; a forced call is forbidden_role', async () => {
  const { app, page: p } = await launch('MARKETING');
  await expect(p.locator('nav.nav button[data-view="audit"]')).toHaveCount(0);
  const forced = await p.evaluate(() => window.mosaiq.listAuditEntries({}));
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  const forcedOne = await p.evaluate(() => window.mosaiq.getAuditEntry('1198'));
  expect(forcedOne).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  await app.close();
});

test('a forbidden_role answer is shown as a message on the screen', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await open(p);
  await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(4);
  role = 'MARKETING'; // the role changed on the server after sign-in
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.getByTestId('error-code')).toHaveText('forbidden_role');
  await expect(p.getByTestId('error-correlation')).toHaveText('corr-role-001');
  await app.close();
});

test('no password or password hash appears on a user entry', async () => {
  const { app, page: p } = await launch('ADMIN');
  await open(p);
  await p.locator('#filter-entity').selectOption('app_user');
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(2);
  for (const id of ['1198', '1197']) {
    await p.getByTestId(`view-audit-${id}`).click();
    await expect(p.getByTestId('audit-entity-pk')).toHaveText('4f6c1d52-8a3e-4b17-9c20-5d1e7a9b3c64');
    await expect(p.getByTestId('audit-action')).toHaveText(id === '1198' ? 'UPDATE' : 'INSERT');
    const text = (await p.locator('div:has(> h2:text-is("Audit trail"))').innerText()).toLowerCase();
    expect(text).not.toContain('password');
    expect(text).not.toContain('hash');
  }
  // The recorded list itself carries none of them either.
  for (const name of ['audit-entries', 'audit-entry-app-user-update', 'audit-entry-insert']) {
    expect(example('audit-trail', name).toLowerCase()).not.toMatch(/password/);
  }
  await app.close();
});

test('a product edited in the desktop client appears in the trail and names the acting user', async () => {
  const { app, page: p } = await launch('ADMIN');
  const edit = await p.evaluate(() => window.mosaiq.updateProduct(1001, { name: 'New name' }));
  expect(edit.ok).toBe(true);
  await open(p);
  await p.locator('#filter-entity').selectOption('product');
  await p.locator('#filter-entity_pk').fill('1001');
  await p.locator('#filter-action').selectOption('UPDATE');
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(1);
  await expect(p.getByTestId('audit-user-2001')).toHaveText(ACTOR);
  const query = seen.filter((s) => s.url.includes('/audit-entries?')).at(-1)!;
  expect(query.url).toContain('entity=product');
  expect(query.url).toContain('entity_pk=1001');
  await p.getByTestId('view-audit-2001').click();
  await expect(p.getByTestId('audit-user')).toHaveText(ACTOR);
  await expect(p.getByTestId('audit-after')).toContainText('"name": "New name"');
  await app.close();
});
