import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { PLAIN_MESSAGES } from '../../src/shared/campaign-messages';
import { shot } from '../support/evidence';

// A campaign's audience, budget, conditions and exclusions (F28-09, #878), against a fake gateway
// that answers with the recorded examples of contracts/campaigns/ and contracts/segments/ and keeps
// what it is given, so a refusal such as campaign_not_draft arises from what is stored, as it does in
// the service. Campaign 301 is the one the campaign examples describe. The fake never echoes a value
// it was sent without escaping it as XML text.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const NS = 'https://mosaiq.maxthecoder.online/xml';
const AUTH = `${NS}/auth/v1`;

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
/** Element names and values of a request body, in order, to compare a sent request with a recorded one. */
const fieldsOf = (xml: string): [string, string][] =>
  [...xml.matchAll(/<([a-z_]+)>([^<]*)<\/\1>/g)].map((m) => [m[1] as string, m[2] as string]);
const xmlText = (value: string | number): string =>
  String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

type Status = 'DRAFT' | 'ACTIVE' | 'FINISHED' | 'CANCELLED';
const SHOWN: Record<Status, string> = {
  DRAFT: 'campaign',
  ACTIVE: 'draft-to-active',
  FINISHED: 'active-to-finished',
  CANCELLED: 'draft-to-cancelled',
};
const LEGAL: Record<Status, Status[]> = {
  DRAFT: ['ACTIVE', 'CANCELLED'],
  ACTIVE: ['FINISHED', 'CANCELLED'],
  FINISHED: [],
  CANCELLED: [],
};

const CUSTOMER_A = '6f1c2a9e-3b44-4d1e-9a52-0c7d8e1f2a3b';
const CUSTOMER_B = 'b7d9e0f4-52a1-4c38-8e6d-1f2a3b4c5d6e';
const UNKNOWN_CUSTOMER = '00000000-0000-4000-8000-000000000000';
/** Customers holding LOYAL in the segments example; the audience can never exceed it in this fake. */
const HOLDERS = 31;
const IN_AUDIENCE = 25;

interface Plan {
  cap?: string;
  total?: string;
  perCustomer?: string;
  registered?: string;
  min?: string;
  days?: string;
  customers: string[];
  labels: string[];
}

/** What the recorded examples describe. */
const recordedPlan = (): Plan => ({
  cap: '5000',
  total: '150000.00',
  perCustomer: '300.00',
  registered: '2026-06-30',
  min: '2',
  days: '180',
  customers: [CUSTOMER_A, CUSTOMER_B],
  labels: ['DORMANT'],
});

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'MARKETING';
let seen: Seen[] = [];
let stored: Status = 'DRAFT';
let plan: Plan = recordedPlan();
let nextRefusal: { file: string; status: number } | null = null;
/** A sub-resource whose reads are refused with 403 forbidden_role, to show one section failing alone. */
let unreadable: string | null = null;

function reset(): void {
  seen = [];
  stored = 'DRAFT';
  plan = recordedPlan();
  nextRefusal = null;
  unreadable = null;
}

const audienceXml = (): string => {
  const size = plan.cap === undefined ? IN_AUDIENCE : Math.min(IN_AUDIENCE, Number(plan.cap));
  return `<CampaignAudience xmlns="${NS}/campaigns/v1"><campaign_id>301</campaign_id><label_code>LOYAL</label_code>${
    plan.cap === undefined ? '' : `<max_customers>${xmlText(plan.cap)}</max_customers>`
  }<audience_size>${size}</audience_size><as_of>2026-11-02T15:04:05Z</as_of></CampaignAudience>`;
};
const budgetXml = (): string =>
  `<CampaignBudget xmlns="${NS}/campaigns/v1"><campaign_id>301</campaign_id>${
    plan.total === undefined ? '' : `<total_amount>${xmlText(plan.total)}</total_amount>`
  }${plan.perCustomer === undefined ? '' : `<per_customer_cap>${xmlText(plan.perCustomer)}</per_customer_cap>`}</CampaignBudget>`;
