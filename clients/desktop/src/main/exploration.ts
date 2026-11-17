import type { ErrorView, Result } from '../shared/api';
import {
  centsText,
  EXPLORATION_OPERATIONS as OPS,
  figuresFor,
  instant,
  netSales,
  profileWindow,
  DAY_MS,
  type ConsentView,
  type ExplorationProfileView,
  type ExplorationQuery,
  type ExplorationSalesView,
  type Figures,
  type FiguresView,
  type InterestsView,
  type NamedId,
  type NetSale,
  type ProfileState,
  type ProfileView,
  type PurchaseView,
  type PurposeView,
  type ReversalInput,
  type RestrictionsView,
  type RuleSetWindow,
  type SaleInput,
  type Share,
  type ShareView,
  type Window,
} from '../shared/exploration';
import { localError, plain, type Backoffice } from './backoffice';
import { checked, failed, invalidInput, optionalDateTime, pageOf, record, uuid } from './checks';
import { Names, PRODUCT_BATCH } from './names';
import { ContractDefect, defectView } from './transport';

// Customer exploration (F28-03, #872): one customer's behaviour across windows. Five services each
// answer for their own facts and this class only reads them through the transport (XML, XSD-validated):
// rfm-engine for the profile and the window it was scored in, sales-service for the sales and their
// reversals, products-service and categories-service for what each line was, and customer-preferences
// for what the customer restricted. The figures of a window are worked out here from the sales (the
// rules are in shared/exploration.ts); no service answers them. Each operation is checked against the
// role's x-mosaiq-roles before it is called, and a refusal by a service is passed on as it came.

/** A window can be as long as the longest rule set window (rfm-engine: 3650 days). */
const MAX_WINDOW_DAYS = 3650;
const PAGE_SIZE = 100;
/** 2,000 sales or reversals of one customer; a service that holds more is read to here and the screen says so. */
const PAGE_LIMIT = 20;
/** The purchases listed on screen; every sale of the window still counts in the figures. */
const PURCHASES_SHOWN = 100;

const truthy = (v: string | undefined): boolean => v === 'true' || v === '1';
const number = (v: string | undefined): number | undefined => (v === undefined ? undefined : Number(v));

/** Present only when the service sent it: a field it left out stays absent. */
function present<T extends object>(view: T): T {
  return Object.fromEntries(Object.entries(view).filter(([, v]) => v !== undefined)) as T;
}

interface ProfileXml {
  customer_id: string;
  state: string;
  rule_set_id?: string;
  r_days?: string;
  f_count?: string;
  m_total?: string;
  r_score?: string;
  f_score?: string;
  m_score?: string;
  label_code?: string;
  via_fallback?: string;
  computed_at?: string;
  next_change_at?: string;
}

interface SaleXml {
  sale_id: string;
  store_id: string;
  channel_id: string;
  occurred_at: string;
  lines?: { product_id: string; quantity: string; unit_price: string }[];
}

interface ReversalXml {
  sale_id: string;
  lines?: { product_id: string; quantity: string }[];
}

interface PurposeXml {
  purpose: string;
  state: string;
  decision_id?: string;
  recorded_at?: string;
  recorded_by?: string;
  source?: string;
  basis?: string;
}

type Collected<T> = { ok: true; items: T[]; total: number } | { ok: false; error: ErrorView };

function windowOf(from: unknown, to: unknown, label: string): Window {
  const a = optionalDateTime(from, `${label} start`);
  const b = optionalDateTime(to, `${label} end`);
  if (a === undefined || b === undefined) throw invalidInput(`${label} needs a start and an end.`);
  const [ta, tb] = [Date.parse(a), Date.parse(b)];
  if (ta >= tb) throw invalidInput(`${label} must end after it starts.`);
  if (tb - ta > MAX_WINDOW_DAYS * DAY_MS)
    throw invalidInput(`${label} is longer than ${MAX_WINDOW_DAYS} days.`);
  return { from: instant(ta), to: instant(tb) };
}

function profileOf(x: ProfileXml): ProfileView {
  return present({
    customer_id: x.customer_id,
    state: x.state as ProfileState,
    rule_set_id: number(x.rule_set_id),
    r_days: number(x.r_days),
    f_count: number(x.f_count),
    m_total: x.m_total,
    r_score: number(x.r_score),
    f_score: number(x.f_score),
    m_score: number(x.m_score),
    label_code: x.label_code,
    via_fallback: x.via_fallback === undefined ? undefined : truthy(x.via_fallback),
    computed_at: x.computed_at,
    next_change_at: x.next_change_at,
  });
}

export class Exploration {
  constructor(
    private readonly office: Backoffice,
    private readonly now: () => number = Date.now,
  ) {}

