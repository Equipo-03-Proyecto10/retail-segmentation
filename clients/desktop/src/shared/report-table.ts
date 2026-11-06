import type { ReportRowView, ReportView } from './api';

// The one table an executive report is made of (F28-14, #883). The screen, the CSV, the Excel
// workbook and the PDF all read it, so a figure is formatted once and cannot differ between them.
// Values are the decimals exactly as the service sent them.

export interface MeasureColumn {
  code: string;
  kind: string;
  unit: string;
  /** Index into a row of the table. */
  column: number;
  bound?: 'lower' | 'upper';
}

export interface ReportTable {
  columns: string[];
  rows: string[][];
  /** The columns that hold figures (numeric in a workbook). */
  measures: MeasureColumn[];
  /** How many leading columns describe the row (period, segment, store, ...). */
  dimensionCount: number;
}

export const SYNTHETIC_LABEL = 'Synthetic';
export const OBSERVED_LABEL = 'Observed';

const SEGMENT_STATES: Record<string, string> = {
  unassigned_result: 'Unassigned (no result)',
  no_open_assignment: 'No open assignment',
};

const periodOf = (r: ReportRowView): string => {
  if (r.period_start === undefined) return '';
  return r.period_end === undefined || r.period_end === r.period_start
    ? r.period_start
    : `${r.period_start} to ${r.period_end}`;
};

const segmentOf = (r: ReportRowView): string =>
  r.segment_code ??
  (r.segment_state === undefined ? '' : (SEGMENT_STATES[r.segment_state] ?? r.segment_state));

interface Dimension {
  header: string;
  cell: (r: ReportRowView) => string;
  present: (r: ReportRowView) => boolean;
}

const DIMENSIONS: Dimension[] = [
  { header: 'Period', cell: periodOf, present: (r) => r.period_start !== undefined },
  {
    header: 'Segment',
    cell: segmentOf,
    present: (r) => r.segment_code !== undefined || r.segment_state !== undefined,
  },
  {
    header: 'From segment',
    cell: (r) => r.from_segment_code ?? '',
    present: (r) => r.from_segment_code !== undefined,
  },
  { header: 'Store', cell: (r) => r.store ?? '', present: (r) => r.store !== undefined },
  { header: 'Channel', cell: (r) => r.channel ?? '', present: (r) => r.channel !== undefined },
  {
    header: 'Campaign',
    cell: (r) => r.campaign_id ?? '',
    present: (r) => r.campaign_id !== undefined,
  },
  {
    header: 'Model version',
    cell: (r) => r.model_version_id ?? '',
    present: (r) => r.model_version_id !== undefined,
  },
];

export function reportTable(report: ReportView): ReportTable {
  const dims = DIMENSIONS.filter((d) => report.rows.some(d.present));
  // Measures in the order the service first sends them, with an interval's bounds beside the value.
  const seen = new Map<string, { kind: string; unit: string; lower: boolean; upper: boolean }>();
  for (const row of report.rows)
    for (const m of row.measures) {
      const known = seen.get(m.code) ?? { kind: m.kind, unit: m.unit, lower: false, upper: false };
      known.lower ||= m.lower !== undefined;
      known.upper ||= m.upper !== undefined;
      seen.set(m.code, known);
    }
  const measures: MeasureColumn[] = [];
  const columns = dims.map((d) => d.header);
  for (const [code, m] of seen) {
    const add = (header: string, bound?: 'lower' | 'upper'): void => {
      measures.push({
        code,
        kind: m.kind,
        unit: m.unit,
        column: columns.length,
        ...(bound ? { bound } : {}),
      });
      columns.push(header);
    };
    add(`${code} (${m.unit})`);
    if (m.lower) add(`${code} lower`, 'lower');
    if (m.upper) add(`${code} upper`, 'upper');
  }
  columns.push('Data origin');
  const rows = report.rows.map((r) => {
    const cells = dims.map((d) => d.cell(r));
    for (const col of measures) {
      const m = r.measures.find((x) => x.code === col.code);
      cells.push(col.bound === undefined ? (m?.value ?? '') : (m?.[col.bound] ?? ''));
    }
    cells.push(r.synthetic ? SYNTHETIC_LABEL : OBSERVED_LABEL);
    return cells;
  });
  return { columns, rows, measures, dimensionCount: dims.length };
}

