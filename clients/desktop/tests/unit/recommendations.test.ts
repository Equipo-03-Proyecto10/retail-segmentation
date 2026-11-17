import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { operationsForRole } from '../../src/main/bundle';
import { Recommendations } from '../../src/main/recommendations';
import { ContractDefect } from '../../src/main/transport';
import { rootElement, XsdValidator } from '../../src/main/xml';
import { RECOMMENDATION_OPERATIONS } from '../../src/shared/modules';
import {
  availabilityText,
  dayAfter,
  dayStart,
  factorsText,
  kindText,
  named,
  reasonText,
  shareText,
} from '../../src/shared/recommendations';
import {
  bundle,
  claims,
  example,
  makeOffice,
  makeTransport,
  MemoryVault,
  tokenResult,
  type Reply,
  type Responder,
  type Seen,
} from '../support/fake';
import {
  CATEGORIES,
  CHANNELS_LIST,
  CUSTOMER,
  DECISION,
  categoriesPage,
  channelsPage,
  productsPage,
  storesPage,
  STORES,
} from '../support/recommendations';

const REC = '/api/recommendations/v2';
const V2 = 'https://mosaiq.maxthecoder.online/xml/recommendations/v2';
const V2_EXAMPLES = new URL('../../../../contracts/recommendations/examples/v2', import.meta.url).pathname;
const v2 = (name: string): string => example('recommendations', `v2/${name}`);

type Overrides = Record<string, Reply>;

function responder(role: string, over: Overrides = {}): Responder {
  return (r) => {
    const url = new URL(r.url);
    const path = url.pathname;
    if (r.method === 'POST' && path.endsWith('/v1/sessions'))
      return { status: 201, body: tokenResult('a1', 'r1') };
    if (path.endsWith('/v1/sessions/current'))
      return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
    if (path === `${REC}/recommendations`)
      return over.answer ?? { status: 200, body: v2('recommended-with-alternatives') };
    if (path === `${REC}/recommendation-decisions`)
      return over.list ?? { status: 200, body: v2('decisions-page') };
    if (path === `${REC}/recommendation-decisions/${DECISION}`)
      return over.decision ?? { status: 200, body: v2('decision') };
    if (path.startsWith(`${REC}/recommendation-decisions/`))
      return { status: 404, body: example('recommendations', 'decision-not-found') };
    if (path === '/api/products/v1/products')
      return (
        over.products ?? {
          status: 200,
          body: productsPage((url.searchParams.get('ids') ?? '').split(',').map(Number)),
        }
      );
    if (path === '/api/categories/v1/categories')
      return over.categories ?? { status: 200, body: categoriesPage(CATEGORIES) };
    if (path === '/api/stores/v1/stores') return over.stores ?? { status: 200, body: storesPage(STORES) };
    if (path === '/api/sales/v1/channels')
      return over.channels ?? { status: 200, body: channelsPage(CHANNELS_LIST) };
    return { status: 500 };
  };
}

