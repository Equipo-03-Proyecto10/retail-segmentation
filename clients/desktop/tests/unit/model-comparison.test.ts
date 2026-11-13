import { describe, expect, it } from 'vitest';
import type { ModelVersionView } from '../../src/shared/api';
import { COMPARISON_CRITERIA, compareModelAssignments, UNASSIGNED } from '../../src/shared/model-comparison';

const version = (version_id: number, name: string, stability?: string): ModelVersionView => ({
  version_id,
  name,
  method: version_id === 1 ? 'RFM_RULES' : 'KMEANS',
  state: 'DRAFT',
  source: 'REGISTERED',
  window_days: 180,
  parameters: { features: ['R', 'F', 'M'], normalization: 'MIN_MAX' },
  centroids: [],
  sample_size: 4,
  labels: [],
  dataset: { fingerprint: 'a'.repeat(64), origin: 'OBSERVED', row_count: 4 },
  metrics: stability === undefined ? {} : { stability },
  created_at: '2026-01-01T00:00:00Z',
});

describe('model comparison', () => {
  it('shows exactly the three predeclared F23-06 criteria and only the recorded stability score', () => {
    const result = compareModelAssignments(
      version(1, 'rules', '0.71'),
      version(2, 'clusters', '0.65'),
      [],
      [],
    );
    expect(result.criteria.map((c) => [c.name, c.margin])).toEqual([
      ['Interpretability', '0.20'],
      ['Stability', '0.10'],
      ['Usefulness for decisions', '0.05'],
    ]);
    expect(result.criteria).toEqual([
      { ...COMPARISON_CRITERIA[0] },
      { ...COMPARISON_CRITERIA[1], first_score: '0.71', second_score: '0.65' },
      { ...COMPARISON_CRITERIA[2] },
    ]);
  });

  it('counts RN-39 agreement, disagreement and one-sided customers without producing a verdict', () => {
    const result = compareModelAssignments(
      version(1, 'rules'),
      version(2, 'clusters'),
      [
        { customer_id: 'same', label_code: 'LOYAL' },
        { customer_id: 'different', label_code: 'LOYAL' },
        { customer_id: 'both-unassigned' },
        { customer_id: 'first-only', label_code: 'LOST' },
      ],
      [
        { customer_id: 'same', label_code: 'LOYAL' },
        { customer_id: 'different', label_code: 'AT_RISK' },
        { customer_id: 'both-unassigned' },
        { customer_id: 'second-only', label_code: 'CHAMPION' },
      ],
    );
    expect(result).toMatchObject({ compared: 3, agreed: 2, disagreed: 1, first_only: 1, second_only: 1 });
    expect(result.first_population).toEqual({ LOYAL: 2, [UNASSIGNED]: 1, LOST: 1 });
    expect(result.second_population).toEqual({ LOYAL: 1, AT_RISK: 1, [UNASSIGNED]: 1, CHAMPION: 1 });
    expect(result.cells.reduce((sum, cell) => sum + cell.customers, 0)).toBe(result.compared);
    expect(result.cells.filter((cell) => cell.first_label === cell.second_label)).toEqual([
      { first_label: 'LOYAL', second_label: 'LOYAL', customers: 1 },
      { first_label: UNASSIGNED, second_label: UNASSIGNED, customers: 1 },
    ]);
    expect(result).not.toHaveProperty('verdict');
    expect(result.criteria.map((criterion) => criterion.name)).not.toContain('Agreement');
  });
});
