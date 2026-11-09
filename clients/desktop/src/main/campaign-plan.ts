import type {
  AudienceBreakdownView,
  AudienceReasonCode,
  AudiencePreviewView,
  AudienceView,
  BudgetView,
  EligibilityView,
  ExclusionsView,
  Result,
} from '../shared/api';
import { endsSession } from '../shared/modules';
import type { Backoffice } from './backoffice';
import { refused } from './campaigns';
import { checked, FieldChecks, id, invalidInput, record } from './checks';
import type { CallResult } from './transport';
import type { FieldValue } from './xml';

// A campaign's plan: audience, budget, conditions (eligibility rules) and exclusions (F28-09, #878),
// through campaigns-service (contracts/campaigns/, F25-03). Each is a sub-resource of the campaign's
// own path, read on its own and replaced whole while the campaign is DRAFT. Every value from the
// screens is checked here against the XSD and the contract's bounds before a body is built, and
// nothing is sent when one fails. What only the service can know stays its answer, passed on with
// its code and correlation id and a plain sentence: campaign_not_draft, unknown_customer,
// unknown_label, invalid_body. The campaign's validity (starts_on, ends_on) is not here: it is the
// campaign's own and is edited through Campaigns.updateCampaign.

const MAX_CAMPAIGN_ID = 2147483647;
const MAX_CUSTOMERS = 10_000_000;
const MAX_MIN_PURCHASES = 1000;
const MAX_WINDOW_DAYS = 731;
const MAX_EXCLUDED_CUSTOMERS = 1000;
const MAX_EXCLUDED_LABELS = 20;
const LABEL_MAX = 40;
const WHOLE = /^[0-9]+$/;
const MONEY = /^[0-9]+\.[0-9]{2}$/;
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
// contracts/campaigns/campaigns.xsd Uuid: lower-case hexadecimal only.
const CUSTOMER_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

interface AudienceXml {
  campaign_id: string;
  label_code: string;
  max_customers?: string;
  audience_size: string;
  as_of: string;
}

interface BudgetXml {
  campaign_id: string;
  total_amount?: string;
  per_customer_cap?: string;
}

interface EligibilityXml {
  campaign_id: string;
  registered_on_or_before?: string;
  min_purchases?: string;
  purchase_window_days?: string;
}

interface ExclusionsXml {
  campaign_id: string;
  customer_ids?: string[];
  label_codes?: string[];
}

interface BreakdownXml {
  campaign_id: string;
  label_code: string;
  label_holders: string;
  reasons?: { code: string; excluded: string }[];
  audience_size: string;
  as_of: string;
}

interface SegmentXml {
  label_code: string;
  size: string;
}

/** Drops the members a service left out, so a view carries only what it was told. */
function present<T extends object>(view: T): T {
  return Object.fromEntries(Object.entries(view).filter(([, v]) => v !== undefined)) as T;
}

function optionalNumber(value: string | undefined): number | undefined {
  return value === undefined ? undefined : Number(value);
}

export function audienceOf(x: AudienceXml): AudienceView {
  return present({
    campaign_id: Number(x.campaign_id),
    label_code: x.label_code,
    max_customers: optionalNumber(x.max_customers),
    audience_size: Number(x.audience_size),
    as_of: x.as_of,
  });
}

export function budgetOf(x: BudgetXml): BudgetView {
  return present({
    campaign_id: Number(x.campaign_id),
    total_amount: x.total_amount,
    per_customer_cap: x.per_customer_cap,
  });
}

export function eligibilityOf(x: EligibilityXml): EligibilityView {
  return present({
    campaign_id: Number(x.campaign_id),
    registered_on_or_before: x.registered_on_or_before,
    min_purchases: optionalNumber(x.min_purchases),
    purchase_window_days: optionalNumber(x.purchase_window_days),
  });
}

export function exclusionsOf(x: ExclusionsXml): ExclusionsView {
  return {
    campaign_id: Number(x.campaign_id),
    customer_ids: x.customer_ids ?? [],
    label_codes: x.label_codes ?? [],
  };
}

/** The breakdown as the contract fixes it: six reasons, each a code the contract names. */
export function breakdownOf(x: BreakdownXml): AudienceBreakdownView {
  return {
    campaign_id: Number(x.campaign_id),
    label_code: x.label_code,
    label_holders: Number(x.label_holders),
    reasons: (x.reasons ?? []).map((r) => ({
      code: r.code as AudienceReasonCode,
      excluded: Number(r.excluded),
    })),
    audience_size: Number(x.audience_size),
    as_of: x.as_of,
  };
}

