import { join } from 'node:path';
import type {
  ExportOutcome,
  KpiDefinitionView,
  ReportFormat,
  ReportMeasureView,
  ReportQuery,
  ReportRowView,
  ReportView,
  Result,
} from '../shared/api';
import { KPI_OPERATIONS, mayExport } from '../shared/modules';
import { reportFileStem } from '../shared/report-table';
import { localError, type Backoffice } from './backoffice';
import { checked, failed, invalidInput, optionalId, optionalText, pageOf, record } from './checks';
import { Names } from './names';
import { reportCsv, reportPdf, reportWorkbook } from './report-files';

// Executive reports (F28-14, #883): analytics-kpis read through the transport (XML, XSD-validated),
// shown on screen and written to PDF, CSV or Excel. An export is written from the report this
// class last served to the signed-in user, never from figures the renderer sends back, so the
// file and the screen cannot differ. Only the dimensions the service supports are offered, and
// the service refuses the rest.

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DIMENSIONS = ['segment', 'store', 'channel', 'period'];
const GRANULARITIES = ['day', 'week', 'month'];
const MAX_WINDOW_DAYS = 731;
const PAGE_SIZE = 100;
/** 5,000 rows: far more than a meeting report holds; a larger one is cut and says so. */
const PAGE_LIMIT = 50;
const MAX_ID = 2147483647;
const DAY = 86_400_000;

export interface ReportExportEnv {
  /** Opens the save dialog; undefined when the user cancels. */
  chooseFile(suggestedName: string, format: ReportFormat): Promise<string | undefined>;
  write(path: string, data: Buffer | string): void;
  appendLine(path: string, line: string): void;
  now(): Date;
}

interface MeasureXml {
  code: string;
  kind: string;
  unit: string;
  value?: string;
  lower?: string;
  upper?: string;
}

interface RowXml {
  period_start?: string;
  period_end?: string;
  segment_code?: string;
  segment_state?: string;
  from_segment_code?: string;
  store_id?: string;
  channel_id?: string;
  campaign_id?: string;
  model_version_id?: string;
  synthetic: string;
  measures: MeasureXml[];
}

interface ReportXml {
  kpi: string;
  definition_version: string;
  from: string;
  to: string;
  granularity?: string;
  group_by?: string[];
  generated_at: string;
  store_scope: { restricted: string };
  data_origin: { synthetic: string; spans_model_versions: string };
  rows?: RowXml[];
}

interface DefinitionXml {
  kpi_code: string;
  name: string;
  version: string;
  definition: string;
  window: string;
  dimensions?: string[];
  measures: { code: string; kind: string; unit: string; definition: string }[];
}

const isTrue = (v: string): boolean => v === 'true' || v === '1';

function definitionOf(x: DefinitionXml): KpiDefinitionView {
  return {
    kpi_code: x.kpi_code,
    name: x.name,
    version: Number(x.version),
    definition: x.definition,
    window: x.window,
    dimensions: x.dimensions ?? [],
    measures: x.measures.map((m) => ({ code: m.code, kind: m.kind, unit: m.unit, definition: m.definition })),
  };
}

function measureOf(m: MeasureXml): ReportMeasureView {
  return {
    code: m.code,
    kind: m.kind,
    unit: m.unit,
    value: m.value ?? null,
    ...(m.lower === undefined ? {} : { lower: m.lower }),
    ...(m.upper === undefined ? {} : { upper: m.upper }),
  };
}

