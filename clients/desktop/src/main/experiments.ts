import type {
  ArmCountView,
  ArmKind,
  AssignmentChecksView,
  AssignmentView,
  BalanceView,
  ExperimentArmView,
  ExperimentChecksView,
  ExperimentDataOrigin,
  ExperimentDesignView,
  ExperimentFlag,
  ExperimentMetric,
  ExperimentState,
  ExperimentView,
  FrameView,
  Paged,
  Result,
  SampleRatioView,
} from '../shared/api';
import { EXPERIMENT_OPERATIONS } from '../shared/modules';
import { MAX_ARMS, MIN_ARMS } from '../shared/experiments';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  id,
  invalidInput,
  optionalBoolean,
  optionalId,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import type { Group } from './xml';

// The A/B experiment module (F28-10, #879): experimentation-service's experiments, read through the
// transport (XML, XSD-validated), and the two writes that module owns, planning an experiment and
// assigning its frame. The service decides every figure (the customers each arm needs, whether the
// plan is UNDERPOWERED, the checks of an assignment); this class only passes them on, and checks
// the values the screen sends, naming the field the way the service would. Whether an arm set is
// legal (one control at most, two offers without a control) is the service's to refuse: its
// `control_count` and `treatment_count` come back as returned. The operations are published ahead
// of their implementation (ADR-0073); a service that does not answer yet is shown as it answers.

const MAX_ID = 2147483647;
const NAME_MAX = 120;
const DESCRIPTION_MAX = 500;
const WINDOW_MAX_DAYS = 365;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DECIMAL = /^\d+(\.\d+)?$/;

const STATES: readonly ExperimentState[] = ['PLANNED', 'ASSIGNED', 'ANALYZED'];
const ORIGINS: readonly ExperimentDataOrigin[] = ['OBSERVED', 'SEEDED', 'INJECTED'];
const METRICS: readonly ExperimentMetric[] = ['CONVERSION', 'REVENUE_PER_CUSTOMER'];
const ARM_KINDS: readonly ArmKind[] = ['CONTROL', 'TREATMENT'];

export interface ArmXml {
  arm_id: string;
  position: string;
  kind: ArmKind;
  name: string;
  treatment_description: string;
}

export interface DesignXml {
  primary_metric: ExperimentMetric;
  secondary_metric?: ExperimentMetric;
  estimator?: ExperimentDesignView['estimator'];
  alpha?: string;
  comparison_alpha?: string;
  power?: string;
  minimum_detectable_effect?: string;
  baseline?: string;
  baseline_std_dev?: string;
  required_per_arm?: string;
  smallest_detectable_effect?: string;
  allocation?: 'EQUAL';
  conversion_window_days: string;
  settle_days?: string;
  starts_on: string;
  analysis_at?: string;
}

interface FrameXml {
  taken_at: string;
  size: string;
  digest: string;
  label_code: string;
  model_version_id?: string;
  exclusions?: { reason: string; count: string }[];
}

export interface SampleRatioXml {
  status: SampleRatioView['status'];
  p_value: string;
  threshold: string;
}

export interface BalanceXml {
  measure: BalanceView['measure'];
  arm_id: string;
  reference_arm_id: string;
  standardized_mean_difference: string;
  flagged: string;
  threshold: string;
}

interface AssignmentXml {
  experiment_id: string;
  assigned_at: string;
  assigned_by?: string;
  assigned_total: string;
  acknowledged_underpowered: string;
  arms: { arm_id: string; kind: ArmKind; assigned: string }[];
  checks?: {
    recomputation_matches: string;
    sample_ratio: SampleRatioXml;
    balance?: BalanceXml[];
  };
}

