import { describe, expect, it } from 'vitest';
import { Models } from '../../src/main/models';
import type { ModelVersionView, TrainingView } from '../../src/shared/api';
import {
  compareVersions,
  labelSizes,
  originText,
  publicationImpact,
  rawCentroids,
  rawText,
  sizeText,
  trainingText,
} from '../../src/shared/kmeans';
import { claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';

// The figures the K-means screen shows (F28-05, #874), computed from versions read through the
// real parser from the recorded examples, so a change of shape in the contract breaks here.

async function versions(): Promise<{
  draft: ModelVersionView;
  published: ModelVersionView;
  rules: ModelVersionView;
}> {
  const { office } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims('ANALYST') } : { status: 204 };
      return { status: 200, body: example('model-registry', 'model-versions-page') };
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  const res = await new Models(office).listVersions({});
  if (!res.ok) throw new Error(res.error.code);
  const [draft, published, rules] = res.value.items as [ModelVersionView, ModelVersionView, ModelVersionView];
  return { draft, published, rules };
}

/** A second K-means version that differs from the draft in centroids, k, seed and metrics. */
function other(base: ModelVersionView): ModelVersionView {
  return {
    ...base,
    version_id: 7,
    name: 'kmeans-k5-trial-2',
    seed: '77',
    parameters: { ...base.parameters, k: 5 },
    centroids: [...base.centroids, { cluster_index: 4, label_code: 'LOYAL', r: '0.5', f: '0.5', m: '0.5' }],
    labels: [
      { label_code: 'CHAMPIONS', share: '0.1' },
      { label_code: 'LOYAL', share: '0.5' },
      { label_code: 'AT_RISK', share: '0.25' },
      { label_code: 'LOST', share: '0.15' },
    ],
    metrics: { inertia: '300.5', silhouette: '0.5' },
    dataset: { ...base.dataset, fingerprint: 'a'.repeat(64) },
  };
}

describe('centroids in raw units', () => {
  it('puts each min-max coordinate back with the bounds the version froze', async () => {
    const { draft } = await versions();
    const [champions, , , lost] = rawCentroids(draft);
    // raw = min + coordinate * (max - min)
    expect(champions?.r).toBeCloseTo(0.1204 * 180, 6);
    expect(champions?.f).toBeCloseTo(1 + 0.812 * 41, 6);
    expect(champions?.m).toBeCloseTo(5 + 0.7733 * (9120.5 - 5), 6);
    expect(lost?.r).toBeCloseTo(0.9122 * 180, 6);
    expect([rawText('R', champions?.r ?? null), rawText('F', champions?.f ?? null)]).toEqual([
      '21.7',
      '34.3',
    ]);
    expect(rawText('M', champions?.m ?? null)).toBe('7054.02');
    expect(rawCentroids(draft).map((c) => c.label_code)).toEqual(['CHAMPIONS', 'LOYAL', 'AT_RISK', 'LOST']);
  });

  it('leaves a centroid as it is under NONE, and leaves out a variable the fit did not use', async () => {
    const { draft } = await versions();
    const none: ModelVersionView = {
      ...draft,
      parameters: { ...draft.parameters, normalization: 'NONE', features: ['R', 'M'] },
    };
    const [first] = rawCentroids(none);
    expect(first).toEqual({ label_code: 'CHAMPIONS', r: 0.1204, f: null, m: 0.7733 });
    expect(rawText('F', null)).toBe('—');
  });

  it('shows no value when the bounds were not sent, rather than a coordinate passed off as raw', async () => {
    const { draft } = await versions();
    const bare: ModelVersionView = { ...draft };
    delete bare.bounds;
    expect(rawCentroids(bare).every((c) => c.r === null && c.f === null && c.m === null)).toBe(true);
  });

  it('has no centroids for a rule-based version', async () => {
    const { rules } = await versions();
    expect(rawCentroids(rules)).toEqual([]);
  });

  it('lists centroids by cluster number, whatever order they arrived in', async () => {
    const { draft } = await versions();
    const shuffled = { ...draft, centroids: [...draft.centroids].reverse() };
    expect(rawCentroids(shuffled).map((c) => c.label_code)).toEqual([
      'CHAMPIONS',
      'LOYAL',
      'AT_RISK',
      'LOST',
    ]);
  });
});

