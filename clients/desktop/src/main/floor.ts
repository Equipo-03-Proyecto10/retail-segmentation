import type {
  CheckInDetailView,
  CheckInView,
  EngagementEventView,
  GroupBy,
  Paged,
  Result,
  ReviewView,
  SaleView,
  StockLevelView,
  StoreView,
  SummaryView,
} from '../shared/api';
import type { Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  id,
  invalidInput,
  MAX_INT,
  optionalBoolean,
  optionalDate,
  optionalDateTime,
  optionalId,
  optionalUuid,
  pageNumber,
  pageOf,
  pageSize,
  record,
  uuid,
} from './checks';
import { Names } from './names';

// What happened on the floor (F16-04, #417): stock below a threshold, sales read
// back, engagement summaries and voids, and staff check-ins with their reviews.
// Every value from the screens is checked here before a path or a body is built.

const MAX_STORE_ID = 32767;
const REASON_MAX = 500;
const GROUPS: GroupBy[] = ['store', 'day', 'type'];
const EVENT_TYPES = ['QR_SCAN', 'RECO_CLICK'];

const truthy = (v: string | undefined): boolean => v === 'true' || v === '1';

interface StockLevelXml {
  store_id: string;
  product_id: string;
  quantity_on_hand: string;
  updated_at: string;
}

interface SaleXml {
  sale_id: string;
  customer_id: string;
  store_id: string;
  channel_id: string;
  occurred_at: string;
  total: string;
  lines?: { product_id: string; quantity: string; unit_price: string }[];
}

interface EventXml {
  event_id: string;
  type: string;
  store_id: string;
  product_id: string;
  customer_id?: string;
  label_code?: string;
  rank?: string;
  recorded_by: string;
  occurred_at: string;
  recorded_at: string;
  voided: string;
  history?: { action: string; reason: string; by: string; at: string }[];
}

interface CheckInXml {
  check_in_id: string;
  user_id: string;
  store_id: string;
  latitude?: string;
  longitude?: string;
  accuracy_m?: string;
  distance_m?: string;
  reason: string;
  auto_verified: string;
  verified: string;
  occurred_at: string;
  recorded_at: string;
  reviews?: ReviewXml[];
}

interface ReviewXml {
  review_id: string;
  verified: string;
  reason: string;
  reviewed_by: string;
  reviewed_at: string;
}

