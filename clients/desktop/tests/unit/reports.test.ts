import { inflateRawSync } from 'node:zlib';
import { describe, expect, it } from 'vitest';
import { Reports, type ReportExportEnv } from '../../src/main/reports';
import { reportCsv, reportPdf, reportWorkbook } from '../../src/main/report-files';
import type { ReportFormat, ReportView } from '../../src/shared/api';
import { reportChart, reportFileStem, reportSummary, reportTable } from '../../src/shared/report-table';
import { claims, example, makeOffice, MemoryVault, tokenResult, type Responder } from '../support/fake';

const BOM_RE = new RegExp(`^${String.fromCharCode(0xfeff)}`);
const KPI = '/api/analytics-kpis/v1/kpi-reports';

function responder(role: string, report = 'net-sales'): Responder {
  return (r) => {
    const url = new URL(r.url);
    if (r.method === 'POST' && url.pathname.endsWith('/v1/sessions'))
      return { status: 201, body: tokenResult('a1', 'r1') };
    if (url.pathname.endsWith('/v1/sessions/current'))
      return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
    if (url.pathname === `${KPI}/definitions`)
      return { status: 200, body: example('analytics-kpis', 'kpi-definitions') };
    if (url.pathname === `${KPI}/net-sales`) return { status: 200, body: example('analytics-kpis', report) };
    if (url.pathname === '/api/stores/v1/stores')
      return { status: 200, body: example('stores', 'directory-page') };
    if (url.pathname === '/api/sales/v1/channels')
      return { status: 200, body: example('sales', 'list-channels') };
    return { status: 500 };
  };
}