const eligibilityXml = (): string =>
  `<CampaignEligibilityRules xmlns="${NS}/campaigns/v1"><campaign_id>301</campaign_id>${
    plan.registered === undefined
      ? ''
      : `<registered_on_or_before>${xmlText(plan.registered)}</registered_on_or_before>`
  }${plan.min === undefined ? '' : `<min_purchases>${xmlText(plan.min)}</min_purchases>`}${
    plan.days === undefined ? '' : `<purchase_window_days>${xmlText(plan.days)}</purchase_window_days>`
  }</CampaignEligibilityRules>`;
const exclusionsXml = (): string =>
  `<CampaignExclusions xmlns="${NS}/campaigns/v1"><campaign_id>301</campaign_id>${plan.customers
    .map((id) => `<customer_ids>${xmlText(id)}</customer_ids>`)
    .join(
      '',
    )}${plan.labels.map((code) => `<label_codes>${xmlText(code)}</label_codes>`).join('')}</CampaignExclusions>`;

const PARTS: Record<string, () => string> = {
  audience: audienceXml,
  budget: budgetXml,
  'eligibility-rules': eligibilityXml,
  exclusions: exclusionsXml,
};

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
      if (path === '/api/segments/v1/segments') return reply(200, example('segments', 'list-segments'));
      const writer = role === 'ADMIN' || role === 'MARKETING';
      if (method !== 'GET' && !writer) return reply(403, example('campaigns', 'forbidden-role'));
      if (path === '/api/campaigns/v1/campaigns') return reply(200, example('campaigns', 'campaigns'));
      const match = /^\/api\/campaigns\/v1\/campaigns\/(\d+)(?:\/([a-z-]+))?$/.exec(path);
      if (!match || match[1] !== '301') return reply(404, example('campaigns', 'campaign-not-found'));
      const part = match[2];
      if (part === undefined && method === 'PATCH') {
        if (nextRefusal !== null) {
          const r = nextRefusal;
          nextRefusal = null;
          return reply(r.status, example('campaigns', r.file));
        }
        return stored === 'DRAFT'
          ? reply(200, example('campaigns', 'campaign'))
          : reply(409, example('campaigns', 'campaign-not-draft'));
      }
      if (part === undefined) return reply(200, example('campaigns', SHOWN[stored]));
      const field = (name: string): string => new RegExp(`<${name}>([^<]*)</${name}>`).exec(body)?.[1] ?? '';
      const all = (name: string): string[] =>
        [...body.matchAll(new RegExp(`<${name}>([^<]*)</${name}>`, 'g'))].map((m) => m[1] as string);
      if (part === 'transitions') {
        const to = field('to') as Status;
        if (field('expected_status') !== stored) return reply(409, example('campaigns', 'status-changed'));
        if (!LEGAL[stored].includes(to)) return reply(409, example('campaigns', 'illegal-transition'));
        const file = `${stored.toLowerCase()}-to-${to.toLowerCase()}`;
        stored = to;
        return reply(200, example('campaigns', file));
      }
      const render = PARTS[part];
      if (render === undefined) return reply(404, example('campaigns', 'campaign-not-found'));
      if (method === 'GET') {
        return unreadable === part
          ? reply(403, example('campaigns', 'forbidden-role'))
          : reply(200, render());
      }
      if (nextRefusal !== null) {
        const r = nextRefusal;
        nextRefusal = null;
        return reply(r.status, example('campaigns', r.file));
      }
      if (stored !== 'DRAFT') return reply(409, example('campaigns', 'campaign-not-draft'));
      // The fake stores what the XSD allows; an id it does not know is refused as the service would.
      if (part === 'exclusions' && all('customer_ids').includes(UNKNOWN_CUSTOMER))
        return reply(422, example('campaigns', 'unknown-customer'));
      if (part === 'audience') plan.cap = field('max_customers') || undefined;
      if (part === 'budget') {
        plan.total = field('total_amount') || undefined;
        plan.perCustomer = field('per_customer_cap') || undefined;
      }
      if (part === 'eligibility-rules') {
        plan.registered = field('registered_on_or_before') || undefined;
        plan.min = field('min_purchases') || undefined;
        plan.days = field('purchase_window_days') || undefined;
      }
      if (part === 'exclusions') {
        plan.customers = all('customer_ids');
        plan.labels = all('label_codes');
      }
      return reply(200, render());
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

