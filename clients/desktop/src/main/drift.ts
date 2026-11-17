import type {
  DistributionVariable,
  DriftAlertQuery,
  DriftAlertView,
  DriftBinView,
  DriftComparison,
  DriftKind,
  DriftReportQuery,
  DriftReportView,
  DriftStatisticView,
  DriftVariable,
  OutcomeSegmentView,
  OutcomeTestView,
  Paged,
  Result,
  VariableDriftView,
} from '../shared/api';
import { mentionsConceptDrift } from '../shared/drift-wording';
import { DRIFT_OPERATIONS } from '../shared/modules';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  id,
  invalidInput,
  optionalId,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import { ContractDefect } from './transport';

// Drift reports and alerts (F28-07, #876): model-registry's reads, through the transport (XML,
// XSD-validated). The service decides every state, level, comparison and claim; this class reads
// them, checks the values the screen sends, and refuses an answer that says more than its own
// evidence supports: the XSD cannot say which element goes with which kind, and the wording rule
// of ADR-0065 depends on it. Such an answer is a contract defect and no part of it is used. The
// four operations are published ahead of their implementation and marked planned (ADR-0073); a
// service that does not answer yet is shown as it answers.

const KINDS: readonly string[] = ['DATA_DRIFT', 'OUTCOME_TEST'];
const COMPARISONS: readonly string[] = ['COMPARED', 'INSUFFICIENT_DATA', 'NOT_COMPARED'];
const VARIABLES: readonly string[] = ['SEGMENT_SHARES', 'R', 'F', 'M', 'REPURCHASE_RATE'];
const SERVICE = 'model-registry';
/** The contract's identifiers are int64. */
const MAX_ID = Number.MAX_SAFE_INTEGER;

interface StatisticXml {
  statistic: string;
  value: string;
  p_value?: string;
  degrees_of_freedom?: string;
  threshold_basis: string;
  watch_threshold: string;
  alarm_threshold: string;
  level: string;
}

interface BinXml {
  label: string;
  lower?: string;
  upper?: string;
  reference_share: string;
  current_share: string;
  contribution: string;
}

interface RatesXml {
  customers: string;
  repurchasers: string;
  rate?: string;
}

interface SegmentXml {
  label_code: string;
  reference: RatesXml;
  current: RatesXml;
  difference_points?: string;
  z?: string;
  p_value?: string;
  adjusted_p_value?: string;
  result: string;
  persisted: string;
}

interface OutcomeXml {
  relation: string;
  horizon_days: string;
  cohort_as_of: string;
  thresholds: { alpha: string; min_difference_points: string; min_customers_per_side: string };
  segments: SegmentXml[];
  claim?: string;
}

interface VersionXml {
  version_id: string;
  name: string;
  method: string;
}

interface WindowXml {
  start: string;
  end: string;
  days: string;
  customer_count: string;
}

interface ReferenceXml {
  sample_size: string;
  captured_at: string;
}

interface VariableXml {
  variable: string;
  state: string;
  statistics: StatisticXml[];
  bins: BinXml[];
}

interface ReportXml {
  report_id: string;
  kind: string;
  model_version: VersionXml;
  comparison: string;
  not_compared_reason?: string;
  window: WindowXml;
  reference: ReferenceXml;
  previous_report_id?: string;
  variables?: VariableXml[];
  outcome_test?: OutcomeXml;
  alert_ids?: string[];
  computed_at: string;
}

interface AlertXml {
  alert_id: string;
  report_id: string;
  previous_report_id: string;
  kind: string;
  claim: string;
  model_version: VersionXml;
  variable: string;
  window: WindowXml;
  reference: ReferenceXml;
  statistics?: StatisticXml[];
  bins?: BinXml[];
  outcome_test?: OutcomeXml;
  suggested_review: { code: string; text: string };
  raised_at: string;
}

const isTrue = (v: string): boolean => v === 'true' || v === '1';

/** Present only when the service sent it: a field it left out stays absent. */
function present<T extends object>(view: T): T {
  return Object.fromEntries(Object.entries(view).filter(([, v]) => v !== undefined)) as T;
}

const optionalNumber = (v: string | undefined): number | undefined =>
  v === undefined ? undefined : Number(v);

function statisticOf(x: StatisticXml): DriftStatisticView {
  return present({
    statistic: x.statistic as DriftStatisticView['statistic'],
    value: x.value,
    p_value: x.p_value,
    degrees_of_freedom: optionalNumber(x.degrees_of_freedom),
    threshold_basis: x.threshold_basis as DriftStatisticView['threshold_basis'],
    watch_threshold: x.watch_threshold,
    alarm_threshold: x.alarm_threshold,
    level: x.level as DriftStatisticView['level'],
  });
}

function binOf(x: BinXml): DriftBinView {
  return present({
    label: x.label,
    lower: x.lower,
    upper: x.upper,
    reference_share: x.reference_share,
    current_share: x.current_share,
    contribution: x.contribution,
  });
}

