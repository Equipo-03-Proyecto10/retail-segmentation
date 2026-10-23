import type { CampaignStatus, CampaignView, LabelView, Paged, Result } from '../shared/api';
import type { Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  id,
  invalidInput,
  optionalDate,
  optionalText,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import type { CallResult } from './transport';
import type { FieldValue } from './xml';

// Campaigns and their transitions (F16-08, #486). Every value from the screens
// is checked here before a path or a body is built from it. The lifecycle is
// campaigns-service's: nothing here decides whether a move is legal or whether a
// start precedes an end, so ends_before_start, duplicate_draft, illegal_transition,
// status_changed, campaign_ended, experiment_not_ready and campaign_not_draft are
// the service's answers, passed on as returned.

const MAX_CAMPAIGN_ID = 2147483647;
const NAME_MAX = 120;
const LABEL_MAX = 40;
const STATUSES: readonly CampaignStatus[] = ['DRAFT', 'ACTIVE', 'FINISHED', 'CANCELLED'];

interface CampaignXml {
  campaign_id: string;
  name: string;
  label_code: string;
  starts_on: string;
  ends_on: string;
  status: CampaignStatus;
  allowed_transitions?: CampaignStatus[];
}

interface SegmentXml {
  label_code: string;
  name: string;
}

function campaignOf(x: CampaignXml): CampaignView {
  return {
    campaign_id: Number(x.campaign_id),
    name: x.name,
    label_code: x.label_code,
    starts_on: x.starts_on,
    ends_on: x.ends_on,
    status: x.status,
    allowed_transitions: x.allowed_transitions ?? [],
  };
}

function status(value: unknown, label: string): CampaignStatus {
  if (typeof value !== 'string' || !STATUSES.includes(value as CampaignStatus))
    throw invalidInput(`${label} must be one of ${STATUSES.join(', ')}.`);
  return value as CampaignStatus;
}

/** A required calendar date; a missing or malformed one names its field. */
function date(f: FieldChecks, field: string, value: unknown): string {
  if (typeof value !== 'string' || value === '') {
    f.details.push({ field, code: 'required' });
    return '';
  }
  try {
    return optionalDate(value, field) ?? '';
  } catch {
    f.details.push({ field, code: 'invalid_format' });
    return '';
  }
}

export class Campaigns {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  listCampaigns(query: unknown): Promise<Result<Paged<CampaignView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      if (q.status !== undefined) params.set('status', status(q.status, 'The status'));
      const label = optionalText(q.label_code, 'The label');
      if (label?.trim()) {
        if (label.length > LABEL_MAX) throw invalidInput('The label is too long.');
        params.set('label_code', label.trim());
      }
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/campaigns?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.Campaigns as { Campaign?: CampaignXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.Campaign ?? []).map(campaignOf), page: pageOf(data) } };
    });
  }

  getCampaign(campaignId: unknown): Promise<Result<CampaignView>> {
    return this.run(async () => {
      const cid = id(campaignId, MAX_CAMPAIGN_ID, 'The campaign');
      const res = await this.office.authed({ method: 'GET', path: `/v1/campaigns/${cid}` });
      return this.campaignResult(res);
    });
  }

  /** The label vocabulary, from segments-service (campaigns-service stores only the code). */
  listLabels(): Promise<Result<LabelView[]>> {
    return this.run(async () => {
      const res = await this.office.authed({ method: 'GET', path: '/v1/segments' });
      if (!res.ok) return failed(res);
      const data = res.data?.Segments as { Segment?: SegmentXml[] };
      return {
        ok: true,
        value: (data.Segment ?? []).map((s) => ({ label_code: s.label_code, name: s.name })),
      };
    });
  }

  createCampaign(draft: unknown): Promise<Result<CampaignView>> {
    return this.run(async () => {
      const d = record(draft, 'The campaign');
      const f = new FieldChecks();
      const fields: Record<string, FieldValue> = {
        name: f.text('name', d.name, NAME_MAX),
        label_code: f.text('label_code', d.label_code, LABEL_MAX),
        starts_on: date(f, 'starts_on', d.starts_on),
        ends_on: date(f, 'ends_on', d.ends_on),
      };
      f.done();
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/campaigns',
        body: { root: 'CreateCampaignRequest', fields },
      });
      return this.campaignResult(res);
    });
  }

  /** Only the fields that changed are sent. Outside DRAFT the service answers 409 campaign_not_draft. */
  updateCampaign(campaignId: unknown, changes: unknown): Promise<Result<CampaignView>> {
    return this.run(async () => {
      const cid = id(campaignId, MAX_CAMPAIGN_ID, 'The campaign');
      const c = record(changes, 'The changes');
      const f = new FieldChecks();
      // The XSD fixes this order: name, label_code, starts_on, ends_on.
      const fields: Record<string, FieldValue> = {};
      if (c.name !== undefined) fields.name = f.text('name', c.name, NAME_MAX);
      if (c.label_code !== undefined) fields.label_code = f.text('label_code', c.label_code, LABEL_MAX);
      if (c.starts_on !== undefined) fields.starts_on = date(f, 'starts_on', c.starts_on);
      if (c.ends_on !== undefined) fields.ends_on = date(f, 'ends_on', c.ends_on);
      f.done();
      if (Object.keys(fields).length === 0) throw invalidInput('Nothing was changed.');
      const res = await this.office.authed({
        method: 'PATCH',
        path: `/v1/campaigns/${cid}`,
        body: { root: 'UpdateCampaignRequest', fields },
      });
      return this.campaignResult(res);
    });
  }

  /** The only write for a campaign's status. Whether the move is legal is the service's to say. */
  transitionCampaign(
    campaignId: unknown,
    to: unknown,
    expectedStatus: unknown,
  ): Promise<Result<CampaignView>> {
    return this.run(async () => {
      const cid = id(campaignId, MAX_CAMPAIGN_ID, 'The campaign');
      const fields = {
        to: status(to, 'The new status'),
        expected_status: status(expectedStatus, 'The expected status'),
      };
      const res = await this.office.authed({
        method: 'POST',
        path: `/v1/campaigns/${cid}/transitions`,
        body: { root: 'TransitionCampaignRequest', fields },
      });
      return this.campaignResult(res);
    });
  }

  private campaignResult(res: CallResult): Result<CampaignView> {
    if (!res.ok) return failed(res);
    return { ok: true, value: campaignOf(res.data?.Campaign as CampaignXml) };
  }
}
