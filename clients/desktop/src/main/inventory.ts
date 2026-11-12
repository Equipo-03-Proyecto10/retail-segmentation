import type { InventoryPlanView, InventoryQuery, Result } from '../shared/api';
import {
  categoryPlan,
  centsText,
  segmentPlan,
  toCents,
  type DemandLine,
  type SegmentLine,
} from '../shared/inventory';
import { INVENTORY_OPERATIONS } from '../shared/modules';
import { localError, type Backoffice } from './backoffice';
import { checked, failed, id, invalidInput, pageOf, record } from './checks';
import { Names, PRODUCT_BATCH } from './names';

// Demand and stock for the inventory planner (F28-12, #881). Two reads for one store and period:
// the demand report (analytics-kpis, grouped by category and segment, ADR-0079) and the stock levels
// (stock-service), with each product mapped to its category through products-service. Both are read
// through the transport, so each response is validated against its own XSD. The planner sees no
// customer: the demand report aggregates them, and nothing here reads a customer.

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DAY = 86_400_000;
const MAX_WINDOW_DAYS = 366;
const PAGE_SIZE = 100;
/** Pages read per list: 5,000 rows. A longer list is cut and the plan says so. */
const PAGE_LIMIT = 50;
const MAX_ID = 2147483647;

interface MeasureXml {
  code: string;
  kind: string;
  unit: string;
  value?: string;
}

interface DemandRowXml {
  segment_code?: string;
  segment_state?: string;
  store_id?: string;
  category_id?: string;
  synthetic: string;
  measures: MeasureXml[];
}

interface DemandReportXml {
  generated_at: string;
  data_origin: { synthetic: string };
  rows?: DemandRowXml[];
}

interface StockLevelXml {
  product_id: string;
  quantity_on_hand: string;
}

interface ProductXml {
  product_id: string;
  category_id: string;
}

const isTrue = (v: string): boolean => v === 'true' || v === '1';

const fail = (code: string, message: string): Result<never> => ({
  ok: false,
  error: localError(code, message),
});

function checkedQuery(raw: unknown): InventoryQuery {
  const q = record(raw, 'The plan');
  const store_id = id(q.store_id, MAX_ID, 'The store');
  const date = (v: unknown, label: string): string => {
    if (typeof v !== 'string' || !DATE.test(v) || Number.isNaN(Date.parse(v)))
      throw invalidInput(`${label} must be a date (YYYY-MM-DD).`);
    return v;
  };
  const from = date(q.from, 'From');
  const to = date(q.to, 'To');
  if (Date.parse(to) < Date.parse(from)) throw invalidInput('To cannot be before From.');
  if ((Date.parse(to) - Date.parse(from)) / DAY >= MAX_WINDOW_DAYS)
    throw invalidInput(`A plan covers at most ${MAX_WINDOW_DAYS} days.`);
  return { store_id, from, to };
}

/** The measure's value as cents, or null when the service did not send it. */
function amountOf(measures: MeasureXml[], code: string): number | null {
  const m = measures.find((x) => x.code === code);
  if (m?.value === undefined) return null;
  return toCents(m.value);
}

function countOf(measures: MeasureXml[], code: string): number | null {
  const m = measures.find((x) => x.code === code);
  if (m?.value === undefined) return null;
  const n = Number(m.value);
  return Number.isInteger(n) ? n : null;
}

export class Inventory {
  constructor(private readonly office: Backoffice) {}

