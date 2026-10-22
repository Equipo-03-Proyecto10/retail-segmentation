import { describe, expect, it } from 'vitest';
import { Customers } from '../../src/main/customers';
import { claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// Customers, preferences, segments and recommendations (F16-03, #414), against the
// recorded examples of their contracts.

const CUSTOMER = '7b3ad781-c5de-4f40-ae50-1fa825ce2b25';

async function signedIn(role: string, responder: Responder): Promise<{ customers: Customers; seen: Seen[] }> {
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
  return { customers: new Customers(office), seen };
}

const route =
  (table: Record<string, [number, string]>): Responder =>
  (r) => {
    const url = new URL(r.url);
    const hit = table[`${r.method} ${url.pathname.replace('/api/', '')}`] ?? table[`${r.method} *`];
    if (hit === undefined) return { status: 500, body: '' };
    const [status, body] = hit;
    return { status, body };
  };

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

const CHANNELS = [200, example('sales', 'list-channels')] as [number, string];

describe('customers', () => {
  it('searches with q, email and paging, naming each registration channel from sales-service', async () => {
    const { customers, seen } = await signedIn(
      'ANALYST',
      route({
        'GET customers/v1/customers': [200, example('customers', 'search-literal-percent')],
        'GET sales/v1/channels': CHANNELS,
      }),
    );
    const res = await customers.search({
      q: ' 100% Club_ ',
      email: 'club.member@example.com',
      page: 2,
      size: 50,
    });
    expect(paths(seen)[0]).toBe(
      'GET /api/customers/v1/customers?q=100%25+Club_&email=club.member%40example.com&page=2&size=50',
    );
    expect(res.ok && res.value.items[0]).toMatchObject({
      name: '100% Club_member',
      registration_channel_name: 'physical_store',
    });
    // The channels are read once for the whole page.
    expect(paths(seen).filter((p) => p.includes('/channels'))).toHaveLength(1);
  });

  it('opens one customer, and leaves out an email the service did not send', async () => {
    const { customers } = await signedIn(
      'MARKETING',
      route({
        ['GET customers/v1/customers/' + CUSTOMER]: [200, example('customers', 'get-customer')],
        'GET sales/v1/channels': CHANNELS,
      }),
    );
    const res = await customers.get(CUSTOMER);
    expect(res.ok && 'email' in res.value).toBe(false);
  });

  it('corrects with PATCH, sending only the given fields', async () => {
    const { customers, seen } = await signedIn(
      'STORE_MANAGER',
      route({
        ['PATCH customers/v1/customers/' + CUSTOMER]: [200, example('customers', 'correct-customer')],
        'GET sales/v1/channels': CHANNELS,
      }),
    );
    const res = await customers.correct(CUSTOMER, { name: 'Ana Torres García', registration_channel_id: 5 });
    expect(res.ok).toBe(true);
    expect(seen[0]!.method).toBe('PATCH');
    expect(seen[0]!.body).toContain(
      '<name>Ana Torres García</name><registration_channel_id>5</registration_channel_id>',
    );
    expect(seen[0]!.body).not.toContain('<email>');
  });

  it.each([
    ['email_taken', 409, 'email-taken'],
    ['unknown_channel', 422, 'unknown-channel'],
    ['invalid_body', 422, 'invalid-body'],
  ])('shows %s as returned', async (code, status, file) => {
    const { customers } = await signedIn('ADMIN', () => ({ status, body: example('customers', file) }));
    const res = await customers.correct(CUSTOMER, { email: 'a@b.co' });
    expect(res).toMatchObject({ ok: false, error: { code } });
    expect(!res.ok && res.error.details).toHaveLength(1);
  });

  it('refuses a bad id, an empty change and an invalid value before sending', async () => {
    const { customers, seen } = await signedIn('ADMIN', () => ({ status: 500, body: '' }));
    expect(await customers.get('x/y')).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(await customers.correct(CUSTOMER, {})).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(await customers.correct(CUSTOMER, { email: 'not an email' })).toMatchObject({
      ok: false,
      error: { code: 'invalid_body', details: [{ field: 'email', code: 'invalid_format' }] },
    });
    expect(await customers.correct(CUSTOMER, { name: '' })).toMatchObject({
      ok: false,
      error: { details: [{ field: 'name', code: 'required' }] },
    });
    expect(await customers.search({ size: 101 })).toMatchObject({ ok: false });
    expect(seen).toHaveLength(0);
  });
});

describe('preferences', () => {
  const table = {
    ['GET customer-preferences/v1/preference-profiles/' + CUSTOMER]: [
      200,
      example('customer-preferences', 'get-profile'),
    ],
    'GET sales/v1/channels': CHANNELS,
    'GET categories/v1/categories': [200, example('categories', 'list-categories')],
  } as Record<string, [number, string]>;

  it('names both sets and offers every channel and category', async () => {
    const { customers } = await signedIn('AUDITOR', route(table));
    const res = await customers.preferences(CUSTOMER);
    expect(res.ok && res.value.channels.map((c) => c.name)).toEqual(['mobile_app', 'physical_store']);
    // Category 2 is not in the page: it stays marked as unavailable, never guessed.
    expect(res.ok && res.value.categories.map((c) => c.name)).toEqual([
      'Category 2 (unavailable)',
      'Cheese',
      'Yogurt',
    ]);
    expect(res.ok && res.value.channel_options).toHaveLength(5);
  });

  it('reads an empty profile as two empty sets', async () => {
    const { customers } = await signedIn(
      'ADMIN',
      route({
        ...table,
        ['GET customer-preferences/v1/preference-profiles/' + CUSTOMER]: [
          200,
          example('customer-preferences', 'get-empty-profile'),
        ],
      }),
    );
    const res = await customers.preferences(CUSTOMER);
    expect(res.ok && [res.value.channels, res.value.categories]).toEqual([[], []]);
  });

  it('sends the whole channel set with PUT, one element per id', async () => {
    const { customers, seen } = await signedIn(
      'ADMIN',
      route({
        ...table,
        ['PUT customer-preferences/v1/preference-profiles/' + CUSTOMER + '/preferred-channels']: [
          200,
          example('customer-preferences', 'replace-preferred-channels'),
        ],
      }),
    );
    const res = await customers.replaceChannels(CUSTOMER, [5, 1, 3]);
    expect(seen[0]!.method).toBe('PUT');
    expect(seen[0]!.body).toContain(
      '<channel_ids>5</channel_ids><channel_ids>1</channel_ids><channel_ids>3</channel_ids>',
    );
    expect(res.ok && res.value.channels.map((c) => c.id)).toEqual([1, 3, 5]);
  });

  it('sends an empty list as a body with no ids, which clears the set', async () => {
    const { customers, seen } = await signedIn(
      'STORE_MANAGER',
      route({
        ...table,
        ['PUT customer-preferences/v1/preference-profiles/' + CUSTOMER + '/interest-categories']: [
          200,
          example('customer-preferences', 'clear-interest-categories'),
        ],
      }),
    );
    const res = await customers.replaceCategories(CUSTOMER, []);
    expect(seen[0]!.body).toMatch(
      /<ReplaceInterestCategoriesRequest [^>]*\/?>(<\/ReplaceInterestCategoriesRequest>)?$/,
    );
    expect(seen[0]!.body).not.toContain('category_ids');
    expect(res.ok && res.value.categories).toEqual([]);
  });

  it.each([
    ['unknown_channel', 'unknown-channel'],
    ['unknown_category', 'unknown-category'],
  ])('shows %s as returned', async (code, file) => {
    const { customers } = await signedIn('ADMIN', () => ({
      status: 422,
      body: example('customer-preferences', file),
    }));
    const res =
      code === 'unknown_channel'
        ? await customers.replaceChannels(CUSTOMER, [99])
        : await customers.replaceCategories(CUSTOMER, [99]);
    expect(res).toMatchObject({ ok: false, error: { code, details: [{ code: 'unknown' }] } });
  });

  it('refuses anything that is not a list of ids', async () => {
    const { customers, seen } = await signedIn('ADMIN', () => ({ status: 500, body: '' }));
    expect(await customers.replaceChannels(CUSTOMER, '1,2')).toMatchObject({ ok: false });
    expect(await customers.replaceCategories(CUSTOMER, [1, 'x'])).toMatchObject({ ok: false });
    expect(await customers.replaceCategories(CUSTOMER, [0])).toMatchObject({ ok: false });
    expect(seen).toHaveLength(0);
  });
});

describe('segments', () => {
  const vocabulary = { 'GET segments/v1/segments': [200, example('segments', 'list-segments')] } as Record<
    string,
    [number, string]
  >;

  it('lists the labels in business order', async () => {
    const { customers } = await signedIn('ANALYST', route(vocabulary));
    const res = await customers.vocabulary();
    expect(res.ok && res.value.labels.map((l) => l.label_code)).toEqual([
      'CHAMPION',
      'LOYAL',
      'POTENTIAL',
      'AT_RISK',
      'HIBERNATING',
      'LOST',
    ]);
    expect(res.ok && res.value.unassigned_count).toBe(14);
  });

  it('shows an assigned customer with its label name, scores, since and run', async () => {
    const { customers, seen } = await signedIn(
      'ANALYST',
      route({
        ...vocabulary,
        'GET segments/v1/segment-assignments/current': [200, example('segments', 'current-assigned')],
      }),
    );
    const res = await customers.current(CUSTOMER);
    expect(paths(seen)[0]).toBe(`GET /api/segments/v1/segment-assignments/current?customer_id=${CUSTOMER}`);
    expect(res).toEqual({
      ok: true,
      value: {
        customer_id: CUSTOMER,
        state: 'assigned',
        label_code: 'LOYAL',
        label_name: 'Loyal',
        r_score: 4,
        f_score: 5,
        m_score: 4,
        since: '2026-10-12T03:00:00Z',
        run_id: 42,
      },
    });
  });

  it.each([
    ['unassigned_result', 'current-unassigned-result', { since: '2026-10-12T03:00:00Z', run_id: 42 }],
    ['no_open_assignment', 'current-no-open-assignment', {}],
  ])('shows %s as returned and adds nothing', async (state, file, rest) => {
    const { customers, seen } = await signedIn(
      'ANALYST',
      route({
        ...vocabulary,
        'GET segments/v1/segment-assignments/current': [200, example('segments', file)],
      }),
    );
    const res = await customers.current(CUSTOMER);
    expect(res).toEqual({ ok: true, value: { customer_id: CUSTOMER, state, ...rest } });
    // No label means the vocabulary is not even read.
    expect(paths(seen).some((p) => p.includes('/segments/v1/segments'))).toBe(false);
  });

  it('pages a customer history, and lists a label with open=true', async () => {
    const { customers, seen } = await signedIn(
      'ANALYST',
      route({
        ...vocabulary,
        'GET segments/v1/segment-assignments': [200, example('segments', 'customer-history')],
      }),
    );
    const res = await customers.assignments({ customer_id: CUSTOMER, page: 1, size: 20 });
    expect(paths(seen)[0]).toBe(
      `GET /api/segments/v1/segment-assignments?customer_id=${CUSTOMER}&page=1&size=20`,
    );
    expect(
      res.ok && res.value.items.map((r) => [r.run_id, r.label_name ?? null, r.valid_to ?? null]),
    ).toEqual([
      [42, 'Loyal', null],
      [41, null, '2026-10-12T03:00:00Z'],
      [40, 'At risk', '2026-09-14T03:00:00Z'],
    ]);
    seen.length = 0;
    await customers.assignments({ label_code: 'LOYAL', open: true });
    expect(paths(seen)[0]).toBe(
      'GET /api/segments/v1/segment-assignments?label_code=LOYAL&open=true&page=1&size=20',
    );
  });

  it('needs exactly one of a customer and a label', async () => {
    const { customers, seen } = await signedIn('ANALYST', () => ({ status: 500, body: '' }));
    expect(await customers.assignments({})).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(await customers.assignments({ customer_id: CUSTOMER, label_code: 'LOYAL' })).toMatchObject({
      ok: false,
    });
    expect(seen).toHaveLength(0);
  });
});

describe('recommendations', () => {
  const table = (file: string): Record<string, [number, string]> => ({
    'GET recommendations/v1/recommendations': [200, example('recommendations', file)],
    'GET products/v1/products': [200, example('products', 'products')],
    'GET categories/v1/categories': [200, example('categories', 'list-categories')],
  });

  it('reads every product name of a list with exactly one products call', async () => {
    const { customers, seen } = await signedIn('MARKETING', route(table('recommended')));
    const res = await customers.recommendations(CUSTOMER);
    const productCalls = paths(seen).filter((p) => p.includes('/products/v1/products'));
    expect(productCalls).toHaveLength(1);
    expect(productCalls[0]).toContain('ids=245%2C120');
    expect(res.ok && res.value.status).toBe('recommended');
    expect(res.ok && res.value.items[0]).toMatchObject({
      product_id: 245,
      quantity_on_hand: 8,
      reasons: [
        { signal: 'segment', segment_buyers: 17 },
        { signal: 'preferred_category', category_id: 10, category_name: 'Category 10 (unavailable)' },
        { signal: 'purchase_history', category_id: 12, category_name: 'Yogurt', share: '0.4' },
      ],
    });
  });

  it.each([
    ['none_match', 'none-match', 'LOYAL_HIGH_VALUE'],
    ['no_open_assignment', 'no-open-assignment', undefined],
    ['unassigned_result', 'unassigned-result', undefined],
    ['no_usual_store', 'no-usual-store', 'LOYAL_HIGH_VALUE'],
  ])('shows %s as returned, without reading any product', async (status, file, label) => {
    const { customers, seen } = await signedIn('MARKETING', route(table(file)));
    const res = await customers.recommendations(CUSTOMER);
    expect(res.ok && res.value.status).toBe(status);
    expect(res.ok && res.value.items).toEqual([]);
    expect(res.ok && res.value.label_code).toBe(label);
    expect(paths(seen).filter((p) => !p.includes('/recommendations/'))).toEqual([]);
  });

  it('shows an unknown customer as returned', async () => {
    const { customers } = await signedIn('MARKETING', () => ({
      status: 422,
      body: example('recommendations', 'unknown-customer'),
    }));
    expect(await customers.recommendations(CUSTOMER)).toMatchObject({
      ok: false,
      error: { code: 'unknown_customer' },
    });
  });
});
