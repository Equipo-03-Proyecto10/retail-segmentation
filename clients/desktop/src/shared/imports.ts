import type { DuplicateKind, ImportRejectionView, SaleImportView } from './api';

// Pure rules of the import screen (F28-02, #871), so a unit test can pin them: the identity the
// counts must satisfy, and how rejections are told apart and filtered.

export interface Reconciliation {
  received: number;
  accepted: number;
  rejected: number;
  pending: number;
  /** accepted + rejected + pending. */
  sum: number;
  /** received = accepted + rejected + pending, which the service keeps in every status (ADR-0070 section 3). */
  holds: boolean;
  /** The service reports a count as final only at COMPLETED; before that, pending rows are still being applied. */
  final: boolean;
}

export function reconcile(
  view: Pick<SaleImportView, 'status' | 'received' | 'accepted' | 'rejected' | 'pending'>,
): Reconciliation {
  const sum = view.accepted + view.rejected + view.pending;
  return {
    received: view.received,
    accepted: view.accepted,
    rejected: view.rejected,
    pending: view.pending,
    sum,
    holds: sum === view.received,
    final: view.status === 'COMPLETED' && view.pending === 0,
  };
}

/** The identity as one sentence of figures: "Received 9 = accepted 4 + rejected 3 + pending 2". */
export function identityText(r: Reconciliation): string {
  return `Received ${r.received} = accepted ${r.accepted} + rejected ${r.rejected} + pending ${r.pending}`;
}

export const KIND_LABEL: Readonly<Record<DuplicateKind, string>> = {
  FILE: 'Repeat in the file',
  RECORDED_SALE: 'Already recorded',
};

/** What the Kind column shows: a duplicate's kind, and nothing for any other rejection. */
export function kindLabel(r: ImportRejectionView): string {
  return r.duplicate_of === undefined ? '' : KIND_LABEL[r.duplicate_of];
}

export const WHOLE_ROW = 'whole row';

/** The column at fault; the row as a whole when the service names none. */
export function fieldLabel(r: ImportRejectionView): string {
  return r.field ?? WHOLE_ROW;
}

export interface RejectionFilter {
  code?: string;
  /** A column name, or WHOLE_ROW. */
  field?: string;
  /** FILE, RECORDED_SALE, or 'NONE' for rejections that are not duplicates. */
  kind?: DuplicateKind | 'NONE';
  /** Only the repeats of this line of the file. */
  firstLine?: number;
}

export function matches(r: ImportRejectionView, f: RejectionFilter): boolean {
  if (f.code !== undefined && r.code !== f.code) return false;
  if (f.field !== undefined && fieldLabel(r) !== f.field) return false;
  if (f.kind !== undefined && (f.kind === 'NONE' ? r.duplicate_of !== undefined : r.duplicate_of !== f.kind))
    return false;
  if (f.firstLine !== undefined && !(r.duplicate_of === 'FILE' && r.first_line === f.firstLine)) return false;
  return true;
}

export function filterRejections(
  rows: readonly ImportRejectionView[],
  f: RejectionFilter,
): ImportRejectionView[] {
  return rows.filter((r) => matches(r, f));
}

/** The distinct values of a column of the loaded rejections, sorted, for a filter's choices. */
export function distinct(
  rows: readonly ImportRejectionView[],
  of: (r: ImportRejectionView) => string,
): string[] {
  return [...new Set(rows.map(of))].sort();
}

export interface DuplicateCounts {
  inFile: number;
  recorded: number;
}

export function duplicateCounts(rows: readonly ImportRejectionView[]): DuplicateCounts {
  return {
    inFile: rows.filter((r) => r.duplicate_of === 'FILE').length,
    recorded: rows.filter((r) => r.duplicate_of === 'RECORDED_SALE').length,
  };
}
