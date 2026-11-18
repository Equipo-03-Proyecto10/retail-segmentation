import { createServer, type Server } from 'node:http';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, launchElectron, test, type ElectronApplication, type Page } from './electron-test';
import { shot } from '../support/evidence';

// The RFM configuration module (F28-04, #873) against a fake gateway that answers with the recorded
// examples of contracts/rfm-engine/. createRfmRuleSet and getRfmRuleSet are marked planned in the
// contract, so the service does not answer them yet; the fake does, as the contract says it will. No
// request value is echoed into a response.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const NS = 'https://mosaiq.maxthecoder.online/xml';
const example = (name: string): string =>
  readFileSync(join(CONTRACTS, 'rfm-engine', 'examples', `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${NS}/auth/v1"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${NS}/auth/v1"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2099-01-01T00:00:00Z</expires_at></SessionClaims>`;

const HOSTILE = '<img src=x onerror="window.xss=1">';
const profile = (id: number, r: number, f: number, m: string): string =>
  `<RfmProfile><customer_id>00000000-0000-4000-8000-00000000000${id}</customer_id><state>scored</state><rule_set_id>3</rule_set_id><r_days>${r}</r_days><f_count>${f}</f_count><m_total>${m}</m_total><r_score>4</r_score><f_score>5</f_score><m_score>4</m_score><label_code>LOYAL</label_code><via_fallback>false</via_fallback><computed_at>2026-10-30T14:22:05Z</computed_at></RfmProfile>`;
/** Five scored customers, so each label of the fixed draft holds a known number. */
const PROFILES = [
  profile(1, 30, 9, '842.50'),
  profile(2, 5, 9, '900.00'),
  profile(3, 100, 5, '50.00'),
  profile(4, 400, 1, '10.00'),
  profile(5, 20, 1, '5000.00'),
];
const profilesPage = `<RfmProfiles xmlns="${NS}/rfm-engine/v1" xmlns:common="${NS}/common/v1">${PROFILES.join('')}<common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>${PROFILES.length}</common:total></common:PageInfo></RfmProfiles>`;

let server: Server;
let base = '';
let role = 'ANALYST';
let hostile = false;
let seen: { method: string; path: string; body: string }[] = [];

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    let body = '';
    req.on('data', (chunk) => (body += String(chunk)));
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      seen.push({ method: req.method ?? 'GET', path: path + url.search, body });
      if (req.method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return req.method === 'DELETE' ? reply(204) : reply(200, claims(role));
      const name = (xml: string): string =>
        hostile ? xml.replace(/<name>[^<]*<\/name>/g, `<name><![CDATA[${HOSTILE}]]></name>`) : xml;
      if (path === '/api/rfm-engine/v1/rfm-rule-sets' && req.method === 'GET')
        return reply(200, name(example('rule-sets-page')));
      if (path === '/api/rfm-engine/v1/rfm-rule-sets' && req.method === 'POST')
        return reply(201, example('rule-set-created'));
      if (path === '/api/rfm-engine/v1/rfm-rule-sets/3') return reply(200, name(example('rule-set')));
      if (path === '/api/rfm-engine/v1/rfm-rule-sets/4') return reply(200, name(example('rule-set-created')));
      if (path === '/api/rfm-engine/v1/rfm-profiles') return reply(200, profilesPage);
      reply(404);
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(() => {
  seen = [];
  hostile = false;
});

async function launch(as: string): Promise<{ app: ElectronApplication; page: Page }> {
  role = as;
  const { app } = await launchElectron(base);
  const page = await app.firstWindow();
  await page.waitForSelector('form');
  await page.fill('#f-email', 'a@b.c');
  await page.fill('#f-password', 'pw');
  await page.click('button[type=submit]');
  await expect(page.getByTestId('role')).toHaveText(`Signed in as ${as}`);
  await page.locator('nav.nav button[data-view="rfm-rules"]').click();
  await expect(page.getByTestId('rfm-draft')).toBeVisible();
  await expect(page.locator('[data-testid=rfm-rule-set] option')).toHaveCount(2);
  return { app, page };
}

const issues = (page: Page) => page.locator('[data-testid=rfm-issue]');
const field = (page: Page, name: string) => page.locator(`input[data-field="${name}"]`);

/** Opens the published rule set #3 and derives a draft from it. */
async function deriveFromPublished(page: Page): Promise<void> {
  await page.getByTestId('rfm-rule-set').selectOption('3');
  await page.getByTestId('rfm-open').click();
  await expect(page.getByTestId('rfm-opened')).toBeVisible();
  await page.getByTestId('rfm-derive').click();
  await expect(page.getByTestId('rfm-derived')).toBeVisible();
}

test('a published rule set opens read-only, and a new version is derived into a draft', async () => {
  const { page } = await launch('ANALYST');
  await page.getByTestId('rfm-rule-set').selectOption('3');
  await page.getByTestId('rfm-open').click();
  const opened = page.getByTestId('rfm-opened');
  await expect(opened).toContainText('#3 Seeded bands, 180-day window (published)');
  await expect(page.getByTestId('rfm-readonly-note')).toContainText('published, so it is read-only');
  // Nothing in it can be typed into, and the cut-points and bands are the stored ones.
  await expect(opened.locator('input, select, textarea, button')).toHaveCount(0);
  await expect(page.locator('[data-testid=rfm-opened-cutpoints] tbody tr').first()).toContainText('14');
  await expect(page.locator('[data-testid=rfm-opened-bands] tbody tr')).toHaveCount(4);
  await shot(page, '873-desktop-rfm-01-read-only');

  await page.getByTestId('rfm-derive').click();
  await expect(page.getByTestId('rfm-derived')).toContainText('The stored rule set is unchanged');
  await expect(page.locator('#rfm-name')).toHaveValue('Seeded bands, 180-day window (new version)');
  await expect(field(page, 'bands[3].label_code')).toHaveValue('LOST');
  // The stored rule set is still what it was.
  await expect(opened).toContainText('#3 Seeded bands, 180-day window (published)');
  expect(seen.filter((r) => r.method === 'POST' && r.path.includes('rfm-rule-sets'))).toEqual([]);
});

test('validation names the band or the window that is wrong, as the draft is edited', async () => {
  const { page } = await launch('ANALYST');
  // A blank draft has no name yet, and cannot be previewed.
  await expect(issues(page)).toHaveText(['The rule set needs a name.']);
  await expect(page.getByTestId('rfm-preview-run')).toBeDisabled();

  await deriveFromPublished(page);
  // The recorded rule set's bands do not overlap (#1060), so the derived draft is valid as it stands.
  await expect(page.getByTestId('rfm-valid')).toBeVisible();
  await expect(page.getByTestId('rfm-preview-run')).toBeEnabled();

  // Widening LOYAL to R 4 to 5 puts CHAMPION inside it: the draft says which bands, and where.
  await field(page, 'bands[1].r_max').fill('5');
  await expect(issues(page)).toHaveText([
    'Band 1 (CHAMPION) and Band 2 (LOYAL) overlap: both match R 5, F 5, M 4 to 5.',
  ]);
  await expect(page.getByTestId('rfm-preview-run')).toBeDisabled();
  await shot(page, '873-desktop-rfm-02-band-overlap');

  await field(page, 'bands[1].r_max').fill('4');
  await expect(page.getByTestId('rfm-valid')).toBeVisible();
  await expect(page.getByTestId('rfm-preview-run')).toBeEnabled();

  await page.locator('#rfm-window').fill('4000');
  await expect(issues(page)).toHaveText(['Window: 4000 days is outside 1 to 3650 days.']);
  await expect(page.locator('#rfm-window')).toHaveAttribute('aria-invalid', 'true');
  await page.locator('#rfm-window').fill('180');

  await field(page, 'recency_boundaries[1]').fill('10');
  await expect(issues(page)).toHaveText([
    'Recency cut-point for score 4 (10) is below the one for score 5 (14); recency cut-points must not decrease from score 5 to 2.',
  ]);
  await field(page, 'recency_boundaries[1]').fill('35');

  await field(page, 'bands[2].r_min').fill('4');
  await field(page, 'bands[2].r_max').fill('2');
  await expect(issues(page)).toHaveText(['Band 3 (AT_RISK): R minimum 4 is greater than its maximum 2.']);
  await field(page, 'bands[2].r_min').fill('2');
  await field(page, 'bands[2].r_max').fill('3');
  await expect(page.getByTestId('rfm-valid')).toBeVisible();

  // Removing and adding a band keeps the rest of the draft.
  await page.getByTestId('rfm-band-remove-3').click();
  await expect(page.locator('[data-testid=rfm-bands] tbody tr')).toHaveCount(3);
  await page.getByTestId('rfm-band-add').click();
  await expect(issues(page)).toHaveText(['Band 4 needs a label.']);
});

test('the distribution per label is shown before storing, and goes stale when the draft changes', async () => {
  const { page } = await launch('ANALYST');
  await deriveFromPublished(page);
  await page.getByTestId('rfm-preview-run').click();
  await expect(page.getByTestId('rfm-preview-table')).toBeVisible();
  await expect(page.getByTestId('rfm-preview-basis')).toContainText(
    '5 customers scored under #3 Seeded bands, 180-day window, whose R, F and M cover 180 days',
  );
  await expect(page.getByTestId('rfm-preview-note')).toContainText('the same window');
  const rows = page.locator('[data-testid=rfm-preview-table] tbody tr');
  // Label, customers, share, and how many took the label only because no band matched.
  await expect(rows).toHaveText(['LOST240.0%1', 'AT_RISK120.0%0', 'CHAMPION120.0%0', 'LOYAL120.0%0']);
  await shot(page, '873-desktop-rfm-03-preview');
  expect(seen.filter((r) => r.path.startsWith('/api/rfm-engine/v1/rfm-profiles'))).toHaveLength(1);
  expect(seen.filter((r) => r.method === 'POST' && r.path.includes('rfm-rule-sets'))).toEqual([]);

  // An analyst reads and previews; storing is the administrator's.
  await expect(page.getByTestId('rfm-save')).toHaveCount(0);

  // Editing the draft removes a preview that no longer describes it.
  await field(page, 'recency_boundaries[0]').fill('1');
  await expect(page.getByTestId('rfm-preview')).toHaveCount(0);
  await page.getByTestId('rfm-preview-run').click();
  await expect(page.getByTestId('rfm-preview-table')).toBeVisible();
});

test('a different window is said not to change the figures already held', async () => {
  const { page } = await launch('ANALYST');
  await deriveFromPublished(page);
  await page.locator('#rfm-window').fill('90');
  await page.getByTestId('rfm-preview-run').click();
  await expect(page.getByTestId('rfm-preview-note')).toContainText("The draft's window is 90 days, not 180.");
});

test('an administrator stores the previewed draft as a new, unpublished rule set', async () => {
  const { page } = await launch('ADMIN');
  await deriveFromPublished(page);
  await page.locator('#rfm-name').fill('Quarterly bands 2026-Q4');
  // Storing waits for a preview of this very draft.
  await expect(page.getByTestId('rfm-save')).toBeDisabled();
  await page.getByTestId('rfm-preview-run').click();
  await expect(page.getByTestId('rfm-preview-table')).toBeVisible();
  await expect(page.getByTestId('rfm-save')).toBeEnabled();
  await page.getByTestId('rfm-save').click();
  await expect(page.getByTestId('rfm-saved')).toContainText('Stored as rule set #4. It is not published');
  const post = seen.find((r) => r.method === 'POST' && r.path === '/api/rfm-engine/v1/rfm-rule-sets');
  expect(post?.body).toContain(
    '<RfmRuleSetRequest xmlns="https://mosaiq.maxthecoder.online/xml/rfm-engine/v1">',
  );
  expect(post?.body).toContain('<name>Quarterly bands 2026-Q4</name>');
  expect(post?.body).toContain('<fallback_label_code>LOST</fallback_label_code>');
  expect(post?.body.match(/<bands>/g)).toHaveLength(4);
  await shot(page, '873-desktop-rfm-04-stored');
});

test('the main process refuses what the screen would not offer', async () => {
  const { page } = await launch('ANALYST');
  const refused = await page.evaluate(() =>
    window.mosaiq.createRfmRuleSet({
      name: 'x',
      window_days: 180,
      recency_boundaries: [14, 35, 70, 120],
      frequency_boundaries: [8, 5, 3, 2],
      monetary_boundaries: ['1200.00', '800.00', '450.00', '200.00'],
      bands: [{ label_code: 'LOST', r_min: 1, r_max: 1, f_min: 1, f_max: 3, m_min: 1, m_max: 2 }],
      fallback_label_code: 'LOST',
    }),
  );
  expect(refused).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(seen.filter((r) => r.method === 'POST' && r.path.includes('rfm-rule-sets'))).toEqual([]);
});

test('text from the service reaches the screen as text', async () => {
  hostile = true;
  const { page } = await launch('ANALYST');
  await page.getByTestId('rfm-rule-set').selectOption('3');
  await page.getByTestId('rfm-open').click();
  await expect(page.getByTestId('rfm-opened')).toContainText(HOSTILE);
  await expect(page.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { xss?: number }).xss)).toBeUndefined();
});