const nav = (page: Page, name: string) => page.locator(`nav.nav button[data-view="${name}"]`);
const planWrites = (): Seen[] => seen.filter((s) => s.method === 'PUT');
const lastWrite = (): Seen => planWrites().at(-1) as Seen;
const section = (page: Page, id: string) => page.getByTestId(`plan-${id}`);

/** Opens campaign 301 and waits for its plan to be drawn: the sections read on their own. */
async function openPlan(page: Page): Promise<void> {
  await nav(page, 'campaigns').click();
  await page.getByTestId('view-campaign-301').click();
  await expect(page.getByTestId('campaign-name')).toHaveText('Holiday Loyalty Preview');
  await expect(page.getByTestId('campaign-plan')).toBeVisible();
}

test('a DRAFT shows what is stored in each section, for a role that writes', async () => {
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  await expect(page.locator('#audience-max_customers')).toHaveValue('5000');
  await expect(page.getByTestId('audience-label')).toHaveText('Loyal (LOYAL)');
  await expect(page.locator('#conditions-registered_on_or_before')).toHaveValue('2026-06-30');
  await expect(page.locator('#conditions-min_purchases')).toHaveValue('2');
  await expect(page.locator('#conditions-purchase_window_days')).toHaveValue('180');
  await expect(page.locator('#exclusions-customer_ids')).toHaveValue(`${CUSTOMER_A}\n${CUSTOMER_B}`);
  // DORMANT is stored and the vocabulary no longer lists it: it is still offered, ticked.
  await expect(page.getByTestId('exclusion-label-DORMANT')).toBeChecked();
  await expect(page.getByTestId('exclusion-label-LOST')).not.toBeChecked();
  await expect(page.locator('#budget-total_amount')).toHaveValue('150000.00');
  await expect(page.locator('#budget-per_customer_cap')).toHaveValue('300.00');
  await expect(page.getByTestId('plan-locked')).toHaveCount(0);
  await shot(page, '878-desktop-campaign-plan-01-sections');
  expect(
    seen
      .filter((s) => s.method === 'GET' && /\/campaigns\/301\/[a-z-]+$/.test(s.url))
      .map((s) => s.url.split('/').pop())
      .sort(),
  ).toEqual(['audience', 'budget', 'eligibility-rules', 'exclusions']);
  await app.close();
});

test('the preview shows the estimated population, how many were left out and why, and follows a save', async () => {
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  await expect(page.getByTestId('preview-result')).toBeEmpty();
  await page.getByTestId('preview-run').click();
  await expect(page.getByTestId('preview-size')).toHaveText(`${IN_AUDIENCE} customers`);
  await expect(page.getByTestId('preview-as-of')).toHaveText('2026-11-02T15:04:05Z');
  await expect(page.getByTestId('preview-holders')).toHaveText(String(HOLDERS));
  await expect(page.getByTestId('preview-left-out')).toHaveText(String(HOLDERS - IN_AUDIENCE));
  const why = page.getByTestId('preview-reasons');
  await expect(why.getByTestId('preview-reason-customers')).toHaveText(
    'Excluded by id: 2 customers are listed and left out.',
  );
  await expect(why.getByTestId('preview-reason-labels')).toHaveText(
    'Excluded by label: holders of DORMANT are left out.',
  );
  await expect(why.getByTestId('preview-reason-registered')).toContainText(
    'registered on or before 2026-06-30',
  );
  await expect(why.getByTestId('preview-reason-purchases')).toContainText(
    'at least 2 net purchases in the 180 days before the preview',
  );
  await expect(why.getByTestId('preview-reason-cap')).toHaveText(
    'Cap: the audience is cut to at most 5000 customers.',
  );
  await expect(page.getByTestId('preview-note')).toContainText('not split by reason');
  await shot(page, '878-desktop-campaign-plan-02-preview');

  // A save is followed by a new count, because the preview on screen would otherwise be stale.
  await page.locator('#audience-max_customers').fill('10');
  await page.getByTestId('audience-save').click();
  await expect(section(page, 'audience').getByTestId('notice')).toHaveText('Audience saved.');
  await expect(page.getByTestId('preview-size')).toHaveText('10 customers');
  await expect(page.getByTestId('preview-left-out')).toHaveText(String(HOLDERS - 10));
  await expect(page.getByTestId('preview-reason-cap')).toContainText('at most 10 customers');
  await app.close();
});

