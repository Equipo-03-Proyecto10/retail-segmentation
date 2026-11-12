import { describe, expect, it } from 'vitest';
import type { Backoffice } from '../../src/main/backoffice';
import { Experiments } from '../../src/main/experiments';
import type { ExperimentView } from '../../src/shared/api';
import {
  armName,
  formatCount,
  hasControl,
  metricFigure,
  NO_EFFECT_STATEMENT,
  readDesign,
  sampleSizeOf,
  sampleSizeText,
  shiftDecimal,
} from '../../src/shared/experiments';
import { EXPERIMENT_OPERATIONS, visibleGroups } from '../../src/shared/modules';
import { claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// The A/B experiment module (F28-10, #879), against the recorded examples of contracts/experimentation/.
// The experimentation operations are published ahead of their implementation (ADR-0073), so these
// examples are what the client is built and tested against until the service answers.

const EX = (name: string): string => example('experimentation', name);

async function signedIn(
  role: string,
  responder: Responder,
): Promise<{ experiments: Experiments; seen: Seen[]; office: Backoffice }> {
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
  return { experiments: new Experiments(office), seen, office };
}

const answer =
  (status: number, file: string): Responder =>
  () => ({ status, body: EX(file) });

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

/** A request body without its declaration or the whitespace between tags, to compare with a recorded one. */
const squeezed = (xml: string): string =>
  xml
    .replace(/<\?xml[^>]*\?>/, '')
    .replace(/>\s+</g, '><')
    .trim();

const PLAN = {
  name: 'Win-back offer for at-risk customers',
  campaign_id: 4,
  primary_metric: 'CONVERSION',
  starts_on: '2026-10-01',
  conversion_window_days: 14,
  minimum_detectable_effect: '0.02',
  arms: [
    { kind: 'CONTROL', name: 'No offer', treatment_description: 'Held back: receives no win-back offer.' },
    {
      kind: 'TREATMENT',
      name: '10 percent win-back voucher',
      treatment_description: 'Offered a 10 percent voucher on the next purchase.',
    },
  ],
};

describe('who may call what', () => {
  it('offers the module to the roles that read experiments and to no one else', async () => {
    const names = (operations: string[]) =>
      visibleGroups(operations).flatMap((g) => g.modules.map((m) => m.name));
    expect(names(['listExperiments'])).toContain('experiments');
    expect(names(['getCurrentAssignment'])).not.toContain('experiments');
    // The module's four operations are the contract's, with the contract's roles.
    const writer = await signedIn('MARKETING', answer(200, 'experiments-none'));
    const reader = await signedIn('ANALYST', answer(200, 'experiments-none'));
    const floor = await signedIn('STORE_MANAGER', answer(200, 'experiments-none'));
    const operations = (office: { state(): { status: string } }): string[] =>
      (office.state() as { operations?: string[] }).operations ?? [];
    expect(operations(writer.office)).toEqual(expect.arrayContaining(Object.values(EXPERIMENT_OPERATIONS)));
    expect(operations(reader.office)).toEqual(
      expect.arrayContaining([EXPERIMENT_OPERATIONS.list, EXPERIMENT_OPERATIONS.get]),
    );
    expect(operations(reader.office)).not.toContain(EXPERIMENT_OPERATIONS.plan);
    expect(operations(reader.office)).not.toContain(EXPERIMENT_OPERATIONS.assign);
    for (const op of Object.values(EXPERIMENT_OPERATIONS)) expect(operations(floor.office)).not.toContain(op);
  });

  it('refuses a write for a role the contract does not allow, before anything is sent', async () => {
    const { experiments, seen } = await signedIn('ANALYST', answer(200, 'experiment-planned'));
    expect(await experiments.plan(PLAN)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(await experiments.assign(15, true)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
    expect(seen).toHaveLength(0);
    // A role that may read still reads.
    expect((await experiments.get(15)).ok).toBe(true);
  });

  it('refuses a read for a role the contract does not allow', async () => {
    const { experiments, seen } = await signedIn('STORE_MANAGER', answer(200, 'experiments-page'));
    expect(await experiments.list({})).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(await experiments.get(12)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(seen).toHaveLength(0);
  });
});

describe('reading experiments', () => {
  it('lists in XML with the filters and the paging, and reads each plan through the XSD', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(200, 'experiments-page'));
    const res = await experiments.list({
      state: 'ANALYZED',
      data_origin: 'OBSERVED',
      campaign_id: 7,
      page: 2,
      size: 50,
    });
    expect(paths(seen)).toEqual(
      [
        '/api/experimentation/v1/experiments?campaign_id=7&state=ANALYZED&data_origin=OBSERVED&page=2&size=50',
      ].map((p) => `GET ${p}`),
    );
    expect(seen[0]?.headers.Accept).toBe('application/xml');
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.items.map((e) => [e.experiment_id, e.state, e.design_kind])).toEqual([
      [16, 'ANALYZED', 'OFFER_COMPARISON'],
      [12, 'ANALYZED', 'CONTROLLED'],
      [3, 'ASSIGNED', 'CONTROLLED'],
    ]);
    expect(res.value.items[1]?.arms.map((a) => a.name)).toEqual(['No offer', '10 percent win-back voucher']);
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 3 });
  });

  it('reads an empty page', async () => {
    const { experiments } = await signedIn('AUDITOR', answer(200, 'experiments-none'));
    const res = await experiments.list(undefined);
    expect(res.ok && res.value.items).toEqual([]);
  });

  it('keeps every decimal as the service sent it and reads the plan, frame and flags', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(200, 'experiment-planned'));
    const res = await experiments.get(15);
    expect(paths(seen)).toEqual(['GET /api/experimentation/v1/experiments/15']);
    if (!res.ok) throw new Error(res.error.code);
    const e = res.value;
    expect(e).toMatchObject({
      experiment_id: 15,
      state: 'PLANNED',
      design_kind: 'CONTROLLED',
      assignment_rule: 'V1',
      flags: ['UNDERPOWERED'],
      salt: '09848d0652fb9a5ef2acc1efa72eec4c7176b7a729c224c0e23eda3cf5402f6f',
    });
    expect(e.design).toMatchObject({
      primary_metric: 'CONVERSION',
      alpha: '0.05',
      power: '0.80',
      minimum_detectable_effect: '0.02',
      baseline: '0.10',
      required_per_arm: 3839,
      smallest_detectable_effect: '0.0537',
      conversion_window_days: 14,
      settle_days: 7,
    });
    expect(e.design.analysis_at).toBeUndefined();
    expect(e.assignment).toBeUndefined();
    expect(e.frame?.size).toBe(1200);
    expect(e.frame?.exclusions.map((x) => [x.reason, x.count])).toEqual([
      ['NO_MARKETING_CONTACT_CONSENT', 96],
      ['NO_PERSONALIZATION_CONSENT', 41],
      ['OVERLAPPING_EXPERIMENT', 310],
    ]);
  });

  it('reads the checks stored with an assignment', async () => {
    const { experiments } = await signedIn('ANALYST', answer(200, 'experiment-controlled'));
    const res = await experiments.get(12);
    if (!res.ok) throw new Error(res.error.code);
    const checks = res.value.assignment?.checks;
    expect(res.value.assignment).toMatchObject({ assigned_total: 8000, acknowledged_underpowered: false });
    expect(checks?.recomputation_matches).toBe(true);
    expect(checks?.sample_ratio).toEqual({ status: 'PASSED', p_value: '1.000000', threshold: '0.001' });
    expect(checks?.balance.map((b) => [b.measure, b.standardized_mean_difference, b.flagged])).toEqual([
      ['PRE_RECENCY_DAYS', '0.012', false],
      ['PRE_PURCHASE_COUNT', '0.008', false],
      ['PRE_NET_SPEND', '0.021', false],
    ]);
  });

  it('reads an experiment of the earlier rule as readable, with no frame, salt or checks', async () => {
    const { experiments } = await signedIn('AUDITOR', answer(200, 'experiment-legacy'));
    const res = await experiments.get(3);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value).toMatchObject({ assignment_rule: 'V0', data_origin: 'SEEDED', flags: [] });
    expect(res.value.frame).toBeUndefined();
    expect(res.value.salt).toBeUndefined();
    expect(res.value.assignment?.checks).toBeUndefined();
    expect(res.value.design.required_per_arm).toBeUndefined();
    expect(sampleSizeOf(res.value)).toBeNull();
  });

  it('refuses a filter or an id the contract cannot carry before anything is sent', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(200, 'experiments-page'));
    for (const bad of [
      { state: 'DONE' },
      { data_origin: 'REAL' },
      { campaign_id: 0 },
      { page: 0 },
      { size: 101 },
      'x',
    ])
      expect(await experiments.list(bad)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    for (const bad of [0, -1, 1.5, '12', null])
      expect(await experiments.get(bad)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
  });

  it('shows the service’s own refusal as returned', async () => {
    const { experiments } = await signedIn('MARKETING', answer(404, 'experiment-not-found'));
    expect(await experiments.get(99)).toMatchObject({ ok: false, error: { code: 'experiment_not_found' } });
  });

  it('refuses a response outside the contract and renders nothing of it', async () => {
    const drifted = EX('experiment-planned').replace('<state>PLANNED</state>', '<state>PAUSED</state>');
    const { experiments } = await signedIn('MARKETING', () => ({ status: 200, body: drifted }));
    expect(await experiments.get(15)).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
  });
});

