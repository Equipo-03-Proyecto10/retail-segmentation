import { describe, expect, it } from 'vitest';
import { Models } from '../../src/main/models';
import type { TrainingDraft } from '../../src/shared/api';
import { claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// The K-means module's main-process half (F28-05, #874), against the recorded examples of
// contracts/model-registry/. The service is not implemented yet, so every answer is an example.

const REGISTRY = '/api/model-registry/v1';

async function signedIn(role: string, responder: Responder): Promise<{ models: Models; seen: Seen[] }> {
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
  return { models: new Models(office), seen };
}

const answer =
  (status: number, file: string): Responder =>
  () => ({ status, body: example('model-registry', file) });

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

/** The leaf elements of a request body, in order, so a recorded request compares to a sent one. */
const fieldsOf = (xml: string): [string, string][] =>
  [...xml.matchAll(/<([a-z_]+)>([^<]*)<\/\1>/g)].map((m) => [m[1] as string, m[2] as string]);
const recorded = (name: string): [string, string][] => fieldsOf(example('model-registry', name));

/** What training.request.xml records. */
const GOOD: TrainingDraft = {
  version_name: 'kmeans-k5-trial-2',
  window_days: 180,
  k: 5,
  max_iterations: 100,
  tolerance: '0.0001',
  features: ['R', 'F', 'M'],
  normalization: 'MIN_MAX',
  seed: '77',
};

describe('reading versions', () => {
  it('lists with method, state and paging, and reads centroids, bounds, labels and metrics', async () => {
    const { models, seen } = await signedIn('ANALYST', answer(200, 'model-versions-page'));
    const res = await models.listVersions({ method: 'KMEANS', state: 'DRAFT', page: 2, size: 50 });
    expect(paths(seen)).toEqual([`GET ${REGISTRY}/model-versions?method=KMEANS&state=DRAFT&page=2&size=50`]);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 3 });
    expect(res.value.items.map((v) => [v.version_id, v.method, v.state, v.source])).toEqual([
      [6, 'KMEANS', 'DRAFT', 'TRAINING'],
      [5, 'KMEANS', 'PUBLISHED', 'TRAINING'],
      [3, 'RFM_RULES', 'RETIRED', 'REGISTERED'],
    ]);
    const draft = res.value.items[0];
    expect(draft).toMatchObject({
      name: 'kmeans-k4-trial',
      window_days: 180,
      seed: '424242',
      parameters: {
        k: 4,
        max_iterations: 100,
        tolerance: '0.0001',
        features: ['R', 'F', 'M'],
        normalization: 'MIN_MAX',
      },
      bounds: { r_min: '0', r_max: '180', f_min: '1', f_max: '42', m_min: '5.0', m_max: '9120.5' },
      sample_size: 18432,
      metrics: { inertia: '412.78', silhouette: '0.41', stability: '0.93' },
      dataset: { origin: 'OBSERVED', row_count: 18432 },
      training_id: 8,
    });
    expect(draft?.centroids.map((c) => [c.cluster_index, c.label_code, c.r])).toEqual([
      [0, 'CHAMPIONS', '0.1204'],
      [1, 'LOYAL', '0.2951'],
      [2, 'AT_RISK', '0.631'],
      [3, 'LOST', '0.9122'],
    ]);
    expect(draft?.labels).toEqual([
      { label_code: 'CHAMPIONS', share: '0.12' },
      { label_code: 'LOYAL', share: '0.31' },
      { label_code: 'AT_RISK', share: '0.33' },
      { label_code: 'LOST', share: '0.24' },
    ]);
  });

  it('reads a rule-based version as one with no centroids, bounds, seed or k', async () => {
    const { models } = await signedIn('ANALYST', answer(200, 'model-version-rfm-rules'));
    const res = await models.getVersion(3);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.method).toBe('RFM_RULES');
    expect(res.value.centroids).toEqual([]);
    expect(res.value.bounds).toBeUndefined();
    expect(res.value.seed).toBeUndefined();
    expect(res.value.parameters.k).toBeUndefined();
    expect(res.value.metrics).toEqual({});
    expect(res.value.retired_at).toBe('2026-10-28T09:30:00Z');
  });

  it('reads one version, marks seeded data, and passes an unknown version on as returned', async () => {
    const { models, seen } = await signedIn('AUDITOR', answer(200, 'model-version-from-web-run'));
    const res = await models.getVersion(4);
    expect(paths(seen)).toEqual([`GET ${REGISTRY}/model-versions/4`]);
    expect(res.ok && [res.value.source, res.value.dataset.origin]).toEqual(['WEB_RUN', 'SEEDED']);
    const { models: none } = await signedIn('AUDITOR', answer(404, 'model-version-not-found'));
    const missing = await none.getVersion(99);
    expect(!missing.ok && missing.error.code).toBe('model_version_not_found');
  });

  it('keeps a page of one version a list', async () => {
    const one = example('model-registry', 'model-versions-page')
      .replace(/<ModelVersion>[\s\S]*?<\/ModelVersion>\s*(?=<ModelVersion>)/, '')
      .replace(/<ModelVersion>[\s\S]*?<\/ModelVersion>\s*(?=<ModelVersion>)/, '');
    const { models } = await signedIn('ANALYST', () => ({ status: 200, body: one }));
    const res = await models.listVersions({});
    expect(res.ok && res.value.items.map((v) => v.version_id)).toEqual([3]);
  });

  it('refuses a filter that is not a method, a state or a number, before sending anything', async () => {
    const { models, seen } = await signedIn('ANALYST', answer(200, 'model-versions-page'));
    for (const query of [{ method: 'DBSCAN' }, { state: 'LIVE' }, { page: 0 }, { size: 101 }, 'x'])
      expect((await models.listVersions(query)).ok).toBe(false);
    expect((await models.getVersion('6')).ok).toBe(false);
    expect((await models.getVersion(0)).ok).toBe(false);
    expect(seen).toEqual([]);
  });
});

