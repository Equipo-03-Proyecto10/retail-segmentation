import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parse } from 'yaml';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { eventsPage, field as xmlField, read as readEvent, recordedEvents } from '../support/domain-events';
import { shot } from '../support/evidence';

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
const AUDITOR_ID = '11111111-1111-1111-1111-000000000006';
const ADMIN_ID = '11111111-1111-1111-1111-000000000001';
/** An event the fake answers outside its contract: a credential among the keys of its wrapper. */
const DEFECT_ID = '00000000-0000-4000-8000-000000000bad';
/** The service's refusal of a `to` before `from`: the contract's error envelope, as audit-entries uses it. */
const BEFORE_FROM = `<Error xmlns="${COMMON}"><code>invalid_query</code><message>A query parameter is not an allowed value.</message><correlation_id>corr-events-422</correlation_id><details><field>to</field><code>before_from</code></details></Error>`;

/** The contract's own example of the 503 a read answers when its AUDIT_READ cannot be recorded. */
const auditUnavailable = (
  parse(readFileSync(join(CONTRACTS, 'audit-trail', 'openapi.yaml'), 'utf8')) as {
    components: {
      responses: Record<string, { content: Record<string, { examples: Record<string, { value: string }> }> }>;
    };
  }
).components.responses.RecordedReadUnavailable!.content['application/xml']!.examples.audit_unavailable!.value;

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
let unrecordable = false;

const entries = (): string[] => [...recorded, ...blocks(example('audit-trail', 'audit-entries'))];

