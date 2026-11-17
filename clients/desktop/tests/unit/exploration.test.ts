import { describe, expect, it } from 'vitest';
import { Exploration } from '../../src/main/exploration';
import { MODULE_GROUPS, visibleGroups } from '../../src/shared/modules';
import {
  agreement,
  centsText,
  figuresFor,
  netSales,
  profileWindow,
  toCents,
  windowText,
  type ReversalInput,
  type SaleInput,
} from '../../src/shared/exploration';
import { claims, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Reply, Seen } from '../support/fake';
import {
  agreeingProfile,
  answer,
  bigSale,
  CATEGORY_OF,
  CUSTOMER,
  example,
  noReversals,
  OBJECTED,
  PENDING,
  salesPage,
  UNASSIGNED,
  type Answer,
  type Behaviour,
} from '../support/exploration';

// Customer exploration (F28-03, #872). The rules are pinned on their own, then the main-process class is
// run against the recorded examples of the contracts it reads: rfm-engine, sales, customer-preferences,
// products, categories and stores.

const NOW = Date.parse('2026-10-30T14:22:05Z');
const WINDOW = { from: '2026-10-01T00:00:00Z', to: '2026-11-01T00:00:00Z' };
const PROFILE_WINDOW = { from: '2026-05-03T14:22:05Z', to: '2026-10-30T14:22:05Z' };

async function signedIn(
  role: string,
  behaviour: Behaviour = {},
  extra?: (r: Seen) => Reply | undefined,
): Promise<{ exploration: Exploration; seen: Seen[] }> {
  const { office, seen } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
      const custom = extra?.(r);
      if (custom !== undefined) return custom;
      const reply = answer(new URL(r.url), behaviour);
      return reply ?? { status: 404, body: '' };
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  seen.length = 0;
  return { exploration: new Exploration(office, () => NOW), seen };
}

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

const forbidden = (path: string): Behaviour => ({
  override: { [path]: { status: 403, body: example('customer-preferences', 'forbidden-role') } },
});

describe('money and windows', () => {
  it('works in whole cents', () => {
    expect(toCents('12.50')).toBe(1250);
    expect(toCents('0.05')).toBe(5);
    expect(centsText(110_00)).toBe('110.00');
    expect(centsText(5)).toBe('0.05');
    expect(centsText(203_25 + 4_25)).toBe('207.50');
  });

  it('puts the profile’s window before the instant it was computed, by the rule set’s days', () => {
    expect(profileWindow('2026-10-30T14:22:05Z', 180)).toEqual(PROFILE_WINDOW);
    expect(windowText(PROFILE_WINDOW)).toBe('2026-05-03T14:22:05Z to 2026-10-30T14:22:05Z (180 days)');
    expect(windowText({ from: '2026-10-01T00:00:00Z', to: '2026-10-02T00:00:00Z' })).toContain('(1 day)');
  });
});

const sale = (id: number, at: string, channel: number, lines: [number, number, string][]): SaleInput => ({
  sale_id: id,
  occurred_at: at,
  store_id: 1,
  channel_id: channel,
  lines: lines.map(([product_id, quantity, unit_price]) => ({ product_id, quantity, unit_price })),
});

const SALES: SaleInput[] = [
  sale(3, '2026-10-20T10:00:00Z', 2, [
    [1007, 1, '12.50'],
    [1020, 2, '4.25'],
  ]),
  sale(2, '2026-10-10T10:00:00Z', 1, [[1020, 4, '4.25']]),
  sale(1, '2026-09-01T10:00:00Z', 1, [[1051, 1, '89.00']]),
];
const category = (p: number): number | null => CATEGORY_OF[p] ?? null;