interface ExperimentXml {
  experiment_id: string;
  name: string;
  campaign_id?: string;
  state: ExperimentState;
  design_kind: ExperimentView['design_kind'];
  assignment_rule: ExperimentView['assignment_rule'];
  data_origin: ExperimentDataOrigin;
  arms: ArmXml[];
  design: DesignXml;
  frame?: FrameXml;
  salt?: string;
  assignment?: AssignmentXml;
  flags?: ExperimentFlag[];
  planned_at?: string;
  planned_by?: string;
}

export const isTrue = (v: string): boolean => v === 'true' || v === '1';

export const number = (v: string | undefined): number | undefined =>
  v === undefined ? undefined : Number(v);

/** Present only when the service sent it: a field it left out stays absent. */
export function present<T extends object>(view: T): T {
  return Object.fromEntries(Object.entries(view).filter(([, v]) => v !== undefined)) as T;
}

export function armOf(x: ArmXml): ExperimentArmView {
  return {
    arm_id: Number(x.arm_id),
    position: Number(x.position),
    kind: x.kind,
    name: x.name,
    treatment_description: x.treatment_description,
  };
}

export function designOf(x: DesignXml): ExperimentDesignView {
  return present({
    primary_metric: x.primary_metric,
    secondary_metric: x.secondary_metric,
    estimator: x.estimator,
    alpha: x.alpha,
    comparison_alpha: x.comparison_alpha,
    power: x.power,
    minimum_detectable_effect: x.minimum_detectable_effect,
    baseline: x.baseline,
    baseline_std_dev: x.baseline_std_dev,
    required_per_arm: number(x.required_per_arm),
    smallest_detectable_effect: x.smallest_detectable_effect,
    allocation: x.allocation,
    conversion_window_days: Number(x.conversion_window_days),
    settle_days: number(x.settle_days),
    starts_on: x.starts_on,
    analysis_at: x.analysis_at,
  });
}

export function sampleRatioOf(x: SampleRatioXml): SampleRatioView {
  return { status: x.status, p_value: x.p_value, threshold: x.threshold };
}

export function balanceOf(x: BalanceXml): BalanceView {
  return {
    measure: x.measure,
    arm_id: Number(x.arm_id),
    reference_arm_id: Number(x.reference_arm_id),
    standardized_mean_difference: x.standardized_mean_difference,
    flagged: isTrue(x.flagged),
    threshold: x.threshold,
  };
}

export function checksOf(x: { sample_ratio: SampleRatioXml; balance?: BalanceXml[] }): ExperimentChecksView {
  return { sample_ratio: sampleRatioOf(x.sample_ratio), balance: (x.balance ?? []).map(balanceOf) };
}

function frameOf(x: FrameXml): FrameView {
  return present({
    taken_at: x.taken_at,
    size: Number(x.size),
    digest: x.digest,
    label_code: x.label_code,
    model_version_id: number(x.model_version_id),
    exclusions: (x.exclusions ?? []).map((e) => ({ reason: e.reason, count: Number(e.count) })),
  });
}

function assignmentOf(x: AssignmentXml): AssignmentView {
  const arms: ArmCountView[] = x.arms.map((a) => ({
    arm_id: Number(a.arm_id),
    kind: a.kind,
    assigned: Number(a.assigned),
  }));
  const checks: AssignmentChecksView | undefined =
    x.checks === undefined
      ? undefined
      : { recomputation_matches: isTrue(x.checks.recomputation_matches), ...checksOf(x.checks) };
  return present({
    experiment_id: Number(x.experiment_id),
    assigned_at: x.assigned_at,
    assigned_by: x.assigned_by,
    assigned_total: Number(x.assigned_total),
    acknowledged_underpowered: isTrue(x.acknowledged_underpowered),
    arms,
    checks,
  });
}

