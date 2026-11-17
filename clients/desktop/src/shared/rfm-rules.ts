// The rules of an RFM rule set (F28-04, #873), as rfm-engine's contract states them (RN-65, ADR-0063).
// Pure, so the main process refuses what the service would refuse, the screen explains it in the same
// words, and a unit test can pin both. A rule set is a window, four cut-points for each measure, bands
// that map a box of scores to a label, and a fallback label.
//
// A cut-point list is in the order of the scores 5, 4, 3 and 2: recency (days) is non-decreasing, since
// the longer ago the purchase the lower the score; frequency and monetary are non-increasing. Bands do
// not overlap, so a scored customer matches at most one; one that matches none takes the fallback (RN-46).

import type { RfmBandView, RfmPreviewLabelView, RfmRuleSetDraft, RfmRuleSetView } from './api';

export const WINDOW_RANGE: readonly [number, number] = [1, 3650];
export const NAME_MAX = 80;
/** The score each cut-point list starts at, in order. */
export const SCORES = [5, 4, 3, 2] as const;
const MONEY = /^[0-9]+\.[0-9]{2}$/;

export interface DraftIssue {
  /** The field as the service names it, with the index of a band or cut-point when there is one. */
  field: string;
  code:
    | 'required'
    | 'too_long'
    | 'out_of_range'
    | 'invalid_format'
    | 'not_monotonic'
    | 'band_range'
    | 'bands_overlap';
  /** What is wrong, naming the band or the window. */
  message: string;
}

const MEASURES = [
  { key: 'recency_boundaries', label: 'Recency', unit: 'days', direction: 'rise' },
  { key: 'frequency_boundaries', label: 'Frequency', unit: 'purchases', direction: 'fall' },
  { key: 'monetary_boundaries', label: 'Monetary', unit: 'currency', direction: 'fall' },
] as const;

const AXES = [
  { letter: 'R', min: 'r_min', max: 'r_max', name: 'recency' },
  { letter: 'F', min: 'f_min', max: 'f_max', name: 'frequency' },
  { letter: 'M', min: 'm_min', max: 'm_max', name: 'monetary' },
] as const;

/** Money in whole cents, so two amounts compare without a rounding error. */
export function cents(value: string): number {
  const [whole = '0', fraction = '00'] = value.split('.');
  return Number(whole) * 100 + Number(fraction);
}

const isScore = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 5;

/** How a band is named in a message: its position in the list, and its label when it has one. */
export function bandName(bands: readonly RfmBandView[], index: number): string {
  const label = bands[index]?.label_code.trim() ?? '';
  return label === '' ? `Band ${index + 1}` : `Band ${index + 1} (${label})`;
}

/** The box a band covers on one axis, as a range of scores. */
const span = (low: number, high: number): string => (low === high ? String(low) : `${low} to ${high}`);

/**
 * Every reason a draft cannot be saved, each naming the band or the window that is wrong. An empty
 * list means the draft is one the service accepts (it still answers for a name already used).
 */
