import type { ModelFeature, ModelVersionView, TrainingView } from './api';
import { OBSERVED_LABEL, SYNTHETIC_LABEL } from './report-table';

// What the K-means module computes from what model-registry returns (F28-05, #874): centroids in
// raw units, the size under each label, a side-by-side comparison of two versions and the plain
// statement of what a publication changes for customers. Pure functions over the views, so the
// screen and the unit tests read the same figures. Nothing here ranks one version above another:
// a comparison has no direction (RN-39).

export const FEATURES: readonly ModelFeature[] = ['R', 'F', 'M'];

/** What each variable is measured in, once a centroid is back in raw units. */
export const FEATURE_UNITS: Readonly<Record<ModelFeature, string>> = {
  R: 'days since the last purchase',
  F: 'net purchases',
  M: 'net amount',
};

/** A centroid in raw units; null where the variable was not part of the fit or cannot be put back. */
export interface RawCentroid {
  label_code: string;
  r: number | null;
  f: number | null;
  m: number | null;
}

function toRaw(coordinate: string, min: string | undefined, max: string | undefined): number | null {
  const x = Number(coordinate);
  const lo = Number(min);
  const hi = Number(max);
  if (min === undefined || max === undefined || ![x, lo, hi].every(Number.isFinite)) return null;
  return lo + x * (hi - lo);
}

/**
 * The version's centroids in raw units, in cluster order. Under MIN_MAX a coordinate is
 * `min + coordinate * (max - min)` with the bounds the version froze; under NONE it already is raw.
 * A variable the fit did not use is null, and so is any coordinate whose bounds were not sent.
 */
export function rawCentroids(version: ModelVersionView): RawCentroid[] {
  const { features, normalization } = version.parameters;
  const b = version.bounds;
  const value = (
    feature: ModelFeature,
    coordinate: string,
    min: string | undefined,
    max: string | undefined,
  ): number | null => {
    if (!features.includes(feature)) return null;
    if (normalization === 'NONE') {
      const x = Number(coordinate);
      return Number.isFinite(x) ? x : null;
    }
    return toRaw(coordinate, min, max);
  };
  return [...version.centroids]
    .sort((a, c) => a.cluster_index - c.cluster_index)
    .map((c) => ({
      label_code: c.label_code,
      r: value('R', c.r, b?.r_min, b?.r_max),
      f: value('F', c.f, b?.f_min, b?.f_max),
      m: value('M', c.m, b?.m_min, b?.m_max),
    }));
}

const NOT_AVAILABLE = '—';

/** Recency and frequency to one decimal, the net amount to two; "—" where there is no value. */
export function rawText(feature: ModelFeature, value: number | null): string {
  if (value === null) return NOT_AVAILABLE;
  return value.toFixed(feature === 'M' ? 2 : 1);
}

export interface LabelSize {
  label_code: string;
  share: number;
  /** The share of the customers the version was fitted on, to the nearest customer. */
  customers: number;
}

/**
 * How many customers sat under each label when the version was fitted. The version carries a share
 * per label and the number of customers it was fitted on, and no count per cluster: several clusters
 * can share a label (ADR-0030), so the size is the label's, not a cluster's.
 */
export function labelSizes(version: ModelVersionView): LabelSize[] {
  return version.labels.map((l) => ({
    label_code: l.label_code,
    share: Number(l.share),
    customers: Math.round(Number(l.share) * version.sample_size),
  }));
}

export const percentText = (share: number): string => `${(share * 100).toFixed(1)}%`;

export function sizeText(size: LabelSize | undefined): string {
  return size === undefined ? NOT_AVAILABLE : `${size.customers} (${percentText(size.share)})`;
}

export function originText(version: ModelVersionView): string {
  return version.dataset.origin === 'SEEDED' ? `${SYNTHETIC_LABEL} (seeded)` : OBSERVED_LABEL;
}

const orDash = (value: string | undefined): string => value ?? NOT_AVAILABLE;

/** One line of the side-by-side summary. */
export interface SummaryRow {
  name: string;
  values: [string, string];
  differs: boolean;
}

