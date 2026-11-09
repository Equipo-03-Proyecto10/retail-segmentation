import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { CampaignPlan } from '../../src/main/campaign-plan';
import { XsdValidator } from '../../src/main/xml';
import { AUDIENCE_REASONS } from '../../src/shared/api';
import {
  leftOut,
  PLAIN_MESSAGES,
  plainDetail,
  plainRefusal,
  reasonsOf,
  reasonText,
} from '../../src/shared/campaign-messages';
import { bundle, claims, errorXml, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Reply, Responder, Seen } from '../support/fake';

// A campaign's audience, budget, conditions and exclusions (F28-09, #878), against the recorded
// examples of contracts/campaigns/. Campaign 12 is the one the examples describe.

const NS = 'https://mosaiq.maxthecoder.online/xml/campaigns/v1';
const CONTRACTS = new URL('../../../../contracts', import.meta.url).pathname;
const CUSTOMER_A = '6f1c2a9e-3b44-4d1e-9a52-0c7d8e1f2a3b';
const CUSTOMER_B = 'b7d9e0f4-52a1-4c38-8e6d-1f2a3b4c5d6e';

async function signedIn(role: string, responder: Responder): Promise<{ plan: CampaignPlan; seen: Seen[] }> {
  const { office, seen } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
      return responder(r);
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  seen.length = 0;
  return { plan: new CampaignPlan(office), seen };
}

const answer =
  (status: number, file: string): Responder =>
  () => ({ status, body: example('campaigns', file) });

/** Each sub-resource answers with its recorded example, on a read and on a write. */
const STORED: Record<string, string> = {
  audience: 'campaign-audience',
  budget: 'campaign-budget',
  'eligibility-rules': 'campaign-eligibility-rules',
  exclusions: 'campaign-exclusions',
  breakdown: 'campaign-audience-breakdown',
};
const stored = (r: Seen): Reply => {
  const part = new URL(r.url).pathname.split('/').pop() as string;
  if (part === 'segments') return { status: 200, body: example('segments', 'list-segments') };
  return { status: 200, body: example('campaigns', STORED[part] as string) };
};

const paths = (seen: Seen[]): string[] => seen.map((s) => `${s.method} ${new URL(s.url).pathname}`);

/** The element names and values of a request body, in order, so a recorded request compares to a sent one. */
const fieldsOf = (xml: string): [string, string][] =>
  [...xml.matchAll(/<([a-z_]+)>([^<]*)<\/\1>/g)].map((m) => [m[1] as string, m[2] as string]);
const recorded = (name: string): [string, string][] => fieldsOf(example('campaigns', name));