export interface Bar {
  label: string;
  value: number;
  text: string;
  synthetic: boolean;
}

export interface Chart {
  title: string;
  bars: Bar[];
  /** Rows beyond the bars that fit a chart. */
  omitted: number;
}

export const MAX_BARS = 12;

/** A bar chart of the report's first measure, one bar per row in the order of the table. */
export function reportChart(report: ReportView, table: ReportTable): Chart | null {
  const first = table.measures[0];
  if (first === undefined || table.rows.length < 2) return null;
  const bars: Bar[] = [];
  table.rows.forEach((row, i) => {
    const text = row[first.column] ?? '';
    const value = Number(text);
    if (text === '' || !Number.isFinite(value)) return;
    const label = row.slice(0, table.dimensionCount).filter(Boolean).join(' / ') || 'Total';
    bars.push({ label, value, text, synthetic: report.rows[i]?.synthetic ?? false });
  });
  if (bars.length < 2) return null;
  return {
    title: `${first.code} (${first.unit})`,
    bars: bars.slice(0, MAX_BARS),
    omitted: Math.max(0, bars.length - MAX_BARS),
  };
}

/** The headline facts of a report, as label and value, for the screen, the PDF and the workbook. */
export function reportSummary(report: ReportView, table: ReportTable): [string, string][] {
  const out: [string, string][] = [
    ['Report', `${report.name} (definition version ${report.definition_version})`],
    ['Window', `${report.from} to ${report.to}${report.granularity ? `, by ${report.granularity}` : ''}`],
    ['Grouped by', report.group_by.length > 0 ? report.group_by.join(', ') : 'Whole window'],
    ['Generated at', report.generated_at],
    ['Stores', report.store_restricted ? 'Your assigned stores only' : 'All stores'],
    [
      'Data origin',
      report.synthetic
        ? `${SYNTHETIC_LABEL}: some figures come from seeded data (ADR-0052)`
        : `${OBSERVED_LABEL}: no figure comes from seeded data`,
    ],
  ];
  if (report.spans_model_versions) out.push(['Model versions', 'The window holds a change of model version']);
  out.push([
    'Rows',
    report.rows_total > table.rows.length
      ? `${table.rows.length} of ${report.rows_total} (the report was cut)`
      : String(table.rows.length),
  ]);
  const first = table.measures[0];
  if (first !== undefined && table.rows.length > 1) {
    let best = -1;
    let top = -Infinity;
    table.rows.forEach((row, i) => {
      const v = Number(row[first.column]);
      if (row[first.column] !== '' && Number.isFinite(v) && v > top) {
        top = v;
        best = i;
      }
    });
    const row = table.rows[best];
    if (row !== undefined)
      out.push([
        `Highest ${first.code}`,
        `${row.slice(0, table.dimensionCount).filter(Boolean).join(' / ') || 'Total'}: ${row[first.column]}`,
      ]);
  }
  return out;
}

/** Each metric's definition, as label and text. */
export function reportDefinitions(report: ReportView): [string, string][] {
  const d = report.definition;
  return [
    [d.name, d.definition],
    ['Window', d.window],
    ...d.measures.map((m): [string, string] => [`${m.code} (${m.kind}, ${m.unit})`, m.definition]),
  ];
}

/** A file name for an export: the KPI, the window, and the Synthetic marker when it applies. */
export function reportFileStem(report: ReportView): string {
  return `mosaiq-report-${report.kpi}-${report.from}_${report.to}${report.synthetic ? '-synthetic' : ''}`;
}