function reset(): void {
  seen = [];
  recorded = [];
  unrecordable = false;
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
      if (path.startsWith('/api/audit-trail/v1/domain-events')) {
        if (role !== 'ADMIN' && role !== 'AUDITOR') return reply(403, forbidden);
        // A read the service cannot record is not served (ADR-0072 section 4).
        if (unrecordable) return reply(503, auditUnavailable);
        const events = recordedEvents();
        const one = /\/domain-events\/([0-9a-f-]{36})$/.exec(path);
        if (one) {
          if (one[1] === DEFECT_ID) return reply(200, readEvent('domain-event-credential.invalid'));
          const found = events.find((e) => e.id === one[1]);
          return found ? reply(200, found.document) : reply(404, readEvent('domain-event-not-found'));
        }
        const e = url.searchParams;
        const [since, until] = [e.get('from'), e.get('to')];
        if (since && until && Date.parse(until) < Date.parse(since)) return reply(422, BEFORE_FROM);
        const listed = events.filter((ev) => {
          const at = Date.parse(ev.recordedAt);
          return (
            (e.get('kind') === null || ev.kind === e.get('kind')) &&
            (e.get('actor_user_id') === null ||
              xmlField(ev.document, 'actor_user_id') === e.get('actor_user_id')) &&
            (e.get('actor_role') === null || xmlField(ev.document, 'actor_role') === e.get('actor_role')) &&
            (e.get('subject_type') === null || ev.subjectType === e.get('subject_type')) &&
            (e.get('outcome') === null || xmlField(ev.document, 'outcome') === e.get('outcome')) &&
            (since === null || at >= Date.parse(since)) &&
            (until === null || at <= Date.parse(until))
          );
        });
        return reply(200, eventsPage(listed.map((ev) => ev.element)));
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
    await shot(p, '487-desktop-audit-01-list');
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
    await shot(p, '487-desktop-audit-02-entry-detail');

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
  await shot(p, '487-desktop-audit-03-invalid-query');
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

// The decisions and versions of the trail (F28-15, #884, ADR-0072): model versions, calculations,
// permissions, critical acts and reads of the trail, found by kind, actor and date. The fake answers
// with the contract's recorded events, filtered as the service filters them.

const TOTAL = recordedEvents().length;
const openEvents = async (p: Page): Promise<void> => {
  await open(p);
  await p.getByTestId('audit-tab-events').click();
  await expect(p.locator('[data-testid=events-table] tbody tr')).toHaveCount(TOTAL);
};
const rows = (p: Page) => p.locator('[data-testid=events-table] tbody tr');
const eventsRequests = (): string[] => seen.map((s) => s.url).filter((u) => u.includes('/domain-events'));
const idOf = (kind: string, subjectType?: string): string =>
  recordedEvents().find(
    (e) => e.kind === kind && (subjectType === undefined || e.subjectType === subjectType),
  )!.id;

for (const as of ['ADMIN', 'AUDITOR']) {
  test(`${as} finds decisions by kind, actor and date, and opens one; the list has no write control`, async () => {
    const { app, page: p } = await launch(as);
    await openEvents(p);
    await shot(p, '884-desktop-audit-01-decisions');
    expect(eventsRequests().at(-1)).toContain('size=20');
    // Who: the auditor's own read of the trail.
    await p.locator('#filter-actor_user_id').fill(AUDITOR_ID);
    await p.getByRole('button', { name: 'Search' }).click();
    await expect(rows(p)).toHaveCount(1);
    await expect(rows(p).first()).toContainText('AUDIT_READ');
    expect(eventsRequests().at(-1)).toContain(`actor_user_id=${AUDITOR_ID}`);

    // What and when: one administrator's decisions on the seventh.
    await p.locator('#filter-actor_user_id').fill(ADMIN_ID);
    await p.locator('#filter-from').fill('2026-11-07T00:00:00Z');
    await p.locator('#filter-to').fill('2026-11-07T23:59:59Z');
    await p.getByRole('button', { name: 'Search' }).click();
    await expect(rows(p)).toHaveCount(3);
    const query = eventsRequests().at(-1)!;
    expect(query).toContain('from=2026-11-07T00%3A00%3A00Z');
    expect(query).toContain('to=2026-11-07T23%3A59%3A59Z');
    await p.locator('#filter-kind').selectOption('PERMISSION_CHANGED');
    await p.getByRole('button', { name: 'Search' }).click();
    await expect(rows(p)).toHaveCount(2);
    await shot(p, '884-desktop-audit-02-decisions-filtered');

    // An owner and a version: the rollback to model version 11 by the administrator.
    await p.locator('#filter-kind').selectOption('MODEL_ROLLED_BACK');
    await p.getByRole('button', { name: 'Search' }).click();
    await expect(rows(p)).toHaveCount(1);
    const id = idOf('MODEL_ROLLED_BACK');
    await expect(p.getByTestId(`event-actor-${id}`)).toHaveText(`ADMIN ${ADMIN_ID}`);
    await expect(p.getByTestId(`event-version-${id}`)).toHaveText('model 11');
    await p.getByTestId(`view-event-${id}`).click();
    await expect(p.getByTestId('event-kind')).toHaveText('MODEL_ROLLED_BACK');
    await expect(p.getByTestId('event-actor')).toHaveText(`ADMIN ${ADMIN_ID}`);
    await expect(p.getByTestId('event-source')).toHaveText('svc_models');
    await expect(p.getByTestId('event-version')).toHaveText('model 11');
    await expect(p.getByTestId('event-subject')).toHaveText('model_version 11');
    await expect(p.getByTestId('event-detail-previous_version_id')).toHaveText('12');
    await expect(p.getByTestId('event-detail-reason_code')).toHaveText('DRIFT_ALERT');
    await expect(p.getByTestId('event-entry-hash')).toHaveText(/^[0-9a-f]{64}$/);
    await shot(p, '884-desktop-audit-03-decision-detail');

    // Append-only: the screen offers nothing to create, edit or delete, and no write reaches the service.
    const screen = p.locator('div:has(> h2:text-is("Audit trail"))');
    const labels = await screen.locator('button').allTextContents();
    expect(labels.filter((t) => /create|add|new|edit|save|delete|remove|void|update/i.test(t))).toEqual([]);
    expect(seen.filter((s) => s.method !== 'GET' && s.url.includes('audit'))).toEqual([]);
    await app.close();
  });
}

test('the four shortcuts fill in the filters and find the decisions of the story', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await open(p);
  // Model versions: both kinds that concern a version, found by what they are about.
  await p.getByTestId('audit-topic-model-versions').click();
  await expect(rows(p)).toHaveCount(2);
  await expect(p.locator('#filter-subject_type')).toHaveValue('model_version');
  await expect(p.locator('#filter-kind')).toHaveValue('');
  expect(eventsRequests().at(-1)).toContain('subject_type=model_version');
  const kinds = await p.locator('[data-testid=events-table] tbody tr td:nth-child(2)').allTextContents();
  expect(kinds.sort()).toEqual(['MODEL_PUBLISHED', 'MODEL_ROLLED_BACK']);
  await shot(p, '884-desktop-audit-04-model-versions');

  // Campaign decisions.
  await p.getByTestId('audit-topic-campaign-decisions').click();
  await expect(rows(p)).toHaveCount(1);
  await expect(p.locator('#filter-kind')).toHaveValue('CRITICAL_ACTION');
  await expect(p.locator('#filter-subject_type')).toHaveValue('campaign');
  expect(eventsRequests().at(-1)).toContain('kind=CRITICAL_ACTION&subject_type=campaign');
  await p.getByTestId(`view-event-${idOf('CRITICAL_ACTION', 'campaign')}`).click();
  await expect(p.getByTestId('event-subject')).toHaveText('campaign 12');
  await expect(p.getByTestId('event-detail-action')).toHaveText('CAMPAIGN_ACTIVATED');
  await expect(p.getByTestId('event-actor')).toHaveText('MARKETING 11111111-1111-1111-1111-000000000004');

  // Permissions, then narrowed by date from the same filters.
  await p.getByTestId('audit-topic-permissions').click();
  await expect(rows(p)).toHaveCount(2);
  await expect(p.locator('#filter-kind')).toHaveValue('PERMISSION_CHANGED');
  await p.locator('#filter-from').fill('2026-11-07T10:06:00Z');
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(rows(p)).toHaveCount(1);
  await rows(p).first().getByRole('button', { name: 'View' }).click();
  await expect(p.getByTestId('event-detail-change')).toHaveText('STORES_ASSIGNED');
  await expect(p.getByTestId('event-detail-store_id')).toHaveText('4, 9');
  await shot(p, '884-desktop-audit-05-permissions');

  // Rule changes are row changes of a rule (RN-28): the shortcut switches to that list, filtered.
  recorded.unshift(
    `<AuditEntry><audit_id>2002</audit_id><user_id>${ADMIN_ID}</user_id><entity>segment_rule</entity><entity_pk>14</entity_pk><action>UPDATE</action><data_before>{"min_value":3}</data_before><data_after>{"min_value":4}</data_after><executed_at>2026-11-06T10:00:00.000Z</executed_at></AuditEntry>`,
  );
  await p.getByTestId('audit-topic-rule-changes').click();
  await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(1);
  await expect(p.locator('#filter-entity')).toHaveValue('segment_rule');
  await expect(p.getByTestId('audit-user-2002')).toHaveText(ADMIN_ID);
  expect(seen.filter((s) => s.url.includes('/audit-entries?')).at(-1)!.url).toContain('entity=segment_rule');
  await p.locator('#filter-user_id').fill(AUDITOR_ID);
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.locator('[data-testid=audit-table] tbody tr')).toHaveCount(0);
  await shot(p, '884-desktop-audit-06-rule-changes');
  await app.close();
});

