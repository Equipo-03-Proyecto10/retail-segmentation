import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';

// The A/B experiment module (F28-10, #879), against a fake gateway that answers with the recorded
// examples of contracts/experimentation/ and contracts/campaigns/. Experiment 15 is the plan the
// examples describe as UNDERPOWERED. The fake keeps whether it was assigned, so a refusal such as
// underpowered_not_acknowledged arises from what is stored, as it does in the service.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

const inner = (xml: string, root: string): string =>
  new RegExp(`<${root}[^>]*>([\\s\\S]*)</${root}>`).exec(xml)?.[1] ?? '';

/** Experiment 15 as the examples describe the plan, and as it reads once the frame is assigned. */
const planned = (): string => example('experimentation', 'experiment-planned');
const assigned = (): string =>
  planned()
    .replace('<state>PLANNED</state>', '<state>ASSIGNED</state>')
    .replace('</starts_on>', '</starts_on><analysis_at>2026-11-30T08:00:00Z</analysis_at>')
    .replace(
      '</salt>',
      `</salt><assignment>${inner(example('experimentation', 'assignment-summary'), 'AssignmentSummary')}</assignment>`,
    );

/** The recorded page of experiments, with the planned one added once it exists. */
const listPage = (withPlanned: boolean): string => {
  const page = example('experimentation', 'experiments-page');
  if (!withPlanned) return page;
  return page.replace(
    '<common:PageInfo>',
    `<Experiment>${inner(planned(), 'Experiment')}</Experiment><common:PageInfo>`,
  );
};

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'MARKETING';
let seen: Seen[] = [];
let isPlanned = false;
let isAssigned = false;
/** Replaces the name of experiment 12, to prove that what a service says is shown as text. */
let nameOf12: string | null = null;

function reset(): void {
  seen = [];
  isPlanned = false;
  isAssigned = false;
  nameOf12 = null;
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      seen.push({ method, url: url.toString(), body });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (path === '/api/campaigns/v1/campaigns') return reply(200, example('campaigns', 'campaigns'));
      if (/^\/api\/campaigns\/v1\/campaigns\/\d+$/.test(path))
        return reply(200, example('campaigns', 'campaign'));

      if (path === '/api/experimentation/v1/experiments') {
        if (method === 'GET') return reply(200, listPage(isPlanned));
        isPlanned = true;
        return reply(201, planned());
      }
      const one = /^\/api\/experimentation\/v1\/experiments\/(\d+)$/.exec(path);
      if (one) {
        const id = one[1];
        if (id === '12') {
          const xml = example('experimentation', 'experiment-controlled');
          return reply(
            200,
            nameOf12 === null ? xml : xml.replace('Win-back offer for at-risk customers', nameOf12),
          );
        }
        if (id === '16') return reply(200, example('experimentation', 'experiment-offer-comparison'));
        if (id === '3') return reply(200, example('experimentation', 'experiment-legacy'));
        if (id === '15' && isPlanned) return reply(200, isAssigned ? assigned() : planned());
        return reply(404, example('experimentation', 'experiment-not-found'));
      }
      if (method === 'POST' && path === '/api/experimentation/v1/experiment-assignments') {
        if (isAssigned) return reply(409, example('experimentation', 'already-assigned'));
        if (!body.includes('<acknowledge_underpowered>true</acknowledge_underpowered>'))
          return reply(409, example('experimentation', 'underpowered-not-acknowledged'));
        isAssigned = true;
        return reply(201, example('experimentation', 'assignment-summary'));
      }
      reply(404);
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
  const page = await app.firstWindow();
  await page.waitForSelector('form');
  await page.fill('#f-email', 'a@b.c');
  await page.fill('#f-password', 'pw');
  await page.click('button[type=submit]');
  await expect(page.getByTestId('role')).toHaveText(`Signed in as ${as}`);
  return { app, page };
}

const nav = (page: Page) => page.locator('nav.nav button[data-view="experiments"]');
const writes = (): Seen[] => seen.filter((s) => s.method !== 'GET' && s.url.includes('/experimentation/'));

test('the list shows each design and its flags, and a design with no control says no effect can be claimed', async () => {
  const { app, page } = await launch('MARKETING');
  await nav(page).click();
  await expect(page.locator('[data-testid=experiments-table] tbody tr')).toHaveCount(3);
  await expect(page.locator('[data-experiment-id="16"]')).toContainText('Offer comparison (no control)');
  await expect(page.locator('[data-experiment-id="12"]')).toContainText('Controlled');
  await expect(page.locator('[data-experiment-id="3"]')).toContainText('Synthetic');
  await shot(page, '879-desktop-experiments-01-list');

  await page.getByTestId('view-experiment-16').click();
  await expect(page.getByTestId('no-effect-notice')).toContainText(
    'no effect of the campaign against no treatment can be claimed',
  );
  await expect(page.getByTestId('experiment-design-kind')).toContainText('Offer comparison');
  await shot(page, '879-desktop-experiments-02-no-control');

  await page.getByTestId('view-experiment-12').click();
  await expect(page.getByTestId('experiment-state')).toHaveText('ANALYZED');
  await expect(page.getByTestId('no-effect-notice')).toHaveCount(0);
  // The campaign is named by its owner, from its id.
  await expect(page.getByTestId('experiment-campaign')).toContainText('(#4)');
  await app.close();
});