describe('sizes', () => {
  it('are the customers under each label when the version was fitted', async () => {
    const { draft } = await versions();
    expect(labelSizes(draft)).toEqual([
      { label_code: 'CHAMPIONS', share: 0.12, customers: 2212 },
      { label_code: 'LOYAL', share: 0.31, customers: 5714 },
      { label_code: 'AT_RISK', share: 0.33, customers: 6083 },
      { label_code: 'LOST', share: 0.24, customers: 4424 },
    ]);
    expect(sizeText(labelSizes(draft)[0])).toBe('2212 (12.0%)');
    expect(sizeText(undefined)).toBe('—');
  });
});

describe('comparing two versions', () => {
  it('sets sizes, centroids, inertia and silhouette side by side, flagging what differs', async () => {
    const { draft } = await versions();
    const c = compareVersions(draft, other(draft));
    const row = (name: string): [string, string, boolean] => {
      const r = c.summary.find((s) => s.name === name);
      return [r?.values[0] ?? '', r?.values[1] ?? '', r?.differs ?? false];
    };
    expect(row('Inertia')).toEqual(['412.78', '300.5', true]);
    expect(row('Silhouette')).toEqual(['0.41', '0.5', true]);
    expect(row('Stability')).toEqual(['0.93', '—', true]);
    expect(row('Clusters (k)')).toEqual(['4', '5', true]);
    expect(row('Seed')).toEqual(['424242', '77', true]);
    expect(row('Variables')).toEqual(['R, F, M', 'R, F, M', false]);
    expect(row('Window (days)')).toEqual(['180', '180', false]);
    expect(c.sizes.map((s) => [s.label_code, sizeText(s.cells[0]), sizeText(s.cells[1])])).toEqual([
      ['CHAMPIONS', '2212 (12.0%)', '1843 (10.0%)'],
      ['LOYAL', '5714 (31.0%)', '9216 (50.0%)'],
      ['AT_RISK', '6083 (33.0%)', '4608 (25.0%)'],
      ['LOST', '4424 (24.0%)', '2765 (15.0%)'],
    ]);
  });

  it('aligns centroids by label, and by position when a label is shared, never by cluster number', async () => {
    const { draft } = await versions();
    const c = compareVersions(draft, other(draft));
    expect(c.centroids.map((r) => [r.label, r.cells[0] !== null, r.cells[1] !== null])).toEqual([
      ['CHAMPIONS', true, true],
      ['LOYAL', true, true],
      ['AT_RISK', true, true],
      ['LOST', true, true],
      ['LOYAL (2)', false, true],
    ]);
    expect(c.centroids[4]?.cells[1]?.r).toBeCloseTo(0.5 * 180, 6);
  });

  it('is marked methodological, and never says that one version is better', async () => {
    const { draft } = await versions();
    const { notes } = compareVersions(draft, other(draft));
    expect(notes[0]).toContain('methodological comparison');
    expect(notes[0]).toContain('not customer behaviour');
    expect(notes[0]).toContain('does not say that either one is better');
    expect(notes.join(' ')).not.toMatch(/\b(better than|worse|winner|improved|declined)\b/);
  });

  it('warns that inertia and silhouette do not compare across data, k, variables or normalization', async () => {
    const { draft } = await versions();
    const same = compareVersions(draft, { ...draft, version_id: 8 }).notes.join(' ');
    expect(same).not.toContain('different data');
    expect(same).not.toContain('depend on k');
    const differs = compareVersions(draft, other(draft)).notes.join(' ');
    expect(differs).toContain('different data');
    expect(differs).toContain('depend on k, the variables and the normalization');
  });

  it('marks Synthetic when either version was fitted on seeded data', async () => {
    const { draft } = await versions();
    expect(compareVersions(draft, draft).synthetic).toBe(false);
    const seeded = { ...draft, dataset: { ...draft.dataset, origin: 'SEEDED' as const } };
    expect(compareVersions(draft, seeded).synthetic).toBe(true);
    expect(compareVersions(seeded, draft).synthetic).toBe(true);
    expect(compareVersions(draft, seeded).summary.find((s) => s.name === 'Data origin')?.values).toEqual([
      'Observed',
      'Synthetic (seeded)',
    ]);
    expect(originText(seeded)).toBe('Synthetic (seeded)');
  });

  it('compares a K-means version with a rule-based one, and says the latter has no centroids', async () => {
    const { draft, rules } = await versions();
    const c = compareVersions(draft, rules);
    expect(c.notes.some((n) => n.includes('rfm-quintiles-v2 has no centroids'))).toBe(true);
    expect(c.centroids.every((r) => r.cells[1] === null)).toBe(true);
    expect(c.summary.find((s) => s.name === 'Inertia')?.values).toEqual(['412.78', '—']);
  });

  it('says so when a version cannot be put back into raw units', async () => {
    const { draft } = await versions();
    const bare: ModelVersionView = { ...draft };
    delete bare.bounds;
    expect(compareVersions(bare, draft).notes.join(' ')).toContain('cannot be put back into raw units');
  });
});

