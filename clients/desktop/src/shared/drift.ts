import type {
  DriftAlertView,
  DriftBinView,
  DriftClaim,
  DriftComparison,
  DriftKind,
  DriftReferenceView,
  DriftReportView,
  DriftStatisticView,
  DriftVariable,
  DriftWindowView,
  ModelVersionRefView,
  OutcomeResult,
  OutcomeSegmentView,
  OutcomeTestView,
  ReviewCode,
  StatisticLevel,
  VariableState,
} from './api';
import type { NamedOutcome } from './drift-wording';

// Everything the drift module (F28-07, #876) says and the numbers its charts are drawn from, as
// pure functions, so a unit test can read every sentence a screen can show. The wording follows
// F23-07 (ADR-0065, ADR-0064): a finding about a distribution is a "distribution change"; the
// phrase of a change in the relation between a segment and an outcome appears in exactly one
// function, `claimText`, and only for an outcome-test result that names its relation, horizon and
// cohort; a window that crosses a change of version is never compared and is never said to show
// what customers did. The service decides every state, level and claim; this file only words them.

/** ADR-0065, false-alarm control 1: a window of fewer customers than this gets no number. */
export const MIN_WINDOW_CUSTOMERS = 200;
/** ADR-0065: a segment is tested only when both sides have at least this many customers. */
export const MIN_SEGMENT_CUSTOMERS = 100;

export const KIND_LABEL: Readonly<Record<DriftKind, string>> = {
  DATA_DRIFT: 'Distribution comparison',
  OUTCOME_TEST: 'Outcome test',
};

export const COMPARISON_LABEL: Readonly<Record<DriftComparison, string>> = {
  COMPARED: 'Compared',
  INSUFFICIENT_DATA: 'Insufficient data',
  NOT_COMPARED: 'Not compared',
};

export const VARIABLE_LABEL: Readonly<Record<DriftVariable, string>> = {
  SEGMENT_SHARES: 'Segment shares',
  R: 'Recency (R)',
  F: 'Frequency (F)',
  M: 'Monetary (M)',
  REPURCHASE_RATE: 'Repurchase rate',
};

/** What a distribution change is about, as the subject of a sentence that follows a colon. */
const VARIABLE_SUBJECT: Readonly<Record<DriftVariable, string>> = {
  SEGMENT_SHARES: 'the share of customers in each segment',
  R: 'the recency (R) of the customers',
  F: 'the frequency (F) of the customers',
  M: 'the monetary value (M) of the customers',
  REPURCHASE_RATE: 'the repurchase rate',
};

export const STATE_LABEL: Readonly<Record<VariableState, string>> = {
  NORMAL: 'Normal',
  WATCH: 'Watch',
  ALARM_PENDING: 'Alarm pending',
  ALARM: 'Alarm',
};

const STATE_MEANING: Readonly<Record<VariableState, string>> = {
  NORMAL: 'No statistic passed its watch threshold.',
  WATCH: 'A statistic passed its watch threshold. A watch is shown and never alerted.',
  ALARM_PENDING:
    'The alarm condition holds in this window and did not in the previous one. An alert needs two consecutive windows.',
  ALARM: 'The alarm condition holds in this window and in the previous one, so an alert was raised.',
};

export const LEVEL_LABEL: Readonly<Record<StatisticLevel, string>> = {
  NORMAL: 'Normal',
  WATCH: 'Watch',
  ALARM: 'Alarm',
};

export const RESULT_LABEL: Readonly<Record<OutcomeResult, string>> = {
  PASSED: 'Passed the test',
  NOT_PASSED: 'Did not pass',
  NOT_TESTED: 'Not tested',
};

export const REVIEW_LABEL: Readonly<Record<ReviewCode, string>> = {
  REVIEW_SEGMENT_MIX: 'Review the segment mix',
  REVIEW_FEATURE_DISTRIBUTION: 'Review the variable’s distribution',
  REVIEW_PREDICTIVE_RELATION: 'Review what the segments predict',
};

const STATISTIC_LABEL = {
  PSI: 'Population stability index (PSI)',
  CHI_SQUARE: 'Chi-square goodness of fit',
} as const;

