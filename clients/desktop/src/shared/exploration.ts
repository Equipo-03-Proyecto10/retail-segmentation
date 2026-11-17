import type { ErrorView } from './api';

// Customer exploration (F28-03, #872): one customer's behaviour set against more than one window.
// Pure data and rules, so the main process and the screen apply the same ones and a unit test can pin
// them. Money is whole cents here and never a float. A sale counts by its net units (ADR-0062): the
// units sold less the units a reversal took back, and a sale whose net amount is zero stops counting.

export const DAY_MS = 86_400_000;

/** The read operations the module calls (x-mosaiq-roles of each contract); each is checked before its call. */
export const EXPLORATION_OPERATIONS = {
  profile: 'getRfmProfile',
  ruleSet: 'getRfmRuleSet',
  sales: 'listSales',
  reversals: 'listSaleReversals',
  restrictions: 'getRestrictionProfile',
  consent: 'getConsentState',
  interests: 'getInterestProfile',
} as const;

/** A period of time: `from` is inclusive and `to` exclusive, both RFC 3339 instants in UTC. */
export interface Window {
  from: string;
  to: string;
}

export interface SaleInput {
  sale_id: number;
  occurred_at: string;
  store_id: number;
  channel_id: number;
  lines: { product_id: number; quantity: number; unit_price: string }[];
}

/** The units one reversal took back, from sales-service (`quantity` of each reversal line). */
export interface ReversalInput {
  sale_id: number;
  lines: { product_id: number; quantity: number }[];
}

/** 'counted' is a purchase; 'partly_reversed' still counts, for less; 'reversed' has no net amount and does not count. */
export type SaleStatus = 'counted' | 'partly_reversed' | 'reversed';

export interface NetSale {
  sale_id: number;
  occurred_at: string;
  time: number;
  store_id: number;
  channel_id: number;
  /** What the lines charged, in cents. */
  charged: number;
  /** What remains after reversals, in cents. Equal to `charged` when no reversal was applied. */
  net: number;
  status: SaleStatus;
  lines: { product_id: number; units: number; price: number }[];
}

/** "12.50" is 1250. The contracts' money is a string of digits, a point and two decimals. */
export function toCents(value: string): number {
  const [whole = '0', fraction = ''] = value.split('.');
  return Number(whole) * 100 + Number(fraction.padEnd(2, '0').slice(0, 2));
}

