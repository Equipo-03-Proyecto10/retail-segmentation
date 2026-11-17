import type {
  MigrationCellView,
  MigrationMatrixView,
  MigrationState,
  MigrationTotalView,
  MigrationView,
  SegmentLabelView,
} from './api';

// The rules that turn a migration matrix into a grid and prove that it adds up (F28-06, #875).
// Pure functions, shared by the screen and the unit tests, so what a test proves is what is drawn.
// Nothing here classifies a customer: states, movements and counts are the service's (RN-59).

export interface StateRef {
  state: MigrationState;
  label_code?: string;
}

export type Side = 'before' | 'after';

/** A state as a map key: labels are told apart by code, the other two states stand alone. */
export function stateKey(ref: StateRef): string {
  return ref.state === 'label' ? `label:${ref.label_code ?? ''}` : ref.state;
}

export const cellKey = (previous: StateRef, next: StateRef): string =>
  `${stateKey(previous)}>${stateKey(next)}`;

const previousOf = (c: MigrationCellView): StateRef => ({
  state: c.previous_state,
  ...(c.previous_label_code === undefined ? {} : { label_code: c.previous_label_code }),
});

const nextOf = (c: MigrationCellView): StateRef => ({
  state: c.new_state,
  ...(c.new_label_code === undefined ? {} : { label_code: c.new_label_code }),
});

export interface Axis {
  ref: StateRef;
  key: string;
  /** The words a reader sees. */
  name: string;
}

/**
 * What a state is called. A customer with no assignment at the earlier instant is New; with none at
 * the later one, "Not in later run" (RN-59). Both differ from Unassigned, which is a run that scored
 * the customer and found no purchase to label (RN-21).
 */
export function stateName(ref: StateRef, side: Side, names: ReadonlyMap<string, string>): string {
  if (ref.state === 'label') return names.get(ref.label_code ?? '') ?? ref.label_code ?? '';
  if (ref.state === 'unassigned') return 'Unassigned';
  return side === 'before' ? 'New' : 'Not in later run';
}

export function labelNames(labels: readonly SegmentLabelView[]): Map<string, string> {
  return new Map(labels.map((l) => [l.label_code, l.name]));
}

export interface Grid {
  rows: Axis[];
  columns: Axis[];
  /** The cell of each before and after pair; a pair with no customer has none. */
  cells: Map<string, MigrationCellView>;
  /** Computed from the cells, never taken from the service: what a reader adds up. */
  rowTotals: Map<string, number>;
  columnTotals: Map<string, number>;
  grandTotal: number;
}

/**
 * The full grid. Its axes come from the label vocabulary in business order (the service omits a cell
 * with no customer, so a zero cell is drawn from the axes), then Unassigned and New, which every
 * matrix may hold. A label the matrix names but the vocabulary lacks is appended, so no cell is lost.
 */
export function buildGrid(matrix: MigrationMatrixView, vocabulary: readonly SegmentLabelView[] | null): Grid {
  const names = labelNames(vocabulary ?? []);
  const codes: string[] = [...(vocabulary ?? [])]
    .sort((a, b) => a.ordinal_position - b.ordinal_position)
    .map((l) => l.label_code);
  const extra = (code: string | undefined): void => {
    if (code !== undefined && !codes.includes(code)) codes.push(code);
  };
  for (const t of matrix.totals) extra(t.label_code);
  for (const c of matrix.cells) {
    extra(c.previous_label_code);
    extra(c.new_label_code);
  }
  const states: StateRef[] = [
    ...codes.map((code): StateRef => ({ state: 'label', label_code: code })),
    { state: 'unassigned' },
    { state: 'new' },
  ];
  const axis = (side: Side): Axis[] =>
    states.map((ref) => ({ ref, key: stateKey(ref), name: stateName(ref, side, names) }));

  const cells = new Map<string, MigrationCellView>();
  const rowTotals = new Map<string, number>();
  const columnTotals = new Map<string, number>();
  let grandTotal = 0;
  for (const c of matrix.cells) {
    cells.set(cellKey(previousOf(c), nextOf(c)), c);
    const [row, column] = [stateKey(previousOf(c)), stateKey(nextOf(c))];
    rowTotals.set(row, (rowTotals.get(row) ?? 0) + c.customer_count);
    columnTotals.set(column, (columnTotals.get(column) ?? 0) + c.customer_count);
    grandTotal += c.customer_count;
  }
  return { rows: axis('before'), columns: axis('after'), cells, rowTotals, columnTotals, grandTotal };
}

export type Mismatch =
  | { kind: 'before' | 'after' | 'entries' | 'exits'; key: string; stated: number; computed: number }
  | { kind: 'customers' | 'before_sum' | 'after_sum'; stated: number; computed: number }
  | { kind: 'unlisted'; key: string };

export interface Reconciliation {
  ok: boolean;
  mismatches: Mismatch[];
}

/**
 * Proves that the matrix adds up. For every state it stands in the totals, the sum of its row of cells
 * is its before count and the sum of its column its after count; the customers who entered it are its
 * column less the customers who stayed, and those who left it its row less the same. The row totals,
 * the column totals and the customer count each sum to one number. A cell naming a state the totals
 * lack is reported, too. Anything else is a defect to show, not to hide.
 */
