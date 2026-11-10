import { parseCsv, UnclosedQuote } from './csv';
import { buildXml, type FieldGroup } from './xml';

// What the main process does with the file the user chose (F28-02, #871): read it as UTF-8 CSV,
// check its header against contract version 1, and cut its rows into the numbered chunks of
// ADR-0070 section 1. The limits are the contract's: a chunk holds at most 150 rows and 60,000
// bytes of serialized body, a row at most 64 cells, a cell at most 256 characters.

/** The eight columns of CSV contract v1, in order (ADR-0020, ADR-0070). */
export const IMPORT_HEADER: readonly string[] = [
  'transaction_id',
  'customer_id',
  'store_id',
  'channel_id',
  'occurred_at',
  'product_id',
  'quantity',
  'unit_price',
];

export const MAX_ROWS_PER_CHUNK = 150;
/** The client stops a chunk here, so the 64 KiB cap of the edge is never what it discovers. */
export const MAX_CHUNK_BYTES = 60_000;
export const MAX_IMPORT_ROWS = 100_000;
export const MAX_CHUNKS = 667;
export const MAX_CELLS = 64;
export const MAX_CELL_CHARS = 256;
/** About 3 times the web page's 5 MiB, which holds roughly 65,000 lines; the ceiling is 100,000 rows. */
export const MAX_FILE_BYTES = 16 * 1024 * 1024;

const CHUNK_ROOT = 'SaleImportChunk';
/** A row with more cells than this is cut to this many: it is wrong_cell_count however many it has. */
const TRIMMED_CELLS = IMPORT_HEADER.length + 1;

export interface ImportRow {
  /** The line of the file, the header being line 1. */
  line: number;
  cells: string[];
}

export interface PlannedChunk {
  /** From 0. */
  sequence: number;
  rows: ImportRow[];
  /** The size of the serialized request body. */
  bytes: number;
}

export interface ImportPlan {
  totalRows: number;
  blankLines: number;
  chunks: PlannedChunk[];
}

/** A file the console will not send. Nothing has been sent when this is thrown. */
export class ImportFileError extends Error {
  constructor(
    readonly reason: string,
    message: string,
    readonly details: { field: string; code: string }[] = [],
  ) {
    super(message);
  }
}

/** Characters XML 1.0 can carry: tab, line feed, carriage return and everything from space up but U+FFFE and U+FFFF. */
function carriable(code: number): boolean {
  return code >= 0x20 ? code !== 0xfffe && code !== 0xffff : code === 0x09 || code === 0x0a || code === 0x0d;
}

function carry(value: string): string {
  for (let i = 0; i < value.length; i += 1) {
    if (!carriable(value.charCodeAt(i))) {
      return Array.from(value, (ch) => (carriable(ch.codePointAt(0) as number) ? ch : '�')).join('');
    }
  }
  return value;
}

function cut(value: string, max: number): string {
  if (value.length <= max) return value;
  const characters = Array.from(value);
  return characters.length <= max ? value : characters.slice(0, max).join('');
}

/**
 * The cells as the chunk can carry them, so that one odd row can never make the service refuse
 * a chunk of good rows (ADR-0020). A character XML cannot hold becomes U+FFFD, a cell is cut at
 * 256 characters and a row at 64 cells. No column accepts a longer cell and no row with more than
 * eight cells is valid, so the service still rejects each of them by line, with its own reason.
 */
export function limitCells(cells: readonly string[]): string[] {
  return cells.slice(0, MAX_CELLS).map((c) => cut(carry(c), MAX_CELL_CHARS));
}

/** The request fields of a chunk, as the transport builds them. */
export function chunkFields(rows: readonly ImportRow[]): { rows: FieldGroup[] } {
  return { rows: rows.map((r) => ({ line: String(r.line), cells: r.cells })) };
}

/** The serialized request body of a chunk, exactly as the transport sends it. */
export function chunkXml(namespace: string, rows: readonly ImportRow[]): string {
  return buildXml(CHUNK_ROOT, namespace, chunkFields(rows));
}

/**
 * Cuts rows into chunks of at most 150 rows and 60,000 bytes. A row's size is measured by
 * serializing it with the same builder the transport uses, so a chunk's size is its body's size.
 */