describe('reading', () => {
  it('reads the audience with its cap, its size and when it was counted', async () => {
    const { plan, seen } = await signedIn('AUDITOR', stored);
    const res = await plan.getAudience(12);
    expect(paths(seen)).toEqual(['GET /api/campaigns/v1/campaigns/12/audience']);
    expect(res).toEqual({
      ok: true,
      value: {
        campaign_id: 12,
        label_code: 'LOYAL',
        max_customers: 5000,
        audience_size: 3120,
        as_of: '2026-11-02T15:04:05Z',
      },
    });
  });

  it('shows an audience that was never written without a cap', async () => {
    const body = example('campaigns', 'campaign-audience').replace(
      /<max_customers>.*<\/max_customers>\s*/,
      '',
    );
    const { plan } = await signedIn('ANALYST', () => ({ status: 200, body }));
    const res = await plan.getAudience(12);
    expect(res.ok && 'max_customers' in res.value).toBe(false);
  });

  it('reads the budget as two-decimal text, never as a number', async () => {
    const { plan, seen } = await signedIn('ANALYST', stored);
    const res = await plan.getBudget(12);
    expect(paths(seen)).toEqual(['GET /api/campaigns/v1/campaigns/12/budget']);
    expect(res).toEqual({
      ok: true,
      value: { campaign_id: 12, total_amount: '150000.00', per_customer_cap: '300.00' },
    });
  });

  it('reads the conditions, and a campaign with none has only its id', async () => {
    const { plan, seen } = await signedIn('MARKETING', stored);
    expect(await plan.getEligibility(12)).toEqual({
      ok: true,
      value: {
        campaign_id: 12,
        registered_on_or_before: '2026-06-30',
        min_purchases: 2,
        purchase_window_days: 180,
      },
    });
    expect(paths(seen)).toEqual(['GET /api/campaigns/v1/campaigns/12/eligibility-rules']);
    const bare = example('campaigns', 'campaign-eligibility-rules').replace(
      /<(registered_on_or_before|min_purchases|purchase_window_days)>.*<\/\1>\s*/g,
      '',
    );
    const none = await signedIn('MARKETING', () => ({ status: 200, body: bare }));
    expect(await none.plan.getEligibility(12)).toEqual({ ok: true, value: { campaign_id: 12 } });
  });

  it('reads the exclusions as lists, one entry or none included', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    expect(await plan.getExclusions(12)).toEqual({
      ok: true,
      value: { campaign_id: 12, customer_ids: [CUSTOMER_A, CUSTOMER_B], label_codes: ['DORMANT'] },
    });
    expect(paths(seen)).toEqual(['GET /api/campaigns/v1/campaigns/12/exclusions']);
    const one = example('campaigns', 'campaign-exclusions')
      .replace(/<customer_ids>b7d9.*<\/customer_ids>\s*/, '')
      .replace(/<label_codes>.*<\/label_codes>\s*/, '');
    const single = await signedIn('ADMIN', () => ({ status: 200, body: one }));
    expect(await single.plan.getExclusions(12)).toEqual({
      ok: true,
      value: { campaign_id: 12, customer_ids: [CUSTOMER_A], label_codes: [] },
    });
    const bare = one.replace(/<customer_ids>.*<\/customer_ids>\s*/, '');
    const none = await signedIn('ADMIN', () => ({ status: 200, body: bare }));
    expect(await none.plan.getExclusions(12)).toEqual({
      ok: true,
      value: { campaign_id: 12, customer_ids: [], label_codes: [] },
    });
  });

  it('refuses a malformed campaign id before sending', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    for (const bad of [0, -1, 1.5, '12/budget', undefined, 2147483648])
      expect(await plan.getBudget(bad)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
  });
});

describe('saving', () => {
  it('sends the recorded audience request and shows the audience now stored', async () => {
    const { plan, seen } = await signedIn('MARKETING', stored);
    const res = await plan.putAudience(12, { max_customers: '5000' });
    expect(paths(seen)).toEqual(['PUT /api/campaigns/v1/campaigns/12/audience']);
    expect(fieldsOf(seen[0]!.body as string)).toEqual(recorded('put-campaign-audience.request'));
    expect(res.ok && res.value.audience_size).toBe(3120);
  });

  it('sends the recorded budget request', async () => {
    const { plan, seen } = await signedIn('MARKETING', stored);
    const res = await plan.putBudget(12, { total_amount: '150000.00', per_customer_cap: '300.00' });
    expect(paths(seen)).toEqual(['PUT /api/campaigns/v1/campaigns/12/budget']);
    expect(fieldsOf(seen[0]!.body as string)).toEqual(recorded('put-campaign-budget.request'));
    expect(res.ok && res.value.total_amount).toBe('150000.00');
  });

  it('sends the recorded conditions request', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    const res = await plan.putEligibility(12, {
      registered_on_or_before: '2026-06-30',
      min_purchases: '2',
      purchase_window_days: '180',
    });
    expect(paths(seen)).toEqual(['PUT /api/campaigns/v1/campaigns/12/eligibility-rules']);
    expect(fieldsOf(seen[0]!.body as string)).toEqual(recorded('put-campaign-eligibility-rules.request'));
    expect(res.ok && res.value.min_purchases).toBe(2);
  });

  it('sends the recorded exclusions request, an id in capitals folded to the XSD lower case', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    const res = await plan.putExclusions(12, {
      customer_ids: [CUSTOMER_A.toUpperCase(), ` ${CUSTOMER_B} `],
      label_codes: ['DORMANT'],
    });
    expect(paths(seen)).toEqual(['PUT /api/campaigns/v1/campaigns/12/exclusions']);
    expect(fieldsOf(seen[0]!.body as string)).toEqual(recorded('put-campaign-exclusions.request'));
    expect(res.ok && res.value.customer_ids).toEqual([CUSTOMER_A, CUSTOMER_B]);
  });

  it('removes a setting by sending an empty body, which the XSD accepts', async () => {
    const { plan, seen } = await signedIn('MARKETING', stored);
    expect((await plan.putAudience(12, { max_customers: '' })).ok).toBe(true);
    expect((await plan.putBudget(12, { total_amount: '', per_customer_cap: '' })).ok).toBe(true);
    expect(
      (
        await plan.putEligibility(12, {
          registered_on_or_before: '',
          min_purchases: '',
          purchase_window_days: '',
        })
      ).ok,
    ).toBe(true);
    expect((await plan.putExclusions(12, { customer_ids: [], label_codes: [] })).ok).toBe(true);
    expect(seen).toHaveLength(4);
    for (const s of seen) expect(s.body).toMatch(/^<\?xml[^>]*\?><Put\w+ xmlns="[^"]+"\/>$/);
    const validator = new XsdValidator(bundle);
    for (const s of seen) expect((await validator.validate(s.body as string, NS)).valid).toBe(true);
  });

  it('sends one amount alone, and a per-customer limit equal to the total', async () => {
    const { plan, seen } = await signedIn('MARKETING', stored);
    await plan.putBudget(12, { total_amount: '', per_customer_cap: '12.50' });
    await plan.putBudget(12, { total_amount: '300.00', per_customer_cap: '300.00' });
    expect(seen.map((s) => fieldsOf(s.body as string))).toEqual([
      [['per_customer_cap', '12.50']],
      [
        ['total_amount', '300.00'],
        ['per_customer_cap', '300.00'],
      ],
    ]);
  });
});

