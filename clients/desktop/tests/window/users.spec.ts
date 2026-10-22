import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';

// User administration, the account menu and the administrator transfer (F16-06,
// #484), against a fake gateway that answers with the recorded examples of
// contracts/users/ and keeps the users it creates, changes and hands over.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const USERS = 'https://mosaiq.maxthecoder.online/xml/users/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';
const SUB = '5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33';
const ALEX = '22222222-2222-4222-8222-222222222222';
const MORGAN = '33333333-3333-4333-8333-333333333333';
const INACTIVE = '44444444-4444-4444-8444-444444444444';
const CURRENT = 'placeholder-current-password-123';
const NEW_PASSWORD = 'placeholder-new-password-456';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>${SUB}</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

interface Account {
  id: string;
  name: string;
  email: string;
  role: string;
  active: boolean;
}
const initial = (): Account[] => [
  { id: SUB, name: 'Ada Admin', email: 'ada.admin@example.com', role: 'ADMIN', active: true },
  { id: ALEX, name: 'Alex Analyst', email: 'alex.analyst@example.com', role: 'ANALYST', active: true },
  { id: MORGAN, name: 'Mia Marketing', email: 'mia.marketing@example.com', role: 'MARKETING', active: true },
  { id: INACTIVE, name: 'Ian Inactive', email: 'ian.inactive@example.com', role: 'AUDITOR', active: false },
];

const userXml = (a: Account, root = 'User'): string =>
  `<${root}><user_id>${a.id}</user_id><name>${a.name}</name><email>${a.email}</email><role_code>${a.role}</role_code><is_active>${a.active}</is_active><created_at>2026-10-15T15:00:00Z</created_at></${root}>`;
const oneUser = (a: Account): string => userXml(a).replace('<User>', `<User xmlns="${USERS}">`);
const usersPage = (list: Account[]): string =>
  `<Users xmlns="${USERS}" xmlns:common="${COMMON}">${list.map((a) => userXml(a)).join('')}<common:PageInfo><common:page>1</common:page><common:size>20</common:size><common:total>${list.length}</common:total></common:PageInfo></Users>`;

interface Seen {
  method: string;
  url: string;
  body: string;
}
interface Refusal {
  service: string;
  file: string;
  status: number;
  headers?: Record<string, string>;
}

let server: Server;
let base = '';
let role = 'ADMIN';
let seen: Seen[] = [];
let db: Account[] = initial();
let nextRefusal: Refusal | null = null;
let revoked = false;

