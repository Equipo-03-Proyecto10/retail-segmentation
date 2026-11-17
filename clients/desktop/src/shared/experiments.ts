// What the A/B experiment screens say about a design (F28-10, #879). Pure rules and words, so the
// renderer and a unit test read the same ones. The service decides every figure (the customers each
// arm needs, the flags, the checks); nothing here computes a statistic. What is computed is
// arithmetic on the plan the service froze: how many customers the frame supplies to each arm.

import type {
  ArmKind,
  BalanceMeasure,
  BalanceView,
  ExperimentDataOrigin,
  ExperimentDesignView,
  ExperimentFlag,
  ExperimentMetric,
  ExperimentView,
  SampleRatioView,
} from './api';

/** The XSD's bounds on a plan's arms (ExperimentRequest). */
export const MIN_ARMS = 2;
export const MAX_ARMS = 5;

/** Said wherever a design has no control (ADR-0068 rule 3, RN-24). */
export const NO_EFFECT_STATEMENT =
  'This design has no control arm, so it holds no untreated population. It can show the difference between the two offers and its interval, but no effect of the campaign against no treatment can be claimed.';

export interface DesignReading {
  controls: number;
  treatments: number;
  /** controlled: one control and treatments. offer_comparison: two treatments and no control. invalid: the service would refuse it. */
  design: 'controlled' | 'offer_comparison' | 'invalid';
  /** True only for a controlled design; an effect is only ever a treatment against the control. */
  effectClaimable: boolean;
  /** True whenever no arm is a control: the screen then says no effect can be claimed. */
  noControl: boolean;
  statement: string;
  problems: string[];
}

/** What a set of arms is, and whether the service would accept it (RN-24, ADR-0068 rule 3). */
export function readDesign(arms: readonly { kind: ArmKind }[]): DesignReading {
  const controls = arms.filter((a) => a.kind === 'CONTROL').length;
  const treatments = arms.filter((a) => a.kind === 'TREATMENT').length;
  const problems: string[] = [];
  if (arms.length < MIN_ARMS || arms.length > MAX_ARMS)
    problems.push(`An experiment has ${MIN_ARMS} to ${MAX_ARMS} arms.`);
  if (controls > 1) problems.push('A control may appear at most once.');
  if (treatments === 0) problems.push('An experiment needs at least one treatment.');
  if (controls === 0 && treatments !== 0 && treatments !== 2)
    problems.push('Without a control, an experiment compares exactly two offers.');
  const noControl = controls === 0;
  if (problems.length > 0)
    return {
      controls,
      treatments,
      design: 'invalid',
      effectClaimable: false,
      noControl,
      statement: noControl ? NO_EFFECT_STATEMENT : 'This design cannot be planned as it stands.',
      problems,
    };
  if (noControl)
    return {
      controls,
      treatments,
      design: 'offer_comparison',
      effectClaimable: false,
      noControl,
      statement: NO_EFFECT_STATEMENT,
      problems,
    };
  return {
    controls,
    treatments,
    design: 'controlled',
    effectClaimable: true,
    noControl,
    statement: `One control and ${treatments} ${treatments === 1 ? 'treatment' : 'treatments'}: the only design that can state an effect, and only of a treatment against the control.`,
    problems,
  };
}

/** Whether an experiment as served has a control; both the design kind and the arms must say so. */
export function hasControl(e: Pick<ExperimentView, 'design_kind' | 'arms'>): boolean {
  return e.design_kind === 'CONTROLLED' && e.arms.filter((a) => a.kind === 'CONTROL').length === 1;
}

const COUNT = new Intl.NumberFormat('en-US');
export const formatCount = (n: number): string => COUNT.format(n);

export interface SampleSizeReading {
  /** The customers each arm needs, as the service computed it at planning. */
  required: number;
  arms: number;
  /** The frame the plan froze; absent only if the service sent none. */
  frameSize?: number;
  /** The frame divided by the arms, rounded down: what each arm can be dealt. Arms differ by at most one. */
  perArm?: number;
  /** The service's flag, not a recomputation. */
  underpowered: boolean;
  smallestDetectable?: string;
  minimumDetectable?: string;
}

/** The sample size of a planned design, or null when the plan recorded none (assignment rule V0). */
export function sampleSizeOf(
  e: Pick<ExperimentView, 'design' | 'frame' | 'arms' | 'flags'>,
): SampleSizeReading | null {
  const required = e.design.required_per_arm;
  if (required === undefined) return null;
  const size = e.frame?.size;
  return {
    required,
    arms: e.arms.length,
    ...(size === undefined ? {} : { frameSize: size, perArm: Math.floor(size / e.arms.length) }),
    underpowered: e.flags.includes('UNDERPOWERED'),
    ...(e.design.smallest_detectable_effect === undefined
      ? {}
      : { smallestDetectable: e.design.smallest_detectable_effect }),
    ...(e.design.minimum_detectable_effect === undefined
      ? {}
      : { minimumDetectable: e.design.minimum_detectable_effect }),
  };
}