describe('planning', () => {
  it('sends the recorded request: the inputs only, in the XSD order, with each arm', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(201, 'experiment-planned'));
    const res = await experiments.plan(PLAN);
    expect(res.ok && res.value.state).toBe('PLANNED');
    expect(paths(seen)).toEqual(['POST /api/experimentation/v1/experiments']);
    expect(seen[0]?.headers['Content-Type']).toBe('application/xml');
    expect(squeezed(seen[0]?.body ?? '')).toBe(squeezed(EX('experiment.request')));
  });

  it('sends alpha and power only when they were typed, and a revenue metric as it is', async () => {
    const { experiments, seen } = await signedIn('ADMIN', answer(201, 'experiment-planned'));
    await experiments.plan({
      ...PLAN,
      primary_metric: 'REVENUE_PER_CUSTOMER',
      minimum_detectable_effect: '1.50',
      alpha: '0.10',
      power: '0.90',
    });
    const body = seen[0]?.body ?? '';
    expect(body).toContain('<primary_metric>REVENUE_PER_CUSTOMER</primary_metric>');
    expect(body).toContain('<minimum_detectable_effect>1.50</minimum_detectable_effect>');
    expect(body.indexOf('<alpha>')).toBeGreaterThan(body.indexOf('<minimum_detectable_effect>'));
    expect(body.indexOf('<power>')).toBeGreaterThan(body.indexOf('<alpha>'));
    expect(body.indexOf('<arms>')).toBeGreaterThan(body.indexOf('<power>'));
    expect(body).not.toContain('data_origin');
  });

  it('sends a no-control design as two treatments, as the contract’s offer comparison', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(201, 'experiment-offer-comparison'));
    const two = {
      ...PLAN,
      arms: [
        { kind: 'TREATMENT', name: 'Voucher', treatment_description: 'A voucher.' },
        { kind: 'TREATMENT', name: 'Free delivery', treatment_description: 'Free delivery.' },
      ],
    };
    const res = await experiments.plan(two);
    expect(res.ok && res.value.design_kind).toBe('OFFER_COMPARISON');
    expect((seen[0]?.body ?? '').match(/<kind>TREATMENT<\/kind>/g)).toHaveLength(2);
    expect(seen[0]?.body).not.toContain('CONTROL');
  });

  it('escapes what was typed, so a name cannot add an element', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(201, 'experiment-planned'));
    await experiments.plan({ ...PLAN, name: 'Spring & <summer> "sale"' });
    expect(seen[0]?.body).toContain('<name>Spring &amp; &lt;summer&gt; &quot;sale&quot;</name>');
  });

  it('names each field it refuses and sends nothing', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(201, 'experiment-planned'));
    const res = await experiments.plan({
      name: ' ',
      campaign_id: 0,
      primary_metric: 'AVERAGE_TICKET',
      starts_on: 'soon',
      conversion_window_days: 400,
      minimum_detectable_effect: '-1',
      alpha: '1.5',
      power: 'high',
      arms: [{ kind: 'CONTROL', name: '', treatment_description: 'x'.repeat(501) }],
    });
    expect(res).toMatchObject({ ok: false, error: { code: 'invalid_body' } });
    if (res.ok) throw new Error('refused');
    expect(res.error.details).toEqual([
      { field: 'name', code: 'required' },
      { field: 'campaign_id', code: 'out_of_range' },
      { field: 'primary_metric', code: 'required' },
      { field: 'starts_on', code: 'invalid_format' },
      { field: 'conversion_window_days', code: 'out_of_range' },
      { field: 'minimum_detectable_effect', code: 'invalid_format' },
      { field: 'alpha', code: 'out_of_range' },
      { field: 'power', code: 'invalid_format' },
      { field: 'arms', code: 'out_of_range' },
    ]);
    expect(seen).toHaveLength(0);
  });

  it('names the arm field it refuses', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(201, 'experiment-planned'));
    const res = await experiments.plan({
      ...PLAN,
      arms: [
        { kind: 'CONTROL', name: '', treatment_description: 'ok' },
        { kind: 'MAYBE', name: 'x'.repeat(121), treatment_description: '' },
      ],
    });
    if (res.ok) throw new Error('refused');
    expect(res.error.details).toEqual([
      { field: 'arm_1_name', code: 'required' },
      { field: 'arm_2_kind', code: 'required' },
      { field: 'arm_2_name', code: 'too_long' },
      { field: 'arm_2_treatment_description', code: 'required' },
    ]);
    expect(seen).toHaveLength(0);
  });

  it('leaves the arm rules to the service and shows its refusal with the details', async () => {
    const { experiments } = await signedIn('MARKETING', answer(422, 'invalid-body'));
    const twoControls = {
      ...PLAN,
      arms: [
        { kind: 'CONTROL', name: 'A', treatment_description: 'a' },
        { kind: 'CONTROL', name: 'B', treatment_description: 'b' },
      ],
    };
    const res = await experiments.plan(twoControls);
    expect(res).toMatchObject({
      ok: false,
      error: { code: 'invalid_body', details: [{ field: 'arms', code: 'control_count' }] },
    });
  });

  it('shows a closed campaign as the service refuses it', async () => {
    const { experiments } = await signedIn('MARKETING', answer(409, 'campaign-closed'));
    expect(await experiments.plan(PLAN)).toMatchObject({ ok: false, error: { code: 'campaign_closed' } });
  });
});