/** A centroid row of the comparison; null where that version has no such cluster. */
export interface CentroidRow {
  label: string;
  cells: [RawCentroid | null, RawCentroid | null];
}

export interface SizeRow {
  label_code: string;
  cells: [LabelSize | undefined, LabelSize | undefined];
}

export interface VersionComparison {
  versions: [ModelVersionView, ModelVersionView];
  summary: SummaryRow[];
  sizes: SizeRow[];
  centroids: CentroidRow[];
  /** Why the two cannot be read as a verdict, and what could not be shown. */
  notes: string[];
  /** Either version was fitted on seeded data (ADR-0052). */
  synthetic: boolean;
}

const featuresText = (v: ModelVersionView): string => v.parameters.features.join(', ');

function summaryOf(a: ModelVersionView, b: ModelVersionView): SummaryRow[] {
  const row = (name: string, pick: (v: ModelVersionView) => string): SummaryRow => {
    const values: [string, string] = [pick(a), pick(b)];
    return { name, values, differs: values[0] !== values[1] };
  };
  return [
    row('Version', (v) => `${v.name} (#${v.version_id})`),
    row('State', (v) => v.state),
    row('Method', (v) => v.method),
    row('Clusters (k)', (v) => (v.parameters.k === undefined ? NOT_AVAILABLE : String(v.parameters.k))),
    row('Variables', featuresText),
    row('Normalization', (v) => v.parameters.normalization),
    row('Window (days)', (v) => String(v.window_days)),
    row('Seed', (v) => orDash(v.seed)),
    row('Data origin', originText),
    row('Customers fitted on', (v) => String(v.sample_size)),
    row('Inertia', (v) => orDash(v.metrics.inertia)),
    row('Silhouette', (v) => orDash(v.metrics.silhouette)),
    row('Stability', (v) => orDash(v.metrics.stability)),
  ];
}

/** Centroids keyed by label, and by position when several clusters share one: a cluster number means something only inside its own fit (ADR-0018). */
function centroidsByKey(v: ModelVersionView): Map<string, RawCentroid> {
  const seen = new Map<string, number>();
  const out = new Map<string, RawCentroid>();
  for (const c of rawCentroids(v)) {
    const n = (seen.get(c.label_code) ?? 0) + 1;
    seen.set(c.label_code, n);
    out.set(n === 1 ? c.label_code : `${c.label_code} (${n})`, c);
  }
  return out;
}

export function compareVersions(a: ModelVersionView, b: ModelVersionView): VersionComparison {
  const sizesA = new Map(labelSizes(a).map((s) => [s.label_code, s]));
  const sizesB = new Map(labelSizes(b).map((s) => [s.label_code, s]));
  const sizes: SizeRow[] = [...new Set([...sizesA.keys(), ...sizesB.keys()])].map((label) => ({
    label_code: label,
    cells: [sizesA.get(label), sizesB.get(label)],
  }));

  const centroidsA = centroidsByKey(a);
  const centroidsB = centroidsByKey(b);
  const centroids: CentroidRow[] = [...new Set([...centroidsA.keys(), ...centroidsB.keys()])].map(
    (label) => ({ label, cells: [centroidsA.get(label) ?? null, centroidsB.get(label) ?? null] }),
  );

  const notes = [
    'This is a methodological comparison. Where two versions differ, the model changed, not customer behaviour (ADR-0064), and the table does not say that either one is better (RN-39).',
  ];
  if (a.dataset.fingerprint !== b.dataset.fingerprint)
    notes.push(
      'The versions were fitted on different data, so their inertia and silhouette are not directly comparable.',
    );
  if (
    a.parameters.k !== b.parameters.k ||
    featuresText(a) !== featuresText(b) ||
    a.parameters.normalization !== b.parameters.normalization
  )
    notes.push(
      'Inertia and silhouette depend on k, the variables and the normalization, so compare them only between versions that share all three.',
    );
  for (const v of [a, b]) {
    if (v.centroids.length === 0)
      notes.push(`${v.name} has no centroids: a ${v.method} version is not defined by clusters.`);
    else if (v.parameters.normalization === 'MIN_MAX' && v.bounds === undefined)
      notes.push(
        `The centroids of ${v.name} cannot be put back into raw units: its normalization bounds are missing.`,
      );
  }
  notes.push(
    'Centroids are in raw units: days since the last purchase, net purchases and net amount, put back from the normalization bounds each version froze.',
    'Sizes are the customers under each label when the version was fitted; a version carries no count per cluster.',
  );

  return {
    versions: [a, b],
    summary: summaryOf(a, b),
    sizes,
    centroids,
    notes,
    synthetic: a.dataset.origin === 'SEEDED' || b.dataset.origin === 'SEEDED',
  };
}