  /** What the role may call, or null when nobody is signed in. */
  private operations(): string[] | null {
    const state = this.office.state();
    return state.status === 'signed_in' ? state.operations : null;
  }

  /** A role reads a call only when the contract's x-mosaiq-roles allow its operation. */
  private allowed(operation: string, what: string): { ok: false; error: ErrorView } | null {
    const operations = this.operations();
    if (operations === null) return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return operations.includes(operation)
      ? null
      : {
          ok: false,
          error: localError('forbidden_role', `Your role may not read ${what} (operation ${operation}).`),
        };
  }

  /** Every page of a list, newest first as the services return them, up to the page limit. */
  private async collect<X, T>(
    path: string,
    params: URLSearchParams,
    root: string,
    element: string,
    parse: (row: X) => T,
  ): Promise<Collected<T>> {
    const items: T[] = [];
    let total = 0;
    for (let page = 1; page <= PAGE_LIMIT; page += 1) {
      params.set('page', String(page));
      params.set('size', String(PAGE_SIZE));
      const res = await this.office.authed({ method: 'GET', path: `${path}?${params.toString()}` });
      if (!res.ok) return { ok: false, error: plain(res.error) };
      const data = res.data?.[root] as Record<string, unknown>;
      const rows = (data[element] ?? []) as X[];
      items.push(...rows.map(parse));
      total = pageOf(data).total;
      if (rows.length === 0 || items.length >= total) break;
    }
    return { ok: true, items, total };
  }

  // ---------- the profile and its window ----------