describe('net purchases (ADR-0062)', () => {
  it('takes back the returned units, and a sale with nothing left stops counting', () => {
    const reversals: ReversalInput[] = [
      { sale_id: 3, lines: [{ product_id: 1020, quantity: 1 }] },
      { sale_id: 2, lines: [{ product_id: 1020, quantity: 4 }] },
    ];
    const net = netSales(SALES, reversals);
    expect(net.map((s) => [s.sale_id, s.charged, s.net, s.status])).toEqual([
      [3, 21_00, 16_75, 'partly_reversed'],
      [2, 17_00, 0, 'reversed'],
      [1, 89_00, 89_00, 'counted'],
    ]);
  });

  it('adds up two reversals of one line, and never counts a negative unit', () => {
    const net = netSales(SALES, [
      { sale_id: 3, lines: [{ product_id: 1020, quantity: 1 }] },
      { sale_id: 3, lines: [{ product_id: 1020, quantity: 5 }] },
    ]);
    expect(net[0]?.lines.find((l) => l.product_id === 1020)?.units).toBe(0);
    expect(net[0]?.net).toBe(12_50);
  });

  it('leaves every sale as charged when no reversal could be read', () => {
    const net = netSales(SALES, null);
    expect(net.map((s) => [s.charged, s.net, s.status])).toEqual([
      [21_00, 21_00, 'counted'],
      [17_00, 17_00, 'counted'],
      [89_00, 89_00, 'counted'],
    ]);
  });
});

describe('the figures of a window', () => {
  const net = netSales(SALES, [{ sale_id: 3, lines: [{ product_id: 1020, quantity: 1 }] }]);

  it('counts the sales inside the window, its start included and its end excluded', () => {
    const w = { from: '2026-10-10T10:00:00Z', to: '2026-10-20T10:00:00Z' };
    const f = figuresFor(net, w, w.to, category);
    expect(f.frequency).toBe(1);
    expect(f.spend).toBe(17_00);
    expect(f.last_purchase_at).toBe('2026-10-10T10:00:00Z');
    const wider = figuresFor(
      net,
      { from: w.from, to: '2026-10-20T10:00:01Z' },
      '2026-10-20T10:00:01Z',
      category,
    );
    expect(wider.frequency).toBe(2);
  });

  it('measures recency in whole days from the latest purchase to the stated instant', () => {
    const f = figuresFor(net, WINDOW, '2026-10-30T14:22:05Z', category);
    expect(f.recency_days).toBe(10);
    expect(f.last_purchase_at).toBe('2026-10-20T10:00:00Z');
    expect(figuresFor(net, WINDOW, '2026-10-21T09:59:59Z', category).recency_days).toBe(0);
  });

  it('has no recency when the window holds no purchase', () => {
    const f = figuresFor(
      net,
      { from: '2025-01-01T00:00:00Z', to: '2025-02-01T00:00:00Z' },
      '2025-02-01T00:00:00Z',
      category,
    );
    expect(f).toMatchObject({ frequency: 0, spend: 0, categories: [], channels: [] });
    expect(f.recency_days).toBeUndefined();
  });

  it('breaks spend into categories and channels that each add up to the window', () => {
    const f = figuresFor(net, WINDOW, WINDOW.to, category);
    expect(f.channels).toEqual([
      { id: 1, purchases: 1, units: 4, spend: 17_00 },
      { id: 2, purchases: 1, units: 2, spend: 16_75 },
    ]);
    expect(f.channels.reduce((n, s) => n + s.spend, 0)).toBe(f.spend);
    expect(f.categories.reduce((n, s) => n + s.spend, 0)).toBe(f.spend);
    // Yogurt is bought in both sales (once in each, 1 and 4 units after the return), cheese once.
    expect(f.categories).toEqual([
      { id: 12, purchases: 2, units: 5, spend: 21_25 },
      { id: 11, purchases: 1, units: 1, spend: 12_50 },
    ]);
  });

  it('lists a product whose category is not known under no category, and counts a purchase once in a category', () => {
    const f = figuresFor(net, WINDOW, WINDOW.to, () => null);
    expect(f.categories).toEqual([{ id: null, purchases: 2, units: 6, spend: 33_75 }]);
  });
});

describe('agreement between the profile and the sales', () => {
  const sales = { recency_days: 14, frequency: 1, spend: '110.00' };

  it('compares recency, frequency and spend, each on its own', () => {
    expect(agreement({ r_days: 14, f_count: 1, m_total: '110.00' }, sales)).toEqual({
      recency: true,
      frequency: true,
      spend: true,
    });
    expect(agreement({ r_days: 30, f_count: 9, m_total: '842.50' }, sales)).toEqual({
      recency: false,
      frequency: false,
      spend: false,
    });
  });

  it('reads an unassigned profile as nothing to count', () => {
    expect(agreement({ f_count: 0, m_total: '0.00' }, { frequency: 0, spend: '0.00' })).toEqual({
      recency: true,
      frequency: true,
      spend: true,
    });
  });
});