describe('reading trainings', () => {
  it('lists with a state and reads a running, a completed and a failed training', async () => {
    const { models, seen } = await signedIn('MARKETING', answer(200, 'trainings-page'));
    const res = await models.listTrainings({ state: 'RUNNING', page: 1, size: 1 });
    expect(paths(seen)).toEqual([`GET ${REGISTRY}/model-trainings?state=RUNNING&page=1&size=1`]);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.items.map((t) => [t.training_id, t.state, t.version_id, t.failure_code])).toEqual([
      [9, 'RUNNING', undefined, undefined],
      [8, 'COMPLETED', 6, undefined],
      [6, 'FAILED', undefined, 'source_unavailable'],
    ]);
    const done = res.value.items[1];
    expect(done).toMatchObject({ iterations: 31, converged: true, seed: '424242' });
    expect(done?.metrics).toEqual({ inertia: '358.12', silhouette: '0.37', stability: '0.88' });
  });

  it('opens one training: progress has no figures until it completes', async () => {
    const { models, seen } = await signedIn('ANALYST', answer(200, 'training-running'));
    const res = await models.getTraining(9);
    expect(paths(seen)).toEqual([`GET ${REGISTRY}/model-trainings/9`]);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value).toMatchObject({ state: 'RUNNING', seed: '77', parameters: { k: 5 } });
    expect(res.value.dataset).toBeUndefined();
    expect(res.value.iterations).toBeUndefined();
    const { models: failed } = await signedIn('ANALYST', answer(200, 'training-failed'));
    const lost = await failed.getTraining(6);
    expect(lost.ok && [lost.value.state, lost.value.failure_code]).toEqual(['FAILED', 'source_unavailable']);
    const { models: gone } = await signedIn('ANALYST', answer(404, 'model-training-not-found'));
    const missing = await gone.getTraining(1);
    expect(!missing.ok && missing.error.code).toBe('model_training_not_found');
  });
});

