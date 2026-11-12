import { describe, expect, it } from 'vitest';
import { serviceNamespace } from '../../src/main/bundle';
import {
  chunkXml,
  IMPORT_HEADER,
  ImportFileError,
  limitCells,
  MAX_CHUNK_BYTES,
  MAX_ROWS_PER_CHUNK,
  planChunks,
  planImport,
  type ImportRow,
} from '../../src/main/import-chunks';
import { XsdValidator } from '../../src/main/xml';
import { bundle } from '../support/fake';

// The chunker of the console's import (F28-02, #871, ADR-0070 sections 1 and 2).

const NS = serviceNamespace(bundle, 'sales');
const BOM = String.fromCharCode(0xfeff);
const validator = new XsdValidator(bundle);
const HEADER = IMPORT_HEADER.join(',');
const rowOf = (line: number, extra = ''): ImportRow => ({
  line,
  cells: [
    `T-${line}${extra}`,
    '11111111-1111-1111-1111-000000000001',
    '3',
    '2',
    '2026-10-02T14:03:00-06:00',
    '501',
    '2',
    '19.90',
  ],
});
const rows = (n: number, from = 2): ImportRow[] => Array.from({ length: n }, (_, i) => rowOf(from + i));
const csv = (n: number): Buffer =>
  Buffer.from([HEADER, ...rows(n).map((r) => r.cells.join(','))].join('\n') + '\n');

describe('chunk sizes', () => {
  it('stops a chunk at 150 rows, numbers chunks from 0 and keeps every line once, in order', () => {
    const chunks = planChunks(NS, rows(400));
    expect(chunks.map((c) => c.sequence)).toEqual([0, 1, 2]);
    expect(chunks.map((c) => c.rows.length)).toEqual([150, 150, 100]);
    expect(chunks.flatMap((c) => c.rows.map((r) => r.line))).toEqual(rows(400).map((r) => r.line));
  });

  it('holds exactly 150 rows in a full chunk and starts the next at row 151', () => {
    const chunks = planChunks(NS, rows(151));
    expect(chunks.map((c) => c.rows.length)).toEqual([MAX_ROWS_PER_CHUNK, 1]);
  });

  it('stops a chunk before 60,000 bytes of serialized body, and the size it reports is the body size', () => {
    const wide = (line: number): ImportRow => ({
      line,
      cells: Array.from({ length: 8 }, () => 'x'.repeat(256)),
    });
    const chunks = planChunks(
      NS,
      Array.from({ length: 200 }, (_, i) => wide(i + 2)),
    );
    expect(chunks.length).toBeGreaterThan(5);
    for (const c of chunks) {
      const body = Buffer.byteLength(chunkXml(NS, c.rows));
      expect(c.bytes).toBe(body);
      expect(body).toBeLessThanOrEqual(MAX_CHUNK_BYTES);
      expect(c.rows.length).toBeLessThan(MAX_ROWS_PER_CHUNK);
    }
  });

  it('counts bytes, not characters, and the escapes the serializer writes', () => {
    const odd = (line: number): ImportRow => ({
      line,
      cells: ['é'.repeat(100), '😀'.repeat(60), `&"'<>`.repeat(40), '', 'ñ', '1', '2', '3'],
    });
    const chunks = planChunks(
      NS,
      Array.from({ length: 120 }, (_, i) => odd(i + 2)),
    );
    for (const c of chunks) {
      expect(c.bytes).toBe(Buffer.byteLength(chunkXml(NS, c.rows)));
      expect(c.bytes).toBeLessThanOrEqual(MAX_CHUNK_BYTES);
    }
    expect(chunks.length).toBeGreaterThan(1);
  });

  it('plans the same chunks for the same rows, so a resumed import cuts the file where the first run did', () => {
    const first = planChunks(NS, rows(700));
    const again = planChunks(NS, rows(700));
    expect(again.map((c) => [c.sequence, c.rows[0]?.line, c.rows.length, c.bytes])).toEqual(
      first.map((c) => [c.sequence, c.rows[0]?.line, c.rows.length, c.bytes]),
    );
  });

  it('cuts a row too large for any chunk down to nine cells, which is still not eight', () => {
    const huge: ImportRow = { line: 2, cells: Array.from({ length: 64 }, () => '&'.repeat(256)) };
    expect(Buffer.byteLength(chunkXml(NS, [huge]))).toBeGreaterThan(MAX_CHUNK_BYTES);
    const [chunk] = planChunks(NS, [huge]);
    expect(chunk?.rows[0]?.cells).toHaveLength(9);
    expect(chunk?.bytes).toBeLessThanOrEqual(MAX_CHUNK_BYTES);
  });
});