test('a role other than ADMIN or AUDITOR has no way into the module; a forced call never leaves the main process', async () => {
  const { app, page: p } = await launch('MARKETING');
  await expect(p.locator('nav.nav button[data-view="audit"]')).toHaveCount(0);
  await expect(p.getByTestId('audit-tabs')).toHaveCount(0);
  const before = seen.length;
  const calls = await p.evaluate(async (id) => {
    return [
      await window.mosaiq.listDomainEvents({}),
      await window.mosaiq.getDomainEvent(id),
      await window.mosaiq.listAuditEntries({}),
      await window.mosaiq.getAuditEntry('1198'),
    ];
  }, idOf('AUDIT_READ'));
  for (const r of calls) expect(r).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  // Refused in the main process: the gateway saw none of the four.
  expect(seen).toHaveLength(before);
  expect(eventsRequests()).toEqual([]);
  await app.close();
});

test('the main process refuses a malformed event filter before anything is sent', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await openEvents(p);
  const before = seen.length;
  for (const q of [
    { kind: 'nope' },
    { actor_user_id: 'x' },
    { model_version_id: '0' },
    { from: 'yesterday' },
    { subject_type: 'CAMPAIGN' },
    { source: 'models' },
  ])
    expect(await p.evaluate((query) => window.mosaiq.listDomainEvents(query), q)).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
  expect(await p.evaluate(() => window.mosaiq.getDomainEvent('1/../2'))).toMatchObject({
    ok: false,
    error: { code: 'invalid_input' },
  });
  expect(seen).toHaveLength(before);
  await app.close();
});