async function setup(
  role: string,
  options: { choose?: string | null; failAudit?: boolean; report?: string } = {},
) {
  const { office, seen } = makeOffice(
    responder(role, options.report),
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  const files: Record<string, Buffer | string> = {};
  const lines: string[] = [];
  const asked: string[] = [];
  const env: ReportExportEnv = {
    chooseFile: (name: string, format: ReportFormat) => {
      asked.push(`${name}|${format}`);
      return Promise.resolve(options.choose === null ? undefined : (options.choose ?? `/tmp/out.${format}`));
    },
    write: (p, d) => void (files[p] = d),
    appendLine: (_p, l) => {
      if (options.failAudit) throw new Error('disk full');
      lines.push(l);
    },
    now: () => new Date('2026-11-03T10:00:00Z'),
  };
  return { reports: new Reports(office, env, '/cfg'), files, lines, asked, seen };
}

const query = { kpi: 'net-sales', from: '2026-09-01', to: '2026-10-31', group_by: ['store', 'period'] };

/** Reads a zip written by the report code: file name to text. */
function unzip(zip: Buffer): Record<string, string> {
  const out: Record<string, string> = {};
  const end = zip.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  const count = zip.readUInt16LE(end + 10);
  let at = zip.readUInt32LE(end + 16);
  for (let i = 0; i < count; i += 1) {
    const size = zip.readUInt32LE(at + 20);
    const nameLength = zip.readUInt16LE(at + 28);
    const local = zip.readUInt32LE(at + 42);
    const name = zip.subarray(at + 46, at + 46 + nameLength).toString('utf8');
    const start = local + 30 + zip.readUInt16LE(local + 26);
    out[name] = inflateRawSync(zip.subarray(start, start + size)).toString('utf8');
    at += 46 + nameLength;
  }
  return out;
}

const hand = (): ReportView => ({
  kpi: 'net-sales',
  name: 'Net sales',
  definition_version: 1,
  from: '2026-10-01',
  to: '2026-10-31',
  group_by: ['store'],
  generated_at: '2026-10-31T18:00:00Z',
  store_restricted: false,
  synthetic: true,
  spans_model_versions: false,
  definition: {
    kpi_code: 'net-sales',
    name: 'Net sales',
    version: 1,
    definition: 'What still stands (accounts for (parens) & <tags>).',
    window: 'The month.',
    dimensions: ['store'],
    measures: [{ code: 'net_amount', kind: 'OBSERVED', unit: 'currency', definition: 'Sum.' }],
  },
  rows: [
    {
      store: '=HYPERLINK("http://x")',
      synthetic: true,
      measures: [{ code: 'net_amount', kind: 'OBSERVED', unit: 'currency', value: '-50.25' }],
    },
    {
      store: '@cmd',
      synthetic: false,
      measures: [{ code: 'net_amount', kind: 'OBSERVED', unit: 'currency', value: '10.00' }],
    },
  ],
  rows_total: 2,
});

describe('reading a report', () => {
  it('reads the examples through the XSD and names stores, channels and the definition', async () => {
    const { reports, seen } = await setup('ANALYST');
    const res = await reports.report(query);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.name).toBe('Net sales');
    expect(res.value.rows).toHaveLength(4);
    expect(res.value.synthetic).toBe(true);
    expect(res.value.rows[2]?.synthetic).toBe(true);
    expect(res.value.definition.measures.map((m) => m.code)).toEqual([
      'net_amount',
      'net_units',
      'net_orders',
    ]);
    const asked = seen.find((s) => s.url.includes('/kpi-reports/net-sales'));
    expect(new URL(asked?.url ?? '').searchParams.get('group_by')).toBe('store,period');
    expect(asked?.headers.Accept).toBe('application/xml');
  });

  it('shows the Synthetic marker on the screen figures, not on observed ones', async () => {
    const { reports } = await setup('ANALYST');
    const res = await reports.report(query);
    if (!res.ok) throw new Error(res.error.code);
    const table = reportTable(res.value);
    expect(table.columns.at(-1)).toBe('Data origin');
    expect(table.rows.map((r) => r.at(-1))).toEqual(['Observed', 'Observed', 'Synthetic', 'Observed']);
    expect(reportSummary(res.value, table).find(([k]) => k === 'Data origin')?.[1]).toMatch(/^Synthetic/);
    expect(reportFileStem(res.value)).toBe('mosaiq-report-net-sales-2026-09-01_2026-10-31-synthetic');
  });

  it('refuses a query the contract cannot answer before anything is sent', async () => {
    const { reports, seen } = await setup('ANALYST');
    const before = seen.length;
    for (const bad of [
      { ...query, kpi: 'nope' },
      { ...query, from: '2026-10-31', to: '2026-10-01' },
      { ...query, from: '2020-01-01', to: '2026-10-31' },
      { ...query, group_by: ['customer'] },
      { ...query, granularity: 'year' },
    ])
      expect(await reports.report(bad)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen.length).toBe(before);
  });

  it('does not read a report the role is not allowed to call', async () => {
    const { reports } = await setup('STORE_MANAGER');
    const res = await reports.report({ ...query, kpi: 'migrations' });
    expect(res).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  });
});