describe('chunk bodies', () => {
  it('validate against sales.xsd, including empty cells and a row with no cell', async () => {
    const odd: ImportRow[] = [
      rowOf(2),
      { line: 3, cells: ['', '', '', '', '', '', '', ''] },
      { line: 4, cells: [] },
      { line: 5, cells: ['a&b <c> "d" \'e\'', ' padded ', 'x'.repeat(256)] },
    ];
    const [chunk] = planChunks(NS, odd);
    const xml = chunkXml(NS, (chunk as { rows: ImportRow[] }).rows);
    expect(await validator.validate(xml, NS)).toEqual({ valid: true, errors: [] });
  });

  it('write one element per cell in order, and an empty cell as an empty element', () => {
    const xml = chunkXml(NS, [{ line: 7, cells: ['a', '', 'c'] }]);
    expect(xml).toContain('<rows><line>7</line><cells>a</cells><cells/><cells>c</cells></rows>');
  });

  it('are all valid for a full 150-row chunk of the planner', async () => {
    const chunks = planChunks(NS, rows(300));
    for (const c of chunks) expect((await validator.validate(chunkXml(NS, c.rows), NS)).valid).toBe(true);
  });
});

describe('limitCells', () => {
  it('cuts a cell at 256 characters without splitting a character, and a row at 64 cells', () => {
    const cut = limitCells(['😀'.repeat(300), 'x'.repeat(300), 'short']);
    expect(Array.from(cut[0] as string)).toHaveLength(256);
    expect((cut[1] as string).length).toBe(256);
    expect(cut[2]).toBe('short');
    expect(limitCells(Array.from({ length: 80 }, () => 'a'))).toHaveLength(64);
  });

  it('replaces characters XML cannot carry and keeps tab, line feed and carriage return', () => {
    expect(limitCells(['a\u0000b\u0001c￾d\u0009e\nf\rg'])).toEqual(['a�b�c�d\u0009e\nf\rg']);
  });
});

describe('planImport', () => {
  it('reads a file with a byte-order mark, CRLF lines and a quoted value', () => {
    const text = `${BOM}${HEADER}\r\n"T-1",11111111-1111-1111-1111-000000000001,3,2,2026-10-02T14:03:00-06:00,501,2,19.90\r\n\r\nT-2,c,3,2,d,501,2,"1,5"\r\n`;
    const plan = planImport(NS, Buffer.from(text));
    expect(plan.totalRows).toBe(2);
    expect(plan.blankLines).toBe(1);
    expect(plan.chunks).toHaveLength(1);
    expect(plan.chunks[0]?.rows.map((r) => r.line)).toEqual([2, 4]);
    expect(plan.chunks[0]?.rows[1]?.cells[7]).toBe('1,5');
  });

  it('cuts 320 rows into 3 chunks numbered from 0', () => {
    const plan = planImport(NS, csv(320));
    expect(plan.totalRows).toBe(320);
    expect(plan.chunks.map((c) => [c.sequence, c.rows.length])).toEqual([
      [0, 150],
      [1, 150],
      [2, 20],
    ]);
  });

  const refusal = (bytes: Buffer): ImportFileError => {
    try {
      planImport(NS, bytes);
    } catch (e) {
      return e as ImportFileError;
    }
    throw new Error('the file was accepted');
  };

  it('refuses a file that is not UTF-8 text', () => {
    const e = refusal(Buffer.from([0x74, 0x2c, 0xe9, 0x0a]));
    expect(e.reason).toBe('not_utf8');
    expect(e.message).toMatch(/CSV UTF-8/);
  });

  it('refuses a quoted value that is never closed, naming its line', () => {
    const e = refusal(Buffer.from(`${HEADER}\nT-1,"open\n`));
    expect(e.reason).toBe('unclosed_quote');
    expect(e.message).toMatch(/Line 2/);
  });

  it('refuses a header that is not the eight columns, naming the first wrong one', () => {
    const e = refusal(
      Buffer.from(
        'transaction_id,customer_id,store_id,channel,occurred_at,product_id,quantity,unit_price\n1\n',
      ),
    );
    expect(e.reason).toBe('header_mismatch');
    expect(e.details).toEqual([{ field: 'header[3]', code: 'unexpected_column' }]);
    expect(e.message).toMatch(/Column 4 is "channel"/);
    expect(refusal(Buffer.from(`${HEADER},extra\n1\n`)).details).toEqual([
      { field: 'header[8]', code: 'unexpected_column' },
    ]);
    expect(refusal(Buffer.from('transaction_id,customer_id\n1\n')).message).toMatch(/Column 3 is missing/);
  });

  it('refuses an empty file and a file with a header and no rows', () => {
    expect(refusal(Buffer.from('')).reason).toBe('empty');
    expect(refusal(Buffer.from(`${HEADER}\n`)).reason).toBe('no_rows');
    expect(refusal(Buffer.from(`${HEADER}\n\n\n`)).reason).toBe('no_rows');
  });

  it('refuses more than 100,000 rows and more than 667 chunks', () => {
    expect(refusal(csv(100_001)).reason).toBe('too_many_rows');
    const wide = Array.from({ length: 8 }, () => 'x'.repeat(256)).join(',');
    const big = Buffer.from(`${HEADER}\n${Array.from({ length: 20_000 }, () => wide).join('\n')}\n`);
    expect(refusal(big).reason).toBe('too_many_chunks');
  });

  it('accepts exactly 100,000 rows in 667 chunks', () => {
    const plan = planImport(NS, csv(100_000));
    expect(plan.totalRows).toBe(100_000);
    expect(plan.chunks).toHaveLength(667);
    expect(plan.chunks.at(-1)?.rows).toHaveLength(100_000 - 666 * 150);
  });
});