export function sampleSizeText(r: SampleSizeReading): string {
  const need = `Each arm needs ${formatCount(r.required)} customers.`;
  if (r.frameSize === undefined || r.perArm === undefined) return need;
  return `${need} The frame holds ${formatCount(r.frameSize)} customers, which supplies ${formatCount(r.perArm)} to each of ${r.arms} arms.`;
}

export const FLAG_TEXT: Readonly<Record<ExperimentFlag, { title: string; text: string }>> = {
  UNDERPOWERED: {
    title: 'Underpowered',
    text: 'The frame cannot supply the customers each arm needs to detect the planned effect. A result that is not significant is inconclusive, never "no effect".',
  },
  SAMPLE_RATIO_FAILED: {
    title: 'Sample ratio check failed',
    text: 'The customers reaching the analysis do not match the planned allocation, which points at a fault in the pipeline. The result is invalid and its estimate is withheld.',
  },
  BALANCE_FLAGGED: {
    title: 'Balance flagged',
    text: 'An arm differs from its reference on a measure taken before assignment by more than the threshold. It is a diagnostic only: it never changes the estimate and an assignment is never redrawn.',
  },
  RECOMPUTATION_MISMATCH: {
    title: 'Recomputation mismatch',
    text: 'Recomputing the assignment from the plan did not give the stored arms, so the assignment cannot be called verified.',
  },
};

export const METRIC_TEXT: Readonly<Record<ExperimentMetric, string>> = {
  CONVERSION: 'Conversion: the share of assigned customers with a qualifying sale in their window',
  REVENUE_PER_CUSTOMER:
    'Revenue per assigned customer: net sales in the window, zero for a customer who bought nothing',
};

/** The unit the minimum detectable effect is typed in, for each metric (ADR-0068 rule 4). */
export const MDE_UNIT_TEXT: Readonly<Record<ExperimentMetric, string>> = {
  CONVERSION: 'A rate difference: 0.02 is two percentage points.',
  REVENUE_PER_CUSTOMER: 'Money per assigned customer, for example 1.50.',
};

export const ESTIMATOR_TEXT: Readonly<Record<NonNullable<ExperimentDesignView['estimator']>, string>> = {
  DIFFERENCE_OF_PROPORTIONS: 'Difference of proportions (unpooled Wald interval, pooled two-sided test)',
  DIFFERENCE_OF_MEANS: 'Difference of means (Welch interval and test, with a bootstrap beside it)',
};

export const BALANCE_MEASURE_TEXT: Readonly<Record<BalanceMeasure, string>> = {
  PRE_RECENCY_DAYS: 'Days since the last purchase, before the frame',
  PRE_PURCHASE_COUNT: 'Purchases before the frame',
  PRE_NET_SPEND: 'Net spend before the frame',
};

/** Synthetic is the label of every figure from seeded or injected data (RN-26, ADR-0052). */
export const SYNTHETIC_LABEL = 'Synthetic';

export const isSynthetic = (origin: ExperimentDataOrigin): boolean => origin !== 'OBSERVED';

export function sampleRatioText(c: SampleRatioView): string {
  return c.status === 'PASSED'
    ? `Passed (p = ${c.p_value}; a p-value below ${c.threshold} fails the check).`
    : `Failed (p = ${c.p_value}, below ${c.threshold}): the customers reaching the analysis do not match the planned allocation. The result will be invalid and its estimate withheld.`;
}

export function balanceText(b: BalanceView): string {
  return b.flagged
    ? 'Above the threshold: a diagnostic only, it never changes the estimate.'
    : 'Within the threshold.';
}

/** A name for an arm id; an id the arms do not hold is shown as an id. */
export function armName(arms: readonly { arm_id: number; name: string }[], armId: number): string {
  return arms.find((a) => a.arm_id === armId)?.name ?? `Arm #${armId}`;
}

/**
 * Moves the decimal point of a plain decimal string without going through a float, so a figure is
 * never rounded on the way to the screen: "0.0537" by 2 is "5.37". Null when it is not a plain decimal.
 */
export function shiftDecimal(value: string, places: number): string | null {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (m === null) return null;
  const sign = m[1] ?? '';
  const whole = m[2] ?? '';
  const digits = whole + (m[3] ?? '');
  const point = whole.length + places;
  let intPart: string;
  let fracPart: string;
  if (point <= 0) {
    intPart = '0';
    fracPart = '0'.repeat(-point) + digits;
  } else if (point >= digits.length) {
    intPart = digits + '0'.repeat(point - digits.length);
    fracPart = '';
  } else {
    intPart = digits.slice(0, point);
    fracPart = digits.slice(point);
  }
  intPart = intPart.replace(/^0+(?=\d)/, '');
  const negative = sign === '-' && /[1-9]/.test(intPart + fracPart);
  return `${negative ? '-' : ''}${intPart}${fracPart === '' ? '' : `.${fracPart}`}`;
}

/** A figure in its metric's own unit: a rate difference also in percentage points, money per customer. */
export function metricFigure(metric: ExperimentMetric, value: string): string {
  if (metric === 'REVENUE_PER_CUSTOMER') return `${value} per customer`;
  const points = shiftDecimal(value, 2);
  return points === null ? value : `${value} (${points} percentage points)`;
}