async function setup(role: string, over: Overrides = {}) {
  const { office, seen } = makeOffice(
    responder(role, over),
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  return { recs: new Recommendations(office), seen };
}

const calls = (seen: Seen[], path: string) => seen.filter((s) => new URL(s.url).pathname === path);

describe('the recommendations v2 schema in the bundle', () => {
  it('bundles recommendations.v2.xsd beside the v1 schema, under its own namespace', () => {
    expect(bundle.schemas[V2]).toBe('recommendations/recommendations.v2.xsd');
    expect(bundle.schemas['https://mosaiq.maxthecoder.online/xml/recommendations/v1']).toBe(
      'recommendations/recommendations.xsd',
    );
  });

  it('accepts every valid v2 example and refuses every invalid one', async () => {
    const validator = new XsdValidator(bundle);
    const files = readdirSync(V2_EXAMPLES).filter((f) => f.endsWith('.xml'));
    expect(files.length).toBeGreaterThan(10);
    for (const file of files) {
      const xml = readFileSync(join(V2_EXAMPLES, file), 'utf8');
      const root = rootElement(xml);
      expect(root?.namespace, file).toBe(V2);
      const verdict = await validator.validate(xml, V2);
      expect(verdict.valid, `${file}: ${verdict.errors[0] ?? ''}`).toBe(!file.endsWith('.invalid.xml'));
    }
  });

  it('gives the recommendations roles to the three v2 operations, and decisions fewer roles', () => {
    const roles = (id: string): string[] =>
      [...(bundle.operations.find((o) => o.operationId === id)?.roles ?? [])].sort();
    expect(roles(RECOMMENDATION_OPERATIONS.inspect)).toEqual(
      [
        'ADMIN',
        'ANALYST',
        'AUDITOR',
        'LOYALTY_OFFICER',
        'MARKETING',
        'SALES_ASSOCIATE',
        'STORE_MANAGER',
      ].sort(),
    );
    expect(roles(RECOMMENDATION_OPERATIONS.decisions)).toEqual([
      'ADMIN',
      'ANALYST',
      'AUDITOR',
      'STORE_MANAGER',
    ]);
    expect(roles(RECOMMENDATION_OPERATIONS.decision)).toEqual(roles(RECOMMENDATION_OPERATIONS.decisions));
    expect(operationsForRole(bundle, 'MARKETING')).toContain(RECOMMENDATION_OPERATIONS.inspect);
    expect(operationsForRole(bundle, 'MARKETING')).not.toContain(RECOMMENDATION_OPERATIONS.decisions);
  });
});

describe('the transport at a later major', () => {
  const v2Path = `/v2/recommendations?customer_id=${CUSTOMER}`;

  it('routes /v2 to the same service and checks the answer against the v2 schema', async () => {
    const { transport, seen } = makeTransport(() => ({ status: 200, body: v2('recommended') }));
    expect(transport.route('/v2/recommendation-decisions/x')).toEqual({
      service: 'recommendations',
      version: 'v2',
    });
    const res = await transport.call({ method: 'GET', path: v2Path });
    expect(res.ok && res.root).toBe('Recommendation');
    expect(seen[0]?.url).toBe(`http://gw.test/api/recommendations${v2Path}`);
  });

  it('refuses a v1 answer on a v2 path, and a v2 answer on a v1 path', async () => {
    const v1 = makeTransport(() => ({ status: 200, body: example('recommendations', 'recommended') }));
    const wrongMajor = await v1.transport.call({ method: 'GET', path: v2Path }).catch((e: unknown) => e);
    expect(wrongMajor).toBeInstanceOf(ContractDefect);
    expect(String((wrongMajor as ContractDefect).reason)).toMatch(/root namespace/);

    const other = makeTransport(() => ({ status: 200, body: v2('recommended') }));
    const stale = await other.transport
      .call({ method: 'GET', path: `/v1/recommendations?customer_id=${CUSTOMER}` })
      .catch((e: unknown) => e);
    expect(stale).toBeInstanceOf(ContractDefect);
  });

  it('keeps reading /v1 answers against the v1 schema', async () => {
    const { transport } = makeTransport(() => ({
      status: 200,
      body: example('recommendations', 'recommended'),
    }));
    const res = await transport.call({ method: 'GET', path: `/v1/recommendations?customer_id=${CUSTOMER}` });
    expect(res.ok && res.root).toBe('Recommendation');
  });

  it('refuses an answer outside the v2 schema, such as an item with a product name', async () => {
    const { transport } = makeTransport(() => ({
      status: 200,
      body: readFileSync(join(V2_EXAMPLES, 'item-with-product-name.invalid.xml'), 'utf8'),
    }));
    const err = await transport.call({ method: 'GET', path: v2Path }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ContractDefect);
    expect(String((err as ContractDefect).reason)).toMatch(/schema validation/);
  });

  it('does not call a major whose schema is not bundled', async () => {
    const { transport, seen } = makeTransport(() => ({ status: 200 }));
    await expect(transport.call({ method: 'GET', path: '/v9/recommendations' })).rejects.toThrow(
      /no bundled schema/,
    );
    await expect(transport.call({ method: 'GET', path: '/recommendations' })).rejects.toThrow(/versioned/);
    expect(seen).toHaveLength(0);
  });

  it('reads a refusal in the common namespace at v2', async () => {
    const { transport } = makeTransport(() => ({
      status: 422,
      body: example('recommendations', 'unknown-store'),
    }));
    const res = await transport.call({ method: 'GET', path: v2Path });
    expect(!res.ok && res.error).toMatchObject({
      code: 'unknown_store',
      details: [{ field: 'store_id', code: 'unknown' }],
    });
  });
});

describe('inspecting a recommendation', () => {
  it('asks for the customer, store and channel it was given, and nothing else', async () => {
    const { recs, seen } = await setup('ANALYST');
    const res = await recs.inspect({ customer_id: CUSTOMER, store_id: 5, channel_id: 1, limit: 4 });
    expect(res.ok).toBe(true);
    const asked = calls(seen, `${REC}/recommendations`);
    expect(asked).toHaveLength(1);
    expect(asked[0]?.headers.Accept).toBe('application/xml');
    expect(Object.fromEntries(new URL(asked[0]?.url ?? '').searchParams)).toEqual({
      customer_id: CUSTOMER,
      store_id: '5',
      channel_id: '1',
      limit: '4',
    });
  });

  it('never defaults the store or the channel', async () => {
    const { recs, seen } = await setup('ANALYST');
    await recs.inspect({ customer_id: CUSTOMER });
    const url = new URL(calls(seen, `${REC}/recommendations`)[0]?.url ?? '');
    expect([...url.searchParams.keys()]).toEqual(['customer_id']);
  });

  it('shows each item with its factors, availability and reasons, named from their owners', async () => {
    const { recs } = await setup('ANALYST');
    const res = await recs.inspect({ customer_id: CUSTOMER, store_id: 5, channel_id: 1 });
    if (!res.ok) throw new Error(res.error.code);
    const v = res.value;
    expect(v).toMatchObject({
      decision_id: DECISION,
      customer_id: CUSTOMER,
      status: 'recommended',
      label_code: 'LOYAL_HIGH_VALUE',
      run_id: 41,
      model_version_id: 7,
      store: { id: 5, name: 'Polanco' },
      store_source: 'given',
      channel: { id: 1, name: 'mobile_app' },
      usual_store: { id: 3, name: 'Coyoacan' },
    });
    expect(v.exclusions).toEqual([
      { reason: 'already_bought', count: 2 },
      { reason: 'not_sellable', count: 1 },
      { reason: 'out_of_stock', count: 2 },
    ]);
    const [first, second] = v.items;
    expect(first).toMatchObject({
      product: { id: 120, name: 'Item 120' },
      category: { id: 7, name: 'Dairy' },
      kind: 'primary',
      position: 1,
      factors: { signals_matched: 1, segment_buyers: 9 },
      availability: {
        store: { id: 5, name: 'Polanco' },
        channel: { id: 1, name: 'mobile_app' },
        quantity_on_hand: 22,
      },
      reasons: [{ signal: 'segment', segment_buyers: 9 }],
    });
    expect(second).toMatchObject({
      factors: { signals_matched: 1, category_share: '0.4' },
      reasons: [{ signal: 'purchase_history', category: { id: 12, name: 'Yogurt' }, share: '0.4' }],
    });
  });

  it('marks an alternative with the product it replaces and why that product is unavailable', async () => {
    const { recs } = await setup('ANALYST');
    const res = await recs.inspect({ customer_id: CUSTOMER, store_id: 5, channel_id: 1 });
    if (!res.ok) throw new Error(res.error.code);
    const alternative = res.value.items.find((i) => i.kind === 'alternative');
    expect(alternative).toMatchObject({
      product: { id: 246 },
      alternative_for: { id: 245, name: 'Item 245' },
      unavailable_reason: 'out_of_stock',
      availability: { store: { id: 5 } },
      reasons: [{ signal: 'relation_substitute' }],
    });
    expect(alternative?.factors).toBeUndefined();
    expect(kindText(alternative!, res.value.store)).toBe(
      'Alternative for Item 245 (#245), which is unavailable here (no unit is on hand)',
    );
  });

  it('marks a product held by another store as that store, and not as stock here', async () => {
    const { recs } = await setup('ANALYST');
    const res = await recs.inspect({ customer_id: CUSTOMER, store_id: 5, channel_id: 1 });
    if (!res.ok) throw new Error(res.error.code);
    const elsewhere = res.value.items.find((i) => i.kind === 'elsewhere');
    expect(elsewhere).toMatchObject({
      product: { id: 88 },
      availability: { store: { id: 3, name: 'Coyoacan' }, quantity_on_hand: 6 },
      unavailable_reason: 'out_of_stock',
      factors: { signals_matched: 2, segment_buyers: 5, category_share: '0.25' },
    });
    expect(elsewhere?.alternative_for).toBeUndefined();
    expect(kindText(elsewhere!, res.value.store)).toBe(
      'Another store: unavailable at Polanco (#5) (no unit is on hand), so not recommended there',
    );
    expect(availabilityText(elsewhere!.availability, 'elsewhere')).toBe(
      '6 on hand at Coyoacan (#3) on mobile_app (#1), another store',
    );
    expect(res.value.items.map((i) => i.kind)).toEqual(['primary', 'primary', 'alternative', 'elsewhere']);
  });

  it('reads the product names in one call, however many items there are', async () => {
    const { recs, seen } = await setup('ANALYST');
    await recs.inspect({ customer_id: CUSTOMER, store_id: 5 });
    const products = calls(seen, '/api/products/v1/products');
    expect(products).toHaveLength(1);
    const ids = new URL(products[0]?.url ?? '').searchParams.get('ids')?.split(',').sort();
    expect(ids).toEqual(['120', '245', '246', '331', '88']);
    expect(calls(seen, '/api/stores/v1/stores')).toHaveLength(1);
    expect(calls(seen, '/api/categories/v1/categories')).toHaveLength(1);
  });

  it('shows an id without a name when its owner cannot be read, and still shows the item', async () => {
    const { recs } = await setup('ANALYST', {
      products: { status: 503, body: example('recommendations', 'database-unavailable') },
      stores: { status: 503, body: example('recommendations', 'database-unavailable') },
    });
    const res = await recs.inspect({ customer_id: CUSTOMER, store_id: 5, channel_id: 1 });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.store).toEqual({ id: 5 });
    expect(res.value.items[0]?.product).toEqual({ id: 120 });
    expect(named(res.value.items[0]!.product, 'Product')).toBe('Product #120 (name unavailable)');
    expect(res.value.items).toHaveLength(4);
  });

  it('shows every status as the service sent it, and reads no names when nothing was offered', async () => {
    const statuses: [string, string, boolean][] = [
      ['none-match', 'none_match', true],
      ['no-usual-store', 'no_usual_store', false],
      ['no-open-assignment', 'no_open_assignment', false],
      ['unassigned-result', 'unassigned_result', false],
      ['personalization-withdrawn', 'PERSONALIZATION_WITHDRAWN', true],
    ];
    for (const [file, status, hasStore] of statuses) {
      const { recs, seen } = await setup('ANALYST', { answer: { status: 200, body: v2(file) } });
      const res = await recs.inspect({ customer_id: CUSTOMER });
      if (!res.ok) throw new Error(res.error.code);
      expect(res.value.status, file).toBe(status);
      expect(res.value.items, file).toEqual([]);
      expect(res.value.store !== undefined, file).toBe(hasStore);
      expect(calls(seen, '/api/products/v1/products'), file).toHaveLength(0);
    }
    const { recs } = await setup('ANALYST', { answer: { status: 200, body: v2('none-match') } });
    const none = await recs.inspect({ customer_id: CUSTOMER });
    expect(none.ok && none.value.exclusions).toEqual([
      { reason: 'already_bought', count: 3 },
      { reason: 'out_of_stock', count: 4 },
    ]);
  });

  it('shows a refusal as the service returned it', async () => {
    const refusals: [string, number, string][] = [
      ['unknown-store', 422, 'unknown_store'],
      ['unknown-channel', 422, 'unknown_channel'],
      ['forbidden-store', 403, 'forbidden_store'],
    ];
    for (const [file, status, code] of refusals) {
      const { recs } = await setup('STORE_MANAGER', {
        answer: { status, body: example('recommendations', file) },
      });
      const res = await recs.inspect({ customer_id: CUSTOMER, store_id: 9 });
      expect(res.ok, file).toBe(false);
      expect(!res.ok && res.error.code, file).toBe(code);
      expect(!res.ok && res.error.details[0]?.field, file).toMatch(/^(store_id|channel_id)$/);
    }
  });

  it('refuses a value that cannot be sent, before sending anything', async () => {
    const { recs, seen } = await setup('ANALYST');
    const before = seen.length;
    const bad: unknown[] = [
      undefined,
      {},
      { customer_id: 'not-a-uuid' },
      { customer_id: CUSTOMER, store_id: 0 },
      { customer_id: CUSTOMER, store_id: 1.5 },
      { customer_id: CUSTOMER, store_id: '5' },
      { customer_id: CUSTOMER, channel_id: -1 },
      { customer_id: CUSTOMER, limit: 51 },
      { customer_id: CUSTOMER, limit: 0 },
      { customer_id: CUSTOMER, limit: Number.NaN },
    ];
    for (const q of bad) {
      const res = await recs.inspect(q);
      expect(!res.ok && res.error.code, JSON.stringify(q)).toBe('invalid_input');
    }
    expect(seen.length).toBe(before);
  });

  it('refuses a role the contract does not allow, before sending anything', async () => {
    const { recs, seen } = await setup('INVENTORY_PLANNER');
    const before = seen.length;
    const res = await recs.inspect({ customer_id: CUSTOMER });
    expect(!res.ok && res.error.code).toBe('forbidden_role');
    expect(seen.length).toBe(before);
  });

  it('refuses while signed out', async () => {
    const { office } = makeOffice(responder('ANALYST'));
    const res = await new Recommendations(office).inspect({ customer_id: CUSTOMER });
    expect(!res.ok && res.error.code).toBe('signed_out');
  });
});

