import { describe, expect, it } from 'vitest';
import type { RfmRuleSetDraft, RfmRuleSetView } from '../../src/shared/api';
import {
  blankDraft,
  cents,
  deriveDraft,
  labelOf,
  previewDistribution,
  scoreOf,
  validateDraft,
} from '../../src/shared/rfm-rules';

// The rules of a rule set (F28-04, #873) as rfm-engine's contract states them. GOOD is the recorded
// example (contracts/rfm-engine/examples/rule-set.request.json) as it stood before #1060: its LOYAL band
// still spans R 4 to 5, so CHAMPION lies inside it and the overlap rule has a case to find. With R 4 to
// 4, as the example now has it, the draft is accepted.

const GOOD: RfmRuleSetDraft = {
  name: 'Quarterly bands 2026-Q4',
  window_days: 180,
  recency_boundaries: [14, 35, 70, 120],
  frequency_boundaries: [8, 5, 3, 2],
  monetary_boundaries: ['1200.00', '800.00', '450.00', '200.00'],
  bands: [
    { label_code: 'CHAMPION', r_min: 5, r_max: 5, f_min: 5, f_max: 5, m_min: 4, m_max: 5 },
    { label_code: 'LOYAL', r_min: 4, r_max: 5, f_min: 4, f_max: 5, m_min: 3, m_max: 5 },
    { label_code: 'AT_RISK', r_min: 2, r_max: 3, f_min: 4, f_max: 5, m_min: 1, m_max: 5 },
    { label_code: 'LOST', r_min: 1, r_max: 1, f_min: 1, f_max: 3, m_min: 1, m_max: 2 },
  ],
  fallback_label_code: 'LOST',
};
const copy = (): RfmRuleSetDraft => structuredClone(GOOD);