export function planChunks(namespace: string, rows: readonly ImportRow[]): PlannedChunk[] {
  const probe: ImportRow = { line: 1, cells: [] };
  // The root element, its namespace and its closing tag, around the rows.
  const frame =
    Buffer.byteLength(chunkXml(namespace, [probe])) - Buffer.byteLength('<rows><line>1</line></rows>');
  const size = (row: ImportRow): number => Buffer.byteLength(chunkXml(namespace, [row])) - frame;

  const chunks: PlannedChunk[] = [];
  let current: ImportRow[] = [];
  let bytes = frame;
  const flush = (): void => {
    if (current.length === 0) return;
    chunks.push({ sequence: chunks.length, rows: current, bytes });
    current = [];
    bytes = frame;
  };
  for (const original of rows) {
    let row = original;
    let own = size(row);
    if (frame + own > MAX_CHUNK_BYTES) {
      // Only a row of many large cells gets here, and it cannot have eight cells.
      row = { line: row.line, cells: row.cells.slice(0, TRIMMED_CELLS) };
      own = size(row);
    }
    if (current.length >= MAX_ROWS_PER_CHUNK || bytes + own > MAX_CHUNK_BYTES) flush();
    current.push(row);
    bytes += own;
  }
  flush();
  return chunks;
}

/**
 * Reads the chosen file and plans its chunks. Throws ImportFileError for a file the console will
 * not send: not UTF-8 text, a quote never closed, the wrong header, no rows, or more rows than an
 * import may hold.
 */
export function planImport(namespace: string, bytes: Buffer): ImportPlan {
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new ImportFileError(
      'not_utf8',
      'The file is not valid UTF-8 text. Save it as "CSV UTF-8" and choose it again.',
      [{ field: 'file', code: 'not_utf8' }],
    );
  }
  let parsed: ReturnType<typeof parseCsv>;
  try {
    parsed = parseCsv(text);
  } catch (e) {
    if (e instanceof UnclosedQuote)
      throw new ImportFileError(
        'unclosed_quote',
        `Line ${e.line}: a quoted value is never closed, so the rest of the file cannot be read as rows.`,
        [{ field: `line ${e.line}`, code: 'unclosed_quote' }],
      );
    throw e;
  }
  const [header, ...data] = parsed.records;
  if (header === undefined)
    throw new ImportFileError('empty', 'The file is empty. It needs the header line and at least one row.', [
      { field: 'file', code: 'empty' },
    ]);
  const wrong = IMPORT_HEADER.findIndex((name, i) => header.cells[i] !== name);
  if (wrong >= 0 || header.cells.length !== IMPORT_HEADER.length) {
    const at = wrong >= 0 ? wrong : IMPORT_HEADER.length;
    throw new ImportFileError(
      'header_mismatch',
      `The header must be ${IMPORT_HEADER.join(', ')}. Column ${at + 1} is ${
        header.cells[at] === undefined ? 'missing' : `"${cut(header.cells[at], 40)}"`
      }.`,
      [{ field: `header[${at}]`, code: 'unexpected_column' }],
    );
  }
  if (data.length === 0)
    throw new ImportFileError('no_rows', 'The file has a header and no rows.', [
      { field: 'file', code: 'empty' },
    ]);
  if (data.length > MAX_IMPORT_ROWS)
    throw new ImportFileError(
      'too_many_rows',
      `The file has ${data.length} rows; an import takes at most ${MAX_IMPORT_ROWS}. Split the file.`,
      [{ field: 'file', code: 'too_many_rows' }],
    );
  const rows = data.map((r): ImportRow => ({ line: r.line, cells: limitCells(r.cells) }));
  const chunks = planChunks(namespace, rows);
  if (chunks.length > MAX_CHUNKS)
    throw new ImportFileError(
      'too_many_chunks',
      `The file needs ${chunks.length} chunks; an import takes at most ${MAX_CHUNKS}. Split the file.`,
      [{ field: 'file', code: 'too_many_chunks' }],
    );
  return { totalRows: rows.length, blankLines: parsed.blankLines, chunks };
}
