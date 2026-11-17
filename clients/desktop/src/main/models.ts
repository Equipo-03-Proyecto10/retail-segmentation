import type {
  CentroidView,
  DataOrigin,
  DatasetView,
  MetricsView,
  ModelFeature,
  ModelMethod,
  ModelParametersView,
  ModelVersionView,
  Normalization,
  Paged,
  PublicationView,
  Result,
  TrainingDraft,
  TrainingState,
  TrainingView,
  VersionSource,
  VersionState,
} from '../shared/api';
import { FEATURES } from '../shared/kmeans';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  id,
  invalidInput,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import type { Fields } from './xml';

// K-means models (F28-05, #874): model-registry read and written through the transport, in XML
// validated against its XSD. A training is started and followed here; its result is a DRAFT version
// the service registers, and publishing is the service's decision as much as ours: the role is
// checked here before anything is sent, and the service still answers every call. What a version
// holds is immutable (ADR-0064), so there is no operation that edits or deletes one.

const MAX_ID = Number.MAX_SAFE_INTEGER;
const NAME_MAX = 80;
const REASON_MAX = 500;
const WINDOW_MAX = 3650;
const K_RANGE: [number, number] = [2, 20];
const ITERATIONS_RANGE: [number, number] = [1, 1000];
const MAX_SEED = 9223372036854775807n;
const MIN_SEED = -9223372036854775808n;
const METHODS: readonly ModelMethod[] = ['RFM_RULES', 'KMEANS'];
const VERSION_STATES: readonly VersionState[] = ['DRAFT', 'PUBLISHED', 'RETIRED'];
const TRAINING_STATES: readonly TrainingState[] = ['RUNNING', 'COMPLETED', 'FAILED'];
const NORMALIZATIONS: readonly Normalization[] = ['NONE', 'MIN_MAX'];
const DECIMAL = /^[0-9]{1,12}(\.[0-9]{1,12})?$/;
const WHOLE = /^-?[0-9]{1,19}$/;

interface ParametersXml {
  k?: string;
  max_iterations?: string;
  tolerance?: string;
  features: ModelFeature[];
  normalization: Normalization;
}

interface MetricsXml {
  inertia?: string;
  silhouette?: string;
  stability?: string;
}

interface DatasetXml {
  fingerprint: string;
  origin: DataOrigin;
  row_count: string;
}

interface VersionXml {
  version_id: string;
  name: string;
  method: ModelMethod;
  state: VersionState;
  source: VersionSource;
  window_days: string;
  seed?: string;
  parameters: ParametersXml;
  frozen_scoring: {
    centroids?: { cluster_index: string; label_code: string; r: string; f: string; m: string }[];
    normalization_bounds?: Record<'r_min' | 'r_max' | 'f_min' | 'f_max' | 'm_min' | 'm_max', string>;
  };
  reference_distribution: { sample_size: string; labels: { label_code: string; share: string }[] };
  dataset: DatasetXml;
  metrics: MetricsXml;
  training_id?: string;
  created_at: string;
  published_at?: string;
  retired_at?: string;
}

interface TrainingXml {
  training_id: string;
  state: TrainingState;
  version_name: string;
  window_days: string;
  seed: string;
  parameters: ParametersXml;
  dataset?: DatasetXml;
  metrics?: MetricsXml;
  iterations?: string;
  converged?: string;
  version_id?: string;
  started_at: string;
  finished_at?: string;
  failure_code?: string;
}

interface PublicationXml {
  publication_id: string;
  version_id: string;
  action: 'PUBLISH' | 'ROLLBACK';
  previous_version_id?: string;
  reason: string;
  published_at: string;
}

const present = <T>(value: T | undefined, key: string): Record<string, T> =>
  value === undefined ? {} : { [key]: value };

function parametersOf(x: ParametersXml): ModelParametersView {
  return {
    ...(x.k === undefined ? {} : { k: Number(x.k) }),
    ...(x.max_iterations === undefined ? {} : { max_iterations: Number(x.max_iterations) }),
    ...present(x.tolerance, 'tolerance'),
    features: x.features,
    normalization: x.normalization,
  };
}

function datasetOf(x: DatasetXml): DatasetView {
  return { fingerprint: x.fingerprint, origin: x.origin, row_count: Number(x.row_count) };
}

function metricsOf(x: MetricsXml): MetricsView {
  return {
    ...present(x.inertia, 'inertia'),
    ...present(x.silhouette, 'silhouette'),
    ...present(x.stability, 'stability'),
  };
}

