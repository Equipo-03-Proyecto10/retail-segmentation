import { describe, expect, it } from 'vitest';
import { Inventory } from '../../src/main/inventory';
import { categoryPlan, centsText, segmentPlan, toCents, type DemandLine } from '../../src/shared/inventory';
import { claims, example, makeOffice, MemoryVault, tokenResult, type Responder } from '../support/fake';

// Demand and stock for the inventory planner (F28-12, #881): the rules are pure, and the plan is read
// through the same transport the screen uses, against the contract examples.

describe('money in cents', () => {
  it('reads a two-place decimal and rejects anything else', () => {
    expect(toCents('17920.00')).toBe(1792000);
    expect(toCents('960.5')).toBe(96050);
    expect(toCents('-3.10')).toBe(-310);
    expect(toCents('12.345')).toBeNull();
    expect(toCents('12,00')).toBeNull();
  });

  it('shows cents as two places', () => {
    expect(centsText(1792000)).toBe('17920.00');
    expect(centsText(96050)).toBe('960.50');
    expect(centsText(5)).toBe('0.05');
    expect(centsText(-310)).toBe('-3.10');
  });
});

describe('categoryPlan', () => {
  const names = new Map([
    [12, 'Cameras'],
    [15, 'Tags'],
  ]);

  it('adds a category up over its segments and sets short-by against the units on hand', () => {
    const demand: DemandLine[] = [
      { category_id: 12, units: 140, orders: 66, amount_cents: 1792000 },
      { category_id: 12, units: 12, orders: 7, amount_cents: 156000 },
      { category_id: 15, units: 40, orders: 25, amount_cents: 96000 },
    ];
    expect(categoryPlan(demand, new Map([[12, 35]]), names)).toEqual([
      {
        category_id: 12,
        category: 'Cameras',
        demand_units: 152,
        demand_orders: 73,
        demand_amount_cents: 1948000,
        on_hand: 35,
        short_by: 117,
      },
      {
        category_id: 15,
        category: 'Tags',
        demand_units: 40,
        demand_orders: 25,
        demand_amount_cents: 96000,
        on_hand: 0,
        short_by: 40,
      },
    ]);
  });

  it('never reports a negative shortfall when stock covers the demand', () => {
    const [line] = categoryPlan(
      [{ category_id: 12, units: 10, orders: 4, amount_cents: 0 }],
      new Map([[12, 50]]),
      names,
    );
    expect(line?.short_by).toBe(0);
    expect(line?.on_hand).toBe(50);
  });

  it('lists a category with stock and no demand, with no shortfall', () => {
    const plan = categoryPlan([], new Map([[12, 8]]), names);
    expect(plan).toHaveLength(1);
    expect(plan[0]).toMatchObject({ category: 'Cameras', demand_units: 0, on_hand: 8, short_by: 0 });
  });

  it('names a category it cannot read, rather than hiding it', () => {
    const [line] = categoryPlan(
      [{ category_id: 99, units: 1, orders: 1, amount_cents: 100 }],
      new Map(),
      names,
    );
    expect(line?.category).toBe('Category 99 (unavailable)');
  });
});

describe('segmentPlan', () => {
  it('merges the same label across categories and puts the largest demand first', () => {
    const plan = segmentPlan([
      { label: '[no_open_assignment]', units: 12, orders: 7, amount_cents: 156000 },
      { label: 'LOYAL', units: 140, orders: 66, amount_cents: 1792000 },
      { label: 'LOYAL', units: 40, orders: 25, amount_cents: 96000 },
    ]);
    expect(plan).toEqual([
      { label: 'LOYAL', units: 180, orders: 91, amount_cents: 1888000 },
      { label: '[no_open_assignment]', units: 12, orders: 7, amount_cents: 156000 },
    ]);
  });
});

// ---------- the plan as read through the transport ----------

/** Stock for two products in category 12, one store. Valid against stock's XSD. */
const STOCK = `<?xml version="1.0" encoding="UTF-8"?>
<StockLevels xmlns="https://mosaiq.maxthecoder.online/xml/stock/v1"
             xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1">
  <StockLevel>
    <store_id>1</store_id>
    <product_id>1001</product_id>
    <quantity_on_hand>30</quantity_on_hand>
    <updated_at>2026-10-15T16:42:10Z</updated_at>
  </StockLevel>
  <StockLevel>
    <store_id>1</store_id>
    <product_id>1002</product_id>
    <quantity_on_hand>5</quantity_on_hand>
    <updated_at>2026-10-15T16:42:10Z</updated_at>
  </StockLevel>
  <common:PageInfo>
    <common:page>1</common:page>
    <common:size>100</common:size>
    <common:total>2</common:total>
  </common:PageInfo>
</StockLevels>`;

const DEMAND = example('analytics-kpis', 'demand');
const DEMAND_PATH = '/api/analytics-kpis/v1/kpi-reports/demand';