describe('exporting a report', () => {
  it('writes PDF, CSV and Excel from the report on screen, with one audit line each', async () => {
    const { reports, files, lines, asked } = await setup('AUDITOR');
    const shown = await reports.report(query);
    if (!shown.ok) throw new Error('report');
    for (const format of ['pdf', 'csv', 'xlsx'] as const)
      expect(await reports.exportReport(format)).toEqual({
        ok: true,
        value: { rows: 4, file: `/tmp/out.${format}` },
      });
    expect(asked[0]).toBe('mosaiq-report-net-sales-2026-09-01_2026-10-31-synthetic.pdf|pdf');
    expect(lines.map((l) => JSON.parse(l))).toMatchObject([
      { role: 'AUDITOR', kpi: 'net-sales', format: 'pdf', synthetic: true, rows: 4 },
      { format: 'csv' },
      { format: 'xlsx' },
    ]);

    const table = reportTable(shown.value);
    // CSV: the same cells as the screen.
    const csv = (files['/tmp/out.csv'] as string).replace(BOM_RE, '').trimEnd().split('\r\n');
    expect(csv[0]).toBe(table.columns.join(','));
    expect(csv.slice(1)).toEqual(table.rows.map((r) => r.join(',')));
    // Excel: the measure cells are numbers equal to the screen text.
    const sheets = unzip(files['/tmp/out.xlsx'] as Buffer);
    const report = sheets['xl/worksheets/sheet2.xml'] as string;
    const first = table.measures[0]?.column ?? 0;
    for (const row of table.rows) expect(report).toContain(`<v>${row[first]}</v>`);
    expect(sheets['xl/worksheets/sheet1.xml']).toContain('Synthetic');
    expect(sheets['xl/worksheets/sheet3.xml']).toContain('Sum of net_amount');
    // PDF: every figure of the screen is drawn.
    const pdf = (files['/tmp/out.pdf'] as Buffer).toString('latin1');
    for (const row of table.rows)
      for (const cell of row) expect(pdf).toContain(`(${cell.replace(/[()\\]/g, '\\$&')})`);
    expect(pdf).toContain('SYNTHETIC');
  });

  it('exports the newest report when an older request finishes after it', async () => {
    const base = responder('AUDITOR');
    let release = (): void => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    const slow: Responder = async (r) => {
      if (!r.url.includes('/kpi-reports/net-sales')) return base(r);
      // The older request (September) is held until the newer one has finished;
      // the newer one (October) answers for its own period.
      if (r.url.includes('from=2026-09-01')) {
        await held;
        return base(r);
      }
      const october = example('analytics-kpis', 'net-sales').replace(
        '<from>2026-09-01</from>',
        '<from>2026-10-01</from>',
      );
      return { status: 200, body: october };
    };
    const { office } = makeOffice(
      slow,
      new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
    );
    await office.signIn({ email: 'a@b.c', password: 'pw' });
    const asked: string[] = [];
    const env: ReportExportEnv = {
      chooseFile: (name: string, format: ReportFormat) => {
        asked.push(name);
        return Promise.resolve(`/tmp/out.${format}`);
      },
      write: () => undefined,
      appendLine: () => undefined,
      now: () => new Date('2026-11-03T10:00:00Z'),
    };
    const reports = new Reports(office, env, '/cfg');
    const older = reports.report(query);
    const newer = await reports.report({ ...query, from: '2026-10-01' });
    release();
    expect(await older).toMatchObject({ ok: true });
    if (!newer.ok) throw new Error('report');
    expect(newer.value.from).toBe('2026-10-01');
    expect(await reports.exportReport('csv')).toMatchObject({ ok: true });
    // The file is the newest report, the one on screen, not the older one that finished last.
    expect(asked).toEqual([`${reportFileStem(newer.value)}.csv`]);
    expect(asked[0]).toContain('2026-10-01');
  });

  it('exports nothing before a report is open, or after it failed', async () => {
    const { reports, files } = await setup('AUDITOR');
    expect(await reports.exportReport('csv')).toMatchObject({
      ok: false,
      error: { code: 'nothing_to_export' },
    });
    await reports.report({ ...query, kpi: 'nope' });
    expect(await reports.exportReport('csv')).toMatchObject({
      ok: false,
      error: { code: 'nothing_to_export' },
    });
    expect(files).toEqual({});
  });

  it('refuses a role that may read but not export, writing and auditing nothing', async () => {
    const { reports, files, lines } = await setup('STORE_MANAGER', { report: 'net-sales-store-scoped' });
    const shown = await reports.report({ kpi: 'net-sales', from: '2026-10-01', to: '2026-10-31' });
    expect(shown.ok).toBe(true);
    expect(await reports.exportReport('pdf')).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(files).toEqual({});
    expect(lines).toEqual([]);
  });

  it('refuses an unknown format, writes nothing when cancelled and nothing it could not record', async () => {
    const a = await setup('AUDITOR');
    await a.reports.report(query);
    expect(await a.reports.exportReport('docx')).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    const c = await setup('AUDITOR', { choose: null });
    await c.reports.report(query);
    expect(await c.reports.exportReport('csv')).toEqual({ ok: true, value: null });
    expect(c.files).toEqual({});
    const d = await setup('AUDITOR', { failAudit: true });
    await d.reports.report(query);
    expect(await d.reports.exportReport('csv')).toMatchObject({
      ok: false,
      error: { code: 'audit_unavailable' },
    });
    expect(d.files).toEqual({});
  });
});