function versionOf(x: VersionXml): ModelVersionView {
  const centroids: CentroidView[] = (x.frozen_scoring.centroids ?? []).map((c) => ({
    cluster_index: Number(c.cluster_index),
    label_code: c.label_code,
    r: c.r,
    f: c.f,
    m: c.m,
  }));
  return {
    version_id: Number(x.version_id),
    name: x.name,
    method: x.method,
    state: x.state,
    source: x.source,
    window_days: Number(x.window_days),
    ...present(x.seed, 'seed'),
    parameters: parametersOf(x.parameters),
    centroids,
    ...(x.frozen_scoring.normalization_bounds === undefined
      ? {}
      : { bounds: { ...x.frozen_scoring.normalization_bounds } }),
    sample_size: Number(x.reference_distribution.sample_size),
    labels: x.reference_distribution.labels.map((l) => ({ label_code: l.label_code, share: l.share })),
    dataset: datasetOf(x.dataset),
    metrics: metricsOf(x.metrics),
    ...(x.training_id === undefined ? {} : { training_id: Number(x.training_id) }),
    created_at: x.created_at,
    ...present(x.published_at, 'published_at'),
    ...present(x.retired_at, 'retired_at'),
  };
}

function trainingOf(x: TrainingXml): TrainingView {
  return {
    training_id: Number(x.training_id),
    state: x.state,
    version_name: x.version_name,
    window_days: Number(x.window_days),
    seed: x.seed,
    parameters: parametersOf(x.parameters),
    ...(x.dataset === undefined ? {} : { dataset: datasetOf(x.dataset) }),
    ...(x.metrics === undefined ? {} : { metrics: metricsOf(x.metrics) }),
    ...(x.iterations === undefined ? {} : { iterations: Number(x.iterations) }),
    ...(x.converged === undefined ? {} : { converged: x.converged === 'true' || x.converged === '1' }),
    ...(x.version_id === undefined ? {} : { version_id: Number(x.version_id) }),
    started_at: x.started_at,
    ...present(x.finished_at, 'finished_at'),
    ...present(x.failure_code, 'failure_code'),
  };
}

function publicationOf(x: PublicationXml): PublicationView {
  return {
    publication_id: Number(x.publication_id),
    version_id: Number(x.version_id),
    action: x.action,
    ...(x.previous_version_id === undefined ? {} : { previous_version_id: Number(x.previous_version_id) }),
    reason: x.reason,
    published_at: x.published_at,
  };
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], label: string): T | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' || !allowed.includes(value as T))
    throw invalidInput(`${label} is not one of: ${allowed.join(', ')}.`);
  return value as T;
}

/** A whole number within the XSD's facets; the refusal names its field. */
function whole(f: FieldChecks, field: string, value: unknown, [min, max]: [number, number]): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max) {
    f.details.push({ field, code: 'out_of_range' });
    return 0;
  }
  return value;
}

/** A positive decimal (the XSD says greater than 0), kept as the text typed so no digit is rounded. */
function tolerance(f: FieldChecks, value: unknown): string {
  if (typeof value !== 'string' || !DECIMAL.test(value.trim())) {
    f.details.push({ field: 'tolerance', code: 'invalid_format' });
    return '';
  }
  const text = value.trim();
  if (!(Number(text) > 0)) f.details.push({ field: 'tolerance', code: 'out_of_range' });
  return text;
}

function features(f: FieldChecks, value: unknown): ModelFeature[] {
  if (!Array.isArray(value) || value.some((x) => !FEATURES.includes(x as ModelFeature))) {
    f.details.push({ field: 'features', code: 'invalid_format' });
    return [];
  }
  const chosen = FEATURES.filter((x) => value.includes(x));
  if (chosen.length === 0) f.details.push({ field: 'features', code: 'required' });
  return [...chosen];
}

function normalizationOf(f: FieldChecks, value: unknown): Normalization {
  if (typeof value === 'string' && NORMALIZATIONS.includes(value as Normalization))
    return value as Normalization;
  f.details.push({
    field: 'normalization',
    code: value === undefined || value === '' ? 'required' : 'invalid_format',
  });
  return 'MIN_MAX';
}

/** An optional seed: a signed 64-bit whole number as text, or nothing so the service chooses and records one. */
function seed(f: FieldChecks, value: unknown): string | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' || !WHOLE.test(value.trim())) {
    f.details.push({ field: 'seed', code: 'invalid_format' });
    return undefined;
  }
  const n = BigInt(value.trim());
  if (n < MIN_SEED || n > MAX_SEED) f.details.push({ field: 'seed', code: 'out_of_range' });
  return n.toString();
}

export class Models {
  constructor(private readonly office: Backoffice) {}

  /** Refuses a role that may not call the operation, before anything is sent (x-mosaiq-roles). */
  private mayCall(operationId: string, doing: string): Result<never> | null {
    const state = this.office.state();
    if (state.status === 'signed_in' && state.operations.includes(operationId)) return null;
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return { ok: false, error: localError('forbidden_role', `Your role may not ${doing}.`) };
  }

