import { describe, expect, it } from 'vitest';
import type { ImportRejectionView, SaleImportView } from '../../src/shared/api';
import {
  distinct,
  duplicateCounts,
  fieldLabel,
  filterRejections,
  identityText,
  kindLabel,
  reconcile,
  WHOLE_ROW,
} from '../../src/shared/imports';

// The rules of the import screen (F28-02, #871): the counts identity, and how rejections are
// told apart and filtered. The rows are the recorded list-sale-import-rejections example.

const rows: ImportRejectionView[] = [
  { line: 4, field: 'quantity', code: 'invalid_quantity', message: 'm4' },
  { line: 6, field: 'occurred_at', code: 'future_dated', message: 'm6' },
  { line: 8, code: 'duplicate_row', message: 'm8', duplicate_of: 'FILE', first_line: 3 },
  { line: 9, code: 'duplicate_row', message: 'm9', duplicate_of: 'RECORDED_SALE', sale_id: 4790 },
  { line: 12, code: 'duplicate_row', message: 'm12', duplicate_of: 'FILE', first_line: 3 },
  { line: 15, code: 'duplicate_row', message: 'm15', duplicate_of: 'RECORDED_SALE' },
  { line: 16, field: 'quantity', code: 'invalid_quantity', message: 'm16' },
];
const lines = (r: ImportRejectionView[]): number[] => r.map((x) => x.line);

const view = (
  over: Partial<SaleImportView>,
): Pick<SaleImportView, 'status' | 'received' | 'accepted' | 'rejected' | 'pending'> => ({
  status: 'PROCESSING',
  received: 9,
  accepted: 4,
  rejected: 3,
  pending: 2,
  ...over,
});

describe('the counts identity', () => {
  it('holds when received = accepted + rejected + pending, and is final only at COMPLETED with nothing pending', () => {
    expect(reconcile(view({}))).toMatchObject({ sum: 9, holds: true, final: false });
    expect(reconcile(view({ status: 'COMPLETED', accepted: 5, rejected: 4, pending: 0 }))).toMatchObject({
      holds: true,
      final: true,
    });
    expect(reconcile(view({ status: 'CANCELLED' })).final).toBe(false);
  });

  it('is shown as not holding when the figures disagree', () => {
    expect(reconcile(view({ pending: 1 }))).toMatchObject({ sum: 8, holds: false });
  });

  it('is written out as figures', () => {
    expect(identityText(reconcile(view({})))).toBe('Received 9 = accepted 4 + rejected 3 + pending 2');
  });
});

describe('telling rejections apart', () => {
  it('names the kind of a duplicate and leaves other rejections without one', () => {
    expect(rows.map(kindLabel)).toEqual([
      '',
      '',
      'Repeat in the file',
      'Already recorded',
      'Repeat in the file',
      'Already recorded',
      '',
    ]);
  });

  it('names the column, or the whole row when the service names none', () => {
    expect(fieldLabel(rows[0] as ImportRejectionView)).toBe('quantity');
    expect(fieldLabel(rows[2] as ImportRejectionView)).toBe(WHOLE_ROW);
  });

  it('counts repeats in the file and recorded sales separately', () => {
    expect(duplicateCounts(rows)).toEqual({ inFile: 2, recorded: 2 });
  });
});

describe('filtering', () => {
  it('filters by code, by field, by kind and by the line a repeat repeats, alone or together', () => {
    expect(lines(filterRejections(rows, {}))).toEqual([4, 6, 8, 9, 12, 15, 16]);
    expect(lines(filterRejections(rows, { code: 'invalid_quantity' }))).toEqual([4, 16]);
    expect(lines(filterRejections(rows, { field: 'occurred_at' }))).toEqual([6]);
    expect(lines(filterRejections(rows, { field: WHOLE_ROW }))).toEqual([8, 9, 12, 15]);
    expect(lines(filterRejections(rows, { kind: 'FILE' }))).toEqual([8, 12]);
    expect(lines(filterRejections(rows, { kind: 'RECORDED_SALE' }))).toEqual([9, 15]);
    expect(lines(filterRejections(rows, { kind: 'NONE' }))).toEqual([4, 6, 16]);
    expect(lines(filterRejections(rows, { firstLine: 3 }))).toEqual([8, 12]);
    expect(lines(filterRejections(rows, { firstLine: 5 }))).toEqual([]);
    expect(lines(filterRejections(rows, { code: 'duplicate_row', kind: 'RECORDED_SALE' }))).toEqual([9, 15]);
    expect(lines(filterRejections(rows, { code: 'future_dated', kind: 'FILE' }))).toEqual([]);
  });

  it('lists the distinct codes and fields of the rows loaded, sorted', () => {
    expect(distinct(rows, (r) => r.code)).toEqual(['duplicate_row', 'future_dated', 'invalid_quantity']);
    expect(distinct(rows, fieldLabel)).toEqual(['occurred_at', 'quantity', WHOLE_ROW]);
  });
});
