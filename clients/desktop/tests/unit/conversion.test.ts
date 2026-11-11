import { describe, expect, it } from 'vitest';
import type { Backoffice } from '../../src/main/backoffice';
import { ConversionResults } from '../../src/main/conversion';
import type { ExperimentResultView } from '../../src/shared/api';
import {
  armRows,
  attributionRows,
  attributionTexts,
  comparisonColumns,
  comparisonRows,
  comparisonsOf,
  controlArm,
  kindOf,
  pendingText,
  percent,
  statusText,
  warningsOf,
} from '../../src/shared/conversion';
import { CONVERSION_OPERATIONS, MODULE_GROUPS } from '../../src/shared/modules';
import { claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// The conversion analysis module (F28-11, #880), against the recorded results of
// contracts/experimentation/. The operation is published ahead of its implementation (ADR-0073), so
// these examples are what the client is built and tested against until the service answers.

const EX = (name: string): string => example('experimentation', name);

async function signedIn(
  role: string,
  responder: Responder,
): Promise<{ results: ConversionResults; seen: Seen[]; office: Backoffice }> {
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
  return { results: new ConversionResults(office), seen, office };
}

const answer =
  (status: number, file: string): Responder =>
  () => ({ status, body: EX(file) });

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

/** The recorded result as the module reads it. */
async function read(file: string, id = 12, basis = 'PRIMARY'): Promise<ExperimentResultView> {
  const { results } = await signedIn('ANALYST', answer(200, file));
  const res = await results.result(id, basis);
  if (!res.ok) throw new Error(res.error.code);
  return res.value;
}

const RESULTS = [
  'result-complete',
  'result-revenue',
  'result-inconclusive',
  'result-pending',
  'result-invalid',
  'result-offer-comparison',
];

describe('who may read a result', () => {
  it('offers the module to the roles that read results and to no one else', async () => {
    const operations = (office: Backoffice): string[] =>
      (office.state() as { operations?: string[] }).operations ?? [];
    for (const role of ['ADMIN', 'ANALYST', 'MARKETING', 'AUDITOR']) {
      const { office } = await signedIn(role, answer(200, 'result-complete'));
      expect(operations(office)).toContain(CONVERSION_OPERATIONS.result);
    }
    for (const role of ['STORE_MANAGER', 'INVENTORY_PLANNER']) {
      const { office } = await signedIn(role, answer(200, 'result-complete'));
      expect(operations(office)).not.toContain(CONVERSION_OPERATIONS.result);
    }
    const module = MODULE_GROUPS.flatMap((g) => g.modules).find((m) => m.name === 'conversion');
    expect(module?.operation).toBe(CONVERSION_OPERATIONS.result);
  });

  it('reads nothing for a role the contract does not allow, whatever the screen sends', async () => {
    const { results, seen } = await signedIn('STORE_MANAGER', answer(200, 'result-complete'));
    expect(await results.result(12, 'PRIMARY')).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
    expect(seen).toHaveLength(0);
  });
});

describe('reading a result', () => {
  it('asks for the stored result in XML and keeps every decimal as the service sent it', async () => {
    const { results, seen } = await signedIn('ANALYST', answer(200, 'result-complete'));
    const res = await results.result(12, 'PRIMARY');
    expect(paths(seen)).toEqual(['GET /api/experimentation/v1/experiment-results/12?basis=PRIMARY']);
    expect(seen[0]?.headers.Accept).toBe('application/xml');
    if (!res.ok) throw new Error(res.error.code);
    const r = res.value;
    expect(r).toMatchObject({
      experiment_id: 12,
      campaign_id: 4,
      basis: 'PRIMARY',
      status: 'COMPLETE',
      elapsed_share: '1',
      synthetic: false,
      design_kind: 'CONTROLLED',
      flags: [],
    });
    expect(r.arms.map((a) => [a.name, a.assigned, a.converted, a.pending, a.conversion_rate])).toEqual([
      ['No offer', 4000, 400, 0, '0.1000'],
      ['10 percent win-back voucher', 4000, 484, 0, '0.1210'],
    ]);
    expect(r.arms[0]?.exposed_conversion_rate).toBeUndefined();
    expect(r.arms[1]?.exposed_conversion_rate).toBe('0.1270');
    expect(r.comparisons[0]).toEqual({
      label: 'EFFECT',
      metric: 'CONVERSION',
      role: 'PRIMARY',
      arm_id: 32,
      against_arm_id: 31,
      difference: '0.0210',
      lower: '0.0073',
      upper: '0.0347',
      confidence_level: '0.950',
      test: 'POOLED_Z',
      p_value: '0.002739',
      conclusion: 'SIGNIFICANT',
    });
    expect(r.comparisons[1]?.conclusion).toBeUndefined();
    expect(r.comparisons[1]?.bootstrap).toEqual({
      resamples: 10000,
      lower: '-0.29',
      upper: '2.46',
      agrees: true,
    });
    expect(r.design).toMatchObject({ required_per_arm: 3839, analysis_at: '2026-10-22T08:00:00Z' });
    expect(r.checks?.sample_ratio.status).toBe('PASSED');
    expect(r.checks?.balance).toHaveLength(3);
    expect(r.assumptions).toHaveLength(8);
    expect(r.attribution).toEqual({
      label: 'ATTRIBUTION',
      window_days: 14,
      arms: [
        { arm_id: 32, attributed_sales: 431, attributed_customers: 377, attributed_revenue: '15980.40' },
      ],
      attributed_sales: 431,
      attributed_revenue: '15980.40',
      unattributed_conversions: 107,
      as_of: '2026-10-22T08:00:07Z',
    });
  });

  it('reads the revenue effect with its interval, and a bootstrap beside it', async () => {
    const r = await read('result-revenue', 13);
    const primary = r.comparisons[0];
    expect(primary?.incremental_revenue).toEqual({ amount: '14080.00', lower: '5097.26', upper: '23062.74' });
    expect(primary?.degrees_of_freedom).toBe('15865.4');
    expect(primary?.bootstrap?.agrees).toBe(true);
  });

  it('reads a pending result as counts only, a withheld one with its failed check, and a comparison of offers', async () => {
    const pending = await read('result-pending', 18);
    expect(pending).toMatchObject({ status: 'PENDING', comparisons: [], elapsed_share: '0.2857' });
    expect(pending.checks).toBeUndefined();
    expect(pending.arms.map((a) => a.pending)).toEqual([1939, 1917]);

    const invalid = await read('result-invalid', 19);
    expect(invalid).toMatchObject({ status: 'INVALID', comparisons: [], flags: ['SAMPLE_RATIO_FAILED'] });
    expect(invalid.checks?.sample_ratio).toEqual({
      status: 'FAILED',
      p_value: '0.000016',
      threshold: '0.001',
    });

    const offers = await read('result-offer-comparison', 16);
    expect(offers.design_kind).toBe('OFFER_COMPARISON');
    expect(offers.effect_refusal).toMatchObject({ code: 'NO_CONTROL' });
    expect(offers.comparisons.map((c) => c.label)).toEqual(['TREATMENT_DIFFERENCE', 'TREATMENT_DIFFERENCE']);
  });

  it('asks for a re-read when it is asked to', async () => {
    const { results, seen } = await signedIn('MARKETING', answer(200, 'result-complete'));
    await results.result(12, 'RE_READ');
    expect(paths(seen)).toEqual(['GET /api/experimentation/v1/experiment-results/12?basis=RE_READ']);
  });

  it('refuses an id or a basis the contract cannot carry before anything is sent', async () => {
    const { results, seen } = await signedIn('ANALYST', answer(200, 'result-complete'));
    for (const bad of [0, -1, 1.5, '12', null, undefined])
      expect(await results.result(bad, 'PRIMARY')).toMatchObject({
        ok: false,
        error: { code: 'invalid_input' },
      });
    for (const bad of ['LATEST', '', undefined, 1])
      expect(await results.result(12, bad)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
  });

  it('shows the service’s own refusal as returned', async () => {
    const notAssigned = await signedIn('ANALYST', answer(409, 'experiment-not-assigned'));
    expect(await notAssigned.results.result(15, 'PRIMARY')).toMatchObject({
      ok: false,
      error: { code: 'experiment_not_assigned' },
    });
    const missing = await signedIn('ANALYST', answer(404, 'experiment-not-found'));
    expect(await missing.results.result(99, 'PRIMARY')).toMatchObject({
      ok: false,
      error: { code: 'experiment_not_found' },
    });
  });

  it('refuses a response outside the contract and renders nothing of it', async () => {
    const drifted = EX('result-complete').replace('<status>COMPLETE</status>', '<status>DONE</status>');
    const { results } = await signedIn('ANALYST', () => ({ status: 200, body: drifted }));
    expect(await results.result(12, 'PRIMARY')).toMatchObject({
      ok: false,
      error: { code: 'contract_defect' },
    });
  });
});

describe('an effect only against a control', () => {
  it('names a treatment against the control an effect, and reads the control of a controlled design', async () => {
    const r = await read('result-complete');
    expect(controlArm(r)?.name).toBe('No offer');
    expect(r.comparisons.map((c) => kindOf(c, r))).toEqual(['effect', 'effect']);
    expect(comparisonRows(r, 'effect')).toHaveLength(2);
    expect(comparisonRows(r, 'treatment_difference')).toEqual([]);
  });

  it('shows the difference and its interval next to each other, in the metric’s own unit', async () => {
    const r = await read('result-complete');
    const [primary, secondary] = comparisonRows(r, 'effect');
    expect(primary).toEqual([
      'Effect',
      'Conversion',
      'Primary',
      '10 percent win-back voucher',
      'No offer',
      '+0.0210 (+2.10 percentage points)',
      '0.0073 to 0.0347 (0.73 to 3.47 percentage points)',
      '95.0% interval',
      '0.002739',
      'Pooled z test, two-sided',
      "Significant at the experiment's alpha",
    ]);
    expect(secondary?.slice(1, 3)).toEqual(['Revenue per customer', 'Secondary (no claim)']);
    expect(secondary?.[5]).toBe('+1.08 per customer');
    expect(secondary?.[6]).toBe('-0.31 to 2.47 per customer');
    expect(secondary?.[9]).toBe(
      'Welch t test, two-sided, 7994.1 degrees of freedom. Bootstrap (10,000 resamples): -0.29 to 2.46, agrees',
    );
    expect(secondary?.[10]).toBe('No claim (secondary metric)');
    // A conversion-only effect has no money column.
    expect(comparisonColumns(r, 'effect')).not.toContain('Incremental revenue');
  });

  it('shows money only on an effect of revenue, with its interval', async () => {
    const r = await read('result-revenue', 13);
    expect(comparisonColumns(r, 'effect').at(-1)).toBe('Incremental revenue');
    expect(comparisonRows(r, 'effect')[0]?.at(-1)).toBe('14080.00 (interval 5097.26 to 23062.74)');
    expect(comparisonRows(r, 'effect')[0]?.[7]).toBe('97.5% interval');
  });

  it('shows two offers without a control as a difference between treatments, never as an effect', async () => {
    const r = await read('result-offer-comparison', 16);
    expect(controlArm(r)).toBeUndefined();
    expect(comparisonRows(r, 'effect')).toEqual([]);
    const rows = comparisonRows(r, 'treatment_difference');
    expect(rows).toHaveLength(2);
    expect(rows[0]?.[0]).toBe('Difference between treatments');
    expect(rows[0]?.[3]).toBe('Free delivery');
    expect(rows[0]?.[4]).toBe('10 percent voucher');
    const words = [...comparisonColumns(r, 'treatment_difference'), ...rows.flat()].join(' ');
    expect(words).not.toMatch(/incremental|uplift|effect|caused/i);
  });

  it('refuses to show as an effect what the service labelled an effect without a control', async () => {
    const offers = await read('result-offer-comparison', 16);
    const labelled: ExperimentResultView = {
      ...offers,
      comparisons: offers.comparisons.map((c) => ({ ...c, label: 'EFFECT' as const })),
    };
    expect(labelled.comparisons.map((c) => kindOf(c, labelled))).toEqual(['refused', 'refused']);
    expect(comparisonRows(labelled, 'effect')).toEqual([]);
    expect(comparisonRows(labelled, 'treatment_difference')).toEqual([]);
    expect(comparisonsOf(labelled, 'refused')).toHaveLength(2);

    // A controlled design whose arms hold no control is no better, and an effect against a treatment is not one.
    const complete = await read('result-complete');
    const noControlArm: ExperimentResultView = {
      ...complete,
      arms: complete.arms.map((a) => ({ ...a, kind: 'TREATMENT' as const })),
    };
    expect(controlArm(noControlArm)).toBeUndefined();
    expect(noControlArm.comparisons.map((c) => kindOf(c, noControlArm))).toEqual(['refused', 'refused']);
    const againstTreatment: ExperimentResultView = {
      ...complete,
      comparisons: complete.comparisons.map((c) => ({ ...c, against_arm_id: 32 })),
    };
    expect(againstTreatment.comparisons.map((c) => kindOf(c, againstTreatment))).toEqual([
      'refused',
      'refused',
    ]);
  });

  it('calls an underpowered non-significant result inconclusive, never no effect', async () => {
    const r = await read('result-inconclusive', 17);
    const row = comparisonRows(r, 'effect')[0];
    expect(row?.at(-1)).toBe(
      'Inconclusive: the plan was underpowered, so this is not evidence that there is no effect',
    );
    expect(row?.at(-1)).not.toMatch(/^no effect/i);
  });
});

describe('attribution apart from the effect', () => {
  it('never uses the words an effect is reserved for', async () => {
    for (const file of RESULTS) {
      const r = await read(file, 12);
      const texts = attributionTexts(r);
      if (r.attribution === undefined) continue;
      expect(texts.length).toBeGreaterThan(5);
      for (const text of texts) expect(text).not.toMatch(/incremental|uplift|caused/i);
    }
  });

  it('labels every attributed figure Attribution and lists them apart from the comparisons', async () => {
    const r = await read('result-complete');
    const rows = attributionRows(r);
    expect(rows).toEqual([
      ['Attribution', '10 percent win-back voucher', '431', '377', '15980.40'],
      ['Attribution', 'All treatment arms', '431', '—', '15980.40'],
    ]);
    const effectCells = comparisonRows(r, 'effect').flat().join(' ');
    expect(effectCells).not.toContain('15980.40');
    expect(effectCells).not.toMatch(/attribut/i);
  });

  it('states that attribution is credit for a sale and not a measure of what the campaign changed', async () => {
    const r = await read('result-offer-comparison', 16);
    expect(attributionTexts(r)[0]).toMatch(/commercial credit, not a measure of what the campaign changed/);
    // An experiment with no control still has an attribution, and no effect.
    expect(attributionRows(r)).toHaveLength(3);
  });

  it('has none to show when none is written yet', async () => {
    const r = await read('result-pending', 18);
    expect(r.attribution).toBeUndefined();
    expect(attributionRows(r)).toEqual([]);
    expect(attributionTexts(r)).toEqual([]);
  });
});

describe('the warnings of the design', () => {
  const codes = (r: ExperimentResultView): string[] => warningsOf(r).map((w) => w.code);

  it('carries what the design flagged, in the order the service sent it, and the inconclusive reading', async () => {
    expect(codes(await read('result-inconclusive', 17))).toEqual([
      'UNDERPOWERED',
      'BALANCE_FLAGGED',
      'INCONCLUSIVE',
    ]);
  });

  it('says that a design with no control can claim no effect', async () => {
    const r = await read('result-offer-comparison', 16);
    expect(codes(r)).toEqual(['NO_CONTROL']);
    expect(warningsOf(r)[0]?.title).toBe('No control: no effect can be claimed');
  });

  it('says that a result is not read yet, or that its estimate is withheld', async () => {
    expect(codes(await read('result-pending', 18))).toEqual(['PENDING']);
    expect(codes(await read('result-invalid', 19))).toEqual(['INVALID', 'SAMPLE_RATIO_FAILED']);
  });

  it('has none for a sound result, and labels seeded figures and a re-read', async () => {
    const complete = await read('result-complete');
    expect(codes(complete)).toEqual([]);
    expect(codes({ ...complete, synthetic: true, basis: 'RE_READ' })).toEqual(['SYNTHETIC', 'RE_READ']);
  });

  it('shows a bootstrap that disagrees with the Welch interval', async () => {
    const r = await read('result-complete');
    const [first, second] = r.comparisons;
    const disagreeing: ExperimentResultView = {
      ...r,
      comparisons: [first!, { ...second!, bootstrap: { ...second!.bootstrap!, agrees: false } }],
    };
    expect(codes(disagreeing)).toEqual(['BOOTSTRAP_DISAGREES']);
  });

  it('travels with every arm row, so a table taken on its own keeps it', async () => {
    const r = await read('result-inconclusive', 17);
    for (const row of armRows(r)) {
      expect(row.at(-1)).toBe('Underpowered; Balance flagged; Inconclusive');
      expect(row.at(-2)).toBe(r.as_of);
      expect(row.at(-3)).toBe('Observed');
    }
    const sound = armRows(await read('result-complete'));
    expect(sound.map((row) => row.at(-1))).toEqual(['None', 'None']);
    const seeded = armRows({ ...(await read('result-complete')), synthetic: true });
    expect(seeded.map((row) => row.at(-3))).toEqual(['Synthetic', 'Synthetic']);
  });
});

describe('what a result says about the customers it cannot count yet', () => {
  it('states the pending customers of each arm, and that they are inside the arm’s customers', async () => {
    const r = await read('result-pending', 18);
    expect(pendingText(r)).toBe(
      "3,856 customers are pending (No offer: 1,939; Free coffee with any basket: 1,917). They have not bought yet and their conversion window is still open. They are counted in each arm's customers, so a rate can still rise.",
    );
  });

  it('says so when no customer is pending', async () => {
    expect(pendingText(await read('result-complete'))).toBe(
      'No customer is pending: every conversion window has closed, so each customer has bought or has not.',
    );
  });

  it('shows counts only before the analysis instant, with the share of the window that has elapsed', async () => {
    const r = await read('result-pending', 18);
    expect(statusText(r)).toBe(
      'Counts only. No difference, interval, p-value or direction is shown before the analysis instant (2026-11-26T08:00:00Z); 28.57% of the conversion window has elapsed.',
    );
    expect(comparisonRows(r, 'effect')).toEqual([]);
    expect(statusText(await read('result-complete'))).toBeNull();
    expect(statusText(await read('result-invalid', 19))).toMatch(/estimate is withheld/);
  });

  it('writes a fraction as a percentage without rounding it', () => {
    expect(percent('0.2857')).toBe('28.57%');
    expect(percent('1')).toBe('100%');
    expect(percent('0.950')).toBe('95.0%');
    expect(percent('0.1000')).toBe('10.00%');
    expect(percent('n/a')).toBe('n/a');
  });
});