  listVersions(query: unknown): Promise<Result<Paged<ModelVersionView>>> {
    return checked(this.office, async () => {
      const refused = this.mayCall('listModelVersions', 'read model versions');
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const method = oneOf(q.method, METHODS, 'The method');
      if (method !== undefined) params.set('method', method);
      const state = oneOf(q.state, VERSION_STATES, 'The state');
      if (state !== undefined) params.set('state', state);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/model-versions?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.ModelVersions as { ModelVersion?: VersionXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.ModelVersion ?? []).map(versionOf), page: pageOf(data) } };
    });
  }

  getVersion(versionId: unknown): Promise<Result<ModelVersionView>> {
    return checked(this.office, async () => {
      const refused = this.mayCall('getModelVersion', 'read model versions');
      if (refused !== null) return refused;
      const vid = id(versionId, MAX_ID, 'The version');
      const res = await this.office.authed({ method: 'GET', path: `/v1/model-versions/${vid}` });
      if (!res.ok) return failed(res);
      return { ok: true, value: versionOf(res.data?.ModelVersion as VersionXml) };
    });
  }

  listTrainings(query: unknown): Promise<Result<Paged<TrainingView>>> {
    return checked(this.office, async () => {
      const refused = this.mayCall('listModelTrainings', 'read trainings');
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const state = oneOf(q.state, TRAINING_STATES, 'The state');
      if (state !== undefined) params.set('state', state);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/model-trainings?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.ModelTrainings as { ModelTraining?: TrainingXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.ModelTraining ?? []).map(trainingOf), page: pageOf(data) } };
    });
  }

  getTraining(trainingId: unknown): Promise<Result<TrainingView>> {
    return checked(this.office, async () => {
      const refused = this.mayCall('getModelTraining', 'read trainings');
      if (refused !== null) return refused;
      const tid = id(trainingId, MAX_ID, 'The training');
      const res = await this.office.authed({ method: 'GET', path: `/v1/model-trainings/${tid}` });
      if (!res.ok) return failed(res);
      return { ok: true, value: trainingOf(res.data?.ModelTraining as TrainingXml) };
    });
  }

  /**
   * Starts a K-means training. Every value is checked first, so a refusal names its field and
   * nothing is sent; the service checks them again and answers 422 `invalid_body` for the rest.
   */
  startTraining(draft: unknown): Promise<Result<TrainingView>> {
    return checked(this.office, async () => {
      const refused = this.mayCall('createModelTraining', 'start a training');
      if (refused !== null) return refused;
      const d = record(draft, 'The training') as Partial<Record<keyof TrainingDraft, unknown>>;
      const f = new FieldChecks();
      const name = f.text('version_name', d.version_name, NAME_MAX).trim();
      const windowDays = whole(f, 'window_days', d.window_days, [1, WINDOW_MAX]);
      const k = whole(f, 'k', d.k, K_RANGE);
      const iterations = whole(f, 'max_iterations', d.max_iterations, ITERATIONS_RANGE);
      const tol = tolerance(f, d.tolerance);
      const chosen = features(f, d.features);
      const normalization = normalizationOf(f, d.normalization);
      const chosenSeed = seed(f, d.seed);
      f.done();
      // The XSD fixes this order: version_name, method, window_days, seed, parameters.
      const fields: Fields = {
        version_name: name,
        method: 'KMEANS',
        window_days: String(windowDays),
        ...(chosenSeed === undefined ? {} : { seed: chosenSeed }),
        parameters: {
          k: String(k),
          max_iterations: String(iterations),
          tolerance: tol,
          features: chosen,
          normalization,
        },
      };
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/model-trainings',
        body: { root: 'ModelTrainingRequest', fields },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: trainingOf(res.data?.ModelTraining as TrainingXml) };
    });
  }

  /** The one write on a version: it publishes a DRAFT, with the reason the publication keeps. */
  publish(versionId: unknown, reason: unknown): Promise<Result<PublicationView>> {
    return checked(this.office, async () => {
      const refused = this.mayCall('publishModelVersion', 'publish a version');
      if (refused !== null) return refused;
      const vid = id(versionId, MAX_ID, 'The version');
      const f = new FieldChecks();
      const why = f.text('reason', reason, REASON_MAX);
      f.done();
      const res = await this.office.authed({
        method: 'POST',
        path: `/v1/model-versions/${vid}/publications`,
        body: { root: 'ModelPublicationRequest', fields: { reason: why.trim() } },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: publicationOf(res.data?.ModelPublication as PublicationXml) };
    });
  }
}