test('a subject id without its subject is refused on the screen, naming the field, and nothing is sent', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await openEvents(p);
  const before = eventsRequests().length;
  await p.locator('#filter-subject_id').fill('12');
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.getByTestId('error-code')).toHaveText('invalid_input');
  await expect(p.getByTestId('error-detail')).toHaveText('requires_subject_type');
  expect(eventsRequests()).toHaveLength(before);
  // With its subject it is sent, and finds the activation of campaign 12.
  await p.locator('#filter-subject_type').selectOption('campaign');
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(rows(p)).toHaveCount(1);
  expect(eventsRequests().at(-1)).toContain('subject_type=campaign&subject_id=12');
  await app.close();
});

test('a refusal by the service reaches the decisions screen with its detail', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await openEvents(p);
  await p.locator('#filter-from').fill('2026-11-07T00:00:00Z');
  await p.locator('#filter-to').fill('2026-11-06T00:00:00Z');
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.getByTestId('error-code')).toHaveText('invalid_query');
  await expect(p.getByTestId('error-correlation')).toHaveText('corr-events-422');
  await expect(p.getByTestId('error-detail')).toHaveText('before_from');
  await shot(p, '884-desktop-audit-07-decisions-invalid-query');

  // An unknown event is domain_event_not_found, as returned.
  const unknown = await p.evaluate(() =>
    window.mosaiq.getDomainEvent('3a5c8d1e-0b47-5c2f-9e6a-1d4b7f2c8e90'),
  );
  expect(unknown).toMatchObject({
    ok: false,
    error: { code: 'domain_event_not_found', correlation_id: 'corr-events-404' },
  });

  // The role changed on the server after sign-in: forbidden_role is shown as a message.
  role = 'MARKETING';
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.getByTestId('error-code')).toHaveText('forbidden_role');
  await expect(p.getByTestId('error-correlation')).toHaveText('corr-role-001');
  await app.close();
});

test('a read the service could not record is shown as audit_unavailable, with no event', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await openEvents(p);
  unrecordable = true;
  await p.getByRole('button', { name: 'Search' }).click();
  await expect(p.getByTestId('error-code')).toHaveText('audit_unavailable');
  await expect(p.getByTestId('error-correlation')).toHaveText('corr-dependency-503c');
  await expect(p.getByTestId('events-table')).toHaveCount(0);
  await shot(p, '884-desktop-audit-08-audit-unavailable');
  await app.close();
});

test('an event the service sends outside its contract is a defect, and none of it is shown', async () => {
  const { app, page: p } = await launch('ADMIN');
  await openEvents(p);
  const outside = await p.evaluate((id) => window.mosaiq.getDomainEvent(id), DEFECT_ID);
  expect(outside).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
  expect(JSON.stringify(outside).toLowerCase()).not.toMatch(/password|argon2/);
  // Nothing the trail shows, in either list, carries a credential or a token.
  for (const tab of ['audit-tab-events', 'audit-tab-rows']) {
    await p.getByTestId(tab).click();
    const text = (await p.locator('div:has(> h2:text-is("Audit trail"))').innerText()).toLowerCase();
    expect(text).not.toMatch(/password|argon2|token|secret|credential/);
  }
  for (const e of recordedEvents())
    expect(e.document.toLowerCase()).not.toMatch(/password|argon2|token|secret/);
  await app.close();
});

test('the decisions fit a narrow and a wide window, with the table in its own scroll area', async () => {
  const { app, page: p } = await launch('AUDITOR');
  await openEvents(p);
  await p.getByTestId(`view-event-${idOf('MODEL_ROLLED_BACK')}`).click();
  await expect(p.getByTestId('event-entry-hash')).toHaveText(/^[0-9a-f]{64}$/);
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
    await expect.poll(async () => p.evaluate(() => window.innerWidth)).toBeLessThanOrEqual(width);
    const fits = await p.evaluate(() => {
      const scroll = document.querySelector('[data-testid=events-table]')!.parentElement!;
      const hash = document.querySelector('[data-testid=event-entry-hash]') as HTMLElement;
      return {
        page: document.documentElement.scrollWidth <= window.innerWidth + 1,
        table: scroll.getBoundingClientRect().right <= window.innerWidth + 1,
        scrolls: getComputedStyle(scroll).overflowX === 'auto',
        hash: hash.getBoundingClientRect().right <= window.innerWidth + 1,
      };
    });
    expect(fits, `${width}px`).toEqual({ page: true, table: true, scrolls: true, hash: true });
    await p.getByTestId('events-table').scrollIntoViewIfNeeded();
    await shot(p, `884-desktop-audit-09-${width}px`);
  }
  await app.close();
});
