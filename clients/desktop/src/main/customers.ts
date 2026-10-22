import type {
  AssignmentRowView,
  ChoiceView,
  CurrentAssignmentView,
  CustomerView,
  Paged,
  PreferencesView,
  RecommendationView,
  Result,
  SegmentVocabularyView,
} from '../shared/api';
import type { Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  id,
  invalidInput,
  optionalBoolean,
  optionalText,
  pageNumber,
  pageOf,
  pageSize,
  record,
  uuid,
} from './checks';
import { Names } from './names';
import type { CallResult } from './transport';

// Customers, their preferences, their segment and their recommendations (F16-03, #414).
// Four services each answer for their own facts; the names come from sales-service,
// categories-service and products-service, composed here (ADR-0035). Every value from
// the screens is checked before a path or a body is built from it.

const MAX_SHORT = 32767;
const SEARCH_MAX = 160;
const NAME_MAX = 120;
const EMAIL_MAX = 160;
const PHONE_MAX = 20;
/** The XSD's Email pattern, so a refusal names its field the way the service would. */
const EMAIL = /^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/;
const LABEL_MAX = 64;

interface CustomerXml {
  customer_id: string;
  name: string;
  email?: string;
  phone?: string;
  registration_channel_id: string;
  registered_on: string;
}

interface ProfileXml {
  customer_id: string;
  preferred_channel_ids?: string[];
  interest_category_ids?: string[];
}

interface ScoresXml {
  label_code?: string;
  r_score?: string;
  f_score?: string;
  m_score?: string;
}

interface CurrentXml extends ScoresXml {
  customer_id: string;
  state: string;
  since?: string;
  run_id?: string;
}

interface RowXml extends ScoresXml {
  customer_id: string;
  run_id: string;
  valid_from: string;
  valid_to?: string;
}

interface SegmentXml {
  label_code: string;
  name: string;
  description?: string;
  ordinal_position: string;
  size: string;
}

interface ReasonXml {
  signal: string;
  segment_buyers?: string;
  category_id?: string;
  share?: string;
}

interface RecommendationXml {
  customer_id: string;
  status: string;
  computed_at: string;
  window: { days: string; start: string; end: string };
  label_code?: string;
  run_id?: string;
  usual_store_id?: string;
  items?: { product_id: string; category_id: string; quantity_on_hand: string; reasons: ReasonXml[] }[];
}

/** Present only when the service sent it: a state is shown as returned, never completed. */
function number(value: string | undefined): number | undefined {
  return value === undefined ? undefined : Number(value);
}

function present<T extends object>(view: T): T {
  return Object.fromEntries(Object.entries(view).filter(([, v]) => v !== undefined)) as T;
}

/** Whole numbers of 1 to 32767, as a set of ids; a list of anything else is refused. */
function idList(value: unknown, label: string): number[] {
  if (!Array.isArray(value)) throw invalidInput(`${label} must be a list.`);
  return value.map((v) => id(v, MAX_SHORT, label));
}

export class Customers {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  private async customerOf(x: CustomerXml, names: Names): Promise<CustomerView> {
    return present({
      customer_id: x.customer_id,
      name: x.name,
      email: x.email,
      phone: x.phone,
      registration_channel_id: Number(x.registration_channel_id),
      registration_channel_name: await names.channel(Number(x.registration_channel_id)),
      registered_on: x.registered_on,
    });
  }

