import { describe, expect, it } from 'vitest';
import { Floor, lineTotal } from '../../src/main/floor';
import { XsdValidator } from '../../src/main/xml';
import {
  bundle,
  errorXml,
  example,
  makeOffice,
  MemoryVault,
  type Responder,
  type Seen,
} from '../support/fake';

const PRODUCTS_NS = 'https://mosaiq.maxthecoder.online/xml/products/v1';
const COMMON_NS = 'https://mosaiq.maxthecoder.online/xml/common/v1';
const EVENT_ID = 'a3c1f7e2-5b48-4d0e-9f6a-1c2b3d4e5f63';
const CHECK_IN_ID = '7c1f9a52-3b0e-4d8e-9a64-5f2d1c0b8e72';

const productPage = (ids: number[]): string =>
  `<Products xmlns="${PRODUCTS_NS}" xmlns:common="${COMMON_NS}">${ids
    .map(
      (id) =>
        `<Product><product_id>${id}</product_id><sku>S-${id}</sku><name>Item ${id}</name><category_id>12</category_id><list_price>1.00</list_price><is_active>true</is_active></Product>`,
    )
    .join(
      '',
    )}<common:PageInfo><common:page>1</common:page><common:size>50</common:size><common:total>${ids.length}</common:total></common:PageInfo></Products>`;

function floor(responder: Responder): { floor: Floor; seen: Seen[] } {
  const vault = new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') });
  const { office, seen } = makeOffice(responder, vault);
  return { floor: new Floor(office), seen };
}

/** Answers like the six services, from the recorded examples. */
const services: Responder = (r) => {
  const url = new URL(r.url);
  const path = url.pathname;
  if (path === '/api/products/v1/products') {
    const ids = (url.searchParams.get('ids') ?? '').split(',').filter(Boolean).map(Number);
    return { status: 200, body: productPage(ids) };
  }
  if (path === '/api/stores/v1/stores') return { status: 200, body: example('stores', 'directory-page') };
  if (path === '/api/sales/v1/channels') return { status: 200, body: example('sales', 'list-channels') };
  if (path === '/api/stock/v1/stock-levels')
    return { status: 200, body: example('stock', 'stock-levels-filtered') };
  if (path === '/api/sales/v1/sales') return { status: 200, body: example('sales', 'list-sales') };
  if (path === '/api/sales/v1/sales/4812') return { status: 200, body: example('sales', 'get-sale') };
  if (path === '/api/engagement/v1/engagement-summaries')
    return { status: 200, body: example('engagement', `summary-by-${url.searchParams.get('group_by')}`) };
  if (path === '/api/engagement/v1/engagement-events')
    return { status: 200, body: example('engagement', 'events-page') };
  if (path === `/api/engagement/v1/engagement-events/${EVENT_ID}/void`)
    return { status: 200, body: example('engagement', 'voided-event') };
  if (path === '/api/check-ins/v1/check-ins')
    return { status: 200, body: example('check-ins', 'check-ins-page') };
  if (path === `/api/check-ins/v1/check-ins/${CHECK_IN_ID}`)
    return { status: 200, body: example('check-ins', 'check-in-with-reviews') };
  if (path === `/api/check-ins/v1/check-ins/${CHECK_IN_ID}/reviews`)
    return { status: 201, body: example('check-ins', 'review') };
  return { status: 404, body: errorXml('not_found') };
};

const calls = (seen: Seen[], path: string): Seen[] => seen.filter((s) => new URL(s.url).pathname === path);

describe('names, read by id from the owning service', () => {
  it('resolves product names with ids=, at most 50 per call', async () => {
    const { floor: f, seen } = floor((r) => {
      if (r.url.includes('/stock-levels')) {
        const levels = Array.from(
          { length: 120 },
          (_, i) =>
            `<StockLevel><store_id>1</store_id><product_id>${i + 1}</product_id><quantity_on_hand>1</quantity_on_hand><updated_at>2026-10-15T16:42:10Z</updated_at></StockLevel>`,
        ).join('');
        return {
          status: 200,
          body: `<StockLevels xmlns="https://mosaiq.maxthecoder.online/xml/stock/v1" xmlns:common="${COMMON_NS}">${levels}<common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>120</common:total></common:PageInfo></StockLevels>`,
        };
      }
      return services(r);
    });
    const res = await f.stockLevels({ size: 100 });
    const batches = calls(seen, '/api/products/v1/products').map((s) =>
      new URL(s.url).searchParams.get('ids')!.split(','),
    );
    expect(batches.map((b) => b.length)).toEqual([50, 50, 20]);
    expect(res.ok && res.value.items[119]?.product_name).toBe('Item 120');
  });

  it('names stock levels by product and store, and sends the threshold', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.stockLevels({ store_id: 1, below: 15, page: 1, size: 20 });
    const query = new URL(calls(seen, '/api/stock/v1/stock-levels')[0]!.url).searchParams;
    expect(Object.fromEntries(query)).toEqual({ store_id: '1', below: '15', page: '1', size: '20' });
    expect(res.ok && res.value.items[0]).toMatchObject({
      product_id: 120,
      product_name: 'Item 120',
      // The recorded stock is at store 3, which the recorded directory does not list.
      store_name: 'Store 3 (unavailable)',
      quantity_on_hand: 14,
    });
  });

  it('shows a product that cannot be read as unavailable instead of failing the screen', async () => {
    const { floor: f } = floor((r) =>
      r.url.includes('/products') ? { status: 503, body: errorXml('database_unavailable') } : services(r),
    );
    const res = await f.stockLevels({});
    expect(res.ok && res.value.items[0]?.product_name).toBe('Product 120 (unavailable)');
  });
});

