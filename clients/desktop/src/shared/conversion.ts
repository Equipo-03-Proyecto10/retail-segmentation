// What the conversion analysis screen says about a result (F28-11, #880). Pure rules and words, so the
// renderer and a unit test read the same ones. The service decides every figure; nothing here computes
// a statistic. What this file decides is how a figure is named: an effect only against a control, a
// difference between treatments otherwise, attribution apart from both (ADR-0068, ADR-0069), and which
// warnings of the design travel with the result.

import type { ArmOutcomeView, AttributionView, ComparisonView, ExperimentResultView } from './api';
import { FLAG_TEXT, formatCount, NO_EFFECT_STATEMENT, shiftDecimal, SYNTHETIC_LABEL } from './experiments';

/** A fraction as sent, written as a percentage without going through a float: "0.2857" is "28.57%". */
export function percent(fraction: string): string {
  const shifted = shiftDecimal(fraction, 2);
  return shifted === null ? fraction : `${shifted}%`;
}

/** The control of a controlled design: the kind says so and exactly one arm is a control. */
export function controlArm(
  r: Pick<ExperimentResultView, 'design_kind' | 'arms'>,
): ArmOutcomeView | undefined {
  if (r.design_kind !== 'CONTROLLED') return undefined;
  const controls = r.arms.filter((a) => a.kind === 'CONTROL');
  return controls.length === 1 ? controls[0] : undefined;
}

export type ComparisonKind = 'effect' | 'treatment_difference' | 'refused';

/**
 * How a comparison may be shown. An effect is a treatment's difference against the control of a
 * controlled design and nothing else; an EFFECT the service sent without one is refused here, never
 * shown as an effect (ADR-0068 rule 3).
 */
export function kindOf(
  c: Pick<ComparisonView, 'label' | 'arm_id' | 'against_arm_id'>,
  r: Pick<ExperimentResultView, 'design_kind' | 'arms'>,
): ComparisonKind {
  if (c.label !== 'EFFECT') return 'treatment_difference';
  const control = controlArm(r);
  return control !== undefined && c.against_arm_id === control.arm_id && c.arm_id !== control.arm_id
    ? 'effect'
    : 'refused';
}

export function comparisonsOf(r: ExperimentResultView, kind: ComparisonKind): ComparisonView[] {
  return r.comparisons.filter((c) => kindOf(c, r) === kind);
}

export function armLabel(r: Pick<ExperimentResultView, 'arms'>, armId: number): string {
  return r.arms.find((a) => a.arm_id === armId)?.name ?? `Arm #${armId}`;
}

const NOT_ZERO = /[1-9]/;
const signed = (v: string): string => (v.startsWith('-') || !NOT_ZERO.test(v) ? v : `+${v}`);

/** A figure in its metric's own unit, with its sign: a rate difference also in points, money per customer. */
function figure(metric: ComparisonView['metric'], value: string): string {
  const v = signed(value);
  if (metric === 'REVENUE_PER_CUSTOMER') return `${v} per customer`;
  const points = shiftDecimal(value, 2);
  return points === null ? v : `${v} (${signed(points)} percentage points)`;
}

export function differenceText(c: ComparisonView): string {
  return figure(c.metric, c.difference);
}

/** The interval beside the difference, as sent and, for a rate, in percentage points. */
export function intervalText(c: ComparisonView): string {
  if (c.metric === 'REVENUE_PER_CUSTOMER') return `${c.lower} to ${c.upper} per customer`;
  const lo = shiftDecimal(c.lower, 2);
  const hi = shiftDecimal(c.upper, 2);
  return lo === null || hi === null
    ? `${c.lower} to ${c.upper}`
    : `${c.lower} to ${c.upper} (${lo} to ${hi} percentage points)`;
}

export function confidenceText(c: ComparisonView): string {
  return `${percent(c.confidence_level)} interval`;
}

export function methodText(c: ComparisonView): string {
  const test =
    c.test === 'POOLED_Z'
      ? 'Pooled z test, two-sided'
      : `Welch t test, two-sided${c.degrees_of_freedom === undefined ? '' : `, ${c.degrees_of_freedom} degrees of freedom`}`;
  if (c.bootstrap === undefined) return test;
  const b = c.bootstrap;
  return `${test}. Bootstrap (${formatCount(b.resamples)} resamples): ${b.lower} to ${b.upper}, ${b.agrees ? 'agrees' : 'DISAGREES'}`;
}

export function conclusionText(c: ComparisonView, kind: ComparisonKind): string {
  if (c.conclusion === undefined) return c.role === 'SECONDARY' ? 'No claim (secondary metric)' : '—';
  if (c.conclusion === 'SIGNIFICANT') return "Significant at the experiment's alpha";
  if (c.conclusion === 'NOT_SIGNIFICANT') return "Not significant at the experiment's alpha";
  return kind === 'effect'
    ? 'Inconclusive: the plan was underpowered, so this is not evidence that there is no effect'
    : 'Inconclusive: the plan was underpowered, so this is not evidence that the offers do not differ';
}