describe('the module in the console', () => {
  it('sits in the analysis group and follows getRfmProfile', () => {
    const analysis = MODULE_GROUPS.find((g) => g.id === 'analysis');
    expect(analysis?.modules.find((m) => m.name === 'exploration')).toEqual({
      name: 'exploration',
      label: 'Customer exploration',
      operation: 'getRfmProfile',
    });
    // Services needs no operation, so it is always there.
    expect(visibleGroups(['getRfmProfile']).flatMap((g) => g.modules.map((m) => m.name))).toEqual([
      'exploration',
      'services',
    ]);
    // A role with only the preference reads is offered no exploration.
    expect(
      visibleGroups(['getRestrictionProfile', 'getConsentState']).flatMap((g) =>
        g.modules.map((m) => m.name),
      ),
    ).toEqual(['services']);
  });
});

describe('the profile and its window', () => {
  it('reads the profile and the rule set that holds its window', async () => {
    const { exploration, seen } = await signedIn('ANALYST');
    const res = await exploration.profile(CUSTOMER);
    expect(paths(seen)).toEqual([
      `GET /api/rfm-engine/v1/rfm-profiles/${CUSTOMER}`,
      'GET /api/rfm-engine/v1/rfm-rule-sets/3',
    ]);
    expect(res).toMatchObject({
      ok: true,
      value: {
        profile: {
          state: 'scored',
          r_days: 30,
          f_count: 9,
          m_total: '842.50',
          r_score: 4,
          label_code: 'LOYAL',
          via_fallback: false,
          computed_at: '2026-10-30T14:22:05Z',
        },
        rule_set: { rule_set_id: 3, window_days: 180, published: true },
        window: PROFILE_WINDOW,
      },
    });
  });

  it('keeps the profile when a role may not read rule sets, and says the window is unknown', async () => {
    const { exploration, seen } = await signedIn('STORE_MANAGER');
    const res = await exploration.profile(CUSTOMER);
    expect(res.ok && res.value.window).toBeUndefined();
    expect(res).toMatchObject({
      ok: true,
      value: { profile: { f_count: 9 }, rule_set_error: { code: 'forbidden_role' } },
    });
    // The rule set was never asked for.
    expect(paths(seen)).toEqual([`GET /api/rfm-engine/v1/rfm-profiles/${CUSTOMER}`]);
  });

  it('keeps the profile when the rule set read is refused or breaks its contract', async () => {
    const refused = await signedIn('ANALYST', forbidden('/rfm-rule-sets/3'));
    expect(await refused.exploration.profile(CUSTOMER)).toMatchObject({
      ok: true,
      value: { rule_set_error: { code: 'forbidden_role', correlation_id: 'corr-forbidden-role' } },
    });
    const broken = await signedIn('ANALYST', { ruleSet: '<nope/>' });
    expect(await broken.exploration.profile(CUSTOMER)).toMatchObject({
      ok: true,
      value: { rule_set_error: { code: 'contract_defect' } },
    });
  });

  it('gives a pending profile no rule set, and an unassigned one its window and no recency', async () => {
    const pending = await signedIn('ANALYST', { profile: example('rfm-engine', 'profile-pending') });
    const p = await pending.exploration.profile(PENDING);
    expect(p).toMatchObject({ ok: true, value: { profile: { state: 'pending' } } });
    expect(paths(pending.seen)).toHaveLength(1);

    const unassigned = await signedIn('ANALYST', { profile: example('rfm-engine', 'profile-unassigned') });
    const u = await unassigned.exploration.profile(UNASSIGNED);
    expect(u).toMatchObject({
      ok: true,
      value: { profile: { state: 'unassigned', f_count: 0, m_total: '0.00' } },
    });
    expect(u.ok && u.value.profile.r_days).toBeUndefined();
    expect(u.ok && u.value.window).toBeDefined();
  });

  it('shows a service’s refusal as returned', async () => {
    const { exploration } = await signedIn('ANALYST', {
      override: {
        [`/rfm-profiles/${CUSTOMER}`]: { status: 403, body: example('rfm-engine', 'forbidden-role') },
      },
    });
    expect(await exploration.profile(CUSTOMER)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role', correlation_id: 'corr-forbidden-role' },
    });
  });

  it('shows customer_not_found as returned', async () => {
    const { exploration } = await signedIn('ANALYST', {
      override: {
        [`/rfm-profiles/${CUSTOMER}`]: { status: 404, body: example('rfm-engine', 'customer-not-found') },
      },
    });
    expect(await exploration.profile(CUSTOMER)).toMatchObject({
      ok: false,
      error: { code: 'customer_not_found' },
    });
  });
});