test('a preview of a plan with no rule says that no rule leaves anyone out', async () => {
  plan = { customers: [], labels: [] };
  const { app, page } = await launch('ADMIN');
  await openPlan(page);
  await page.getByTestId('preview-run').click();
  await expect(page.getByTestId('preview-size')).toHaveText(`${IN_AUDIENCE} customers`);
  await expect(page.getByTestId('preview-reason-none')).toHaveText(
    'No exclusion, condition or cap is set, so no rule leaves anyone out.',
  );
  await expect(page.getByTestId('preview-reasons').locator('li')).toHaveCount(1);
  await app.close();
});

test('each section saves the recorded request and then shows what the service stored', async () => {
  plan = { customers: [], labels: ['DORMANT'] };
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  await expect(page.locator('#audience-max_customers')).toHaveValue('');

  await page.locator('#audience-max_customers').fill('5000');
  await page.getByTestId('audience-save').click();
  await expect(section(page, 'audience').getByTestId('notice')).toHaveText('Audience saved.');
  expect(fieldsOf(lastWrite().body)).toEqual(fieldsOf(example('campaigns', 'put-campaign-audience.request')));
  expect(lastWrite().url).toMatch(/\/api\/campaigns\/v1\/campaigns\/301\/audience$/);

  await page.locator('#budget-total_amount').fill('150000.00');
  await page.locator('#budget-per_customer_cap').fill('300.00');
  await page.getByTestId('budget-save').click();
  await expect(section(page, 'budget').getByTestId('notice')).toHaveText('Budget saved.');
  expect(fieldsOf(lastWrite().body)).toEqual(fieldsOf(example('campaigns', 'put-campaign-budget.request')));

  await page.locator('#conditions-registered_on_or_before').fill('2026-06-30');
  await page.locator('#conditions-min_purchases').fill('2');
  await page.locator('#conditions-purchase_window_days').fill('180');
  await page.getByTestId('conditions-save').click();
  await expect(section(page, 'conditions').getByTestId('notice')).toHaveText('Conditions saved.');
  expect(fieldsOf(lastWrite().body)).toEqual(
    fieldsOf(example('campaigns', 'put-campaign-eligibility-rules.request')),
  );

  await page.locator('#exclusions-customer_ids').fill(`${CUSTOMER_A}, ${CUSTOMER_B.toUpperCase()}`);
  await expect(page.getByTestId('exclusion-label-DORMANT')).toBeChecked();
  await page.getByTestId('exclusions-save').click();
  await expect(section(page, 'exclusions').getByTestId('notice')).toHaveText('Exclusions saved.');
  expect(fieldsOf(lastWrite().body)).toEqual(
    fieldsOf(example('campaigns', 'put-campaign-exclusions.request')),
  );
  // The form was drawn again from the stored lists: ids in the XSD's lower case, the label ticked.
  await expect(page.locator('#exclusions-customer_ids')).toHaveValue(`${CUSTOMER_A}\n${CUSTOMER_B}`);
  await expect(page.getByTestId('exclusion-label-DORMANT')).toBeChecked();
  expect(planWrites()).toHaveLength(4);

  // Ticking another label and unticking this one replaces the list.
  await page.getByTestId('exclusion-label-LOST').check();
  await page.getByTestId('exclusion-label-DORMANT').uncheck();
  await page.getByTestId('exclusions-save').click();
  // The form is drawn again from what was stored: the label the vocabulary lacks is gone.
  await expect(page.getByTestId('exclusion-label-DORMANT')).toHaveCount(0);
  await expect(page.getByTestId('exclusion-label-LOST')).toBeChecked();
  expect(fieldsOf(lastWrite().body)).toEqual([
    ['customer_ids', CUSTOMER_A],
    ['customer_ids', CUSTOMER_B],
    ['label_codes', 'LOST'],
  ]);

  // Emptying a field removes that setting: the body is empty.
  await page.locator('#audience-max_customers').fill('');
  await page.getByTestId('audience-save').click();
  await expect.poll(() => lastWrite().body).toMatch(/<PutCampaignAudienceRequest [^>]*\/>$/);
  await expect(page.locator('#audience-max_customers')).toHaveValue('');
  await app.close();
});