function checkedQuery(raw: unknown): ReportQuery {
  const q = record(raw, 'The report');
  if (typeof q.kpi !== 'string' || KPI_OPERATIONS[q.kpi] === undefined)
    throw invalidInput('Choose one of the published reports.');
  const date = (v: unknown, label: string): string => {
    if (typeof v !== 'string' || !DATE.test(v) || Number.isNaN(Date.parse(v)))
      throw invalidInput(`${label} must be a date (YYYY-MM-DD).`);
    return v;
  };
  const from = date(q.from, 'From');
  const to = date(q.to, 'To');
  if (Date.parse(to) < Date.parse(from)) throw invalidInput('To cannot be before From.');
  if ((Date.parse(to) - Date.parse(from)) / DAY >= MAX_WINDOW_DAYS)
    throw invalidInput(`A report covers at most ${MAX_WINDOW_DAYS} days.`);
  const query: ReportQuery = { kpi: q.kpi, from, to };
  if (q.group_by !== undefined) {
    if (
      !Array.isArray(q.group_by) ||
      q.group_by.some((d) => typeof d !== 'string' || !DIMENSIONS.includes(d))
    )
      throw invalidInput(`Group by any of: ${DIMENSIONS.join(', ')}.`);
    const unique = [...new Set(q.group_by as string[])];
    if (unique.length > 0) query.group_by = unique;
  }
  const granularity = optionalText(q.granularity, 'The granularity');
  if (granularity !== undefined && granularity !== '') {
    if (!GRANULARITIES.includes(granularity)) throw invalidInput('The granularity is day, week or month.');
    query.granularity = granularity;
  }
  const store = optionalId(q.store_id, MAX_ID, 'The store');
  if (store !== undefined) query.store_id = store;
  const channel = optionalId(q.channel_id, MAX_ID, 'The channel');
  if (channel !== undefined) query.channel_id = channel;
  const segment = optionalText(q.segment_code, 'The segment')?.trim();
  if (segment) {
    if (segment.length > 40) throw invalidInput('The segment is too long.');
    query.segment_code = segment;
  }
  return query;
}

function paramsOf(q: ReportQuery, page: number): URLSearchParams {
  const p = new URLSearchParams({ from: q.from, to: q.to });
  if (q.group_by) p.set('group_by', q.group_by.join(','));
  // The service refuses a granularity given without period (retention aside), so it is sent only with it.
  if (q.granularity && (q.group_by?.includes('period') || q.kpi === 'retention'))
    p.set('granularity', q.granularity);
  if (q.store_id !== undefined) p.set('store_id', String(q.store_id));
  if (q.segment_code !== undefined) p.set('segment_code', q.segment_code);
  if (q.channel_id !== undefined) p.set('channel_id', String(q.channel_id));
  p.set('page', String(page));
  p.set('size', String(PAGE_SIZE));
  return p;
}

const fail = (code: string, message: string): Result<never> => ({
  ok: false,
  error: localError(code, message),
});

export class Reports {
  private last: { userId: string; report: ReportView } | null = null;
  /** Counts report requests, so only the newest may become `last` (ADR-0077). */
  private requests = 0;

  constructor(
    private readonly office: Backoffice,
    private readonly env: ReportExportEnv,
    private readonly auditDir: string,
  ) {}

  definitions(): Promise<Result<KpiDefinitionView[]>> {
    return checked(this.office, async () => {
      const params = new URLSearchParams({ page: '1', size: String(PAGE_SIZE) });
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/kpi-reports/definitions?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.KpiDefinitions as { KpiDefinition?: DefinitionXml[] };
      return { ok: true, value: (data.KpiDefinition ?? []).map(definitionOf) };
    });
  }