const QUERY = { customer_id: CUSTOMER, ...{ from: WINDOW.from, to: WINDOW.to } };
const BOTH = { ...QUERY, compare_from: PROFILE_WINDOW.from, compare_to: PROFILE_WINDOW.to };

describe('the sales over two windows', () => {
  it('reads the sales once for both windows, net of the reversals, with each figure’s window', async () => {
    const { exploration, seen } = await signedIn('ANALYST');
    const res = await exploration.sales(BOTH);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    const sales = paths(seen).filter((p) => p.includes('/sales/v1/sales'));
    expect(sales).toHaveLength(1);
    const q = new URL(`http://x${sales[0]?.slice(4)}`).searchParams;
    expect(q.get('customer_id')).toBe(CUSTOMER);
    // The covering range: the profile's window starts earlier, the chosen one ends later.
    expect(q.get('from')).toBe(PROFILE_WINDOW.from);
    expect(q.get('to')).toBe(WINDOW.to);
    expect(paths(seen).filter((p) => p.includes('/sale-reversals'))).toEqual([
      `GET /api/sales/v1/sale-reversals?customer_id=${CUSTOMER}&page=1&size=100`,
    ]);

    const v = res.value;
    expect(v).toMatchObject({
      basis: 'net',
      sales_read: 2,
      sales_total: 2,
      incomplete: false,
      unknown_products: 0,
    });
    // The recorded sale 4812 is returned in part (110.00 of 203.25 remains), and 4811 is cancelled.
    expect(v.chosen).toMatchObject({
      window: WINDOW,
      end: '2026-10-30T14:22:05Z',
      frequency: 1,
      spend: '110.00',
      recency_days: 14,
      last_purchase_at: '2026-10-15T20:32:10Z',
    });
    expect(v.compare).toMatchObject({
      window: PROFILE_WINDOW,
      end: '2026-10-30T14:22:05Z',
      frequency: 1,
      spend: '110.00',
      recency_days: 14,
    });
    // 1007 is cheese; 1020 (2 left of 3) and 1051 (1 left of 2) are yogurt. Both add up to 110.00.
    expect(v.chosen.categories).toEqual([
      { id: 12, name: 'Yogurt', purchases: 1, units: 3, spend: '97.50' },
      { id: 11, name: 'Cheese', purchases: 1, units: 1, spend: '12.50' },
    ]);
    expect(v.chosen.channels).toEqual([
      { id: 3, name: 'physical_store', purchases: 1, units: 4, spend: '110.00' },
    ]);
    expect(
      v.purchases.map((p) => [p.sale_id, p.status, p.charged, p.net, p.store_name, p.channel_name]),
    ).toEqual([
      [4812, 'partly_reversed', '203.25', '110.00', 'Polanco', 'physical_store'],
      [4811, 'reversed', '4.25', '0.00', 'Centro Historico', 'mobile_app'],
    ]);
    expect(v.purchases_total).toBe(2);
  });

  it('counts the sales as charged, and says why, when the reversals cannot be read', async () => {
    for (const [role, behaviour, code] of [
      ['ANALYST', { override: { '/sale-reversals': { status: 404, body: '' } } }, 'contract_defect'],
      ['ANALYST', forbidden('/sale-reversals'), 'forbidden_role'],
      [
        'ANALYST',
        {
          override: {
            '/sale-reversals': { status: 503, body: example('customer-preferences', 'audit-unavailable') },
          },
        },
        'audit_unavailable',
      ],
    ] as [string, Behaviour, string][]) {
      const { exploration } = await signedIn(role, behaviour);
      const res = await exploration.sales(QUERY);
      expect(res).toMatchObject({ ok: true, value: { basis: 'charged', reversals_error: { code } } });
      // The sales of the window are both counted: 203.25 and 4.25, as charged.
      expect(res.ok && [res.value.chosen.frequency, res.value.chosen.spend]).toEqual([2, '207.50']);
    }
  });

  it('measures recency to the end of the window, or to now when the window ends later', async () => {
    const { exploration } = await signedIn('ANALYST');
    const res = await exploration.sales({ ...QUERY, to: '2026-12-31T00:00:00Z' });
    expect(res.ok && res.value.chosen.end).toBe('2026-10-30T14:22:05Z');
    const earlier = await exploration.sales({
      ...QUERY,
      from: '2026-10-01T00:00:00Z',
      to: '2026-10-16T00:00:00Z',
    });
    expect(earlier.ok && [earlier.value.chosen.end, earlier.value.chosen.recency_days]).toEqual([
      '2026-10-16T00:00:00Z',
      0,
    ]);
  });

  it('shows the lines of a product whose category was refused under no category, and says so', async () => {
    const { exploration } = await signedIn('ANALYST', forbidden('/products'));
    const res = await exploration.sales(QUERY);
    expect(res).toMatchObject({
      ok: true,
      value: { unknown_products: 3, categories_error: { code: 'forbidden_role' } },
    });
    expect(res.ok && res.value.chosen.categories).toEqual([
      { id: null, name: 'No category known', purchases: 1, units: 4, spend: '110.00' },
    ]);
  });

  it('asks for the products 50 at a time', async () => {
    const { exploration, seen } = await signedIn('ANALYST', {
      override: {
        '/v1/sales': { status: 200, body: bigSale(120) },
        '/sale-reversals': { status: 200, body: noReversals() },
      },
    });
    const res = await exploration.sales(QUERY);
    const calls = paths(seen).filter((p) => p.includes('/products/v1/products'));
    expect(calls).toHaveLength(3);
    for (const call of calls) expect(new URL(`http://x${call.slice(4)}`).searchParams.get('size')).toBe('50');
    // None of the 120 products has a category the fake knows: every line is listed under none.
    expect(res.ok && [res.value.unknown_products, res.value.chosen.categories.map((c) => c.id)]).toEqual([
      120,
      [null],
    ]);
  });

  it('says when the service holds more sales than were read', async () => {
    const rows = [
      { id: 9, at: '2026-10-20T10:00:00Z' },
      { id: 8, at: '2026-10-19T10:00:00Z' },
    ];
    const { exploration, seen } = await signedIn(
      'ANALYST',
      { override: { '/sale-reversals': { status: 200, body: noReversals() } } },
      (r) => {
        const url = new URL(r.url);
        if (!url.pathname.endsWith('/v1/sales')) return undefined;
        // The first page holds two of five sales, and the service has no more to give after it.
        return { status: 200, body: salesPage(url.searchParams.get('page') === '1' ? rows : [], 5) };
      },
    );
    const res = await exploration.sales(QUERY);
    expect(res).toMatchObject({ ok: true, value: { sales_read: 2, sales_total: 5, incomplete: true } });
    expect(paths(seen).filter((p) => p.includes('/v1/sales?'))).toHaveLength(2);
  });

  it('lists the newest 100 sales of the window and counts them all', async () => {
    const rows = Array.from({ length: 120 }, (_, i) => ({
      id: 1000 + i,
      at: `2026-10-${String(1 + Math.floor(i / 6)).padStart(2, '0')}T${String(8 + (i % 6)).padStart(2, '0')}:00:00Z`,
    }));
    const { exploration } = await signedIn('ANALYST', {
      override: {
        '/v1/sales': { status: 200, body: salesPage(rows.slice().reverse(), 120) },
        '/sale-reversals': { status: 200, body: noReversals() },
      },
    });
    const res = await exploration.sales(QUERY);
    expect(
      res.ok && [res.value.chosen.frequency, res.value.purchases.length, res.value.purchases_total],
    ).toEqual([120, 100, 120]);
    expect(res.ok && res.value.purchases[0]?.sale_id).toBe(1119);
  });

  it('shows a refusal of the sales as returned, and reads nothing else', async () => {
    const { exploration, seen } = await signedIn('ANALYST', forbidden('/v1/sales'));
    expect(await exploration.sales(QUERY)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role', correlation_id: 'corr-forbidden-role' },
    });
    expect(paths(seen)).toHaveLength(1);
  });

  it.each([
    ['a customer that is not a UUID', { ...QUERY, customer_id: 'x/y' }],
    ['no customer', { from: WINDOW.from, to: WINDOW.to }],
    ['a window with no end', { customer_id: CUSTOMER, from: WINDOW.from }],
    ['a window that ends before it starts', { ...QUERY, from: WINDOW.to, to: WINDOW.from }],
    ['a window of one instant', { ...QUERY, to: WINDOW.from }],
    ['a window longer than the longest rule set', { ...QUERY, from: '2010-01-01T00:00:00Z' }],
    ['a date with no offset', { ...QUERY, from: '2026-10-01' }],
    ['one end of the profile’s window', { ...QUERY, compare_from: PROFILE_WINDOW.from }],
    ['a query that is not an object', 'x'],
  ])('refuses %s before sending anything', async (_name, query) => {
    const { exploration, seen } = await signedIn('ANALYST');
    expect(await exploration.sales(query)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toEqual([]);
  });
});