  plan(raw: unknown): Promise<Result<InventoryPlanView>> {
    return checked(this.office, async () => {
      const query = checkedQuery(raw);
      const who = this.office.identity();
      const state = this.office.state();
      if (who === null || state.status !== 'signed_in') return fail('signed_out', 'You are signed out.');
      const needed = [
        INVENTORY_OPERATIONS.demand,
        INVENTORY_OPERATIONS.stock,
        INVENTORY_OPERATIONS.products,
        INVENTORY_OPERATIONS.stores,
      ];
      if (!needed.every((op) => state.operations.includes(op)))
        return fail('forbidden_role', 'Your role may not read this plan.');

      // 1. The demand report, grouped by category and segment for this store.
      const demandRows: DemandRowXml[] = [];
      let generatedAt = '';
      let synthetic = false;
      let demandTruncated = true;
      for (let page = 1; page <= PAGE_LIMIT; page += 1) {
        const params = new URLSearchParams({
          from: query.from,
          to: query.to,
          store_id: String(query.store_id),
          group_by: 'category,segment',
          page: String(page),
          size: String(PAGE_SIZE),
        });
        const res = await this.office.authed({
          method: 'GET',
          path: `/v1/kpi-reports/demand?${params.toString()}`,
        });
        if (!res.ok) return failed(res);
        const data = res.data?.DemandReport as DemandReportXml & Record<string, unknown>;
        generatedAt = data.generated_at;
        synthetic ||= isTrue(data.data_origin.synthetic);
        const rows = data.rows ?? [];
        demandRows.push(...rows);
        const total = pageOf(data).total;
        if (rows.length === 0 || demandRows.length >= total) {
          demandTruncated = false;
          break;
        }
      }

      const demand: DemandLine[] = [];
      const segments: SegmentLine[] = [];
      for (const r of demandRows) {
        synthetic ||= isTrue(r.synthetic);
        const units = countOf(r.measures, 'net_units');
        const orders = countOf(r.measures, 'net_orders');
        const cents = amountOf(r.measures, 'net_amount');
        if (units === null || orders === null || cents === null)
          return fail('figure_unavailable', 'The demand report did not return every figure.');
        const categoryId = Number(r.category_id);
        demand.push({ category_id: categoryId, units, orders, amount_cents: cents });
        segments.push({
          label:
            r.segment_state === 'assigned' && r.segment_code !== undefined
              ? r.segment_code
              : `[${r.segment_state ?? 'not grouped'}]`,
          units,
          orders,
          amount_cents: cents,
        });
      }

      // 2. The stock on hand in this store, one line per product.
      const onHandByProduct = new Map<number, number>();
      let stockTruncated = true;
      for (let page = 1; page <= PAGE_LIMIT; page += 1) {
        const params = new URLSearchParams({
          store_id: String(query.store_id),
          page: String(page),
          size: String(PAGE_SIZE),
        });
        const res = await this.office.authed({
          method: 'GET',
          path: `/v1/stock-levels?${params.toString()}`,
        });
        if (!res.ok) return failed(res);
        const data = res.data?.StockLevels as { StockLevel?: StockLevelXml[] } & Record<string, unknown>;
        const levels = data.StockLevel ?? [];
        for (const l of levels) {
          const product = Number(l.product_id);
          onHandByProduct.set(product, (onHandByProduct.get(product) ?? 0) + Number(l.quantity_on_hand));
        }
        if (levels.length === 0 || levels.length < PAGE_SIZE) {
          stockTruncated = false;
          break;
        }
      }

      // 3. Each stocked product's category, in batches, so stock adds up by category.
      const productIds = [...onHandByProduct.keys()];
      const categoryOf = new Map<number, number>();
      for (let i = 0; i < productIds.length; i += PRODUCT_BATCH) {
        const batch = productIds.slice(i, i + PRODUCT_BATCH);
        const params = new URLSearchParams({ ids: batch.join(','), size: String(PRODUCT_BATCH) });
        const res = await this.office.authed({ method: 'GET', path: `/v1/products?${params.toString()}` });
        if (!res.ok) return failed(res);
        const data = res.data?.Products as { Product?: ProductXml[] };
        for (const p of data.Product ?? []) categoryOf.set(Number(p.product_id), Number(p.category_id));
      }
      const onHand = new Map<number, number>();
      for (const [product, qty] of onHandByProduct) {
        const category = categoryOf.get(product);
        if (category !== undefined) onHand.set(category, (onHand.get(category) ?? 0) + qty);
      }

      // 4. Names for the store and each category the plan shows.
      const names = new Names(this.office);
      const storeName = await names.store(query.store_id);
      const categoryIds = new Set([...demand.map((d) => d.category_id), ...onHand.keys()]);
      const categoryNames = new Map<number, string>();
      for (const c of categoryIds) categoryNames.set(c, await names.category(c));

      const categories = categoryPlan(demand, onHand, categoryNames);
      const view: InventoryPlanView = {
        store_id: query.store_id,
        store_name: storeName,
        from: query.from,
        to: query.to,
        generated_at: generatedAt,
        synthetic,
        truncated: demandTruncated || stockTruncated,
        categories: categories.map((c) => ({
          category_id: c.category_id,
          category: c.category,
          demand_units: c.demand_units,
          demand_orders: c.demand_orders,
          demand_amount: centsText(c.demand_amount_cents),
          on_hand: c.on_hand,
          short_by: c.short_by,
        })),
        segments: segmentPlan(segments).map((s) => ({
          label: s.label,
          units: s.units,
          orders: s.orders,
          amount: centsText(s.amount_cents),
        })),
      };
      return { ok: true, value: view };
    });
  }
}
