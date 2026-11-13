import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { Planning } from '../../src/main/planning';
import {
  claims,
  errorXml,
  example,
  makeOffice,
  MemoryVault,
  tokenResult,
  type Responder,
} from '../support/fake';

const KPI = '/api/analytics-kpis/v1/kpi-reports';
const INVALID_WITH_CUSTOMER = new URL(
  '../../../../contracts/analytics-kpis/examples/demand-coverage-with-customer.invalid.xml',
  import.meta.url,
).pathname;

function responder(role: string, report: () => { status: number; body?: string }): Responder {
  return (r) => {
    const url = new URL(r.url);
    if (r.method === 'POST' && url.pathname.endsWith('/v1/sessions'))
      return { status: 201, body: tokenResult('a1', 'r1') };
    if (url.pathname.endsWith('/v1/sessions/current'))
      return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
    if (url.pathname === `${KPI}/demand-coverage`) return report();
    if (url.pathname === '/api/stores/v1/stores')
      return { status: 200, body: example('stores', 'directory-page') };
    if (url.pathname === '/api/categories/v1/categories')
      return { status: 200, body: example('categories', 'list-categories') };
    return { status: 500 };
  };
}

async function setup(
  role = 'INVENTORY_PLANNER',
  report: () => { status: number; body?: string } = () => ({
    status: 200,
    body: example('analytics-kpis', 'demand-coverage'),
  }),
) {
  const { office, seen } = makeOffice(
    responder(role, report),
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  const planning = new Planning(office, () => new Date('2026-11-03T10:00:00Z'));
  const calls = () => seen.filter((s) => s.url.includes('/demand-coverage'));
  return { planning, calls };
}

const query = { from: '2026-10-01', to: '2026-10-28', store_id: 3, target_cover_days: 14 };

describe('inventory planning', () => {
  it('reads the report through the XSD and sends the window, store and target', async () => {
    const { planning, calls } = await setup();
    const res = await planning.report(query);
    if (!res.ok) throw new Error(res.error.code);
    const url = new URL(calls()[0]!.url);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      from: '2026-10-01',
      to: '2026-10-28',
      store_id: '3',
      target_cover_days: '14',
      page: '1',
      size: '20',
    });
    expect(res.value.window_days).toBe(28);
    expect(res.value.target_cover_days).toBe(14);
    expect(res.value.calculation).toMatch(/replenishment_units = max\(0/);
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 3 });
  });

  it('shows demand by segment and category beside availability and needs', async () => {
    const { planning } = await setup();
    const res = await planning.report(query);
    if (!res.ok) throw new Error(res.error.code);
    const [short, idle, unstocked] = res.value.rows;
    expect(short).toMatchObject({
      store_id: 3,
      category_id: 4,
      net_units: 336,
      daily_demand: '12.0000',
      on_hand: 120,
      days_of_cover: '10.00',
      replenishment_units: 48,
      synthetic: false,
    });
    // The stores and categories examples hold other ids, so the names fall back without failing the screen.
    expect([short!.store, short!.category]).toEqual(['Store 3 (unavailable)', 'Category 4 (unavailable)']);
    expect(short!.segments).toEqual([
      { segment: 'LOYAL', net_units: 168, share: '0.5000' },
      { segment: 'AT_RISK', net_units: 112, share: '0.3333' },
      { segment: 'No open assignment', net_units: 56, share: '0.1667' },
    ]);
    expect(idle).toMatchObject({ net_units: 0, days_of_cover: null, replenishment_units: 0, segments: [] });
    expect(unstocked).toMatchObject({ on_hand: 0, replenishment_units: 21, synthetic: true });
    expect(res.value.synthetic).toBe(true);
  });

  it('holds no customer data', async () => {
    const { planning } = await setup();
    const res = await planning.report(query);
    if (!res.ok) throw new Error(res.error.code);
    const text = JSON.stringify(res.value);
    expect(text).not.toMatch(/customer/i);
    expect(text).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  });

  it('refuses a report that carries a customer, as its schema does', async () => {
    const { planning } = await setup('INVENTORY_PLANNER', () => ({
      status: 200,
      body: readFileSync(INVALID_WITH_CUSTOMER, 'utf8'),
    }));
    const res = await planning.report(query);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe('contract_defect');
  });

  it.each([
    [{ ...query, to: '2026-11-03' }, /ended/],
    [{ ...query, from: '2026-10-29' }, /before From/],
    [{ ...query, from: '2024-01-01' }, /at most 731/],
    [{ ...query, target_cover_days: 181 }, /target cover/],
    [{ ...query, from: '1 Oct' }, /date/],
    [{ ...query, from: '2026-02-30' }, /date/],
    [{ ...query, store_id: 0 }, /store/],
  ])('refuses %j before sending anything', async (bad, message) => {
    const { planning, calls } = await setup();
    const res = await planning.report(bad);
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.error.code).toBe('invalid_input');
      expect(res.error.message).toMatch(message);
    }
    expect(calls()).toHaveLength(0);
  });

  it('refuses a role the contract does not allow before sending anything', async () => {
    const { planning, calls } = await setup('MARKETING', () => ({
      status: 403,
      body: errorXml('forbidden_role'),
    }));
    const res = await planning.report(query);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.error.code).toBe('forbidden_role');
    expect(calls()).toHaveLength(0);
  });
});
