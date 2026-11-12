import type {
  DecisionSummaryView,
  ExclusionView,
  InspectedItemView,
  InspectedReasonView,
  InspectionView,
  NamedView,
  Paged,
  Result,
} from '../shared/api';
import { RECOMMENDATION_OPERATIONS } from '../shared/modules';
import { STATUSES } from '../shared/recommendations';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  invalidInput,
  MAX_INT,
  optionalDateTime,
  optionalId,
  optionalText,
  optionalUuid,
  pageNumber,
  pageOf,
  pageSize,
  record,
  uuid,
} from './checks';
import { Names } from './names';

// The recommendations inspection (F28-08, #877): a recommendation judged at a reference store and
// channel, and the decisions recommendations-service recorded for it (ADR-0067). The service decides
// every kind, factor, availability and exclusion; this class reads them through the transport
// (XML, XSD-validated, `/v2`) and names the ids from their owners (ADR-0035), within the call.
// Every read is checked against its own operation first, since decisions are open to fewer roles
// than the recommendation itself. The three operations are published ahead of their implementation
// (ADR-0073); a service that does not answer yet is shown as it answers.

const MAX_LIMIT = 50;

interface ReasonXml {
  signal: string;
  segment_buyers?: string;
  category_id?: string;
  share?: string;
}

interface ItemXml {
  product_id: string;
  category_id: string;
  kind: string;
  position: string;
  factors?: { signals_matched: string; segment_buyers?: string; category_share?: string };
  availability: { store_id: string; channel_id?: string; quantity_on_hand: string };
  alternative_for?: string;
  unavailable_reason?: string;
  reasons: ReasonXml[];
}

interface ExclusionXml {
  reason: string;
  count: string;
}

interface AnswerXml {
  decision_id: string;
  customer_id?: string;
  status: string;
  computed_at: string;
  window?: { days: string; start: string; end: string };
  label_code?: string;
  run_id?: string;
  model_version_id?: string;
  usual_store_id?: string;
  store_id?: string;
  store_source?: string;
  channel_id?: string;
  exclusions?: ExclusionXml[];
  items?: ItemXml[];
}

interface SummaryXml {
  decision_id: string;
  customer_id?: string;
  status: string;
  computed_at: string;
  store_id?: string;
  store_source?: string;
  channel_id?: string;
  item_count: string;
  exclusions?: ExclusionXml[];
}

const number = (v: string | undefined): number | undefined => (v === undefined ? undefined : Number(v));

/** Present only when the service sent it: a field it left out stays absent. */
function present<T extends object>(view: T): T {
  return Object.fromEntries(Object.entries(view).filter(([, v]) => v !== undefined)) as T;
}

const exclusionOf = (x: ExclusionXml): ExclusionView => ({ reason: x.reason, count: Number(x.count) });

/** The names of every store, channel, product and category a screen call needs, each list read at most once. */
class Lookup {
  private readonly names: Names;
  private stores: Map<number, string> | null | undefined;
  private channels: Map<number, string> | null | undefined;
  private categories: Map<number, string> | null | undefined;
  private products = new Map<number, string>();

  constructor(office: Backoffice) {
    this.names = new Names(office);
  }

  /** Reads what the ids of one answer will need, and nothing else. */
  async prepare(need: {
    stores: Iterable<number>;
    channels: Iterable<number>;
    categories: Iterable<number>;
    products: Iterable<number>;
  }): Promise<void> {
    const stores = [...need.stores];
    const channels = [...need.channels];
    const categories = [...need.categories];
    const products = [...need.products];
    if (stores.length > 0) {
      const all = await this.names.stores();
      this.stores = all === null ? null : new Map(all.map((s) => [s.store_id, s.name]));
    }
    if (channels.length > 0) this.channels = await this.names.channels();
    if (categories.length > 0) this.categories = await this.names.categories();
    if (products.length > 0) this.products = await this.names.products(products);
  }

