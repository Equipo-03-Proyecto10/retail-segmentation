import { createServer, type Server } from 'node:http';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;
const error = (code: string, details = ''): string =>
  `<Error xmlns="${COMMON}"><code>${code}</code><message>Server says ${code}</message><correlation_id>srv-42</correlation_id>${details}</Error>`;

let server: Server;
let base = '';
let role = 'ANALYST';
let deleted = 0;

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml' });
      res.end(body);
    };
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', () => {
      if (req.method === 'POST' && req.url === '/api/auth/v1/sessions') {
        return body.includes('wrong')
          ? reply(401, error('invalid_credentials'))
          : body.includes('blank')
            ? reply(
                422,
                error('invalid_body', '<details><field>password</field><code>required</code></details>'),
              )
            : reply(201, tokens);
      }
      if (req.url === '/api/auth/v1/sessions/current') {
        if (req.method === 'DELETE') {
          deleted += 1;
          return reply(204);
        }
        return reply(200, claims(role));
      }
      reply(403, error('forbidden_role'));
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());

async function launch(): Promise<{ app: ElectronApplication; page: Page }> {
  const dir = mkdtempSync(join(tmpdir(), 'mosaiq-desktop-'));
  writeFileSync(join(dir, 'settings.json'), JSON.stringify({ baseUrl: base }));
  const app = await electron.launch({ args: ['.'], env: { ...process.env, MOSAIQ_DESKTOP_CONFIG_DIR: dir } });
  const page = await app.firstWindow();
  await page.waitForSelector('form');
  return { app, page };
}

async function signIn(page: Page, email: string, password = 'pw'): Promise<void> {
  await page.fill('#f-email', email);
  await page.fill('#f-password', password);
  await page.click('button[type=submit]');
}

test('window is locked down', async () => {
  const { app, page } = await launch();
  const prefs = await app.evaluate(({ BrowserWindow }) => {
    const p = (
      BrowserWindow.getAllWindows()[0]!.webContents as unknown as {
        getLastWebPreferences(): Record<string, unknown>;
      }
    ).getLastWebPreferences();
    return { ci: p.contextIsolation, ni: p.nodeIntegration, sb: p.sandbox, ws: p.webSecurity };
  });
  expect(prefs).toMatchObject({ ci: true, ni: false, sb: true });
  expect(prefs.ws).not.toBe(false);
  expect(await page.evaluate(() => typeof require)).toBe('undefined');
  expect(await page.evaluate(() => typeof process)).toBe('undefined');
  expect(await page.evaluate(() => Object.keys(window.mosaiq).sort())).toEqual([
    'getState',
    'onStateChange',
    'probeService',
    'signIn',
    'signOut',
  ]);
  const csp = await page.getAttribute('meta[http-equiv="Content-Security-Policy"]', 'content');
  expect(csp).toContain("script-src 'self'");
  expect(csp).not.toContain('unsafe-inline');
  // No inline script runs and no navigation or new window is allowed.
  await page.evaluate(() => {
    const s = document.createElement('script');
    s.textContent = 'window.inlineRan = true';
    document.body.append(s);
  });
  expect(await page.evaluate(() => 'inlineRan' in window)).toBe(false);
  await page.evaluate(() => {
    location.href = 'https://example.com/';
  });
  await page.evaluate(() => window.open('https://example.com/'));
  await page.waitForTimeout(500);
  expect(app.windows()).toHaveLength(1);
  expect(page.url()).toMatch(/^file:/);
  await app.close();
});

test('staff sign-in shows role-gated services, then sign-out', async () => {
  role = 'MARKETING';
  const { app, page } = await launch();
  await signIn(page, 'a@b.c');
  await expect(page.getByTestId('role')).toHaveText('Signed in as MARKETING');
  await expect(page.locator('[data-service=campaigns]')).toHaveCount(1);
  await expect(page.locator('[data-service=audit-trail]')).toHaveCount(0);
  // A forced call refused with 403 forbidden_role is shown, not hidden.
  await page.locator('[data-service=campaigns] button').click();
  await expect(page.getByTestId('error-code')).toHaveText('forbidden_role');
  await expect(page.getByTestId('error-correlation')).toHaveText('srv-42');
  deleted = 0;
  await page.getByText('Sign out').click();
  await page.waitForSelector('form');
  expect(deleted).toBe(1);
  await app.close();
});

test('CUSTOMER is signed out again with a staff-only message', async () => {
  role = 'CUSTOMER';
  deleted = 0;
  const { app, page } = await launch();
  await signIn(page, 'c@b.c');
  await expect(page.getByTestId('error-message')).toContainText('staff roles');
  expect(deleted).toBe(1);
  await expect(page.locator('form')).toBeVisible();
  await app.close();
});

test('an error envelope shows code, message, correlation id and field details', async () => {
  const { app, page } = await launch();
  await signIn(page, 'blank@b.c');
  await expect(page.getByTestId('error-code')).toHaveText('invalid_body');
  await expect(page.getByTestId('error-message')).toHaveText('Server says invalid_body');
  await expect(page.getByTestId('error-correlation')).toHaveText('srv-42');
  await expect(page.getByTestId('detail-password')).toHaveText('password: required');
  await app.close();
});
