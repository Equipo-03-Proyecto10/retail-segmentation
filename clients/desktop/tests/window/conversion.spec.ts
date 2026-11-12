import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';

// The conversion analysis module (F28-11, #880), against a fake gateway that answers with the
// recorded results of contracts/experimentation/. The list of experiments the picker offers is the
// recorded one, with a few more derived from the recorded controlled experiment, so that each
// recorded result (complete, revenue, inconclusive, pending, invalid, offer comparison) has an
// experiment to open.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const EXPERIMENTATION = 'https://mosaiq.maxthecoder.online/xml/experimentation/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

const inner = (xml: string, root: string): string =>
  new RegExp(`<${root}[^>]*>([\\s\\S]*)</${root}>`).exec(xml)?.[1] ?? '';

const controlled = example('experimentation', 'experiment-controlled');
const derived = (id: number, name: string, state: string): string =>
  inner(controlled, 'Experiment')
    .replace(/<experiment_id>12<\/experiment_id>/g, `<experiment_id>${id}</experiment_id>`)
    .replace(/<name>[^<]*<\/name>/, `<name>${name}</name>`)
    .replace('<state>ANALYZED</state>', `<state>${state}</state>`);

/** id, state and the inside of each experiment the picker is offered. */
const EXPERIMENTS: [number, string, string][] = [
  [12, 'ANALYZED', inner(controlled, 'Experiment')],
  [13, 'ANALYZED', derived(13, 'Double points or a free gift', 'ANALYZED')],
  [16, 'ANALYZED', inner(example('experimentation', 'experiment-offer-comparison'), 'Experiment')],
  [17, 'ANALYZED', derived(17, 'Small frame, big hopes', 'ANALYZED')],
  [18, 'ASSIGNED', derived(18, 'Free coffee with any basket', 'ASSIGNED')],
  [19, 'ANALYZED', derived(19, 'Welcome voucher', 'ANALYZED')],
  [3, 'ASSIGNED', inner(example('experimentation', 'experiment-legacy'), 'Experiment')],
];

const list = (state: string | null): string => {
  const rows = EXPERIMENTS.filter(([, s]) => state === null || s === state);
  return `<Experiments xmlns="${EXPERIMENTATION}" xmlns:common="${COMMON}">${rows
    .map(([, , xml]) => `<Experiment>${xml}</Experiment>`)
    .join(
      '',
    )}<common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>${rows.length}</common:total></common:PageInfo></Experiments>`;
};

const RESULT: Record<string, string> = {
  '12': 'result-complete',
  '13': 'result-revenue',
  '16': 'result-offer-comparison',
  '17': 'result-inconclusive',
  '18': 'result-pending',
  '19': 'result-invalid',
};

interface Seen {
  method: string;
  url: string;
}