/** A draft member as typed; anything but text is a defect of the caller, not a value to correct. */
function typed(draft: Record<string, unknown>, field: string, label: string): string {
  const value = draft[field];
  if (typeof value !== 'string') throw invalidInput(`${label} is incomplete.`);
  return value.trim();
}

/** A whole number from 1 to max; empty text is absent. */
function whole(f: FieldChecks, field: string, text: string, max: number): string | undefined {
  if (text === '') return undefined;
  if (!WHOLE.test(text)) {
    f.details.push({ field, code: 'invalid_format' });
    return undefined;
  }
  const n = Number(text);
  if (n < 1 || n > max) {
    f.details.push({ field, code: 'out_of_range' });
    return undefined;
  }
  return String(n);
}

/** A non-negative amount with exactly two decimals, as the contract's Money; empty text is absent. */
function money(f: FieldChecks, field: string, text: string): string | undefined {
  if (text === '') return undefined;
  if (!MONEY.test(text)) {
    f.details.push({ field, code: 'invalid_format' });
    return undefined;
  }
  return text;
}

/** Cents as a whole number, so two amounts compare without floating point. */
function cents(amount: string): bigint {
  return BigInt(amount.replace('.', ''));
}

/** A real calendar date: 2026-02-31 is refused, which Date.parse alone would not. */
function calendarDate(f: FieldChecks, field: string, text: string): string | undefined {
  if (text === '') return undefined;
  const m = DATE.exec(text);
  const day = m === null ? null : new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (
    m === null ||
    day === null ||
    day.getUTCFullYear() !== Number(m[1]) ||
    day.getUTCMonth() !== Number(m[2]) - 1 ||
    day.getUTCDate() !== Number(m[3])
  ) {
    f.details.push({ field, code: 'invalid_format' });
    return undefined;
  }
  return text;
}

/** Only the members that were given, in the XSD's order; an empty body removes the whole setting. */
function given(
  members: Record<string, string | string[] | undefined>,
): Record<string, FieldValue | string[]> {
  return Object.fromEntries(Object.entries(members).filter(([, v]) => v !== undefined)) as Record<
    string,
    FieldValue | string[]
  >;
}

export class CampaignPlan {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  private path(campaignId: unknown, part: string): string {
    return `/v1/campaigns/${id(campaignId, MAX_CAMPAIGN_ID, 'The campaign')}/${part}`;
  }

  // ---------- audience ----------

  getAudience(campaignId: unknown): Promise<Result<AudienceView>> {
    return this.run(async () =>
      this.audienceResult(
        await this.office.authed({ method: 'GET', path: this.path(campaignId, 'audience') }),
      ),
    );
  }

  /** Replaces the cap on the audience; empty text removes it. */
  putAudience(campaignId: unknown, draft: unknown): Promise<Result<AudienceView>> {
    return this.run(async () => {
      const path = this.path(campaignId, 'audience');
      const d = record(draft, 'The audience');
      const f = new FieldChecks();
      const max = whole(f, 'max_customers', typed(d, 'max_customers', 'The audience'), MAX_CUSTOMERS);
      f.done();
      const res = await this.office.authed({
        method: 'PUT',
        path,
        body: { root: 'PutCampaignAudienceRequest', fields: given({ max_customers: max }) },
      });
      return this.audienceResult(res);
    });
  }

  // ---------- budget ----------

  getBudget(campaignId: unknown): Promise<Result<BudgetView>> {
    return this.run(async () =>
      this.budgetResult(await this.office.authed({ method: 'GET', path: this.path(campaignId, 'budget') })),
    );
  }