test('each section is validated when saved, in plain words next to its field, and nothing is sent', async () => {
  const { app, page } = await launch('MARKETING');
  await openPlan(page);

  await page.locator('#audience-max_customers').fill('0');
  await page.getByTestId('audience-save').click();
  await expect(section(page, 'audience').getByTestId('detail-max_customers')).toHaveText(
    'The maximum number of customers is outside the allowed range.',
  );
  await page.locator('#audience-max_customers').fill('lots');
  await page.getByTestId('audience-save').click();
  await expect(section(page, 'audience').getByTestId('detail-max_customers')).toHaveText(
    'The maximum number of customers is not in the expected format.',
  );

  await page.locator('#budget-total_amount').fill('100.00');
  await page.locator('#budget-per_customer_cap').fill('100.01');
  await page.getByTestId('budget-save').click();
  await expect(section(page, 'budget').getByTestId('detail-per_customer_cap')).toHaveText(
    'The per-customer limit cannot be larger than the total budget.',
  );
  await page.locator('#budget-total_amount').fill('100');
  await page.getByTestId('budget-save').click();
  await expect(section(page, 'budget').getByTestId('detail-total_amount')).toHaveText(
    'The total budget is not in the expected format.',
  );
  // What was typed is still there.
  await expect(page.locator('#budget-total_amount')).toHaveValue('100');

  await page.locator('#conditions-registered_on_or_before').fill('2026-06-30');
  await page.locator('#conditions-min_purchases').fill('2');
  await page.locator('#conditions-purchase_window_days').fill('');
  await page.getByTestId('conditions-save').click();
  await expect(section(page, 'conditions').getByTestId('detail-purchase_window_days')).toHaveText(
    'The purchase window must be given together with the other purchase rule, or both left empty.',
  );
  await page.locator('#conditions-purchase_window_days').fill('732');
  await page.getByTestId('conditions-save').click();
  await expect(section(page, 'conditions').getByTestId('detail-purchase_window_days')).toHaveText(
    'The purchase window is outside the allowed range.',
  );

  await page.locator('#exclusions-customer_ids').fill('not-a-customer');
  await page.getByTestId('exclusions-save').click();
  await expect(section(page, 'exclusions').getByTestId('detail-customer_ids')).toHaveText(
    'The customer ids is not in the expected format.',
  );
  await shot(page, '878-desktop-campaign-plan-03-validation');
  await page.locator('#exclusions-customer_ids').fill(`${CUSTOMER_A}\n${CUSTOMER_A}`);
  await page.getByTestId('exclusions-save').click();
  await expect(section(page, 'exclusions').getByTestId('detail-customer_ids')).toHaveText(
    'The customer ids lists the same value twice.',
  );
  expect(planWrites()).toHaveLength(0);
  await app.close();
});

test('the validity of a campaign is its dates: checked when saved, and an end before the start is refused in plain words', async () => {
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  const form = page.getByTestId('campaign-edit-form');
  await page.locator('#edit-starts_on').fill('');
  await form.getByRole('button', { name: 'Save changes' }).click();
  await expect(form.getByTestId('detail-starts_on')).toHaveText('The start date is required.');
  expect(seen.filter((s) => s.method === 'PATCH')).toHaveLength(0);

  // The order of the two dates is the service's to judge: the request is sent and its refusal shown.
  await page.locator('#edit-starts_on').fill('2026-11-01');
  await page.locator('#edit-ends_on').fill('2026-10-01');
  nextRefusal = { file: 'ends-before-start', status: 422 };
  await form.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByTestId('error-message')).toHaveText(PLAIN_MESSAGES.ends_before_start as string);
  await expect(page.getByTestId('error-code')).toHaveText('ends_before_start');
  await expect(form.getByTestId('detail-ends_on')).toHaveText(
    'The end date cannot be before the start date.',
  );
  expect(seen.filter((s) => s.method === 'PATCH')).toHaveLength(1);
  await app.close();
});