describe('validating before anything is sent', () => {
  const details = (res: Awaited<ReturnType<CampaignPlan['putBudget']>>): unknown =>
    res.ok ? null : res.error.details;

  it('refuses a cap that is not a whole number from 1 to 10,000,000', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    for (const [value, code] of [
      ['12.5', 'invalid_format'],
      ['-3', 'invalid_format'],
      ['many', 'invalid_format'],
      ['0', 'out_of_range'],
      ['10000001', 'out_of_range'],
    ] as const) {
      const res = await plan.putAudience(12, { max_customers: value });
      expect(res).toMatchObject({
        ok: false,
        error: { code: 'invalid_body', details: [{ field: 'max_customers', code }] },
      });
    }
    expect((await plan.putAudience(12, { max_customers: '10000000' })).ok).toBe(true);
    expect(seen).toHaveLength(1);
  });

  it('refuses amounts that are not two-decimal text, and a per-customer limit above the total', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    for (const bad of ['300', '300.5', '300.000', '-1.00', '1,50', 'ten', '.50'])
      expect(details(await plan.putBudget(12, { total_amount: bad, per_customer_cap: '' }))).toEqual([
        { field: 'total_amount', code: 'invalid_format' },
      ]);
    expect(details(await plan.putBudget(12, { total_amount: '100.00', per_customer_cap: '100.01' }))).toEqual(
      [{ field: 'per_customer_cap', code: 'exceeds_total_amount' }],
    );
    expect(details(await plan.putBudget(12, { total_amount: '9.99', per_customer_cap: '1000.00' }))).toEqual([
      { field: 'per_customer_cap', code: 'exceeds_total_amount' },
    ]);
    expect(seen).toHaveLength(0);
  });

  it('refuses conditions out of range, a date that is not on the calendar, and one purchase rule alone', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    const conditions = (r: string, m: string, d: string) =>
      plan.putEligibility(12, { registered_on_or_before: r, min_purchases: m, purchase_window_days: d });
    const fields = async (r: string, m: string, d: string): Promise<unknown> => {
      const res = await conditions(r, m, d);
      return res.ok ? null : res.error.details;
    };
    expect(await fields('2026-02-31', '', '')).toEqual([
      { field: 'registered_on_or_before', code: 'invalid_format' },
    ]);
    expect(await fields('30/06/2026', '', '')).toEqual([
      { field: 'registered_on_or_before', code: 'invalid_format' },
    ]);
    expect(await fields('', '0', '30')).toEqual([{ field: 'min_purchases', code: 'out_of_range' }]);
    expect(await fields('', '1001', '30')).toEqual([{ field: 'min_purchases', code: 'out_of_range' }]);
    expect(await fields('', '2', '732')).toEqual([{ field: 'purchase_window_days', code: 'out_of_range' }]);
    expect(await fields('', '2', '')).toEqual([{ field: 'purchase_window_days', code: 'required_together' }]);
    expect(await fields('', '', '30')).toEqual([{ field: 'min_purchases', code: 'required_together' }]);
    expect(seen).toHaveLength(0);
    expect((await conditions('2028-02-29', '1000', '731')).ok).toBe(true);
    expect(seen).toHaveLength(1);
  });

  it('refuses ids that are not UUIDs, an empty entry, a repeated entry and more than the limits allow', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    const fields = async (customers: string[], labels: string[]): Promise<unknown> => {
      const res = await plan.putExclusions(12, { customer_ids: customers, label_codes: labels });
      return res.ok ? null : res.error.details;
    };
    expect(await fields(['not-a-uuid'], [])).toEqual([{ field: 'customer_ids', code: 'invalid_format' }]);
    expect(await fields([CUSTOMER_A, ''], [])).toEqual([{ field: 'customer_ids', code: 'required' }]);
    expect(await fields([CUSTOMER_A, CUSTOMER_A.toUpperCase()], [])).toEqual([
      { field: 'customer_ids', code: 'duplicate' },
    ]);
    expect(await fields([], ['A', 'B', 'A'])).toEqual([{ field: 'label_codes', code: 'duplicate' }]);
    expect(await fields([], ['x'.repeat(41)])).toEqual([{ field: 'label_codes', code: 'too_long' }]);
    const many = Array.from(
      { length: 1001 },
      (_, i) => `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`,
    );
    expect(await fields(many, [])).toEqual([{ field: 'customer_ids', code: 'max_items' }]);
    expect(
      await fields(
        [],
        Array.from({ length: 21 }, (_, i) => `L${i}`),
      ),
    ).toEqual([{ field: 'label_codes', code: 'max_items' }]);
    expect(seen).toHaveLength(0);
    // Exactly the limits are sent.
    expect((await plan.putExclusions(12, { customer_ids: many.slice(0, 1000), label_codes: [] })).ok).toBe(
      true,
    );
    expect(seen).toHaveLength(1);
  });

  it('names every wrong field at once, and refuses a draft that is not complete', async () => {
    const { plan, seen } = await signedIn('ADMIN', stored);
    const res = await plan.putBudget(12, { total_amount: 'x', per_customer_cap: 'y' });
    expect(!res.ok && res.error.details).toEqual([
      { field: 'total_amount', code: 'invalid_format' },
      { field: 'per_customer_cap', code: 'invalid_format' },
    ]);
    expect(await plan.putBudget(12, { total_amount: '1.00' })).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(await plan.putAudience(12, undefined)).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(await plan.putExclusions(12, { customer_ids: 'x', label_codes: [] })).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(seen).toHaveLength(0);
  });
});