  /** Replaces the budget; empty text removes that limit. The per-customer limit may not exceed the total. */
  putBudget(campaignId: unknown, draft: unknown): Promise<Result<BudgetView>> {
    return this.run(async () => {
      const path = this.path(campaignId, 'budget');
      const d = record(draft, 'The budget');
      const f = new FieldChecks();
      const total = money(f, 'total_amount', typed(d, 'total_amount', 'The budget'));
      const cap = money(f, 'per_customer_cap', typed(d, 'per_customer_cap', 'The budget'));
      if (total !== undefined && cap !== undefined && cents(cap) > cents(total))
        f.details.push({ field: 'per_customer_cap', code: 'exceeds_total_amount' });
      f.done();
      const res = await this.office.authed({
        method: 'PUT',
        path,
        body: {
          root: 'PutCampaignBudgetRequest',
          fields: given({ total_amount: total, per_customer_cap: cap }),
        },
      });
      return this.budgetResult(res);
    });
  }

  // ---------- conditions (eligibility rules) ----------

  getEligibility(campaignId: unknown): Promise<Result<EligibilityView>> {
    return this.run(async () =>
      this.eligibilityResult(
        await this.office.authed({ method: 'GET', path: this.path(campaignId, 'eligibility-rules') }),
      ),
    );
  }

  /** Replaces the rules; empty text removes one. The two purchase members are given together or not at all. */
  putEligibility(campaignId: unknown, draft: unknown): Promise<Result<EligibilityView>> {
    return this.run(async () => {
      const path = this.path(campaignId, 'eligibility-rules');
      const d = record(draft, 'The conditions');
      const f = new FieldChecks();
      const registered = calendarDate(
        f,
        'registered_on_or_before',
        typed(d, 'registered_on_or_before', 'The conditions'),
      );
      const rawMin = typed(d, 'min_purchases', 'The conditions');
      const rawDays = typed(d, 'purchase_window_days', 'The conditions');
      const min = whole(f, 'min_purchases', rawMin, MAX_MIN_PURCHASES);
      const days = whole(f, 'purchase_window_days', rawDays, MAX_WINDOW_DAYS);
      // Together, judged on what was typed: a value refused above already names its own field.
      if (rawMin !== '' && rawDays === '')
        f.details.push({ field: 'purchase_window_days', code: 'required_together' });
      if (rawDays !== '' && rawMin === '')
        f.details.push({ field: 'min_purchases', code: 'required_together' });
      f.done();
      const res = await this.office.authed({
        method: 'PUT',
        path,
        body: {
          root: 'PutCampaignEligibilityRulesRequest',
          fields: given({
            registered_on_or_before: registered,
            min_purchases: min,
            purchase_window_days: days,
          }),
        },
      });
      return this.eligibilityResult(res);
    });
  }

  // ---------- exclusions ----------

  getExclusions(campaignId: unknown): Promise<Result<ExclusionsView>> {
    return this.run(async () =>
      this.exclusionsResult(
        await this.office.authed({ method: 'GET', path: this.path(campaignId, 'exclusions') }),
      ),
    );
  }

  /**
   * Replaces both lists. At most 1000 customer ids (lower-case UUIDs) and 20 label codes, each without
   * duplicates. Whether an id or a label exists is the service's to say: unknown_customer, unknown_label.
   */
  putExclusions(campaignId: unknown, draft: unknown): Promise<Result<ExclusionsView>> {
    return this.run(async () => {
      const path = this.path(campaignId, 'exclusions');
      const d = record(draft, 'The exclusions');
      const f = new FieldChecks();
      // An id is the same customer in either case; the XSD accepts lower case only.
      const customers = this.list(f, 'customer_ids', d.customer_ids, MAX_EXCLUDED_CUSTOMERS, true, (v) =>
        CUSTOMER_ID.test(v),
      );
      const labels = this.list(
        f,
        'label_codes',
        d.label_codes,
        MAX_EXCLUDED_LABELS,
        false,
        (v) => v.length <= LABEL_MAX,
      );
      f.done();
      const res = await this.office.authed({
        method: 'PUT',
        path,
        body: {
          root: 'PutCampaignExclusionsRequest',
          fields: { customer_ids: customers, label_codes: labels },
        },
      });
      return this.exclusionsResult(res);
    });
  }

  /** A list of text values: each non-empty and well formed, none twice, no more than max. */
  private list(
    f: FieldChecks,
    field: string,
    value: unknown,
    max: number,
    lowerCase: boolean,
    wellFormed: (item: string) => boolean,
  ): string[] {
    if (!Array.isArray(value) || value.some((v) => typeof v !== 'string'))
      throw invalidInput('The exclusions are incomplete.');
    const items = (value as string[]).map((v) => (lowerCase ? v.trim().toLowerCase() : v.trim()));
    if (items.some((v) => v === '')) f.details.push({ field, code: 'required' });
    else if (items.some((v) => !wellFormed(v)))
      f.details.push({ field, code: field === 'customer_ids' ? 'invalid_format' : 'too_long' });
    else if (new Set(items).size !== items.length) f.details.push({ field, code: 'duplicate' });
    else if (items.length > max) f.details.push({ field, code: 'max_items' });
    return items;
  }