  report(raw: unknown): Promise<Result<ReportView>> {
    this.requests += 1;
    const request = this.requests;
    this.last = null;
    return checked(this.office, async () => {
      const query = checkedQuery(raw);
      const who = this.office.identity();
      const state = this.office.state();
      if (who === null || state.status !== 'signed_in') return fail('signed_out', 'You are signed out.');
      if (!state.operations.includes(KPI_OPERATIONS[query.kpi] as string))
        return fail('forbidden_role', 'Your role may not read this report.');

      const catalog = await this.definitions();
      if (!catalog.ok) return catalog;
      const definition = catalog.value.find((d) => d.kpi_code === query.kpi);
      if (definition === undefined) return fail('not_found', 'This report is not published.');

      const rows: RowXml[] = [];
      let head: ReportXml | undefined;
      let total = 0;
      for (let page = 1; page <= PAGE_LIMIT; page += 1) {
        const res = await this.office.authed({
          method: 'GET',
          path: `/v1/kpi-reports/${encodeURIComponent(query.kpi)}?${paramsOf(query, page).toString()}`,
        });
        if (!res.ok) return failed(res);
        const data = res.data?.KpiReport as ReportXml & Record<string, unknown>;
        head ??= data;
        total = pageOf(data).total;
        rows.push(...(data.rows ?? []));
        if ((data.rows ?? []).length === 0 || rows.length >= total) break;
      }
      const h = head as ReportXml;
      const names = new Names(this.office);
      const views: ReportRowView[] = [];
      for (const r of rows) {
        views.push({
          ...(r.period_start === undefined ? {} : { period_start: r.period_start }),
          ...(r.period_end === undefined ? {} : { period_end: r.period_end }),
          ...(r.segment_code === undefined ? {} : { segment_code: r.segment_code }),
          ...(r.segment_state === undefined ? {} : { segment_state: r.segment_state }),
          ...(r.from_segment_code === undefined ? {} : { from_segment_code: r.from_segment_code }),
          ...(r.store_id === undefined
            ? {}
            : { store: `${await names.store(Number(r.store_id))} (#${r.store_id})` }),
          ...(r.channel_id === undefined
            ? {}
            : { channel: `${await names.channel(Number(r.channel_id))} (#${r.channel_id})` }),
          ...(r.campaign_id === undefined ? {} : { campaign_id: r.campaign_id }),
          ...(r.model_version_id === undefined ? {} : { model_version_id: r.model_version_id }),
          synthetic: isTrue(r.synthetic),
          measures: r.measures.map(measureOf),
        });
      }
      const report: ReportView = {
        kpi: h.kpi,
        name: definition.name,
        definition_version: Number(h.definition_version),
        from: h.from,
        to: h.to,
        ...(h.granularity === undefined ? {} : { granularity: h.granularity }),
        group_by: h.group_by ?? [],
        generated_at: h.generated_at,
        store_restricted: isTrue(h.store_scope.restricted),
        synthetic: isTrue(h.data_origin.synthetic) || views.some((r) => r.synthetic),
        spans_model_versions: isTrue(h.data_origin.spans_model_versions),
        definition,
        rows: views,
        rows_total: Math.max(total, views.length),
      };
      // An older request that finishes after a newer one must not replace it:
      // the screen shows the newest, and the export must match the screen.
      if (request === this.requests) this.last = { userId: who.userId, report };
      return { ok: true, value: report };
    });
  }

  async exportReport(raw: unknown): Promise<Result<ExportOutcome>> {
    const who = this.office.identity();
    if (who === null) return fail('signed_out', 'You are signed out.');
    if (raw !== 'pdf' && raw !== 'csv' && raw !== 'xlsx')
      return fail('invalid_input', 'Choose PDF, CSV or Excel.');
    const format: ReportFormat = raw;
    const state = this.office.state();
    const report = this.last?.userId === who.userId ? this.last.report : null;
    if (report === null) return fail('nothing_to_export', 'Open a report before exporting it.');
    if (
      !mayExport(who.role) ||
      state.status !== 'signed_in' ||
      !state.operations.includes(KPI_OPERATIONS[report.kpi] as string)
    )
      return fail('forbidden_role', 'Your role may not export this report.');

    const stamp = this.env.now().toISOString();
    const file = await this.env.chooseFile(`${reportFileStem(report)}.${format}`, format);
    if (file === undefined) return { ok: true, value: null };

    const entry = {
      at: stamp,
      user_id: who.userId,
      role: who.role,
      module: 'reports',
      kpi: report.kpi,
      format,
      synthetic: report.synthetic,
    };
    const log = join(this.auditDir, 'export-audit.log');
    try {
      // Audit first: an export that cannot be recorded is not written.
      this.env.appendLine(log, `${JSON.stringify({ ...entry, rows: report.rows.length, file })}\n`);
    } catch {
      return fail('audit_unavailable', 'The export was not written because it could not be recorded.');
    }
    try {
      this.env.write(
        file,
        format === 'pdf' ? reportPdf(report) : format === 'xlsx' ? reportWorkbook(report) : reportCsv(report),
      );
    } catch {
      try {
        this.env.appendLine(log, `${JSON.stringify({ ...entry, outcome: 'write_failed', file })}\n`);
      } catch {
        // The first line already says an export was attempted.
      }
      return fail('write_failed', 'The file could not be written.');
    }
    return { ok: true, value: { rows: report.rows.length, file } };
  }
}
