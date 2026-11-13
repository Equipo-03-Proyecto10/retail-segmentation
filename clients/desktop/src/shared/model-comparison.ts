import type {
  AgreementCellView,
  ModelComparisonView,
  ModelCriterionView,
  ModelVersionView,
  VersionAssignmentView,
} from './api';

export const UNASSIGNED = 'Unassigned';

/** The criteria fixed before F23-06's results, copied without turning agreement into a criterion. */
export const COMPARISON_CRITERIA = [
  {
    name: 'Interpretability',
    question: 'Does each segment behave as its label says?',
    measure: "Share of the measurable claims in each populated label's description that hold.",
    margin: '0.20',
  },
  {
    name: 'Stability',
    question: 'Do the same customers keep their segment when something that should not matter changes?',
    measure: 'Mean of bootstrap-resample ARI and shifted-window ARI.',
    margin: '0.10',
  },
  {
    name: 'Usefulness for decisions',
    question: 'Do the segments say who will spend next?',
    measure: 'Mean adjusted R-squared of later log spend explained by segment over two origins.',
    margin: '0.05',
  },
] as const;

const label = (row: VersionAssignmentView): string => row.label_code ?? UNASSIGNED;

function population(rows: readonly VersionAssignmentView[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const row of rows) counts[label(row)] = (counts[label(row)] ?? 0) + 1;
  return counts;
}

function criteria(first: ModelVersionView, second: ModelVersionView): ModelCriterionView[] {
  return COMPARISON_CRITERIA.map((criterion) => ({
    ...criterion,
    ...(criterion.name === 'Stability' && first.metrics.stability !== undefined
      ? { first_score: first.metrics.stability }
      : {}),
    ...(criterion.name === 'Stability' && second.metrics.stability !== undefined
      ? { second_score: second.metrics.stability }
      : {}),
  }));
}

/** Applies RN-39 to one latest row per customer from each version. */
export function compareModelAssignments(
  first: ModelVersionView,
  second: ModelVersionView,
  firstRows: readonly VersionAssignmentView[],
  secondRows: readonly VersionAssignmentView[],
): ModelComparisonView {
  const a = new Map(firstRows.map((row) => [row.customer_id, label(row)]));
  const b = new Map(secondRows.map((row) => [row.customer_id, label(row)]));
  let agreed = 0;
  let disagreed = 0;
  let firstOnly = 0;
  let secondOnly = 0;
  const cells = new Map<string, AgreementCellView>();
  for (const [customer, firstLabel] of a) {
    const secondLabel = b.get(customer);
    if (secondLabel === undefined) {
      firstOnly += 1;
      continue;
    }
    if (firstLabel === secondLabel) agreed += 1;
    else disagreed += 1;
    const key = `${firstLabel}\u0000${secondLabel}`;
    const current = cells.get(key);
    cells.set(key, {
      first_label: firstLabel,
      second_label: secondLabel,
      customers: (current?.customers ?? 0) + 1,
    });
  }
  for (const customer of b.keys()) if (!a.has(customer)) secondOnly += 1;
  return {
    first,
    second,
    criteria: criteria(first, second),
    first_population: population(firstRows),
    second_population: population(secondRows),
    compared: agreed + disagreed,
    agreed,
    disagreed,
    first_only: firstOnly,
    second_only: secondOnly,
    cells: [...cells.values()].sort(
      (x, y) => x.first_label.localeCompare(y.first_label) || x.second_label.localeCompare(y.second_label),
    ),
  };
}