function reset(): void {
  seen = [];
  db = initial();
  nextRefusal = null;
  revoked = false;
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
      seen.push({ method, url: url.toString(), body });
      if (method === 'POST' && path === '/api/auth/v1/sessions') {
        revoked = false;
        return reply(201, tokens);
      }
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (revoked) return reply(401, example('stores', 'session-revoked'));
      const field = (name: string): string => new RegExp(`<${name}>([^<]*)</${name}>`).exec(body)?.[1] ?? '';
      if (method !== 'GET' && role !== 'ADMIN' && !path.endsWith('/password'))
        return reply(403, example('stores', 'forbidden-role'));
      if (method !== 'GET' && nextRefusal !== null) {
        const r = nextRefusal;
        nextRefusal = null;
        return reply(r.status, example(r.service, r.file), r.headers);
      }

      if (path === '/api/users/v1/roles') return reply(200, example('users', 'roles'));
      if (path === '/api/users/v1/users' && method === 'GET') {
        const wanted = url.searchParams;
        const q = (wanted.get('q') ?? '').toLowerCase();
        return reply(
          200,
          usersPage(
            db.filter(
              (a) =>
                (!wanted.get('role') || a.role === wanted.get('role')) &&
                (!wanted.get('active') || String(a.active) === wanted.get('active')) &&
                (q === '' || `${a.name} ${a.email}`.toLowerCase().includes(q)),
            ),
          ),
        );
      }
      if (path === '/api/users/v1/users' && method === 'POST') {
        if (field('role_code') === 'ADMIN') return reply(409, example('users', 'single-administrator'));
        const made = {
          id: MORGAN.replace('3333', '5555').replace(/3/g, '5'),
          name: field('name'),
          email: field('email'),
          role: field('role_code'),
          active: true,
        };
        db.push(made);
        return reply(201, oneUser(made));
      }
      const user = /^\/api\/users\/v1\/users\/([^/]+)(\/.*)?$/.exec(path);
      if (user) {
        const account = db.find((a) => a.id === user[1]);
        if (!account) return reply(404, example('users', 'user-not-found'));
        if (user[2] === undefined && method === 'GET') return reply(200, oneUser(account));
        if (user[2] === undefined && method === 'PATCH') {
          const role_code = field('role_code');
          if (role_code === 'ADMIN' || (account.role === 'ADMIN' && role_code !== ''))
            return reply(409, example('users', 'use-administrator-transfer'));
          if (field('name')) account.name = field('name');
          if (field('email')) account.email = field('email');
          if (role_code) account.role = role_code;
          return reply(200, oneUser(account));
        }
        if (user[2] === '/deactivation') {
          if (method === 'POST' && account.role === 'ADMIN')
            return reply(409, example('users', 'administrator-required'));
          account.active = method !== 'POST';
          return reply(200, oneUser(account));
        }
        if (user[2] === '/password' && method === 'PUT') {
          if (account.id === SUB && field('current_password') !== CURRENT)
            return reply(422, example('users', 'invalid-current-password'));
          if (account.id === SUB) revoked = true;
          return reply(204);
        }
      }
      if (path === '/api/users/v1/administrator-transfers' && method === 'POST') {
        const successor = db.find((a) => a.id === field('successor_user_id'));
        const incumbent = db.find((a) => a.role === 'ADMIN')!;
        if (!successor || successor === incumbent) return reply(422, example('users', 'invalid-successor'));
        if (!successor.active) return reply(409, example('users', 'successor-inactive'));
        incumbent.role = successor.role;
        successor.role = 'ADMIN';
        // The service ends both sessions; the signed-in app is the former administrator's.
        revoked = true;
        role = 'ADMIN';
        return reply(204);
      }
      reply(404, example('users', 'user-not-found'));
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(reset);

async function signIn(page: Page, as: string): Promise<void> {
  role = as;
  await page.waitForSelector('form');
  await page.fill('#f-email', 'a@b.c');
  await page.fill('#f-password', 'pw');
  await page.click('button[type=submit]');
  await expect(page.getByTestId('role')).toHaveText(`Signed in as ${as}`);
}

async function launch(as: string): Promise<{ app: ElectronApplication; page: Page }> {
  const dir = mkdtempSync(join(tmpdir(), 'mosaiq-desktop-'));
  writeFileSync(join(dir, 'settings.json'), JSON.stringify({ baseUrl: base }));
  const app = await electron.launch({ args: ['.'], env: { ...process.env, MOSAIQ_DESKTOP_CONFIG_DIR: dir } });
  const page = await app.firstWindow();
  await signIn(page, as);
  return { app, page };
}

const nav = (page: Page) =>
  page.locator('nav.nav button').evaluateAll((bs) => bs.map((b) => (b as HTMLElement).dataset.view));
const open = (page: Page, name: string) => page.locator(`nav.nav button[data-view="${name}"]`).click();
const last = (suffix: string): Seen => seen.filter((s) => s.url.endsWith(suffix)).at(-1)!;

test('the users screen lists, filters and opens a user in XML; roles come from users-service', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'users');
  await expect(page.locator('[data-testid=users-table] tbody tr')).toHaveCount(4);
  await expect(page.locator('#filter-role option')).toHaveCount(8);

  await page.selectOption('#filter-role', 'ANALYST');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.locator('[data-testid=users-table] tbody tr')).toHaveCount(1);
  expect(seen.filter((s) => s.url.includes('/users?')).at(-1)!.url).toContain('role=ANALYST');

  await page.selectOption('#filter-role', '');
  await page.selectOption('#filter-active', 'false');
  await page.fill('#filter-q', 'ian');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.locator('[data-testid=users-table] tbody tr')).toHaveCount(1);
  const query = new URL(seen.filter((s) => s.url.includes('/users?')).at(-1)!.url).searchParams;
  expect(Object.fromEntries(query)).toMatchObject({ active: 'false', q: 'ian' });

  await page.getByTestId(`view-user-${INACTIVE}`).click();
  await expect(page.getByTestId('user-name')).toHaveText('Ian Inactive');
  await expect(page.getByTestId('user-status')).toHaveText('Inactive');
  await shot(page, '484-desktop-users-01-list-and-detail');
  expect(seen.some((s) => s.url.endsWith(`/users/${INACTIVE}`) && s.method === 'GET')).toBe(true);
  // Accounts are deactivated, never deleted.
  await expect(page.getByRole('button', { name: /delete|remove/i })).toHaveCount(0);
  await app.close();
});