  store = (id: number): NamedView => named(id, this.stores?.get(id));
  channel = (id: number): NamedView => named(id, this.channels?.get(id));
  category = (id: number): NamedView => named(id, this.categories?.get(id));
  product = (id: number): NamedView => named(id, this.products.get(id));
}

function named(id: number, name: string | undefined): NamedView {
  return name === undefined ? { id } : { id, name };
}

function reasonOf(x: ReasonXml, lookup: Lookup): InspectedReasonView {
  return present({
    signal: x.signal,
    segment_buyers: number(x.segment_buyers),
    category: x.category_id === undefined ? undefined : lookup.category(Number(x.category_id)),
    share: x.share,
  });
}

function itemOf(x: ItemXml, lookup: Lookup): InspectedItemView {
  const f = x.factors;
  return present({
    product: lookup.product(Number(x.product_id)),
    category: lookup.category(Number(x.category_id)),
    kind: x.kind,
    position: Number(x.position),
    factors:
      f === undefined
        ? undefined
        : present({
            signals_matched: Number(f.signals_matched),
            segment_buyers: number(f.segment_buyers),
            category_share: f.category_share,
          }),
    availability: present({
      store: lookup.store(Number(x.availability.store_id)),
      channel:
        x.availability.channel_id === undefined
          ? undefined
          : lookup.channel(Number(x.availability.channel_id)),
      quantity_on_hand: Number(x.availability.quantity_on_hand),
    }),
    alternative_for: x.alternative_for === undefined ? undefined : lookup.product(Number(x.alternative_for)),
    unavailable_reason: x.unavailable_reason,
    reasons: x.reasons.map((r) => reasonOf(r, lookup)),
  });
}

/** Every id of an answer that has a name somewhere else. */
function needs(x: AnswerXml): Parameters<Lookup['prepare']>[0] {
  const items = x.items ?? [];
  const stores = new Set<number>();
  const channels = new Set<number>();
  const categories = new Set<number>();
  const products = new Set<number>();
  for (const id of [x.store_id, x.usual_store_id]) if (id !== undefined) stores.add(Number(id));
  if (x.channel_id !== undefined) channels.add(Number(x.channel_id));
  for (const i of items) {
    stores.add(Number(i.availability.store_id));
    if (i.availability.channel_id !== undefined) channels.add(Number(i.availability.channel_id));
    categories.add(Number(i.category_id));
    products.add(Number(i.product_id));
    if (i.alternative_for !== undefined) products.add(Number(i.alternative_for));
    for (const r of i.reasons) if (r.category_id !== undefined) categories.add(Number(r.category_id));
  }
  return { stores, channels, categories, products };
}

export class Recommendations {
  constructor(private readonly office: Backoffice) {}

  /** A role reads a call only when the contract's x-mosaiq-roles allow its operation. */
  private allowed(operation: string, what: string): Result<never> | null {
    const state = this.office.state();
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return state.operations.includes(operation)
      ? null
      : { ok: false, error: localError('forbidden_role', `Your role may not read ${what}.`) };
  }