describe('what the customer restricted and allowed', () => {
  it('reads the restrictions and names the channels and categories', async () => {
    const { exploration, seen } = await signedIn('ANALYST');
    const res = await exploration.restrictions(CUSTOMER);
    expect(paths(seen)[0]).toBe(
      `GET /api/customer-preferences/v1/preference-profiles/${CUSTOMER}/restrictions`,
    );
    expect(res).toMatchObject({
      ok: true,
      value: {
        personalization_allowed: true,
        channels: [{ id: 5, name: 'call_center' }],
        categories: [{ id: 9, name: 'Beverages' }],
      },
    });
  });

  it('shows a customer who withdrew personalization, with nothing else restricted', async () => {
    const { exploration } = await signedIn('MARKETING', {
      restrictions: example('customer-preferences', 'get-restrictions-personalization-withdrawn'),
    });
    expect(await exploration.restrictions(OBJECTED)).toEqual({
      ok: true,
      value: { customer_id: OBJECTED, personalization_allowed: false, channels: [], categories: [] },
    });
  });

  it('refuses a role that may not read a restriction, before asking, and shows a service’s refusal as returned', async () => {
    const none = await signedIn('INVENTORY_PLANNER');
    expect(await none.exploration.restrictions(CUSTOMER)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role', correlation_id: '-' },
    });
    expect(none.seen).toEqual([]);

    const service = await signedIn('ANALYST', forbidden('/restrictions'));
    expect(await service.exploration.restrictions(CUSTOMER)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role', correlation_id: 'corr-forbidden-role' },
    });
  });

  it('reads the consent record only for the roles ADR-0066 names', async () => {
    for (const role of ['ADMIN', 'AUDITOR', 'STORE_MANAGER']) {
      const { exploration, seen } = await signedIn(role);
      const res = await exploration.consent(CUSTOMER);
      expect(paths(seen)).toEqual([
        `GET /api/customer-preferences/v1/preference-profiles/${CUSTOMER}/consent`,
      ]);
      expect(res).toMatchObject({ ok: true, value: { as_of: '2026-11-05T14:30:00Z' } });
      expect(res.ok && res.value.purposes.map((p) => [p.purpose, p.state, p.basis])).toEqual([
        ['PERSONALIZATION', 'GRANTED', 'EXPLICIT'],
        ['MARKETING_CONTACT', 'WITHDRAWN', 'PROGRAMME_DEFAULT'],
        ['PROFILING', 'GRANTED', 'PROGRAMME_DEFAULT'],
      ]);
    }
    for (const role of ['ANALYST', 'MARKETING']) {
      const { exploration, seen } = await signedIn(role);
      expect(await exploration.consent(CUSTOMER)).toMatchObject({
        ok: false,
        error: { code: 'forbidden_role', message: expect.stringContaining('consent record') },
      });
      expect(seen).toEqual([]);
    }
  });

  it('keeps a purpose that was never recorded as not recorded, with nothing invented', async () => {
    const { exploration } = await signedIn('ADMIN', {
      consent: example('customer-preferences', 'get-consent-not-recorded'),
    });
    const res = await exploration.consent(CUSTOMER);
    expect(res.ok && res.value.purposes).toEqual([
      { purpose: 'PERSONALIZATION', state: 'NOT_RECORDED' },
      { purpose: 'MARKETING_CONTACT', state: 'NOT_RECORDED' },
      { purpose: 'PROFILING', state: 'NOT_RECORDED' },
    ]);
  });

  it('shows a service that cannot record the read as returned: audit_unavailable', async () => {
    const answers: Record<string, Answer> = {
      '/consent': { status: 503, body: example('customer-preferences', 'audit-unavailable') },
    };
    const { exploration } = await signedIn('ADMIN', { override: answers });
    expect(await exploration.consent(CUSTOMER)).toMatchObject({
      ok: false,
      error: {
        code: 'audit_unavailable',
        message: 'The read could not be recorded, so it was not served.',
        correlation_id: 'corr-audit-unavailable',
      },
    });
  });

  it('reads declared interests apart from the inferred ones, with the window of each', async () => {
    const { exploration } = await signedIn('ANALYST');
    const res = await exploration.interests(CUSTOMER);
    expect(res).toMatchObject({
      ok: true,
      value: {
        declared: [
          { id: 2, name: 'Category 2 (unavailable)' },
          { id: 11, name: 'Cheese' },
          { id: 12, name: 'Yogurt' },
        ],
        inferred: [
          { category: { id: 7 }, source: 'NET_PURCHASES', window_days: 180, strength: 42 },
          { category: { id: 15 }, window_days: 180, strength: 18 },
        ],
      },
    });
  });
});

