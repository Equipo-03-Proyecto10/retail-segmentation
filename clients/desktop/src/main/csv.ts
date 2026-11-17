// A CSV reader for the sales file the console imports (F28-02, #871, ADR-0070 section 6). It runs
// in the main process only: the renderer never sees the file.
//
// It reads the dialect of the web's page (Python's csv module, ADR-0020): commas, double quotes
// with "" as the escaped quote, a quote inside an unquoted value kept as it is, any of CRLF, LF
// or CR as the end of a line, a quoted value that may hold commas and line breaks, and a leading
// byte-order mark ignored. Empty lines are skipped, as the web's reader does.

export interface CsvRecord {
  /** The line of the file where the record starts, the first line being 1. */
  line: number;
  cells: string[];
}

export interface CsvParse {
  records: CsvRecord[];
  /** Empty lines, which are not records. */
  blankLines: number;
}

/** A value opened with a quote that the file never closes. Reading on would swallow the rest of the file. */
export class UnclosedQuote extends Error {
  constructor(readonly line: number) {
    super(`line ${line}: a quoted value is not closed`);
  }
}

type State = 'start' | 'field' | 'quoted' | 'quote';

export function parseCsv(text: string): CsvParse {
  const records: CsvRecord[] = [];
  let blankLines = 0;
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0;
  let line = 1;
  const n = text.length;

  while (i < n) {
    const startLine = line;
    const cells: string[] = [];
    let cell = '';
    let state: State = 'start';
    let consumed = false;
    let quoteLine = line;
    let ended = false;

    // Consumes a line break at i - 1 (a CR followed by LF is one break) and counts the line.
    const breakLine = (c: string): void => {
      if (c === '\r' && text[i] === '\n') i += 1;
      line += 1;
    };

    while (i < n && !ended) {
      const c = text[i] as string;
      i += 1;
      if (state === 'quoted') {
        if (c === '"') state = 'quote';
        else if (c === '\r' || c === '\n') {
          const crlf = c === '\r' && text[i] === '\n';
          cell += crlf ? '\r\n' : c;
          breakLine(c);
        } else cell += c;
        continue;
      }
      consumed = true;
      if (c === '\r' || c === '\n') {
        breakLine(c);
        ended = true;
        consumed = state !== 'start' || cells.length > 0;
      } else if (c === ',') {
        cells.push(cell);
        cell = '';
        state = 'start';
      } else if (state === 'quote' && c === '"') {
        cell += '"';
        state = 'quoted';
      } else if (state === 'start' && c === '"') {
        state = 'quoted';
        quoteLine = line;
      } else {
        cell += c;
        state = 'field';
      }
    }

    if (state === 'quoted') throw new UnclosedQuote(quoteLine);
    if (!consumed) {
      blankLines += 1;
      continue;
    }
    cells.push(cell);
    records.push({ line: startLine, cells });
  }
  return { records, blankLines };
}