  profile(customerId: unknown): Promise<Result<ExplorationProfileView>> {
    return checked(this.office, async () => {
      const cid = uuid(customerId, 'The customer');
      const refused = this.allowed(OPS.profile, 'the rfm-engine profile');
      if (refused !== null) return refused;
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/rfm-profiles/${encodeURIComponent(cid)}`,
      });
      if (!res.ok) return failed(res);
      const profile = profileOf(res.data?.RfmProfile as ProfileXml);
      const view: ExplorationProfileView = { profile };
      if (profile.rule_set_id !== undefined) {
        const found = await this.ruleSet(profile.rule_set_id);
        if ('error' in found) view.rule_set_error = found.error;
        else {
          view.rule_set = found.ruleSet;
          if (profile.computed_at !== undefined)
            view.window = profileWindow(profile.computed_at, found.ruleSet.window_days);
        }
      }
      return { ok: true, value: view };
    });
  }

  /**
   * The rule set carries the window. A role that may not read rule sets, or a read that fails, leaves the profile
   * without a window and says why; it never fails the profile, whose figures are still the service's.
   */
  private async ruleSet(id: number): Promise<{ ruleSet: RuleSetWindow } | { error: ErrorView }> {
    const refused = this.allowed(OPS.ruleSet, 'the rule set that holds the profile’s window');
    if (refused !== null) return { error: refused.error };
    try {
      const res = await this.office.authed({ method: 'GET', path: `/v1/rfm-rule-sets/${id}` });
      if (!res.ok) return { error: plain(res.error) };
      const x = res.data?.RfmRuleSet as {
        rule_set_id: string;
        name: string;
        window_days: string;
        published: string;
      };
      return {
        ruleSet: {
          rule_set_id: Number(x.rule_set_id),
          name: x.name,
          window_days: Number(x.window_days),
          published: truthy(x.published),
        },
      };
    } catch (e) {
      if (e instanceof ContractDefect) return { error: defectView(e) };
      throw e;
    }
  }

  // ---------- the sales ----------

  /** The units reversals took back. Soft: the sales figures are still shown, as charged, when this cannot be read. */
  private async reversals(customer: string): Promise<{ list: ReversalInput[] } | { error: ErrorView }> {
    const refused = this.allowed(OPS.reversals, 'the reversals of a sale');
    if (refused !== null) return { error: refused.error };
    try {
      const got = await this.collect<ReversalXml, ReversalInput>(
        '/v1/sale-reversals',
        new URLSearchParams({ customer_id: customer }),
        'SaleReversals',
        'SaleReversal',
        (r) => ({
          sale_id: Number(r.sale_id),
          lines: (r.lines ?? []).map((l) => ({
            product_id: Number(l.product_id),
            quantity: Number(l.quantity),
          })),
        }),
      );
      if (!got.ok) return { error: got.error };
      // A part of the reversals would give a net that is neither charged nor net.
      if (got.items.length < got.total)
        return {
          error: localError(
            'too_many_reversals',
            `The customer has ${got.total} reversals and only ${got.items.length} could be read.`,
          ),
        };
      return { list: got.items };
    } catch (e) {
      if (e instanceof ContractDefect) return { error: defectView(e) };
      throw e;
    }
  }

  /** The category of each product, 50 ids to a call. A batch that cannot be read leaves its products without one. */
  private async categoriesOf(
    ids: number[],
  ): Promise<{ categories: Map<number, number>; unknown: number; error?: ErrorView }> {
    const categories = new Map<number, number>();
    let error: ErrorView | undefined;
    const wanted = [...new Set(ids)];
    for (let i = 0; i < wanted.length; i += PRODUCT_BATCH) {
      const batch = wanted.slice(i, i + PRODUCT_BATCH);
      const params = new URLSearchParams({ ids: batch.join(','), size: String(PRODUCT_BATCH) });
      const res = await this.office.authed({ method: 'GET', path: `/v1/products?${params.toString()}` });
      if (!res.ok) {
        error ??= plain(res.error);
        continue;
      }
      const page = res.data?.Products as { Product?: { product_id: string; category_id: string }[] };
      for (const p of page.Product ?? []) categories.set(Number(p.product_id), Number(p.category_id));
    }
    const unknown = wanted.filter((id) => !categories.has(id)).length;
    return error === undefined ? { categories, unknown } : { categories, unknown, error };
  }

  sales(query: unknown): Promise<Result<ExplorationSalesView>> {
    return checked(this.office, async () => {
      const q = record(query, 'The query') as Partial<Record<keyof ExplorationQuery, unknown>>;
      const customer = uuid(q.customer_id, 'The customer');
      const chosen = windowOf(q.from, q.to, 'The sales window');
      const withCompare = q.compare_from !== undefined || q.compare_to !== undefined;
      const compare = withCompare
        ? windowOf(q.compare_from, q.compare_to, 'The profile’s window')
        : undefined;
      const refused = this.allowed(OPS.sales, 'the sales of a customer');
      if (refused !== null) return refused;

      // One read of the sales covers both windows.
      const windows = compare === undefined ? [chosen] : [chosen, compare];
      const params = new URLSearchParams({
        customer_id: customer,
        from: instant(Math.min(...windows.map((w) => Date.parse(w.from)))),
        to: instant(Math.max(...windows.map((w) => Date.parse(w.to)))),
      });
      const read = await this.collect<SaleXml, SaleInput>('/v1/sales', params, 'Sales', 'Sale', (s) => ({
        sale_id: Number(s.sale_id),
        occurred_at: s.occurred_at,
        store_id: Number(s.store_id),
        channel_id: Number(s.channel_id),
        lines: (s.lines ?? []).map((l) => ({
          product_id: Number(l.product_id),
          quantity: Number(l.quantity),
          unit_price: l.unit_price,
        })),
      }));
      if (!read.ok) return { ok: false, error: read.error };

      const reversals = await this.reversals(customer);
      const net = netSales(read.items, 'list' in reversals ? reversals.list : null);
      const products = await this.categoriesOf(read.items.flatMap((s) => s.lines.map((l) => l.product_id)));
      const names = new Names(this.office);
      const categoryNames = await names.categories();
      const categoryOf = (productId: number): number | null => products.categories.get(productId) ?? null;
      const endOf = (w: Window): string => instant(Math.min(Date.parse(w.to), this.now()));

      const shareOf = async (kind: 'category' | 'channel', s: Share): Promise<ShareView> => {
        let name: string;
        if (kind === 'channel') name = await names.channel(s.id as number);
        else if (s.id === null) name = 'No category known';
        else name = categoryNames?.get(s.id) ?? `Category ${s.id} (unavailable)`;
        return { id: s.id, name, purchases: s.purchases, units: s.units, spend: centsText(s.spend) };
      };
      const viewOf = async (f: Figures): Promise<FiguresView> =>
        present({
          window: f.window,
          end: f.end,
          frequency: f.frequency,
          spend: centsText(f.spend),
          last_purchase_at: f.last_purchase_at,
          recency_days: f.recency_days,
          categories: await Promise.all(f.categories.map((s) => shareOf('category', s))),
          channels: await Promise.all(f.channels.map((s) => shareOf('channel', s))),
        });

      const chosenFigures = figuresFor(net, chosen, endOf(chosen), categoryOf);
      const purchases = await this.purchasesOf(net, chosen, names);
      const view: ExplorationSalesView = {
        basis: 'list' in reversals ? 'net' : 'charged',
        sales_read: read.items.length,
        sales_total: read.total,
        incomplete: read.items.length < read.total,
        unknown_products: products.unknown,
        chosen: await viewOf(chosenFigures),
        purchases: purchases.shown,
        purchases_total: purchases.total,
      };
      if ('error' in reversals) view.reversals_error = reversals.error;
      if (products.error !== undefined) view.categories_error = products.error;
      if (compare !== undefined)
        view.compare = await viewOf(figuresFor(net, compare, endOf(compare), categoryOf));
      return { ok: true, value: view };
    });
  }

  /** The sales of the window, newest first, reversed ones marked. The first PURCHASES_SHOWN are named. */
  private async purchasesOf(
    net: readonly NetSale[],
    w: Window,
    names: Names,
  ): Promise<{ shown: PurchaseView[]; total: number }> {
    const [from, to] = [Date.parse(w.from), Date.parse(w.to)];
    const inWindow = net
      .filter((s) => s.time >= from && s.time < to)
      .sort((a, b) => b.time - a.time || b.sale_id - a.sale_id);
    const shown: PurchaseView[] = [];
    for (const s of inWindow.slice(0, PURCHASES_SHOWN))
      shown.push({
        sale_id: s.sale_id,
        occurred_at: s.occurred_at,
        store_name: await names.store(s.store_id),
        channel_name: await names.channel(s.channel_id),
        charged: centsText(s.charged),
        net: centsText(s.net),
        status: s.status,
      });
    return { shown, total: inWindow.length };
  }

  // ---------- what the customer allowed and restricted ----------

  restrictions(customerId: unknown): Promise<Result<RestrictionsView>> {
    return checked(this.office, async () => {
      const cid = uuid(customerId, 'The customer');
      const refused = this.allowed(OPS.restrictions, 'the restrictions of a customer');
      if (refused !== null) return refused;
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/preference-profiles/${encodeURIComponent(cid)}/restrictions`,
      });
      if (!res.ok) return failed(res);
      const x = res.data?.RestrictionProfile as {
        customer_id: string;
        personalization_allowed: string;
        restricted_channel_ids?: string[];
        restricted_category_ids?: string[];
      };
      const names = new Names(this.office);
      return {
        ok: true,
        value: {
          customer_id: x.customer_id,
          personalization_allowed: truthy(x.personalization_allowed),
          channels: await this.named(x.restricted_channel_ids, (n) => names.channel(n)),
          categories: await this.named(x.restricted_category_ids, (n) => names.category(n)),
        },
      };
    });
  }

  private async named(ids: string[] | undefined, name: (id: number) => Promise<string>): Promise<NamedId[]> {
    const out: NamedId[] = [];
    for (const raw of ids ?? []) out.push({ id: Number(raw), name: await name(Number(raw)) });
    return out;
  }

  /** The record of each purpose. Only the roles ADR-0066 names read it; any other is refused here and by the service. */
  consent(customerId: unknown): Promise<Result<ConsentView>> {
    return checked(this.office, async () => {
      const cid = uuid(customerId, 'The customer');
      const refused = this.allowed(OPS.consent, 'the consent record');
      if (refused !== null) return refused;
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/preference-profiles/${encodeURIComponent(cid)}/consent`,
      });
      if (!res.ok) return failed(res);
      const x = res.data?.ConsentState as { customer_id: string; as_of: string; purposes?: PurposeXml[] };
      return {
        ok: true,
        value: {
          customer_id: x.customer_id,
          as_of: x.as_of,
          purposes: (x.purposes ?? []).map((p): PurposeView =>
            present({
              purpose: p.purpose,
              state: p.state,
              decision_id: number(p.decision_id),
              recorded_at: p.recorded_at,
              recorded_by: p.recorded_by,
              source: p.source,
              basis: p.basis,
            }),
          ),
        },
      };
    });
  }

  /** What the customer declared, and, apart from it, what the platform inferred from net purchases (ADR-0066). */
  interests(customerId: unknown): Promise<Result<InterestsView>> {
    return checked(this.office, async () => {
      const cid = uuid(customerId, 'The customer');
      const refused = this.allowed(OPS.interests, 'the interests of a customer');
      if (refused !== null) return refused;
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/preference-profiles/${encodeURIComponent(cid)}/interests`,
      });
      if (!res.ok) return failed(res);
      const x = res.data?.InterestProfile as {
        customer_id: string;
        declared_category_ids?: string[];
        inferred_interests?: {
          category_id: string;
          source: string;
          window_days: string;
          strength: string;
          inferred_at: string;
        }[];
      };
      const names = new Names(this.office);
      const inferred = [];
      for (const i of x.inferred_interests ?? [])
        inferred.push({
          category: { id: Number(i.category_id), name: await names.category(Number(i.category_id)) },
          source: i.source,
          window_days: Number(i.window_days),
          strength: Number(i.strength),
          inferred_at: i.inferred_at,
        });
      return {
        ok: true,
        value: {
          customer_id: x.customer_id,
          declared: await this.named(x.declared_category_ids, (n) => names.category(n)),
          inferred,
        },
      };
    });
  }
}