describe('what the service refuses', () => {
  const REFUSALS = [
    ['campaign_not_draft', 409, 'campaign-not-draft'],
    ['campaign_not_found', 404, 'campaign-not-found'],
    ['forbidden_role', 403, 'forbidden-role'],
    ['invalid_body', 422, 'invalid-plan-body'],
    ['invalid_body', 422, 'invalid-audience-body'],
    ['invalid_body', 422, 'invalid-exclusions-body'],
    ['unknown_customer', 422, 'unknown-customer'],
    ['unknown_label', 422, 'unknown-label'],
  ] as const;

  it.each(REFUSALS)(
    'shows %s (%i, %s) in plain words, with its code and correlation id as returned',
    async (code, status, file) => {
      const { plan } = await signedIn('ADMIN', answer(status, file));
      const res = await plan.putBudget(12, { total_amount: '10.00', per_customer_cap: '' });
      expect(res).toMatchObject({ ok: false, error: { code, message: PLAIN_MESSAGES[code] } });
      const returned = fieldsOfError(example('campaigns', file));
      expect(!res.ok && res.error.correlation_id).toBe(returned.correlation_id);
      expect(!res.ok && res.error.details).toEqual(returned.details);
    },
  );

  it('shows a refusal of any of the four writes and of every read the same way', async () => {
    const { plan } = await signedIn('ADMIN', answer(409, 'campaign-not-draft'));
    for (const res of [
      await plan.putAudience(12, { max_customers: '5' }),
      await plan.putBudget(12, { total_amount: '5.00', per_customer_cap: '' }),
      await plan.putEligibility(12, {
        registered_on_or_before: '2026-01-01',
        min_purchases: '',
        purchase_window_days: '',
      }),
      await plan.putExclusions(12, { customer_ids: [], label_codes: ['X'] }),
    ])
      expect(res).toMatchObject({ ok: false, error: { code: 'campaign_not_draft' } });
    const missing = await signedIn('ANALYST', answer(404, 'campaign-not-found'));
    for (const res of [
      await missing.plan.getAudience(1),
      await missing.plan.getBudget(1),
      await missing.plan.getEligibility(1),
      await missing.plan.getExclusions(1),
      await missing.plan.preview(1),
    ])
      expect(res).toMatchObject({ ok: false, error: { code: 'campaign_not_found' } });
  });

  it('keeps the service sentence for a code it has no plain words for', () => {
    const error = {
      code: 'something_new',
      message: 'As the service said.',
      correlation_id: 'c',
      details: [],
    };
    expect(plainRefusal(error)).toEqual(error);
  });

  it('words every field-level reason a refusal can carry', () => {
    expect(plainDetail({ field: 'per_customer_cap', code: 'exceeds_total_amount' })).toBe(
      'The per-customer limit cannot be larger than the total budget.',
    );
    expect(plainDetail({ field: 'customer_ids', code: 'max_items' })).toBe(
      'The customer ids has more entries than are allowed.',
    );
    expect(plainDetail({ field: 'audience_size', code: 'additional_property' })).toBe(
      'audience_size cannot be set here.',
    );
    expect(plainDetail({ field: 'label_code', code: 'unknown' })).toBe('The label is not known.');
    expect(plainDetail({ field: 'ends_on', code: 'before_start' })).toBe(
      'The end date cannot be before the start date.',
    );
    expect(plainDetail({ field: 'x', code: 'strange' })).toBe('x was refused (strange).');
  });
});