describe('validating a draft', () => {
  it('finds the overlap when one band lies inside another', () => {
    // CHAMPION (R 5, F 5, M 4 to 5) lies inside LOYAL (R 4 to 5, F 4 to 5, M 3 to 5). The contract says
    // no two bands overlap, so the service would refuse this draft; the draft says where.
    const found = validateDraft(GOOD);
    expect(found.map((i) => i.code)).toEqual(['bands_overlap']);
    expect(found[0]?.message).toBe(
      'Band 1 (CHAMPION) and Band 2 (LOYAL) overlap: both match R 5, F 5, M 4 to 5.',
    );
  });

  it('accepts bands that do not overlap and says nothing', () => {
    const draft = copy();
    (draft.bands[1] as { r_min: number }).r_min = 4;
    (draft.bands[1] as { r_max: number }).r_max = 4;
    expect(validateDraft(draft)).toEqual([]);
    expect(validateDraft(blankDraft())).toEqual([
      { field: 'name', code: 'required', message: 'The rule set needs a name.' },
    ]);
  });

  it('names the window that is wrong', () => {
    const draft = copy();
    draft.bands = [GOOD.bands[3] as never];
    draft.window_days = 4000;
    expect(validateDraft(draft)).toEqual([
      { field: 'window_days', code: 'out_of_range', message: 'Window: 4000 days is outside 1 to 3650 days.' },
    ]);
    draft.window_days = 0;
    expect(validateDraft(draft)[0]?.message).toBe('Window: 0 days is outside 1 to 3650 days.');
    draft.window_days = Number.NaN;
    expect(validateDraft(draft)[0]?.message).toBe('Window: no number days is outside 1 to 3650 days.');
  });

  it('names the cut-point that breaks the order, with the score it belongs to', () => {
    const draft = copy();
    draft.bands = [GOOD.bands[3] as never];
    draft.recency_boundaries = [14, 10, 70, 120];
    draft.frequency_boundaries = [8, 5, 6, 2];
    draft.monetary_boundaries = ['1200.00', '800.00', '450.00', '500.00'];
    expect(validateDraft(draft).map((i) => [i.field, i.code, i.message])).toEqual([
      [
        'recency_boundaries[1]',
        'not_monotonic',
        'Recency cut-point for score 4 (10) is below the one for score 5 (14); recency cut-points must not decrease from score 5 to 2.',
      ],
      [
        'frequency_boundaries[2]',
        'not_monotonic',
        'Frequency cut-point for score 3 (6) is above the one for score 4 (5); frequency cut-points must not increase from score 5 to 2.',
      ],
      [
        'monetary_boundaries[3]',
        'not_monotonic',
        'Monetary cut-point for score 2 (500.00) is above the one for score 3 (450.00); monetary cut-points must not increase from score 5 to 2.',
      ],
    ]);
  });

  it('refuses a cut-point that is not a number of its kind', () => {
    const draft = copy();
    draft.bands = [GOOD.bands[3] as never];
    draft.frequency_boundaries = [8, 5, 3, 0];
    draft.monetary_boundaries = ['1200', '800.00', '450.00', '200.00'];
    draft.recency_boundaries = [14, 35, 70.5, 120];
    expect(validateDraft(draft).map((i) => [i.field, i.code])).toEqual([
      ['recency_boundaries[2]', 'out_of_range'],
      ['frequency_boundaries[3]', 'out_of_range'],
      ['monetary_boundaries[0]', 'invalid_format'],
    ]);
  });

  it('names a band whose minimum exceeds its maximum, by position and label', () => {
    const draft = copy();
    draft.bands = [
      { label_code: 'LOYAL', r_min: 5, r_max: 4, f_min: 1, f_max: 5, m_min: 1, m_max: 5 },
      GOOD.bands[3] as never,
    ];
    expect(validateDraft(draft)).toEqual([
      {
        field: 'bands[0].r_min',
        code: 'band_range',
        message: 'Band 1 (LOYAL): R minimum 5 is greater than its maximum 4.',
      },
    ]);
  });

  it('names the two bands that overlap and the box they share', () => {
    const draft = copy();
    draft.bands = [
      { label_code: 'A', r_min: 1, r_max: 3, f_min: 1, f_max: 5, m_min: 1, m_max: 5 },
      { label_code: 'LOST', r_min: 5, r_max: 5, f_min: 1, f_max: 5, m_min: 1, m_max: 5 },
      { label_code: 'B', r_min: 3, r_max: 4, f_min: 2, f_max: 3, m_min: 5, m_max: 5 },
    ];
    expect(validateDraft(draft)).toEqual([
      {
        field: 'bands[2]',
        code: 'bands_overlap',
        message: 'Band 1 (A) and Band 3 (B) overlap: both match R 3, F 2 to 3, M 5.',
      },
    ]);
  });

  it('wants a name, a label for every band, at least one band and a fallback', () => {
    const draft = copy();
    draft.name = '   ';
    draft.fallback_label_code = '';
    draft.bands = [{ label_code: '', r_min: 1, r_max: 1, f_min: 1, f_max: 1, m_min: 1, m_max: 1 }];
    expect(validateDraft(draft).map((i) => [i.field, i.message])).toEqual([
      ['name', 'The rule set needs a name.'],
      ['bands[0].label_code', 'Band 1 needs a label.'],
      ['fallback_label_code', 'The fallback label is needed: it is taken when no band matches.'],
    ]);
    draft.bands = [];
    draft.name = 'x'.repeat(81);
    expect(validateDraft(draft).map((i) => i.code)).toEqual(['too_long', 'required', 'required']);
  });

  it('refuses a score outside 1 to 5 and does not also call it an overlap', () => {
    const draft = copy();
    draft.bands = [
      { label_code: 'A', r_min: 0, r_max: 9, f_min: 1, f_max: 5, m_min: 1, m_max: 5 },
      { label_code: 'B', r_min: 1, r_max: 5, f_min: 1, f_max: 5, m_min: 1, m_max: 5 },
    ];
    expect(validateDraft(draft).map((i) => [i.field, i.code])).toEqual([['bands[0].r_min', 'out_of_range']]);
  });
});

