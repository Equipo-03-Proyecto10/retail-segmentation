// Demand and stock for the inventory planner (F28-12, #881): demand by category and by segment for one
// store and period, next to the units on hand in that store. Pure functions over the figures the main
// process read, so the screen and the unit tests apply the same rules. A planner's figures never carry
// a customer: the demand report aggregates them (ADR-0079).
//
// "Short by" is a planning hint, not a forecast: the period's demand in units above the units on hand
// today. It is never negative, and it does not say how much to order.

export interface DemandLine {
  category_id: number;
  units: number;
  orders: number;
  amount_cents: number;
}

export interface SegmentLine {
  /** The label code when the segment is assigned; otherwise the state, shown in brackets. */
  label: string;
  units: number;
  orders: number;
  amount_cents: number;
}

export interface CategoryPlanLine {
  category_id: number;
  category: string;
  demand_units: number;
  demand_orders: number;
  demand_amount_cents: number;
  on_hand: number;
  short_by: number;
}

/** A category's figures, aggregated over every segment of the store. A category with stock and no demand is listed too. */
export function categoryPlan(
  demand: readonly DemandLine[],
  onHand: ReadonlyMap<number, number>,
  names: ReadonlyMap<number, string>,
): CategoryPlanLine[] {
  const byCategory = new Map<number, Omit<CategoryPlanLine, 'category' | 'on_hand' | 'short_by'>>();
  for (const line of demand) {
    const cell = byCategory.get(line.category_id) ?? {
      category_id: line.category_id,
      demand_units: 0,
      demand_orders: 0,
      demand_amount_cents: 0,
    };
    cell.demand_units += line.units;
    cell.demand_orders += line.orders;
    cell.demand_amount_cents += line.amount_cents;
    byCategory.set(line.category_id, cell);
  }
  for (const id of onHand.keys()) {
    if (!byCategory.has(id))
      byCategory.set(id, { category_id: id, demand_units: 0, demand_orders: 0, demand_amount_cents: 0 });
  }
  return [...byCategory.values()]
    .map((c) => {
      const stock = onHand.get(c.category_id) ?? 0;
      return {
        ...c,
        category: names.get(c.category_id) ?? `Category ${c.category_id} (unavailable)`,
        on_hand: stock,
        short_by: Math.max(0, c.demand_units - stock),
      };
    })
    .sort((a, b) => a.category.localeCompare(b.category) || a.category_id - b.category_id);
}

/** The period's demand by segment label, across every category; the largest demand first. */
export function segmentPlan(segments: readonly SegmentLine[]): SegmentLine[] {
  const byLabel = new Map<string, SegmentLine>();
  for (const s of segments) {
    const cell = byLabel.get(s.label) ?? { label: s.label, units: 0, orders: 0, amount_cents: 0 };
    cell.units += s.units;
    cell.orders += s.orders;
    cell.amount_cents += s.amount_cents;
    byLabel.set(s.label, cell);
  }
  return [...byLabel.values()].sort((a, b) => b.units - a.units || a.label.localeCompare(b.label));
}

/** A decimal string such as "17920.00" in cents. Null when the text is not a decimal with at most two places. */
export function toCents(decimal: string): number | null {
  if (!/^-?\d+(\.\d{1,2})?$/.test(decimal)) return null;
  const negative = decimal.startsWith('-');
  const [whole, fraction = ''] = (negative ? decimal.slice(1) : decimal).split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  return negative ? -cents : cents;
}

/** Cents as the two-place text the screen shows. */
export function centsText(cents: number): string {
  const sign = cents < 0 ? '-' : '';
  const abs = Math.abs(cents);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`;
}