function experimentOf(x: ExperimentXml): ExperimentView {
  return present({
    experiment_id: Number(x.experiment_id),
    name: x.name,
    campaign_id: number(x.campaign_id),
    state: x.state,
    design_kind: x.design_kind,
    assignment_rule: x.assignment_rule,
    data_origin: x.data_origin,
    arms: x.arms.map(armOf),
    design: designOf(x.design),
    frame: x.frame === undefined ? undefined : frameOf(x.frame),
    salt: x.salt,
    assignment: x.assignment === undefined ? undefined : assignmentOf(x.assignment),
    flags: x.flags ?? [],
    planned_at: x.planned_at,
    planned_by: x.planned_by,
  });
}

function enumOf<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  if (typeof value !== 'string' || !allowed.includes(value as T))
    throw invalidInput(`${label} is not known.`);
  return value as T;
}

/** A calendar date a field names when it is missing or malformed. */
function dateField(f: FieldChecks, field: string, value: unknown): string {
  if (typeof value !== 'string' || value === '') {
    f.details.push({ field, code: 'required' });
    return '';
  }
  if (!DATE.test(value) || Number.isNaN(Date.parse(value))) {
    f.details.push({ field, code: 'invalid_format' });
    return '';
  }
  return value;
}

/** A positive decimal as typed (the XSD's PositiveNumber), kept as text so nothing is rounded on the way. */
function positiveDecimal(f: FieldChecks, field: string, value: unknown): string {
  if (typeof value !== 'string' || value.trim() === '') {
    f.details.push({ field, code: 'required' });
    return '';
  }
  const text = value.trim();
  if (!DECIMAL.test(text)) {
    f.details.push({ field, code: 'invalid_format' });
    return '';
  }
  if (!(Number(text) > 0)) {
    f.details.push({ field, code: 'out_of_range' });
    return '';
  }
  return text;
}

/** A probability strictly between 0 and 1 (alpha, power), as typed; absent when left empty. */
function optionalProbability(f: FieldChecks, field: string, value: unknown): string | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' || !DECIMAL.test(value.trim())) {
    f.details.push({ field, code: 'invalid_format' });
    return undefined;
  }
  const n = Number(value.trim());
  if (!(n > 0 && n < 1)) {
    f.details.push({ field, code: 'out_of_range' });
    return undefined;
  }
  return value.trim();
}

/** The inputs of a plan, in the XSD's order. Anything the service computes is never an input. */
function planFields(draft: unknown): Record<string, string | Group[]> {
  const d = record(draft, 'The experiment');
  const f = new FieldChecks();
  const name = f.text('name', d.name, NAME_MAX);
  const campaign = f.id('campaign_id', d.campaign_id, MAX_ID);
  let metric: ExperimentMetric = 'CONVERSION';
  if (typeof d.primary_metric === 'string' && METRICS.includes(d.primary_metric as ExperimentMetric))
    metric = d.primary_metric as ExperimentMetric;
  else f.details.push({ field: 'primary_metric', code: 'required' });
  const starts = dateField(f, 'starts_on', d.starts_on);
  let days = 0;
  if (
    typeof d.conversion_window_days !== 'number' ||
    !Number.isInteger(d.conversion_window_days) ||
    d.conversion_window_days < 1 ||
    d.conversion_window_days > WINDOW_MAX_DAYS
  )
    f.details.push({ field: 'conversion_window_days', code: 'out_of_range' });
  else days = d.conversion_window_days;
  const mde = positiveDecimal(f, 'minimum_detectable_effect', d.minimum_detectable_effect);
  const alpha = optionalProbability(f, 'alpha', d.alpha);
  const power = optionalProbability(f, 'power', d.power);

  const armsRaw = d.arms;
  const arms: Group[] = [];
  if (!Array.isArray(armsRaw) || armsRaw.length < MIN_ARMS || armsRaw.length > MAX_ARMS) {
    f.details.push({ field: 'arms', code: 'out_of_range' });
  } else {
    armsRaw.forEach((raw: unknown, i) => {
      const a = record(raw, `Arm ${i + 1}`);
      const kind = typeof a.kind === 'string' && ARM_KINDS.includes(a.kind as ArmKind) ? a.kind : null;
      if (kind === null) f.details.push({ field: `arm_${i + 1}_kind`, code: 'required' });
      arms.push({
        kind: kind ?? 'TREATMENT',
        name: f.text(`arm_${i + 1}_name`, a.name, NAME_MAX),
        treatment_description: f.text(
          `arm_${i + 1}_treatment_description`,
          a.treatment_description,
          DESCRIPTION_MAX,
        ),
      });
    });
  }
  f.done();
  return {
    name,
    campaign_id: String(campaign),
    primary_metric: metric,
    starts_on: starts,
    conversion_window_days: String(days),
    minimum_detectable_effect: mde,
    ...(alpha === undefined ? {} : { alpha }),
    ...(power === undefined ? {} : { power }),
    arms,
  };
}