describe('report files', () => {
  it('neutralizes a CSV cell that starts with a formula character but keeps negative numbers', () => {
    const csv = reportCsv(hand());
    const rows = csv.replace(BOM_RE, '').split('\r\n');
    expect(rows[1]).toBe(`"'=HYPERLINK(""http://x"")",-50.25,Synthetic`);
    expect(rows[2]).toBe("'@cmd,10.00,Observed");
    for (const lead of ['=', '+', '-', '@', '\t', '\r']) {
      const report = hand();
      (report.rows[0] as { store: string }).store = `${lead}x`;
      const cell = reportCsv(report).replace(BOM_RE, '').split('\r\n')[1] ?? '';
      expect(cell.replace(/^"/, '').startsWith("'")).toBe(true);
    }
  });

  it('keeps the workbook text as strings and the figures as numbers', () => {
    const sheets = unzip(reportWorkbook(hand()));
    expect(Object.keys(sheets)).toContain('xl/workbook.xml');
    const report = sheets['xl/worksheets/sheet2.xml'] as string;
    expect(report).toContain(
      't="inlineStr"><is><t xml:space="preserve">=HYPERLINK(&quot;http://x&quot;)</t>',
    );
    expect(report).toContain('<c r="B2"><v>-50.25</v></c>');
    expect(sheets['xl/worksheets/sheet3.xml']).toContain('(parens) &amp; &lt;tags&gt;');
  });

  it('writes a PDF whose cross-reference table points at its objects', () => {
    const pdf = reportPdf(hand());
    const text = pdf.toString('latin1');
    expect(text.startsWith('%PDF-1.4')).toBe(true);
    const xref = Number(/startxref\n(\d+)/.exec(text)?.[1]);
    expect(text.slice(xref, xref + 4)).toBe('xref');
    const offsets = [...text.slice(xref).matchAll(/(\d{10}) 00000 n/g)].map((m) => Number(m[1]));
    offsets.forEach((o, i) => expect(text.slice(o, o + 7)).toBe(`${i + 1} 0 obj`));
    expect(text).toContain('\\(parens\\) & <tags>');
    expect(text).toContain('[Synthetic]');
  });

  it('paginates a long report instead of drawing off the page', () => {
    const report = hand();
    report.rows = Array.from({ length: 120 }, (_, i) => ({
      store: `Store ${i}`,
      synthetic: false,
      measures: [{ code: 'net_amount', kind: 'OBSERVED', unit: 'currency', value: `${i}.00` }],
    }));
    report.rows_total = 120;
    report.synthetic = false;
    const text = reportPdf(report).toString('latin1');
    expect(Number(/\/Count (\d+)/.exec(text)?.[1])).toBeGreaterThan(2);
    expect(text).toContain('(Store 119)');
  });

  it('charts the first measure of a grouped report, and not a single figure', () => {
    const report = hand();
    const chart = reportChart(report, reportTable(report));
    expect(chart?.bars.map((b) => b.value)).toEqual([-50.25, 10]);
    expect(chart?.bars[0]?.synthetic).toBe(true);
    report.rows = report.rows.slice(0, 1);
    expect(reportChart(report, reportTable(report))).toBeNull();
  });
});