describe('starting a training', () => {
  it('sends the recorded request, in the order the XSD fixes, and returns the run as RUNNING', async () => {
    const { models, seen } = await signedIn('ADMIN', answer(201, 'training-running'));
    const res = await models.startTraining(GOOD);
    expect(paths(seen)).toEqual([`POST ${REGISTRY}/model-trainings`]);
    expect(fieldsOf(seen[0]?.body ?? '')).toEqual(recorded('training.request'));
    expect(res.ok && [res.value.training_id, res.value.state]).toEqual([9, 'RUNNING']);
  });

  it('omits an empty or absent seed, so the service chooses and records one', async () => {
    const { models, seen } = await signedIn('ADMIN', answer(201, 'training-running'));
    const next = { ...GOOD, version_name: 'kmeans-k4-next', k: 4 };
    await models.startTraining({ ...next, seed: '' });
    const bare: Partial<TrainingDraft> = { ...next };
    delete bare.seed;
    await models.startTraining(bare);
    expect(seen.map((s) => fieldsOf(s.body ?? ''))).toEqual([
      recorded('training-without-seed.request'),
      recorded('training-without-seed.request'),
    ]);
  });

  it('sends a chosen subset of variables in R, F, M order, and a negative or 64-bit seed as typed', async () => {
    const { models, seen } = await signedIn('ADMIN', answer(201, 'training-running'));
    await models.startTraining({
      ...GOOD,
      features: ['M', 'R'],
      normalization: 'NONE',
      seed: '-9223372036854775808',
    });
    const sent = fieldsOf(seen[0]?.body ?? '');
    expect(sent.filter(([n]) => n === 'features').map(([, v]) => v)).toEqual(['R', 'M']);
    expect(sent.find(([n]) => n === 'normalization')?.[1]).toBe('NONE');
    expect(sent.find(([n]) => n === 'seed')?.[1]).toBe('-9223372036854775808');
  });

  it('refuses each value outside the contract naming its field, and sends nothing', async () => {
    const { models, seen } = await signedIn('ADMIN', answer(201, 'training-running'));
    const bad = async (changes: Record<string, unknown>): Promise<[string, string][]> => {
      const res = await models.startTraining({ ...GOOD, ...changes });
      if (res.ok) throw new Error('expected a refusal');
      expect(res.error.code).toBe('invalid_body');
      return res.error.details.map((d) => [d.field, d.code]);
    };
    expect(await bad({ version_name: '  ' })).toEqual([['version_name', 'required']]);
    expect(await bad({ version_name: 'x'.repeat(81) })).toEqual([['version_name', 'too_long']]);
    expect(await bad({ window_days: 0 })).toEqual([['window_days', 'out_of_range']]);
    expect(await bad({ window_days: 3651 })).toEqual([['window_days', 'out_of_range']]);
    expect(await bad({ k: 1 })).toEqual([['k', 'out_of_range']]);
    expect(await bad({ k: 21 })).toEqual([['k', 'out_of_range']]);
    expect(await bad({ k: 2.5 })).toEqual([['k', 'out_of_range']]);
    expect(await bad({ k: Number.NaN })).toEqual([['k', 'out_of_range']]);
    expect(await bad({ max_iterations: 1001 })).toEqual([['max_iterations', 'out_of_range']]);
    expect(await bad({ tolerance: '0' })).toEqual([['tolerance', 'out_of_range']]);
    expect(await bad({ tolerance: '1e-4' })).toEqual([['tolerance', 'invalid_format']]);
    expect(await bad({ tolerance: '' })).toEqual([['tolerance', 'invalid_format']]);
    expect(await bad({ features: [] })).toEqual([['features', 'required']]);
    expect(await bad({ features: ['R', 'X'] })).toEqual([['features', 'invalid_format']]);
    expect(await bad({ normalization: 'LOG' })).toEqual([['normalization', 'invalid_format']]);
    expect(await bad({ normalization: '' })).toEqual([['normalization', 'required']]);
    expect(await bad({ seed: '1.5' })).toEqual([['seed', 'invalid_format']]);
    expect(await bad({ seed: '9223372036854775808' })).toEqual([['seed', 'out_of_range']]);
    expect(await bad({ k: 1, window_days: 0 })).toEqual([
      ['window_days', 'out_of_range'],
      ['k', 'out_of_range'],
    ]);
    expect(seen).toEqual([]);
  });

  it('passes the service refusals on as returned', async () => {
    const { models: busy } = await signedIn('ADMIN', answer(409, 'training-in-progress'));
    const running = await busy.startTraining(GOOD);
    expect(!running.ok && running.error.code).toBe('training_in_progress');
    const { models: taken } = await signedIn('ADMIN', answer(409, 'model-version-name-taken'));
    const dup = await taken.startTraining(GOOD);
    expect(!dup.ok && dup.error.code).toBe('model_version_name_taken');
    const { models: invalid } = await signedIn('ADMIN', answer(422, 'invalid-body'));
    const refused = await invalid.startTraining(GOOD);
    expect(!refused.ok && refused.error.details).toEqual([
      { field: 'frozen_scoring', code: 'method_mismatch' },
    ]);
  });
});