  search(query: unknown): Promise<Result<Paged<CustomerView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const text = optionalText(q.q, 'The search text');
      if (text?.trim()) {
        if (text.trim().length > SEARCH_MAX) throw invalidInput('The search text is too long.');
        params.set('q', text.trim());
      }
      const email = optionalText(q.email, 'The email');
      if (email?.trim()) {
        if (email.trim().length > EMAIL_MAX) throw invalidInput('The email is too long.');
        params.set('email', email.trim());
      }
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/customers?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.Customers as { Customer?: CustomerXml[] } & Record<string, unknown>;
      const names = new Names(this.office);
      const items: CustomerView[] = [];
      for (const c of data.Customer ?? []) items.push(await this.customerOf(c, names));
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  get(customerId: unknown): Promise<Result<CustomerView>> {
    return this.run(async () => {
      const cid = uuid(customerId, 'The customer');
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/customers/${encodeURIComponent(cid)}`,
      });
      return this.customerResult(res);
    });
  }

  /** PATCH sends only the fields that are given; an absent field is left as it is. */
  correct(customerId: unknown, changes: unknown): Promise<Result<CustomerView>> {
    return this.run(async () => {
      const cid = uuid(customerId, 'The customer');
      const c = record(changes, 'The changes');
      const f = new FieldChecks();
      const fields: Record<string, string> = {};
      if (c.name !== undefined) fields.name = f.text('name', c.name, NAME_MAX);
      if (c.email !== undefined) {
        const email = f.text('email', c.email, EMAIL_MAX);
        if (email !== '' && !EMAIL.test(email)) f.details.push({ field: 'email', code: 'invalid_format' });
        fields.email = email;
      }
      if (c.phone !== undefined) fields.phone = f.text('phone', c.phone, PHONE_MAX);
      if (c.registration_channel_id !== undefined)
        fields.registration_channel_id = String(
          f.id('registration_channel_id', c.registration_channel_id, MAX_SHORT),
        );
      f.done();
      if (Object.keys(fields).length === 0) throw invalidInput('Change at least one field.');
      const res = await this.office.authed({
        method: 'PATCH',
        path: `/v1/customers/${encodeURIComponent(cid)}`,
        body: { root: 'CorrectCustomerRequest', fields },
      });
      return this.customerResult(res);
    });
  }

  private async customerResult(res: CallResult): Promise<Result<CustomerView>> {
    if (!res.ok) return failed(res);
    return {
      ok: true,
      value: await this.customerOf(res.data?.Customer as CustomerXml, new Names(this.office)),
    };
  }

  channels(): Promise<Result<ChoiceView[]>> {
    return this.run(async () => {
      const res = await this.office.authed({ method: 'GET', path: '/v1/channels' });
      if (!res.ok) return failed(res);
      const data = res.data?.Channels as { Channel?: { channel_id: string; name: string }[] };
      return {
        ok: true,
        value: (data.Channel ?? []).map((c) => ({ id: Number(c.channel_id), name: c.name })),
      };
    });
  }

  preferences(customerId: unknown): Promise<Result<PreferencesView>> {
    return this.run(async () => {
      const cid = uuid(customerId, 'The customer');
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/preference-profiles/${encodeURIComponent(cid)}`,
      });
      return this.profileResult(res);
    });
  }

  replaceChannels(customerId: unknown, channelIds: unknown): Promise<Result<PreferencesView>> {
    return this.run(async () => {
      const cid = uuid(customerId, 'The customer');
      const ids = idList(channelIds, 'A channel');
      const res = await this.office.authed({
        method: 'PUT',
        path: `/v1/preference-profiles/${encodeURIComponent(cid)}/preferred-channels`,
        body: { root: 'ReplacePreferredChannelsRequest', fields: { channel_ids: ids.map(String) } },
      });
      return this.profileResult(res);
    });
  }

  replaceCategories(customerId: unknown, categoryIds: unknown): Promise<Result<PreferencesView>> {
    return this.run(async () => {
      const cid = uuid(customerId, 'The customer');
      const ids = idList(categoryIds, 'A category');
      const res = await this.office.authed({
        method: 'PUT',
        path: `/v1/preference-profiles/${encodeURIComponent(cid)}/interest-categories`,
        body: { root: 'ReplaceInterestCategoriesRequest', fields: { category_ids: ids.map(String) } },
      });
      return this.profileResult(res);
    });
  }

  private async profileResult(res: CallResult): Promise<Result<PreferencesView>> {
    if (!res.ok) return failed(res);
    const p = res.data?.PreferenceProfile as ProfileXml;
    const names = new Names(this.office);
    const channelNames = await names.channels();
    const categoryNames = await names.categories();
    const choose = async (
      ids: string[] | undefined,
      name: (n: number) => Promise<string>,
    ): Promise<ChoiceView[]> => {
      const out: ChoiceView[] = [];
      for (const raw of ids ?? []) out.push({ id: Number(raw), name: await name(Number(raw)) });
      return out;
    };
    const options = (m: Map<number, string> | null): ChoiceView[] =>
      m === null ? [] : [...m].map(([cid, name]) => ({ id: cid, name }));
    return {
      ok: true,
      value: {
        customer_id: p.customer_id,
        channels: await choose(p.preferred_channel_ids, (n) => names.channel(n)),
        categories: await choose(p.interest_category_ids, (n) => names.category(n)),
        channel_options: options(channelNames),
        category_options: options(categoryNames),
      },
    };
  }

  vocabulary(): Promise<Result<SegmentVocabularyView>> {
    return this.run(async () => {
      const res = await this.office.authed({ method: 'GET', path: '/v1/segments' });
      if (!res.ok) return failed(res);
      const d = res.data?.Segments as {
        run_id?: string;
        run_at?: string;
        unassigned_count: string;
        Segment?: SegmentXml[];
      };
      const labels = (d.Segment ?? [])
        .map((s) =>
          present({
            label_code: s.label_code,
            name: s.name,
            description: s.description,
            ordinal_position: Number(s.ordinal_position),
            size: Number(s.size),
          }),
        )
        .sort((a, b) => a.ordinal_position - b.ordinal_position);
      return {
        ok: true,
        value: present({
          run_id: number(d.run_id),
          run_at: d.run_at,
          unassigned_count: Number(d.unassigned_count),
          labels,
        }),
      };
    });
  }

  /** The label's name comes from the vocabulary; a label the vocabulary lacks has no name. */
  private async labelNames(): Promise<Map<string, string>> {
    const res = await this.office.authed({ method: 'GET', path: '/v1/segments' });
    if (!res.ok) return new Map();
    const d = res.data?.Segments as { Segment?: SegmentXml[] };
    return new Map((d.Segment ?? []).map((s) => [s.label_code, s.name]));
  }

  current(customerId: unknown): Promise<Result<CurrentAssignmentView>> {
    return this.run(async () => {
      const cid = uuid(customerId, 'The customer');
      const params = new URLSearchParams({ customer_id: cid });
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/segment-assignments/current?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const x = res.data?.CurrentAssignment as CurrentXml;
      const labelName = x.label_code === undefined ? undefined : (await this.labelNames()).get(x.label_code);
      return {
        ok: true,
        value: present({
          customer_id: x.customer_id,
          state: x.state,
          label_code: x.label_code,
          label_name: labelName,
          r_score: number(x.r_score),
          f_score: number(x.f_score),
          m_score: number(x.m_score),
          since: x.since,
          run_id: number(x.run_id),
        }),
      };
    });
  }

  assignments(query: unknown): Promise<Result<Paged<AssignmentRowView>>> {
    return this.run(async () => {
      const q = record(query, 'The query');
      const params = new URLSearchParams();
      const hasCustomer = q.customer_id !== undefined;
      const hasLabel = q.label_code !== undefined;
      if (hasCustomer === hasLabel) throw invalidInput('Give a customer or a label, and not both.');
      if (hasCustomer) params.set('customer_id', uuid(q.customer_id, 'The customer'));
      else {
        const label = optionalText(q.label_code, 'The label');
        if (label === undefined || label.trim() === '' || label.length > LABEL_MAX)
          throw invalidInput('The label must be 1 to 64 characters.');
        params.set('label_code', label);
      }
      const open = optionalBoolean(q.open, 'Open rows only');
      if (open === true) params.set('open', 'true');
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/segment-assignments?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.SegmentAssignments as { SegmentAssignment?: RowXml[] } & Record<string, unknown>;
      const rows = data.SegmentAssignment ?? [];
      const labels = rows.some((r) => r.label_code !== undefined) ? await this.labelNames() : new Map();
      const items = rows.map((r) =>
        present({
          customer_id: r.customer_id,
          run_id: Number(r.run_id),
          label_code: r.label_code,
          label_name: r.label_code === undefined ? undefined : labels.get(r.label_code),
          r_score: number(r.r_score),
          f_score: number(r.f_score),
          m_score: number(r.m_score),
          valid_from: r.valid_from,
          valid_to: r.valid_to,
        }),
      );
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  /** One call for the list's product names, however many items it has. */
  recommendations(customerId: unknown): Promise<Result<RecommendationView>> {
    return this.run(async () => {
      const cid = uuid(customerId, 'The customer');
      const params = new URLSearchParams({ customer_id: cid });
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/recommendations?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const x = res.data?.Recommendation as RecommendationXml;
      const items = x.items ?? [];
      const names = new Names(this.office);
      if (items.length > 0) await names.products(items.map((i) => Number(i.product_id)));
      const categoryIds = new Set<number>();
      for (const i of items) {
        categoryIds.add(Number(i.category_id));
        for (const r of i.reasons) if (r.category_id !== undefined) categoryIds.add(Number(r.category_id));
      }
      const categoryNames = categoryIds.size > 0 ? await names.categories() : null;
      const categoryName = (n: number): string => categoryNames?.get(n) ?? `Category ${n} (unavailable)`;
      return {
        ok: true,
        value: present({
          customer_id: x.customer_id,
          status: x.status,
          computed_at: x.computed_at,
          window: { days: Number(x.window.days), start: x.window.start, end: x.window.end },
          label_code: x.label_code,
          run_id: number(x.run_id),
          usual_store_id: number(x.usual_store_id),
          items: items.map((i) => ({
            product_id: Number(i.product_id),
            product_name: names.product(Number(i.product_id)),
            category_id: Number(i.category_id),
            category_name: categoryName(Number(i.category_id)),
            quantity_on_hand: Number(i.quantity_on_hand),
            reasons: i.reasons.map((r) =>
              present({
                signal: r.signal,
                segment_buyers: number(r.segment_buyers),
                category_id: number(r.category_id),
                category_name: r.category_id === undefined ? undefined : categoryName(Number(r.category_id)),
                share: r.share,
              }),
            ),
          })),
        }),
      };
    });
  }
}