test('an assigned experiment shows its sample size and the sample ratio and balance checks', async () => {
  const { app, page } = await launch('AUDITOR');
  await nav(page).click();
  await page.getByTestId('view-experiment-12').click();
  await expect(page.getByTestId('sample-size-required')).toHaveText('Customers needed per arm: 3,839');
  await expect(page.getByTestId('sample-size-supply')).toContainText('supplies 4,000 to each of 2 arms');
  await expect(page.getByTestId('powered')).toBeVisible();
  await expect(page.getByTestId('underpowered')).toHaveCount(0);
  await expect(page.getByTestId('check-sample-ratio')).toContainText('Passed (p = 1.000000');
  await expect(page.getByTestId('check-recomputation')).toContainText('Recomputation: passed');
  const balance = page.locator('[data-testid=balance-table] tbody tr');
  await expect(balance).toHaveCount(3);
  await expect(balance.first()).toContainText('Days since the last purchase, before the frame');
  await expect(balance.first()).toContainText('Within the threshold');
  await expect(page.getByTestId('assigned-total')).toHaveText('8,000');
  await expect(page.getByTestId('exclusions-table')).toContainText('NO_PERSONALIZATION_CONSENT');
  await shot(page, '879-desktop-experiments-03-checks');

  // An experiment of the earlier rule is readable and says it is not verified.
  await page.getByTestId('view-experiment-3').click();
  await expect(page.getByTestId('experiment-rule')).toContainText('not called verified');
  await expect(page.getByTestId('checks-none')).toBeVisible();
  await expect(page.getByTestId('sample-size')).toContainText('No sample size was planned');
  await app.close();
});

test('planning shows the sample size before anyone is assigned and flags an underpowered plan, which is assigned only once acknowledged', async () => {
  const { app, page } = await launch('MARKETING');
  await nav(page).click();
  await page.getByTestId('plan-panel').locator('summary').click();

  // The design is read while it is entered.
  await expect(page.getByTestId('design-reading')).toHaveAttribute('data-design', 'controlled');
  await page.fill('#plan-arm_1_name', 'No offer');
  await page.fill('#plan-arm_1_treatment_description', 'Held back: receives no win-back offer.');
  await page.fill('#plan-arm_2_name', 'Basket booster');
  await page.fill(
    '#plan-arm_2_treatment_description',
    'Offered 5 percent off a basket of five or more items.',
  );
  await expect(page.getByTestId('design-reading')).toHaveAttribute('data-design', 'controlled');
  await page.selectOption('#plan-arm_1_kind', 'TREATMENT');
  await expect(page.getByTestId('design-reading')).toHaveAttribute('data-design', 'offer_comparison');
  await expect(page.getByTestId('no-effect-notice-form')).toContainText('no effect of the campaign');
  await page.getByTestId('add-arm').click();
  await expect(page.getByTestId('design-reading')).toHaveAttribute('data-design', 'invalid');
  await expect(page.getByTestId('design-problems')).toContainText('exactly two offers');
  await expect(page.getByTestId('plan-submit')).toBeDisabled();
  await page.getByTestId('remove-arm-3').click();
  await page.selectOption('#plan-arm_1_kind', 'CONTROL');
  await expect(page.getByTestId('design-reading')).toHaveAttribute('data-design', 'controlled');
  // What was typed survives the arms being redrawn.
  await expect(page.locator('#plan-arm_2_name')).toHaveValue('Basket booster');

  await page.fill('#plan-name', 'Spring basket booster');
  await page.selectOption('#plan-campaign_id', '301');
  await page.fill('#plan-conversion_window_days', '14');
  await page.fill('#plan-minimum_detectable_effect', '0.02');
  await page.fill('#plan-starts_on', '2026-11-16');
  await page.getByTestId('plan-submit').click();
  await expect(page.getByTestId('plan-warning')).toContainText('cannot be edited or deleted');
  expect(writes()).toHaveLength(0);
  await page.getByTestId('confirm-plan').click();

  await expect(page.getByTestId('notice')).toHaveText('Experiment #15 planned. Nobody is assigned yet.');
  await expect(page.getByTestId('sample-size-required')).toHaveText('Customers needed per arm: 3,839');
  await expect(page.getByTestId('sample-size-supply')).toContainText('supplies 600 to each of 2 arms');
  await expect(page.getByTestId('underpowered')).toContainText('Underpowered');
  await expect(page.getByTestId('underpowered')).toContainText('inconclusive, never "no effect"');
  await expect(page.getByTestId('underpowered')).toContainText('0.0537 (5.37 percentage points)');
  await expect(page.getByTestId('experiment-flags')).toContainText('Underpowered');
  await expect(page.getByTestId('not-assigned')).toBeVisible();
  await shot(page, '879-desktop-experiments-04-underpowered');
  const plan = writes().at(-1)!;
  expect(plan.method).toBe('POST');
  expect(plan.body).toContain('<campaign_id>301</campaign_id>');
  expect(plan.body).toContain('<minimum_detectable_effect>0.02</minimum_detectable_effect>');
  expect(plan.body.match(/<arms>/g)).toHaveLength(2);
  // The service computes these; the screen never sends them.
  for (const computed of ['required_per_arm', 'baseline', 'salt', 'frame', 'data_origin'])
    expect(plan.body).not.toContain(computed);
  await expect(page.locator('[data-testid=experiments-table] tbody tr')).toHaveCount(4);

  // The assignment waits for an explicit acknowledgement.
  await expect(page.getByTestId('assign')).toBeDisabled();
  const forced = await page.evaluate(() => window.mosaiq.assignExperiment(15, false));
  expect(forced).toMatchObject({ ok: false, error: { code: 'underpowered_not_acknowledged' } });
  await page.getByTestId('acknowledge-underpowered').check();
  await expect(page.getByTestId('assign')).toBeEnabled();
  await page.getByTestId('assign').click();
  await expect(page.getByTestId('assign-warning')).toContainText('written once and never rewritten');
  await page.getByTestId('confirm-assign').click();

  await expect(page.getByTestId('notice')).toHaveText('Experiment #15 assigned: 8,000 customers.');
  await expect(page.getByTestId('experiment-state')).toHaveText('ASSIGNED');
  await expect(page.getByTestId('check-sample-ratio')).toContainText('Passed');
  await expect(page.locator('[data-testid=balance-table] tbody tr')).toHaveCount(3);
  await expect(page.getByTestId('design-analysis-at')).toHaveText('2026-11-30T08:00:00Z');
  await expect(page.getByTestId('assign-form')).toHaveCount(0);
  await shot(page, '879-desktop-experiments-05-assigned');
  const assign = writes().at(-1)!;
  expect(assign.body).toContain('<acknowledge_underpowered>true</acknowledge_underpowered>');
  await app.close();
});