describe('the stored decisions', () => {
  it('lists the summaries with the filters given, newest first as returned', async () => {
    const { recs, seen } = await setup('ANALYST');
    const res = await recs.listDecisions({
      customer_id: CUSTOMER,
      store_id: 5,
      status: 'recommended',
      from: '2026-10-01T00:00:00Z',
      to: '2026-11-01T00:00:00Z',
      page: 1,
      size: 20,
    });
    if (!res.ok) throw new Error(res.error.code);
    const asked = calls(seen, `${REC}/recommendation-decisions`)[0];
    expect(Object.fromEntries(new URL(asked?.url ?? '').searchParams)).toEqual({
      customer_id: CUSTOMER,
      store_id: '5',
      status: 'recommended',
      from: '2026-10-01T00:00:00Z',
      to: '2026-11-01T00:00:00Z',
      page: '1',
      size: '20',
    });
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 3 });
    expect(res.value.items.map((d) => d.decision_id)).toEqual([
      DECISION,
      '9b2d4c7e-5a10-4f38-8c61-d3e0a7b94f12',
      'a05e6b12-8d49-4c73-b1f6-94d3e7a0c2b8',
    ]);
    expect(res.value.items[0]).toMatchObject({
      customer_id: CUSTOMER,
      status: 'recommended',
      store: { id: 5, name: 'Polanco' },
      store_source: 'given',
      channel: { id: 1, name: 'mobile_app' },
      item_count: 4,
    });
    expect(res.value.items[0]?.exclusions).toHaveLength(3);
    expect(res.value.items[1]?.channel).toBeUndefined();
    expect(res.value.items[2]?.customer_id).toBeUndefined();
  });

  it('shows an empty page as empty', async () => {
    const { recs } = await setup('AUDITOR', { list: { status: 200, body: v2('decisions-none') } });
    const res = await recs.listDecisions({});
    expect(res.ok && res.value).toEqual({ items: [], page: { page: 1, size: 20, total: 0 } });
  });

  it('keeps a page of one decision a list', async () => {
    const page = v2('decisions-page');
    const first = /<RecommendationDecisionSummary>[\s\S]*?<\/RecommendationDecisionSummary>/.exec(page)![0];
    const one = page
      .replace(/<RecommendationDecisionSummary>[\s\S]*<\/RecommendationDecisionSummary>/, first)
      .replace('<common:total>3</common:total>', '<common:total>1</common:total>');
    const { recs } = await setup('AUDITOR', { list: { status: 200, body: one } });
    const res = await recs.listDecisions({});
    expect(res.ok && res.value.items).toHaveLength(1);
    expect(res.ok && res.value.items[0]?.exclusions).toHaveLength(3);
  });

  it('refuses a filter the service would refuse, before sending anything', async () => {
    const { recs, seen } = await setup('ANALYST');
    const before = seen.length;
    const bad: unknown[] = [
      { customer_id: 'x' },
      { store_id: 0 },
      { status: 'shipped' },
      { from: '2026-10-01' },
      { to: 'tomorrow' },
      { from: '2026-10-02T00:00:00Z', to: '2026-10-01T00:00:00Z' },
      { from: '2026-10-01T00:00:00Z', to: '2026-10-01T00:00:00Z' },
      { page: 0 },
      { size: 101 },
    ];
    for (const q of bad) {
      const res = await recs.listDecisions(q);
      expect(!res.ok && res.error.code, JSON.stringify(q)).toBe('invalid_input');
    }
    expect(seen.length).toBe(before);
  });

  it('opens a decision with what was offered, what was excluded and why', async () => {
    const { recs, seen } = await setup('ANALYST');
    const res = await recs.openDecision(DECISION);
    if (!res.ok) throw new Error(res.error.code);
    expect(calls(seen, `${REC}/recommendation-decisions/${DECISION}`)).toHaveLength(1);
    const v = res.value;
    expect(v).toMatchObject({ decision_id: DECISION, customer_id: CUSTOMER, status: 'recommended' });
    expect(v.window).toBeUndefined();
    expect(v.usual_store).toBeUndefined();
    expect(v.exclusions.map((e) => `${e.count} ${e.reason}`)).toEqual([
      '2 already_bought',
      '1 not_sellable',
      '2 out_of_stock',
    ]);
    expect(v.items.map((i) => [i.kind, i.product.id])).toEqual([
      ['primary', 120],
      ['primary', 331],
      ['alternative', 246],
      ['elsewhere', 88],
    ]);
    const alternative = v.items[2];
    expect(alternative?.alternative_for?.id).toBe(245);
    expect(alternative?.unavailable_reason).toBe('out_of_stock');
    // A decision holds what the client received: opened, it equals the answer item for item.
    const answer = await recs.inspect({ customer_id: CUSTOMER, store_id: 5, channel_id: 1 });
    expect(answer.ok && answer.value.items).toEqual(v.items);
  });

  it('shows a missing decision as the service refused it', async () => {
    const { recs } = await setup('ANALYST');
    const res = await recs.openDecision('00000000-0000-4000-8000-000000000000');
    expect(!res.ok && res.error.code).toBe('decision_not_found');
  });

  it('refuses a decision id that is not a UUID', async () => {
    const { recs, seen } = await setup('ANALYST');
    const before = seen.length;
    for (const id of [undefined, '', 'abc', 7, `${DECISION}/../x`]) {
      const res = await recs.openDecision(id);
      expect(!res.ok && res.error.code, String(id)).toBe('invalid_input');
    }
    expect(seen.length).toBe(before);
  });

  it("passes a store manager's refusal for a store it is not assigned to through", async () => {
    const { recs } = await setup('STORE_MANAGER', {
      list: { status: 403, body: example('recommendations', 'forbidden-store') },
    });
    const res = await recs.listDecisions({ store_id: 9 });
    expect(!res.ok && res.error).toMatchObject({
      code: 'forbidden_store',
      details: [{ field: 'store_id', code: 'not_assigned' }],
    });
  });

  it('is narrower than the recommendation: a role that may inspect but not read decisions is refused', async () => {
    const { recs, seen } = await setup('MARKETING');
    const inspected = await recs.inspect({ customer_id: CUSTOMER });
    expect(inspected.ok).toBe(true);
    const afterInspect = seen.length;
    const list = await recs.listDecisions({});
    const open = await recs.openDecision(DECISION);
    expect(!list.ok && list.error.code).toBe('forbidden_role');
    expect(!open.ok && open.error.code).toBe('forbidden_role');
    expect(seen.length).toBe(afterInspect);
  });

  it('reads no customer: a decision shows an id, or that the customer was anonymized', async () => {
    const { recs, seen } = await setup('ANALYST');
    await recs.listDecisions({});
    await recs.openDecision(DECISION);
    expect(seen.some((s) => s.url.includes('/customers'))).toBe(false);
  });
});