describe('assigning', () => {
  it('sends the recorded request without an acknowledgement, and reads the checks stored with the assignment', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(201, 'assignment-summary'));
    const res = await experiments.assign(12, false);
    expect(paths(seen)).toEqual(['POST /api/experimentation/v1/experiment-assignments']);
    expect(squeezed(seen[0]?.body ?? '')).toBe(squeezed(EX('assignment.request')));
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.assigned_total).toBe(8000);
    expect(res.value.arms.map((a) => [a.arm_id, a.assigned])).toEqual([
      [31, 4000],
      [32, 4000],
    ]);
    expect(res.value.checks?.sample_ratio.status).toBe('PASSED');
    expect(res.value.checks?.balance).toHaveLength(3);
  });

  it('sends the acknowledgement only when it was given', async () => {
    const { experiments, seen } = await signedIn('ADMIN', answer(201, 'assignment-summary'));
    await experiments.assign(15, true);
    expect(squeezed(seen[0]?.body ?? '')).toBe(squeezed(EX('assignment-acknowledged.request')));
    await experiments.assign(15, undefined);
    expect(seen[1]?.body).not.toContain('acknowledge_underpowered');
  });

  it('shows an underpowered plan assigned without acknowledgement as the service refuses it', async () => {
    const { experiments } = await signedIn('MARKETING', answer(409, 'underpowered-not-acknowledged'));
    expect(await experiments.assign(15, false)).toMatchObject({
      ok: false,
      error: { code: 'underpowered_not_acknowledged' },
    });
  });

  it('shows the other assignment refusals as returned', async () => {
    for (const code of ['already-assigned', 'empty-frame', 'assignment-conflict']) {
      const { experiments } = await signedIn('MARKETING', answer(409, code));
      expect(await experiments.assign(12, false)).toMatchObject({
        ok: false,
        error: { code: code.replaceAll('-', '_') },
      });
    }
  });

  it('refuses an id or an acknowledgement it cannot carry before anything is sent', async () => {
    const { experiments, seen } = await signedIn('MARKETING', answer(201, 'assignment-summary'));
    expect(await experiments.assign(0, false)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(await experiments.assign(12, 'yes')).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(seen).toHaveLength(0);
  });
});