test('a server refusal is shown in plain words, with its code and correlation id behind the details', async () => {
  const { app, page } = await launch('ADMIN');
  await openPlan(page);

  nextRefusal = { file: 'invalid-plan-body', status: 422 };
  await page.getByTestId('budget-save').click();
  const budget = section(page, 'budget');
  await expect(budget.getByTestId('error-message')).toHaveText(PLAIN_MESSAGES.invalid_body as string);
  await expect(budget.getByTestId('error-code')).toHaveText('invalid_body');
  await expect(budget.getByTestId('error-correlation')).toHaveText('corr-plan-422');
  await expect(budget.getByTestId('detail-per_customer_cap')).toHaveText(
    'The per-customer limit cannot be larger than the total budget.',
  );
  await shot(page, '878-desktop-campaign-plan-04-refusal');

  nextRefusal = { file: 'invalid-audience-body', status: 422 };
  await page.getByTestId('audience-save').click();
  // audience_size names no field of the form, so its reason is among the details of the error.
  await expect(section(page, 'audience').getByTestId('error-code')).toHaveText('invalid_body');
  await expect(section(page, 'audience').getByTestId('error-detail')).toHaveText('additional_property');

  await page.locator('#exclusions-customer_ids').fill(UNKNOWN_CUSTOMER);
  await page.getByTestId('exclusions-save').click();
  const exclusions = section(page, 'exclusions');
  await expect(exclusions.getByTestId('error-message')).toHaveText(PLAIN_MESSAGES.unknown_customer as string);
  await expect(exclusions.getByTestId('error-code')).toHaveText('unknown_customer');
  await expect(exclusions.getByTestId('detail-customer_ids')).toHaveText(
    'The customer ids names a customer that is not in the customer list.',
  );
  // Nothing was lost by the refusal.
  await expect(page.locator('#exclusions-customer_ids')).toHaveValue(UNKNOWN_CUSTOMER);

  nextRefusal = { file: 'unknown-label', status: 422 };
  await page.getByTestId('exclusions-save').click();
  await expect(exclusions.getByTestId('error-message')).toHaveText(PLAIN_MESSAGES.unknown_label as string);

  nextRefusal = { file: 'invalid-exclusions-body', status: 422 };
  await page.getByTestId('exclusions-save').click();
  await expect(exclusions.getByTestId('detail-customer_ids')).toHaveText(
    'The customer ids has more entries than are allowed.',
  );
  await app.close();
});

test('a campaign that is no longer DRAFT refuses a write with campaign_not_draft, in plain words', async () => {
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  // Someone activates it while this screen still shows DRAFT.
  stored = 'ACTIVE';
  await page.locator('#budget-total_amount').fill('200000.00');
  await page.getByTestId('budget-save').click();
  const budget = section(page, 'budget');
  await expect(budget.getByTestId('error-code')).toHaveText('campaign_not_draft');
  await expect(budget.getByTestId('error-message')).toHaveText(PLAIN_MESSAGES.campaign_not_draft as string);
  await expect(budget.getByTestId('error-correlation')).toHaveText('corr-campaign-409');
  await app.close();
});

test('outside DRAFT every control of the plan is drawn disabled, and a forced write is refused', async () => {
  stored = 'ACTIVE';
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  await expect(page.getByTestId('plan-locked')).toHaveText(
    "A ACTIVE campaign's plan cannot be changed: only a DRAFT can.",
  );
  await expect(page.locator('#budget-total_amount')).toHaveValue('150000.00');
  const controls = page.locator('[data-testid=campaign-plan] :is(input, textarea, button[type=submit])');
  expect(await controls.count()).toBeGreaterThan(8);
  for (const control of await controls.all()) await expect(control).toBeDisabled();
  const forced = await page.evaluate(() =>
    window.mosaiq.putCampaignBudget(301, { total_amount: '1.00', per_customer_cap: '' }),
  );
  expect(forced).toMatchObject({ ok: false, error: { code: 'campaign_not_draft' } });
  await app.close();
});