test('an ADMIN creates a user: the picker has no ADMIN, and the refusals show as returned', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'users');
  await page.getByTestId('new-user').click();
  await expect(page.locator('[data-testid=user-new] #c-role_code option')).toHaveText([
    'ANALYST',
    'STORE_MANAGER',
    'MARKETING',
    'INVENTORY_PLANNER',
    'AUDITOR',
    'CUSTOMER',
  ]);

  // A short password and a bad email are refused by the schema's facets, before sending.
  await page.fill('#c-name', 'Morgan Manager');
  await page.fill('#c-email', 'not-an-email');
  await page.fill('#c-password', 'short');
  await page.getByRole('button', { name: 'Create user' }).click();
  await expect(page.getByTestId('detail-email')).toHaveText('email: invalid_format');
  await expect(page.getByTestId('detail-password')).toHaveText('password: min_length');
  expect(seen.some((s) => s.method === 'POST' && s.url.endsWith('/users'))).toBe(false);

  // email_taken from the service, shown as returned.
  nextRefusal = { service: 'users', file: 'email-taken', status: 409 };
  await page.fill('#c-email', 'morgan.manager@example.com');
  await page.fill('#c-password', NEW_PASSWORD);
  await page.selectOption('#c-role_code', 'STORE_MANAGER');
  await page.getByRole('button', { name: 'Create user' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('email_taken');
  // What was typed stays; the password is never put back.
  await expect(page.locator('#c-name')).toHaveValue('Morgan Manager');
  await expect(page.locator('#c-password')).toHaveValue('');

  // 422 invalid_body with its details next to the fields.
  nextRefusal = { service: 'users', file: 'invalid-body', status: 422 };
  await page.fill('#c-password', NEW_PASSWORD);
  await page.getByRole('button', { name: 'Create user' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('invalid_body');
  await expect(page.getByTestId('detail-email')).toHaveText('email: invalid_format');
  await expect(page.getByTestId('detail-password')).toHaveText('password: min_length');

  await page.fill('#c-password', NEW_PASSWORD);
  await page.getByRole('button', { name: 'Create user' }).click();
  await expect(page.getByTestId('notice')).toHaveText('User created.');
  await expect(page.getByTestId('user-role')).toHaveText('STORE_MANAGER');
  await expect(page.locator('[data-testid=users-table] tbody tr')).toHaveCount(5);
  const post = seen.filter((s) => s.method === 'POST' && s.url.endsWith('/users')).at(-1)!;
  expect(post.body).toContain(`<password>${NEW_PASSWORD}</password>`);
  await expect(page.locator('body')).not.toContainText(NEW_PASSWORD);

  // A forced ADMIN gets 409 single_administrator, shown as returned.
  const forced = await page.evaluate(
    (p) =>
      window.mosaiq.createUser({
        name: 'Second',
        email: 'second@example.com',
        role_code: 'ADMIN',
        password: p,
      }),
    NEW_PASSWORD,
  );
  expect(forced).toMatchObject({ ok: false, error: { code: 'single_administrator' } });
  await app.close();
});

test('an ADMIN edits name, email and a non-ADMIN role; a forced ADMIN change points to the transfer', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'users');
  await page.getByTestId(`view-user-${ALEX}`).click();
  await expect(page.locator('[data-testid=user-edit] #c-role_code option')).toHaveCount(6);
  await page.fill('[data-testid=user-edit] #c-name', 'Alex Senior Analyst');
  await page.fill('[data-testid=user-edit] #c-email', 'alex.senior@example.com');
  await page.selectOption('[data-testid=user-edit] #c-role_code', 'MARKETING');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('notice')).toHaveText('User saved.');
  await expect(page.getByTestId('user-role')).toHaveText('MARKETING');
  await expect(page.locator(`tr[data-user-id="${ALEX}"]`)).toContainText('Alex Senior Analyst');
  const patch = seen.filter((s) => s.method === 'PATCH').at(-1)!;
  expect(patch.body).toContain('<name>Alex Senior Analyst</name><email>alex.senior@example.com</email>');

  // Forced promotion, then demotion of the ADMIN: both are the service's to refuse.
  const promoted = await page.evaluate((id) => window.mosaiq.updateUser(id, { role_code: 'ADMIN' }), ALEX);
  expect(promoted).toMatchObject({ ok: false, error: { code: 'use_administrator_transfer' } });
  await page.getByTestId(`view-user-${SUB}`).click();
  await expect(page.getByTestId('user-name')).toHaveText('Ada Admin');
  await page.selectOption('[data-testid=user-edit] #c-role_code', 'ANALYST');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('use_administrator_transfer');
  await expect(page.getByTestId('transfer-hint')).toContainText('Transfer administrator');
  await app.close();
});