// ---------- numbers and dates ----------

/** A whole number with thousands separators, the same on every machine. */
export function count(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/** A share between 0 and 1 as a percentage with one decimal; the decimal the service sent decides it. */
export function percent(share: string | undefined): string {
  if (share === undefined) return 'no customers';
  return `${(Number(share) * 100).toFixed(1)}%`;
}

/** The date part of an RFC 3339 instant. */
export function day(instant: string): string {
  return instant.slice(0, 10);
}

// ---------- plain facts ----------

export function versionText(v: ModelVersionRefView): string {
  return `${v.name} (model version ${v.version_id}, ${v.method})`;
}

/** A DATA_DRIFT window is the trailing days ending at `end`; an OUTCOME_TEST window is a cohort's outcome window. */
export function windowText(kind: DriftKind, w: DriftWindowView): string {
  return kind === 'DATA_DRIFT'
    ? `${w.days} days ending ${w.end}, ${count(w.customer_count)} customers`
    : `Outcome window from ${w.start} to ${w.end} (${w.days} days), ${count(w.customer_count)} customers`;
}

export function referenceText(r: DriftReferenceView): string {
  return `${count(r.sample_size)} customers, captured ${r.captured_at}`;
}

export function relationText(o: Pick<OutcomeTestView, 'horizon_days'>): string {
  return `Repurchase within ${o.horizon_days} days, given the customer’s segment`;
}

/** The three things an outcome-test result names beside a claim. */
export function namedOutcome(o: OutcomeTestView): NamedOutcome {
  return { relation: relationText(o), horizon: `${o.horizon_days} days`, cohort: day(o.cohort_as_of) };
}

// ---------- comparison ----------

/** The short word for a table cell. */
export function comparisonLabel(r: Pick<DriftReportView, 'comparison' | 'not_compared_reason'>): string {
  return r.comparison === 'NOT_COMPARED' && r.not_compared_reason === 'VERSION_CHANGE'
    ? `${COMPARISON_LABEL.NOT_COMPARED} (change of version)`
    : COMPARISON_LABEL[r.comparison];
}

/**
 * Why a report holds no number, or null when it does. A window that crosses a change of model
 * version is a methodological difference, so it is said so and nothing else (ADR-0064).
 */
export function comparisonNotice(r: Pick<DriftReportView, 'kind' | 'comparison' | 'window'>): string | null {
  if (r.comparison === 'COMPARED') return null;
  if (r.comparison === 'NOT_COMPARED')
    return 'Not compared: the window crosses a change of model version, a publication or a rollback. A change of version is a methodological difference, so nothing in this window is compared and no figure is given.';
  return r.kind === 'DATA_DRIFT'
    ? `Insufficient data: the window holds ${count(r.window.customer_count)} customers and a comparison needs at least ${MIN_WINDOW_CUSTOMERS}. No figure is given.`
    : `Insufficient data: no segment has at least ${MIN_SEGMENT_CUSTOMERS} customers on both sides of the test. Nothing is tested and no figure is given.`;
}

// ---------- statistics against thresholds ----------

export function statisticName(s: Pick<DriftStatisticView, 'statistic'>): string {
  return STATISTIC_LABEL[s.statistic];
}

/** One threshold as a table cell reads it: PSI is reached at or above it, a p-value is below it. */
export function thresholdCell(s: DriftStatisticView, which: 'watch' | 'alarm'): string {
  const threshold = which === 'watch' ? s.watch_threshold : s.alarm_threshold;
  return s.threshold_basis === 'VALUE' ? `at or above ${threshold}` : `p below ${threshold}`;
}

export function thresholdText(s: DriftStatisticView): string {
  return `watch ${thresholdCell(s, 'watch')}, alarm ${thresholdCell(s, 'alarm')}`;
}

/** One statistic, its value and the thresholds it is judged by, in a sentence. */
export function statisticText(s: DriftStatisticView): string {
  const figure =
    s.threshold_basis === 'VALUE'
      ? s.value
      : `${s.value} (p = ${s.p_value ?? 'not given'}, ${s.degrees_of_freedom ?? '?'} degrees of freedom)`;
  return `${statisticName(s)}: ${figure}. Thresholds: ${thresholdText(s)}. Level: ${LEVEL_LABEL[s.level]}.`;
}

export const THRESHOLDS_NOTE =
  'The thresholds are conventions, not derived from these data (ADR-0065). A watch is shown, never alerted.';

export function stateText(state: VariableState): string {
  return `${STATE_LABEL[state]}. ${STATE_MEANING[state]}`;
}

// ---------- bins ----------

export function binName(b: Pick<DriftBinView, 'label'>): string {
  return b.label === 'NO_VALUE' ? 'No value' : b.label;
}

/** The range a decile bin covers, or '' for a segment and for the no-value bin. */
export function binRange(b: DriftBinView): string {
  return b.lower !== undefined && b.upper !== undefined ? `${b.lower} to ${b.upper}` : '';
}

// ---------- the claim ----------

export interface ClaimSubject {
  claim: DriftClaim;
  variable: DriftVariable;
  model_version: ModelVersionRefView;
  outcome_test?: OutcomeTestView;
}

/**
 * What a finding says about itself, and the only place the phrase concept drift is written
 * (ADR-0065). It is said only of an outcome-test result that persisted, and then with the
 * relation, the horizon and the cohort beside it; every other finding is a distribution change.
 */
export function claimText(c: ClaimSubject, form: 'short' | 'full'): string {
  const o = c.outcome_test;
  if (c.claim === 'CONCEPT_DRIFT' && o !== undefined && o.segments.some((s) => s.persisted)) {
    const named = namedOutcome(o);
    const where = `relation: ${named.relation}; horizon: ${named.horizon}; cohort as of ${named.cohort}`;
    if (form === 'short') return `Concept drift (${where})`;
    const segments = o.segments
      .filter((s) => s.persisted)
      .map((s) => s.label_code)
      .join(', ');
    return `Concept drift (${where}): the repurchase rate of ${segments} differs from the reference cohort’s, past the Holm-adjusted test and by at least ${o.thresholds.min_difference_points} percentage points, in two consecutive matured cohorts.`;
  }
  if (c.claim === 'DISTRIBUTION_CHANGE')
    return form === 'short'
      ? 'Distribution change'
      : `Distribution change: ${VARIABLE_SUBJECT[c.variable]} differs from the reference of model version ${c.model_version.version_id}. This says nothing about why, and does not show that the segments stopped predicting anything.`;
  return 'No claim is made.';
}

export function reviewLabel(code: ReviewCode): string {
  return REVIEW_LABEL[code];
}

export const REVIEW_NOTE = 'A suggestion to review, not a verdict.';

// ---------- the outcome test ----------

export function resultText(s: OutcomeSegmentView): string {
  const base = RESULT_LABEL[s.result];
  return s.result === 'PASSED' && !s.persisted ? `${base}, first time` : base;
}

/** How the test is run, with the thresholds the service applied. */
export function outcomeMethod(o: OutcomeTestView): string {
  return `A customer repurchases if at least one accepted sale of theirs, net of reversals, falls in the ${o.horizon_days} days after the cohort’s as-of date. Each segment’s rate is compared with the reference cohort’s by a two-proportion z-test, Holm-adjusted across the tested segments at ${o.thresholds.alpha}. A segment is tested only when both sides have at least ${o.thresholds.min_customers_per_side} customers, and a difference counts from ${o.thresholds.min_difference_points} percentage points.`;
}

/** What the test found, in words that make no claim the service did not. */
export function outcomeVerdict(o: OutcomeTestView): string {
  if (o.claim !== undefined) return '';
  const passed = o.segments.filter((s) => s.result === 'PASSED');
  return passed.length === 0
    ? 'No segment passed the test in this cohort.'
    : `${passed.map((s) => s.label_code).join(', ')} passed the test in this cohort. Nothing is claimed until a segment passes in two consecutive matured cohorts.`;
}

// ---------- tables ----------

/** A report in one line for the list: what its variables or segments came to. */
export function reportSummary(r: DriftReportView): string {
  if (r.comparison !== 'COMPARED') return '';
  if (r.outcome_test !== undefined) {
    const o = r.outcome_test;
    const tested = o.segments.filter((s) => s.result !== 'NOT_TESTED').length;
    const passed = o.segments.filter((s) => s.result === 'PASSED').length;
    return `${tested} of ${o.segments.length} segments tested, ${passed} passed`;
  }
  return r.variables.map((v) => `${VARIABLE_LABEL[v.variable]}: ${STATE_LABEL[v.state]}`).join('; ');
}

/** An alert in a table cell: what changed. */
export function alertSummary(a: DriftAlertView): string {
  return claimText(a, 'short');
}

// ---------- chart models ----------

export interface BarGroup {
  key: string;
  label: string;
  /** The range under the label, when the group has one. */
  detail: string;
  reference: number;
  current: number;
  referenceText: string;
  currentText: string;
}

/** Two shares (or rates) per group on one 0-to-max axis: the reference beside the current. */
export interface BarChartModel {
  title: string;
  /** What the axis measures, for the accessible name. */
  measure: string;
  max: number;
  ticks: number[];
  groups: BarGroup[];
}

const round4 = (n: number): number => Math.round(n * 10000) / 10000;

/** A round axis top above the largest value, and its ticks. */
export function axisOf(values: readonly number[]): { max: number; ticks: number[] } {
  const top = Math.max(0, ...values);
  const step = top <= 0.2 ? 0.05 : top <= 0.5 ? 0.1 : 0.2;
  const max = round4(Math.max(step, Math.ceil(round4(top / step)) * step));
  const ticks: number[] = [];
  for (let t = 0; t <= max + 1e-9; t = round4(t + step)) ticks.push(t);
  return { max, ticks };
}

/** The reference and current share of each bin of one variable; an alert charts the few bins that moved most. */
export function sharesChart(
  variable: DriftVariable,
  bins: readonly DriftBinView[],
  onlyMoved = false,
): BarChartModel {
  const axis = axisOf(bins.flatMap((b) => [Number(b.reference_share), Number(b.current_share)]));
  return {
    title: onlyMoved
      ? `${VARIABLE_LABEL[variable]}: share of customers in the ${bins.length} bins that moved most`
      : `${VARIABLE_LABEL[variable]}: share of customers in each bin`,
    measure: 'share of customers',
    ...axis,
    groups: bins.map((b) => ({
      key: b.label,
      label: binName(b),
      detail: binRange(b),
      reference: Number(b.reference_share),
      current: Number(b.current_share),
      referenceText: percent(b.reference_share),
      currentText: percent(b.current_share),
    })),
  };
}

/** The reference and current repurchase rate of each segment. */
export function ratesChart(o: OutcomeTestView): BarChartModel {
  const rate = (r: { rate?: string }): number => (r.rate === undefined ? 0 : Number(r.rate));
  const axis = axisOf(o.segments.flatMap((s) => [rate(s.reference), rate(s.current)]));
  return {
    title: `${relationText(o)}: rate of each segment`,
    measure: 'rate of repurchase',
    ...axis,
    groups: o.segments.map((s) => ({
      key: s.label_code,
      label: s.label_code,
      detail: '',
      reference: rate(s.reference),
      current: rate(s.current),
      referenceText: percent(s.reference.rate),
      currentText: percent(s.current.rate),
    })),
  };
}

/** A PSI against its two thresholds on one scale. A p-value has no such scale and is stated in words. */
export interface GaugeModel {
  value: number;
  max: number;
  watch: number;
  alarm: number;
  level: StatisticLevel;
}

export function gaugeOf(s: DriftStatisticView): GaugeModel | null {
  if (s.threshold_basis !== 'VALUE') return null;
  const [value, watch, alarm] = [Number(s.value), Number(s.watch_threshold), Number(s.alarm_threshold)];
  return { value, watch, alarm, max: Math.max(alarm * 2, value * 1.1), level: s.level };
}