export class Experiments {
  constructor(private readonly office: Backoffice) {}

  /** A role calls an operation only when the contract's x-mosaiq-roles allow it. */
  private allowed(operation: string, what: string): Result<never> | null {
    const state = this.office.state();
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return state.operations.includes(operation)
      ? null
      : { ok: false, error: localError('forbidden_role', `Your role may not ${what}.`) };
  }

  list(query: unknown): Promise<Result<Paged<ExperimentView>>> {
    return checked(this.office, async () => {
      const q = record(query ?? {}, 'The search');
      const refused = this.allowed(EXPERIMENT_OPERATIONS.list, 'read experiments');
      if (refused !== null) return refused;
      const params = new URLSearchParams();
      const campaign = optionalId(q.campaign_id, MAX_ID, 'The campaign');
      if (campaign !== undefined) params.set('campaign_id', String(campaign));
      if (q.state !== undefined) params.set('state', enumOf(q.state, STATES, 'The state'));
      if (q.data_origin !== undefined)
        params.set('data_origin', enumOf(q.data_origin, ORIGINS, 'The data origin'));
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/experiments?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.Experiments as { Experiment?: ExperimentXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.Experiment ?? []).map(experimentOf), page: pageOf(data) } };
    });
  }

  get(experimentId: unknown): Promise<Result<ExperimentView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(EXPERIMENT_OPERATIONS.get, 'read an experiment');
      if (refused !== null) return refused;
      const eid = id(experimentId, MAX_ID, 'The experiment');
      const res = await this.office.authed({ method: 'GET', path: `/v1/experiments/${eid}` });
      if (!res.ok) return failed(res);
      return { ok: true, value: experimentOf(res.data?.Experiment as ExperimentXml) };
    });
  }

  /** Freezes a plan. Nothing in it is editable afterwards and there is no operation to delete it. */
  plan(draft: unknown): Promise<Result<ExperimentView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(EXPERIMENT_OPERATIONS.plan, 'plan an experiment');
      if (refused !== null) return refused;
      const fields = planFields(draft);
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/experiments',
        body: { root: 'ExperimentRequest', fields },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: experimentOf(res.data?.Experiment as ExperimentXml) };
    });
  }

  /**
   * Assigns the whole frame, once. The acknowledgement is sent only when it is true; whether it is
   * needed is the service's to say (409 underpowered_not_acknowledged), so a stale screen cannot
   * assign an underpowered plan by omission.
   */
  assign(experimentId: unknown, acknowledgeUnderpowered: unknown): Promise<Result<AssignmentView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(EXPERIMENT_OPERATIONS.assign, 'assign an experiment');
      if (refused !== null) return refused;
      const eid = id(experimentId, MAX_ID, 'The experiment');
      const acknowledge = optionalBoolean(acknowledgeUnderpowered, 'The acknowledgement');
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/experiment-assignments',
        body: {
          root: 'AssignmentRequest',
          fields: {
            experiment_id: String(eid),
            ...(acknowledge === true ? { acknowledge_underpowered: 'true' } : {}),
          },
        },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: assignmentOf(res.data?.AssignmentSummary as AssignmentXml) };
    });
  }
}