test('deactivate and reactivate show the new state after a reload; the only ADMIN stays', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'users');
  await page.getByTestId(`view-user-${MORGAN}`).click();
  await page.getByTestId('deactivate').click();
  await page.getByTestId('confirm-deactivate').click();
  await expect(page.getByTestId('notice')).toHaveText('User deactivated.');
  await expect(page.locator(`tr[data-user-id="${MORGAN}"]`)).toContainText('Inactive');
  expect(last('/deactivation').method).toBe('POST');

  await page.getByTestId('reactivate').click();
  await expect(page.getByTestId('notice')).toHaveText('User reactivated.');
  await expect(page.locator(`tr[data-user-id="${MORGAN}"]`)).toContainText('Active');
  expect(last('/deactivation').method).toBe('DELETE');

  await page.getByTestId(`view-user-${SUB}`).click();
  await expect(page.getByTestId('user-name')).toHaveText('Ada Admin');
  await page.getByTestId('deactivate').click();
  await page.getByTestId('confirm-deactivate').click();
  await expect(page.getByTestId('error-code')).toHaveText('administrator_required');
  await expect(page.getByTestId('error-message')).toHaveText('The only administrator cannot be deactivated.');
  await app.close();
});

test('an ADMIN resets another user password: never shown back, and 12 characters at least', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'users');
  await page.getByTestId(`view-user-${ALEX}`).click();
  await page.fill('[data-testid=user-reset] #c-new_password', 'elevenchars');
  await page.getByRole('button', { name: 'Reset password' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('invalid_body');
  await expect(page.getByTestId('detail-new_password')).toHaveText('new_password: min_length');
  expect(seen.some((s) => s.url.endsWith('/password'))).toBe(false);

  await page.fill('[data-testid=user-reset] #c-new_password', NEW_PASSWORD);
  await page.getByRole('button', { name: 'Reset password' }).click();
  await expect(page.getByTestId('notice')).toHaveText('Password reset. It is not shown.');
  await expect(page.locator('[data-testid=user-reset] #c-new_password')).toHaveValue('');
  await expect(page.locator('body')).not.toContainText(NEW_PASSWORD);
  const put = last('/password');
  expect(put.method).toBe('PUT');
  expect(put.url).toContain(`/users/${ALEX}/password`);
  expect(put.body).not.toContain('current_password');
  // The caller stays signed in.
  await expect(page.getByTestId('role')).toHaveText('Signed in as ADMIN');
  await app.close();
});