let server: Server;
let base = '';
let role = 'ANALYST';
let seen: Seen[] = [];
/** Replaces the first assumption of every result, to prove that what a service says is shown as text. */
let statement: string | null = null;
/** Makes the service label every comparison an effect, as a faulty one might. */
let relabel = false;

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    req.on('data', () => undefined);
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      seen.push({ method, url: url.toString() });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (path === '/api/experimentation/v1/experiments')
        return reply(200, list(url.searchParams.get('state')));
      const one = /^\/api\/experimentation\/v1\/experiment-results\/(\d+)$/.exec(path);
      if (one) {
        const file = RESULT[one[1] as string];
        if (file === undefined) return reply(409, example('experimentation', 'experiment-not-assigned'));
        let xml = example('experimentation', file);
        if (url.searchParams.get('basis') === 'RE_READ')
          xml = xml.replace('<basis>PRIMARY</basis>', '<basis>RE_READ</basis>');
        if (relabel) xml = xml.replaceAll('<label>TREATMENT_DIFFERENCE</label>', '<label>EFFECT</label>');
        if (statement !== null)
          xml = xml.replace(/<statement>[^<]*<\/statement>/, `<statement>${statement}</statement>`);
        return reply(200, xml);
      }
      reply(404);
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(() => {
  seen = [];
  statement = null;
  relabel = false;
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

const nav = (page: Page) => page.locator('nav.nav button[data-view="conversion"]');

async function openResult(page: Page, id: number, basis = 'PRIMARY'): Promise<void> {
  await nav(page).click();
  await expect(page.locator('#result-experiment_id option')).not.toHaveCount(1);
  await page.selectOption('#result-experiment_id', String(id));
  await page.selectOption('#result-basis', basis);
  await page.getByTestId('result-open').click();
  await expect(page.getByTestId('result')).toBeVisible();
}

const resultCalls = (): string[] =>
  seen.filter((s) => s.url.includes('/experiment-results/')).map((s) => s.url);

test('a result shows each arm, the difference and its interval, and says that no customer is pending', async () => {
  const { app, page } = await launch('ANALYST');
  await openResult(page, 12);
  await expect(page.locator('[data-testid=arms-results] tbody tr')).toHaveCount(2);
  await expect(page.locator('[data-testid=arms-results] tbody tr').first()).toContainText('No offer');
  await expect(page.locator('[data-testid=arms-results] tbody tr').last()).toContainText('12.10%');
  await expect(page.getByTestId('pending-statement')).toContainText('No customer is pending');
  const effect = page.locator('[data-testid=effect-table] tbody tr').first();
  await expect(effect).toContainText('Effect');
  await expect(effect).toContainText('+0.0210 (+2.10 percentage points)');
  await expect(effect).toContainText('0.0073 to 0.0347 (0.73 to 3.47 percentage points)');
  await expect(effect).toContainText('95.0% interval');
  await expect(effect).toContainText("Significant at the experiment's alpha");
  await expect(page.getByTestId('result-basis')).toHaveText('The stored primary result');
  await expect(page.getByTestId('design-warnings-none')).toBeVisible();
  await expect(page.getByTestId('planned-vs-analysed')).toContainText('Planned: 3,839 customers per arm.');
  expect(resultCalls()).toEqual([`${base}/api/experimentation/v1/experiment-results/12?basis=PRIMARY`]);
  await shot(page, '880-desktop-conversion-01-result');
  await app.close();
});

test('attribution is a section of its own, labelled, and never calls itself incremental', async () => {
  const { app, page } = await launch('ANALYST');
  await openResult(page, 12);
  const attribution = page.getByTestId('attribution');
  await expect(attribution).toHaveAttribute('data-label', 'ATTRIBUTION');
  await expect(attribution.locator('h4')).toHaveText('Attribution');
  await expect(attribution.locator('tbody tr').first()).toContainText('Attribution');
  await expect(attribution.locator('tbody tr').first()).toContainText('15980.40');
  await expect(page.getByTestId('attribution-unattributed')).toContainText('107 treatment conversions');
  expect(await attribution.innerText()).not.toMatch(/incremental|uplift|caused/i);
  // The two are different elements, and the attributed revenue is nowhere in the effect.
  expect(await page.getByTestId('effect-table').innerText()).not.toContain('15980.40');
  expect(await page.getByTestId('effect-table').innerText()).not.toMatch(/attribut/i);
  await expect(attribution.locator('[data-testid=effect-table]')).toHaveCount(0);
  await shot(page, '880-desktop-conversion-02-attribution');
  await app.close();
});

test('money of an effect appears only on an effect of revenue, with its interval', async () => {
  const { app, page } = await launch('MARKETING');
  await openResult(page, 13);
  await expect(page.locator('[data-testid=effect-table] thead th').last()).toHaveText('Incremental revenue');
  await expect(page.locator('[data-testid=effect-table] tbody tr').first()).toContainText(
    '14080.00 (interval 5097.26 to 23062.74)',
  );
  await expect(page.locator('[data-testid=effect-table] tbody tr')).toHaveCount(4);
  await app.close();
});

test('a design with no control shows the difference between treatments and says that no effect can be claimed', async () => {
  const { app, page } = await launch('AUDITOR');
  await openResult(page, 16);
  await expect(page.getByTestId('effect-table')).toHaveCount(0);
  await expect(page.locator('[data-testid=difference-table] tbody tr')).toHaveCount(2);
  await expect(page.locator('[data-testid=difference-table] tbody tr').first()).toContainText(
    'Difference between treatments',
  );
  await expect(page.getByTestId('effect-refusal')).toContainText(
    'states no effect of the campaign against no treatment',
  );
  await expect(page.getByTestId('design-warnings')).toContainText('No control: no effect can be claimed');
  await expect(page.getByTestId('result-design-kind')).toContainText('no control');
  // Attribution stays, and it too is credit and not an effect.
  await expect(page.getByTestId('attribution-table')).toBeVisible();
  const text = await page.getByTestId('result').innerText();
  expect(text).not.toMatch(/incremental|uplift/i);
  await shot(page, '880-desktop-conversion-03-no-control');
  await app.close();
});

test('the warnings of the design stay in view next to the result', async () => {
  const { app, page } = await launch('ANALYST');
  await openResult(page, 17);
  const bar = page.getByTestId('design-warnings');
  await expect(bar).toContainText('Underpowered');
  await expect(bar).toContainText('Balance flagged');
  await expect(bar).toContainText('Inconclusive');
  await expect(page.getByTestId('warning-UNDERPOWERED')).toContainText('inconclusive, never "no effect"');
  await expect(page.getByTestId('effect-caveat')).toHaveText(
    'Read with: Underpowered; Balance flagged; Inconclusive.',
  );
  await expect(page.locator('[data-testid=effect-table] tbody tr').first()).toContainText('Inconclusive');
  // Every arm row carries them too.
  await expect(page.locator('[data-testid=arms-results] tbody tr').first()).toContainText(
    'Underpowered; Balance flagged; Inconclusive',
  );
  // The bar is above the figures, so it is read before them.
  const barTop = (await bar.boundingBox())!.y;
  expect(barTop).toBeLessThan((await page.getByTestId('arms-results').boundingBox())!.y);
  // Scrolled to the end of the result, the bar is still in view.
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(bar).toBeInViewport();
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, '880-desktop-conversion-04-warnings');
  await app.close();
});

test('a result not read yet shows counts and the pending customers, and no interval', async () => {
  const { app, page } = await launch('ANALYST');
  await openResult(page, 18);
  await expect(page.getByTestId('result-status')).toHaveText('Pending: counts only');
  await expect(page.getByTestId('result-elapsed')).toHaveText('28.57%');
  await expect(page.getByTestId('pending-statement')).toContainText('3,856 customers are pending');
  await expect(page.getByTestId('result-status-text')).toContainText(
    'before the analysis instant (2026-11-26T08:00:00Z)',
  );
  await expect(page.getByTestId('effect-table')).toHaveCount(0);
  await expect(page.getByTestId('difference-table')).toHaveCount(0);
  await expect(page.getByTestId('checks-none')).toBeVisible();
  await expect(page.getByTestId('attribution-none')).toBeVisible();
  await expect(page.getByTestId('design-warnings')).toContainText('Not read yet');
  await shot(page, '880-desktop-conversion-05-pending');
  await app.close();
});

test('a result whose sample ratio check failed withholds the estimate and says so', async () => {
  const { app, page } = await launch('ANALYST');
  await openResult(page, 19);
  await expect(page.getByTestId('result-status')).toHaveText('Invalid: the estimate is withheld');
  await expect(page.getByTestId('effect-table')).toHaveCount(0);
  await expect(page.getByTestId('check-sample-ratio')).toContainText('Failed (p = 0.000016');
  await expect(page.getByTestId('design-warnings')).toContainText('Estimate withheld');
  await expect(page.getByTestId('design-warnings')).toContainText('Sample ratio check failed');
  await app.close();
});

test('a re-read is asked for as such, and says it replaces nothing', async () => {
  const { app, page } = await launch('ANALYST');
  await openResult(page, 12, 'RE_READ');
  expect(resultCalls()).toEqual([`${base}/api/experimentation/v1/experiment-results/12?basis=RE_READ`]);
  await expect(page.getByTestId('result-basis')).toContainText('A re-read: it replaces nothing');
  await expect(page.getByTestId('design-warnings')).toContainText('Re-read');
  await app.close();
});

test('the first table, which the shared export writes, says what it is', async () => {
  const { app, page } = await launch('ANALYST');
  await openResult(page, 17);
  const headers = await page.locator('table').first().locator('thead th').allTextContents();
  expect(headers).toEqual(expect.arrayContaining(['Data', 'As of', 'Design warnings']));
  expect(headers[0]).toBe('Arm');
  await expect(page.getByTestId('export')).toBeVisible();
  await app.close();
});

test('the service’s own refusal is shown as returned', async () => {
  const { app, page } = await launch('ANALYST');
  await nav(page).click();
  await expect(page.locator('#result-experiment_id option')).not.toHaveCount(1);
  await page.selectOption('#result-experiment_id', '3');
  await page.getByTestId('result-open').click();
  await expect(page.getByTestId('error-code')).toHaveText('experiment_not_assigned');
  await app.close();
});

test('the picker offers only experiments that can have a result, and a planned one is never asked for', async () => {
  const { app, page } = await launch('ANALYST');
  await nav(page).click();
  await expect(page.locator('#result-experiment_id option')).toHaveCount(8);
  await expect(page.locator('#result-experiment_id option[value="16"]')).toContainText('no control');
  const states = seen
    .filter((s) => s.url.includes('/experimentation/v1/experiments?'))
    .map((s) => new URL(s.url).searchParams.get('state'));
  expect(states.sort()).toEqual(['ANALYZED', 'ASSIGNED']);
  await app.close();
});

test('a role the contract does not allow has no module, and a forced read is refused before it is sent', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  await expect(nav(page)).toHaveCount(0);
  const read = await page.evaluate(() => window.mosaiq.getExperimentResult(12, 'PRIMARY'));
  expect(read).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(resultCalls()).toEqual([]);
  await app.close();
});

test('what the service says is shown as text, never as markup', async () => {
  statement = '&lt;img src=x onerror=&quot;window.pwned=1&quot;&gt;';
  const { app, page } = await launch('ANALYST');
  await openResult(page, 12);
  await expect(page.getByTestId('assumptions')).toContainText('img src=x onerror');
  await expect(page.getByTestId('result').locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => 'pwned' in window)).toBe(false);
  await app.close();
});

test('an effect the service sends for a design with no control is not shown as one', async () => {
  relabel = true;
  const { app, page } = await launch('ANALYST');
  await openResult(page, 16);
  await expect(page.getByTestId('effect-refused')).toContainText('It is not shown');
  await expect(page.getByTestId('effect-table')).toHaveCount(0);
  await expect(page.getByTestId('difference-table')).toHaveCount(0);
  await expect(page.getByTestId('effect-refusal')).toBeVisible();
  expect(await page.getByTestId('result').innerText()).not.toMatch(/incremental|uplift/i);
  await app.close();
});
