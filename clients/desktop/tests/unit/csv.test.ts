import { describe, expect, it } from 'vitest';
import { parseCsv, UnclosedQuote } from '../../src/main/csv';

// The reader of the file the console imports (F28-02, #871): the dialect of the web's page.

const BOM = String.fromCharCode(0xfeff);
const cells = (text: string): string[][] => parseCsv(text).records.map((r) => r.cells);
const lines = (text: string): number[] => parseCsv(text).records.map((r) => r.line);

describe('records and line numbers', () => {
  it('reads a header and rows, each with the line it starts on', () => {
    expect(cells('a,b\n1,2\n3,4\n')).toEqual([
      ['a', 'b'],
      ['1', '2'],
      ['3', '4'],
    ]);
    expect(lines('a,b\n1,2\n3,4\n')).toEqual([1, 2, 3]);
  });

  it('ends a line with CRLF, LF or a lone CR, and counts each as one line', () => {
    for (const eol of ['\r\n', '\n', '\r']) {
      const parsed = parseCsv(['h', 'x', 'y'].join(eol) + eol);
      expect(parsed.records.map((r) => r.cells[0])).toEqual(['h', 'x', 'y']);
      expect(parsed.records.map((r) => r.line)).toEqual([1, 2, 3]);
    }
  });

  it('reads a last line with no line break', () => {
    expect(cells('a,b\n1,2')).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ]);
  });

  it('ignores a byte-order mark at the start', () => {
    expect(cells(`${BOM}transaction_id,customer_id\n1,2\n`)[0]).toEqual(['transaction_id', 'customer_id']);
  });

  it('skips empty lines, counts them, and keeps the line numbers of the file', () => {
    const parsed = parseCsv('h\n\n1\n\n\n2\n');
    expect(parsed.records.map((r) => [r.line, r.cells[0]])).toEqual([
      [1, 'h'],
      [3, '1'],
      [6, '2'],
    ]);
    expect(parsed.blankLines).toBe(3);
  });

  it('does not count the final line break as an empty line', () => {
    expect(parseCsv('a\n1\n').blankLines).toBe(0);
    expect(parseCsv('a\r\n1\r\n').blankLines).toBe(0);
  });

  it('keeps a line of spaces as a row, as the web reader does', () => {
    expect(cells('a\n  \n')).toEqual([['a'], ['  ']]);
  });
});

describe('cells', () => {
  it('keeps empty cells and the empty cell after a trailing comma', () => {
    expect(cells('a,,c\n,\nx,y,\n')).toEqual([
      ['a', '', 'c'],
      ['', ''],
      ['x', 'y', ''],
    ]);
  });

  it('keeps the spaces around a value', () => {
    expect(cells(' a , b \n')).toEqual([[' a ', ' b ']]);
  });

  it('reads quoted values with commas, escaped quotes and line breaks', () => {
    expect(cells('"a,b","say ""hi""",\n')).toEqual([['a,b', 'say "hi"', '']]);
    expect(cells('"two\nlines","x"\n')).toEqual([['two\nlines', 'x']]);
    expect(cells('"two\r\nlines"\n')).toEqual([['two\r\nlines']]);
  });

  it('numbers the line after a value that spans lines by the physical line it starts on', () => {
    const text = 'h\n"a\nb\r\nc",x\nnext\n';
    expect(lines(text)).toEqual([1, 2, 5]);
    expect(cells(text)[2]).toEqual(['next']);
  });

  it('reads an empty quoted value as a row', () => {
    expect(cells('h\n""\n')).toEqual([['h'], ['']]);
  });

  it('keeps a quote inside an unquoted value, and text after a closing quote, as Python does', () => {
    expect(cells('ab"c,d\n')).toEqual([['ab"c', 'd']]);
    expect(cells('"ab"c,d\n')).toEqual([['abc', 'd']]);
  });

  it('refuses a quoted value that is never closed, naming the line it opened on', () => {
    expect(() => parseCsv('h\nx,"open\nmore\n')).toThrow(UnclosedQuote);
    try {
      parseCsv('h\nx,"open\nmore\n');
    } catch (e) {
      expect((e as UnclosedQuote).line).toBe(2);
    }
  });
});
