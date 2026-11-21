import type {
  Paged,
  Result,
  RfmBandView,
  RfmPreviewView,
  RfmRuleSetDraft,
  RfmRuleSetView,
} from '../shared/api';
import { previewDistribution, validateDraft, type RawProfile } from '../shared/rfm-rules';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  id,
  invalidInput,
  optionalBoolean,
  pageNumber,
  pageOf,
  pageSize,
  record,
  Refused,
} from './checks';
import type { Fields } from './xml';

// RFM configuration (F28-04, #873): rfm-engine's rule sets, read and written through the transport in XML
// validated against its XSD. A rule set is immutable (ADR-0064): there is no operation here that edits or
// deletes one, and a change is a new rule set. Creating one publishes nothing: publication belongs to
// model-registry. A draft lives on the screen until it is stored; a preview scores the raw R, F and M of
// the customers rfm-engine has already scored under a stored rule set with the draft's cut-points and
// bands, so the distribution is seen before anything is written. The service is not complete yet
// (createRfmRuleSet and getRfmRuleSet are marked planned), so tests answer with its recorded examples.

const MAX_ID = Number.MAX_SAFE_INTEGER;
const PROFILE_PAGE = 100;
/** A preview reads every scored profile; a base larger than this is refused rather than half counted. */
export const PREVIEW_LIMIT = 20000;

const OPS = {
  list: 'listRfmRuleSets',
  get: 'getRfmRuleSet',
  create: 'createRfmRuleSet',
  profiles: 'listRfmProfiles',
} as const;

interface BandXml {
  label_code: string;
  r_min: string;
  r_max: string;
  f_min: string;
  f_max: string;
  m_min: string;
  m_max: string;
}

interface RuleSetXml {
  rule_set_id: string;
  name: string;
  window_days: string;
  recency_boundaries: string[];
  frequency_boundaries: string[];
  monetary_boundaries: string[];
  bands: BandXml[];
  fallback_label_code: string;
  published: string;
  created_at: string;
  created_by?: string;
}

interface ProfileXml {
  state: string;
  r_days?: string;
  f_count?: string;
  m_total?: string;
}

const truthy = (v: string | undefined): boolean => v === 'true' || v === '1';

function bandOf(b: BandXml): RfmBandView {
  return {
    label_code: b.label_code,
    r_min: Number(b.r_min),
    r_max: Number(b.r_max),
    f_min: Number(b.f_min),
    f_max: Number(b.f_max),
    m_min: Number(b.m_min),
    m_max: Number(b.m_max),
  };
}

// One rule set answers as its governed record (F22-09), which carries the fields of the listed
// rule set and more; an older service answers the listed form.
function recordOf(data: Record<string, unknown> | undefined): RuleSetXml {
  return (data?.RfmRuleSetDetail ?? data?.RfmRuleSet) as RuleSetXml;
}

export function ruleSetOf(x: RuleSetXml): RfmRuleSetView {
  return {
    rule_set_id: Number(x.rule_set_id),
    name: x.name,
    window_days: Number(x.window_days),
    recency_boundaries: (x.recency_boundaries ?? []).map(Number),
    frequency_boundaries: (x.frequency_boundaries ?? []).map(Number),
    monetary_boundaries: x.monetary_boundaries ?? [],
    bands: (x.bands ?? []).map(bandOf),
    fallback_label_code: x.fallback_label_code,
    published: truthy(x.published),
    created_at: x.created_at,
    ...(x.created_by === undefined ? {} : { created_by: x.created_by }),
  };
}

/** A draft from the screen, checked against the contract. A refusal names the band or the window. */
export function draftOf(value: unknown): RfmRuleSetDraft {
  const d = record(value, 'The rule set');
  const shapeError = (what: string): Refused =>
    invalidInput(`The rule set's ${what} is not in the shape the contract needs.`);
  if (typeof d.name !== 'string') throw shapeError('name');
  if (typeof d.window_days !== 'number') throw shapeError('window');
  for (const key of ['recency_boundaries', 'frequency_boundaries', 'monetary_boundaries', 'bands'] as const)
    if (!Array.isArray(d[key])) throw shapeError(key.replace('_', ' '));
  if (typeof d.fallback_label_code !== 'string') throw shapeError('fallback label');
  const bands = (d.bands as unknown[]).map((b, i) => {
    const band = record(b, `Band ${i + 1}`);
    if (typeof band.label_code !== 'string') throw shapeError(`band ${i + 1}`);
    return band as unknown as RfmBandView;
  });
  const draft: RfmRuleSetDraft = {
    name: d.name,
    window_days: d.window_days,
    recency_boundaries: d.recency_boundaries as number[],
    frequency_boundaries: d.frequency_boundaries as number[],
    monetary_boundaries: d.monetary_boundaries as string[],
    bands,
    fallback_label_code: d.fallback_label_code,
  };
  const issues = validateDraft(draft);
  if (issues.length > 0)
    throw new Refused(
      localError(
        'invalid_body',
        issues.map((i) => i.message).join(' '),
        issues.map((i) => ({ field: i.field, code: i.code })),
      ),
    );
  return draft;
}