/** Code, correlation id and details of a recorded common/v1 Error. */
function fieldsOfError(xml: string): { correlation_id: string; details: { field: string; code: string }[] } {
  const correlation_id = /<correlation_id>([^<]*)</.exec(xml)?.[1] ?? '';
  const details = [
    ...xml.matchAll(/<details>\s*<field>([^<]*)<\/field>\s*<code>([^<]*)<\/code>\s*<\/details>/g),
  ].map((m) => ({ field: m[1] as string, code: m[2] as string }));
  return { correlation_id, details };
}

describe('every code of the contract', () => {
  /** Codes worded by the shell, not by the campaigns screens: a session that ended, a dependency that is down. */
  const SHELL = ['invalid_token', 'session_revoked', 'auth_unavailable', 'database_unavailable'];

  /** The `code` of every example under every error response a /v1/campaigns operation lists. */
  function codesOfTheContract(): Set<string> {
    const base = join(CONTRACTS, 'campaigns');
    const doc = parse(readFileSync(join(base, 'openapi.yaml'), 'utf8')) as {
      paths: Record<string, Record<string, { responses?: Record<string, { $ref?: string }> }>>;
      components: {
        responses: Record<string, { content: Record<string, { examples: Record<string, unknown> }> }>;
      };
    };
    const codes = new Set<string>();
    for (const [path, item] of Object.entries(doc.paths)) {
      if (!path.startsWith('/v1/campaigns')) continue;
      for (const op of Object.values(item)) {
        for (const response of Object.values(op.responses ?? {})) {
          if (response.$ref === undefined) continue;
          const found = doc.components.responses[response.$ref.split('/').pop() as string];
          for (const e of Object.values(found?.content['application/json']?.examples ?? {})) {
            const ex = e as { value?: { code: string }; externalValue?: string };
            const code =
              ex.value?.code ??
              (JSON.parse(readFileSync(join(base, ex.externalValue as string), 'utf8')) as { code: string })
                .code;
            codes.add(code);
          }
        }
      }
    }
    return codes;
  }

  it('has a plain sentence, or is worded by the shell', () => {
    const codes = codesOfTheContract();
    // The contract lists these today; a code added to it fails here until it is worded.
    expect([...codes].sort()).toEqual(
      [
        'auth_unavailable',
        'campaign_ended',
        'campaign_not_draft',
        'campaign_not_found',
        'database_unavailable',
        'duplicate_draft',
        'ends_before_start',
        'experiment_not_ready',
        'forbidden_role',
        'illegal_transition',
        'invalid_body',
        'invalid_pagination',
        'invalid_token',
        'malformed_body',
        'not_acceptable',
        'payload_too_large',
        'rate_limited',
        'session_revoked',
        'status_changed',
        'unknown_customer',
        'unknown_label',
        'unsupported_media_type',
      ].sort(),
    );
    const unworded = [...codes].filter((c) => PLAIN_MESSAGES[c] === undefined && !SHELL.includes(c));
    expect(unworded).toEqual([]);
  });

  it('words each code as one sentence that names no field of the wire', () => {
    for (const message of Object.values(PLAIN_MESSAGES)) {
      expect(message).toMatch(/^[A-Z].*[.]$/);
      expect(message).not.toMatch(/[a-z]+_[a-z_]+/);
    }
  });
});