export function validateDraft(draft: RfmRuleSetDraft): DraftIssue[] {
  const issues: DraftIssue[] = [];
  const add = (field: string, code: DraftIssue['code'], message: string): void => {
    issues.push({ field, code, message });
  };

  const name = draft.name.trim();
  if (name === '') add('name', 'required', 'The rule set needs a name.');
  else if (draft.name.length > NAME_MAX)
    add('name', 'too_long', `The name is ${draft.name.length} characters; at most ${NAME_MAX} are allowed.`);

  const [low, high] = WINDOW_RANGE;
  if (!Number.isInteger(draft.window_days) || draft.window_days < low || draft.window_days > high)
    add(
      'window_days',
      'out_of_range',
      `Window: ${Number.isFinite(draft.window_days) ? draft.window_days : 'no number'} days is outside ${low} to ${high} days.`,
    );

  for (const measure of MEASURES) {
    const values = draft[measure.key] as readonly (number | string)[];
    if (values.length !== 4) {
      add(measure.key, 'invalid_format', `${measure.label} needs four cut-points, for scores 5, 4, 3 and 2.`);
      continue;
    }
    const numbers: (number | null)[] = values.map((value, index) => {
      const field = `${measure.key}[${index}]`;
      const score = SCORES[index];
      if (measure.key === 'monetary_boundaries') {
        if (typeof value !== 'string' || !MONEY.test(value)) {
          add(
            field,
            'invalid_format',
            `${measure.label} cut-point for score ${score} must be an amount with two decimals, such as 450.00.`,
          );
          return null;
        }
        return cents(value);
      }
      const least = measure.key === 'frequency_boundaries' ? 1 : 0;
      if (typeof value !== 'number' || !Number.isInteger(value) || value < least) {
        add(
          field,
          'out_of_range',
          `${measure.label} cut-point for score ${score} must be a whole number of ${measure.unit}, ${least} or more.`,
        );
        return null;
      }
      return value;
    });
    for (let i = 1; i < 4; i += 1) {
      const before = numbers[i - 1];
      const here = numbers[i];
      if (before == null || here == null) continue;
      const wrong = measure.direction === 'rise' ? here < before : here > before;
      if (wrong)
        add(
          `${measure.key}[${i}]`,
          'not_monotonic',
          `${measure.label} cut-point for score ${SCORES[i]} (${values[i]}) ${
            measure.direction === 'rise' ? 'is below' : 'is above'
          } the one for score ${SCORES[i - 1]} (${values[i - 1]}); ${measure.label.toLowerCase()} cut-points must ${
            measure.direction === 'rise' ? 'not decrease' : 'not increase'
          } from score 5 to 2.`,
        );
    }
  }

  if (draft.bands.length === 0) add('bands', 'required', 'The rule set needs at least one band.');
  const sound = new Set<number>();
  draft.bands.forEach((band, index) => {
    const who = bandName(draft.bands, index);
    let fine = true;
    if (band.label_code.trim() === '') {
      add(`bands[${index}].label_code`, 'required', `${who} needs a label.`);
      fine = false;
    }
    for (const axis of AXES) {
      const lo = band[axis.min];
      const hi = band[axis.max];
      if (!isScore(lo) || !isScore(hi)) {
        add(
          `bands[${index}].${axis.min}`,
          'out_of_range',
          `${who}: the ${axis.name} scores must be whole numbers from 1 to 5.`,
        );
        fine = false;
      } else if (lo > hi) {
        add(
          `bands[${index}].${axis.min}`,
          'band_range',
          `${who}: ${axis.letter} minimum ${lo} is greater than its maximum ${hi}.`,
        );
        fine = false;
      }
    }
    if (fine) sound.add(index);
  });
  for (let a = 0; a < draft.bands.length; a += 1) {
    for (let b = a + 1; b < draft.bands.length; b += 1) {
      if (!sound.has(a) || !sound.has(b)) continue;
      const first = draft.bands[a] as RfmBandView;
      const second = draft.bands[b] as RfmBandView;
      const shared = AXES.map((axis) => [
        Math.max(first[axis.min], second[axis.min]),
        Math.min(first[axis.max], second[axis.max]),
      ]);
      if (shared.every(([lo, hi]) => (lo as number) <= (hi as number)))
        add(
          `bands[${b}]`,
          'bands_overlap',
          `${bandName(draft.bands, a)} and ${bandName(draft.bands, b)} overlap: both match ${AXES.map(
            (axis, i) => `${axis.letter} ${span(...(shared[i] as [number, number]))}`,
          ).join(', ')}.`,
        );
    }
  }

  if (draft.fallback_label_code.trim() === '')
    add('fallback_label_code', 'required', 'The fallback label is needed: it is taken when no band matches.');
  return issues;
}