describe('the words', () => {
  it('names an id with its name, or says the name is unavailable', () => {
    expect(named({ id: 5, name: 'Polanco' }, 'Store')).toBe('Polanco (#5)');
    expect(named({ id: 5 }, 'Store')).toBe('Store #5 (name unavailable)');
  });

  it('prints the factors as sent, with the share as a percentage', () => {
    expect(factorsText({ signals_matched: 2, segment_buyers: 5, category_share: '0.25' })).toBe(
      '2 of 3 signals matched; 5 segment buyers; category share 0.25 (25%)',
    );
    expect(factorsText(undefined)).toMatch(/^Not scored/);
    expect(shareText('0.4')).toBe('0.4 (40%)');
    expect(shareText('1')).toBe('1 (100%)');
    expect(shareText('0')).toBe('0 (0%)');
  });

  it('explains each signal, and prints one it does not know as sent', () => {
    expect(reasonText({ signal: 'segment', segment_buyers: 9 })).toMatch(/9 other customers/);
    expect(
      reasonText({ signal: 'purchase_history', category: { id: 12, name: 'Yogurt' }, share: '0.4' }),
    ).toBe("Purchase history: Yogurt (#12) is 0.4 (40%) of the customer's buying");
    expect(reasonText({ signal: 'relation_substitute' })).toMatch(/substitute/);
    expect(reasonText({ signal: 'future_signal' })).toBe('future_signal');
  });

  it('turns a day into the instants the service reads, the last day included', () => {
    expect(dayStart('2026-10-01')).toBe('2026-10-01T00:00:00Z');
    expect(dayAfter('2026-10-31')).toBe('2026-11-01T00:00:00Z');
    expect(dayAfter('2026-12-31')).toBe('2027-01-01T00:00:00Z');
  });
});