describe('the preview', () => {
  const BREAKDOWN = 'GET /api/campaigns/v1/campaigns/12/audience/breakdown';
  const READS = [
    'GET /api/campaigns/v1/campaigns/12/audience',
    'GET /api/campaigns/v1/campaigns/12/eligibility-rules',
    'GET /api/campaigns/v1/campaigns/12/exclusions',
  ];
  const name = (code: string): string => (code === 'DORMANT' ? 'Dormant (DORMANT)' : code);
  /** Everything answered from the recorded examples, except an audience that is 25 customers strong. */
  const previewResponder: Responder = (r) => {
    const reply = stored(r);
    return reply.body !== undefined && r.url.endsWith('/audience')
      ? { ...reply, body: reply.body.replace('<audience_size>3120', '<audience_size>25') }
      : reply;
  };
  /** The breakdown is answered as given, and everything else as above. */
  const breakdownAnswers =
    (reply: Reply | ((r: Seen) => Reply)): Responder =>
    (r) =>
      r.url.endsWith('/audience/breakdown')
        ? typeof reply === 'function'
          ? reply(r)
          : reply
        : previewResponder(r);

  it('shows each reason with its count, read from the breakdown, and no longer reads the label holders', async () => {
    const { plan, seen } = await signedIn('MARKETING', stored);
    const res = await plan.preview(12);
    expect(paths(seen)).toEqual([BREAKDOWN, ...READS]);
    if (!res.ok) throw new Error('a preview was expected');
    expect(res.value.breakdown).toEqual({
      campaign_id: 12,
      label_code: 'LOYAL',
      label_holders: 4000,
      reasons: [
        { code: 'not_in_customer_reference', excluded: 12 },
        { code: 'excluded_customer', excluded: 2 },
        { code: 'excluded_label', excluded: 0 },
        { code: 'registered_after_cutoff', excluded: 310 },
        { code: 'too_few_purchases', excluded: 556 },
        { code: 'over_max_customers', excluded: 0 },
      ],
      audience_size: 3120,
      as_of: '2026-11-02T15:04:05Z',
    });
    expect(res.value).toMatchObject({
      audience: { max_customers: 5000 },
      eligibility: { min_purchases: 2 },
      exclusions: { label_codes: ['DORMANT'] },
    });
    expect('breakdown_error' in res.value).toBe(false);
    expect('label_holders' in res.value).toBe(false);
  });

  it('keeps the contract identity: the label holders are the reasons plus the audience, once each', async () => {
    const { plan } = await signedIn('MARKETING', stored);
    const res = await plan.preview(12);
    const b = res.ok ? res.value.breakdown : undefined;
    if (b === undefined) throw new Error('a breakdown was expected');
    expect(b.reasons.map((r) => r.code)).toEqual([...AUDIENCE_REASONS]);
    expect(b.reasons.reduce((sum, r) => sum + r.excluded, 0) + b.audience_size).toBe(b.label_holders);
    expect(res.ok && leftOut(res.value)).toBe(880);
  });

  it('words each reason with the rule it applies, and says when that rule is not set', async () => {
    const { plan } = await signedIn('MARKETING', stored);
    const res = await plan.preview(12);
    if (!res.ok) throw new Error('a preview was expected');
    expect(AUDIENCE_REASONS.map((code) => reasonText(code, res.value, name))).toEqual([
      'Not in the customer list',
      'Excluded by id (2 listed)',
      'Excluded by label (Dormant (DORMANT))',
      'Registered after 2026-06-30',
      'Fewer than 2 net purchases in the 180 days before the preview',
      'Above the cap of 5000 customers',
    ]);
    const unset = {
      ...res.value,
      audience: { campaign_id: 12, label_code: 'LOYAL', audience_size: 31, as_of: 'x' },
      eligibility: { campaign_id: 12 },
      exclusions: { campaign_id: 12, customer_ids: [], label_codes: [] },
    };
    expect(AUDIENCE_REASONS.map((code) => reasonText(code, unset, name))).toEqual([
      'Not in the customer list',
      'Excluded by id (none listed)',
      'Excluded by label (none listed)',
      'Registered after the cutoff (no cutoff set)',
      'Too few purchases (no purchase condition set)',
      'Above the cap (no cap set)',
    ]);
    const one = {
      ...res.value,
      eligibility: { campaign_id: 12, min_purchases: 1, purchase_window_days: 30 },
    };
    expect(reasonText('too_few_purchases', one, name)).toBe(
      'Fewer than 1 net purchase in the 30 days before the preview',
    );
  });

  it('falls back to the audience total and the label holders when the breakdown is refused, and says why', async () => {
    const { plan, seen } = await signedIn(
      'MARKETING',
      breakdownAnswers({ status: 403, body: example('campaigns', 'forbidden-role') }),
    );
    const res = await plan.preview(12);
    expect(paths(seen)).toEqual([BREAKDOWN, ...READS, 'GET /api/segments/v1/segments']);
    if (!res.ok) throw new Error('a preview was expected');
    expect('breakdown' in res.value).toBe(false);
    expect(res.value.breakdown_error).toMatchObject({
      code: 'forbidden_role',
      message: PLAIN_MESSAGES.forbidden_role,
      correlation_id: 'corr-role-001',
    });
    expect(res.value).toMatchObject({ audience: { audience_size: 25 }, label_holders: 31 });
    // 31 customers hold LOYAL and 25 are in the audience: 6 are left out, in one total.
    expect(leftOut(res.value)).toBe(6);
  });

  it('falls back the same way while the operation is not implemented, whatever the service answers', async () => {
    const notFound = await signedIn(
      'MARKETING',
      breakdownAnswers({ status: 404, body: errorXml('not_found', 'No route matches.') }),
    );
    const missing = await notFound.plan.preview(12);
    expect(missing.ok && missing.value.breakdown_error?.code).toBe('not_found');
    expect(missing.ok && leftOut(missing.value)).toBe(6);

    // Not the contract's XML at all: a contract defect, which is also a reason to fall back.
    const html = await signedIn('MARKETING', breakdownAnswers({ status: 404, body: '<html>nope</html>' }));
    const defect = await html.plan.preview(12);
    expect(defect.ok && defect.value.breakdown_error?.code).toBe('contract_defect');
    expect(defect.ok && 'breakdown' in defect.value).toBe(false);
    expect(defect.ok && leftOut(defect.value)).toBe(6);
  });

  it('does not fall back when the session has ended', async () => {
    const { plan, seen } = await signedIn(
      'MARKETING',
      breakdownAnswers({
        status: 401,
        body: errorXml('session_revoked', 'The token session is no longer live.'),
      }),
    );
    expect(await plan.preview(12)).toMatchObject({ ok: false, error: { code: 'session_revoked' } });
    expect(paths(seen)).toEqual([BREAKDOWN]);
  });

  it('never reports a negative number left out in the fallback, and may lack the label holders', async () => {
    const refused = { status: 403, body: example('campaigns', 'forbidden-role') };
    const larger = await signedIn('MARKETING', (r) =>
      r.url.endsWith('/audience/breakdown') ? refused : stored(r),
    );
    const res = await larger.plan.preview(12);
    // The audience was counted after the label changed: 3120 against 31 holders.
    expect(res.ok && res.value.audience.audience_size).toBe(3120);
    expect(res.ok && leftOut(res.value)).toBe(0);

    const down = await signedIn('MARKETING', (r) =>
      r.url.endsWith('/audience/breakdown') || r.url.endsWith('/segments')
        ? { status: 403, body: example('segments', 'forbidden-role') }
        : previewResponder(r),
    );
    const noHolders = await down.plan.preview(12);
    expect(noHolders.ok && 'label_holders' in noHolders.value).toBe(false);
    expect(noHolders.ok && leftOut(noHolders.value)).toBeUndefined();
    const unknown = await signedIn('MARKETING', (r) =>
      r.url.endsWith('/audience/breakdown')
        ? refused
        : r.url.endsWith('/segments')
          ? { status: 200, body: example('segments', 'list-segments').replace(/LOYAL/g, 'ELSEWHERE') }
          : previewResponder(r),
    );
    const other = await unknown.plan.preview(12);
    expect(other.ok && 'label_holders' in other.value).toBe(false);
  });

  it('states each rule that can leave customers out in the fallback, and none when nothing is set', async () => {
    const { plan } = await signedIn(
      'MARKETING',
      breakdownAnswers({ status: 403, body: example('campaigns', 'forbidden-role') }),
    );
    const res = await plan.preview(12);
    if (!res.ok) throw new Error('a preview was expected');
    expect(reasonsOf(res.value, name).map((r) => [r.id, r.text])).toEqual([
      ['customers', 'Excluded by id: 2 customers are listed and left out.'],
      ['labels', 'Excluded by label: holders of Dormant (DORMANT) are left out.'],
      ['registered', 'Condition: only customers registered on or before 2026-06-30.'],
      [
        'purchases',
        'Condition: only customers with at least 2 net purchases in the 180 days before the preview. A sale whose net amount is zero does not count.',
      ],
      ['cap', 'Cap: the audience is cut to at most 5000 customers.'],
    ]);
    const empty = {
      ...res.value,
      audience: { campaign_id: 12, label_code: 'LOYAL', audience_size: 31, as_of: 'x' },
      eligibility: { campaign_id: 12 },
      exclusions: { campaign_id: 12, customer_ids: [], label_codes: [] },
    };
    expect(reasonsOf(empty, name).map((r) => r.id)).toEqual(['none']);
    expect(leftOut(empty)).toBe(0);
  });

  it('shows a refusal of the audience as returned when the breakdown was refused too, and reads nothing more', async () => {
    const { plan, seen } = await signedIn('MARKETING', answer(404, 'campaign-not-found'));
    expect(await plan.preview(99)).toMatchObject({ ok: false, error: { code: 'campaign_not_found' } });
    expect(paths(seen)).toEqual([
      'GET /api/campaigns/v1/campaigns/99/audience/breakdown',
      'GET /api/campaigns/v1/campaigns/99/audience',
    ]);
  });

  it('refuses a malformed campaign id before sending', async () => {
    const { plan, seen } = await signedIn('MARKETING', stored);
    expect(await plan.preview('12/budget')).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(await plan.getAudienceBreakdown(0)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
  });
});

describe('the XSD', () => {
  it('accepts every recorded plan example and request', async () => {
    const validator = new XsdValidator(bundle);
    for (const f of [
      'campaign-audience',
      'campaign-budget',
      'campaign-eligibility-rules',
      'campaign-exclusions',
      'campaign-audience-breakdown',
      'put-campaign-audience.request',
      'put-campaign-budget.request',
      'put-campaign-eligibility-rules.request',
      'put-campaign-exclusions.request',
    ])
      expect((await validator.validate(example('campaigns', f), NS)).valid, f).toBe(true);
  });

  it('refuses a breakdown with a reason the contract does not name, and one with a reason missing', async () => {
    const validator = new XsdValidator(bundle);
    for (const f of [
      'campaign-audience-breakdown-unknown-reason.invalid',
      'campaign-audience-breakdown-missing-reason.invalid',
    ])
      expect((await validator.validate(example('campaigns', f), NS)).valid, f).toBe(false);
  });
});

describe('the roles', () => {
  const operation = (operationId: string) => bundle.operations.find((o) => o.operationId === operationId);

  it('reads for the four roles that read campaigns and writes for ADMIN and MARKETING, as the contract says', () => {
    for (const part of ['Audience', 'Budget', 'EligibilityRules', 'Exclusions']) {
      expect(operation(`getCampaign${part}`)?.roles?.slice().sort()).toEqual([
        'ADMIN',
        'ANALYST',
        'AUDITOR',
        'MARKETING',
      ]);
      expect(operation(`putCampaign${part}`)?.roles?.slice().sort()).toEqual(['ADMIN', 'MARKETING']);
    }
  });

  it('reads the breakdown for the same four roles as the audience', () => {
    expect(operation('getCampaignAudienceBreakdown')?.roles?.slice().sort()).toEqual([
      'ADMIN',
      'ANALYST',
      'AUDITOR',
      'MARKETING',
    ]);
    expect(operation('getCampaignAudienceBreakdown')).toMatchObject({
      method: 'GET',
      path: '/v1/campaigns/{campaign_id}/audience/breakdown',
    });
  });

  it('is asked about only operations the contract has, by the id it gives them', () => {
    const source = readFileSync(
      new URL('../../src/renderer/campaign-plan.ts', import.meta.url).pathname,
      'utf8',
    );
    const asked = [...source.matchAll(/'((?:get|put)Campaign\w+)'/g)].map((m) => m[1] as string);
    expect(new Set(asked).size).toBe(8);
    for (const id of asked) expect(operation(id), id).toBeDefined();
  });
});