test('any staff role changes its own password from the account menu and signs in again', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  await open(page, 'services');
  await page.getByTestId('account-menu').locator('summary').click();
  await page.fill('#account-current_password', 'a-wrong-current-password');
  await page.fill('#account-new_password', NEW_PASSWORD);
  await page.getByRole('button', { name: 'Change password' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('invalid_current_password');
  await expect(page.getByTestId('detail-current_password')).toHaveText('current_password: incorrect');
  await expect(page.getByTestId('role')).toHaveText('Signed in as STORE_MANAGER');

  nextRefusal = {
    service: 'users',
    file: 'rate-limited',
    status: 429,
    headers: { 'Retry-After': '540' },
  };
  await page.fill('#account-current_password', CURRENT);
  await page.fill('#account-new_password', NEW_PASSWORD);
  await page.getByRole('button', { name: 'Change password' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('rate_limited');
  await expect(page.getByTestId('error-retry-after')).toHaveText('540');

  await page.fill('#account-current_password', CURRENT);
  await page.fill('#account-new_password', NEW_PASSWORD);
  await page.getByRole('button', { name: 'Change password' }).click();
  await expect(page.getByTestId('notice')).toHaveText('Password changed, sign in again.');
  await expect(page.locator('#f-email')).toBeVisible();
  const put = last('/password');
  expect(put.url).toContain(`/users/${SUB}/password`);
  expect(put.body).toContain(`<current_password>${CURRENT}</current_password>`);
  await app.close();
});

test('the transfer screen warns in a dialog, shows its refusals, and ends both sessions', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'transfer');
  // Only active users who are not the incumbent are offered.
  await expect(page.locator('#transfer-successor_user_id option')).toHaveText([
    'Choose an active user',
    'Alex Analyst <alex.analyst@example.com> (ANALYST)',
    'Mia Marketing <mia.marketing@example.com> (MARKETING)',
  ]);

  // The warning, captured before any refusal is on screen.
  await page.selectOption('#transfer-successor_user_id', ALEX);
  await page.getByRole('button', { name: 'Transfer the administrator role' }).click();
  await expect(page.getByTestId('transfer-warning')).toContainText('ends the sessions of both accounts');
  await shot(page, '484-desktop-transfer-02-warning-dialog');
  await page.getByRole('button', { name: 'Cancel' }).click();
  await page.selectOption('#transfer-successor_user_id', '');

  // No successor chosen: refused before sending.
  await page.getByRole('button', { name: 'Transfer the administrator role' }).click();
  await expect(page.getByTestId('transfer-dialog')).toBeVisible();
  await page.getByTestId('confirm-transfer').click();
  await expect(page.getByTestId('detail-successor_user_id')).toHaveText('successor_user_id: required');
  expect(seen.some((s) => s.url.endsWith('/administrator-transfers'))).toBe(false);

  // Cancel sends nothing.
  await page.selectOption('#transfer-successor_user_id', ALEX);
  await page.getByRole('button', { name: 'Transfer the administrator role' }).click();
  await expect(page.getByTestId('transfer-warning')).toContainText('ends the sessions of both accounts');
  await page.getByTestId('cancel-transfer').click();
  await expect(page.getByTestId('transfer-dialog')).toBeHidden();
  expect(seen.some((s) => s.url.endsWith('/administrator-transfers'))).toBe(false);

  // The refusals the service gives, shown as returned.
  nextRefusal = { service: 'users', file: 'successor-inactive', status: 409 };
  await page.getByRole('button', { name: 'Transfer the administrator role' }).click();
  await page.getByTestId('confirm-transfer').click();
  await expect(page.getByTestId('error-code')).toHaveText('successor_inactive');
  await shot(page, '484-desktop-transfer-03-successor-inactive');
  const incumbent = await page.evaluate((id) => window.mosaiq.transferAdministrator(id), SUB);
  expect(incumbent).toMatchObject({ ok: false, error: { code: 'invalid_successor' } });
  await expect(page.getByTestId('role')).toHaveText('Signed in as ADMIN');

  await page.getByRole('button', { name: 'Transfer the administrator role' }).click();
  await page.getByTestId('confirm-transfer').click();
  // The next call is refused with 401 session_revoked: back to sign-in.
  await expect(page.getByTestId('notice')).toHaveText('Your session was revoked. Sign in again.');
  await expect(page.locator('#f-email')).toBeVisible();
  const post = last('/administrator-transfers');
  expect(post.body).toContain(`<successor_user_id>${ALEX}</successor_user_id>`);

  // The successor signs in again: exactly one ADMIN, and the former one holds the successor's old role.
  await signIn(page, 'ADMIN');
  await open(page, 'users');
  await page.selectOption('#filter-role', 'ADMIN');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.locator('[data-testid=users-table] tbody tr')).toHaveCount(1);
  await expect(page.locator(`tr[data-user-id="${ALEX}"]`)).toContainText('ADMIN');
  await page.selectOption('#filter-role', 'ANALYST');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.locator(`tr[data-user-id="${SUB}"]`)).toContainText('ANALYST');
  await app.close();
});

test('screens and controls follow each role', async () => {
  let { app, page } = await launch('AUDITOR');
  expect(await nav(page)).toContain('users');
  expect(await nav(page)).not.toContain('transfer');
  await open(page, 'users');
  await page.getByTestId(`view-user-${ALEX}`).click();
  await expect(page.getByTestId('user-name')).toHaveText('Alex Analyst');
  await expect(page.getByTestId('new-user')).toHaveCount(0);
  await expect(page.getByTestId('user-edit')).toHaveCount(0);
  await expect(page.getByTestId('deactivate')).toHaveCount(0);
  await expect(page.getByTestId('user-reset')).toHaveCount(0);
  // The account menu is there for every staff role.
  await expect(page.getByTestId('account-menu')).toHaveCount(1);
  const forced = await page.evaluate((id) => window.mosaiq.deactivateUser(id), ALEX);
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  await app.close();

  ({ app, page } = await launch('ANALYST'));
  expect(await nav(page)).not.toContain('users');
  expect(await nav(page)).not.toContain('transfer');
  await expect(page.getByTestId('account-menu')).toHaveCount(1);
  await app.close();

  ({ app, page } = await launch('ADMIN'));
  expect(await nav(page)).toEqual(expect.arrayContaining(['users', 'transfer']));
  await app.close();
});