describe('scoring and labels', () => {
  it('gives the highest score whose cut-point is met, and 1 when none is', () => {
    const r = GOOD.recency_boundaries;
    expect([0, 14, 15, 35, 36, 70, 71, 120, 121].map((v) => scoreOf('recency', v, r))).toEqual([
      5, 5, 4, 4, 3, 3, 2, 2, 1,
    ]);
    const f = GOOD.frequency_boundaries;
    expect([9, 8, 7, 5, 3, 2, 1].map((v) => scoreOf('frequency', v, f))).toEqual([5, 5, 4, 4, 3, 2, 1]);
    const m = GOOD.monetary_boundaries.map(cents);
    expect(
      [cents('1200.00'), cents('1199.99'), cents('200.00'), cents('199.99')].map((v) =>
        scoreOf('monetary', v, m),
      ),
    ).toEqual([5, 4, 2, 1]);
  });

  it('takes the band that matches, and the fallback when none does', () => {
    expect(labelOf(GOOD, 5, 5, 5)).toEqual({ label_code: 'CHAMPION', via_fallback: false });
    expect(labelOf(GOOD, 2, 4, 1)).toEqual({ label_code: 'AT_RISK', via_fallback: false });
    expect(labelOf(GOOD, 3, 1, 5)).toEqual({ label_code: 'LOST', via_fallback: true });
  });
});

describe('the preview', () => {
  const draft = {
    ...copy(),
    bands: [GOOD.bands[1] as never, GOOD.bands[2] as never, GOOD.bands[3] as never],
  };

  it('counts customers per label, largest first, and says which took the fallback', () => {
    const profiles = [
      { r_days: 30, f_count: 9, m_total: '842.50' }, // 4 5 4: LOYAL
      { r_days: 5, f_count: 9, m_total: '900.00' }, // 5 5 4: LOYAL
      { r_days: 100, f_count: 5, m_total: '50.00' }, // 2 4 1: AT_RISK
      { r_days: 400, f_count: 1, m_total: '10.00' }, // 1 1 1: LOST (a band)
      { r_days: 20, f_count: 1, m_total: '5000.00' }, // 4 1 5: no band, fallback
    ];
    expect(previewDistribution(draft, profiles)).toEqual(
      [
        { label_code: 'LOYAL', customers: 2, via_fallback: 0, share: '0.4000' },
        { label_code: 'AT_RISK', customers: 1, via_fallback: 0, share: '0.2000' },
        { label_code: 'LOST', customers: 2, via_fallback: 1, share: '0.4000' },
      ].sort((a, b) => b.customers - a.customers || a.label_code.localeCompare(b.label_code)),
    );
  });

  it('changes when the cut-points change, for the same customers', () => {
    const profiles = [{ r_days: 30, f_count: 9, m_total: '842.50' }];
    expect(previewDistribution(draft, profiles)[0]?.label_code).toBe('LOYAL');
    const stricter = { ...draft, recency_boundaries: [5, 10, 20, 25] };
    // Recency 30 now scores 1, which no kept band matches.
    expect(previewDistribution(stricter, profiles)).toEqual([
      { label_code: 'LOST', customers: 1, via_fallback: 1, share: '1.0000' },
    ]);
    expect(previewDistribution(draft, [])).toEqual([]);
  });
});

describe('deriving a new version', () => {
  const stored: RfmRuleSetView = {
    ...GOOD,
    rule_set_id: 3,
    published: true,
    created_at: '2026-09-01T12:00:00Z',
  };

  it('copies the rule set into a draft with a new name and leaves the stored one alone', () => {
    const before = structuredClone(stored);
    const derived = deriveDraft(stored);
    expect(derived.name).toBe('Quarterly bands 2026-Q4 (new version)');
    expect(derived).not.toHaveProperty('rule_set_id');
    expect(derived).not.toHaveProperty('published');
    derived.bands[0]!.label_code = 'CHANGED';
    derived.recency_boundaries[0] = 1;
    expect(stored).toEqual(before);
  });

  it('keeps the name within its 80 characters', () => {
    const long = deriveDraft({ ...stored, name: 'n'.repeat(80) });
    expect(long.name).toHaveLength(80);
    expect(long.name.endsWith(' (new version)')).toBe(true);
  });
});