function ratesOf(x: RatesXml): OutcomeSegmentView['reference'] {
  return present({ customers: Number(x.customers), repurchasers: Number(x.repurchasers), rate: x.rate });
}

function segmentOf(x: SegmentXml): OutcomeSegmentView {
  return present({
    label_code: x.label_code,
    reference: ratesOf(x.reference),
    current: ratesOf(x.current),
    difference_points: x.difference_points,
    z: x.z,
    p_value: x.p_value,
    adjusted_p_value: x.adjusted_p_value,
    result: x.result as OutcomeSegmentView['result'],
    persisted: isTrue(x.persisted),
  });
}

function outcomeOf(x: OutcomeXml): OutcomeTestView {
  return present({
    relation: x.relation as OutcomeTestView['relation'],
    horizon_days: Number(x.horizon_days),
    cohort_as_of: x.cohort_as_of,
    thresholds: {
      alpha: x.thresholds.alpha,
      min_difference_points: x.thresholds.min_difference_points,
      min_customers_per_side: Number(x.thresholds.min_customers_per_side),
    },
    segments: x.segments.map(segmentOf),
    claim: x.claim as OutcomeTestView['claim'],
  });
}

function variableOf(x: VariableXml): VariableDriftView {
  return {
    variable: x.variable as DistributionVariable,
    state: x.state as VariableDriftView['state'],
    statistics: x.statistics.map(statisticOf),
    bins: x.bins.map(binOf),
  };
}

function reportOf(x: ReportXml): DriftReportView {
  return present({
    report_id: Number(x.report_id),
    kind: x.kind as DriftKind,
    model_version: {
      version_id: Number(x.model_version.version_id),
      name: x.model_version.name,
      method: x.model_version.method,
    },
    comparison: x.comparison as DriftComparison,
    not_compared_reason: x.not_compared_reason as DriftReportView['not_compared_reason'],
    window: {
      start: x.window.start,
      end: x.window.end,
      days: Number(x.window.days),
      customer_count: Number(x.window.customer_count),
    },
    reference: { sample_size: Number(x.reference.sample_size), captured_at: x.reference.captured_at },
    previous_report_id: optionalNumber(x.previous_report_id),
    variables: (x.variables ?? []).map(variableOf),
    outcome_test: x.outcome_test === undefined ? undefined : outcomeOf(x.outcome_test),
    alert_ids: (x.alert_ids ?? []).map(Number),
    computed_at: x.computed_at,
  });
}

function alertOf(x: AlertXml): DriftAlertView {
  return present({
    alert_id: Number(x.alert_id),
    report_id: Number(x.report_id),
    previous_report_id: Number(x.previous_report_id),
    kind: x.kind as DriftKind,
    claim: x.claim as DriftAlertView['claim'],
    model_version: {
      version_id: Number(x.model_version.version_id),
      name: x.model_version.name,
      method: x.model_version.method,
    },
    variable: x.variable as DriftVariable,
    window: {
      start: x.window.start,
      end: x.window.end,
      days: Number(x.window.days),
      customer_count: Number(x.window.customer_count),
    },
    reference: { sample_size: Number(x.reference.sample_size), captured_at: x.reference.captured_at },
    statistics: (x.statistics ?? []).map(statisticOf),
    bins: (x.bins ?? []).map(binOf),
    outcome_test: x.outcome_test === undefined ? undefined : outcomeOf(x.outcome_test),
    suggested_review: {
      code: x.suggested_review.code as DriftAlertView['suggested_review']['code'],
      text: x.suggested_review.text,
    },
    raised_at: x.raised_at,
  });
}

/**
 * Whether an answer is something the service may say. An INSUFFICIENT_DATA or NOT_COMPARED report
 * carries no number; a report holds only the evidence of its own kind; the phrase of a change in
 * the relation to an outcome comes only with an outcome-test result that persisted (ADR-0064,
 * ADR-0065). Returns what is wrong, or null.
 */
export function reportFault(r: DriftReportView): string | null {
  if (r.comparison !== 'COMPARED' && (r.variables.length > 0 || r.outcome_test !== undefined))
    return 'a report without a comparison carries a statistic';
  if ((r.comparison === 'NOT_COMPARED') !== (r.not_compared_reason !== undefined))
    return 'the reason for not comparing does not match the comparison';
  if (r.kind === 'DATA_DRIFT' && r.outcome_test !== undefined)
    return 'a distribution report carries an outcome test';
  if (r.kind === 'OUTCOME_TEST' && r.variables.length > 0)
    return 'an outcome test carries distribution variables';
  if (r.comparison === 'COMPARED' && r.kind === 'DATA_DRIFT' && r.variables.length === 0)
    return 'a compared distribution report carries no variable';
  if (r.comparison === 'COMPARED' && r.kind === 'OUTCOME_TEST' && r.outcome_test === undefined)
    return 'a compared outcome test carries no result';
  if (r.outcome_test?.claim !== undefined && !r.outcome_test.segments.some((s) => s.persisted))
    return 'an outcome test makes a claim that no segment supports';
  return null;
}