const METRIC_NAME: Record<ComparisonView['metric'], string> = {
  CONVERSION: 'Conversion',
  REVENUE_PER_CUSTOMER: 'Revenue per customer',
};

const COMPARISON_BASE_COLUMNS = [
  'Label',
  'Metric',
  'Role',
  'Arm',
  'Against',
  'Difference',
  'Interval',
  'Confidence',
  'p-value',
  'Method',
  'Conclusion',
];

/** An effect in money exists only when the primary metric is revenue per customer, and only on an effect. */
export const hasRevenueEffect = (r: ExperimentResultView): boolean =>
  comparisonsOf(r, 'effect').some((c) => c.incremental_revenue !== undefined);

export function comparisonColumns(
  r: ExperimentResultView,
  kind: 'effect' | 'treatment_difference',
): string[] {
  return kind === 'effect' && hasRevenueEffect(r)
    ? [...COMPARISON_BASE_COLUMNS, 'Incremental revenue']
    : COMPARISON_BASE_COLUMNS;
}

/** The money an effect on revenue is worth, derived only from the difference against the control, with its interval. */
export function revenueEffectText(c: ComparisonView): string {
  const e = c.incremental_revenue;
  return e === undefined ? '—' : `${e.amount} (interval ${e.lower} to ${e.upper})`;
}

/** The rows of the effect table or of the table of differences between treatments; never mixed. */
export function comparisonRows(r: ExperimentResultView, kind: 'effect' | 'treatment_difference'): string[][] {
  const money = kind === 'effect' && hasRevenueEffect(r);
  return comparisonsOf(r, kind).map((c) => [
    kind === 'effect' ? 'Effect' : 'Difference between treatments',
    METRIC_NAME[c.metric],
    c.role === 'PRIMARY' ? 'Primary' : 'Secondary (no claim)',
    armLabel(r, c.arm_id),
    armLabel(r, c.against_arm_id),
    differenceText(c),
    intervalText(c),
    confidenceText(c),
    c.p_value,
    methodText(c),
    conclusionText(c, kind),
    ...(money ? [revenueEffectText(c)] : []),
  ]);
}

export interface DesignWarning {
  code: string;
  title: string;
  text: string;
}

/**
 * Everything that must be read with the figures: what the design flagged, whether it holds a control,
 * and the state of the reading. The screen keeps it next to the result and the export carries it on
 * every arm row.
 */
export function warningsOf(r: ExperimentResultView): DesignWarning[] {
  const out: DesignWarning[] = [];
  if (controlArm(r) === undefined)
    out.push({
      code: 'NO_CONTROL',
      title: 'No control: no effect can be claimed',
      text: NO_EFFECT_STATEMENT,
    });
  if (r.status === 'INVALID')
    out.push({
      code: 'INVALID',
      title: 'Estimate withheld',
      text: 'The sample ratio check failed, so the service withholds the estimate. The counts are shown as they are.',
    });
  if (r.status === 'PENDING')
    out.push({
      code: 'PENDING',
      title: 'Not read yet',
      text: `Counts only: no difference, interval, p-value or direction is shown before the analysis instant${
        r.design.analysis_at === undefined ? '' : ` (${r.design.analysis_at})`
      }.`,
    });
  for (const f of r.flags) out.push({ code: f, title: FLAG_TEXT[f].title, text: FLAG_TEXT[f].text });
  if (r.comparisons.some((c) => c.conclusion === 'INCONCLUSIVE'))
    out.push({
      code: 'INCONCLUSIVE',
      title: 'Inconclusive',
      text: 'A non-significant result of an underpowered plan is inconclusive, never "no effect".',
    });
  if (r.comparisons.some((c) => c.bootstrap?.agrees === false))
    out.push({
      code: 'BOOTSTRAP_DISAGREES',
      title: 'Bootstrap disagrees',
      text: 'The bootstrap beside the Welch interval disagrees with it on the sign or on whether the interval excludes zero. Read the figure with care.',
    });
  if (r.synthetic)
    out.push({
      code: 'SYNTHETIC',
      title: SYNTHETIC_LABEL,
      text: 'Some figures come from seeded or injected data, not from real customers (ADR-0052).',
    });
  if (r.basis === 'RE_READ')
    out.push({
      code: 'RE_READ',
      title: 'Re-read',
      text: `Computed again as of ${r.as_of}. It replaces nothing: the stored primary result stays as it was.`,
    });
  return out;
}