const labelsOf = (v: ModelVersionView): string[] =>
  v.centroids.length > 0
    ? [...new Set(v.centroids.map((c) => c.label_code))]
    : v.labels.map((l) => l.label_code);

/**
 * What publishing a draft changes for customers, in plain sentences, from the draft and the version
 * that is published now (null when none is). The screen shows it before the user confirms.
 */
export function publicationImpact(draft: ModelVersionView, published: ModelVersionView | null): string[] {
  const lines: string[] = [];
  if (published === null)
    lines.push(`No version is published yet, so "${draft.name}" becomes the first published version.`);
  else
    lines.push(
      `"${draft.name}" replaces "${published.name}" (${published.method}), which becomes RETIRED. Exactly one version is published at a time.`,
    );
  lines.push(
    `Every customer is scored again under "${draft.name}" in a full run. The publication is recorded first and does not wait for that run, so segments change after you confirm, not at the same instant.`,
  );
  const labels = labelsOf(draft);
  if (labels.length > 0)
    lines.push(`Customers will hold one of ${labels.length} labels: ${labels.join(', ')}.`);
  if (published !== null) {
    const before = labelsOf(published);
    const dropped = before.filter((l) => !labels.includes(l));
    const added = labels.filter((l) => !before.includes(l));
    if (dropped.length > 0) lines.push(`Labels no customer holds afterwards: ${dropped.join(', ')}.`);
    if (added.length > 0) lines.push(`Labels customers do not hold now: ${added.join(', ')}.`);
    const now = new Map(labelSizes(published).map((s) => [s.label_code, s.share]));
    const next = labelSizes(draft);
    if (next.length > 0)
      lines.push(
        `Share of customers per label when each version was fitted, published then draft: ${next
          .map(
            (s) =>
              `${s.label_code} ${now.has(s.label_code) ? percentText(now.get(s.label_code) as number) : NOT_AVAILABLE} to ${percentText(s.share)}`,
          )
          .join('; ')}.`,
      );
    if (published.method !== draft.method)
      lines.push(`The method changes from ${published.method} to ${draft.method}.`);
  }
  lines.push(
    'A customer whose segment changes because of this publication is recorded with the cause VERSION_CHANGE. It is never counted as customer behaviour (ADR-0064).',
    'Segments already written are not rewritten: the past stays as it was scored. Undoing a publication is a rollback, a new publication, never an edit.',
  );
  if (draft.dataset.origin === 'SEEDED')
    lines.push(`${SYNTHETIC_LABEL}: this draft was fitted on seeded data (ADR-0052).`);
  return lines;
}

/** One sentence on where a training stands. A running one says how long it has run. */
export function trainingText(t: TrainingView, now: number): string {
  const started = Date.parse(t.started_at);
  const seconds = (to: number): string => {
    const s = Math.max(0, Math.round((to - started) / 1000));
    return Number.isNaN(started) ? 'an unknown time' : `${s} s`;
  };
  if (t.state === 'RUNNING') return `Training #${t.training_id} is running for ${seconds(now)}.`;
  const ended = t.finished_at === undefined ? now : Date.parse(t.finished_at);
  if (t.state === 'FAILED')
    return `Training #${t.training_id} failed after ${seconds(ended)}${t.failure_code === undefined ? '' : ` (${t.failure_code})`}. Nothing was registered.`;
  const how =
    t.iterations === undefined
      ? ''
      : ` after ${t.iterations} iterations${t.converged === undefined ? '' : t.converged ? ', converged' : ', not converged'}`;
  return `Training #${t.training_id} completed in ${seconds(ended)}${how}.`;
}