export function alertFault(a: DriftAlertView): string | null {
  const outcome = a.kind === 'OUTCOME_TEST';
  if ((a.claim === 'CONCEPT_DRIFT') !== outcome) return 'the claim does not match the kind of the alert';
  if (outcome) {
    if (a.variable !== 'REPURCHASE_RATE' || a.statistics.length > 0 || a.bins.length > 0)
      return 'an outcome-test alert carries distribution evidence';
    const o = a.outcome_test;
    if (o === undefined || o.claim !== 'CONCEPT_DRIFT' || !o.segments.some((s) => s.persisted))
      return 'an outcome-test alert without a persisted result';
  } else {
    if (a.variable === 'REPURCHASE_RATE' || a.outcome_test !== undefined || a.statistics.length === 0)
      return 'a distribution alert does not hold distribution evidence';
    if (mentionsConceptDrift(a.suggested_review.text))
      return 'a distribution alert suggests a review in the words of an outcome result';
  }
  return null;
}

export class Drift {
  constructor(private readonly office: Backoffice) {}

  /** A role reads a call only when the contract's x-mosaiq-roles allow its operation. */
  private allowed(operation: string): Result<never> | null {
    const state = this.office.state();
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return state.operations.includes(operation)
      ? null
      : { ok: false, error: localError('forbidden_role', 'Your role may not read drift reports.') };
  }

  private defect(correlationId: string, reason: string): ContractDefect {
    return new ContractDefect(SERVICE, correlationId, reason);
  }

  listReports(query: unknown): Promise<Result<Paged<DriftReportView>>> {
    return checked(this.office, async () => {
      const refused = this.allowed(DRIFT_OPERATIONS.reports);
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The query');
      const params = new URLSearchParams();
      const kind = oneOf<DriftReportQuery['kind']>(q.kind, KINDS, 'The kind');
      if (kind !== undefined) params.set('kind', kind);
      const version = optionalId(q.model_version_id, MAX_ID, 'The model version');
      if (version !== undefined) params.set('model_version_id', String(version));
      const comparison = oneOf<DriftReportQuery['comparison']>(q.comparison, COMPARISONS, 'The comparison');
      if (comparison !== undefined) params.set('comparison', comparison);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/drift-reports?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.DriftReports as { DriftReport?: ReportXml[] } & Record<string, unknown>;
      const items = (data.DriftReport ?? []).map(reportOf);
      for (const r of items) {
        const fault = reportFault(r);
        if (fault !== null) throw this.defect(res.correlationId, fault);
      }
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  getReport(reportId: unknown): Promise<Result<DriftReportView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(DRIFT_OPERATIONS.report);
      if (refused !== null) return refused;
      const n = id(reportId, MAX_ID, 'The report');
      const res = await this.office.authed({ method: 'GET', path: `/v1/drift-reports/${n}` });
      if (!res.ok) return failed(res);
      const report = reportOf(res.data?.DriftReport as ReportXml);
      const fault = reportFault(report);
      if (fault !== null) throw this.defect(res.correlationId, fault);
      return { ok: true, value: report };
    });
  }

  listAlerts(query: unknown): Promise<Result<Paged<DriftAlertView>>> {
    return checked(this.office, async () => {
      const refused = this.allowed(DRIFT_OPERATIONS.alerts);
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The query');
      const params = new URLSearchParams();
      const kind = oneOf<DriftAlertQuery['kind']>(q.kind, KINDS, 'The kind');
      if (kind !== undefined) params.set('kind', kind);
      const version = optionalId(q.model_version_id, MAX_ID, 'The model version');
      if (version !== undefined) params.set('model_version_id', String(version));
      const variable = oneOf<DriftAlertQuery['variable']>(q.variable, VARIABLES, 'The variable');
      if (variable !== undefined) params.set('variable', variable);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/drift-alerts?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.DriftAlerts as { DriftAlert?: AlertXml[] } & Record<string, unknown>;
      const items = (data.DriftAlert ?? []).map(alertOf);
      for (const a of items) {
        const fault = alertFault(a);
        if (fault !== null) throw this.defect(res.correlationId, fault);
      }
      return { ok: true, value: { items, page: pageOf(data) } };
    });
  }

  getAlert(alertId: unknown): Promise<Result<DriftAlertView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(DRIFT_OPERATIONS.alert);
      if (refused !== null) return refused;
      const n = id(alertId, MAX_ID, 'The alert');
      const res = await this.office.authed({ method: 'GET', path: `/v1/drift-alerts/${n}` });
      if (!res.ok) return failed(res);
      const alert = alertOf(res.data?.DriftAlert as AlertXml);
      const fault = alertFault(alert);
      if (fault !== null) throw this.defect(res.correlationId, fault);
      return { ok: true, value: alert };
    });
  }
}

/** An optional enumerated filter; an empty choice means no filter. */
function oneOf<T extends string | undefined>(value: unknown, allowed: readonly string[], label: string): T {
  if (value === undefined || value === '') return undefined as T;
  if (typeof value !== 'string' || !allowed.includes(value)) throw invalidInput(`${label} is not known.`);
  return value as T;
}
