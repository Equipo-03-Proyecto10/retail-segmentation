import { crc32, deflateRawSync } from 'node:zlib';
import type { ReportView } from '../shared/api';
import {
  reportChart,
  reportDefinitions,
  reportSummary,
  reportTable,
  SYNTHETIC_LABEL,
  type ReportTable,
} from '../shared/report-table';
import { toCsv } from './exports';

// The three files an executive report is written to (F28-14, #883). Each is built from the same
// ReportTable the screen shows, so the figures cannot differ. No dependency: a CSV is text, an
// Excel workbook is a zip of XML parts, and the PDF is a single-font document drawn with
// rectangles and text.

export function reportCsv(report: ReportView): string {
  const table = reportTable(report);
  return toCsv(table.columns, table.rows);
}

// ---------- Excel (.xlsx) ----------

const XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';
const MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
const REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const PKG_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships';
const NUMBER = /^-?\d+(\.\d+)?$/;

/** XML 1.0 allows tab, newline, carriage return and the characters from space up, minus two. */
const xmlChar = (ch: string): boolean => {
  const code = ch.codePointAt(0) as number;
  return code === 9 || code === 10 || code === 13 || (code >= 32 && code !== 0xfffe && code !== 0xffff);
};

function xmlText(value: string): string {
  return [...value]
    .filter(xmlChar)
    .join('')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function columnName(index: number): string {
  let n = index + 1;
  let name = '';
  while (n > 0) {
    name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

/** A sheet of rows; a cell is text (stored as an inline string, never read as a formula) or a number. */
function sheetXml(rows: (string | { number: string })[][]): string {
  const body = rows
    .map((cells, r) => {
      const cs = cells
        .map((cell, c) => {
          const ref = `${columnName(c)}${r + 1}`;
          if (typeof cell !== 'string') return `<c r="${ref}"><v>${cell.number}</v></c>`;
          if (cell === '') return '';
          return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xmlText(cell)}</t></is></c>`;
        })
        .join('');
      return `<row r="${r + 1}">${cs}</row>`;
    })
    .join('');
  return `${XML_HEAD}<worksheet xmlns="${MAIN_NS}"><sheetData>${body}</sheetData></worksheet>`;
}

export function reportWorkbook(report: ReportView): Buffer {
  const table = reportTable(report);
  const numeric = new Set(table.measures.map((m) => m.column));
  const data: (string | { number: string })[][] = [
    table.columns,
    ...table.rows.map((row) =>
      row.map((cell, c) => (numeric.has(c) && NUMBER.test(cell) ? { number: cell } : cell)),
    ),
  ];
  const sheets: [string, (string | { number: string })[][]][] = [
    ['Summary', reportSummary(report, table)],
    ['Report', data],
    ['Definitions', [['Term', 'Meaning'], ...reportDefinitions(report)]],
  ];
  const parts: [string, string][] = [
    [
      '[Content_Types].xml',
      `${XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${sheets
        .map(
          (_, i) =>
            `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
        )
        .join('')}</Types>`,
    ],
    [
      '_rels/.rels',
      `${XML_HEAD}<Relationships xmlns="${PKG_REL_NS}"><Relationship Id="rId1" Type="${REL_NS}/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    ],
    [
      'xl/workbook.xml',
      `${XML_HEAD}<workbook xmlns="${MAIN_NS}" xmlns:r="${REL_NS}"><sheets>${sheets
        .map(([name], i) => `<sheet name="${name}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`)
        .join('')}</sheets></workbook>`,
    ],
    [
      'xl/_rels/workbook.xml.rels',
      `${XML_HEAD}<Relationships xmlns="${PKG_REL_NS}">${sheets
        .map(
          (_, i) =>
            `<Relationship Id="rId${i + 1}" Type="${REL_NS}/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`,
        )
        .join('')}</Relationships>`,
    ],
    ...sheets.map(([, rows], i): [string, string] => [`xl/worksheets/sheet${i + 1}.xml`, sheetXml(rows)]),
  ];
  return zip(parts);
}

/** A zip of text files, deflated, with a fixed timestamp so the same report gives the same bytes. */
export function zip(files: [string, string][]): Buffer {
  const DOS_DATE = ((2026 - 1980) << 9) | (1 << 5) | 1;
  const chunks: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  for (const [name, text] of files) {
    const raw = Buffer.from(text, 'utf8');
    const packed = deflateRawSync(raw);
    const nameBytes = Buffer.from(name, 'utf8');
    const crc = crc32(raw);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(8, 8);
    local.writeUInt16LE(0, 10);
    local.writeUInt16LE(DOS_DATE, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(raw.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    chunks.push(local, nameBytes, packed);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8);
    entry.writeUInt16LE(8, 10);
    entry.writeUInt16LE(0, 12);
    entry.writeUInt16LE(DOS_DATE, 14);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(packed.length, 20);
    entry.writeUInt32LE(raw.length, 24);
    entry.writeUInt16LE(nameBytes.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, nameBytes);
    offset += local.length + nameBytes.length + packed.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...chunks, directory, end]);
}

// ---------- PDF ----------

const PAGE_W = 842;
const PAGE_H = 595;
const MARGIN = 36;
const GLYPH = 0.56;

/** The standard Helvetica fonts read WinAnsi; anything outside Latin-1 becomes '?'. */
export function pdfSafe(value: string): string {
  let out = '';
  for (const ch of value) {
    const code = ch.codePointAt(0) as number;
    out += code < 0x20 || (code >= 0x7f && code <= 0x9f) || code > 0xff ? (code < 0x20 ? ' ' : '?') : ch;
  }
  return out;
}

const pdfString = (value: string): string =>
  `(${pdfSafe(value).replaceAll('\\', '\\\\').replaceAll('(', '\\(').replaceAll(')', '\\)')})`;

const fit = (value: string, width: number, size: number): string => {
  const max = Math.max(1, Math.floor(width / (GLYPH * size)));
  return value.length <= max ? value : `${value.slice(0, Math.max(1, max - 3))}...`;
};

function wrap(value: string, width: number, size: number): string[] {
  const max = Math.max(8, Math.floor(width / (GLYPH * size)));
  const lines: string[] = [];
  let line = '';
  for (const word of value.split(/\s+/).filter(Boolean)) {
    if (line !== '' && line.length + 1 + word.length > max) {
      lines.push(line);
      line = '';
    }
    let rest = word;
    while (rest.length > max) {
      lines.push(rest.slice(0, max));
      rest = rest.slice(max);
    }
    line = line === '' ? rest : `${line} ${rest}`;
  }
  if (line !== '') lines.push(line);
  return lines.length > 0 ? lines : [''];
}

class Page {
  private readonly pages: string[][] = [[]];
  y = MARGIN;

  private get ops(): string[] {
    return this.pages[this.pages.length - 1] as string[];
  }

  newPage(): void {
    this.pages.push([]);
    this.y = MARGIN;
  }

  ensure(height: number, onBreak?: () => void): void {
    if (this.y + height <= PAGE_H - MARGIN) return;
    this.newPage();
    onBreak?.();
  }

  text(x: number, value: string, size: number, bold = false): void {
    this.ops.push(
      `BT /${bold ? 'F2' : 'F1'} ${size} Tf ${x.toFixed(2)} ${(PAGE_H - this.y - size).toFixed(2)} Td ${pdfString(value)} Tj ET`,
    );
  }

  rect(x: number, w: number, h: number, rgb: [number, number, number]): void {
    this.ops.push(
      `${rgb.join(' ')} rg ${x.toFixed(2)} ${(PAGE_H - this.y - h).toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re f 0 g`,
    );
  }

  build(): Buffer {
    const objects: string[] = [];
    const pageIds = this.pages.map((_, i) => 5 + i * 2);
    objects.push('<< /Type /Catalog /Pages 2 0 R >>');
    objects.push(
      `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] /Count ${pageIds.length} >>`,
    );
    objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
    objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');
    this.pages.forEach((ops, i) => {
      const stream = ops.join('\n');
      objects.push(
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${6 + i * 2} 0 R >>`,
      );
      objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
    });
    let out = '%PDF-1.4\n';
    const offsets: number[] = [];
    objects.forEach((body, i) => {
      offsets.push(out.length);
      out += `${i + 1} 0 obj\n${body}\nendobj\n`;
    });
    const xref = out.length;
    out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    for (const o of offsets) out += `${String(o).padStart(10, '0')} 00000 n \n`;
    out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    return Buffer.from(out, 'latin1');
  }
}

function drawTable(page: Page, table: ReportTable): void {
  const size = 8;
  const usable = PAGE_W - 2 * MARGIN;
  const numeric = new Set(table.measures.map((m) => m.column));
  const lengths = table.columns.map((h, c) =>
    Math.min(40, Math.max(6, h.length, ...table.rows.map((r) => (r[c] ?? '').length))),
  );
  const total = lengths.reduce((a, b) => a + b, 0);
  const widths = lengths.map((l) => (l / total) * usable);
  const starts = widths.map((_, c) => MARGIN + widths.slice(0, c).reduce((a, b) => a + b, 0));
  const cell = (c: number, value: string, bold: boolean): void => {
    const shown = fit(value, (widths[c] as number) - 6, size);
    const x = numeric.has(c)
      ? (starts[c] as number) + (widths[c] as number) - 3 - shown.length * GLYPH * size
      : (starts[c] as number) + 3;
    page.text(x, shown, size, bold);
  };
  const header = (): void => {
    table.columns.forEach((h, c) => cell(c, h, true));
    page.y += 13;
  };
  page.ensure(26, undefined);
  header();
  for (const row of table.rows) {
    page.ensure(12, header);
    row.forEach((v, c) => cell(c, v, false));
    page.y += 11;
  }
}

export function reportPdf(report: ReportView): Buffer {
  const table = reportTable(report);
  const page = new Page();
  const width = PAGE_W - 2 * MARGIN;
  page.text(MARGIN, `${report.name} - ${report.from} to ${report.to}`, 16, true);
  page.y += 24;
  if (report.synthetic) {
    page.text(
      MARGIN,
      `${SYNTHETIC_LABEL.toUpperCase()}: some figures in this report come from seeded data (ADR-0052).`,
      10,
      true,
    );
    page.y += 16;
  }
  for (const [label, value] of reportSummary(report, table)) {
    for (const [i, line] of wrap(value, width - 130, 9).entries()) {
      page.ensure(12);
      if (i === 0) page.text(MARGIN, label, 9, true);
      page.text(MARGIN + 130, line, 9);
      page.y += 12;
    }
  }
  const chart = reportChart(report, table);
  if (chart !== null) {
    page.y += 8;
    page.ensure(20 + chart.bars.length * 14);
    page.text(MARGIN, chart.title, 10, true);
    page.y += 16;
    const top = Math.max(...chart.bars.map((b) => b.value), 0);
    for (const bar of chart.bars) {
      page.ensure(14);
      const label = bar.synthetic ? `${bar.label} [${SYNTHETIC_LABEL}]` : bar.label;
      page.text(MARGIN, fit(label, 230, 8), 8);
      const length = top > 0 ? (Math.max(bar.value, 0) / top) * 400 : 0;
      page.rect(MARGIN + 240, length, 9, bar.synthetic ? [0.75, 0.55, 0.2] : [0.2, 0.4, 0.7]);
      page.text(MARGIN + 246 + length, bar.text, 8);
      page.y += 14;
    }
    if (chart.omitted > 0) {
      page.text(MARGIN, `${chart.omitted} more rows are in the table.`, 8);
      page.y += 12;
    }
  }
  page.y += 12;
  page.ensure(40);
  page.text(MARGIN, 'Figures', 12, true);
  page.y += 18;
  drawTable(page, table);
  page.y += 14;
  page.ensure(30);
  page.text(MARGIN, 'Definitions', 12, true);
  page.y += 18;
  for (const [term, meaning] of reportDefinitions(report)) {
    const lines = wrap(meaning, width - 170, 9);
    page.ensure(12 * lines.length);
    page.text(MARGIN, fit(term, 160, 9), 9, true);
    for (const line of lines) {
      page.text(MARGIN + 170, line, 9);
      page.y += 12;
    }
  }
  return page.build();
}