describe('what a publication changes for customers', () => {
  it('names the version it replaces, the re-scoring, the labels and that the past is kept', async () => {
    const { draft, published } = await versions();
    const text = publicationImpact(draft, published).join('\n');
    expect(text).toContain('"kmeans-k4-trial" replaces "kmeans-k4-2026-10" (KMEANS), which becomes RETIRED');
    expect(text).toContain('Every customer is scored again under "kmeans-k4-trial" in a full run');
    expect(text).toContain('one of 4 labels: CHAMPIONS, LOYAL, AT_RISK, LOST');
    expect(text).toContain('VERSION_CHANGE');
    expect(text).toContain('never counted as customer behaviour');
    expect(text).toContain('not rewritten');
    expect(text).not.toContain('The method changes');
  });

  it('says which labels stop being held and which start, and that the method changes', async () => {
    const { draft, published } = await versions();
    const text = publicationImpact(other(draft), { ...published, method: 'RFM_RULES' }).join('\n');
    expect(text).toContain('The method changes from RFM_RULES to KMEANS.');
    const swapped = publicationImpact(
      { ...draft, centroids: draft.centroids.slice(0, 3), labels: draft.labels.slice(0, 3) },
      published,
    ).join('\n');
    expect(swapped).toContain('Labels no customer holds afterwards: LOST.');
    const added = publicationImpact(other(draft), {
      ...published,
      centroids: published.centroids.slice(0, 3),
      labels: published.labels.slice(0, 3),
    }).join('\n');
    expect(added).toContain('Labels customers do not hold now: LOST.');
  });

  it('says it is the first publication when nothing is published, and marks seeded data', async () => {
    const { draft } = await versions();
    const first = publicationImpact(draft, null).join('\n');
    expect(first).toContain(
      'No version is published yet, so "kmeans-k4-trial" becomes the first published version.',
    );
    expect(first).not.toContain('Synthetic');
    const seeded = publicationImpact(
      { ...draft, dataset: { ...draft.dataset, origin: 'SEEDED' } },
      null,
    ).join('\n');
    expect(seeded).toContain('Synthetic: this draft was fitted on seeded data (ADR-0052).');
  });
});

describe('following a training', () => {
  const base: TrainingView = {
    training_id: 9,
    state: 'RUNNING',
    version_name: 'kmeans-k5-trial-2',
    window_days: 180,
    seed: '77',
    parameters: { k: 5, features: ['R', 'F', 'M'], normalization: 'MIN_MAX' },
    started_at: '2026-10-31T09:10:00Z',
  };
  const at = (iso: string): number => Date.parse(iso);

  it('says how long a running training has run', () => {
    expect(trainingText(base, at('2026-10-31T09:10:38Z'))).toBe('Training #9 is running for 38 s.');
    expect(trainingText(base, at('2026-10-31T09:09:00Z'))).toBe('Training #9 is running for 0 s.');
  });

  it('says how a finished one ended, and that a failed one registered nothing', () => {
    const done: TrainingView = {
      ...base,
      state: 'COMPLETED',
      iterations: 31,
      converged: true,
      finished_at: '2026-10-31T09:11:40Z',
    };
    expect(trainingText(done, at('2027-01-01T00:00:00Z'))).toBe(
      'Training #9 completed in 100 s after 31 iterations, converged.',
    );
    expect(trainingText({ ...done, converged: false }, 0)).toContain('not converged');
    const lost: TrainingView = {
      ...base,
      state: 'FAILED',
      finished_at: '2026-10-31T09:10:03Z',
      failure_code: 'source_unavailable',
    };
    expect(trainingText(lost, 0)).toBe(
      'Training #9 failed after 3 s (source_unavailable). Nothing was registered.',
    );
  });
});