function responder(role: string, options: { demand?: string; stock?: string } = {}): Responder {
  return (r) => {
    const url = new URL(r.url);
    if (r.method === 'POST' && url.pathname.endsWith('/v1/sessions'))
      return { status: 201, body: tokenResult('a1', 'r1') };
    if (url.pathname.endsWith('/v1/sessions/current'))
      return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
    if (url.pathname === DEMAND_PATH) return { status: 200, body: options.demand ?? DEMAND };
    if (url.pathname === '/api/stock/v1/stock-levels') return { status: 200, body: options.stock ?? STOCK };
    if (url.pathname === '/api/products/v1/products')
      return { status: 200, body: example('products', 'products') };
    if (url.pathname === '/api/stores/v1/stores')
      return { status: 200, body: example('stores', 'directory-page') };
    if (url.pathname === '/api/categories/v1/categories')
      return { status: 200, body: example('categories', 'list-categories') };
    return { status: 500 };
  };
}

async function planFor(
  role: string,
  options: { demand?: string; stock?: string } = {},
  query: unknown = undefined,
) {
  const { office, seen } = makeOffice(
    responder(role, options),
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  const inventory = new Inventory(office);
  const result = await inventory.plan(query ?? { store_id: 1, from: '2026-10-01', to: '2026-10-31' });
  return { result, seen };
}

describe('the inventory plan', () => {
  it('shows each category next to the units on hand, and the demand by segment', async () => {
    const { result } = await planFor('INVENTORY_PLANNER');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const plan = result.value;
    expect(plan.store_id).toBe(1);
    expect(plan.store_name).toBe('Centro Historico');
    expect(plan.synthetic).toBe(false);
    expect(plan.truncated).toBe(false);
    expect(plan.categories).toHaveLength(2);
    expect(plan.categories).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          category_id: 12,
          category: 'Yogurt',
          demand_units: 152,
          demand_orders: 73,
          demand_amount: '19480.00',
          on_hand: 35,
          short_by: 117,
        }),
        expect.objectContaining({
          category_id: 15,
          demand_units: 40,
          demand_orders: 25,
          demand_amount: '960.00',
          on_hand: 0,
          short_by: 40,
          category: 'Category 15 (unavailable)',
        }),
      ]),
    );
    expect(plan.segments).toEqual([
      { label: 'LOYAL', units: 180, orders: 91, amount: '18880.00' },
      { label: '[no_open_assignment]', units: 12, orders: 7, amount: '1560.00' },
    ]);
  });

  it('reads the demand report only for the store and the period asked', async () => {
    const { seen } = await planFor('INVENTORY_PLANNER');
    const demand = seen.find((s) => new URL(s.url).pathname === DEMAND_PATH);
    expect(demand).toBeDefined();
    const params = new URL(demand!.url).searchParams;
    expect(params.get('store_id')).toBe('1');
    expect(params.get('from')).toBe('2026-10-01');
    expect(params.get('to')).toBe('2026-10-31');
    expect(params.get('group_by')).toBe('category,segment');
  });

  it('refuses a role that may not read demand, before any figure is read', async () => {
    const { result, seen } = await planFor('MARKETING');
    expect(result).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(seen.some((s) => new URL(s.url).pathname === DEMAND_PATH)).toBe(false);
  });

  it('asks for a store and a period, and a period no longer than the window', async () => {
    const noStore = await planFor('INVENTORY_PLANNER', {}, { from: '2026-10-01', to: '2026-10-31' });
    expect(noStore.result).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    const long = await planFor(
      'INVENTORY_PLANNER',
      {},
      { store_id: 1, from: '2025-01-01', to: '2026-10-31' },
    );
    expect(long.result).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    const reversed = await planFor(
      'INVENTORY_PLANNER',
      {},
      { store_id: 1, from: '2026-10-31', to: '2026-10-01' },
    );
    expect(reversed.result).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
  });

  it('refuses a plan with a figure missing, rather than showing it as zero', async () => {
    const withoutAmount = DEMAND.replace('<value>17920.00</value>', '');
    const { result } = await planFor('INVENTORY_PLANNER', { demand: withoutAmount });
    expect(result).toMatchObject({ ok: false, error: { code: 'figure_unavailable' } });
  });

  it('says when the demand report was cut at the page limit', async () => {
    // Every page claims more rows than it holds, so the read stops at the limit and says so.
    const claimsMore = DEMAND.replace('<common:total>3</common:total>', '<common:total>9999</common:total>');
    const { result, seen } = await planFor('INVENTORY_PLANNER', { demand: claimsMore });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value.truncated).toBe(true);
    const demandCalls = seen.filter((s) => new URL(s.url).pathname === DEMAND_PATH).length;
    expect(demandCalls).toBe(50);
  });
});