describe('what a design says', () => {
  const arm = (kind: 'CONTROL' | 'TREATMENT') => ({ kind });

  it('reads one control and treatments as the only design that can state an effect', () => {
    const r = readDesign([arm('CONTROL'), arm('TREATMENT'), arm('TREATMENT')]);
    expect(r).toMatchObject({ design: 'controlled', effectClaimable: true, noControl: false, problems: [] });
  });

  it('says that no effect can be claimed for two treatments and no control', () => {
    const r = readDesign([arm('TREATMENT'), arm('TREATMENT')]);
    expect(r).toMatchObject({ design: 'offer_comparison', effectClaimable: false, noControl: true });
    expect(r.statement).toBe(NO_EFFECT_STATEMENT);
    expect(NO_EFFECT_STATEMENT).toMatch(/no effect of the campaign against no treatment can be claimed/);
  });

  it('says it too for a no-control design the service would refuse', () => {
    const r = readDesign([arm('TREATMENT'), arm('TREATMENT'), arm('TREATMENT')]);
    expect(r.design).toBe('invalid');
    expect(r.noControl).toBe(true);
    expect(r.statement).toBe(NO_EFFECT_STATEMENT);
    expect(r.problems).toEqual(['Without a control, an experiment compares exactly two offers.']);
  });

  it('names what makes a design invalid', () => {
    expect(readDesign([arm('CONTROL'), arm('CONTROL')]).problems).toEqual([
      'A control may appear at most once.',
      'An experiment needs at least one treatment.',
    ]);
    expect(readDesign([arm('CONTROL')]).problems[0]).toBe('An experiment has 2 to 5 arms.');
    expect(readDesign(Array.from({ length: 6 }, () => arm('TREATMENT'))).design).toBe('invalid');
  });

  it('holds an experiment to a control only when both its kind and its arms say so', () => {
    const arms = (...k: ('CONTROL' | 'TREATMENT')[]) =>
      k.map((kind, i) => ({ arm_id: i + 1, position: i, kind, name: `A${i}`, treatment_description: 'x' }));
    expect(hasControl({ design_kind: 'CONTROLLED', arms: arms('CONTROL', 'TREATMENT') })).toBe(true);
    expect(hasControl({ design_kind: 'OFFER_COMPARISON', arms: arms('CONTROL', 'TREATMENT') })).toBe(false);
    expect(hasControl({ design_kind: 'CONTROLLED', arms: arms('TREATMENT', 'TREATMENT') })).toBe(false);
  });
});