test('a refusal of the plan is shown beside its field, and the service’s own refusals as returned', async () => {
  const { app, page } = await launch('ADMIN');
  await nav(page).click();
  await page.getByTestId('plan-panel').locator('summary').click();
  await page.fill('#plan-arm_1_name', 'No offer');
  await page.fill('#plan-arm_1_treatment_description', 'Held back.');
  await page.fill('#plan-arm_2_name', 'Offer');
  await page.fill('#plan-arm_2_treatment_description', 'An offer.');
  await page.getByTestId('plan-submit').click();
  await page.getByTestId('confirm-plan').click();
  // Nothing was typed in the other fields: the main process names each one and sends nothing.
  await expect(page.getByTestId('detail-name')).toHaveText('name: required');
  await expect(page.getByTestId('detail-campaign_id')).toHaveText('campaign_id: out_of_range');
  await expect(page.getByTestId('detail-conversion_window_days')).toHaveText(
    'conversion_window_days: out_of_range',
  );
  await expect(page.getByTestId('detail-minimum_detectable_effect')).toHaveText(
    'minimum_detectable_effect: required',
  );
  expect(writes()).toHaveLength(0);
  await app.close();
});

test('a role that only reads sees no plan form and no assignment, and a forced write is refused before it is sent', async () => {
  isPlanned = true;
  const { app, page } = await launch('ANALYST');
  await expect(nav(page)).toHaveCount(1);
  await nav(page).click();
  await expect(page.getByTestId('plan-panel')).toHaveCount(0);
  await page.getByTestId('view-experiment-15').click();
  await expect(page.getByTestId('not-assigned')).toBeVisible();
  await expect(page.getByTestId('assign-form')).toHaveCount(0);
  const plan = await page.evaluate(() =>
    window.mosaiq.planExperiment({
      name: 'x',
      campaign_id: 1,
      primary_metric: 'CONVERSION',
      starts_on: '2026-11-16',
      conversion_window_days: 14,
      minimum_detectable_effect: '0.02',
      arms: [
        { kind: 'CONTROL', name: 'a', treatment_description: 'a' },
        { kind: 'TREATMENT', name: 'b', treatment_description: 'b' },
      ],
    }),
  );
  expect(plan).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  const assign = await page.evaluate(() => window.mosaiq.assignExperiment(15, true));
  expect(assign).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(writes()).toHaveLength(0);
  await app.close();
});

test('the module is hidden from a role the contract does not allow', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  await expect(nav(page)).toHaveCount(0);
  const read = await page.evaluate(() => window.mosaiq.listExperiments({}));
  expect(read).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(seen.some((s) => s.url.includes('/experimentation/'))).toBe(false);
  await app.close();
});

test('what the service says is shown as text, never as markup', async () => {
  nameOf12 = '&lt;img src=x onerror=&quot;window.pwned=1&quot;&gt;';
  const { app, page } = await launch('MARKETING');
  await nav(page).click();
  await page.getByTestId('view-experiment-12').click();
  await expect(page.getByTestId('experiment-detail').locator('h3')).toContainText('img src=x onerror');
  await expect(page.getByTestId('experiment-detail').locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => 'pwned' in window)).toBe(false);
  await app.close();
});
