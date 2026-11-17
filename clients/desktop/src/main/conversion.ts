import type {
  ArmAttributionView,
  ArmOutcomeView,
  AssumptionView,
  AttributionView,
  ComparisonView,
  Conclusion,
  ExperimentDataOrigin,
  ExperimentFlag,
  ExperimentResultView,
  ResultBasis,
  ResultStatus,
  Result,
} from '../shared/api';
import { CONVERSION_OPERATIONS } from '../shared/modules';
import { localError, type Backoffice } from './backoffice';
import { checked, failed, id, invalidInput } from './checks';
import {
  checksOf,
  designOf,
  isTrue,
  number,
  present,
  type ArmXml,
  type BalanceXml,
  type DesignXml,
  type SampleRatioXml,
} from './experiments';

// The conversion analysis module (F28-11, #880): experimentation-service's result for one experiment,
// read through the transport (XML, XSD-validated). Nothing is written. The service decides every
// figure (the counts, each difference and its interval, the conclusion, the flags, the attribution);
// this class passes them on, keeping every decimal as sent, and checks the two values the screen
// sends. The operation is published ahead of its implementation (ADR-0073); a service that does not
// answer yet is shown as it answers.

const MAX_ID = 2147483647;
const BASES: readonly ResultBasis[] = ['PRIMARY', 'RE_READ'];

interface ArmOutcomeXml extends Omit<ArmXml, 'position' | 'treatment_description'> {
  assigned: string;
  analysed: string;
  exposed: string;
  converted: string;
  pending: string;
  not_converted: string;
  conversion_rate: string;
  exposed_conversion_rate?: string;
  net_revenue: string;
  revenue_per_customer: string;
  revenue_std_dev: string;
}

interface ComparisonXml {
  label: ComparisonView['label'];
  metric: ComparisonView['metric'];
  role: ComparisonView['role'];
  arm_id: string;
  against_arm_id: string;
  difference: string;
  lower: string;
  upper: string;
  confidence_level: string;
  test: ComparisonView['test'];
  p_value: string;
  degrees_of_freedom?: string;
  conclusion?: Conclusion;
  incremental_revenue?: { amount: string; lower: string; upper: string };
  bootstrap?: { resamples: string; lower: string; upper: string; agrees: string };
}

interface AttributionXml {
  label: 'ATTRIBUTION';
  window_days: string;
  arms?: {
    arm_id: string;
    attributed_sales: string;
    attributed_customers: string;
    attributed_revenue: string;
  }[];
  attributed_sales: string;
  attributed_revenue: string;
  unattributed_conversions: string;
  as_of: string;
}

interface ResultXml {
  experiment_id: string;
  campaign_id?: string;
  basis: ResultBasis;
  status: ResultStatus;
  as_of: string;
  elapsed_share: string;
  data_origin: ExperimentDataOrigin;
  synthetic: string;
  design_kind: ExperimentResultView['design_kind'];
  design: DesignXml;
  arms: ArmOutcomeXml[];
  checks?: { sample_ratio: SampleRatioXml; balance?: BalanceXml[] };
  comparisons?: ComparisonXml[];
  effect_refusal?: { code: string; message: string };
  attribution?: AttributionXml;
  assumptions?: AssumptionView[];
  flags?: ExperimentFlag[];
}

function outcomeOf(x: ArmOutcomeXml): ArmOutcomeView {
  return present({
    arm_id: Number(x.arm_id),
    kind: x.kind,
    name: x.name,
    assigned: Number(x.assigned),
    analysed: Number(x.analysed),
    exposed: Number(x.exposed),
    converted: Number(x.converted),
    pending: Number(x.pending),
    not_converted: Number(x.not_converted),
    conversion_rate: x.conversion_rate,
    exposed_conversion_rate: x.exposed_conversion_rate,
    net_revenue: x.net_revenue,
    revenue_per_customer: x.revenue_per_customer,
    revenue_std_dev: x.revenue_std_dev,
  });
}

function comparisonOf(x: ComparisonXml): ComparisonView {
  return present({
    label: x.label,
    metric: x.metric,
    role: x.role,
    arm_id: Number(x.arm_id),
    against_arm_id: Number(x.against_arm_id),
    difference: x.difference,
    lower: x.lower,
    upper: x.upper,
    confidence_level: x.confidence_level,
    test: x.test,
    p_value: x.p_value,
    degrees_of_freedom: x.degrees_of_freedom,
    conclusion: x.conclusion,
    incremental_revenue: x.incremental_revenue,
    bootstrap:
      x.bootstrap === undefined
        ? undefined
        : {
            resamples: Number(x.bootstrap.resamples),
            lower: x.bootstrap.lower,
            upper: x.bootstrap.upper,
            agrees: isTrue(x.bootstrap.agrees),
          },
  });
}

function attributionOf(x: AttributionXml): AttributionView {
  const arms: ArmAttributionView[] = (x.arms ?? []).map((a) => ({
    arm_id: Number(a.arm_id),
    attributed_sales: Number(a.attributed_sales),
    attributed_customers: Number(a.attributed_customers),
    attributed_revenue: a.attributed_revenue,
  }));
  return {
    label: x.label,
    window_days: Number(x.window_days),
    arms,
    attributed_sales: Number(x.attributed_sales),
    attributed_revenue: x.attributed_revenue,
    unattributed_conversions: Number(x.unattributed_conversions),
    as_of: x.as_of,
  };
}

function resultOf(x: ResultXml): ExperimentResultView {
  return present({
    experiment_id: Number(x.experiment_id),
    campaign_id: number(x.campaign_id),
    basis: x.basis,
    status: x.status,
    as_of: x.as_of,
    elapsed_share: x.elapsed_share,
    data_origin: x.data_origin,
    synthetic: isTrue(x.synthetic),
    design_kind: x.design_kind,
    design: designOf(x.design),
    arms: x.arms.map(outcomeOf),
    checks: x.checks === undefined ? undefined : checksOf(x.checks),
    comparisons: (x.comparisons ?? []).map(comparisonOf),
    effect_refusal: x.effect_refusal,
    attribution: x.attribution === undefined ? undefined : attributionOf(x.attribution),
    assumptions: x.assumptions ?? [],
    flags: x.flags ?? [],
  });
}

export class ConversionResults {
  constructor(private readonly office: Backoffice) {}

  /** A role reads a result only when the contract's x-mosaiq-roles allow its operation. */
  private allowed(operation: string): Result<never> | null {
    const state = this.office.state();
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return state.operations.includes(operation)
      ? null
      : { ok: false, error: localError('forbidden_role', 'Your role may not read experiment results.') };
  }

  /** The stored primary result, or a labelled re-read as of now. Neither is written by this client. */
  result(experimentId: unknown, basis: unknown): Promise<Result<ExperimentResultView>> {
    return checked(this.office, async () => {
      const refused = this.allowed(CONVERSION_OPERATIONS.result);
      if (refused !== null) return refused;
      const eid = id(experimentId, MAX_ID, 'The experiment');
      if (typeof basis !== 'string' || !BASES.includes(basis as ResultBasis))
        throw invalidInput('The basis is PRIMARY or RE_READ.');
      const params = new URLSearchParams({ basis });
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/experiment-results/${eid}?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: resultOf(res.data?.ExperimentResult as ResultXml) };
    });
  }
}