export const warningTitles = (r: ExperimentResultView): string =>
  warningsOf(r)
    .map((w) => w.title)
    .join('; ');

/** What a status says about the comparisons that are not there. */
export function statusText(r: ExperimentResultView): string | null {
  if (r.status === 'PENDING')
    return `Counts only. No difference, interval, p-value or direction is shown before the analysis instant${
      r.design.analysis_at === undefined ? '' : ` (${r.design.analysis_at})`
    }; ${percent(r.elapsed_share)} of the conversion window has elapsed.`;
  if (r.status === 'INVALID')
    return 'The sample ratio check failed, so the estimate is withheld. No difference or interval is shown.';
  return null;
}

/** The customers who have not bought yet with their window still open, stated for every result. */
export function pendingText(r: Pick<ExperimentResultView, 'arms'>): string {
  const total = r.arms.reduce((n, a) => n + a.pending, 0);
  if (total === 0)
    return 'No customer is pending: every conversion window has closed, so each customer has bought or has not.';
  const per = r.arms.map((a) => `${a.name}: ${formatCount(a.pending)}`).join('; ');
  return `${formatCount(total)} customers are pending (${per}). They have not bought yet and their conversion window is still open. They are counted in each arm's customers, so a rate can still rise.`;
}

export const ARM_COLUMNS = [
  'Arm',
  'Kind',
  'Assigned',
  'Analysed',
  'Exposed',
  'Converted',
  'Pending',
  'Not converted',
  'Conversion rate',
  'Conversion rate among the exposed (descriptive, not causal)',
  'Net revenue',
  'Revenue per customer',
  'Data',
  'As of',
  'Design warnings',
];

/**
 * Each arm's observed figures. Every row carries the data origin, the instant the net sales were read
 * and the design warnings, so a table taken on its own (the generic CSV export writes the first one)
 * still says what it is: these are observed counts, neither an effect nor attribution.
 */
export function armRows(r: ExperimentResultView): string[][] {
  const titles = warningTitles(r);
  return r.arms.map((a) => [
    a.name,
    a.kind === 'CONTROL' ? 'Control' : 'Treatment',
    formatCount(a.assigned),
    formatCount(a.analysed),
    formatCount(a.exposed),
    formatCount(a.converted),
    formatCount(a.pending),
    formatCount(a.not_converted),
    percent(a.conversion_rate),
    a.exposed_conversion_rate === undefined ? '—' : percent(a.exposed_conversion_rate),
    a.net_revenue,
    a.revenue_per_customer,
    r.synthetic ? SYNTHETIC_LABEL : 'Observed',
    r.as_of,
    titles === '' ? 'None' : titles,
  ]);
}

/**
 * What attribution is, in words that never call it an effect: no field or sentence of the attribution
 * section uses the words the effect is reserved for (ADR-0069).
 */
export const ATTRIBUTION_NOTE =
  'Attribution credits each sale to at most one campaign, from a recorded exposure before the sale and inside the conversion window. It is commercial credit, not a measure of what the campaign changed, and it is neither added to nor compared with the comparison against a control.';

export const UNATTRIBUTED_NOTE =
  "A treatment customer who bought without a recorded exposure counts in the arm's conversions but is credited to nothing, so attributed revenue is below the arm's revenue by design.";

export const ATTRIBUTION_COLUMNS = [
  'Label',
  'Arm',
  'Attributed sales',
  'Attributed customers',
  'Attributed revenue',
];

export function attributionRows(r: ExperimentResultView): string[][] {
  const a = r.attribution;
  if (a === undefined) return [];
  return [
    ...a.arms.map((x) => [
      'Attribution',
      armLabel(r, x.arm_id),
      formatCount(x.attributed_sales),
      formatCount(x.attributed_customers),
      x.attributed_revenue,
    ]),
    ['Attribution', 'All treatment arms', formatCount(a.attributed_sales), '—', a.attributed_revenue],
  ];
}

export function unattributedText(a: Pick<AttributionView, 'unattributed_conversions'>): string {
  return `${formatCount(a.unattributed_conversions)} treatment conversions are not held by this campaign.`;
}

export function windowText(a: Pick<AttributionView, 'window_days' | 'as_of'>): string {
  return `Window: ${a.window_days} days, the conversion window. As of ${a.as_of}.`;
}

/** Every sentence the attribution section can show, for a test that none uses the effect's words. */
export function attributionTexts(r: ExperimentResultView): string[] {
  const a = r.attribution;
  if (a === undefined) return [];
  return [
    ATTRIBUTION_NOTE,
    UNATTRIBUTED_NOTE,
    ...ATTRIBUTION_COLUMNS,
    ...attributionRows(r).flat(),
    unattributedText(a),
    windowText(a),
  ];
}