/** The request body's fields, in the order the XSD fixes. */
function fieldsOf(draft: RfmRuleSetDraft): Fields {
  return {
    name: draft.name.trim(),
    window_days: String(draft.window_days),
    recency_boundaries: draft.recency_boundaries.map(String),
    frequency_boundaries: draft.frequency_boundaries.map(String),
    monetary_boundaries: draft.monetary_boundaries,
    bands: draft.bands.map((b) => ({
      label_code: b.label_code.trim(),
      r_min: String(b.r_min),
      r_max: String(b.r_max),
      f_min: String(b.f_min),
      f_max: String(b.f_max),
      m_min: String(b.m_min),
      m_max: String(b.m_max),
    })),
    fallback_label_code: draft.fallback_label_code.trim(),
  };
}

export class RfmRules {
  constructor(private readonly office: Backoffice) {}

  private mayCall(operationId: string, doing: string): Result<never> | null {
    const state = this.office.state();
    if (state.status === 'signed_in' && state.operations.includes(operationId)) return null;
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return { ok: false, error: localError('forbidden_role', `Your role may not ${doing}.`) };
  }

  listRuleSets(query: unknown): Promise<Result<Paged<RfmRuleSetView>>> {
    return checked(this.office, async () => {
      const refused = this.mayCall(OPS.list, 'read rule sets');
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const published = optionalBoolean(q.published, 'Published');
      if (published !== undefined) params.set('published', String(published));
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/rfm-rule-sets?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.RfmRuleSets as { RfmRuleSet?: RuleSetXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.RfmRuleSet ?? []).map(ruleSetOf), page: pageOf(data) } };
    });
  }

  getRuleSet(ruleSetId: unknown): Promise<Result<RfmRuleSetView>> {
    return checked(this.office, async () => {
      const refused = this.mayCall(OPS.get, 'read a rule set');
      if (refused !== null) return refused;
      const rid = id(ruleSetId, MAX_ID, 'The rule set');
      const res = await this.office.authed({ method: 'GET', path: `/v1/rfm-rule-sets/${rid}` });
      if (!res.ok) return failed(res);
      return { ok: true, value: ruleSetOf(recordOf(res.data)) };
    });
  }

  /** Every scored customer's raw R, F and M under one stored rule set, read page by page. */
  private async scoredProfiles(ruleSetId: number): Promise<Result<RawProfile[]>> {
    const found: RawProfile[] = [];
    let page = 1;
    let seen = 0;
    for (;;) {
      const params = new URLSearchParams({
        state: 'scored',
        rule_set_id: String(ruleSetId),
        page: String(page),
        size: String(PROFILE_PAGE),
      });
      const res = await this.office.authed({ method: 'GET', path: `/v1/rfm-profiles?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.RfmProfiles as { RfmProfile?: ProfileXml[] } & Record<string, unknown>;
      const total = pageOf(data).total;
      if (total > PREVIEW_LIMIT)
        return {
          ok: false,
          error: localError(
            'too_many_profiles',
            `${total} customers are scored under this rule set; a preview reads at most ${PREVIEW_LIMIT}, so it would show a part of them.`,
          ),
        };
      const rows = data.RfmProfile ?? [];
      for (const p of rows)
        if (
          p.state === 'scored' &&
          p.r_days !== undefined &&
          p.f_count !== undefined &&
          p.m_total !== undefined
        )
          found.push({ r_days: Number(p.r_days), f_count: Number(p.f_count), m_total: p.m_total });
      seen += rows.length;
      if (rows.length === 0 || seen >= total) return { ok: true, value: found };
      page += 1;
    }
  }

  /**
   * The draft's distribution of customers per label, from the figures rfm-engine holds for the customers
   * it scored under the basis rule set. The draft is checked first, and nothing is written.
   */
  previewRuleSet(draft: unknown, basisRuleSetId: unknown): Promise<Result<RfmPreviewView>> {
    return checked(this.office, async () => {
      const readRules = this.mayCall(OPS.get, 'read a rule set');
      if (readRules !== null) return readRules;
      const readProfiles = this.mayCall(OPS.profiles, 'read customers’ profiles');
      if (readProfiles !== null) return readProfiles;
      const parsed = draftOf(draft);
      const basisId = id(basisRuleSetId, MAX_ID, 'The rule set to preview on');
      const basis = await this.getRuleSet(basisId);
      if (!basis.ok) return basis;
      const profiles = await this.scoredProfiles(basisId);
      if (!profiles.ok) return profiles;
      return {
        ok: true,
        value: {
          basis: {
            rule_set_id: basis.value.rule_set_id,
            name: basis.value.name,
            window_days: basis.value.window_days,
          },
          draft_window_days: parsed.window_days,
          customers: profiles.value.length,
          labels: previewDistribution(parsed, profiles.value),
        },
      };
    });
  }

  /** The one write: a new, immutable and unpublished rule set. The service answers for a name already used. */
  createRuleSet(draft: unknown): Promise<Result<RfmRuleSetView>> {
    return checked(this.office, async () => {
      const refused = this.mayCall(OPS.create, 'store a rule set');
      if (refused !== null) return refused;
      const parsed = draftOf(draft);
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/rfm-rule-sets',
        body: { root: 'RfmRuleSetRequest', fields: fieldsOf(parsed) },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: ruleSetOf(recordOf(res.data)) };
    });
  }
}
