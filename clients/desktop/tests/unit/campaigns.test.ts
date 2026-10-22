import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { Campaigns } from '../../src/main/campaigns';
import { XsdValidator } from '../../src/main/xml';
import { bundle, claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// Campaigns and their transitions (F16-08, #486), against the recorded examples of contracts/campaigns/.

const NS = 'https://mosaiq.maxthecoder.online/xml/campaigns/v1';
const GOOD = {
  name: 'Holiday Loyalty Preview',
  label_code: 'LOYAL',
  starts_on: '2026-11-01',
  ends_on: '2026-12-31',
};

async function signedIn(role: string, responder: Responder): Promise<{ campaigns: Campaigns; seen: Seen[] }> {
  const { office, seen } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
      return responder(r);
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  seen.length = 0;
  return { campaigns: new Campaigns(office), seen };
}

const answer =
  (status: number, file: string): Responder =>
  () => ({ status, body: example('campaigns', file) });

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

/** The element names and values of a request body, in order, so a recorded request compares to a sent one. */
const fieldsOf = (xml: string): [string, string][] =>
  [...xml.matchAll(/<([a-z_]+)>([^<]*)<\/\1>/g)].map((m) => [m[1] as string, m[2] as string]);
const recorded = (name: string): [string, string][] => fieldsOf(example('campaigns', name));

describe('reading', () => {
  it('lists with status, label and paging, with the moves each campaign allows', async () => {
    const { campaigns, seen } = await signedIn('AUDITOR', answer(200, 'campaigns'));
    const res = await campaigns.listCampaigns({ status: 'DRAFT', label_code: ' LOYAL ', page: 2, size: 50 });
    expect(paths(seen)).toEqual([
      'GET /api/campaigns/v1/campaigns?status=DRAFT&label_code=LOYAL&page=2&size=50',
    ]);
    expect(res.ok && res.value.items.map((c) => [c.campaign_id, c.status, c.allowed_transitions])).toEqual([
      [301, 'DRAFT', ['ACTIVE', 'CANCELLED']],
      [302, 'ACTIVE', ['FINISHED', 'CANCELLED']],
    ]);
    expect(res.ok && res.value.page).toEqual({ page: 1, size: 20, total: 2 });
  });

  it('opens one campaign, and one with no moves left has an empty list', async () => {
    const { campaigns, seen } = await signedIn('ANALYST', answer(200, 'campaign'));
    const res = await campaigns.getCampaign(301);
    expect(paths(seen)).toEqual(['GET /api/campaigns/v1/campaigns/301']);
    expect(res.ok && res.value).toMatchObject({
      campaign_id: 301,
      label_code: 'LOYAL',
      ends_on: '2026-12-31',
    });
    const body = example('campaigns', 'campaign').replace(
      /<allowed_transitions>.*<\/allowed_transitions>\s*/g,
      '',
    );
    const { campaigns: done } = await signedIn('ANALYST', () => ({ status: 200, body }));
    const bare = await done.getCampaign(301);
    expect(bare.ok && bare.value.allowed_transitions).toEqual([]);
  });

  it('reads the label names from segments-service', async () => {
    const segments = readFileSync(
      join(__dirname, '..', '..', '..', '..', 'contracts', 'segments', 'examples', 'list-segments.xml'),
      'utf8',
    );
    const { campaigns, seen } = await signedIn('MARKETING', () => ({ status: 200, body: segments }));
    const res = await campaigns.listLabels();
    expect(paths(seen)).toEqual(['GET /api/segments/v1/segments']);
    expect(res.ok && res.value[1]).toEqual({ label_code: 'LOYAL', name: 'Loyal' });
    expect(res.ok && res.value).toHaveLength(6);
  });

  it('shows an unknown campaign as campaign_not_found, as returned', async () => {
    const { campaigns } = await signedIn('ADMIN', answer(404, 'campaign-not-found'));
    expect(await campaigns.getCampaign(9999)).toMatchObject({
      ok: false,
      error: { code: 'campaign_not_found' },
    });
  });

  it('refuses a malformed id, status or query before sending', async () => {
    const { campaigns, seen } = await signedIn('ADMIN', answer(200, 'campaign'));
    expect(await campaigns.getCampaign(0)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(await campaigns.getCampaign('1/transitions' as unknown as number)).toMatchObject({ ok: false });
    expect(await campaigns.listCampaigns({ status: 'ARCHIVED' })).toMatchObject({ ok: false });
    expect(await campaigns.listCampaigns({ label_code: 'x'.repeat(41) })).toMatchObject({ ok: false });
    expect(seen).toHaveLength(0);
  });
});

describe('creating', () => {
  it('sends the recorded request and shows the campaign as DRAFT', async () => {
    const { campaigns, seen } = await signedIn('MARKETING', answer(201, 'campaign'));
    const res = await campaigns.createCampaign(GOOD);
    expect(paths(seen)).toEqual(['POST /api/campaigns/v1/campaigns']);
    expect(fieldsOf(seen[0]!.body as string)).toEqual(recorded('create-campaign.request'));
    expect(res.ok && res.value.status).toBe('DRAFT');
  });

  it('sends an end before the start as typed and shows ends_before_start with its field', async () => {
    const { campaigns, seen } = await signedIn('ADMIN', answer(422, 'ends-before-start'));
    const res = await campaigns.createCampaign({ ...GOOD, ends_on: '2026-10-01' });
    expect(seen).toHaveLength(1);
    expect(res).toMatchObject({
      ok: false,
      error: { code: 'ends_before_start', details: [{ field: 'ends_on', code: 'before_start' }] },
    });
  });

  it('shows duplicate_draft and unknown_label as returned', async () => {
    const dup = await signedIn('ADMIN', answer(409, 'duplicate-draft'));
    expect(await dup.campaigns.createCampaign(GOOD)).toMatchObject({
      ok: false,
      error: { code: 'duplicate_draft', correlation_id: 'corr-campaign-409a' },
    });
    const label = await signedIn('ADMIN', answer(422, 'unknown-label'));
    expect(await label.campaigns.createCampaign({ ...GOOD, label_code: 'NOPE' })).toMatchObject({
      ok: false,
      error: { code: 'unknown_label', details: [{ field: 'label_code', code: 'unknown' }] },
    });
  });

  it('refuses missing and malformed values before sending, naming the field', async () => {
    const { campaigns, seen } = await signedIn('ADMIN', answer(201, 'campaign'));
    const res = await campaigns.createCampaign({
      name: '',
      label_code: 'x'.repeat(41),
      starts_on: '01/11/2026',
      ends_on: '',
    });
    expect(!res.ok && res.error.details).toEqual([
      { field: 'name', code: 'required' },
      { field: 'label_code', code: 'too_long' },
      { field: 'starts_on', code: 'invalid_format' },
      { field: 'ends_on', code: 'required' },
    ]);
    expect(seen).toHaveLength(0);
  });

  it('shows 403 forbidden_role to a read-only role, as returned', async () => {
    const { campaigns } = await signedIn('ANALYST', () => ({
      status: 403,
      body: example('segments', 'forbidden-role'),
    }));
    expect(await campaigns.createCampaign(GOOD)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
  });
});

describe('editing', () => {
  it('sends only the changed fields, in the XSD order', async () => {
    const { campaigns, seen } = await signedIn('MARKETING', answer(200, 'campaign'));
    await campaigns.updateCampaign(301, { ends_on: '2027-01-15', name: 'Holiday Loyalty Launch' });
    expect(paths(seen)).toEqual(['PATCH /api/campaigns/v1/campaigns/301']);
    expect(fieldsOf(seen[0]!.body as string)).toEqual(recorded('update-campaign.request'));
  });

  it('accepts every field the XSD allows alone', async () => {
    const { campaigns, seen } = await signedIn('ADMIN', answer(200, 'campaign'));
    for (const changes of [
      { name: 'N' },
      { label_code: 'LOST' },
      { starts_on: '2026-11-02' },
      { ends_on: '2026-12-30' },
    ])
      expect((await campaigns.updateCampaign(301, changes)).ok).toBe(true);
    expect(seen).toHaveLength(4);
  });

  it('refuses an empty change before sending', async () => {
    const { campaigns, seen } = await signedIn('ADMIN', answer(200, 'campaign'));
    expect(await campaigns.updateCampaign(301, {})).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(seen).toHaveLength(0);
  });

  it('shows campaign_not_draft, ends_before_start and campaign_not_found as returned', async () => {
    for (const [status, file, code] of [
      [409, 'campaign-not-draft', 'campaign_not_draft'],
      [422, 'ends-before-start', 'ends_before_start'],
      [404, 'campaign-not-found', 'campaign_not_found'],
    ] as const) {
      const { campaigns } = await signedIn('ADMIN', answer(status, file));
      expect(await campaigns.updateCampaign(301, { name: 'X' })).toMatchObject({
        ok: false,
        error: { code },
      });
    }
  });
});

describe('transitions', () => {
  it.each([
    ['DRAFT', 'ACTIVE', 'draft-to-active'],
    ['DRAFT', 'CANCELLED', 'draft-to-cancelled'],
    ['ACTIVE', 'FINISHED', 'active-to-finished'],
    ['ACTIVE', 'CANCELLED', 'active-to-cancelled'],
  ] as const)('sends the recorded request for %s to %s', async (from, to, file) => {
    const { campaigns, seen } = await signedIn('MARKETING', answer(200, file));
    const res = await campaigns.transitionCampaign(301, to, from);
    expect(paths(seen)).toEqual(['POST /api/campaigns/v1/campaigns/301/transitions']);
    expect(fieldsOf(seen[0]!.body as string)).toEqual(recorded(`${file}.request`));
    expect(res.ok && res.value.status).toBe(to);
  });

  it.each([
    [409, 'illegal-transition', 'illegal_transition'],
    [409, 'status-changed', 'status_changed'],
    [409, 'campaign-ended', 'campaign_ended'],
    [409, 'experiment-not-ready', 'experiment_not_ready'],
    [404, 'campaign-not-found', 'campaign_not_found'],
  ] as const)('shows %s %s as returned', async (status, file, code) => {
    const { campaigns } = await signedIn('ADMIN', answer(status, file));
    expect(await campaigns.transitionCampaign(301, 'ACTIVE', 'DRAFT')).toMatchObject({
      ok: false,
      error: { code },
    });
  });

  it('sends an illegal move as asked, so the service refuses it, and refuses an unknown status locally', async () => {
    const { campaigns, seen } = await signedIn('ADMIN', answer(409, 'illegal-transition'));
    expect(await campaigns.transitionCampaign(301, 'FINISHED', 'DRAFT')).toMatchObject({ ok: false });
    expect(seen).toHaveLength(1);
    expect(await campaigns.transitionCampaign(301, 'DONE', 'DRAFT')).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(await campaigns.transitionCampaign(301, 'ACTIVE', undefined)).toMatchObject({ ok: false });
    expect(seen).toHaveLength(1);
  });
});

describe('the XSD', () => {
  it('accepts the recorded requests and refuses a status it does not list', async () => {
    const validator = new XsdValidator(bundle);
    for (const f of ['create-campaign.request', 'update-campaign.request', 'draft-to-active.request'])
      expect((await validator.validate(example('campaigns', f), NS)).valid).toBe(true);
    expect((await validator.validate(example('campaigns', 'campaign-status.invalid'), NS)).valid).toBe(false);
  });
});