/** The score a value earns on one measure, from its four cut-points; 1 when none is met. */
export function scoreOf(
  measure: 'recency' | 'frequency' | 'monetary',
  value: number,
  cuts: readonly number[],
): number {
  for (let i = 0; i < 4; i += 1) {
    const cut = cuts[i] as number;
    if (measure === 'recency' ? value <= cut : value >= cut) return SCORES[i] as number;
  }
  return 1;
}

/** The label a customer's scores take under a draft, and whether it is the fallback. */
export function labelOf(
  draft: Pick<RfmRuleSetDraft, 'bands' | 'fallback_label_code'>,
  r: number,
  f: number,
  m: number,
): { label_code: string; via_fallback: boolean } {
  const band = draft.bands.find(
    (b) => b.r_min <= r && r <= b.r_max && b.f_min <= f && f <= b.f_max && b.m_min <= m && m <= b.m_max,
  );
  return band === undefined
    ? { label_code: draft.fallback_label_code, via_fallback: true }
    : { label_code: band.label_code, via_fallback: false };
}

/** What one scored customer is made of: the raw R, F and M under the basis rule set's window. */
export interface RawProfile {
  r_days: number;
  f_count: number;
  m_total: string;
}

/**
 * How many customers each label would hold if the draft's cut-points and bands scored the raw figures
 * of `profiles`. Largest first, then by label, so the screen and a test read the same order. A label
 * that only the fallback gives is counted under the fallback label, with its customers marked.
 */
export function previewDistribution(
  draft: RfmRuleSetDraft,
  profiles: readonly RawProfile[],
): RfmPreviewLabelView[] {
  const counts = new Map<string, { customers: number; via_fallback: number }>();
  for (const p of profiles) {
    const r = scoreOf('recency', p.r_days, draft.recency_boundaries);
    const f = scoreOf('frequency', p.f_count, draft.frequency_boundaries);
    const m = scoreOf('monetary', cents(p.m_total), draft.monetary_boundaries.map(cents));
    const { label_code, via_fallback } = labelOf(draft, r, f, m);
    const row = counts.get(label_code) ?? { customers: 0, via_fallback: 0 };
    row.customers += 1;
    if (via_fallback) row.via_fallback += 1;
    counts.set(label_code, row);
  }
  const total = profiles.length;
  return [...counts.entries()]
    .map(([label_code, row]) => ({
      label_code,
      customers: row.customers,
      via_fallback: row.via_fallback,
      share: total === 0 ? '0.0000' : (row.customers / total).toFixed(4),
    }))
    .sort((a, b) => b.customers - a.customers || a.label_code.localeCompare(b.label_code));
}

/** A new draft that starts from a stored rule set. The stored one is never edited (ADR-0064). */
export function deriveDraft(source: RfmRuleSetView): RfmRuleSetDraft {
  const suffix = ' (new version)';
  const name =
    source.name.length + suffix.length > NAME_MAX
      ? source.name.slice(0, NAME_MAX - suffix.length)
      : source.name;
  return {
    name: `${name}${suffix}`,
    window_days: source.window_days,
    recency_boundaries: [...source.recency_boundaries],
    frequency_boundaries: [...source.frequency_boundaries],
    monetary_boundaries: [...source.monetary_boundaries],
    bands: source.bands.map((b) => ({ ...b })),
    fallback_label_code: source.fallback_label_code,
  };
}

/** An empty draft with the shape of the contract's recorded example, so the screen has something to edit. */
export function blankDraft(): RfmRuleSetDraft {
  return {
    name: '',
    window_days: 180,
    recency_boundaries: [14, 35, 70, 120],
    frequency_boundaries: [8, 5, 3, 2],
    monetary_boundaries: ['1200.00', '800.00', '450.00', '200.00'],
    bands: [
      { label_code: 'LOYAL', r_min: 4, r_max: 5, f_min: 4, f_max: 5, m_min: 3, m_max: 5 },
      { label_code: 'LOST', r_min: 1, r_max: 1, f_min: 1, f_max: 3, m_min: 1, m_max: 2 },
    ],
    fallback_label_code: 'LOST',
  };
}
