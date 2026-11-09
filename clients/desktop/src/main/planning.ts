import type { PlanningQuery, PlanningRowView, PlanningView, Result } from '../shared/api';
import { SEGMENT_STATES } from '../shared/report-table';
import type { Backoffice } from './backoffice';
import {
  checked,
  failed,
  invalidInput,
  MAX_INT,
  optionalId,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import { Names } from './names';

// Inventory planning (F28-12, #881): demand by segment and category beside stock on hand, read
// from analytics-kpis' demand-coverage report (XML, XSD-validated). The service computes cover
// and needs and states how; this class only names the stores and categories. The report holds
// segment counts and no customer, so nothing here can show one.

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DAY = 86_400_000;
const MAX_WINDOW_DAYS = 731;
const MAX_TARGET_DAYS = 180;

interface SegmentXml {
  segment_code?: string;
  segment_state: string;
  net_units: string;
  share: string;
}

interface RowXml {
  store_id: string;
  category_id: string;
  net_units: string;
  daily_demand: string;
  on_hand: string;
  days_of_cover?: string;
  replenishment_units: string;
  synthetic: string;
  segments?: SegmentXml[];
}

interface ReportXml {
  from: string;
  to: string;
  window_days: string;
  target_cover_days: string;
  generated_at: string;
  calculation: string;
  store_scope: { restricted: string };
  data_origin: { synthetic: string };
  rows?: RowXml[];
}

const isTrue = (v: string): boolean => v === 'true' || v === '1';

function checkedQuery(raw: unknown, today: string): PlanningQuery {
  const q = record(raw, 'The planning query');
  const date = (v: unknown, label: string): string => {
    if (typeof v !== 'string' || !DATE.test(v) || Number.isNaN(Date.parse(v)))
      throw invalidInput(`${label} must be a date (YYYY-MM-DD).`);
    return v;
  };
  const from = date(q.from, 'From');
  const to = date(q.to, 'To');
  if (Date.parse(to) < Date.parse(from)) throw invalidInput('To cannot be before From.');
  if ((Date.parse(to) - Date.parse(from)) / DAY >= MAX_WINDOW_DAYS)
    throw invalidInput(`A window covers at most ${MAX_WINDOW_DAYS} days.`);
  // A day still running would lower the daily demand; the service refuses it too.
  if (to >= today) throw invalidInput('To must be a day that has ended (before today, UTC).');
  const query: PlanningQuery = { from, to, page: pageNumber(q.page), size: pageSize(q.size) };
  const store = optionalId(q.store_id, MAX_INT, 'The store');
  if (store !== undefined) query.store_id = store;
  const category = optionalId(q.category_id, MAX_INT, 'The category');
  if (category !== undefined) query.category_id = category;
  const target = optionalId(q.target_cover_days, MAX_TARGET_DAYS, 'The target cover');
  if (target !== undefined) query.target_cover_days = target;
  return query;
}

function paramsOf(q: PlanningQuery): URLSearchParams {
  const p = new URLSearchParams({ from: q.from, to: q.to });
  if (q.store_id !== undefined) p.set('store_id', String(q.store_id));
  if (q.category_id !== undefined) p.set('category_id', String(q.category_id));
  if (q.target_cover_days !== undefined) p.set('target_cover_days', String(q.target_cover_days));
  p.set('page', String(q.page ?? 1));
  p.set('size', String(q.size ?? 20));
  return p;
}

export class Planning {
  constructor(
    private readonly office: Backoffice,
    private readonly now: () => Date = () => new Date(),
  ) {}

  report(raw: unknown): Promise<Result<PlanningView>> {
    return checked(this.office, async () => {
      const query = checkedQuery(raw, this.now().toISOString().slice(0, 10));
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/kpi-reports/demand-coverage?${paramsOf(query).toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.DemandCoverageReport as ReportXml & Record<string, unknown>;
      const names = new Names(this.office);
      const rows: PlanningRowView[] = [];
      for (const r of data.rows ?? []) {
        const storeId = Number(r.store_id);
        const categoryId = Number(r.category_id);
        rows.push({
          store_id: storeId,
          store: await names.store(storeId),
          category_id: categoryId,
          category: await names.category(categoryId),
          net_units: Number(r.net_units),
          daily_demand: r.daily_demand,
          on_hand: Number(r.on_hand),
          days_of_cover: r.days_of_cover ?? null,
          replenishment_units: Number(r.replenishment_units),
          synthetic: isTrue(r.synthetic),
          segments: (r.segments ?? []).map((s) => ({
            segment: s.segment_code ?? SEGMENT_STATES[s.segment_state] ?? s.segment_state,
            net_units: Number(s.net_units),
            share: s.share,
          })),
        });
      }
      return {
        ok: true,
        value: {
          from: data.from,
          to: data.to,
          window_days: Number(data.window_days),
          target_cover_days: Number(data.target_cover_days),
          generated_at: data.generated_at,
          calculation: data.calculation,
          store_restricted: isTrue(data.store_scope.restricted),
          synthetic: isTrue(data.data_origin.synthetic) || rows.some((r) => r.synthetic),
          rows,
          page: pageOf(data),
        },
      };
    });
  }
}