/** "12.50" × 3 → "37.50", in whole cents so no float rounding shows. */
export function lineTotal(unitPrice: string, quantity: number): string {
  const [whole = '0', frac = ''] = unitPrice.split('.');
  const cents = (Number(whole) * 100 + Number(frac.padEnd(2, '0').slice(0, 2))) * quantity;
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`;
}

function reviewOf(r: ReviewXml): ReviewView {
  return {
    review_id: Number(r.review_id),
    verified: truthy(r.verified),
    reason: r.reason,
    reviewed_by: r.reviewed_by,
    reviewed_at: r.reviewed_at,
  };
}

function reason(f: FieldChecks, value: unknown): string {
  return f.text('reason', value, REASON_MAX);
}

export class Floor {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  private async get(path: string, params?: URLSearchParams) {
    const query = params === undefined ? '' : `?${params.toString()}`;
    return this.office.authed({ method: 'GET', path: `${path}${query}` });
  }

  // ---------- stores, for the pickers ----------

  stores(): Promise<Result<StoreView[]>> {
    return this.run(async () => {
      const stores = await new Names(this.office).stores();
      if (stores === null) {
        // Read once more to show the refusal as the service returned it.
        const res = await this.get('/v1/stores', new URLSearchParams({ page: '1', size: '1' }));
        return res.ok ? { ok: true, value: [] } : failed(res);
      }
      return { ok: true, value: stores };
    });
  }

  // ---------- stock ----------

  stockLevels(query: unknown): Promise<Result<Paged<StockLevelView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const store = optionalId(q.store_id, MAX_STORE_ID, 'The store');
      if (store !== undefined) params.set('store_id', String(store));
      const below = optionalId(q.below, MAX_INT, 'The threshold');
      if (below !== undefined) params.set('below', String(below));
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.get('/v1/stock-levels', params);
      if (!res.ok) return failed(res);
      const data = res.data?.StockLevels as { StockLevel?: StockLevelXml[] } & Record<string, unknown>;
      const levels = data.StockLevel ?? [];
      const names = new Names(this.office);
      await names.products(levels.map((l) => Number(l.product_id)));
      const items = await Promise.all(
        levels.map(async (l) => ({
          store_id: Number(l.store_id),
          store_name: await names.store(Number(l.store_id)),
          product_id: Number(l.product_id),
          product_name: names.product(Number(l.product_id)),
          quantity_on_hand: Number(l.quantity_on_hand),
          updated_at: l.updated_at,
        })),
      );
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  // ---------- sales ----------

  listSales(query: unknown): Promise<Result<Paged<SaleView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const customer = optionalUuid(q.customer_id, 'The customer');
      if (customer !== undefined) params.set('customer_id', customer);
      const store = optionalId(q.store_id, MAX_STORE_ID, 'The store');
      if (store !== undefined) params.set('store_id', String(store));
      const from = optionalDateTime(q.from, 'From');
      if (from !== undefined) params.set('from', from);
      const to = optionalDateTime(q.to, 'To');
      if (to !== undefined) params.set('to', to);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.get('/v1/sales', params);
      if (!res.ok) return failed(res);
      const data = res.data?.Sales as { Sale?: SaleXml[] } & Record<string, unknown>;
      const sales = data.Sale ?? [];
      const names = new Names(this.office);
      await names.products(sales.flatMap((s) => (s.lines ?? []).map((l) => Number(l.product_id))));
      const items = await Promise.all(sales.map((s) => this.saleOf(s, names)));
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  getSale(saleId: unknown): Promise<Result<SaleView>> {
    return this.run(async () => {
      const sid = id(saleId, Number.MAX_SAFE_INTEGER, 'The sale');
      const res = await this.get(`/v1/sales/${encodeURIComponent(sid)}`);
      if (!res.ok) return failed(res);
      const sale = res.data?.Sale as SaleXml;
      const names = new Names(this.office);
      await names.products((sale.lines ?? []).map((l) => Number(l.product_id)));
      return { ok: true, value: await this.saleOf(sale, names) };
    });
  }

  private async saleOf(s: SaleXml, names: Names): Promise<SaleView> {
    return {
      sale_id: Number(s.sale_id),
      customer_id: s.customer_id,
      store_id: Number(s.store_id),
      store_name: await names.store(Number(s.store_id)),
      channel_id: Number(s.channel_id),
      channel_name: await names.channel(Number(s.channel_id)),
      occurred_at: s.occurred_at,
      total: s.total,
      lines: (s.lines ?? []).map((l) => ({
        product_id: Number(l.product_id),
        product_name: names.product(Number(l.product_id)),
        quantity: Number(l.quantity),
        unit_price: l.unit_price,
        line_total: lineTotal(l.unit_price, Number(l.quantity)),
      })),
    };
  }

  // ---------- engagement ----------

  engagementSummary(query: unknown): Promise<Result<SummaryView>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The summary');
      if (typeof q.group_by !== 'string' || !GROUPS.includes(q.group_by as GroupBy))
        throw invalidInput('Group by must be store, day or type.');
      const params = new URLSearchParams({ group_by: q.group_by });
      const from = optionalDate(q.from, 'From');
      if (from !== undefined) params.set('from', from);
      const to = optionalDate(q.to, 'To');
      if (to !== undefined) params.set('to', to);
      const store = optionalId(q.store_id, MAX_STORE_ID, 'The store');
      if (store !== undefined) params.set('store_id', String(store));
      const res = await this.get('/v1/engagement-summaries', params);
      if (!res.ok) return failed(res);
      const data = res.data?.EngagementSummary as {
        from?: string;
        to?: string;
        group_by: GroupBy;
        groups?: { key: string; count: string }[];
      };
      const names = new Names(this.office);
      const groups = await Promise.all(
        (data.groups ?? []).map(async (g) => ({
          key: g.key,
          label: data.group_by === 'store' ? await names.store(Number(g.key)) : g.key,
          count: Number(g.count),
        })),
      );
      const value: SummaryView = { group_by: data.group_by, groups };
      if (data.from !== undefined) value.from = data.from;
      if (data.to !== undefined) value.to = data.to;
      return { ok: true, value };
    });
  }

  listEngagementEvents(query: unknown): Promise<Result<Paged<EngagementEventView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      if (q.type !== undefined) {
        if (typeof q.type !== 'string' || !EVENT_TYPES.includes(q.type))
          throw invalidInput('The type must be QR_SCAN or RECO_CLICK.');
        params.set('type', q.type);
      }
      const store = optionalId(q.store_id, MAX_STORE_ID, 'The store');
      if (store !== undefined) params.set('store_id', String(store));
      const customer = optionalUuid(q.customer_id, 'The customer');
      if (customer !== undefined) params.set('customer_id', customer);
      const voided = optionalBoolean(q.voided, 'Voided');
      if (voided !== undefined) params.set('voided', String(voided));
      const from = optionalDateTime(q.from, 'From');
      if (from !== undefined) params.set('from', from);
      const to = optionalDateTime(q.to, 'To');
      if (to !== undefined) params.set('to', to);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.get('/v1/engagement-events', params);
      if (!res.ok) return failed(res);
      const data = res.data?.EngagementEvents as { EngagementEvent?: EventXml[] } & Record<string, unknown>;
      const events = data.EngagementEvent ?? [];
      const names = new Names(this.office);
      await names.products(events.map((e) => Number(e.product_id)));
      const items = await Promise.all(events.map((e) => this.eventOf(e, names)));
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  /** Voids are corrections, never deletions: the event stays listed, marked voided. */
  voidEngagementEvent(eventId: unknown, why: unknown): Promise<Result<EngagementEventView>> {
    return this.run(async () => {
      const eid = uuid(eventId, 'The event');
      const f = new FieldChecks();
      const fields = { reason: reason(f, why) };
      f.done();
      const res = await this.office.authed({
        method: 'POST',
        path: `/v1/engagement-events/${encodeURIComponent(eid)}/void`,
        body: { root: 'VoidRequest', fields },
      });
      if (!res.ok) return failed(res);
      const event = res.data?.EngagementEvent as EventXml;
      const names = new Names(this.office);
      await names.products([Number(event.product_id)]);
      return { ok: true, value: await this.eventOf(event, names) };
    });
  }

  private async eventOf(e: EventXml, names: Names): Promise<EngagementEventView> {
    const view: EngagementEventView = {
      event_id: e.event_id,
      type: e.type,
      store_id: Number(e.store_id),
      store_name: await names.store(Number(e.store_id)),
      product_id: Number(e.product_id),
      product_name: names.product(Number(e.product_id)),
      recorded_by: e.recorded_by,
      occurred_at: e.occurred_at,
      recorded_at: e.recorded_at,
      voided: truthy(e.voided),
      history: (e.history ?? []).map((h) => ({ action: h.action, reason: h.reason, by: h.by, at: h.at })),
    };
    if (e.customer_id !== undefined) view.customer_id = e.customer_id;
    if (e.label_code !== undefined) view.label_code = e.label_code;
    if (e.rank !== undefined) view.rank = Number(e.rank);
    return view;
  }

  // ---------- check-ins ----------

  listCheckIns(query: unknown): Promise<Result<Paged<CheckInView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const store = optionalId(q.store_id, MAX_STORE_ID, 'The store');
      if (store !== undefined) params.set('store_id', String(store));
      const user = optionalUuid(q.user_id, 'The user');
      if (user !== undefined) params.set('user_id', user);
      const verified = optionalBoolean(q.verified, 'Verified');
      if (verified !== undefined) params.set('verified', String(verified));
      const from = optionalDateTime(q.from, 'From');
      if (from !== undefined) params.set('from', from);
      const to = optionalDateTime(q.to, 'To');
      if (to !== undefined) params.set('to', to);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.get('/v1/check-ins', params);
      if (!res.ok) return failed(res);
      const data = res.data?.CheckIns as { CheckIn?: CheckInXml[] } & Record<string, unknown>;
      const names = new Names(this.office);
      const items = await Promise.all((data.CheckIn ?? []).map((c) => this.checkInOf(c, names)));
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  getCheckIn(checkInId: unknown): Promise<Result<CheckInDetailView>> {
    return this.run(async () => {
      const cid = uuid(checkInId, 'The check-in');
      const res = await this.get(`/v1/check-ins/${encodeURIComponent(cid)}`);
      if (!res.ok) return failed(res);
      const detail = res.data?.CheckInDetail as CheckInXml;
      const view = await this.checkInOf(detail, new Names(this.office));
      return { ok: true, value: { ...view, reviews: (detail.reviews ?? []).map(reviewOf) } };
    });
  }

  /** A review is appended; nothing edits or deletes a check-in or an earlier review. */
  reviewCheckIn(checkInId: unknown, verified: unknown, why: unknown): Promise<Result<ReviewView>> {
    return this.run(async () => {
      const cid = uuid(checkInId, 'The check-in');
      const f = new FieldChecks();
      if (typeof verified !== 'boolean') f.details.push({ field: 'verified', code: 'required' });
      const fields = { verified: String(verified), reason: reason(f, why) };
      f.done();
      const res = await this.office.authed({
        method: 'POST',
        path: `/v1/check-ins/${encodeURIComponent(cid)}/reviews`,
        body: { root: 'ReviewRequest', fields },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: reviewOf(res.data?.Review as ReviewXml) };
    });
  }

  private async checkInOf(c: CheckInXml, names: Names): Promise<CheckInView> {
    const view: CheckInView = {
      check_in_id: c.check_in_id,
      user_id: c.user_id,
      store_id: Number(c.store_id),
      store_name: await names.store(Number(c.store_id)),
      reason: c.reason,
      auto_verified: truthy(c.auto_verified),
      verified: truthy(c.verified),
      occurred_at: c.occurred_at,
      recorded_at: c.recorded_at,
    };
    for (const k of ['latitude', 'longitude', 'accuracy_m', 'distance_m'] as const)
      if (c[k] !== undefined) view[k] = c[k];
    return view;
  }
}