  // ---------- preview ----------

  /**
   * The audience with the customers it leaves out counted by reason, each under the first reason that
   * applies (getCampaignAudienceBreakdown). Read through the preview, not through the bridge on its own.
   */
  getAudienceBreakdown(campaignId: unknown): Promise<Result<AudienceBreakdownView>> {
    return this.run(async () =>
      this.breakdownResult(
        await this.office.authed({ method: 'GET', path: this.path(campaignId, 'audience/breakdown') }),
      ),
    );
  }

  /**
   * What the audience is now, and why it is that size: the breakdown by reason, and the audience, the
   * conditions and the exclusions it was counted under, to word each reason.
   *
   * The breakdown is campaigns-service's, and an operation published ahead of its implementation
   * (ADR-0073) may not be answered yet. When it is not, for any reason but an ended session, the preview
   * falls back to what the audience read says: its one total, and (from segments-service, ADR-0041: the
   * client composes related data itself) how many customers hold the label, so the number left out is a
   * difference that is not split by reason. The screen says so, and carries why in `breakdown_error`.
   * The holders are optional in that fallback: a segments-service that refuses the read, or no longer
   * lists the label, leaves the preview without them, as names.ts leaves a name unavailable.
   */
  preview(campaignId: unknown): Promise<Result<AudiencePreviewView>> {
    return this.run(async () => {
      const breakdown = await this.getAudienceBreakdown(campaignId);
      if (!breakdown.ok && endsSession(breakdown.error.code)) return breakdown;
      const audience = await this.getAudience(campaignId);
      if (!audience.ok) return audience;
      const eligibility = await this.getEligibility(campaignId);
      if (!eligibility.ok) return eligibility;
      const exclusions = await this.getExclusions(campaignId);
      if (!exclusions.ok) return exclusions;
      const rules = {
        audience: audience.value,
        eligibility: eligibility.value,
        exclusions: exclusions.value,
      };
      if (breakdown.ok) {
        const view: AudiencePreviewView = { ...rules, breakdown: breakdown.value };
        return { ok: true, value: view };
      }
      const holders = await this.labelHolders(audience.value.label_code);
      const view: AudiencePreviewView = present({
        ...rules,
        breakdown_error: breakdown.error,
        label_holders: holders,
      });
      return { ok: true, value: view };
    });
  }

  private async labelHolders(labelCode: string): Promise<number | undefined> {
    const res = await this.office.authed({ method: 'GET', path: '/v1/segments' });
    if (!res.ok) return undefined;
    const d = res.data?.Segments as { Segment?: SegmentXml[] };
    const found = (d.Segment ?? []).find((s) => s.label_code === labelCode);
    return found === undefined ? undefined : Number(found.size);
  }

  // ---------- answers ----------

  private audienceResult(res: CallResult): Result<AudienceView> {
    if (!res.ok) return refused(res);
    return { ok: true, value: audienceOf(res.data?.CampaignAudience as AudienceXml) };
  }

  private breakdownResult(res: CallResult): Result<AudienceBreakdownView> {
    if (!res.ok) return refused(res);
    return { ok: true, value: breakdownOf(res.data?.CampaignAudienceBreakdown as BreakdownXml) };
  }

  private budgetResult(res: CallResult): Result<BudgetView> {
    if (!res.ok) return refused(res);
    return { ok: true, value: budgetOf(res.data?.CampaignBudget as BudgetXml) };
  }

  private eligibilityResult(res: CallResult): Result<EligibilityView> {
    if (!res.ok) return refused(res);
    return { ok: true, value: eligibilityOf(res.data?.CampaignEligibilityRules as EligibilityXml) };
  }

  private exclusionsResult(res: CallResult): Result<ExclusionsView> {
    if (!res.ok) return refused(res);
    return { ok: true, value: exclusionsOf(res.data?.CampaignExclusions as ExclusionsXml) };
  }
}