export function centsText(cents: number): string {
  const sign = cents < 0 ? '-' : '';
  const abs = Math.abs(cents);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`;
}

/** An instant as the contracts write it: UTC, with no fraction when there is none. */
export function instant(time: number): string {
  return new Date(time).toISOString().replace('.000Z', 'Z');
}

/** The window the rule set's `window_days` makes, ending at the instant the profile was computed (RN-65). */
export function profileWindow(computedAt: string, windowDays: number): Window {
  const end = Date.parse(computedAt);
  return { from: instant(end - windowDays * DAY_MS), to: instant(end) };
}

/** A window as the screen states it next to a figure: both instants and its length in days. */
export function windowText(w: Window): string {
  const days = Math.round((Date.parse(w.to) - Date.parse(w.from)) / DAY_MS);
  return `${w.from} to ${w.to} (${days} ${days === 1 ? 'day' : 'days'})`;
}

/**
 * Applies the reversals to the sales. With `reversals` null none could be read, so every sale stays as
 * charged and `status` says counted or, with nothing charged, reversed.
 */
export function netSales(sales: readonly SaleInput[], reversals: readonly ReversalInput[] | null): NetSale[] {
  const returned = new Map<string, number>();
  for (const r of reversals ?? [])
    for (const l of r.lines) {
      const key = `${r.sale_id}:${l.product_id}`;
      returned.set(key, (returned.get(key) ?? 0) + l.quantity);
    }
  return sales.map((s) => {
    const lines = s.lines.map((l) => ({
      product_id: l.product_id,
      units: Math.max(0, l.quantity - (returned.get(`${s.sale_id}:${l.product_id}`) ?? 0)),
      price: toCents(l.unit_price),
    }));
    const charged = s.lines.reduce((sum, l) => sum + l.quantity * toCents(l.unit_price), 0);
    const net = lines.reduce((sum, l) => sum + l.units * l.price, 0);
    const status: SaleStatus = net === 0 ? 'reversed' : net < charged ? 'partly_reversed' : 'counted';
    return {
      sale_id: s.sale_id,
      occurred_at: s.occurred_at,
      time: Date.parse(s.occurred_at),
      store_id: s.store_id,
      channel_id: s.channel_id,
      charged,
      net,
      status,
      lines,
    };
  });
}

/** One category or one channel inside a window. `id` is null for the lines whose product has no category the console could read. */
export interface Share {
  id: number | null;
  /** Purchases that include it. A purchase counts once in each category it spans, so categories add up to more than the window. */
  purchases: number;
  units: number;
  /** Net amount, in cents. */
  spend: number;
}

export interface Figures {
  window: Window;
  /** The instant recency is measured to. */
  end: string;
  frequency: number;
  /** In cents. */
  spend: number;
  last_purchase_at?: string;
  recency_days?: number;
  categories: Share[];
  channels: Share[];
}

const byShare = (a: Share, b: Share): number =>
  b.spend - a.spend || b.purchases - a.purchases || (a.id ?? Infinity) - (b.id ?? Infinity);

/**
 * The figures of one window, from the sales that fall in it: frequency, spend, recency, and the share of each
 * category and channel. `end` is where recency is measured to; `categoryOf` is null for a product with no known category.
 */
export function figuresFor(
  sales: readonly NetSale[],
  window: Window,
  end: string,
  categoryOf: (productId: number) => number | null,
): Figures {
  const from = Date.parse(window.from);
  const to = Date.parse(window.to);
  const counted = sales.filter((s) => s.status !== 'reversed' && s.time >= from && s.time < to);
  const categories = new Map<number | null, Share>();
  const channels = new Map<number | null, Share>();
  const share = (m: Map<number | null, Share>, id: number | null): Share => {
    let s = m.get(id);
    if (s === undefined) {
      s = { id, purchases: 0, units: 0, spend: 0 };
      m.set(id, s);
    }
    return s;
  };
  for (const sale of counted) {
    const channel = share(channels, sale.channel_id);
    channel.purchases += 1;
    channel.units += sale.lines.reduce((n, l) => n + l.units, 0);
    channel.spend += sale.net;
    const seen = new Set<number | null>();
    for (const line of sale.lines) {
      if (line.units === 0) continue;
      const category = share(categories, categoryOf(line.product_id));
      if (!seen.has(category.id)) {
        seen.add(category.id);
        category.purchases += 1;
      }
      category.units += line.units;
      category.spend += line.units * line.price;
    }
  }
  const last = counted.reduce<number | undefined>(
    (t, s) => (t === undefined || s.time > t ? s.time : t),
    undefined,
  );
  const figures: Figures = {
    window,
    end,
    frequency: counted.length,
    spend: counted.reduce((sum, s) => sum + s.net, 0),
    categories: [...categories.values()].sort(byShare),
    channels: [...channels.values()].sort(byShare),
  };
  if (last !== undefined) {
    figures.last_purchase_at = instant(last);
    figures.recency_days = Math.max(0, Math.floor((Date.parse(end) - last) / DAY_MS));
  }
  return figures;
}

/** What the profile of rfm-engine says about the same customer, to be set beside the sales over its window. */
export interface ProfileFigures {
  r_days?: number;
  f_count?: number;
  /** The decimal exactly as the service sent it. */
  m_total?: string;
}

export interface Agreement {
  recency: boolean;
  frequency: boolean;
  spend: boolean;
}

/** Whether the sales, over the profile's window, give the figures the profile holds. A difference is shown, never settled. */
export function agreement(
  profile: ProfileFigures,
  sales: Pick<FiguresView, 'recency_days' | 'frequency' | 'spend'>,
): Agreement {
  return {
    recency: profile.r_days === sales.recency_days,
    frequency: (profile.f_count ?? 0) === sales.frequency,
    spend: toCents(profile.m_total ?? '0.00') === toCents(sales.spend),
  };
}

// ---------- what the main process hands the screen ----------

export interface NamedId {
  id: number;
  name: string;
}

/** The rule set a profile was scored under, which is where its window comes from. */
export interface RuleSetWindow {
  rule_set_id: number;
  name: string;
  window_days: number;
  published: boolean;
}

export type ProfileState = 'scored' | 'unassigned' | 'pending';

export interface ProfileView {
  customer_id: string;
  state: ProfileState;
  rule_set_id?: number;
  r_days?: number;
  f_count?: number;
  m_total?: string;
  r_score?: number;
  f_score?: number;
  m_score?: number;
  label_code?: string;
  via_fallback?: boolean;
  computed_at?: string;
  next_change_at?: string;
}

export interface ExplorationProfileView {
  profile: ProfileView;
  /** Absent when the role may not read rule sets or the read failed; `rule_set_error` says which. */
  rule_set?: RuleSetWindow;
  rule_set_error?: ErrorView;
  /** The profile's window: the rule set's days ending at `computed_at`. Absent when either is unknown. */
  window?: Window;
}

export interface ShareView {
  id: number | null;
  name: string;
  purchases: number;
  units: number;
  spend: string;
}

export interface FiguresView {
  window: Window;
  end: string;
  frequency: number;
  spend: string;
  last_purchase_at?: string;
  recency_days?: number;
  categories: ShareView[];
  channels: ShareView[];
}

export interface PurchaseView {
  sale_id: number;
  occurred_at: string;
  store_name: string;
  channel_name: string;
  charged: string;
  net: string;
  status: SaleStatus;
}

export interface ExplorationQuery {
  customer_id: string;
  /** The chosen sales window. */
  from: string;
  to: string;
  /** The profile's window, to be read from the same sales. Both or neither. */
  compare_from?: string;
  compare_to?: string;
}

export interface ExplorationSalesView {
  /** 'net': reversals were read and applied. 'charged': they could not be, and `reversals_error` says why. */
  basis: 'net' | 'charged';
  reversals_error?: ErrorView;
  /** Sales read for the covering range, and how many the service holds there. */
  sales_read: number;
  sales_total: number;
  /** The service holds more sales than were read: the figures of the oldest days are partial. */
  incomplete: boolean;
  /** Products whose category could not be read; their lines are listed under no category. */
  unknown_products: number;
  /** Why the first category read failed, when one did. */
  categories_error?: ErrorView;
  chosen: FiguresView;
  compare?: FiguresView;
  /** The sales of the chosen window, newest first, reversed ones marked. */
  purchases: PurchaseView[];
  purchases_total: number;
}

export interface RestrictionsView {
  customer_id: string;
  /** True only when the current PERSONALIZATION decision is GRANTED; a purpose never recorded reads false too. */
  personalization_allowed: boolean;
  channels: NamedId[];
  categories: NamedId[];
}

export interface PurposeView {
  purpose: string;
  state: string;
  decision_id?: number;
  recorded_at?: string;
  recorded_by?: string;
  source?: string;
  basis?: string;
}

export interface ConsentView {
  customer_id: string;
  as_of: string;
  purposes: PurposeView[];
}

export interface InferredInterestView {
  category: NamedId;
  source: string;
  window_days: number;
  strength: number;
  inferred_at: string;
}

export interface InterestsView {
  customer_id: string;
  declared: NamedId[];
  inferred: InferredInterestView[];
}