describe('the sample size', () => {
  const planned = async (file: string): Promise<ExperimentView> => {
    const { experiments } = await signedIn('MARKETING', answer(200, file));
    const res = await experiments.get(15);
    if (!res.ok) throw new Error(res.error.code);
    return res.value;
  };

  it('shows the customers each arm needs and flags the plan the service flagged', async () => {
    const e = await planned('experiment-planned');
    const r = sampleSizeOf(e);
    expect(r).toEqual({
      required: 3839,
      arms: 2,
      frameSize: 1200,
      perArm: 600,
      underpowered: true,
      smallestDetectable: '0.0537',
      minimumDetectable: '0.02',
    });
    expect(sampleSizeText(r!)).toBe(
      'Each arm needs 3,839 customers. The frame holds 1,200 customers, which supplies 600 to each of 2 arms.',
    );
  });

  it('does not flag a plan the service did not flag', async () => {
    const e = await planned('experiment-controlled');
    expect(sampleSizeOf(e)).toMatchObject({
      required: 3839,
      frameSize: 8000,
      perArm: 4000,
      underpowered: false,
    });
  });

  it('divides the frame by the arms of a three-arm plan, rounded down', async () => {
    const e = await planned('experiment-planned');
    const three = { ...e, arms: [...e.arms, { ...e.arms[1]!, arm_id: 99, position: 2 }] };
    expect(sampleSizeOf(three)?.perArm).toBe(400);
  });

  it('writes a figure in its metric’s unit without rounding it', () => {
    expect(metricFigure('CONVERSION', '0.0537')).toBe('0.0537 (5.37 percentage points)');
    expect(metricFigure('CONVERSION', '0.02')).toBe('0.02 (2 percentage points)');
    expect(metricFigure('REVENUE_PER_CUSTOMER', '1.50')).toBe('1.50 per customer');
    expect(formatCount(3839)).toBe('3,839');
  });

  it('moves a decimal point exactly', () => {
    expect(shiftDecimal('0.0210', 2)).toBe('2.10');
    expect(shiftDecimal('0.0073', 2)).toBe('0.73');
    expect(shiftDecimal('-0.0031', 2)).toBe('-0.31');
    expect(shiftDecimal('1', 2)).toBe('100');
    expect(shiftDecimal('0.5', 2)).toBe('50');
    expect(shiftDecimal('0.001', 1)).toBe('0.01');
    expect(shiftDecimal('0.0000', 2)).toBe('0.00');
    expect(shiftDecimal('-0.0000', 2)).toBe('0.00');
    expect(shiftDecimal('1e-3', 2)).toBeNull();
    expect(shiftDecimal('', 2)).toBeNull();
  });

  it('names an arm by its id, and an unknown id as an id', () => {
    expect(armName([{ arm_id: 31, name: 'No offer' }], 31)).toBe('No offer');
    expect(armName([{ arm_id: 31, name: 'No offer' }], 77)).toBe('Arm #77');
  });
});