describe('sales', () => {
  it('reads a sale with its lines, line totals, total as returned, channel and store names', async () => {
    const { floor: f, seen } = floor((r) =>
      r.url.includes('/stores')
        ? {
            status: 200,
            body: example('stores', 'directory-page').replaceAll(
              '<store_id>2</store_id>',
              '<store_id>3</store_id>',
            ),
          }
        : services(r),
    );
    const res = await f.getSale(4812);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.value).toMatchObject({
      sale_id: 4812,
      store_name: 'Coyoacan',
      channel_name: 'physical_store',
      total: '203.25',
    });
    expect(res.value.lines.map((l) => [l.product_name, l.quantity, l.unit_price, l.line_total])).toEqual([
      ['Item 1007', 1, '12.50', '12.50'],
      ['Item 1020', 3, '4.25', '12.75'],
      ['Item 1051', 2, '89.00', '178.00'],
    ]);
    expect(calls(seen, '/api/products/v1/products')).toHaveLength(1);
  });

  it('lists sales with the filters as a query', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.listSales({
      customer_id: '7b3ad781-c5de-4f40-ae50-1fa825ce2b25',
      store_id: 3,
      from: '2026-10-01T00:00:00Z',
      to: '2026-10-31T23:59:00Z',
    });
    expect(res.ok && res.value.items.map((s) => s.sale_id)).toEqual([4812, 4811]);
    expect(Object.fromEntries(new URL(calls(seen, '/api/sales/v1/sales')[0]!.url).searchParams)).toEqual({
      customer_id: '7b3ad781-c5de-4f40-ae50-1fa825ce2b25',
      store_id: '3',
      from: '2026-10-01T00:00:00Z',
      to: '2026-10-31T23:59:00Z',
      page: '1',
      size: '20',
    });
  });

  it.each([
    [{ customer_id: 'not-a-uuid' }, 'The customer'],
    [{ from: '2026-10-01' }, 'From'],
    [{ size: 101 }, 'The page size'],
  ])('refuses %o before anything is sent', async (query, label) => {
    const { floor: f, seen } = floor(services);
    const res = await f.listSales(query);
    expect(!res.ok && res.error.message).toContain(label);
    expect(seen).toHaveLength(0);
  });

  it('shows sale_not_found as returned', async () => {
    const { floor: f } = floor(() => ({ status: 404, body: example('sales', 'sale-not-found') }));
    const res = await f.getSale(9);
    expect(!res.ok && res.error.code).toBe('sale_not_found');
  });

  it('works out line totals in whole cents', () => {
    expect(lineTotal('0.10', 3)).toBe('0.30');
    expect(lineTotal('19.99', 7)).toBe('139.93');
  });
});