describe('every read follows the role', () => {
  it('refuses a role with none of the reads the profile and the sales before any call', async () => {
    // INVENTORY_PLANNER is the one staff role the contracts leave out of both. The floor roles of ADR-0060
    // cannot sign in here until the auth contract names them (F19-04).
    const { exploration, seen } = await signedIn('INVENTORY_PLANNER');
    expect(await exploration.profile(CUSTOMER)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
    expect(await exploration.sales(QUERY)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(seen).toEqual([]);
  });

  it('refuses every read when nobody is signed in', async () => {
    const { office, seen } = makeOffice(() => ({ status: 500, body: '' }));
    const exploration = new Exploration(office);
    for (const res of [
      await exploration.profile(CUSTOMER),
      await exploration.sales(QUERY),
      await exploration.restrictions(CUSTOMER),
      await exploration.consent(CUSTOMER),
      await exploration.interests(CUSTOMER),
    ])
      expect(res).toMatchObject({ ok: false, error: { code: 'signed_out' } });
    expect(seen).toEqual([]);
  });

  it('refuses a customer that is not a UUID before asking', async () => {
    const { exploration, seen } = await signedIn('ADMIN');
    for (const res of [
      await exploration.profile('x'),
      await exploration.restrictions('../x'),
      await exploration.consent(1),
      await exploration.interests(undefined),
    ])
      expect(res).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toEqual([]);
  });

  it('agrees with the profile example when the sales say the same', async () => {
    const { exploration } = await signedIn('ANALYST', { profile: agreeingProfile() });
    const profile = await exploration.profile(CUSTOMER);
    const sales = await exploration.sales(BOTH);
    expect(profile.ok && sales.ok).toBe(true);
    if (!profile.ok || !sales.ok || sales.value.compare === undefined) return;
    expect(agreement(profile.value.profile, sales.value.compare)).toEqual({
      recency: true,
      frequency: true,
      spend: true,
    });
  });
});