  /** A recommendation judged at the store and channel given, with its factors, availability and alternatives. */
  inspect(query: unknown): Promise<Result<InspectionView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(RECOMMENDATION_OPERATIONS.inspect, 'recommendations');
      if (refused !== null) return refused;
      const q = record(query, 'The inspection');
      const params = new URLSearchParams({ customer_id: uuid(q.customer_id, 'The customer') });
      const store = optionalId(q.store_id, MAX_INT, 'The store');
      if (store !== undefined) params.set('store_id', String(store));
      const channel = optionalId(q.channel_id, MAX_INT, 'The channel');
      if (channel !== undefined) params.set('channel_id', String(channel));
      const limit = optionalId(q.limit, MAX_LIMIT, 'The limit');
      if (limit !== undefined) params.set('limit', String(limit));
      const res = await this.office.authed({
        method: 'GET',
        path: `/v2/recommendations?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: await this.inspectionOf(res.data?.Recommendation as AnswerXml) };
    });
  }

  /** The decisions the service recorded, newest first as it returns them. */
  listDecisions(query: unknown): Promise<Result<Paged<DecisionSummaryView>>> {
    return checked(this.office, async () => {
      const refused = this.allowed(RECOMMENDATION_OPERATIONS.decisions, 'recommendation decisions');
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const customer = optionalUuid(q.customer_id, 'The customer');
      if (customer !== undefined) params.set('customer_id', customer);
      const store = optionalId(q.store_id, MAX_INT, 'The store');
      if (store !== undefined) params.set('store_id', String(store));
      const status = optionalText(q.status, 'The status');
      if (status !== undefined && status !== '') {
        if (!STATUSES.includes(status))
          throw invalidInput('The status is not one of the recommendation statuses.');
        params.set('status', status);
      }
      const from = optionalDateTime(q.from, 'From');
      const to = optionalDateTime(q.to, 'To');
      if (from !== undefined && to !== undefined && Date.parse(to) <= Date.parse(from))
        throw invalidInput('To must be after From.');
      if (from !== undefined) params.set('from', from);
      if (to !== undefined) params.set('to', to);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({
        method: 'GET',
        path: `/v2/recommendation-decisions?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.RecommendationDecisions as {
        RecommendationDecisionSummary?: SummaryXml[];
      } & Record<string, unknown>;
      const rows = data.RecommendationDecisionSummary ?? [];
      const lookup = new Lookup(this.office);
      await lookup.prepare({
        stores: rows.flatMap((r) => (r.store_id === undefined ? [] : [Number(r.store_id)])),
        channels: rows.flatMap((r) => (r.channel_id === undefined ? [] : [Number(r.channel_id)])),
        categories: [],
        products: [],
      });
      const items = rows.map((r) =>
        present({
          decision_id: r.decision_id,
          customer_id: r.customer_id,
          status: r.status,
          computed_at: r.computed_at,
          store: r.store_id === undefined ? undefined : lookup.store(Number(r.store_id)),
          store_source: r.store_source,
          channel: r.channel_id === undefined ? undefined : lookup.channel(Number(r.channel_id)),
          item_count: Number(r.item_count),
          exclusions: (r.exclusions ?? []).map(exclusionOf),
        }),
      );
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  /** One recorded decision: what was offered, what was excluded and why. */
  openDecision(decisionId: unknown): Promise<Result<InspectionView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(RECOMMENDATION_OPERATIONS.decision, 'recommendation decisions');
      if (refused !== null) return refused;
      const id = uuid(decisionId, 'The decision');
      const res = await this.office.authed({
        method: 'GET',
        path: `/v2/recommendation-decisions/${encodeURIComponent(id)}`,
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: await this.inspectionOf(res.data?.RecommendationDecision as AnswerXml) };
    });
  }

  private async inspectionOf(x: AnswerXml): Promise<InspectionView> {
    const lookup = new Lookup(this.office);
    await lookup.prepare(needs(x));
    return present({
      decision_id: x.decision_id,
      customer_id: x.customer_id,
      status: x.status,
      computed_at: x.computed_at,
      window:
        x.window === undefined
          ? undefined
          : { days: Number(x.window.days), start: x.window.start, end: x.window.end },
      label_code: x.label_code,
      run_id: number(x.run_id),
      model_version_id: number(x.model_version_id),
      usual_store: x.usual_store_id === undefined ? undefined : lookup.store(Number(x.usual_store_id)),
      store: x.store_id === undefined ? undefined : lookup.store(Number(x.store_id)),
      store_source: x.store_source,
      channel: x.channel_id === undefined ? undefined : lookup.channel(Number(x.channel_id)),
      exclusions: (x.exclusions ?? []).map(exclusionOf),
      items: (x.items ?? []).map((i) => itemOf(i, lookup)),
    });
  }
}