describe('engagement', () => {
  it('names the store groups of a summary', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.engagementSummary({ group_by: 'store', from: '2026-10-13', to: '2026-10-15' });
    expect(res.ok && res.value.groups.map((g) => [g.key, g.label, g.count])).toEqual([
      ['1', 'Centro Historico', 42],
      ['3', 'Store 3 (unavailable)', 57],
      ['7', 'Store 7 (unavailable)', 9],
    ]);
    expect(
      Object.fromEntries(
        new URL(calls(seen, '/api/engagement/v1/engagement-summaries')[0]!.url).searchParams,
      ),
    ).toEqual({
      group_by: 'store',
      from: '2026-10-13',
      to: '2026-10-15',
    });
  });

  it('keeps day and type keys as they are', async () => {
    const { floor: f } = floor(services);
    const res = await f.engagementSummary({ group_by: 'type' });
    expect(res.ok && res.value.groups.every((g) => g.label === g.key)).toBe(true);
  });

  it('refuses a group_by outside store, day and type', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.engagementSummary({ group_by: 'week' });
    expect(!res.ok && res.error.code).toBe('invalid_input');
    expect(seen).toHaveLength(0);
  });

  it('lists events with their history', async () => {
    const { floor: f } = floor(services);
    const res = await f.listEngagementEvents({ type: 'QR_SCAN', voided: true });
    if (!res.ok) throw new Error(res.error.code);
    const voided = res.value.items.find((e) => e.event_id === EVENT_ID)!;
    expect(voided.voided).toBe(true);
    expect(voided.history[0]).toMatchObject({ action: 'VOIDED', reason: 'Scanned the wrong shelf label.' });
    expect(res.value.items[1]).toMatchObject({ type: 'RECO_CLICK', label_code: 'LOYAL_HIGH_VALUE', rank: 2 });
  });

  it('voids with a reason, in a body that validates against the XSD', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.voidEngagementEvent(EVENT_ID, 'Scanned the wrong shelf label.');
    expect(res.ok && res.value.voided).toBe(true);
    const post = seen.find((s) => s.method === 'POST')!;
    expect(new URL(post.url).pathname).toBe(`/api/engagement/v1/engagement-events/${EVENT_ID}/void`);
    expect(post.body).toContain('<VoidRequest');
    const check = await new XsdValidator(bundle).validate(
      post.body!,
      'https://mosaiq.maxthecoder.online/xml/engagement/v1',
    );
    expect(check.valid).toBe(true);
  });

  it('refuses a blank reason next to its field, and sends nothing', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.voidEngagementEvent(EVENT_ID, '   ');
    expect(!res.ok && res.error.details).toEqual([{ field: 'reason', code: 'required' }]);
    expect(seen).toHaveLength(0);
  });

  it.each([
    ['already-voided', 409, 'already_voided'],
    ['event-not-found', 404, 'event_not_found'],
    ['forbidden-role', 403, 'forbidden_role'],
  ])('shows the %s refusal as returned', async (file, status, code) => {
    const { floor: f } = floor(() => ({ status, body: example('engagement', file) }));
    const res = await f.voidEngagementEvent(EVENT_ID, 'Wrong label.');
    expect(!res.ok && res.error.code).toBe(code);
  });
});

describe('check-ins', () => {
  it('lists check-ins with every reason the service returns', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.listCheckIns({ store_id: 3, verified: false });
    expect(
      Object.fromEntries(new URL(calls(seen, '/api/check-ins/v1/check-ins')[0]!.url).searchParams),
    ).toMatchObject({
      store_id: '3',
      verified: 'false',
    });
    expect(res.ok && res.value.items.map((c) => c.reason)).toContain('no_fix');
    expect(res.ok && res.value.items.find((c) => c.reason === 'no_fix')?.latitude).toBeUndefined();
  });

  it('opens a check-in with its reviews in order', async () => {
    const { floor: f } = floor(services);
    const res = await f.getCheckIn(CHECK_IN_ID);
    expect(res.ok && res.value.reviews.map((r) => r.review_id)).toEqual([41, 42]);
    expect(res.ok && res.value).toMatchObject({
      reason: 'outside_geofence',
      verified: true,
      auto_verified: false,
    });
  });

  it('appends a review with a verdict and a reason that validate against the XSD', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.reviewCheckIn(CHECK_IN_ID, true, 'Entrance camera confirms the visit at 14:02.');
    expect(res.ok && res.value.review_id).toBe(42);
    const post = seen.find((s) => s.method === 'POST')!;
    expect(post.body).toContain(
      '<verified>true</verified><reason>Entrance camera confirms the visit at 14:02.</reason>',
    );
    const check = await new XsdValidator(bundle).validate(
      post.body!,
      'https://mosaiq.maxthecoder.online/xml/check-ins/v1',
    );
    expect(check.valid).toBe(true);
  });

  it('refuses a review without a reason or a verdict, and sends nothing', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.reviewCheckIn(CHECK_IN_ID, 'yes', '');
    expect(!res.ok && res.error.details).toEqual([
      { field: 'verified', code: 'required' },
      { field: 'reason', code: 'required' },
    ]);
    expect(seen).toHaveLength(0);
  });

  it('refuses an id that is not a UUID before building a path', async () => {
    const { floor: f, seen } = floor(services);
    const res = await f.getCheckIn('../../users');
    expect(!res.ok && res.error.code).toBe('invalid_input');
    expect(seen).toHaveLength(0);
  });

  it('shows check_in_not_found as returned', async () => {
    const { floor: f } = floor(() => ({ status: 404, body: example('check-ins', 'check-in-not-found') }));
    const res = await f.getCheckIn(CHECK_IN_ID);
    expect(!res.ok && res.error.code).toBe('check_in_not_found');
  });
});