describe('publishing', () => {
  it('sends the recorded reason to the version it names and returns the publication', async () => {
    const { models, seen } = await signedIn('ADMIN', answer(201, 'publication-published'));
    const reason = 'K-means k=4 separates the at-risk customers better than the rule set.';
    const res = await models.publish(6, `  ${reason} `);
    expect(paths(seen)).toEqual([`POST ${REGISTRY}/model-versions/6/publications`]);
    expect(fieldsOf(seen[0]?.body ?? '')).toEqual(recorded('publication.request'));
    expect(res.ok && res.value).toEqual({
      publication_id: 12,
      version_id: 5,
      action: 'PUBLISH',
      previous_version_id: 3,
      reason,
      published_at: '2026-10-28T09:30:00Z',
    });
  });

  it('refuses a missing or oversized reason, and a version that is not an id, before sending', async () => {
    const { models, seen } = await signedIn('ADMIN', answer(201, 'publication-published'));
    const empty = await models.publish(6, '   ');
    expect(!empty.ok && empty.error.details).toEqual([{ field: 'reason', code: 'required' }]);
    const long = await models.publish(6, 'x'.repeat(501));
    expect(!long.ok && long.error.details).toEqual([{ field: 'reason', code: 'too_long' }]);
    expect((await models.publish('6', 'why')).ok).toBe(false);
    expect((await models.publish(0, 'why')).ok).toBe(false);
    expect(seen).toEqual([]);
  });

  it('passes a version that is not a draft, a lost race and an unknown version on as returned', async () => {
    for (const [status, file, code] of [
      [409, 'version-not-draft', 'version_not_draft'],
      [409, 'publication-conflict', 'publication_conflict'],
      [404, 'model-version-not-found', 'model_version_not_found'],
    ] as const) {
      const { models } = await signedIn('ADMIN', answer(status, file));
      const res = await models.publish(6, 'why');
      expect(!res.ok && res.error.code).toBe(code);
    }
  });
});

describe('roles', () => {
  it('lets the roles that read versions read, and only the administrator train or publish', async () => {
    for (const role of ['ANALYST', 'MARKETING', 'AUDITOR']) {
      const { models, seen } = await signedIn(role, answer(200, 'model-versions-page'));
      expect((await models.listVersions({})).ok).toBe(true);
      const train = await models.startTraining(GOOD);
      expect(!train.ok && train.error.code).toBe('forbidden_role');
      const publish = await models.publish(6, 'why');
      expect(!publish.ok && publish.error.code).toBe('forbidden_role');
      // Only the read went out: a role that may not write never reaches the service.
      expect(paths(seen)).toEqual([`GET ${REGISTRY}/model-versions?page=1&size=20`]);
    }
    const { models: admin } = await signedIn('ADMIN', answer(201, 'training-running'));
    expect((await admin.startTraining(GOOD)).ok).toBe(true);
  });

  it('shows a role that does not read versions nothing, and sends nothing', async () => {
    for (const role of ['STORE_MANAGER', 'INVENTORY_PLANNER']) {
      const { models, seen } = await signedIn(role, answer(200, 'model-versions-page'));
      for (const result of [
        await models.listVersions({}),
        await models.getVersion(6),
        await models.listTrainings({}),
        await models.getTraining(9),
        await models.startTraining(GOOD),
        await models.publish(6, 'why'),
      ])
        expect(!result.ok && result.error.code).toBe('forbidden_role');
      expect(seen).toEqual([]);
    }
  });
});