export function reconcile(matrix: MigrationMatrixView): Reconciliation {
  const grid = buildGrid(matrix, null);
  const mismatches: Mismatch[] = [];
  const stayed = (t: MigrationTotalView): number => grid.cells.get(cellKey(t, t))?.customer_count ?? 0;
  const listed = new Set<string>();
  let before = 0;
  let after = 0;
  for (const t of matrix.totals) {
    const key = stateKey(t);
    listed.add(key);
    before += t.before_count;
    after += t.after_count;
    const row = grid.rowTotals.get(key) ?? 0;
    const column = grid.columnTotals.get(key) ?? 0;
    if (row !== t.before_count)
      mismatches.push({ kind: 'before', key, stated: t.before_count, computed: row });
    if (column !== t.after_count)
      mismatches.push({ kind: 'after', key, stated: t.after_count, computed: column });
    if (column - stayed(t) !== t.entries)
      mismatches.push({ kind: 'entries', key, stated: t.entries, computed: column - stayed(t) });
    if (row - stayed(t) !== t.exits)
      mismatches.push({ kind: 'exits', key, stated: t.exits, computed: row - stayed(t) });
  }
  for (const key of [...grid.rowTotals.keys(), ...grid.columnTotals.keys()])
    if (!listed.has(key) && !mismatches.some((m) => m.kind === 'unlisted' && m.key === key))
      mismatches.push({ kind: 'unlisted', key });
  if (grid.grandTotal !== matrix.customer_count)
    mismatches.push({ kind: 'customers', stated: matrix.customer_count, computed: grid.grandTotal });
  if (before !== matrix.customer_count)
    mismatches.push({ kind: 'before_sum', stated: matrix.customer_count, computed: before });
  if (after !== matrix.customer_count)
    mismatches.push({ kind: 'after_sum', stated: matrix.customer_count, computed: after });
  return { ok: mismatches.length === 0, mismatches };
}

/** One mismatch in plain words, naming the state through the same names the grid uses. */
export function describeMismatch(m: Mismatch, nameOf: (key: string) => string): string {
  switch (m.kind) {
    case 'before':
      return `${nameOf(m.key)}: the row adds up to ${m.computed} customers, the before count says ${m.stated}.`;
    case 'after':
      return `${nameOf(m.key)}: the column adds up to ${m.computed} customers, the after count says ${m.stated}.`;
    case 'entries':
      return `${nameOf(m.key)}: ${m.computed} customers entered by the cells, the entries say ${m.stated}.`;
    case 'exits':
      return `${nameOf(m.key)}: ${m.computed} customers left by the cells, the exits say ${m.stated}.`;
    case 'unlisted':
      return `${nameOf(m.key)}: cells count customers in a state that has no totals.`;
    case 'customers':
      return `The cells add up to ${m.computed} customers, the matrix says ${m.stated}.`;
    case 'before_sum':
      return `The before counts add up to ${m.computed} customers, the matrix says ${m.stated}.`;
    case 'after_sum':
      return `The after counts add up to ${m.computed} customers, the matrix says ${m.stated}.`;
  }
}

/** The model version of each side, in words; an unknown one is said to be unknown. */
export function versionsText(
  matrix: Pick<MigrationMatrixView, 'from_model_version_id' | 'to_model_version_id'>,
): string {
  const word = (v: number | undefined): string => (v === undefined ? 'unknown' : `model version ${v}`);
  return `Before: ${word(matrix.from_model_version_id)}. After: ${word(matrix.to_model_version_id)}.`;
}

/**
 * Why a matrix is, or is not, methodological. The flag is the service's; these words only explain
 * which of its two causes applies, and never turn a model change into behaviour (ADR-0064).
 */
export function methodologicalText(matrix: MigrationMatrixView): string {
  if (!matrix.methodological)
    return 'Both dates fall under one model version, so a difference between them shows customer behaviour.';
  if (matrix.from_model_version_id === undefined || matrix.to_model_version_id === undefined)
    return 'A model version is not known for one of the dates, so a difference between them may come from the model and is not shown as behaviour.';
  return 'The two dates fall under different model versions, so a difference between them may come from the model and is not shown as behaviour.';
}

export const VARIABLE_NAMES: Readonly<Record<string, string>> = {
  recency: 'Recency',
  frequency: 'Frequency',
  monetary: 'Monetary',
};

export const variableName = (variable: string): string => VARIABLE_NAMES[variable] ?? variable;

/** The variables a migration names as responsible, or the reason there are none. */
export function responsibleText(
  m: Pick<MigrationView, 'responsible_variables' | 'rank_only' | 'previous_state' | 'new_state'>,
): string {
  if (m.responsible_variables.length > 0) return m.responsible_variables.map(variableName).join(', ');
  if (m.rank_only) return 'None: only the rank moved';
  if (m.previous_state !== 'label' || m.new_state !== 'label') return 'None to compare';
  return 'None';
}