for (const as of ['ANALYST', 'AUDITOR']) {
  test(`${as} reads the plan and previews the audience, sees no control, and a forced write is refused`, async () => {
    const { app, page } = await launch(as);
    await openPlan(page);
    await expect(page.getByTestId('audience-cap')).toHaveText('5000');
    await expect(page.getByTestId('conditions-registered')).toHaveText('2026-06-30');
    await expect(page.getByTestId('conditions-purchases')).toHaveText(
      'At least 2 in the 180 days before the request',
    );
    await expect(page.getByTestId('exclusions-customers').locator('li')).toHaveText([CUSTOMER_A, CUSTOMER_B]);
    await expect(page.getByTestId('exclusions-labels')).toHaveText('DORMANT');
    await expect(page.getByTestId('budget-total')).toHaveText('150000.00');
    await expect(page.getByTestId('budget-per-customer')).toHaveText('300.00');
    for (const form of ['audience', 'conditions', 'exclusions', 'budget'])
      await expect(page.getByTestId(`${form}-form`)).toHaveCount(0);
    await expect(page.locator('[data-testid=campaign-plan] :is(textarea, button[type=submit])')).toHaveCount(
      0,
    );
    await page.getByTestId('preview-run').click();
    await expect(page.getByTestId('preview-size')).toHaveText(`${IN_AUDIENCE} customers`);
    await shot(page, `878-desktop-campaign-plan-05-${as.toLowerCase()}-read-only`);
    const forced = await page.evaluate(() =>
      window.mosaiq.putCampaignExclusions(301, { customer_ids: [], label_codes: [] }),
    );
    expect(forced).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role', message: PLAIN_MESSAGES.forbidden_role },
    });
    expect(planWrites().length).toBe(1);
    await app.close();
  });
}

test('a section whose read is refused shows it in place, and the others stay usable', async () => {
  unreadable = 'budget';
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  const budget = section(page, 'budget');
  await expect(budget.getByTestId('error-code')).toHaveText('forbidden_role');
  await expect(budget.getByTestId('error-message')).toHaveText(PLAIN_MESSAGES.forbidden_role as string);
  await expect(page.locator('#audience-max_customers')).toHaveValue('5000');
  await page.locator('#audience-max_customers').fill('7');
  await page.getByTestId('audience-save').click();
  await expect(section(page, 'audience').getByTestId('notice')).toHaveText('Audience saved.');
  // Reading again after the refusal is gone shows the budget.
  unreadable = null;
  await budget.getByTestId('plan-budget-reload').click();
  await expect(page.locator('#budget-total_amount')).toHaveValue('150000.00');
  await app.close();
});

test("activating keeps the existing rules: the moves are the service's, and the plan locks once ACTIVE", async () => {
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  await expect(page.locator('[data-testid=campaign-transitions] button')).toHaveText([
    'Move to ACTIVE',
    'Move to CANCELLED',
  ]);
  await page.getByTestId('transition-ACTIVE').click();
  await expect(page.getByTestId('campaign-status')).toHaveText('ACTIVE');
  const move = seen.filter((s) => s.url.endsWith('/transitions')).at(-1) as Seen;
  expect(fieldsOf(move.body)).toEqual(fieldsOf(example('campaigns', 'draft-to-active.request')));
  await expect(page.getByTestId('plan-locked')).toBeVisible();
  await expect(page.locator('#budget-total_amount')).toBeDisabled();
  await expect(page.locator('[data-testid=campaign-transitions] button')).toHaveText([
    'Move to FINISHED',
    'Move to CANCELLED',
  ]);
  // The plan saves nothing by itself and offers no move of its own.
  expect(planWrites()).toHaveLength(0);
  await app.close();
});

test('what a service returns is shown as text, never as markup', async () => {
  const markup = '<img src=x onerror=alert(1)>';
  plan.labels = [markup];
  plan.customers = [];
  const { app, page } = await launch('MARKETING');
  await openPlan(page);
  await page.getByTestId('preview-run').click();
  await expect(page.getByTestId('preview-reason-labels')).toBeVisible();
  await expect(page.locator('[data-testid=campaign-plan] img')).toHaveCount(0);
  expect(await page.getByTestId('plan-exclusions').locator('label', { hasText: 'onerror' }).count()).toBe(1);
  await app.close();
});
